"""
Generic SQLite Database Walker.

Discovers schema from any SQLite database via PRAGMA introspection,
walks foreign key relationships from a sampled seed row, and returns
a complete connected subgraph of data.

Zero hardcoded table names, column names, or domain knowledge.

Usage:
    walker = SQLiteWalker("/path/to/any.db")
    result = walker.walk(seed_table="users")
    # result: {"users": [{...}], "orders": [{...}], ...}
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class ColumnInfo:
    """A column discovered via PRAGMA table_info."""
    name: str
    col_type: str
    notnull: bool
    default_value: Any
    is_pk: bool


@dataclass
class ForeignKey:
    """A FK relationship discovered via PRAGMA foreign_key_list."""
    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class TableInfo:
    """Complete metadata for one table."""
    name: str
    columns: Dict[str, ColumnInfo] = field(default_factory=dict)
    primary_keys: List[str] = field(default_factory=list)
    foreign_keys_out: List[ForeignKey] = field(default_factory=list)
    foreign_keys_in: List[ForeignKey] = field(default_factory=list)


@dataclass
class Relationship:
    """A navigable edge between two tables."""
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    direction: str  # "parent" (I FK to them) or "child" (they FK to me)


# =============================================================================
# Walker
# =============================================================================

class SQLiteWalker:
    """
    Generic SQLite database walker.

    Discovers schema via PRAGMA, builds a relationship graph,
    samples a seed row, and walks outward to collect all related data.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._schema: Optional[Dict[str, TableInfo]] = None
        self._graph: Optional[Dict[str, List[Relationship]]] = None
        # Precomputed sets for the eligibility heuristic
        self._all_pk_columns: Optional[Set[str]] = None
        self._all_fk_columns: Optional[Set[str]] = None

    # -----------------------------------------------------------------
    # Connection helper
    # -----------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = self._dict_factory
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> Dict[str, Any]:
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    # -----------------------------------------------------------------
    # Schema Discovery
    # -----------------------------------------------------------------

    @property
    def schema(self) -> Dict[str, TableInfo]:
        if self._schema is None:
            self._schema = self._discover_schema()
        return self._schema

    @property
    def graph(self) -> Dict[str, List[Relationship]]:
        if self._graph is None:
            self._graph = self._build_relationship_graph()
        return self._graph

    def _discover_schema(self) -> Dict[str, TableInfo]:
        conn = self._connect()
        try:
            tables_rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()

            schema: Dict[str, TableInfo] = {}

            for trow in tables_rows:
                tname = trow["name"]
                info = TableInfo(name=tname)

                # Columns and PKs
                for col in conn.execute(f"PRAGMA table_info([{tname}])").fetchall():
                    ci = ColumnInfo(
                        name=col["name"],
                        col_type=col["type"],
                        notnull=bool(col["notnull"]),
                        default_value=col["dflt_value"],
                        is_pk=col["pk"] > 0,
                    )
                    info.columns[ci.name] = ci
                    if ci.is_pk:
                        info.primary_keys.append(ci.name)

                # Outgoing FKs
                for fk in conn.execute(f"PRAGMA foreign_key_list([{tname}])").fetchall():
                    info.foreign_keys_out.append(ForeignKey(
                        from_table=tname,
                        from_column=fk["from"],
                        to_table=fk["table"],
                        to_column=fk["to"],
                    ))

                schema[tname] = info

            # Populate foreign_keys_in (reverse direction)
            for tinfo in schema.values():
                for fk in tinfo.foreign_keys_out:
                    if fk.to_table in schema:
                        schema[fk.to_table].foreign_keys_in.append(fk)

            # Precompute eligibility sets
            self._all_pk_columns = set()
            self._all_fk_columns = set()
            for tinfo in schema.values():
                for pk in tinfo.primary_keys:
                    self._all_pk_columns.add(pk)
                for fk in tinfo.foreign_keys_out:
                    self._all_fk_columns.add(fk.from_column)

            return schema
        finally:
            conn.close()

    def _build_relationship_graph(self) -> Dict[str, List[Relationship]]:
        graph: Dict[str, List[Relationship]] = defaultdict(list)

        for tinfo in self.schema.values():
            for fk in tinfo.foreign_keys_out:
                # child -> parent direction
                graph[fk.from_table].append(Relationship(
                    from_table=fk.from_table,
                    from_column=fk.from_column,
                    to_table=fk.to_table,
                    to_column=fk.to_column,
                    direction="parent",
                ))
                # parent -> child direction
                graph[fk.to_table].append(Relationship(
                    from_table=fk.to_table,
                    from_column=fk.to_column,
                    to_table=fk.from_table,
                    to_column=fk.from_column,
                    direction="child",
                ))

        return dict(graph)

    # -----------------------------------------------------------------
    # Row Identity
    # -----------------------------------------------------------------

    def _row_identity(self, table: str, row: Dict[str, Any]) -> tuple:
        """Hashable identity for a row, based on PKs."""
        tinfo = self.schema[table]
        if tinfo.primary_keys:
            pk_vals = tuple(row.get(pk) for pk in tinfo.primary_keys)
            return (table, pk_vals)
        # No PK — fall back to all values
        return (table, tuple(sorted(row.items())))

    # -----------------------------------------------------------------
    # Seed Sampling
    # -----------------------------------------------------------------

    def sample_seed(
        self,
        table: Optional[str] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Pick a random row from a table.

        If table is None, auto-pick the table with the most incoming FKs
        (the most "central" entity).
        """
        if table is None:
            table = self._pick_seed_table()

        if table not in self.schema:
            raise ValueError(f"Table '{table}' not found. Available: {list(self.schema.keys())}")

        conn = self._connect()
        try:
            if where:
                conditions = " AND ".join(f"[{k}] = ?" for k in where)
                params = list(where.values())
                row = conn.execute(
                    f"SELECT * FROM [{table}] WHERE {conditions} ORDER BY RANDOM() LIMIT 1",
                    params,
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT * FROM [{table}] ORDER BY RANDOM() LIMIT 1"
                ).fetchone()

            if row is None:
                raise ValueError(f"No rows found in table '{table}'" +
                                 (f" with filter {where}" if where else ""))
            return table, row
        finally:
            conn.close()

    def _pick_seed_table(self) -> str:
        """Pick the table with the most incoming FK references."""
        best_table = None
        best_score = -1

        for tname, tinfo in self.schema.items():
            # Score = number of tables that FK to this one
            score = len(tinfo.foreign_keys_in)
            if score > best_score:
                best_score = score
                best_table = tname

        if best_table is None:
            # No FKs at all — pick any table
            return next(iter(self.schema))

        return best_table

    # -----------------------------------------------------------------
    # BFS FK Walk
    # -----------------------------------------------------------------

    def _walk_fk(
        self,
        seed_table: str,
        seed_row: Dict[str, Any],
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Set[str]]:
        """
        BFS walk from seed row following FK relationships in both directions.

        Returns:
            (result dict, visited_tables set)
        """
        result: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        result[seed_table].append(seed_row)

        visited_tables: Set[str] = {seed_table}
        seen_rows: Set[tuple] = {self._row_identity(seed_table, seed_row)}

        # Queue entries: (table_name, row_dict)
        queue: deque = deque()
        queue.append((seed_table, seed_row))

        conn = self._connect()
        try:
            while queue:
                current_table, current_row = queue.popleft()

                for rel in self.graph.get(current_table, []):
                    join_value = current_row.get(rel.from_column)
                    if join_value is None:
                        continue

                    target_rows = conn.execute(
                        f"SELECT * FROM [{rel.to_table}] WHERE [{rel.to_column}] = ?",
                        (join_value,),
                    ).fetchall()

                    first_visit_to_target = rel.to_table not in visited_tables

                    for row in target_rows:
                        row_key = self._row_identity(rel.to_table, row)
                        if row_key in seen_rows:
                            continue

                        seen_rows.add(row_key)
                        result[rel.to_table].append(row)

                        # Enqueue new rows so their relationships are followed too.
                        # But only if this is a table we haven't fully explored yet,
                        # OR the row itself is new (for tables with multiple seed rows).
                        if first_visit_to_target:
                            queue.append((rel.to_table, row))

                    visited_tables.add(rel.to_table)
        finally:
            conn.close()

        return dict(result), visited_tables

    # -----------------------------------------------------------------
    # Disconnected Table Resolution
    # -----------------------------------------------------------------

    def _is_eligible_for_matching(self, column_name: str) -> bool:
        """
        Check if a column is a good candidate for cross-table matching.

        Eligible if it is a PK in any table, or an FK column in any table,
        or its name matches a PK column name in another table.
        """
        return column_name in self._all_pk_columns or column_name in self._all_fk_columns

    def _build_value_index(
        self,
        result: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Set[Any]]:
        """
        Build an index of eligible column values from already-sampled data.

        Returns:
            {column_name: {value1, value2, ...}} for eligible columns only.
        """
        index: Dict[str, Set[Any]] = defaultdict(set)

        for rows in result.values():
            for row in rows:
                for col, val in row.items():
                    if val is not None and self._is_eligible_for_matching(col):
                        index[col].add(val)

        return dict(index)

    def _resolve_disconnected(
        self,
        result: Dict[str, List[Dict[str, Any]]],
        visited_tables: Set[str],
        independent_sample_limit: int,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Attempt to link unvisited tables via column-name + value matching.
        """
        unvisited = set(self.schema.keys()) - visited_tables
        if not unvisited:
            return result

        value_index = self._build_value_index(result)

        conn = self._connect()
        try:
            # Keep iterating because resolving one table may enable resolving another
            # (e.g., resolving flights enables resolving flight_dates via FK walk)
            progress = True
            while progress and unvisited:
                progress = False

                for table in list(unvisited):
                    tinfo = self.schema[table]
                    matched = False

                    for col_name in tinfo.columns:
                        if not self._is_eligible_for_matching(col_name):
                            continue

                        values = value_index.get(col_name)
                        if not values:
                            continue

                        # Query matching rows
                        placeholders = ",".join("?" for _ in values)
                        rows = conn.execute(
                            f"SELECT * FROM [{table}] WHERE [{col_name}] IN ({placeholders})",
                            list(values),
                        ).fetchall()

                        if rows:
                            result.setdefault(table, []).extend(rows)
                            unvisited.discard(table)
                            visited_tables.add(table)
                            matched = True
                            progress = True

                            # Update value index with new data
                            for row in rows:
                                for c, v in row.items():
                                    if v is not None and self._is_eligible_for_matching(c):
                                        value_index.setdefault(c, set()).add(v)

                            # BFS from new rows to pick up further FK children
                            self._extend_from_new_rows(
                                conn, table, rows, result, visited_tables,
                                unvisited, value_index,
                            )
                            break  # Move to next unvisited table

                    if not matched:
                        continue

            # Anything still unvisited: sample independently
            for table in list(unvisited):
                rows = conn.execute(
                    f"SELECT * FROM [{table}] ORDER BY RANDOM() LIMIT ?",
                    (independent_sample_limit,),
                ).fetchall()
                if rows:
                    result.setdefault(table, []).extend(rows)
                    visited_tables.add(table)
                    unvisited.discard(table)

                    self._extend_from_new_rows(
                        conn, table, rows, result, visited_tables,
                        unvisited, value_index,
                    )

        finally:
            conn.close()

        return result

    def _extend_from_new_rows(
        self,
        conn: sqlite3.Connection,
        table: str,
        rows: List[Dict[str, Any]],
        result: Dict[str, List[Dict[str, Any]]],
        visited_tables: Set[str],
        unvisited: Set[str],
        value_index: Dict[str, Set[Any]],
    ) -> None:
        """
        BFS from newly added rows to pick up FK children/parents
        that are still unvisited.
        """
        queue: deque = deque()
        seen_rows: Set[tuple] = set()

        # Seed seen_rows with everything already in result
        for t, rs in result.items():
            for r in rs:
                seen_rows.add(self._row_identity(t, r))

        for row in rows:
            queue.append((table, row))

        while queue:
            current_table, current_row = queue.popleft()

            for rel in self.graph.get(current_table, []):
                join_value = current_row.get(rel.from_column)
                if join_value is None:
                    continue

                target_rows = conn.execute(
                    f"SELECT * FROM [{rel.to_table}] WHERE [{rel.to_column}] = ?",
                    (join_value,),
                ).fetchall()

                first_visit = rel.to_table not in visited_tables

                for row in target_rows:
                    row_key = self._row_identity(rel.to_table, row)
                    if row_key in seen_rows:
                        continue

                    seen_rows.add(row_key)
                    result.setdefault(rel.to_table, []).append(row)

                    # Update value index
                    for c, v in row.items():
                        if v is not None and self._is_eligible_for_matching(c):
                            value_index.setdefault(c, set()).add(v)

                    if first_visit:
                        queue.append((rel.to_table, row))

                visited_tables.add(rel.to_table)
                unvisited.discard(rel.to_table)

    # -----------------------------------------------------------------
    # Main Entry Point
    # -----------------------------------------------------------------

    def walk(
        self,
        seed_table: Optional[str] = None,
        seed_where: Optional[Dict[str, Any]] = None,
        resolve_disconnected: bool = True,
        independent_sample_limit: int = 3,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Walk the database starting from a seed row, collecting all related data.

        Args:
            seed_table: Table to start from. None = auto-select the most
                        central table (most incoming FKs).
            seed_where: Optional filter dict {column: value} for seed row.
            resolve_disconnected: Whether to attempt column-name matching
                                  for tables not reachable via FK.
            independent_sample_limit: Number of rows to independently sample
                                      from truly disconnected tables.

        Returns:
            Dict of table_name -> list of row dicts.
        """
        # 1. Discover schema (cached)
        _ = self.schema
        _ = self.graph

        # 2. Sample seed
        seed_table_name, seed_row = self.sample_seed(seed_table, seed_where)

        # 3. BFS walk via FKs
        result, visited = self._walk_fk(seed_table_name, seed_row)

        # 4. Resolve disconnected tables
        if resolve_disconnected:
            result = self._resolve_disconnected(result, visited, independent_sample_limit)

        return result

    # -----------------------------------------------------------------
    # Convenience Methods
    # -----------------------------------------------------------------

    def get_table_names(self) -> List[str]:
        return list(self.schema.keys())

    def get_schema_summary(self) -> str:
        lines = []
        for tname, tinfo in self.schema.items():
            pk_str = ", ".join(tinfo.primary_keys) or "(no PK)"
            lines.append(f"{tname} [PK: {pk_str}]")
            for col in tinfo.columns.values():
                flags = []
                if col.is_pk:
                    flags.append("PK")
                if col.notnull:
                    flags.append("NOT NULL")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                lines.append(f"  {col.name} {col.col_type}{flag_str}")
            for fk in tinfo.foreign_keys_out:
                lines.append(f"  FK: {fk.from_column} -> {fk.to_table}.{fk.to_column}")
            lines.append("")
        return "\n".join(lines)

    def get_disconnected_clusters(self) -> List[Set[str]]:
        """Find connected components in the FK relationship graph."""
        all_tables = set(self.schema.keys())
        visited: Set[str] = set()
        clusters: List[Set[str]] = []

        for table in all_tables:
            if table in visited:
                continue

            # BFS to find connected component
            component: Set[str] = set()
            queue: deque = deque([table])
            while queue:
                current = queue.popleft()
                if current in component:
                    continue
                component.add(current)
                for rel in self.graph.get(current, []):
                    if rel.to_table not in component:
                        queue.append(rel.to_table)

            visited |= component
            clusters.append(component)

        return clusters
