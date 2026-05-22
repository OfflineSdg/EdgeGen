# synthesizer

Agent-library-agnostic framework for running Claude-based agentic synthesis loops. Provides the base class, tool registry, intent definitions, and the agentic loop that executes intents and records trajectories.

## Module: `base.py`

### Configuration

**`SynthesizerConfig`**

| Field | Env var | Default |
|---|---|---|
| `model` | `SYNTH_MODEL` | `gpt-4.1` |
| `azure_endpoint` | `AZURE_API_BASE` | — |
| `api_key` | `AZURE_API_KEY` | — |
| `api_version` | `AZURE_API_VERSION` | `2024-12-01-preview` |
| `max_steps` | `SYNTH_MAX_STEPS` | `40` |
| `max_tokens` | `SYNTH_MAX_TOKENS` | `4096` |

```python
config = SynthesizerConfig.from_env()
```

---

### Core data structures

**`IntentDef`** — defines one synthesizable action

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Unique identifier (e.g. `"CREATE_ORDER"`) |
| `brief` | `str` | One-sentence description of the task |
| `informed_rules` | `str` | Business rules provided to the agent in informed mode |
| `tools` | `List[str]` | Tool names the agent may call |
| `sampler` | `Callable[[], dict]` | Returns a random set of task parameters |
| `task_prompt` | `Callable[[dict], str]` | Builds the user-facing task instruction from params |
| `postconditions` | `Callable[[dict, List[Step]], dict]` | Evaluates success after execution |

**`Step`** — one tool call: `tool`, `args`, `result`, `is_error`

**`Trajectory`** — full execution trace: intent, mode, params, steps, success, postconditions, metrics

**`SynthesisResult`** — output wrapper: synthesizer name, params, list of trajectory dicts

---

### Tool Registry

**`ToolRegistry`** — maps tool names to implementations and schemas

```python
registry = ToolRegistry()
registry.register("get_user", get_user_fn, "Fetch a user by ID", {
    "type": "object",
    "properties": {"user_id": {"type": "integer"}},
    "required": ["user_id"],
})

result, is_error = registry.call("get_user", {"user_id": 42})
```

**`ToolEntry`** — wraps `(fn, description, input_schema)`; can export to OpenAI or Anthropic tool format.

---

### Agentic loop

**`run_agent(intent_def, params, mode, tool_registry, config) -> Trajectory`**

Executes one intent run using Azure OpenAI with function calling. Runs for up to `config.max_steps` steps, dispatches tool calls via the registry, and records every step.

**Synthesis modes**

| Mode | Behaviour |
|---|---|
| `"blind"` | Agent has no prior knowledge; must learn from error responses |
| `"informed"` | Agent receives `intent_def.informed_rules` as system context |

---

### `BaseSynthesizer`

Abstract base class. Subclass it, implement `setup_tools()` and `setup_intents()`, then call `run_batch()`.

```python
from agent_synth.synthesizer.base import BaseSynthesizer, IntentDef

class MySynthesizer(BaseSynthesizer):
    name = "my_domain"

    def setup_tools(self):
        self.register_tool("get_user", get_user_fn, "Get user by ID", {...})
        self.register_tool("update_user", update_user_fn, "Update user", {...})

    def setup_intents(self):
        self.register_intent(IntentDef(
            name="UPDATE_EMAIL",
            brief="Update a user's email address",
            informed_rules="Email must be unique across all users.",
            tools=["get_user", "update_user"],
            sampler=lambda: {"user_id": random.choice(user_ids())},
            task_prompt=lambda p: f"Update the email for user {p['user_id']} to new@example.com",
            postconditions=lambda p, steps: {"email_updated": find_in_steps(steps, "update_user") is not None},
        ))

synth = MySynthesizer()
results = synth.run_batch(count=10, mode="informed")
```

**Key methods**

| Method | Description |
|---|---|
| `setup_tools()` | Abstract — register tools via `self.register_tool()` |
| `setup_intents()` | Abstract — register intents via `self.register_intent()` |
| `setup_database()` | Optional override — initialize or reset the DB before synthesis |
| `run(params)` | Run one synthesis with explicit params |
| `run_batch(count, intent, mode)` | Run `count` syntheses, cycling through intents |
| `sample_params(intent, mode)` | Sample a random parameter set for an intent |
| `list_intents()` | List registered intent names |
| `list_tools()` | List registered tool names |

---

### Utility functions

**`find_in_steps(steps, tool_name) -> dict | None`** — find the first successful result for a given tool in a trajectory's steps.
