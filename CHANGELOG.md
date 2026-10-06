# Changelog

All notable changes to lean-pkg are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, a minor bump may
change the registry schema or receipt format, always with a migration).

## [Unreleased]

### Changed
- The comparator PASS predicate is the full line `Your solution is okay!`
  for both mechanisms, as observed in the first live `lake comparator` run
  (2026-10-06); the kernel acceptance phrase alone no longer counts.
- The sample workflow page says which steps were run and which are
  illustrative; it shows the live toolchain run.

## [0.3.0] - 2026-10-06

The index schema (milestone M1's catalog layer, pulled forward): a public,
forkable list of trust-graded packages whose every row can be re-derived
from bytes at its pin.

### Added
- `index/<name>/entry.yaml`, schema `index-entry/1` (keys in a fixed order;
  unknown keys refused; no local paths), with verbatim receipt copies under
  `index/<name>/receipts/`. The index root is `--index PATH`, env
  `LEAN_PKG_INDEX`, or `./index`.
- `lean-pkg index add NAME`: builds an entry from the registry row
  (`--from-registry`) or from flags, reads the receipts from a clone at the
  pin, validates them with the `gate` and `comparator` validators, computes
  the trust level (`lower-tier`, `comparator`, `palomar`), refuses a
  duplicate repository and commit, and appends `index-added` to the event log.
- `lean-pkg index check [NAME ...] [--clone | --repo-dir PATH]`: static
  checks, or a re-derivation from bytes at the pin (receipts, logs,
  toolchain, `lake-manifest.json`, license, tag, headline files) with trust
  recomputed; Palomar ids and Reservoir listings are verified by API when
  reachable and reported as skipped otherwise. Exit 0, 1 or 2; `--json`.
- `lean-pkg index render [--out PATH] [--check]`: the Markdown table
  `index/README.md`, with a do-not-hand-edit banner; a test keeps it current.
- Docs: [The index](docs/index-catalog.md); CONTRIBUTING, the pull-request
  template and the index-entry issue template use the schema's field names.
- First index entry: `gromov-filling-lean` (lower-tier; Lean v4.29.0; pin
  1c4ccb9c; receipt produced on the Auburn University Easley Cluster).
- Safety of the index (from the fresh-model review): evidence files are
  written with symlinks refused at the pin and on disk; a rename into
  `.lean-receipts/` is no longer evidence-only (`gate` benefits too); a
  Palomar id grades an entry only at its registered commit and `check`
  compares the Palomar entry's source with the index entry; `index add`
  verifies that the pin and evidence are on a public branch unless
  `--allow-unpushed`; receipt copies with local-path strings are refused;
  `COMPARATOR_RECEIPT_HOST_LABEL` gives receipts a public-safe host; `render`
  fails cleanly on a malformed entry; one broken entry no longer blocks
  checking another; the private-boundary check is case-insensitive.

- `LEAN_PKG_EVENTS`: environment variable for the event-log path (after
  `--events`, before the registry's sibling file).

### Fixed
- `lean_pkg.__version__` now agrees with `pyproject.toml` (it still read 0.1.0).
- Documentation site: complete dark-mode palette (header, links, syntax
  colours, tables, footer, search, copy buttons) and light-mode contrast,
  measured to WCAG AA on every page; the workflow diagram is a light card in
  both themes.
- A comparator receipt produced under the test-only digest override now
  carries `test_only: true` and is refused by every validator.
- The toolchain probe requires the judging binaries to be executable, as the
  vendored script does.


## [0.2.0] - 2026-10-06

Milestone M0 of the charter: a tool the lab can consume instead of its own
copy. Checks were run locally with `make check` (hosted CI turns on at the
public flip).

### Added
- `lean-pkg init`: bootstrap an empty registry and its event log; refuses an
  existing registry unless `--force` and it holds no packages.
- Registry schema v2 and `lean-pkg migrate`: additive publication fields per
  package (`license_spdx`, `formalization_yaml`, `palomar[]`, `doi`,
  `reservoir`, `consent[]`); v1 registries stay readable by every command;
  `migrate` is idempotent and has `--dry-run`.
- `lean-pkg record NAME EVENT --field K=V`: evidence events with required
  fields and registry effects (`consent-recorded`, `palomar-preflight-pass`,
  `palomar-submitted`, `palomar-review`, `palomar-registered`,
  `palomar-withdrawn`, `doi-minted`, `reservoir-listed`, `mathlib-pr-opened`);
  `validate` refuses a DOI whose commit no Palomar entry names; `audit` finds
  `palomar-preflight-missing`.
- `comparator-receipt --mechanism auto|toolchain|legacy`: the toolchain
  mechanism runs `lake comparator` with the toolchain's bundled NanoDa and
  con-ron through the vendored PalomarTemplate `verify-comparator.sh`
  (Lean v4.35.0-rc2 or later, what Palomar runs); legacy keeps the pinned
  stack. Receipt schema `comparator-receipt/2` with `mechanism` and per-
  mechanism `tools`; schema 1 still validates.
- `gate` on an already-public package records the receipt and the
  `gate-passed` event without changing the status.
- `record NAME reservoir-listed` verifies the listing against Reservoir's
  package API (a 404 refuses; an unreachable network records `verified: false`).
- The toolchain mechanism checks that `bwrap` can create a user namespace
  before judging and names the Ubuntu 24.04 AppArmor fix when it cannot.
- Governance: `SECURITY.md`, issue and pull-request templates with a required
  AI-assistance line, `docs/decisions/` (0001 files and git only; 0002
  benevolent maintainer with deputies), deputy and succession lines in
  `MAINTAINERS.md`.
- Private-boundary check scans only files git would ship.
- Agent skill `search-before-building`: Loogle, Mathlib at the pin, the
  Reservoir index (not the name-only site search), Palomar, known authors and
  the GitHub long tail, with every query recorded and one decision per need;
  step 0 of `lean-paper-companion` and a row in the router.
- FAQ section on Reservoir: how it differs from lean-pkg and Palomar, why
  there is nothing to register (listing is automatic), and searching it
  before formalizing.

## [0.1.0] - 2026-10-05

First tagged release: the tool extracted from its private lab origin into its own
repository, with the comparator tier and the documentation site. The
repository is private at this version; checks were run locally (see
RELEASING.md).

### Added
- `lean-pkg` registry CLI: statuses `scratch → active → gate-pending →
  verified-private → public → upstreamed`, leases (`claim`/`release`),
  lower-tier receipts (`lake build`, sorry scan, `#print axioms`) bound to a
  commit, `gate`, `promote` with a dry-run checklist and a one-way `--yes`,
  `audit`, `llms` contract output.
- Comparator tier: `comparator-receipt` runner wrapping the vendored Palomar
  `comparator-check.sh`; all-or-nothing evidence write; host policy;
  `lean-pkg comparator --receipt [--check]`; `gate --comparator`;
  audit findings `comparator-drift` and `comparator-missing`.
- Evidence-only descendant rule: a receipt bound to commit X is accepted at
  HEAD H when H descends from X and only `.lean-receipts/` paths differ.
- Eighteen agent skills under `skills/` with an installer for Claude Code
  and Codex (`scripts/install_skills.py`).
- Documentation site (`docs/`, Jekyll Cayman): motivation, getting started,
  how-to, lifecycle, receipts, comparator, CLI, agents, newcomers, sample
  workflow with a seven-step diagram, FAQ, skills; client-side search and a
  dark/light toggle; `llms.txt`.
- Private-boundary check (`scripts/private-boundary-check.sh`) and a test
  suite of 240 tests.
- Secretary-bird logo and favicon; `CITATION.cff`.
