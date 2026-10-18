# 0006: An explicit claim-to-comparator mapping (`judged_as` in claims.yaml)

Status: accepted. Author: Claude, the lean-pkg operations agent, drafting for Le Chen. Created: 2026-10-18.
Decided: accepted 2026-10-18 by Le Chen; open questions 1 and 2 stay open. Implemented in: (release).

## Summary

A theorem or lemma claim in `claims.yaml` may carry an optional key `judged_as`: the full name of the
comparator theorem (a `theorem_names` entry) that judges it, when the Challenge theorem is not named after
the claim's `decl`. For that claim the mapping replaces the naming rule (the `decl`, or `<Prefix>.<decl>`).
It is checked, never trusted, and `receipt check` reads it from the repository alone. No schema version
changes: `claims/1` gains one optional key, and a file without it is read exactly as before. The
implementation pull request is the one that adds this file.

## Motivation

The lts-2026.10 stage 0 report (docs/stacks/lts-2026.10.md, "Known, pre-existing") found that
markovprocess-pilot and total-coloring-lean, both in the public index, fail `receipt check`'s claimed-theorems
check (and `coverage check`'s R11, once a `coverage.yaml` exists): their comparator configurations judge
Mathlib-only restatements (`exit_mean`;
`TotalColoringAudit.HighDegree.exists_total_coloring_of_card_le_two_mul_maxDegree`), while their claims
name the library theorems (`MarkovProcessPilot.integral_eval_exitTimeTrunc_Ioo`;
`TotalColoring.exists_valid_assignment_of_highDegree`). The naming rule cannot connect them, and the only
place the connection is written is the lean-pkg index entry (`headline[].judged_as`), which a referee
holding only the repository does not have. `claims.yaml` could not say it: claims/1 refuses unknown keys.

## Design

- `claims.yaml`: optional `judged_as: <Lean name>` on a claim of kind `theorem` or `lemma`. The schema
  refuses a malformed name, `TODO`, the key on any other kind, and the same name on two claims (one
  comparator theorem judges one claim). `normalize_claim` keeps the key only when set, so a cache without it
  compares equal.
- One coverage rule (`comparator_config.claim_covered`), used everywhere the naming rule was: the claims
  ledger (`promote`, `publish`), `comparator config --coverage` and `--from-claims`, `claims mark
  --from-comparator`, `status`'s scope block, `coverage check` R11, `page` and `receipt check`. With
  `judged_as` set, the claim is covered by exactly that configured name (a scaffold TODO name makes it
  `todo`) and only when its `decl` is the fully qualified name of a declaration its file declares. Without
  it, the naming rule applies to every configured name no claim maps: a mapped name credits only its claim.
- `comparator_config.judged_as_problems`: a `judged_as` that names no theorem of a configuration tracked at
  the commit, or whose claim's `decl` is not declared in its file. `receipt check`'s claimed-theorems check
  fails on any of them, whatever the claim's status (a false mapping is a false statement about the evidence
  even on an internal claim); `comparator config --coverage` prints them and gains the `--json` key
  `judged_as_problems`; the ledger's `no-comparator-config` detail names the missing name.
- Where it is read: in `claims.yaml` at the receipt commit X, like the rest of the claims. `claims.yaml` is
  bound to X (the docs-only rule refuses a change to it), so a mapping added after X is a new commit to gate.

## Compatibility and migration

- A claims file without the key is unchanged, and so is every verdict on it. lean-pkg 0.13.0 and older
  refuse a claims file with the key (claims/1 refused unknown keys), so a package should add it only once it
  requires a lean-pkg release that has it.
- A package whose Challenge theorems already follow the naming rule needs nothing.
- A package in the case above adds `judged_as` to each judged claim and gates the new commit (a new lower-tier
  receipt and comparator receipt bound to it). No source or configuration changes.
- Goldens: the `CLAIMS FILE SCHEMA` help text and `comparator-coverage`'s `--json` key set
  (`judged_as_problems`).

## Trust model

Yes: what `receipt check`, the ledger (`promote`) and `coverage check` accept. A claim the naming rule left
uncovered can now count as comparator-judged. What a dishonest or mistaken author could get accepted that
they could not before: a claim mapped to a Challenge theorem that states something weaker than the claim.
That was already possible with the naming rule (a Challenge named `<Prefix>.<decl>` that states something
else); in both cases the comparator checks only that the Solution proves the Challenge, and whether the
Challenge says what the claim says is for a reader of both statements (the page shows the Challenge
statement). The mapping is explicit, in the bound claims, and checked for existence and uniqueness. It also
adds refusals: a mapped name no longer covers another claim by its name. Pinning tests:
`tests/test_judged_as.py` (schema refusals; a mapped name credits no other claim; a mapping to an
unconfigured name or an undeclared decl fails `receipt check`; a mapping added after X does not rebind; a
second, unjudged reusable claim still fails; the ledger and `--coverage` credit the mapping).

## Alternatives

- Read the index entry's `judged_as`: the referee's check would depend on the index, against the trust
  model's re-check-from-the-repository principle.
- Rename the Challenge and Solution theorems to `<Prefix>.<decl>`: works today, but needs Lean changes and
  a comparator re-run, and forces a naming scheme on Challenge files written for readers.
- A mapping in `coverage.yaml` or `lean-pkg-signoff.json`, read at the published ref: would let a mapping be
  added without a new gate, but it splits what a claim means across two commits (the claims at X, their
  mapping at the ref), and `coverage.yaml`'s R11 would still fail a package with a smoke configuration that
  judges no claim. Neither public package could pass without a new gate anyway: each has reusable theorem
  claims no configuration judges (below).
- A prefix map: rejected before (docs/comparator.md, "There is no prefix map"); a per-claim name is narrower.

## Open questions

1. Both packages also mark theorems `reusable` that no configuration judges (markovprocess-pilot: 1 of 2;
   total-coloring-lean: 6 of 8). The claimed-theorems check fails them independently of this proposal.
   Each needs a Challenge/Solution of its own or another status; that is the author's decision.
2. Should `index add --headline ... :: CHALLENGE` read `judged_as` from `claims.yaml`, or check that both agree?

## AI assistance

Drafted and implemented by Claude (Anthropic), the lean-pkg operations agent, from the lts-2026.10 stage 0
finding, at Le Chen's request; to be reviewed and decided by the maintainer.
