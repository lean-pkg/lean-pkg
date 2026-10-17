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
| [0002](0002-committed-docs-only-opt-in.md) | A committed docs-only opt-in, read at the bound commit | draft |
| [1.0 command tree](../design/1.0-command-tree.md) | The 1.0 command tree and format freeze (written before this process; kept at its path) | `accepted` 2026-10-17 by Le Chen; implementation in stages (stage 1: aliases, noun moves, real subparsers, newcomer page) |
