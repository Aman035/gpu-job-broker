"""Find past job failures similar to a new log or a free-text question.

Pipeline (each step is measured in docs/RETRIEVAL.md):

1. Redact secrets before anything is indexed or returned.
2. Keep only signal lines. A line whose template (digits masked) appears in
   many logs is boilerplate (heartbeats, epoch counters, deprecation warnings).
   This is learned from the corpus, not hand-listed, so it also applies to
   pasted logs. DEBUG lines are dropped: they describe state, not failures.
3. Tokenise for error text: split CamelCase and snake_case, keep short error
   codes (137, 401), drop long numbers (step counts, shard ids), light stemming.
4. Expand the query with a small glossary that maps everyday words to the
   vocabulary failures are logged in ("disk is full" -> "no space left on device").
5. Score with BM25 over signal lines plus the recorded resolution, whose plain
   wording bridges different log formats of the same cause.
6. Abstain unless the question describes a failure (failure words, phrases such
   as "ran out", or a status code) or literally matches a log line: return no
   results rather than the five least-bad matches.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import data_dir
from .glossary import describes_failure, expand
from .text import LineFilter, redact, tokenize

DEFAULT_LOGS = data_dir() / "01b_failure_logs.jsonl"


@dataclass
class FailureLog:
    id: str
    team: str
    log: str  # redacted
    resolution: str
    signal: list[str] = field(default_factory=list)


@dataclass
class Match:
    id: str
    team: str
    score: float
    resolution: str | None
    evidence: list[str]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "team": self.team,
            "score": round(self.score, 3),
            "resolution": self.resolution,
            "evidence": self.evidence,
        }


@dataclass
class SearchResult:
    matches: list[Match]
    abstained: bool
    concepts: list[str]
    suggested_resolution: str | None
    ambiguous: bool  # top matches look alike but were fixed differently

    def to_dict(self) -> dict:
        return {
            "matches": [m.to_dict() for m in self.matches],
            "abstained": self.abstained,
            "concepts": self.concepts,
            "suggested_resolution": self.suggested_resolution,
            "ambiguous": self.ambiguous,
        }


@dataclass
class SearchOptions:
    """Switches used by the evaluation to measure each step."""

    filter_lines: bool = True
    expand_query: bool = True
    index_resolution: bool = True
    group_by_fix: bool = True
    abstain: bool = True
    abstain_unless_failure: bool = True  # False: v1 rule, any glossary concept counts as on-topic
    min_raw_score: float = 6.0  # for questions that hit no glossary concept


class FailureIndex:
    k1 = 1.2
    b = 0.75

    def __init__(self, logs: list[FailureLog], options: SearchOptions | None = None) -> None:
        self.options = options or SearchOptions()
        self.logs = logs
        self.filter = LineFilter([entry.log for entry in logs])
        self.docs: list[Counter[str]] = []
        self.log_docs: list[Counter[str]] = []  # signal lines only, for the literal-match check
        for entry in logs:
            entry.signal = self.filter.signal_lines(entry.log) if self.options.filter_lines else entry.log.splitlines()
            text = " ".join(entry.signal)
            self.log_docs.append(Counter(tokenize(text)))
            if self.options.index_resolution:
                text += " " + entry.resolution
            self.docs.append(Counter(tokenize(text)))
        self.avgdl = sum(sum(d.values()) for d in self.docs) / len(self.docs)
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    @classmethod
    def from_jsonl(cls, path: Path = DEFAULT_LOGS, options: SearchOptions | None = None) -> FailureIndex:
        logs = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    logs.append(FailureLog(d["id"], d["team"], redact(d["log"]), d.get("resolution") or ""))
        return cls(logs, options)

    def _bm25(self, weights: dict[str, float], doc: Counter[str]) -> float:
        length = sum(doc.values())
        score = 0.0
        for term, w in weights.items():
            tf = doc.get(term, 0)
            if not tf:
                continue
            norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avgdl))
            score += w * self.idf.get(term, 0.0) * norm
        return score

    def query_tokens(self, text: str) -> list[str]:
        text = redact(text)
        if "\n" in text.strip() and self.options.filter_lines:
            text = " ".join(self.filter.signal_lines(text))  # a pasted log
        return tokenize(text)

    def search(self, text: str, k: int = 5) -> SearchResult:
        tokens = self.query_tokens(text)
        raw = {t: 1.0 for t in tokens}
        if self.options.expand_query:
            weights, concepts = expand(tokens)
        else:
            weights, concepts = dict(raw), []

        doc_scores = [self._bm25(weights, doc) for doc in self.docs]
        if self.options.group_by_fix:
            doc_scores = self._boost_by_fix(doc_scores)
        scored = sorted(
            ((s, i) for i, s in enumerate(doc_scores)),
            key=lambda x: (-x[0], self.logs[x[1]].id),
        )
        # Literal fallback for pasted logs: compare against log lines only, not the
        # human-written fix text, which shares everyday words with questions.
        best_raw = max((self._bm25(raw, doc) for doc in self.log_docs), default=0.0)
        on_topic = describes_failure(text) if self.options.abstain_unless_failure else bool(concepts)
        abstain = self.options.abstain and not on_topic and best_raw < self.options.min_raw_score
        if abstain or not scored or scored[0][0] <= 0:
            return SearchResult([], True, concepts, None, False)

        matches = [
            Match(
                id=self.logs[i].id,
                team=self.logs[i].team,
                score=s,
                resolution=self.logs[i].resolution or None,
                evidence=self.logs[i].signal[:3],
            )
            for s, i in scored[:k]
            if s > 0
        ]
        return SearchResult(matches, False, concepts, *self._suggest(matches))

    def _boost_by_fix(self, scores: list[float]) -> list[float]:
        """Add to each log the best score among logs with the same recorded fix.

        Logs fixed the same way share a cause, so a strong match on one of them is
        evidence for the others even when their wording differs ("jwt expired"
        vs "401 Unauthorized"). Logs without a recorded fix stand alone.
        """
        best: dict[str, float] = {}
        for entry, s in zip(self.logs, scores, strict=True):
            if entry.resolution:
                best[entry.resolution] = max(best.get(entry.resolution, 0.0), s)
        return [
            s + best[entry.resolution] if entry.resolution and s > 0 else s
            for entry, s in zip(self.logs, scores, strict=True)
        ]

    def _suggest(self, matches: list[Match]) -> tuple[str | None, bool]:
        """Score-weighted vote over the fixes of the top matches (logs without a fix abstain).

        Ambiguous: two top matches have near-identical logs but were fixed
        differently, so the log alone cannot tell which fix applies.
        """
        votes: dict[str, float] = {}
        for m in matches:
            if m.resolution:
                votes[m.resolution] = votes.get(m.resolution, 0.0) + m.score
        if not votes:
            return None, False
        suggestion = max(votes.items(), key=lambda kv: kv[1])[0]

        by_id = {e.id: e for e in self.logs}
        signals = [(m, set(tokenize(" ".join(by_id[m.id].signal)))) for m in matches if m.resolution]
        ambiguous = any(
            a.resolution != b.resolution and _jaccard(ta, tb) >= 0.9
            for i, (a, ta) in enumerate(signals)
            for b, tb in signals[i + 1 :]
        )
        return suggestion, ambiguous


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
