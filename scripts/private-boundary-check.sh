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

# Public URLs that legitimately contain one of the strings above: the
# maintainer's public university homepage, whose path carries a username.
# Written in halves like the list above. A hit is allowed only if, after
# removing these prefixes, the line no longer matches any forbidden string.
allowed_public=(
  'webhome.auburn.edu/~lzc''0090/'
)

patterns="$(mktemp)"
trap 'rm -f "$patterns"' EXIT
printf '%s\n' "${forbidden[@]}" > "$patterns"

# The public name of a university cluster (an acknowledgement, not infrastructure) is not forbidden.
# Scan only what git would ship: tracked files plus untracked files that are
# not ignored (so a new file is caught before its first commit, while ignored
# scratch such as worktree ledgers or caches never trips the check). Outside a
# git checkout, fall back to a recursive scan that skips VCS metadata, caches,
# virtualenvs and build output.
if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  hits="$(cd "$root" && git ls-files -z --cached --others --exclude-standard \
    | xargs -0 -r grep -nIiF -f "$patterns" -- || true)"
else
  hits="$(grep -rnIiF -f "$patterns" "$root" \
    --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
    --exclude-dir=.venv --exclude-dir=venv --exclude-dir=build --exclude-dir=dist \
    --exclude-dir='*.egg-info' || true)"
fi

if [ -n "$hits" ]; then
  # Re-check the matched CONTENT only (drop the "path:line:" prefix, which may
  # itself contain a private path on a developer machine) after removing the
  # allowed public prefixes.
  for pub in "${allowed_public[@]}"; do
    hits="$(printf '%s\n' "$hits" | sed "s#${pub}#<public-url>/#g")"
  done
  kept=""
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    content="${hit#*:}"; content="${content#*:}"
    if printf '%s\n' "$content" | grep -qF -f "$patterns"; then
      kept="${kept}${hit}"$'\n'
    fi
  done <<< "$hits"
  hits="${kept%$'\n'}"
fi

if [ -n "$hits" ]; then
  echo "private-boundary-check: FAIL: forbidden strings found:" >&2
  printf '%s\n' "$hits" >&2
  exit 1
fi
echo "private-boundary-check: OK (${#forbidden[@]} forbidden strings, ${#allowed_public[@]} public-URL exception(s), none found under $root)"
