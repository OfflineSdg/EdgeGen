"""Claude Code-based scenario-guided DB sampler.

Replaces ScenarioGuidedSampler. Instead of using LLM to generate SQL queries
and then walking FK graphs, this invokes Claude Code which can directly query
the database with sqlite3, understand the scenario requirements, and return
a verified DB snapshot with all necessary entities.
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SAMPLER_PROMPT_TEMPLATE = """You are sampling database state for a test case scenario.

## Scenario Context
{scenario_description}

## Required Tool Chain
{tool_chain}

## Tool Descriptions
{tool_descriptions}

## Database Schema
{db_schema}

## Database Path
{db_path}

## CRITICAL: READ-ONLY ACCESS
The database is READ-ONLY. You MUST only use SELECT queries. Do NOT run INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or any other write operation. Use: sqlite3 "file:{db_path}?mode=ro" "SELECT ..."

## Task
Using the schema above, query the database with sqlite3 to find entities that match this scenario.
Skip schema exploration — you already have it. Go directly to finding the right data.

You must:
1. Find a real entity that fits the scenario requirements for the given tool chain
2. Retrieve all related data via JOINs following foreign key relationships
3. Verify that the scenario is FEASIBLE with this data:
   - If the tool chain involves searching or creating records: confirm the prerequisites exist (e.g., valid lookup keys, available dates, existing routes)
   - If the workflow is multi-step: confirm each step's output can feed the next step's input
   - If the scenario involves denial/rejection: confirm the data state actually triggers that denial per business rules
4. If no suitable entity exists for a feasible scenario, output {{"error": "no_suitable_entity", "reason": "..."}}

## Output Format

Write the JSON to `{output_path}`.

The output MUST include:
1. All relevant table data as `"table_name": [row_dicts]`
2. A special `"_feasibility"` key that summarizes what operations are actually achievable with this data

The `_feasibility` object should contain:
- What entities were found and their key properties
- What operations the tool chain can successfully perform given this data
- Any constraints or limitations (e.g., dates only available in a certain range, certain statuses)
- A plain-language summary of what a coherent scenario looks like with this data

Include ONLY rows relevant to this specific scenario. Do not dump the entire database.
"""


class ClaudeGuidedSampler:
    """Sample DB state using Claude Code for intelligent scenario-aware queries.

    Drop-in replacement for ScenarioGuidedSampler. Same interface:
    `await sampler.sample(scenario, path, tool_descriptions) -> dict`
    """

    def __init__(
        self,
        db_path: str | Path,
        model: str = "claude-sonnet-4-6",
        max_turns: int = 60,
        log_dir: str | Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.model = model
        self.max_turns = max_turns
        self.log_dir = Path(log_dir) if log_dir else None
        self._call_count = 0
        self._db_schema = self._load_schema()

    def _load_schema(self) -> str:
        """Load DB schema once at init to avoid wasting turns on exploration."""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")
        schema_parts = [row[0] for row in cursor.fetchall()]
        conn.close()
        return "\n\n".join(schema_parts)

    async def sample(
        self,
        scenario: Any,
        path: Any,
        tool_descriptions: dict[str, str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Sample database state for the given scenario using Claude Code.

        Args:
            scenario: ScenarioContext with violated_subgoals and constraints
            path: SampledPath with ordered tool names
            tool_descriptions: dict of tool_name -> description

        Returns:
            Dict of table_name -> list of row dicts (same as StateSampler/ScenarioGuidedSampler)
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="claude_sample_"
        ) as f:
            output_path = f.name

        scenario_desc = ""
        if scenario:
            if hasattr(scenario, "violated_subgoals"):
                scenario_desc += "Violated subgoals:\n"
                for sg_id, details in scenario.violated_subgoals.items():
                    scenario_desc += f"  {sg_id}: {details}\n"
            if hasattr(scenario, "context_constraints"):
                scenario_desc += f"\nContext constraints: {scenario.context_constraints}\n"
            if hasattr(scenario, "expected_behaviors"):
                scenario_desc += f"Expected behaviors: {scenario.expected_behaviors}\n"

        tool_chain_str = " -> ".join(path.tools) if hasattr(path, "tools") else str(path)
        tool_desc_str = "\n".join(f"- {name}: {desc}" for name, desc in tool_descriptions.items())

        prompt = SAMPLER_PROMPT_TEMPLATE.format(
            scenario_description=scenario_desc or "General test case (no specific violation scenario)",
            tool_chain=tool_chain_str,
            tool_descriptions=tool_desc_str,
            db_schema=self._db_schema,
            db_path=str(self.db_path),
            output_path=output_path,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--print",
                "--permission-mode", "auto",
                "--model", self.model,
                "--max-turns", str(self.max_turns),
                "--verbose",
                "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.db_path.parent.parent),
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                logger.warning("Claude Code sampler timed out")
                return {}

            stdout_str = stdout.decode() if stdout else ""
            stderr_str = stderr.decode() if stderr else ""

            # Save Claude Code log
            self._call_count += 1
            if self.log_dir:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                log_file = self.log_dir / f"sampler_{self._call_count:03d}.log"
                with open(log_file, "w") as lf:
                    lf.write(f"=== Prompt ===\n{prompt}\n\n")
                    lf.write(f"=== Return Code: {proc.returncode} ===\n\n")
                    lf.write(f"=== Stdout ===\n{stdout_str}\n\n")
                    lf.write(f"=== Stderr ===\n{stderr_str}\n")

            if proc.returncode != 0:
                logger.warning(f"Claude Code sampler failed (rc={proc.returncode})")
                logger.warning(f"  stderr: {stderr_str[:500]}")
                logger.warning(f"  stdout (tail): {stdout_str[-500:]}")
                return {}

            output_file = Path(output_path)
            if output_file.exists() and output_file.stat().st_size > 0:
                with open(output_file) as f:
                    data = json.load(f)
                output_file.unlink()
                if "error" in data:
                    logger.warning(f"Claude sampler found no suitable entity: {data.get('reason', '')}")
                    return {}
                return data
            else:
                logger.warning("Claude Code sampler did not produce output file")
                return {}

        except Exception as e:
            logger.error(f"Claude Code sampler error: {e}")
            return {}
        finally:
            Path(output_path).unlink(missing_ok=True)
