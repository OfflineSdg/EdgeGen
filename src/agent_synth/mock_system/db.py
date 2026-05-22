"""Generic database connection manager for SQLite mock systems."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict


def dict_factory(cursor: sqlite3.Cursor, row: tuple) -> Dict[str, Any]:
    """Convert SQLite rows to dictionaries."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def get_conn(db_path: str | Path):
    """Get a database connection with dict row factory and FK enforcement.

    Args:
        db_path: Path to the SQLite database file.

    Yields:
        sqlite3.Connection with auto-commit on success, rollback on error.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path, schema_path: str | Path) -> None:
    """Initialize database from a schema SQL file.

    Args:
        db_path: Path to the SQLite database file (created if not exists).
        schema_path: Path to the .sql schema file.
    """
    with open(schema_path) as f:
        schema = f.read()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.close()


def reset_db(db_path: str | Path, schema_path: str | Path) -> None:
    """Drop and recreate database from schema.

    Args:
        db_path: Path to the SQLite database file.
        schema_path: Path to the .sql schema file.
    """
    p = Path(db_path)
    if p.exists():
        p.unlink()
    init_db(db_path, schema_path)
