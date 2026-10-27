---
title: "Plan: hygiene, the 1.0 format freeze and the next features (2026-10)"
---

[&larr; lean-pkg documentation](../index.md)

*Status: in progress. Written 2026-10-26 after the 0.21.0 release, from three read-only surveys of the
repository (pending work in the design docs; test-suite and CI health; code structure and command-line
consistency). Rows 10 to 15 come from issues filed the same day (#325 to #330). Each item lands as its
own pull request that names its row here; this page is updated as rows land.*

## The items

| # (issue) | Item | Evidence | Size | PR | Status |
|---|---|---|---|---|---|
| 1 [#310](https://github.com/lean-pkg/lean-pkg/issues/310) | Tests leave `__pycache__` in `src/`; a session-end check refuses it | `tests/test_bootstrap_collaborator.py` builds the stub's environment from scratch, so `PYTHONPYCACHEPREFIX` never reaches `lean_pkg.cli` | S | A [#323](https://github.com/lean-pkg/lean-pkg/pull/323) | done |
| 2 [#311](https://github.com/lean-pkg/lean-pkg/issues/311) | CI hardening: job time limits and a per-test timeout, pip cache, cancel superseded runs, Python 3.13 and 3.14 in the matrix | `.github/workflows/ci.yml` has no `timeout-minutes` and tests 3.11 and 3.12 only | S | A [#323](https://github.com/lean-pkg/lean-pkg/pull/323) | done |
| 3 [#312](https://github.com/lean-pkg/lean-pkg/issues/312) | Tests that skip everywhere run again: `jsonschema` and `setuptools` in the `test` extra | `tests/test_formalization.py` (4 skips), `tests/test_adapters.py` `importorskip("setuptools")` | S | A [#323](https://github.com/lean-pkg/lean-pkg/pull/323) | done |
| 4 [#313](https://github.com/lean-pkg/lean-pkg/issues/313) | Loose ends: the `public-safety` target's old `lean-pkg init` spelling; the `tomllib` fallback that cannot run on Python 3.11+; `make test` says when it falls back to a serial run | `Makefile`, `src/lean_pkg/build_scope.py` | XS | A [#323](https://github.com/lean-pkg/lean-pkg/pull/323) | done |
| 5 [#314](https://github.com/lean-pkg/lean-pkg/issues/314) | A Python linter: `ruff check` in `make check` and CI, pinned like ShellCheck | no linter configured anywhere | S-M | D [#333](https://github.com/lean-pkg/lean-pkg/pull/333) | done |
| 6 [#315](https://github.com/lean-pkg/lean-pkg/issues/315) | The format freeze, [1.0 command tree](1.0-command-tree.md) section 6 items 1 to 3: a schema marker for the lower-tier receipt, a `v` key on event lines, `docs/formats.md` | section 6; no marker in `src/` | S-M | B [#324](https://github.com/lean-pkg/lean-pkg/pull/324) | done |
| 7 [#316](https://github.com/lean-pkg/lean-pkg/issues/316) | Command-line consistency: the commit flags only on write commands; `--repo` for the package checkout everywhere, `--project` kept as a permanent alias | `add_json()` in `cli.py`; `receipt comparator`, `claim-axioms`, `dup-constants` (`lean-exercise --project` names a Mathlib-providing project, not the checkout: kept) | S | C [#322](https://github.com/lean-pkg/lean-pkg/pull/322) | done |
| 8 [#317](https://github.com/lean-pkg/lean-pkg/issues/317) | `cli.py` split: the shared core (`Fail`, git helpers, registry IO, events) moves to `core.py`; gate, audit and the parser follow | `cli.py` is 9442 lines; 36 modules import it as `core` | L | E: stage 1 [#345](https://github.com/lean-pkg/lean-pkg/pull/345), stage 2 [#351](https://github.com/lean-pkg/lean-pkg/pull/351), stage 3 [#353](https://github.com/lean-pkg/lean-pkg/pull/353) | done |
| 9a [#318](https://github.com/lean-pkg/lean-pkg/issues/318) | A compute receipt from a CI runner (GitHub Actions first) | [sites and CI receipts](sites-and-ci-receipts.md) section 4 | L | F: proposal 0008 [#331](https://github.com/lean-pkg/lean-pkg/pull/331), accepted [#337](https://github.com/lean-pkg/lean-pkg/pull/337); S1 [#338](https://github.com/lean-pkg/lean-pkg/pull/338), S2 [#348](https://github.com/lean-pkg/lean-pkg/pull/348), S3 [#355](https://github.com/lean-pkg/lean-pkg/pull/355), S4 [#363](https://github.com/lean-pkg/lean-pkg/pull/363) (follow-up `publish --attested` [#364](https://github.com/lean-pkg/lean-pkg/pull/364)), S5 [#358](https://github.com/lean-pkg/lean-pkg/pull/358) | implemented (a live run in a public sample repository still to do) |
| 9b [#319](https://github.com/lean-pkg/lean-pkg/issues/319) | Proposal [0002](../proposals/0002-committed-docs-only-opt-in.md): the committed docs-only opt-in | accepted 2026-10-26 | M | G: G1 [#349](https://github.com/lean-pkg/lean-pkg/pull/349), G2 [#356](https://github.com/lean-pkg/lean-pkg/pull/356), G3 [#360](https://github.com/lean-pkg/lean-pkg/pull/360) | implemented (G4, the removal of the deprecated registry route, scheduled: the latest of 1.0.0, one minor after G3 ships, two minors plus 60 days) |
| 9c [#320](https://github.com/lean-pkg/lean-pkg/issues/320) | Proposal [0003](../proposals/0003-conditional-claims-and-discharge.md): conditional claims and discharge | accepted 2026-10-26 | L | H: H0 (docs) [#346](https://github.com/lean-pkg/lean-pkg/pull/346), H1 [#350](https://github.com/lean-pkg/lean-pkg/pull/350), H2 [#354](https://github.com/lean-pkg/lean-pkg/pull/354), H3 [#359](https://github.com/lean-pkg/lean-pkg/pull/359), H4 [#366](https://github.com/lean-pkg/lean-pkg/pull/366) | in progress (H5 next) |
| 10 [#326](https://github.com/lean-pkg/lean-pkg/issues/326) | `new`: the lakefile globs include the root module (`["Foo", "Foo.+"]`), so an umbrella module builds | `lean-pkg new --kind library --module Foo` writes `globs = ["Foo.+"]`; `lake build Foo.Solution` then fails on an umbrella `Foo.lean` | XS | I [#334](https://github.com/lean-pkg/lean-pkg/pull/334) | done |
| 11 [#327](https://github.com/lean-pkg/lean-pkg/issues/327) | `new`: the newest **stable** Lean and Mathlib by default, an rc only when no newer stable exists, and the chosen pins printed | on 2026-10-26 `new` pinned `v4.35.0-rc2` although `v4.35.0` was released | S | I [#334](https://github.com/lean-pkg/lean-pkg/pull/334) | done |
| 12 [#328](https://github.com/lean-pkg/lean-pkg/issues/328) | `coverage check --file` offline, before the package is registered, running every schema and cross-file check that needs no receipt | `coverage check NAME --file` refuses an unregistered package; `claims check --file` already works offline | S-M | J [#335](https://github.com/lean-pkg/lean-pkg/pull/335) | done |
| 13 [#325](https://github.com/lean-pkg/lean-pkg/issues/325) | `verify` runs the claims/1 and coverage/1 checks at HEAD (step 0) before producing any receipt | a claims schema refusal at the gate voided both receipts and cost a full re-run | S | J [#335](https://github.com/lean-pkg/lean-pkg/pull/335) | done |
| 14 [#330](https://github.com/lean-pkg/lean-pkg/issues/330) | `report`: redact registry-derived names (lease workspaces) and the index path in the text it attaches itself, so its own guard does not block every report; name the section a refusal came from | with a live lease every report is refused; filed reports showed a partly redacted index path | S | K [#336](https://github.com/lean-pkg/lean-pkg/pull/336) | done |
| 15 [#329](https://github.com/lean-pkg/lean-pkg/issues/329) | `repo export` / `new`: extraction from existing modules with a rename map, and a ledger that re-checks the extraction against the source commit | a hand-written per-declaration ledger was needed to show statements and proofs unchanged | L | L (design first): proposal 0009, accepted 2026-10-26 ([#357](https://github.com/lean-pkg/lean-pkg/pull/357)); stages S1 to S5: S1 [#361](https://github.com/lean-pkg/lean-pkg/pull/361), S2 [#367](https://github.com/lean-pkg/lean-pkg/pull/367) | in progress (S3 next) |
| 16 [#339](https://github.com/lean-pkg/lean-pkg/issues/339) | `repo export`: set the export commit's author and committer explicitly (`--author`, else the source repository's local git config, never the global identity), refuse a non-noreply address without `--allow-author-email`, scan the identity and the message; `publish --dry-run` lists the addresses that go public | the root commit of an export carried the maintainer's personal address from the global git config, and public-safety did not flag it | S | M [#344](https://github.com/lean-pkg/lean-pkg/pull/344) | done |
| 17 [#340](https://github.com/lean-pkg/lean-pkg/issues/340) | `repo export`: carry into `.lean-pkg-public-allow` only the allow lines the export's own scan used; none used, no file | twelve unused allow lines, one spelling an institutional user id, were committed into a public repository and allowed nothing | S | M [#344](https://github.com/lean-pkg/lean-pkg/pull/344) | done |
| 18 [#341](https://github.com/lean-pkg/lean-pkg/issues/341) | `formalization render --write` needs `--force` for a routine derived-key refresh after a re-gate: rewrite the derived keys without `--force` when every kept key is unchanged, or have `verify` refresh formalization.yaml as `gate --refresh-formalization` does | after a fresh-history export and `verify`, publish reported formalization.yaml stale (`status.evidence`) and the routine refresh needed `--force`, which reads as overriding a safety check; it rewrote only derived keys | S | [#347](https://github.com/lean-pkg/lean-pkg/pull/347) | done |

## Order

The pull requests are ordered so that no two edit the same part of `cli.py` at once.

1. **Wave 1, in parallel:** A (items 1 to 4), B (item 6), C (item 7).
2. **Wave 2:** D (item 5), after wave 1, because a linter's first pass touches most files.
3. **Wave 2b, after D and before E:** I (rows 10 and 11, both in `scaffold.py`), J (row 12, then row 13, which
   reuses row 12's offline check) and K (row 14). Small, contained fixes from reports filed on 2026-10-26: they
   land after D, so the linter's first pass does not conflict with them, and before the split moves code.
4. **Wave 3:** E (item 8), alone. Every name `cli.py` exports keeps working for one release through
   re-exports, so `from . import cli as core` callers and outside scripts do not break at once.
5. **Wave 4:** F, G and H (items 9a to 9c), each in its own staged pull requests; F starts once proposal
   0008 is accepted. L (row 15) is designed first: its extraction ledger is a new on-disk format, so it may
   need a proposal (docs/proposals/README.md).
6. Rows 16 and 17 (privacy) were taken alongside wave 3 because they do not touch `cli.py`'s core; row 18 follows.

## The bar for every pull request

- `make check` passes, including the full suite, and hosted CI is green.
- An upgrade-record entry in the in-progress record (`docs/upgrades/`), or a line under
  `no_author_change`; `lean-pkg doctor --upgrade-from` reports nothing uncurated.
- A test for every new refusal path; JSON shapes and help goldens change only with a CHANGELOG entry.
- AI assistance disclosed in the description ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
