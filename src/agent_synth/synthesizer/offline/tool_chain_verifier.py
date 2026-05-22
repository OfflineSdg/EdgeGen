"""Tool chain verification using LLM-as-judge approach.

Provides two-phase verification for sampled tool chains:
1. Pre-generation: Verify ordering constraints and data flow
2. Post-generation: Verify task-tool alignment after generating task summary

Inspired by TaskBench (arxiv:2311.18760) alignment metrics and LLM self-critique mechanisms.
"""

import json
import logging
from http import HTTPStatus
from typing import Any

from .client.llm_client import LLMClient
from .models.llm_payload import LLMPayload
from .models.tools_schema import ToolSchema
from .models.pipeline_models import (
    SampledPath,
    ToolGraph,
    ExtractedConstraint,
    ConstraintViolation,
    VerificationResult,
    ConstraintType,
    ViolationSeverity,
)

logger = logging.getLogger(__name__)


# ============================================================================
# System Prompts
# ============================================================================

CONSTRAINT_EXTRACTION_SYSTEM_PROMPT = """You are an expert at analyzing tool API documentation to identify ordering and usage constraints.

Your task is to extract explicit or strongly implied constraints from tool descriptions that dictate:
- When a tool MUST be called first in any workflow
- When a tool MUST be called after another specific tool
- When a tool REQUIRES another tool to have been called first
- When a tool CONFLICTS with another tool (cannot be used together)
- When a tool MUST be the last tool in a workflow

Be conservative: only extract constraints that are EXPLICIT or STRONGLY IMPLIED in the description.
Do NOT infer constraints from general logic - focus on what the description actually states."""


CHAIN_REFINEMENT_SYSTEM_PROMPT = """You are an expert at fixing tool chain ordering issues.

Your task is to take an invalid tool chain and the constraint violations found, then produce a corrected chain that:
1. Satisfies all ordering constraints (e.g., tool X must come before tool Y)
2. Removes conflicting tools if necessary
3. Adds any required predecessor tools that are missing
4. Maintains logical data flow between tools

If the chain cannot be fixed while keeping all required tools, you may:
- Remove tools that cause unresolvable conflicts
- Add required predecessor tools

Return the fixed chain as a JSON array of tool names in the correct order."""


ALIGNMENT_VERIFICATION_SYSTEM_PROMPT = """You verify that a generated task description can be fulfilled by an agent using the prescribed tool chain.

The agent has access to:
1. The prescribed tools (for data retrieval, modifications, and actions)
2. Its own policy/domain knowledge (for answering questions about rules, fees, allowances, eligibility)

You must check:

1. SUFFICIENCY: Can the agent handle this task using these tools PLUS its own knowledge?
   - Tools are needed for: looking up specific records, modifying data, executing actions, searching
   - The agent's OWN KNOWLEDGE handles: explaining policies, stating rules, calculating fees it already knows, denying requests with reasons
   - Only flag insufficiency if the task requires RETRIEVING SPECIFIC DATA from a system (a particular user's record, a specific flight's status, a particular reservation) and NO tool in the chain can do that retrieval.

2. RELEVANCE: Does each tool in the chain serve a clear purpose for this task?
   - A tool is relevant if the task involves an action the tool performs OR if the agent would reasonably call it as part of handling the request
   - A tool is NOT relevant only if it has absolutely no connection to any aspect of the task

A task is ALIGNED if:
- The tools (combined with the agent's knowledge) are sufficient to handle the task
- Each tool has at least some logical connection to the task

A task is NOT ALIGNED only if:
- The task requires looking up or modifying SPECIFIC records/data that no tool can access (e.g., task says "check my reservation" but no tool can retrieve reservations)
- A tool in the chain is completely unrelated to any aspect of the task (e.g., "calculate" when the task has nothing involving numbers or computation)"""


# ============================================================================
# Main Verifier Class
# ============================================================================

