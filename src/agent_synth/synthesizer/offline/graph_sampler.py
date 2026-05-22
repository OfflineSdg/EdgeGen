"""Graph sampling strategies for generating tool paths."""

import random
from typing import Any

from .models.pipeline_models import SamplingPattern, SampledPath
from .exception.exception import SyntheticDataGenerationError
from .exception.error_codes import ErrorCode
from .graph_builder import GraphBuilder


class GraphSampler:
    """Sample valid paths through the tool graph.

    Supports three sampling patterns:
    - Node: Single tool (for simple queries)
    - Chain: Sequential path of tools
    - DAG: Paths with parallel branches

    Note: All methods that access the graph are now synchronous because
    GraphBuilder.build() must be called (and awaited) before using this class.
    The graph must be pre-built by calling `await graph_builder.build()` before
    instantiating this sampler.
    """

    def __init__(
        self,
        graph_builder: GraphBuilder,
        max_path_length: int = 5,
        seed: int | None = None
    ):
        self.graph_builder = graph_builder
        self.max_path_length = max_path_length
        self.rng = random.Random(seed)
        # Cache the tool graph to avoid needing async calls during sampling
        self._tool_graph = None

    def _ensure_graph_built(self):
        """Ensure the graph has been built before sampling.

        This method retrieves the cached tool_graph. The graph must have been
        built by calling `await graph_builder.build()` before using the sampler.
        """
        if self._tool_graph is None:
            if self.graph_builder._tool_graph is None:
                raise SyntheticDataGenerationError(
                    ErrorCode.INVALID_VALUE.value,
                    "Graph must be built before sampling. Call `await graph_builder.build()` first."
                )
            self._tool_graph = self.graph_builder._tool_graph
        return self._tool_graph

    def sample(
        self,
        pattern: SamplingPattern,
        count: int = 1
    ) -> list[SampledPath]:
        """Sample paths according to the specified pattern."""
        paths = []

        for _ in range(count):
            if pattern == SamplingPattern.NODE:
                path = self._sample_node()
            elif pattern == SamplingPattern.CHAIN:
                path = self._sample_chain()
            elif pattern == SamplingPattern.DAG:
                path = self._sample_dag()
            else:
                raise SyntheticDataGenerationError(
                    ErrorCode.INVALID_VALUE.value,f"Unsupported sampling pattern: {pattern} provided. Unable to generate sample path.",
                )

            if path:
                paths.append(path)

        return paths

    def sample_mixed(
        self,
        count: int,
        weights: dict[SamplingPattern, float] | None = None
    ) -> list[SampledPath]:
        """Sample paths with mixed patterns according to weights."""
        if weights is None:
            weights = {
                SamplingPattern.NODE: 0.2,
                SamplingPattern.CHAIN: 0.5,
                SamplingPattern.DAG: 0.3
            }

        # Normalize weights
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}

        paths = []
        patterns = list(weights.keys())
        probs = list(weights.values())

        for _ in range(count):
            pattern = self.rng.choices(patterns, weights=probs)[0]
            sampled = self.sample(pattern, count=1)
            if sampled:
                paths.append(sampled[0])

        return paths

    def _sample_node(self) -> SampledPath:
        """Sample a single tool."""
        tool_graph = self._ensure_graph_built()
        tools = list(tool_graph.tools.keys())

        # Prefer entry points (often more useful as standalone)
        entry_points = self._get_entry_points()
        if entry_points:
            tool = self.rng.choice(entry_points)
        else:
            tool = self.rng.choice(tools)

        return SampledPath(
            pattern=SamplingPattern.NODE,
            tools=[tool]
        )

    def _sample_chain(self) -> SampledPath:
        """Sample a sequential chain of tools."""
        tool_graph = self._ensure_graph_built()

        # Start from an entry point, preferring those with successors
        entry_points = self._get_entry_points()
        if not entry_points:
            entry_points = list(tool_graph.tools.keys())

        # Prioritize entry points that have successors for better chain formation
        entry_with_successors = [
            ep for ep in entry_points
            if self._get_successors(ep)
        ]

        if entry_with_successors:
            start = self.rng.choice(entry_with_successors)
        else:
            start = self.rng.choice(entry_points)

        path = [start]
        visited = {start}

        # Extend the chain
        while len(path) < self.max_path_length:
            current = path[-1]

            # Get valid successors
            successors = self._get_successors(current)
            # Also consider tools that could logically follow
            successors = self._expand_successors(current, successors, tool_graph, visited)

            # Filter out visited
            valid_next = [s for s in successors if s not in visited]

            if not valid_next:
                break

            next_tool = self.rng.choice(valid_next)

            path.append(next_tool)
            visited.add(next_tool)

        # Return correct pattern based on actual path length
        actual_pattern = SamplingPattern.NODE if len(path) == 1 else SamplingPattern.CHAIN
        return SampledPath(
            pattern=actual_pattern,
            tools=path
        )

    def _sample_dag(self) -> SampledPath:
        """Sample a DAG pattern with potential parallel branches.

        A valid DAG must have:
        1. At least 2 tools
        2. At least one parallel group (otherwise it's just a chain)

        If we can't create a valid DAG, we fall back to a chain.
        """
        tool_graph = self._ensure_graph_built()

        # Try multiple times to get a valid DAG
        max_attempts = 5
        for attempt in range(max_attempts):
            # Start with a chain (minimum 2 tools for DAG)
            base_chain = self._sample_chain()
            path = list(base_chain.tools)
            parallel_groups = []

            # Need at least 2 tools to form a DAG
            if len(path) < 2:
                continue

            # Try to add parallel branches at certain points
            for i, tool in enumerate(path[:-1]):
                current = tool_graph.tools[tool]

                # Look for tools that could run in parallel (same type, same inputs)
                parallel_candidates = self._find_parallel_candidates(
                    tool, tool_graph, set(path)
                )

                if parallel_candidates:
                    # Add one parallel tool
                    parallel_tool = self.rng.choice(parallel_candidates)
                    if parallel_tool not in path:
                        path.append(parallel_tool)
                        parallel_groups.append([tool, parallel_tool])
                        break  # One parallel group is enough for a DAG

            # Valid DAG must have parallel groups
            if parallel_groups and len(path) >= 2:
                return SampledPath(
                    pattern=SamplingPattern.DAG,
                    tools=path,
                    parallel_groups=parallel_groups
                )

        # Couldn't create a valid DAG, fall back to chain
        # but label it correctly as CHAIN
        chain = self._sample_chain()
        return SampledPath(
            pattern=SamplingPattern.CHAIN,
            tools=chain.tools,
            parallel_groups=[]
        )

    def _get_entry_points(self) -> list[str]:
        """Get tools that can be starting points (sync, uses cached graph)."""
        tool_graph = self._ensure_graph_built()

        # For resource graph, find tools with no dependencies
        has_incoming = {edge[1] for edge in tool_graph.edges}
        no_deps = [name for name in tool_graph.tools if name not in has_incoming]

        return no_deps if no_deps else list(tool_graph.tools.keys())

    def _get_successors(self, tool_name: str) -> list[str]:
        """Get tools that can follow the given tool (sync, uses cached graph)."""
        self._ensure_graph_built()
        return list(self.graph_builder._graph.successors(tool_name))

    def _get_predecessors(self, tool_name: str) -> list[str]:
        """Get tools that must precede the given tool (sync, uses cached graph)."""
        self._ensure_graph_built()
        return list(self.graph_builder._graph.predecessors(tool_name))

    def _expand_successors(
        self,
        current: str,
        direct_successors: list[str],
        tool_graph: Any,
        visited: set[str]
    ) -> list[str]:
        """Expand successor list with semantically valid options.

        Uses type-based matching (output-type -> input-type) to find
        additional valid successors beyond direct graph edges.
        """
        expanded = set(direct_successors)
        current_tool = tool_graph.tools[current]

        # Get output types of current tool
        output_types = set(t.lower() for t in current_tool.get_output_types())

        # Find tools whose input types match current tool's output
        for name, tool in tool_graph.tools.items():
            if name in visited or name == current:
                continue
            # Check type compatibility
            input_types = set(t.lower() for t in tool.get_input_types())
            if output_types & input_types:
                expanded.add(name)

        return list(expanded)

    def _tools_compatible(self, source_tool: Any, target_tool: Any) -> bool:
        """Check if source tool's output could feed target tool's input.

        Uses pure type-based matching following TaskBench approach.
        """
        output_types = set(t.lower() for t in source_tool.get_output_types())
        input_types = set(t.lower() for t in target_tool.get_input_types())
        return bool(output_types & input_types)

    def _find_parallel_candidates(
        self,
        tool: str,
        tool_graph: Any,
        exclude: set[str]
    ) -> list[str]:
        """Find tools that could run in parallel with the given tool."""
        current = tool_graph.tools[tool]
        candidates = []

        for name, other in tool_graph.tools.items():
            if name in exclude or name == tool:
                continue

            # Same type tools with similar parameters can often run in parallel
            current_params = set(p.get_name() for p in current.get_input_schema().get_parameters())
            other_params = set(p.get_name() for p in other.get_input_schema().get_parameters())

            # If they share input parameters, they might be parallel options
            if current_params & other_params:
                candidates.append(name)

        return candidates

    def get_coverage_stats(self, paths: list[SampledPath]) -> dict[str, Any]:
        """Get statistics on tool coverage from sampled paths."""
        tool_graph = self._ensure_graph_built()
        all_tools = set(tool_graph.tools.keys())

        used_tools = set()
        pattern_counts = {p: 0 for p in SamplingPattern}
        path_lengths = []

        for path in paths:
            used_tools.update(path.tools)
            pattern_counts[path.pattern] += 1
            path_lengths.append(len(path))

        return {
            "total_paths": len(paths),
            "unique_tools_used": len(used_tools),
            "total_tools": len(all_tools),
            "coverage_pct": len(used_tools) / len(all_tools) * 100 if all_tools else 0,
            "unused_tools": list(all_tools - used_tools),
            "pattern_distribution": {p.value: c for p, c in pattern_counts.items()},
            "avg_path_length": sum(path_lengths) / len(path_lengths) if path_lengths else 0,
            "max_path_length": max(path_lengths) if path_lengths else 0
        }

    def ensure_coverage(
        self,
        existing_paths: list[SampledPath],
        min_coverage: float = 0.8
    ) -> list[SampledPath]:
        """Generate additional paths to ensure minimum tool coverage."""
        tool_graph = self._ensure_graph_built()
        all_tools = set(tool_graph.tools.keys())

        used_tools = set()
        for path in existing_paths:
            used_tools.update(path.tools)

        additional_paths = []
        unused = all_tools - used_tools

        # Generate paths that include unused tools
        for tool in unused:
            # Create a chain starting with or including the unused tool
            path = self._sample_chain_including(tool)
            if path:
                additional_paths.append(path)
                used_tools.update(path.tools)

            # Check if we've reached coverage target
            coverage = len(used_tools) / len(all_tools)
            if coverage >= min_coverage:
                break

        return additional_paths

    def _sample_chain_including(self, required_tool: str) -> SampledPath | None:
        """Sample a chain that includes a specific tool."""
        tool_graph = self._ensure_graph_built()
        tool = tool_graph.tools.get(required_tool)

        if not tool:
            return None

        path = []

        # If tool needs predecessors, add them first
        predecessors = self._get_predecessors(required_tool)
        if predecessors:
            # Pick one predecessor
            pred = self.rng.choice(predecessors)
            path.append(pred)

        path.append(required_tool)

        # Add successors if tool is READ type
        successors = self._get_successors(required_tool)
        if successors:
            succ = self.rng.choice(successors)
            path.append(succ)

        return SampledPath(
            pattern=SamplingPattern.CHAIN,
            tools=path
        )
