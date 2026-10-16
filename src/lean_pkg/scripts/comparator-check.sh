#!/usr/bin/env bash
# Canonical copy for the lab (ADR-0058 D2). Vendored 2026-10-05 from the
# 2026-09-03 spike in Article-Pinning-Correlated-L1-Lean (layer 21694d7).
# Tool pins below are the Palomar verifier's; change them only via a commit
# here reviewed against bin/formal-math-watch.py findings.
# Lab additions (2026-10-07, ADR-0058 amendment D8; byte-identical copy ships
# inside lean-pkg as src/lean_pkg/scripts/comparator-check.sh):
#   --configs <glob>     configuration discovery override (repeatable; relative
#                        to the project directory). Default discovery stays
#                        comparator*.json and comparator/config*.json.
#   --lint-configs       validate the configurations only (axiom subset and the
#                        definition-hole sign-off below) and exit 0/1. Needs
#                        only jq: no Lean, Lake, Go, cargo, git or build.
#   LEAN4EXPORT_COMMIT=<40-hex>            use this lean4export source instead
#                                           of the toolchain's release tag.
#   LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1  with LEAN4EXPORT_COMMIT: when that
#       source pins a different Lean, build it under the PROJECT toolchain in a
#       dedicated checkout and verify the built binary reports the project's
#       Lean version. Needed for point releases lean4export never tagged:
#       verified on v4.34.1 with LEAN4EXPORT_COMMIT=076e8e57707e813375e8f9da8bf989799ace9680
#       (the v4.34.0 tag), which builds and reports version 4.34.1 (verified by
#       hand on a lab host, openai/math family-072 replay, 2026-10-07; the
#       script path itself is not yet exercised end to end). The deviation is printed on
#       its own "== lean4export toolchain override:" line in every log.
#   LEAN_PATH shadowing (2026-10-16): before the comparator runs under `lake env`,
#   lake_shadow_check refuses a LEAN_PATH entry other than the project's own
#   .lake/build/lib/lean that holds one of the project's root namespaces (Lake
#   lists the root package last; Lean loads <R>/ from the first entry holding
#   it, so a dependency's copy would be exported and judged instead).
#   Definition holes need a human sign-off. leanprover/comparator compares a
#   name listed in definition_names by name, universes, type and safety only
#   (Comparator/Compare.lean), never by body, and its README says such holes
#   must be checked by an additional, potentially human, verifier. A config
#   with non-empty definition_names is therefore REFUSED unless it carries
#       "judged_as": {"reviewer": "<human name>", "date": "YYYY-MM-DD",
#                     "definitions": [<every definition_names entry>],
#                     "basis": "<how the bodies were compared, optional>"}
#   where reviewer must not be an agent/model token (claude, codex, gpt, opus,
#   sonnet, fable, agent, bot, opencode, assistant, llm, ai). This records the
#   human verifier; it is a declaration, not a verification. comparator's
#   Config derives Lean.FromJson, which ignores unknown fields, so judged_as
#   may stay in the file; the checked copy strips it anyway.
# Palomar acceptance bar for one Lake project.
#
# leanprover/comparator -- with a lean4export matching the project's toolchain, the independent nanoda
# kernel, and the landrun sandbox -- must accept every comparator configuration the project declares,
# i.e. print "Your solution is okay!": each Solution theorem has exactly the statement of its trusted
# Challenge namesake, uses only the standard axioms, and replays in both kernels.
#
# Run from a project directory (the one holding lakefile.toml / lean-toolchain), after `lake exe cache get`:
#     cd <project> && bash ../.github/scripts/comparator-check.sh
# Needs elan (lean, lake), git, go, cargo and jq; Linux for landrun.
#
# Configurations checked, relative to the project directory:
#     comparator*.json            (Palomar template layout: comparator.json next to the lakefile)
#     comparator/config*.json     (a comparator/ directory of trusted statement files with per-topic configs)
#
# Tool pins default to those of the Palomar registry's verifier
# (https://github.com/PalomarRegistry/PalomarSubmission, .github/workflows/submission.yml); lean4export is
# taken at the release tag named after the project's Lean version unless LEAN4EXPORT_COMMIT is set.
set -euo pipefail

