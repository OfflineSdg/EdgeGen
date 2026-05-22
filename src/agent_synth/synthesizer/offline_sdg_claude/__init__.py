"""Claude Code-based replacements for the offline SDG sampler and verifier.

This module provides drop-in replacements for:
- ScenarioGuidedSampler → ClaudeGuidedSampler
- ExecutionVerifier → ClaudeExecutionVerifier
- ToolChainVerifier.verify_alignment → ClaudeAlignmentVerifier

These use Claude Code (CLI) to read the database directly, understand the policy,
and verify feasibility in a single intelligent pass — replacing the heuristic +
LLM-as-judge approach that produces infeasible test cases.
"""

from .claude_guided_sampler import ClaudeGuidedSampler
from .claude_verifier import ClaudeVerifier

__all__ = ["ClaudeGuidedSampler", "ClaudeVerifier"]
