---
title: Lifecycle
---

[&larr; lean-pkg documentation](index.md)

*Verified before it goes public. One package, one workspace, at a time.*

## One registry, append-only history

A single YAML registry (`version: 3`, a `packages` map; `lean-pkg migrate`
upgrades a `version: 1` or `version: 2` file in place, additively) is the source of
truth. Each entry records the repository URL, a `$HOME`-relative
`local_path`, visibility, status, the current lease, the Lean toolchain and
Mathlib pin (detected at `register`), the upstream target
(`none | mathlib | palomar`), an `automation` AI-disclosure note, the `gate`
evidence pointers (`receipt`, `comparator`, `axioms_allowed`), the v2
publication fields (`license_spdx`, `formalization_yaml`, `palomar[]`, `doi`,
`reservoir`, `consent[]`; empty until recorded), the v3 claims cache
(`claims_file`, `claims[]`, `claims_synced_commit`; written by
`lean-pkg claims sync`, see [Prior-art search](prior-art.md)), the optional
`paper` (`{arxiv, status}` for a package that verifies a paper, status
`unpublished | preprint | published`; `promote` refuses while it is
unpublished with no arXiv id), promotion
stamps, a `reaudit_after` date, notes, and `history[]`.

Every transition appends one entry to `history[]` and one JSON line to the
event log. History is never rewritten: a repair is a new event. GitHub is
only a visibility mirror; the registry wins conflicts, except under
`audit --fix`, which copies GitHub's visibility and archived flag into the
registry.

## Status enum

```
scratch | active | gate-pending | verified-private | public | upstreamed | archived
```

| Transition | Made by | Evidence |
|---|---|---|
| scratch → active | `update --status` | owner named |
| active → gate-pending | `update --status` | lower-tier check submitted |
| gate-pending → active | `update --status` | check abandoned |
| gate-pending → verified-private | `gate` | a valid lower-tier receipt, optionally with comparator evidence |
| public → public | `gate` | a package that was public before it was ever gated (for example a collaborator-owned repository): the receipt and the `gate-passed` event are recorded, the status does not change |
| verified-private → gate-pending | `update --status` | re-certification after a content change |
| verified-private → verified-private | `gate --regate` | re-certification in one step: a valid receipt for a commit descending from the gated one (or any commit with `--reason`); event `regated` |
| verified-private → public | `promote --yes` | clean checklist: a byte-valid comparator PASS, both receipts and every comparator log committed at HEAD, HEAD contained in `origin/<branch>` |
| public → upstreamed | `update --status` | a recorded `mathlib-pr-merged` or `palomar-registered` event (not retracted, not withdrawn), or a `--reason` containing the URL |
| any status → archived | `update --status --reason`, or `audit --fix` from GitHub's archived flag | — |

Public is one-way: from `public` the only moves are to `upstreamed` and
`archived`. A refused move names the moves allowed from the current status.

`register` starts a package at `scratch`, `active` or `gate-pending`, or at
`public` for a repository that is already public (`--visibility public` and a
`--reason` are both required). It cannot start one at `verified-private` or
`upstreamed`. `validate` refuses a `verified-private` package without a
`gate.receipt`; `audit` reports `status-visibility-mismatch` for a
`verified-private` package that is not private, or a `public`/`upstreamed`
one that is not public.

## Leases

One package has one workspace at a time. `claim` is compare-and-swap: it
refuses when a different agent holds an unexpired lease and names the holder
and the expiry. A same-agent re-claim refreshes the lease. `--force`
requires `--reason` and is logged as `claim-forced`. `release` is holder-only
unless forced. Leases expire after a TTL (24 hours by default). `renew`
restarts the holder's live lease from now (`claim-refreshed`) and refuses
once it has lapsed, so work longer than the TTL renews before then;
`leases --agent A --expiring 2` lists what is about to lapse. `audit` reports
expired leases; `audit --fix` clears them with a `lease-expired` event.

## Events

Each event line is one JSON object: `ts`, `event`, `name`, plus
event-specific fields. Event names: `registry-created`, `registered`,
`updated`, `claimed`, `claim-refreshed`, `claim-forced`, `released`,
`release-forced`, `lease-expired`, `gate-passed`, `regated`, `comparator-attached`,
`promoted`, `audit-fix`, `registry-migrated`, `index-added` (event log only,
not `history[]`), `evidence-retracted`, and the evidence events written
by `record`: `consent-recorded`, `palomar-preflight-pass`, `palomar-submitted`,
`palomar-review`, `palomar-registered`, `palomar-withdrawn`, `doi-minted`,
`reservoir-listed`, `mathlib-pr-opened`, `mathlib-pr-merged`. Package events
are also appended to the package's `history[]`; `index-added`,
`prior-art-searched`, `registry-created` and `registry-migrated` are in the
event log only. Timestamps are UTC ISO-8601 with an offset, second
precision; same-second events keep file order.

## Non-interactive by default

No code path blocks on stdin off a terminal. The curses board starts only
when both stdin and stdout are terminals; otherwise `board` prints the
static table. Prompt paths refuse with exit code 2. `--json` gives
machine-readable output on every subcommand with unchanged exit codes:
0 success or dry run, 1 refusal, 2 usage error.

Next: [Receipts and trust](receipts.md).