COMPARATOR_COMMIT="${COMPARATOR_COMMIT:-575674928e239f5bc452aab72d1dd7b0f1326494}"
NANODA_COMMIT="${NANODA_COMMIT:-68d5ca9db226849b41a6fff59d796ff19d0a8840}"
LANDRUN_COMMIT="${LANDRUN_COMMIT:-811cfff51ceaf3d9843708aa6d22e9b84ccac8b4}"
TOOLS_DIR="${TOOLS_DIR:-$HOME/.cache/lean-ci-tools}"
LOG_DIR="${LOG_DIR:-$PWD/.lake/comparator-logs}"
ALLOW_MISMATCH="${LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH:-0}"
STANDARD_AXIOMS='["propext","Quot.sound","Classical.choice"]'
# Reviewer tokens that name a model or an agent, never a human (whole word or -/_ token, case-insensitive).
REVIEWER_DENY='^(claude|codex|gpt|opus|sonnet|fable|agent|bot|opencode|assistant|llm|ai)$'

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
usage: comparator-check.sh [--configs <glob>]... [--lint-configs] [-h|--help]

  --configs <glob>   configuration files to check, relative to the project directory
                     (repeatable; replaces the default comparator*.json / comparator/config*.json)
  --lint-configs     only validate the configurations (axiom subset, definition-hole sign-off); no build
  -h, --help         this text

env: LEAN4EXPORT_COMMIT, LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1, COMPARATOR_COMMIT, NANODA_COMMIT,
     LANDRUN_COMMIT, TOOLS_DIR, LOG_DIR (see the header comment of this script)
USAGE
}

lint_mode=0
config_globs=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --lint-configs) lint_mode=1; shift ;;
    --configs) [ $# -ge 2 ] || die "--configs needs a glob argument"; config_globs+=("$2"); shift 2 ;;
    --configs=*) config_globs+=("${1#--configs=}"); shift ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

command -v jq >/dev/null 2>&1 || die "jq is required"

