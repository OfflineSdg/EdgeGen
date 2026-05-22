# synthesizer/offline_sdg_claude

Drop-in Claude Code replacements for the standard offline pipeline's LiteLLM-based sampler and verifier. Injects Claude Code directly into the generation loop for smarter database sampling and single-pass fix-and-verify.

## When to use this instead of `offline/`

| | `offline/` | `offline_sdg_claude/` |
|---|---|---|
| DB sampling | Random walk via `StateSampler` / LLM-generated SQL via `ScenarioGuidedSampler` | Claude Code inspects the DB and policy in one pass |
| Post-generation verification | Separate `ExecutionVerifier` + alignment judge | Claude verifies grounding, scenario feasibility, and policy alignment — and fixes issues in the same step |
| Repair | Multiple re-generation attempts | Single-pass fix: corrects parameters, grading notes, policy claims |
| Rate limiting | Unlimited async LLM calls | Bounded semaphore (max 3 concurrent Claude Code calls) |

Use `offline_sdg_claude/` when you want higher-quality, grounded test cases and are willing to trade some throughput for better correctness.

---

## Modules

### `claude_guided_sampler.py` — `ClaudeGuidedSampler`

Replaces `ScenarioGuidedSampler`. Asks Claude Code to:
1. Inspect the database schema and rows
2. Understand the scenario requirements (violated subgoals, context constraints)
3. Find a matching seed entity and walk related data
4. Verify the scenario is actually feasible before returning

Returns `Dict[str, List[dict]]` — the same format as `SQLiteWalker.walk()`.

### `claude_verifier.py` — `ClaudeVerifier`

Replaces `ExecutionVerifier` + `ToolChainVerifier.verify_alignment`. Given a generated `TestCase`, Claude Code:
1. Queries the database to check every parameter value exists
2. Verifies scenario feasibility and subgoal correctness
3. Checks alignment with the domain policy (PRD)
4. Returns one of three statuses:

| Status | Meaning |
|---|---|
| `"valid"` | Test case passes all checks unchanged |
| `"fixed"` | Issues found but corrected — returns a repaired `TestCase` |
| `"rejected"` | Unfixable grounding or feasibility errors |

### `run.py` — CLI entry point (`ClaudeOfflineTestCaseGenerator`)

Extends `OfflineTestCaseGenerator` to inject `ClaudeGuidedSampler` and `ClaudeVerifier` into the pipeline. Runs all combinations in parallel, bounded by a semaphore of 3 concurrent Claude Code calls.

---

## Usage

```bash
python -m agent_synth.synthesizer.offline_sdg_claude.run \
    --db-path mock_system/db/airline.db \
    --sdg-dir mock_system/sdg \
    --output output/testcases_claude.json \
    --model anthropic--claude-4.6-sonnet \
    --llm-model azure/gpt-4.1 \
    --batch-size 10 \
    --domain airline
```

**Required arguments**

| Argument | Description |
|---|---|
| `--db-path` | Path to the SQLite mock database |
| `--sdg-dir` | Directory with `tools.json`, `online_subgoals.json`, `PRD.md` |

**Optional arguments**

| Argument | Default | Description |
|---|---|---|
| `--output` | `output/testcases_claude.json` | Output JSON path |
| `--model` | `anthropic--claude-4.6-sonnet` | Claude model for sampler and verifier |
| `--llm-model` | `azure/gpt-5.4` | LLM model for test case text generation |
| `--batch-size` | `5` | Number of violation combinations to process |
| `--domain` | `airline` | Domain name (label only) |
| `--tool-schema` | `tools.json` | Tool schema filename inside `--sdg-dir` |
| `--rules-file` | `online_subgoals.json` | Business rules filename |
| `--prd-file` | `PRD.md` | PRD filename |
| `--log-dir` | `<output_dir>/logs` | Directory for Claude Code session logs |

The runner searches for `.env` files starting from the `--db-path` and `--sdg-dir` directories upward, then falls back to the current directory.
