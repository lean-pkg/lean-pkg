# 0009: Declaration-level extraction with a rename map and an extraction ledger

Status: accepted. Author: Claude, the lean-pkg operations agent, drafting for Le Chen. Created: 2026-10-26.
Decided: accepted 2026-10-26 by Le Chen, as written, with the recommended answers to open questions 1 to 8
(and to the one left to S4). Implemented in: (release), by #361 (S1, the scanner spans), #367 (S2, `ledger`
and `recheck`, `text/1`), #373 (S3, `decls` and `--scaffold`), #377 (S4, `types/1`) and #PR_EX5 (S5,
`recheck --public-summary`); tracked in [#329](https://github.com/lean-pkg/lean-pkg/issues/329).

## Summary

An author who moves declarations from several private modules into a new library, under new namespaces
and with a few renames, gets a tool-made record that the library is faithful to the source at a named
commit, and a command that re-checks that record later. Three verbs join the existing `lean-pkg-extract`
tool: `ledger` records a ledger for a target the author ported by hand, `decls` writes the target modules
and then records, and `recheck` re-verifies a ledger against the source and the target. The input is
repeatable flags (a module map, a namespace map, renames, deliberate drops, declared new declarations),
optionally in an `@argsfile`; the manifest `lean-pkg-extract/1` does not change. The only new format is
the ledger, `lean-pkg-extract-ledger/1`, which is evidence: written once, never rewritten, read by every
later release. Its first comparison method, `text/1`, compares comment-stripped, renamed declaration text
by SHA-256; a later method, `types/1`, compares elaborated types. The ledger never enters the exported
tree, and no gate, page or index reads it. This proposal answers issue
[#329](https://github.com/lean-pkg/lean-pkg/issues/329); no code changes until it is accepted.

## Motivation

- **The field report (#329).** The reporter extracted reusable declarations from several existing project
  modules into a new library, gave each module a new namespace and renamed a few declarations. The
  comparator checks Challenge against Solution inside the new library, but nothing checks the new library
  against the source it came from. To give the downstream owner evidence that statements and proofs are
  unchanged, the reporter wrote a separate per-declaration ledger: strip comments, split declarations,
  apply the recorded renames, compare statement and proof text, report IDENTICAL / DROPPED / DIFFERS /
  NEW with source and target SHA-256. Result: 54 identical, 3 dropped by design, 0 failures. They asked
  for first-class support, the ledger kept out of the exported tree because it names source paths that
  may be private, and an elaborated-type comparison as the stronger version.
- **What lean-pkg offers today.** `lean-pkg-extract plan` (docs/extract.md) works on whole files: every
  file of a planned public repository is traced to a blob of one commit. It has no notion of a
  declaration, a namespace move or a rename, and its write mode is still to come (two-tracks gap 17,
  `docs/design/two-tracks.md:313`). `repo export` moves a registered package as a whole. The manual
  "Checklist after a namespace or import transport" (`docs/extract.md:270-379`) is the closest thing, and
  it is a checklist, not a record.
- **Why this is a proposal.** The new verbs alone would go straight to a pull request
  (docs/proposals/README.md, "Anything else (a new command or flag ...)"). The ledger is what makes this
  a proposal: it is a new lean-pkg on-disk format that `recheck` reads back and that is handed to a
  downstream owner as evidence, and docs/formats.md:36 says "A new version of any lean-pkg format is a
  schema change: it starts as a design proposal". It is not a report in the sense of formats.md
  ("Reports", line 70: output "never read back"), because `recheck` reads it back.

## Design

### Owner and commands

The verbs belong to `lean-pkg-extract` (`src/lean_pkg/extract.py`), the read-only, commit-pinned,
private-aware extraction tool. `lean-pkg extract ...` reaches them once extension dispatch lands
([1.0 command tree](../design/1.0-command-tree.md), "Extensions"); that is not implemented today
(docs/extract.md:434-436), so the spelling below is the script's.

Record a ledger for a hand-ported target (stage S2):

```text
lean-pkg-extract ledger --repo SRC --commit C --target TDIR [--target-commit T]
    --from-module SRC/Path.lean:New.Module ...   (repeatable, at least one; the issue's spelling)
    [--namespace OLD.Ns=NEW.Ns ...]              (prefix map; longest prefix wins; an equal-length overlap is refused)
    [--rename OLD.qual=NEW.qual ...]             (fully qualified; applied after --namespace)
    [--drop OLD.qual:REASON ...]                 (split at the first ':' outside «»; a one-line reason is required)
    [--new NEW.qual:REASON ...]                  (target declarations with no source, declared)
    [--allow-differs OLD.qual:REASON ...]        (a disclosed difference, listed as `weakened`)
    --ledger PATH [--json] [@ARGSFILE]
```

Write the target modules and record (stage S3):

```text
lean-pkg-extract decls --repo SRC --commit C --output DIR [--scaffold NAME --author TEXT --license SPDX]
    (the same map flags) --ledger PATH [--dry-run] [--json]
```

Re-check (stage S2):

```text
lean-pkg-extract recheck --ledger PATH --repo SRC --target TDIR [--commit C] [--target-commit T] [--json]
```

- **Input.** Repeatable flags, and `@ARGSFILE` through argparse `fromfile_prefix_chars`, so a long map
  lives in a file the author keeps. The manifest `lean-pkg-extract/1` is not extended: it is strict and
  refuses unknown keys (`_keys`, `extract.py:216`), so a `declarations` key would be refused by every
  older tool and would need `/2`, since `roots` would become optional. One input format per granularity;
  the ledger records the resolved map, so a recheck needs nothing else.
- **Commits.** `recheck --commit` defaults to the ledger's `source.commit`; passing a newer commit asks
  "is the library still faithful to the source at C?". `--target-commit` defaults to `HEAD`. Both sides
  are read from git objects at their commit (`_git` :139, `resolve_commit` :146, `ls_tree` :154,
  `read_blobs` :169), so neither working tree matters and neither is written. `decls` before the first
  commit of DIR records `target.commit: null`; a recheck of such a ledger requires `--target-commit`.
- **Exit codes and output** follow extract.py: 0 clean; 1 refused or failing (every reason listed,
  verdict first and again last); 2 usage, also under `--json` (`_Parser.error`, `extract.py:1042`).
  Output is deterministic: no timestamps, no host paths, keys sorted.

### The ledger format `lean-pkg-extract-ledger/1`

Strict JSON, keys sorted, written by the tool only.

```json
{"schema": "lean-pkg-extract-ledger/1", "method": "text/1", "ok": true,
 "source": {"commit": "<40 hex>", "lean_toolchain": "leanprover/lean4:v4.x.y", "lake_manifest_sha256": "<hex>",
            "modules": [{"path": "Src/A.lean", "module": "Src.A", "blob": "<oid>", "sha256": "<hex>"}]},
 "target": {"commit": "<40 hex> or null", "lean_toolchain": "...", "lake_manifest_sha256": "<hex>",
            "modules": [{"path": "New/A.lean", "module": "New.A", "blob": "<oid>", "sha256": "<hex>"}]},
 "map": {"modules": [{"from": "Src/A.lean", "to": "New.A"}],
         "namespaces": [{"from": "Old.A", "to": "New.A"}], "renames": [{"from": "...", "to": "..."}],
         "drops": [{"decl": "...", "reason": "..."}], "new": [{"decl": "...", "reason": "..."}],
         "allow_differs": [{"decl": "...", "reason": "..."}]},
 "entries": [{"kind": "theorem", "source": "Old.A.foo", "target": "New.A.foo",
              "source_module": "Src.A", "target_module": "New.A", "verdict": "IDENTICAL",
              "source_raw_sha256": "<hex>", "source_sha256": "<hex>", "target_sha256": "<hex>",
              "whitespace_only": false, "doc_changed": false, "reason": null}],
 "context": [{"source_module": "Src.A", "target_module": "New.A", "verdict": "IDENTICAL",
              "source_sha256": "<hex>", "target_sha256": "<hex>"}],
 "warnings": [],
 "totals": {"IDENTICAL": 54, "DROPPED": 3, "DIFFERS": 0, "NEW": 0, "MISSING": 0, "weakened": 0}}
```

| Key | Required | Meaning |
|---|---|---|
| `schema` | yes | `lean-pkg-extract-ledger/1` |
| `method` | yes | the comparison method: `text/1` (S2), later `types/1` (S4) |
| `ok` | yes | true when no entry or context row fails |
| `source`, `target` | yes | commit (target may be null, see above), toolchain pin, lake-manifest hash, and every module involved with its path, module name, git blob oid and SHA-256 of its raw bytes |
| `map` | yes | the resolved input: modules, namespaces, renames, drops, declared new, allowed differences, each with its reason where one is required |
| `entries` | yes | one row per declaration, keyed by qualified name (see the text method for anonymous instances and `mutual` blocks) |
| `context` | yes | one row per mapped module for its non-declaration top-level commands (`variable`, `open`, `universe`, `set_option`, local notation and instances) |
| `warnings` | yes | e.g. a toolchain or lake-manifest mismatch between the two sides |
| `totals` | yes | a count per verdict, and `weakened` |

**What each hash covers** (SHA-256 of UTF-8):

- `source_raw_sha256`: the declaration's normalized text before any map is applied. It detects drift in
  the source even when the map is later edited.
- `source_sha256`: the normalized source text after the namespace and rename maps. Directly comparable
  with `target_sha256`.
- `target_sha256`: the normalized target text.
- Module level: the git blob oid and the SHA-256 of the raw bytes of every module on both sides, plus the
  `lean-toolchain` text and the SHA-256 of `lake-manifest.json` on both sides. The pins catch "same text,
  different Mathlib"; a mismatch is a warning, not a refusal.

**Evidence, not state.** The ledger follows the receipt rule of docs/formats.md (lines 29 to 34):
readers accept every version forever, the tool never rewrites a ledger, re-recording writes a new file,
and there is no migration. An existing `--ledger PATH` is refused.

**Methods are versioned separately.** `text/1` and `types/1` are method markers inside a `/1` ledger. A
change to normalization is a new method version (`text/2`), not a new ledger version. A reader that meets
an unknown method refuses the ledger and names the upgrade. Keys a later method adds (the type hashes of
S4) are optional keys under the additive rule, so the ledger stays `/1`.

**Name.** It is the "extraction ledger" in the docs and in the file. It is never the claims ledger:
`claims-ledger/1` (`LEDGER_SCHEMA`, `src/lean_pkg/ledger.py:44`) is a different report, and the new
constant is named so the two cannot be confused (`EXTRACT_LEDGER_SCHEMA`).

**Docs.** S2 adds an owned-formats row to docs/formats.md. `tests/test_formats.py:402` requires every
`name/N` constant in `src/` to appear on that page, so the constant cannot land without its row; the
method strings `text/1` and `types/1` match the same pattern (`test_formats.py:406`) and are named on the
page too.

### Verdicts

| Verdict | Meaning | Failure? |
|---|---|---|
| IDENTICAL | `source_sha256 == target_sha256` | no |
| DROPPED | a source declaration in a mapped module, absent from the target, listed in `--drop` | no |
| DIFFERS | present on both sides, hashes unequal | yes, unless listed in `--allow-differs` (then counted under `weakened`) |
| NEW | a target declaration with no source counterpart | yes, unless declared with `--new` |
| MISSING | a source declaration absent from the target and not dropped | yes |

`whitespace_only: true` marks a DIFFERS whose whitespace-collapsed token hash matches: a hint, never a
pass. A context row is IDENTICAL or DIFFERS; a DIFFERS context row fails unless allowed, and it is the
main guard against the false negatives below. A changed docstring sets `doc_changed` and is informational.

### The text method `text/1`

The functions live in `src/lean_pkg/lean_scan.py`, after `declarations` (:178), which keeps its behavior
(page and claims depend on it).

1. **`strip_comments(text)`.** Remove `--` comments and nested `/- -/` comments, docstrings `/-- -/` and
   `/-! -/` included. Keep string and character literals and «» names, as a sibling of `_mask` (:83) that
   blanks comments only.
2. **`decl_spans(text)`.** A span starts at each top-level command whose indentation is at most that of
   the current declaration's keyword. Boundary commands are the `KEYWORDS` (:18) plus `example`,
   anonymous `instance`, `attribute`, `open`, `variable`, `universe`, `set_option`,
   `notation`/`infix*`/`macro`/`syntax`/`elab`/`macro_rules`, `namespace`/`section`/`end`/`mutual`, `#...`,
   `export`, `deriving instance` and `initialize`. `where`, `deriving`, `termination_by` and
   `decreasing_by` stay with their declaration. Each span is keyed by its qualified name, with namespace
   tracking shared with `declarations()` (a test asserts the two agree). A `mutual ... end` block is one
   entry keyed by its sorted names; an anonymous instance is keyed `instance#k@Module` (k its ordinal in
   the module) and its header is shown. Other commands go to the module's context rows. A file whose
   comment or bracket structure cannot be read is refused, never half split.
3. **`apply_map(span, ...)`.** Tokenize identifiers outside strings (starting from `port_modules._TOKEN`,
   `src/lean_pkg/port_modules.py:56`). A token equal to a fully qualified old name becomes the new
   qualified name; a token equal to an old name relative to the open namespaces (from the scanner's scope
   stack) becomes the new name relative to the target's scope; a token that starts with an old namespace
   prefix and `.` gets the namespace map; `namespace X` and `open X` lines in context rows get the
   namespace map. Renames apply after namespaces and simultaneously; a chain or cycle (`A=B`, `B=C`) is
   refused as ambiguous.
4. **`normalize(span)`.** Strip trailing whitespace, drop blank lines, remove the indentation common to
   all lines, keep relative indentation (Lean layout is semantic). A second hash with all whitespace
   collapsed feeds `whitespace_only`.

**Known false positives** (DIFFERS although equivalent): reformatting and reindentation; `lemma` versus
`theorem`, `ℕ` versus `Nat`; reordered attributes; `public` or `@[expose]` added for the module system,
`private` removed; different `open` lines, so that names are written differently in ways the map does not
cover; proof golfing. Mitigation: `--allow-differs` with a reason, and `types/1`.

**Known false negatives** (IDENTICAL although different): a changed `variable`, `open`, `universe`,
`set_option`, local notation or local instance, or an `attribute [instance]` elsewhere (context rows catch
these within mapped modules, not across imports); a different Mathlib or toolchain under the same text
(pins recorded, mismatch warned); a short name that resolves to a different constant after the namespace
move; auto-bound implicits; macros and notation from modules not extracted; instance priorities set
elsewhere. Only `types/1` closes the statement-side gaps; that the proofs still check is shown by building
the target, not by the ledger.

### The elaborated-type method `types/1` (stage S4)

- **Driver.** A rename-aware Lean snippet next to `LEAN_SNIPPET` (`src/lean_pkg/comparator_types.py:65`),
  with the same mechanics: `driver_script` (:592), `driver_config` (:643), `--emit-script` (`emit_path`
  :679) for Slurm, `run_local` (:696), `refuse_non_elan` (:666). For each declaration it substitutes
  constants per the map with `Expr.replace` on `.const n ls` (prefix-mapping generated names such as
  `foo.match_1` and `_proof_k`), prints with `pp.all` and `pp.fullNames`, and prints `Expr.hash` as a
  cross-check; the value (proof term) optionally the same way. Nothing in `src/` stores a raw type hash
  today; this stage adds one.
- **Ledger.** Entries gain `source_type_sha256` and `target_type_sha256` (and `value_*` when requested),
  optional keys within `/1`, under `method: types/1`.
- **Running it.** The source side runs on a checkout at C and the target side on a checkout at T: two
  builds. Differing toolchains are refused, because a `pp.all` print is only comparable under one
  toolchain; pretty-printed default output is not hashed, since it is not canonical or round-trip safe.
- **Verdict.** A text DIFFERS whose type hashes are equal is shown as `DIFFERS (types equal)`; under
  `--method types` it clears the failure for the statement (the note after the open questions).
- **Ownership cross-check** (optional): list the constants each module owns natively, as item 3 of the
  docs/extract.md checklist does (:313), to catch generated names and the anonymous instances the text
  method keys by ordinal.

### Privacy

- **The ledger is private by default.** It names source paths, the source commit, source module names and
  drop reasons, any of which may be private.
- **`--ledger PATH` is required**, with no default, and is refused when it resolves inside the target tree
  (TDIR, or `--output DIR`); inside SRC's work tree or git directory (the rule and wording of `--report`,
  `extract.py:1092-1099`, which keeps SRC read-only); when it is a symlink or an existing non-regular
  file; or when it already exists.
- **The exported tree** (S3) is staged beside DIR and scanned with public-safety before anything is moved
  into place, as `repo export` does (`ps.scan(..., export=True, ...)`, `src/lean_pkg/fresh_repo.py:361`);
  a blocking hit refuses and nothing is written. Target modules carry no source path or provenance comment
  by default. `public_safety.Scanner` (:360) takes a registry package dict; S3 verifies that a minimal
  `{"name": NAME}` serves, since there is no registry record.
- **An optional public summary** (S5): `recheck --public-summary PATH` writes
  `lean-pkg-extract-ledger-summary/1`, a report never read back as evidence, with the target commit and
  module names, per entry the target name, verdict and `target_sha256`, `source_sha256` as a hash
  commitment (a holder of the source can verify it; it tells anyone else nothing), and the totals. It
  leaves out source paths, source names (a rename reveals the old name), drop reasons and the source
  commit (unless `--include-source-commit`). It is scanned with public-safety before it is written and may
  live inside the target.
- **Text output.** Lines that list source paths carry "PRIVATE: keep this list out of anything
  published", as `render` does (`extract.py:975`).

### Stages

Each stage is its own green pull request with its upgrade-record entry
(`docs/upgrades/<version>.yaml`, kinds from `RECORD_KINDS`, `src/lean_pkg/upgrade.py:63`).

| Stage | What | Size | Upgrade record |
|---|---|---|---|
| S0 | This proposal (docs only) | XS | `no_author_change` |
| S1 | `strip_comments`, `decl_spans`, `normalize`, `apply_map` in `lean_scan.py`; library only, no CLI | M | `no_author_change` (internal) |
| S2 | `ledger` and `recheck` with `text/1` (new `extract_ledger.py`, subparsers in `extract.py`'s `parser()` :1048 and `main` :1118); formats.md row; docs/extract.md section; docs/index.md route | M | `kind: command` (both verbs) and `kind: format` (`lean-pkg-extract-ledger/1`), `breaking: false` |
| S3 | `decls` write mode and `--scaffold`, sharing a write helper (staging, public-safety, `os.replace`, "DIR empty, outside SRC") with the file-level `write` of gap 17; `scaffold.files()` (:337) split so package files come without the example theorem, `new` byte-identical; pins copied from the source commit; `git init`, no commit | M/L | `kind: command` (`decls`) |
| S4 | `types/1` (`--method types`, `--emit-script`, `--from-output`) | L | `kind: command` (flags) and `kind: format` (optional keys within `/1`) |
| S5 | `--public-summary` (optional) | S | `kind: command`; the summary schema joins "Reports" in formats.md |

S0 to S2 meet the reporter's need, since they have a hand-ported target. When S3 lands, two-tracks.md
row 17 says "declaration-level write: #329; file-level `write`: still open"; it does not claim gap 17
closed.

### Tests

S1: every declaration form of the docs/extract.md checklist (item 1, :286), anonymous instances,
`mutual`, `where`, `termination_by`; a column-0 `open X in` inside a proof, which must not split; a
comment containing `theorem`, strings containing `--`, «» names; agreement with `declarations()` on the
existing fixtures; an unreadable file refused.

S2, in `tests/test_extract_ledger.py` with a fixture source repository and a target repository built as
the `src` fixture of `tests/test_extract_plan.py:99`:

- **Happy paths:** IDENTICAL, DROPPED, declared NEW, allowed DIFFERS; a repeat run is byte-identical;
  source and target workspaces unchanged (as `test_extract_plan.py:279`); recheck match, drift and fail.
- **One refusal test each:** `--commit` not a commit; `--repo` not git or a subdirectory (exit 2); a
  missing blob (partial clone); `--from-module` path missing at C, not `.lean`, duplicated source or
  target, target not a Lean name, reserved root (`scaffold.RESERVED_MODULES`, :76); `--namespace` stale
  or an ambiguous overlap; `--rename` stale, duplicate, colliding with another target name, a chain or a
  cycle; `--drop` stale, empty or multi-line reason, dropped yet present in the target, dropped yet still
  referenced by a kept declaration (best effort); `--new` or `--allow-differs` stale; a duplicate
  qualified name in the source; an unsplittable file; `--ledger` inside the target, inside SRC, a symlink,
  or existing; an undeclared NEW, a MISSING or an un-allowed DIFFERS (exit 1, no ledger written, JSON on
  stdout); recheck of a ledger that is not strict JSON, has an unknown schema, a newer `/N` ("upgrade
  lean-pkg"), an unknown method or an invalid map, names a missing commit, or has `target.commit: null`
  without `--target-commit`; usage errors under `--json`.
- **Not a refusal:** a pin mismatch warns and leaves the exit code unchanged.
- **Contract:** `tests/test_formats.py` passes; `tests/test_verdict_first.py` (the first line is the
  verdict).

S3: a written ledger is all IDENTICAL by construction; the target builds in core Lean (guarded as the
existing Lean-needing tests are); `--dry-run` writes nothing; refusals for `--output` existing and
non-empty or inside SRC, an import of an unextracted local module, a public-safety hit (nothing written,
staging removed), invalid `--scaffold` name or author (the `new` refusals), and a ledger refusal leaving no
DIR behind. S4: the driver config is pure and unit-tested; refusals for a toolchain mismatch, a non-elan
lake, an output schema or commit mismatch, and a `--from-output` declaration set that does not match the
ledger. S5: the public-safety refusal, and no source string in the summary.

## Compatibility and migration

- **Additive.** A new format and three new verbs; nothing existing changes. `lean-pkg-extract plan`, its
  manifest `lean-pkg-extract/1` and its report `lean-pkg-extract-plan/1` are untouched. The only change to
  an existing module's visible behavior is in S3: `scaffold.files()` is split, and `tests/test_new.py`
  keeps `new`'s output byte-identical.
- **An older lean-pkg** has no `ledger`, `decls` or `recheck` verb: argparse refuses them as a usage error
  (exit 2). No older release reads a ledger. A release that knows `text/1` only refuses a `types/1`
  ledger, naming the upgrade, rather than checking half of it.
- **No migration command.** The ledger is evidence: never rewritten, every version read forever.
- **Goldens.** `lean-pkg-extract` has no golden help or JSON-key files in `tests/golden` today; the stages
  add none unless the release checklist asks for them.

## Trust model

No, for S0 to S5 as proposed. No gate, `promote`, `publish`, `page`, `index check` or claims command
reads the ledger, and no registry record is written. The ledger is evidence a person hands to a
downstream owner, who judges it; lean-pkg's own trust levels are unchanged.

- **What a ledger shows.** That, under the recorded map and method, each source declaration's text (or,
  with `types/1`, elaborated type) equals the target's, at the two recorded commits and pins. It shows
  nothing about whether the target builds (the build and the receipts do that), and with `text/1` nothing
  about the known false negatives above.
- **A dishonest or mistaken author** could hand over a hand-edited ledger; `recheck` against the source
  and target commits is the defence, and it is why the ledger records every input it needs. A mistaken
  `--allow-differs` or `--drop` is disclosed with its reason and counted in the verdict line.
- **Any later use** of the ledger by a gate, the page (for example "extracted from a private source;
  ledger: N identical") or claims provenance is a separate trust-model proposal (open question 8).

## Alternatives

- **Extend the manifest** with a `declarations` block. Rejected for now: the manifest is strict, so every
  older tool would refuse it, and `roots` would have to become optional, which is `lean-pkg-extract/2`.
  It can come later together with file-level write mode, through its own proposal (open question 2).
- **Own it in `repo export`**, as the issue suggests (`--from-modules ... --rename ... --drop`).
  Rejected: `repo export` (`do_fresh_repo`, `src/lean_pkg/fresh_repo.py:277`) needs a registered package,
  exports the gate-bound tree as is, relocates the registry record (:442) and clears its evidence. A new
  library made from parts of a package is neither a move nor a relocation. Its staging pattern
  (:323-428) and public-safety call are reused.
- **Own it in `new`.** Rejected: `new` (`do_new`, `src/lean_pkg/scaffold.py:419`) only scaffolds and
  reads no source. Its file templates are reused through `decls --scaffold`.
- **Text only, forever.** Rejected as the end state: the false negatives above (short names resolving
  differently after a move, context outside mapped modules) are exactly what the issue's "stronger
  version" asks to close. `text/1` ships first because it needs no build and meets the reporter's case.
- **Do nothing**, leaving the hand-written ledger. Rejected: the reporter needed one, the manual checklist
  in docs/extract.md already describes the failure modes, and a ledger in each author's own format cannot
  be rechecked by anyone else.

## Open questions

Each with a recommendation; all are for the maintainer's decision.

1. **Owner: `lean-pkg-extract` verbs, or `new --from-module` / `repo export --from-modules` as the issue
   says?** Recommendation: `lean-pkg-extract`. It is already the read-only, commit-pinned, private-aware
   extraction tool; `repo export` would carry relocation semantics this must not have; `new` stays a
   scaffold, and its templates are reused through `--scaffold`.
2. **Input: flags plus `@argsfile`, or a manifest key?** Recommendation: flags plus `@argsfile` now. A
   `declarations` block in `lean-pkg-extract/2` can come later, with file-level write mode, through its
   own proposal.
3. **Are NEW and DIFFERS failures?** Recommendation: yes, unless declared with a reason. Declared ones are
   listed under `weakened` (DIFFERS) or `new` (NEW) and counted in the verdict line.
4. **Ledger location: refused inside SRC, or allowed there (it is private)?** Recommendation: refuse
   inside SRC to keep the read-only contract, and document copying it into SRC's `notes/` (an internal
   directory that exports drop) as a manual step. Never inside the target, except the public summary.
5. **Evidence or state?** Recommendation: evidence. Never rewritten, every version read forever, no
   migration.
6. **Should `text/1` ignore module-system modifiers (`public`, `@[expose]`)?** Recommendation: no by
   default; add a disclosed `--ignore-modifier public` flag only if field reports ask for it.
7. **Should `decls` commit?** Recommendation: no. `git init` only, as `new` does (`scaffold.py:458`), so
   the author reviews first.
8. **Later integration** (a page line such as "extracted from a private source; ledger: N identical", or
   claims provenance)? Recommendation: out of scope here; a separate trust-model proposal.

The design also leaves one decision to S4, recorded here so it is not lost: whether `DIFFERS (types
equal)` clears the failure for a statement under `--method types`. Recommendation: yes, for the
statement; the text difference stays visible.

## AI assistance

Drafted by Claude (Anthropic), the lean-pkg operations agent, from issue #329 and a read-only design plan
written by a Claude planning subagent; the code and documentation anchors were checked against the base
commit (`f977122`) and corrected where the plan's were stale. To be reviewed and decided by the
maintainer.
