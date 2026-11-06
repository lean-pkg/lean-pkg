# 0010: The strength of a row's conclusion in coverage.yaml

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-05.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

Add two optional keys to the paper rows of `coverage/1`: `conclusion` (`full`,
`weaker` or `special-case`) and `conclusion_scope` (one line saying what the
Lean proves relative to the paper's result). Today a row's class says how the
Lean statement's *hypotheses* relate to the paper's; nothing says how its
*conclusion* does. With the keys, `lean-pkg page` never calls a result whose
Lean conclusion is weaker than the paper's, or a special case of it, plainly
"Verified.", and says what was proved instead. For package authors and the
readers of their verification pages.

## Motivation

A field report from a public paper companion's generated page. A paper
result, a proposition on the linear exponential stability or instability of
a PDE, renders as:

- **Verified.** Lean accepts the proof, and lean-pkg's record shows every
  check that applies to this result passed;
- **Hypotheses.** The paper's own: the Lean statement assumes nothing the
  paper does not;
- **Note.** ... The semigroup and nonlinear PDE stability conclusions in the
  proposition are excluded; this row covers only modewise ordered-field
  algebra.

Each line is true. The hypotheses line is right: the Lean statement assumes
nothing the paper does not. But the Lean statement concludes much less than
the proposition, and the page says so only in the note, after the status
word, the paper's statement, the Lean statement and the hypotheses. A reader
who stops at "Verified" leaves believing the proposition was formalized.

The cause is in the format. A coverage class (`same-assumptions`,
`inputs-supplied`, `outside`; `src/lean_pkg/coverage.py`) describes the
hypotheses only. There is no way to say that the conclusion is weaker than
the paper's, so the author's only outlet is free text in `note`, which the
page renders last and no tool reads.

## Design

### The keys

Paper rows of `coverage/1` (rows with `paper` and `class`, not the rows of a
`kind: library` file) gain two optional keys:

```yaml
rows:
  - paper: {number: "Proposition 3.2", title: "Linear stability"}
    class: same-assumptions
    conclusion: weaker
    conclusion_scope: "the modewise algebraic inequality only; the semigroup and nonlinear stability conclusions are not formalized"
    claims: [demo/modewise-sign]
```

- `conclusion`: one of `full`, `weaker`, `special-case`.
  - `full`: the Lean proves the paper's conclusion. An absent key means
    `full`, which is what every row means today.
  - `weaker`: the Lean proves a statement strictly weaker than the paper's
    conclusion (a part of it, a bound with a worse constant, an
    inequality instead of an equality, ...).
  - `special-case`: the Lean proves the paper's result for a special case of
    its setting (one dimension, one parameter value, a finite case, ...).
- `conclusion_scope`: one plain-language line (at most 400 characters, the
  limit of `note`) saying what the Lean proves relative to the paper's
  result. The placeholder rule (`TODO`, `FILL-ME`, ...) applies.

Rules, enforced by `coverage check` (both the registered and the offline
form) and therefore by `page`, `verify`'s preflight and `receipt check`'s
coverage item, which all validate through it:

1. `conclusion_scope` is required when `conclusion` is `weaker` or
   `special-case`, and refused when `conclusion` is absent or `full`.
2. Both keys are refused on a row of class `outside`, which proves nothing.
3. `conclusion` names the conclusion, independently of `class`, which names
   the hypotheses. A row can be `same-assumptions` with `conclusion: weaker`
   (the report's case) or `inputs-supplied` with `conclusion: special-case`.
4. A library row refuses both keys, by name, like the other paper-row keys
   (`paper`, `class`, `inputs`): a library row is exactly its Lean statement.
5. A value outside the enum, or a scope that is not one line, is refused.

The schema string stays `coverage/1`: the keys are optional, as
`paper.statement` was in [proposal 0001](0001-paper-statement.md).

### `lean-pkg page`

For a row with `conclusion: weaker` or `special-case`:

- When the result is verified, its status line is never the bare
  **Verified.** It is **Verified: a weaker statement.** or **Verified: a
  special case.**, followed by the rest of today's sentence (what Lean
  accepts and which checks passed).
- Right after the status line, a **Conclusion.** line carries the scope:
  "**Conclusion.** A weaker statement than the paper's: SCOPE" or
  "**Conclusion.** A special case of the paper's result: SCOPE". The line is
  shown whatever the status (verified, conditional, not yet verified), so a
  reader of an unfinished row learns the same thing.
- The page header's verified count says how many of the verified results
  prove a weaker statement or a special case (only the non-zero parts; the
  clause is absent when both are zero), for example:

  ```text
  - Verified: 3 of the 3 result(s) in Lean; of the verified, 1 prove(s) a weaker statement than the paper's and 1 a special case of it.
  ```
- `coverage.md` (`--site`) and the HTML page (`--html`) carry the same
  status and Conclusion lines, which they render from the same body; in the
  coverage table the row's *Correspondence* cell gains a second line, "Weaker
  conclusion: SCOPE" or "Special case: SCOPE".
- A row without the key, or with `conclusion: full`, renders byte-identically
  to today, in every output. A page whose rows carry no key is unchanged.

### `--json`

Additive only:

- `coverage check --json` (both forms) gains `conclusions`:
  `{"full": N, "weaker": N, "special-case": N}` over the paper rows that have
  a Lean counterpart (class not `outside`), an absent key counted as `full`;
  `null` for a library file and when the file is invalid, like `counts`.
- `page --json` gains `verified_conclusions`: `{"weaker": N,
  "special-case": N}`, the verified rows of each kind, present only when
  some row of the file carries `conclusion`, so the payload of every other
  page is unchanged (as `conditional_rows` was added by proposal 0003).

No exit code changes. No command or flag is added.

## Compatibility and migration

- An existing `coverage.yaml` is unchanged and renders as before, byte for
  byte, in the README block, `verification.md`, `coverage.md` and the HTML
  page; `page --check` stays current.
- An older lean-pkg refuses a file that uses either key, because `coverage/1`
  is strict (unknown keys are refused), as it refused `paper.statement`,
  `eprint_sha256` and `kind` when they were added. A package that adds the
  keys needs the release that ships them, or later, in its CI pin.
- No migration command: nothing changes for files without the keys. An
  author whose `note` says a row covers only part of a result writes the two
  keys by hand, from the paper, and re-renders the page (`page --write`).
- The golden CLI contract (`tests/golden`): `tests/golden/json-keys.json`
  gains `conclusions` under `coverage-check` and `coverage-check-offline`.
  No help text changes. `page --json`'s `verified_conclusions` is absent
  from the golden fixture (whose rows carry no key), so the `page` entry is
  unchanged; `tests/golden/outputs-no-keys.json` is unchanged, which pins
  the byte-identical rendering.
- The agent contract (`lean-pkg llms`, `llms.txt`), `docs/page.md` and the
  command reference name the keys and the new `--json` keys.

## Trust model

No receipt, gate or trust level changes: the claims ledger still decides the
word *verified* from the receipts, exactly as before. The keys are the
authors' description, like `class` and `note`, and nothing checks that a
scope matches the Lean statement; the page already tells the reader that
comparing the statements is theirs to do.

What changes is what the page claims. Before, an author could leave a
weakened conclusion unmarked and the page said "Verified." with the caveat
last; that is still possible (the keys are optional), so a dishonest or
careless author is not stopped. A careful author can now make the page say
the truth first. A mistaken author who sets `conclusion: weaker` on a row
that is in fact full only makes the page more modest. The tests pin the
refusals (the enum, the scope rule, `outside`, library rows) and that a
weaker or special-case row never shows the bare **Verified.**

## Alternatives

- **A fourth class** (for example `weaker-conclusion`). Rejected: the class
  describes the hypotheses, and a weaker conclusion can come with the
  paper's own hypotheses or with supplied inputs. A fourth class would mix
  the two axes, need the product of both in the enum, and change the meaning
  of the hypotheses line the page prints from the class.
- **Free text in `note`.** Rejected: it is not machine-readable, so the page
  cannot change its status word or count the rows. Today's notes already say
  it (the field report's does) and still sit under "Verified."
- **Doing nothing.** The page keeps telling a reader who stops at the status
  line that a result was verified when only part of it was.

## Open questions

- Whether `coverage check` should print a hint (a warning, never an error)
  when a row's `note` contains words like "only", "excluded" or "partial"
  but the row sets no `conclusion`. Proposed here as a question only; the
  first implementation does not do it.
- The names of the values: `special-case` against `specialization`, and
  whether `weaker` should be `partial`.
- Whether a library row should ever carry a conclusion strength: today it is
  refused, because a library row has no paper result to be weaker than.

## AI assistance

Drafted by Claude (Anthropic) at Le Chen's request, from a field report on a
public paper companion's page; the design follows the request and the
precedent of proposal 0001.
