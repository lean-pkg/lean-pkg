---
title: CLI reference
---

[&larr; lean-pkg documentation](index.md)

This page covers what every command shares: the global flags, committed mode,
the registry and event-log locations, leases, the package record and the exit
codes. **Every command and subcommand has its own page in the
[command reference](commands/index.md)**, with its usage line, every flag, its
exit codes, its `--json` keys and examples; those pages are generated from the
parser (`make docs`).

## Global flags

Global flags: `--registry PATH`, `--events PATH`, `--json`, `--commit`,
`--push`, `--no-commit`, `--version` (prints which build runs, exit 0:
`lean-pkg X.Y.Z (<short-sha>[+dirty], editable <checkout>)` for an editable
install, `lean-pkg X.Y.Z (installed <package dir>)` for a wheel or pipx install,
`lean-pkg X.Y.Z (unknown build, <package dir>)` without git; the same detection
as `doctor`'s `install` line, local only, never a fetch; with `--json`:
`{"ok": true, "version", "commit", "dirty", "install", "path"}`, where
`install` is `editable`, `installed` or `unknown` and `commit`/`dirty` are null
outside an editable checkout).
Every subcommand also accepts `--json`, `--commit`, `--push` and `--no-commit`
after its name.
`--registry-snapshot COMMIT` (env `LEAN_PKG_REGISTRY_SNAPSHOT`; the flag wins)
runs a read-only command against the registry as it was at `COMMIT` of the git
repository that holds the registry file: `lean-pkg` reads `lean-packages.yaml`
and, when that commit has it, the event log with `git show COMMIT:<path>` into a
temporary copy (a commit without the log reads as an empty log, never the live
one), runs the command against the copy and deletes it. `COMMIT` is anything
`git rev-parse` accepts (a sha, a tag, `HEAD~3`). Reads work: `status`, `list`,
`board` (always the static view), `leases`, `audit` without `--fix`,
`validate`, `wiki`, dry runs, and `comparator config ... --dry-run` (useful with
`--checkout DIR` on a copy at the commit the claims were synced at back then);
the index and the adoption ledger are still read beside the working-tree
registry. Every invocation that would write the registry or its event log, and
so every commit and push, is refused before anything is read, exit 2:
``lean-pkg: read-only: --registry-snapshot: `<cmd>` would write the registry; a
snapshot is read-only (drop --registry-snapshot / unset
LEAN_PKG_REGISTRY_SNAPSHOT to write the current registry)`` (with `--json`
`{"ok": false, "error": ..., "usage": true}`). A registry outside a git work
tree, an unknown commit, or a registry file absent at that commit is a refusal
(exit 1). stderr notes `registry snapshot <sha12> (read-only)`; a `--json`
payload that is one object gains `"registry_snapshot": "<full sha>"`.
Committed mode (`--commit`, or env `LEAN_PKG_COMMIT=1`) applies to invocations
that write, decided from the parsed arguments: every write command, `index add`,
`formalization --write`, `claims sync`, `search --record` and the TUI's keys
(committed after each write); dry runs and read-only commands never fetch, and
an explicit `--commit`/`--push` there prints `ignored: read-only command` on
stderr. Before writing it requires the registry inside a git work tree
on a branch with the registry file and event log unmodified (the refusal names the
uncommitted event, its agent and time, the package's lease holder, and the `git commit`
that clears it), fetches `origin`
and fast-forwards (`pull --ff-only`; a diverged clone is refused); after a
successful write it commits exactly those two files together with the message
`lean-pkg: <event> <name>`. `--push` (env `LEAN_PKG_PUSH=1`) also pushes to
`origin`. A push that fails with a transient transport error (connection
reset, `kex_exchange_identification`, `Could not read from remote`, a timeout,
HTTP 5xx) is retried with backoff: env `LEAN_PKG_PUSH_RETRIES` retries
(default 4), waiting `LEAN_PKG_PUSH_RETRY_DELAY` seconds (default 5) doubled
each time (5, 10, 20, 40 s); any other failure (authentication, a hook's
refusal) is not retried. A push rejected as non-fast-forward (origin moved on
since the pull, including GitHub's `[remote rejected] … (cannot lock ref …)`
for a concurrent update) fetches, rebases that one commit onto `origin/<branch>`
(`git rebase --autostash`, so unrelated modified files in the clone are set aside and restored) and
pushes again (as often as the retry budget allows, at least once); a conflict
only in the event log is resolved as a union (both sides' appended lines,
stable timestamp order), any other conflict aborts the rebase, leaving the
clone exactly as before (the commit kept, ahead), and the failure ends
`; rebase conflict in <files>`; the rebase is skipped when other local
commits are unpushed. A rebased registry is validated before it is pushed: when the merge of both
writes has a schema error neither side had, nothing is pushed, the clone is reset to the local
commit (`git reset --keep`) and the failure ends
`; rebased registry fails validation (N new error(s): ...)`. Each retry and rebase step is a stderr line. A failed push keeps the commit, prints `push failed: origin/<branch>: <git error>` and the
recovery (`git push origin <branch>`; after a rejection,
`git pull --rebase origin <branch>, then git push origin <branch>`; after a rebase that failed validation, reconcile the registry by hand), and exits 1; a `--push` write refuses while the clone is ahead of
`origin/<branch>` (an unpushed commit), naming the commit. `--no-commit` overrides
both variables. Its reports go to stderr: `committed <sha> on <branch>: <message>`, ending
`(committed mode: env LEAN_PKG_COMMIT=1)` or `(committed mode: env LEAN_PKG_PUSH=1)` when a
variable rather than a flag turned the mode on (a wrapper may set one, so `renew` or `claim`
can commit and push with no flag given), and `pushed origin/<branch> (<sha>)`; every
subcommand's `--commit`/`--push` help names its variable and says `set now` when it is set.
A write that runs without committed mode on a registry inside a git work tree and leaves its
registry file or event log modified ends with a stderr line
`NOT COMMITTED: <files> modified in <clone>; every --commit/--push write to this registry refuses until they are committed: git -C <clone> commit ...`,
and a `--json` payload gains `"registry_uncommitted": [<files>]`; a registry outside git gets no
notice.
A `--json` payload gains
`"registry_commit": {"sha", "pushed", "error", "push_error", "push_attempts", "rebased"}` (`"ok": false` when the commit or push
failed). The same object also appears as `"commit"` when the payload has no `"commit"` of its own (the
older key, kept for compatibility); a command's own `"commit"`, such as the source commit `gate` and
`comparator` report, is never replaced, so read `registry_commit`; a commit made with the fallback identity `lean-pkg <lean-pkg@localhost>`
says so on stderr.
The registry is `--registry PATH`, else env
`LEAN_PKG_REGISTRY`, else the user-level default `~/.lean-pkg/registry` (base
directory env `LEAN_PKG_HOME`; a directory or symlink to one holding
`lean-packages.yaml`, or the registry file itself), else `./lean-packages.yaml`;
the event log is `--events PATH`, else `lean-package-events.jsonl` beside an
explicit `--registry`, else env `LEAN_PKG_EVENTS`, else the sibling of whichever
registry is in use. A write checks that every event log it appends to can be
appended (an existing file opened for append, else a writable nearest existing directory) before
the registry file is replaced: a read-only log or a directory refuses, exit 1,
`event log <path> cannot be appended to (<why>); nothing written`, so a registry change never lands without its event
line.

