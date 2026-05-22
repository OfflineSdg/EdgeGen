"""
Generic Synthesizer Interface.

A base framework for agent-based data synthesis that works across environments.
Environment-specific implementations only need to:
1. Register tools in TOOL_REGISTRY
2. Define intents in INTENTS
3. Provide database connection via get_db_connection()

The agentic loop, trajectory recording, and synthesis orchestration are generic.
Uses Azure OpenAI for the LLM backend.
"""

from __future__ import annotations

import abc
import dataclasses
import datetime
import json
import os
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import AzureOpenAI

from dotenv import load_dotenv
load_dotenv()


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class SynthesizerConfig:
    """Configuration for the synthesizer."""
    model: str = "gpt-4.1"
    azure_endpoint: str = ""
    api_key: str = ""
    api_version: str = "2024-12-01-preview"
    max_steps: int = 40
    max_tokens: int = 4096

    @classmethod
    def from_env(cls) -> "SynthesizerConfig":
        """Create config from environment variables."""
        return cls(
            model=os.environ.get("SYNTH_MODEL", "gpt-4.1"),
            azure_endpoint=os.environ.get("AZURE_API_BASE", ""),
            api_key=os.environ.get("AZURE_API_KEY", ""),
            api_version=os.environ.get("AZURE_API_VERSION", "2024-12-01-preview"),
            max_steps=int(os.environ.get("SYNTH_MAX_STEPS", "40")),
            max_tokens=int(os.environ.get("SYNTH_MAX_TOKENS", "4096")),
        )


# =============================================================================
# CORE DATA STRUCTURES
# =============================================================================

@dataclass
class Step:
    """A single tool call and its result."""
    tool: str
    args: Dict[str, Any]
    result: Any
    is_error: bool


@dataclass
class Trajectory:
    """Complete execution trace for one intent run."""
    intent: str
    mode: str
    params: Dict[str, Any]
    steps: List[Step]
    success: bool
    postconditions: Dict[str, bool]
    total_steps: int = 0
    error_count: int = 0
    recovery_count: int = 0

    def compute_metrics(self) -> None:
        """Compute derived metrics from steps."""
        self.total_steps = len(self.steps)
        self.error_count = sum(1 for s in self.steps if s.is_error)
        prev_was_error: Dict[str, bool] = {}
        for s in self.steps:
            if s.tool in prev_was_error and prev_was_error[s.tool] and not s.is_error:
                self.recovery_count += 1
            prev_was_error[s.tool] = s.is_error

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "intent": self.intent,
            "mode": self.mode,
            "params": self.params,
            "success": self.success,
            "postconditions": self.postconditions,
            "total_steps": self.total_steps,
            "error_count": self.error_count,
            "recovery_count": self.recovery_count,
            "steps": [
                {"tool": s.tool, "args": s.args, "result": s.result, "is_error": s.is_error}
                for s in self.steps
            ],
        }


@dataclass
class SynthesisResult:
    """Result of a synthesis run."""
    synthesizer: str
    params: Dict[str, Any]
    trajectories: List[dict]


@dataclass
class IntentDef:
    """Definition of a synthesizable intent."""
    name: str
    brief: str
    informed_rules: str
    tools: List[str]
    sampler: Callable[[], Optional[Dict[str, Any]]]
    task_prompt: Callable[[Dict[str, Any]], str]
    postconditions: Callable[[Dict[str, Any], List[Step]], Dict[str, bool]]


# =============================================================================
# TOOL REGISTRY PROTOCOL
# =============================================================================

