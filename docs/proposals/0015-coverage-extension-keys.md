# 0015: Extension keys in coverage.yaml (`x-` keys that lean-pkg ignores)

Status: accepted. Author: drafted by the AI operations agent at Le Chen's request, from a field report.
Created: 2026-11-11. Revised: 2026-11-11, after one field review.
Decided: 2026-11-11, accepted by Le Chen (maintainer of record), as written, after one field review.
Implemented in: (release).

## Summary

Let `coverage/1` accept keys whose name starts with `x-`, at the top level and on rows (paper and library).
lean-pkg checks only their shape: the name, the value's type and size, and how many there are. It never reads,
renders or judges them. Other tools that generate documents from `coverage.yaml`, such as a book appendix, a
slide deck or a site of their own, can then keep their per-row data on the row it belongs to, not in a sidecar
keyed by row label. Every other unknown key is refused as today, so a misspelled real key such as
`conclusion_scop` is still caught. This is for package authors and for tools that read coverage files.

## Motivation

A field report from a paper companion with 40 coverage rows. Its book appendix generator needed a LaTeX form of
each row's `conclusion_scope`, which is one plain-text line ([proposal 0010](0010-coverage-conclusion-strength.md)).
The author added it to the row (illustrative values):

```yaml
rows:
  - paper: {number: "Theorem 2", title: "A moment bound"}
    class: same-assumptions
    claims: [demo/moment-bound]
    conclusion: weaker
    conclusion_scope: the bound holds for t <= 1 only
    conclusion_scope_tex: 'the bound holds for $t \le 1$ only'
```

`coverage check` refused the file: `rows[3]: unknown key(s) conclusion_scope_tex (allowed: paper, class,
claims, inputs, note, conclusion, conclusion_scope, witness)`. So the TeX went into a sidecar,
`docs/coverage-scope-tex.yaml`, keyed by row label. Each entry repeats the plain-text sentence so that a script
can notice when the two files drift apart. The data is about one row, but it lives in another file, and two
files have to be kept in step by hand.

`coverage/1` refuses unknown keys on purpose. A misspelled key would otherwise be silently ignored, and the page
would say something other than what the author meant. That reason stands. What is missing is a declared place
for data that lean-pkg does not own.

## Design

### Where an extension key may appear

- **The top level** of a paper or library file, for settings that concern the whole file.
- **Each row** (`rows[]`), paper or library.

Nowhere else: not in `manuscript`, `rows[].paper`, `rows[].inputs[]`, `compute`, `nothing_excluded` or
`lean_pkg_version`. An `x-` key there is refused: `rows[3].paper: extension keys (x-...) are allowed only at the
top level and on rows`. A later proposal can widen this if a real need appears.

### Name

`^x-[a-z0-9][a-z0-9_-]*$`, at most 64 characters. Examples: `x-appendix-scope-tex`, `x-conclusion_scope_tex`.
Prefixing the tool's name (`x-<tool>-<field>`) is recommended so that two tools do not collide; it is not
enforced. lean-pkg itself never defines a key that starts with `x-`, so an extension key can never collide with
a future lean-pkg key.

### Value

A string, a number, a boolean or null, or a list or a mapping (with string keys) of these, nested at most two
levels. The value's JSON serialisation is at most 4000 bytes. A string may span several lines. There are at most
16 extension keys on one object. A value outside these bounds is refused, and the error names the key and the
bound.

### What lean-pkg does with them

