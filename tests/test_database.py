import sqlite3

import pytest

import database


def packet(**kw):
    base = {"timestamp": "2026-09-01T12:00:00+02:00", "source_url": "https://ex.com/a",
            "headline": "h", "raw_text": "t", "intensity": 50, "immediacy": 40, "scale": 30}
    return {**base, **kw}


def test_bulk_insert_computes_tension_and_dedupes(tmp_path):
    db = tmp_path / "l.db"
    assert database.commit_packets([packet(), packet(headline="h2", intensity=10)], db) == 2
    assert database.commit_packets([packet()], db) == 0  # duplicate skipped
    with database.connect(db) as conn:
        rows = conn.execute("SELECT * FROM modelspace_ledger ORDER BY id").fetchall()
    assert [r["calculated_tension"] for r in rows] == [2000.0, 400.0]
    assert rows[0]["timestamp"] == "2026-09-01 10:00:00"  # normalised to UTC
    assert rows[0]["source"] == "https://ex.com/a"


def test_batch_is_atomic_on_bad_packet(tmp_path):
    db = tmp_path / "l.db"
    with pytest.raises(sqlite3.IntegrityError):
        database.commit_packets([packet(), packet(headline="bad", intensity=500)], db)
    with database.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM modelspace_ledger").fetchone()[0] == 0
