"""Replay the submission trace through the simulator and compare policies.

broker-replay                      # fair share vs FIFO, plus sensitivity runs
broker-replay --out docs/RESULTS.md
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import MINUTE, BrokerConfig
from ..models import JobStatus
from ..service import Broker, ValidationError
from ..store import Store
from .fake_provider import FakeProvider, ProviderConfig

DEFAULT_TRACE = Path(__file__).resolve().parents[3] / "data" / "01a_trace.csv"


@dataclass
class TraceRow:
    line: int
    submitted_at: float
    team: str
    gpus: int
    estimated_minutes: int
    actual_minutes: int
    priority: str
    outcome: str
    raw: tuple

    @property
    def key(self) -> str:
        """Content hash used as the idempotency key: identical rows are one submission."""
        return hashlib.sha256("|".join(self.raw).encode()).hexdigest()[:16]


@dataclass
class TraceLoad:
    rows: list[TraceRow]
    duplicates: int = 0
    rejected: list[tuple[int, str]] = field(default_factory=list)


def load_trace(path: Path, keep_duplicates: bool = False) -> TraceLoad:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        raw_rows = list(enumerate(reader, start=2))
    rows: list[TraceRow] = []
    for line, r in raw_rows:
        rows.append(
            TraceRow(
                line=line,
                submitted_at=datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00")).timestamp(),
                team=r["team"],
                gpus=int(r["gpus"]),
                estimated_minutes=int(r["estimated_minutes"]),
                actual_minutes=int(r["actual_minutes"]),
                priority=r["priority"],
                outcome=r["outcome"],
                raw=tuple(r.values()),
            )
        )
    rows.sort(key=lambda r: (r.submitted_at, r.line))  # the file is not in time order
    load = TraceLoad(rows=rows)
    if not keep_duplicates:
        seen: set[str] = set()
        kept = []
        for row in rows:
            if row.key in seen:
                load.duplicates += 1
                continue
            seen.add(row.key)
            kept.append(row)
        load.rows = kept
    return load


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class ReplayResult:
    label: str
    broker: Broker
    rejected: list[tuple[int, str]]
    duplicates: int

    def waits_by_team(self) -> dict[str, list[float]]:
        waits: dict[str, list[float]] = {}
        for job in self.broker.jobs.values():
            waits.setdefault(job.team, []).append(job.queued_seconds / MINUTE)
        return waits

    def all_waits(self) -> list[float]:
        return [w for ws in self.waits_by_team().values() for w in ws]

    def started_within(self, minutes: float) -> float:
        jobs = list(self.broker.jobs.values())
        ok = sum(
            1
            for j in jobs
            if j.first_started_at is not None and j.first_started_at - j.submitted_at <= minutes * MINUTE
        )
        return ok / len(jobs)

    def summary(self) -> dict:
        report = self.broker.cost_report()
        reasons: dict[str, int] = {}
        for node in self.broker.nodes.values():
            reasons[node.termination_reason or "alive"] = reasons.get(node.termination_reason or "alive", 0) + 1
        waits = self.all_waits()
        return {
            "label": self.label,
            "jobs": len(self.broker.jobs),
            "p50": percentile(waits, 0.5),
            "p90": percentile(waits, 0.9),
            "p99": percentile(waits, 0.99),
            "within_10": self.started_within(10),
            "total_cost": report.total_cost,
            "idle_share": report.idle_cost / report.total_cost if report.total_cost else 0.0,
            "nodes": len(self.broker.nodes),
            "reclaims": reasons.get("reclaimed", 0),
            "lost_runs": sum(1 for r in self.broker.runs.values() if r.outcome == "lost"),
            "launch_failures": self.broker.launch_failures,
            "boot_timeouts": reasons.get("boot_timeout", 0),
            "never_ready": reasons.get("never_ready", 0),
        }


def replay(
    trace: TraceLoad,
    config: BrokerConfig,
    provider_config: ProviderConfig | None = None,
    label: str = "",
) -> ReplayResult:
    rows = trace.rows
    provider = FakeProvider(provider_config or ProviderConfig())
    broker = Broker(provider, Store(), config, start_time=rows[0].submitted_at)
    rejected = list(trace.rejected)
    for row in rows:
        broker.advance_to(row.submitted_at)
        try:
            broker.submit(
                row.team,
                row.gpus,
                row.estimated_minutes,
                row.priority,
                idempotency_key=row.key,
                actual_minutes=row.actual_minutes,
                outcome=row.outcome,
            )
        except ValidationError as exc:
            rejected.append((row.line, str(exc)))
    broker.run_until_idle()
    unfinished = [
        j.id
        for j in broker.jobs.values()
        if j.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)
    ]
    if unfinished:
        raise RuntimeError(f"replay ended with unfinished jobs: {unfinished[:5]}")
    return ReplayResult(label=label or config.policy, broker=broker, rejected=rejected, duplicates=trace.duplicates)


# ---- reporting ------------------------------------------------------------------
#
# A single provider seed is noisy (p90 wait moved by ~40 minutes between seeds),
# so every row below is a mean over several seeds. Per-team percentiles pool
# the jobs of all seeds.


@dataclass
class Scenario:
    label: str
    runs: list[ReplayResult]

    def mean(self, key: str) -> float:
        return statistics.mean(r.summary()[key] for r in self.runs)

    def stdev(self, key: str) -> float:
        values = [r.summary()[key] for r in self.runs]
        return statistics.stdev(values) if len(values) > 1 else 0.0

    def pooled_waits(self, team: str | None = None) -> list[float]:
        return [w for r in self.runs for t, ws in r.waits_by_team().items() if team is None or t == team for w in ws]

    def team_cost(self, team: str) -> tuple[float, float]:
        costs = [r.broker.cost_report().teams[team] for r in self.runs if team in r.broker.cost_report().teams]
        total = statistics.mean(c.total_cost for c in costs)
        idle = statistics.mean(c.idle_share for c in costs)
        return total, idle


def run_scenario(trace: TraceLoad, label: str, config: BrokerConfig, seeds: list[int]) -> Scenario:
    return Scenario(label, [replay(trace, config, ProviderConfig(seed=s), label) for s in seeds])


def _fmt(x: float, digits: int = 1) -> str:
    return f"{x:,.{digits}f}"


def summary_table(scenarios: list[Scenario]) -> str:
    head = "| Run | p50 wait | p90 wait | p99 wait | Started ≤10 min | Total cost (± sd) | Idle share | Lost runs |"
    lines = [head, "|---|---|---|---|---|---|---|---|"]
    for sc in scenarios:
        lines.append(
            f"| {sc.label} | {_fmt(sc.mean('p50'))} | {_fmt(sc.mean('p90'))} | {_fmt(sc.mean('p99'))} "
            f"| {sc.mean('within_10'):.0%} | {_fmt(sc.mean('total_cost'), 0)} (± {_fmt(sc.stdev('total_cost'), 0)}) "
            f"| {sc.mean('idle_share'):.0%} | {_fmt(sc.mean('lost_runs'))} |"
        )
    return "\n".join(lines)


def team_table(scenarios: list[Scenario]) -> str:
    teams = sorted({t for sc in scenarios for r in sc.runs for t in r.waits_by_team()})
    head = "| Team | Jobs | " + " | ".join(f"{sc.label}: p50 / p90 / p99 wait" for sc in scenarios)
    head += " | " + " | ".join(f"{sc.label}: cost (idle)" for sc in scenarios) + " |"
    lines = [head, "|" + "---|" * (2 + 2 * len(scenarios))]
    for team in teams:
        jobs = len(scenarios[0].runs[0].waits_by_team().get(team, []))
        cells = [team, str(jobs)]
        for sc in scenarios:
            w = sc.pooled_waits(team)
            cells.append(f"{_fmt(percentile(w, 0.5))} / {_fmt(percentile(w, 0.9))} / {_fmt(percentile(w, 0.99))}")
        for sc in scenarios:
            total, idle = sc.team_cost(team)
            cells.append(f"{_fmt(total, 0)} ({idle:.0%})")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Replay the trace and compare scheduling policies")
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--seeds", type=int, default=5, help="provider seeds to average over (1..N)")
    parser.add_argument("--keep-duplicates", action="store_true", help="do not collapse identical rows")
    parser.add_argument("--quick", action="store_true", help="skip the sensitivity runs")
    parser.add_argument("--out", type=Path, help="also write the report to this Markdown file")
    args = parser.parse_args(argv)

    trace = load_trace(args.trace, keep_duplicates=args.keep_duplicates)
    seeds = list(range(1, args.seeds + 1))

    fair = run_scenario(trace, "fair share", BrokerConfig(policy="fairshare"), seeds)
    fifo = run_scenario(trace, "FIFO", BrokerConfig(policy="fifo"), seeds)
    rejected = fair.runs[0].rejected
    out = [
        "# Trace replay",
        "",
        f"`{args.trace.name}`: {len(trace.rows) + trace.duplicates} rows, {trace.duplicates} identical rows "
        f"collapsed as client retries, {len(rejected)} rejected, "
        f"{len(trace.rows) - len(rejected)} jobs replayed. Every figure is a mean over provider "
        f"seeds {seeds[0]}-{seeds[-1]}.",
        "",
        *[f"- Rejected line {line}: {why}" for line, why in rejected],
        "",
        "Wait = minutes a job spent queued, summed over attempts. Costs in provider currency for the week.",
        "",
        "## Policy comparison",
        "",
        summary_table([fair, fifo]),
        "",
        "## Per team",
        "",
        team_table([fair, fifo]),
    ]

    if not args.quick:
        idle = [
            run_scenario(trace, f"idle timeout {m} min", BrokerConfig(idle_timeout_s=m * MINUTE), seeds)
            for m in (0, 5, 10, 20, 30)
        ]
        spot = [fair, run_scenario(trace, "no interruptible", BrokerConfig(use_interruptible=False), seeds)]
        pressure = [fair] + [
            run_scenario(trace, f"large nodes at {int(f * 10)}/10 slots", BrokerConfig(slot_pressure_fraction=f), seeds)
            for f in (0.8, 0.5)
        ]
        out += [
            "",
            "## Sensitivity: idle timeout",
            "",
            summary_table(idle),
            "",
            "## Sensitivity: interruptible nodes for low priority",
            "",
            summary_table(spot),
            "",
            "## Sensitivity: node size under slot pressure",
            "",
            summary_table(pressure),
        ]

    text = "\n".join(out) + "\n"
    sys.stdout.write(text)
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()
