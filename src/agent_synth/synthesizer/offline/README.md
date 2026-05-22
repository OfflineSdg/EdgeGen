# synthesizer/offline

Multi-stage LLM pipeline that generates grounded, verifiable test cases from tool schemas, business rules (subgoals), and live database state.

## Quick start

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

Output is written to `output/testcases.json` (or `OUTPUT_DIR` env var).

---

## Entry point: `runner.py`

**`DomainConfig`** — all paths for one domain

| Field | Description |
|---|---|
| `domain_name` | Short name (e.g. `"airline"`) |
| `data_dir` | Directory containing input files |
| `tool_schema_file` | JSON file with tool definitions |
| `online_rules_file` | JSON file with subgoal/business-rule definitions |
| `prd_file` | Markdown PRD describing the domain |
| `output_file` | Filename for generated test cases |
| `db_path` | Path to the SQLite mock database |

**`async run_pipeline(config)`** — loads inputs, instantiates `OfflineTestCaseGenerator`, runs the full pipeline, writes output.

**`main(config)`** — synchronous wrapper calling `asyncio.run(run_pipeline(config))`.

---

## Pipeline stages

`OfflineTestCaseGenerator.generate_testcases()` orchestrates these stages in order:

| # | Stage | Class | Description |
|---|---|---|---|
| 1 | Validate inputs | `SyntheticDataInputValidator` | Checks PRD, subgoals, tool schemas, and sampling weights |
| 2 | Parse PRD | `PRDParser` | Extracts domain context from the PRD markdown |
| 3 | Build tool graph | `GraphBuilder` | Detects tool dependencies (resource or temporal), optionally enriches with LLM |
| 4 | Enrich subgoals | `SubgoalParser` | Assigns IDs and uses LLM to extract `required_context` and `expected_behavior` |
| 5 | Generate violations | `ViolationCombinator` | Powerset of subgoals — empty set = happy path; LLM filters conflicting combos |
| 6 | Sample tool paths | `GraphSampler` | Samples NODE / CHAIN / DAG patterns from the tool graph |
| 7 | Build scenarios | `ViolationScenarioBuilder` | LLM merges context requirements from selected subgoals into a `ScenarioContext` |
| 8 | Sample DB state | `StateSampler` or `ScenarioGuidedSampler` | Walks the mock DB to collect related rows for the scenario |
| 9 | Generate test case | `LLMGenerator` | Writes a task summary, grading notes, and expected tool calls grounded in real DB values |
| 10 | Verify | `ToolChainVerifier` + `ExecutionVerifier` | Pre/post-generation constraint checking and optional live execution |

---

## Key classes

### `GraphBuilder` (`graph_builder.py`)

Builds a `ToolGraph` from tool schemas.

- **`RESOURCE`** dependency type — edges between tools whose output types match input types
- **`TEMPORAL`** dependency type — all-pairs complete graph (any order is valid)
- Optionally enriched by `LLMGraphEnricher` to discover realistic workflow edges from descriptions

### `GraphSampler` (`graph_sampler.py`)

Samples tool chains from the graph.

| Pattern | Description |
|---|---|
| `NODE` | Single tool |
| `CHAIN` | Sequential tool path |
| `DAG` | Path with parallel branches |

`sample_mixed(count, weights)` — samples with configurable probabilities per pattern.

### `ViolationCombinator` (`violation_combinator.py`)

Generates all 2ⁿ powerset combinations of subgoals. Empty combination = happy path. LLM filters combinations whose subgoals conflict with each other.

### `LLMGenerator` (`llm_generator.py`)

Generates test cases grounded in actual database values. The system prompt explicitly prohibits inventing IDs, names, or dates — all values must come from the DB snapshot.

Each test case contains:
- `task_summary` — natural-language user instruction using real DB values
- `grading_notes` — list of `GradingNote(category, assertion)` for evaluation
- `expected_tools` — ordered list of `ToolCall(tool_name, parameters, expected_output_type)`

### `ToolChainVerifier` (`tool_chain_verifier.py`)

Two-phase verification:
1. **Pre-generation** — checks tool ordering constraints (e.g. `MUST_BE_FIRST`, `MUST_FOLLOW`) against the sampled chain; refines or resamples if violated
2. **Post-generation alignment** — LLM-as-judge checks whether the task summary aligns with the tool chain

### `ExecutionVerifier` (`execution_verifier.py`)

Executes expected tool calls against the mock database, then rolls back. Distinguishes:
- **Grounding errors** — hallucinated IDs, missing fields (fail the test case)
- **Domain logic errors** — valid business-rule violations (kept as intentional test scenarios)

### `ScenarioGuidedSampler` (`scenario_guided_sampler.py`)

Replaces `StateSampler` when scenario context is available. LLM generates SQL to find a seed entity that matches the scenario requirements; `SQLiteWalker` then walks the FK graph from that seed.

---

## Sub-packages

| Package | Contents |
|---|---|
| `models/` | All data classes: `TestCase`, `ToolCall`, `SampledPath`, `ToolGraph`, `ScenarioContext`, `ViolationCombination`, `SubGoal`, `ToolSchema`, `LLMPayload`, `LLMResponse` |
| `client/` | `LLMClient` abstract base + `LiteLLMClient` implementation (with exponential backoff) |
| `exception/` | `EvaluationError` hierarchy and component error codes |
| `utils/` | `SyntheticDataInputValidator`, `metrics_utils` (majority voting, pass@k helpers) |

---

## Configuration

`OfflineTestCaseGenerator` accepts a `config` dict. Key constants (from `constants.py`):

| Key | Default | Description |
|---|---|---|
| `batch_size` | `5` | Max violation combinations to process |
| `pattern_weight_node_probability` | `0.2` | NODE pattern sampling weight |
| `pattern_weight_chain_probability` | `0.5` | CHAIN pattern sampling weight |
| `pattern_weight_dag_probability` | `0.3` | DAG pattern sampling weight |
| `max_path_length` | `4` | Max tools in a sampled chain |
| `dependency_type` | `RESOURCE` | Tool graph dependency type |
| `verify_tool_chain` | `True` | Enable pre-generation constraint checking |
| `verify_alignment` | `True` | Enable post-generation alignment check |
| `verify_execution` | `False` | Enable live execution verification |
| `max_refinements` | `3` | Max attempts to fix a constraint-violating chain |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `LLM_MODEL` | `azure/gpt-4.1` | Model for all pipeline LLM calls |
| `AZURE_API_BASE` | — | Azure OpenAI endpoint |
| `AZURE_API_KEY` | — | Azure OpenAI key |
| `AZURE_API_VERSION` | — | Azure API version |
| `BATCH_SIZE` | `5` | Overrides `batch_size` config key |
| `OUTPUT_DIR` | `output/` | Output directory for generated test cases |
| `AGENT_INSPECT_DEBUG_SDG` | — | Set to `1` to save debug artifacts |
