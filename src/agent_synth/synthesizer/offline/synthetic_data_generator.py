import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Dict, List


from .client.llm_client import LLMClient
from .constants import (
    BATCH_SIZE, DEFAULT_BATCH_SIZE,
    PATTERN_WEIGHT_NODE_PROBABILITY, DEFAULT_PATTERN_WEIGHT_NODE,
    PATTERN_WEIGHT_CHAIN_PROBABILITY, DEFAULT_PATTERN_WEIGHT_CHAIN,
    PATTERN_WEIGHT_DAG_PROBABILITY, DEFAULT_PATTERN_WEIGHT_DAG,
    MAX_PATH_LENGTH, DEFAULT_MAX_PATH_LENGTH,
    SUBGOAL_LIMIT, DEFAULT_SUBGOAL_LIMIT,
    DEPENDENCY_TYPE, DEFAULT_DEPENDENCY_TYPE,
    VERIFY_TOOL_CHAIN, DEFAULT_VERIFY_TOOL_CHAIN,
    VERIFY_ALIGNMENT, DEFAULT_VERIFY_ALIGNMENT,
    MAX_REFINEMENTS, DEFAULT_MAX_REFINEMENTS,
    CACHE_CONSTRAINTS, DEFAULT_CACHE_CONSTRAINTS,
    VERIFY_EXECUTION, DEFAULT_VERIFY_EXECUTION,
    MAX_REPAIR_ATTEMPTS, DEFAULT_MAX_REPAIR_ATTEMPTS,
)
from .models.agent_data_sample import SubGoal
from .utils.synthetic_data_input_validator import SyntheticDataInputValidator
from .models.pipeline_models import ToolSchema, SamplingPattern, GenerationContext, SampledPath, TestCase
from .prd_parser import PRDParser
from .graph_builder import GraphBuilder
from .graph_sampler import GraphSampler
from .state_sampler import StateSampler
from .llm_generator import LLMGenerator
from .llm_graph_enricher import LLMGraphEnricher
from .violation_combinator import ViolationCombinator
from .violation_scenario_builder import ViolationScenarioBuilder
from .subgoal_parser import SubgoalParser
from .tool_chain_verifier import ToolChainVerifier
from .execution_verifier import ExecutionVerifier
from .scenario_guided_sampler import ScenarioGuidedSampler
from .utils import get_config_or_default

logger = logging.getLogger(__name__)


