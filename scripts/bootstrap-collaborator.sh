#!/usr/bin/env bash
# Set up lean-pkg and one package for a collaborator: step 0 of
# docs/existing-package-comparator.md.
#
#   bash scripts/bootstrap-collaborator.sh OWNER/PACKAGE
#
# Checks the machine and GitHub access, installs lean-pkg with pipx, clones this
# repository and the package under ~/src, creates a personal registry in
# ~/.lean-pkg/registry (git-tracked, committed), registers the package there,
# installs the agent skills for Codex, and runs `lean-pkg doctor`. Idempotent.
# Never pushes, gates, promotes or builds. Ends with READY (exit 0) or
# NOT READY (exit 1).
#
# Overrides: LEAN_PKG_SRC (checkout directory, must be under $HOME; default
# ~/src), LEAN_PKG_REGISTRY_DIR (default ~/.lean-pkg/registry),
# LEAN_PKG_GIT_BASE (clone URL base; default https://github.com).
set -uo pipefail

[ $# -eq 1 ] && [[ "$1" == */* ]] || { echo "usage: bash scripts/bootstrap-collaborator.sh OWNER/PACKAGE" >&2; exit 2; }
PKG_SLUG="$1"
PKG="${PKG_SLUG##*/}"
SRC="${LEAN_PKG_SRC:-$HOME/src}"
REG_DIR="${LEAN_PKG_REGISTRY_DIR:-$HOME/.lean-pkg/registry}"
REG="$REG_DIR/lean-packages.yaml"
GIT_BASE="${LEAN_PKG_GIT_BASE:-https://github.com}"
TOOL_REPO="lean-pkg/lean-pkg"
MIN_FREE_GB=20

fail=0; warn=0
ok()   { printf '  [ok]   %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }
note() { printf '  [warn] %s\n' "$*"; warn=$((warn+1)); }
have() { command -v "$1" >/dev/null 2>&1; }
finish() {
  echo
  if [ "$fail" -eq 0 ]; then echo "READY ($warn warning(s))"; exit 0; fi
  echo "NOT READY ($fail failure(s))"; exit 1
}

case "$SRC" in "$HOME"/*) ;; *) echo "LEAN_PKG_SRC must be under \$HOME (registry paths are home-relative)" >&2; exit 2 ;; esac

echo "== 1. machine and tools"
[ "$(uname -s)" = Linux ] && ok "Linux" || note "not Linux: setup works, comparator-receipt (guide step 7) will not"
for t in git gh python3 pipx elan; do
  have "$t" && ok "$t" || bad "$t not on PATH"
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null && ok "python3 >= 3.11" || bad "python3 older than 3.11"
for t in go cargo jq; do
  have "$t" && ok "$t" || note "$t not on PATH: needed later by comparator-receipt (guide step 7)"
done
if have bwrap || have landrun; then ok "sandbox: bwrap or landrun"; else note "neither bwrap nor landrun: comparator-receipt needs one (landrun is built on first use)"; fi
for t in lake lean; do
  p="$(command -v "$t" 2>/dev/null || true)"
  case "$p" in "" | "$HOME/.elan/bin/"*) ;; *) note "$t resolves to $p, not ~/.elan/bin: put ~/.elan/bin first on PATH so lean-toolchain is honoured" ;; esac
done
free_gb=$(df -Pk "$HOME" | awk 'NR == 2 {print int($4 / 1048576)}')
[ "${free_gb:-0}" -ge "$MIN_FREE_GB" ] && ok "disk: $free_gb GB free" \
  || note "disk: ${free_gb:-?} GB free; budget about $MIN_FREE_GB GB for the toolchain, the Mathlib cache and the build"
[ "$fail" -eq 0 ] || { echo "Install the missing tools, then run this again."; finish; }

echo "== 2. GitHub access (a failure here is a human step)"
gh auth status >/dev/null 2>&1 && ok "gh is logged in" || { bad "gh is not logged in: run 'gh auth login'"; finish; }
gh auth setup-git >/dev/null 2>&1 && ok "git uses gh's credentials for https" || note "gh auth setup-git failed"
for r in "$TOOL_REPO" "$PKG_SLUG"; do
  gh api "repos/$r" --silent >/dev/null 2>&1 && ok "access to $r" \
    || bad "no access to $r: accept the invitation (https://github.com/$r/invitations) or ask the owner for access"
done
[ "$fail" -eq 0 ] || finish

echo "== 3. lean-pkg"
if have lean-pkg; then
  pipx upgrade lean-pkg >/dev/null 2>&1 || true
else
  pipx install "git+$GIT_BASE/$TOOL_REPO.git" >/dev/null 2>&1 || { bad "pipx install failed"; finish; }
  pipx ensurepath >/dev/null 2>&1 || true
  export PATH="${PIPX_BIN_DIR:-$HOME/.local/bin}:$PATH"
fi
have lean-pkg && ok "$(lean-pkg --version)" || { bad "lean-pkg not on PATH; open a new shell and run this again"; finish; }

echo "== 4. checkouts under $SRC"
mkdir -p "$SRC"
clone_or_update() {  # OWNER/REPO DIR
  if [ -d "$2/.git" ]; then
    if [ -n "$(git -C "$2" status --porcelain)" ]; then note "$2 has local changes; left as is"
    elif git -C "$2" pull -q --ff-only; then ok "$2 up to date at $(git -C "$2" rev-parse --short HEAD)"
    else note "$2: git pull --ff-only failed; left as is"; fi
  elif git clone -q "$GIT_BASE/$1.git" "$2"; then ok "cloned $1 to $2"
  else bad "clone of $1 failed"; fi
}
clone_or_update "$TOOL_REPO" "$SRC/lean-pkg"
clone_or_update "$PKG_SLUG" "$SRC/$PKG"
[ "$fail" -eq 0 ] || finish

echo "== 5. personal registry $REG (yours; the owner keeps their own)"
if [ ! -f "$REG" ]; then
  mkdir -p "$REG_DIR"
  [ -d "$REG_DIR/.git" ] || git -C "$REG_DIR" init -q
  lean-pkg --registry "$REG" init >/dev/null
  git -C "$REG_DIR" add lean-packages.yaml lean-package-events.jsonl
  git -C "$REG_DIR" commit -qm "registry: initialize lean-pkg"
  ok "registry created and committed"
else
  ok "registry exists"
fi
export LEAN_PKG_REGISTRY="$REG"
if lean-pkg status "$PKG" >/dev/null 2>&1; then
  ok "$PKG already registered"
elif lean-pkg register "$PKG" --repo "git@github.com:$PKG_SLUG.git" --path "${SRC#"$HOME"/}/$PKG" >/dev/null; then
  git -C "$REG_DIR" add -A && git -C "$REG_DIR" commit -qm "registry: register $PKG"
  ok "$PKG registered and committed"
else
  bad "lean-pkg register $PKG failed"
fi
lean-pkg validate >/dev/null 2>&1 && ok "registry validates" || bad "lean-pkg validate failed"

echo "== 6. agent skills for Codex"
if python3 "$SRC/lean-pkg/scripts/install_skills.py" --agent codex >/dev/null 2>&1; then
  ok "skills installed (or already present) in ~/.agents/skills"
else
  note "skill conflicts; see: python3 $SRC/lean-pkg/scripts/install_skills.py --agent codex --dry-run"
fi

echo "== 7. lean-pkg doctor"
lean-pkg doctor >/dev/null 2>&1 && ok "doctor exit 0" || bad "lean-pkg doctor failed; run it to see why"

echo
echo "Package checkout: $SRC/$PKG"
echo "Add this to your shell startup file:"
echo "  export LEAN_PKG_REGISTRY=$REG"
echo "Next (guide step 2): cd $SRC/$PKG && lake exe cache get && lake build"
finish
