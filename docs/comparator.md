---
title: The comparator tier
---

[&larr; lean-pkg documentation](index.md)

*Check the theorem you claimed, not just the one that compiled.*

## The layout

The project declares, next to its `lean-toolchain`, a trusted statement
module, an untrusted proof module, and one or more configurations:

```
Challenge.lean      trusted: Mathlib-only statements, each proof replaced by `sorry`
Solution.lean       untrusted: the same names and statements, with proofs
comparator.json     the configuration (or several: comparator*.json, comparator/config*.json)
```

```json
{
  "challenge_module": "Challenge",
  "solution_module": "Solution",
  "theorem_names": ["MyProof.two_mul_le"],
  "definition_names": [],
  "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
  "enable_nanoda": true
}
```

This is the [Palomar submission layout](https://github.com/PalomarRegistry/PalomarSubmission).
Keeping it means a package that passes here is also in the shape Palomar
accepts.

## Writing the Challenge and the Solution

The comparator proves one thing: each `Solution` theorem has exactly the type
of its `Challenge` namesake and is proved from the permitted axioms. Whether
the `Challenge` says what the paper says is a human judgement. Write it for a
reader who trusts Mathlib and nothing else.

**Challenge** (trusted, read by people):

- `import Mathlib` and nothing from your project. Every object in a statement
  is written in Mathlib's vocabulary: unfold your project's definitions into
  Mathlib terms, or quantify over any object with the Mathlib property that
  characterizes it.
- Put the theorems in a namespace of their own, for example
  `<Package>Audit.<Claim>`. The Solution imports your project, so a Challenge
  theorem whose full name is already a project declaration (say
  `MyPackage.cos_sq`) cannot be restated there.
- Each proof is `sorry`. Keep `definition_names` empty; a non-empty list
  needs a named human sign-off (next section).
- A docstring above each theorem says in words what it claims and which
  result of the paper it is.

**Solution** (untrusted, checked by machine):

- Imports Mathlib and your project, and any other dependency the proofs need
  (a non-Mathlib library may appear here, never in the Challenge).
- Restates each Challenge theorem with the same name and the same type, and
  proves it by bridging to your project's theorem: small lemmas showing that
  the Mathlib-vocabulary objects of the Challenge equal your project's
  definitions, then a rewrite and an application of the project result.
  Helper lemmas go in a namespace of their own so they cannot collide with a
  Challenge name.
- A project result that is a `def` (data) rather than a `theorem` gets a
  theorem stating the property you actually certify; list that theorem.

**Configuration**: `theorem_names` lists the Challenge names, fully
qualified. One configuration per claim, or per small group of claims, keeps
failures readable. `definition_names` may be omitted: a missing (or `null`)
list field means the empty list, the runner records it as `[]` and
`lean-pkg comparator NAME --receipt R --check` reads the committed
configuration the same way. `permitted_axioms` must be written out (`[]`
for none); the runner refuses a configuration without it before anything
runs. Check the configurations alone, with no build, from the
project directory, using the script in a clone of this repository:

```bash
bash ~/src/lean-pkg/src/lean_pkg/scripts/comparator-check.sh --lint-configs
# LINT-OK comparator.json
# All 1 configuration(s) lint clean.
```

### Scaffolding entries from claims.yaml

A package with many claims and few hand-written entries can list the rest
in one step, once `claims.yaml` is committed and synced
(`lean-pkg claims sync NAME`):

```bash
lean-pkg comparator config --from-claims NAME --dry-run     # print the would-be JSON
lean-pkg comparator config --from-claims NAME               # extend comparator.json
```

For every claim of kind `theorem` or `lemma` (not `superseded`) that no
tracked configuration covers yet, it appends a Challenge name
`<Package>Audit.<decl>` to `theorem_names` (`--challenge-namespace NS`
changes the prefix) and records, under `lean_pkg_scaffold.todo`, the claim,
the declaration, its file and two `TODO:` lines: the Challenge statement a
human must write in Mathlib vocabulary, and the Solution bridge to the
project theorem. A claim counts as covered only by its full declaration
name: a configured name is its `decl`, or is `<Prefix>.<decl>` (the form
this command writes), and only when `decl` is the fully qualified name of
a declaration its `file` declares (namespaces resolved). A name that merely
shares the last component (`A.C.thm` for `A.B.thm`, or a hand-written
`<Package>Audit.thm`) covers nothing, and a bare or partial `decl` is
covered by no name; the claims ledger and `--coverage` use the same rule.
Existing keys and entries are never removed, renamed or reordered, and a
second run adds nothing. It prints the counts (already configured, added, skipped:
definitions, constructions, libraries and superseded claims). `--out`
names another configuration (`comparator*.json` or
`comparator/config*.json`; created with the standard fields when absent).
Each configuration's receipt log is named by its path without `.json`, `/`
becoming `_`, so `comparator_config_a.json` and `comparator/config_a.json`
would share `comparator_config_a.log`: `comparator-receipt` refuses such a
pair (exit 1, nothing written, both files named) and `--out` refuses to
create one.
It refuses when `claims.yaml` is not tracked at HEAD or the claims are not
synced at HEAD, and a live lease held by another agent refuses the write
(`--agent`). The registry is never written.

**Without `claims sync`.** `claims sync` writes the registry, so a worker
without registry rights cannot run it. `--claims-file PATH` reads the claims
from a working-copy `claims.yaml` instead of the synced cache, in every mode
(`--challenge-from-type`, `--emit-script`, `--from-output`, with or without
`--checkout` and `--no-registry`). The file is checked exactly like
`claims check --file PATH NAME` (schema, and its `package` must be NAME; any
error refuses, nothing written) and its claims are normalized the way
`claims sync` caches them, so the configuration is the one the registry
route produces for the same claims. Neither the tracked `claims.yaml` nor
the synced commit is consulted, and nothing is written to the registry or
the event log. The output says which source was used: a line
`claims source: registry cache (synced at <c12>)` or
`claims source: --claims-file PATH (sha256 ...; ...)`, and JSON
`claims_source` {kind `registry`|`file`, path, sha256, synced_commit,
claims}. Declarations are not looked up in this mode (as with
`claims check --file`); a missing one shows up as a `type_error` under a
types flag, and `gate` checks them at the receipt's commit. `--emit-script`
prints the ingestion command with the same `--claims-file`.

```bash
lean-pkg comparator config --from-claims beta --claims-file claims.yaml --challenge-from-type --dry-run
```

Until each Challenge and Solution exists, comparator fails on the TODO
names, which is the point: nothing is certified by scaffolding. Remove an
entry from `lean_pkg_scaffold.todo` once both are written, and the key once
the list is empty; comparator ignores unknown keys, but do not submit a
configuration that still carries the key to Palomar. Names still listed
there are left out of `status.main_results` in `formalization.yaml`.

### Pre-filling statements from the elaborated types

`--challenge-from-type` fills each entry the scaffold adds with a starting
point instead of two `TODO:` lines: the Challenge statement is the
declaration's type as Lean pretty-prints it on the build host, and the
Solution restates it and closes it with `exact @_root_.<decl>`:

```json
{
  "theorem_name": "BetaAudit.Demo.wow",
  "claim": "beta/wow", "decl": "Demo.wow", "file": "Demo/Main.lean",
  "type": "∀ {α : Type u} [inst : Inhabited α] (s : Demo.Thing α), s = s",
  "needs_restatement": true,
  "restatement_reason": "the type uses project definitions (Demo.Main)",
  "challenge": "-- TODO: restate in Mathlib vocabulary: ...\n/-- wow summary -/\ntheorem BetaAudit.Demo.wow.{u} :\n    ... := by\n  sorry",
  "solution": "theorem BetaAudit.Demo.wow.{u} :\n    ... := by\n  exact @_root_.Demo.wow"
}
```

`needs_restatement: true` (with a `TODO:` comment on the Challenge) marks
the entries a person must still restate in Mathlib vocabulary: the type
mentions a constant from a module outside `Init`, `Std`, `Lean`, `Mathlib`
and Mathlib's own dependencies, or the printed type would not re-elaborate
(an inaccessible `✝` name or an elided `⋯` proof). An entry whose type is in
Mathlib vocabulary and passed the round-trip check below carries
`needs_restatement: false` and a Challenge that elaborates; it still needs
the paper check below. A printed type that fails the round-trip check keeps
the `TODO:` lines, with `needs_restatement: true`,
`restatement_reason: "round_trip_failed"`, the check's error in `type_error`
and the printed text in `printed_type` (a starting point, not a statement). A
declaration Lean reports as unknown (or a claim whose `file` maps to no
module) keeps its `TODO:` lines, gets `needs_restatement: true`, and records
`type_error: <first line of the error>`. A declaration whose extraction
failed (`lake env lean` exited non-zero, for instance a module not built, or
timed out) fails the whole run instead; see "Exit codes and partial runs"
below. Every entry stays under
`lean_pkg_scaffold.todo` until its Challenge and Solution are written into the
modules.

How a type is obtained: for each declaration, a scratch file in a temp
directory imports the claim's module (from its `file`) and `Lean`, and runs

```lean
set_option pp.fullNames true
set_option pp.proofs true
set_option pp.analyze true
open Lean Elab Command in
#eval show CommandElabM Unit from do
  let env ← getEnv
  let n : Name := ((Name.anonymous).str "Demo").str "wow"
  match env.find? n with
  | none => IO.println s!"LEANPKG-ERROR unknown constant {n}"
  | some ci =>
    let fmt ← liftTermElabM (Meta.ppExpr ci.type)
    let mods := ci.type.getUsedConstants.toList.filterMap fun c =>
      (env.getModuleIdxFor? c).map fun i => (env.header.moduleNames[i.toNat]!).toString
    IO.println s!"LEANPKG-LEVELS {String.intercalate "," (ci.levelParams.map toString)}"
    IO.println s!"LEANPKG-MODULES {String.intercalate "," mods.eraseDups}"
    IO.println "LEANPKG-TYPE-BEGIN"
    IO.println (fmt.pretty 100)
    IO.println "LEANPKG-TYPE-END"
```

with `lake env lean <file>` from the checkout (one process per declaration,
`--jobs` at a time, default 2 or the CPUs available to the driver if fewer;
`--timeout` seconds each, default 120). It
prints the declared universe names (so the Challenge is
`theorem X.{u, v} : ...` with the same names), the modules of every
constant in the type, and the type with all binders, implicit and instance
ones included. `pp.analyze` adds the annotations a pretty-printed term
needs to elaborate back to the same term (for instance
`Eq.{u + 1} (α := α) Inhabited.default Inhabited.default`), and
`pp.proofs true` prints proofs inside types rather than `⋯`.

The pretty-printer does not guarantee a round trip, even with these options:
on a real package it dropped coercion targets (`↑n + 1` for an integer cast),
the domain of an integral, and explicit `Matrix` types inside existentials,
so the printed text elaborated to another statement or not at all. So the
driver checks every printed type `T` in a second `lake env lean` run, in the
same module context:

```lean
import Demo.Main
set_option autoImplicit false
-- LEANPKG-ROUNDTRIP Demo.wow
universe u_1 v
example :
    (T) :=
  @_root_.Demo.wow
```

which passes only when `T` elaborates (no auto-bound names) and the
declaration inhabits it, i.e. `T` is definitionally equal to the declaration's
type. Only an exit status of 0 makes the result `ok` (`round_trip: "ok"`); a
non-zero one makes it `status: "error"`, `failure: "round_trip"`,
`round_trip: "failed"`, with `round_trip_rc` and the error, and
`summary.round_trip_failed` counts them (they are not extraction failures and
do not change the exit status). The check runs with the declaration's own
module imported, so it does not prove the statement is in Mathlib
vocabulary; the module test above does that. Output from a driver without
the check (no `round_trip` field) is pre-filled but always
`needs_restatement`. Other limits: binder names of
non-dependent hypotheses are dropped (`h : P` becomes `P →`, the same type);
notation from scoped namespaces is not opened, so it prints as the
underlying constants; the claim's `file` must map to a module under the
Lake directory (`Demo/Main.lean` → `Demo.Main`), and a library with a custom
`srcDir` gives a `type_error`. Build the package first (`lake build`): an
unbuilt module is a `type_error`, not a build.

It refuses when the Lake directory (`--lake-env DIR`, a directory inside the
checkout; default the checkout) has no `lakefile.lean` or `lakefile.toml`,
and when `lake` (or a `lean` on `PATH`) is not elan's. Nothing is written
into the checkout except the configuration; the scratch files live in a temp
directory.

**Three modes.** Elaborating a large package is heavy, so the step splits
into two halves that run on different hosts, with the one-shot mode built
from the same two halves:

```bash
# 1. on the workstation: write the driver, run nothing
lean-pkg comparator config --from-claims beta --emit-script /scratch/beta-types.sh --jobs 4
# 2. on the cluster, with the checkout at the same commit
sbatch --time=04:00:00 --cpus-per-task=4 --mem=64G \
  --wrap 'cd ~/lean/beta && lake build && bash /scratch/beta-types.sh /scratch/beta-types.json'
# 3. back on the workstation: fill the scaffold (refused unless the JSON's commit is HEAD)
lean-pkg comparator config --from-claims beta --from-output /scratch/beta-types.json --dry-run
lean-pkg comparator config --from-claims beta --from-output /scratch/beta-types.json
# or, on a host that may run Lean: all three in one
lean-pkg comparator config --from-claims beta --challenge-from-type
```

`--emit-script F` writes a self-contained `bash` + `python3` driver to `F`
(outside the checkout, or under its `.lake/`) and lists the declarations; it
embeds the package, the commit, the declarations and their modules, and the
Lean snippet. Run it as `cd <checkout> && bash F OUT.json`; it refuses
(exit 2) without a lakefile, with a non-elan `lake`, or when HEAD is not the
embedded commit, and `LEAN_PKG_TYPES_JOBS` / `LEAN_PKG_TYPES_TIMEOUT` override
the embedded `--jobs` / `--timeout`. Without `--jobs` the driver picks the default on the host it
runs on. It defaults `LEAN_NUM_THREADS` to 1 and keeps an explicit value (see
"On shared cluster nodes" below). It writes `OUT.json`:

```json
{"schema": "comparator-types/1", "package": "beta", "commit": "<40-hex HEAD>",
 "toolchain": "leanprover/lean4:v4.35.0",
 "summary": {"ok": false, "total": 2, "filled": 1, "errors": 1, "round_trip_failed": 0,
             "declaration_errors": 0, "jobs": 2,
             "backoff": []},
 "results": [{"decl": "Demo.baz", "type": "∀ (n : Nat), n + 0 = n", "status": "ok", "error": null,
              "levels": [], "modules": ["Init.Prelude"], "rc": 0, "failure": null,
              "round_trip": "ok", "round_trip_rc": 0},
             {"decl": "Demo.zap", "type": null, "status": "error",
              "error": "unknown module prefix 'Demo'", "levels": [], "modules": [],
              "rc": 1, "failure": "extraction", "output_tail": "...last lines of lake env lean..."}]}
```

`levels`, `modules`, `summary`, `rc`, `failure`, `output_tail`, `round_trip`
and `round_trip_rc` are additive; a result without `modules` is marked `needs_restatement`. `--from-output F` refuses (nothing written) a file whose
`schema` is not `comparator-types/1`, whose `package` is another package, or
whose `commit` is not the checkout's HEAD; a declaration with no result in the
file falls back to the TODO lines with a `type_error`. `--challenge-from-type`
emits the driver to a temp directory, runs it from the checkout and ingests
its output exactly as `--from-output` would. `--dry-run` writes no
configuration in any mode (and no driver with `--emit-script`), and a second
run adds nothing, so Lean is not run again for claims already scaffolded.

**Exit codes and partial runs.** Each result records the exit status of its
`lake env lean` process (`rc`) and, when it failed, why (`failure`):
`declaration` when Lean ran to the end and reported the declaration unknown
(or the claim names no module), `extraction` when it did not (non-zero exit,
timeout, the process could not start). `summary.errors` counts extraction
failures, `summary.declaration_errors` the others. The driver exits 0 when no
extraction failed, 1 when some did, and 2 when some did and nothing was
filled (2 is also its refusal status, which writes no `OUT.json`); the output
file is written in all three cases. `--from-output` and
`--challenge-from-type` refuse (exit 1, nothing written) to ingest a run with
extraction failures, naming the counts; a declaration with no result in the
file counts as one. `--allow-partial` ingests it anyway: the filled entries
are pre-filled as usual, and the failed ones keep their `TODO:` lines with
`needs_restatement: true`, `restatement_reason: "extraction_failed"` and the
`type_error`. A later run skips names already scaffolded, so to retry them
remove those entries from `theorem_names` and `lean_pkg_scaffold.todo` first.
Output written by an older driver has no `failure`: there a non-zero `rc`, or
an error other than an unknown constant, counts as an extraction failure.

**Another checkout, or a package someone else holds.** `--checkout DIR`
uses that checkout instead of the registry's `local_path` (for instance a
clone on the host that ran the driver), in every mode. It refuses unless DIR
is a git checkout whose HEAD is the commit the claims were synced at, or an
evidence-only descendant of it, naming both commits and the paths that
differ, and (with a types flag) unless the Lake directory has a lakefile.
The configuration is written into DIR. `--no-registry --out FILE` is a
scratch mode for `--from-output` or `--challenge-from-type`: the checkout's
`comparator.json` plus the new entries is written to FILE (outside the
checkout, or under its `.lake/`; overwritten if present), the package lease
is not consulted (so it works while another agent holds the package; `--agent`
is refused), and the output lists what was not done: the lease check, the
checkout's configuration, and any registry, evidence or receipt write. The
registry is still read for the synced claims. Copy FILE into the checkout
under the lease to use it.

