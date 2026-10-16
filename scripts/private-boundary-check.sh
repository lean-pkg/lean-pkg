#!/usr/bin/env bash
# private-boundary-check.sh: fail if the tree contains any string that belongs
# to a private operations environment (host names, user names, private paths,
# private notification channels, internal agent names).
#
# Usage: bash scripts/private-boundary-check.sh [DIR]             tree: what git would ship now
#        bash scripts/private-boundary-check.sh --history [DIR]   every blob, commit message, author and
#                                                                committer identity, annotated tag
#                                                                (tagger and message) reachable from
#                                                                any ref (the public flip exposes all)
#
# Each forbidden string is written below as two adjacent quoted halves, so
# this file never contains the literal it forbids and can be scanned like
# every other file. Matching is case-insensitive and fixed-string, everywhere
# (the first match and the re-check after the public-URL exceptions alike).
# Binary files and blobs are not skipped: their printable runs (non-printable
# bytes turned into line breaks) are scanned and a hit is labelled "binary".
# A tracked symbolic link is scanned as its target string, which is what git
# stores and publishes.
set -euo pipefail

mode=tree
if [ "${1:-}" = "--history" ]; then mode=history; shift; fi
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
blob="$(mktemp)"
trap 'rm -f "$patterns" "$blob"' EXIT
printf '%s\n' "${forbidden[@]}" > "$patterns"
# Extra forbidden strings, one per line, from a file (the tests plant made-up names this way).
if [ -n "${PRIVATE_BOUNDARY_EXTRA:-}" ]; then grep -v '^$' "$PRIVATE_BOUNDARY_EXTRA" >> "$patterns" || true; fi

# grep_any FILE LABEL: the forbidden strings in FILE as "LABEL:LINE:text" lines. A text file is
# grepped as is; a binary one (grep -I would skip it) through its printable runs, as "LABEL:binary:text".
grep_any() {
  local found
  found="$(grep -nIiF -f "$patterns" -- "$1" || true)"
  if [ -z "$found" ] && ! grep -qI '' -- "$1" 2>/dev/null && [ -s "$1" ]; then
    found="$(LC_ALL=C tr -c '[:print:]\t\n' '\n' < "$1" | grep -iF -f "$patterns" | sed 's/^/binary:/' || true)"
  fi
  [ -n "$found" ] && printf '%s\n' "$found" | sed "s#^#$2:#"
  return 0
}

# The public name of a university cluster (an acknowledgement, not infrastructure) is not forbidden.
# Scan only what git would ship: tracked files plus untracked files that are
# not ignored (so a new file is caught before its first commit, while ignored
# scratch such as worktree ledgers or caches never trips the check). Outside a
# git checkout, fall back to a recursive scan that skips VCS metadata, caches,
# virtualenvs and build output.
if [ "$mode" = history ]; then
  git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "private-boundary-check: --history needs a git checkout" >&2; exit 2; }
  hits=""
  # every blob reachable from any ref, once each, labelled by one path it had
  # (a symbolic link's blob is its target string); every annotated tag object (tag name, tagger,
  # message)
  while read -r otype osha opath; do
    case "$otype" in blob|tag) ;; *) continue ;; esac
    git -C "$root" cat-file "$otype" "$osha" > "$blob"
    [ "$otype" = tag ] && opath="tag-object"
    found="$(grep_any "$blob" "${opath}@${osha:0:12}")"
    [ -n "$found" ] || continue
    hits="${hits}${found}"$'\n'
  done < <(git -C "$root" rev-list --all --objects | git -C "$root" cat-file --batch-check='%(objecttype) %(objectname) %(rest)' | sort -u -k2,2)
  # every author and committer identity (published with each commit)
  found="$(git -C "$root" log --all --format='%an <%ae>%n%cn <%ce>' | sort -u | grep -iF -f "$patterns" | sed 's#^#commit-identity:-:#' || true)"
  [ -z "$found" ] || hits="${hits}${found}"$'\n'
  # every commit message
  while read -r c; do
    found="$(git -C "$root" log -1 --format=%B "$c" | grep -iF -f "$patterns" -- || true)"
    [ -n "$found" ] || continue
    hits="${hits}$(printf '%s\n' "$found" | sed "s#^#commit-message:${c:0:12}:#")"$'\n'
  done < <(git -C "$root" rev-list --all)
  hits="${hits%$'\n'}"
elif git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  hits=""
  while IFS= read -r -d '' f; do
    if [ -L "$root/$f" ]; then       # the target string is what git ships
      found="$(readlink -- "$root/$f" | grep -iF -f "$patterns" | sed "s#^#${f}:symlink:#" || true)"
    elif [ -f "$root/$f" ]; then
      found="$(grep_any "$root/$f" "$f")"
    else
      continue
    fi
    [ -n "$found" ] && hits="${hits}${found}"$'\n'
  done < <(git -C "$root" ls-files -z --cached --others --exclude-standard)
  hits="${hits%$'\n'}"
else
  hits="$(grep -rnaiF -f "$patterns" "$root" \
    --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
    --exclude-dir=.venv --exclude-dir=venv --exclude-dir=build --exclude-dir=dist \
    --exclude-dir='*.egg-info' || true)"
fi

if [ -n "$hits" ]; then
  # Re-check the matched CONTENT only (drop the "path:line:" prefix, which may
  # itself contain a private path on a developer machine) after removing the
  # allowed public prefixes.
  for pub in "${allowed_public[@]}"; do
    hits="$(printf '%s\n' "$hits" | sed "s#${pub}#<public-url>/#gI")"
  done
  kept=""
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    content="${hit#*:}"; content="${content#*:}"
    if printf '%s\n' "$content" | grep -qiF -f "$patterns"; then
      kept="${kept}${hit}"$'\n'
    fi
  done <<< "$hits"
  hits="${kept%$'\n'}"
fi

if [ -n "$hits" ]; then
  echo "private-boundary-check: FAIL ($mode): forbidden strings found:" >&2
  printf '%s\n' "$hits" >&2
  exit 1
fi
echo "private-boundary-check: OK ($mode: ${#forbidden[@]} forbidden strings, ${#allowed_public[@]} public-URL exception(s), none found under $root)"
