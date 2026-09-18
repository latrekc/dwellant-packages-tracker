#!/usr/bin/env bash
# Thin wrapper around scripts/deploy.py — forwards all arguments.
# Usage: ./scripts/deploy.sh [--check] [--restart] [--no-restart] [--card-only] [TARGET]
set -euo pipefail
exec python3 "$(dirname "$0")/deploy.py" "$@"
