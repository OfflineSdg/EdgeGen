"""LLM-based generation for test cases using LLMClient.

Generic approach:
1. Takes a sampled tool path (from graph sampling)
2. Receives a real database snapshot (from SQLiteWalker)
3. Uses LLM to generate a COHERENT task summary using actual DB values
4. Generates grading notes aligned with the task and tools
5. Extracts expected tool calls with real parameter values

For violation tests:
- Includes scenario context from online subgoal combinations
- Generates scenarios that test whether agent correctly enforces business rules
"""

import json
import logging
from http import HTTPStatus

from .client.llm_client import LLMClient
from .exception.exception import SyntheticDataGenerationError
from .exception.error_codes import ErrorCode
from .models.llm_payload import LLMPayload

from .models.pipeline_models import (
    TestCase, ToolCall, GradingNote, ConstraintViolation,
    SampledPath, GenerationContext, Difficulty, SamplingPattern, ToolGraph,
    ScenarioContext, VerificationResult
)

logger = logging.getLogger(__name__)


class LLMGenerator:
    """Generate test case components using LLMClient."""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def generate_test_case(
        self,
        context: GenerationContext,
        tool_graph: ToolGraph
    ) -> TestCase:
        prompt = self._build_generation_prompt(context, tool_graph)

        llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt=self._get_system_prompt(),
            temperature=0.7,
            max_tokens=2000,
        ))

        if llm_response.status != HTTPStatus.OK or not llm_response.completion:
            raise SyntheticDataGenerationError(internal_code=ErrorCode.UNSUCCESSFUL_SYNTHETIC_DATA_GENERATION.value,
                                 message=f"Unable to generate testcases using llm due to status: {llm_response.status} from LLM client.")


        content = llm_response.completion.strip()
        return self._parse_response(content, context, tool_graph)

    def _get_system_prompt(self) -> str:
        return """You are an expert at creating test cases for AI agent evaluation.

Your job is to create test scenarios that evaluate whether an agent correctly enforces business rules.

CRITICAL: You are given REAL database state. Use ONLY the actual values from the database — do NOT invent IDs, names, dates, or any other values.

TASK SUMMARY RULES:
- Start with "You are..." (user proxy perspective)
- Keep it concise: 3-4 sentences maximum
- Use ONLY values from the provided database state
- IMPORTANT: Always include unique identifiers that distinguish the entity (e.g., entity IDs, reference codes, membership numbers, or any domain-specific identifiers from the database)

GRADING NOTE RULES:
- Must be VERIFIABLE FACTS that can be checked against the scenario
- Reference specific values from the database state used in the task summary
- DO NOT include tool invocation checks (tool calls are verified separately)
- Focus on business rule enforcement and expected agent behavior

EXPECTED TOOLS RULES:
- For each tool in the workflow, provide the exact parameter values chosen from the database state
- Parameters must use real values from the database — no placeholders or invented data
- You MUST include ALL required parameters for each tool — do NOT leave parameters empty

Output valid JSON matching the requested schema."""

    def _build_generation_prompt(
        self,
        context: GenerationContext,
        tool_graph: ToolGraph
    ) -> str:
        # Build tool details
        tool_details = []
        for tool_name in context.sampled_path.tools:
            tool = tool_graph.tools.get(tool_name)
            if tool:
                params = {}
                for p in tool.get_input_schema().get_parameters():
                    param_info = {
                        "type": p.get_type().value,
                        "description": p.get_description(),
                        "required": p.get_required(),
                    }
                    if p.get_enum():
                        param_info["enum"] = p.get_enum()
                    params[p.get_name()] = param_info
                tool_details.append({
                    "name": tool_name,
                    "description": tool.get_description(),
                    "parameters": params,
                    "returns": tool.get_returns_description(),
                })

        # Build violation/scenario section
        scenario_section = ""
        grading_notes_example = '    "grading_notes": [{"assertion": "Agent must [describe what agent should do/say with real values]", "category": "general"}]'

        if context.scenario_context is not None:
            sc = context.scenario_context
            subgoal_ids = list(sc.violated_subgoals.keys())
            subgoal_lines = [
                f"- {sg_id}: {behavior}"
                for sg_id, behavior in zip(subgoal_ids, sc.expected_behaviors)
            ]

            scenario_section = f"""
## BUSINESS RULES TO TEST
{chr(10).join(subgoal_lines)}

## SCENARIO CONSTRAINTS
{json.dumps(sc.context_constraints, indent=2)}
"""
            # Build grading notes example
            grading_note_examples = ",\n        ".join(
                f'{{"assertion": "Agent must [describe required agent behavior for {sg_id} with real DB values]", "category": "{sg_id}"}}'
                for sg_id in subgoal_ids
            )
            grading_notes_example = f"""    "grading_notes": [
        {grading_note_examples}
    ]"""

        db_snapshot_str = json.dumps(context.db_snapshot, indent=2, default=str)

        # Build relationship annotation section
        relationships_section = ""
        if context.db_relationships:
            relationships_section = f"""
## DATA OWNERSHIP / RELATIONSHIPS (CRITICAL — respect these when pairing entities)
{context.db_relationships}

IMPORTANT: A row in a child table ONLY belongs to the parent row whose key matches.
For example, if reservations.user_id references users.user_id, then a reservation
with user_id='X' belongs ONLY to user 'X' — never assign it to a different user.
"""

        prompt = f"""Generate a test case for evaluating an AI agent.

## TOOLS (agent will call in this order)
{json.dumps(tool_details, indent=2)}

## DATABASE STATE (use ONLY these real values — do NOT invent any data)
{db_snapshot_str}
{relationships_section}{scenario_section}
## Domain Context
{context.domain_context if context.domain_context else "Not specified"}

---

IMPORTANT RULES:
1. Use ONLY values from the DATABASE STATE above — no invented IDs, names, dates, or amounts
2. RESPECT DATA OWNERSHIP — check the RELATIONSHIPS section to know which rows belong to which entity. A reservation belongs to the user whose user_id matches, NOT to any random user in the snapshot
3. Pick values that make the tool workflow coherent (e.g., a user who has a reservation)
4. Ensure data flow consistency — if Tool A needs a user_id that Tool B also uses, pick the same one
5. For enums, choose from the tool parameter options ONLY
6. If your scenario involves a user acting on an entity they do NOT own, make it an explicit ownership-violation test — the grading notes must verify the agent denies the request

Generate a JSON object with:

1. **task_summary**: A user request starting with "You are..."
   - 3-4 sentences maximum
   - Use real values from the database state
   - Include unique identifiers (IDs, codes, reference numbers) that the user would naturally know and mention

2. **grading_notes**: Verifiable assertions about what the AGENT must do or say
   - Focus on AGENT BEHAVIOR: "Agent must...", "Agent should...", "Agent explicitly states..."
   - NOT scenario facts: avoid "User has...", "Reservation includes...", "Policy states..."
   - Reference real values used in the task for verification
   - Focus on business rule enforcement (NOT tool calls)
   - DO NOT prefix with "For SG1:" etc. — the category field handles this

3. **expected_tools**: For each tool in the workflow, the exact parameters with real values
   - Must use values from the database state

Output JSON:
{{
    "task_summary": "You are [name from DB][Optional: unique identifier]. You want to [action]. [relevant context with real values including any reference numbers/IDs the user would know].",

{grading_notes_example},

    "expected_tools": [
        {{"tool_name": "tool1", "parameters": {{"param1": "real_value"}}, "order": 0}},
        {{"tool_name": "tool2", "parameters": {{"param1": "real_value"}}, "order": 1}}
    ]
}}

JSON Output:"""

        return prompt

    def _parse_response(
        self,
        content: str,
        context: GenerationContext,
        tool_graph: ToolGraph
    ) -> TestCase:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        data = json.loads(content)

        grading_notes = []
        for note in data.get("grading_notes", []):
            if isinstance(note, str):
                grading_notes.append(GradingNote(assertion=note, category="general"))
            elif isinstance(note, dict):
                assertion = note.get("assertion", "")
                grading_notes.append(GradingNote(
                    assertion=assertion,
                    category=note.get("category", "general"),
                ))
            else:
                continue

        # Validate subgoal coverage for violation tests
        if context.scenario_context is not None:
            required_subgoals = set(context.scenario_context.violated_subgoals.keys())
            covered_subgoals = {note.category for note in grading_notes}
            missing = required_subgoals - covered_subgoals
            if missing:
                logger.warning(f"Missing grading notes for subgoals: {missing}")

        expected_tools = self._build_tool_calls(data, context, tool_graph)
        difficulty = self._assess_difficulty(context)

        test_case = TestCase.create(
            task_summary=data.get("task_summary", ""),
            grading_notes=grading_notes,
            expected_tools=expected_tools
        )

        if context.scenario_context is not None:
            sc = context.scenario_context
            test_case.violated_subgoals = sc.violated_subgoals
            test_case.combination_index = sc.combination_index
            test_case.scenario_context = sc.context_constraints

        return test_case

    def _build_tool_calls(
        self,
        llm_output: dict,
        context: GenerationContext,
        tool_graph: ToolGraph
    ) -> list[ToolCall]:
        """Build tool calls from LLM output.

        Uses the LLM's expected_tools directly when available, as the LLM
        selects which tools are relevant to the generated scenario. Falls
        back to the sampled path only if the LLM returns no tools.
        """
        tool_calls = []

        llm_tools = llm_output.get("expected_tools", [])
        if not llm_tools or not isinstance(llm_tools, list):
            # Fallback: use sampled path with empty params
            for i, tool_name in enumerate(context.sampled_path.tools):
                tool = tool_graph.tools.get(tool_name)
                return_type = tool.get_returns_description() if tool else "unknown"
                tool_calls.append(ToolCall(
                    tool_name=tool_name,
                    parameters={},
                    expected_output_type=return_type,
                    order=i,
                ))
            return tool_calls

        for i, t in enumerate(llm_tools):
            if not isinstance(t, dict) or "tool_name" not in t:
                continue
            tool_name = t["tool_name"]
            tool = tool_graph.tools.get(tool_name)
            return_type = tool.get_returns_description() if tool else "unknown"
            params = t.get("parameters", {})

            tool_calls.append(ToolCall(
                tool_name=tool_name,
                parameters=params,
                expected_output_type=return_type,
                order=t.get("order", i),
            ))

        return tool_calls

        return tool_calls

    def _assess_difficulty(self, context: GenerationContext) -> Difficulty:
        path_length = len(context.sampled_path)
        has_parallel = bool(context.sampled_path.parallel_groups)

        if path_length <= 2:
            return Difficulty.EASY
        elif path_length >= 4 or has_parallel:
            return Difficulty.HARD
        else:
            return Difficulty.MEDIUM

    async def repair_test_case(
        self,
        test_case: TestCase,
        issues: list[ConstraintViolation],
        context: GenerationContext,
        tool_graph: ToolGraph,
    ) -> TestCase:
        """Repair a test case by feeding errors back to the LLM.

        Sends the original test case, the collated errors (execution + alignment),
        and the DB snapshot to the LLM with instructions to fix only the broken
        values while preserving scenario intent.

        :param test_case: the failed test case to repair.
        :param issues: list of all constraint violations found.
        :param context: original generation context (includes DB snapshot).
        :param tool_graph: the tool dependency graph.
        :return: a repaired TestCase.
        """
        prompt = self._build_repair_prompt(test_case, issues, context, tool_graph)

        llm_response = await self.llm_client.make_request_with_payload(LLMPayload(
            user_prompt=prompt,
            system_prompt=self._get_repair_system_prompt(),
            temperature=0.3,
            max_tokens=2000,
        ))

        if llm_response.status != HTTPStatus.OK or not llm_response.completion:
            raise SyntheticDataGenerationError(
                internal_code=ErrorCode.UNSUCCESSFUL_SYNTHETIC_DATA_GENERATION.value,
                message=f"Unable to repair test case: status {llm_response.status}",
            )

        content = llm_response.completion.strip()
        return self._parse_response(content, context, tool_graph)

    def _get_repair_system_prompt(self) -> str:
        return """You are an expert at fixing test cases for AI agent evaluation.

You are given a test case that FAILED verification. The errors tell you exactly what went wrong — typically hallucinated values that don't exist in the database.

Your job: fix the test case so it passes verification. Rules:

1. Replace hallucinated/invalid values with REAL values from the provided database state
2. Keep the overall scenario INTENT intact (same type of user request, same business rules tested)
3. Ensure data flow consistency — if you change one value, update all references to it
4. Do NOT invent new values — only use what exists in the database
5. Keep the same tool ordering unless an alignment error specifically says otherwise

Output valid JSON matching the same schema as the original test case."""

    def _build_repair_prompt(
        self,
        test_case: TestCase,
        issues: list[ConstraintViolation],
        context: GenerationContext,
        tool_graph: ToolGraph,
    ) -> str:
        # Format the original test case
        original_tc = {
            "task_summary": test_case.task_summary,
            "grading_notes": [
                {"assertion": gn.assertion, "category": gn.category}
                for gn in test_case.grading_notes
            ],
            "expected_tools": [
                {"tool_name": tc.tool_name, "parameters": tc.parameters, "order": tc.order}
                for tc in test_case.expected_tools
            ],
        }

        # Format errors
        error_descriptions = []
        for issue in issues:
            error_descriptions.append(
                f"- [{issue.constraint_type}] {issue.tool_name}: {issue.description}"
            )

        # DB snapshot (same as generation, no truncation for repair — errors need full context)
        db_snapshot_str = json.dumps(context.db_snapshot, indent=2, default=str)
        if len(db_snapshot_str) > 8000:
            db_snapshot_str = db_snapshot_str[:8000] + "\n... (truncated)"

        # Tool details for reference
        tool_details = []
        for tool_name in context.sampled_path.tools:
            tool = tool_graph.tools.get(tool_name)
            if tool:
                params = {}
                for p in tool.get_input_schema().get_parameters():
                    param_info = {
                        "type": p.get_type().value,
                        "description": p.get_description(),
                        "required": p.get_required(),
                    }
                    if p.get_enum():
                        param_info["enum"] = p.get_enum()
                    params[p.get_name()] = param_info
                tool_details.append({
                    "name": tool_name,
                    "description": tool.get_description(),
                    "parameters": params,
                })

        return f"""Fix the following test case that FAILED verification.

## ORIGINAL TEST CASE (has errors)
{json.dumps(original_tc, indent=2)}

## ERRORS FOUND
{chr(10).join(error_descriptions)}

## TOOL DEFINITIONS
{json.dumps(tool_details, indent=2)}

## DATABASE STATE (use ONLY these values)
{db_snapshot_str}

## DATA OWNERSHIP / RELATIONSHIPS
{context.db_relationships if context.db_relationships else "Not specified"}

---

Fix the test case by replacing invalid values with real values from the database.
Keep the scenario intent the same. Ensure all tool parameters use values that exist in the database.
Respect data ownership — only assign entities to the user who owns them per the relationships above.
Update the task_summary and grading_notes to reflect any value changes.

Output the corrected JSON:
{{
    "task_summary": "...",
    "grading_notes": [{{"assertion": "...", "category": "..."}}],
    "expected_tools": [{{"tool_name": "...", "parameters": {{...}}, "order": 0}}]
}}

JSON Output:"""
