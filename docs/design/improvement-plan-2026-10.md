---
title: "Plan: hygiene, the 1.0 format freeze and the next features (2026-10)"
---

[&larr; lean-pkg documentation](../index.md)

*Status: in progress. Written 2026-10-26 after the 0.21.0 release, from three read-only surveys of the
repository (pending work in the design docs; test-suite and CI health; code structure and command-line
consistency). Each item lands as its own pull request that names its row here; this page is updated
as rows land.*

## The items

| # (issue) | Item | Evidence | Size | PR | Status |
|---|---|---|---|---|---|
| 1 [#310](https://github.com/lean-pkg/lean-pkg/issues/310) | Tests leave `__pycache__` in `src/`; a session-end check refuses it | `tests/test_bootstrap_collaborator.py` builds the stub's environment from scratch, so `PYTHONPYCACHEPREFIX` never reaches `lean_pkg.cli` | S | A | planned |
| 2 [#311](https://github.com/lean-pkg/lean-pkg/issues/311) | CI hardening: job time limits and a per-test timeout, pip cache, cancel superseded runs, Python 3.13 and 3.14 in the matrix | `.github/workflows/ci.yml` has no `timeout-minutes` and tests 3.11 and 3.12 only | S | A | planned |
| 3 [#312](https://github.com/lean-pkg/lean-pkg/issues/312) | Tests that skip everywhere run again: `jsonschema` and `setuptools` in the `test` extra | `tests/test_formalization.py` (4 skips), `tests/test_adapters.py` `importorskip("setuptools")` | S | A | planned |
| 4 [#313](https://github.com/lean-pkg/lean-pkg/issues/313) | Loose ends: the `public-safety` target's old `lean-pkg init` spelling; the `tomllib` fallback that cannot run on Python 3.11+; `make test` says when it falls back to a serial run | `Makefile`, `src/lean_pkg/build_scope.py` | XS | A | planned |
| 5 [#314](https://github.com/lean-pkg/lean-pkg/issues/314) | A Python linter: `ruff check` in `make check` and CI, pinned like ShellCheck | no linter configured anywhere | S-M | D | planned |
| 6 [#315](https://github.com/lean-pkg/lean-pkg/issues/315) | The format freeze, [1.0 command tree](1.0-command-tree.md) section 6 items 1 to 3: a schema marker for the lower-tier receipt, a `v` key on event lines, `docs/formats.md` | section 6; no marker in `src/` | S-M | B | planned |
| 7 [#316](https://github.com/lean-pkg/lean-pkg/issues/316) | Command-line consistency: the commit flags only on write commands; `--repo` for the package checkout everywhere, `--project` kept as a permanent alias | `add_json()` in `cli.py`; `receipt comparator`, `claim-axioms`, `dup-constants` (`lean-exercise --project` names a Mathlib-providing project, not the checkout: kept) | S | C [#322](https://github.com/lean-pkg/lean-pkg/pull/322) | in review |
| 8 [#317](https://github.com/lean-pkg/lean-pkg/issues/317) | `cli.py` split: the shared core (`Fail`, git helpers, registry IO, events) moves to `core.py`; gate, audit and the parser follow | `cli.py` is 9442 lines; 36 modules import it as `core` | L | E | planned |
| 9a [#318](https://github.com/lean-pkg/lean-pkg/issues/318) | A compute receipt from a CI runner (GitHub Actions first) | [sites and CI receipts](sites-and-ci-receipts.md) section 4 | L | F | planned |
| 9b [#319](https://github.com/lean-pkg/lean-pkg/issues/319) | Proposal [0002](../proposals/0002-committed-docs-only-opt-in.md): the committed docs-only opt-in | accepted 2026-10-26 | M | G | planned |
| 9c [#320](https://github.com/lean-pkg/lean-pkg/issues/320) | Proposal [0003](../proposals/0003-conditional-claims-and-discharge.md): conditional claims and discharge | accepted 2026-10-26 | L | H | planned |

## Order

The pull requests are ordered so that no two edit the same part of `cli.py` at once.

1. **Wave 1, in parallel:** A (items 1 to 4), B (item 6), C (item 7).
2. **Wave 2:** D (item 5), after wave 1, because a linter's first pass touches most files.
3. **Wave 3:** E (item 8), alone. Every name `cli.py` exports keeps working for one release through
   re-exports, so `from . import cli as core` callers and outside scripts do not break at once.
4. **Wave 4:** F, G and H (items 9a to 9c), each in its own staged pull requests.

## The bar for every pull request

- `make check` passes, including the full suite, and hosted CI is green.
- An upgrade-record entry in the in-progress record (`docs/upgrades/`), or a line under
  `no_author_change`; `lean-pkg doctor --upgrade-from` reports nothing uncurated.
- A test for every new refusal path; JSON shapes and help goldens change only with a CHANGELOG entry.
- AI assistance disclosed in the description ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
