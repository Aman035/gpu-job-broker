"""Evaluate failure retrieval and measure what each pipeline step contributes.

    broker-eval-retrieval
    broker-eval-retrieval --out docs/RETRIEVAL_RESULTS.md

Four test sets, from least to most independent of the pipeline:
- given (01c): the 24 labelled questions provided. The glossary was written
  after reading them, so this score is optimistic.
- own held-out (data/holdout_queries.jsonl): 24 paraphrases I wrote before the
  pipeline. Not used for scoring decisions, but the glossary was written with
  them in view, so they are not independent either.
- blind (data/blind_queries.jsonl): 24 questions written by a separate agent
  that saw only the brief and the 59 logs, never the code, glossary or other
  questions. Its first score was recorded before any change to the pipeline.
- log -> log: every log used as the query (leave-one-out), for the "paste a
  failed job's log" use. Relevant = other logs of the same cause.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from ..paths import data_dir
from .index import DEFAULT_LOGS, FailureIndex, SearchOptions

DATA = data_dir()


@dataclass
class Query:
    qid: str
    text: str
    expected: set[str]


def load_queries(path: Path) -> list[Query]:
    out = []
    with open(path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                out.append(Query(d["qid"], d["query"], set(d["expected_ids"])))
    return out


def precision_at(ids: list[str], expected: set[str], k: int) -> float:
    """Share of the top k that is relevant. Returning fewer than k counts the gap as misses."""
    return sum(1 for i in ids[:k] if i in expected) / k


@dataclass
class Scores:
    p3: float
    p5: float
    abstain_ok: int  # questions with nothing relevant that correctly got no results
    abstain_total: int
    false_abstain: int  # questions with relevant logs that got no results

    def cell(self) -> str:
        """P@3 / P@5 / unanswerable correctly refused (answerable wrongly refused)."""
        return f"{self.p3:.2f} / {self.p5:.2f} / {self.abstain_ok} of {self.abstain_total} ({self.false_abstain})"


def score(index: FailureIndex, queries: list[Query]) -> Scores:
    p3 = p5 = 0.0
    relevant = [q for q in queries if q.expected]
    abstain_ok = false_abstain = 0
    for q in queries:
        result = index.search(q.text, k=5)
        ids = [m.id for m in result.matches]
        if q.expected:
            p3 += precision_at(ids, q.expected, 3)
            p5 += precision_at(ids, q.expected, 5)
            false_abstain += result.abstained
        else:
            abstain_ok += result.abstained
    n = max(1, len(relevant))
    return Scores(p3 / n, p5 / n, abstain_ok, len(queries) - len(relevant), false_abstain)


def cause_groups(query_sets: list[list[Query]]) -> list[set[str]]:
    groups: list[set[str]] = []
    for queries in query_sets:
        for q in queries:
            if q.expected and q.expected not in groups:
                groups.append(q.expected)
    return groups


def leave_one_out(index: FailureIndex, groups: list[set[str]]) -> tuple[float, float]:
    """Use each log as the query; exclude itself from the results."""
    p3 = p5 = 0.0
    n = 0
    for entry in index.logs:
        relevant = set().union(*(g for g in groups if entry.id in g)) - {entry.id}
        if not relevant:
            continue
        result = index.search(entry.log, k=6)
        ids = [m.id for m in result.matches if m.id != entry.id]
        p3 += precision_at(ids, relevant, 3)
        p5 += precision_at(ids, relevant, 5)
        n += 1
    return p3 / n, p5 / n


def ablation() -> list[tuple[str, SearchOptions]]:
    base = SearchOptions(
        filter_lines=False, expand_query=False, index_resolution=False, group_by_fix=False, abstain=False
    )
    steps = [
        ("BM25 on the raw log", base),
        ("+ keep signal lines only", replace(base, filter_lines=True)),
        ("+ index the resolution text", replace(base, filter_lines=True, index_resolution=True)),
        ("+ glossary query expansion", replace(base, filter_lines=True, index_resolution=True, expand_query=True)),
        (
            "+ rank by shared fix",
            replace(base, filter_lines=True, index_resolution=True, expand_query=True, group_by_fix=True),
        ),
        ("+ abstain unless a glossary concept is hit (v1)", SearchOptions(abstain_unless_failure=False)),
        ("+ abstain unless the question describes a failure (final)", SearchOptions()),
    ]
    return steps


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate failure retrieval")
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--queries", type=Path, default=DATA / "01c_retrieval_queries.jsonl")
    parser.add_argument("--holdout", type=Path, default=DATA / "holdout_queries.jsonl")
    parser.add_argument("--blind", type=Path, default=DATA / "blind_queries.jsonl")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    sets = [
        ("given", load_queries(args.queries)),
        ("own held-out", load_queries(args.holdout)),
        ("blind", load_queries(args.blind)),
    ]
    groups = cause_groups([queries for _, queries in sets])

    lines = [
        "# Retrieval evaluation",
        "",
        "Each cell: P@3 / P@5 / unanswerable questions correctly refused (answerable questions wrongly refused).",
        "P@k is the share of the top k results whose id is relevant, averaged over questions that have relevant logs.",
        "Sets run from least to most independent of the pipeline; see `src/broker/retrieval/evaluate.py`.",
        "",
        "| Pipeline | Given | Own held-out | Blind | Log→log P@3 / P@5 |",
        "|---|---|---|---|---|",
    ]
    for label, options in ablation():
        index = FailureIndex.from_jsonl(args.logs, options)
        cells = [score(index, queries).cell() for _, queries in sets]
        l3, l5 = leave_one_out(index, groups)
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {l3:.2f} / {l5:.2f} |")

    final = FailureIndex.from_jsonl(args.logs)
    lines += [
        "",
        "## Per question (final pipeline)",
        "",
        "| Set | Id | Question | P@5 | Top 5 |",
        "|---|---|---|---|---|",
    ]
    for name, queries in sets:
        for q in queries:
            result = final.search(q.text, k=5)
            ids = [m.id for m in result.matches]
            p5 = f"{precision_at(ids, q.expected, 5):.1f}" if q.expected else ("ok" if result.abstained else "MISS")
            shown = ", ".join(i if i in q.expected or not q.expected else f"~~{i}~~" for i in ids) or "(abstained)"
            lines.append(f"| {name} | {q.qid} | {q.text} | {p5} | {shown} |")

    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.out:
        args.out.write_text(text)


if __name__ == "__main__":
    main()
