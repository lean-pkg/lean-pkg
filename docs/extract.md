---
title: Extracting a public Lean repository
---

[&larr; lean-pkg documentation](index.md)

*Which files of my paper repository would a public Lean-verification repository contain, byte for byte, and is anything private among them?*

`lean-pkg-extract` is a separate console script, installed with lean-pkg,
for a working repository that mixes a paper (TeX, bibliography, reviews,
correspondence) with its Lean formalization. It plans a standalone public
repository that holds only the Lean package and its verification material,
with every file traced to a blob of one source commit.

**P1: plan only; write comes next.** This release has the dry run,
`lean-pkg-extract plan`. It writes nothing. A later release adds
`lean-pkg-extract write --output DIR`, which materializes exactly the plan.

For a library you ported by hand, declaration by declaration, from private
modules (new namespaces, a few renames, some declarations left out),
`lean-pkg-extract ledger` records an [extraction
ledger](#declaration-level-extraction-ledger): each source declaration
compared with its port at two named commits, and `lean-pkg-extract recheck`
re-verifies it later. `lean-pkg-extract decls` [writes those target modules
for you](#writing-the-target-modules-decls) from the same map, optionally as a
whole new package, and records the ledger of what it wrote.

```bash
lean-pkg-extract plan --repo . --commit HEAD --manifest extract.json
```

```text
plan: OK: 11 file(s) included (1253 bytes), 4 excluded, 1 allowed private, 3 module(s), 2 claim(s), 0 weakening(s); source commit bf1c0dd6...
included (to <- from, size, sha256, blob, mode, class):
  Demo.lean <- lean/Demo.lean  35  1e48bc25...  2f2cea21...  100644  lean-source
  ...
excluded (PRIVATE: keep this list out of anything published):
  lean/notes/todo.md  private-marker: internal-dir notes/
  ...
modules (module -> file, library):
  Demo.Basic -> Demo/Basic.lean  (Demo)
claims (id -> decl -> file):
  demo/main-bound -> Demo.main_bound -> Demo/Main.lean
pins:
  lean_toolchain: lean-toolchain <- lean/lean-toolchain  sha256 b1a88ff9...  leanprover/lean4:v4.20.0
  lake_manifest: lake-manifest.json <- lean/lake-manifest.json  sha256 acafb9ee...
    mathlib: git https://github.com/leanprover-community/mathlib4 @ <40-hex rev>
totals: ...
plan: OK: ...
```

## What it reads, and what it never touches

- It reads git objects at `--commit` only: `git ls-tree -r -z --full-tree`
  and `git cat-file --batch`. The working tree, the index and uncommitted
  edits are never read, so the plan describes the commit, not your checkout.
- `--commit` may be any revision; the plan records the full 40-hex SHA. A
  revision that is not a commit of `--repo` is refused.
- `--repo` must be the top directory of the repository (manifest paths are
  relative to it); a bare repository works too.
- No registry, event log, lease or receipt is opened, and there is no network
  access: lazy fetches of a partial clone are switched off, so a blob missing
  from the object store is a refusal.
- The only write is `--report PATH`, the JSON plan, which must lie outside
  the source repository (the report lists private paths, and a dry run leaves
  the source unchanged).

## The manifest

The manifest is strict JSON, schema `lean-pkg-extract/1`. Duplicate keys,
unknown keys and non-standard constants (`NaN`) are refused. JSON rather than
YAML because it has one reading: no implicit types, anchors or merge keys.

```json
{
  "schema": "lean-pkg-extract/1",
  "source_commit": "0123456789abcdef0123456789abcdef01234567",
  "roots": [
    {"from": "lean", "to": ".", "kind": "lean-package"},
    {"from": "verification", "to": "verification", "kind": "verification"}
  ],
  "pins": {
    "lean_toolchain": "lean/lean-toolchain",
    "lake_manifest": "lean/lake-manifest.json",
    "claims": "lean/claims.yaml",
    "verification": ["verification/check.sh"]
  },
  "exclude": [{"path": "lean/Scratch/**", "reason": "experiments, not part of the release"}],
  "allow_private": [{"path": "lean/docs/overview.tex", "reason": "the statement overview the README links"}]
}
```

| Key | Meaning |
|---|---|
| `schema` | `lean-pkg-extract/1` (required) |
| `source_commit` | optional; when given, a full 40-hex SHA equal to the resolved `--commit` |
| `roots` | required; `from` (a path in the source, `.` for all of it), `to` (its place in the output, `.` for the output root), `kind`: `lean-package`, `verification`, `docs` or `data` |
| `pins` | required `lean_toolchain` and `lake_manifest`; optional `claims` and `verification` (scripts and controls). Every pin must exist at the commit, be a regular file and be included |
| `exclude` | `{path, reason}` globs over source paths; matching files are left out and listed with the reason |
| `allow_private` | `{path, reason}` globs that include a default-denied file; every use is disclosed in the plan, and an entry that matches no denied file is refused |
| `lean_libs` | `{name, src_dir, roots}`; required for a `lakefile.lean` package (the plan does not run Lean), refused for a `lakefile.toml` one, whose `[[lean_lib]]` and `[[lean_exe]]` tables are read instead |
| `dependency_namespaces` | `{package: [Namespace, ...]}` when a dependency's root namespace is not its capitalized name |
| `allow_unresolved_imports` | `{import, reason}`: an import that resolves to nothing is accepted. **This weakens the import check**; each one is disclosed under `weakened`, and an entry never needed is refused |
| `adopted` | `{paths, upstream, upstream_commit, upstream_license_file, license, license_file?, provenance?}`: files adopted (vendored) from another project, and where their license travels. See [Licenses of adopted files](#licenses-of-adopted-files); a problem here is a **warning**, a malformed entry a refusal |

Paths in `from`, `to` and `pins` must be plain relative POSIX paths: no `..`,
no leading `/`, no backslash, no empty or `.` segment. Globs use the
`.lean-pkg-private-patterns` syntax (`**/`, `*`, `?`; a glob without `/`
matches the file name at any depth).

Roots may nest. A file belongs to the deepest root whose `from` contains it,
so `{"from": "lean", "to": "."}` beside `{"from": "lean/docs", "to": "docs"}`
carves `lean/docs` out of the outer root. Targets may nest too
(`.` and `verification` above), but two roots may not share a `to`, two files
may not map to one output path, a file may not also be a directory, and an
outer root may not write into an inner root's target directory.

## Classification: default deny

Every file under a root is classified, in this order:

1. `exclude` rules: left out, listed with their reason.
2. Default-denied classes, left out unless `allow_private` names them:

   | Class | Paths |
   |---|---|
   | `private-patterns` | `.lean-pkg-private-patterns` |
   | `receipts` | anything under `.lean-receipts/` (receipts bind the source commits, not the extract) |
   | `build-output` | anything under `.lake/` or `.git/` |
   | `review-material` | a `reviews/`, `prompts/` or `transcripts/` directory, `*.transcript*`, `*.jsonl` event streams |
   | `correspondence` | `*.eml`, `*.mbox`, a `mail/` or `correspondence/` directory |
   | `private-marker` | `notes/`, `approvals/`, `drafts/`, `scratch/`, `*.priv`, `*.private`, `TODO-private*` |
   | `manuscript` | `*.tex`, `*.bib`, `*.pdf`, `*.bbl` |

3. Pinned files (class `pin:<role>`).
4. What the root's kind carries: everywhere `README*`, `LICENSE*`, `COPYING*`,
   `NOTICE*`, `AUTHORS*`, `CHANGELOG*`, `CONTRIBUTING*`, `CITATION*`,
   `.gitignore`, `.gitattributes`; `lean-package` adds the Lake files and
   `.lean .md .txt .toml .json .yaml .yml`; `verification` adds
   `.lean .sh .py .md .txt .toml .json .yaml .yml .expected` and `Makefile`;
   `docs` adds `.md .txt .rst .html .css .svg .png .jpg .jpeg .gif`; `data`
   adds `.json .csv .tsv .txt .yaml .yml .toml .md`.

A file that falls in none of these is **unclassified, and the plan is
refused** with its path: exclude it, move it, or put it in a root whose kind
carries it. Files outside every root are not part of the plan at all.

## Licenses of adopted files

A port can pass every check above (sources, hashes, the import DAG) and still
drop the license of files adopted from another project: twelve files copied
from an MIT-licensed upstream are fine to publish only with that upstream's
license notice beside them. The plan therefore checks where each adopted
file's license goes, and reports a problem as a **warning**: the verdict, the
exit code and every existing refusal stay as they were.

Declare the adoption in the manifest:

```json
"adopted": [{
  "paths": ["lean/Demo/Vendor/**"],
  "upstream": "https://github.com/example/Upstream",
  "upstream_commit": "0123456789abcdef0123456789abcdef01234567",
  "upstream_license_file": "LICENSE",
  "license": "MIT",
  "license_file": "lean/LICENSES/LicenseRef-Upstream-MIT.txt",
  "provenance": "lean/LICENSES/Upstream-files.txt"
}]
```

`paths` are globs over source paths (the `exclude` syntax); `upstream`,
`upstream_commit` (the full 40-hex commit the files and the license were
taken from), `upstream_license_file` (its path in the upstream repository)
and `license` (an SPDX identifier) are required. The plan lists each entry
under `adopted[]` with its included files and warns when:

- `license_file` is absent: the files are extracted without the upstream
  license. The warning names the files, the upstream license file and the
  commit, and the scoped path to copy it to;
- `license_file` or `provenance` is missing at the commit, excluded or outside
  every root, so it is not carried into the target;
- the `provenance` file does not mention every adopted file (by its output
  path or its source path);
- `license_file` lies under `LICENSES/` but is named neither by the SPDX id
  nor by a `LicenseRef-` id, which `reuse lint` reports as a bad license;
- an entry matches no included file (stale; a warning, not a refusal, since
  it weakens nothing).

Without any `adopted` entry, the plan still warns when the **nearest**
`LICENSE*` or `COPYING*` above an included file (in its own source directory
or any parent, up to the repository root) is not included: a vendored
directory whose `LICENSE` was excluded, or a source repository whose root
license stays behind because the roots start below it. A nearer license that
is carried silences the warning, and files declared under `adopted` are
checked by their entry instead.

### The scoped pattern

Carry the upstream license for the adopted files only; do not grant a
blanket license for the whole new project by copying it to the root:

1. Copy the upstream license file byte for byte from `upstream_commit`
   (`git -C UPSTREAM show COMMIT:LICENSE > LICENSES/LicenseRef-Upstream-MIT.txt`),
   keeping its copyright line.
2. List the adopted files and where they came from, in the license's own
   `REUSE.toml` annotation, a provenance file, or both:

   ```toml
   version = 1

   [[annotations]]
   path = ["Demo/Vendor/A.lean", "Demo/Vendor/B.lean"]
   SPDX-FileCopyrightText = "2024 Upstream Authors"
   SPDX-License-Identifier = "LicenseRef-Upstream-MIT"
   ```

3. Point `license_file` (and `provenance`, if any) at them and re-run the plan.

The `LicenseRef-<Upstream>-MIT` id is what keeps this
[REUSE](https://reuse.software/)-compatible: REUSE wants every file under
`LICENSES/` named by its license id, and a non-SPDX id must start with
`LicenseRef-`. `LICENSES/MIT.txt` with plain `MIT` works too, but REUSE
expects that file to be the generic MIT text, with the upstream's copyright
moved into `SPDX-FileCopyrightText`; the `LicenseRef-` copy keeps the file
byte for byte. A name such as `LICENSES/Upstream-MIT.txt` is fine for
readers but `reuse lint` reports it as a bad license.

Until the new project chooses its own license, `reuse lint` reports the
project's own files as missing licensing information. That is the honest
state, not a defect to fix by licensing everything under the upstream's
terms.

## Disabled negative fixtures after a namespace or import transport

Negative controls are often stored with a second suffix, such as
`Controls/NoInstance.lean.disabled`, so that no `lean_lib` glob or `*.lean`
search picks them up. After a namespace isolation or an import transport
(renamed modules, a vendored namespace, rewritten `open` lines), those files
are easy to leave behind: they still name the old modules and namespaces, and
an ordinary `*.lean` inventory never lists them. In one port, five such
fixtures kept their old unqualified `open` names, and nothing noticed until a
strict expected-reason audit refused all five.

The plan does not pass over them silently, but it does not check them
either. Such a file under a root has no class any root kind carries, so the
plan is **refused** with its path until it is excluded or moved. Once it is
excluded, it is a line in the excluded list: the module inventory,
`unowned_lean_files` and the import check read `*.lean` files only, so a stale
`open` or `import` in it goes unreported there.

The plan therefore **warns**: every excluded or unclassified file under a root
whose name is `*.lean.<suffix>` (`Foo.lean.disabled`, `Foo.lean.off`,
`Foo.lean.skip`, ...) is named in one entry of `warnings[]`, which points
here and at `lean_diag`'s expected-reason check. It is a warning, not a
refusal: `ok` and the exit code do not change. Only that pattern counts:
`*.lean` itself, `*.olean` and `*.ilean` are not named. A file outside every
root is not part of the plan, so it gets no warning; run the checklist below
over the whole package anyway.

Checklist item, after any namespace or import transport and before native,
comparator or cluster gates: list the disabled fixtures and run each one's
expected-reason check ([negative tests](negative-tests.md)).

```bash
# from the package directory: every control kept out of the build by its suffix
git ls-files '*.lean.disabled' '*.lean.off' '*.lean.skip'
find . -path ./.lake -prune -o -type f -name '*.lean.*' -print

# each one must still fail, and for the reason it was written for
lake env lean Controls/NoInstance.lean.disabled > out.log 2>&1
python3 -m lean_pkg.lean_diag out.log --error-code lean.synthInstanceFailed --file NoInstance.lean.disabled
```

Lean compiles a file whatever its suffix, so no copy to `.lean` is needed. A
fixture that still says `open OldName` fails with ``unknown namespace `OldName` ``,
which `lean_diag` reports as `wrong-reason`, not `expected`. Fix those files
before an expensive gate, not after it.

## Checklist after a namespace or import transport: every declaration form, one combined import

A transport that moves modules into a new namespace or rewrites their
headers can leave a package in which every module compiles alone and the
whole does not. A field report: after a namespace transport, every module
built and a file-by-file check passed, yet importing the modules together
failed. Two separate negative controls each declared an anonymous
`IsProbabilityMeasure` instance, and Lean gave both the same generated
name in the same namespace. The header-transport collector had qualified
the theorem and `def` names, but it missed the anonymous instances, and
also an `abbrev CellIndex` that occurred 55 times and had moved namespace.
A sparse new output tree also hid unchanged accepted modules from the
import path (item 5). Run the checklist below after any such transport,
before native, comparator, full replay or cluster gates.

This checklist is step 3 of the [cheap-first gate order](receipts.md#cheap-checks-first-gate-order): run it, and the project's own source policy, before any build.

1. **Audit every declaration form, not only theorems and `def`s.** A
   collector keyed on `theorem` and `def` misses the rest. List every
   command that declares a constant or binds a name to a namespace:
   - `def`, `theorem`, `lemma`, `abbrev`, `opaque`, `axiom` (and
     `constant` in sources ported from Lean 3; Lean 4 spells it `opaque`),
     `example` (no constant, but it is elaborated in the current
     namespace);
   - `structure`, `class`, `class inductive`, `inductive`, with the
     constructors, projections and recursors Lean derives from them;
   - `instance` in every form: named, **anonymous** (Lean generates the
     name from the class and its arguments, never from the module, and
     puts it in the current namespace), with a priority, `local`,
     `scoped`, and `attribute [instance]` on an existing constant;
   - each with its modifiers and attributes: `private` (the name embeds
     the module name, so it changes when the module is renamed),
     `protected`, `noncomputable`, `partial`, `unsafe`, `@[simp]`,
     `@[reducible]` and doc comments, which a line-based rewrite must
     carry along;
   - the commands that read the namespace: `scoped notation`, `scoped`
     `macro`, `syntax` and `elab`, `open ... in`, `export`.
2. **Keep each declaration's intended namespace.** Map every old name to
   its new name, and check that nothing is left in the old namespace and
   nothing landed in a namespace it was not meant for. Give the instances
   of separate controls explicit names (`instance instIsProbControlA : ...`)
   or a namespace of their own: two anonymous instances of one class at
   the same arguments get the same name.
3. **Cross-check against complete native ownership, not a source scan.**
   Lean's own environment says which constants each module owns, the
   generated ones included (instance names, `.eq_1`, `match_1`,
   `_proof_1`). List them from the built modules before and after the
   transport, and compare the two lists against the name map of item 2:

   ```lean
   import Lean
   import MyPkg.Aggregate   -- item 4
   open Lean in
   #eval show CoreM Unit from do
     let env ← getEnv
     let mut rows : Array String := #[]
     for (n, _) in env.constants.map₁.toList do
       if let some i := env.getModuleIdxFor? n then
         let m := env.header.moduleNames[i.toNat]!
         if (`MyPkg).isPrefixOf m then rows := rows.push s!"{m} {n}"
     for r in rows.qsort (· < ·) do IO.println r
   ```

   Run it with `lake env lean Ownership.lean > ownership.txt`, outside the
   package's library globs. A constant missing from the map, one still under
   the old namespace, or a module whose count changed for no stated reason
   is a transport defect.
4. **Build one combined import before the expensive gates.** Add an
   aggregate module that imports every module of the package (the
   controls that are meant to compile included), build it, then run
   [`dup_constants`](duplicate-constants.md) on it:

   ```bash
   lake build MyPkg.Aggregate
   python3 -m lean_pkg.dup_constants --repo . MyPkg.Aggregate
   ```

   The two catch different cases. When two modules declare one name with
   different values (two data instances, say), the combined import fails:
   ``import MyPkg.Controls.B failed, environment already contains
   'MyPkg.instHasKNat' from MyPkg.Controls.A``. When the two copies have the
   same type (two proofs of the same `Prop` instance), Lean keeps one copy
   without comparing proofs and the import passes, and only `dup_constants`
   reports the duplicate. Each module compiling alone shows neither.
5. **Resolve import winners the way Lean does.** Lean 4.35's
   `SearchPath.findWithExt` picks the first search-path entry that holds a
   directory (or an `.olean`) named after the module's **root** component,
   and then looks for the module only there. It does not go on to the next
   entry when the leaf file is missing there. A file-by-file winner check
   (the first entry holding the leaf wins) answers differently. With a
   sparse new output tree placed before the accepted build:

   ```text
   LEAN_PATH=new/lib/lean:accepted/.lake/build/lib/lean
   new/lib/lean/MyPkg/Controls/A.olean                  # only the re-emitted module
   accepted/.lake/build/lib/lean/MyPkg/Controls/B.olean # unchanged, accepted
   ```

   A file-existence check says `MyPkg.Controls.B` comes from `accepted/`.
   Lean resolves it to `new/` and fails with ``object file
   '.../new/lib/lean/MyPkg/Controls/B.olean' of module MyPkg.Controls.B
   does not exist``. If `new/` held a stale `B.olean`, Lean would load it
   silently. For each module, the winner is the first entry holding
   `<Root>/` or `<Root>.olean`, and the module must exist in that entry.
   Otherwise put a complete tree first, or none. The same rule is behind
   the `LEAN_PATH` check of the Slurm template
   ([Fresh builds and LEAN_PATH](receipts.md#fresh-builds-and-lean_path)).

Then run the disabled-fixture item above, which covers the controls that
no import reaches.

## Refusals

The plan lists every refusal at once (exit 1), sorted:

- unsafe manifest paths (traversal, absolute), overlapping targets, a root
  whose `from` does not exist;
- a gitlink (submodule) under a root: refused, never silently dropped;
- a symlink whose target is absolute, leaves its root or the repository, does
  not exist, or is (or contains) an excluded file;
- a missing, excluded or symlinked pin; a `lean-toolchain` that is empty or a
  floating channel (`stable`, `nightly`, ...);
- a `lake-manifest.json` package that is not a `git` dependency at a full
  40-hex `rev`, or a `path` dependency outside the included roots;
- a claims file that fails the schema `lean-pkg claims check` applies, a
  claim whose `file` is not included (resolved relative to the claims file's
  output directory), or whose declaration does not occur in that file;
- an `import X` in an included `.lean` file that is neither an included
  module (a `lean_lib` root is a name prefix of `X`, as in Lake), nor
  `Init`/`Std`/`Lean`/`Lake`, nor the namespace of a pinned dependency, nor
  allowed by `allow_unresolved_imports`. An import of a library module whose
  file is excluded is refused with that reason. The check is static and best
  effort: it reads `import` lines with comments and strings masked and does
  not run Lean;
- a stale `allow_private` or `allow_unresolved_imports` entry;
- a malformed `adopted` entry (the [license checks](#licenses-of-adopted-files)
  themselves are warnings);
- a `--report` path inside the source repository.

## Output and exit codes

`--json` prints schema `lean-pkg-extract-plan/1`: `ok`, `source` (`commit`,
`manifest_sha256`), `roots`, `included[]` {to, from, size, sha256, blob, mode,
class, root}, `excluded[]` {from, class, reason}, `allowed_private[]`,
`weakened[]`, `modules[]` {module, file, from, lib}, `unowned_lean_files[]`,
`claims[]` {id, decl, file, module}, `pins` (each with its sha256 and blob;
the toolchain `value`; the manifest's `packages[]`), `adopted[]` {index,
paths, upstream, upstream_commit, upstream_license_file, license, files,
license_file, provenance}, `refusals[]`, `warnings[]`, `totals`. The text
form prints the verdict first and again last; a plan with warnings says
`N warning(s)` in the verdict and lists them after the refusals. Warnings
never change `ok` or the exit code.

The excluded inventory names private paths. It goes only to stdout and to
`--report`; never paste it into anything public.

The output is deterministic: sorted, no timestamps, no host paths. Two runs on
the same commit and manifest are byte-identical.

Exit codes: 0 the plan is clean, 1 refused, 2 usage (argparse; `--repo` not a
git repository or a subdirectory of one).

## Declaration-level extraction ledger

*I ported declarations from several private modules into a new library,
under new namespaces and with a few renames. Can I show the library's
downstream owner that statements and proofs are unchanged?*

`lean-pkg-extract ledger` compares a target library you ported by hand with
the source it came from, declaration by declaration, and writes a ledger
(`lean-pkg-extract-ledger/1`) that names both commits, every module on both
sides, the map you applied and a verdict with SHA-256 hashes for each
declaration. `lean-pkg-extract recheck` re-verifies that ledger later, by
anyone who holds the source. It is the record proposal
[0009](proposals/0009-extraction-ledger.md) specifies. `decls` writes the
target modules for you ([below](#writing-the-target-modules-decls)), and
`--method types` compares the elaborated types too
([`types/1`](#the-comparison-types1)).

```bash
lean-pkg-extract ledger --repo ~/paper --commit HEAD --target ~/newlib \
    --from-module Src/A.lean:New.A --from-module Src/B.lean:New.B \
    --namespace Old.A=New.A --namespace Old.B=New.B \
    --rename Old.A.foo=New.A.bar \
    --drop 'Old.A.aux:exploration only, never used' \
    --ledger ~/ledgers/newlib-1.json
```

```text
ledger: OK (text/1): 5 identical, 1 dropped, 0 differs, 0 new, 0 missing, 0 weakened; 2 context row(s) (0 differ); 0 warning(s); source 0034dfa7..., target 395069d8...; written to ~/ledgers/newlib-1.json
entries (PRIVATE: source names and paths; keep this list out of anything published):
  IDENTICAL theorem   Old.A.foo -> New.A.bar
  IDENTICAL theorem   Old.A.uses_foo -> New.A.uses_foo
  DROPPED   lemma     Old.A.aux -> New.A.aux  (dropped: exploration only, never used)
  IDENTICAL def       Old.A.f -> New.A.f
  IDENTICAL instance  instance#1@Src.A -> instance#1@New.A
  IDENTICAL theorem   Old.B.g -> New.B.g
context rows (each module's other commands, after mapping):
  IDENTICAL Src.A -> New.A
  IDENTICAL Src.B -> New.B
modules (PRIVATE: source names and paths; keep this list out of anything published):
  ...
totals: IDENTICAL 5, DROPPED 1, DIFFERS 0, NEW 0, MISSING 0, weakened 0
ledger: OK (text/1): ...
```

### The input

Everything is a flag, repeatable where it says so; put a long map in a file
and pass `@FILE` (one argument per line; a line `--flag value` is the flag
and the rest of the line, so a reason may hold spaces; blank lines and lines
starting with `#` are skipped). The manifest `lean-pkg-extract/1` of `plan`
is not used.

| Flag | Meaning |
|---|---|
| `--repo SRC --commit C` | the source repository (its top directory) and commit |
| `--target TDIR [--target-commit T]` | the target repository (its top directory) and commit, `HEAD` by default |
| `--from-module SRC/Path.lean:New.Module` | a source module and the target module it was ported to; at least one, each side at most once |
| `--namespace OLD.Ns=NEW.Ns` | a namespace prefix map; the longest prefix wins |
| `--rename OLD.qual=NEW.qual` | a declaration renamed, fully qualified; it takes precedence over `--namespace` |
| `--drop OLD.qual:REASON` | a source declaration left out on purpose, with a one-line reason |
| `--new NEW.qual:REASON` | a target declaration with no source counterpart, declared, with a reason |
| `--allow-differs OLD.qual:REASON` | a difference you disclose (counted as `weakened`); `context@<source module>` for a module's context row |
| `--ledger PATH` | where to write the ledger (required, see below; not with `--emit-script`) |
| `--method text\|types` | the comparison method: `text` (`text/1`, the default) or `types` (`types/1`, [below](#the-comparison-types1)) |
| `--emit-script DIR` | `types/1`, step 1: write the two Lean programs into the new directory DIR, nothing else |
| `--from-output OUT ...` | `types/1`, step 2: the two programs' outputs (source and target, any order) |

Both sides are read from git objects at their commit, never from a working
tree, and neither repository is written. The target module `New.A` is the
file `New/A.lean` in the target repository (or the one file ending in
`/New/A.lean`). The source module's name is its path from the nearest
directory holding a `lakefile.toml` or `lakefile.lean`.

### The comparison: `text/1`

For each module pair, both files are split into declarations: comments and
docstrings stripped, strings and `«»` names kept, a declaration's
attributes, modifiers, `where`, `termination_by` and `decreasing_by` kept
with it. A `mutual` block is one entry, keyed by its sorted names; an
anonymous instance or example is keyed by its position, `instance#k@Module`.
The namespace and rename maps are applied to the source text (whole
identifiers only, relative names resolved in the scope where they stand),
the text is normalized (trailing space and blank lines dropped, common
indentation removed, relative indentation kept) and hashed with SHA-256.

| Verdict | Meaning | Failure? |
|---|---|---|
| IDENTICAL | the mapped source text equals the target text | no |
| DROPPED | absent from the target and listed in `--drop` | no |
| DIFFERS | in both, the texts differ | yes, unless listed in `--allow-differs` (then counted as `weakened`) |
| NEW | a target declaration with no source counterpart | yes, unless declared with `--new` |
| MISSING | absent from the target and not dropped | yes |

`whitespace_only` marks a DIFFERS whose texts are equal once all whitespace
is collapsed: a hint, never a pass. `doc_changed` marks a changed
docstring, for information. Each module pair also gets a context row: its
other top-level commands (`open`, `variable`, `universe`, `set_option`,
`namespace`/`section`/`end`, notation, attributes, ...) in order, mapped and
hashed; a DIFFERS context row fails unless allowed with
`--allow-differs context@<source module>:REASON`. The module header
(`module`, `import`, `prelude`) is not compared: imports change with the
module map, and the build checks them. Module-system modifiers are not
ignored: `public def f` against `def f` DIFFERS.

What `text/1` cannot see (proposal 0009, "Known false negatives"): a short
name that resolves to a different constant after the move, a different
Mathlib under the same text, context from modules not extracted. The
lean-toolchain and the `lake-manifest.json` of both sides are recorded, and
a toolchain or dependency revision that differs is a warning, never a
refusal. That the proofs still check is shown by building the target, not by
the ledger.

### The comparison: `types/1`

*The texts differ only in the proof, or the texts are equal but a short
name may now resolve to another constant: are the statements the
source's?*

`--method types` adds the elaborated type of every declaration both sides
have to the text comparison. lean-pkg never runs Lean, so it takes two
steps, with the same map flags (an `@ARGSFILE` serves both):

```bash
# 1. write the two Lean programs (nothing else; no --ledger needed)
lean-pkg-extract ledger @newlib-args.txt --method types --emit-script ~/ledgers/newlib-types

# 2. run each in its own checkout, clean and at the commit the ledger compares, after building its modules
cd ~/paper  && lake build Src.A Src.B && lake env lean --run ~/ledgers/newlib-types/source.lean > ~/ledgers/source.out
cd ~/newlib && lake build New.A New.B && lake env lean --run ~/ledgers/newlib-types/target.lean > ~/ledgers/target.out

# 3. read both outputs back and write the ledger
lean-pkg-extract ledger @newlib-args.txt --method types \
    --from-output ~/ledgers/source.out ~/ledgers/target.out --ledger ~/ledgers/newlib-2.json
```

```text
ledger: OK (types/1): 4 identical, 1 dropped, 1 differs, 0 new, 0 missing, 0 weakened; 2 context row(s) (0 differ); 0 warning(s); types: 4 compared, 0 differ, 1 cleared a text difference; source 0034dfa7..., target 5b1e7c02...; written to ~/ledgers/newlib-2.json
entries (PRIVATE: source names and paths; keep this list out of anything published):
  DIFFERS (types equal) theorem   Old.A.foo -> New.A.bar  (cleared: the statement's elaborated type is the source's)
  IDENTICAL theorem   Old.A.uses_foo -> New.A.uses_foo
  ...
```

Each program (`source.lean`, `target.lean`; core Lean only, run with
`lake env lean --run`) imports its side's mapped modules and prints, for each
declaration the text comparison pairs (both sides present and named, so not
an anonymous instance or example), a canonical serialization of its
elaborated type: the `Expr` walked structurally, every constant renamed by
the map on the source side (a renamed name and every name under it, so
generated names such as `foo.match_1` and a structure's fields follow; else
the longest namespace prefix), binder names left out, binder annotations
kept, universe parameters numbered by position, private names compared by
their user names. An inductive type, `structure` or `class` brings its
constructors' types. Nothing is pretty-printed: that output is neither
canonical nor round-trip safe. The ledger records each serialization's
SHA-256 as `source_type_sha256` and `target_type_sha256`.

| Text | Types | Verdict shown | Failure? |
|---|---|---|---|
| equal | equal | IDENTICAL | no |
| differs | equal, a theorem, axiom, opaque constant or inductive type | DIFFERS (types equal) | no: the statement is the source's (`types_cleared`); the text difference stays in the ledger and the counts |
| differs | equal, a `def`, `abbrev` or `instance` | DIFFERS (types equal) | yes, unless allowed: the value is not compared |
| differs | differ | DIFFERS (types differ) | yes, unless allowed |
| equal | differ | IDENTICAL (types differ) | yes, unless allowed (`--allow-differs`, then `weakened`): the false negative `text/1` cannot see |

An entry with no constant name (an anonymous instance or example) keeps its
text verdict. MISSING, NEW, DROPPED and the context rows are as under
`text/1`. An `--allow-differs` the types settle (equal types that clear the
difference, or an IDENTICAL text whose types are equal too) is stale.

Each output is one JSON object per line (`lean-pkg-extract-types/1`): a
header with `side`, `script_sha256`, the checkout's `commit`
(`git rev-parse HEAD` where it ran) and `dirty` (`git status`, tracked
files), the Lean build
(`lean`, `lean_githash`) and the declaration count; one line per
declaration, in order; and a final `done` line. `--from-output` reads it
strictly and refuses, with nothing written:

- a file that is missing, not UTF-8, empty, truncated (no final newline or
  no `done` line), or holds any line the program does not write (send only
  its stdout to the file); unknown or missing keys, duplicate JSON keys, a
  newer `lean-pkg-extract-types/N`;
- a declaration line duplicated or out of order, counts that disagree, a
  declaration missing from the environment (build the modules first), kinds
  that do not match its names;
- two outputs of one side, or no output of a side;
- an output written by another program: `script_sha256` is the SHA-256 of
  the program with its own hash left as a placeholder, and lean-pkg
  regenerates the program for the ledger it computes, so an edited program,
  an older map or other commits are refused (emit again and rerun);
- an output run at another commit than the ledger compares, outside a git
  checkout, or in a checkout with uncommitted changes to tracked files;
- a declaration list that does not match the ledger's;
- two different Lean builds (version or githash): an elaborated type is only
  comparable under one toolchain. A Lean that differs from a side's
  `lean-toolchain` pin is a warning.

The ledger stays `lean-pkg-extract-ledger/1` with `method: types/1` and the
optional keys of that method ([formats](formats.md#formats-lean-pkg-owns)):
per entry `source_type_sha256`, `target_type_sha256` (null when the entry is
not compared) and `types_cleared`; per ledger `types` {`lean`,
`lean_githash`, `source_script_sha256`, `target_script_sha256`}. A `text/1`
ledger has none of them and is byte for byte what it was before `types/1`.
`--emit-script DIR` is refused inside SRC or the target (the source program
names private source declarations), when DIR exists, and when its parent does
not; the programs read the compiled environment and run `git rev-parse` and
`git status`, and write only to stdout.

### Where the ledger may live

The ledger names source paths, the source commit, source names and drop
reasons: it is private. `--ledger PATH` is refused inside SRC (its work tree
or git directory; `lean-pkg-extract` leaves SRC unchanged), inside the
target repository (for `decls`, inside `--output DIR`: the ledger never
enters the exported tree), when it is a symlink, when its directory does not exist, and when it already exists. To
keep it beside the source, copy it into SRC's `notes/` by hand, a directory
an export drops.

The ledger is written only when nothing fails and nothing is refused, and
then once: it is evidence, never rewritten. To record again, write a new
file. No gate, page, index or claims command reads it.

### Refusals and failures

Exit 1, nothing written, every reason listed (and the JSON on stdout under
`--json`):

- `--commit` or `--target-commit` that is not a commit; a blob missing from
  the object store (a partial clone);
- `--from-module`: the path missing at C, not a `.lean` file, not a plain
  relative path, mapped twice, a target module that is not a Lean name,
  under a reserved root (`Init`, `Lean`, `Mathlib`, ...), the target of two
  source modules, or with no file in the target;
- `--namespace` or `--rename` that is stale (acts on no source
  declaration), malformed, mapped twice (an overlap of equal length), a
  chain or a cycle (`A=B`, `B=C`), or that makes two declarations land on
  one target name;
- `--drop`, `--new`, `--allow-differs`: stale, named twice, an empty or
  multi-line reason; a dropped declaration still present in the target, or
  still referenced by a kept one (best effort); an allowed difference that
  is identical; a declaration both dropped and allowed to differ;
- a qualified name declared in two source modules; a file that cannot be
  split into declarations (an unclosed comment or bracket, ...), refused
  whole;
- the ledger location (above);
- any failure: MISSING, an undeclared NEW, a DIFFERS or context DIFFERS not
  allowed.

`--repo` or `--target` that is not a git repository, or a subdirectory of
one, is a usage error (exit 2), as is any argparse error; under `--json`
the error is a JSON object.

### Rechecking a ledger

```bash
lean-pkg-extract recheck --ledger ~/ledgers/newlib-1.json --repo ~/paper --target ~/newlib
lean-pkg-extract recheck --ledger ~/ledgers/newlib-1.json --repo ~/paper --target ~/newlib --commit main --target-commit main
```

`recheck` reads the ledger strictly: exact keys at every level, closed
verdicts, duplicate JSON keys refused, totals that count the entries. A
newer `lean-pkg-extract-ledger/N`, or a method this release does not know
(anything but `text/1` and `types/1`; a release before `types/1` refuses a
`types/1` ledger), is refused with "upgrade lean-pkg". It then recomputes the
ledger from its own recorded map:

- at the recorded commits (the defaults: `--commit` is the ledger's source
  commit, `--target-commit` its target commit), every field must match, so
  an edited hash or verdict is refused and the differing fields listed;
- at another `--commit` or `--target-commit` ("is the library still
  faithful to the source at C?"), nothing may fail, and what changed since
  the ledger (declarations whose source or target text moved, verdicts,
  module blobs) is listed as drift.

A ledger whose `target.commit` is null needs `--target-commit`. `recheck`
writes nothing. Exit 0 the ledger holds, 1 refused or failing, 2 usage.

A `types/1` ledger cannot be recomputed without Lean, so `recheck` refuses it
without `--from-output`. `recheck --emit-script DIR` writes the programs for
the commits it rechecks (the recorded ones by default), and
`recheck --from-output source.out target.out` reads their outputs back exactly as
`ledger` does and then compares every field, the type hashes and the `types`
block included (so another Lean build at the recorded commits is drift). A
`text/1` ledger refuses both flags.

### Writing the target modules: `decls`

*I have not ported anything yet. Can lean-pkg write the new library from the
source, with the namespaces and renames applied, and give me the ledger?*

```bash
lean-pkg-extract decls --repo ~/paper --commit HEAD --output ~/newlib \
    --from-module Src/A.lean:New.A --from-module Src/B.lean:New.B \
    --namespace Old.A=New.A --namespace Old.B=New.B \
    --rename Old.A.foo=New.A.bar \
    --drop 'Old.A.aux:exploration only, never used' \
    --scaffold newlib --author 'Ada Lovelace' \
    --ledger ~/ledgers/newlib-1.json
```

```text
decls: OK (text/1): 5 identical, 1 dropped, 0 differs, 0 new, 0 missing, 0 weakened; 2 context row(s) (0 differ); 0 warning(s); source 0034dfa7..., target not committed; 11 file(s) written to ~/newlib, git initialized, nothing committed; ledger written to ~/ledgers/newlib-1.json
entries (PRIVATE: source names and paths; keep this list out of anything published):
  IDENTICAL theorem   Old.A.foo -> New.A.bar
  ...
files written under ~/newlib (11):
  .gitignore
  CITATION.cff
  LICENSE
  LICENSES/Apache-2.0.txt
  New/A.lean
  New/B.lean
  README.md
  REUSE.toml
  lake-manifest.json
  lakefile.toml
  lean-toolchain
public-safety of the staged tree: 0 blocking, 0 warn-only
next (nothing is committed):
  cd ~/newlib
  git add -A && git commit -m 'extracted library'
  lean-pkg-extract recheck --ledger ~/ledgers/newlib-1.json --repo SRC --target ~/newlib --target-commit HEAD
decls: OK (text/1): ...
```

`decls` takes the flags of `ledger` except `--target`, `--target-commit`,
`--new` and `--allow-differs` (a written target has no NEW declaration and
nothing that differs), plus:

| Flag | Meaning |
|---|---|
| `--output DIR` | where to write: absent or an empty directory, not a symbolic link, its parent existing, and (resolved) outside SRC |
| `--scaffold NAME` | also write the package around the modules, then `git init` (nothing committed) |
| `--author TEXT`, `--license Apache-2.0\|MIT` | with `--scaffold`: the CITATION.cff author (default `git config user.name`) and the license, as for `lean-pkg new` |
| `--dry-run` | read and compare in memory, report what would be written, write nothing (the public-safety scan runs only when writing) |

**What a target module holds.** Each mapped source module, read at the
commit from git objects, becomes the file of its target module (`New.A` is
`DIR/New/A.lean`): every top-level command in source order, with the
namespace and rename maps applied exactly as `ledger` applies them to the
source side (so the ledger is IDENTICAL by construction); the declarations
named in `--drop` left out, with their docstrings; header imports of mapped
modules rewritten to the target module; and every comment except docstrings
removed, so no source path or provenance note is carried. An import of a
module of the source that is not extracted is refused: map it too, or
remove the import in the source.

**`--scaffold NAME`.** The pins come from the source commit, from the Lake
package that holds the modules: `lean-toolchain` byte for byte (refused when
there is none), and `lake-manifest.json` with every dependency pin as it is
and only its root package name set to NAME (the source's own name may be
private). `lakefile.toml` names one library per target root and requires
each direct dependency of the manifest at its pinned input revision (a
dependency that is not a git dependency is a warning: add it by hand).
README.md is a skeleton with the lean-pkg credit line; .gitignore,
CITATION.cff, LICENSE, LICENSES/ and REUSE.toml are the package files of
`lean-pkg new`, without its example theorem. Then `git init`, as `new` does;
nothing is committed, so you review first. Without `--scaffold`, DIR holds
the modules only and is not a repository.

**How it is written.** First everything is computed in memory, the ledger
with it: any refusal or failure stops here, and nothing is written. Then
the tree is written into a staging directory beside DIR,
`.<DIR name>.lean-pkg-stage`, and scanned with
[public-safety](commands/public-safety.md) as `repo export` scans its staged tree
(every file; the source repository's patterns file and declared addresses
apply); a blocking hit refuses, with the staging directory removed. Then
(with `--scaffold`) `git init` runs in the staging directory, the ledger is
written (`target.commit: null`: nothing is committed yet), and one
`os.replace` moves the tree into place. A failure on the way removes the
staging directory and the ledger: DIR is left as it was. A staging
directory that already exists (an interrupted run) is refused and left
alone; remove it yourself.

**Rechecking.** Commit DIR as written (`git add -A && git commit`); then
`recheck` with `--target DIR --target-commit HEAD` (and the ledger and
SRC) recomputes the ledger byte for byte. Without
`--scaffold`, `git init` DIR first; if you move the modules into another
package instead, record a new ledger there with `ledger`. That the target
builds is shown by `lake build`, not by the ledger.

**Refusals** (exit 1, nothing written, every reason listed): every refusal
of `ledger` that applies (the map, the commit, the ledger location, a file
that cannot be split, a dropped declaration still referenced by a kept
one); `--output` non-empty, a file, a symbolic link, inside SRC (also
through a symbolic link), or with no parent directory; the staging
directory already present; a public-safety blocking hit; an import of an
unextracted module of the source; with `--scaffold`, a NAME `lean-pkg new`
refuses, no author, no `lean-toolchain` in the source, or DIR inside an
existing Lake package; a write that fails part way. `--author` or
`--license` without `--scaffold` is a usage error (exit 2).

### Output

Text output prints the verdict first and again last; the entries and modules
lists carry "PRIVATE: source names and paths". `--json` prints the ledger
object (sorted keys) plus `refusals`, `failures` and `written` (and, for
`recheck`, `drift`; with `--emit-script`, `programs` {dir, files, count,
written, source_script_sha256, target_script_sha256}, where `failures`
lists the text failures the types may still settle; for `decls`, `output` {files, scaffold, dry_run,
written, git_init, committed, object_format} and `public_safety` {ok,
block_hits, warn_hits}, null when no scan ran); the file at `--ledger`
holds the ledger object alone.
The ledger's keys are documented in proposal
[0009](proposals/0009-extraction-ledger.md#the-ledger-format-lean-pkg-extract-ledger1);
each context row also carries `reason` (the allowed difference, or null).

## What comes next

`write --output DIR` will materialize the plan into a new directory and verify
every file against its recorded blob, through the same staged, scanned,
one-step write that `decls` uses (`src/lean_pkg/extract_write.py`). `lean-pkg extract ...` as a subcommand of
`lean-pkg` is left to the extension-dispatch work; use the `lean-pkg-extract`
script. For excluded `*.lean.*` files (disabled controls the import check
does not read) the plan warns; see
[Disabled negative fixtures](#disabled-negative-fixtures-after-a-namespace-or-import-transport).
After a namespace or import transport, run the
[checklist for every declaration form](#checklist-after-a-namespace-or-import-transport-every-declaration-form-one-combined-import).
For consolidating several registered packages into one, see
[Consolidate several checkpoint packages into one](paper-companion.md#consolidate-several-checkpoint-packages-into-one).
