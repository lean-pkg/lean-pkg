#!/usr/bin/env bash
# Stub of `lake` for the comparator-receipt setup stages (#26.1). Each call is appended to
# $LAKE_STUB_CALLS; LAKE_STUB_FAIL=<word> makes a call whose arguments contain that word exit 3.
set -euo pipefail
echo "lake $*" >> "${LAKE_STUB_CALLS:-/dev/null}"
echo "stub lake $*"
if [ -n "${LAKE_STUB_FAIL:-}" ] && [[ " $* " == *" ${LAKE_STUB_FAIL} "* ]]; then
  echo "error: could not clone mathlib (stub)" >&2
  exit 3
fi
