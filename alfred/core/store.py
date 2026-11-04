"""SQLite store: the append-only event log plus a small key/value state table.

Later phases add actions and outcomes tables here; the event log is the raw
material for the self-improvement loop, so it is never updated or deleted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from alfred.core.events import Event

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_source_kind ON events (source, kind);
CREATE INDEX IF NOT EXISTS events_received_at ON events (received_at);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None)  # autocommit
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_SCHEMA)
        self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- events ---------------------------------------------------------------

    def append_event(self, event: Event) -> int:
        cur = self._db.execute(
            "INSERT INTO events (id, source, kind, payload, received_at) VALUES (?, ?, ?, ?, ?)",
            (event.id, event.source, event.kind, json.dumps(event.payload, default=str), event.received_at.isoformat()),
        )
        return int(cur.lastrowid)

    def recent_events(self, limit: int = 20, source: str | None = None) -> list[Event]:
        sql = "SELECT * FROM events"
        args: list[Any] = []
        if source:
            sql += " WHERE source = ?"
            args.append(source)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        return [self._row_to_event(r) for r in self._db.execute(sql, args)]

    def iter_events(self) -> Iterator[Event]:
        for row in self._db.execute("SELECT * FROM events ORDER BY seq"):
            yield self._row_to_event(row)

    def count_events(self) -> dict[str, int]:
        rows = self._db.execute("SELECT source || '.' || kind AS topic, COUNT(*) AS n FROM events GROUP BY topic")
        return {r["topic"]: r["n"] for r in rows}

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            source=row["source"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            id=row["id"],
            received_at=datetime.fromisoformat(row["received_at"]),
        )

    # -- state ----------------------------------------------------------------

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (key, json.dumps(value, default=str)),
        )

    # -- pause switch (read by every actor before doing anything T1+) ---------

    def is_paused(self) -> bool:
        return bool(self.get_state("paused", False))

    def set_paused(self, paused: bool) -> None:
        self.set_state("paused", paused)
