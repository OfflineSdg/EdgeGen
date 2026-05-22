"""Data models for synthetic data generation pipeline."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import uuid

from .agent_data_sample import SubGoal
from .tools_schema import ToolSchema

class SamplingPattern(str, Enum):
    """Graph sampling pattern types."""
    NODE = "node"       # Single tool
    CHAIN = "chain"     # Sequential path
    DAG = "dag"         # Parallel/branching
    
class TestCaseStatus(str, Enum):
    """Status of a test case in HITL workflow."""
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FLAGGED = "flagged"


class Difficulty(str, Enum):
    """Test case difficulty level."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass
class ToolCall:
    """Expected tool call with parameters."""
    tool_name: str
    parameters: dict[str, Any]
    expected_output_type: str
    order: int = 0  # Position in sequence

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "parameters": self.parameters,
            "expected_output_type": self.expected_output_type,
            "order": self.order,
        }


TERMINATING_CONDITION = (
    "The task is considered complete if the instruction goal is satisfied "
    "or you are transferred to another agent or you find yourself in a "
    "situation in which the scenario does not provide enough information "
    "for you to continue the conversation."
)


@dataclass
class Constraint:
    """Business rule/constraint extracted from instructions."""
    rule_type: str  # "precondition", "limit", "conditional"
    description: str
    applies_to: list[str]  # Tool names this constraint applies to
    condition: str | None = None  # For conditional rules


# ============================================================================
# Tool Chain Verification Models
# ============================================================================

class ConstraintType(str, Enum):
    """Types of tool ordering/usage constraints."""
    MUST_BE_FIRST = "must_be_first"
    MUST_BE_LAST = "must_be_last"
    MUST_FOLLOW = "must_follow"
    REQUIRES_PREDECESSOR = "requires_predecessor"
    CONFLICTS_WITH = "conflicts_with"


class ViolationSeverity(str, Enum):
    """Severity level of constraint violations."""
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ExtractedConstraint:
    """A constraint extracted from a tool's description by LLM.

    Represents ordering or usage constraints that must be respected
    when sampling tool chains.
    """
    tool_name: str
    constraint_type: ConstraintType
    depends_on: str | None = None  # Tool name for MUST_FOLLOW/REQUIRES_PREDECESSOR
    conflicts_with: str | None = None  # Tool name for CONFLICTS_WITH
    description: str = ""  # Human-readable explanation

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "constraint_type": self.constraint_type.value,
            "depends_on": self.depends_on,
            "conflicts_with": self.conflicts_with,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExtractedConstraint":
        return cls(
            tool_name=data["tool_name"],
            constraint_type=ConstraintType(data["constraint_type"]),
            depends_on=data.get("depends_on"),
            conflicts_with=data.get("conflicts_with"),
            description=data.get("description", ""),
        )


@dataclass
class ConstraintViolation:
    """A constraint violation found during tool chain verification."""
    constraint_type: str  # "ordering", "data_flow", "alignment"
    tool_name: str
    description: str
    severity: ViolationSeverity

    def to_dict(self) -> dict:
        return {
            "constraint_type": self.constraint_type,
            "tool_name": self.tool_name,
            "description": self.description,
            "severity": self.severity.value,
        }


@dataclass
class VerificationResult:
    """Result of tool chain verification.

    Contains validation status, any issues found, and optionally
    a fixed chain suggested by the LLM.
    """
    is_valid: bool
    issues: list[ConstraintViolation]
    fixed_chain: list[str] | None = None  # LLM-suggested fixed tool order
    confidence: float = 1.0  # Confidence score for LLM-based verification

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "issues": [i.to_dict() for i in self.issues],
            "fixed_chain": self.fixed_chain,
            "confidence": self.confidence,
        }


@dataclass
class GradingNote:
    """Verifiable assertion for test case evaluation."""
    assertion: str
    category: str  # "tool_call", "communication", "verification", "policy"

    def to_dict(self) -> dict:
        return {
            "type": "gradingNotes",
            "details": self.assertion,
            "turn": "all",
        }


@dataclass
class TestCase:
    """Generated test case for agent evaluation."""
    id: str
    task_summary: str
    grading_notes: list[GradingNote]
    expected_tools: list[ToolCall]
    # Violation test metadata (optional)
    violated_subgoals: dict[str, str] = field(default_factory=dict)
    domain: str = ""

    @classmethod
    def create(
        cls,
        task_summary: str,
        grading_notes: list[GradingNote],
        expected_tools: list[ToolCall],
    ) -> "TestCase":
        """Factory method to create a new test case with generated ID."""
        return cls(
            id=str(uuid.uuid4())[:8],
            task_summary=task_summary,
            grading_notes=grading_notes,
            expected_tools=expected_tools,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "id": self.id,
            "input": [
                {
                    "role": "user",
                    "content": self.task_summary,
                    "terminating_condition": TERMINATING_CONDITION,
                }
            ],
            "metadata": {
                "subgoals": [gn.to_dict() for gn in self.grading_notes],
                "expectedTools": [tc.to_dict() for tc in self.expected_tools],
            },
            "domain": self.domain,
        }
        # Add violation test metadata if present
        if self.violated_subgoals:
            result["violated_subgoals"] = self.violated_subgoals
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TestCase":
        """Create TestCase from dictionary."""
        # Prefer task_summary field, fallback to input[0].content
        task_summary = data.get("task_summary")
        if not task_summary and "input" in data:
            task_summary = data["input"][0]["content"]

        return cls(
            id=data["id"],
            task_summary=task_summary,
            grading_notes=[
                GradingNote(
                    assertion=gn["details"],
                    category=gn.get("category", "general"),
                )
                for gn in data["grading_notes"]
            ],
            expected_tools=[
                ToolCall(
                    tool_name=tc["tool_name"],
                    parameters=tc["parameters"],
                    expected_output_type=tc["expected_output_type"],
                    order=tc.get("order", 0),
                )
                for tc in data["expected_tools"]
            ],
            # Violation test metadata
            violated_subgoals=data.get("violated_subgoals", {}),
        )