# lint_config <cfg>: the configuration-level rules (axiom subset; definition holes need a human
# sign-off). Prints "error: ..." to stderr and returns 1 on the first failing clause.
lint_config() {
  local cfg="$1" msg
  jq -e 'type == "object"' "$cfg" >/dev/null 2>&1 \
    || { echo "error: $cfg: not a JSON object" >&2; return 1; }
  if ! jq -e --argjson std "$STANDARD_AXIOMS" '((.permitted_axioms // []) - $std) == []' "$cfg" >/dev/null; then
    echo "error: $cfg: permitted_axioms must be a subset of propext, Quot.sound, Classical.choice" >&2
    return 1
  fi
  msg="$(jq -r --arg deny "$REVIEWER_DENY" '
    def holes: (.definition_names // []);
    if (holes | length) == 0 then empty
    elif (.judged_as | type) != "object" then
      "definition_names is non-empty (\(holes | length) hole(s)) but there is no judged_as sign-off object"
    elif ((.judged_as.reviewer | type) != "string")
         or ((.judged_as.reviewer | gsub("^\\s+|\\s+$"; "") | length) == 0) then
      "judged_as.reviewer must be a non-empty human name"
    elif ([.judged_as.reviewer | ascii_downcase | splits("[^a-z0-9]+") | select(length > 0)]
          | any(test($deny))) then
      "judged_as.reviewer \(.judged_as.reviewer | tojson) names an agent/model token, not a human reviewer"
    elif ((.judged_as.date | type) != "string")
         or ((.judged_as.date | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$")) | not) then
      "judged_as.date must be an ISO date YYYY-MM-DD"
    elif (.judged_as.definitions | type) != "array" then
      "judged_as.definitions must be an array covering every definition_names entry"
    elif ((holes - .judged_as.definitions) | length) > 0 then
      "judged_as.definitions does not cover definition hole(s): \((holes - .judged_as.definitions) | join(", "))"
    else empty end' "$cfg")"
  if [ -n "$msg" ]; then
    echo "error: $cfg: $msg (comparator checks definition holes by name and type only, never by body; a human must sign off in judged_as)" >&2
    return 1
  fi
  return 0
}

# hole_summary <cfg>: one line naming the signed-off holes, empty when the config has none.
hole_summary() {
  jq -r 'if ((.definition_names // []) | length) == 0 then empty
         else "definition holes: \((.definition_names | length)) signed off by \(.judged_as.reviewer) on \(.judged_as.date)" end' "$1"
}

shopt -s nullglob
if [ "${#config_globs[@]}" -gt 0 ]; then
  configs=()
  for g in "${config_globs[@]}"; do
    for f in $g; do configs+=("$f"); done
  done
else
  configs=(comparator*.json comparator/config*.json)
fi
shopt -u nullglob
[ "${#configs[@]}" -gt 0 ] || die "no comparator configuration found (comparator*.json, comparator/config*.json, or --configs <glob>)"

if [ "$lint_mode" = 1 ]; then
  failures=0
  for cfg in "${configs[@]}"; do
    if lint_config "$cfg"; then
      echo "LINT-OK $cfg$(s=$(hole_summary "$cfg"); [ -n "$s" ] && echo " ($s)")"
    else
      failures=$((failures + 1))
    fi
  done
  [ "$failures" -eq 0 ] || die "$failures configuration(s) refused"
  echo "All ${#configs[@]} configuration(s) lint clean."
  exit 0
fi

[ -f lean-toolchain ] || die "run this from a project directory containing lean-toolchain"
for tool in git go cargo jq lean lake; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done

toolchain="$(tr -d '[:space:]' < lean-toolchain)"
tag="v${toolchain#*:v}"
lean4export_release="release $tag"
if [ -z "${LEAN4EXPORT_COMMIT:-}" ]; then
  refs="$(git ls-remote https://github.com/leanprover/lean4export "refs/tags/$tag" "refs/tags/$tag^{}")"
  LEAN4EXPORT_COMMIT="$(awk -v t="refs/tags/$tag^{}" '$2==t {print $1}' <<<"$refs")"
  [ -n "$LEAN4EXPORT_COMMIT" ] || LEAN4EXPORT_COMMIT="$(awk -v t="refs/tags/$tag" '$2==t {print $1}' <<<"$refs")"
  [ -n "$LEAN4EXPORT_COMMIT" ] || die "leanprover/lean4export has no release $tag for toolchain $toolchain. Point-release gap: set LEAN4EXPORT_COMMIT=<commit of the nearest lower release tag> and LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1 to build that source under $toolchain (the built binary's reported Lean version is verified); e.g. for v4.34.1 use LEAN4EXPORT_COMMIT=076e8e57707e813375e8f9da8bf989799ace9680 (the v4.34.0 tag)"
else
  lean4export_release="commit override"
fi

echo "project $(pwd), toolchain $toolchain, configurations: ${configs[*]}"

mkdir -p "$TOOLS_DIR/bin" "$LOG_DIR"

checkout_at() {  # <url> <dir> <commit>
  if [ ! -d "$2/.git" ]; then
    git clone --quiet --filter=blob:none "$1" "$2"
  fi
  if [ "$(git -C "$2" rev-parse HEAD)" != "$3" ]; then
    git -C "$2" fetch --quiet --depth 1 origin "$3"
    git -C "$2" checkout --quiet --detach "$3"
  fi
}

echo "== comparator @ $COMPARATOR_COMMIT"
checkout_at https://github.com/leanprover/comparator.git "$TOOLS_DIR/comparator" "$COMPARATOR_COMMIT"
(cd "$TOOLS_DIR/comparator" && lake build comparator)
comparator_bin="$TOOLS_DIR/comparator/.lake/build/bin/comparator"

lean4export_dir="$TOOLS_DIR/lean4export"
checkout_at https://github.com/leanprover/lean4export.git "$lean4export_dir" "$LEAN4EXPORT_COMMIT"
export_toolchain="$(tr -d '[:space:]' < "$lean4export_dir/lean-toolchain")"
override_note=""
if [ "$export_toolchain" != "$toolchain" ]; then
  if [ "$ALLOW_MISMATCH" = 1 ]; then
    # Dedicated checkout so the shared one stays pristine; its lean-toolchain is rewritten to the
    # project's and the built binary must report the project's Lean version (checked below).
    lean4export_dir="$TOOLS_DIR/lean4export-override-${toolchain#*:v}"
    checkout_at https://github.com/leanprover/lean4export.git "$lean4export_dir" "$LEAN4EXPORT_COMMIT"
    printf '%s\n' "$toolchain" > "$lean4export_dir/lean-toolchain"
    override_note="source pinned $export_toolchain, built under $toolchain"
    lean4export_release="commit override, $override_note"
  else
    die "lean4export $LEAN4EXPORT_COMMIT targets $export_toolchain but the project uses $toolchain; set LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1 (with LEAN4EXPORT_COMMIT) to build it under the project toolchain; the built binary's reported Lean version is then verified"
  fi
fi
echo "== lean4export @ $LEAN4EXPORT_COMMIT ($lean4export_release)"
[ -z "$override_note" ] || echo "== lean4export toolchain override: commit $LEAN4EXPORT_COMMIT $override_note"
(cd "$lean4export_dir" && lake build lean4export)
lean4export_bin="$lean4export_dir/.lake/build/bin/lean4export"
if [ -n "$override_note" ]; then
  want="${toolchain#*:v}"
  got="$("$lean4export_bin" --help 2>&1 | head -n 1 | jq -r '.meta.lean.version // empty' 2>/dev/null || true)"
  [ "$got" = "$want" ] \
    || die "lean4export built under $toolchain reports Lean version '${got:-?}', expected '$want'; the override is not usable for this toolchain"
  echo "== lean4export toolchain override verified: binary reports Lean $got"
fi

echo "== nanoda_lib @ $NANODA_COMMIT"
checkout_at https://github.com/robsimmons/nanoda_lib.git "$TOOLS_DIR/nanoda" "$NANODA_COMMIT"
cargo build --release --locked --quiet --manifest-path "$TOOLS_DIR/nanoda/Cargo.toml"
nanoda_bin="$TOOLS_DIR/nanoda/target/release/nanoda_bin"

echo "== landrun @ $LANDRUN_COMMIT"
landrun_bin="$TOOLS_DIR/bin/landrun"
if [ ! -x "$landrun_bin" ] || [ "$(cat "$landrun_bin.commit" 2>/dev/null)" != "$LANDRUN_COMMIT" ]; then
  GOBIN="$TOOLS_DIR/bin" CGO_ENABLED=0 go install "github.com/zouuup/landrun/cmd/landrun@$LANDRUN_COMMIT"
  echo "$LANDRUN_COMMIT" > "$landrun_bin.commit"
fi

for bin in "$comparator_bin" "$lean4export_bin" "$nanoda_bin" "$landrun_bin"; do
  [ -x "$bin" ] || die "missing tool $bin"
done

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
lake_shadow_check "$lean_path" \
  || die "a LEAN_PATH entry shadows this project's own modules (lean-path-shadow lines above); a dependency's copy would be judged instead; remove it and rebuild"

# Comparator runs `lake build <module>` and lean4export inside landrun sandboxes that may execute only the
# toolchain directory, so resolve lake/lean to the toolchain's own binaries rather than the elan shims.
lean_prefix="$(lean --print-prefix)"
export PATH="$lean_prefix/bin:$PATH"
export COMPARATOR_LANDRUN="$landrun_bin"
export COMPARATOR_LEAN4EXPORT="$lean4export_bin"
export COMPARATOR_NANODA="$nanoda_bin"
export LEAN_ABORT_ON_PANIC=1

failures=0
for cfg in "${configs[@]}"; do
  name="$(tr '/' '_' <<<"${cfg%.json}")"
  log="$LOG_DIR/$name.log"
  # As in the registry: only the three standard axioms may be permitted, and the nanoda re-check is always
  # on (comparator is handed a copy of the configuration with enable_nanoda forced to true). Definition
  # holes must carry a human judged_as sign-off (lab rule, ADR-0058 D8); the sign-off itself is not
  # handed to comparator.
  lint_config "$cfg" || exit 1
  summary="$(hole_summary "$cfg")"
  [ -z "$summary" ] || echo "$cfg: $summary"
  checked_cfg="$LOG_DIR/$name.json"
  jq '.enable_nanoda = true | del(.judged_as)' "$cfg" > "$checked_cfg"

  echo "== comparator $cfg  (log: $log)"
  set +e
  lake env "$comparator_bin" "$checked_cfg" 2>&1 | tee "$log"
  status="${PIPESTATUS[0]}"
  set -e
  if [ "$status" -eq 0 ] && grep -qxF 'Your solution is okay!' "$log"; then
    echo "PASS $cfg"
  else
    echo "::error::comparator did not accept $cfg (exit status $status)"
    failures=$((failures + 1))
  fi
done

[ "$failures" -eq 0 ] || die "$failures comparator configuration(s) not accepted"
echo "All ${#configs[@]} comparator configuration(s) accepted."
