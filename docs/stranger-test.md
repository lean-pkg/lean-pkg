---
title: Stranger test
---

[&larr; lean-pkg documentation](index.md)

*If someone who has never seen the tool cannot get a receipt from the README,
the README is wrong.*

lean-pkg goes public only after a person outside the project runs it from the
documentation alone and records the run. This page is that record's template.
The maintainer reads the transcript, fixes what it exposes, and signs gate G1
of the public-flip checklist ([RELEASING](../RELEASING.md)) when a run
succeeds.

Results so far: the first outsider run (2026-10-10, lean-pkg 0.7.22) met all four success criteria in 52 minutes with an AI agent's help; its findings are issues [#61](https://github.com/lean-pkg/lean-pkg/issues/61) and [#62](https://github.com/lean-pkg/lean-pkg/issues/62) (bugs) and [#63](https://github.com/lean-pkg/lean-pkg/issues/63) to [#66](https://github.com/lean-pkg/lean-pkg/issues/66) (documentation).

## Who can run it

- You have not worked on lean-pkg, watched it being built, or been walked
  through it.
- You bring your own Lake project, or a fresh small one you write for the
  test. It must not be one of the packages in this repository's index.
- You use a machine that has never had lean-pkg installed: a fresh account,
  container or virtual machine is fine. Linux is required for
  `lean-pkg receipt comparator`.

You may use a coding agent, if you say so in the transcript. You may not ask
the maintainers for help during the run; if you are stuck for more than 30
minutes, record where and stop. A stuck run is a useful result.

## What counts as success

All four, in order:

1. `lean-pkg` installed from the README's instructions.
2. Your project in the Challenge/Solution layout with a `comparator.json`
   ([Getting started](getting-started.md), worked example step 1).
3. `lean-pkg receipt comparator` ends with `comparator receipt written: ...` and the
   file `.lean-receipts/<commit12>-comparator.json` exists. It writes nothing
   unless every configuration is accepted.
4. `lean-pkg comparator check NAME --receipt <that file>` reports the
   receipt valid.

## Before you start

- Note the time. Record wall-clock time for each step; with the legacy
  mechanism the first `lean-pkg receipt comparator` can take about 25 minutes while it
  builds its tools; the toolchain mechanism (Lean v4.35.0-rc2 or later)
  builds none.
- Read only `README.md` and the pages it links to. Note every page you open.
- Copy the template below into a file named `stranger-test-YYYY-MM-DD.md`.
- The transcript may become public. Replace your user name, home path, host
  name and anything else private with `<user>`, `<home>`, `<host>` before you
  send it. Do not paste tokens or keys.

## Template

````markdown
# lean-pkg stranger test

## Run

- Date (YYYY-MM-DD) and time zone:
- Tester (name or GitHub handle):
- Relationship to the project (confirm: never worked on or been shown lean-pkg):
- lean-pkg commit or release tested (`lean-pkg --version`, and the README's commit if known):
- Coding agent used, if any (name, version, what it did):

## Machine

- OS and version:
- Fresh account, container or VM? (which):
- CPU cores / RAM / free disk:
- Python version (`python3 --version`):
- elan / lean / lake versions (`elan --version`, `lake --version`):
- Other tools installed for the run (go, cargo, jq, gh, ...), with versions:

## Project under test

- Repository (URL, or "local only"):
- Commit (`git rev-parse HEAD`):
- `lean-toolchain`:
- Mathlib rev (from `lake-manifest.json`):
- Theorems in `comparator.json` (names):
- How the Challenge/Solution split was made (by hand / with an agent / other):

## Steps

Repeat this block for every command that mattered, in order.

### Step N: <what you were trying to do>

- Doc page and section you were following:
- Command:

  ```text
  <exact command>
  ```

- Result (exit code, last lines of output):

  ```text
  <output>
  ```

- Wall-clock time:
- Outcome: worked as documented / worked after a change (what) / stuck

## Success criteria

| # | Criterion | Met? | Evidence |
|---|---|---|---|
| 1 | lean-pkg installed from the README | yes / no | command and `lean-pkg --version` output |
| 2 | Challenge/Solution layout with `comparator.json` | yes / no | file list |
| 3 | `lean-pkg receipt comparator` accepted every configuration | yes / no | receipt file name and its `commit` field |
| 4 | `lean-pkg comparator check NAME --receipt ...` valid | yes / no | output line |

## Where the documentation failed you

| Page and section | What it said | What actually happened or was missing | How you got past it (or "stuck") |
|---|---|---|---|
| | | | |

## Overall

- Total wall-clock time:
- Would you use this tool on your own project? Why or why not:
- The one change that would have helped most:

## Attachments

- The comparator receipt JSON (the `.lean-receipts/<commit12>-comparator.json` file).
- The per-configuration logs, if a step failed.
````

## Sending the transcript

Open an issue in this repository titled `Stranger test: YYYY-MM-DD` and attach
the transcript and the receipt, or send them to a maintainer listed in
[MAINTAINERS](../MAINTAINERS.md). A maintainer re-checks the receipt with
`lean-pkg comparator check NAME --receipt PATH`, records the result, and
files an issue for every row of "Where the documentation failed you".
