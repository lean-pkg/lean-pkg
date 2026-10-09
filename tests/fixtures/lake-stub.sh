#!/usr/bin/env bash
# Stub of `lake` for the comparator-receipt setup stages (#26.1). Each call is appended to
# $LAKE_STUB_CALLS; LAKE_STUB_FAIL=<word> makes a call whose arguments contain that word exit 3.
# A repository-selecting Git variable that reaches it is recorded as a `leaked NAME` line (#27).
set -euo pipefail
echo "lake $*" >> "${LAKE_STUB_CALLS:-/dev/null}"
for v in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
         GIT_COMMON_DIR GIT_NAMESPACE; do
  if [ -n "${!v+x}" ]; then echo "leaked $v" >> "${LAKE_STUB_CALLS:-/dev/null}"; fi
done
echo "stub lake $*"
if [ -n "${LAKE_STUB_FAIL:-}" ] && [[ " $* " == *" ${LAKE_STUB_FAIL} "* ]]; then
  echo "error: could not clone mathlib (stub)" >&2
  exit 3
fi
