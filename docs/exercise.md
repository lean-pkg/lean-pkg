---
title: Checking Lean exercises
---

[&larr; lean-pkg documentation](index.md)

*Did the Solution prove the theorem that was asked, and only with the axioms allowed?*

`lean-exercise` is a separate console script, installed with lean-pkg, for
exercises: a teacher or an agent writes a `Challenge.lean` whose theorems end
in `sorry`, a learner writes a `Solution.lean`, and `lean-exercise check`
judges the Solution with the same `lake comparator` that the comparator tier
uses (see [The comparator tier](comparator.md)). It has nothing to do with
the registry. It never opens a registry, an event log, a git repository, a
receipt or a lease. A verdict is never registry evidence.

```bash
lean-exercise check --challenge Challenge.lean --solution Solution.lean
```

```text
judging t (1/1) ...
PASS: 1/1 theorem(s) accepted (sandboxed, leanprover/lean4:v4.35.0-rc2)
accepted           t: Your solution is okay! [con-ron, nanoda, lean] (8.3s)
PASS: 1/1 theorem(s) accepted (sandboxed, leanprover/lean4:v4.35.0-rc2)
```

## What it does

1. It reads both files without running Lean. Every `theorem` or `lemma` in
   the Challenge whose proof mentions `sorry` is one exercise, named in full
   (`namespace Foo` + `theorem bar` is `Foo.bar`). `--theorems a,b` judges
   only the theorems you name, and each one must exist in the Challenge.