- **`coverage check`** (both forms, and verify's preflight, which runs the same check) applies the shape rules
  above and nothing else. An extension value is outside every judged check:
  - the placeholder rule (`FILL-ME`, `TODO` and the rest, a warning under verify's preflight);
  - the one-line and length limits of `summary`, `note` and `conclusion_scope`;
  - the `paper.statement` rules (code fences, the README block's markers);
  - every rule that reads claims, receipts or the page.

  The text is never shown, so none of these applies.
- **The page** (`page`, `page --check`, the README block, `site render`), the claims ledger, `receipt check`,
  `status` and the catalog never read an extension key. A test pins that an extension value does not appear on
  the generated page.
- **`--json`**: where `coverage check --json` returns the parsed document (the offline form's `doc`), the keys are
  there unchanged. One additive key, `extension_keys`, lists the extension keys found, as `"<root>.x-..."` and
  `"rows[N].x-..."`, sorted, empty when there are none.
- **The text output** of `coverage check` adds one line after the verdict when the file has extension keys. The
  line names each distinct key with the number of objects that carry it, for example
  `extension keys (not checked, never shown): x-appendix-scope-tex on 7 row(s); x-appendix-scope-text on 1
  row(s)`. A misspelled extension key, which no check can catch, then shows up as a second name beside the
  intended one. A file without extension keys prints exactly what it prints today.
- **Commands that write `coverage.yaml`** keep every extension key and its value:
  - `formalization anchor --write` edits only the manuscript lines, so every other line, extension keys and
    comments included, stays byte for byte;
  - `claims rebase --out-dir` writes its proposed `coverage.yaml` from the parsed document, so each extension
    key keeps its value and its object, though its layout may change as for every other key;
  - `new` writes none.

  A test pins both writers.

### Documentation

[page.md](../page.md#coverageyaml) gains an *Extension keys* section that states the rules above, and says in one
sentence that lean-pkg checks nothing inside an extension key and never shows it, so a reader must not take its
content as checked. [formats.md](../formats.md) lists the rule beside the other coverage keys.

### The field report's case

```yaml
    conclusion_scope: the bound holds for t <= 1 only
    x-appendix-scope-tex: 'the bound holds for $t \le 1$ only'
```

The sidecar and its repeated sentence can go: the plain and the TeX forms now sit side by side on the same row,
where a diff shows them together.

## Compatibility and migration

- **Schema string.** `coverage/1` stays `coverage/1`. Like proposals 0010 and 0011, this widens what the version
  accepts without changing the meaning of anything it accepted before. A file without `x-` keys is checked
  exactly as today.
- **Older lean-pkg.** A file that uses an extension key is refused by an older lean-pkg, with `unknown key(s)
  x-...`. That is loud, not silent. A package that adopts extension keys needs the release that ships this, and
  its `lean_pkg_version` key records which release it uses. Nothing in an existing registry or receipt changes.
- **Migration command.** None is needed: no existing file changes meaning.
- **Goldens.** `coverage check --json` gains `extension_keys`. The JSON-keys golden and the coverage check help
  golden change. No text golden changes.

## Trust model

Nothing that a receipt proves, or that `gate`, `promote`, `publish` or `index check` accept, changes. lean-pkg
never reads an extension value, so a value cannot make a check pass or a page say more.

What could mislead:

- **A reader of `coverage.yaml`** may read an `x-` value as if lean-pkg had checked it. The documentation says it
  is not checked, and the page never shows it. A downstream tool that renders extension values (the book
  appendix) is responsible for what it prints. That is the situation today with the sidecar file, too.
- **Hidden text in a public file.** An extension value is committed text, like the rest of the file, so the
  public-safety and private-boundary scans already cover it. The value cap keeps one key from becoming a large
  payload.
- **Typos in real keys.** These are still refused. Only names that start with `x-` are exempt, and a misspelled
  `x-` name is a key that lean-pkg does not read anyway.

The tests that pin this:

- an extension value never appears on the page;
- a non-`x-` unknown key is still refused;
- an `x-` key in a nested object is refused;
- the bounds are enforced;
- an extension value with placeholder text or an over-long line fails no check;
- `formalization anchor --write` and `claims rebase --out-dir` keep extension keys and their values.

## Alternatives

- **A defined `conclusion_scope_tex` key** (or `tex` siblings of `note` and `conclusion_scope`). lean-pkg would
  then own its meaning: validate it, and probably typeset it on the page, which today renders Markdown and shows
  TeX only in the statement blocks. That is a larger design (how the page shows TeX in a line, which siblings
  exist), and it serves readers of the page, not the generator that asked. It can be a later proposal, and an
  `x-` key in use meanwhile shows whether the need is general.
- **Accept any unknown key.** This loses the typo protection that is the reason `coverage/1` is strict.
- **A single `extensions:` mapping** at the top level, keyed by row label. That is the sidecar moved inside the
  file: the data is still away from its row and still keyed by a label that can drift.
- **One reserved key per object** (`x: {appendix_scope_tex: ...}`) instead of a prefix. This is about equal: one
  name to reserve, and the same bounds apply to its mapping. The prefix was chosen because the field report asked
  for it, and because it is the convention people know from other formats (OpenAPI's `x-` extensions). This is
  open question 2.
- **Do nothing.** The sidecar works, but every tool that needs per-row data invents its own sidecar and its own
  drift check.

## Open questions

The field reviewer, the author of the reporting package (59 rows; its sidecar carries TeX scope sentences for 7
rows, the longest 390 bytes), answered the draft's four questions:

1. **Scope.** Top level plus rows is enough. Per-result rendering text belongs to the row, so `rows[].paper`
   needs none. Settled as designed.
2. **Prefix or mapping.** The flat `x-` prefix: it is greppable, it follows the usual vendor-key convention,
   and `x-appendix-scope-tex` reads naturally next to `conclusion_scope`. Settled as designed.
3. **Bounds.** 4000 bytes per value, 16 keys per object and two levels of nesting fit comfortably. Settled as
   designed.
4. **claims/1.** Useful later (a book label, a display name), not needed now, and the same rules would do. Left
   for a later proposal.

The review also asked for three additions, all folded into the design above:

- commands that rewrite `coverage.yaml` keep extension keys;
- `coverage check` names the extension keys it did not check, so a misspelled one is visible;
- extension values are outside every judged check.

Nothing is open.

## AI assistance

Drafted by Claude Code (the AI operations agent) at Le Chen's request, from the field report quoted above and the
current `coverage/1` validator (`coverage.py`, `_keys`). The maintainer reviews and decides.
