"""The Job Broker: job lifecycle, scheduling, node lifecycle and the event loop.

Time only moves through `advance_to`. Every step finds the next due event from
persisted timestamps (job end, node ready, reclaim, reaper deadline, back-off
expiry, aging), jumps the clock there and processes it. Nothing lives in an
in-memory timer, so a restart loses nothing but the capacity back-off state,
which is safe to lose.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

from . import policy
from .cloud import CapacityUnavailable, CloudProvider, QuotaExceeded
from .config import BrokerConfig
from .models import (
    ALLOWED_GPUS,
    NODE_TYPES_BY_SIZE,
    TERMINAL_STATUSES,
    Job,
    JobStatus,
    Node,
    NodeState,
    Priority,
    Run,
    node_price,
)
from .store import Store

TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_ESTIMATE_MINUTES = 7 * 24 * 60
EPS = 1e-6

log = logging.getLogger(__name__)


class BrokerError(Exception):
    pass


class ValidationError(BrokerError):
    pass


class NotFound(BrokerError):
    pass


class InvalidTransition(BrokerError):
    pass


class IdempotencyConflict(BrokerError):
    """The same idempotency key was reused with a different request body."""


@dataclass
class _Backoff:
    until: float = 0.0
    failures: int = 0


class Broker:
    def __init__(
        self,
        provider: CloudProvider,
        store: Store | None = None,
        config: BrokerConfig | None = None,
        start_time: float = 0.0,
    ) -> None:
        self.provider = provider
        self.store = store or Store()
        self.config = config or BrokerConfig()

        self.jobs: dict[str, Job] = {d["id"]: Job(**d) for d in self.store.all("job")}
        self.nodes: dict[str, Node] = {d["id"]: Node(**d) for d in self.store.all("node")}
        self.runs: dict[str, Run] = {d["id"]: Run(**d) for d in self.store.all("run")}
        self._idem: dict[str, dict] = {d["key"]: d for d in self.store.all("idem")}
        self.now: float = self.store.get_kv("now", start_time)
        self._seq: dict[str, int] = self.store.get_kv("seq", {"job": 0, "node": 0})
        self.usage: dict[str, float] = self.store.get_kv("usage", {})
        self._usage_at: float = self.store.get_kv("usage_at", self.now)
        self.launch_failures: int = self.store.get_kv("launch_failures", 0)

        self._open: dict[str, Job] = {j.id: j for j in self.jobs.values() if j.status not in TERMINAL_STATUSES}
        self._backoff: dict[tuple[str, bool], _Backoff] = {}
        self._rebalancing: set[str] = set()
        self._dirty: set[tuple[str, str]] = set()

        self._reconcile()

    # ======================================================================
    # Public operations
    # ======================================================================

    def submit(
        self,
        team: str,
        gpus: int,
        estimated_minutes: float,
        priority: str = "normal",
        idempotency_key: str | None = None,
        *,
        actual_minutes: float | None = None,
        outcome: str | None = None,
    ) -> tuple[Job, bool]:
        """Create a job. Returns (job, created). A repeated key returns the original job."""
        self._validate_submission(team, gpus, estimated_minutes, priority)
        fingerprint = {"team": team, "gpus": gpus, "estimated_minutes": estimated_minutes, "priority": priority}

        if idempotency_key is not None:
            existing = self._idem.get(idempotency_key)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise IdempotencyConflict(f"idempotency key {idempotency_key!r} was used for a different request")
                return self.jobs[existing["job_id"]], False

        self._seq["job"] += 1
        job = Job(
            id=f"job-{self._seq['job']:06d}",
            team=team,
            gpus=gpus,
            estimated_minutes=estimated_minutes,
            priority=Priority(priority),
            submitted_at=self.now,
            idempotency_key=idempotency_key,
            actual_minutes=actual_minutes,
            planned_outcome=JobStatus(outcome) if outcome else None,
            queued_since=self.now,
            seq=self._seq["job"],
        )
        self.jobs[job.id] = job
        self._open[job.id] = job
        self._touch(job)
        if idempotency_key is not None:
            record = {"key": idempotency_key, "job_id": job.id, "fingerprint": fingerprint}
            self._idem[idempotency_key] = record
            self.store.put("idem", idempotency_key, record)
        self._process()
        return job, True

    def cancel(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job.status == JobStatus.CANCELLED:
            return job  # cancelling twice is harmless
        if job.status in TERMINAL_STATUSES:
            raise InvalidTransition(f"job {job_id} already {job.status.value}")
        self._accrue_usage()
        if job.status == JobStatus.RUNNING:
            self._finish(job, JobStatus.CANCELLED)
        else:
            job.node_id = None  # releases a reservation on a booting node, if any
            self._close_queue_wait(job)
            job.status = JobStatus.CANCELLED
            job.ended_at = self.now
            self._open.pop(job.id, None)
            self._touch(job)
        self._process()
        return job

    def complete(self, job_id: str, outcome: str = "succeeded") -> Job:
        """Called by a client to report that a running job ended."""
        job = self.get_job(job_id)
        status = JobStatus(outcome)
        if status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            raise ValidationError("outcome must be 'succeeded' or 'failed'")
        if job.status != JobStatus.RUNNING:
            raise InvalidTransition(f"job {job_id} is {job.status.value}, not running")
        self._accrue_usage()
        self._finish(job, status)
        self._process()
        return job

    def advance_to(self, t: float) -> None:
        """Move simulated time forward to t, processing every event on the way."""
        if t < self.now - EPS:
            raise ValidationError("the clock cannot move backwards")
        while True:
            nxt = self._next_event_time()
            if nxt is None or nxt > t:
                break
            self.now = nxt
            self._process()
        self.now = t
        self._process()

    def advance(self, seconds: float) -> None:
        self.advance_to(self.now + seconds)

    def run_until_idle(self, limit_s: float = 30 * 24 * 3600) -> None:
        """Process events until no job is open and no node is alive (or no event remains)."""
        deadline = self.now + limit_s
        while self._open or any(n.alive for n in self.nodes.values()):
            nxt = self._next_event_time()
            if nxt is None or nxt > deadline:
                break
            self.advance_to(nxt)

    # ---- queries -----------------------------------------------------------

    def get_job(self, job_id: str) -> Job:
        try:
            return self.jobs[job_id]
        except KeyError:
            raise NotFound(f"job {job_id} not found") from None

    def queued_jobs(self) -> list[Job]:
        queued = [j for j in self._open.values() if j.status == JobStatus.QUEUED]
        return self._order(queued) if queued else []

    def alive_nodes(self) -> list[Node]:
        return sorted((n for n in self.nodes.values() if n.alive), key=lambda n: n.id)

    def jobs_on(self, node_id: str) -> list[Job]:
        return [j for j in self._open.values() if j.node_id == node_id]

    def cost_report(self, start: float | None = None, end: float | None = None):
        from .costs import cost_report

        return cost_report(
            list(self.nodes.values()),
            list(self.runs.values()),
            start=0.0 if start is None else start,
            end=self.now if end is None else end,
            now=self.now,
            min_billing_s=self.config.min_billing_s,
        )

    def effective_priority(self, job: Job) -> Priority:
        return policy.effective_priority(job, self.now, self.config.low_priority_aging_s)

    # ======================================================================
    # Event processing
    # ======================================================================

    def _process(self) -> None:
        self._accrue_usage()
        self._sync_nodes()
        self._finish_due_jobs()
        self._update_idle()
        self._reap_idle_nodes()
        self._schedule()
        self._update_idle()
        self._commit()

    def _next_event_time(self) -> float | None:
        now = self.now
        candidates: list[float] = []
        provider_next = self.provider.next_event_after(now)
        if provider_next is not None:
            candidates.append(provider_next)
        for node in self.nodes.values():
            if node.state == NodeState.BOOTING:
                candidates.append(node.launched_at + self.config.boot_timeout_s)
            elif node.state == NodeState.READY and node.idle_since is not None:
                candidates.append(self._reap_deadline(node))
        has_waiting = False
        for job in self._open.values():
            if job.planned_end is not None:
                candidates.append(job.planned_end)
            elif job.status == JobStatus.QUEUED and job.node_id is None:
                has_waiting = True
                if job.priority == Priority.LOW and not job.pinned_on_demand:
                    candidates.append(job.submitted_at + self.config.low_priority_aging_s)
        if has_waiting:
            candidates.extend(b.until for b in self._backoff.values())
        future = [t for t in candidates if t > now + EPS]
        return min(future) if future else None

    # ---- node lifecycle ----------------------------------------------------

    def _sync_nodes(self) -> None:
        """Pull node state from the provider and react to changes."""
        for node in list(self.nodes.values()):
            if node.state not in (NodeState.BOOTING, NodeState.READY, NodeState.DRAINING):
                continue
            if node.provider_id is None:  # booting or later always has one; be defensive
                self._node_gone(node, self.now, "unknown_to_provider")
                continue
            try:
                status = self.provider.describe(node.provider_id, self.now)
            except KeyError:
                self._node_gone(node, self.now, "unknown_to_provider")
                continue
            if status.status == "terminated":
                self._node_gone(node, status.terminated_at or self.now, status.termination_reason)
            elif status.status == "pending":
                if self.now >= node.launched_at + self.config.boot_timeout_s - EPS:
                    self._terminate(node, "boot_timeout")
            else:
                if node.state == NodeState.BOOTING:
                    node.state = NodeState.READY
                    node.ready_at = status.ready_at
                    self._touch(node)
                    for job in self.jobs_on(node.id):
                        self._start(job, node)
                if status.status == "reclaim_notice" and node.state != NodeState.DRAINING:
                    node.state = NodeState.DRAINING
                    self._touch(node)

    def _launch(self, team: str, type_name: str, interruptible: bool) -> Node | None:
        self._seq["node"] += 1
        node = Node(
            id=f"node-{self._seq['node']:06d}",
            team=team,
            type=type_name,
            interruptible=interruptible,
            price_per_hour=node_price(type_name, interruptible),
            state=NodeState.LAUNCHING,
            launched_at=self.now,
        )
        self.nodes[node.id] = node
        self._touch(node)
        # Make the intent durable before the provider call. If we crash after
        # the provider launched the node, reconciliation adopts it by token.
        self._commit()
        key = (type_name, interruptible)
        try:
            node.provider_id = self.provider.launch_node(
                type_name, self.now, token=node.id, interruptible=interruptible
            )
        except (CapacityUnavailable, QuotaExceeded):
            self.launch_failures += 1
            log.warning("launch failed: %s%s for %s", type_name, " (interruptible)" if interruptible else "", team)
            del self.nodes[node.id]
            self._dirty.discard(("node", node.id))
            self.store.delete("node", node.id)
            backoff = self._backoff.setdefault(key, _Backoff())
            backoff.failures += 1
            delay = min(self.config.backoff_base_s * 2 ** (backoff.failures - 1), self.config.backoff_max_s)
            backoff.until = self.now + delay
            return None
        self._backoff.pop(key, None)
        node.state = NodeState.BOOTING
        self._touch(node)
        log.info("launched %s %s for %s", node.id, type_name, team)
        return node

    def _terminate(self, node: Node, reason: str) -> None:
        if node.provider_id is not None:
            self.provider.terminate_node(node.provider_id, self.now)
        self._node_gone(node, self.now, reason)

    def _node_gone(self, node: Node, at: float, reason: str | None) -> None:
        node.state = NodeState.TERMINATED
        node.terminated_at = at
        node.termination_reason = reason
        log.info("node %s (%s) ended: %s", node.id, node.team, reason)
        node.idle_since = None
        self._touch(node)
        for job in self.jobs_on(node.id):
            if job.status == JobStatus.RUNNING:
                self._lose_run(job, at, pin=node.interruptible)
            else:
                job.node_id = None  # reservation on a node that never came up
                self._touch(job)

    def _update_idle(self) -> None:
        busy = {j.node_id for j in self._open.values() if j.node_id is not None}
        for node in self.nodes.values():
            if node.state == NodeState.DRAINING and node.id not in busy:
                self._terminate(node, "drained_after_reclaim_notice")
            elif node.state == NodeState.READY:
                if node.id in busy:
                    if node.idle_since is not None:
                        node.idle_since = None
                        self._touch(node)
                elif node.idle_since is None:
                    node.idle_since = self.now
                    self._touch(node)

    def _reap_deadline(self, node: Node) -> float:
        # An idle node younger than the billing minimum is already paid for.
        assert node.idle_since is not None
        return max(node.idle_since + self.config.idle_timeout_s, node.launched_at + self.config.min_billing_s)

    def _reap_idle_nodes(self) -> None:
        for node in list(self.nodes.values()):
            idle = node.state == NodeState.READY and node.idle_since is not None
            if idle and self.now >= self._reap_deadline(node) - EPS:
                self._terminate(node, "idle")

    # ---- job lifecycle -------------------------------------------------------

    def _start(self, job: Job, node: Node) -> None:
        self._close_queue_wait(job)
        job.status = JobStatus.RUNNING
        job.node_id = node.id
        job.started_at = self.now
        job.attempts += 1
        if job.first_started_at is None:
            job.first_started_at = self.now
        run = Run(
            id=f"{job.id}#{job.attempts}",
            job_id=job.id,
            node_id=node.id,
            team=job.team,
            gpus=job.gpus,
            started_at=self.now,
        )
        self.runs[run.id] = run
        self._touch(job)
        self._touch(run)

    def _finish(self, job: Job, status: JobStatus) -> None:
        self._close_run(job, self.now, status.value)
        job.status = status
        job.ended_at = self.now
        job.node_id = None
        self._open.pop(job.id, None)
        self._touch(job)

    def _lose_run(self, job: Job, at: float, pin: bool) -> None:
        """The node under a running job disappeared: put the job back in the queue."""
        self._close_run(job, at, "lost")
        job.status = JobStatus.QUEUED
        job.node_id = None
        job.started_at = None
        job.queued_since = at
        log.warning("job %s lost its node and was requeued", job.id)
        if pin:
            job.pinned_on_demand = True
        self._touch(job)

    def _close_run(self, job: Job, at: float, outcome: str) -> None:
        run = self.runs[f"{job.id}#{job.attempts}"]
        run.ended_at = at
        run.outcome = outcome
        self._touch(run)

    def _close_queue_wait(self, job: Job) -> None:
        if job.queued_since is not None:
            job.queued_seconds += self.now - job.queued_since
            job.queued_since = None

    def _finish_due_jobs(self) -> None:
        for job in list(self._open.values()):
            end = job.planned_end
            if end is not None and self.now >= end - EPS:
                self._finish(job, job.planned_outcome or JobStatus.SUCCEEDED)

    # ======================================================================
    # Scheduling
    # ======================================================================

    def _schedule(self) -> None:
        waiting = [j for j in self._open.values() if j.status == JobStatus.QUEUED and j.node_id is None]
        if not waiting:
            return
        ordered = self._order(waiting)
        free = self._free_gpus()
        self._rebalancing = (
            self._nodes_to_drain_for_slots(ordered, free) if self.config.policy == "fairshare" else set()
        )
        blocked: set[tuple[str, bool]] = set()
        for job in ordered:
            if job.node_id is not None:
                continue  # reserved by a launch earlier in this pass
            node = self._best_fit(job, free)
            if node is not None:
                self._place(job, node, free)
            else:
                self._launch_for(job, ordered, free, blocked)

    def _order(self, jobs: list[Job]) -> list[Job]:
        """Order waiting jobs according to the configured policy."""
        if self.config.policy == "fifo":
            return policy.order_fifo(jobs)
        return policy.order_fair_share(
            jobs, self.usage, self.config.team_weights, self.now, self.config.low_priority_aging_s
        )

    def _spot_eligible(self, job: Job) -> bool:
        return (
            self.config.use_interruptible and not job.pinned_on_demand and self.effective_priority(job) == Priority.LOW
        )

    def _free_gpus(self) -> dict[str, int]:
        free = {n.id: n.gpus for n in self.nodes.values() if n.state in (NodeState.BOOTING, NodeState.READY)}
        for job in self._open.values():
            if job.node_id in free:
                free[job.node_id] -= job.gpus
        return free

    def _best_fit(self, job: Job, free: dict[str, int]) -> Node | None:
        """The team's node with the least free GPUs that still fits the job."""
        spot_ok = self._spot_eligible(job)
        best: Node | None = None
        best_key = None
        for node_id, gpus_free in free.items():
            node = self.nodes[node_id]
            if node.state not in (NodeState.BOOTING, NodeState.READY) or node_id in self._rebalancing:
                continue
            if node.team != job.team or gpus_free < job.gpus:
                continue
            if node.interruptible and not spot_ok:
                continue
            key = (gpus_free, node.state != NodeState.READY, node.id)
            if best_key is None or key < best_key:
                best, best_key = node, key
        return best

    def _place(self, job: Job, node: Node, free: dict[str, int]) -> None:
        free[node.id] -= job.gpus
        job.node_id = node.id
        self._touch(job)
        if node.state == NodeState.READY:
            self._start(job, node)

    def _launch_for(self, job: Job, ordered: list[Job], free: dict[str, int], blocked: set) -> None:
        spot = self._spot_eligible(job)
        if (job.team, spot) in blocked:
            return
        alive = sum(1 for n in self.nodes.values() if n.alive)
        if alive >= self.config.max_nodes:
            if not self._evict_idle_node(free):
                blocked.add((job.team, spot))
                return
            alive -= 1
        if self.config.policy == "fairshare" and self._over_slot_share(job.team, ordered, alive):
            blocked.add((job.team, spot))
            return

        group = [j for j in ordered if j.team == job.team and j.node_id is None and self._spot_eligible(j) == spot]
        booting = sum(
            n.gpus
            for n in self.nodes.values()
            if n.team == job.team and n.interruptible == spot and n.state == NodeState.BOOTING
        )
        need = policy.gpus_to_launch(
            job.gpus,
            sum(j.gpus for j in group),
            booting,
            alive,
            self.config.max_nodes,
            self.config.slot_pressure_fraction,
        )
        for node_type in NODE_TYPES_BY_SIZE:
            if node_type.gpus < need:
                continue
            backoff = self._backoff.get((node_type.name, spot))
            if backoff is not None and backoff.until > self.now:
                continue
            node = self._launch(job.team, node_type.name, spot)
            if node is None:
                continue
            free[node.id] = node.gpus
            self._place(job, node, free)
            for other in group:
                if other is not job and other.node_id is None and free[node.id] >= other.gpus:
                    self._place(other, node, free)
            return
        blocked.add((job.team, spot))

    def _over_slot_share(self, team: str, ordered: list[Job], alive: int) -> bool:
        """While other teams are waiting for slots, a team may not exceed its share of the cap."""
        others_waiting = {j.team for j in ordered if j.node_id is None and j.team != team}
        if not others_waiting or self.config.max_nodes - alive > len(others_waiting):
            return False
        share = policy.slot_share(self.config.max_nodes, len({j.team for j in self._open.values()}))
        team_nodes = sum(1 for n in self.nodes.values() if n.alive and n.team == team)
        return team_nodes >= share

    def _nodes_to_drain_for_slots(self, ordered: list[Job], free: dict[str, int]) -> set[str]:
        """Pick nodes to stop filling so that a team blocked by the node cap gets a slot.

        Nodes are dedicated to one team, so a slot only frees up when a whole
        node empties. A busy team keeps refilling its own nodes and would hold
        every slot for as long as its backlog lasts. For each team that is
        under its slot share and has nowhere to run, take the over-share team's
        node expected to free up soonest (by the jobs' estimates) and stop
        placing new work on it. Nothing running is interrupted.

        Recomputed on every pass from current state, so nothing to persist.
        """
        alive = [n for n in self.nodes.values() if n.alive]
        if len(alive) < self.config.max_nodes:
            return set()
        if any(n.state == NodeState.READY and n.idle_since is not None for n in alive):
            return set()  # an idle node can simply be evicted

        share = policy.slot_share(self.config.max_nodes, len({j.team for j in self._open.values()}))
        node_count: dict[str, int] = {}
        for n in alive:
            node_count[n.team] = node_count.get(n.team, 0) + 1

        starved: list[str] = []
        for job in ordered:
            team = job.team
            if team in starved or node_count.get(team, 0) >= share:
                continue
            fits_own_node = any(self.nodes[nid].team == team and gpus >= job.gpus for nid, gpus in free.items())
            if not fits_own_node:
                starved.append(team)

        drains: set[str] = set()
        for _ in starved:
            donors = [t for t, c in node_count.items() if c > share]
            if not donors:
                break
            donor = max(donors, key=lambda t: (node_count[t], t))
            candidates = [n for n in alive if n.team == donor and n.state == NodeState.READY and n.id not in drains]
            if not candidates:
                break
            node = min(candidates, key=self._expected_free_at)
            drains.add(node.id)
            node_count[donor] -= 1
        return drains

    def _expected_free_at(self, node: Node) -> tuple[float, str]:
        ends = [
            j.started_at + j.estimated_minutes * 60
            for j in self._open.values()
            if j.node_id == node.id and j.status == JobStatus.RUNNING and j.started_at is not None
        ]
        return (max(ends, default=self.now), node.id)

    def _evict_idle_node(self, free: dict[str, int]) -> bool:
        """Under the node cap, an idle node is worth less than a waiting job."""
        idle = [n for n in self.nodes.values() if n.state == NodeState.READY and n.idle_since is not None]
        if not idle:
            return False
        victim = min(idle, key=lambda n: (n.idle_since, n.id))
        self._terminate(victim, "evicted_for_capacity")
        free.pop(victim.id, None)
        return True

    # ======================================================================
    # Fair-share accounting
    # ======================================================================

    def _accrue_usage(self) -> None:
        """Exponentially decayed GPU-seconds per team, integrated exactly between events."""
        dt = self.now - self._usage_at
        if dt <= 0:
            return
        running: dict[str, int] = {}
        for job in self._open.values():
            if job.status == JobStatus.RUNNING:
                running[job.team] = running.get(job.team, 0) + job.gpus
        self.usage = policy.decay_usage(self.usage, running, dt, self.config.fairshare_half_life_s)
        self._usage_at = self.now

    # ======================================================================
    # Persistence and recovery
    # ======================================================================

    def _touch(self, obj: Job | Node | Run) -> None:
        kind = {Job: "job", Node: "node", Run: "run"}[type(obj)]
        self._dirty.add((kind, obj.id))

    def _commit(self) -> None:
        tables: dict[str, Mapping[str, Job | Node | Run]] = {"job": self.jobs, "node": self.nodes, "run": self.runs}
        for kind, obj_id in self._dirty:
            obj = tables[kind].get(obj_id)
            if obj is not None:
                self.store.put(kind, obj_id, obj.to_dict())
        self._dirty.clear()
        self.store.set_kv("now", self.now)
        self.store.set_kv("seq", self._seq)
        self.store.set_kv("usage", self.usage)
        self.store.set_kv("usage_at", self._usage_at)
        self.store.set_kv("launch_failures", self.launch_failures)
        self.store.commit()

    def _reconcile(self) -> None:
        """Make broker state agree with the provider after a (re)start."""
        provider_nodes = {s.token: s for s in self.provider.list_nodes(self.now)}
        for node in list(self.nodes.values()):
            if node.state != NodeState.LAUNCHING:
                continue
            status = provider_nodes.get(node.id)
            if status is None:
                # The launch never reached the provider.
                log.info("recovery: dropping launch intent %s, provider never saw it", node.id)
                del self.nodes[node.id]
                self.store.delete("node", node.id)
            else:
                log.info("recovery: adopting %s launched before the restart", node.id)
                node.provider_id = status.id
                node.state = NodeState.BOOTING
                self._touch(node)
        known = set(self.nodes)
        for token, status in provider_nodes.items():
            if token not in known and status.status != "terminated":
                # Alive at the provider but unknown to us: it can only cost money.
                log.warning("recovery: terminating orphan node %s", status.id)
                self.provider.terminate_node(status.id, self.now)
        self._process()

    # ======================================================================
    # Validation
    # ======================================================================

    @staticmethod
    def _validate_submission(team: str, gpus: int, estimated_minutes: float, priority: str) -> None:
        if not isinstance(team, str) or not TEAM_RE.match(team):
            raise ValidationError("team must be 1-64 lowercase letters, digits, '-' or '_'")
        if isinstance(gpus, bool) or gpus not in ALLOWED_GPUS:
            raise ValidationError(f"gpus must be one of {list(ALLOWED_GPUS)}")
        if not isinstance(estimated_minutes, (int, float)) or not 0 < estimated_minutes <= MAX_ESTIMATE_MINUTES:
            raise ValidationError(f"estimated_minutes must be in (0, {MAX_ESTIMATE_MINUTES}]")
        if priority not in {p.value for p in Priority}:
            raise ValidationError("priority must be low, normal or high")
