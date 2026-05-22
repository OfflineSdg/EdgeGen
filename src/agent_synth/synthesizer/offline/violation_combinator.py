"""Generate 2^n violation combinations from subgoals.

For n subgoals, generates all subsets (full powerset including empty set),
representing all possible combinations of subgoals to test together.
The empty set (index 0) represents the happy path where no subgoals are violated.
Uses LLMClient to detect conflicting combinations that cannot be tested together.
"""

import json
import logging
from http import HTTPStatus
from typing import Optional

from .client.llm_client import LLMClient
from .exception.exception import SyntheticDataGenerationError
from .exception.error_codes import ErrorCode
from .models.llm_payload import LLMPayload
from .models.pipeline_models import OnlineSubgoal, ViolationCombination

logger = logging.getLogger(__name__)


class ViolationCombinator:
    """Generate all valid violation combinations from subgoals."""

    def __init__(
        self,
        subgoals: list[OnlineSubgoal],
        llm_client: Optional[LLMClient] = None,
    ):
        self.subgoals = subgoals
        self.n = len(subgoals)
        self.llm_client = llm_client
        self._conflict_cache: dict[frozenset[str], str | None] = {}

    async def generate_all_combinations(
        self,
        max_subgoals: int | None = None,
        filter_invalid: bool = True
    ) -> list[ViolationCombination]:
        """Generate all 2^n combinations (powerset including empty set for happy path)."""
        all_combinations = []
        total_combinations = 2 ** self.n

        for i in range(0, total_combinations):
            binary_mask = format(i, f'0{self.n}b')
            selected = [
                self.subgoals[j]
                for j, bit in enumerate(binary_mask)
                if bit == '1'
            ]

            if max_subgoals is not None and len(selected) > max_subgoals:
                continue

            is_valid = True
            conflict_reason = None

            if filter_invalid and len(selected) > 1:
                is_valid, conflict_reason = await self._check_combination_validity(selected)

            combination = ViolationCombination(
                index=i,
                binary_mask=binary_mask,
                subgoals=selected,
                is_valid=is_valid,
                conflict_reason=conflict_reason
            )

            if filter_invalid and not is_valid:
                continue

            all_combinations.append(combination)

        return all_combinations

    async def _check_combination_validity(
        self,
        subgoals: list[OnlineSubgoal]
    ) -> tuple[bool, str | None]:
        cache_key = frozenset(sg.details for sg in subgoals)

        if cache_key in self._conflict_cache:
            reason = self._conflict_cache[cache_key]
            return (reason is None, reason)

        if self.llm_client is not None:
            is_valid, reason = await self._llm_conflict_detection(subgoals)
        else:
            is_valid, reason = True, None

        self._conflict_cache[cache_key] = reason if not is_valid else None
        return is_valid, reason

    async def _llm_conflict_detection(
        self,
        subgoals: list[OnlineSubgoal]
    ) -> tuple[bool, str | None]:
        subgoal_descriptions = "\n".join([
            f"SG{i + 1}: {sg.details}"
            for i, sg in enumerate(subgoals)
        ])

        prompt = f"""Analyze whether these business rules can be tested together in a single test scenario.

## Subgoals to Test Together
{subgoal_descriptions}

## Question
Can these subgoals be tested together in a single, coherent test scenario?
- Consider if their required contexts conflict (e.g., one needs premium user, one needs basic user)
- Consider if they apply to incompatible situations
- Consider if testing one would prevent testing another

Output JSON:
{{
    "can_test_together": true/false,
    "reason": "Explanation if cannot test together, or null if can"
}}

JSON Output:"""

        try:
            llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
                user_prompt=prompt,
                system_prompt="You analyze business rules for testing compatibility.",
                temperature=0.2,
                max_tokens=500,
            ))

            if llm_response.status != HTTPStatus.OK or not llm_response.completion:
                raise SyntheticDataGenerationError(
                    ErrorCode.UNSUCCESSFUL_SYNTHETIC_DATA_GENERATION.value,
                    f"LLM conflict detection failed due to status: {llm_response.status} from llm client with error: {llm_response.error_message}",
                )

            content = llm_response.completion.strip()

            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            result = json.loads(content)
            can_test = result.get("can_test_together", True)
            reason = result.get("reason")

            return can_test, reason if not can_test else None

        except json.JSONDecodeError as e:
            raise SyntheticDataGenerationError(
                ErrorCode.INVALID_JSON_DECODE_ERROR.value,
                f"Failed to parse LLM response as JSON: {e}",
            )
