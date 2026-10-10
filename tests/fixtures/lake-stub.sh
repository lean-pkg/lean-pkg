#!/usr/bin/env bash
# Stub of `lake` for the comparator-receipt setup stages (#26.1). Each call is appended to
# $LAKE_STUB_CALLS; LAKE_STUB_FAIL=<word> makes a call whose arguments contain that word exit 3.
# A repository-selecting Git variable that reaches it is recorded as a `leaked NAME` line (#27).
# `lake build M...` with LAKE_STUB_OLEANS="M..." writes .lake/build/lib/lean/<M>.olean for each listed
# module and prints lake's `✔ [i/n] Built M` line (#56); without it the build is a no-op.
# LAKE_STUB_ENV=<file> gets one `LEAN_NUM_THREADS=<value|unset>` line per call (#55).
set -euo pipefail
echo "lake $*" >> "${LAKE_STUB_CALLS:-/dev/null}"
for v in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
         GIT_COMMON_DIR GIT_NAMESPACE; do
  if [ -n "${!v+x}" ]; then echo "leaked $v" >> "${LAKE_STUB_CALLS:-/dev/null}"; fi
done
if [ -n "${LAKE_STUB_ENV:-}" ]; then echo "LEAN_NUM_THREADS=${LEAN_NUM_THREADS-unset}" >> "$LAKE_STUB_ENV"; fi
echo "stub lake $*"
if [ -n "${LAKE_STUB_FAIL:-}" ] && [[ " $* " == *" ${LAKE_STUB_FAIL} "* ]]; then
  echo "error: could not clone mathlib (stub)" >&2
  exit 3
fi
if [ "${1:-}" = "build" ] && [ -n "${LAKE_STUB_OLEANS:-}" ]; then
  set -- ${LAKE_STUB_OLEANS}
  i=0
  for m in "$@"; do
    i=$((i + 1))
    f=".lake/build/lib/lean/${m//.//}.olean"
    mkdir -p "$(dirname "$f")"
    echo "olean $m $RANDOM" > "$f"
    echo "✔ [$i/$#] Built $m (0.1s)"
  done
fi
if [ "${1:-}" = "build" ]; then echo "Build completed successfully."; fi
