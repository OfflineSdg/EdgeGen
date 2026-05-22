"""Claude Code-based test case verifier and fixer.

Replaces both ExecutionVerifier and ToolChainVerifier.verify_alignment.
Instead of just flagging issues, Claude Code verifies AND fixes the test case
in a single pass — it has DB access and the full policy, so it can correct
grading notes, parameter values, and policy claims directly.
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..offline.models.pipeline_models import (
    ConstraintViolation,
    TestCase,
    ToolCall,
    GradingNote,
    VerificationResult,
    ViolationSeverity,
)

logger = logging.getLogger(__name__)

VERIFY_AND_FIX_PROMPT_TEMPLATE = """You are verifying and fixing a generated test case for an agent evaluation.

## Test Case
```json
{test_case_json}
```

## Policy Document
{policy_content}

## Database Path
{db_path}

## CRITICAL: READ-ONLY ACCESS
The database is READ-ONLY. You MUST only use SELECT queries. Do NOT run INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or any other write operation. Use: sqlite3 "file:{db_path}?mode=ro" "SELECT ..."

## Your Job

Run sqlite3 queries against the database to verify this test case. If you find issues, FIX THEM and produce a corrected version.

### Checks to perform:

1. **Data Grounding** — Every parameter value in expectedTools must exist in the DB:
   - user_id exists in users table
   - reservation_id exists in reservations table
   - flight_number exists in flights table
   - dates exist in flight_dates table with status='available'
   - payment_id exists in payment_methods table
   - passenger names/DOBs match saved_passengers or reservation_passengers

2. **Scenario Feasibility** — The full workflow must execute end-to-end:
   - If booking: flight exists on specified route AND date
   - If round-trip: BOTH directions have flights on valid dates
   - If modification: reservation exists and is not cancelled
   - Tool sequence must be logically consistent (no cancel-then-modify)

3. **Subgoal / Grading Note Correctness** — Each grading note must be:
   - Factually correct given the DB state (check membership levels, baggage allowances, pricing)
   - Consistent with the policy document (extra bags cost what the policy says, not made-up values)
   - Internally consistent (no contradictions between notes)
   - Achievable given the tool chain

4. **Policy Alignment** — Claims about what the agent should do must match actual policy:
   - Baggage: check membership level from DB, then look up allowance in policy
   - Compensation: check eligibility per policy (cancelled flights, delayed flights, passenger count)
   - Denials: confirm the policy actually says to deny in this specific situation

5. **Tool Call Justification** — For EVERY tool in expectedTools, verify it is justified:
   - For each action tool (send_certificate, cancel_reservation, book_reservation, update_*):
     a. What is the REASON in the scenario for calling this tool?
     b. Does the policy ALLOW this action given the current DB state?
     c. For send_certificate specifically: what flight was cancelled or delayed? Query flight_dates for the actual status. If no flights are cancelled/delayed, the certificate is UNJUSTIFIED — flag it.
     d. For cancel_reservation: does the scenario meet one of the cancellation eligibility criteria from policy? (within 24h, business class, airline cancelled, or has insurance with covered reason)
   - If a tool has no justification in the scenario/policy, either REMOVE it from expectedTools and update the task summary, or flag as unfixable.

6. **Tool Chain Logic** — No contradictory operations:
   - Cannot modify a reservation after cancelling it
   - Cannot issue certificate without meeting compensation eligibility
   - Each tool's parameters must be consistent with previous tools' outputs

### Output

Write a JSON file to `{output_path}` with this structure:

If the test case is CORRECT as-is:
```json
{{
  "status": "valid",
  "fixed_test_case": null
}}
```

If the test case has issues but you CAN fix them:
```json
{{
  "status": "fixed",
  "issues_found": ["description of each issue fixed"],
  "fixed_test_case": {{
    "id": "same id",
    "input": [same structure with corrected content if needed],
    "metadata": {{
      "subgoals": [corrected grading notes],
      "expectedTools": [corrected tool parameters]
    }},
    "domain": "",
    "violated_subgoals": {{corrected if needed}}
  }}
}}
```

If the test case is UNFIXABLE (fundamentally infeasible scenario):
```json
{{
  "status": "rejected",
  "issues_found": ["why it cannot be fixed"]
}}
```

