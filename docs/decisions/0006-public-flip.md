# 0006: Public flip

Date: 2026-10-08. Status: proposed (the maintainer signs each gate; the
repository stays private until every gate below is signed).

## Context

Making `lean-pkg/lean-pkg` public is one-way: forks, caches and clones cannot
be withdrawn. The public-hub RFC sets seven gates, G1 to G7, that must all
hold first, and the maintainer signs each one. This record lists, for each
gate, the evidence as of 2026-10-08 and what is still open, so that each
signature points at something checkable.

## Gates

| Gate | Requirement | Evidence (2026-10-08) | Open | State |
|---|---|---|---|---|
| G1 Stranger test | A person outside the project installs from the README, runs lean-pkg on their own Lake project with a Challenge/Solution layout, obtains a comparator receipt that `lean-pkg comparator --check` accepts, and records a dated transcript. | Template and rules: [stranger-test.md](../stranger-test.md). Collaborator path: [existing-package-comparator.md](../existing-package-comparator.md) with `scripts/bootstrap-collaborator.sh` (tested offline under a fake HOME). The guide's steps 5 to 7 were run end to end on a real paper companion. | No transcript yet. A collaborator's dry run is planned, but a student of the maintainer does not count as outside the project. | open |
| G2 Separation | No private host, user, path, lease, person or notification-channel string in the public tree or history. | `make check` scans the tree on every push (also in CI). `make history` is green on every ref after the one-time rewrite of decision [0003](0003-history-before-the-public-flip.md); the merged pull-request branches were deleted. Index receipt copies are refused when they carry private strings. | GitHub keeps the pre-rewrite commits under the read-only refs of pull requests #1, #2, #3 and #5, and still serves the three pre-rewrite commits of `markovprocess-pilot` by SHA (its history was cleaned the same way on 2026-10-08, before it was made public); only GitHub Support can remove them. One request covering both repositories is drafted, not yet submitted. | open |
| G3 License and disclosure | Apache-2.0, REUSE lint green, AI-assistance disclosure in README and CONTRIBUTING. | `LICENSE`, `NOTICE`, `REUSE.toml`; `make reuse` reports compliant inside `make check`. Disclosure sections in `README.md` and `CONTRIBUTING.md`. | — | met |
| G4 Evidence rule | The evidence-only descendant rule implemented and tested on both receipt tiers. | `gate` and `comparator` accept a receipt at its commit or at a descendant that differs only under `.lean-receipts/`, and refuse any other drift; covered by `tests/test_gate.py`, `tests/test_comparator.py` and `tests/test_comparator_receipt.py`. Observed on real packages on 2026-10-08: a README commit above a comparator receipt was refused, and evidence-only commits were accepted for both tiers. | — | met |
| G5 Index | At least two reference-grade entries keyed to Palomar ids or library pins, and a working `search` over the index. | Three entries, all pinned to commits, all in public repositories, and `lean-pkg index check --clone` re-derives all three from those repositories. Two at comparator trust: `total-coloring-lean` (pin `533f9430cb45`, legacy mechanism) and `markovprocess-pilot` (pin `9bb873e7aa31`, toolchain mechanism, which is what Palomar runs). `lean-pkg search` covers the index (`tests/test_search.py`). No entry has a Palomar id yet; the gate allows library pins instead. | — | ready to sign |
| G6 Institutional | A written determination covering a public, Apache-licensed tool and catalog. | Auburn University's IP office agreed to the notice and the license: decision [0004](0004-copyright-and-license-confirmed-by-auburn.md). | — | met |
| G7 Maintainers | `MAINTAINERS.md` with at least one named successor. | `MAINTAINERS.md` names the maintainer of record; decision [0005](0005-github-account-successor-until-a-deputy-is-named.md) covers succession through the maintainer's GitHub account successor until a deputy is named. | A deputy is not named, and the account-successor setting is not yet confirmed. Fully met when a named deputy is added. | open |

## Decision (proposed)

Make the repository public when G1, G2, G5 and G7 are signed, in this order:

1. Cut a release that matches the documentation (the changes listed under
   Unreleased in `CHANGELOG.md`), with `scripts/release.sh`.
2. Make the repository public, and confirm GitHub Pages serves
   `https://lean-pkg.github.io/lean-pkg/` again (the Pages source is already
   set to `main:/docs`).
3. List the package on Reservoir and mint the first Zenodo DOI through the
   GitHub integration.

## Consequences

- Each gate's row is replaced by its signature (date, and the commit or
  document that proves it) when the maintainer signs it; the status becomes
  accepted when all seven are signed.
- After the flip, private material can no longer be removed from what was
  published; anything found later is handled as a new decision.
