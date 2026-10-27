# Releasing lean-pkg

Everything is a file: a release is three files that agree, one annotated
tag, and release notes copied from the changelog.

## When to tag

The version says what kind of change a release carries. While lean-pkg is
0.x, the rule is:

| `## [Unreleased]` holds | Version | Notes |
|---|---|---|
| Any `### Added` entry: a new command, or a new user-visible flag, field or feature | minor: `0.(N+1).0` | |
| A format change of an on-disk schema (registry, claims, coverage, receipt, estimate) | minor: `0.(N+1).0` | always with a migration, and a `### Changed` entry that names the format and the migration |
| A roadmap milestone landing (M0, M1, ...) | minor: `0.(N+1).0` | at the milestone's exit criteria, on `main` |
| A raised requirement (an upgrade-record entry of kind `requirement`: the Python floor, a dependency) | minor: `0.(N+1).0` | an install that worked can stop working, which is no fix |
| Only `### Fixed` entries and documentation | patch: `0.N.(k+1)` | when someone needs to cite the fix |
| Nothing user-visible | no tag | docs polish and refactors ride with the next release |

Any one minor trigger makes the release a minor. From 0.7.1 to 0.7.54 new
features shipped as patches; that stops here, and the next release after
this policy is `0.8.0`.

### How the version is checked

The version is derived, not chosen: `make release-version` prints it, and
`make cut-release VERSION=X.Y.Z` refuses any other X.Y.Z before it touches a
file, naming the required version and the entries that decide it
(`scripts/release-version.py`). It reads two sources, which must agree:

- **The in-progress upgrade record** (`docs/upgrades/<next>.yaml`, below).
  A minor is required by any entry of kind `command`, `format` or
  `requirement`, and by any entry whose `changelog` heading is `Added`,
  `Deprecated` or `Removed` (a new user-visible flag, field or feature,
  whatever its kind). Entries of kind `behavior` under `Fixed`, `Changed`
  or `Security`, and the pull requests listed under `no_author_change`,
  are a patch.
- **The `## [Unreleased]` headings of CHANGELOG.md.** An entry under
  `### Added`, `### Deprecated` or `### Removed` requires a minor and must
  be backed by a record entry with that `changelog` heading. Entries under
  `### Fixed`, `### Changed`, `### Security` and `### Documentation` are a
  patch: a `### Changed` entry that changes a format has a record entry of
  kind `format`, which is what makes the release a minor.

A roadmap milestone is not in the files: pass it as
`make release-version MILESTONE=M1` and `make cut-release VERSION=X.Y.Z
MILESTONE=M1`. A major (`1.0.0`, below) is a maintainer decision and is
accepted as requested.

Where the two sources disagree the release is refused, never guessed: a
record entry whose `changelog` heading has no entry in `## [Unreleased]`, an
`### Added` entry that no record entry backs, or the entry of a pull request
listed under `no_author_change` under `### Added`, `### Deprecated` or
`### Removed`. An entry belongs to a pull request when its text cites
`(#N)` or when the squash commit `... (#N)` since the last release added its
first line. `make changelog` (part of `make check`, and CI) runs the same
consistency check on every pull request. It also runs
`scripts/release-version.py --curated`: every pull request merged since the
last release must be a record entry or listed under `no_author_change`, and
CI adds the pull request under test (`--pr N`), whose squash commit does not
exist yet, so a missing entry fails the pull request itself instead of
surfacing as `uncurated[]` when the release is cut.

### CHANGELOG headings

Keep a Changelog's six types plus one for documentation:

| Heading | For | Bump |
|---|---|---|
| `### Added` | a new command, or a new user-visible flag, field or feature | minor |
| `### Changed` | changed behaviour, a format change (it names its migration), release tooling, tests or CI | patch, unless the record has a `format` entry |
| `### Deprecated`, `### Removed` | "Aliases and deprecations" below | minor |
| `### Fixed` | a bug fix | patch |
| `### Security` | a security fix | patch |
| `### Documentation` | documentation only (a new page, a recipe, a section) | patch |

A pull request listed under `no_author_change` in the record changes nothing
for a package author, so its entry is never under `### Added`: docs go under
`### Documentation`, release tooling, tests or CI under `### Changed` or
`### Fixed`. Until 0.18.0, docs entries were filed under `### Added`; the
released sections stay as they are (they must equal their tags).

