#!/bin/bash
# Deploy the skill to the OCI box and restart the hub (steps from AGENTS.md).
# Usage: ./deploy.sh          (run from the repo root on your Mac)
set -euo pipefail
KEY="${CANVAS_KEY:-$HOME/.ssh/diqu-oci.key}"
HOST="${CANVAS_HOST:-ubuntu@129.146.63.59}"
cd "$(dirname "$0")"
echo "→ syncing code-canvas/ to $HOST:~/code-canvas/ (nothing on the box is deleted)"
rsync -az --exclude node_modules --exclude __pycache__ --exclude '.DS_Store' \
  -e "ssh -i $KEY" code-canvas/ "$HOST:~/code-canvas/"
echo "→ checking the new code parses on the box's Python, then restarting canvas-hub"
ssh -i "$KEY" "$HOST" 'set -e; cd ~/code-canvas; python3 -m py_compile serve.py build_zoom.py; sudo systemctl restart canvas-hub; sleep 2; systemctl is-active canvas-hub; (command -v node >/dev/null && echo "node: $(node -v)") || echo "node: not installed (the agent cannot take screenshots to self-check)"'
echo "✓ deployed. Hub: http://100.74.9.77:8340/"
