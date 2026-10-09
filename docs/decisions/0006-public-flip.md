# 0006: Public flip

Date: 2026-10-08. Status: proposed (the maintainer signs each gate; the
repository stays private until every gate below is signed).

## Context

Making `lean-pkg/lean-pkg` public is one-way: forks, caches and clones cannot
be withdrawn. The public-hub RFC sets seven gates, G1 to G7, that must all
hold first, and the maintainer signs each one; G8, added on 2026-10-09,
records lean-pkg's own public-safety scan of this repository. This record lists, for each
gate, the evidence as of 2026-10-08 and what is still open, so that each
signature points at something checkable.

## Gates

| Gate | Requirement | Evidence (2026-10-08) | Open | State |
|---|---|---|---|---|
| G1 Stranger test | A person outside the project installs from the README, runs lean-pkg on their own Lake project with a Challenge/Solution layout, obtains a comparator receipt that `lean-pkg comparator --check` accepts, and records a dated transcript. | Template and rules: [stranger-test.md](../stranger-test.md). Collaborator path: [existing-package-comparator.md](../existing-package-comparator.md) with `scripts/bootstrap-collaborator.sh` (tested offline under a fake HOME). The guide's steps 5 to 7 were run end to end on a real paper companion. | Waived by the maintainer on 2026-10-09: the repository is made public before the first outsider transcript; the stranger test still runs (an outsider agreed on 2026-10-09 to run it the next morning) and its findings are handled as ordinary issues. Email sent 2026-10-09 (21:57 UTC) to a PhD student outside the project (never worked on or shown lean-pkg; read collaborator on the private repo): install from the README on his own Lake project, comparator receipt, `comparator --check`, dated transcript per docs/stranger-test.md. No transcript yet. A collaborator's dry run is planned, but a student of the maintainer does not count as outside the project. | waived 2026-10-09 (maintainer) |
| G2 Separation | No private host, user, path, lease, person or notification-channel string in the public tree or history. | `make check` scans the tree on every push (also in CI). `make history` is green on every ref after the one-time rewrite of decision [0003](0003-history-before-the-public-flip.md); the merged pull-request branches were deleted. Index receipt copies are refused when they carry private strings. | Residue accepted by the maintainer on 2026-10-09 (the trees hold no private string; the remaining objects are pre-rewrite PR heads and one force-removed commit, Support ticket 4841981 pending their removal). Residue documented; Support request submitted by the owner on 2026-10-09, ticket 4841981. GitHub keeps the pre-rewrite commits under the read-only refs of pull requests #1, #2, #3 and #5 (106 commits; trees scanned: 0 private strings, messages hit only a channel name) and served one force-removed commit (`b00e483`, a split-form list of the boundary strings) by SHA; the request asks for those refs to be deleted and a garbage collection run. `markovprocess-pilot` was re-checked on 2026-10-09: its PR heads are ancestors of `main` and GitHub holds no orphan refs, so it is not in the request. | accepted with residue 2026-10-09 (maintainer); ticket pending |
| G3 License and disclosure | Apache-2.0, REUSE lint green, AI-assistance disclosure in README and CONTRIBUTING. | `LICENSE`, `NOTICE`, `REUSE.toml`; `make reuse` reports compliant inside `make check`. Disclosure sections in `README.md` and `CONTRIBUTING.md`. | — | met |
| G4 Evidence rule | The evidence-only descendant rule implemented and tested on both receipt tiers. | `gate` and `comparator` accept a receipt at its commit or at a descendant that differs only under `.lean-receipts/`, and refuse any other drift; covered by `tests/test_gate.py`, `tests/test_comparator.py` and `tests/test_comparator_receipt.py`. Observed on real packages on 2026-10-08: a README commit above a comparator receipt was refused, and evidence-only commits were accepted for both tiers. | — | met |
| G5 Index | At least two reference-grade entries keyed to Palomar ids or library pins, and a working `search` over the index. | Three entries, all pinned to commits, all in public repositories, and `lean-pkg index check --clone` re-derives all three from those repositories. All three at comparator trust: `total-coloring-lean` (pin `533f9430cb45`, legacy mechanism), `markovprocess-pilot` (pin `05d99d75df82`, toolchain mechanism, which is what Palomar runs) and `gromov-filling-lean` (pin `ba787a3eb26e`, legacy mechanism). `lean-pkg search` covers the index (`tests/test_search.py`). No entry has a Palomar id yet; the gate allows library pins instead. Signed 2026-10-09 at this commit: three entries at comparator trust, `index check --clone` re-derives all three, `search` covered by tests. | — | signed 2026-10-09 |
| G6 Institutional | A written determination covering a public, Apache-licensed tool and catalog. | Auburn University's IP office agreed to the notice and the license: decision [0004](0004-copyright-and-license-confirmed-by-auburn.md). | — | met |
| G7 Maintainers | `MAINTAINERS.md` with at least one named successor. | `MAINTAINERS.md` names the maintainer of record and, since 2026-10-09, a named deputy (operations, releases): Yimin Zhong (@lowrank), who accepted by email the same day. Decision [0005](0005-github-account-successor-until-a-deputy-is-named.md) keeps the maintainer's GitHub account successor as a second line of succession — confirmed set by the maintainer on 2026-10-09. | Org owner invitation for @lowrank sent 2026-10-09 (role admin); access becomes effective when he accepts — no separate repository invitation is needed. | signed 2026-10-09 |
| G8 Public-safety self-scan clean | `make public-safety` (lean-pkg's own `public-safety --strict --history` over a fresh clone with every ref, registered in a throwaway registry; RELEASING.md checklist step 3) reports 0 blocking and 0 warnings, with the untracked `.lean-pkg-private-patterns` (block lines for the private-boundary list, never a raw private string; anchored allow lines for fixtures). Signed with: the date, the summary line (without the clone path), and the patterns file's line count. | 2026-10-09, at `4e9615d` (the recalibrated latex-label family of #37): `0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)` under `--strict`, patterns file 57 lines (13 block, 9 allow); also clean under v0.7.10 (`ef7e698`) with two more allow lines for its latex-label false positives. The first run without the file had 264 hits (193 blocking, 71 warnings), all classified: tool self-documentation of its own built-in patterns, test fixtures, the maintainer's public homepage URL, and 13 latex-label false positives; no real private string. Signed 2026-10-09: `make public-safety` on `main` (full history, `--strict`) printed 0 blocking / 0 warnings with the untracked 57-line patterns file (13 block, 9 allow); first raw run 264 hits, all intentional fixtures or the tool quoting its own patterns; positive control caught. | Re-run before the flip on the commit to be made public, and sign with that run's date and line. | signed 2026-10-09 |

## Decision (accepted 2026-10-09)

Accepted by the maintainer on 2026-10-09 with two gates overridden: G1 waived and G2 accepted with its documented residue (see the rows). The flip was executed the same day.

Make the repository public when G1, G2, G5, G7 and G8 are signed, in this order:

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
  accepted when all eight are signed.
- After the flip, private material can no longer be removed from what was
  published; anything found later is handled as a new decision.
