"""
Database State Sampler.

Thin wrapper around SQLiteWalker — samples a connected subgraph of rows
from the mock SQLite database. Returns plain dicts; no dataclasses.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...mock_system.walker import SQLiteWalker
from ...mock_system.db import get_conn


class StateSampler:
    """Samples database state for test case generation using SQLiteWalker.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.walker = SQLiteWalker(str(self.db_path))

    def sample(
        self,
        seed_table: Optional[str] = None,
        seed_where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Sample a connected subgraph from the database.

        Returns:
            Dict mapping table_name -> list of row dicts.
        """
        return self.walker.walk(seed_table=seed_table, seed_where=seed_where)

    def get_relationship_annotations(self) -> str:
        """Return a human-readable description of FK relationships.

        Used to annotate the DB snapshot in LLM prompts so the model
        understands ownership and association between rows across tables.
        """
        lines = []
        for tinfo in self.walker.schema.values():
            for fk in tinfo.foreign_keys_out:
                lines.append(
                    f"- {fk.from_table}.{fk.from_column} references "
                    f"{fk.to_table}.{fk.to_column} "
                    f"(each {fk.from_table} row BELONGS TO the {fk.to_table} "
                    f"row with matching {fk.to_column})"
                )
        return "\n".join(lines)

    def get_table_counts(self) -> Dict[str, int]:
        """Get row counts for all tables in the database."""
        with get_conn(self.db_path) as conn:
            stats = {}
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for t in tables:
                name = t["name"]
                stats[name] = conn.execute(f"SELECT COUNT(*) as cnt FROM [{name}]").fetchone()["cnt"]
            return stats


def ensure_seed_database(db_path: str | Path, seed_db_path: str | Path) -> None:
    """Ensure the seed database exists.

    If it doesn't exist, create it from the current database.
    """
    db_path = Path(db_path)
    seed_db_path = Path(seed_db_path)
    if not seed_db_path.exists():
        if db_path.exists():
            shutil.copy2(db_path, seed_db_path)
        else:
            raise FileNotFoundError(
                f"Neither seed database ({seed_db_path}) nor main database ({db_path}) exists"
            )


def reset_database_to_seed(db_path: str | Path, seed_db_path: str | Path) -> None:
    """Reset the working database to the seed/original state."""
    ensure_seed_database(db_path, seed_db_path)
    shutil.copy2(seed_db_path, db_path)


def create_seed_from_current(db_path: str | Path, seed_db_path: str | Path) -> None:
    """Create/update the seed database from the current database state."""
    db_path = Path(db_path)
    seed_db_path = Path(seed_db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")
    shutil.copy2(db_path, seed_db_path)
