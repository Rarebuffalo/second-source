#!/usr/bin/env bash
# Quality gate: run before every commit. Fails (non-zero exit) if lint, formatting or tests fail.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