@dataclass
class ToolGraph:
    """Graph representation of tools and their dependencies."""
    tools: dict[str, ToolSchema]
    edges: list[tuple[str, str, dict]]  # (from_tool, to_tool, edge_attrs)
    constraints: list[Constraint]

    def get_entry_tools(self) -> list[str]:
        """Get tools that can be entry points (no incoming dependencies)."""
        has_incoming = {edge[1] for edge in self.edges}
        return [name for name in self.tools if name not in has_incoming]

    def get_successors(self, tool_name: str) -> list[str]:
        """Get tools that can follow the given tool."""
        return [edge[1] for edge in self.edges if edge[0] == tool_name]

    def get_predecessors(self, tool_name: str) -> list[str]:
        """Get tools that must precede the given tool."""
        return [edge[0] for edge in self.edges if edge[1] == tool_name]


@dataclass
class SampledPath:
    """A sampled path through the tool graph."""
    pattern: SamplingPattern
    tools: list[str]  # Ordered list of tool names
    parallel_groups: list[list[str]] = field(default_factory=list)  # For DAG: groups that can run in parallel

    def __len__(self) -> int:
        return len(self.tools)


@dataclass
class GenerationContext:
    """Context for generating a test case."""
    sampled_path: SampledPath
    db_snapshot: dict[str, list[dict[str, Any]]]  # table_name -> rows from SQLiteWalker
    domain_context: str  # Extracted from instructions
    scenario_context: "ScenarioContext | None" = None  # For violation tests
    db_relationships: str = ""  # FK relationship annotations for LLM grounding


# ============================================================================
# Online Subgoal Violation Models
# ============================================================================
@dataclass
class OnlineSubgoal(SubGoal):
    """An online subgoal (business rule) that the agent must enforce.

    Extends :obj:`SubGoal` with additional fields for violation testing.
    Inherits ``details`` and ``category`` from :obj:`SubGoal`.
    """
    id: Optional[str] = None         # "SG1", "SG2", ... (auto-assigned by SubgoalParser)
    # LLM-extracted fields (populated by SubgoalParser):
    required_context: dict[str, Any] = field(default_factory=dict)  # Scenario setup needed
    expected_behavior: str = ""      # What agent should do when enforcing

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "details": self.details,
            "category": self.category.value if self.category else None,
            "required_context": self.required_context,
            "expected_behavior": self.expected_behavior
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OnlineSubgoal":
        return cls(
            details=data["details"],
            id=data.get("id"),
            required_context=data.get("required_context", {}),
            expected_behavior=data.get("expected_behavior", "")
        )

# ============================================================================
# Violation Models
# ============================================================================


@dataclass
class ViolationCombination:
    """A combination of subgoals to test together.

    For n subgoals, we generate 2^n-1 combinations (powerset excluding empty set).
    Each combination represents a test scenario that validates enforcement of
    the selected subgoals simultaneously.
    """
    index: int                       # Binary index (1 to 2^n-1)
    binary_mask: str                 # e.g., "011" indicating which subgoals
    subgoals: list["OnlineSubgoal"]  # The selected subgoals
    is_valid: bool = True            # False if subgoals conflict
    conflict_reason: str | None = None  # LLM-generated explanation if invalid

    def get_subgoal_ids(self) -> list[str]:
        """Get list of subgoal IDs derived from binary mask positions."""
        return [f"SG{j + 1}" for j, bit in enumerate(self.binary_mask) if bit == '1']

    def get_subgoal_map(self) -> dict[str, str]:
        """Get a dictionary mapping subgoal IDs to their details.

        Returns:
            Dictionary with keys like "SG1", "SG2" and values being the subgoal details.
        """
        result = {}
        sg_index = 0
        for j, bit in enumerate(self.binary_mask):
            if bit == '1':
                sg_id = f"SG{j + 1}"
                if sg_index < len(self.subgoals):
                    result[sg_id] = self.subgoals[sg_index].details
                sg_index += 1
        return result

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "binary_mask": self.binary_mask,
            "subgoal_ids": self.get_subgoal_ids(),
            "is_valid": self.is_valid,
            "conflict_reason": self.conflict_reason
        }


@dataclass
class ScenarioContext:
    """Context constraints for violation test case generation.

    This captures the scenario setup required to test specific online subgoals.
    """
    violated_subgoals: dict[str, str]     # {"SG1": "subgoal details", "SG3": "subgoal details"} - IDs to details mapping
    combination_index: int           # Binary index from ViolationCombination
    context_constraints: dict[str, Any] = field(default_factory=dict)  # e.g., {"cabin": "basic_economy"}
    expected_behaviors: list[str] = field(default_factory=list)  # What agent should do for each subgoal

    def to_dict(self) -> dict:
        return {
            "violated_subgoals": self.violated_subgoals,
            "combination_index": self.combination_index,
            "context_constraints": self.context_constraints,
            "expected_behaviors": self.expected_behaviors
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScenarioContext":
        return cls(
            violated_subgoals=data["violated_subgoals"],
            combination_index=data["combination_index"],
            context_constraints=data.get("context_constraints", {}),
            expected_behaviors=data.get("expected_behaviors", [])
        )
