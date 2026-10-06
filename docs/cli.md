---
title: CLI reference
---

[&larr; lean-pkg documentation](index.md)

Global flags: `--registry PATH`, `--events PATH`, `--json`. Every subcommand
also accepts `--json` after its name. Exit codes: 0 success or dry run,
1 refusal or validation failure (reason on stderr; with `--json` also
`{"ok": false, "error": ...}` on stdout), 2 usage error or a prompt attempted
off a terminal.

The authoritative, machine-oriented version of this page is
[llms.txt](llms.txt), which is the exact output of `lean-pkg llms`.

| Subcommand | Purpose | Key flags |
|---|---|---|
| `board` | curses TUI on a terminal; static table otherwise | |
| `list` | static table, script-friendly | `--json` |
| `status NAME` | full record plus the last five history entries | |
| `register NAME` | the only write path for new packages; validated, deduping, auto-detects toolchain and Mathlib pin | `--repo URL` `--path HOME-RELATIVE` `[--owner-project]` `[--visibility]` `[--status]` `[--upstream-target none|mathlib|palomar]` `[--automation]` `[--notes]` `[--dry-run]` |
| `update NAME` | set fields; manual status moves need `--reason` | `--status` `--reason` `--automation` `--notes` `--upstream-target` `--reaudit-after` `--owner-project` `--repo` `--redetect` `--agent` `--force` |
| `claim NAME` | compare-and-swap lease, 24 h default | `--workspace W` `--agent A` `[--ttl HOURS]` `[--reason]` `[--force]` |
| `release NAME` | release a lease; holder-only unless forced | `[--agent]` `[--force]` |
| `gate NAME` | validate a lower-tier receipt; `gate-pending → verified-private`; identical re-gate is a no-op | `--receipt PATH` `[--comparator PATH]` |
| `comparator NAME` | attach comparator evidence to a gated package (event `comparator-attached`), or validate only | `--receipt PATH` `[--check]` |
| `promote NAME` | promotion checklist; dry run unless `--yes`; the flip is one-way | `[--yes]` |
| `audit` | sweep expired leases; reconcile GitHub visibility and archived flag; receipt, comparator, toolchain drift | `[--fix]` |
| `validate` | schema, enum, and history sanity; exit 0 or 1 | |
| `llms` | print the agent contract | |
| `wiki` | render a Markdown status page | `[--out PATH]` `[--candidates PATH]` |

`comparator-receipt [--project DIR] [--check SCRIPT]` is the separate console
script that produces comparator receipts; see
[The comparator tier](comparator.md).

## Promotion checklist lines

| Line | Meaning |
|---|---|
| REUSE lint | `reuse lint` in the repository (WARN when `reuse` is not installed) |
| LICENSE/COPYING present | GitHub must detect an OSI license for Reservoir indexing |
| `lake-manifest.json` tracked at HEAD | Reservoir requires a root manifest |
| gate receipt bound to HEAD | HEAD equals the receipt commit or is an evidence-only descendant |
| comparator PASS bound to HEAD | the comparator receipt re-validates from bytes |
| gh visibility matches registry | GitHub and the registry agree |
| automation disclosure | required when `upstream_target` is `mathlib` |

Any `✗` refuses; `WARN` does not.

## Audit findings

`lease-expired` (cleared, event appended), `missing-path`, `toolchain-drift`,
`toolchain-missing`, `receipt-missing`, `receipt-drift`, `comparator-missing`,
`comparator-drift`, `visibility-mismatch`, `archived-upstream`, and `warn`
for GitHub queries that could not be made. Only `--fix` mutates visibility
and archived state; lease sweeps always apply.
