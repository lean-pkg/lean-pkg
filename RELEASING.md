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

1. Move the entries under `## [Unreleased]` in `CHANGELOG.md` to a new
   `## [X.Y.Z] - YYYY-MM-DD` section.
2. Set `version` in `pyproject.toml` and `version` plus `date-released` in
   `CITATION.cff`.
3. Commit (`release: X.Y.Z`) and push `main`.
4. Run `make release VERSION=X.Y.Z` (that is `scripts/release.sh`). It
   refuses unless: on `main`, clean tree, `main == origin/main`, the three
   files agree (`scripts/check-versions.py`), the changelog section exists
   and is non-empty, and `make check` is green. Then it creates the annotated
   tag, pushes it, and opens the GitHub release with the changelog section as
   notes.
5. Request a Pages build if docs changed:
   `gh api -X POST repos/lean-pkg/lean-pkg/pages/builds`.
6. Once the repository is public: Zenodo archives the GitHub release
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
and get it green first. A hit there is removed only by rewriting history or
by publishing from a fresh history; both are maintainer decisions recorded in
`docs/decisions/`. Decision 0003 chose the rewrite: `scripts/rewrite-history.sh`
(dry run by default, `--push` to publish), then reset every other clone. `make reuse` (REUSE 3.3 compliance, `pipx install reuse`)
is part of `make check` and must report "compliant", not "SKIPPED", on the
release machine.
