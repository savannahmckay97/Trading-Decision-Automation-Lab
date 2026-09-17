"""SQLite event store with append-only raw, snapshot and decision records."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .collectors import CollectionError, RawEvent
from .util import canonical_json, now_ms, stable_id


SCHEMA_VERSION = 1


class EventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS raw_events(
                raw_id TEXT PRIMARY KEY,
                received_time_ms INTEGER NOT NULL,
                event_time_ms INTEGER,
                source TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                symbol TEXT NOT NULL,
                critical INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS normalized_snapshots(
                snapshot_id TEXT PRIMARY KEY,
                observed_at_ms INTEGER NOT NULL,
                research_symbol TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions(
                decision_id TEXT PRIMARY KEY,
                decision_time_ms INTEGER NOT NULL,
                research_symbol TEXT NOT NULL,
                setup_state TEXT NOT NULL,
                signal TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS setup_states(
                strategy_id TEXT NOT NULL,
                research_symbol TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(strategy_id, research_symbol)
            );
            CREATE TABLE IF NOT EXISTS collection_errors(
                error_id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_time_ms INTEGER NOT NULL,
                source TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                symbol TEXT NOT NULL,
                critical INTEGER NOT NULL,
                detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS heartbeats(
                heartbeat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                time_ms INTEGER NOT NULL,
                cycle_status TEXT NOT NULL,
                detail TEXT
            );
            """
        )
        self.db.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def append_collection(self, events: Iterable[RawEvent], errors: Iterable[CollectionError]) -> None:
        with self.db:
            for event in events:
                self.db.execute(
                    """INSERT INTO raw_events
                    (raw_id,received_time_ms,event_time_ms,source,endpoint,symbol,critical,payload_json)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        event.raw_id,
                        event.received_time_ms,
                        event.event_time_ms,
                        event.source,
                        event.endpoint,
                        event.symbol,
                        int(event.critical),
                        canonical_json(event.payload),
                    ),
                )
            for error in errors:
                self.db.execute(
                    """INSERT INTO collection_errors
                    (received_time_ms,source,endpoint,symbol,critical,detail)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        error.received_time_ms,
                        error.source,
                        error.endpoint,
                        error.symbol,
                        int(error.critical),
                        error.detail,
                    ),
                )

    def append_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = stable_id("snapshot", snapshot)
        with self.db:
            self.db.execute(
                "INSERT INTO normalized_snapshots VALUES(?,?,?,?)",
                (
                    snapshot_id,
                    int(snapshot["observed_at_ms"]),
                    snapshot["research_symbol"],
                    canonical_json(snapshot),
                ),
            )
        return snapshot_id

    def append_decision(self, decision: dict[str, Any]) -> str:
        decision_id = stable_id("decision", decision)
        with self.db:
            self.db.execute(
                "INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                (
                    decision_id,
                    int(decision["decision_time_ms"]),
                    decision["research_symbol"],
                    decision["setup"]["state"],
                    decision["signal"],
                    canonical_json(decision),
                ),
            )
        return decision_id

    def load_setup_state(self, strategy_id: str, research_symbol: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT payload_json FROM setup_states WHERE strategy_id=? AND research_symbol=?",
            (strategy_id, research_symbol),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_setup_state(self, strategy_id: str, research_symbol: str, state: dict[str, Any]) -> None:
        with self.db:
            self.db.execute(
                """INSERT INTO setup_states VALUES(?,?,?,?)
                ON CONFLICT(strategy_id,research_symbol) DO UPDATE SET
                updated_at_ms=excluded.updated_at_ms,payload_json=excluded.payload_json""",
                (strategy_id, research_symbol, now_ms(), canonical_json(state)),
            )

    def heartbeat(self, status: str, detail: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO heartbeats(time_ms,cycle_status,detail) VALUES(?,?,?)",
                (now_ms(), status, detail),
            )

    def counts(self) -> dict[str, int]:
        names = ["raw_events", "normalized_snapshots", "decisions", "collection_errors", "heartbeats"]
        return {name: int(self.db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}

    def backup_to(self, destination: str | Path) -> Path:
        """Create a transactionally consistent SQLite backup."""

        target = Path(destination).expanduser().resolve()
        if str(target) == self.path or target.exists():
            raise FileExistsError(f"backup destination must be new: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(target)
        try:
            self.db.backup(backup)
            result = backup.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError(f"backup integrity check failed: {result}")
        except Exception:
            backup.close()
            target.unlink(missing_ok=True)
            raise
        finally:
            backup.close()
        return target


def default_database_path() -> Path:
    configured = os.environ.get("MARKET_OBSERVER_DB")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "trading_decision_lab.sqlite"


def restore_database(backup_path: str | Path, destination: str | Path) -> Path:
    """Restore a verified backup into a new path; never overwrite an existing DB."""

    source = Path(backup_path).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"backup does not exist: {source}")
    if target.exists():
        raise FileExistsError(f"restore destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        result = source_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError(f"backup integrity check failed: {result}")
    finally:
        source_db.close()
    try:
        shutil.copyfile(source, target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target