class ToolChainVerifier:
    """Verifies sampled tool chains for constraint compliance and task alignment.

    Uses LLM-as-judge approach with two verification phases:
    - Pre-generation: Validates ordering constraints and data flow
    - Post-generation: Validates task-tool alignment

    When verification fails, attempts iterative refinement using LLM feedback.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_schemas: dict[str, ToolSchema],
        domain_context: str = "",
        config: dict[str, Any] | None = None
    ):
        """Initialize the verifier.

        Args:
            llm_client: Client for LLM API calls
            tool_schemas: Dictionary mapping tool names to their schemas
            domain_context: Optional domain context for better understanding
            config: Optional configuration dict with keys:
                - verify_tool_chain: Enable Phase 1 verification (default: True)
                - verify_alignment: Enable Phase 2 verification (default: True)
                - max_refinements: Max LLM refinement attempts (default: 2)
                - cache_constraints: Cache extracted constraints (default: True)
        """
        self.llm_client = llm_client
        self.tool_schemas = tool_schemas
        self.domain_context = domain_context
        self.config = config or {}

        # Configuration - use underscore prefix to avoid shadowing methods
        self._enable_tool_chain_verification = self.config.get("verify_tool_chain", True)
        self._enable_alignment_verification = self.config.get("verify_alignment", True)
        self._max_refinements = self.config.get("max_refinements", 2)
        self._cache_constraints = self.config.get("cache_constraints", True)

        # Constraint cache: tool_name -> list of extracted constraints
        self._constraint_cache: dict[str, list[ExtractedConstraint]] = {}

    # ========================================================================
    # Phase 1: Pre-Generation Verification
    # ========================================================================

    async def verify_pre_generation(
        self,
        sampled_path: SampledPath,
        tool_graph: ToolGraph
    ) -> VerificationResult:
        """Verify tool chain.

        Checks:
        1. Ordering constraints from tool descriptions (LLM-extracted)
        2. Data flow compatibility (output types -> input types)

        On failure, attempts to generate a fixed chain using LLM.

        Args:
            sampled_path: The sampled tool path to verify
            tool_graph: The tool graph with schemas and edges

        Returns:
            VerificationResult with validation status, issues, and optional fixed chain
        """
        if not self._enable_tool_chain_verification:
            return VerificationResult(is_valid=True, issues=[])

        issues: list[ConstraintViolation] = []

        # Step 1: Extract and verify ordering constraints
        ordering_issues = await self._verify_ordering_constraints(sampled_path)
        issues.extend(ordering_issues)

        # Step 2: Verify data flow compatibility
        data_flow_issues = self._verify_data_flow(sampled_path, tool_graph)
        issues.extend(data_flow_issues)

        # Determine validity (errors make it invalid, warnings are acceptable)
        is_valid = not any(i.severity == ViolationSeverity.ERROR for i in issues)

        # If invalid, attempt to generate a fixed chain
        fixed_chain = None
        if not is_valid:
            fixed_chain = await self.refine_chain(sampled_path, issues, tool_graph)

        return VerificationResult(
            is_valid=is_valid,
            issues=issues,
            fixed_chain=fixed_chain,
            confidence=1.0
        )

    async def _verify_ordering_constraints(
        self,
        sampled_path: SampledPath
    ) -> list[ConstraintViolation]:
        """Verify all ordering constraints are satisfied."""
        issues: list[ConstraintViolation] = []
        tool_positions = {tool: i for i, tool in enumerate(sampled_path.tools)}

        for tool_name in sampled_path.tools:
            constraints = await self.extract_constraints(tool_name)

            for constraint in constraints:
                if constraint.constraint_type == ConstraintType.MUST_BE_FIRST:
                    if tool_positions[tool_name] != 0:
                        issues.append(ConstraintViolation(
                            constraint_type="ordering",
                            tool_name=tool_name,
                            description=f"{tool_name} must be called first but is at position {tool_positions[tool_name]}",
                            severity=ViolationSeverity.ERROR
                        ))

                elif constraint.constraint_type == ConstraintType.MUST_BE_LAST:
                    if tool_positions[tool_name] != len(sampled_path.tools) - 1:
                        issues.append(ConstraintViolation(
                            constraint_type="ordering",
                            tool_name=tool_name,
                            description=f"{tool_name} must be called last but is at position {tool_positions[tool_name]}",
                            severity=ViolationSeverity.ERROR
                        ))

                elif constraint.constraint_type in (ConstraintType.MUST_FOLLOW, ConstraintType.REQUIRES_PREDECESSOR):
                    predecessor = constraint.depends_on
                    if predecessor and predecessor in tool_positions:
                        if tool_positions[predecessor] >= tool_positions[tool_name]:
                            issues.append(ConstraintViolation(
                                constraint_type="ordering",
                                tool_name=tool_name,
                                description=f"{tool_name} must be called after {predecessor}",
                                severity=ViolationSeverity.ERROR
                            ))
                    elif predecessor and predecessor not in tool_positions:
                        issues.append(ConstraintViolation(
                            constraint_type="ordering",
                            tool_name=tool_name,
                            description=f"{tool_name} requires {predecessor} but it's not in the chain",
                            severity=ViolationSeverity.ERROR
                        ))

                elif constraint.constraint_type == ConstraintType.CONFLICTS_WITH:
                    conflicting_tool = constraint.conflicts_with
                    if conflicting_tool and conflicting_tool in tool_positions:
                        issues.append(ConstraintViolation(
                            constraint_type="conflict",
                            tool_name=tool_name,
                            description=f"{tool_name} conflicts with {conflicting_tool} (both in chain)",
                            severity=ViolationSeverity.ERROR
                        ))

        return issues

    def _verify_data_flow(
        self,
        sampled_path: SampledPath,
        tool_graph: ToolGraph
    ) -> list[ConstraintViolation]:
        """Verify data flow compatibility between consecutive tools."""
        issues: list[ConstraintViolation] = []

        for i in range(len(sampled_path.tools) - 1):
            current_tool_name = sampled_path.tools[i]
            next_tool_name = sampled_path.tools[i + 1]

            current_tool = tool_graph.tools.get(current_tool_name)
            next_tool = tool_graph.tools.get(next_tool_name)

            if not current_tool or not next_tool:
                continue

            # Check if there's any type overlap
            output_types = set(t.lower() for t in current_tool.get_output_types())
            input_types = set(t.lower() for t in next_tool.get_input_types())

            # Allow if either has no type info (generic tools)
            if not output_types or not input_types:
                continue

            # Check for any overlap
            if not output_types & input_types:
                # Check if there's an explicit edge in the graph (LLM enricher may have found semantic connections)
                has_edge = any(
                    e[0] == current_tool_name and e[1] == next_tool_name
                    for e in tool_graph.edges
                )

                if not has_edge:
                    issues.append(ConstraintViolation(
                        constraint_type="data_flow",
                        tool_name=next_tool_name,
                        description=f"No data flow from {current_tool_name} (outputs: {output_types}) to {next_tool_name} (inputs: {input_types})",
                        severity=ViolationSeverity.WARNING  # Warning, not error - may have semantic connection
                    ))

        return issues

    # ========================================================================
    # Phase 2: Post-Generation Alignment Verification
    # ========================================================================

    async def verify_alignment(
        self,
        sampled_path: SampledPath,
        task_summary: str,
        tool_graph: ToolGraph
    ) -> VerificationResult:
        """Verify that the generated task summary aligns with the sampled tool chain.

        Based on TaskBench's alignment metric approach.

        Args:
            sampled_path: The tool chain used for generation
            task_summary: The generated task instruction
            tool_graph: The tool graph with schemas

        Returns:
            VerificationResult with alignment status and any issues
        """
        if not self._enable_alignment_verification:
            return VerificationResult(is_valid=True, issues=[])

        prompt = self._build_alignment_verification_prompt(
            sampled_path, task_summary, tool_graph
        )

        llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt=ALIGNMENT_VERIFICATION_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=1000
        ))

        if llm_response.status != HTTPStatus.OK:
            logger.warning(f"Alignment verification LLM call failed: {llm_response.error_message}")
            # On LLM failure, assume valid to avoid blocking
            return VerificationResult(is_valid=True, issues=[], confidence=0.5)

        return self._parse_alignment_response(llm_response.completion, sampled_path, tool_graph)

    def _build_alignment_verification_prompt(
        self,
        sampled_path: SampledPath,
        task_summary: str,
        tool_graph: ToolGraph
    ) -> str:
        """Build prompt for alignment verification."""
        tool_details = []
        for tool_name in sampled_path.tools:
            tool = tool_graph.tools.get(tool_name)
            if tool:
                tool_details.append({
                    "name": tool_name,
                    "description": tool.get_description()
                })

        # Include domain context so verifier knows what the agent already knows
        domain_section = ""
        if self.domain_context:
            truncated = self.domain_context[:2000]
            domain_section = f"""
