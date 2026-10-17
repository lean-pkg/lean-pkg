## Scope

One logical change. What it does and why, in two or three sentences. Name the
issue, field-report finding or accepted proposal it implements, if any.

## Checks run

The checklist of CONTRIBUTING.md, "What a change must carry".

- [ ] `make check` is green (tests, private-boundary check, tab scan, version agreement, REUSE)
- [ ] new behaviour and every new refusal path have a test; tests use temporary registries only
- [ ] `make goldens` run and `git diff tests/golden` reviewed if help text or a `--json` payload changed
- [ ] `make docs` run if the CLI contract or an index entry changed (CI fails on a stale generated file)
- [ ] docs updated for any user-visible change; CHANGELOG `Unreleased` entry added
- [ ] Upgrade record: docs/upgrades/<next>.yaml has an entry for this PR, or `no_author_change` with a reason (see RELEASING.md step 0)
- [ ] no host name, user name, local path or private paper name in files, commit messages or this text
- [ ] a schema change, a removed or renamed command, flag or JSON key, or a trust-model change links its accepted proposal (docs/proposals)

### Commands that actually ran

One line per command with the result you observed, for example
"`make check`: 1777 passed, 4 skipped; all gates green". List only commands
that ran. A gate that could not run on your host (Slurm, comparator, a real
Lean build) is listed as "not run, because ...", never left out.

### Regression evidence (fixes only)

How the new test fails on the base revision, for example "with `main`'s
`receipt.py`, `test_x` fails: <the assertion>".

## Index entry (only for a pull request that adds or changes `index/<name>/`)

- [ ] the entry was written by `lean-pkg index add` (trust is computed, receipts are verbatim copies; nothing typed by hand)
- [ ] `lean-pkg index check --clone <name>` passes; its output is pasted below
- [ ] `index/README.md` re-rendered (`make docs` or `lean-pkg index render`)
- [ ] `consent.recorded` is true, or the entry is marked not for publication in `notes`

## Trust boundary

Does this change what a receipt proves, what `gate` or `promote` accepts, or
what the boundary check forbids? If yes, say exactly how, and add the test
that pins the new behaviour. An effect you have not established is written
"unknown", never "no".

## Remaining proof gaps

What this pull request does not establish, for example "tested only with the
toolchain mechanism" or "comparator not rerun". Write "none" only when you
have established that; if unsure, write "unknown".

## AI assistance

State which AI tools were used and how (for example "implementation drafted
with Claude, reviewed and tested by me"), or "none". The author is
responsible for every line.