```bash
lean-pkg comparator config --from-claims beta --from-output /scratch/beta-types.json \
  --checkout ~/clones/beta --no-registry --out /scratch/beta-comparator.json --allow-partial
```

**Jobs and file handles.** Every `lake env lean` process opens the `.olean`
files of its whole import closure, so a few parallel processes over Mathlib
can exhaust the node's file table, especially on Lustre or NFS ("Too many
open files in system", ENFILE, or the per-process EMFILE). Keep `--jobs` (or
`LEAN_PKG_TYPES_JOBS`) small; more workers rarely help, since each process
spends most of its time loading the same files. When a declaration fails that
way (errno 23/24 starting the process, or "too many open files" in its
output; its `failure` is `resource`), the driver halves the workers and
retries only those declarations, at least two rounds and down to one worker,
pausing `LEAN_PKG_TYPES_BACKOFF_SECONDS` (default 5) times the round number
before each. `summary.backoff` lists the rounds
(`{"round", "jobs", "retried", "recovered"}`); what still fails counts as an
extraction failure.

**On shared cluster nodes.** The file table is per node, not per job: on a
Slurm cluster whose nodes share a filesystem, Lake and Lean processes from
several jobs placed on one node can still exhaust it together (ENFILE), even
though each driver caps and backs off its own workers. The answer is fewer
threads and workers, not a bigger allocation: set `LEAN_NUM_THREADS=1` (or
another small value) in the Slurm script, keep `LEAN_PKG_TYPES_JOBS` (or
`--jobs`) at 2 or less, and log `/proc/sys/fs/file-nr` so a failure shows
the node's table. Do not request a whole node (`--exclusive`) and do not
`--exclude` the node: size CPUs and memory from a pilot's `sacct`
(`TotalCPU / Elapsed`, `MaxRSS`) instead, as the
[lean-shared-cluster-verification](../skills/lean-shared-cluster-verification/SKILL.md)
skill describes. The emitted driver honours both settings: it reads
`LEAN_PKG_TYPES_JOBS`, and near its top it runs
`export LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"`, so its Lean processes use
one thread unless the job script sets another value, which is kept. (The
one-shot `--challenge-from-type` run on a workstation leaves
`LEAN_NUM_THREADS` as it finds it.)

