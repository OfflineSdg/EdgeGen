#!/usr/bin/env bash
# Run offline SDG (Claude) for a given mock system.
#
# Usage:
#   bash run_sdg.sh airline [--target 40] [--batch-size 8]
#   bash run_sdg.sh retail --target 30
#   bash run_sdg.sh toolsandbox

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <domain> [--target N] [--batch-size N]"
    echo "  domain: airline | retail | toolsandbox"
    exit 1
fi

DOMAIN="$1"; shift

case "$DOMAIN" in
    airline|retail|toolsandbox) ;;
    *) echo "Unknown domain: $DOMAIN (expected: airline, retail, toolsandbox)"; exit 1 ;;
esac

LOOP_SCRIPT="$SCRIPT_DIR/mock_systems/$DOMAIN/sdg/run_sdg_loop.sh"

if [[ ! -f "$LOOP_SCRIPT" ]]; then
    echo "Script not found: $LOOP_SCRIPT"
    exit 1
fi

exec bash "$LOOP_SCRIPT" "$@"
