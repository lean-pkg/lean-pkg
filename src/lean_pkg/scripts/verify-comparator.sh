#!/usr/bin/env bash
# Vendored from https://github.com/PalomarRegistry/PalomarTemplate (Apache-2.0),
# scripts/verify-comparator.sh at commit 2891de4c48955af824969a263d31b25e7a9a1406, 2026-10-05.
# Local changes (lean-pkg): (1) the config path is taken from $1 (default
# comparator.json); (2) the script runs in the current directory (the runner
# sets cwd to the project) instead of cd'ing to its own parent, because it is
# shipped as package data, not inside the project; (3) `lake exe cache get` is
# skipped when VERIFY_COMPARATOR_SKIP_CACHE_GET=1 (the runner sets it for a project
# declared to have no Mathlib, where Lake has no `cache` executable), and the
# line "== lake comparator (toolchain) <config>" is printed right before `lake
# comparator`, so a failure before it is reported as a tool failure, not as a
# rejected proof; (4) before `lake comparator`, lake_shadow_check refuses a
# LEAN_PATH entry other than the project's own .lake/build/lib/lean that holds
# one of the project's root namespaces (Lake lists the root package last and
# Lean loads <R>/ from the first entry holding it; lean_pkg/lake_hygiene.py);
# (5) a file-wide `# shellcheck disable=SC2016` below: the shared
# lake_shadow_check block's single-quoted backticks are literal (Lean name
# literals in roots/globs), and a line-level directive would have to go into
# every copy of that block.
# Everything else is upstream.
# shellcheck disable=SC2016
set -euo pipefail

# Judge Solution against Challenge the way Palomar does: with the `lake
# comparator` that ships in this project's own toolchain, replaying the proof
# through Lean's kernel and the toolchain's bundled independent kernels
# (NanoDa and con-ron). Nothing is built from a pin; everything that judges
# comes from `lean-toolchain`, which Palomar requires to be v4.35.0-rc2 or
# later.

config_path="${1:-comparator.json}"

for required_command in bwrap lake lean python3; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "error: $required_command is required to run lake comparator" >&2
    exit 1
  fi
done

toolchain=$(tr -d '[:space:]' < lean-toolchain)
prefix=$(lean --print-prefix)
for tool in lake leanexport leanchecker nanoda_bin con-ron; do
  if [ ! -x "$prefix/bin/$tool" ]; then
    echo "error: toolchain $toolchain does not bundle $tool" >&2
    echo "Palomar requires leanprover/lean4:v4.35.0-rc2 or later" >&2
    exit 1
  fi
done

# Palomar ignores `enable_nanoda` and rejects `external_kernels` in a submitted
# comparator.json: it registers the toolchain's bundled kernels itself. This
# generated copy does the same, so the local check judges as the registry does.
config=$(mktemp "${TMPDIR:-/tmp}/palomar-comparator.XXXXXX")
trap 'rm -f "$config"' EXIT
python3 - "$config_path" "$config" "$prefix" <<'PY'
import json
import pathlib
import sys

source, destination, prefix = sys.argv[1:]
try:
    config = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as error:
    print(f"error: cannot read valid Comparator config {source}: {error}", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(config, dict):
    print(f"error: {source} must contain one JSON object", file=sys.stderr)
    raise SystemExit(1)
if "external_kernels" in config:
    print(f"error: {source}: external_kernels is not a submitter field; Palomar rejects it", file=sys.stderr)
    raise SystemExit(1)
config.pop("enable_nanoda", None)
config["external_kernels"] = {
    "nanoda": [f"{prefix}/bin/nanoda_bin"],
    "con-ron": [f"{prefix}/bin/con-ron"],
}
pathlib.Path(destination).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

if [ "${VERIFY_COMPARATOR_SKIP_CACHE_GET:-}" = 1 ]; then
  echo "skipping lake exe cache get (VERIFY_COMPARATOR_SKIP_CACHE_GET=1: no Mathlib dependency)"
else
  lake exe cache get
fi
# >>> lean-pkg lake_shadow_check (keep identical in every copy; see lean_pkg/lake_hygiene.py)
lake_root_namespaces() {
  local found
  found=$(
    {
      if [ -f lakefile.toml ]; then
        awk '/^[[:space:]]*\[/ { lib = ($0 ~ /^[[:space:]]*\[\[[[:space:]]*lean_lib[[:space:]]*\]\]/) }
             lib && /^[[:space:]]*(name|roots|globs)[[:space:]]*=/' lakefile.toml | grep -oE '"[^"]+"' | tr -d '"'
      fi
      if [ -f lakefile.lean ]; then
        grep -oE 'lean_lib[[:space:]]+(«[^»]+»|[A-Za-z_][A-Za-z0-9_.]*)' lakefile.lean | awk '{print $2}'
        tr '\n' ' ' < lakefile.lean | grep -oE '(roots|globs)[[:space:]]*:=[[:space:]]*#\[[^]]*\]' \
          | grep -oE '`[^]`[:space:],)]+' | tr -d '`'
      fi
    } 2>/dev/null | sed -e 's/^«\([^»]*\)».*$/\1/' -e 's/[.].*$//' | grep -E '^[A-Za-z_]' | sort -u || true
  )
  if [ -z "$found" ]; then
    found=$(find . -path ./.lake -prune -o -name '*.lean' ! -path ./lakefile.lean -print 2>/dev/null \
      | sed -e 's|^\./||' -e 's|/.*$||' -e 's|\.lean$||' | grep -E '^[A-Za-z_]' | sort -u || true)
  fi
  printf '%s\n' "$found"
}
lake_shadow_check() {
  local own entry r n=0 hits=0 roots
  local -a entries
  own="$(pwd -P)/.lake/build/lib/lean"
  roots=$(lake_root_namespaces)
  IFS=: read -r -a entries <<<"${1:-}"
  for entry in "${entries[@]}"; do
    [ -n "$entry" ] || continue
    n=$((n + 1))
    if [ "$(cd "$entry" 2>/dev/null && pwd -P)" = "$own" ]; then continue; fi
    for r in $roots; do
      if [ -d "$entry/$r" ] || [ -e "$entry/$r.olean" ] || [ -e "$entry/$r.ilean" ]; then
        echo "lean-path-shadow: $entry/$r"
        hits=$((hits + 1))
      fi
    done
  done
  if [ "$hits" -gt 0 ]; then return 1; fi
  echo "lean-path-shadow: none ($n entries, roots $(printf '%s' "$roots" | paste -sd, -))"
}
# <<< lean-pkg lake_shadow_check
lean_path="$(lake env printenv LEAN_PATH)"
if ! lake_shadow_check "$lean_path"; then
  echo "error: a LEAN_PATH entry shadows this project's own modules (lean-path-shadow lines above);" \
    "a dependency's copy would be judged instead; remove it and rebuild" >&2
  exit 1
fi
echo "== lake comparator (toolchain) $config_path"
lake comparator --config "$config"