IMPORTANT: When fixing, use ONLY real values from the database. Query the DB to get correct values.
IMPORTANT: "Fixing" means ONLY modifying the test case JSON (task summary, grading notes, expected tools, violated_subgoals). You must NEVER modify, insert, update, or delete any data in the database. The database is the source of truth — adapt the test case to match the DB, not the other way around.
"""


class ClaudeVerifier:
    """Verify and fix test cases using Claude Code.

    Returns either the original test case (if valid), a fixed version,
    or a rejection with reasons.
    """

    def __init__(
        self,
        db_path: str | Path,
        policy_content: str = "",
        model: str = "anthropic--claude-4.6-sonnet",
        max_turns: int = 25,
        log_dir: str | Path | None = None,
    ):
        self.db_path = Path(db_path)
        self.policy_content = policy_content
        self.model = model
        self.max_turns = max_turns
        self.log_dir = Path(log_dir) if log_dir else None
        self._call_count = 0

    async def verify_and_fix(self, test_case: TestCase) -> tuple[str, TestCase | None, list[str]]:
        """Verify a test case and fix it if possible.

        Returns:
            Tuple of (status, fixed_test_case_or_none, issues_list)
            status: "valid" | "fixed" | "rejected"
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="claude_vf_"
        ) as f:
            output_path = f.name

        tc_dict = test_case.to_dict()
        test_case_json = json.dumps(tc_dict, indent=2, default=str)

        prompt = VERIFY_AND_FIX_PROMPT_TEMPLATE.format(
            test_case_json=test_case_json,
            policy_content=self.policy_content,
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
                logger.warning("Claude verifier timed out")
                return ("valid", None, [])

            stdout_str = stdout.decode() if stdout else ""
            stderr_str = stderr.decode() if stderr else ""

            # Save Claude Code log
            self._call_count += 1
            if self.log_dir:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                log_file = self.log_dir / f"verifier_{self._call_count:03d}.log"
                with open(log_file, "w") as lf:
                    lf.write(f"=== Test Case ID: {test_case.id} ===\n\n")
                    lf.write(f"=== Return Code: {proc.returncode} ===\n\n")
                    lf.write(f"=== Stdout ===\n{stdout_str}\n\n")
                    lf.write(f"=== Stderr ===\n{stderr_str}\n")

            if proc.returncode != 0:
                logger.warning(f"Claude verifier failed (rc={proc.returncode})")
                logger.warning(f"  stdout (tail): {stdout_str[-300:]}")
                return ("valid", None, [])

            output_file = Path(output_path)
            if output_file.exists():
                with open(output_file) as f:
                    data = json.load(f)
                output_file.unlink()
                return self._parse_result(data, test_case)
            else:
                logger.warning("Claude verifier did not produce output file")
                return ("valid", None, [])

        except Exception as e:
            logger.error(f"Claude verifier error: {e}")
            return ("valid", None, [])
        finally:
            Path(output_path).unlink(missing_ok=True)

    def verify(self, test_case: TestCase) -> VerificationResult:
        """Legacy interface for compatibility."""
        status, _, issues = self.verify_and_fix(test_case)
        is_valid = status != "rejected"
        violations = [
            ConstraintViolation(
                constraint_type="verification",
                tool_name="",
                description=issue,
                severity=ViolationSeverity.ERROR,
            )
            for issue in issues
        ]
        return VerificationResult(is_valid=is_valid, issues=violations)

    def _parse_result(
        self, data: dict, original_test_case: TestCase
    ) -> tuple[str, TestCase | None, list[str]]:
        """Parse Claude's verify-and-fix output."""
        status = data.get("status", "valid")
        issues = data.get("issues_found", [])

        if status == "valid":
            return ("valid", None, [])

        if status == "rejected":
            return ("rejected", None, issues)

        if status == "fixed":
            fixed_data = data.get("fixed_test_case")
            if not fixed_data:
                return ("valid", None, [])

            try:
                fixed_tc = self._rebuild_test_case(fixed_data, original_test_case)
                return ("fixed", fixed_tc, issues)
            except Exception as e:
                logger.warning(f"Failed to parse fixed test case: {e}")
                return ("rejected", None, issues + [f"Parse error: {e}"])

        return ("valid", None, [])

    def _rebuild_test_case(self, fixed_data: dict, original: TestCase) -> TestCase:
        """Rebuild a TestCase from the fixed JSON data."""
        metadata = fixed_data.get("metadata", {})

        grading_notes = []
        for note in metadata.get("subgoals", []):
            grading_notes.append(GradingNote(
                assertion=note.get("details", ""),
                category=note.get("category", "general"),
            ))

        expected_tools = []
        for tool in metadata.get("expectedTools", []):
            expected_tools.append(ToolCall(
                tool_name=tool.get("tool_name", ""),
                parameters=tool.get("parameters", {}),
                expected_output_type=tool.get("expected_output_type", ""),
                order=tool.get("order", 0),
            ))

        task_summary = fixed_data.get("input", [{}])[0].get("content", original.task_summary)

        fixed_tc = TestCase(
            id=fixed_data.get("id", original.id),
            task_summary=task_summary,
            grading_notes=grading_notes or original.grading_notes,
            expected_tools=expected_tools or original.expected_tools,
            violated_subgoals=fixed_data.get("violated_subgoals", original.violated_subgoals),
            domain=fixed_data.get("domain", original.domain),
        )
        return fixed_tc
