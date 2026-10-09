---
title: CLI reference
---

[&larr; lean-pkg documentation](index.md)

Global flags: `--registry PATH`, `--events PATH`, `--json`, `--commit`,
`--push`, `--no-commit`, `--version` (prints `lean-pkg <version>`, exit 0).
Every subcommand also accepts `--json`, `--commit`, `--push` and `--no-commit`
after its name.
Committed mode (`--commit`, or env `LEAN_PKG_COMMIT=1`) applies to invocations
that write, decided from the parsed arguments: every write command, `index add`,
`formalization --write`, `claims sync`, `search --record` and the TUI's keys
(committed after each write); dry runs and read-only commands never fetch, and
an explicit `--commit`/`--push` there prints `ignored: read-only command` on
stderr. Before writing it requires the registry inside a git work tree
on a branch with the registry file and event log unmodified, fetches `origin`
and fast-forwards (`pull --ff-only`; a diverged clone is refused); after a
successful write it commits exactly those two files together with the message
`lean-pkg: <event> <name>`. `--push` (env `LEAN_PKG_PUSH=1`) also pushes to
`origin`; a push rejected as non-fast-forward (origin moved on since the
pull) is retried once: fetch, rebase that one commit onto `origin/<branch>`
(the event log merges as a union of appended lines), push again; the retry is
skipped when other local commits are unpushed, and a rebase conflict is
aborted. A failed push exits 1 and keeps the commit. `--no-commit` overrides
both variables. Its reports go to stderr; a `--json` payload gains
`"commit": {"sha", "pushed", "error"}` (`"ok": false` when the commit or push
failed); a commit made with the fallback identity `lean-pkg <lean-pkg@localhost>`
says so on stderr.
The registry is `--registry PATH`, else env
`LEAN_PKG_REGISTRY`, else the user-level default `~/.lean-pkg/registry` (base
directory env `LEAN_PKG_HOME`; a directory or symlink to one holding
`lean-packages.yaml`, or the registry file itself), else `./lean-packages.yaml`;
the event log is `--events PATH`, else `lean-package-events.jsonl` beside an
explicit `--registry`, else env `LEAN_PKG_EVENTS`, else the sibling of whichever
registry is in use. Exit codes: 0 success or dry run,
1 refusal or validation failure (reason on stderr; with `--json` also
`{"ok": false, "error": ...}` on stdout), 2 usage error or a prompt attempted
off a terminal (with `--json` a usage refusal also prints
`{"ok": false, "error": ..., "usage": true}`).

The authoritative, machine-oriented version of this page is
[llms.txt](llms.txt), which is the exact output of `lean-pkg llms`.

