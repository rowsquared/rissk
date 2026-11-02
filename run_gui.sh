#!/usr/bin/env bash
# RISSK GUI launcher (uv-aware)
# Run this script from any directory — it always resolves relative to itself.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"   # the project root (this script lives at the repo root)

echo ""
echo "Starting RISSK GUI..."
echo "Open your browser at: http://localhost:8080"
echo "(Press Ctrl+C to stop)"
echo ""
uv run --extra gui python app/main.py
