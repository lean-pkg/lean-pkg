## Scope

One logical change. What it does and why, in two or three sentences.

## Checks run

- [ ] `make check` is green (tests, private-boundary check, tab scan, version agreement)
- [ ] `llms.txt` regenerated if the CLI contract changed
- [ ] docs updated for any user-visible change; CHANGELOG `Unreleased` entry added

## Index entry (only for a pull request that adds or changes `index/<name>/`)

- [ ] the entry was written by `lean-pkg index add` (trust is computed, receipts are verbatim copies; nothing typed by hand)
- [ ] `lean-pkg index check --clone <name>` passes; its output is pasted below
- [ ] `index/README.md` re-rendered with `lean-pkg index render`
- [ ] `consent.recorded` is true, or the entry is marked not for publication in `notes`

## Trust boundary

Does this change what a receipt proves, what `gate` or `promote` accepts, or
what the boundary check forbids? If yes, say exactly how, and add the test
that pins the new behaviour.

## AI assistance

State which AI tools were used and how (for example "implementation drafted
with Claude, reviewed and tested by me"), or "none". The author is
responsible for every line.
