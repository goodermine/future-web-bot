"""Module 3 - Modelspace Ledger (persistence).

SQLite time-series ledger of scored packets. ``calculated_tension`` is a
generated column (``intensity * immediacy``) so it can never drift from its
inputs.

Usage::

    python database.py --db data/modelspace.db --init
    python database.py --db data/modelspace.db --load data/scored.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

log = logging.getLogger("webbot.database")

DEFAULT_DB = "data/modelspace.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS modelspace_ledger (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          DATETIME NOT NULL,
    source             TEXT     NOT NULL,
    intensity          INTEGER  NOT NULL CHECK (intensity BETWEEN 1 AND 100),
    immediacy          INTEGER  NOT NULL CHECK (immediacy BETWEEN 1 AND 100),
    scale              INTEGER  NOT NULL CHECK (scale BETWEEN 1 AND 100),
    calculated_tension REAL GENERATED ALWAYS AS (intensity * immediacy) STORED,
    headline           TEXT,
    method             TEXT,
    content_hash       TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_ledger_timestamp ON modelspace_ledger (timestamp);
"""

_INSERT = """
INSERT INTO modelspace_ledger
    (timestamp, source, intensity, immediacy, scale, headline, method, content_hash)
VALUES (:timestamp, :source, :intensity, :immediacy, :scale, :headline, :method, :content_hash)
ON CONFLICT (content_hash) DO NOTHING
"""


@contextmanager
def connect(db_path: str | Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _normalise_timestamp(value: str | datetime | None) -> str:
    """Store timestamps as UTC 'YYYY-MM-DD HH:MM:SS' so SQLite date functions work."""
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _row(packet: dict) -> dict:
    source = packet.get("source") or packet.get("source_url") or "unknown"
    headline = packet.get("headline") or ""
    digest_src = f"{source}\x1f{headline}\x1f{packet.get('raw_text', '')}"
    return {
        "timestamp": _normalise_timestamp(packet.get("timestamp")),
        "source": source,
        "intensity": int(packet["intensity"]),
        "immediacy": int(packet["immediacy"]),
        "scale": int(packet["scale"]),
        "headline": headline,
        "method": packet.get("method"),
        "content_hash": packet.get("content_hash") or hashlib.sha256(digest_src.encode()).hexdigest(),
    }


def insert_packets(conn: sqlite3.Connection, packets: Iterable[dict]) -> int:
    """Bulk insert analysed packets in one transaction. Duplicates are skipped.

    Returns the number of rows actually inserted. Malformed packets raise before
    anything is written, so a batch is all-or-nothing.
    """
    rows = [_row(p) for p in packets]
    before = conn.total_changes
    with conn:  # transaction
        conn.executemany(_INSERT, rows)
    inserted = conn.total_changes - before
    log.info("Inserted %d/%d packets (%d duplicates skipped)", inserted, len(rows), len(rows) - inserted)
    return inserted


def commit_packets(packets: Iterable[dict], db_path: str | Path = DEFAULT_DB) -> int:
    """Convenience wrapper: open, ensure schema, insert, close."""
    with connect(db_path) as conn:
        init_schema(conn)
        return insert_packets(conn, packets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the modelspace ledger.")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--init", action="store_true", help="Create the schema if missing")
    parser.add_argument("--load", help="JSON array of scored packets to insert")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    with connect(args.db) as conn:
        init_schema(conn)
        if args.load:
            insert_packets(conn, json.loads(Path(args.load).read_text()))
        count = conn.execute("SELECT COUNT(*) FROM modelspace_ledger").fetchone()[0]
    print(f"{args.db}: {count} rows in modelspace_ledger")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
