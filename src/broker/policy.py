"""Scheduling policy as pure functions.

The broker (`service.py`) owns state and side effects; the decisions about
*who goes next* and *how big a node to launch* live here, so they can be read,
tested and changed without the event loop. Reasoning: docs/DECISIONS.md.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from .models import NODE_TYPES_BY_SIZE, PRIORITY_RANK, Job, Priority

LARGEST_NODE_GPUS = NODE_TYPES_BY_SIZE[-1].gpus


def effective_priority(job: Job, now: float, low_aging_s: float) -> Priority:
    """`low` becomes `normal` after waiting long enough, or after losing a run to a reclaim."""
    if job.priority == Priority.LOW and (job.pinned_on_demand or now - job.submitted_at >= low_aging_s):
        return Priority.NORMAL
    return job.priority


def order_fifo(jobs: Iterable[Job]) -> list[Job]:
    """Baseline: global priority, then arrival."""
    return sorted(jobs, key=lambda j: (-PRIORITY_RANK[j.priority], j.submitted_at, j.seq))


def order_fair_share(
    jobs: Iterable[Job],
    usage: Mapping[str, float],
    weights: Mapping[str, float],
    now: float,
    low_aging_s: float,
) -> list[Job]:
    """Teams take turns, least recent (weighted) usage first; priority orders jobs within a team."""
    by_team: dict[str, list[Job]] = {}
    for job in jobs:
        by_team.setdefault(job.team, []).append(job)
    for team_jobs in by_team.values():
        team_jobs.sort(key=lambda j: (-PRIORITY_RANK[effective_priority(j, now, low_aging_s)], j.submitted_at, j.seq))
    teams = sorted(by_team, key=lambda t: (usage.get(t, 0.0) / weights.get(t, 1.0), t))
    ordered: list[Job] = []
    for depth in range(max((len(v) for v in by_team.values()), default=0)):
        for team in teams:
            if depth < len(by_team[team]):
                ordered.append(by_team[team][depth])
    return ordered


def decay_usage(
    usage: Mapping[str, float],
    running_gpus: Mapping[str, int],
    dt: float,
    half_life_s: float,
) -> dict[str, float]:
    """Exponentially decayed GPU-seconds, integrated exactly over an interval with constant load."""
    if dt <= 0:
        return dict(usage)
    decay = 0.5 ** (dt / half_life_s)
    gain = half_life_s / math.log(2) * (1 - decay)  # integral of the decay kernel over dt
    return {
        team: usage.get(team, 0.0) * decay + running_gpus.get(team, 0) * gain for team in set(usage) | set(running_gpus)
    }


def slot_share(max_nodes: int, teams_with_work: int) -> int:
    """Each active team's fair share of node slots."""
    return math.ceil(max_nodes / max(1, teams_with_work))


def gpus_to_launch(
    job_gpus: int,
    waiting_gpus: int,
    booting_gpus: int,
    alive_nodes: int,
    max_nodes: int,
    slot_pressure_fraction: float | None,
) -> int:
    """Minimum GPUs the next node should have.

    Covers the team's waiting demand plus what it already has booting (jobs
    arrive one at a time, so booting nodes signal a burst: 1 -> 4 -> 8),
    capped at the largest node. Optionally, once slots are scarce, always
    the largest node.
    """
    need = max(job_gpus, min(waiting_gpus + booting_gpus, LARGEST_NODE_GPUS))
    if slot_pressure_fraction is not None and alive_nodes >= max_nodes * slot_pressure_fraction:
        need = LARGEST_NODE_GPUS
    return need
