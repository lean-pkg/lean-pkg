# 0011: Non-vacuity witnesses on coverage rows

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-07.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

[Proposal 0003](0003-conditional-claims-and-discharge.md) added an optional `witness` to claims: another claim
whose Lean type is `∃` over the theorem's parameters of the conjunction of all its hypotheses. The Lean-side
probe checks that type mechanically (the `hyp-witness` fact of `hyp-probe/1`), and the ledger records the
witness as `checked`, `unchecked` or `mismatch`. Two things keep it from reaching a reader:

1. A witness is accepted only on a conditional claim (one with `hypotheses`). The common case, a theorem
   whose hypotheses are the paper's own setting, cannot have one.
2. No page shows a witness. 0003 planned a page line for it ("non-vacuity witness: ..."), but it was not
   built.

This proposal does three things:

- accepts `witness` on any claim whose theorem has at least one hypothesis;
- adds an optional `witness` key to the rows of `coverage/1`, naming the claim-level witness that speaks for
  the paper result;
- shows, on that row of `lean-pkg page`, that the result's hypotheses can be met, and whether the probe
  checked this.

It is for package authors who want a reader to see that a verified theorem is not vacuously true, and for
the readers of their pages.

## Motivation

A Lean theorem whose hypotheses can never hold together proves anything; it is vacuously true. The
[comparator guide](../comparator.md#do-not-weaken-the-statement) (rule 5) leaves this check to the human
reviewer. For a theorem about objects defined in the package, this is the concrete risk. A definition can be
wrong in a way that makes it impossible to satisfy: a covariance that is not positive definite, a filtration
condition that no process meets, a pair of bounds with an empty range. The theorem then compiles, passes the
comparator against a Challenge that uses the same definitions, and says nothing.

Formalizations of whole papers now publish an existence result next to each headline theorem. One public
example is a repository of AI-assisted formalizations of random-geometry papers. Its headline result is the
conjunction of the two main theorems with "there exists a normalized whole-plane Gaussian free field", so the
field the theorems quantify over is shown to exist. An existing lean-pkg paper companion does the same by
hand: it states and proves that "the paper's setting is satisfiable" (a probability space carrying a
space-time white noise and its filtration) as an ordinary claim, which the page lists as just another
result.

lean-pkg already has the checking machinery and is missing the two last steps:

- **The witness is refused where it matters most.** `claims check` refuses `witness` on a claim without
  `hypotheses`: "witness is set but the claim has no hypotheses" (`src/lean_pkg/claims.py`). A
  `same-assumptions` result, whose hypotheses are the paper's own and so are not listed as assumptions,
  cannot carry one.
- **The ledger knows, the page does not.** The ledger row carries `witness: {claim, state}` and
  `lean-pkg claims ledger` prints it. The README block, `verification.md`, `coverage.md` and the HTML page say nothing, so
  a reader cannot tell a theorem with a checked witness from one without.

## Design

### `claims/1`: a witness on any claim with a hypothesis

The rule "witness is set but the claim has no hypotheses" is relaxed. A claim may set `witness` when its
theorem has at least one hypothesis: an explicit or implicit binder of `Prop` type, the binders that
`hyp-decl` already reports. Listing them in `hypotheses` is no longer required.

- Everything else in 0003 is unchanged:
  - the witness is another claim of the same package, of kind theorem or lemma, and not the claim itself;
  - its Challenge type must be exactly the expected witness type (`hyp-witness`);
  - a theorem with no parameters cannot have a witness (`witness-type-mismatch`: prove its hypotheses
    instead).
- Setting `witness` does not make a claim conditional. Only `hypotheses` does, as today.
- A `witness` on a claim whose theorem has no hypothesis at all stays refused. The probe reports this from
  the binders, as `witness-type-mismatch` with the detail "has no hypotheses, so there is nothing to
  witness".

Example:

```yaml
claims:
  - id: demo/lfpp-converges
    decl: Demo.lfpp_converges          # ∀ γ h, 0 < γ → γ < 2 → IsGFF h → Converges γ h
    kind: theorem
    witness: demo/lfpp-converges-nonvacuous
  - id: demo/lfpp-converges-nonvacuous
    decl: Demo.lfpp_converges_nonvacuous   # ∃ γ h, 0 < γ ∧ γ < 2 ∧ IsGFF h
    kind: theorem
```

### `coverage/1`: the row names its witness

Paper rows, and the rows of a `kind: library` file, gain one optional key:

```yaml
rows:
  - paper: {number: "Theorem 1.1", title: "Convergence of LFPP"}
    class: same-assumptions
    claims: [demo/lfpp-converges, demo/lfpp-converges-sublemma]
    witness: demo/lfpp-converges-nonvacuous
```

- `witness`: a claim id. It must be the `witness` of one of the row's `claims` in `claims.yaml`. That claim
  is the row's *headline*: the claim whose hypotheses stand for the paper result's hypotheses.
- `coverage check`, in both the registered and the offline form, refuses:
  - a `witness` that is not a claim id;
  - a `witness` that is not the claim-level witness of any claim in the row's `claims`;
  - a `witness` on a row of class `outside`, which has no Lean claims.

  `page`, `verify`'s preflight and `receipt check` validate through `coverage check`, as for proposal 0010.
  The offline form has no claims file, so it checks only the claim-id shape and the `outside` rule; the
  registered form checks all three.
- The key is independent of `class` and of `conclusion` (0010). A `same-assumptions` row with a checked
  witness is the strongest row: the paper's hypotheses, and a proof that they can hold.
- The schema string stays `coverage/1`; the key is optional, as `paper.statement` was in proposal 0001.

The row key does not duplicate the claim key. The claim key carries the relation the probe checks (this
witness, of that theorem). The row key says which of the row's theorems speaks for the paper result. A row
often lists several claims, and only the headline's hypotheses are the paper's.

### `lean-pkg page`

For a row with `witness`, a **Hypotheses can be met.** line follows the row's Hypotheses line. Its text
depends on the ledger's witness state for the headline claim:

| State | Line |
|---|---|
| `checked` | **Hypotheses can be met.** Lean proves that some instance satisfies all of them together (`WITNESS_DECL`, checked by lean-pkg's probe against the Challenge). |
| `unchecked` | **Hypotheses can be met:** claimed by `WITNESS_DECL`, not yet checked (REASON). |
| `mismatch` | **Hypotheses can be met:** the named witness does not have the expected type, so nothing is shown. |

- The header's verified line gains a clause, only when some row sets `witness`: "N of the verified have a
  checked witness that their hypotheses can be met".
- `coverage.md` (`--site`) and the HTML page (`--html`) carry the same line. The *Correspondence* cell of the
  coverage table gains "Witness: checked", "Witness: not yet checked" or "Witness: wrong type".
- A row without `witness` renders byte-identically to today, in every output. No line says "no witness": an
  absent witness means the author did not supply one, not that the hypotheses are unsatisfiable, and a
  page full of "no witness" lines would teach readers to skip them.
- The fixed explanatory paragraph gains one sentence, only when some row sets `witness`: "A witness shows the
  hypotheses can be met under the package's own definitions; whether those definitions match the paper's
  objects is the reader's comparison."

### `--json`

Additive only:

- `coverage check --json` (both forms) gains `witnesses`: `{"rows": N, "checked": N, "unchecked": N,
  "mismatch": N}`. The registered form counts by the ledger state; the offline form gives only `rows` and
  `null` for the others. It is `null` for an invalid file.
- `page --json` gains `verified_with_witness`: N, present only when some row sets `witness`.

No exit code changes. No command or flag is added. The ledger's codes are unchanged; `witness-type-mismatch`
gains one detail text.

## Compatibility and migration

- An existing `claims.yaml` and `coverage.yaml` are unchanged and render as before, byte for byte;
  `page --check` stays current.
- An older lean-pkg refuses:
  - a `claims.yaml` that sets `witness` on a claim without `hypotheses`, with today's error;
  - a `coverage.yaml` that uses the row key, because `coverage/1` is strict.

  A package that uses either needs the release that ships them, or later, in its CI pin.
- No migration command. An author who already proves a satisfiability theorem by hand restates it in the
  expected witness type if needed, names it as the headline claim's `witness`, names it on the row, and
  re-renders (`page --write`). The probe needs a Challenge for the witness, as in 0003: the witness claim is
  compared like any other.
- Golden CLI contract (`tests/golden`):
  - `json-keys.json` gains `witnesses` under `coverage-check` and `coverage-check-offline`;
  - `page`'s `verified_with_witness` is absent from the golden fixture, so its entry is unchanged;
  - `outputs-no-keys.json` is unchanged, which pins the byte-identical rendering;
  - no help text changes.
- The agent contract (`lean-pkg llms`, `llms.txt`), `docs/page.md`, `docs/formats.md` (the relaxed claim
  rule) and the command reference name the key and the new `--json` keys.

## Trust model

What a receipt proves does not change, nor what `gate`, `promote` or `publish` accept. The ledger still
decides *verified* from the receipts. A witness never makes a claim verified or conditional, and a
`mismatch` is already a `failed` diagnostic today.

What changes is what the page claims, and this time the claim is machine-checked, unlike 0010's
`conclusion`:

- **A checked witness** means the probe, in the sealed verify log at the bound commit, found a Challenge
  theorem of exactly the expected witness type, compared by the comparator. A dishonest author cannot get
  "checked" for a witness of the wrong statement: the type comparison (`Expr.eqv`) fails, and the page says
  "wrong type". The tests pin this. A witness of a weaker statement, for example one that drops a hypothesis
  or fixes a parameter inside a hypothesis instead of quantifying it, is not the expected type and is
  refused.
- **What a checked witness does not show:** that the package's definitions are the paper's. A wrong
  definition that happens to be satisfiable gets a checked witness. The page says so in the one added
  sentence. The comparator's Challenge, read by a human, is still the only place where definitions are
  compared with the paper.
- **The row key is the author's choice of headline.** A careless author could name the witness of a minor
  claim on a row whose main theorem has none. The rule that the row's witness must be the claim-level
  witness of one of the row's claims keeps it tied to that row. Which claim is the headline is not checked;
  it is visible, because the line names the witness declaration.
- **Effect on `receipt check` and the index:** unknown until implemented; expected to be none, since neither
  reads coverage rows beyond validating them.

## Alternatives

- **A row key with no claim-level witness** (any claim id, shown as "the setting is satisfiable"). Rejected
  as the main design: nothing would check that the named theorem witnesses this row's hypotheses, so the
  page would print an author's assertion in the voice of a check. It is kept as an open question for
  setting-level existence results that are not the exact witness type.
- **Derive the row's witness from its claims, with no row key.** Rejected: a row lists several claims, and
  the page cannot tell which one's hypotheses are the paper's. A witness of a sublemma's hypotheses says
  nothing about the result.
- **Require a witness on every `same-assumptions` row.** Rejected for now. For a theorem without parameters
  a witness is impossible, as 0003 notes; many rows are algebra with trivially satisfiable hypotheses; and
  existing packages would fail. A `coverage check` hint is open question 2.
- **Show "no witness" on rows without one.** Rejected: see the page section.
- **Doing nothing.** The checking machinery stays invisible, and authors keep proving "the setting is
  satisfiable" as ordinary results that the page lists without saying what they are for.

## Open questions

1. **Setting-level witnesses.** The public example's witness is "a normalized whole-plane GFF exists": the
   existence of the objects, not the exact `∃ γ h, 0 < γ ∧ γ < 2 ∧ IsGFF h`. The exact type implies it,
   so the strict design accepts more information. But should a looser form, the `∃` of a subset of the
   hypotheses, be accepted and shown with a weaker line ("the objects exist")?
2. **A `coverage check` hint** (a warning, never an error) for a `same-assumptions` row whose headline
   theorem has a hypothesis headed by a package-defined `def` and no witness: the case where a vacuous
   definition is most likely.
3. **Instance binders.** Should `[IsProbabilityMeasure P]` and other instance-implicit `Prop` binders count
   as hypotheses of the expected witness type? Today `hyp-decl` reports every `Prop` binder with a constant
   head, explicit or not, so they are included. That is right for non-vacuity, but it makes witnesses
   longer to state.
4. **Library rows.** This draft allows the key on library rows, because non-vacuity is a property of the
   Lean statement and libraries have hypotheses too. Proposal 0010 refused `conclusion` there, because a
   library row has no paper to be weaker than. Is the asymmetry right?
5. **Several headline claims on one row.** Should the key take a list, one witness per headline, for a row
   whose paper result is a conjunction proved as several theorems?

## AI assistance

Drafted by Claude (Anthropic) at Le Chen's request. The design builds on the existing claim-level witness
of proposal 0003 (its rules in `src/lean_pkg/claims.py`, the `hyp-witness` probe fact in `docs/formats.md`
and the ledger's witness states), and follows the layout of proposal 0010. The motivation comes from a
reading of a public AI-assisted formalization repository whose headline results include existence
witnesses.
