#!/usr/bin/env bash
# Cut a release: scripts/release.sh X.Y.Z
# Everything is a file: the version lives in pyproject.toml, CITATION.cff and
# CHANGELOG.md; the tag is an annotated git tag; the GitHub release notes are
# the CHANGELOG section. Refuses unless every gate below passes.
set -euo pipefail
cd "$(dirname "$0")/.."

V="${1:-}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: scripts/release.sh X.Y.Z" >&2; exit 2; }
TAG="v$V"

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "release: not on main" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "release: working tree not clean" >&2; exit 1; }
git fetch -q origin
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || { echo "release: main differs from origin/main (pull or push first)" >&2; exit 1; }
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then echo "release: tag $TAG already exists" >&2; exit 1; fi

agreed="$(python3 scripts/check-versions.py)" || exit 1
[[ "$agreed" == "versions: $V "* ]] || { echo "release: files say a different version than $V" >&2; exit 1; }
grep -qE "^## \[$V\] - [0-9]{4}-[0-9]{2}-[0-9]{2}$" CHANGELOG.md || { echo "release: CHANGELOG.md needs '## [$V] - YYYY-MM-DD'" >&2; exit 1; }

make check

NOTES="$(awk -v v="$V" '/^## \[/{p = ($0 ~ "^## \\[" v "\\]")} p' CHANGELOG.md | sed '1d')"
[[ -n "$(printf '%s' "$NOTES" | tr -d '[:space:]')" ]] || { echo "release: CHANGELOG section for $V is empty" >&2; exit 1; }

git tag -a "$TAG" -m "lean-pkg $V" -m "$NOTES"
git push origin "$TAG"
printf '%s\n' "$NOTES" | gh release create "$TAG" --title "lean-pkg $V" --notes-file - >/dev/null
echo "release: $TAG at $(git rev-parse --short HEAD); $(gh release view "$TAG" --json url -q .url)"
