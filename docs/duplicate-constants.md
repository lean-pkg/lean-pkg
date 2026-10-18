---
title: Constants that two modules both declare
---

[&larr; lean-pkg documentation](index.md)

*My environment has `f.congr_simp` in two modules. Which one did the kernel use, and are they the same?*

When two imported modules both carry a constant of the same name, Lean's
`importModules` does not always fail. It keeps one copy when the two
"subsume" each other: same type, same universe parameters and, for a
theorem, the same `all` list. **The proofs are not compared.** Which copy
`env.find?` then returns depends on the import order, and the module that
`env.getModuleIdxFor?` names is the first owner, not necessarily the one
whose copy won.

This happens routinely, and mostly harmlessly, for auxiliaries that Lean
creates on demand in every module that needs them: `f.congr_simp`,
`f.eq_1`, `f.match_1.congr_eq_1`, `f._unary.induct_unfolding`. Lean's own
library has about a hundred such names, a few of them with different proof
terms in different modules. A reviewer who reads one copy may not be
reading the one the environment holds.

After a namespace or import transport, run it on a module that imports
the whole package: see the
[transport checklist](extract.md#checklist-after-a-namespace-or-import-transport-every-declaration-form-one-combined-import).

## The check

```bash
lake build
python3 -m lean_pkg.dup_constants --project . MyLib
```

```text
refused: 1 in-scope duplicate(s) refused: Dup.unequal
  accepted: Dup.equal [A, B (winner)]
  refused: Dup.unequal [A, B (winner)]
      unequal: A differs from the winner in value
  accepted: g.congr_simp [A, B (winner)]
out of scope (--scope project): 90 accepted, 39 refused; --json lists them
refused: 1 in-scope duplicate(s) refused: Dup.unequal
```

It imports the given modules exactly as `import` would, walks the constant
table of every module in the import closure (the tables `importModules`
merged) and, for every name found in more than one of them, compares each
owner's own `ConstantInfo` with the copy `env.find?` returns. The
comparison is exact (`Expr.equal`, binder names and annotations included)
on the kind, the type, the value or proof term, the universe parameters,
the `all` list, the safety (`unsafe`, `partial`), the reducibility hints,
and the shape of an inductive type, constructor or recursor.

A duplicate is **accepted** only when every owner's copy equals the
winner, the winner is one of the owners' copies (pointer identity, so the
report names the module whose copy won), the recorded owner is an owner,
every owner's `.olean` lies in the project, one of its Lake packages or
the toolchain, and there are exactly two owners. It is **refused** as

- `unequal`, with the fields that differ, per owner;
- `extra-owner`: three or more owners, unless the exact owner set is
  pinned with `--allow-owners NAME=M1,M2,M3`; an owner set other than the
  pinned one (a new third owner, or a pinned one gone) is refused too, and
  a pin that matches no duplicate is refused;
- `unknown-owner`: `env.find?` does not return the name, its result is no
  owner's copy, the recorded owner is not an owner, or an `.olean` cannot
  be attributed.

The whole run is refused when a module is not built, when Lean itself
refuses the import (an unequal duplicate it cannot merge:
`environment already contains`), or when the probe's output is incomplete. Nothing is
whitelisted by name: a generated or private name (`_proof_n`, `match_n`,
`eq_n`, `_private.…`) is judged like any other, and no count is added up
across owners or scopes in place of the per-name list.

Exit codes: 0 every in-scope duplicate accepted, 1 any refused or the
probe failed, 2 usage. `--json` prints the full report (schema
`lean-pkg-dup-constants/1`): per duplicate the owners with their origin
(`project`, `package:NAME`, `toolchain`), the winner, the recorded owner,
the differing fields and the reasons.

## Scope

`--scope project` (the default) judges the duplicates with at least one
owner built by the project itself. `--scope packages` adds those with an
owner in a Lake dependency, and `--scope all` judges the toolchain's too.
Duplicates outside the scope are still listed, with their verdicts, under
`out_of_scope`; they do not change the exit code. Lean's own library does
not pass `--scope all` today.

## How it runs

The probe is a small Lean program shipped with lean-pkg
(`lean_pkg/scripts/DupConstants.lean`), run with the project's toolchain
as `lean --run`. Its `LEAN_PATH` is built from the project's
`lake-manifest.json`, read as data: a `git` package at
`<packagesDir>/<name>`, or `<packagesDir>/<name>/<subDir>` when it has a
`subDir`, and a `path` package at its `dir`, relative to the manifest's
directory (for example `vendor/WalshIntegral`). A `dir`, `subDir` or
`packagesDir` that is absolute, leaves its base or goes through a symlink
is refused, as is a package that is not checked out. `lake env` is not
used, because a `lakefile.lean` is code. The modules are imported with `loadExts := false`, so no
initializer or environment extension of the project runs, and the probe
runs inside the same bwrap sandbox `lean-exercise` uses (read-only file
system, no network); `--no-sandbox` reads the files without it.
`lean4export` is not enough here: its NDJSON export records each constant
once, without the module that declared it.

From Python:

```python
from lean_pkg import dup_constants as dc

report = dc.duplicate_constants("path/to/project", ["MyLib"], allow={"f.congr_simp": frozenset({"A", "B", "C"})})
report["ok"], report["verdict"], [d["name"] for d in report["duplicates"] if d["verdict"] == "refused"]
```

The checks above are tests in `tests/test_dup_constants.py`; the ones
that build a small project (two modules that each realize `g.congr_simp`,
and two that add `Dup.equal` and `Dup.unequal` by `addDecl`) run when
`leanprover/lean4:v4.35.0-rc2` is installed.
