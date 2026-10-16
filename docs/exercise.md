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
   dependencies, your files copied byte for byte, and one comparator
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
| `accepted` | `Your solution is okay!`: same statement, permitted axioms only, accepted by every kernel (con-ron, NanoDa, Lean's own) |
| `statement_mismatch` | the Solution proves a different statement (for example a weakened one), or declares the name as something other than a theorem |
| `illegal_axiom` | the proof uses an axiom outside `--axioms`. `axiom` names it: `sorry` for `sorryAx`, a user `axiom`, or `<thm>._native.native_decide.ax_…` for `native_decide` |
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
- **No Mathlib in v1.** Imports are limited to `Init`, `Std`, `Lean` and
  `Defs`. An `import Mathlib` is refused with exit 2.
- A `private theorem` cannot be judged by name and is refused.

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
`axiom` is present only for `illegal_axiom`. `kept` is the `--keep`
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
| `--toolchain T` | `leanprover/lean4:vX.Y.Z` or `vX.Y.Z`, `v4.35.0-rc2` or later, installed |
| `--timeout S` | seconds per theorem (default 600) |
| `--keep DIR` | build in `DIR` and keep it |
| `--no-sandbox` | self-check only (see above) |
| `--json` | the object above |

## Not yet

- Mathlib exercises (a v2 item: the project would need a Mathlib dependency
  and its cache, as the comparator tier has).
- `--no-sandbox` on macOS has not been tried end to end. WSL2 has not been
  tested.
