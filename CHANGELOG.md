# Changelog

All notable changes to lean-pkg are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, a minor bump may
change the registry schema or receipt format, always with a migration).

## [Unreleased]

### Added

- `lean-pkg public-safety NAME [--history|--no-history] [--json]`: read-only scan of the package's
  tree (what git would ship) and, by default, every blob and commit message reachable from any ref
  under the package directory, for private patterns: the package's `.lean-pkg-private-patterns`
  (regexes, `#` comments, `allow: REGEX` exemptions; the file itself is a hit when tracked), built-in
  defaults (absolute home paths, `~/Dropbox/`, `approvals/`, `notes/`, `*.private`, `TODO-private`,
  undeclared email addresses), user and host names derived at scan time from the record's
  `local_path`/`agent`/`workspace`, and internal files by path (`approvals/`, `notes/`, `drafts/`,
  `scratch/`, `*.priv`). Each hit names its location (path and line; history adds the commit), a
  pattern label and a redacted excerpt (every match replaced by `[…]`; raw private strings are never
  printed). Exit 1 on hits; `--json` `{ok, hits, tree_hits, history_hits, patterns_source, ...}`.
  `promote` runs it and refuses while hits remain (`--skip-public-safety` turns it into a `WARN`);
  `publish NAME --public-safety` adds it to the publish checklist. (#35)
- `lean-pkg release NAME --fresh-repo DIR [--remote URL] [--keep-receipts] [--source-commit SHA]
  [--dry-run]`: export the gate-bound commit's tree (package directory only; the patterns file,
  internal paths and `.lean-receipts/` dropped and listed) into a new single-commit repository at
  DIR, after a public-safety scan of the exported tree (any hit refuses, nothing written). The commit
  message records the source commit and the export date. The registry entry is then relocated with
  the `update --path` machinery: gate and comparator evidence cleared, `verified-private` becomes
  `gate-pending`, and a dated note keeps the old receipt paths and the source commit. `--dry-run`
  prints the plan and the next commands (gate, `comparator-receipt --prepare`, publish) and writes
  nothing. docs/receipts.md gains "Publishing a package incubated beside private work". (#35)

## [0.7.8] - 2026-10-09

### Added

- `--registry-snapshot COMMIT` global option (env `LEAN_PKG_REGISTRY_SNAPSHOT`): run a read-only
  command against the registry file and event log as of `COMMIT` of the git repository holding
  them (read with `git show` into a temporary copy). Every write is refused with exit 2
  (`read-only: --registry-snapshot`); `--json` objects gain `registry_snapshot`. Replaces pointing
  `LEAN_PKG_REGISTRY` at a hand-made historical copy, for instance to run
  `comparator config --checkout DIR --dry-run` on a copy at an earlier claims-synced commit. (#34)

### Changed

- The `comparator config --emit-script` driver now runs `export LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"`
  near its top: one Lean thread per process on a cluster node unless the job script sets a value,
  which is kept. The one-shot `--challenge-from-type` run is unchanged. docs/comparator.md gains
  "On shared cluster nodes": the file table is per node, so request a whole node or one job per
  node, set `LEAN_NUM_THREADS`, and keep `LEAN_PKG_TYPES_JOBS` at 2 or less. (#34)

## [0.7.7] - 2026-10-09

### Fixed

- `audit` human output led with the check code even for `info` findings (an archived package with a
  vanished upstream printed `warn    NAME: archived; ... [info]`). Each line is now `LEVEL  CHECK
  NAME: DETAIL`: the level (`warn`/`info`) first, the check code aligned after it; the trailing
  ` [info]` suffix is dropped (the column carries it, TTY or not). A last line counts the findings
  by level ("N finding(s): W warn, I info"), and `audit --json` gains top-level `counts`
  `{warn, info}`. (#33)
- `audit`: a failed gh query reported the check as the literal `warn`, which was never a documented
  check value. Its `check` is now `gh-missing`, `gh-not-authenticated`, `gh-no-access`,
  `gh-not-found` or `gh-error` (also for "gh returned non-JSON", which now carries `gh: error`); the
  `gh` key is unchanged. (#33)

## [0.7.6] - 2026-10-09

### Added

- `comparator config --from-claims NAME --checkout DIR`: use another checkout of the package
  (for instance the clone that ran the type driver) instead of the registry's `local_path`;
  refused, with both commits and the differing paths, unless its HEAD is the claims-synced commit
  or an evidence-only descendant, and, with a types flag, unless it has the lakefile. (#31)
- `comparator config --from-claims NAME --from-output F --no-registry --out FILE` (also with
  `--challenge-from-type`): scratch mode for a package under another agent's lease; writes the
  checkout's `comparator.json` merged with the new entries to FILE outside the checkout, never
  consults the lease, and lists what it did not do (JSON `no_registry`, `not_done[]`). (#31)

### Fixed

- `update --notes TEXT` silently replaced the notes. It now prints the replaced value and "use
  --append-note to keep the previous note" (JSON `hint`); new `update NAME --append-note TEXT --agent A`
  appends `YYYY-MM-DD (A): TEXT` (UTC), creating `notes` if absent; it cannot be combined with
  `--notes`. (#32)
- docs/comparator.md "Host policy" gains "Which hosts can produce receipts": run `lean-pkg comparator
  host-check` on the candidate host; one that fails `mechanism toolchain` cannot produce `toolchain`
  receipts. `doctor`'s `tool comparator` line says to run `host-check` when `bwrap` is missing. (#32)
- `audit` `duplicate-namespace` had no way to record an intended collision (a comparator
  `Challenge`/`Solution` layout, say). New `lean-pkg update NAME --namespace-note TEXT [--namespace NS]`
  stores `namespace_notes: {NS: TEXT}` (optional, additive; `validate` checks its shape); the finding is
  `info` with the note once every package sharing the namespace has one, else `warn` naming the
  command. (#32)
- `audit` `missing-path`: the detail gives the recorded path and the fix (`lean-pkg update NAME --path
  DIR --reason "checkout moved" --dry-run`, then without `--dry-run`), and says "found at DIR" when a
  directory next to the recorded one has a lakefile and the package's git remote. (#32)
- `audit`: a failed gh query says why. The `warn` finding gains `gh` (`gh-missing`,
  `not-authenticated`, `no-access`, `not-found`, `error`) and a detail naming the remedy ("token lacks
  access to OWNER/REPO: re-run `gh auth login` with a token that can read it" vs "repository not
  found"). A GraphQL "Could not resolve to a Repository" is checked against `gh auth status` and the
  checkout's own git remote, since GitHub reports a repository the token cannot read as not found.
  An `archived` package whose upstream is gone is `info`. New `audit --no-gh` skips the gh checks
  (`gh-skipped`, info). (#32)
- `audit`: the toolchain floor is the comparator/promotion requirement, so `below-toolchain-floor` is a
  `warn` only for `public`/`upstreamed` packages, packages with comparator evidence, or under the new
  `audit --promotion`; elsewhere it is `info` ("toolchain below the comparator floor FLOOR (matters only
  for promote/comparator)"). Every `audit --json` finding gains `level` (`warn` | `info`); human output
  marks info lines `[info]`. (#32)
- `comparator config --challenge-from-type` / `--emit-script` driver: a `lake env lean` process that
  exits non-zero (or times out) is an extraction failure, not a per-declaration type error. Each
  result records `rc`, `failure` (`declaration` or `extraction`) and `output_tail`; the output gains
  a `summary` `{ok, total, filled, errors, declaration_errors, jobs}`; the driver exits 1 when any
  extraction failed and 2 when nothing was filled (it exited 0 after 224 of 225 extractions failed).
  `--from-output` and `--challenge-from-type` refuse such a run (exit 1, counts named, nothing
  written) unless `--allow-partial`, which uses the filled entries and keeps the rest as TODO with
  `restatement_reason: extraction_failed`. (#30)
- The type driver no longer runs more Lean processes than the CPUs it may use (default
  `min(2, CPUs)`, decided on the host that runs it, Slurm allocation included), and when a
  declaration fails with "too many open files" (ENFILE/EMFILE, typical on Lustre/NFS) it halves
  the workers and retries those declarations, at least two rounds down to one worker, recording
  the rounds in `summary.backoff`. `LEAN_PKG_TYPES_BACKOFF_SECONDS` sets the pause. (#30)
- `comparator config --challenge-from-type`: a pretty-printed type is no longer trusted as is.
  The driver re-elaborates each printed type in the declaration's module context
  (`example : (T) := @_root_.<decl>`, `autoImplicit false`) and only an exit status of 0 makes the
  result `ok`; on a real package 5 of 24 entries marked ready had lost a coercion target, an
  integral domain or a `Matrix` type. A failure keeps the TODO lines with `needs_restatement: true`,
  `restatement_reason: round_trip_failed`, the error in `type_error` and the text in
  `printed_type`; `summary.round_trip_failed` and the JSON `types.round_trip_failed` count them.
  Output without the check is pre-filled but always `needs_restatement`. (#31)

## [0.7.5] - 2026-10-09

### Added

- `list` and `board` accept `--fields a,b,c` (JSON and text), the same validation and dotted
  paths as `status --fields` (one implementation): each package record is cut to those keys,
  `--json` keeps the dump's shape and adds a `fields` list, text prints YAML; `board --fields`
  never opens the TUI. `--only` stays a pure package filter (full records). `list --json --only
  NAME --fields status` used to exit 2. (#29)
- `--no-claims` on `status`, `list` and `board`: drops the bulk cached arrays (the package-record
  keys tagged `[bulk]` in `status --help` and docs/cli.md; today `claims`, which can be hundreds
  of KB) from each record, every other key and the output shape unchanged; `--json` adds an
  `omitted` list. The `--help` of the three commands and docs/cli.md now say precisely that
  `--only` filters packages and `--fields` trims fields. (#29)

### Changed

- `lean-pkg --version` says which build runs: `lean-pkg X.Y.Z (<short-sha>[+dirty], editable
  <checkout>)` for an editable install, `(installed <package dir>)` for a wheel or pipx install,
  `(unknown build, <package dir>)` without git. The same local detection as `doctor`'s `install`
  line (which now also marks a dirty checkout `+dirty`); no network. `--version --json` prints
  `{"ok": true, "version", "commit", "dirty", "install", "path"}`. (#29)

## [0.7.4] - 2026-10-09

### Added

- `status NAME --fields a,b,c` (JSON and text): only those keys of the package record; dotted
  paths reach into mappings (`gate.receipt`, `lease.agent`). `--json` keeps the nesting and adds
  a `fields` list; text prints YAML. An unknown field exits 2 listing the valid keys.
  `list --json --only NAME[,NAME]` and `board --json --only NAME` restrict the registry dump to
  those packages with the shape unchanged (also the static table; `board --only` never opens the
  TUI); an unknown name exits 1. (#28)
- `lean-pkg status --help` lists every top-level key of the package record with a one-line
  meaning, and docs/cli.md gains a "Package record" section. Both come from one table in the
  code (`PACKAGE_FIELDS`), which also defines the keys `validate` requires. (#28)

### Changed

- `doctor`: an optional tool's `n/a ... not on PATH` line says what the tool is for and when it
  is needed (`comparator`, `nanoda`, `bwrap`, `go`, `cargo`, `jq`: only to produce comparator
  receipts on this host; `reuse`: release gate only; `gh`: promote, audit, reservoir check), so a
  clean doctor on a read-only host no longer reads as a partial failure. Levels are unchanged. (#28)

## [0.7.3] - 2026-10-09

### Added

- `comparator-receipt` runs in stages (`checkout`, `cache-get`, `build`, `comparator`,
  `receipt`), each logged to `<evidence-dir>/comparator-stages/<NN>-<stage>.log` with a
  `stages.json` (status, exit, wall time). Both are kept on failure, and the error names the
  failed stage and its log. `--evidence-dir DIR` defaults to `.lake/comparator-receipt`.
  `--prepare` runs only the setup stages and is idempotent. A later run whose commit,
  toolchain and config hash match a completed setup skips straight to the comparison.
  `--from-prepared DIR` reuses another directory's setup and refuses a mismatch. The receipt
  gains an additive `stages` summary. (#26.1)
- `lean-pkg gate NAME --receipt R [--comparator C] --check`: read-only validation against HEAD
  (binding, the derived-only `formalization.yaml` rule, toolchain at the bound commit, sorry
  count, axioms; with `--comparator` both tiers). It prints `✓` lines or the refusal `gate`
  would print, writes nothing, records no event and works at any status (exit 0/1).
  `comparator --check` now prints the same binding `✓` lines and adds `checks[]` and `head` to
  its JSON. (#26.2)
- `update NAME --path NEW [--dry-run]` (relocate) ends with a `binding at the new path` block
  that groups the binding diagnostics with the next command(s). The block covers each
  recorded receipt's commit against the new HEAD with the evidence-only verdict, the
  `formalization.yaml` exception (pointer, changed, derived-only) and any other changes since
  the receipt commit. The next commands are `gate NAME --receipt R [--refresh-formalization]`,
  `update NAME --formalization-yaml REL` when a missing pointer is the only reason a
  derived-only regeneration is cleared, `comparator-receipt` + `comparator`, `claims sync`, or
  "nothing to re-gate". JSON gains `binding` and `next_commands[]`. (#26.3)
- `lean-pkg comparator config --coverage NAME`: a read-only explainer. At the gate-bound commit
  it reports how many theorem/lemma claims the comparator configuration tracked there covers,
  does not cover, or lists only as TODO. It names configurations changed since then and keeps
  the candidate's counts apart, never substituted. It says plainly that a changed or
  uncommitted config cannot certify the bound tree, so a new gate at a commit that tracks the
  config is required, and it prints the exact next commands. (#26.5)

### Fixed

- A launcher that leaked `GIT_INDEX_FILE` (or `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`,
  `GIT_NAMESPACE`) made `comparator-receipt`'s dependency checkout use the wrong index. The
  runner now removes these from every `lake`, `git` and judge it runs and lists them in
  `stages.json` (`git_env_scrubbed`); `lean-pkg` does the same for its `git` calls in a
  package checkout (not in the registry repository). `comparator host-check` warns
  (`git-env`; additive `env_warnings[]` in `--json`), and so does the `doctor` `host` line,
  when any is set. (#27)
- `gate NAME --receipt R --refresh-formalization` refused at an evidence-only descendant
  whose `formalization.yaml` had only derived keys regenerated when no `formalization_yaml`
  pointer was recorded yet, because the derived-only exception follows the pointer. The
  refresh owns that path and records the pointer right after the gate, so it now judges the
  exception for that path. A kept-key change still refuses. A new end-to-end test covers a
  clean checkout: refresh, commit as an evidence-only descendant, then `gate --check` and
  `comparator --check` pass; it also checks that kept keys are byte-equal across the
  refresh. (#26.4)

## [0.7.2] - 2026-10-09

### Added

- `lean-pkg comparator config --from-claims NAME --challenge-from-type [--lake-env DIR] [--jobs N]
  [--timeout S]`: pre-fills each scaffolded Challenge with the declaration's elaborated type
  (pretty-printed by `lake env lean` on the build host) and its Solution with
  `exact @_root_.<decl>`; `needs_restatement: true` marks types that use project definitions,
  and a declaration that fails keeps its `TODO:` lines with `type_error`. `--emit-script F`
  and `--from-output F` split it across hosts (driver on a Slurm node, JSON
  `comparator-types/1` checked against HEAD). (#25)
- `lean-pkg claims migrate NAME --from PATH [--out claims.yaml] [--dry-run]`:
  turns a legacy claims JSON into a `claims/1` skeleton in the checkout,
  copying the fields it can map and marking the rest `TODO`
  (`file: TODO.lean`, which `claims sync` refuses). It never overwrites, never writes
  the registry, and grants nothing: commit the file, then `claims sync`. (#24.1)

### Changed

- `reservoir check` before promote: when every ✗ is pending promote, the closing line reads
  "not eligible yet (pre-promote): promote --yes will satisfy the ✗ items above" instead of
  "not eligible: fix every ✗ above"; exit codes and JSON are unchanged.

### Fixed

- `lean-pkg search` no longer sends natural-language terms to Loogle, which
  parses only declaration and type patterns. The term is routed by shape: a
  Lean pattern (arrows, binders, `:`, `(`, `?`, `_`, `⊢`, math operators, a
  `Nat.`-style dotted name) goes to Loogle; prose skips Loogle with a one-line
  reason and an example pattern, and every other source still runs.
  `--loogle` forces Loogle. `--json` gains `route` (`signature` or
  `natural-language`) at the top and on every source. (#24.3)
- `claims check NAME` no longer passes a package with no claims coverage. No
  cached claims and no `claims.yaml` at HEAD (`coverage: none`), or a
  committed but unsynced `claims.yaml` (`coverage: unsynced`), now exits 1
  with a next-step hint naming any legacy `*claims.json` in the checkout;
  `claims list NAME` states the same coverage. `--json` gains `coverage`,
  `cached_claims`, `claims_yaml`, `legacy_claims` and `hint`. (#24.1)

## [0.7.1] - 2026-10-09

### Added

- `lean-pkg comparator config --from-claims NAME [--out comparator.json]
  [--dry-run]`: adds one `theorem_names` entry (`<Package>Audit.<decl>`)
  per synced `theorem`/`lemma` claim that no tracked comparator
  configuration covers, with `TODO:` Challenge and Solution lines under
  `lean_pkg_scaffold.todo`. It never removes or reorders existing entries,
  prints already-configured/added/skipped counts, and refuses when
  `claims.yaml` is not tracked or not synced at HEAD. Names still marked TODO
  are left out of `formalization.yaml`'s `status.main_results`. (#23.6)

### Fixed

- Consent re-recorded with the same `who`, `role` and `pointer` is now a
  no-op (`already recorded`, exit 0, `"already_recorded": true`), like a
  repeated `palomar-preflight-pass`; a changed pointer is still a new record.
  `publish --dry-run` and the human `status` view show each (who, role)
  once (`status --json` keeps every record), and `audit` gains the
  informational finding `consent-duplicate`. (#23.1)
- The `publish --dry-run` tag hint no longer assumes `origin` is GitHub: it
  names the checkout's GitHub remote, found by URL among `git remote -v`
  (preferring the one that names the package's `repo`), adds "after
  promote" while the package is not public, and says so when no remote
  points at GitHub. (#23.2)
- `reservoir check` before promote: while the package is not `public` or
  `upstreamed`, the GitHub-dependent items `promote --yes` will satisfy are
  labelled `pending promote: ...` (still failing, exit 1) and a closing
  `pre-promote:` line says what promote will fix; `--json` gains
  `pre_promote` and `pending_promote[]`. A new `WARN` flags a
  `lean-toolchain` that is a release candidate or nightly. (#23.3)
- `comparator host-check` judges each mechanism against its own
  requirements and prints a `mechanism toolchain|legacy: ok|fail` line
  for each, naming what fails. The verdict names only the usable mechanisms
  and says that Palomar runs only `toolchain`: a host where `bwrap` cannot
  create a user namespace can still produce `legacy` receipts (landrun does
  not use bwrap), and the verdict now says why `toolchain` fails there and
  that `legacy` receipts do not predict Palomar. `toolchain` now also needs
  `python3`, which `verify-comparator.sh` requires. The Landlock thresholds
  are unchanged and now cite their source: the pinned comparator calls
  landrun with `--best-effort`, so any ABI from 1 runs (ABI 9 is landrun's
  strict default, not a requirement), and below ABI 4 the sandbox has no
  network rules. `--json` gains `mechanism_detail` and `palomar`;
  docs/comparator.md has a per-mechanism requirements table. (#23.4)
- Evidence circularity: the evidence-only descendant rule now also admits
  the package's recorded `formalization.yaml` when it was tracked at the
  bound commit and only the keys the renderer derives changed (compared as
  YAML data). Committing a receipt together with the regenerated file that
  cites it no longer breaks gate, comparator, promote, relocate, audit and
  the publish claims check. A changed kept key or any other path still
  refuses, naming it; `claims.yaml` is never exempt. (#23.5)

## [0.7.0] - 2026-10-09

### Added

- Committed mode: a `--push` rejected as non-fast-forward is retried once:
  fetch, rebase the one new commit onto `origin/<branch>` (the event log
  merges as a union of appended lines), push again. Skipped when other local
  commits are unpushed; a rebase conflict is aborted and the local commit is
  kept, reported as before. (#22.9)
- `formalization NAME --write --no-registry` writes the file (lease checked,
  same refusals) without touching the registry: no pointer update, no event,
  no lock, `--commit` ignored; `--json` gains `"registry_skipped": true`. The
  docs now say plainly that `--write` alone is a registry write. (#22.8)
- `register` notes (stderr, and `notes` in `--json`) when another registered
  package's checkout uses the same Lean root namespace (each `lean_lib` in
  the lakefile, else the top-level `*.lean` files); a warning, never a
  refusal. `audit` gains the informational finding `duplicate-namespace`.
  (#22.7)
- `lean-pkg comparator host-check`: a read-only check (no registry needed)
  of kernel, Landlock ABI, bwrap user namespaces and go/cargo/jq/git/lean/lake,
  with a verdict naming the usable mechanisms (exit 0 can, 1 cannot).
  `comparator-receipt` prints the verdict on stderr when it starts, and
  `doctor` gains a `host` line. `comparator NAME` without `--receipt` is now
  a usage error raised by lean-pkg rather than by argparse (exit 2 either
  way). (#22.5)
- Paper-coupled packages: an optional registry field `paper: {arxiv, status}`
  (status `unpublished`, `preprint` or `published`; additive, no migration),
  set with `update NAME --paper-arxiv ID` (status becomes `preprint`) and
  `--paper-status S`. `promote` gains a `✗` line while the paper is
  unpublished and no arXiv id is recorded; `validate` checks the field. (#22.10)
- `gate NAME --receipt R --refresh-formalization [--agent A]` gates and then
  regenerates `formalization.yaml` (with `--write --force` semantics: kept
  keys survive) and its registry pointer in one step. Another agent's live
  lease and a kept key failing the schema refuse before the gate; an
  identical re-gate whose file is current is refused with nothing written.
  `--json` gains `"formalization"`. (#22.4)
- `lean-pkg publish NAME --dry-run`: one read-only checklist from
  verified-private to listed (the promote checklist, consent, license_spdx
  against the LICENSE text, formalization.yaml current and valid, claims
  synced, lakefile package name, tag at HEAD) followed by the exact next
  `promote`/`git tag`/`record` commands; exit 0 ready, 1 not. Named `publish`
  because `release` already releases a lease; without `--dry-run` it is
  refused in this version. (#22.1)
- `lean-pkg reservoir check NAME [--offline]`: read-only checks of the
  documented Reservoir listing criteria (public, not a fork, 2 stars,
  root manifest and toolchain tracked, license, lakefile package without
  `reservoir := false`, display metadata, tag) and of Reservoir's package
  API, printing the `record ... reservoir-listed` command once listed. No
  automatic poller or recording. (#22.2)

## [0.6.0] - 2026-10-09

### Added

- `record` validates evidence fields before any effect, each refusal naming
  its rule: a `doi`/`concept` must look like a DOI (doi-format), a
  `mathlib-pr-*` `url` must be an `https://github.com/...` URL
  (mathlib-pr-url), `palomar-registered` needs status `public` or
  `upstreamed` (palomar-registered-public) and a `comparator_config` tracked
  at the package's HEAD (palomar-config-tracked), and a non-integer
  `version` is refused cleanly while `palomar-withdrawn version=01` matches
  version 1 (palomar-version). A `reservoir-listed` url naming neither the
  package nor its repository warns (stderr and `notes`); an identical
  `palomar-preflight-pass` is an exit-0 no-op (`"already_recorded": true`);
  a field holding a local absolute path warns and is listed in
  `private_strings`. (#20)
- `search` scores the registry's packages themselves (name exact 3; notes,
  owner_project and repo by token), so `search NAME --offline` finds a
  registered package with no cached claims; package hits print as
  `package: NAME (status) — repo`, and every local hit gains `hit_kind`
  (`package` or `claim`) and `repo` in `--json`. (#20)
- `doctor` gains a `gh auth` line (the github.com account; `warn` when gh is
  not logged in, `n/a` without gh) and says "no origin remote" when the
  registry repository (or an editable install) has none. (#20)
- `audit` gains two informational findings: `receipt-untracked` (a recorded
  gate or comparator receipt file is present but not tracked at the
  reference branch tip) and `duplicate-path` (two packages resolve to the
  same checkout). (#20)
- `lean-pkg update NAME --path NEW --reason TEXT [--agent A] [--redetect]
  [--dry-run]` relocates a package to another checkout without losing its
  name, history or claims cache (event `relocated` with `old_path`,
  `new_path`, `reason`, `cleared`). It refuses the current path, another
  package's path and a foreign live lease (no `--force`), never touches the
  file system, and clears gate receipts and cached claims not bound to the
  new checkout's HEAD; a `verified-private` package then returns to
  `gate-pending` (event `status-changed`). (#13)
- Committed mode for a registry kept in git: global `--commit` (env
  `LEAN_PKG_COMMIT=1`) makes every write command fetch `origin` and
  `pull --ff-only` first (refusing a diverged clone or registry files that
  are already modified), then commit `lean-packages.yaml` and
  `lean-package-events.jsonl` together as `lean-pkg: <event> <name>`;
  `--push` (env `LEAN_PKG_PUSH=1`) also pushes, exiting 1 with the manual
  `git push` command when that fails. `--no-commit` overrides both. Reports
  go to stderr; stdout and `--json` payloads are unchanged. (#7)
- Registry resolution gains a user-level default between the environment
  and the current directory: `--registry`, then `LEAN_PKG_REGISTRY`, then
  `~/.lean-pkg/registry` (base directory overridable with `LEAN_PKG_HOME`; a
  directory or symlink to one holding `lean-packages.yaml`, or the registry
  file itself), then `./lean-packages.yaml`. Non-interactive shells no longer
  need a wrapper to find the registry. `doctor`'s `registry` line ends with
  `[source: flag|env|user-default|cwd]`, `doctor --json` adds `"source"` to
  that check, and a new `user-default` line shows what the default points at
  (`n/a` when not set up). Set it up with
  `mkdir -p ~/.lean-pkg && ln -s /path/to/registry-repo ~/.lean-pkg/registry`. (#12)
- `lean-pkg renew NAME --agent A [--ttl H]`: holder-only restart of a live
  lease (event `claim-refreshed`); refuses when there is no lease, another
  agent holds it, or it has already lapsed, instead of silently recording a
  fresh claim. `lean-pkg leases [--agent A] [--expiring H]` lists held leases
  soonest-expiry first. `doctor` adds a `leases` line that warns about leases
  lapsing within 2 h and expired-but-unswept ones. (#10)
- `lean-pkg doctor` reports `registry-repo`: the git work tree holding the
  registry, its branch, behind/ahead counts against `origin/<branch>` and
  uncommitted tracked files, naming `lean-packages.yaml` and
  `lean-package-events.jsonl` when they are among them; `warn` means do not
  write from this clone yet (pull, push, or commit first). `doctor --fetch`
  fetches `origin` in both the tool checkout and the registry repo before
  comparing. (#11)
- `board`/`list` have an `EXPIRES` column (`in 2h13m` while a lease is live,
  `3h ago` once it lapsed and `audit --fix` has not swept it yet); their
  `--json` output adds `expires_at` to every lease. (#8)

### Changed

- `lean-pkg audit` is read-only without `--fix`: an expired lease is reported
  (`lease-expired ... expired; \`audit --fix\` clears it`, `fixed: false`)
  instead of being cleared and logged. `audit --fix` sweeps as before and now
  says on stderr how many changes it wrote so the two registry files get
  committed together; `--json` carries a `fixed` count. The TUI's `a` is
  read-only. (#9)
- Advisory notes from `claim` and `index add` (missing prior-art search or
  record) go to stderr, so the last stdout line is the result and
  `lean-pkg claim ... | tail -1` scripts see it; `--json` keeps `notes`. (#6)
- Index: `gromov-filling-lean` re-pinned at `ba787a3eb26e` with a comparator
  receipt for Theorem 1.1 (legacy mechanism), whose Challenge copies the
  project's surface-area definitions into a Mathlib-only `Defs.lean`; trust is
  now `comparator`. All three index entries are at comparator trust.
- Index: `markovprocess-pilot` re-pinned at `05d99d75df82`, the commit that
  credits Scott Armstrong for the lemma adapted from MarkovProcess and cites
  MarkovProcess; fresh lower-tier and comparator receipts (toolchain
  mechanism).
- One time policy, documented in `lean-pkg llms` and DESIGN.md: every
  timestamp written is UTC ISO-8601 with an offset at second precision,
  every date written is the UTC date (the wiki's `updated:` used the local
  date), and same-second events keep file order. `leases --json` `now` is at
  second precision. `doctor` adds a `time` line that warns about naive
  (legacy) timestamps, which are still read as UTC and accepted by
  `validate`. (#19)
- Refusals name the fix: a missing registry says where the path came from
  (`resolved from flag|env|user-default|cwd`) and how to create one
  (`lean-pkg init`, `LEAN_PKG_REGISTRY`, `~/.lean-pkg/registry`); `gate` at
  `verified-private` points to `update NAME --status gate-pending --reason
  ...` then `gate`; a different comparator or lower-tier receipt already at
  the canonical `.lean-receipts/` path is named by its full path with the
  file to pass instead. (#19)
- Docs: `audit --fix` (not `audit`) clears expired leases; `renew` and
  `leases` are described in README, how-to, lifecycle, DESIGN, the agents
  page and the `lean-pkg-agent` skill (renew before the TTL lapses); the
  `cli.py` usage block lists every subcommand; `llms` states which events
  are log-only and the remaining `record` field rules. `register --ttl`
  help says it is accepted and ignored. (#19)

### Fixed

- `claims sync` without a `claims.yaml` says the file is missing in the
  checkout instead of "not committed at HEAD ... untracked files do not
  count", which stays for a file that exists but is not committed. (#19)
- `leases --expiring` refuses a negative window (exit 2). (#19)
- `audit` aligns check names to the longest one shown instead of a fixed 20
  columns; `board`/`list` rows carry no trailing spaces; the `promote` gh
  visibility WARN keeps the whole first line of gh's message instead of
  cutting it at 120 characters. (#19)

- `record` validates the would-be registry before writing: an evidence event
  that introduces a schema error is refused with the rule named (for example
  rule doi-in-palomar: a `doi-minted` commit must already be recorded by a
  `palomar-*` event), nothing is written, and `--json` prints
  `{"ok": false, ...}`. Before, such an event was saved and then blocked every
  later `update`/`register`. (#16)
- `update`, `register` and `record` no longer refuse because of schema errors
  already in the registry (another package's, say): those are reported on
  stderr as `pre-existing (not caused by this change): ...` and in `--json` as
  `preexisting_errors`; an error the change itself introduces still refuses.
  (#16)
- `record NAME retract --field event=E --field ts=T --reason TEXT` is the
  append-only correction path for a wrong evidence entry: the entry stays in
  `history[]`, an `evidence-retracted` entry names it, and `validate` and the
  upstreamed check ignore it. (#16)
- The documented transitions now exist: `update --status upstreamed` from
  `public` (needs a recorded `mathlib-pr-merged`, a new evidence event, or
  `palomar-registered`, or a `--reason` containing the URL) and
  `update --status archived` from any status, including `public` and
  `upstreamed`; public still never returns to a private status. A refused
  move lists the moves allowed from the current status. (#16)
- Every command except `doctor` refuses a registry whose schema version is
  newer than this lean-pkg supports (`registry schema version 9 is not
  supported (supported: 1, 2, 3)`, exit 1) instead of showing an empty table;
  `doctor` still reports it as a warning, and `migrate` of older versions is
  unchanged. (#16)
- Registry writes are safe across processes on one host. Every write command
  (and the TUI's claim/release/gate keys) holds an exclusive `flock` on
  `<registry>.lock`, a sibling file created on first use, from load through
  save, event append and the `--commit` step, so 8 parallel `claim`s have one
  winner and parallel `update`s lose nothing (`search --record` and
  `index add` lock only around their event append). `save_registry` writes a
  private temp file in the registry's directory, fsyncs it and replaces the
  registry atomically, keeping the old file's mode (no shared `.tmp`). An
  event line is appended only after the registry that records it is saved,
  in one write + fsync, so a failure in between leaves no orphan event.
  `audit --fix` reports an unparsable lease as `lease-invalid` instead of a
  traceback. An unexpected exception still lets `--commit` report (and not
  commit), then exits 1 with `lean-pkg: internal error: <type>: <msg>`
  (`{"ok": false, "error": ...}` under `--json`; traceback with
  `LEAN_PKG_DEBUG=1`). The TUI re-reads the registry under the lock before
  acting and refuses a TTL that is not a positive whole number instead of
  defaulting it to 24. Add `lean-packages.yaml.lock` to the registry
  repository's `.gitignore`. (#14)
- A hand-broken lease (`claimed_at: zzz`, `ttl_hours: 'x'`) no longer crashes
  `list`, `board`, `leases`, `claim`, `release`, `renew`, `update` or
  `doctor`: tables show `?` / `INVALID` in the lease cells, `leases` lists it
  as invalid (`--json`: `"invalid": true`, `expires_at` and
  `remaining_seconds` null), `doctor`'s `leases` line warns, and the writers
  refuse with `lease fields unparseable (claimed_at=..., ttl_hours=...)`;
  `release NAME --force --reason TEXT` clears it. (#18)
- `doctor` runs each check on its own: one that raises prints
  `warn <item>: internal error ...` and the rest still print. A missing
  binary is exit 127 from the subprocess helper (`promote` without `gh`
  reports `gh not on PATH`), `wiki --out` to an unwritable path fails with
  `cannot write <path>: <reason>`, and any other `OSError` is a one-line
  refusal (exit 1) rather than an internal error. (#18)
- Committed mode covers every write: `index add`, `formalization --write`,
  `claims sync`, `search --record` and the TUI's claim/release/gate keys
  (each committed after the write, inside the lock). One predicate,
  `is_write_invocation`, decides from the parsed arguments both the registry
  lock and committed mode, so dry runs (`register`/`update`/`migrate
  --dry-run`, `promote` without `--yes`, `audit` without `--fix`,
  `comparator --check`) never fetch or pull. `--commit`/`--push` on a
  read-only command print `ignored: read-only command` on stderr;
  `--no-commit` is accepted after the subcommand; a `--json` payload gains
  `"commit": {"sha", "pushed", "error"}` and says `"ok": false` when the
  commit or push failed; the fallback identity is reported on stderr. (#18)
- An explicit `--registry` uses its sibling event log even when
  `LEAN_PKG_EVENTS` is set (precedence: `--events`, the sibling of
  `--registry`, `LEAN_PKG_EVENTS`, the sibling of the resolved registry), so a
  scratch registry never writes into a shared log; `doctor`'s `events` line
  ends `[source: flag|registry-sibling|env|sibling]`. (#18)
- `formalization NAME --write` checks the lease and dry-runs the registry
  update before it writes `formalization.yaml`, so a refusal leaves the file
  untouched; `--force --reason TEXT` overrides another agent's live lease
  (`--force` alone still only overwrites a differing file). (#18)
- The index root is `--index`, else `LEAN_PKG_INDEX`, else `index/` beside
  the registry when it exists, else `./index`, so `doctor`'s `index` line
  (now ending `[source: ...]`) no longer changes with the working directory.
  `index check`, `render` and `migrate` exit 1 with `no index at <path>`
  when the root is missing (`render` creates it only for an explicit
  `--index` or `--out`), and `index migrate` on an empty index says so. (#18)

- `gate` and `comparator` compared a receipt's `lean_toolchain` with the
  working-tree `lean-toolchain`, so an uncommitted edit could make a receipt
  pass. Both now read `lean-toolchain` at the receipt's bound commit and the
  refusal names both values. Modified tracked files in the checkout print a
  stderr WARN (`dirty` in `--json`); the new `--refuse-dirty` flag refuses
  instead. (#15)
- The `promote` checklist passed with untracked or unpushed evidence. It now
  also requires the gate receipt, the comparator receipt and every comparator
  log it names to be committed at HEAD, unmodified, and HEAD to be contained
  in `origin/<branch>`, judged on local refs (nothing is fetched). Under
  `--yes`, a failed `gh` visibility query is a ✗, not a WARN. The comparator
  line now says what was checked (config and log bytes match the receipt,
  PASS line present) and no longer says "nanoda replay". (#15)
- A receipt's `generated_at`, and a comparator receipt's, must now parse as an
  ISO-8601 timestamp; being non-empty is not enough. The stored receipt name
  now drops leading dots and dashes from the job id, so `../../evil` is stored
  as `<c12>-evil.json`, and a job id left with no usable characters is
  refused. (#15)
- `register --status` only accepts `scratch`, `active` and `gate-pending`.
  `public` is accepted only together with `--visibility public` and a
  non-empty `--reason` (the reason is stored on the `registered` event);
  `verified-private` and `upstreamed` are refused. `validate` now refuses a
  `verified-private` package with no `gate.receipt`. A status/visibility
  mismatch (`verified-private` but not private, `public`/`upstreamed` but not
  public) is reported by `audit` as `status-visibility-mismatch` rather than
  refused by `validate`, because existing registries hold such rows. (#15)
- One `local_path` normaliser for `register`, `update --path` and
  relocation: a leading `~/` is expanded against `$HOME` (`~user` is
  refused), a relative path is taken relative to `$HOME`, and duplicates
  and collisions are found by comparing the resolved absolute paths, so
  `register --path $HOME/pkgs/alpha` no longer adds a second row on a
  checkout stored as `pkgs/alpha`. The stored form is `$HOME`-relative
  (absolute only outside `$HOME`), without a trailing slash. An empty or
  whitespace `--path`, or one resolving to `$HOME` itself, is refused
  instead of auto-detecting `$HOME/lean-toolchain`. (#17)
- Empty or whitespace values for `--repo` and `--owner-project` are refused
  by `register` and `update` (exit 1); `update --notes ''` still clears the
  notes. `update --license-spdx` accepts SPDX expressions (identifiers
  joined by `AND`/`OR`, `WITH` an exception, parentheses), such as
  `MIT OR Apache-2.0`. (#17)
- A missing `--reason` is always a refusal: `update --force` and
  `update --status` without one now exit 1 with `{"ok": false, "error": ...}`
  under `--json` (were usage errors, exit 2, no JSON). Usage errors print
  `{"ok": false, "error": ..., "usage": true}` under `--json` (exit stays 2).
  `release --force` requires `--reason` (the TUI asks for one) and records
  `released_by` as the acting `--agent` when given, else `force`, with the
  reason. `update --path` to the current checkout is a no-op ("already at
  <path>; nothing written", exit 0; was exit 1). `llms --json` prints
  `{"ok": true, "text": ...}`. (#17)
- A holder's re-`claim` without `--ttl` keeps the lease's TTL (it reset a
  72 h lease to 24 h), as `renew` does; a new claim still defaults to 24 h.
  `claim-refreshed` records the `reason` when one is given. (#17)
- `init` records only the registry file name and the schema version in the
  shared event log (it wrote this machine's absolute path), and
  `init --force` marks its `registry-created` event `"forced": true`
  (`--json` adds `forced`). (#17)

## [0.5.0] - 2026-10-08

### Added

- `lean-pkg doctor` also reports `lean`, `go`, `cargo` and `jq`, and warns
  when the `lake` or `lean` on PATH is not elan's proxy (`$ELAN_HOME/bin`,
  default `~/.elan/bin`), since such a binary ignores `lean-toolchain`.
- Index: `markovprocess-pilot` re-pinned at `9bb873e7aa31` after its history
  was cleaned and the repository made public; fresh lower-tier and comparator
  receipts (toolchain mechanism). Every index entry is now public and passes
  `index check --clone`.
- Decision 0006 (proposed): the public-flip gates G1 to G7 with their
  evidence and what is still open.
- `docs/existing-package-comparator.md`: a section for packages that forbid
  `sorry`, set `warningAsError`, or run `mk_all --check` in CI: a scoped
  Challenge exception with a tested placeholder scan, the `Audit`
  `leanOptions` override, and `mk_all --check --lib`.
- Index: `total-coloring-lean` re-pinned at `533f9430cb45` with a comparator
  receipt for both headline theorems (legacy mechanism); trust is now
  `comparator`.
- `scripts/bootstrap-collaborator.sh OWNER/PACKAGE`: one-command collaborator
  setup (machine and GitHub access checks, pipx install, checkouts under
  `~/src`, a committed personal registry with the package registered, Codex
  skills, `doctor`), idempotent, ending READY or NOT READY; tested offline
  under a fake HOME. It is step 0 of the comparator guide, which also gains a
  note on starting the setup from Codex.
- `docs/existing-package-comparator.md`: step-by-step guide to adding the
  comparator tier to a package that already builds, for a collaborator who
  knows little Lean.
- `docs/comparator.md`: how to write the Challenge and the Solution, a
  checklist against weakened statements, and what a package below the
  toolchain floor gets.
- `docs/getting-started.md`: installing while the repository is private,
  Linux-only sandboxing and disk needs, and the `lean_lib` entries the
  Challenge and Solution modules need.
- `docs/stranger-test.md`: who may run the gate G1 stranger test, what counts
  as success, and the transcript template.
- `lean-pkg formalization NAME [--write] [--out PATH] [--force] [--author NAME]`:
  render the package's `formalization.yaml` (mathlib-initiative schema v0.4,
  the file Palomar reads) from the registry and the repository at HEAD.
  Derived keys are regenerated every run (`project.name`, `project.license`,
  `automation.notes`, `status.sorry_count`/`axioms`/`evidence` from the gate
  receipt, `status.main_results` from every tracked comparator configuration
  with `judged_as` carried for definition holes, `alignment` from
  `claims.yaml`); every other key is kept from the existing file, seeded on
  first render from registry data (authors from `consent[]` owners/authors,
  Mathlib `builds-on`) or with a marked `[PLACEHOLDER]` and a stderr warning.
  Nothing is invented: the registry holds no paper metadata, so `sources` is
  a placeholder until a human fills it. `--write` is idempotent, refuses a
  differing file without `--force`, and sets `formalization_yaml` through the
  `update` path. Validated against the vendored v0.4 JSON schema
  (`src/lean_pkg/schemas/`, upstream commit `99c678e5`, fetched 2026-10-07)
  with `jsonschema` when importable, else a structural check (docs/formalization.md).
- `lean-pkg audit`: `formalization-missing` and `formalization-drift`
  findings for packages whose `formalization_yaml` is set (the file no longer
  equals what the generator emits); `audit` never rewrites it.

- Definition holes need a human sign-off: a comparator configuration with a
  non-empty `definition_names` is refused unless it carries
  `judged_as {reviewer, date, definitions ⊇ definition_names, basis?}` naming
  a human (model/agent tokens refused). Enforced by `comparator-check.sh`, by
  `comparator-receipt` before anything is built (nothing written), and by
  every validator against the file at the bound commit. The runner copies
  `judged_as` into the receipt entry when present (optional field; old
  receipts still validate). Rationale: comparator compares holes by name and
  type only, never by body (`src/lean_pkg/holes.py`, docs/comparator.md).
- `comparator-check.sh --configs <glob>` (repeatable discovery override) and
  `--lint-configs` (configuration rules only, jq-only, no build).
- `comparator-check.sh`: `LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1` with
  `LEAN4EXPORT_COMMIT` builds lean4export under the project toolchain in a
  dedicated checkout and verifies the binary's reported Lean version, for
  point releases lean4export never tagged (v4.34.1); the deviation is printed
  on its own `== lean4export toolchain override:` line.

- `scripts/private-boundary-check.sh --history` and `make history`: scan every
  blob and commit message reachable from any ref (the public-flip prerequisite).
- REUSE 3.3 compliance: `REUSE.toml`, `LICENSES/Apache-2.0.txt`, `make reuse`
  (part of `make check`; skipped with a notice when the tool is absent).

- `lean-pkg gate` and `lean-pkg comparator` scan the receipt bytes for
  private local paths (the `index add` scan) before copying them into
  `.lean-receipts/`: each hit is a stderr `WARN` naming the field and the
  kind of string (never the value) and an entry of `"private_strings"` in the
  JSON payload. `--refuse-private` turns the warning into a refusal (exit 1,
  nothing copied). Mapping keys are scanned as well as values; a private key
  is labelled by position (`receipt.artifacts.<key#0>`), never by its text
  (also in the `index add` scan).

- `lean-pkg audit --toolchain-floor VERSION` (env `LEAN_PKG_TOOLCHAIN_FLOOR`,
  default `leanprover/lean4:v4.35.0-rc2`, Palomar's floor): a
  `below-toolchain-floor` finding for every package whose `lean_toolchain` is
  older, except `scratch` and `archived` ones. Versions compare as
  `vMAJOR.MINOR.PATCH[-rcN]` with an rc older than its release; nightly and
  other strings are reported as `toolchain-unparsed`, never compared.

- `lean-pkg --version` prints `lean-pkg <version>` and exits 0.
- `lean-pkg doctor [--json]`: one `ok`/`warn`/`n/a` line each for the
  version, the install (an editable checkout, one whose git index tracks the
  package, adds short HEAD, branch, and behind/ahead counts against `origin/<branch>` from local refs only, never
  fetched), the registry and its schema version, the event log, the index and
  its entry count, `lake`/`elan`/`git`/`gh`/`reuse`/`bwrap`/`comparator`/`nanoda`
  on PATH, and the Python version. Exit 1 when the registry is unreadable or
  its schema version is unsupported, else 0.

### Changed

- Copyright notice is now "Copyright (c) 2026 Le Chen and Auburn University",
  confirmed with Auburn IPX for release under Apache-2.0 (decision 0004);
  new `NOTICE` file.
- Until a deputy is named, succession is covered by the maintainer's GitHub
  account successor (decision 0005); `MAINTAINERS.md` points to it.
- Decision 0003 accepted (rewrite history once before the public flip);
  `scripts/rewrite-history.sh` performs and verifies the rewrite.
- The repository moved to the `lean-pkg` organization:
  <https://github.com/lean-pkg/lean-pkg>, documentation at
  <https://lean-pkg.github.io/lean-pkg/>. GitHub redirects the old repository
  URL; the old documentation URL does not redirect.
- The vendored `scripts/comparator-check.sh` now carries the lab additions
  above and is no longer byte-for-byte the Palomar verifier script; tool pins
  are unchanged.

### Fixed

- Packages whose `local_path` is a subdirectory of their git repository
  (`lean/`, `lean/contrib/`): the evidence-only-descendant rule compared
  repository-root paths with `.lean-receipts/`, so `lean/.lean-receipts/...`
  never matched and any later commit, even a paper edit outside the package,
  was drift for `gate`, `promote` and `audit`. Paths are now judged relative
  to the package directory: only changes under it count, and within it only
  `.lean-receipts/` is evidence (DESIGN.md).

- `make test` runs pytest with a private bytecode cache
  (`PYTHONPYCACHEPREFIX` in a fresh `mktemp -d` directory, removed
  afterwards by an EXIT trap, also on Ctrl-C; `-p no:cacheprovider` kept), so concurrent runs no longer share
  pytest's rewritten `.pyc` files (a segmentation fault inside
  `_pytest/assertion/rewrite.py` was observed with two sessions at once). The
  gate is safe to run concurrently (RELEASING.md).

- `lean-pkg audit` no longer prints one `warn` line per package whose `repo`
  is not a GitHub URL (the line echoed the repo path, which may be private).
  Each such package gets a `no-github-mirror` finding with the fixed detail
  "repo is not on GitHub; visibility not mirrored"; the human output collapses
  them into one line "N package(s) without a GitHub mirror (visibility not
  checked): a, b, c", and `--json` keeps one finding per package. `gh query
  failed` warnings are unchanged. Check names longer than the column (such as
  `below-toolchain-floor`) are now separated from the package name by a space.

- `lean-pkg audit` judges `receipt-drift` and `comparator-drift` against the
  package's reference branch (the remote default branch from
  `refs/remotes/origin/HEAD`, else `main`, else `master`, else HEAD; local refs
  only; when the local branch and `origin/<b>` both exist, the tip that contains
  the other, and origin with a note when they diverged), not
  the checkout's HEAD, so a checkout parked on a feature branch no longer
  reports false drift. The evidence-only-descendant rule is unchanged. When
  the checkout is on another branch, a `checkout-branch` finding says
  "checkout on X; judged against B" and drift details carry the same note.

- Prior-art record slugs longer than 60 characters get an 8-hex suffix of the
  full slug, so two long terms sharing a prefix no longer share a record file.

- `index migrate [--check]`: rewrite `index-entry/1` entries as `index-entry/2`
  with `claims` derived from the headline; the two first entries migrated.

## [0.4.1] - 2026-10-06

### Added
- `update --license-spdx SPDX` and `update --formalization-yaml REL` set the
  registry row's publication facts (previously only `register` could).
- Index: third entry `total-coloring-lean` (lower-tier at 55edc814; the
  example of an unfinished project: two proved headlines, goal and gaps in
  notes).
- Docs: how an unfinished project is listed honestly (headlines are what is
  proved, goals and gaps in `notes`, partial results as `experimental` claims,
  progress as a new pin), with `total-coloring-lean` as the example; FAQ entry.

## [0.4.0] - 2026-10-06

Claim-level prior-art search (issue #4): search before building, across the
group's own packages and the public sources, with a citable record.

### Added
- `claims.yaml` (schema `claims/1`) at a package repository root: named,
  reusable results with stable ids `<package>/<slug>`, kinds, statuses,
  aliases, keywords, supersession (acyclic) and dependencies.
- `lean-pkg claims sync NAME` reads the file at `HEAD`, checks every
  declaration occurs in its file, and caches the claims with the commit in
  the registry (event `claims-synced`); `claims list` and `claims check`.
- `lean-pkg search TERM`: registry claims, the public index, Loogle,
  LeanSearch, Reservoir (the index data, cached by commit, never the
  name-only site search), Palomar (results API, cached by revision) and
  GitHub, in that fixed order; every source `ok` or `skipped: <reason>`,
  never zero hits when unreachable; `--offline`, `--sources`, `--json`.
- `lean-pkg search --record` writes `docs/prior-art/<date>-<slug>.md` in the
  research repository (`decision: pending` for a human) and one
  `prior-art-searched` event; claims of other private packages are listed
  only in a private package's record, marked `private-claims: yes`.
- Index: `index-entry/2` adds `claims[]`, derived from the headline at
  `index add`; `index check` refuses claims that drift from the headline;
  `index render` lists them. `index-entry/1` entries stay valid.
- Advisory notes: `claim` when a package has no recent search and no claims,
  `index add` when no record at the pin names a headline.
- Docs page `docs/prior-art.md`.

### Changed
- Registry version 3: `claims_file`, `claims`, `claims_synced_commit`
  (additive; `lean-pkg migrate` moves v1 and v2 files forward; both stay
  readable).

### Fixed (fresh-model review of the branch)
- A record never says "Not found in registry" when registry hits were
  withheld for privacy; the Hits cell says `0 shown (+N withheld)`.
- Search terms are folded (accents stripped, any script's letters count):
  `Itô` matches `Ito`; `ε` and `黎曼` are valid terms.
- `--repo-dir` outside the package's own checkout is treated as an unknown
  target, so other private packages' claims are withheld.
- Skip reasons are scrubbed of local paths before they reach output or a record.
- The record is created atomically and never follows a symlink; a malformed
  registry entry makes the registry source `skipped: unreadable` instead of a traceback.

## [0.3.2] - 2026-10-06

### Added
- Index: `markovprocess-pilot` now at comparator trust; its headline theorem
  is judged through a Mathlib-only Challenge (`exit_mean`) by `lake comparator`.

### Fixed
- `COMPARATOR_RECEIPT_HOST_LABEL` now also replaces the host name in the
  receipt's `host_policy` sentence (a copied receipt leaked it).

## [0.3.1] - 2026-10-06

### Changed
- Index: `comparator` trust requires coverage. Each headline item may carry
  `judged_as`, the Challenge theorem name; comparator trust needs every
  headline judged by a configuration in the receipt, else the entry is
  lower-tier (with a lower-tier receipt) or refused.
- The comparator PASS predicate is the full line `Your solution is okay!`
  for both mechanisms, as observed in the first live `lake comparator` run
  (2026-10-06); the kernel acceptance phrase alone no longer counts.
- The sample workflow page says which steps were run and which are
  illustrative; it shows the live toolchain run.

## [0.3.0] - 2026-10-06

The index schema (milestone M1's catalog layer, pulled forward): a public,
forkable list of trust-graded packages whose every row can be re-derived
from bytes at its pin.

### Added
- `index/<name>/entry.yaml`, schema `index-entry/1` (keys in a fixed order;
  unknown keys refused; no local paths), with verbatim receipt copies under
  `index/<name>/receipts/`. The index root is `--index PATH`, env
  `LEAN_PKG_INDEX`, or `./index`.
- `lean-pkg index add NAME`: builds an entry from the registry row
  (`--from-registry`) or from flags, reads the receipts from a clone at the
  pin, validates them with the `gate` and `comparator` validators, computes
  the trust level (`lower-tier`, `comparator`, `palomar`), refuses a
  duplicate repository and commit, and appends `index-added` to the event log.
- `lean-pkg index check [NAME ...] [--clone | --repo-dir PATH]`: static
  checks, or a re-derivation from bytes at the pin (receipts, logs,
  toolchain, `lake-manifest.json`, license, tag, headline files) with trust
  recomputed; Palomar ids and Reservoir listings are verified by API when
  reachable and reported as skipped otherwise. Exit 0, 1 or 2; `--json`.
- `lean-pkg index render [--out PATH] [--check]`: the Markdown table
  `index/README.md`, with a do-not-hand-edit banner; a test keeps it current.
- Docs: [The index](docs/index-catalog.md); CONTRIBUTING, the pull-request
  template and the index-entry issue template use the schema's field names.
- First index entry: `gromov-filling-lean` (lower-tier; Lean v4.29.0; pin
  1c4ccb9c; receipt produced on the Auburn University Easley Cluster).
- Safety of the index (from the fresh-model review): evidence files are
  written with symlinks refused at the pin and on disk; a rename into
  `.lean-receipts/` is no longer evidence-only (`gate` benefits too); a
  Palomar id grades an entry only at its registered commit and `check`
  compares the Palomar entry's source with the index entry; `index add`
  verifies that the pin and evidence are on a public branch unless
  `--allow-unpushed`; receipt copies with local-path strings are refused;
  `COMPARATOR_RECEIPT_HOST_LABEL` gives receipts a public-safe host; `render`
  fails cleanly on a malformed entry; one broken entry no longer blocks
  checking another; the private-boundary check is case-insensitive.

- `LEAN_PKG_EVENTS`: environment variable for the event-log path (after
  `--events`, before the registry's sibling file).

### Fixed
- `lean_pkg.__version__` now agrees with `pyproject.toml` (it still read 0.1.0).
- Documentation site: complete dark-mode palette (header, links, syntax
  colours, tables, footer, search, copy buttons) and light-mode contrast,
  measured to WCAG AA on every page; the workflow diagram is a light card in
  both themes.
- A comparator receipt produced under the test-only digest override now
  carries `test_only: true` and is refused by every validator.
- The toolchain probe requires the judging binaries to be executable, as the
  vendored script does.


## [0.2.0] - 2026-10-06

Milestone M0 of the charter: a tool the lab can consume instead of its own
copy. Checks were run locally with `make check` (hosted CI turns on at the
public flip).

### Added
- `lean-pkg init`: bootstrap an empty registry and its event log; refuses an
  existing registry unless `--force` and it holds no packages.
- Registry schema v2 and `lean-pkg migrate`: additive publication fields per
  package (`license_spdx`, `formalization_yaml`, `palomar[]`, `doi`,
  `reservoir`, `consent[]`); v1 registries stay readable by every command;
  `migrate` is idempotent and has `--dry-run`.
- `lean-pkg record NAME EVENT --field K=V`: evidence events with required
  fields and registry effects (`consent-recorded`, `palomar-preflight-pass`,
  `palomar-submitted`, `palomar-review`, `palomar-registered`,
  `palomar-withdrawn`, `doi-minted`, `reservoir-listed`, `mathlib-pr-opened`);
  `validate` refuses a DOI whose commit no Palomar entry names; `audit` finds
  `palomar-preflight-missing`.
- `comparator-receipt --mechanism auto|toolchain|legacy`: the toolchain
  mechanism runs `lake comparator` with the toolchain's bundled NanoDa and
  con-ron through the vendored PalomarTemplate `verify-comparator.sh`
  (Lean v4.35.0-rc2 or later, what Palomar runs); legacy keeps the pinned
  stack. Receipt schema `comparator-receipt/2` with `mechanism` and per-
  mechanism `tools`; schema 1 still validates.
- `gate` on an already-public package records the receipt and the
  `gate-passed` event without changing the status.
- `record NAME reservoir-listed` verifies the listing against Reservoir's
  package API (a 404 refuses; an unreachable network records `verified: false`).
- The toolchain mechanism checks that `bwrap` can create a user namespace
  before judging and names the Ubuntu 24.04 AppArmor fix when it cannot.
- Governance: `SECURITY.md`, issue and pull-request templates with a required
  AI-assistance line, `docs/decisions/` (0001 files and git only; 0002
  benevolent maintainer with deputies), deputy and succession lines in
  `MAINTAINERS.md`.
- Private-boundary check scans only files git would ship.
- Agent skill `search-before-building`: Loogle, Mathlib at the pin, the
  Reservoir index (not the name-only site search), Palomar, known authors and
  the GitHub long tail, with every query recorded and one decision per need;
  step 0 of `lean-paper-companion` and a row in the router.
- FAQ section on Reservoir: how it differs from lean-pkg and Palomar, why
  there is nothing to register (listing is automatic), and searching it
  before formalizing.

## [0.1.0] - 2026-10-05

First tagged release: the tool extracted from its private lab origin into its own
repository, with the comparator tier and the documentation site. The
repository is private at this version; checks were run locally (see
RELEASING.md).

### Added
- `lean-pkg` registry CLI: statuses `scratch → active → gate-pending →
  verified-private → public → upstreamed`, leases (`claim`/`release`),
  lower-tier receipts (`lake build`, sorry scan, `#print axioms`) bound to a
  commit, `gate`, `promote` with a dry-run checklist and a one-way `--yes`,
  `audit`, `llms` contract output.
- Comparator tier: `comparator-receipt` runner wrapping the vendored Palomar
  `comparator-check.sh`; all-or-nothing evidence write; host policy;
  `lean-pkg comparator --receipt [--check]`; `gate --comparator`;
  audit findings `comparator-drift` and `comparator-missing`.
- Evidence-only descendant rule: a receipt bound to commit X is accepted at
  HEAD H when H descends from X and only `.lean-receipts/` paths differ.
- Eighteen agent skills under `skills/` with an installer for Claude Code
  and Codex (`scripts/install_skills.py`).
- Documentation site (`docs/`, Jekyll Cayman): motivation, getting started,
  how-to, lifecycle, receipts, comparator, CLI, agents, newcomers, sample
  workflow with a seven-step diagram, FAQ, skills; client-side search and a
  dark/light toggle; `llms.txt`.
- Private-boundary check (`scripts/private-boundary-check.sh`) and a test
  suite of 240 tests.
- Secretary-bird logo and favicon; `CITATION.cff`.
