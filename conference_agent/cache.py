from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class Cache:
    """Persistent TTL cache. Keys never contain provider credentials."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "cache.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, stored REAL NOT NULL)"
        )

    @staticmethod
    def key(namespace: str, value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        return namespace + ":" + hashlib.sha256(encoded).hexdigest()

    def get(self, key: str, ttl: float) -> Any | None:
        if ttl <= 0:
            return None
        row = self.connection.execute("SELECT value, stored FROM cache WHERE key=?", (key,)).fetchone()
        if row is None or time.time() - row[1] > ttl:
            return None
        return json.loads(row[0])

    def put(self, key: str, value: Any) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), time.time()),
            )

    def close(self) -> None:
        self.connection.close()
