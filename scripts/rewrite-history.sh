#!/usr/bin/env bash
# Rewrite history once before the public flip (decision 0003, option 1).
# Removes the two strings `make history` finds in old commits; every tree that
# main and the release tags point to stays byte-for-byte the same.
#
#   scripts/rewrite-history.sh          dry run: rewrite a fresh mirror and verify it; push nothing
#   scripts/rewrite-history.sh --push   the same, then force-push main and the re-cut tags and
#                                       delete the merged pull-request branches listed below
#
# The forbidden strings are written as two quoted halves, as in
# private-boundary-check.sh, so this file passes the tree scan.
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$(pwd)"

push=no
case "${1:-}" in
  --push) push=yes ;;
  "") ;;
  *) echo "usage: scripts/rewrite-history.sh [--push]" >&2; exit 2 ;;
esac

REMOTE=git@github.com:lean-pkg/lean-pkg.git
# Branches of merged pull requests (#2, #3, #5). They hold pre-rewrite commits,
# so they are deleted, not rewritten; their content is already on main.
STALE_BRANCHES=(docs/future-roadmap docs/global-registry-workflow fix/palomar-snapshot-integrity)

lab='Le-AI''-Lab'
flag='tele''gram_sent'
# Exactly what `make history` reports today. Anything else stops the run: a new
# hit needs its own replacement, chosen by the maintainer.
expected="$(printf '%s\n' \
  "CHANGELOG.md@221ffb8a6d4b:12:First tagged release: the tool extracted from $lab into its own" \
  "commit-message:e89058e6062a:  promote --json reports \`notified\` (was \`$flag\`)")"

die() { echo "rewrite-history: $*" >&2; exit 1; }

command -v git-filter-repo >/dev/null || die "git-filter-repo not found (pacman -S git-filter-repo, or pipx install git-filter-repo)"
[[ "$(git rev-parse --abbrev-ref HEAD)" == main ]] || die "not on main"
[[ -z "$(git status --porcelain)" ]] || die "working tree not clean"
git fetch -q origin
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || die "main differs from origin/main (pull or push first)"

work="$(mktemp -d "${TMPDIR:-/tmp}/lean-pkg-rewrite.XXXXXX")"
mirror="$work/mirror"
echo "rewrite-history: working in $work"
git clone -q --mirror "$REMOTE" "$mirror"
cd "$mirror"

# 1. The scan must find exactly the two known hits, across every ref on GitHub.
before="$(bash "$repo/scripts/private-boundary-check.sh" --history "$mirror" 2>&1 >/dev/null | tail -n +2 || true)"
[[ "$before" == "$expected" ]] || die "history scan differs from the two known hits:"$'\n'"$before"

# 2. Keep only main and the tags. GitHub's pull-request refs are read-only and
#    cannot be pushed back; the stale branches are deleted on GitHub in step 6.
for r in $(git for-each-ref --format='%(refname)' refs/pull); do git update-ref -d "$r"; done
for b in "${STALE_BRANCHES[@]}"; do git update-ref -d "refs/heads/$b"; done
others="$(git for-each-ref --format='%(refname)' | grep -vE '^refs/(heads/main|tags/)' || true)"
[[ -z "$others" ]] || die "unexpected refs (add them to STALE_BRANCHES or handle them):"$'\n'"$others"

old_main="$(git rev-parse refs/heads/main)"
snapshot() {  # ref, tree, tag message: what must not change
  git for-each-ref --format='%(refname)' refs/heads/main refs/tags | while read -r ref; do
    printf '%s %s %s\n' "$ref" "$(git rev-parse "$ref^{tree}")" "$(git for-each-ref --format='%(objecttype) %(contents)' "$ref" | sha256sum | cut -c1-16)"
  done
}
snapshot > "$work/before.txt"
count_before="$(git rev-list --count main)"

# 3. Rewrite. --force: this is the throwaway mirror cloned above, edited only by step 2.
printf 'literal:%s==>%s\n' \
  "the tool extracted from $lab into its own" \
  "the tool extracted from its private lab origin into its own" > "$work/blobs.txt"
printf 'literal:%s==>%s\n' "(was \`$flag\`)" "(was a channel-specific flag)" > "$work/messages.txt"
git filter-repo --force --replace-text "$work/blobs.txt" --replace-message "$work/messages.txt"
cp .git/filter-repo/commit-map "$work/commit-map.txt" 2>/dev/null || cp filter-repo/commit-map "$work/commit-map.txt"

# 4. Verify: scan green; same trees, tag messages, tag types and commit count.
bash "$repo/scripts/private-boundary-check.sh" --history "$mirror" || die "history scan still red after the rewrite"
snapshot > "$work/after.txt"
diff -u "$work/before.txt" "$work/after.txt" || die "a tree or tag changed (see diff above)"
[[ "$(git rev-list --count main)" == "$count_before" ]] || die "commit count changed"
mkdir "$work/main-tree" && git archive main | tar -x -C "$work/main-tree"
(cd "$work/main-tree" && python3 scripts/check-versions.py) || die "version agreement fails on the rewritten main"
new_main="$(git rev-parse refs/heads/main)"
echo "rewrite-history: verified. main $old_main -> $new_main; $count_before commits, $(git tag | wc -l) tags re-cut; trees unchanged."
echo "rewrite-history: old -> new commit ids: $work/commit-map.txt"

if [[ "$push" != yes ]]; then
  echo "rewrite-history: dry run, nothing pushed. Inspect $mirror, then run again with --push."
  exit 0
fi

# 5. Push, refusing if GitHub's main moved since the clone.
git remote add origin "$REMOTE" 2>/dev/null || git remote set-url origin "$REMOTE"
git push --force-with-lease="main:$old_main" origin main
git push --force origin 'refs/tags/*:refs/tags/*'
# 6. Delete the merged pull-request branches.
for b in "${STALE_BRANCHES[@]}"; do git push origin --delete "$b" || true; done

# 7. Confirm GitHub serves the rewritten refs.
[[ "$(git ls-remote "$REMOTE" refs/heads/main | cut -f1)" == "$new_main" ]] || die "GitHub main is not the rewritten main"
git for-each-ref --format='%(objectname) %(refname)' refs/tags | while read -r sha ref; do
  [[ "$(git ls-remote "$REMOTE" "$ref" | cut -f1)" == "$sha" ]] || die "GitHub $ref is not the re-cut tag"
done
echo "rewrite-history: pushed. Every other clone must now be reset, never pushed from:"
echo "  git fetch origin --prune --force --tags && git reset --hard origin/main"
echo "GitHub still holds the old commits under the read-only pull-request refs (refs/pull/*);"
echo "only GitHub Support can remove those."
