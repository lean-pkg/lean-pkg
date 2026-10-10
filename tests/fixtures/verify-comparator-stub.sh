#!/usr/bin/env bash
# Stub of the vendored verify-comparator.sh (toolchain mechanism) for runner tests.
# Argument 1 is the config path. STUB_MODE=pass prints the acceptance line; anything else fails.
# STUB_BEHAVIOUR (#39) adds judge behaviours before the verdict: slow (progress lines with pauses),
# stdin (reads stdin first; hangs forever unless stdin is /dev/null or closed), flood (1 MiB on each of
# stdout and stderr), silent (sleeps with no output), python (a Python child printing, then sleeping).
# STUB_PIDFILE gets this script's pid; STUB_SLEEP sets the pause (default 1 s). STUB_CALLS gets one line
# per call: the config argument and, for a JSON file, its theorem_names. STUB_FAIL_MATCH=<word> fails a
# call whose config argument or theorem_names contain that word. STUB_HANG_MATCH=<word> makes such a call
# print "stub hanging" and sleep (STUB_SLEEP, default 1 s) before its verdict, so a test can kill it.
set -euo pipefail
cfg="${1:-comparator.json}"
names="$(python3 -c 'import json,sys; print(",".join(json.load(open(sys.argv[1]))["theorem_names"]))' "$cfg" 2>/dev/null || true)"
[ -n "${STUB_CALLS:-}" ] && echo "$cfg $names" >> "$STUB_CALLS"
if [ -n "${STUB_FAIL_MATCH:-}" ] && [[ "$cfg $names" == *"$STUB_FAIL_MATCH"* ]]; then STUB_MODE=fail; fi
[ -n "${STUB_PIDFILE:-}" ] && echo $$ > "$STUB_PIDFILE"
pause="${STUB_SLEEP:-1}"
echo "== lake comparator (toolchain) $cfg"
echo "project $(pwd), toolchain $(tr -d '[:space:]' < lean-toolchain)"
if [ -n "${STUB_HANG_MATCH:-}" ] && [[ "$cfg $names" == *"$STUB_HANG_MATCH"* ]]; then
  echo "stub hanging"; sleep "$pause"
fi
case "${STUB_BEHAVIOUR:-}" in
  slow) for i in 1 2 3; do echo "stub progress $i"; sleep "$pause"; done ;;
  stdin) read -r _line || true; echo "stub stdin closed" ;;
  flood) head -c 1048576 /dev/zero | tr '\0' 'e' >&2; echo >&2
         head -c 1048576 /dev/zero | tr '\0' 'o'; echo ;;
  silent) sleep "$pause" ;;
  python) python3 -c 'import time; print("stub python progress"); time.sleep(float("'"$pause"'"))' ;;
esac
if [ "${STUB_MODE:-pass}" = pass ]; then
  echo "Your solution is okay!"
else
  echo "error: comparator rejected $cfg" >&2
  exit 1
fi
