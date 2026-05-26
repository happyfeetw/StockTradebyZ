#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

python_bin="${PYTHON:-python3}"
exec "$python_bin" scripts/harness/check.py "$@"
