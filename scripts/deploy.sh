#!/usr/bin/env sh
# Thin wrapper around scripts/deploy.py. The real default DEPLOYS (not
# read-only); pass --check or --dry-run to preview. On first run it creates a
# local .venv with the deploy dependency (requirements-dev.txt), then reuses it.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"
VENV="$ROOT/.venv"

if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install -q -r "$ROOT/requirements-dev.txt"
fi

exec "$VENV/bin/python" "$DIR/deploy.py" "$@"
