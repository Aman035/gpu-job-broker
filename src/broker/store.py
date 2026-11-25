"""Small SQLite-backed document store.

Entities are stored as JSON documents keyed by (kind, id). This keeps the
schema trivial to evolve; the data set is small enough that every query the
broker needs is done in memory after loading.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from typing import Any


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        # Callers serialise access (the API holds one lock per request), so the
        # connection may be used from the web server's worker threads.
        self._db = sqlite3.connect(path, isolation_level="DEFERRED", check_same_thread=False)
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
                kind TEXT NOT NULL,
                id   TEXT NOT NULL,
                data TEXT NOT NULL,
                PRIMARY KEY (kind, id)
            );
            CREATE TABLE IF NOT EXISTS kv (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._db.commit()

    def put(self, kind: str, id: str, data: dict) -> None:
        self._db.execute(
            "INSERT INTO entities (kind, id, data) VALUES (?, ?, ?) "
            "ON CONFLICT(kind, id) DO UPDATE SET data = excluded.data",
            (kind, id, json.dumps(data)),
        )

    def delete(self, kind: str, id: str) -> None:
        self._db.execute("DELETE FROM entities WHERE kind = ? AND id = ?", (kind, id))

    def all(self, kind: str) -> Iterator[dict]:
        for (data,) in self._db.execute("SELECT data FROM entities WHERE kind = ?", (kind,)):
            yield json.loads(data)

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def commit(self) -> None:
        self._db.commit()

    def rollback(self) -> None:
        self._db.rollback()

    def close(self) -> None:
        self._db.close()
