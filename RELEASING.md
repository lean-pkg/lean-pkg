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
| Only `### Fixed` entries and documentation | patch: `0.N.(k+1)` | when someone needs to cite the fix |
| Nothing user-visible | no tag | docs polish and refactors ride with the next release |

Any one minor trigger makes the release a minor. From 0.7.1 to 0.7.54 new
features shipped as patches; that stops here, and the next release after
this policy is `0.8.0`. `make cut-release` warns (and does not refuse) when
a patch version is requested while `## [Unreleased]` has an `### Added` or
`### Changed` section, and prints the minor version it suggests; a
`### Changed` entry that changes no format may still go out in a patch, which
is why it is a warning.

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

After 1.0.0, versions follow [Semantic Versioning](https://semver.org/)
proper: a breaking change (a removed command, flag or JSON key, or a format
an older lean-pkg cannot read) is a major, an addition a minor, a fix a
patch.

A tag is a claim about a commit. Tag when the claim is true, not on a
calendar. Receipts bound to a commit stay valid across tags; a tag never
changes evidence.

## Checklist

`make cut-release VERSION=X.Y.Z` runs the whole checklist below from a clean
`main` (`scripts/cut-release.py`). It lists the `### Added` entries for step 0,
which stays a judgment, so read them, and warns when X.Y.Z is a patch bump
while `## [Unreleased]` has an `### Added` or `### Changed` section ("When to
tag"), then: commits the citation bump (2a),
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
   (docs/design/upgrade-preflight.md): `lean-pkg doctor --upgrade-from
   PREVIOUS --json` must report an empty `uncurated[]` and
   `record_problems[]`, every merged pull request being either an entry
   or listed under `no_author_change`.
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
step 4 passes). The gate is safe to run concurrently: `make test` gives
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