The evidence writers `gate`, `comparator`, `record`, `promote --yes`,
`claims sync` and `search --record` take an optional `--agent A`. With it,
a live lease held by another agent is a refusal (exit 1, nothing written)
and `A` is stored as `actor` on the history entry and the event line.
Without it nothing is refused, as before, but a live lease prints a
warning naming the holder on stderr. `--check` and promote's dry run are
read-only and never refused for a lease.

Help topics, in the manner of `gh help <topic>`, collect what belongs to no
single command: `lean-pkg help exit-codes` (the exit codes and what 1 means for
each command that answers yes or no), `lean-pkg help environment` (every
environment variable lean-pkg, comparator-receipt and lean-exercise read, with
its default; `--json` adds a `variables` list) and `lean-pkg help formatting`
(`--json`, stdout and stderr, the named schemas, filtering with jq).
`lean-pkg help` lists the commands and topics; `lean-pkg help COMMAND` prints
exactly `lean-pkg COMMAND --help`.

The authoritative, machine-oriented version of this page is
[llms.txt](llms.txt), which is the exact output of `lean-pkg llms`.
## Exit codes

0 success or dry run, 1 refusal or validation failure (reason on stderr; with
`--json` also `{"ok": false, "error": ...}` on stdout), 2 usage error or a
prompt attempted off a terminal (with `--json` a usage refusal, argparse's own
errors included, also prints `{"ok": false, "error": ..., "usage": true}`).
What 1 means for the commands that answer yes or no, and the codes of the
other programs: `lean-pkg help exit-codes`; each command's page lists its own.

