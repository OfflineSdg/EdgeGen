"""Parser for subgoals.

Converts SubGoal objects to OnlineSubgoal objects and enriches them
with required context and expected behavior using an LLM.
"""

import json
import logging
from http import HTTPStatus
from typing import Any, List, Optional

from .client.llm_client import LLMClient
from .exception.exception import SyntheticDataGenerationError
from .exception.error_codes import ErrorCode
from .models.llm_payload import LLMPayload
from .models.agent_data_sample import SubGoal
from .models.pipeline_models import OnlineSubgoal

logger = logging.getLogger(__name__)


class SubgoalParser:
    """Convert SubGoal objects to enriched OnlineSubgoal objects.

    Takes a list of SubGoal and converts each to an OnlineSubgoal with
    auto-assigned IDs, then enriches with required_context and expected_behavior.

    :param subgoals: a list of :obj:`SubGoal` to convert and enrich.
    :param tool_schema_map: Default to ``None``. Map of tool name to ToolSchema for LLM context.
    :param prd_context: Default to ``None``. Domain context from PRD for LLM enrichment.
    :param llm_client: LLM client for enrichment (required).
    """

    def __init__(
        self,
        subgoals: List[SubGoal],
        tool_schema_map: dict[str, Any] | None = None,
        prd_context: str | None = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.subgoals = subgoals
        self.tool_schema_map = tool_schema_map or {}
        self.prd_context = prd_context or ""
        self.llm_client = llm_client

    async def parse(self, limit: int | None = None) -> list[OnlineSubgoal]:
        """Convert SubGoals to OnlineSubgoals and enrich with context.

        :param limit: Default to ``None``. Maximum number of subgoals to process.
        :return: a list of enriched :obj:`OnlineSubgoal` objects.
        """
        source = self.subgoals[:limit] if limit is not None else self.subgoals

        # Convert SubGoal → OnlineSubgoal with auto-assigned IDs
        online_subgoals = [
            OnlineSubgoal(
                details=sg.details,
                category=sg.category,
                id=f"SG{i + 1}",
            )
            for i, sg in enumerate(source)
        ]

        if self.llm_client is None:
            raise SyntheticDataGenerationError(
                ErrorCode.MISSING_VALUE.value,
                "LLM client is required for subgoal parsing",
            )
        online_subgoals = await self._enrich_with_llm(online_subgoals)

        return online_subgoals

    async def _enrich_with_llm(self, subgoals: list[OnlineSubgoal]) -> list[OnlineSubgoal]:
        """Use LLM to extract required context and expected behavior."""
        tool_names = list(self.tool_schema_map.keys()) if self.tool_schema_map else []

        prompt = self._build_extraction_prompt(subgoals, tool_names)

        llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt=self._get_system_prompt(),
            temperature=0.3,
            max_tokens=3000,
        ))

        if llm_response.status != HTTPStatus.OK or not llm_response.completion:
            raise SyntheticDataGenerationError(
                ErrorCode.UNSUCCESSFUL_SYNTHETIC_DATA_GENERATION.value,
                f"LLM enrichment failed due to status: {llm_response.status} from llm client with error: {llm_response.error_message} ",
            )

        content = llm_response.completion.strip()
        return self._parse_llm_response(content, subgoals)

    def _get_system_prompt(self) -> str:
        return """You are an expert at analyzing business rules for AI agent evaluation.

Given a list of online subgoals (business rules an agent must follow), extract:
1. required_context: What scenario setup is needed to test this rule (as key-value pairs)
2. expected_behavior: What the agent should do when correctly enforcing this rule

Be generic and domain-agnostic - extract the semantic meaning without hardcoding domain-specific values.

Output valid JSON matching the requested schema."""

    def _build_extraction_prompt(
        self,
        subgoals: list[OnlineSubgoal],
        tool_names: list[str]
    ) -> str:
        subgoal_list = "\n".join([
            f"{sg.id}: {sg.details}"
            for sg in subgoals
        ])

        return f"""Analyze these online subgoals and extract the required context and expected behavior for each.

## Online Subgoals
{subgoal_list}

## Available Tools (for reference)
{', '.join(tool_names) if tool_names else 'Not specified'}

## Domain Context
{self.prd_context[:2000] if self.prd_context else 'Not specified'}

---

For each subgoal, extract:
1. required_context: A dictionary of context variables needed to create a test scenario
   - Use generic keys like "entity_type", "user_attribute", "condition", etc.
   - Values should describe what's needed, not specific values
2. expected_behavior: A single sentence describing what the agent should do

Output as JSON array:
[
    {{
        "id": "SG1",
        "required_context": {{"key": "description of what's needed"}},
        "expected_behavior": "What agent should do"
    }},
    ...
]

JSON Output:"""

    def _parse_llm_response(
        self,
        content: str,
        subgoals: list[OnlineSubgoal]
    ) -> list[OnlineSubgoal]:
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            extracted = json.loads(content)
            extracted_by_id = {item["id"]: item for item in extracted}

            for subgoal in subgoals:
                if subgoal.id in extracted_by_id:
                    data = extracted_by_id[subgoal.id]
                    subgoal.required_context = data.get("required_context", {})
                    subgoal.expected_behavior = data.get("expected_behavior", "")

        except (json.JSONDecodeError, KeyError) as e:
            raise RuntimeError(f"LLM response parsing failed: {e}")

        return subgoals
