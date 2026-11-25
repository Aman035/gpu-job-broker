"""Text handling for failure logs: secret redaction, signal lines, tokens."""

from __future__ import annotations

import re
from collections import Counter

# ---- 1. redaction --------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization:\s*basic\s+)\S+"), r"\1[REDACTED]"),
    (
        re.compile(r"(?i)\b(\w*(?:password|passwd|secret|token|api_?key|access_?key)\w*)\s*([=:])\s*\S+"),
        r"\1\2[REDACTED]",
    ),
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
            df.update({line_template(line) for line in log.splitlines() if line.strip()})
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
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "for",
        "from",
        "get",
        "got",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "keep",
        "keeps",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "says",
        "so",
        "some",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "to",
        "too",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "after",
        "before",
        "again",
        "because",
        "during",
        "each",
        "about",
        "any",
        "all",
        "also",
        "than",
        "over",
        "under",
        "out",
        "off",
    ]
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
