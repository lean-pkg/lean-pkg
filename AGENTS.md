# AGENTS.md

Front door for coding and research agents working with or on `lean-pkg`.

## Using the tool

Run `lean-pkg llms` and follow it. It is the agent contract: the standing
rules, the status transitions and the evidence each one needs, every
subcommand with an example, both receipt schemas, the event-line schema and
the exit codes. The same text is in [llms.txt](llms.txt).

The rules that matter most:

- Never hand-edit the registry YAML. Use `register` and `update`.
- Claim a package before you work on it, and release it when you are done.
- Pass flags and use `--json`. Nothing prompts off a tty; prompt paths exit 2.
- `promote` is a dry run unless `--yes` is given, and `--yes` makes the
  repository public. That cannot be undone. Read the dry-run checklist
  first.
- Run `lean-pkg receipt comparator` only where its host policy allows (a Slurm job,
  a host in `COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or an explicit
  `COMPARATOR_RECEIPT_ALLOW_HOST=1`).

The supported interface is the CLI and its `--json` outputs. `import lean_pkg`
is not: internal function signatures may change in any release. When one
changes, the CHANGELOG says so (as it did for `public_safety.report_lines` in
0.7.14), but script against the CLI, not the module.

## Skills

[skills/README.md](skills/README.md) indexes agent skills for LLM-assisted
Lean verification (statement fidelity, receipts, publication), with an
installer (`scripts/install_skills.py`). They defer to `lean-pkg llms` for
anything about the tool. When you edit a skill, keep its folder name equal to
its frontmatter `name` and keep the required sections; `tests/test_skills.py`
enforces both and checks every quoted `lean-pkg` command against the CLI.

## Working on the tool

```bash
python3 -m pip install -e '.[test]'      # or: uv pip install -e '.[test]'
python3 -m pytest -q                      # full suite; also works without install (pythonpath = src)
bash scripts/private-boundary-check.sh    # must exit 0
```

Layout:

- `src/lean_pkg/cli.py`: the `lean-pkg` CLI (`main(argv)`): dispatch, committed mode, the
  registry lock, `LLMS_TEXT` and the commands not yet in their own module.
- `src/lean_pkg/cli_parser.py`: `build_parser()`, the whole argparse tree `main` parses with
  (the contract tests and `scripts/command_docs.py` use it too), and `_Parser`.
- `src/lean_pkg/gate.py`: the trust gate (`gate`, `gate --check`, `validate_receipt`), the
  comparator evidence path (`validate_comparator`, `comparator attach|check`), the descendant
  rules (`package_descendant`) and `--repo DIR` (`repo_override`). Imported as `gate_mod`:
  `gate` is a common local name for a package's gate record.
- `src/lean_pkg/audit.py`: `registry audit` (`audit_registry`) and `registry validate`
  (`validate_registry`). gate.py, audit.py and cli_parser.py were cut out of cli.py (#317); they
  never import `cli` at the top level (cli_parser's `build_parser` and two audit functions import
  it where they run), and lean-pkg's modules read their names there, not through `cli`
  (`tests/test_core_split.py`). `cli.py` re-exports them for one release.
- `src/lean_pkg/core.py`: the shared core the command modules build on: `Fail` and
  `UsageError`, the registry constants, time policy, the git helpers, registry IO and the
  event log. It never imports `cli` (only `build_scope`, a leaf). The command modules import it
  directly (`from . import core`) and reach a name still defined in `cli.py` as `cli.X`, never
  through a `cli as core` alias (`tests/test_core_split.py`). `cli.py` re-exports every name that
  moved here (#317) for one release, for outside scripts; a test that patches one of them
  patches it on every module that binds it (`patch_core` in `tests/conftest.py`: core, cli,
  gate, audit, cli_parser), and a name defined in a split module is patched there.
- `src/lean_pkg/comparator_receipt.py`: the `comparator-receipt` runner (also `lean-pkg receipt comparator`)
  (`main(argv)`).
- `src/lean_pkg/scripts/comparator-check.sh`: the vendored Palomar
  acceptance script. Keep it byte-for-byte with upstream; change tool pins
  only in a dedicated commit.
- `tests/`: pytest suite. `conftest.py` scrubs repository-local Git
  variables at import time, so fixture repositories never touch an
  enclosing repository.

Conventions: keep behavior and JSON output shapes stable (they are a
contract), make one logical change per commit, use spaces and never tab
characters, and write a test for every refusal path you add. When you change
`LLMS_TEXT`, run `make docs` to regenerate `llms.txt`; `tests/test_docs.py` enforces this. AI
assistance must be disclosed in the PR description (see CONTRIBUTING.md).

Verdict first: every command that lists findings (`public-safety`, `audit`,
`index check`, `validate`, and any new scanner) prints its verdict and counts
before the list, and again last when the list can be long, because tool output
windows truncate. `tests/test_verdict_first.py` checks the first line.

Documentation at milestones: before `make release`, fill the documentation
gaps for every feature added since the previous pass: README.md, the routes in
docs/index.md and docs/how-to.md, the command reference (docs/commands/,
generated by `make docs` from the parser and docs/_commands/), and the agent contract
(`lean-pkg llms`, llms.txt). A CHANGELOG `### Added` entry with no documented
home blocks the release (RELEASING.md, checklist step 0): a new subcommand must
reach the front door (README.md, docs/index.md or docs/how-to.md) and have its
notes in docs/_commands/intro/ and an example in docs/_commands/examples.yaml;
an option-level entry (flag, field, configuration key) is at
home in the linked detail page (its command page, docs/comparator.md or
docs/formalization.md). The subcommand-coverage test in `tests/test_docs.py`,
run by `make check`, is the floor, not the ceiling.
