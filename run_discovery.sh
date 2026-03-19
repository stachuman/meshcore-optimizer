#!/usr/bin/env bash
# Automated MeshCore network discovery
# Run via systemd timer or manually: ./run_discovery.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
CONFIG="$DIR/config.json"
LOG="$DIR/logs/discovery_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$DIR/logs"

echo "=== MeshCore Discovery $(date) ===" | tee "$LOG"

# Activate venv
source "$VENV/bin/activate"

cd "$DIR"
PYTHONUNBUFFERED=1 python -m meshcore_optimizer.discovery --config "$CONFIG" 2>&1 | tee -a "$LOG"

EXIT_CODE=${PIPESTATUS[0]}

if [ $EXIT_CODE -eq 0 ]; then
    echo "=== Discovery completed successfully $(date) ===" | tee -a "$LOG"
else
    echo "=== Discovery FAILED (exit $EXIT_CODE) $(date) ===" | tee -a "$LOG"
fi

# Keep last 30 days of logs
find "$DIR/logs" -name "discovery_*.log" -mtime +30 -delete 2>/dev/null || true

exit $EXIT_CODE
