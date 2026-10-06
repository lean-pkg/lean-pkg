---
title: Lifecycle
---

[&larr; lean-pkg documentation](index.md)

*Verified before it goes public. One package, one workspace, at a time.*

## One registry, append-only history

A single YAML registry (`version: 1`, a `packages` map) is the source of
truth. Each entry records the repository URL, a `$HOME`-relative
`local_path`, visibility, status, the current lease, the Lean toolchain and
Mathlib pin (detected at `register`), the upstream target
(`none | mathlib | palomar`), an `automation` AI-disclosure note, the `gate`
evidence pointers (`receipt`, `comparator`, `axioms_allowed`), promotion
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
| verified-private → gate-pending | `update --status` | re-certification after a content change |
| verified-private → public | `promote --yes` | clean checklist, including a byte-valid comparator PASS |
| public → upstreamed | maintainer | Mathlib PR merged or Palomar entry |
| scratch / active / gate-pending / verified-private → archived | `update --status`, or `audit --fix` from GitHub's archived flag | — |

## Leases

One package has one workspace at a time. `claim` is compare-and-swap: it
refuses when a different agent holds an unexpired lease and names the holder
and the expiry. A same-agent re-claim refreshes the lease. `--force`
requires `--reason` and is logged as `claim-forced`. `release` is holder-only
unless forced. Leases expire after a TTL (24 hours by default); `audit`
clears expired ones with a `lease-expired` event.

## Events

Each event line is one JSON object: `ts`, `event`, `name`, plus
event-specific fields. Event names: `registry-created`, `registered`,
`updated`, `claimed`, `claim-refreshed`, `claim-forced`, `released`,
`release-forced`, `lease-expired`, `gate-passed`, `comparator-attached`,
`promoted`, `audit-fix`. The same entry is appended to the package's
`history[]`.

## Non-interactive by default

No code path blocks on stdin off a terminal. The curses board starts only
when both stdin and stdout are terminals; otherwise `board` prints the
static table. Prompt paths refuse with exit code 2. `--json` gives
machine-readable output on every subcommand with unchanged exit codes:
0 success or dry run, 1 refusal, 2 usage error.

Next: [Receipts and trust](receipts.md).
