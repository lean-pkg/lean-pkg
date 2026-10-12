#!/usr/bin/env bash
# Cut a release: scripts/release.sh X.Y.Z
# Everything is a file: the version lives in pyproject.toml, CITATION.cff and
# CHANGELOG.md; the tag is an annotated git tag; the GitHub release notes are
# the CHANGELOG section. Refuses unless every gate below passes.
#
# The tag push and the GitHub release are retried on transient network errors
# (#73) and are idempotent: re-running after a partial failure completes only
# the missing steps. LEAN_PKG_RELEASE_RETRIES (attempts per step, default 4)
# and LEAN_PKG_RELEASE_RETRY_DELAY (first delay in seconds, default 10, doubled
# per retry: 10, 20, 40 s) tune the retries.
set -euo pipefail
cd "$(dirname "$0")/.."

V="${1:-}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: scripts/release.sh X.Y.Z" >&2; exit 2; }
TAG="v$V"

RETRIES="${LEAN_PKG_RELEASE_RETRIES:-4}"
[[ "$RETRIES" =~ ^[0-9]+$ ]] && (( RETRIES >= 1 )) || RETRIES=4
DELAY="${LEAN_PKG_RELEASE_RETRY_DELAY:-10}"
[[ "$DELAY" =~ ^[0-9]+$ ]] || DELAY=10

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "release: not on main" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "release: working tree not clean" >&2; exit 1; }
# --no-tags: a tag someone else put on origin must not become the local tag; the
# ls-remote check before the push compares tag objects instead.
git fetch -q --no-tags origin
HEAD_SHA="$(git rev-parse HEAD)"
[[ "$HEAD_SHA" == "$(git rev-parse origin/main)" ]] || { echo "release: main differs from origin/main (pull or push first)" >&2; exit 1; }
REUSE_TAG=0
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  [[ "$(git rev-parse "refs/tags/$TAG^{commit}")" == "$HEAD_SHA" ]] \
    || { echo "release: tag $TAG already exists at another commit ($(git rev-parse --short "refs/tags/$TAG^{commit}"))" >&2; exit 1; }
  [[ "$(git cat-file -t "refs/tags/$TAG")" == "tag" ]] || { echo "release: tag $TAG exists but is not annotated" >&2; exit 1; }
  REUSE_TAG=1
fi

agreed="$(python3 scripts/check-versions.py)" || exit 1
[[ "$agreed" == "versions: $V "* ]] || { echo "release: files say a different version than $V" >&2; exit 1; }
grep -qE "^## \[$V\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" CHANGELOG.md || { echo "release: CHANGELOG.md needs '## [$V] - YYYY-MM-DD'" >&2; exit 1; }

make check

NOTES="$(awk -v v="$V" '/^## \[/{p = ($0 ~ "^## \\[" v "\\]")} p' CHANGELOG.md | sed '1d')"
[[ -n "$(printf '%s' "$NOTES" | tr -d '[:space:]')" ]] || { echo "release: CHANGELOG section for $V is empty" >&2; exit 1; }
NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT
printf '%s\n' "$NOTES" > "$NOTES_FILE"

# --- failure classification (consistent with committed mode, #68/#71) -------
# A permanent refusal is checked first: an SSH auth failure also prints
# "Could not read from remote repository", which alone would look transient.
PERMANENT_MARKERS=("permission denied" "authentication failed" "bad credentials" "repository not found"
  "[rejected]" "[remote rejected]" "already exists" "http 401" "http 403" "http 404" "http 422")
TRANSIENT_MARKERS=("connection reset" "kex_exchange_identification" "could not read from remote repository"
  "tls handshake timeout" "i/o timeout" "timed out" "timeout" "connection closed by" "connection refused"
  "the remote end hung up unexpectedly" "early eof" "broken pipe" "could not resolve host"
  "temporary failure in name resolution" "network is unreachable" "ssh: connect to host")
TRANSIENT_HTTP_RE='(returned error:|http|status)[[:space:]]*5[0-9][0-9]([^0-9]|$)'

# Prints the matched marker and succeeds when the text is a transient failure.
transient_reason() {
  local low m
  low="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  for m in "${PERMANENT_MARKERS[@]}"; do [[ "$low" == *"$m"* ]] && return 1; done
  for m in "${TRANSIENT_MARKERS[@]}"; do [[ "$low" == *"$m"* ]] && { printf '%s' "$m"; return 0; }; done
  if m="$(printf '%s\n' "$low" | grep -Eo "$TRANSIENT_HTTP_RE" | head -1)" && [[ -n "$m" ]]; then
    printf '%s' "$(printf '%s' "$m" | sed -E 's/[^0-9]+$//')"; return 0
  fi
  return 1
}

