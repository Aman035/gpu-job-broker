"""Fake cloud provider.

It behaves like an external system: it has its own store, it keeps billing
nodes while the broker is down, and it only knows time through the `now`
argument the caller passes (the simulated clock).

Every random outcome is derived from a seeded hash of a stable key (the
launch token, or a time bucket), never from a shared RNG stream. That makes
runs reproducible and means there is no RNG state to lose on restart.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

from .models import NODE_TYPES, node_price
from .store import Store

MINUTE = 60.0
HOUR = 3600.0


class ProviderError(Exception):
    pass


class CapacityUnavailable(ProviderError):
    """The provider has no capacity of the requested type right now."""


class QuotaExceeded(ProviderError):
    """The account already has the maximum number of nodes alive."""


@dataclass
class ProviderConfig:
    seed: int = 7
    max_alive: int = 10
    boot_min_s: float = 4 * MINUTE
    boot_max_s: float = 8 * MINUTE
    never_ready_prob: float = 0.03
    never_ready_fail_after_s: float = 15 * MINUTE
    reclaim_notice_s: float = 2 * MINUTE
    reclaim_rate_per_node_hour: float = 0.05
    # Capacity outages are what make failures bursty: inside an outage most
    # launches of that pool fail; outside one, few do.
    baseline_failure_prob: float = 0.02
    outage_failure_prob: float = 0.9
    outage_start_prob_per_hour: float = 0.06
    interruptible_outage_start_prob_per_hour: float = 0.12
    outage_mean_duration_s: float = 40 * MINUTE


@dataclass
class ProviderNode:
    id: str
    token: str
    type: str
    interruptible: bool
    price_per_hour: float
    launched_at: float
    ready_at: float | None  # None: this node never becomes ready
    fail_at: float | None
    notice_at: float | None  # interruptible only
    reclaim_at: float | None
    terminated_at: float | None = None
    termination_reason: str | None = None


@dataclass
class NodeStatus:
    id: str
    token: str
    status: str  # pending | running | reclaim_notice | terminated
    launched_at: float
    ready_at: float | None
    terminated_at: float | None
    termination_reason: str | None


class FakeProvider:
    def __init__(self, config: ProviderConfig | None = None, store: Store | None = None) -> None:
        self.config = config or ProviderConfig()
        self.store = store or Store()
        self._nodes: dict[str, ProviderNode] = {
            d["id"]: ProviderNode(**d) for d in self.store.all("pnode")
        }

    # ---- deterministic randomness ---------------------------------------

    def _rng(self, *key: object) -> random.Random:
        return random.Random(":".join(str(k) for k in (self.config.seed, *key)))

    def in_outage(self, type_name: str, interruptible: bool, now: float) -> bool:
        """True if the (type, interruptible) pool is inside a capacity outage.

        Each hour bucket may start one outage at a random offset with an
        exponential duration. Looking back a few buckets catches outages that
        started earlier and are still going.
        """
        c = self.config
        p = c.interruptible_outage_start_prob_per_hour if interruptible else c.outage_start_prob_per_hour
        bucket = int(now // HOUR)
        for b in range(bucket - 4, bucket + 1):
            rng = self._rng("outage", type_name, interruptible, b)
            if rng.random() >= p:
                continue
            start = b * HOUR + rng.random() * HOUR
            duration = rng.expovariate(1.0 / c.outage_mean_duration_s)
            if start <= now < start + duration:
                return True
        return False

    # ---- public API --------------------------------------------------------

    def launch_node(self, type_name: str, now: float, *, token: str, interruptible: bool = False) -> str:
        """Launch a node. Idempotent on `token`: a retry returns the same node."""
        if type_name not in NODE_TYPES:
            raise ValueError(f"unknown node type {type_name!r}")
        for node in self._nodes.values():
            if node.token == token:
                return node.id

        if sum(1 for n in self._nodes.values() if self._alive(n, now)) >= self.config.max_alive:
            raise QuotaExceeded(f"at most {self.config.max_alive} nodes may be alive")

        c = self.config
        rng = self._rng("launch", token)
        fail_prob = c.outage_failure_prob if self.in_outage(type_name, interruptible, now) else c.baseline_failure_prob
        if rng.random() < fail_prob:
            raise CapacityUnavailable(f"no {'interruptible ' if interruptible else ''}{type_name} capacity")

        ready_at = fail_at = notice_at = reclaim_at = None
        if rng.random() < c.never_ready_prob:
            fail_at = now + c.never_ready_fail_after_s
        else:
            ready_at = now + rng.uniform(c.boot_min_s, c.boot_max_s)
            if interruptible:
                hours_until_notice = rng.expovariate(c.reclaim_rate_per_node_hour)
                notice_at = ready_at + hours_until_notice * HOUR
                reclaim_at = notice_at + c.reclaim_notice_s

        node = ProviderNode(
            id=f"i-{len(self._nodes) + 1:05d}",
            token=token,
            type=type_name,
            interruptible=interruptible,
            price_per_hour=node_price(type_name, interruptible),
            launched_at=now,
            ready_at=ready_at,
            fail_at=fail_at,
            notice_at=notice_at,
            reclaim_at=reclaim_at,
        )
        self._nodes[node.id] = node
        self._save(node)
        return node.id

    def terminate_node(self, node_id: str, now: float) -> None:
        node = self._nodes[node_id]
        self._settle(node, now)
        if node.terminated_at is None:
            node.terminated_at = now
            node.termination_reason = "terminated_by_client"
            self._save(node)

    def describe(self, node_id: str, now: float) -> NodeStatus:
        node = self._nodes[node_id]
        self._settle(node, now)
        if node.terminated_at is not None:
            status = "terminated"
        elif node.ready_at is None or now < node.ready_at:
            status = "pending"
        elif node.notice_at is not None and now >= node.notice_at:
            status = "reclaim_notice"
        else:
            status = "running"
        return NodeStatus(
            id=node.id,
            token=node.token,
            status=status,
            launched_at=node.launched_at,
            ready_at=node.ready_at if node.ready_at is not None and now >= node.ready_at else None,
            terminated_at=node.terminated_at,
            termination_reason=node.termination_reason,
        )

    def list_nodes(self, now: float) -> list[NodeStatus]:
        return [self.describe(node_id, now) for node_id in self._nodes]

    def next_event_after(self, now: float) -> float | None:
        """Earliest future time at which some live node changes state."""
        times = []
        for node in self._nodes.values():
            if node.terminated_at is not None:
                continue
            for t in (node.ready_at, node.fail_at, node.notice_at, node.reclaim_at):
                if t is not None and t > now:
                    times.append(t)
        return min(times) if times else None

    def billed_cost(self, now: float, min_billing_s: float = 10 * MINUTE) -> float:
        """What the provider would invoice: per second, 10-minute minimum."""
        total = 0.0
        for node in self._nodes.values():
            self._settle(node, now)
            end = node.terminated_at if node.terminated_at is not None else now
            seconds = end - node.launched_at
            if node.terminated_at is not None:
                seconds = max(seconds, min_billing_s)
            total += node.price_per_hour * seconds / HOUR
        return total

    # ---- internals -----------------------------------------------------------

    def _alive(self, node: ProviderNode, now: float) -> bool:
        self._settle(node, now)
        return node.terminated_at is None

    def _settle(self, node: ProviderNode, now: float) -> None:
        """Apply provider-side terminations (boot failure, reclaim) that are due."""
        if node.terminated_at is not None:
            return
        if node.fail_at is not None and now >= node.fail_at:
            node.terminated_at, node.termination_reason = node.fail_at, "never_ready"
            self._save(node)
        elif node.reclaim_at is not None and now >= node.reclaim_at:
            node.terminated_at, node.termination_reason = node.reclaim_at, "reclaimed"
            self._save(node)

    def _save(self, node: ProviderNode) -> None:
        self.store.put("pnode", node.id, asdict(node))
        self.store.commit()

