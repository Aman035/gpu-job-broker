"""Cost and GPU-hour report per team for a time window.

Every node second belongs to the team that owns the node (nodes are never
shared between teams). A team's cost is split into:

- used: the node's price multiplied by the share of its GPUs that the team's
  running jobs occupied, over time;
- idle: everything else on that team's nodes (boot time, empty time before the
  reaper, unused GPUs on a partly filled node, failed launches and the 10-minute
  minimum-billing top-up).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Node, Run

HOUR = 3600.0


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


@dataclass
class TeamCost:
    team: str
    gpu_hours: float = 0.0
    node_hours: float = 0.0
    used_cost: float = 0.0
    idle_cost: float = 0.0
    nodes_launched: int = 0

    @property
    def total_cost(self) -> float:
        return self.used_cost + self.idle_cost

    @property
    def idle_share(self) -> float:
        return self.idle_cost / self.total_cost if self.total_cost else 0.0

    def to_dict(self) -> dict:
        return {
            "team": self.team,
            "gpu_hours": round(self.gpu_hours, 4),
            "node_hours": round(self.node_hours, 4),
            "used_cost": round(self.used_cost, 4),
            "idle_cost": round(self.idle_cost, 4),
            "total_cost": round(self.total_cost, 4),
            "idle_share": round(self.idle_share, 4),
            "nodes_launched": self.nodes_launched,
        }


@dataclass
class CostReport:
    start: float
    end: float
    teams: dict[str, TeamCost] = field(default_factory=dict)

    @property
    def total_cost(self) -> float:
        return sum(t.total_cost for t in self.teams.values())

    @property
    def idle_cost(self) -> float:
        return sum(t.idle_cost for t in self.teams.values())

    def to_dict(self) -> dict:
        total = self.total_cost
        return {
            "start": self.start,
            "end": self.end,
            "total_cost": round(total, 4),
            "idle_cost": round(self.idle_cost, 4),
            "idle_share": round(self.idle_cost / total, 4) if total else 0.0,
            "teams": [self.teams[t].to_dict() for t in sorted(self.teams)],
        }


def cost_report(
    nodes: list[Node],
    runs: list[Run],
    start: float,
    end: float,
    now: float,
    min_billing_s: float = 600.0,
) -> CostReport:
    if end < start:
        raise ValueError("end must not be before start")
    report = CostReport(start=start, end=end)

    def team(name: str) -> TeamCost:
        return report.teams.setdefault(name, TeamCost(team=name))

    runs_by_node: dict[str, list[Run]] = {}
    for run in runs:
        runs_by_node.setdefault(run.node_id, []).append(run)
        seconds = _overlap(run.started_at, run.ended_at if run.ended_at is not None else now, start, end)
        team(run.team).gpu_hours += run.gpus * seconds / HOUR

    for node in nodes:
        if node.provider_id is None:
            continue  # the launch never reached the provider, nothing billed
        node_end = node.terminated_at if node.terminated_at is not None else now
        seconds = _overlap(node.launched_at, node_end, start, end)
        cost = node.price_per_hour * seconds / HOUR
        # The minimum-billing top-up is charged when the node ends.
        if node.terminated_at is not None and start <= node.terminated_at < end:
            cost += node.price_per_hour * max(0.0, min_billing_s - (node_end - node.launched_at)) / HOUR
        used_gpu_seconds = sum(
            r.gpus * _overlap(r.started_at, r.ended_at if r.ended_at is not None else now, start, end)
            for r in runs_by_node.get(node.id, [])
        )
        used = node.price_per_hour * used_gpu_seconds / node.gpus / HOUR
        t = team(node.team)
        t.node_hours += seconds / HOUR
        t.used_cost += used
        t.idle_cost += cost - used
        if start <= node.launched_at < end:
            t.nodes_launched += 1
    return report
