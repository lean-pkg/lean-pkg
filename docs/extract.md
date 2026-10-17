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
excluded (or when it lies outside every root), it is only a line in the
excluded list: the module inventory, `unowned_lean_files` and the import check
read `*.lean` files only, so a stale `open` or `import` in it goes unreported.

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

## What comes next

`write --output DIR` will materialize the plan into a new directory and verify
every file against its recorded blob. `lean-pkg extract ...` as a subcommand of
`lean-pkg` is left to the extension-dispatch work; use the `lean-pkg-extract`
script. A plan warning that names excluded `*.lean.*` files (disabled
controls the import check does not read) is a possible small follow-up. For consolidating several registered packages into one, see
[Consolidate several checkpoint packages into one](paper-companion.md#consolidate-several-checkpoint-packages-into-one).
