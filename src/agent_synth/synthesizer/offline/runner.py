"""Generic runner for the OfflineTestCaseGenerator pipeline.

Loads tool schemas, subgoals, and PRD from a data directory and runs
the full pipeline: graph build -> violation combos -> path sampling ->
DB state sampling -> LLM generation -> output.

Domain-specific code provides a DomainConfig with file paths.

Environment variables:
    AZURE_API_VERSION       Azure OpenAI API version
    AZURE_API_BASE          Azure OpenAI endpoint
    AZURE_API_KEY           Azure OpenAI key
    LLM_MODEL               Model name (default: azure/gpt-4.1)
    BATCH_SIZE              Max violation combos to process (default: 5)
    OUTPUT_DIR              Output directory (default: output/)
    AGENT_INSPECT_DEBUG_SDG Set to "1" to save debug artifacts
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .client.litellm_client import LiteLLMClient
from .models.agent_data_sample import SubGoal, SubGoalCategory
from .models.tools_schema import ToolSchema
from .synthetic_data_generator import OfflineTestCaseGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

CATEGORY_MAP = {
    "output_rule": SubGoalCategory.OUTPUT_RULE,
    "tool_call_rule": SubGoalCategory.TOOL_CALL_RULE,
    "compliance_rule": SubGoalCategory.COMPLIANCE_RULE,
}


@dataclass
class DomainConfig:
    """Configuration for a specific domain's offline pipeline."""
    domain_name: str
    data_dir: Path
    tool_schema_file: str
    online_rules_file: str
    prd_file: str
    output_file: str
    db_path: Path | None = None


# =============================================================================
# Data loaders
# =============================================================================

def load_tool_schemas(config: DomainConfig) -> list[ToolSchema]:
    """Load tool schemas from the domain's tool schema JSON file."""
    schema_path = config.data_dir / config.tool_schema_file
    with open(schema_path) as f:
        data = json.load(f)

    tools = []
    for tool_data in data["tools"]:
        tool_dict = {
            "name": tool_data["name"],
            "description": tool_data["description"],
            "input_schema": {
                "parameters": _input_dict_to_param_list(tool_data.get("input", {})),
            },
            "output_schema": _output_to_schema(tool_data.get("output", {})),
            "input_param_names": list(tool_data.get("input", {}).keys()),
            "output_field_names": tool_data.get("output-type", []),
        }
        tools.append(ToolSchema.from_dict(tool_dict))

    logger.info(f"Loaded {len(tools)} tool schemas from {schema_path}")
    return tools


def _input_dict_to_param_list(input_dict: dict) -> list[dict]:
    """Convert ``{param_name: {type, description, ...}}`` to a param list."""
    params = []
    for name, spec in input_dict.items():
        param = {"name": name, **spec}
        if param.get("type") == "string" and "enum" in param:
            param["type"] = "enum"
        params.append(param)
    return params


def _output_to_schema(output: dict) -> dict:
    """Convert the data-file output format to ToolSchema output_schema."""
    if not output:
        return {"fields": []}

    out_type = output.get("type", "string")
    if out_type == "object" and "properties" in output:
        return {
            "fields": [{
                "name": output.get("description", "result").lower().replace(" ", "_"),
                "type": "object",
                "description": output.get("description", ""),
                "properties": [{"name": k, **v} for k, v in output["properties"].items()],
            }]
        }
    elif out_type == "array":
        return {
            "fields": [{
                "name": output.get("description", "result").lower().replace(" ", "_"),
                "type": "array",
                "description": output.get("description", ""),
                "items_schema": output.get("items", {}),
            }]
        }
    else:
        return {
            "fields": [{
                "name": "result",
                "type": out_type,
                "description": output.get("description", ""),
            }]
        }


def load_subgoals(config: DomainConfig) -> list[SubGoal]:
    """Load subgoals from the domain's online rules JSON file."""
    rules_path = config.data_dir / config.online_rules_file
    with open(rules_path) as f:
        data = json.load(f)

    subgoals = []
    for sg in data["subgoals"]:
        category = CATEGORY_MAP.get(sg.get("category"), SubGoalCategory.COMPLIANCE_RULE)
        subgoals.append(SubGoal(details=sg["details"], category=category))

    logger.info(f"Loaded {len(subgoals)} subgoals from {rules_path}")
    return subgoals


def load_prd(config: DomainConfig) -> str:
    """Load PRD markdown from the domain's PRD file."""
    prd_path = config.data_dir / config.prd_file
    with open(prd_path) as f:
        content = f.read()
    logger.info(f"Loaded PRD ({len(content)} chars) from {prd_path}")
    return content


# =============================================================================
# Main
# =============================================================================

async def run_pipeline(config: DomainConfig):
    """Run the full offline test case generation pipeline."""
    tools = load_tool_schemas(config)
    subgoals = load_subgoals(config)
    prd_content = load_prd(config)

    model = os.environ.get("LLM_MODEL", "azure/gpt-4.1")
    llm_client = LiteLLMClient(
        model=model,
        max_tokens=2000,
        temperature=0.7,
    )
    logger.info(f"Using LLM model: {model}")

    batch_size = int(os.environ.get("BATCH_SIZE", "5"))
    generator = OfflineTestCaseGenerator(
        llm_client=llm_client,
        tool_schemas=tools,
        subgoals=subgoals,
        prd_content=prd_content,
        config={"batch_size": batch_size},
        db_path=str(config.db_path) if config.db_path else None,
    )

    logger.info(f"Starting generation (batch_size={batch_size})...")
    test_cases = await generator.generate_testcases()
    logger.info(f"Generated {len(test_cases)} test cases")

    for i, tc in enumerate(test_cases):
        print(f"\n--- Test Case {i + 1} ---")
        print(f"Task: {tc.task_summary}")
        print(f"  Grading Notes:")
        for gn in tc.grading_notes:
            print(f"    [{gn.category}] {gn.assertion}")
        print(f"  Expected Tools:")
        for et in tc.expected_tools:
            print(f"    #{et.order}: {et.tool_name}")
            print(f"      parameters: {et.parameters}")
            print(f"      expected_output_type: {et.expected_output_type}")

    output_dir = Path(os.environ.get("OUTPUT_DIR", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / config.output_file

    output = [tc.to_dict() for tc in test_cases]

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Saved {len(test_cases)} test cases to {output_path}")


def main(config: DomainConfig):
    """Entry point for domain-specific runners."""
    asyncio.run(run_pipeline(config))
