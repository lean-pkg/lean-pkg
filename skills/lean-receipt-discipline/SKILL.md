---
name: lean-receipt-discipline
description: "Turn a Lean verification run into evidence a stranger can re-derive: freeze the candidate commit first, bind the receipt to full hashes and pins, accept only terminal results, interpret a leaf build as evidence for its import closure only, commit receipts as evidence-only descendants, and choose the right replay layer (lake build, #print axioms, leanchecker, comparator with an independent kernel). Trigger phrases: verification receipt, bind receipt to commit, receipt commit equals HEAD, evidence-only descendant, leaf build vs full build, job still running counted as pass, pristine rebuild, cache replay, leanchecker, lean4checker, independent kernel, nanoda, external replay."
license: Apache-2.0
---

# Receipt discipline

## Problem

A verification claim is only as good as what binds it to bytes. Typical
failures: a receipt names an abbreviated hash; a running job is reported as a
pass; a leaf build is described as verifying the whole commit; the receipt is
committed first, which moves HEAD and breaks the binding; a cache replay is
called a pristine build; a green verdict is carried by prose from one commit
to another.

## Why it happens

Five things are easy to conflate: the source commit, the build that ran, the
transcript it produced, the receipt that summarizes it, and the prose that
explains it. Each can be right while the links between them are wrong.

## Rule

1. **Freeze, then verify.** Commit the candidate. Verify that commit, in a
   clean checkout. Resolve it with `git rev-parse` to 40 lowercase hex.
2. **Bind everything.** The receipt records the full commit, the
   `lean-toolchain` content, the full Mathlib revision, the checker command,
   a job identity, a timestamp, the axiom result, and the SHA-256 of a frozen
   report that itself hashes the sources and the transcript.
3. **Terminal only.** Queued, running, or "active" is not green. Record the
   exit status of the exact job.
4. **Scope honestly.** A targeted build covers exactly the import closure of
   its target. Modules in the same commit that it does not import are not
   verified by it; a dependency that fails first means "closure failed", not
   "target failed".
5. **Evidence-only descendants.** Commit the receipt after gating, in a child
   commit that changes only evidence paths. A tool that checks "receipt
   commit equals HEAD" should accept exactly that relation and nothing more.
6. **No verdict transfer.** A result belongs to one commit. After a merge or
   a base move, re-run on the new commit.
7. **Keep failures.** Failed attempts and their repaired successors are both
   evidence; do not rewrite history to hide the first.

## How to apply

```bash
set -euo pipefail
C=$(git rev-parse HEAD); test -z "$(git status --porcelain)"
lake exe cache get
# a fresh build: Lake's artifact cache restores a module (`Reused`) without its sorry warning
rm -rf .lake/build
export LAKE_CACHE_DIR="$(mktemp -d)" LAKE_ARTIFACT_CACHE=false   # never LAKE_CACHE_DIR="" (not disabled)
lake build 2>&1 | tee build.log
! grep -E '\] (Reused|Replayed|Unpacked) MyPkg' build.log
! grep -rnE '\bsorry\b|\badmit\b' --include='*.lean' MyPkg/
# shadow preflight: no LEAN_PATH entry but ./.lake/build/lib/lean may hold MyPkg/ or MyPkg.olean
lake env printenv LEAN_PATH | tr : '\n' | grep -vxF "$PWD/.lake/build/lib/lean" | while read -r d; do
  if [ -e "$d/MyPkg" ] || [ -e "$d/MyPkg.olean" ]; then echo "shadowed by $d" >&2; exit 1; fi
done
lake lean AxiomCheck.lean > axioms.txt   # `lake lean`, not `lake env lean` (see below)
python3 audit.py axioms.txt
lake env leanchecker MyPkg       # optional layer: silent on success, exit 0; reads LEAN_PATH (preflight above)
sha256sum build.log axioms.txt > report.sha256
```

Two Lake behaviours undermine a transcript (checked in the Lake and Lean
sources of v4.35.0-rc2). Lake's local artifact cache is read unless switched
off, and a package lakefile's `enableArtifactCache = true` overrides
`LAKE_ARTIFACT_CACHE=false`, so the build gets an empty cache directory of
its own; `--no-cache` only governs downloads. And Lake lists the root
package's lib directory last on `LEAN_PATH` while Lean loads `<R>/` from the
first entry holding it: under `lake env lean FILE` a dependency's build
directory holding the package's namespace is loaded instead (its `#print
axioms` is then about someone else's declaration). `lake build` and `lake
lean FILE` pass explicit module paths and are not affected. `lean-pkg
receipt slurm --template` does all of this, and `receipt slurm` refuses a
log showing a root-package module `Reused`, `Replayed` or `Unpacked`.

Replay layers, in increasing strength (see the Lean reference page
"Validating a Lean Proof"):

| Layer | Catches | Does not catch |
|---|---|---|
| `lake build` | elaboration and kernel errors | axioms, `sorry` hidden in dependencies, wrong statement |
| `#print axioms` (exact inventory) | `sorryAx`, user axioms | bugs in the elaborator's handling of kernel state |
| `leanchecker` | replays stored declarations from `.olean` files through the kernel; ships with the toolchain | a crafted malformed `.olean`; statement mismatch |
| comparator + independent kernel (nanoda) | statement drift from a trusted Challenge, replay in a second kernel | the trusted statement itself being the wrong mathematics |

`leanchecker --fresh` re-checks transitive imports too; with a Mathlib slice
that can need far more memory than the build. After two out-of-memory kills
of the same replay on a shared machine, stop and move it to a bigger host.

With lean-pkg, the lower tier is the gate receipt and the top layer is
produced by `lean-pkg receipt comparator`; see `lean-pkg-agent`.

## Anti-patterns

- Abbreviated hashes in receipts.
- Feeding a combined log that contains the negative control's `sorryAx` to the
  receipt generator.
- Calling a warm-cache rerun "independent" or "pristine".
- A completion percentage built from declaration or file counts.

## Verification

From a fresh clone at the receipt's commit, a stranger can rerun the recorded
command and reproduce the transcript hash, and `git diff --name-only <commit>
HEAD` lists only evidence paths.

Learned from: several verification campaigns where a running job was briefly
reported as a pass, a receipt-first commit broke the HEAD binding, and a
non-fail-fast shell pushed a receipt after a failed merge, September to
October 2026.
