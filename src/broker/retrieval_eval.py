"""Evaluate failure retrieval and measure what each pipeline step contributes.

    broker-eval-retrieval
    broker-eval-retrieval --out docs/RETRIEVAL_RESULTS.md

Three test sets:
- the 24 labelled questions provided (01c);
- 24 held-out paraphrases written before the pipeline was built and never used
  for tuning (data/holdout_queries.jsonl), as a check on unseen questions;
- every log used as the query (leave-one-out), for the "paste a failed job's
  log" use. A log's relevant set is the other logs that share its cause.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .retrieval import DEFAULT_LOGS, FailureIndex, SearchOptions

DATA = Path(__file__).resolve().parents[2] / "data"


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

    def row(self) -> str:
        return (
            f"{self.p3:.3f} | {self.p5:.3f} | {self.abstain_ok}/{self.abstain_total} | {self.false_abstain}"
        )


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
    base = SearchOptions(filter_lines=False, expand_query=False, index_resolution=False, group_by_fix=False, abstain=False)
    steps = [
        ("BM25 on the raw log", base),
        ("+ keep signal lines only", replace(base, filter_lines=True)),
        ("+ index the resolution text", replace(base, filter_lines=True, index_resolution=True)),
        ("+ glossary query expansion", replace(base, filter_lines=True, index_resolution=True, expand_query=True)),
        ("+ rank by shared fix", replace(base, filter_lines=True, index_resolution=True, expand_query=True, group_by_fix=True)),
        ("+ abstain when nothing matches (final)", SearchOptions()),
    ]
    return steps


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate failure retrieval")
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--queries", type=Path, default=DATA / "01c_retrieval_queries.jsonl")
    parser.add_argument("--holdout", type=Path, default=DATA / "holdout_queries.jsonl")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    given = load_queries(args.queries)
    holdout = load_queries(args.holdout)
    groups = cause_groups([given, holdout])

    lines = [
        "# Retrieval evaluation",
        "",
        "P@k = share of the top k results whose id is relevant, averaged over questions that have relevant logs.",
        "Abstain = questions with nothing relevant that correctly returned no results.",
        "False abstain = questions with relevant logs that returned nothing.",
        "",
        "| Pipeline | Given P@3 | Given P@5 | Given abstain | Given false abstain "
        "| Held-out P@3 | Held-out P@5 | Held-out abstain | Held-out false abstain | Log→log P@3 | Log→log P@5 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, options in ablation():
        index = FailureIndex.from_jsonl(args.logs, options)
        g = score(index, given)
        h = score(index, holdout)
        l3, l5 = leave_one_out(index, groups)
        lines.append(f"| {label} | {g.row()} | {h.row()} | {l3:.3f} | {l5:.3f} |")

    final = FailureIndex.from_jsonl(args.logs)
    lines += ["", "## Per question (final pipeline)", "", "| Set | Id | Question | P@5 | Top 5 |", "|---|---|---|---|---|"]
    for name, queries in (("given", given), ("held-out", holdout)):
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