class ToolEntry:
    """A registered tool with its implementation and schema."""

    def __init__(
        self,
        fn: Callable[..., Any],
        description: str,
        input_schema: dict,
    ):
        self.fn = fn
        self.description = description
        self.input_schema = input_schema

    def to_openai_tool(self, name: str) -> dict:
        """Convert to OpenAI function-calling tool format."""
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def to_anthropic_tool(self, name: str) -> dict:
        """Convert to Anthropic tool format (legacy compatibility)."""
        return {
            "name": name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """Registry for tools available to the agent."""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str,
        input_schema: dict,
    ) -> None:
        """Register a tool."""
        self._tools[name] = ToolEntry(fn, description, input_schema)

    def get(self, name: str) -> Optional[ToolEntry]:
        """Get a tool by name."""
        return self._tools.get(name)

    def call(self, name: str, args: dict) -> Tuple[Any, bool]:
        """Call a tool and return (result, is_error)."""
        entry = self._tools.get(name)
        if entry is None:
            return {"error": f"Unknown tool: {name}"}, True
        try:
            raw = entry.fn(**args)
            return _serialize(raw), False
        except (ValueError, LookupError) as exc:
            return {"error": str(exc)}, True
        except Exception as exc:
            return {"error": f"Unexpected error: {exc}"}, True

    def get_tool_definitions(self, tool_names: List[str]) -> List[dict]:
        """Get OpenAI function-calling tool definitions for the given tool names."""
        return [
            self._tools[name].to_openai_tool(name)
            for name in tool_names
            if name in self._tools
        ]

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())


# =============================================================================
# SERIALIZATION
# =============================================================================

