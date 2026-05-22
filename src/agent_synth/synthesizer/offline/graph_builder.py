"""Build tool dependency graph from schemas.

Implements graph generation faithful to TaskBench paper:
- Resource Dependency: Connect tools based on output-type -> input-type matching
- Temporal Dependency: Complete graph where all tools can follow any other tool

Additionally supports LLM-based graph enrichment to discover edges from tool descriptions.
"""

import networkx as nx
from typing import Any, TYPE_CHECKING

from .models.pipeline_models import ToolGraph, Constraint
from .models.tools_schema import ToolSchema

if TYPE_CHECKING:
    from .llm_graph_enricher import LLMGraphEnricher


class DependencyType:
    """Types of dependency graphs supported."""
    RESOURCE = "resource"  # Output type -> Input type matching
    TEMPORAL = "temporal"  # Complete graph (all tools connected)


class GraphBuilder:
    """Build a dependency graph from tool schemas.

    Supports two dependency detection modes (following TaskBench):

    1. Resource Dependency (for tools with type info):
       - Connect Tool A -> Tool B if A's output_types intersect B's input_types
       - Pure type-based matching, no domain-specific rules
       - Optionally enriched with LLM-based edge discovery

    2. Temporal Dependency (for tools without type info):
       - Create complete graph where all tools can follow any other tool
       - Used when tools only have generic parameters without type semantics
    """

    def __init__(
        self,
        tools: dict[str, ToolSchema],
        dependency_type: str = DependencyType.RESOURCE,
        llm_enricher: "LLMGraphEnricher | None" = None
    ):
        self.tools = tools
        self.dependency_type = dependency_type
        self.llm_enricher = llm_enricher
        self._graph: nx.DiGraph | None = None
        self._tool_graph: ToolGraph | None = None

    async def build(self) -> ToolGraph:
        """Build and return the tool graph.

        If llm_enricher is provided and dependency_type is RESOURCE, the graph
        will be enriched with additional edges discovered from tool descriptions.
        """
        if self._tool_graph is not None:
            return self._tool_graph

        tools = self.tools

        # Build networkx graph for internal operations
        self._graph = nx.DiGraph()

        # Add all tools as nodes
        for name, tool in tools.items():
            self._graph.add_node(name, tool=tool)

        # Detect edges based on dependency type
        edges = await self._detect_dependencies(tools)

        # Add edges to graph
        for from_tool, to_tool, attrs in edges:
            self._graph.add_edge(from_tool, to_tool, **attrs)

        # Constraints can be provided externally if needed
        constraints = []

        self._tool_graph = ToolGraph(
            tools=tools,
            edges=edges,
            constraints=constraints
        )

        return self._tool_graph

    async def _detect_dependencies(
        self,
        tools: dict[str, ToolSchema]
    ) -> list[tuple[str, str, dict]]:
        """Detect dependencies based on configured dependency type."""
        if self.dependency_type == DependencyType.TEMPORAL:
            return self._build_temporal_graph(tools)
        else:  # Default to resource dependency
            return await self._build_resource_graph(tools)

    #TODO: need to think about how we can optimize this for large tool sets - currently O(n^2) for pairwise comparison
    async def _build_resource_graph(
        self,
        tools: dict[str, ToolSchema]
    ) -> list[tuple[str, str, dict]]:
        """Build resource dependency graph.

        Following TaskBench: Connect tools based on output-type -> input-type matching.
        Tool A -> Tool B if intersection(A.output_types, B.input_types) is non-empty.

        If llm_enricher is provided, additional edges are discovered from descriptions.
        """
        edges = []
        tool_list = list(tools.values())

        # Step 1: Build type-based edges
        for i, tool_a in enumerate(tool_list):
            output_types = set(t.lower() for t in tool_a.get_output_types())

            for j, tool_b in enumerate(tool_list):
                if i == j:  # No self-loops
                    continue

                input_types = set(t.lower() for t in tool_b.get_input_types())

                # Check if output types of A intersect input types of B
                matching_types = output_types.intersection(input_types)
                if matching_types:
                    edges.append((
                        tool_a.name,
                        tool_b.name,
                        {
                            "type": "resource",
                            "matching_types": list(matching_types)
                        }
                    ))

        # Step 2: Enrich with LLM-discovered edges if enricher is provided
        if self.llm_enricher:
            additional_edges = await self.llm_enricher.find_additional_edges(tools, edges)
            edges.extend(additional_edges)

        return edges

    def _build_temporal_graph(
        self,
        tools: dict[str, ToolSchema]
    ) -> list[tuple[str, str, dict]]:
        """Build temporal dependency graph.

        Following TaskBench: Create complete graph where all tools can follow
        any other tool. Used when tools don't have explicit type dependencies.
        """
        edges = []
        tool_names = list(tools.keys())

        for i, source in enumerate(tool_names):
            for j, target in enumerate(tool_names):
                if i != j:  # No self-loops
                    edges.append((
                        source,
                        target,
                        {"type": "temporal"}
                    ))

        return edges

    async def get_networkx_graph(self) -> nx.DiGraph:
        """Get the underlying networkx graph."""
        await self.build()
        return self._graph

    async def get_entry_points(self) -> list[str]:
        """Get tools that can be starting points.

        For resource graphs: tools with no incoming edges
        For temporal graphs: all tools (since it's complete)
        """
        tool_graph = await self.build()
        tools = tool_graph.tools

        if self.dependency_type == DependencyType.TEMPORAL:
            # In temporal graph, any tool can be entry point
            return list(tools.keys())

        # For resource graph, find tools with no dependencies
        has_incoming = {edge[1] for edge in tool_graph.edges}
        no_deps = [name for name in tools if name not in has_incoming]


        return no_deps if no_deps else list(tools.keys())

    async def get_terminal_points(self) -> list[str]:
        """Get tools that can be ending points.

        For resource graphs: tools with no outgoing edges
        For temporal graphs: all tools (since it's complete)
        """
        tool_graph = await self.build()
        tools = tool_graph.tools

        if self.dependency_type == DependencyType.TEMPORAL:
            # In temporal graph, any tool can be terminal
            return list(tools.keys())

        # For resource graph, find tools with no successors
        has_outgoing = {edge[0] for edge in tool_graph.edges}
        no_out = [name for name in tools if name not in has_outgoing]

        return no_out if no_out else list(tools.keys())

    async def get_successors(self, tool_name: str) -> list[str]:
        """Get tools that can follow the given tool."""
        await self.build()
        return list(self._graph.successors(tool_name))

    async def get_predecessors(self, tool_name: str) -> list[str]:
        """Get tools that must precede the given tool."""
        await self.build()
        return list(self._graph.predecessors(tool_name))

    async def export_to_dict(self) -> dict[str, Any]:
        """Export graph structure as dictionary."""
        tool_graph = await self.build()

        return {
            "dependency_type": self.dependency_type,
            "tools": {
                name: {
                    "description": tool.get_description(),
                    "parameters": [p.get_name() for p in tool.get_input_schema().get_parameters()],
                    "returns": tool.get_returns_description(),
                    "input_types": tool.get_input_types(),
                    "output_types": tool.get_output_types()
                }
                for name, tool in tool_graph.tools.items()
            },
            "edges": [
                {"from": e[0], "to": e[1], "attrs": e[2]}
                for e in tool_graph.edges
            ],
            "entry_points": await self.get_entry_points(),
            "terminal_points": await self.get_terminal_points()
        }
