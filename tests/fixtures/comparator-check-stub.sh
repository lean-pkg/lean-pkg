#!/usr/bin/env bash
# Stub of bin/comparator-check.sh for runner tests. STUB_MODE=pass|fail|nolog
set -euo pipefail
mode="${STUB_MODE:-pass}"
echo "project $(pwd), toolchain $(tr -d '[:space:]' < lean-toolchain)"
echo "== comparator @ 5555555555555555555555555555555555555555"
echo "== lean4export @ 6666666666666666666666666666666666666666 (release v4.32.0)"
echo "== nanoda_lib @ 7777777777777777777777777777777777777777"
echo "== landrun @ 8888888888888888888888888888888888888888"
mkdir -p "$LOG_DIR"
shopt -s nullglob
for cfg in comparator*.json comparator/config*.json; do
  name="$(tr '/' '_' <<<"${cfg%.json}")"
  log="$LOG_DIR/$name.log"
  echo "Building Challenge" > "$log"
  if [ "$mode" = pass ]; then
    echo "Your solution is okay!" >> "$log"
    echo "PASS $cfg"
  elif [ "$mode" = nolog ]; then
    rm -f "$log"
    echo "PASS $cfg"
  else
    echo "::error::comparator did not accept $cfg (exit status 1)"
    exit 1
  fi
done
echo "All configurations accepted."
