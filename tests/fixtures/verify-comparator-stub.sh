#!/usr/bin/env bash
# Stub of the vendored verify-comparator.sh (toolchain mechanism) for runner tests.
# Argument 1 is the config path. STUB_MODE=pass prints the acceptance line; anything else fails.
set -euo pipefail
cfg="${1:-comparator.json}"
echo "== lake comparator (toolchain) $cfg"
echo "project $(pwd), toolchain $(tr -d '[:space:]' < lean-toolchain)"
if [ "${STUB_MODE:-pass}" = pass ]; then
  echo "Your solution is okay!"
else
  echo "error: comparator rejected $cfg" >&2
  exit 1
fi
