#!/usr/bin/env bash
# Run SDG pipeline iteratively until target number of test cases is reached.
# Uses relative paths — run from anywhere:
#   bash mock_systems/airline/sdg/run_sdg_loop.sh [--target 40] [--batch-size 8]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOCK_SYSTEM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

TARGET="${TARGET:-40}"
BATCH_SIZE="${BATCH_SIZE:-8}"
DB_PATH="$MOCK_SYSTEM_DIR/db/airline_final.db"
SDG_DIR="$SCRIPT_DIR/data"
OUTPUT_FILE="$SCRIPT_DIR/output/testcases_claude_final.json"
LOG_DIR="$SCRIPT_DIR/output/logs"
AGENT_SYNTH_SRC="$REPO_ROOT/src"

while [[ $# -gt 0 ]]; do
    case $1 in
        --target) TARGET="$2"; shift 2 ;;
        --batch-size) BATCH_SIZE="$2"; shift 2 ;;
        --output) OUTPUT_FILE="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$OUTPUT_FILE")"
INITIAL_CHECKSUM=$(md5 -q "$DB_PATH")
ITERATION=0

chmod a-w "$DB_PATH"
trap 'chmod u+w "$DB_PATH"' EXIT

echo "=== SDG Loop (Airline) ==="
echo "  Target:     $TARGET test cases"
echo "  Batch size: $BATCH_SIZE per iteration"
echo "  Output:     $OUTPUT_FILE"
echo "  DB:         $DB_PATH"
echo "  DB MD5:     $INITIAL_CHECKSUM"
echo "  Logs:       $LOG_DIR/"
echo ""

get_count() {
    if [[ -f "$OUTPUT_FILE" ]]; then
        python3 -c "import json; print(len(json.load(open('$OUTPUT_FILE'))))" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}

CURRENT=$(get_count)
echo "  Current:    $CURRENT test cases"
echo ""

if [[ "$CURRENT" -ge "$TARGET" ]]; then
    echo "Already have $CURRENT >= $TARGET test cases. Done."
    exit 0
fi

while true; do
    ITERATION=$((ITERATION + 1))
    CURRENT=$(get_count)
    REMAINING=$((TARGET - CURRENT))

    if [[ "$REMAINING" -le 0 ]]; then
        echo ""
        echo "=== Target reached: $CURRENT / $TARGET ==="
        break
    fi

    THIS_BATCH=$BATCH_SIZE
    if [[ "$REMAINING" -lt "$BATCH_SIZE" ]]; then
        THIS_BATCH=$((REMAINING + 2))
    fi

    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    ITER_LOG="$LOG_DIR/iteration_${ITERATION}_${TIMESTAMP}.log"
    TEMP_OUTPUT="/tmp/sdg_airline_${ITERATION}_${TIMESTAMP}.json"

    echo "--- Iteration $ITERATION: $CURRENT/$TARGET (batch=$THIS_BATCH) ---"

    PYTHONPATH="$AGENT_SYNTH_SRC:${PYTHONPATH:-}" python -m agent_synth.synthesizer.offline_sdg_claude.run \
        --db-path "$DB_PATH" \
        --sdg-dir "$SDG_DIR" \
        --output "$TEMP_OUTPUT" \
        --batch-size "$THIS_BATCH" \
        --domain "airline" \
        --rules-file "online_subgoals.json" \
        --log-dir "$LOG_DIR/iteration_${ITERATION}_${TIMESTAMP}" \
        2>&1 | tee "$ITER_LOG"

    CURRENT_CHECKSUM=$(md5 -q "$DB_PATH")
    if [[ "$CURRENT_CHECKSUM" != "$INITIAL_CHECKSUM" ]]; then
        echo ""
        echo "!!! ERROR: DATABASE MODIFIED !!!"
        echo "  Before: $INITIAL_CHECKSUM"
        echo "  After:  $CURRENT_CHECKSUM"
        echo "Aborting to prevent data corruption."
        rm -f "$TEMP_OUTPUT"
        exit 1
    fi
    echo "  DB integrity OK ($CURRENT_CHECKSUM)"

    NEW_COUNT=$(python3 -c "import json; print(len(json.load(open('$TEMP_OUTPUT'))))" 2>/dev/null || echo "0")

    if [[ "$NEW_COUNT" -gt 0 ]]; then
        python3 -c "
import json

existing = []
try:
    with open('$OUTPUT_FILE') as f:
        existing = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    pass

with open('$TEMP_OUTPUT') as f:
    new_cases = json.load(f)

existing_ids = {tc['id'] for tc in existing}
added = [tc for tc in new_cases if tc['id'] not in existing_ids]
merged = existing + added

with open('$OUTPUT_FILE', 'w') as f:
    json.dump(merged, f, indent=2)

print(f'  Added {len(added)} new test cases (total: {len(merged)})')
"
    else
        echo "  No new test cases generated this iteration"
    fi

    rm -f "$TEMP_OUTPUT"

    CURRENT=$(get_count)
    echo "  Progress: $CURRENT / $TARGET"
    echo ""

    if [[ "$CURRENT" -ge "$TARGET" ]]; then
        echo "=== Target reached: $CURRENT / $TARGET ==="
        break
    fi
done

echo ""
echo "=== Final Summary ==="
echo "  Total iterations: $ITERATION"
echo "  Test cases:       $(get_count)"
echo "  Output:           $OUTPUT_FILE"
echo "  DB integrity:     OK (MD5 unchanged: $INITIAL_CHECKSUM)"
echo "  Logs:             $LOG_DIR/"
