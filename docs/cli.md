---
title: CLI reference
---

[&larr; lean-pkg documentation](index.md)

Global flags: `--registry PATH`, `--events PATH`, `--json`, `--version` (prints
`lean-pkg <version>`, exit 0). Every subcommand
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
| `init` | bootstrap an empty registry file and its event log; refuses an existing registry unless `--force` and it holds no packages | `[--force]` |
| `migrate` | upgrade the registry schema in place to version 3; v1 to v2 adds `license_spdx`, `formalization_yaml`, `palomar[]`, `doi`, `reservoir`, `consent[]`, v2 to v3 adds `claims_file`, `claims[]`, `claims_synced_commit`, all with empty defaults; additive and idempotent; the `registry-migrated` event records it | `[--dry-run]` |
| `record NAME EVENT` | record a publication evidence event with its required fields: `consent-recorded`, `palomar-preflight-pass`, `palomar-submitted`, `palomar-review`, `palomar-registered`, `palomar-withdrawn`, `doi-minted`, `reservoir-listed` (verified against Reservoir's package API: a 404 refuses, an unreachable network records `verified: false`), `mathlib-pr-opened`; each updates the matching v2 field (`mathlib-pr-opened` only appends to history) and appends to history | `--field KEY=VALUE` (repeatable) `[--reason]` |
| `register NAME` | the only write path for new packages; validated, deduping, auto-detects toolchain and Mathlib pin | `--repo URL` `--path HOME-RELATIVE` `[--owner-project]` `[--visibility]` `[--status]` `[--upstream-target none|mathlib|palomar]` `[--automation]` `[--notes]` `[--dry-run]` |
| `update NAME` | set fields; manual status moves need `--reason` | `--status` `--reason` `--automation` `--notes` `--upstream-target` `--reaudit-after` `--owner-project` `--repo` `--redetect` `--agent` `--force` `[--license-spdx SPDX]` `[--formalization-yaml REL]` |
| `claim NAME` | compare-and-swap lease, 24 h default | `--workspace W` `--agent A` `[--ttl HOURS]` `[--reason]` `[--force]` |
| `release NAME` | release a lease; holder-only unless forced | `[--agent]` `[--force]` |
| `gate NAME` | validate a lower-tier receipt; `gate-pending → verified-private`; identical re-gate is a no-op; receipt bytes are scanned for private local paths before the copy into `.lean-receipts/` (stderr `WARN` naming the field, `private_strings` in JSON) | `--receipt PATH` `[--comparator PATH]` `[--refuse-private]` (refuse, exit 1, nothing copied) |
| `comparator NAME` | attach comparator evidence to a gated package (event `comparator-attached`), or validate only; same private-string scan as `gate` | `--receipt PATH` `[--check]` `[--refuse-private]` |
| `promote NAME` | promotion checklist; dry run unless `--yes`; the flip is one-way | `[--yes]` |
| `audit` | sweep expired leases; reconcile GitHub visibility and archived flag; receipt, comparator, toolchain, formalization.yaml drift; toolchain floor | `[--fix]` `[--toolchain-floor VERSION]` |
| `validate` | schema, enum, and history sanity; exit 0 or 1 | |
| `doctor` | one `ok`/`warn`/`n/a` line each: version; install kind and path (an editable install inside a git checkout adds its short HEAD, branch, and behind/ahead counts against `origin/<branch>` from local refs only, never fetched); registry path and schema version; events path; index path and entry count; `lake`, `elan`, `git`, `gh`, `reuse`, `bwrap`, `comparator`, `nanoda` on PATH; Python version. Exit 0 unless the registry is unreadable (a missing registry counts) | |
| `llms` | print the agent contract | |
| `wiki` | render a Markdown status page | `[--out PATH]` `[--candidates PATH]` |
| `index add NAME` | write `index/<name>/entry.yaml` from a clone at the pin: receipts validated with the `gate` and `comparator` validators and copied verbatim, trust computed, duplicates refused, event `index-added` | `--by HANDLE` `--headline 'NAME :: FILE :: SUMMARY'` `[--from-registry]` `[--repo URL]` `[--repo-dir PATH]` `[--lower REL]` `[--comparator REL]` `[--license SPDX]` `[--tag T]` `[--field-tag T]` `[--formalization-yaml REL]` `[--consent-recorded]` `[--notes]` `[--index PATH]` |
| `index check [NAME ...]` | static checks, or re-derive each entry from bytes at the pin and recompute trust; Palomar and Reservoir verified when reachable, else skipped; exit 0, 1 or 2 | `[--clone]` `[--repo-dir PATH]` `[--index PATH]` |
| `index render` | write the Markdown table (default `index/README.md`), plus a claims list when `index-entry/2` entries exist | `[--out PATH]` `[--check]` `[--index PATH]` |
| `index migrate` | rewrite every `index-entry/1` entry as `index-entry/2`, deriving `claims` from its headline; nothing else changes; `--check` exits 1 when an entry is not current and writes nothing | `[--check]` `[--index PATH]` |
| `claims sync NAME` | read `claims.yaml` (schema `claims/1`) at `HEAD` of the checkout, check every `decl` occurs in its `file`, cache the claims and the commit in the registry; event `claims-synced`; identical re-sync is a no-op | |
| `claims list [NAME]` | table of cached claims: id, kind, status, trust (derived from the receipts), pin, declaration | |
| `claims check [NAME]` | re-validate cached claims (schema, unique ids, acyclic supersession, declarations at the synced commit); exit 0 or 1 | |
| `formalization NAME` | render `formalization.yaml` (mathlib-initiative v0.4, what Palomar reads) from the registry and the repo at HEAD; stdout by default; `--write` writes `<repo>/<formalization_yaml or formalization.yaml>`, is a no-op when equal, refuses a differing file without `--force`, then sets the registry pointer (event `updated`); validated against the vendored schema; see [formalization.yaml](formalization.md) | `[--write]` `[--out PATH]` `[--force]` `[--author NAME]…` `[--agent A]` |
| `search TERM` | prior-art search in the fixed order registry, index, loogle, leansearch, reservoir, palomar, github; each source `ok` or `skipped: <reason>`; exit 0 even with zero hits; see [Prior-art search](prior-art.md) | `[--package NAME]` `[--offline]` `[--sources a,b,c]` `[--record]` `[--repo-dir PATH]` `[--index PATH]` |

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
`comparator-drift` (both judged against the reference branch: the remote default
branch from `refs/remotes/origin/HEAD`, else `main`, else HEAD; local refs only, never
fetched), `checkout-branch` (informational: the checkout is on another branch than the
one judged), `formalization-missing`, `formalization-drift` (the written
`formalization.yaml` no longer equals what `lean-pkg formalization` emits; see
[formalization.yaml](formalization.md)), `visibility-mismatch`, `archived-upstream`,
`below-toolchain-floor` (the registry's `lean_toolchain` is older than the floor),
`toolchain-unparsed` (a nightly or other string that is not `vMAJOR.MINOR.PATCH[-rcN]`;
never compared), and `warn` for GitHub queries that could not be made.

The toolchain floor is `--toolchain-floor VERSION`, else the environment variable
`LEAN_PKG_TOOLCHAIN_FLOOR`, else `leanprover/lean4:v4.35.0-rc2` (Palomar's floor: the first
toolchain with `lake comparator`). An rc is older than the release of the same version.
`scratch` and `archived` packages are exempt. An unparsable floor is a usage error (exit 2). Only `--fix` mutates visibility
and archived state; lease sweeps always apply.
