# agent-synth

Agent-library-agnostic framework for mock systems and synthesis-driven testing. Extracted from [tau2bench] to work with any agent library.

## Overview

`agent-synth` provides three core capabilities:

1. **Mock System Utilities** — Generic SQLite walker and connection manager for mock API databases
2. **Synthesizer Framework** — BaseSynthesizer, tool registry, and agentic loop for generating test trajectories
3. **Offline Pipeline** — LLM-powered test case generation from tool schemas and business rules

## Installation

```bash
git clone <REPLACE_WITH_URL>/tau2bench.git
cd tau2bench

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install agent-synth (the framework) and tau2-bench (the domains)
pip install -e agent-synth/
pip install -e tau2-bench/
```

## Quick Start

### Using the SQLiteWalker

The walker discovers schema from any SQLite database via PRAGMA introspection, walks FK relationships, and returns connected subgraphs:

```python
from agent_synth.mock_system.walker import SQLiteWalker

walker = SQLiteWalker("/path/to/any.db")
result = walker.walk(seed_table="users")
# result: {"users": [{...}], "orders": [{...}], ...}
```

### Using the Synthesizer Framework

Create a domain-specific synthesizer by subclassing `BaseSynthesizer`:

```python
from agent_synth.synthesizer.base import BaseSynthesizer, IntentDef

class MySynthesizer(BaseSynthesizer):
    name = "my_domain"

    def setup_tools(self):
        self.register_tool("get_user", get_user_fn, "Get user by ID", {...})

    def setup_intents(self):
        self.register_intent(IntentDef(
            name="CREATE_USER",
            brief="Create a new user",
            informed_rules="...",
            tools=["create_user", "get_user"],
            sampler=lambda: {"name": "Alice"},
            task_prompt=lambda p: f"Create a user named {p['name']}",
            postconditions=lambda p, steps: {"created": True},
        ))
```

### Running the Offline Pipeline

```python
import asyncio
from pathlib import Path
from agent_synth.synthesizer.offline.runner import DomainConfig, run_pipeline

config = DomainConfig(
    domain_name="airline",
    data_dir=Path("mock_system/sdg"),
    tool_schema_file="tools.json",
    online_rules_file="online_subgoals.json",
    prd_file="PRD.md",
    output_file="testcases.json",
    db_path=Path("mock_system/db/airline.db"),
)
asyncio.run(run_pipeline(config))
```

## Generating a New Domain

`agent-synth` works with Claude Code skills that automate domain creation:

### Step 1: Generate the Mock System

Given an agent with tools/APIs, generate the SQLite-backed mock system:

```
/generate-mock-system path/to/your/agent
```

This creates:
- `mock_system/db/schema.sql` — Database schema
- `mock_system/db/db.py` — Connection manager (imports from `agent_synth`)
- `mock_system/db/models.py` — Dataclass definitions
- `mock_system/apis/` — CRUD operations for all entities
- `mock_system/setup.py` — Seed data initialization

### Step 2: Create the Synthesizer Agent

```
/create-synthesizer-agent path/to/your/agent
```

This creates:
- `synthesizer/synthesizer_agent.py` — Domain-specific tools, intents, samplers
- `synthesizer/__init__.py` — Exports

### Step 3: Run Offline Pipeline

```bash
python -m your_domain.synthesizer.offline.runner --batch 20
```

## Package Structure

```
agent-synth/
├── pyproject.toml
└── src/agent_synth/
    ├── mock_system/
    │   ├── walker.py          # Generic SQLite schema walker (BFS via FK)
    │   └── db.py              # Parametrized connection manager
    └── synthesizer/
        ├── base.py            # BaseSynthesizer, ToolRegistry, run_agent()
        ├── offline/           # Offline test case generation pipeline
        │   ├── runner.py      # Generic runner with DomainConfig
        │   ├── state_sampler.py
        │   ├── synthetic_data_generator.py
        │   ├── graph_builder.py
        │   ├── graph_sampler.py
        │   ├── llm_generator.py
        │   └── ...
        └── offline_sdg_claude/  # Claude Code-powered sampler + verifier
            ├── run.py
            ├── claude_guided_sampler.py
            └── claude_verifier.py
```

## Feature READMEs

Each feature area has its own README with full API reference and usage examples:

- [mock_system/](src/agent_synth/mock_system/README.md) — SQLite walker and connection manager
- [synthesizer/](src/agent_synth/synthesizer/README.md) — Agentic loop, BaseSynthesizer, ToolRegistry
- [synthesizer/offline/](src/agent_synth/synthesizer/offline/README.md) — Multi-stage LLM test case generation pipeline
- [synthesizer/offline_sdg_claude/](src/agent_synth/synthesizer/offline_sdg_claude/README.md) — Claude Code-based sampling and verification

## Architecture

```
┌─────────────────────────────────────────────┐
│              agent-synth (generic)           │
│                                             │
│  SQLiteWalker  BaseSynthesizer              │
│  get_conn()   ToolRegistry     DomainConfig │
│  init_db()    run_agent()                   │
└──────────────────────┬──────────────────────┘
                       │ imports
┌──────────────────────▼──────────────────────┐
│         Domain (e.g. airline, retail)        │
│                                             │
│  mock_system/         synthesizer/          │
│    db/schema.sql        synthesizer_agent.py│
│    db/db.py (wrapper)   offline/runner.py   │
│    db/models.py         offline/data/       │
│    apis/*.py                                │
│    setup.py                                 │
└─────────────────────────────────────────────┘
```

Domain code is thin: it provides schema, APIs, data files, and thin wrappers that inject paths into the generic framework.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `SYNTH_MODEL` | Azure OpenAI model for synthesizer | `gpt-4.1` |
| `AZURE_API_BASE` | Azure OpenAI endpoint | — |
| `AZURE_API_KEY` | Azure OpenAI key | — |
| `AZURE_API_VERSION` | Azure API version | `2024-12-01-preview` |
| `SYNTH_MAX_STEPS` | Max agentic loop steps | `40` |
| `LLM_MODEL` | Model for offline pipeline | `azure/gpt-4.1` |
| `BATCH_SIZE` | Offline pipeline batch size | `5` |
| `OUTPUT_DIR` | Output directory for test cases | `output/` |

## Dependencies

- `anthropic` — Claude API client (for synthesizer agentic loop)
- `openai` — Azure OpenAI client (for synthesizer agentic loop)
- `litellm` — Multi-provider LLM client (for offline pipeline)
- `backoff` — Retry logic for LLM calls
- `networkx` — Tool dependency graph operations
- `python-dotenv` — Environment variable loading

No dependency on tau2 or any specific agent framework.
