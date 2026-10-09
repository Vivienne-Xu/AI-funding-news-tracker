from pathlib import Path

from app.db import TABLES, connect, list_tables


def test_connect_creates_all_tables(tmp_path: Path) -> None:
    conn = connect(tmp_path / "sub" / "test.db")
    assert sorted(list_tables(conn)) == sorted(TABLES)


def test_connect_twice_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "test.db"
    conn = connect(path)
    conn.execute("INSERT INTO sent_reports (kind, period, sent_at) VALUES ('daily', '2026-10-08', 'now')")
    conn.commit()
    conn.close()
    again = connect(path)
    assert again.execute("SELECT COUNT(*) FROM sent_reports").fetchone()[0] == 1


def test_sent_reports_blocks_duplicates(tmp_path: Path) -> None:
    import sqlite3

    import pytest

    conn = connect(tmp_path / "test.db")
    sql = "INSERT INTO sent_reports (kind, period, sent_at) VALUES ('daily', '2026-10-08', 'now')"
    conn.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)
