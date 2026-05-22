"""Scenario-guided database sampler.

Uses the scenario's context_constraints and tool chain to intelligently
sample the mock database. An LLM writes a SQL query to find the right
seed entity, which is then walked via FK graph.

Key improvements over basic StateSampler:
- LLM writes SQL to find seed entities matching scenario requirements
- No random sampling of disconnected entity tables (avoids noise)
- Reference/lookup tables included in full
- Retry loop if LLM's SQL fails
"""

import json
import logging
import sqlite3
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Optional

from .client.llm_client import LLMClient
from .models.llm_payload import LLMPayload
from .models.pipeline_models import ScenarioContext, SampledPath

from ...mock_system.walker import SQLiteWalker

logger = logging.getLogger(__name__)

REFERENCE_TABLE_MAX_ROWS = 50
MAX_SEED_QUERY_ATTEMPTS = 3


class ScenarioGuidedSampler:
    """Sample database state guided by scenario constraints.

    An LLM writes SQL to find the right seed entity based on scenario
    requirements, then the walker produces a clean FK-connected snapshot.

    :param db_path: Path to the SQLite database.
    :param llm_client: LLM client for SQL generation.
    """

    def __init__(self, db_path: str | Path, llm_client: LLMClient):
        self.db_path = Path(db_path)
        self.llm_client = llm_client
        self.walker = SQLiteWalker(str(self.db_path))
        self._reference_tables: Optional[list[str]] = None

    def get_reference_tables(self) -> list[str]:
        """Detect reference/lookup tables (only incoming FKs, small row count)."""
        if self._reference_tables is not None:
            return self._reference_tables

        schema = self.walker.schema
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            ref_tables = []
            for tname, tinfo in schema.items():
                has_outgoing = len(tinfo.foreign_keys_out) > 0
                if has_outgoing:
                    continue
                count = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM [{tname}]"
                ).fetchone()["cnt"]
                if count <= REFERENCE_TABLE_MAX_ROWS:
                    ref_tables.append(tname)
            self._reference_tables = ref_tables
            return ref_tables
        finally:
            conn.close()

    def _get_schema_ddl(self) -> str:
        """Get the CREATE TABLE statements for the LLM prompt."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            tables = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            return "\n\n".join(row[0] for row in tables if row[0])
        finally:
            conn.close()

    def _get_sample_rows(self) -> str:
        """Get a few sample rows per table so the LLM knows the data shape."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            schema = self.walker.schema
            lines = []
            for tname in schema:
                rows = conn.execute(f"SELECT * FROM [{tname}] LIMIT 2").fetchall()
                if rows:
                    lines.append(f"-- {tname} (sample):")
                    for r in rows:
                        lines.append(f"--   {dict(r)}")
            return "\n".join(lines)
        finally:
            conn.close()

    async def _generate_seed_query(
        self,
        scenario: ScenarioContext,
        sampled_path: SampledPath,
        tool_descriptions: dict[str, str],
        previous_error: Optional[str] = None,
    ) -> Optional[str]:
        """Ask the LLM to write a SQL query to find the right seed entity."""
        schema_ddl = self._get_schema_ddl()
        sample_rows = self._get_sample_rows()

        tool_chain_desc = "\n".join(
            f"  {i+1}. {t} — {tool_descriptions.get(t, '')}"
            for i, t in enumerate(sampled_path.tools)
        )
        constraints_str = json.dumps(scenario.context_constraints, indent=2)

        error_section = ""
        if previous_error:
            error_section = f"""
## Previous Attempt Failed
Error: {previous_error}
Fix the SQL query to avoid this error.
"""

        prompt = f"""Write a SQLite query to find ONE random row from the primary entity table that satisfies the scenario requirements below.

## Scenario Requirements
{constraints_str}

## Tool Chain (tools the agent will call)
{tool_chain_desc}

## Database Schema
{schema_ddl}

## Sample Data
{sample_rows}
{error_section}
## Task
Write a single SELECT query that:
1. Finds a row from the main entity table (usually users or the primary table)
2. Ensures that entity has related data in the tables the tools will access
3. Uses EXISTS subqueries or JOINs to verify related data exists
4. Ends with ORDER BY RANDOM() LIMIT 1

Output ONLY the raw SQL query, no markdown, no explanation:"""

        response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt="You write SQLite queries. Output only the SQL query, no markdown formatting, no explanation, no trailing semicolons.",
            temperature=0.0,
            max_tokens=500,
        ))

        if response.status != HTTPStatus.OK or not response.completion:
            return None

        sql = response.completion.strip()
        if sql.startswith("```"):
            sql = sql.split("\n", 1)[1] if "\n" in sql else sql[3:]
            sql = sql.rsplit("```", 1)[0]
        # Strip trailing semicolons and whitespace
        sql = sql.strip().rstrip(";").strip()
        return sql

    def _execute_seed_query(self, sql: str) -> Optional[Dict[str, Any]]:
        """Execute the LLM-generated SQL and return the seed row."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql).fetchone()
            if row:
                return dict(row)
            return None
        except Exception as e:
            raise RuntimeError(f"SQL execution failed: {e}")
        finally:
            conn.close()

    def _detect_seed_table(self, row: Dict[str, Any]) -> Optional[str]:
        """Detect which table this row belongs to based on column names."""
        schema = self.walker.schema
        row_cols = set(row.keys())
        best_match = None
        best_score = 0
        for tname, tinfo in schema.items():
            table_cols = set(tinfo.columns.keys()) if isinstance(tinfo.columns, dict) else set(tinfo.columns)
            overlap = len(row_cols & table_cols)
            if overlap > best_score:
                best_score = overlap
                best_match = tname
        return best_match

    def _include_reference_tables(self, result: Dict[str, List[Dict[str, Any]]]) -> None:
        """Add all rows from reference tables to the result."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            for table in self.get_reference_tables():
                if table not in result:
                    rows = conn.execute(f"SELECT * FROM [{table}]").fetchall()
                    if rows:
                        result[table] = [dict(r) for r in rows]
        finally:
            conn.close()

    async def sample(
        self,
        scenario: ScenarioContext,
        sampled_path: SampledPath,
        tool_descriptions: dict[str, str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Sample database state guided by scenario constraints.

        1. LLM writes SQL to find the right seed entity
        2. Execute SQL (retry on failure)
        3. Walk FK graph from seed (no disconnected random sampling)
        4. Include reference tables

        :param scenario: The scenario context with constraints.
        :param sampled_path: The tool chain for this test case.
        :param tool_descriptions: Dict of tool_name -> description.
        :return: Dict of table_name -> list of row dicts.
        """
        seed_row = None
        seed_table = None
        last_error = None

        for attempt in range(MAX_SEED_QUERY_ATTEMPTS):
            sql = await self._generate_seed_query(
                scenario, sampled_path, tool_descriptions, previous_error=last_error
            )
            if not sql:
                logger.warning("LLM failed to generate seed query")
                break

            try:
                seed_row = self._execute_seed_query(sql)
                if seed_row:
                    seed_table = self._detect_seed_table(seed_row)
                    logger.info(f"Seed found (attempt {attempt+1}): table={seed_table}, row keys={list(seed_row.keys())[:5]}")
                    break
                else:
                    last_error = "Query returned no rows — no entity matches all constraints"
                    logger.info(f"Seed query returned no rows (attempt {attempt+1}), retrying")
            except RuntimeError as e:
                last_error = str(e)
                logger.info(f"Seed query failed (attempt {attempt+1}): {last_error}")

        # Walk from seed
        if seed_row and seed_table:
            pk_cols = self.walker.schema[seed_table].primary_keys
            if pk_cols and pk_cols[0] in seed_row:
                seed_where = {pk_cols[0]: seed_row[pk_cols[0]]}
            else:
                # PK not in result (LLM didn't SELECT *), use first available column
                first_col = list(seed_row.keys())[0]
                seed_where = {first_col: seed_row[first_col]}

            result = self.walker.walk(
                seed_table=seed_table,
                seed_where=seed_where,
                resolve_disconnected=False,
            )
        else:
            logger.warning("Could not find valid seed, falling back to random walk")
            result = self.walker.walk(resolve_disconnected=False)

        # Always include reference tables
        self._include_reference_tables(result)

        return result