## Agent's Built-in Knowledge (from system prompt/policy document)
The agent already knows the following policies and rules WITHOUT needing any tool:
{truncated}

The agent can answer questions about these policies from memory. It does NOT need a tool to explain rules, state fees, or deny requests based on policy.
"""

        return f"""Verify that an agent can handle this task using the prescribed tools plus its own knowledge.

## Task Summary (User Request)
{task_summary}

## Prescribed Tool Chain (in order)
{json.dumps(tool_details, indent=2)}
{domain_section}
## Verification Rules
- The agent KNOWS policies, rules, fees, and allowances from its built-in knowledge — it does NOT need a tool to explain a policy or state a rule.
- Tools are needed ONLY for: retrieving SPECIFIC records (a particular user's profile, a specific reservation's details, a specific flight's status), performing ACTIONS (booking, cancelling, updating specific records), or SEARCHING (finding flights, listing airports).
- If the task asks the agent to explain a general policy, deny a request citing rules, or state standard fees — the agent does that from knowledge. NOT a tool call.
- If the task references a SPECIFIC entity by ID (user_id, reservation_id, flight_number) and needs to RETRIEVE or MODIFY that entity — THAT requires a tool.

## Questions
1. SUFFICIENCY: Does the task require retrieving or modifying SPECIFIC records (by ID) that no tool in the chain can access?
2. RELEVANCE: Is each tool logically connected to at least one aspect of the task? A tool fails ONLY if it has ZERO connection to any part of the task.

Output JSON:
{{
    "is_aligned": true/false,
    "sufficiency": {{
        "can_fulfill_task": true/false,
        "missing_capabilities": ["ONLY list data retrieval/modification of SPECIFIC records by ID that no tool provides"]
    }},
    "relevance": {{
        "tools_with_purpose": ["tools connected to the task"],
        "tools_without_purpose": ["tools with ZERO connection to any aspect of the task"]
    }},
    "order_consistent": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}

JSON Output:"""

    def _parse_alignment_response(
        self,
        response_text: str,
        sampled_path: SampledPath,
        tool_graph: ToolGraph
    ) -> VerificationResult:
        """Parse LLM alignment verification response."""
        try:
            # Extract JSON from response
            response_text = response_text.strip()
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse alignment response: {e}")
            return VerificationResult(is_valid=True, issues=[], confidence=0.5)

        is_aligned = data.get("is_aligned", True)
        confidence = data.get("confidence", 0.8)
        issues: list[ConstraintViolation] = []

        # Check sufficiency — task requires capabilities the tools don't have
        sufficiency = data.get("sufficiency", {})
        if not sufficiency.get("can_fulfill_task", True):
            for capability in sufficiency.get("missing_capabilities", []):
                issues.append(ConstraintViolation(
                    constraint_type="alignment",
                    tool_name="",
                    description=f"Tools insufficient: task requires '{capability}' but no tool provides it",
                    severity=ViolationSeverity.ERROR
                ))

        # Check relevance — tools with no logical connection to the task
        relevance = data.get("relevance", {})
        for tool in relevance.get("tools_without_purpose", []):
            if tool in {t for t in sampled_path.tools}:
                issues.append(ConstraintViolation(
                    constraint_type="alignment",
                    tool_name=tool,
                    description=f"Tool {tool} has no logical connection to this task",
                    severity=ViolationSeverity.ERROR
                ))

        # Order inconsistency is a warning
        if not data.get("order_consistent", True):
            reasoning = data.get("reasoning", "Tool order does not match task narrative")
            issues.append(ConstraintViolation(
                constraint_type="alignment",
                tool_name="",
                description=reasoning,
                severity=ViolationSeverity.WARNING
            ))

        # If no ERROR-level issues, mark as aligned
        has_errors = any(i.severity == ViolationSeverity.ERROR for i in issues)
        if not has_errors:
            is_aligned = True

        return VerificationResult(
            is_valid=is_aligned,
            issues=issues,
            confidence=confidence,
        )

    # ========================================================================
    # Constraint Extraction (LLM-only)
    # ========================================================================

    async def extract_constraints(self, tool_name: str) -> list[ExtractedConstraint]:
        """Extract ordering/usage constraints from tool description using LLM.

        Results are cached per tool to avoid redundant LLM calls.

        Args:
            tool_name: Name of the tool to analyze

        Returns:
            List of extracted constraints
        """
        # Check cache first
        if self._cache_constraints and tool_name in self._constraint_cache:
            return self._constraint_cache[tool_name]

        tool = self.tool_schemas.get(tool_name)
        if not tool:
            return []

        description = tool.get_description()
        prompt = self._build_constraint_extraction_prompt(tool_name, description)

        llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt=CONSTRAINT_EXTRACTION_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=500
        ))

        if llm_response.status != HTTPStatus.OK:
            logger.warning(f"Constraint extraction failed for {tool_name}: {llm_response.error_message}")
            return []

        constraints = self._parse_constraint_response(tool_name, llm_response.completion)

        # Cache the result
        if self._cache_constraints:
            self._constraint_cache[tool_name] = constraints

        return constraints

    def _build_constraint_extraction_prompt(self, tool_name: str, description: str) -> str:
        """Build prompt for constraint extraction."""
        return f"""Analyze this tool and extract any ordering or usage constraints.

Tool: {tool_name}
Description: {description}

Extract constraints in these categories:
- must_be_first: Tool must be the first in any workflow
- must_follow: Tool must come after a specific tool (specify which)
- requires_predecessor: Tool requires another tool to have been called first
- conflicts_with: Tool cannot be used with another tool
- must_be_last: Tool must be the last in any workflow

Only extract EXPLICIT or STRONGLY IMPLIED constraints. If none exist, return an empty list.

Output JSON array:
[{{"type": "constraint_type", "depends_on": "tool_name_or_null", "reason": "brief explanation"}}]

JSON Output:"""

    def _parse_constraint_response(
        self,
        tool_name: str,
        response_text: str
    ) -> list[ExtractedConstraint]:
        """Parse LLM constraint extraction response."""
        try:
            response_text = response_text.strip()
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse constraint response for {tool_name}: {e}")
            return []

        if not isinstance(data, list):
            return []

        constraints: list[ExtractedConstraint] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            constraint_type_str = item.get("type", "")
            try:
                constraint_type = ConstraintType(constraint_type_str)
            except ValueError:
                logger.warning(f"Unknown constraint type: {constraint_type_str}")
                continue

            constraints.append(ExtractedConstraint(
                tool_name=tool_name,
                constraint_type=constraint_type,
                depends_on=item.get("depends_on"),
                conflicts_with=item.get("depends_on") if constraint_type == ConstraintType.CONFLICTS_WITH else None,
                description=item.get("reason", "")
            ))

        return constraints

    # ========================================================================
    # Iterative Refinement
    # ========================================================================

    async def refine_chain(
        self,
        sampled_path: SampledPath,
        violations: list[ConstraintViolation],
        tool_graph: ToolGraph
    ) -> list[str] | None:
        """Use LLM to fix an invalid tool chain based on verification feedback.

        Args:
            sampled_path: The original invalid tool chain
            violations: List of constraint violations found
            tool_graph: The tool graph with schemas

        Returns:
            Fixed tool chain as list of tool names, or None if cannot be fixed
        """
        violation_details = "\n".join(
            f"- [{v.severity.value}] {v.constraint_type}: {v.description}"
            for v in violations
        )

        tool_descriptions = self._format_tool_descriptions(tool_graph)

        prompt = f"""Fix this invalid tool chain based on the constraint violations.

## Current Tool Chain
{json.dumps(sampled_path.tools)}

## Constraint Violations Found
{violation_details}

## Available Tools
{json.dumps(list(tool_graph.tools.keys()))}

## Tool Descriptions
{tool_descriptions}

Fix the chain by:
1. Reordering tools to satisfy ordering constraints
2. Adding missing required predecessor tools
3. Removing conflicting tools
4. Maintaining logical data flow

Return ONLY a JSON array of tool names in the correct order.
If the chain cannot be fixed, return null.

JSON Output:"""

        llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt=CHAIN_REFINEMENT_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=500
        ))

        if llm_response.status != HTTPStatus.OK:
            logger.warning(f"Chain refinement failed: {llm_response.error_message}")
            return None

        return self._parse_refinement_response(llm_response.completion, tool_graph)

    def _format_tool_descriptions(self, tool_graph: ToolGraph) -> str:
        """Format tool descriptions for the refinement prompt."""
        lines = []
        for name, tool in tool_graph.tools.items():
            lines.append(f"- **{name}**: {tool.get_description()}")
        return "\n".join(lines)

    def _parse_refinement_response(
        self,
        response_text: str,
        tool_graph: ToolGraph
    ) -> list[str] | None:
        """Parse LLM chain refinement response."""
        try:
            response_text = response_text.strip()
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]

            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse refinement response: {e}")
            return None

        if data is None:
            return None

        if not isinstance(data, list):
            return None

        # Validate all tools exist
        valid_tools = []
        for tool_name in data:
            if tool_name in tool_graph.tools:
                valid_tools.append(tool_name)
            else:
                logger.warning(f"Refined chain contains unknown tool: {tool_name}")

        return valid_tools if valid_tools else None

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def clear_constraint_cache(self):
        """Clear the constraint cache."""
        self._constraint_cache.clear()

    def get_cached_constraints(self) -> dict[str, list[ExtractedConstraint]]:
        """Get all cached constraints."""
        return dict(self._constraint_cache)
