#!/bin/bash
# post-create.sh: run once when the tutorial container is created (devcontainer.json, postCreateCommand).
# Scaffolds the tutorial package with `lean-pkg new`, pins it to Lean and Mathlib v4.35.0, and downloads
# Mathlib's prebuilt files with `lake exe cache get`, so that nothing of Mathlib is compiled. It reads and
# writes no registry, commits nothing and writes nothing outside ~/src/pi-lambda. Re-running it is safe.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
toolchain=${TUTORIAL_LEAN_TOOLCHAIN:-leanprover/lean4:v4.35.0}
mathlib_rev=${TUTORIAL_MATHLIB_REV:-v4.35.0}
pkg="$HOME/src/pi-lambda"
start=$(date +%s)

# A local dev-container tool may give vscode the host user's uid (updateRemoteUserUID); the image's
# directories then still belong to the old one.
for d in /opt/elan /opt/pipx /var/lib/lean-pkg-trial; do
  [ -O "$d" ] || sudo chown -R "$(id -u):$(id -g)" "$d"
done

author=$(git config --global user.name || true)
author=${author:-A. Mathematician}
if [ ! -e "$pkg/lakefile.toml" ]; then
  mkdir -p "$HOME/src"
  (cd "$HOME/src" && lean-pkg new pi-lambda --kind library --author "$author")
fi
cd "$pkg"
# A Codespace carries the GitHub user's git identity; a local dev container may not. The fallback is set in
# this repository only.
git config user.name >/dev/null || git config user.name "$author"
git config user.email >/dev/null || git config user.email "tutorial@example.com"

# `lean-pkg new` 0.12.0 pins v4.35.0-rc2; the container's stack is v4.35.0 (lts-2026.10).
echo "$toolchain" >lean-toolchain
sed -i -E "s/^rev = \"[^\"]*\"/rev = \"$mathlib_rev\"/" lakefile.toml
lake update
lake exe cache get
lake build PiLambda.Solution PiLambda.Axioms

cat <<MSG

The tutorial package is ready: $pkg ($(( $(date +%s) - start )) s; Mathlib $mathlib_rev, prebuilt).
Open it:     code $pkg
Tutorial:    docs/tutorial-pi-lambda.md (step 2 is done; continue at step 3)
The finished files of steps 3 and 4: $here/pi-lambda/ (cp -r $here/pi-lambda/. $pkg/)
Registry:    LEAN_PKG_REGISTRY=$LEAN_PKG_REGISTRY (a throwaway one, inside this container)
MSG
