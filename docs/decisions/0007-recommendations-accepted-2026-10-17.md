# 0007: Design recommendations accepted on 2026-10-17

Date: 2026-10-17. Status: accepted.

## Context

By 2026-10-17 several design documents and pull requests ended in a list of
choices left to the maintainer: the [1.0 command tree](../design/1.0-command-tree.md),
[compute sites and CI-runner receipts](../design/sites-and-ci-receipts.md),
[two tracks, paper and library](../design/two-tracks.md), `receipt check`
(#198), the newcomer path (`lean-pkg new`, #195), and the verification page
(#205 and proposal 0001, #206). Each choice came with a recommendation. The
maintainer accepted every pending recommendation on 2026-10-17. This record
lists them in one place, so that later pull requests can name the decision
they implement.

Items that concern only the maintainer's own lab (its installer, its
compute-cluster jobs and its scripts) are decided there and are not part of
this record.

## Decision

### 1.0 command tree

The proposal [docs/design/1.0-command-tree.md](../design/1.0-command-tree.md)
is accepted, with its section 8 decisions as proposed.

- Stage 1 (merged in #201) settles two names: `comparator NAME` keeps
  attaching a receipt, as a permanent alias, and the bare
  `formalization NAME` means `formalization render NAME`.
- Stage 2 covers:
  - `release` split into `repo export` and `repo adopt`; `promote` becomes
    `publish --yes`;
  - the `verify NAME` orchestrator: both receipts and the gate in one
    command, with no manual status moves;
  - tiered root help, and the docs and `llms.txt` moved to the new
    spellings;
  - `comparator check`, and `comparator coverage` and
    `comparator migrate-signoff` split out of `comparator config`;
  - the alias `verify-receipts` for `receipt check`.
- The generated command reference has one page per canonical parser node
  after stage 1. Its index groups and tiers come from parser data.
  `lean-exercise` and `lean-pkg-extract` keep hand-written pages, and
  `comparator-receipt` becomes an alias of `receipt comparator`.

### Newcomer path

- `mathlib-rev: none` is valid for a package without Mathlib: a
  `lake-manifest.json` with no Mathlib entry is a valid value for the
  receipt.
- `lean-pkg new` licenses a new package Apache-2.0 by default.
- `new --kind paper|library`, with **paper** the default (as in the 1.0
  tree) and `--kind library` given explicitly.
- These fixes for newcomers go into stage 2:
  - `register --license-spdx`;
  - `verify NAME` makes both receipts and runs the gate;
  - `page --write` turns on the docs-only exception for the README itself;
  - a sensible workstation default for the comparator host policy;
  - no private-path warning for lean-pkg's own install location;
  - `new` writes REUSE/SPDX headers, so `publish --dry-run` passes the
    REUSE lint;
  - re-running `comparator-receipt` after `verify.sh` no longer trips on
    stale resume state.
- A textbook result stays `kind: library`, with the book cited in the
  notes. This answers a design question and changes nothing.

### Compute sites and receipts

- Site profiles (the four decisions of
  [sites-and-ci-receipts.md](../design/sites-and-ci-receipts.md), section 6):
  - package-defined sites stay;
  - the legacy `easley` key of `page --json` is dropped at the 1.0 freeze,
    in favour of `sites`;
  - GitHub Actions gets a built-in profile whose sentence states evidence
    rather than thanks a resource, rendered only when the run is
    corroborated or attested (levels 2 and 3);
  - a CI receipt backs `compute` at levels 2 and 3 only when the job ran
    lean-pkg's pinned reusable verify workflow.
- `receipt check`:
  - keeps its name, with the alias above;
  - stays strict for outside referees: a lower-tier receipt without its
    committed redacted log fails, and the allowed axioms are the default
    three;
  - a committed, verifiable docs-only opt-in is designed in proposal 0002
    (#212), read only at the bound commit X.
- `receipt local --run` warns while a run is in progress and refuses at
  collection when lean-pkg itself changed; it does not stop the run (as
  shipped in #207).
- Comparator names get no prefix maps.

### Two tracks

The four decisions of [two-tracks.md](../design/two-tracks.md#decisions):

- The product kind stays derived from `coverage.yaml`, as shipped; nothing
  is stored in the record. The DOI flag `--track` is renamed `--doi-track`
  (done in stage 1).
- The Mathlib lint is reported in 1.0, and required only for a package with
  `upstream_target: mathlib`.
- Upstreaming is tracked per declaration with optional `claims.yaml` keys
  `upstream: {pr, merged_as, retired}`, additive within `claims/1`.
- Track L succeeds with a Reservoir listing. Mathlib pull requests are
  optional: lean-pkg prepares them and never opens them.
- The maintainer's roadmap has a paper column and a library column per wave,
  with targets of five outside paper pages and five outside libraries listed
  on Reservoir.

### Verification page

- Proposal 0001, the paper's statement in TeX in `coverage.yaml` (#206), is
  accepted; its implementation becomes a pull request.
- Typeset mathematics on the page comes from TeX source only, for now.
- After #205 every published page needs one `page --write`. Page owners are
  told in the release notes of the next release.

### Docs

- The two-track workflow diagram also goes on the README, under the
  quickstart link.

## Consequences

- Implementation pull requests name this record, or the design document's
  section, as the decision they implement. A change of mind is a new
  decision that supersedes the item.
- The design documents above mark these choices as decided and link here.
- Format and trust-model changes still need their own proposal before code
  (docs/proposals/README.md): the committed docs-only opt-in (0002), the
  `claims.yaml` `upstream` keys and the CI receipt are designed there, not
  in this record.