```bash
#SBATCH --cpus-per-task=4          # from a pilot's sacct, not a whole node
#SBATCH --mem=16G                  # likewise
export LEAN_NUM_THREADS=1 LEAN_PKG_TYPES_JOBS=2
cat /proc/sys/fs/file-nr
cd ~/lean/beta && lake build && bash /scratch/beta-types.sh /scratch/beta-types.json
cat /proc/sys/fs/file-nr
```

The smallest complete pair, from a smoke test:

```lean
-- Challenge.lean
import Mathlib

theorem two_mul_le (n : ℕ) : n ≤ 2 * n := by sorry
```

```lean
-- Solution.lean
import Mathlib

theorem two_mul_le (n : ℕ) : n ≤ 2 * n := by omega
```

A realistic Challenge states, for example, that Brownian motion stopped at a
truncated exit time has mean equal to its start point, with the law and the
stopping time written in Mathlib terms (`ProbabilityTheory.IsBrownianReal`,
`MeasureTheory.hittingBtwn`); its Solution proves two bridge lemmas (Mathlib's
hitting time equals the library's exit time, and any such law equals the
library's Brownian motion) and then applies the library theorem.

### Do not weaken the statement

The comparator cannot catch a Challenge that is weaker than the paper. Before
a Challenge is committed, a person checks each theorem against the source:

1. Every hypothesis of the paper's statement is present, and no hypothesis is
   added that the paper does not have.
2. Quantifiers, parameter ranges and constants match: nothing is specialized
   to a case the paper does not restrict to.
3. The conclusion is the paper's conclusion, not a consequence of it or a
   special case.
4. A conditional result keeps its assumptions in the theorem's type; they are
   never dropped to make the statement provable.
5. The statement is not vacuous: its hypotheses can be satisfied, and the
   conclusion is not trivially true (for instance an identity that holds for
   any input).
6. Write the paper's label, the quoted statement from the source and the
   Challenge theorem side by side, and have a second reader sign each row.

The [lean-claim-ledger](../skills/lean-claim-ledger/SKILL.md),
[lean-fidelity-audit-taxonomy](../skills/lean-fidelity-audit-taxonomy/SKILL.md)
and [lean-weakened-hypothesis-vacuity](../skills/lean-weakened-hypothesis-vacuity/SKILL.md)
skills describe this audit in detail.

### Challenge types mention only trusted constants

The comparator checks that the Solution proves the Challenge's statements,
but a statement is only as trustworthy as the constants its type mentions.
The type of every Challenge theorem must refer only to trusted constants:
Mathlib and Lean core, plus the Challenge's own declarations, each of which a
reviewer reads and accepts as part of the statement. Two ways a type quietly
picks up something else:

- **Lifted proofs.** Lean 4.35 lifts an inline proof inside a type, such as a
  `Fin` bound `⟨0, by omega⟩`, into an auxiliary constant `<theorem>._proof_N`
  owned by the Challenge module. The statement then depends on that constant,
  not on a Mathlib lemma; `set_option backward.proofsInPublic` did not remove
  them in a field report. Write the bound as a literal Mathlib proof term
  instead, for example `⟨0, Nat.succ_pos n⟩`, or
  `(Int.toNat_lt' hN).mpr (Int.emod_lt_of_pos j (Int.natCast_pos.mpr hN))`
  for an `Int.toNat (j % N) < N.toNat` index.
- **Project proofs in a type.** A theorem about `MemLp.toLp f h` embeds the
  project's proof `h : MemLp f p μ` in its type. State it with an existential
  witness instead (`∃ h : MemLp f p μ, ‖MemLp.toLp f h‖ = ...`, all original
  hypotheses kept), and prove, in the Solution or a reviewed lemma, an
  explicit two-way `↔` between that statement and the literal original
  proposition; proof irrelevance makes the two agree for any witness. Never
  supply the witness with `sorry` or a definition hole.

To list what each Challenge theorem's type uses, elaborate this file with
`lake lean` (tested on Lean v4.35.0-rc2; it only reads the environment):

```lean
import Lean
import MyProof.Challenge        -- the challenge_module
open Lean

/-- Module roots treated as trusted. -/
def trustedRoots : List Name := [`Init, `Std, `Lean, `Mathlib, `Batteries, `Aesop, `Qq, `Plausible]

/-- Every constant a theorem's TYPE mentions whose module is not under a trusted root. -/
def untrustedInType (thm : Name) : MetaM (List (Name × Name)) := do
  let env ← getEnv
  let some ci := env.find? thm | throwError "unknown constant {thm}"
  let mut out := []
  for c in ci.type.getUsedConstants do
    let mod := match env.getModuleIdxFor? c with
      | some i => env.header.moduleNames[i.toNat]!
      | none => env.mainModule
    unless trustedRoots.any (·.isPrefixOf mod) do
      out := (c, mod) :: out
  return out.reverse

#eval show MetaM Unit from do
  for thm in [`MyProof.main_theorem] do      -- the configuration's theorem_names
    IO.println s!"{thm}: {← untrustedInType thm}"
```

On a toy Challenge (module `Audit.Challenge`, importing a library `Toy`)
whose `lifted` states `myVec n ⟨0, by omega⟩ = 0`, whose `literal` states
`myVec n ⟨0, Nat.succ_pos n⟩ = 0` and whose `usesProject` mentions `Toy.x`, it
prints:

```text
lifted: [(myVec, Audit.Challenge), (lifted._proof_1, Audit.Challenge)]
literal: [(myVec, Audit.Challenge)]
usesProject: [(Toy.x, Toy)]
```

Each line names a constant and its module. A Challenge declaration
(`myVec` above) is expected and must be reviewed; a `._proof_N` constant or a
constant from the project's own library (`Toy.x`) widens what the reader has
to trust, and is rewritten with one of the recipes above.

### Coverage at the gate-bound commit

`lean-pkg comparator config --coverage NAME` is read-only. It writes nothing
and takes no lock. It answers one question: which claims does the comparator
configuration tracked at the gate-bound commit (the commit of the recorded gate
receipt) cover? It reads the claims from `claims.yaml` at that commit, or from
the registry cache when the file is not tracked there. It counts theorem and
lemma claims as covered, not covered, or listed only as a scaffold TODO; it
counts definitions and superseded claims as not compared; and it names the
uncovered ones. It then compares the configurations in the working tree and at
HEAD with those at the bound commit. When one changed (modified, uncommitted,
untracked, committed after the gate, or removed), it names the change and
prints the candidate's counts separately, marked as certifying nothing. The
headline counts always come from the bound commit, and a candidate config is
never substituted for it.

The reason: a comparator receipt binds the configurations tracked at its
commit (the validator requires exactly those, compared byte for byte). So a
changed or uncommitted configuration cannot certify the bound tree. A new
gate at a commit that tracks the configuration is required. The command
prints the exact next steps: commit the configuration, re-gate
(`update --status gate-pending` first when the package is
verified-private), then `comparator-receipt --prepare && comparator-receipt`
and `lean-pkg comparator NAME --receipt ...`. When nothing is uncovered or
changed, it prints `next: nothing to do`. `--json`: `bound_commit`, `head`, `claims_source`,
`configs`, `theorem_claims`, `covered`, `not_covered`, `todo`,
`not_applicable`, `uncovered[]`, `config_changed`, `changed_configs[]`,
`candidate` (`certifies: false`), `statement`, `next_commands[]`.

When you plan the comparator slots for a module from its source, count by
the kernel's classification, not by source keywords. A declaration written
`instance` whose type is a proposition is stored as a theorem
(`ConstantInfo.thmInfo`), and so is any other Prop-valued declaration that
Lean elaborates as a theorem. Such declarations are theorem obligations, and a
keyword tally of `theorem`/`lemma` undercounts them (field report: a topology
module had 12 `theorem`s and 5 Prop-valued `instance`s, 17 theorem constants).
List the theorem constants a module defines from a scratch file, built with
`lake lean List.lean` (checked on Lean 4.35.0-rc2):

```lean
import Lean
import MyPkg.Topology
open Lean in
#eval show CoreM Unit from do
  let env ← getEnv
  let some idx := env.getModuleIdx? `MyPkg.Topology | throwError "module not loaded"
  for n in env.header.moduleData[idx.toNat]!.constNames do
    if let some ci := env.find? n then
      if ci.isTheorem then IO.println n
```

