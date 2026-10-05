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
- Run `comparator-receipt` only where its host policy allows (a Slurm job,
  a host in `COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or an explicit
  `COMPARATOR_RECEIPT_ALLOW_HOST=1`).

## Working on the tool

```bash
python3 -m pip install -e '.[test]'      # or: uv pip install -e '.[test]'
python3 -m pytest -q                      # full suite; also works without install (pythonpath = src)
bash scripts/private-boundary-check.sh    # must exit 0
```

Layout:

- `src/lean_pkg/cli.py`: the `lean-pkg` CLI (`main(argv)`).
- `src/lean_pkg/comparator_receipt.py`: the `comparator-receipt` runner
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
`LLMS_TEXT`, regenerate `llms.txt`; `tests/test_docs.py` enforces this. AI
assistance must be disclosed in the PR description (see CONTRIBUTING.md).