### The record's provisional name

The first pull request after a release opens the next record as the next
minor, `docs/upgrades/0.(N+1).0.yaml` and `.md`, before anyone knows what the
release will hold: its name is provisional. Only the newest record may carry
an unreleased version. `make cut-release` names it for the version actually
cut, in its own commit (step 2b: `docs/upgrades/0.(N+1).0.*` become
`0.N.(k+1).*` for a patch, with the `version:` key, the page's title,
heading and links rewritten), so every released record is named for its
release. `scripts/release-version.py --rename X.Y.Z` does the same by hand.
`lean-pkg doctor --upgrade-from` reports, under `record_problems[]`, a record
whose file name is not its version, an older record that was never released,
and an unreleased record listing a pull request that already shipped (the
rename was missed).

### The breaking-entries file

`src/lean_pkg/data/upgrade-breaking.yaml` lists every record entry with
`breaking: true` (version, id, pr, kind, area), provisional record included.
It ships in the package, so an installed lean-pkg, which has no `docs/`, can
name the breaking changes between the lean-pkg that wrote a package's
receipts and the running one (`status NAME`, `doctor --package NAME`;
[docs/receipts.md](docs/receipts.md#which-lean-pkg-wrote-a-receipt-producer-skew)).
It is generated, never edited: a pull request that adds a breaking entry,
or changes the `version`, `id`, `pr`, `kind`, `area` or `breaking` of one,
runs `python3 scripts/upgrade-breaking.py` (or `make docs`) and commits the
file; `tests/test_producer_skew.py` fails when it differs from the
records. A record entry whose `area` uses a component that is new to the
records also classifies it, in `EVIDENCE_AREAS` or `OTHER_AREAS` of
`src/lean_pkg/producer_skew.py` (the same test names an unclassified one).
`scripts/release-version.py --rename` (step 2b) rewrites the file with the
record, and step 2b commits both.

### Version history

`scripts/release-version.py --history --since 0.1.0` applies the rule to
every tag: each release's CHANGELOG section at its tag, its record when one
exists (from 0.11.0), and the pull requests of its range. These releases do
not follow it. They stay as published: a tag is never moved, renumbered or
deleted, and released CHANGELOG sections are not edited.

| Release | Cut as | The rule gives | Why |
|---|---|---|---|
| 0.3.2 | patch over 0.3.1 | minor, 0.4.0 | one `### Added` entry, before this policy |
| 0.4.1 | patch over 0.4.0 | minor, 0.5.0 | three `### Added` entries, before this policy |
| 0.7.1 to 0.7.6, 0.7.8 to 0.7.11, 0.7.13, 0.7.14, 0.7.16 to 0.7.24, 0.7.28, 0.7.30 to 0.7.33, 0.7.36, 0.7.38, 0.7.40, 0.7.42, 0.7.43, 0.7.45, 0.7.47 to 0.7.52, 0.7.54 | patch | minor | `### Added` entries shipped as patches, before this policy (it starts at 0.8.0) |
| 0.17.0 | minor over 0.16.0 | patch, 0.16.1 | the record holds one `behavior` fix (#273) and two docs pull requests under `no_author_change` (#272, #274), whose CHANGELOG entries were filed under `### Added`; the record had been opened as `0.17.0.yaml` before its contents were known |

From 0.11.0 to 0.16.0 and in 0.18.0 every release has a `command` or
`format` record entry or a backed `### Added` entry, so each minor is right;
the `### Added` sections of 0.11.0 to 0.14.0 also hold entries of
`no_author_change` pull requests, which the rule does not count. Releases
before 0.11.0 have no record, so a patch with `### Changed` entries (0.3.1,
0.7.12, 0.7.15, 0.7.26, 0.7.27, 0.7.29, 0.7.34, 0.7.35, 0.7.39, 0.7.41,
0.7.44) is right unless one of them changed a format, which the files do
not say.

### 1.0.0

1.0.0 is tagged when all of these hold:

- The command tree is final: commands are grouped as noun-verb pairs, old
  spellings are permanent aliases, unknown commands dispatch to extensions,
  and JSON output can be narrowed with `--fields` and `--jq`.
- Every on-disk format (registry, claims, coverage, receipt, estimate) carries
  a version and has a migration path from every earlier version.
- The golden CLI contract (`tests/golden`: help texts and `--json` key sets)
  is the public API: it changes only in a minor (a major after 1.0), with a
  CHANGELOG entry.
- The deprecation policy below is in force, and a schema changes only
  together with a migration command.
- It is used in practice: several public companions run `page --check` in
  CI, and at least one package has gone through a Palomar submission.

### Aliases and deprecations

Decided with the 1.0 command tree
([docs/design/1.0-command-tree.md](docs/design/1.0-command-tree.md),
accepted 2026-10-17). Renaming and removing are different things:

- **A renamed command or flag is an alias forever.** The old spelling parses
  to exactly the namespace of the new one, prints the same output (JSON
  included) and exits with the same code. In text mode it prints one stderr
  line, `note: 'lean-pkg claim' is now 'lean-pkg lease claim' (the old
  spelling keeps working)`, which `LEAN_PKG_NO_ALIAS_NOTE=1` silences;
  `--json` never prints it. Every alias is a row of the alias table
  (`src/lean_pkg/command_tree.py`) and of `ARGV_TABLE`
  (`tests/test_cli_contract_argv.py`), so removing one fails the suite.
  `lean-pkg help aliases` lists them. A spelling is never deprecated.
- **A deprecation removes behavior** (a flag, mode, key or command whose
  effect goes away). It is announced in a `### Deprecated` CHANGELOG section
  with its replacement; it keeps working, with a stderr
  `WARN: deprecated: ...` line naming the replacement and the earliest
  version that may remove it (and a `deprecations[]` key in `--json`
  output); it lives for at least **two minor releases and 60 days, whichever
  is longer**; and it is removed only in a release whose `### Removed`
  section names it. After 1.0.0 a removal is a major release anyway.

Deprecations in force, each with its removal rule:

- `registry-docs-descendants` (proposal 0002, #319): the registry record's
  `docs_descendants: true` as the docs-only opt-in, and
  `update NAME --accept-docs-descendants` that sets it. Replacement: the
  committed `lean-pkg-binding.yaml` (`update NAME --write-binding-file`).
  Removed (stage G4) no earlier than the latest of: 1.0.0, one minor release
  after the release that deprecated it, and two minor releases plus 60 days
  (the maintainer's decision of 2026-10-26). The veto,
  `docs_descendants: false`, stays.

After 1.0.0, versions follow [Semantic Versioning](https://semver.org/)
proper: a breaking change (a removed command, flag or JSON key, or a format
an older lean-pkg cannot read) is a major, an addition a minor, a fix a
patch.

A tag is a claim about a commit. Tag when the claim is true, not on a
calendar. Receipts bound to a commit stay valid across tags; a tag never
changes evidence.

## Checklist

`make cut-release VERSION=X.Y.Z` runs the whole checklist below from a clean
`main` (`scripts/cut-release.py`). It first refuses, before any file is
touched, unless X.Y.Z is the version `make release-version` prints ("How the
version is checked"); then lists the `### Added` entries for step 0, which
stays a judgment, so read them; then: commits the citation bump (2a), names
the upgrade record for X.Y.Z in its own commit (2b, "The record's provisional
name"),
refuses unless the public-safety scan (3) is clean, writes the version files
and the summary line (1, 2), runs `make check` on that tree, commits and
pushes them (4), runs `scripts/release.sh` (5), and waits up to `DOI_WAIT`
seconds (default 300) for Zenodo to record the version DOI (7). A failing
`make check` restores the four version files and stops before the release
commit, so `origin/main` never says X.Y.Z without the gate having passed.
Re-running it after a failure resumes where it stopped: once the version
files say X.Y.Z and `release: X.Y.Z` is in `HEAD`'s history, it resumes at
the push and step 5, and the tag goes on `HEAD`, so a fix committed and
pushed on top of the release commit after a failed step 5 is part of the
release. It refuses when origin already has the tag at another commit,
unless the only commit since is an unpushed step 7 commit, which it pushes
(step 5 then skipped). Every push is retried on transient errors as in
step 5. A step 7 timeout only warns, and
`make record-doi VERSION=X.Y.Z` finishes it later. Set
`LEAN_PKG_COMMIT_TRAILER` to add trailers to every commit both scripts make,
one `Key: value` per line (for an assistant-run release:
`LEAN_PKG_COMMIT_TRAILER='Co-Authored-By: Name <email>'`); a malformed value
is refused before anything is written. The steps by hand:

0. Documentation gap pass: every CHANGELOG `### Added` entry since the
   previous tag has a documented home. A new subcommand must reach the front
   door (README.md, `docs/index.md` or `docs/how-to.md`), have its page in
   the command reference (`docs/commands/`, generated by `make docs`; its
   notes and examples in `docs/_commands/`), and be covered by `lean-pkg llms`
   (llms.txt); `make
   check` enforces the first two with the subcommand-coverage test
   (`tests/test_docs.py`). An option-level entry (a new flag, field or
   configuration key) is at home in the detail page the front door links:
   its command page (`docs/_commands/intro/`), `docs/comparator.md` or
   `docs/formalization.md`; a
   how-to recipe is welcome but not required. The test is the floor, not the
   whole pass. Numbered 0 so that references to steps 1 to 7 elsewhere stay
   valid. The same pass completes the upgrade record
   `docs/upgrades/X.Y.Z.yaml` and its page `docs/upgrades/X.Y.Z.md`
   (docs/design/upgrade-preflight.md; until the cut they carry the
   provisional name, "When to tag"): `lean-pkg doctor --upgrade-from
   PREVIOUS --json` must report an empty `uncurated[]` and
   `record_problems[]`, every merged pull request being either an entry
   or listed under `no_author_change`, and `make release-version` must
   print X.Y.Z.
1. Move the entries under `## [Unreleased]` in `CHANGELOG.md` to a new
   `## [X.Y.Z] - YYYY-MM-DD` section.
2. Set `version` in `pyproject.toml` and `version` plus `date-released` in
   `CITATION.cff`.

   **2a.** Point the BibTeX examples at the new version: the
   `version = {X.Y.Z},` line of the citation block in `README.md` and in
   `docs/index.md`. Commit the two files on their own (`citation: bibtex
   examples cite X.Y.Z`) and push before the release commit, so the tagged
   commit already cites its own version and the release commit stays the
   four version files. Labelled 2a so that references to steps 3 to 7
   elsewhere stay valid.

   **2b.** Name the in-progress upgrade record for X.Y.Z when its
   provisional name differs (`scripts/release-version.py --rename X.Y.Z`:
   both files renamed, the version inside them rewritten), committed on its
   own (`upgrades: the record is X.Y.Z's`) and pushed with 2a, so the
   tagged commit carries the record under its release's name.
3. Public-safety self-scan: `make public-safety` must report 0 blocking
   and 0 warnings under `--strict`. It clones this repository with every
   ref into a temporary directory, registers the clone as `lean-pkg-self`
   in a throwaway registry (never a real one), and runs
   `lean-pkg public-safety lean-pkg-self --history --strict` with this
   checkout's code, history included. It needs the untracked
   `.lean-pkg-private-patterns` at the repository root (listed in
   `.gitignore`; the maintainer keeps it): `block:` lines for the
   private-boundary list in `scripts/private-boundary-check.sh`, written so
   the file never holds a raw private string, and anchored `allow:` lines,
   each with a comment naming the file, for the fixtures and docs that
   quote the built-in patterns on purpose. The split-form `block:` lines
   spell the private strings recoverably, so the file is never committed,
   here or anywhere: the release commit (step 4) adds the four version
   files by name (`CHANGELOG.md`, `CITATION.cff`, `pyproject.toml`,
   `src/lean_pkg/__init__.py`), never `git add -A`, and is aborted if the
   tree is otherwise dirty. A hit is fixed in the tree, or,
   for a real private string, reported before anything is tagged; never
   allowed away. Paste the summary line into the version's `CHANGELOG.md`
   section, which becomes the release notes, as printed (it names no path):
   `public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree,
   0 history); 0 blocking, 0 warning(s)`.
   **3a.** The supported stacks' matrix passes: for every stack in
   [docs/stacks/](docs/stacks/README.md) that is declared (`published`
   set), whose `supported_until` has not passed and whose `lean_pkg` range
   contains X.Y.Z, its matrix entries
   (proposal 0004, "How a stack is tested") run through `verify`,
   `page --write`, `publish --dry-run` and `receipt check` with this release.
   `make cut-release` does not run it (the package builds are too large for
   it); a release whose matrix has a failing entry is not tagged. Labelled
   3a so that references to steps 4 to 7 elsewhere stay valid.
4. Run `make check` on the tree with the version files written, then
   commit (`release: X.Y.Z`; the four version files by name, never
   `git add -A`; abort if anything else is modified or untracked) and push
   `main`. A gate that fails after the push would leave `origin/main` at
   X.Y.Z with no tag.
5. Run `make release VERSION=X.Y.Z` (that is `scripts/release.sh`). It
   refuses unless: on `main`, clean tree, `main == origin/main`, the three
   files agree (`scripts/check-versions.py`), the changelog section exists
   and is non-empty, and `make check` is green. Then it creates the annotated
   tag, pushes it, opens the GitHub release with the changelog section as
   notes, and requests a Pages build. The tag push, the release and the Pages
   request are retried on transient network errors (connection resets,
   timeouts, HTTP 5xx), waiting 10, 20, then 40 s; a permanent refusal (auth
   failure, `[rejected]`, HTTP 401/403/404/422) stops at once with the exact
   error. `LEAN_PKG_RELEASE_RETRIES` (attempts per step, default 4) and
   `LEAN_PKG_RELEASE_RETRY_DELAY` (first delay in seconds, default 10) tune
   this. Every step is idempotent: if the run stops part way, fix the cause
   and run the same command again. It reuses an annotated tag that already
   exists at `HEAD`, skips the push when origin holds the same tag object,
   and skips the release when it already exists. It refuses, and never
   forces, when the tag exists at another commit or origin has a different
   tag object.
6. If the script warned that the Pages build request failed, request it by
   hand: `gh api -X POST repos/lean-pkg/lean-pkg/pages/builds`.
7. Record the Zenodo version DOI: `make record-doi VERSION=X.Y.Z`
   (`scripts/record-doi.py`; `DOI_WAIT=600` polls while Zenodo archives the
   release, usually minutes). It finds the version record under the concept
   DOI in `CITATION.cff` (`doi:`, which stays the concept DOI), checks that it
   points at `tree/vX.Y.Z`, lists the version DOI under `identifiers:`, adds a
   `Zenodo version DOI:` line to the version's `CHANGELOG.md` section, and
   commits (`citation: Zenodo version DOI for X.Y.Z`) and pushes `main`. It
   changes nothing when both files already carry the DOI. The push is
   retried on transient errors as in step 5 (same variables); when it still
   fails, the commit stays local, the message says so, and running the same
   command again (or `make cut-release VERSION=X.Y.Z`) pushes it: a `main`
   ahead of `origin/main` by exactly that commit is resumed, not refused.

## Gates

`make check` runs the test suite, the private-boundary check, the tab scan,
the version-agreement check, and the released-sections check
(`scripts/check-changelog-releases.py`: each `## [X.Y.Z]` section of
`CHANGELOG.md` reads as it did at tag `vX.Y.Z`, record-doi's `Zenodo version
DOI:` line aside; the newest section is skipped until its tag exists, so
step 4 passes), and the CHANGELOG category check (`scripts/release-version.py
--check`, "How the version is checked"). The gate is safe to run concurrently: `make test` gives
each run a private bytecode cache (`PYTHONPYCACHEPREFIX` in a fresh temporary directory,
removed afterwards), so two sessions never share pytest's rewritten `.pyc` files. Hosted CI runs the same targets once the
repository is public; until then the maintainer runs them locally and the
pre-push hook enforces it in clones that opt in:

```bash
git config core.hooksPath .githooks
```

## Before the public flip

`make check` scans the tree git would ship. The flip exposes every commit, so
run `make history` (every blob and commit message reachable from any ref)
and get it green first. Then run `make public-safety` (checklist step 3): the
same scan through lean-pkg's own `public-safety`, which adds the built-in
families (home paths, emails, internal files, manuscript labels) to the
boundary list; decision 0006 gate G8 records its result. A hit there is removed only by rewriting history or
by publishing from a fresh history; both are maintainer decisions recorded in
`docs/decisions/`. Decision 0003 chose the rewrite: `scripts/rewrite-history.sh`
(dry run by default, `--push` to publish), then reset every other clone. `make reuse` (REUSE 3.3 compliance, `pipx install reuse`)
is part of `make check` and must report "compliant", not "SKIPPED", on the
release machine.

Gate G1 (stranger test) needs a dated transcript from someone outside the
project who ran the tool from the documentation alone; the template is
[docs/stranger-test.md](docs/stranger-test.md).
