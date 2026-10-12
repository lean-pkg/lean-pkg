# Releasing lean-pkg

Everything is a file: a release is three files that agree, one annotated
tag, and release notes copied from the changelog.

## When to tag

| Change | Version | When |
|---|---|---|
| A roadmap milestone lands (M0, M1, …) | minor: `0.(N+1).0` | at the milestone's exit criteria, on `main` |
| A fix or a docs-only correction worth citing | patch: `0.N.(k+1)` | when `## [Unreleased]` holds only fixes and someone needs to cite it |
| Nothing user-visible changed | no tag | docs polish and refactors ride with the next release |

A tag is a claim about a commit. Tag when the claim is true, not on a
calendar. Receipts bound to a commit stay valid across tags; a tag never
changes evidence.

## Checklist

0. Documentation gap pass: every CHANGELOG `### Added` entry since the
   previous tag has a documented home. A new subcommand must reach the front
   door (README.md, `docs/index.md` or `docs/how-to.md`), have its
   `docs/cli.md` row, and be covered by `lean-pkg llms` (llms.txt); `make
   check` enforces the first two with the subcommand-coverage test
   (`tests/test_docs.py`). An option-level entry (a new flag, field or
   configuration key) is at home in the detail page the front door links:
   its `docs/cli.md` row, `docs/comparator.md` or `docs/formalization.md`; a
   how-to recipe is welcome but not required. The test is the floor, not the
   whole pass. Numbered 0 so that references to steps 1 to 7 elsewhere stay
   valid.
1. Move the entries under `## [Unreleased]` in `CHANGELOG.md` to a new
   `## [X.Y.Z] - YYYY-MM-DD` section.
2. Set `version` in `pyproject.toml` and `version` plus `date-released` in
   `CITATION.cff`.
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
4. Commit (`release: X.Y.Z`; the four version files by name, never
   `git add -A`; abort if anything else is modified or untracked) and push
   `main`.
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
7. Once the repository is public: Zenodo archives the GitHub release
   automatically; paste the version DOI into `CITATION.cff` (`doi:`) and
   the changelog entry in the next patch.

## Gates

`make check` runs the test suite, the private-boundary check, the tab scan,
and the version-agreement check. The gate is safe to run concurrently: `make test` gives
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