## Commands

The [command reference](commands/index.md) groups the commands by purpose and
has one page per command: [`lean-pkg gate`](commands/gate.md),
[`lean-pkg comparator`](commands/comparator.md) and so on, subcommands as
sections. The other console scripts have pages there too:
[`comparator-receipt`](commands/comparator-receipt.md),
[`lean-exercise`](commands/lean-exercise.md) and
[`lean-pkg-extract`](commands/lean-pkg-extract.md).

Since the 1.0 command tree (stage 1), coordination and maintenance commands
sit under the `lease`, `registry` and `repo` nouns. Every older spelling
(`claim`, `init`, `audit --fix`, `--track`, `receipt slurm --commit`, ...)
is a permanent alias with the same output and exit code; text mode adds one
stderr note after the command (`LEAN_PKG_NO_ALIAS_NOTE=1` silences it).
`lean-pkg help aliases` lists them all.

## Lease holders

A lease records `workspace`, `agent`, `claimed_at`, `ttl_hours` and `reason`, and, when the claim
or a later `renew` passed `--contact TEXT`, a `contact`: free text naming the holding session (an
amsg address such as `lab-desk/n14-paper`, or a channel such as `@lean-ops`). lean-pkg stores and
shows the contact but never checks or uses it, and cannot map a holder to a live session by itself.
The holder is whatever `--agent` (and `--workspace`) the claim passed, so:

- Say how to reach you: `lean-pkg claim NAME --workspace W --agent A --contact lab-desk/n14-paper`;
  `renew NAME --agent A --contact TEXT` sets or replaces it later, `renew` without `--contact`
  keeps it, and `renew NAME --agent A --no-contact` clears it while keeping the lease. `release`
  drops it with the lease.
- Your own leases: `lean-pkg leases --agent A` with the `--agent A` you claimed with (add
  `--expiring 2` for the ones to `renew` soon). Pass the same `A` on every claim, renew and
  evidence write; `renew`, `release` and `--agent` checks compare it literally.
- Who is behind a lease: `lean-pkg leases` (the `CONTACT` column) or, for one package,
  `lean-pkg status NAME --fields lease.agent,lease.contact,lease.workspace,lease.reason,lease.expires_at`
  (`lease.contact` is null when the lease has none).
- Reaching another holder: lean-pkg has no channel of its own; use the contact the holder left.
  The contact is stored in the registry and its event log, so write only what every reader of
  the registry may see. `public-safety` treats its tokens like those of `agent` and `workspace`:
  they become derived patterns at scan time, so a machine name in a contact is flagged if it
  turns up in the package.

## Package record

Each package in the registry, and the `package` object of `status NAME --json`, is a mapping
with these top-level keys (also listed by `lean-pkg status --help`; both come from one table
in the code). Tier `v1` keys are required at every schema version; `v2` and `v3` keys are
added by `lean-pkg migrate` to that schema version; `optional` keys are absent unless set.
`board --json` and `list --json` hold the same records under `packages`. In `status`, `list` and
`board` output (JSON, text and `--fields`), a lease also carries `expires_at`: derived as
`claimed_at` + `ttl_hours`, ISO 8601 UTC (null when the lease fields are unparseable), never
stored in the registry, so `--fields lease.expires_at` reads a lease's expiry directly.

