"""SQLite store: the append-only event log plus a small key/value state table.

Later phases add actions and outcomes tables here; the event log is the raw
material for the self-improvement loop, so it is never updated or deleted.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from alfred.core.events import Event

SCHEMA_VERSION = 2

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

-- One row per model call: cost accounting and budget enforcement.
CREATE TABLE IF NOT EXISTS llm_calls (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    tier          TEXT NOT NULL,
    model         TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read    INTEGER NOT NULL DEFAULT 0,
    cache_write   INTEGER NOT NULL DEFAULT 0,
    usd           REAL NOT NULL,
    stop_reason   TEXT,
    request_id    TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS llm_calls_ts ON llm_calls (ts);

-- Triage decision per event. `corrected_label` is your ground truth: it
-- measures triage accuracy now and becomes eval cases in Phase 4.
CREATE TABLE IF NOT EXISTS triage (
    event_id        TEXT PRIMARY KEY REFERENCES events (id),
    ts              TEXT NOT NULL,
    topic           TEXT NOT NULL,
    label           TEXT NOT NULL,
    urgency         INTEGER NOT NULL,
    summary         TEXT NOT NULL,
    reason          TEXT NOT NULL,
    model           TEXT NOT NULL,
    corrected_label TEXT,
    corrected_at    TEXT
);
CREATE INDEX IF NOT EXISTS triage_ts ON triage (ts);

-- Every message Alfred sends, for audit.
CREATE TABLE IF NOT EXISTS outbox (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    recipient  TEXT NOT NULL,
    subject    TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT
);

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

    def get_event(self, event_id: str) -> Event | None:
        row = self._db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    # -- llm accounting ---------------------------------------------------------

    def record_llm_call(self, **row: Any) -> None:
        row.setdefault("ts", _now())
        cols = ", ".join(row)
        self._db.execute(f"INSERT INTO llm_calls ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    def spend_since(self, since: datetime) -> float:
        row = self._db.execute(
            "SELECT COALESCE(SUM(usd), 0) AS usd FROM llm_calls WHERE ts >= ?", (_iso(since),)
        ).fetchone()
        return float(row["usd"])

    def spend_by_tier_since(self, since: datetime) -> dict[str, dict[str, float]]:
        rows = self._db.execute(
            "SELECT tier, COUNT(*) AS calls, SUM(usd) AS usd FROM llm_calls WHERE ts >= ? GROUP BY tier",
            (_iso(since),),
        )
        return {r["tier"]: {"calls": r["calls"], "usd": round(r["usd"], 4)} for r in rows}

    # -- triage -----------------------------------------------------------------

    def record_triage(self, event: Event, *, label: str, urgency: int, summary: str, reason: str, model: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO triage (event_id, ts, topic, label, urgency, summary, reason, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event.id, _now(), event.topic, label, urgency, summary, reason, model),
        )

    def triage_since(self, since: datetime, exclude_labels: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        sql = "SELECT t.*, e.payload FROM triage t JOIN events e ON e.id = t.event_id WHERE t.ts >= ?"
        args: list[Any] = [_iso(since)]
        if exclude_labels:
            sql += f" AND COALESCE(t.corrected_label, t.label) NOT IN ({', '.join('?' * len(exclude_labels))})"
            args += list(exclude_labels)
        sql += " ORDER BY t.urgency DESC, t.ts"
        return [_triage_row(r) for r in self._db.execute(sql, args)]

    def recent_triage(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT t.*, e.payload FROM triage t JOIN events e ON e.id = t.event_id ORDER BY t.ts DESC LIMIT ?",
            (limit,),
        )
        return [_triage_row(r) for r in rows]

    def correct_triage(self, event_id_prefix: str, label: str) -> str | None:
        rows = self._db.execute(
            "SELECT event_id FROM triage WHERE event_id LIKE ?", (event_id_prefix + "%",)
        ).fetchall()
        if len(rows) != 1:
            return None
        self._db.execute(
            "UPDATE triage SET corrected_label = ?, corrected_at = ? WHERE event_id = ?",
            (label, _now(), rows[0]["event_id"]),
        )
        return rows[0]["event_id"]

    def triage_accuracy(self) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT COUNT(*) AS n, SUM(corrected_label = label) AS agree FROM triage WHERE corrected_label IS NOT NULL"
        ).fetchone()
        n = row["n"] or 0
        return {"reviewed": n, "agree": row["agree"] or 0, "accuracy": round((row["agree"] or 0) / n, 3) if n else None}

    # -- outbox -------------------------------------------------------------------

    def record_outbox(self, *, kind: str, recipient: str, subject: str, status: str, detail: str | None = None) -> None:
        self._db.execute(
            "INSERT INTO outbox (ts, kind, recipient, subject, status, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (_now(), kind, recipient, subject, status, detail),
        )

    def recent_outbox(self, limit: int = 10) -> list[dict[str, Any]]:
        return [dict(r) for r in self._db.execute("SELECT * FROM outbox ORDER BY seq DESC LIMIT ?", (limit,))]

    # -- pause switch (read by every actor before doing anything T1+) ---------

    def is_paused(self) -> bool:
        return bool(self.get_state("paused", False))

    def set_paused(self, paused: bool) -> None:
        self.set_state("paused", paused)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).isoformat()


def _triage_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    d["effective_label"] = d["corrected_label"] or d["label"]
    return d
