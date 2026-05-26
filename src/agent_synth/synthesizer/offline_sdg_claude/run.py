"""Run the offline SDG pipeline with Claude Code-based sampler and verifier.

Usage:
    cd tau2bench/tau2-bench
    python -m agent_synth.synthesizer.offline_sdg_claude.run \
        --db-path mock_system/db/airline_final.db \
        --sdg-dir mock_system/sdg \
        --output output/testcases_claude.json

Or from the tau2bench sdg directory:
    bash mock_system/sdg/run_sdg_claude.sh
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import List

# Load .env from common locations
try:
    from dotenv import load_dotenv
    env_search_paths = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
    ]
    # Also check near the db-path and sdg-dir (parsed later, so check sys.argv)
    for arg_idx, arg in enumerate(sys.argv):
        if arg in ("--db-path", "--sdg-dir") and arg_idx + 1 < len(sys.argv):
            candidate = Path(sys.argv[arg_idx + 1]).resolve().parent
            while candidate != candidate.parent:
                env_candidate = candidate / ".env"
                if env_candidate.exists():
                    env_search_paths.insert(0, env_candidate)
                    break
                candidate = candidate.parent

    for env_path in env_search_paths:
        if env_path.exists():
            load_dotenv(env_path)
            logging.getLogger(__name__).info(f"Loaded .env from {env_path}")
            break
except ImportError:
    pass

# Ensure agent-synth is importable
AGENT_SYNTH_SRC = Path(__file__).resolve().parents[4] / "src"
if str(AGENT_SYNTH_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SYNTH_SRC))

from agent_synth.synthesizer.offline.runner import DomainConfig, load_tool_schemas, load_subgoals, load_prd
from agent_synth.synthesizer.offline.client.litellm_client import LiteLLMClient
from agent_synth.synthesizer.offline.synthetic_data_generator import OfflineTestCaseGenerator
from agent_synth.synthesizer.offline.models.pipeline_models import (
    TestCase, SampledPath, SamplingPattern, GenerationContext,
)
from agent_synth.synthesizer.offline.prd_parser import PRDParser
from agent_synth.synthesizer.offline.graph_builder import GraphBuilder
from agent_synth.synthesizer.offline.graph_sampler import GraphSampler
from agent_synth.synthesizer.offline.llm_generator import LLMGenerator
from agent_synth.synthesizer.offline.llm_graph_enricher import LLMGraphEnricher
from agent_synth.synthesizer.offline.violation_combinator import ViolationCombinator
from agent_synth.synthesizer.offline.violation_scenario_builder import ViolationScenarioBuilder
from agent_synth.synthesizer.offline.subgoal_parser import SubgoalParser
from agent_synth.synthesizer.offline.tool_chain_verifier import ToolChainVerifier
from agent_synth.synthesizer.offline.constants import *
from agent_synth.synthesizer.offline.utils import get_config_or_default

from agent_synth.synthesizer.offline_sdg_claude.claude_guided_sampler import ClaudeGuidedSampler
from agent_synth.synthesizer.offline_sdg_claude.claude_verifier import ClaudeVerifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


class ClaudeOfflineTestCaseGenerator(OfflineTestCaseGenerator):
    """Extended generator that uses Claude Code for sampling and verification.

    Overrides generate_testcases to inject:
    - ClaudeGuidedSampler instead of ScenarioGuidedSampler
    - ClaudeVerifier as post-generation filter
    """

    def __init__(self, *args, claude_sampler: ClaudeGuidedSampler, claude_verifier: ClaudeVerifier, **kwargs):
        super().__init__(*args, **kwargs)
        self.claude_sampler = claude_sampler
        self.claude_verifier = claude_verifier

    async def generate_testcases(self) -> List[TestCase]:
        """Run pipeline with Claude sampler replacing ScenarioGuidedSampler."""
        # Step 1: Parse inputs
        tool_schema_map = {tool.get_name(): tool for tool in self.tool_schemas}

        prd_parser = PRDParser(self.prd_content)
        domain_context = prd_parser.get_domain_context()

        # Step 1b: Enrich subgoals
        subgoal_parser = SubgoalParser(
            subgoals=self.subgoals,
            tool_schema_map=tool_schema_map,
            prd_context=domain_context,
            llm_client=self.llm_client,
        )
        subgoals = await subgoal_parser.parse(limit=self.subgoal_limit)

        # Step 2: Build tool graph
        llm_enricher = LLMGraphEnricher(self.llm_client)
        graph_builder = GraphBuilder(
            tool_schema_map,
            dependency_type=self.dependency_type,
            llm_enricher=llm_enricher,
        )
        tool_graph = await graph_builder.build()

        # Step 3: Generate violation combinations
        combinator = ViolationCombinator(
            subgoals=subgoals,
            llm_client=self.llm_client,
        )
        combinations = await combinator.generate_all_combinations(filter_invalid=True)
        combinations = combinations[:self.batch_size]

        # Step 4: Build scenario builder
        scenario_builder = ViolationScenarioBuilder(
            tool_graph=tool_graph,
            prd_context=domain_context,
            llm_client=self.llm_client,
        )

        # Step 5: Initialize sampler and generators
        sampler = GraphSampler(graph_builder, max_path_length=self.max_path_length)
        pattern_weights = {
            SamplingPattern.NODE: self.pattern_node_weight_prob,
            SamplingPattern.CHAIN: self.pattern_chain_weight_prob,
            SamplingPattern.DAG: self.pattern_dag_weight_prob,
        }

        verifier_config = {
            VERIFY_TOOL_CHAIN: self.verify_tool_chain,
            VERIFY_ALIGNMENT: False,
            MAX_REFINEMENTS: self.max_refinements,
            CACHE_CONSTRAINTS: self.cache_constraints,
        }
        verifier = ToolChainVerifier(
            llm_client=self.llm_client,
            tool_schemas=tool_schema_map,
            domain_context=domain_context,
            config=verifier_config,
        )

        llm_generator = LLMGenerator(self.llm_client)

        # Tool descriptions for Claude sampler
        tool_descriptions = {
            name: tool.get_description()
            for name, tool in tool_graph.tools.items()
        }

        # Concurrency limit for parallel Claude Code calls
        semaphore = asyncio.Semaphore(3)

        async def process_combo(combo) -> TestCase | None:
            """Process a single combo: sample chain → verify chain → build scenario → Claude sample → LLM generate → Claude verify+fix."""
            # Step 6: Sample tool chain
            paths_sample = sampler.sample_mixed(count=1, weights=pattern_weights)
            if not paths_sample:
                logger.warning(f"Could not sample path for combination {combo.index}")
                return None
            path = paths_sample[0]

            # Step 7: Pre-generation verification (tool chain ordering)
            if self.verify_tool_chain:
                verification_passed = False
                for attempt in range(self.max_refinements + 1):
                    result = await verifier.verify_pre_generation(path, tool_graph)
                    if result.is_valid:
                        verification_passed = True
                        break
                    elif result.fixed_chain:
                        path = SampledPath(pattern=path.pattern, tools=result.fixed_chain, parallel_groups=[])
                    else:
                        paths_sample = sampler.sample_mixed(count=1, weights=pattern_weights)
                        if paths_sample:
                            path = paths_sample[0]
                        else:
                            break
                if not verification_passed:
                    logger.warning(f"Could not produce valid chain for combination {combo.index}")
                    return None

            # Step 8: Build scenario
            scenario = await scenario_builder.build_scenario(combo, path)

            # Step 9: *** CLAUDE SAMPLER *** (rate-limited)
            async with semaphore:
                logger.info(f"  Claude sampling for combo {combo.index}, tools: {path.tools}")
                db_snapshot = await self.claude_sampler.sample(scenario, path, tool_descriptions)

            if not db_snapshot:
                logger.warning(f"  Claude sampler returned empty for combo {combo.index}, skipping")
                return None

            context = GenerationContext(
                sampled_path=path,
                db_snapshot=db_snapshot,
                domain_context=domain_context,
                scenario_context=scenario,
            )

            # Step 10: LLM generation
            try:
                test_case = await llm_generator.generate_test_case(context, tool_graph)
            except Exception as e:
                logger.error(f"Error generating test case for combo {combo.index}: {e}")
                return None

            # Step 11: *** CLAUDE VERIFY + FIX *** (rate-limited)
            async with semaphore:
                status, fixed_tc, issues = await self.claude_verifier.verify_and_fix(test_case)

            if status == "valid":
                logger.info(f"  ✓ Test case {test_case.id} passed Claude verification")
                return test_case
            elif status == "fixed":
                logger.info(f"  ✓ Test case {test_case.id} fixed by Claude: {'; '.join(issues[:3])}")
                return fixed_tc
            else:
                logger.warning(f"  ✗ Test case {test_case.id} rejected: {'; '.join(issues)}")
                return None

        # Run all combos in parallel (bounded by semaphore)
        logger.info(f"Processing {len(combinations)} combinations in parallel (max concurrency: 3)")
        tasks = [process_combo(combo) for combo in combinations]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_test_cases: List[TestCase] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Combo processing raised exception: {r}")
            elif r is not None:
                all_test_cases.append(r)

        return all_test_cases


async def main():
    parser = argparse.ArgumentParser(description="Run SDG with Claude Code sampler + verifier")
    parser.add_argument("--db-path", type=str, required=True, help="Path to the database")
    parser.add_argument("--sdg-dir", type=str, required=True, help="Path to sdg/ directory with inputs")
    parser.add_argument("--output", type=str, default="output/testcases_claude.json", help="Output JSON path")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6", help="Claude model for sampler/verifier")
    parser.add_argument("--llm-model", type=str, default="azure/gpt-5.4", help="LLM model for generation")
    parser.add_argument("--batch-size", type=int, default=5, help="Number of test cases to generate")
    parser.add_argument("--domain", type=str, default="airline", help="Domain name")
    parser.add_argument("--tool-schema", type=str, default="tools.json", help="Tool schema filename")
    parser.add_argument("--rules-file", type=str, default="online_subgoals.json", help="Online rules filename")
    parser.add_argument("--prd-file", type=str, default="PRD.md", help="PRD filename")
    parser.add_argument("--log-dir", type=str, default=None, help="Directory for Claude Code logs (sampler/verifier)")
    args = parser.parse_args()

    db_path = Path(args.db_path).resolve()
    sdg_dir = Path(args.sdg_dir).resolve()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    config = DomainConfig(
        domain_name=args.domain,
        data_dir=sdg_dir,
        tool_schema_file=args.tool_schema,
        online_rules_file=args.rules_file,
        prd_file=args.prd_file,
        output_file=output_path.name,
        db_path=db_path,
    )

    tools = load_tool_schemas(config)
    subgoals = load_subgoals(config)
    prd_content = load_prd(config)

    llm_client = LiteLLMClient(model=args.llm_model, max_tokens=2000, temperature=0.7)
    log_dir = Path(args.log_dir) if args.log_dir else output_path.parent / "logs"
    claude_sampler = ClaudeGuidedSampler(db_path=db_path, model=args.model, log_dir=log_dir)
    claude_verifier = ClaudeVerifier(db_path=db_path, policy_content=prd_content, model=args.model, log_dir=log_dir)

    logger.info(f"Using Claude Code sampler + verifier (model={args.model})")
    logger.info(f"LLM generator model: {args.llm_model}")
    logger.info(f"DB: {db_path}")
    logger.info(f"Batch size: {args.batch_size}")

    generator = ClaudeOfflineTestCaseGenerator(
        llm_client=llm_client,
        tool_schemas=tools,
        subgoals=subgoals,
        prd_content=prd_content,
        claude_sampler=claude_sampler,
        claude_verifier=claude_verifier,
        config={
            "batch_size": args.batch_size,
            "verify_alignment": False,
            "verify_execution": False,
            "max_repair_attempts": 2,
            "pattern_weight_node_probability": 0.1,
            "pattern_weight_chain_probability": 0.45,
            "pattern_weight_dag_probability": 0.45,
        },
        db_path=str(db_path),
        execute_tool=None,
        reload_fn=None,
    )

    logger.info(f"Starting generation (batch_size={args.batch_size})...")
    test_cases = await generator.generate_testcases()
    logger.info(f"Generated {len(test_cases)} verified test cases")

    json_data = [tc.to_dict() for tc in test_cases]
    with open(output_path, "w") as f:
        json.dump(json_data, f, indent=2)
    logger.info(f"Saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
