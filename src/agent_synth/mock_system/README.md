# mock_system

Generic SQLite-backed mock system infrastructure. Provides database connection management and schema-agnostic data walking for any SQLite mock API database.

## Modules

### `db.py` — Connection Manager

Thin wrappers around `sqlite3` that enforce foreign keys, auto-commit/rollback, and return rows as dicts.

| Function | Description |
|---|---|
| `get_conn(db_path)` | Context manager — yields a connection, commits on exit, rolls back on error |
| `init_db(db_path, schema_path)` | Create a database from a `.sql` schema file |
| `reset_db(db_path, schema_path)` | Delete and recreate the database from schema |
| `dict_factory(cursor, row)` | Row factory that converts SQLite rows to `{column: value}` dicts |

```python
from agent_synth.mock_system.db import get_conn, init_db

init_db("myapp.db", "schema.sql")

with get_conn("myapp.db") as conn:
    rows = conn.execute("SELECT * FROM users").fetchall()
    # rows: [{"id": 1, "name": "Alice"}, ...]
```

---

### `walker.py` — SQLite Schema Walker

Discovers schema via PRAGMA introspection (no hardcoded table or column names), samples a seed row, and walks outward through foreign key relationships to collect all related data.

#### `SQLiteWalker`

```python
from agent_synth.mock_system.walker import SQLiteWalker

walker = SQLiteWalker("/path/to/any.db")
result = walker.walk(seed_table="orders")
# result: {"orders": [{...}], "customers": [{...}], "products": [{...}], ...}
```

**`walk(seed_table, seed_where, resolve_disconnected, independent_sample_limit)`**

Main entry point. Samples one seed row and collects all related data via BFS.

| Parameter | Default | Description |
|---|---|---|
| `seed_table` | `None` | Table to start from; auto-selects the most central table (most incoming FKs) if omitted |
| `seed_where` | `None` | Optional `{column: value}` filter for the seed row |
| `resolve_disconnected` | `True` | Whether to match tables not reachable via FK using column-name/value heuristics |
| `independent_sample_limit` | `3` | Max rows to independently sample from truly isolated tables |

**Returns** `Dict[str, List[Dict]]` — one list of row dicts per table.

#### Additional methods

| Method | Description |
|---|---|
| `sample_seed(table, where)` | Pick a random seed row from a table |
| `get_table_names()` | List all table names in the database |
| `get_schema_summary()` | Human-readable schema dump with PKs, types, and FK edges |
| `get_disconnected_clusters()` | Find connected components in the FK graph |

#### Internal data structures

| Class | Description |
|---|---|
| `ColumnInfo` | Column metadata from `PRAGMA table_info` |
| `ForeignKey` | FK relationship from `PRAGMA foreign_key_list` |
| `TableInfo` | Full metadata for one table (columns, PKs, FK in/out) |
| `Relationship` | A directed navigable edge between two tables |

## Design notes

- **Zero domain knowledge** — all schema information comes from PRAGMA introspection at runtime.
- **Bidirectional FK traversal** — walks both parent (outgoing FK) and child (incoming FK) directions.
- **Disconnected table resolution** — if some tables are not FK-reachable from the seed, the walker tries to link them by matching eligible column names and values (PK/FK columns only).
- **Row deduplication** — tracks seen rows by PK identity to avoid duplicates.
- **Schema caching** — `schema` and `graph` are computed once and cached as properties.
