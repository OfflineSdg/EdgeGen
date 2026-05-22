"""Execution-based test case verifier.

Executes the expected tool chain against the mock database to verify
that all parameter values are grounded in real data. Only errors that
indicate hallucinated or missing data are treated as failures. Domain-
specific business logic errors (e.g., insufficient inventory, policy
violations) are allowed to pass through, as they may represent valid
test scenarios.

The verifier is side-effect-free: it snapshots the DB before execution
and rolls back afterward, regardless of success or failure.
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from .models.pipeline_models import (
    ConstraintViolation,
    TestCase,
    VerificationResult,
    ViolationSeverity,
)

logger = logging.getLogger(__name__)

# Default patterns that indicate data grounding failures (hallucinated values).
# These are framework-level errors that occur when referenced entities don't
# exist or when the call itself is malformed. They are NOT domain-specific.
DEFAULT_GROUNDING_ERROR_PATTERNS: list[str] = [
    r"not found",
    r"missing \d+ required positional argument",
    r"missing required positional argument",
    r"Field required",
    r"validation error",
    r"invalid literal",
    r"unexpected keyword argument",
    r"got an unexpected keyword argument",
    r"takes \d+ positional arguments? but \d+ (?:was|were) given",
    r"Invalid characters",
]


class ExecutionVerifier:
    """Verify test cases by executing the tool chain against the mock DB.

    Only errors matching grounding_error_patterns are treated as verification
    failures (hallucinated data). All other errors are assumed to be domain-
    specific business logic (valid test scenarios) and are ignored.

    :param execute_tool: callable that takes (tool_name, parameters) and returns
        a tuple of (error: bool, content: str). This decouples the verifier from
        the specific Environment implementation.
    :param db_path: path to the SQLite database file used by the mock system.
    :param reload_fn: callable invoked after DB rollback to refresh any in-memory
        state that was loaded from the DB file.
    :param grounding_error_patterns: regex patterns that identify data grounding
        errors (hallucination). If None, uses DEFAULT_GROUNDING_ERROR_PATTERNS.
        Errors not matching any pattern are considered domain logic and ignored.
    """

    def __init__(
        self,
        execute_tool: Callable[[str, dict[str, Any]], tuple[bool, str]],
        db_path: Path,
        reload_fn: Optional[Callable[[], None]] = None,
        grounding_error_patterns: Optional[list[str]] = None,
    ):
        self.execute_tool = execute_tool
        self.db_path = Path(db_path)
        self.snapshot_path = self.db_path.with_suffix(".db.verification_snapshot")
        self.reload_fn = reload_fn
        patterns = grounding_error_patterns or DEFAULT_GROUNDING_ERROR_PATTERNS
        self._grounding_re = re.compile(
            "|".join(f"({p})" for p in patterns), re.IGNORECASE
        )

    def _is_grounding_error(self, error_content: str) -> bool:
        """Check if an error indicates hallucinated/missing data.

        Returns True for framework-level errors (not found, missing args,
        type validation). Returns False for domain-specific business logic
        errors (not enough seats, policy violations, etc.).
        """
        return bool(self._grounding_re.search(error_content))

    def _save_snapshot(self) -> None:
        shutil.copy2(self.db_path, self.snapshot_path)

    def _rollback(self) -> None:
        shutil.copy2(self.snapshot_path, self.db_path)
        if self.reload_fn:
            self.reload_fn()

    def _cleanup_snapshot(self) -> None:
        if self.snapshot_path.exists():
            self.snapshot_path.unlink()

    def verify(self, test_case: TestCase) -> VerificationResult:
        """Execute the expected tool chain and check for grounding errors.

        Tools are executed sequentially in order. On the first grounding
        error (hallucinated data), execution stops. Domain-specific errors
        are logged but do not cause verification failure.

        :param test_case: the generated test case to verify.
        :return: VerificationResult with is_valid=True if no grounding errors
            were found during execution.
        """
        self._save_snapshot()
        issues: list[ConstraintViolation] = []

        try:
            for tool_call in test_case.expected_tools:
                tool_name = tool_call.tool_name
                params = tool_call.parameters

                try:
                    error, content = self.execute_tool(tool_name, params)
                except Exception as e:
                    error_str = str(e)
                    if self._is_grounding_error(error_str):
                        issues.append(ConstraintViolation(
                            constraint_type="execution_error",
                            tool_name=tool_name,
                            description=f"Tool raised exception: {error_str}",
                            severity=ViolationSeverity.ERROR,
                        ))
                        break
                    else:
                        logger.debug(
                            f"Domain error in {tool_name} (allowed): {error_str}"
                        )
                        continue

                if error:
                    if self._is_grounding_error(content):
                        issues.append(ConstraintViolation(
                            constraint_type="execution_error",
                            tool_name=tool_name,
                            description=f"Execution failed with params {params}: {content}",
                            severity=ViolationSeverity.ERROR,
                        ))
                        break
                    else:
                        logger.debug(
                            f"Domain error in {tool_name} (allowed): {content}"
                        )
        finally:
            self._rollback()
            self._cleanup_snapshot()

        return VerificationResult(
            is_valid=len(issues) == 0,
            issues=issues,
        )
