#!/usr/bin/env bash
# private-boundary-check.sh: fail if the tree contains any string that belongs
# to a private operations environment (host names, user names, private paths,
# private notification channels, internal agent names).
#
# Usage: bash scripts/private-boundary-check.sh [DIR]   (default: repo root)
#
# Each forbidden string is written below as two adjacent quoted halves, so
# this file never contains the literal it forbids and can be scanned like
# every other file. Matching is case-sensitive and fixed-string.
set -euo pipefail

root="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

forbidden=(
  'lech''en'
  'lzc''0090'
  'Green''wood'
  'Eas''ley'
  'Home''-Dell'
  'csm-lzc''0090-lap'
  'chenle02.duck''dns.org'
  'Chenle-AMD''-Repos'
  'Dropbox''/workspace'
  'Le-AI''-Lab'
  'tele''gram'
  'Tele''gram'
  'Hao''ran'
  'ki''mi'
  'w9'':p1'
  'Sisy''phus'
)

patterns="$(mktemp)"
trap 'rm -f "$patterns"' EXIT
printf '%s\n' "${forbidden[@]}" > "$patterns"

# Scan every file except VCS metadata, caches, virtualenvs and build output.
hits="$(grep -rnIF -f "$patterns" "$root" \
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
  --exclude-dir=.venv --exclude-dir=venv --exclude-dir=build --exclude-dir=dist \
  --exclude-dir='*.egg-info' || true)"

if [ -n "$hits" ]; then
  echo "private-boundary-check: FAIL: forbidden strings found:" >&2
  printf '%s\n' "$hits" >&2
  exit 1
fi
echo "private-boundary-check: OK (${#forbidden[@]} forbidden strings, none found under $root)"
