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
6. Abstain when nothing in the question looks like a failure: return no results
   rather than the five least-bad matches.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LOGS = Path(__file__).resolve().parents[2] / "data" / "01b_failure_logs.jsonl"

# ---- 1. redaction --------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization:\s*basic\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(\w*(?:password|passwd|secret|token|api_?key|access_?key)\w*)\s*([=:])\s*\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\b(?:ghp|gho|github_pat|xox[abp]|sk)[-_][A-Za-z0-9_-]{16,}\b"), "[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---- 2. signal lines ------------------------------------------------------------

_NUMBER = re.compile(r"\d+")


def line_template(line: str) -> str:
    return _NUMBER.sub("#", line.strip())


class LineFilter:
    """Drops lines whose template is common across the corpus, and DEBUG lines."""

    def __init__(self, logs: list[str], max_doc_share: float = 0.15) -> None:
        df: Counter[str] = Counter()
        for log in logs:
            df.update({line_template(l) for l in log.splitlines() if l.strip()})
        limit = max(2, int(max_doc_share * len(logs)))
        self.boilerplate = {t for t, n in df.items() if n > limit}

    def signal_lines(self, log: str) -> list[str]:
        lines = []
        for line in log.splitlines():
            line = line.strip()
            if not line or line.startswith("DEBUG") or line_template(line) in self.boilerplate:
                continue
            lines.append(line)
        return lines


# ---- 3. tokens --------------------------------------------------------------------

_STOPWORDS = set(
    """a an and are as at be been but by can could did do does doing for from get got had has have how i if
    in into is it its just keep keeps me my of on or our says so some that the their them then there these
    this to too up very was we were what when where which while who why will with would you your after
    before again because during each about any all also than over under out off""".split()
)
# Split "PermissionError" and "OOMKilled", but not "NaN".
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z][a-z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and not word.endswith("ss"):
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _WORD.findall(text):
        if raw.isdigit():
            if len(raw) <= 3:
                tokens.append(raw)  # error codes such as 137, 401, 403
            continue
        for part in _CAMEL.split(raw):
            part = part.lower()
            if len(part) < 2 or part in _STOPWORDS:
                continue
            tokens.append(stem(part))
    return tokens


# ---- 4. glossary --------------------------------------------------------------------
#
# Each concept: words people use (triggers) -> words failures are logged in.
# Kept deliberately small and about failure classes, not about specific
# questions. In production this would be mined from incident tickets.

GLOSSARY: dict[str, tuple[str, str]] = {
    "out_of_memory": (
        "oom oomkilled memory vram ram 137",
        "out of memory oom oom killed cuda allocate tensor 137",
    ),
    "disk_full": (
        "disk space storage full volume enospc",
        "no space left on device disk usage scratch write",
    ),
    "divergence": (
        "nan inf infinite infinity diverge divergence diverged exploded explode blew unstable gradient",
        "loss nan inf divergence detected gradient norm",
    ),
    "multi_gpu_comms": (
        "nccl allreduce collective communicator distributed interconnect hang hangs rank",
        "nccl communicator aborted rank watchdog collective timeout allreduce",
    ),
    "image_pull": (
        "image pull registry docker",
        "image pull registry docker unauthorized authentication required access denied",
    ),
    "auth_token": (
        "token jwt credential credentials expired unauthorized 401 403 forbidden authentication auth session",
        "token expired unauthorized forbidden jwt bearer auth",
    ),
    "driver_mismatch": (
        "driver cudnn libcudnn cublas symbol library incompatible mismatch",
        "nvidia driver too old cuda version torch requires runtime libcudnn shared object undefined symbol import",
    ),
    "data_access": (
        "dataset missing permission denied labels mount path",
        "file not found no such file directory permission denied errno dataset",
    ),
    "node_reclaimed": (
        "interruptible spot preempt preempted reclaim reclaimed termination terminated notice vanished disappeared heartbeat",
        "interruption warning node terminated termination notice reclaim heartbeat missed node lost",
    ),
}

_CONCEPTS = {
    name: ({stem(t) for t in triggers.split()}, tokenize(expansion))
    for name, (triggers, expansion) in GLOSSARY.items()
}


def expand(tokens: list[str]) -> tuple[dict[str, float], list[str]]:
    """Weighted query terms (original terms weigh 1, glossary terms less) and the concepts hit."""
    weights: dict[str, float] = {}
    for t in tokens:
        weights[t] = weights.get(t, 0.0) + 1.0
    hit = [name for name, (triggers, _) in _CONCEPTS.items() if triggers & set(tokens)]
    for name in hit:
        for t in _CONCEPTS[name][1]:
            weights[t] = max(weights.get(t, 0.0), EXPANSION_WEIGHT)
    return weights, hit


EXPANSION_WEIGHT = 0.4

# ---- 5. index -----------------------------------------------------------------------


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
    min_raw_score: float = 6.0  # for questions that hit no glossary concept


class FailureIndex:
    k1 = 1.2
    b = 0.75

    def __init__(self, logs: list[FailureLog], options: SearchOptions | None = None) -> None:
        self.options = options or SearchOptions()
        self.logs = logs
        self.filter = LineFilter([l.log for l in logs])
        self.docs: list[Counter[str]] = []
        for entry in logs:
            entry.signal = self.filter.signal_lines(entry.log) if self.options.filter_lines else entry.log.splitlines()
            text = " ".join(entry.signal)
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
    def from_jsonl(cls, path: Path = DEFAULT_LOGS, options: SearchOptions | None = None) -> "FailureIndex":
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
        best_raw = max((self._bm25(raw, doc) for doc in self.docs), default=0.0)
        abstain = self.options.abstain and not concepts and best_raw < self.options.min_raw_score
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
        for entry, s in zip(self.logs, scores):
            if entry.resolution:
                best[entry.resolution] = max(best.get(entry.resolution, 0.0), s)
        return [
            s + best[entry.resolution] if entry.resolution and s > 0 else s
            for entry, s in zip(self.logs, scores)
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
            for b, tb in signals[i + 1:]
        )
        return suggestion, ambiguous


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