class OfflineTestCaseGenerator:
    """
    Generator for offline synthetic test cases using an LLM client.

    Orchestrates the full pipeline: parse inputs, build tool graph, generate violation
    combinations, sample tool paths, verify tool chains, sample DB state, and produce
    :obj:`TestCase` objects via LLM generation.

    :param llm_client: the client which allows connection to the LLM model for synthetic data generation.
    :param tool_schemas: a list of :obj:`ToolSchema` objects defining the agent's available tools.
    :param subgoals: a list of :obj:`SubGoal` representing business rules to test.
    :param prd_content: raw PRD markdown content describing the agent's domain.
    :param config: Default to ``None``. Configuration options:
        - **batch_size**: maximum number of violation combinations to process.
        - **pattern_weight_node_probability**: sampling weight for single-tool (node) patterns.
        - **pattern_weight_chain_probability**: sampling weight for sequential (chain) patterns.
        - **pattern_weight_dag_probability**: sampling weight for parallel/branching (DAG) patterns.
        - **max_path_length**: maximum number of tools in a sampled path.
        - **subgoal_limit**: maximum number of subgoals to use.
        - **dependency_type**: strategy for building tool dependency edges.
        - **verify_tool_chain**: enable pre-generation tool chain verification (default: True).
        - **verify_alignment**: enable post-generation task-tool alignment verification (default: True).
        - **max_refinements**: maximum LLM refinement attempts per chain (default: 2).
        - **cache_constraints**: cache extracted tool constraints (default: True).
    """

    def __init__(
            self,
            llm_client: LLMClient,
            tool_schemas: List[ToolSchema],
            subgoals: List[SubGoal],
            prd_content: str,
            config: Optional[Dict[str, Any]] = None,
            db_path: Optional[str] = None,
            execute_tool: Optional[Any] = None,
            reload_fn: Optional[Any] = None,
    ):
        self.llm_client = llm_client
        SyntheticDataInputValidator.validate_tool_schemas(tool_schemas=tool_schemas)
        SyntheticDataInputValidator.validate_subgoals(subgoals=subgoals)
        SyntheticDataInputValidator.validate_prd_content(prd_content=prd_content)
        self.tool_schemas = tool_schemas
        self.subgoals = subgoals
        self.prd_content = prd_content
        self.config = config
        self.db_path = db_path
        self.execute_tool = execute_tool
        self.reload_fn = reload_fn
        self.batch_size = get_config_or_default(config=config, config_key=BATCH_SIZE, default=DEFAULT_BATCH_SIZE)
        self.pattern_node_weight_prob = get_config_or_default(config=config, config_key=PATTERN_WEIGHT_NODE_PROBABILITY, default=DEFAULT_PATTERN_WEIGHT_NODE)
        self.pattern_chain_weight_prob = get_config_or_default(config=config, config_key=PATTERN_WEIGHT_CHAIN_PROBABILITY, default=DEFAULT_PATTERN_WEIGHT_CHAIN)
        self.pattern_dag_weight_prob = get_config_or_default(config=config, config_key=PATTERN_WEIGHT_DAG_PROBABILITY, default=DEFAULT_PATTERN_WEIGHT_DAG)
        self.max_path_length = get_config_or_default(config=config, config_key=MAX_PATH_LENGTH, default=DEFAULT_MAX_PATH_LENGTH)
        self.subgoal_limit = get_config_or_default(config=config, config_key=SUBGOAL_LIMIT, default=DEFAULT_SUBGOAL_LIMIT)
        self.dependency_type = get_config_or_default(config=config, config_key=DEPENDENCY_TYPE, default=DEFAULT_DEPENDENCY_TYPE)

        # Verification config
        self.verify_tool_chain = get_config_or_default(config=config, config_key=VERIFY_TOOL_CHAIN, default=DEFAULT_VERIFY_TOOL_CHAIN)
        self.verify_alignment = get_config_or_default(config=config, config_key=VERIFY_ALIGNMENT, default=DEFAULT_VERIFY_ALIGNMENT)
        self.verify_execution = get_config_or_default(config=config, config_key=VERIFY_EXECUTION, default=DEFAULT_VERIFY_EXECUTION)
        self.max_refinements = get_config_or_default(config=config, config_key=MAX_REFINEMENTS, default=DEFAULT_MAX_REFINEMENTS)
        self.max_repair_attempts = get_config_or_default(config=config, config_key=MAX_REPAIR_ATTEMPTS, default=DEFAULT_MAX_REPAIR_ATTEMPTS)
        self.cache_constraints = get_config_or_default(config=config, config_key=CACHE_CONSTRAINTS, default=DEFAULT_CACHE_CONSTRAINTS)

        SyntheticDataInputValidator.validate_weight_probabilities(self.pattern_node_weight_prob, self.pattern_chain_weight_prob, self.pattern_dag_weight_prob)
        SyntheticDataInputValidator.validate_max_path_length(self.max_path_length)
        SyntheticDataInputValidator.validate_subgoal_limit(self.subgoal_limit)

    async def generate_testcases(
        self,
    ) -> List[TestCase]:
        """
        Run the synthetic data generation pipeline and return a list of :obj:`TestCase`.

        This method orchestrates the full pipeline:
        parse inputs -> build tool graph -> generate violation combinations ->
        build scenario contexts -> sample tool paths -> verify tool chains (with iterative refinement) ->
        sample DB state -> LLM generation -> verify alignment.

        :return: a list of :obj:`TestCase` objects.
        """
        # Step 1: Parse inputs
        tool_schema_map = {tool.get_name(): tool for tool in self.tool_schemas}

        prd_parser = PRDParser(self.prd_content)
        domain_context = prd_parser.get_domain_context()

        # Step 1b: Enrich subgoals with context
        subgoal_parser = SubgoalParser(
            subgoals=self.subgoals,
            tool_schema_map=tool_schema_map,
            prd_context=domain_context,
            llm_client=self.llm_client,
        )
        subgoals = await subgoal_parser.parse(limit=self.subgoal_limit)

        # Step 2: Build tool graph with LLM enrichment
        llm_enricher = LLMGraphEnricher(self.llm_client)
        graph_builder = GraphBuilder(
            tool_schema_map,
            dependency_type=self.dependency_type,
            llm_enricher=llm_enricher,
        )
        tool_graph = await graph_builder.build()
        # Debug output: save graph when env flag is set
        if os.environ.get("AGENT_INSPECT_DEBUG_SDG", "").lower() in ("1", "true"):
            logger.info("Debug flag is set, saving artifacts...")
            await self._save_debug_artifacts(graph_builder)

        # Step 3: Generate violation combinations
        combinator = ViolationCombinator(
            subgoals=subgoals,
            llm_client=self.llm_client,
        )
        combinations = await combinator.generate_all_combinations(filter_invalid=True)
        batch_size = self.batch_size

        combinations = combinations[:batch_size]

        # Step 4: Build scenario builder
        scenario_builder = ViolationScenarioBuilder(
            tool_graph=tool_graph,
            prd_context=domain_context,
            llm_client=self.llm_client,
        )

        # Step 5: Initialize sampler, verifier, and generators
        sampler = GraphSampler(
            graph_builder,
            max_path_length=self.max_path_length
        )
        pattern_weights = {
            SamplingPattern.NODE: self.pattern_node_weight_prob,
            SamplingPattern.CHAIN: self.pattern_chain_weight_prob,
            SamplingPattern.DAG: self.pattern_dag_weight_prob
        }

        # Initialize tool chain verifier
        verifier_config = {
            VERIFY_TOOL_CHAIN: self.verify_tool_chain,
            VERIFY_ALIGNMENT: self.verify_alignment,
            MAX_REFINEMENTS: self.max_refinements,
            CACHE_CONSTRAINTS: self.cache_constraints,
        }
        verifier = ToolChainVerifier(
            llm_client=self.llm_client,
            tool_schemas=tool_schema_map,
            domain_context=domain_context,
            config=verifier_config,
        )

        state_sampler = StateSampler(db_path=self.db_path) if self.db_path else None
        guided_sampler = ScenarioGuidedSampler(
            db_path=self.db_path, llm_client=self.llm_client
        ) if self.db_path else None
        llm_generator = LLMGenerator(self.llm_client)

        # Initialize execution verifier if enabled and dependencies are available
        execution_verifier = None
        if self.verify_execution and self.db_path and self.execute_tool:
            execution_verifier = ExecutionVerifier(
                execute_tool=self.execute_tool,
                db_path=Path(self.db_path),
                reload_fn=self.reload_fn,
            )

        all_test_cases: List[TestCase] = []

        for combo in combinations:
            # Step 6: Sample initial path FIRST (difficulty-controlled)
            paths_sample = sampler.sample_mixed(count=1, weights=pattern_weights)
            if not paths_sample:
                logger.warning(f"Could not sample path for combination {combo.index}")
                continue

            path = paths_sample[0]

            # Step 7: Pre-generation verification with iterative refinement
            if self.verify_tool_chain:
                verification_passed = False
                for attempt in range(self.max_refinements + 1):
                    result = await verifier.verify_pre_generation(path, tool_graph)

                    if result.is_valid:
                        verification_passed = True
                        break
                    elif result.fixed_chain:
                        # Apply LLM's suggested fix
                        logger.info(f"Applying chain fix (attempt {attempt + 1}): {result.fixed_chain}")
                        path = SampledPath(
                            pattern=path.pattern,
                            tools=result.fixed_chain,
                            parallel_groups=[]  # Reset for fixed chain
                        )
                    else:
                        # Cannot fix - try resampling
                        logger.warning(f"Chain verification failed (attempt {attempt + 1}), resampling...")
                        paths_sample = sampler.sample_mixed(count=1, weights=pattern_weights)
                        if paths_sample:
                            path = paths_sample[0]
                        else:
                            break

                if not verification_passed:
                    logger.warning(f"Could not produce valid chain for combination {combo.index} after {self.max_refinements + 1} attempts")
                    continue

            # Step 8: Build scenario CONSTRAINED to this tool chain
            scenario = await scenario_builder.build_scenario(combo, path)

            # Step 9-11: Generate with verification and repair loop
            test_case = None

            # Step 9: Sample database state (guided by scenario)
            tool_descriptions = {
                name: tool.get_description()
                for name, tool in tool_graph.tools.items()
            }
            if guided_sampler:
                db_snapshot = await guided_sampler.sample(scenario, path, tool_descriptions)
            elif state_sampler:
                db_snapshot = state_sampler.sample()
            else:
                db_snapshot = {}

            context = GenerationContext(
                sampled_path=path,
                db_snapshot=db_snapshot,
                domain_context=domain_context,
                scenario_context=scenario,
            )

            try:
                # Step 9: Generate test case
                test_case = await llm_generator.generate_test_case(context, tool_graph)

                # Step 10: Verification + repair loop
                for repair_attempt in range(self.max_repair_attempts + 1):
                    all_issues = []

                    # Execution verification
                    if execution_verifier:
                        exec_result = execution_verifier.verify(test_case)
                        all_issues.extend(exec_result.issues)

                    # Alignment verification
                    if self.verify_alignment:
                        alignment_result = await verifier.verify_alignment(
                            path, test_case.task_summary, tool_graph
                        )
                        if not alignment_result.is_valid:
                            all_issues.extend(alignment_result.issues)

                    # All passed — accept
                    if not all_issues:
                        break

                    # Need repair
                    if repair_attempt < self.max_repair_attempts:
                        logger.info(
                            f"Verification failed for combo {combo.index} "
                            f"(attempt {repair_attempt + 1}/{self.max_repair_attempts}), "
                            f"repairing: {[i.description for i in all_issues]}"
                        )
                        test_case = await llm_generator.repair_test_case(
                            test_case, all_issues, context, tool_graph
                        )
                    else:
                        # Max repairs exhausted — reject
                        logger.warning(
                            f"Test case for combo {combo.index} rejected after "
                            f"{self.max_repair_attempts} repair attempts: "
                            f"{[i.description for i in all_issues]}"
                        )
                        test_case = None

            except Exception as e:
                logger.error(f"Error generating test case for combo {combo.index}: {e}")
                test_case = None

            if test_case is None:
                continue

            all_test_cases.append(test_case)

        return all_test_cases

    @staticmethod
    async def _save_debug_artifacts(graph_builder) -> None:
        """Save tool graph PNG to the output directory."""
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import networkx as nx

        output_dir = Path(os.environ.get("OUTPUT_DIR", "debug_sdg_output"))
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save tool dependency graph as PNG
        try:
            nx_graph = await graph_builder.get_networkx_graph()
            plt.figure(figsize=(16, 12))
            pos = nx.spring_layout(nx_graph, k=2, iterations=50, seed=42)
            nx.draw_networkx_nodes(nx_graph, pos, node_color='lightblue',
                                   node_size=3000, alpha=0.9)
            nx.draw_networkx_edges(nx_graph, pos, edge_color='gray',
                                   arrows=True, arrowsize=20,
                                   arrowstyle='->', width=1.5, alpha=0.6)
            nx.draw_networkx_labels(nx_graph, pos, font_size=8, font_weight='bold')
            plt.title("Tool Dependency Graph", fontsize=16, fontweight='bold')
            plt.axis('off')
            plt.tight_layout()

            graph_path = output_dir / "tool_dependency_graph.png"
            plt.savefig(graph_path, dpi=150, bbox_inches='tight', facecolor='white')
            plt.close()
            logger.info(f"Debug: Tool dependency graph saved to {graph_path}")
        except Exception as e:
            logger.warning(f"Debug: Failed to save graph visualization: {e}")
