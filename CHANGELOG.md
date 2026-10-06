# Changelog

All notable changes to lean-pkg are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, a minor bump may
change the registry schema or receipt format, always with a migration).

## [Unreleased]

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