def _serialize(obj: Any) -> Any:
    """Recursively convert dataclasses, dates, and other non-JSON types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


# =============================================================================
# SYSTEM PROMPTS
# =============================================================================

BLIND_SYSTEM_PROMPT = (
    "You are a system agent. You will be given a task to complete by making "
    "API calls. You have NO prior knowledge of the system's business rules or data requirements.\n\n"
    "CRITICAL: You MUST attempt the main action FIRST with your best guess at the parameters. "
    "Do NOT pre-emptively check for existing data, validate inputs, or call discovery APIs. "
    "Only use other tools AFTER you receive an error response that tells you what's missing.\n\n"
    "Learn from each error and adjust your calls accordingly. "
    "When you have successfully completed the task, say 'TASK COMPLETE' and stop."
)

INFORMED_SYSTEM_PREFIX = (
    "You are a system agent. You will be given a task to complete by making "
    "API calls. You have full knowledge of the relevant business rules:\n\n"
)

INFORMED_SYSTEM_SUFFIX = (
    "\nMake the required API calls to complete the task. "
    "When you have successfully completed the task, say 'TASK COMPLETE' and stop."
)


def build_system_prompt(mode: str, intent_def: IntentDef) -> str:
    """Build the system prompt based on mode."""
    if mode == "blind":
        return BLIND_SYSTEM_PROMPT
    return INFORMED_SYSTEM_PREFIX + intent_def.informed_rules + INFORMED_SYSTEM_SUFFIX


# =============================================================================
# AGENTIC LOOP
# =============================================================================

def run_agent(
    intent_def: IntentDef,
    params: Dict[str, Any],
    mode: str,
    tool_registry: ToolRegistry,
    config: SynthesizerConfig,
) -> Trajectory:
    """
    Execute one intent run and return a recorded Trajectory.

    This is the generic agentic loop that works with any tool registry.
    Uses Azure OpenAI with function calling.
    """
    assert mode in ("blind", "informed"), f"mode must be 'blind' or 'informed', got {mode!r}"

    client = AzureOpenAI(
        azure_endpoint=config.azure_endpoint,
        api_key=config.api_key,
        api_version=config.api_version,
    )

    system = build_system_prompt(mode, intent_def)
    task_prompt = intent_def.task_prompt(params)
    tools = tool_registry.get_tool_definitions(intent_def.tools)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task_prompt},
    ]
    steps: List[Step] = []

    for _ in range(config.max_steps):
        response = client.chat.completions.create(
            model=config.model,
            max_tokens=config.max_tokens,
            messages=messages,
            tools=tools if tools else None,
        )

        choice = response.choices[0]

        if choice.finish_reason == "stop":
            messages.append({"role": "assistant", "content": choice.message.content or ""})
            break

        if choice.finish_reason != "tool_calls":
            messages.append({"role": "assistant", "content": choice.message.content or ""})
            break

        # Process tool calls
        assistant_msg = choice.message
        messages.append(assistant_msg)

        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                tool_args = {}

            result, is_error = tool_registry.call(tool_name, tool_args)
            steps.append(Step(tool=tool_name, args=tool_args, result=result, is_error=is_error))

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, default=str),
            })

    post = intent_def.postconditions(params, steps)
    success = bool(post) and all(post.values())

    traj = Trajectory(
        intent=intent_def.name,
        mode=mode,
        params=params,
        steps=steps,
        success=success,
        postconditions=post,
    )
    traj.compute_metrics()
    return traj


# =============================================================================
# BASE SYNTHESIZER CLASS
# =============================================================================

class BaseSynthesizer(abc.ABC):
    """
    Abstract base class for agent-based synthesizers.

    Subclasses must implement:
    - setup_tools(): Register tools in the tool registry
    - setup_intents(): Register intents
    - setup_database(): Initialize/reset the database (optional)
    """

    name: str = "base"
    description: str = "Base synthesizer"
    supports_batch: bool = True

    def __init__(self, config: SynthesizerConfig = None):
        self.config = config or SynthesizerConfig.from_env()
        self.tool_registry = ToolRegistry()
        self.intents: Dict[str, IntentDef] = {}

        # Setup tools and intents
        self.setup_tools()
        self.setup_intents()

    @abc.abstractmethod
    def setup_tools(self) -> None:
        """Register tools in self.tool_registry."""
        pass

    @abc.abstractmethod
    def setup_intents(self) -> None:
        """Register intents in self.intents."""
        pass

    def setup_database(self) -> None:
        """Initialize or reset the database. Override if needed."""
        pass

    def register_tool(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str,
        input_schema: dict,
    ) -> None:
        """Helper to register a tool."""
        self.tool_registry.register(name, fn, description, input_schema)

    def register_intent(self, intent: IntentDef) -> None:
        """Helper to register an intent."""
        self.intents[intent.name] = intent

    def sample_params(
        self,
        intent: str = None,
        mode: str = "blind",
        **kwargs
    ) -> Optional[dict]:
        """Sample run parameters for an intent."""
        intent_name = (intent.upper() if intent else random.choice(list(self.intents.keys())))
        if intent_name not in self.intents:
            raise ValueError(f"Unknown intent: {intent_name!r}")

        intent_def = self.intents[intent_name]
        run_params = intent_def.sampler()
        if run_params is None:
            return None

        return {"intent": intent_name, "mode": mode, "run_params": run_params}

    def run(self, params: dict) -> SynthesisResult:
        """Run a single synthesis."""
        seed = params.get("seed")
        if seed is not None:
            random.seed(seed)

        intent_name = (params.get("intent") or "").upper()
        if not intent_name:
            raise ValueError("params must include 'intent'")
        if intent_name not in self.intents:
            raise ValueError(f"Unknown intent: {intent_name!r}")

        mode = params.get("mode", "blind")
        intent_def = self.intents[intent_name]

        # Use pre-sampled run_params if provided, else sample now
        run_params = params.get("run_params")
        if run_params is None:
            run_params = intent_def.sampler()
            if run_params is None:
                raise ValueError(f"No eligible records in DB for intent {intent_name!r}")

        traj = run_agent(intent_def, run_params, mode, self.tool_registry, self.config)

        return SynthesisResult(
            synthesizer=self.name,
            params={**params, "run_params": run_params},
            trajectories=[traj.to_dict()],
        )

    def run_batch(
        self,
        count: int,
        intent: str = None,
        mode: str = "blind",
    ) -> List[SynthesisResult]:
        """Run multiple syntheses."""
        results = []
        intent_names = [intent.upper()] if intent else list(self.intents.keys())

        for i in range(count):
            # Cycle through intents if not specified
            current_intent = intent_names[i % len(intent_names)]

            params = self.sample_params(intent=current_intent, mode=mode)
            if params is None:
                continue

            result = self.run(params)
            results.append(result)

        return results

    def list_intents(self) -> List[str]:
        """List available intent names."""
        return list(self.intents.keys())

    def list_tools(self) -> List[str]:
        """List available tool names."""
        return self.tool_registry.list_tools()


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def find_in_steps(steps: List[Step], tool_name: str) -> Optional[dict]:
    """Find the first successful result for a tool in the steps."""
    for s in steps:
        if s.tool == tool_name and not s.is_error:
            return s.result
    return None