# After a failed attempt: sleep and succeed when the step should be retried;
# print the exact error and fail when it is permanent or the retries are spent.
retry_or_stop() {
  local step="$1" n="$2" out="$3" reason wait
  if ! reason="$(transient_reason "$out")"; then
    echo "release: $step attempt $n/$RETRIES failed (not transient; not retrying):" >&2
    printf '%s\n' "$out" >&2
    return 1
  fi
  if (( n >= RETRIES )); then
    echo "release: $step attempt $n/$RETRIES failed (transient: $reason); giving up after $RETRIES attempts:" >&2
    printf '%s\n' "$out" >&2
    return 1
  fi
  wait=$(( DELAY * (1 << (n - 1)) ))
  echo "release: $step attempt $n/$RETRIES failed (transient: $reason); retrying in $wait s" >&2
  sleep "$wait"
}

# --- step 1: the annotated tag (reused when it already exists at HEAD) -----
if (( REUSE_TAG )); then
  echo "release: reusing the local tag $TAG at $(git rev-parse --short HEAD)"
else
  git tag -a "$TAG" -m "lean-pkg $V" -m "$NOTES"
fi
TAG_OBJ="$(git rev-parse "refs/tags/$TAG")"

# --- step 2: push the tag; done when origin has the same tag object --------
push_tag() {
  local n=0 out remote_obj
  while :; do
    n=$(( n + 1 ))
    if out="$(git ls-remote --tags origin "refs/tags/$TAG" 2>&1)"; then
      remote_obj="$(printf '%s\n' "$out" | awk -v r="refs/tags/$TAG" '$2 == r {print $1; exit}')"
      if [[ "$remote_obj" == "$TAG_OBJ" ]]; then
        echo "release: tag $TAG already on origin (same object); push skipped"
        return 0
      elif [[ -n "$remote_obj" ]]; then
        echo "release: origin has $TAG at a different object ($remote_obj; local $TAG_OBJ); refusing, never forced" >&2
        return 1
      fi
      if out="$(git push origin "refs/tags/$TAG" 2>&1)"; then
        [[ -n "$out" ]] && printf '%s\n' "$out"
        echo "release: tag $TAG pushed"
        return 0
      fi
    fi
    retry_or_stop "tag push" "$n" "$out" || return 1
  done
}
push_tag || exit 1

# --- step 3: the GitHub release; done when `gh release view` finds it ------
URL=""
gh_release() {
  local n=0 out
  while :; do
    n=$(( n + 1 ))
    if out="$(gh release view "$TAG" --json url -q .url 2>&1)"; then
      URL="$out"
      (( n == 1 )) && echo "release: GitHub release $TAG already exists; create skipped"
      return 0
    fi
    if [[ "$(printf '%s' "$out" | tr '[:upper:]' '[:lower:]')" == *"release not found"* ]]; then
      if out="$(gh release create "$TAG" --title "lean-pkg $V" --notes-file "$NOTES_FILE" --verify-tag 2>&1)" \
        || [[ "$(printf '%s' "$out" | tr '[:upper:]' '[:lower:]')" == *"already exists"* || "$out" == *"422"* ]]; then
        if out="$(gh release view "$TAG" --json url -q .url 2>&1)"; then URL="$out"; return 0; fi
      fi
    fi
    retry_or_stop "GitHub release" "$n" "$out" || return 1
  done
}
gh_release || exit 1

echo "release: $TAG at $(git rev-parse --short HEAD); $URL"

# --- step 4: request a Pages build; a failure is a warning, not a failure --
pages_build() {
  local n=0 out repo
  while :; do
    n=$(( n + 1 ))
    if repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>&1)"; then
      if out="$(gh api -X POST "repos/$repo/pages/builds" 2>&1)"; then
        echo "release: Pages build requested"
        return 0
      fi
    else
      out="$repo"
    fi
    retry_or_stop "Pages build request" "$n" "$out" || return 1
  done
}
pages_build || echo "release: WARN: Pages build request failed; request it by hand (RELEASING.md step 6)" >&2
exit 0
