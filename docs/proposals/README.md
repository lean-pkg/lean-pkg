# Design proposals

A proposal is a short written design, agreed before the code, for a change
that other people's files, scripts or trust depend on. It is how an outside
contributor can change lean-pkg's formats and commands, and how the
maintainer's reasoning stays on record.

Proposals differ from [decisions](../decisions/README.md): a decision records
a choice about the project (governance, licensing, history); a proposal
designs a change to the tool and ends in code.

## When a proposal is required

- **Any schema change**: a new version of an on-disk format (registry,
  event log, claims, coverage, receipts, estimates, index entries, the
  signoff sidecar) or a change to what an existing version accepts.
- **Removing or renaming** a command, flag or `--json` key, or changing its
  meaning. Adding one does not need a proposal.
- **A trust-model change**: what a receipt proves, what `gate`, `promote`,
  `publish` or `index check` accept or refuse, how a trust level is computed,
  or what the private-boundary and public-safety checks forbid.

Anything else (a new command or flag, a fix, docs) goes straight to a pull
request. When unsure, open a *Proposal* issue and ask.

## Lifecycle

| Status | Meaning |
|---|---|
| `draft` | Open for discussion in a pull request that adds `NNNN-short-title.md`. |
| `accepted` | The maintainer approved it; the PR merges with the status set and the date. Implementation PRs may start. |
| `implemented` | The change is released; the status line names the release. |
| `declined` / `withdrawn` | Merged anyway, with the reason, so the idea is not re-proposed blind. |
| `superseded by NNNN` | A later proposal replaced it. |

1. Copy [0000-template.md](0000-template.md) to `NNNN-short-title.md`, the
   next free number, and open a pull request with only that file and a row
   in the table below. Link a *Proposal* issue if there was one.
2. Discussion happens on the pull request. Anyone may comment; the author
   revises the file.
3. The maintainer decides (below). Accepting does not merge any code.
4. Implementation pull requests name the proposal. The last one sets
   `implemented` and the release.

An accepted proposal is not edited afterwards except for its status line; a
change of design is a new proposal that supersedes it.

## Who decides

The maintainer of record (Le Chen, see [MAINTAINERS.md](../../MAINTAINERS.md))
accepts or declines, with the reasoning written into the proposal. When a
second maintainer is added, a proposal needs the agreement of one maintainer
who is not its author; a disagreement between maintainers is settled by the
maintainer of record and recorded. The AI operations agent may draft a
proposal when asked but never accepts one ([GOVERNANCE.md](../../GOVERNANCE.md)).

## Proposals

| # | Title | Status |
|---|---|---|
| [0000](0000-template.md) | Template | n/a |
| [0001](0001-paper-statement.md) | The paper's statement in coverage.yaml | `accepted` 2026-10-17 by Le Chen; implemented by the pull request that adds `paper.statement` |
| [0002](0002-committed-docs-only-opt-in.md) | A committed docs-only opt-in, read at the bound commit | `accepted` 2026-10-26 by Le Chen, as written; implemented by #349, #356 and #360 (stages G1 to G3, [#319](https://github.com/lean-pkg/lean-pkg/issues/319)); the registry route is deprecated, its removal (G4) scheduled |
| [0003](0003-conditional-claims-and-discharge.md) | Conditional claims and discharge (with a core-Lean prototype in [0003/prototype](0003/prototype/)) | `accepted` 2026-10-26 by Le Chen, as written; implemented in stages H0 (#346), H1 (#350), H2 (#354), H3 (#359), H4 (#366) and H5 (#368), tracked in [#320](https://github.com/lean-pkg/lean-pkg/issues/320); `implemented` at the release that ships them |
| [0004](0004-math-lts-stack.md) | A math LTS stack: a tested Lean + Mathlib + lean-pkg combination, supported for a stated window | `accepted` 2026-10-17 by Le Chen (open question 6 still open); stage 0: [lts-2026.10](../stacks/lts-2026.10.md); stage 1 (`new --stack`, the `stack:` key, the `doctor NAME` and `verify` check, the page line) by the pull request that adds `new --stack`; `doctor --stack-from/--stack-to` to follow |
| [0005](0005-per-claim-axioms.md) | Per-claim axioms read from the built environment (`claim_axioms` receipt key) | `accepted` 2026-10-18 by Le Chen; implemented by the pull request that adds `claim_axioms` |
| [0006](0006-explicit-claim-comparator-mapping.md) | An explicit claim-to-comparator mapping (`judged_as` in claims.yaml) | `accepted` 2026-10-18 by Le Chen; implemented by the pull request that adds `judged_as` |
| [0007](0007-index-built-on-credits.md) | Credits for the libraries an index entry builds on (`built_on` in index-entry/2) | `accepted` 2026-10-18 by Le Chen, after its implementation had merged; implemented by the pull request that adds `built_on` (#267) |
| [0008](0008-github-actions-compute-receipt.md) | A compute receipt from a CI runner, GitHub Actions first (`gha-<run>-<attempt>`, the `ci` block, three corroboration levels) | `accepted` 2026-10-26 by Le Chen, with the recommended answers to its open questions; implemented by #338, #348, #355, #363 and #358 (stages S1 to S5, [#318](https://github.com/lean-pkg/lean-pkg/issues/318)); a live run in a public sample repository still to do |
| [0009](0009-extraction-ledger.md) | Declaration-level extraction with a rename map and an extraction ledger (`lean-pkg-extract ledger`, `decls`, `recheck`; `lean-pkg-extract-ledger/1`) | `accepted` 2026-10-26 by Le Chen, as written, with the recommended answers to its open questions; implementation in stages S1 to S5, tracked in [#329](https://github.com/lean-pkg/lean-pkg/issues/329) |
| [1.0 command tree](../design/1.0-command-tree.md) | The 1.0 command tree and format freeze (written before this process; kept at its path) | `accepted` 2026-10-17 by Le Chen; implementation in stages (stage 1: aliases, noun moves, real subparsers, newcomer page) |