| Subcommand | Purpose | Key flags |
|---|---|---|
| `board` | curses TUI on a terminal; static table otherwise | |
| `list` | static table, script-friendly | `--json` |
| `status NAME` | full record plus the last five history entries | |
| `init` | bootstrap an empty registry file and its event log; refuses an existing registry unless `--force` and it holds no packages; the `registry-created` event records the registry file name and schema version (no host path), and `"forced": true` after `--force` | `[--force]` |
| `migrate` | upgrade the registry schema in place to version 3; v1 to v2 adds `license_spdx`, `formalization_yaml`, `palomar[]`, `doi`, `reservoir`, `consent[]`, v2 to v3 adds `claims_file`, `claims[]`, `claims_synced_commit`, all with empty defaults; additive and idempotent; the `registry-migrated` event records it | `[--dry-run]` |
| `record NAME EVENT` | record a publication evidence event with its required fields: `consent-recorded`, `palomar-preflight-pass`, `palomar-submitted`, `palomar-review`, `palomar-registered`, `palomar-withdrawn`, `doi-minted`, `reservoir-listed` (verified against Reservoir's package API: a 404 refuses, an unreachable network records `verified: false`), `mathlib-pr-opened`, `mathlib-pr-merged`; each updates the matching v2 field (the `mathlib-pr-*` events only append to history) and appends to history. The would-be registry is validated first: an event that introduces a schema error is refused with the rule named and nothing is written (rule doi-in-palomar: a `doi-minted` commit must already be recorded by a `palomar-*` event). Fields are checked first, each refusal naming its rule: `doi` (and `concept`) must look like a DOI, `10.<4-9 digits>/<suffix>` (doi-format); a `mathlib-pr-*` `url` must be an `https://github.com/<owner>/<repo>/...` URL (mathlib-pr-url); `palomar-registered` needs status `public` or `upstreamed` (palomar-registered-public) and a `comparator_config` tracked at the package's HEAD (palomar-config-tracked); a `version` must be an integer, compared as one (`01` is `1`) by `palomar-withdrawn` (palomar-version). A `reservoir-listed` url that names neither the package nor its repository is recorded with a warning (stderr and `notes`); the same `palomar-preflight-pass` commit and run again is a no-op, exit 0 (`"already_recorded": true`); a field holding a local absolute path (`/home/`, `/Users/`, `~`) is recorded with a stderr WARN and listed in `private_strings`. `record NAME retract --field event=E --field ts=T --reason TEXT` marks an earlier evidence entry retracted, append-only (event `evidence-retracted`); `validate` and the upstreamed check ignore it | `--field KEY=VALUE` (repeatable) `[--reason]` |
| `register NAME` | the only write path for new packages; validated, deduping by name and by resolved checkout (`~/` expanded, relative paths taken from `$HOME`, stored `$HOME`-relative), auto-detects toolchain and Mathlib pin; a Lean root namespace (each `lean_lib` in `lakefile.lean` or `lakefile.toml`, else the top-level `*.lean` files) that another registered package's checkout also uses is a stderr note and a `notes` entry in `--json`, never a refusal; an empty `--path`, `--repo` or `--owner-project`, or a path resolving to `$HOME`, is refused | `--repo URL` `--path HOME-RELATIVE` `[--owner-project]` `[--visibility]` `[--status scratch|active|gate-pending|public]` (`public` needs `--visibility public` and `[--reason]`) `[--upstream-target none|mathlib|palomar]` `[--automation]` `[--notes]` `[--dry-run]` |
| `update NAME` | set fields; manual status moves and `--force` need `--reason` (else a refusal, exit 1); empty `--repo`/`--owner-project` are refused (`--notes ''` clears); `--license-spdx` takes an SPDX identifier or expression (`MIT OR Apache-2.0`); `--path` uses the `register` normaliser and the current checkout is a no-op (exit 0); `public` to `upstreamed` needs a recorded `mathlib-pr-merged`/`palomar-registered` event or a URL in `--reason`; any status may move to `archived`; a refused move lists the allowed ones; schema errors already in the registry (another package's) are reported on stderr as pre-existing and do not block (same for `register` and `record`) | `--status` `--reason` `--automation` `--notes` `--upstream-target` `--reaudit-after` `--owner-project` `--repo` `--redetect` `--agent` `--force` `[--license-spdx SPDX]` `[--formalization-yaml REL]` `[--path HOME-RELATIVE --reason TEXT [--dry-run]]` (relocate: event `relocated`) |
| `claim NAME` | compare-and-swap lease, 24 h default for a new claim; the holder's re-claim refreshes it and keeps its TTL unless `--ttl` is given (event `claim-refreshed`, with the reason when given) | `--workspace W` `--agent A` `[--ttl HOURS]` `[--reason]` `[--force]` |
| `release NAME` | release a lease; holder-only unless forced; `--force` needs `--reason`; `released_by` is the acting `--agent`, else `force` | `[--agent]` `[--force --reason TEXT]` |
| `renew NAME` | restart your own live lease from now (event `claim-refreshed`); refuses when there is no lease, another agent holds it, or it has already lapsed (claim again; `audit --fix` sweeps it) | `--agent A` `[--ttl HOURS]` `[--workspace W]` `[--reason]` |
| `leases` | held leases, soonest expiry first, with `live` / `EXPIRED` state; read-only | `[--agent A]` `[--expiring HOURS]` (HOURS >= 0; a negative window exits 2) |
| `gate NAME` | validate a lower-tier receipt; `gate-pending → verified-private`; identical re-gate is a no-op; receipt bytes (keys and values) are scanned for private local paths before the copy into `.lean-receipts/` (stderr `WARN` naming the field, `private_strings` in JSON); `lean-toolchain` is read at the receipt's commit; modified tracked files print a `WARN` (`dirty` in JSON) | `--receipt PATH` `[--comparator PATH]` `[--refuse-private]` (refuse, exit 1, nothing copied) `[--refuse-dirty]` |
| `comparator NAME` | attach comparator evidence to a gated package (event `comparator-attached`), or validate only; same private-string scan and dirty-tree `WARN` as `gate`; without `--receipt` a usage error (exit 2) | `--receipt PATH` `[--check]` `[--refuse-private]` `[--refuse-dirty]` |
| `comparator host-check` | read-only, needs no registry: can this host produce comparator receipts? One `ok`/`warn`/`fail` line each for `kernel`, `landlock` (ABI; below 4 is a `warn`), `bwrap` (user namespace), `go`, `cargo`, `jq`, `git`, `lean`, `lake`, then a verdict naming the usable mechanisms; exit 0 when one is usable, else 1; see [The comparator tier](comparator.md#checking-a-host-first) | `[--json]` |
| `promote NAME` | promotion checklist; dry run unless `--yes`; the flip is one-way. The checklist requires both receipts and every comparator log committed at HEAD unmodified, and HEAD contained in `origin/<branch>` (local refs, never fetched); under `--yes` a failed `gh` query is a failure | `[--yes]` |
| `audit` | read-only without `--fix`: report expired leases, GitHub visibility and archived-flag drift, receipt, comparator, toolchain, formalization.yaml drift, toolchain floor; informational findings (never fixed): `receipt-untracked` (a recorded gate or comparator receipt exists in the working tree but is not tracked at the reference branch tip) and `duplicate-path` (two packages whose `local_path` resolve to the same checkout) and `duplicate-namespace` (two checkouts declaring the same Lean root namespace, which cannot be built or imported together); `--fix` clears expired leases and reconciles visibility/archived from GitHub | `[--fix]` `[--toolchain-floor VERSION]` |
| `validate` | schema, enum, and history sanity; exit 0 or 1 | |
| `doctor` | one `ok`/`warn`/`n/a` line each: version; install kind and path (an editable install inside a git checkout adds its short HEAD, branch, and behind/ahead counts against `origin/<branch>`, from local refs unless `--fetch`); registry path and schema version, ending `[source: flag|env|user-default|cwd]` (`doctor --json` adds `"source"` to that check); `user-default`: what `~/.lean-pkg/registry` points at (`ok`) or `n/a` when it is not set up; events path, ending `[source: flag|registry-sibling|env|sibling]`; `registry-repo`: the git work tree holding the registry with its HEAD, branch, behind/ahead of `origin/<branch>` (or "no origin remote" when there is none) and uncommitted tracked files, naming the registry files among them (`warn` when behind, ahead, detached, or the registry files are uncommitted: do not write from there yet; `n/a` outside a work tree); `leases`: live count, leases lapsing within 2 h and expired-but-unswept ones by name, and leases whose fields cannot be parsed (`warn` when any is non-empty); `time`: naive (offset-less, legacy) lease `claimed_at` / history `ts` values, read as UTC (`warn` when any); index path and entry count, ending `[source: flag|env|registry-sibling|cwd]`; `host`: the `comparator host-check` verdict (`warn` when this host cannot produce comparator receipts); `lake`, `lean`, `elan`, `git`, `gh`, `reuse`, `bwrap`, `comparator`, `nanoda`, `go`, `cargo`, `jq` on PATH (`go`, `cargo` and `jq` are what `comparator-receipt` needs to build the legacy comparator tools; a `lake` or `lean` that is not elan's proxy in `$ELAN_HOME/bin`, default `~/.elan/bin`, is a `warn`, since it ignores each project's `lean-toolchain`); `gh auth`: the github.com account from `gh auth status` (`warn` when gh is present but not logged in, since `promote` and `audit` need it; `n/a` without gh); Python version. A check that raises prints `warn <item>: internal error ...` and the others still run. Exit 1 when the registry is unreadable or its schema version is unsupported (a missing registry counts as unreadable), else 0 | `[--fetch]` |
| `llms` | print the agent contract (`--json`: `{"ok": true, "text": ...}`) | |
| `wiki` | render a Markdown status page | `[--out PATH]` `[--candidates PATH]` |
| `index add NAME` | write `index/<name>/entry.yaml` from a clone at the pin: receipts validated with the `gate` and `comparator` validators and copied verbatim, trust computed, duplicates refused, event `index-added` | `--by HANDLE` `--headline 'NAME :: FILE :: SUMMARY'` `[--from-registry]` `[--repo URL]` `[--repo-dir PATH]` `[--lower REL]` `[--comparator REL]` `[--license SPDX]` `[--tag T]` `[--field-tag T]` `[--formalization-yaml REL]` `[--consent-recorded]` `[--notes]` `[--index PATH]` |
| `index check [NAME ...]` | index root: `--index`, else env `LEAN_PKG_INDEX`, else `index/` beside the registry when present, else `./index`; `check`, `render` and `migrate` exit 1 with `no index at <path>` when it is missing (`render` creates it only for an explicit `--index` or `--out`). Static checks, or re-derive each entry from bytes at the pin and recompute trust; Palomar and Reservoir verified when reachable, else skipped; exit 0, 1 or 2 | `[--clone]` `[--repo-dir PATH]` `[--index PATH]` |
| `index render` | write the Markdown table (default `index/README.md`), plus a claims list when `index-entry/2` entries exist | `[--out PATH]` `[--check]` `[--index PATH]` |
| `index migrate` | rewrite every `index-entry/1` entry as `index-entry/2`, deriving `claims` from its headline; nothing else changes; `--check` exits 1 when an entry is not current and writes nothing | `[--check]` `[--index PATH]` |
| `claims sync NAME` | read `claims.yaml` (schema `claims/1`) at `HEAD` of the checkout, check every `decl` occurs in its `file`, cache the claims and the commit in the registry; event `claims-synced`; identical re-sync is a no-op | |
| `claims list [NAME]` | table of cached claims: id, kind, status, trust (derived from the receipts), pin, declaration | |
| `claims check [NAME]` | re-validate cached claims (schema, unique ids, acyclic supersession, declarations at the synced commit); exit 0 or 1 | |
| `formalization NAME` | render `formalization.yaml` (mathlib-initiative v0.4, what Palomar reads) from the registry and the repo at HEAD; stdout by default; `--write` writes `<repo>/<formalization_yaml or formalization.yaml>`, is a no-op when equal, refuses a differing file without `--force`, then sets the registry pointer (event `updated`), so `--write` is a registry write; `--write --no-registry` writes the file only and leaves the registry untouched (no pointer, no event, no lock, `--commit` ignored; `"registry_skipped": true`); the lease (another agent's live lease refuses unless `--force --reason`) and a dry run of the registry update are checked before the file is written; validated against the vendored schema; see [formalization.yaml](formalization.md) | `[--write [--no-registry]]` `[--out PATH]` `[--force [--reason TEXT]]` `[--author NAME]…` `[--agent A]` |
| `search TERM` | prior-art search in the fixed order registry, index, loogle, leansearch, reservoir, palomar, github; each source `ok` or `skipped: <reason>`; the registry source scores each package's name (exact name 3), notes, owner_project and repo as well as its cached claims, and every local hit carries `hit_kind` (`package` or `claim`) and `repo`; exit 0 even with zero hits; see [Prior-art search](prior-art.md) | `[--package NAME]` `[--offline]` `[--sources a,b,c]` `[--record]` `[--repo-dir PATH]` `[--index PATH]` |

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
branch from `refs/remotes/origin/HEAD`, else `main`, else `master`, else HEAD; local refs
only, never fetched; of the local branch and `origin/<b>`, the tip that contains the other,
or origin with a note when they diverged), `checkout-branch` (informational: the checkout is on another branch than the
one judged), `formalization-missing`, `formalization-drift` (the written
`formalization.yaml` no longer equals what `lean-pkg formalization` emits; see
[formalization.yaml](formalization.md)), `visibility-mismatch`, `archived-upstream`,
`status-visibility-mismatch` (`verified-private` but not private, or `public`/`upstreamed` but not public),
`below-toolchain-floor` (the registry's `lean_toolchain` is older than the floor),
`toolchain-unparsed` (a nightly or other string that is not `vMAJOR.MINOR.PATCH[-rcN]`;
never compared), `no-github-mirror` (the `repo` is not a GitHub URL, so visibility is not
mirrored; the detail never echoes the path, and the human output collapses these into one
line "N package(s) without a GitHub mirror (visibility not checked): a, b" while `--json`
keeps one finding per package), and `warn` for GitHub queries that could not be made.

The toolchain floor is `--toolchain-floor VERSION`, else the environment variable
`LEAN_PKG_TOOLCHAIN_FLOOR`, else `leanprover/lean4:v4.35.0-rc2` (Palomar's floor: the first
toolchain with `lake comparator`). An rc is older than the release of the same version.
`scratch` and `archived` packages are exempt. An unparsable floor is a usage error (exit 2). Only `--fix` mutates visibility
and archived state; lease sweeps always apply.