The list also holds theorems Lean generates, such as the field projections of
a Prop-valued class (`MyPkg.P.h`); decide per name whether a generated
constant is an obligation the comparator should state.

## What `comparator-check.sh` does

The script is vendored unchanged from the Palomar verifier workflow. For
each configuration it:

1. checks out and builds the pinned tools: `leanprover/comparator`, a
   `lean4export` matching the project's Lean release tag, the `nanoda`
   kernel, and the `landrun` sandbox (cached under `~/.cache/lean-ci-tools`);
2. requires `permitted_axioms ⊆ {propext, Quot.sound, Classical.choice}`
   and forces `enable_nanoda = true`;
3. runs `comparator`, which builds `Challenge` and `Solution`, exports both,
   checks statement equality for every listed name, replays the solution in
   `nanoda` and in the default Lean kernel;
4. prints `Your solution is okay!` and `PASS <config>` on success.

All configurations must pass; the script exits non-zero otherwise.

## Definition holes need a human sign-off

`definition_names` lists challenge definitions whose body the solution may
supply. comparator checks such a hole by name, universes, type and safety only
(`Comparator/Compare.lean`), never by body, and its README says hole solutions
must be checked by an additional, potentially human, verifier. So a
configuration with a non-empty `definition_names` is refused — by
`comparator-check.sh`, by `comparator-receipt` before anything is built, and by
`gate --comparator`, `comparator` and `promote` against the file at the bound
commit — unless it records that verifier:

```json
{
  "definition_names": ["MyProof.witness"],
  "judged_as": {
    "reviewer": "Le Chen",
    "date": "2026-10-07",
    "definitions": ["MyProof.witness"],
    "basis": "solution body compared with the challenge statement side by side"
  }
}
```

`reviewer` must be a non-empty human name; a value containing a model or agent
token (`claude`, `codex`, `gpt`, `opus`, `sonnet`, `fable`, `agent`, `bot`,
`opencode`, `assistant`, `llm`, `ai`, as a whole word or `-`/`_` token, any
case) is refused. `date` is `YYYY-MM-DD`. `definitions` must cover every
`definition_names` entry. `basis` is free text. This is a declaration, not a
verification: the tool records who signed, it cannot compare the bodies.
comparator's `Config` derives `Lean.FromJson`, which ignores unknown fields, so
`judged_as` may stay in the file; `comparator-check.sh` strips it from the copy
it hands to comparator anyway. The runner copies `judged_as` into the receipt's
config entry when present.

Background: OpenAI's 2026-10-06 mathematics release lists 42 concrete challenge
definitions as holes across 9 of its 405 configurations, one of them a whole
`MainStatement : Prop`, with no reviewer named.

## `comparator-check.sh` options and overrides

- `--configs <glob>` (repeatable, relative to the project): check these files
  instead of the default `comparator*.json` / `comparator/config*.json`.
- `--lint-configs`: only the configuration rules above (axiom subset,
  definition-hole sign-off); needs just `jq`, no toolchain or build.
- `LEAN4EXPORT_COMMIT=<40-hex>`: use this lean4export source instead of the
  release tag named after the project's Lean version.
- `LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1` (with `LEAN4EXPORT_COMMIT`): when
  that source pins a different Lean, build it under the project toolchain in a
  dedicated checkout and verify the binary reports the project's Lean version.
  For a point release lean4export never tagged, e.g. Lean v4.34.1 (tags jump
  v4.34.0 → v4.35.0-rc1), use the nearest lower tag's commit:
  `LEAN4EXPORT_COMMIT=076e8e57707e813375e8f9da8bf989799ace9680` (v4.34.0)
  built under v4.34.1 reports version 4.34.1. The deviation is printed on a
  `== lean4export toolchain override:` line in every log, next to the usual
  `== lean4export @ <commit>` pin line the runner parses.

## What `comparator-receipt` adds

The runner wraps the script so that evidence is either complete or absent:

- refuses to run on a dirty tree, naming the files, and refuses when there
  is no configuration;
- runs in stages, each with a durable log (see "Stages and `--prepare`" below);
- runs the script with its per-configuration logs in a temporary directory;
- parses the tool pins from the script's own output;
- requires every configuration's log to contain the PASS line;
- only then copies the logs and writes
  `.lean-receipts/<commit12>-comparator.json` into the repository, all or
  nothing (a late write failure removes what was staged);
- records `host`, `job_id` (the Slurm job id, or `local-<host>-<pid>-<stamp>`),
  `host_policy`, and `generated_at`.

## Stages and `--prepare`

A run has five stages: `checkout` (`lake env true`, which clones the
dependencies `lake-manifest.json` pins), `cache-get` (`lake exe cache get`,
only when the manifest has a `mathlib` package), `build` (`lake build` of
every configured challenge and solution module), `comparator` (the mechanism's
judge) and `receipt` (the all-or-nothing write). Without a lakefile the three
setup stages are `skipped`. `COMPARATOR_LAKE` names the `lake` they run
(default: `lake` on `PATH`). Each stage writes
`<evidence-dir>/comparator-stages/<NN>-<stage>.log` (output streamed, so a long
step can be watched with `tail -f`), and `stages.json` beside the logs records
the binding (`commit`, `lean_toolchain`, `config_hash`) and, per stage,
`status` (`ok`, `skipped`, `failed`, `prepared`), `exit`, `wall_s`, `note` and
`log`. Both are kept on failure. The error names the failed stage, its log and
`stages.json`.

Two readiness checks run before anything expensive. Before any stage, a run
that may use `lake comparator` (every mechanism but `legacy`, and every
`--prepare`) refuses when `.lake`, `.lake/packages` or an entry in it is a
symlink resolving outside the project directory: the `lake comparator` sandbox
grants only the project, the toolchain and LakeHome, so it would fail resolving
the dependencies (`EEXIST`) after the setup passed. Copy the dependencies inside
the project instead. At the end of `checkout`, once the dependencies are there
to load the workspace, `lake --no-build build M` runs for each configured
module: exit 0 (up to date) or 3 (needs a rebuild) is fine; Lake's
`unknown target` means no `[[lean_lib]]`'s roots or globs cover the module (see
[getting started](getting-started.md)), and the stage fails naming it, before
the Mathlib cache download and the build. The checkout log records each probe
as a `lake-target:` line. Asking Lake rather than reading the lakefile is
deliberate: a `lakefile.lean` is code, and a root is buildable only when its
library's globs match it.

A judge that stops before judging (verify-comparator.sh fails before
`== lake comparator`, or `lake comparator` fails while resolving the workspace
in its sandbox, after `Resolving dependencies` and before `Building`, or exits
2 because it could not start) is reported as "the judge tool failed before
judging ... a tool/environment error, not a verdict on the proof", never as
"did not accept the solution". `--evidence-dir DIR` moves them. The default is
`<project>/.lake/comparator-receipt`, which the runner leaves out of its
dirty-tree check.

