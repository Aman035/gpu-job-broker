"""Similar-failure search over past job logs. See docs/RETRIEVAL.md."""

from .index import DEFAULT_LOGS, FailureIndex, SearchOptions, SearchResult
from .text import redact, tokenize

__all__ = ["DEFAULT_LOGS", "FailureIndex", "SearchOptions", "SearchResult", "redact", "tokenize"]