| Key | Tier | Meaning |
|---|---|---|
| `name` | v1 | package name; equals its key under packages |
| `repo` | v1 | git URL (or local bare path) of the repository |
| `local_path` | v1 | checkout path, $HOME-relative |
| `visibility` | v1 | private \| public; GitHub is the mirror |
| `status` | v1 | lifecycle status: scratch \| active \| gate-pending \| verified-private \| public \| upstreamed \| archived |
| `owner_project` | v1 | project that owns the package |
| `workspace` | v1 | workspace that registered it |
| `agent` | v1 | agent that registered it |
| `lease` | v1 | live claim {workspace, agent, claimed_at, ttl_hours, reason, contact?} or null; contact: optional address of the holding session (claim/renew --contact); status/list/board output adds the derived expires_at (claimed_at + ttl_hours, UTC; never stored) |
| `lean_toolchain` | v1 | lean-toolchain detected in the checkout |
| `mathlib_pin` | v1 | Mathlib revision from lake-manifest.json |
| `upstream_target` | v1 | upstream goal: none \| mathlib \| palomar |
| `automation` | v1 | automation disclosure (required when upstream_target is mathlib) |
| `gate` | v1 | trust gate {receipt, comparator, axioms_allowed}: receipt paths in the repo |
| `promoted_at` | v1 | when promote --yes made it public, or null |
| `promoted_commit` | v1 | the commit promoted, or null |
| `reaudit_after` | v1 | ISO date after which to re-audit, or null |
| `notes` | v1 | free text |
| `history` | v1 | append-only event list (status prints the last 5; --json all) |
| `license_spdx` | v2 | SPDX license id or expression, or null |
| `formalization_yaml` | v2 | repo-relative path of formalization.yaml, or null |
| `palomar` | v2 | Palomar evidence entries [{id, version, commit, comparator_config, ...}] |
| `doi` | v2 | {concept, versions[{doi, tag, commit, minted_at}]} or null |
| `reservoir` | v2 | {listed_at, url} once Reservoir lists it, or null |
| `consent` | v2 | consent records [{who, role, recorded_at, pointer}] |
| `claims_file` | v3 | repo-relative path of the claims/1 file |
| `claims` | v3 | cached claims from the last `claims sync` (can be hundreds of KB) [bulk] |
| `claims_synced_commit` | v3 | 40-hex commit the cached claims were read at, or null |
| `paper` | optional | {arxiv, status} of the paper behind a paper-coupled package |
| `namespace_notes` | optional | {Lean root namespace: why sharing it with another package is intended} |
| `source_paper` | optional | {title, authors[], arxiv, doi} of the formalized paper (credited in formalization.yaml acknowledgements, never as project.authors) |
| `ai_assistance` | optional | {tools[], roles[]}: AI tools used and for what (rendered into acknowledgements) |
| `track` | optional | DOI track: palomar \| doi-only (absent = palomar); doi-only lets a public package record doi-minted without a palomar-* event (update --track) |
| `docs_descendants` | optional | true: a receipt bound to commit C is also accepted at a descendant of C where no build input under the package directory changed (docs-only rule, docs/receipts.md); absent = false (update --accept-docs-descendants) |
| `build_inputs` | optional | [glob]: extra package-relative paths the docs-only rule treats as build inputs (fnmatch; a glob without / also matches a basename) (update --build-inputs) |
| `headline_config` | optional | repo-relative path of the headline comparator configuration (the paper's main results; formalization.yaml status.main_results lists its theorems); absent = comparator.headline.json when tracked (update --headline-config) |
| `publication_hold` | optional | {reason, at, by}: the owner withdrew publication (update --withdraw-publication); promote refuses while it is set; only update --release-publication-hold lifts it |

## Promotion checklist lines

| Line | Meaning |
|---|---|
| REUSE lint | `reuse lint` in the repository (WARN when `reuse` is not installed) |
| LICENSE/COPYING present | GitHub must detect an OSI license for Reservoir indexing |
| `lake-manifest.json` tracked at HEAD | Reservoir requires a root manifest |
| gate receipt bound to HEAD | HEAD equals the receipt commit or is an evidence-only descendant |
| comparator PASS bound to HEAD | the comparator receipt re-validates from bytes |
| paper | only for a package with a `paper` field: `✗` while `paper.status` is `unpublished` and no `paper.arxiv` is recorded (going public would disclose the results before the preprint) |
| gh visibility matches registry | GitHub and the registry agree |
| automation disclosure | required when `upstream_target` is `mathlib` |

Any `✗` refuses; `WARN` does not.

## Audit findings

`lease-expired` (cleared, event appended), `missing-path` (the recorded path, then the fix
`lean-pkg update NAME --path DIR --reason "checkout moved" --dry-run`, to run again without
`--dry-run`; when a directory one level under the recorded path's parent has a lakefile and a git
remote with the package's repo URL, the detail says "found at DIR" and the command names it), `toolchain-drift`,
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
keeps one finding per package), and `gh-*` for GitHub queries that could not be made.

A failed gh query is the check `gh-OUTCOME` (`gh-missing`, `gh-not-authenticated`, `gh-no-access`,
`gh-not-found`, `gh-error`; through 0.7.6 the check was the literal `warn`) and carries `gh`, the bare
outcome, and a detail that names the remedy:

| `gh` | when | detail |
|---|---|---|
| `gh-missing` | `gh` is not on PATH | gh is not installed: gh checks skipped (install gh, or pass --no-gh) |
| `not-authenticated` | HTTP 401 / bad credentials, or a not-found while `gh auth status` fails | gh is not authenticated: run `gh auth login` (or pass --no-gh) |
| `no-access` | "Resource not accessible" / HTTP 403, or a not-found while a checkout remote that names the repository answers `git ls-remote` | token lacks access to OWNER/REPO: re-run `gh auth login` with a token that can read it |
| `not-found` | "Could not resolve to a Repository" / HTTP 404 and no checkout remote reaches it | repository OWNER/REPO not found |
| `error` | anything else | gh query failed: TEXT |

GitHub answers not-found for a private repository the token cannot read (a fine-grained token
without access to it), which is why a not-found is checked against the checkout's own git remote
(no prompts, 30-second cap). For an `archived` package a `not-found` is `info` ("archived; upstream
repo not found — expected if the owner removed it; see notes"): the package is frozen and the local
checkout is the copy that matters. `audit --no-gh` makes no gh query at all; each GitHub package
gets an `info` `gh-skipped` finding ("gh checks skipped (--no-gh)"), one summary line in the human
output, and visibility and archived state are not compared (so `--fix` reconciles neither).

The toolchain floor is `--toolchain-floor VERSION`, else the environment variable
`LEAN_PKG_TOOLCHAIN_FLOOR`, else `leanprover/lean4:v4.35.0-rc2` (Palomar's floor: the first
toolchain with `lake comparator`). An rc is older than the release of the same version.
`scratch` and `archived` packages are exempt. An unparsable floor is a usage error (exit 2). Only `--fix` mutates visibility
and archived state; lease sweeps always apply.

The floor is the comparator and promotion requirement, not a measure of package health. It is a
`warn` only for `public` and `upstreamed` packages, for packages with comparator evidence (a
`gate.comparator` receipt, a `palomar` entry, or a cached claim at `comparator` or `palomar` trust),
and for every package under `audit --promotion`. Elsewhere `below-toolchain-floor` (and
`toolchain-unparsed`) are `info`: "toolchain below the comparator floor FLOOR (matters only for
promote/comparator)". Every finding in `audit --json` carries `level` (`warn` or `info`), and the
top level carries `counts` (`{"warn": N, "info": M}`, every finding including the collapsed ones). A
human line is `LEVEL  CHECK  NAME: DETAIL`: the level column first (`warn` or `info`, on a TTY or
not; the old trailing `[info]` is gone), then the check code aligned to the longest one, e.g.
`info  gh-not-found  alpha: archived; upstream repo not found ...`. The last line counts the findings
by level: "N finding(s): W warn, I info". `receipt-untracked`, `consent-duplicate`,
`no-github-mirror` and `claims-coverage-none` are always `info`. `audit` exits 0 whatever the findings.