The judge in the `comparator` stage streams too. It runs with stdin from
`/dev/null`, its stdout and stderr merged into one pipe that a reader thread
drains line by line into `04-comparator.log` (and the runner's stdout) as the
lines arrive, under `stdbuf -oL -eL` when `stdbuf` exists (Lean's
`IO.println` is fully buffered on a pipe, so without it `lake comparator`'s
own phase lines would appear only at exit) and with `PYTHONUNBUFFERED=1`.
While the judge prints nothing, a `heartbeat:` line every 60 seconds
(`COMPARATOR_HEARTBEAT_SECONDS`) gives its run time, how long it has been
silent and the size of the files in its private `TMPDIR`, where
`lake comparator` writes its exports. The judge runs in its own process group: a
TERM, INT or HUP to the runner (an external `timeout`, Slurm's time limit)
kills the whole judge tree, and the error names the signal.

**Slots and progress.** A slot is one `theorem_names` entry, numbered `1..N`
across the configurations in order; a unit is what one judge invocation
judges. Stage 04 prints, flushed at once, a line per unit:
`slot 3/242 Foo.bar started` or
`slot 1-242/242 comparator.json[242 names] started`, then
`... done in 812.4s (242/242 done)` or `... failed (exit 1) in 9.0s`. Each unit's judge output goes to
`comparator-stages/04-units/<unit-id>.log`, and its private `TMPDIR` is
`04-units/<unit-id>.tmp/`, removed when the unit ends or is killed.
`04-comparator.partial.json` (schema `comparator-partial/1`) names the slots
done, pending and failed, the run's binding and the resume command; it is
rewritten after every unit, so even a SIGKILL leaves it current, and it is
removed when the stage passes. `stages.json` gains
`progress {done, total, failed, partial_report}`. On TERM, INT or HUP the
comparator stage's status is `partial`, its note reads
`partial: a/N done; ... resume: <command>`, and the runner exits 128 plus the
signal number.

**Resume.** Every slot the toolchain judge accepts is cached as
`comparator-stages/04-slots/<slot-hash>.json`, keyed by the commit, the
config hash, `lean-toolchain`, the mechanism, the judge script's sha256, the
configuration path and the slot name; the unit log that accepted it is kept
content-addressed in `04-slots/logs/` and re-checked (digest and PASS line)
before the slot is trusted. A rerun at the same binding skips those slots
(`slot 1-120/242 comparator.json[120 names] cached ...`) and judges the rest
with a partial copy of the configuration (`theorem_names` cut to the pending
slots, everything else equal) in `04-units/<unit-id>.json`. The
configuration's receipt log is then the accepting unit logs, each under an
`== unit: slots ...` header, every one ending in the PASS line; the receipt
adds `resumed_slots` and, per configuration judged in more than one unit,
`units[] {slots, log_sha256}`. The verdict is the same: the comparator's
acceptance of a configuration is the conjunction of its acceptance of each
name, whose export closure does not depend on the other names.
`--no-resume` judges every slot again. The legacy mechanism judges all
configurations in one `comparator-check.sh` run and does not resume.
`--unit-size 1` makes a capped run resumable per name: each name is its own
unit, cached the moment it is accepted, so a killed run loses at most the
name being judged.

**Parallel judges.** `--judges N` (or `LEAN_PKG_COMPARATOR_JOBS`; default 1)
splits each configuration's pending slots into at most N contiguous shards
of near-equal size, each judged by its own `lake comparator` on a partial
copy of the configuration, at most N at a time (judge lines in the stage log
are prefixed `[unit]`). `lake comparator` takes a whole configuration per
invocation, so this is the only way to shard it; the merge is exact for the
reason given under Resume, and every shard's log must carry the PASS line.
A rejected shard rejects the configuration: the other shards are killed,
no receipt is written, and the partial report names the failed slots. The
receipt records `judges` and, per configuration, `units[]`; its
`theorem_names` stay the configuration's. The legacy mechanism refuses
`--judges` above 1. Shards run in the same checkout: the setup stages have
built everything, so their `lake build` steps are no-ops, but each still
runs `lake exe cache get` (inside `verify-comparator.sh`). Whether sharding
saves time at all depends on how much of their export closure the shards
share (see Runtime).

**Unit size.** `--unit-size K` (or `LEAN_PKG_COMPARATOR_UNIT_SIZE`; default:
the whole configuration) cuts each configuration's pending slots into
contiguous units of at most K names, independent of concurrency: `--judges N`
is then only the pool width, how many units run at once (default 1). Without
`--unit-size`, `--judges N` behaves as above (N shards, N at a time). The
runner prints `units: M of at most K names, width N` once at the start of
stage 04, a guide to memory (N judges at once) and time (M invocations, each
paying its own export closure). `--unit-size 1 --judges 1` judges each name
alone, one at a time. A unit covering the whole configuration judges the
configuration file itself, and its log is the receipt log byte for byte;
otherwise the receipt log is assembled from the unit logs as under Resume.
The receipt and `stages.json` record `unit_size` (`K`, or `null`) beside
`judges`. A value that is not a positive integer exits 2 before any work;
the legacy mechanism refuses `--unit-size`.

**A subset of the slots.** `--slots SPEC` judges only the slots SPEC names:
comma-separated slot names, `<config>:<name>` when a name occurs in several
configurations, 1-based indices or ranges `i-j` over all configurations in
order, or `sample:K`, a stratified sample (round-robin over the strata
configuration plus namespace of the name, the closest proxy for the module
that a configuration carries; `--estimate` fills its sample this way after
the heaviest slots). A SPEC that matches nothing is
refused before any setup runs. A subset run writes a PARTIAL receipt,
`<commit12>-comparator.partial.json` with its logs, into the stage
directory, never into the repository; it is marked `partial: true` and lists
`slots` and `slots_total`. It is diagnostic only: `lean-pkg comparator`,
`gate --comparator` and `promote` refuse it as evidence, and the runner says
so. The slots it judged are cached, so the full run that follows skips them.
A SPEC that selects every slot is a full run. The legacy mechanism refuses
`--slots`.

**Estimate first.**
`comparator-receipt --estimate [--sample K [--force-sample]] [--json]`
runs (or reuses) the setup stages, then judges a sample of the slots one slot per judge invocation, one at a time, and prints
the timings, `per_slot_seconds`, `slots`, `estimated_seconds`,
`estimated_seconds_max`, `lower_bound_seconds`, `recommended_cap` with its
`cap_model`, `remaining_estimate_seconds`, `weights_found`, and a
`whole_unit_warning` (also written to
`comparator-stages/04-comparator.estimate.json`, schema
`comparator-estimate/2`). It writes no receipt; the slots it judged are
cached, so the real run skips them.

The schema. `comparator-estimate/1` carried the timings and the mean model.
`/2` added the sample floor (`sample_floor`, `sample_forced`), the
heaviest-first sample (per-row `weight_bytes`, `weight_module`, `heavy`) and
the max cap model (`estimated_seconds_max`, `cap_model`,
`whole_unit_warning`). Later fields are additive and keep `/2`:
`weights_found`, `olean_modules`, `weighted_slots`, `unsampled_slots`,
`remaining_per_slot_seconds`, `remaining_estimate_seconds` and
`remaining_estimate_model`. A reader of `/2` ignores fields it does not know. The estimate reports time per slot, not
memory: it says nothing about whether the build or the judges fit in the
host's RAM.

The sample. Its size has a floor, `max(5, ceil(0.25 * slots))` capped at the
slot count, recorded as `sample_floor`; the default `--sample` is that
floor, and a larger `--sample K` is taken as given. A `--sample` (or an
explicit `--slots` subset) below the floor is refused (exit 2, nothing
written) unless `--force-sample` is given; then the runner warns and the
estimate records `sample_forced: true`. The sample first takes the
`max(1, ceil(0.2 * K))` heaviest slots, then fills the rest with the
namespace-stratified round-robin of `sample:K` over the remaining slots;
both steps are deterministic. A slot's weight is the size in bytes of the
olean of its module, under the package's own `.lake/build/lib/lean/` (where
Lake writes them; older Lake's `.lake/build/lib/` is read too), built by the
setup stages or unpacked from a prebuilt tarball. The module is the one whose
source declares the name, found by a syntactic scan of the package's `.lean`
files (any `srcDir`, hidden directories such as `.lake` skipped; it follows
`namespace`, `section`, `mutual` and `end`), so a theorem whose namespace is
not its module path is weighed too; failing that, the module named by the
longest proper namespace prefix of the slot's name: `Pkg.Big.thm` tries
`Pkg/Big.olean`, then `Pkg.olean`. A name neither finds weighs 0 and is
never picked as heavy. When no slot has a weight, the estimate records
`weights_found: false` (with `olean_modules`, the number of oleans found, and
`weighted_slots: 0`), the runner warns on stderr that the heaviest-first part
was skipped, and the sample is the stratified one alone. Each sampled row
records `weight_bytes`, `weight_module` and `heavy`. The olean size is a proxy: a bigger olean marks a heavier module,
whose declarations tend to pull in a bigger closure.

The models. `estimated_seconds` is the per-slot mean model,
`per_slot_seconds * slots / judges`: what a run costs when every slot pays
its own export closure at the sampled mean. `estimated_seconds_max` is the
per-slot max model, `per_slot_max_seconds * slots / judges`.
`lower_bound_seconds` is the dearest sampled slot: no run that includes it
can be faster. `recommended_cap` is `ceil_5min(1.25 * M + 600)`, the 600
seconds covering the per-invocation `lake env`, no-op builds and
`lake exe cache get`, where M is `estimated_seconds_max` when the sample
covers less than half the slots (`cap_model: "max"`) and
`estimated_seconds` otherwise (`cap_model: "mean"`): a small sample's mean
says nothing about the unsampled slots, which may be heavier than any it
timed.

`remaining_estimate_seconds` is what the real run still has to judge: the
sampled slots that passed are cached, so it counts only the
`unsampled_slots`, at `remaining_per_slot_seconds`, the mean of the timed
non-heavy (stratified) sampled slots (all timed slots when none is
non-heavy), divided by the judges. The heavy slots were picked as the
heaviest, so one slow outlier among them does not inflate every other slot;
the model is recorded as `remaining_estimate_model`. It is a per-slot model
like the others, so the whole-unit warning applies, and it does not change
`recommended_cap`. A field run motivated these rules: two isolated slots of a 16-slot
configuration (69.5 s and 168.7 s) gave a 3000 s cap under the mean model,
and the other 14 names, judged as one unit, were still in their single
export after 46 minutes when the cap stopped the run with nothing written.
The max model would have asked for 4200 s; the warning below says why even
that is not a bound.

The whole-unit warning. Every model above times names judged alone. The
real run judges each configuration as one unit (or, under `--judges N`, one
unit per shard; each shard pays its own export of its names' closure, so
shards run in parallel but do not shorten an export), and a unit's single
export pays the union of its names' closures, which the isolated per-slot
timings do not bound. The unit cache (`04-slots/`) is per unit, so within a
cap a 14-name unit is accepted or lost whole. With `--unit-size 1` every
name is its own unit, cached on acceptance: the per-slot model then applies
name by name, and a run that hits its cap resumes where it stopped. When
the cap matters, prefer that shape.

**Serial builds on a memory-tight host.** Lake 5 has no `-j`/`--jobs`
option; it schedules build jobs on the Lean task pool, whose size is
`LEAN_NUM_THREADS`. `COMPARATOR_RECEIPT_LEAN_THREADS=1` sets
`LEAN_NUM_THREADS=1` in the environment of every `lake`/`lean` child of the
setup and comparator stages (the children `COMPARATOR_RECEIPT_STACK`
reaches, the judges included), so the `build` stage compiles one module at a
time (with single-threaded elaboration); any positive integer N works the
same way. The runner prints the value it applied and records it in the
receipt as the additive `lean_threads` (`N`, or `null` when the variable is
unset and the children inherit the runner's environment); any other value
is a usage error (exit 2). As a fallback (for example around a runner
without this variable), `COMPARATOR_LAKE` may name a wrapper script that
exports `LEAN_NUM_THREADS=1` and then runs the real `lake`. That caps the number of modules in memory at
once; it does not lower a single module's peak, so a module that alone
exceeds the host's memory still fails. Put the run under a memory cap, for example
`systemd-run --user --scope -p MemoryMax=48G comparator-receipt --prepare`,
so an overrun is a clean kill of that scope (the stage log and
`stages.json` say which stage died) rather than the kernel's OOM killer
choosing a process elsewhere on the host.

**Stack limit.** Lean recurses deeply on a large module. Under the common
8 MiB default soft `RLIMIT_STACK`, a `lean` child can die with SIGSEGV
(exit 139) deep in `lean::sharecommon_quick_fn::visit_ctor`; that is a stack
overflow, not a Lean bug in the package. Before the `build` stage (and,
when the setup is reused or taken from `--from-prepared`, before the
`comparator` stage) the runner reads the soft limit and, when it is at most
8 MiB, prints `WARN: soft stack limit is 8 MiB; ...` on stderr.
`COMPARATOR_RECEIPT_STACK=unlimited` (or a size in kB, such as `65536`)
raises the soft limit for the `lake`/`lean` children of the setup and
comparator stages only, set in each child just before it starts, never
above the hard limit (a larger request is clipped to it, and the line says
so); the runner prints what it applied. Any other value is a usage error
(exit 2). The receipt records the limit the children ran under as the
additive `stack_limit` `{soft_kb, hard_kb, source}` (`soft_kb`/`hard_kb` an
integer or `unlimited`; `source` `inherited` or `COMPARATOR_RECEIPT_STACK`);
validators ignore it.

**Build provenance.** The `build` stage runs
`lake build <Challenge> <Solution>`; when `.lake/build` already holds the package's oleans for this
commit, toolchain and manifest it compiles nothing, and the receipt says so
in the additive `build_provenance`
`{compiled, modules_built, prebuilt, source}`. The rule: only Lake's own
`[i/n] Built <target>` lines in the stage log witness a compilation;
`compiled` is true when there is one and `modules_built` counts the distinct
`Built` targets. Lake's local artifact cache writes oleans without compiling
them, and says so with `Reused`, `Replayed` or `Unpacked` (checked against
the Lake source of v4.35.0-rc2), so an olean write alone proves nothing: the
runner records the mtime of every olean under the package's own
`.lake/build/lib/` (not the dependencies under `.lake/packages/`) before and
after the stage, and an olean that is new or changed with no `Built` line
for its module is counted in the additive `restored` (present when
non-zero); the distinct `Reused` / `Replayed` / `Unpacked` targets are
counted in `cache_reused` (likewise). `prebuilt` is true when the stage ran,
passed and compiled nothing; a skipped stage records
`compiled: false, modules_built: null, prebuilt: false`. `COMPARATOR_RECEIPT_BUILD_SOURCE` declares where a
prebuilt tree came from (free text such as a job id or a host label,
redacted like every receipt string; `null` when unset). The runner prints
`build: compiled N module(s)` or `build: prebuilt (source: ...)` after the
stage, followed by `; N olean(s) restored, not compiled` and
`N module(s) Lake reports reused from a cache` when those are non-zero.
After the build the stage also runs `lake env printenv LEAN_PATH` and
refuses (stage `build` failed, nothing written) when an entry other than the
package's own `.lake/build/lib/lean` holds one of its root namespaces (the
lakefile's `lean_lib` names, roots and globs, else the tracked sources'
top-level names, plus the configured Challenge and Solution modules'): Lake
lists the root package last and Lean loads `<R>/` from the first entry
holding it, so a dependency's copy would be judged instead. Each `lean-path-shadow:`
line goes to `03-build.log`. `verify-comparator.sh` and `comparator-check.sh`
run the same check (one shell function, `lake_shadow_check`) before they
judge; a reused or `--from-prepared` setup carries the provenance recorded
in its `stages.json` when it was built (a state prepared before this field
existed records nulls, printed as `provenance not recorded`).
`comparator --check` and `gate --check` print the same line; validators
ignore the key. Oleans built on another host are acceptable when that host
built the same commit with the same `lean-toolchain` and `lake-manifest.json`
(copy the whole `.lake/build` of the checkout): Lake's traces bind each olean
to its sources and dependencies, so a mismatch makes Lake rebuild it (and
the receipt then says `compiled`), and the comparator replays the Solution's
export with the kernel, so a prebuilt olean cannot carry an unchecked proof
into the verdict. Declare the source so a reader of the receipt knows where
the build happened, for example
`COMPARATOR_RECEIPT_BUILD_SOURCE="cluster job 4711" comparator-receipt`.

### Runtime

Where the time goes. Neither judge does work per slot. One `lake comparator`
invocation (toolchain) runs a sandboxed `lake env`, a `lake build` of the
Challenge (a no-op after setup), then `leanexport Challenge -- <names>` with
every theorem name, about 30 kernel primitives and the permitted axioms; it
writes the transitive closure of those names as NDJSON, values included, so
any statement that mentions `ℝ` pulls in the proofs inside its instances and
with them a large part of Mathlib. `leanexport` is single-threaded. The same
follows for the Solution, then both exports are parsed and compared in
process, and NanoDa (4 threads), con-ron and `leanchecker` each replay the
whole Solution export. `verify-comparator.sh` also runs `lake exe cache get`
first. The legacy mechanism has the same shape (pinned `lean4export`, then
`nanoda` and Lean's kernel under landrun) plus a one-time tool build, and is
not faster. So the cost follows the size of the export closure, not the
number of slots, and the slowest step is one CPU core. A field run of one
242-slot configuration spent 2 h 47 min of single-threaded export writing a
1.92 GB Challenge export; the Solution export had reached 905 MB when an
external 3 h cap killed the run, before any kernel replay had started.

What cuts runtime for a package of about 250 slots:

- Judge the whole configuration in one invocation (`--judges 1`, the
  default). The shared closure is exported and replayed once; this is the
  least total work and is exactly what Palomar runs.
- `--judges N` shortens the wall time only when the shards' closures are
  largely disjoint (theorems in unrelated corners that do not share a heavy
  vocabulary). For a Mathlib analysis package every shard exports most of
  the same closure again: wall time barely drops while CPU, memory and temp
  space grow N-fold. The per-slot timings of `--estimate` show which case
  applies: if the sampled slots cost about the same (`per_slot_min_seconds`
  close to `per_slot_max_seconds`), the closure is shared, and sharding or
  per-slot judging multiplies the work.
- Give the run a cap that fits. Run `comparator-receipt --estimate` after
  `--prepare` and use its `recommended_cap`
  (`ceil_5min(1.25 * per_slot_seconds * slots / judges + 600)`, with
  `per_slot_max_seconds` in place of the mean when the sample covers less
  than half the slots). A batched run of a shared closure usually lands
  between `lower_bound_seconds` and `estimated_seconds`, near the lower end,
  but a unit's union closure is not bounded by isolated timings (see the
  `whole_unit_warning` above).
  For the field package above, a 3 h cap could not finish: plan for well
  over twice the measured export time.
- When no single job can hold the whole run, make progress across jobs:
  judge `--slots 1-60`, `--slots 61-120`, and so on in successive jobs (each
  writes a partial receipt and caches its slots), then run without `--slots`
  to write the receipt from the cache. Each batch pays the shared closure
  again, so use as few batches as the cap allows. An interrupted run keeps
  only the units that completed; a single whole-configuration unit is all
  or nothing.
- Choose the host for one fast core and enough memory and disk: the export
  is single-threaded, NanoDa uses four threads, and each export is a file
  of gigabytes in the unit's `TMPDIR` under the evidence directory, so put
  `--evidence-dir` on local disk, not a network filesystem.
  `LEAN_NUM_THREADS` does not speed up the export.

`comparator-receipt --prepare` runs only the setup stages and writes no
receipt. It is idempotent: a second `--prepare` with a matching completed
setup does nothing. A later `comparator-receipt` whose commit, toolchain and
config hash (sha256 over each configuration's path and bytes) match a
completed setup in the evidence directory skips straight to the comparison,
and the reused stages read `prepared`. Any mismatch reruns the setup.
`--from-prepared DIR` reuses the setup recorded in another evidence directory
and refuses, writing nothing, when it was prepared for anything else. So a
one-run budget is spent on the comparison: retry `--prepare` until it passes,
then run once. A comparison that fails after setup can also be retried
without repeating setup. The receipt gains an additive `stages` list
(`stage`, `status`, `exit`, `wall_s`; no paths) for the stages before
`receipt`. Validators ignore it.

**No host paths in evidence.** Every line the runner writes to a stage
log, every unit and receipt log, and every receipt field has absolute paths
under the checkout, `HOME` and `TMPDIR` replaced by `<checkout>/...`,
`<home>/...` and `<tmp>/...` (longest prefix first, at path boundaries, both
as given and resolved), so `public-safety` does not flag lean-pkg's own
output. An `--evidence-dir` outside the checkout becomes `<evidence>/...`
wherever it sits (under `HOME`, `TMPDIR` or neither): the toolchain judge of
a partial unit (`--unit-size`, `--judges` above 1, a resumed run) reads a
configuration copy from `<evidence>/comparator-stages/04-units/`, and its
command line reaches the committed log. The receipt's hashes and bindings cover the tree's bytes and the
logs as committed, not where the checkout lives, so validation is
unaffected. `--host-label LABEL` (or `COMPARATOR_RECEIPT_HOST_LABEL`)
replaces the host name in `host`, `job_id`, `host_policy` and the output,
and is recorded as `host_label`; without it the host name is kept. Messages
printed to the terminal keep real paths. `stages.json` keeps real paths as
pointers for the operator; it is never committed.

**Which `lake`.** The runner never takes `lake` or `lean` from a bare PATH
lookup, since a non-login shell can find a system `lake` from an old Lean
before elan's proxies. When elan is installed (`$ELAN_HOME/bin/elan`,
default `~/.elan`), elan's `bin/` goes first on `PATH` for every setup
stage, toolchain probe and judge, including the vendored scripts they run,
and `ELAN_TOOLCHAIN` is set to the checkout's `lean-toolchain`. Without
elan, `PATH`'s `lake` is used only when `lake --version` names the Lean
that `lean-toolchain` names; otherwise the run is refused before anything
is written, naming both versions. `COMPARATOR_LAKE` remains an explicit
override for the setup stages. `stages.json` and the receipt record
`toolchain_resolution {source: elan|path|env, lake, version}`, and
`comparator host-check` gains a `lake-path` line that warns when `PATH`'s
`lake` is not elan's or elan is missing.

The runner removes the repository-selecting Git variables (`GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
`GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`, `GIT_NAMESPACE`) from
the environment of every `lake`, `git` and judge it runs. A launcher that
leaks one, such as a `GIT_INDEX_FILE` exported by a Git hook, would otherwise
point Lake's dependency checkout at another repository's index, and the run
would fail in `checkout` before any comparison. `GIT_CONFIG_*`,
`GIT_SSH_COMMAND` and `GIT_TERMINAL_PROMPT` are kept. `stages.json` lists the
names it removed in `git_env_scrubbed` (empty when none were set). `lean-pkg`
applies the same scrub to its own `git` calls in a package checkout, but not
to those in the registry repository. `comparator host-check` warns
(`git-env`, and `env_warnings[]` in `--json`) and the `doctor` `host` line
warns when any of them is set in the calling environment.

## Mechanisms

`comparator-receipt --mechanism auto|toolchain|legacy` chooses how the
solution is judged. The receipt records the choice in its `mechanism` field.

| Mechanism | What judges | When it applies | What `tools` records |
|---|---|---|---|
| `toolchain` | `lake comparator` from the project's own toolchain, with the NanoDa and con-ron kernels the toolchain bundles; the vendored PalomarTemplate `verify-comparator.sh` drives it | Lean v4.35.0-rc2 or later (Palomar's floor). This is what the registry itself runs, so a local pass predicts a Palomar pass | `lean_toolchain` plus the sha256 of `lake`, `leanexport`, `leanchecker`, `nanoda_bin` and `con-ron` under `lean --print-prefix`/bin |
| `legacy` | the pinned comparator, lean4export, nanoda and landrun stack built by `comparator-check.sh` | any toolchain that lean4export has a tag for; kept for packages below the floor | the four 40-hex commits actually used |
| `auto` (default) | | `toolchain` when the project's toolchain bundles the judging binaries, else `legacy` | as above |

First live run of the toolchain mechanism (2026-10-06, Lean v4.35.0-rc2, a
one-theorem smoke configuration): `lake comparator` printed one acceptance
line per kernel (con-ron, NanoDa, Lean's default kernel) and finally
`Your solution is okay!`, and the runner wrote a schema-2 receipt that
`lean-pkg comparator --check` accepted.

The toolchain mechanism needs a Mathlib dependency: a `mathlib` package in
`lake-manifest.json`. `verify-comparator.sh` runs `lake exe cache get`
before judging, and a project without Mathlib has no `cache` executable
(`error: unknown executable cache`). The runner therefore refuses such a
project before any stage runs:

```
the toolchain mechanism needs a Mathlib dependency (lake-manifest.json has none); add it and run lake update
```

`COMPARATOR_RECEIPT_NO_MATHLIB=1` declares that the project knowingly has no
Mathlib; the run then goes ahead, stage `cache-get` is skipped, and the
runner sets `VERIFY_COMPARATOR_SKIP_CACHE_GET=1` so the vendored script
skips its own `lake exe cache get` (a local change to the vendored script,
listed in its header with the others).

A judge that exits non-zero is reported in one of two ways.
`did not accept the solution` means the judge got as far as judging
(`verify-comparator.sh` printed its `== lake comparator (toolchain) <config>`
line, or `comparator-check.sh` printed its rejection line) and the proof or
statement is at fault. `the judge tool failed before judging`, with the
judge's last log line, means a tool or setup step failed first; it is not a
verdict on the proof.

`--mechanism toolchain` on a toolchain below the floor refuses with a message
that names the floor and writes nothing. A `legacy` pass is evidence of a
second-kernel replay, not of what Palomar will say; only the `toolchain`
mechanism is Palomar-predictive.

A package below the floor (for example Lean v4.32.x) can only get a `legacy`
receipt. That is evidence of statement equality and second-kernel replay; it
says nothing about Palomar. If Palomar is the target, move the package to a
toolchain at or above the floor first.

## Host requirements per mechanism

The two mechanisms sandbox their judges differently, and neither needs the
other's sandbox.

| Mechanism | Sandbox | Needs on the host | Source |
|---|---|---|---|
| `toolchain` (what Palomar runs) | `bwrap` (bubblewrap), an unprivileged user namespace | Linux, a working `bwrap`, `lean`, `lake`, `python3`; per project, Lean v4.35.0-rc2 or later. No Landlock | `verify-comparator.sh` (its required commands) and `lake comparator` |
| `legacy` | `landrun` (Landlock) | Linux with Landlock ABI 1 or later (kernel 5.13+), `go`, `cargo`, `git`, `jq`, `lean`, `lake`. No `bwrap` | `comparator-check.sh` (the tools it builds) and the pinned comparator |

`lake comparator` needs an unprivileged user namespace for `bwrap`. Ubuntu
24.04 forbids that by default through AppArmor, and the symptom is
`bwrap: setting up uid map: Permission denied`. The runner checks
`bwrap --ro-bind / / true` before judging and refuses with the fix in its
message: as root, either an AppArmor profile for `/usr/bin/bwrap` that
grants `userns` (narrow, recommended) or
`sysctl kernel.apparmor_restrict_unprivileged_userns=0` (broad). The legacy
mechanism is not affected, so such a host can still produce `legacy`
receipts, which are not Palomar-predictive.

Landlock for the legacy mechanism: landrun at the pinned `LANDRUN_COMMIT`
targets Landlock ABI 9 in strict mode by default, but the pinned comparator
always calls it with `--best-effort` (`buildLandrunArgs` in its `Main.lean`),
so landrun degrades to the ABI the kernel offers and any ABI from 1 runs.
What a lower ABI loses is sandbox strength, not the ability to judge: below
ABI 4 (kernel 6.7) landrun has no network rules, below ABI 6 (kernel 6.12)
no IPC scoping (landrun's README, kernel compatibility table). ABI 9 is not
a requirement of this runner.

## Checking a host first

`lean-pkg comparator host-check` answers "can this host produce comparator
receipts, and which kind?" before any build. It is read-only, needs no
registry and no project, and prints one `ok`/`warn`/`fail` line each for:
`kernel` (Linux and its release), `landlock` (the Landlock ABI from
`landlock_create_ruleset`, plus the LSM list from `/sys/kernel/security/lsm`;
`fail` when absent, `warn` below ABI 4, which lacks network rules; the line
names where the thresholds come from), `bwrap` (the same user-namespace
probe the runner makes), and `go`, `cargo`, `jq`, `git`, `lean`, `lake`,
`python3` on `PATH`. Then one `mechanism toolchain|legacy: ok|fail` line
each, judged against that mechanism's own requirements (the table above)
with the failing ones named, and the verdict, which names only the
mechanisms whose every requirement holds and says that Palomar accepts only
the `toolchain` mechanism (when only `legacy` is usable it says so, and why
`toolchain` fails). Exit 0 when either is usable, 1 when neither (`--json`:
`checks`, `mechanisms`, `mechanism_detail` with `ok`, `needs`, `failing`,
`palomar` per mechanism, `can`, `palomar`, `verdict`). `comparator-receipt`
prints the same verdict on stderr when it starts (a report, never a
refusal), and `lean-pkg doctor` shows it as its `host` line.

## Host policy

The receipt records the host name. Pass `--host-label LABEL` (or set
`COMPARATOR_RECEIPT_HOST_LABEL`) to a public-safe label (for example
`lab-workstation`) when the receipt will be copied into a public index; the
label replaces the host name everywhere in the receipt, including the
`host_policy` sentence, and is recorded as `host_label`.

The comparator build is heavy. The runner allows a run only when one of
these holds, and records which in `host_policy`:

| Condition | Reason recorded |
|---|---|
| `SLURM_JOB_ID` is set | `Slurm job <id> on <host>` |
| the hostname is in `COMPARATOR_RECEIPT_ALLOWED_HOSTS` | `host <host> listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS` |
| `COMPARATOR_RECEIPT_ALLOW_HOST=1` | `allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1` |

Otherwise it refuses with a message naming all three options. In CI, set the
runner's hostname in `COMPARATOR_RECEIPT_ALLOWED_HOSTS` or use the explicit
override; the override is visible in the receipt either way.

### Which hosts can produce receipts

The policy above says where a run is *allowed*; whether a host *can* produce a
receipt depends on its kernel and tools. Run `lean-pkg comparator host-check`
on the candidate host before sending it work. It checks the kernel, the
Landlock ABI, whether `bwrap` can create a user namespace, and the tools each
mechanism needs, then prints `mechanism toolchain: ok|fail` and
`mechanism legacy: ok|fail` (exit 0 when some mechanism can run, 1 when none
can). A host that fails `mechanism toolchain` cannot produce `toolchain`
receipts, the mechanism Palomar runs: the usual causes are a missing `bwrap`,
or a kernel or AppArmor policy that forbids unprivileged user namespaces. Such
a host may still produce `legacy` receipts if Landlock is available.

Older cluster images are a common case. EL7 compute nodes, for example,
often have unprivileged user namespaces disabled: `bwrap` is missing or
cannot unshare, and `unshare -Ur true` fails too. Such a node cannot run the
`toolchain` mechanism at all, however well it builds and audits: a cluster
that passes the full lower-tier gate (`lake build` plus the axiom audit) may
still fail here. Run `lean-pkg comparator host-check` on every candidate
host, compute nodes included, before planning a comparator run, and plan the
comparison on a host that passes `mechanism toolchain` (the build may come
from elsewhere: see "A cluster without unprivileged user namespaces" below).
`lean-pkg doctor` reports the same verdict on its `host` line, and its `tool comparator`
line points at `host-check` when `bwrap` is missing. Which of your own hosts
passes is a fact about your infrastructure: record it in your private registry
notes, not in public documentation.

The maintainer's lab runs the Slurm route on the Auburn University Easley
Cluster: `SLURM_JOB_ID` jobs there produced lower-tier receipts (`lake build`
plus axiom audit) and comparator receipts (statement comparison and kernel
replay) while lean-pkg was developed. A package whose receipts came from
Easley acknowledges it in its README and in `formalization.yaml`'s
`acknowledgements` with the official sentence: "This work was completed in part with resources provided by the Auburn University Easley Cluster."

### A cluster without unprivileged user namespaces

Both sandboxes are kernel features, not tools a user can bring along. The
`toolchain` judge runs inside `bwrap`, which needs either an unprivileged
user namespace (`unshare --user --map-root-user true` succeeds) or a
`bwrap` the site installed setuid root; the `legacy` judge runs inside
`landrun`, which needs Landlock (kernel 5.13 or later with `landlock` in the
LSM list). On a cluster where `bwrap` is missing and
`unshare --user --map-root-user true` fails with `Invalid argument` or
`Operation not permitted`, and the kernel predates Landlock, neither
mechanism can judge there.

A container does not change that. Singularity and Apptainer containers share
the host kernel: `--userns` and `--fakeroot` themselves need unprivileged
user namespaces (or a setuid starter with `/etc/subuid` entries the site
configured), a `bwrap` copied into the image still asks the host kernel for
a user namespace, and a setuid `bwrap` inside the image gains nothing because
the runtime starts the container with `no_new_privs`. There is no supported
portable comparator container recipe. A container that passes
`lean-pkg comparator host-check` with the job's own binds and `PATH` is
fine; one that fails it cannot produce receipts. Never disable, stub or wrap
the sandbox to get past the check (no fake `bwrap`, no
`--mechanism legacy` on a kernel without Landlock, no test-only override
variable outside the test suite): a receipt
whose judge ran unsandboxed is not evidence.

What is supported:

1. Check, do not assume. Run the check where the job would run, inside the
   image with the job's binds and `PATH` export, on a compute node as well
   as the login node (they can differ):

   ```bash
   srun --time=00:05:00 --cpus-per-task=1 --mem=2G \
     singularity exec --bind "$WORK" "$IMAGE" bash -c \
     'export PATH=...; lean-pkg comparator host-check'
   ```

   `mechanism toolchain: ok` means this node can judge; anything else, read
   the failing items it names.
2. Build on the cluster, judge on a host that passes. The `build` stage
   needs no sandbox, and `comparator-receipt --prepare` runs no judge, so it
   may run in a cluster job. Copy the checkout's whole `.lake/build` to a
   checkout of the same commit on the judging host (same `lean-toolchain`
   and `lake-manifest.json`; see "Build provenance" above for why prebuilt
   oleans cannot carry an unchecked proof into the verdict), then prepare
   and judge there, declaring where the build came from:

   ```bash
   # cluster job, inside the image
   cd "$CHECKOUT" && comparator-receipt --prepare
   # judging host, same commit, after copying .lake/build across
   lean-pkg comparator host-check            # must print mechanism toolchain: ok
   export COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)"
   export COMPARATOR_RECEIPT_BUILD_SOURCE="cluster job $JOB"
   comparator-receipt --prepare              # compiles nothing: build: prebuilt
   comparator-receipt --estimate             # judges a sample; read recommended_cap
   systemd-run --user --scope -p MemoryMax=48G \
     timeout "$CAP" comparator-receipt
   ```

   The judge (export and kernel replay) is the expensive part and runs on
   the judging host either way; the split saves only the build. Size the
   memory cap and `CAP` from `--estimate` and "Runtime" above. The receipt
   records the judging host in `host`/`host_policy` and the cluster build
   in `build_provenance.source`.
3. Ask the site. A site that enables unprivileged user namespaces on its
   compute nodes (and adds `bwrap` to the image), or installs a setuid
   `bwrap` on the nodes for jobs run outside a container, makes the node a
   judging host; re-run `host-check` there before relying on it.

## Tool pins

The pins live in `comparator-check.sh` (`COMPARATOR_COMMIT`,
`NANODA_COMMIT`, `LANDRUN_COMMIT`; `lean4export` is taken at the release tag
named after the project's Lean version unless `LEAN4EXPORT_COMMIT` is set).
Change pins only in a dedicated commit, and expect every receipt produced
afterwards to record the new values in `tools`.

## Validation, in one line

`gate --comparator`, `comparator`, and `promote` do not trust the receipt's
word: they re-read the configuration files at the bound commit, hash them,
compare every declared field, hash the logs on disk, look for the PASS line
as a full line, and check the axiom subset and the tool pin format. Any
miss is a named refusal. See [Receipts and trust](receipts.md).

### Pre-checking a receipt made in another checkout

`comparator NAME --check --receipt R` validates against the registered
`local_path`. A receipt made in a worktree or on a feature branch binds that
checkout's HEAD and has its logs there, so pass the checkout with `--repo`:

```bash
lean-pkg comparator beta --check --receipt .lean-receipts/<c12>-comparator.json --repo .
```

The rules are those of `gate --check --repo DIR`: this call only, never
written to the registry; a stderr note names both paths (none when DIR is the
registered path); JSON `repo_override` {path, registered_path, remote_match},
`null` without `--repo`; DIR must be a git work tree and, when it has remotes
and the record names a repository, one remote must be it (a local-path repo
`/p` also matches `Host:/p`, `ssh://Host/p` and `file:///p`; the registered
checkout itself needs no remote), otherwise the caller vouches for it.
Attaching (`comparator NAME --receipt R` without `--check`) refuses `--repo`
(exit 2): it binds the registered checkout, which
`update NAME --path DIR --reason TEXT` moves. `comparator config` takes
`--checkout DIR` instead.
