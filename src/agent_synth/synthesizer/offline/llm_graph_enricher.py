"""LLM-based graph enrichment to discover additional edges from tool descriptions."""

import json
import logging
from typing import Any

from .client.llm_client import LLMClient
from .models.llm_payload import LLMPayload
from .models.tools_schema import ToolSchema
from .constants import STATUS_200

logger = logging.getLogger(__name__)


EDGE_DISCOVERY_SYSTEM_PROMPT = """You are an expert at analyzing tool APIs to identify workflow dependencies.

Your task is to find edges between tools that represent realistic workflows.
An edge from Tool A to Tool B means: "After using Tool A, a user might logically use Tool B next."

Consider these relationship patterns:

1. **Sequential Workflows**: Multi-step processes where one action naturally leads to another

2. **Data Flow**: Tool A retrieves/produces information that Tool B needs or can use

3. **Utility Tools**: Tools like calculators, formatters, or validators that support other operations
   - These can be used BEFORE actions (to compute values needed as input)
   - These can be used AFTER actions (to process or validate results)
   - Connect utility tools to any tool where their function would be helpful

4. **Verification Flows**: Getting information before/after modifications

5. **Alternative/Fallback Paths**: When one approach fails, try another

6. **Escalation Paths**: When automated handling isn't sufficient, escalate to another tool or human

7. **Pre-requisite Relationships**: Actions that must logically precede others

IMPORTANT: Pay special attention to utility/helper tools (like calculators, validators, formatters).
These often have no obvious type connections but are useful in many workflows. Think about WHERE
in a workflow such tools would be helpful and create edges accordingly.

Be thorough. Include all plausible workflow edges, not just the most obvious ones.
Do NOT include edges already in the existing edges list."""


EDGE_DISCOVERY_PROMPT_TEMPLATE = """Analyze these tools and identify workflow edges between them.

## Tools Available:
{tools_section}

## Existing Edges (DO NOT include these - they already exist):
{existing_edges_section}

## Instructions:
1. Identify edges that represent realistic workflow relationships NOT in the existing list
2. Pay special attention to utility/helper tools that may not have obvious connections
   - Think: "When would a user need this tool's functionality?"
   - Think: "What tools produce data that this tool could process?"
   - Think: "What tools need data that this tool could help compute?"
3. Consider both common workflows AND edge cases

Output ONLY a valid JSON array:
[{{"from": "tool_a", "to": "tool_b"}}, ...]

Return [] only if you truly cannot find any additional meaningful edges."""


class LLMGraphEnricher:
    """Use LLM to find additional edges based on tool descriptions."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def find_additional_edges(
        self,
        tools: dict[str, ToolSchema],
        existing_edges: list[tuple[str, str, dict]]
    ) -> list[tuple[str, str, dict]]:
        """Analyze tools and return new edges not already in existing_edges.

        Args:
            tools: Dictionary mapping tool names to their schemas
            existing_edges: List of existing edges as (from_tool, to_tool, attrs) tuples

        Returns:
            List of new edges as (from_tool, to_tool, attrs) tuples
        """
        if not tools:
            return []

        # Build prompt sections
        tools_section = self._format_tools_section(tools)
        existing_edges_section = self._format_existing_edges(existing_edges)

        # Build the prompt
        prompt = EDGE_DISCOVERY_PROMPT_TEMPLATE.format(
            tools_section=tools_section,
            existing_edges_section=existing_edges_section
        )

        # Call LLM
        payload = LLMPayload(
            user_prompt=prompt,
            system_prompt=EDGE_DISCOVERY_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=2000
        )

        response = await self.llm_client.make_request_with_payload(payload)

        if response.status != STATUS_200:
            logger.error(f"LLM request failed: {response.error_message}")
            return []

        # Parse response
        new_edges = self._parse_edges_response(response.completion, tools, existing_edges)
        return new_edges

    def _format_tools_section(self, tools: dict[str, ToolSchema]) -> str:
        """Format tools with descriptions and parameter info for the prompt."""
        lines = []
        for name, tool in tools.items():
            description = tool.get_description()

            # Get parameter names
            params = tool.get_input_schema().get_parameters()
            param_names = [p.get_name() for p in params] if params else []

            # Get output info
            output_types = tool.get_output_types()

            # Format tool info
            tool_line = f"- **{name}**: {description}"
            if param_names:
                tool_line += f"\n  Parameters: {', '.join(param_names)}"
            if output_types:
                tool_line += f"\n  Returns: {', '.join(output_types)}"

            lines.append(tool_line)
        return "\n".join(lines)

    def _format_existing_edges(self, edges: list[tuple[str, str, dict]]) -> str:
        """Format existing edges as a readable list for the prompt."""
        if not edges:
            return "(none)"
        lines = []
        for from_tool, to_tool, _ in edges:
            lines.append(f"- {from_tool} -> {to_tool}")
        return "\n".join(lines)

    def _parse_edges_response(
        self,
        response_text: str,
        tools: dict[str, ToolSchema],
        existing_edges: list[tuple[str, str, dict]]
    ) -> list[tuple[str, str, dict]]:
        """Parse LLM response into edge tuples, filtering invalid edges."""
        try:
            # Try to extract JSON from response
            response_text = response_text.strip()

            # Handle case where LLM wraps response in markdown code block
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                json_lines = []
                in_block = False
                for line in lines:
                    if line.startswith("```"):
                        in_block = not in_block
                        continue
                    if in_block:
                        json_lines.append(line)
                response_text = "\n".join(json_lines)

            edges_data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response was: {response_text}")
            return []

        if not isinstance(edges_data, list):
            logger.error(f"Expected list, got {type(edges_data)}")
            return []

        # Build set of existing edges for deduplication
        existing_set = {(e[0], e[1]) for e in existing_edges}
        tool_names = set(tools.keys())

        new_edges = []
        for edge in edges_data:
            if not isinstance(edge, dict):
                continue

            from_tool = edge.get("from")
            to_tool = edge.get("to")

            # Validate edge
            if not from_tool or not to_tool:
                continue
            if from_tool not in tool_names:
                logger.warning(f"Ignoring edge with unknown source tool: {from_tool}")
                continue
            if to_tool not in tool_names:
                logger.warning(f"Ignoring edge with unknown target tool: {to_tool}")
                continue
            if from_tool == to_tool:
                continue  # No self-loops
            if (from_tool, to_tool) in existing_set:
                continue  # Skip duplicates

            new_edges.append((
                from_tool,
                to_tool,
                {"type": "resource"}  # Use same type as resource edges
            ))

            # Add to existing set to avoid duplicates within LLM response
            existing_set.add((from_tool, to_tool))

        logger.info(f"LLM graph enricher found {len(new_edges)} additional edges")
        return new_edges