2. It checks the host before writing anything (see [Host](#host)) and picks
   the toolchain: `--toolchain T`, else the newest installed release that is
   `v4.35.0-rc2` or later, the first one that ships `lake comparator`.
3. It writes a throwaway Lake project into a new temporary directory (under
   `$TMPDIR`): `lean-toolchain`, `lakefile.toml` with the libraries
   `Challenge`, `Solution` (and `Defs`), a `lake-manifest.json` with no
   dependencies (with `--project`, the project's pinned ones, see
   [Course use](#course-use)), your files copied byte for byte, and one comparator
   configuration per theorem. The NanoDa and con-ron kernels bundled with the
   toolchain are registered as external kernels.
4. It runs `lake comparator --config` once per theorem, with
   `LAKE_ARTIFACT_CACHE=false` and an empty Lake cache inside the temporary
   directory. The comparator stops at the first error, and an axiom error
   does not name the theorem, so one run per theorem is what gives one
   verdict per theorem. `lake comparator` builds the learner's code inside its
   `bwrap` sandbox. `lean-exercise` never runs `lake build` itself.
5. It deletes the temporary directory, unless you pass `--keep DIR`. Then the
   project is built in `DIR` (which must be absent or empty), with each run's
   log in `DIR/cfg/<n>.log`.

## Verdicts

| Verdict | Meaning |
|---|---|
| `accepted` | `Your solution is okay!`: same statement, permitted axioms only, accepted by every kernel (con-ron, NanoDa, Lean's own), and no `--forbid` constant reached |
| `statement_mismatch` | the Solution proves a different statement (for example a weakened one), or declares the name as something other than a theorem |
| `illegal_axiom` | the proof uses an axiom outside `--axioms`. `axiom` names it: `sorry` for `sorryAx`, a user `axiom`, or `<thm>._native.native_decide.ax_…` for `native_decide` |
| `forbidden_constant` | accepted by the comparator, but the proof depends on a constant `--forbid` / `--forbid-exact` names. `constant` names the first one, `constants` all of them; the detail names the helper chain it came through (see [Course use](#course-use)) |
| `kernel_rejected` | a kernel rejected the proof term |
| `build_error` | the Solution does not compile. The first error is the detail. Every theorem after it gets the same verdict without another run, since the build is shared |
| `missing` | the Solution does not declare this theorem. The other theorems are still judged |
| `timeout` | the run took longer than `--timeout` seconds (default 600 per theorem) |
| `tool_error` | anything else, including a Challenge or `Defs.lean` that does not build: fix the exercise, not the Solution |

Exit codes: `0` every theorem accepted; `1` at least one was not; `2` a usage
or host refusal, with nothing left behind.

A helper lemma in the Solution is fine. An extra theorem the Challenge does
not ask for is not judged, so a `native_decide` there does not matter. A
`native_decide` inside a judged theorem is an `illegal_axiom`.

## Rules for the exercise

- **Theorems only in the Challenge.** The comparator compares a definition by
  its type alone, so if the Challenge declared `def f : Nat → Nat` for the
  learner to fill in, any function of that type would pass. A Challenge that
  declares a `def`, `abbrev`, `structure`, `class`, `instance`, `inductive`,
  `opaque` or `axiom` is refused (exit 2).
- **Shared definitions go in `--defs FILE`.** That file becomes the module
  `Defs`, and both files `import Defs`. Both sides then refer to the very same
  constants. `Defs.lean` must not use `sorry`.
- **Standard axioms only.** `--axioms` takes a subset of `propext`,
  `Quot.sound` and `Classical.choice` (default: all three). Anything else is
  refused, so a grader cannot permit a cheat by accident.
- **No Mathlib without `--project`.** Imports are limited to `Init`, `Std`,
  `Lean` and `Defs`. An `import Mathlib` is refused with exit 2 unless
  `--project DIR` names a prebuilt Mathlib project (see [Course use](#course-use)).
- A `private theorem` cannot be judged by name and is refused.
- **Module-headed files are judged as they are.** A Challenge, Solution or
  `Defs.lean` may start with `module` (Lean's module system, what Palomar
  requires); the import rules above then apply to `public import`,
  `meta import` and `import all` too, and a `public def` in the Challenge is
  refused like any definition. In a module a theorem is private unless it is
  inside `public section` ... `end` or declared `public theorem`, and the
  judge cannot export a private one: in the Challenge that is a `tool_error`
  (`the Challenge does not export this theorem`), in the Solution a `missing`
  whose detail says so. A module may import only modules, so with a
  module-headed Challenge `Defs.lean` starts with `module` too, and a shared
  definition whose body a proof unfolds needs `@[expose]`.

## Host

The sandboxed check needs Linux with a working `bwrap` that can create an
unprivileged user namespace, and elan with a toolchain `v4.35.0-rc2` or
later. `lean-exercise` runs the `kernel` and `bwrap` lines of
`lean-pkg comparator host-check` before it writes anything, and refuses with
the failing lines and a hint:

- macOS or Windows: run it inside WSL2 or on a Linux machine.
- Ubuntu 24.04: AppArmor blocks unprivileged user namespaces for `bwrap` by
  default. See [Host requirements per mechanism](comparator.md#host-requirements-per-mechanism)
  for the fix.
- No suitable toolchain: `elan toolchain install leanprover/lean4:v4.35.0-rc2`.

## `--no-sandbox`: checking your own work only

```bash
lean-exercise check --challenge Challenge.lean --solution Solution.lean --no-sandbox
```

This passes `--inadvisably-no-sandbox` to `lake comparator` and skips the
`bwrap` requirement. The Solution's code (its elaboration, tactics, `#eval`,
build) then runs as you, with your rights, on your machine. Use it only to
check work you wrote yourself, for example on a Mac. Never use it to grade
someone else's work. A warning is printed on stderr before and after the
run, the summary says `self-check-only`, and the JSON says
`"trust": "self-check-only"` (a sandboxed run says `"sandboxed"`). It is
never the default.

## JSON

`--json` prints one object, schema `exercise-check/1`:

```json
{
  "schema": "exercise-check/1",
  "ok": false,
  "trust": "sandboxed",
  "toolchain": "leanprover/lean4:v4.35.0-rc2",
  "mechanism": "toolchain",
  "challenge_sha256": "…",
  "solution_sha256": "…",
  "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
  "theorems": [
    {"name": "t", "verdict": "illegal_axiom", "detail": "uses sorry", "axiom": "sorry",
     "kernels": [], "seconds": 2.2}
  ],
  "summary": {"accepted": 0, "total": 1},
  "kept": null
}
```

`kernels` lists the kernels that accepted (`con-ron`, `nanoda`, `lean`).
`axiom` is present only for `illegal_axiom`, `constant` and `constants` only
for `forbidden_constant`. With `--project` the object also has
`"mathlib": true`, with `--forbid` a `forbid` object
(`{"prefixes": [...], "exact": [...], "scope": "solution"}`). `kept` is the `--keep`
directory or `null`. A refusal prints `{"ok": false, "error": …, "usage": true}`
and exits 2.

## Flags

| Flag | Meaning |
|---|---|
| `--challenge FILE` | the Challenge (required) |
| `--solution FILE` | the Solution (required) |
| `--theorems a,b` | judge only these fully qualified Challenge theorems |
| `--axioms a,b` | permitted axioms, a subset of the standard three |
| `--defs FILE` | shared definitions, module `Defs` |
| `--project DIR` | a built Mathlib project: allows `import Mathlib...` (see [Course use](#course-use)) |
| `--forbid PREFIX` | reject a proof that depends on a constant whose name starts with `PREFIX` (repeatable) |
| `--forbid-exact NAME` | the same for exactly `NAME` (repeatable) |
| `--forbid-scope S` | `solution` (default) or `all`, see [Forbidden constants](#forbidden-constants) |
| `--toolchain T` | `leanprover/lean4:vX.Y.Z` or `vX.Y.Z`, `v4.35.0-rc2` or later, installed; with `--project` it must be the project's |
| `--timeout S` | seconds per theorem (default 600) |
| `--keep DIR` | build in `DIR` and keep it |
| `--no-sandbox` | self-check only (see above) |
| `--json` | the object above |

## Course use

A course grading Mathlib exercises needs three things the defaults do not
give: Mathlib, a list of lemmas the students may not use, and one run over
every submission. All three run the students' code in the same sandbox as a
single check.

```bash
lean-exercise batch --challenge Midterm.lean --solutions submissions/ \
    --csv grades.csv --logs logs/ --project ~/course-mathlib \
    --forbid MeasurableSpace.DynkinSystem. --forbid MeasurableSpace.generateFrom \
    --forbid-exact MeasurableSpace.induction_on_inter
```

The rules of such a course map onto the verdicts like this: the Solution
compiles (`build_error` otherwise), proves the Challenge's statement
(`statement_mismatch`), uses only `propext`, `Classical.choice` and
`Quot.sound` (`illegal_axiom`; that is the default `--axioms`), and does not
use a forbidden constant (`forbidden_constant`).

### Mathlib: `--project DIR`

`DIR` is an ordinary Lake project of the teacher's: its `lean-toolchain`
(`v4.35.0-rc2` or later) and `lake-manifest.json` pin Mathlib, and Mathlib is
already built in it (`lake exe cache get`, or `lake build`). Prepare it once,
before judging:

```bash
cd ~/course-mathlib && lake exe cache get && lake build
```

`lean-exercise` refuses (exit 2, nothing run) a project with no `mathlib`
package in its manifest, with a dependency that is not checked out or not a
git dependency, or with no built `Mathlib.olean`. The project's toolchain is
the judge's toolchain; a different `--toolchain` is refused.

For each Solution the throwaway project requires the project's direct
dependencies at their pinned revisions, copies its manifest, and links
`.lake/packages` to the project's packages. Nothing is copied and nothing is
downloaded:

- `lake comparator` covers the home directories inside its sandbox, so the
  packages (usually under a home directory) would be invisible.
  `lean-exercise` sets `COMPARATOR_BWRAP` to a small wrapper script that adds
  a **read-only** bind of the packages directory (and of any package that is
  a link to elsewhere) to every sandbox `lake comparator` starts, then runs
  the real `bwrap`. A Solution that tries to write into Mathlib's build gets
  `read-only file system`; it cannot poison the oleans the next student is
  judged against.
- The wrapper also drops `--share-net`, the one network grant `lake
  comparator` makes (for dependency resolution). Judging never downloads:
  if Lake would need to fetch anything, the run fails as a `tool_error`.
- The wrapper refuses to run (exit 125, a `tool_error`) when the command line
  `lake comparator` hands it does not have the shape it expects, rather than
  run a sandbox it did not bind.

Host requirements are those of the sandboxed check (see [Host](#host)) plus
read access to the project, and disk for the Solution's own build only.
A stale Mathlib build (an olean older than its source) cannot be rebuilt in
the read-only bind, so it shows up as a Challenge that does not build
(`tool_error`): run `lake build` in the project. With `--no-sandbox` there is
no wrapper and the packages are writable by the Solution: a self-check only.

### Forbidden constants

`--forbid PREFIX` (repeatable) and `--forbid-exact NAME` (repeatable) reject
a theorem the comparator accepted when its proof depends on a constant whose
name starts with `PREFIX` or is exactly `NAME`. Write a trailing dot to
forbid a namespace (`MeasurableSpace.DynkinSystem.`); without it a prefix
also matches longer names (`MeasurableSpace.generateFrom` matches
`MeasurableSpace.generateFrom_le`).

The check reads the compiled environment, not the source text: after the
comparator runs, `lean-exercise` exports the built Solution with the
toolchain's `leanexport` in a bwrap sandbox of the same shape (no network,
nothing writable) and walks the export, so notation, `open`, tactics, `simp`
and `exact?` cannot hide a dependency. The walk starts at the judged
theorem's proof term:

- **`--forbid-scope solution` (default).** It follows every constant the
  Solution itself declares (helper lemmas, `private` lemmas, local
  definitions and instances, and the `_proof_n` / `match_n` auxiliaries Lean
  generates), so a forbidden lemma used by a helper, or by a helper of a
  helper, is caught. It reports, but does not enter, the library's constants
  (Mathlib, `Defs`, Lean core), found by exporting the names from the
  environment of the Solution's own imports: a Mathlib lemma that Mathlib
  happens to prove with `induction_on_inter` is not held against the student.
- **`--forbid-scope all`.** It follows library constants too. Use it only
  for narrow patterns: most of measure theory unfolds to `generateFrom`
  somewhere (`borel` is defined with it), so `all` with that prefix rejects
  almost every Solution.
- A constant that occurs in the theorem's **statement** is never reported:
  the Challenge fixed it, so the student cannot avoid it. Do not forbid what
  the statement uses.

The verdict is `forbidden_constant`, with the first constant reached in
`constant`, all of them in `constants`, and the chain of helpers in the
detail (`uses the forbidden constant MeasurableSpace.induction_on_inter
(through helper)`). If the export or the walk fails, the theorem is a
`tool_error`, never `accepted`. Theorems the comparator already rejected are
not walked.

### Batch: `lean-exercise batch`

```bash
lean-exercise batch --challenge C.lean --solutions DIR --csv grades.csv --logs logs/ [check options]
```

Every `*.lean` file directly in `DIR` (sorted, not recursive) is judged as
`check` would judge it, each in its own fresh throwaway project, with the
same `--theorems`, `--axioms`, `--defs`, `--project`, `--forbid*`,
`--toolchain` and `--timeout`. The Challenge, host and toolchain are checked
once, before the first file. `--no-sandbox` is refused: a batch grades other
people's work.

`--csv` (must not exist) gets a header and one row per file:

| Column | Content |
|---|---|
| `file` | the file name, nothing else (no path, no other identifier) |
| `sha256` | of the file |
| `result` | `pass`, `fail`, or `refused` (the file itself was refused, e.g. a forbidden import or not UTF-8) |
| `accepted`, `total` | theorems accepted, theorems judged |
| one column per theorem | its verdict (`refused` for a refused file) |
| `detail` | `name: verdict: detail` of every theorem not accepted, `; `-separated |

`--logs` (absent or empty) gets `<stem>.log` per file: the summary `check`
prints, then each `lake comparator` run's output and the forbidden-constant
result, with the temporary directory written `<workdir>` and the home
directory `~`. Name the files by an anonymous id if the file names should
not identify students; `lean-exercise` adds nothing beyond the name.

Exit codes: `0` every file passed, `1` at least one failed or was refused,
`2` a usage or host refusal before the first file. `--json` prints
`exercise-batch/1`: `ok`, `trust`, `toolchain`, `theorems`, `csv`, `logs`,
`files` (`file`, `sha256`, `result`, `accepted`, `total`, `detail`,
`verdicts`) and `summary` (`passed`, `total`).

## Not yet
- `--no-sandbox` on macOS has not been tried end to end. WSL2 has not been
  tested.
