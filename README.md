# EdgeGen

Generate synthetic test cases for agent evaluation using the Claude-guided offline SDG pipeline.

## Prerequisites

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- `sqlite3` available in PATH

## Downloading the Dataset

The datasets, evaluation results, and metaharness runs are hosted on HuggingFace at [OfflineSdg/EdgeGen](https://huggingface.co/datasets/OfflineSdg/EdgeGen).

### Option 1 — HuggingFace CLI

```bash
pip install huggingface_hub

# Download everything (~1.4 GB)
huggingface-cli download OfflineSdg/EdgeGen --repo-type dataset --local-dir ./hf_data

# Download only the datasets/ folder
huggingface-cli download OfflineSdg/EdgeGen --repo-type dataset --local-dir ./hf_data \
    --include "datasets/*"

# Download eval results for a single domain
huggingface-cli download OfflineSdg/EdgeGen --repo-type dataset --local-dir ./hf_data \
    --include "eval_results_airline/*"
```

### Option 2 — Python (`huggingface_hub`)

```python
from huggingface_hub import snapshot_download, hf_hub_download

# Download the full repo
snapshot_download(repo_id="OfflineSdg/EdgeGen", repo_type="dataset", local_dir="./hf_data")

# Download only the datasets/ folder
snapshot_download(
    repo_id="OfflineSdg/EdgeGen",
    repo_type="dataset",
    allow_patterns=["datasets/*"],
    local_dir="./hf_data",
)

# Download a single file
hf_hub_download(
    repo_id="OfflineSdg/EdgeGen",
    repo_type="dataset",
    filename="datasets/tau2bench_airline/tau2bench_test_real.json",
    local_dir="./hf_data",
)
```

### What's in the repo

| Folder | Contents |
|--------|----------|
| `datasets/tau2bench_airline/` | Train / test / synthetic splits for the airline domain |
| `datasets/tau2bench_retail/` | Train / test / synthetic splits for the retail domain |
| `datasets/toolsandbox/` | Train / synthetic splits for the toolsandbox domain |
| `eval_results_airline/` | Per-model evaluation results — airline (83 files) |
| `eval_results_retail/` | Per-model evaluation results — retail (73 files) |
| `eval_results_toolsandbox/` | Per-model evaluation results — toolsandbox (61 files) |
| `metaharness_gemmaagentmodel/` | Metaharness run data for Gemma agent model |
| `metaharness_gpt5.4agentmodel/` | Metaharness run data for GPT-5.4 agent model (36 files) |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate

cd EdgeGen

# Install agent-synth dependencies
pip install -e src/agent_synth
```

## API Keys

Set the following environment variables (or place them in a `.env` file at the repo root):

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Used by Claude Code CLI for the sampler and verifier |
| `AZURE_API_KEY` | Used by LiteLLM for the LLM generator (default model: `azure/gpt-5.4`) |
| `AZURE_API_BASE` | Azure OpenAI endpoint URL |
| `AZURE_API_VERSION` | Azure API version (e.g. `2024-02-15-preview`) |

If using a non-Azure LLM model, set the appropriate key for your provider (see [LiteLLM docs](https://docs.litellm.ai/docs/providers)). For example, `OPENAI_API_KEY` for OpenAI models.

## Usage

From the repo root (`EdgeGen/`):

```bash
# Run for a single domain
bash run_sdg.sh airline --target 40 --batch-size 8
bash run_sdg.sh retail --target 30
bash run_sdg.sh toolsandbox --target 40

# Or invoke the per-domain script directly
bash mock_systems/airline/sdg/run_sdg_loop.sh --target 10 --batch-size 5
```

### Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--target` | 40 | Number of test cases to generate |
| `--batch-size` | 8 | Test cases attempted per iteration |
| `--output` | `<domain>/sdg/output/testcases_claude_final.json` | Output file path |

## File Structure

```
agent-simulation/
├── run_sdg.sh                          # Top-level entry point
├── EdgeGEn/src/agent_synth/        # Pipeline source code
└── mock_systems/
    └── <domain>/                       # airline | retail | toolsandbox
        ├── db/<domain>_final.db        # SQLite database (read-only at runtime)
        └── sdg/
            ├── run_sdg_loop.sh         # Domain runner script
            └── data/
                ├── tools.json          # Tool schemas
                ├── online_subgoals.json # Subgoal definitions
                └── PRD.md              # Policy / product requirements
```

## How It Works

1. The loop script invokes `python -m agent_synth.synthesizer.offline_sdg_claude.run` per batch
2. The pipeline builds a tool graph, generates violation combinations, and for each:
   - **Claude Code sampler** queries the DB (read-only) to find grounded entity data
   - **LLM generator** (via LiteLLM) produces the test case from the sampled context
   - **Claude Code verifier** validates and fixes the test case against the DB and policy
3. Results are deduplicated and merged into the output JSON
4. DB integrity is checked (MD5) after each iteration — the script aborts if the DB is modified

## Output

Each run produces:
- `mock_systems/<domain>/sdg/output/testcases_claude_final.json` — accumulated test cases
- `mock_systems/<domain>/sdg/output/logs/` — per-iteration logs with Claude sampler/verifier traces
