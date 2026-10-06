# Future roadmap

This document records proposed directions for `lean-pkg`. It is a planning
document, not a promise that every item will ship. The priority is to reduce
friction for contributors while strengthening independent trust and keeping
the maintainer out of the mechanical verification loop.

## Guiding principles

1. **Submission is a claim, not proof.** A contributor may prepare evidence,
   but cannot assign their own trust level.
2. **Trust is re-derived from source.** Verification must bind to an exact
   public commit and be reproducible from that commit's bytes.
3. **Untrusted projects run isolated.** Verification runners must not expose
   repository secrets or privileged host access to submitted code.
4. **Human fidelity review remains explicit.** Machines can check proof
   evidence; humans still judge whether the Lean statement faithfully says
   what the mathematical paper claims.
5. **Palomar and Reservoir are complementary.** `lean-pkg` should hand off
   cleanly to external ecosystem services rather than duplicate them.

## Priority 0: make contribution one-step and independently verifiable

### `lean-pkg index submit`

Add a contributor-facing command that turns a valid local index entry into a
pull request.

Proposed flow:

1. run the same checks as `lean-pkg index add` and `index check`;
2. render the index;
3. create a contribution branch (or fork when needed);
4. commit only the generated index entry, receipt copies, and rendered index;
5. open a pull request with the pin, headline theorem(s), computed trust, and
   AI-assistance disclosure in the body.

Acceptance criteria:

- never asks the contributor to type a trust level;
- refuses unpushed pins or evidence;
- supports `--json` and non-interactive operation;
- prints the exact branch, commit, and pull-request URL on success;
- never pushes unrelated working-tree changes.

### Trusted PR verifier

Add an independent verifier for index-entry pull requests. The verifier must
treat all contributor-supplied metadata and receipts as untrusted input.

Minimum verification:

- clone the submitted package at the exact pin;
- run `lean-pkg index check --clone <name>`;
- compare index receipt copies byte-for-byte with the package evidence;
- recompute trust rather than accepting the PR's declared value;
- verify headline coverage for comparator trust;
- publish a machine-readable verification report on the pull request.

A successful report should identify at least:

- source repository and 40-hex commit;
- Lean toolchain and Mathlib revision;
- lower-tier result;
- comparator result and mechanism, when applicable;
- headline coverage;
- receipt/log hash matches;
- recomputed trust;
- verifier version and verification time.

For stronger entries, support an optional **fresh Comparator execution** rather
than only re-validating committed evidence.

### Reproducible sandbox runner

Verification of external repositories executes untrusted code. Run it in a
disposable, pinned environment with no repository secrets.

Desired properties:

- pinned runner image or equivalent reproducible environment;
- no write credentials for the index repository during verification;
- explicit network policy;
- CPU, memory, disk, and time limits;
- preserved logs and reports as artifacts;
- environment/tool hashes included in the verification report;
- a clean failure mode when the project requires unsupported host features.

The trusted verifier should consume the sandbox result, not run arbitrary Lean
code directly on a maintainer workstation.

## Priority 1: strengthen long-term trust

### Signed verification attestations

Allow the official verifier to sign its final verification statement. The
signature should bind the repository, commit, evidence hashes, recomputed trust,
verifier version, and timestamp. A future Sigstore-style implementation would
let third parties verify that a certificate was issued by an authorized
`lean-pkg` verifier rather than by the submitter.

### Headline theorem change detection

Make statement drift easier for mathematicians to review. In addition to
commit-level drift, report when a headline theorem's statement changes between
two pins. The report should distinguish at least:

- changed hypotheses;
- changed conclusion;
- renamed/moved theorem;
- unchanged statement with proof-only changes.

This is a review aid, not a replacement for kernel verification.

### Fidelity-review records

Add an explicit human-review layer for the question machines cannot answer:
"Does this Lean theorem faithfully represent the mathematical claim?"

Possible fields:

- paper/title or citation;
- theorem/section identifier;
- reviewer handle;
- review date;
- verdict and notes;
- optional second reviewer.

Machine trust and fidelity review should remain separate dimensions.

### Reviewer and deputy model

Reduce the single-maintainer bottleneck. Support trusted reviewers, ideally by
mathematical area, while keeping mechanical trust computation automatic.
Document what reviewers may approve, what requires maintainer action, and how
conflicts or succession are handled.

## Priority 2: ecosystem and usability

### Palomar handoff

Add a command such as `lean-pkg palomar prepare` that validates or generates
the metadata needed for a Palomar submission from a comparator-trusted pin.
Where supported, it may also help open the submission, but Palomar remains the
external authority.

### Searchable web catalog

Evolve beyond the generated Markdown table when the index becomes large enough.
Useful filters include:

- mathematical field;
- headline theorem text;
- author/contributor;
- trust level;
- Lean and Mathlib versions;
- Palomar status;
- DOI;
- Reservoir listing.

The machine-readable index remains the source of truth.

### Policy profiles

Provide named policy sets such as `research`, `publication`, and
`palomar-ready`. Profiles could define required evidence, comparator policy,
license requirements, AI disclosure, consent, and fidelity-review requirements.
Users should still be able to inspect the expanded policy.

### Maintenance automation

Possible follow-ups:

- stale-receipt and re-audit notifications;
- Mathlib/toolchain upgrade warnings;
- cached Comparator tool builds;
- DOI/Zenodo helpers;
- dependency graphs between indexed results;
- stable API output for external tools and agents.

## Suggested implementation order

1. `lean-pkg index submit`;
2. trusted pull-request verifier;
3. reproducible sandbox runner;
4. signed verification attestations;
5. fidelity-review and reviewer workflows;
6. Palomar handoff and catalog improvements.

The first three turn the current CLI/index model into a scalable submission and
verification service without weakening the principle that trust is independently
re-derived.
