# 0001: The paper's statement in coverage.yaml

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-10-17.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

Add one optional key to `coverage/1`: `rows[].paper.statement` on a paper
row and `rows[].statement` on a library row, the result in mathematical words
(TeX). `lean-pkg page` shows it beside the Lean statement, so a reader can
compare the two by eye without opening the paper.

## Motivation

The verification page now opens with "Lean has checked every step of the
proofs listed here. It cannot check that a Lean statement says what the paper
says: that comparison is yours", and shows each result's Lean statement copied
from the source ([design](https://github.com/lean-pkg/lean-pkg/pull/205), `docs/design/page-for-mathematicians.md` in that pull request). The other
half of that comparison, the paper's statement, is not in any file lean-pkg
reads. Today the page can only say "Theorem 1 of the paper: read its
statement there". Palomar's entry pages lead with the informal account before
the formal statement; a lean-pkg page should do the same for each result.

## Design

```yaml
rows:
  - paper:
      number: "Theorem 1.2"
      title: "Main estimate"
      statement: |
        For every $n > 0$,
        \[ \sum_{k \le n} a_k \le C\, n^{1/2} . \]
    class: same-assumptions
    claims: [my-companion/main-estimate]
```

- `paper.statement` (paper rows) and `statement` (library rows): optional,
  a non-empty string of at most 2000 characters, several lines allowed. A line
  that starts with ```` ``` ```` or `~~~` is refused (the page shows the text in
  a code block). The placeholder rule (`TODO`, `FILL-ME`, ...) applies.
- The schema string stays `coverage/1`: the key is optional, and a file
  without it means exactly what it means today.
- `lean-pkg page`: **In the paper** shows the text in a `latex` code block
  (a library row: **In words**). It is shown as TeX source, never typeset, so
  no data reaches a Markdown or HTML renderer as markup. Every output still
  passes the public-safety scan.
- No `--json` key changes; `coverage check` reports a malformed statement as
  an error like any other key.

## Compatibility and migration

- An existing `coverage.yaml` is unchanged and renders as before.
- An older lean-pkg refuses a file with the key (coverage/1 is strict), as it
  refused `eprint_sha256` and `kind` when they were added. A package that adds
  the key needs this version in its CI pin. No migration command: the key is
  written by hand, from the paper.
- No golden CLI change.

## Trust model

No receipt, gate or trust level changes. The statement is the authors'
transcription: nothing checks it against the manuscript, and the page does
not say it does. The risk is a transcription that agrees with a weakened Lean
statement but not with the paper. The page already tells the reader that
fidelity is theirs to check, and the paper reference stays beside it. A later
proposal could compare the text with the labelled environment of the anchored
e-print (`formalization anchor` records its SHA-256; `paper.label` names the
environment).

## Alternatives

- Do nothing: the page points at the paper, and the reader opens it.
- Read the statement from the e-print by `paper.label`: exact, but needs the
  TeX source at render time and macro expansion; a good second step.
- Typeset with MathJax or KaTeX: needs a script, which the HTML page never
  carries; see the design note's options.

## Open questions

- The 2000-character limit.
- Whether `coverage check` should warn when a formalized row has no statement.

## AI assistance

Drafted by Claude (Anthropic) from the page redesign; the implementation and
its tests exist on a branch and wait for this proposal.
