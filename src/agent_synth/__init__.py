"""
agent-synth: Agent-library-agnostic framework for mock systems and synthesis testing.

Sub-packages:
- agent_synth.mock_system: SQLite walker and generic DB connection utilities
- agent_synth.synthesizer: BaseSynthesizer framework
- agent_synth.synthesizer.offline: Offline synthetic data generation pipeline
"""

from .mock_system.walker import SQLiteWalker
from .mock_system.db import get_conn, init_db, reset_db
from .synthesizer.base import (
    BaseSynthesizer,
    SynthesizerConfig,
    SynthesisResult,
    Trajectory,
    Step,
    IntentDef,
    ToolRegistry,
    ToolEntry,
    find_in_steps,
    run_agent,
)

__all__ = [
    "SQLiteWalker",
    "get_conn",
    "init_db",
    "reset_db",
    "BaseSynthesizer",
    "SynthesizerConfig",
    "SynthesisResult",
    "Trajectory",
    "Step",
    "IntentDef",
    "ToolRegistry",
    "ToolEntry",
    "find_in_steps",
    "run_agent",
]
