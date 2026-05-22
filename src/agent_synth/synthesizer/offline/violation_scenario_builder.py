"""Build scenario contexts for violation test case generation.

Takes a ViolationCombination and creates a ScenarioContext that:
1. Merges context requirements from all selected subgoals
2. Uses LLMClient to resolve any ambiguities
"""

import json
import logging
from http import HTTPStatus
from typing import Optional

from .client.llm_client import LLMClient
from .models.llm_payload import LLMPayload
from .exception.exception import SyntheticDataGenerationError
from .exception.error_codes import ErrorCode

from .models.pipeline_models import (
    ViolationCombination, ScenarioContext,
    ToolGraph, SampledPath
)

logger = logging.getLogger(__name__)


class ViolationScenarioBuilder:
    """Build scenario contexts for violation combinations."""

    def __init__(
        self,
        tool_graph: ToolGraph | None = None,
        prd_context: str = "",
        llm_client: Optional[LLMClient] = None,
    ):
        self.tool_graph = tool_graph
        self.prd_context = prd_context
        self.llm_client = llm_client

    async def build_scenario(
        self,
        combination: ViolationCombination,
        sampled_path: SampledPath | None = None,
    ) -> ScenarioContext:
        if self.llm_client is None:
            raise SyntheticDataGenerationError(
                ErrorCode.MISSING_VALUE.value,
                "LLM client is required for building scenario contexts",
            )
        return await self._build_with_llm(combination, sampled_path)

    async def _build_with_llm(
        self,
        combination: ViolationCombination,
        sampled_path: SampledPath | None = None,
    ) -> ScenarioContext:
        # Happy path: no subgoals violated — return minimal scenario context
        if not combination.subgoals:
            return ScenarioContext(
                violated_subgoals={},
                combination_index=combination.index,
                context_constraints={},
                expected_behaviors=["Agent should handle the request successfully following all policies."],
            )

        subgoal_descriptions = "\n".join([
            f"SG{i + 1}: {sg.details}"
            for i, sg in enumerate(combination.subgoals)
        ])

        tool_names = list(self.tool_graph.tools.keys()) if self.tool_graph else []

        # Build tool chain section if a sampled path is provided
        tool_chain_section = ""
        if sampled_path and self.tool_graph:
            tool_chain_lines = []
            for i, tool_name in enumerate(sampled_path.tools):
                tool = self.tool_graph.tools.get(tool_name)
                desc = tool.get_description() if tool else ""
                tool_chain_lines.append(f"  {i + 1}. {tool_name} — {desc}")
            tool_chain_section = f"""
## Agent's ONLY Available Tools (in execution order)
{chr(10).join(tool_chain_lines)}

These are the ONLY tools the agent has. The agent CANNOT:
- Call any tool not listed above
- Look up records if no lookup tool is listed
- Modify data if no modification tool is listed
- Book/cancel/update if no such tool is listed
"""

        system_prompt = """You create test scenarios that are STRICTLY constrained to specific tool chains.

RULE: The scenario you create must be completable using ONLY the listed tools plus the agent's
built-in policy knowledge. If a tool to look up reservations is not listed, the scenario CANNOT
involve looking up a specific reservation. If a booking tool is not listed, the scenario CANNOT
involve making a booking.

Think step by step:
1. First, understand what each tool in the chain actually DOES
2. Then, design a user request where the agent would naturally call these tools in order
3. Finally, check: does this request need ANY action that none of the tools can perform?
   If yes, REDESIGN the scenario until it only needs what the tools provide.

The subgoals (business rules) guide WHAT the agent should say/enforce, but the tools
constrain WHAT ACTIONS the agent can take."""

        prompt = f"""Design a test scenario using ONLY the tools below.

## Step 1: Understand the tools
Look at each tool and understand what it does. The user's request must only need these actions.
{tool_chain_section}
## Step 2: Consider the business rules to test
{subgoal_descriptions}

## Step 3: Domain context
{self.prd_context[:1500] if self.prd_context else 'Not specified'}

---

## Step 4: Design the scenario

Create a user request where:
- The agent calls each tool in the chain (in order) to handle it
- The business rules are tested through the agent's RESPONSE (what it says), not through additional tool calls
- The user does NOT ask for anything that requires a tool not in the chain

KEY PRINCIPLE: If a tool for action X is not in the chain, the scenario CANNOT involve
performing action X. For example, if no "modify" tool is listed, the user cannot ask to
modify something. If no "lookup" tool is listed, the scenario cannot require looking up
a specific record. Design the request around what the tools CAN do.

Output JSON:
{{
    "context_constraints": {{
        "key": "value - what this context variable should be set to"
    }},
    "expected_behaviors": [
        "What agent should do (using ONLY the listed tools + policy knowledge)"
    ],
    "scenario_description": "Brief description"
}}

JSON Output:"""

        try:
            llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
                user_prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.5,
                max_tokens=1500,
            ))

            if llm_response.status != HTTPStatus.OK or not llm_response.completion:
                raise SyntheticDataGenerationError(
                    ErrorCode.UNSUCCESSFUL_SYNTHETIC_DATA_GENERATION.value,
                    f"LLM request failed due to status: {llm_response.status} from llm client with error: {llm_response.error_message}",
                )

            content = llm_response.completion.strip()

            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content)

            return ScenarioContext(
                violated_subgoals=combination.get_subgoal_map(),
                combination_index=combination.index,
                context_constraints=result.get("context_constraints", {}),
                expected_behaviors=result.get("expected_behaviors", [])
            )

        except json.JSONDecodeError as e:
            raise SyntheticDataGenerationError(
                ErrorCode.INVALID_JSON_DECODE_ERROR.value,
                f"Failed to parse LLM response as JSON: {e}",
            )

    async def build_scenarios_for_combinations(
        self,
        combinations: list[ViolationCombination],
        sampled_paths: list[SampledPath] | None = None,
    ) -> list[ScenarioContext]:
        if sampled_paths:
            return [
                await self.build_scenario(combo, path)
                for combo, path in zip(combinations, sampled_paths)
            ]
        return [await self.build_scenario(combo) for combo in combinations]
