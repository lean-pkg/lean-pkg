#!/usr/bin/env bash
# Run the proposal 0003 prototype: build, print axioms, run `lake comparator` on every configuration,
# then show that a proof checked at another pin of the statement module does not transfer.
# Builds a copy in a new temporary directory (under $TMPDIR) and deletes it; writes nothing here.
# Needs elan with leanprover/lean4:v4.35.0-rc2 (the first toolchain with `lake comparator`) and a
# working bwrap. Usage: bash run.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
tc=$(cat "$here/lean-toolchain")
work=$(mktemp -d "${TMPDIR:-/tmp}/discharge-prototype.XXXXXX")
trap 'rm -rf "$work"' EXIT
cp -r "$here/." "$work/"
rm -rf "$work/.lake" "$work/other-pin/.lake"
cd "$work" || exit 2
export ELAN_TOOLCHAIN="$tc" LAKE_ARTIFACT_CACHE=false LAKE_CACHE_DIR="$work/lake-cache"
prefix=$(lean --print-prefix)
redact() { sed -e "s#$work#<workdir>#g" -e "s#$HOME#~#g"; }

echo "== toolchain: $(lean --version)"
echo "== lake build (default targets)"
lake build 2>&1 | redact | tail -n 3
echo "== #print axioms"
lake env lean Axioms.lean 2>&1 | redact

for cfg in configs/*.json; do
  run="$work/run-$(basename "$cfg")"
  python3 - "$cfg" "$run" "$prefix" <<'PY'
import json, sys
src, dst, prefix = sys.argv[1:]
c = json.load(open(src))
c["external_kernels"] = {"nanoda": [prefix + "/bin/nanoda_bin"], "con-ron": [prefix + "/bin/con-ron"]}
json.dump(c, open(dst, "w"), indent=2)
PY
  echo "== lake comparator --config $cfg"
  out=$(lake comparator --config "$run" 2>&1); rc=$?
  printf '%s\n' "$out" | redact | grep -E "kernel accepts|Your solution is okay|Illegal axiom|do not match|don't match|does not match|not a theorem|error: .*\.lean:[0-9]+:[0-9]+|kernel rejected" | head -n 6
  echo "   exit $rc"
done

echo "== HypCheck on the Challenge (the trusted statements): hypotheses, and the discharge rule"
lake env lean --run HypCheck.lean Challenge Audit.Paper.main Audit.Paper.main_of_B Hyp.A Audit.Lib.proofA; echo "   exit $?"
lake env lean --run HypCheck.lean Challenge Audit.Paper.main Audit.Paper.main_of_B Hyp.B; echo "   exit $?"
lake build Bad.AxiomHypothesis 2>&1 | redact | tail -n 1
lake env lean --run HypCheck.lean Bad.AxiomHypothesis Bad.useQ; echo "   exit $?"

echo "== the discharging package at another pin (Hyp.A := True there)"
(cd other-pin && lake build 2>&1 | redact | tail -n 1)
echo "== the same ProofA.lean in this build, at this pin"
cp other-pin/ProofA.lean ProofA.lean
lake build Discharge 2>&1 | redact | grep -E "^error" | head -n 4
