# 0003: Conditional claims and discharge

Status: accepted. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-10-17.
Decided: accepted 2026-10-26 by Le Chen, as written: open questions 1 to 7 and 9 take the recommended answers,
and question 8 the draft's own choice (hypotheses named by declaration). Implemented in: stages H0 #346, H1
#350, H2 #354, H3 #359, H4 #366 and H5 #PR_H5 (release to follow); tracked in
[#320](https://github.com/lean-pkg/lean-pkg/issues/320).

## Summary

A theorem proved in Lean under hypotheses that are not yet formalized is
already a legitimate result: the hypotheses are explicit arguments, so they
are part of its statement, and the comparator checks them. What lean-pkg
lacks is bookkeeping. This proposal adds it:

- a claim names its hypotheses by declaration (`hypotheses: [Hyp.A, Hyp.B]`);
- the claims ledger derives a new status, `conditional`, which is never
  `verified`;
- a hypothesis counts as discharged only through a corollary (the theorem
  applied to a proof of the hypothesis) that the comparator checked in one
  build at the bound commit.

The page, `status`, `next`, the index and `receipt check` then say
"verified under assumptions A, B; discharged: A; outstanding: B". They never
print a conditional result as an unconditional one. Discharge status is
recomputed from evidence on every call and is never stored.

It covers the changes to `claims/1` (additive keys), the ledger, `coverage/1`
(`inputs` that name hypotheses), a Lean-side check whose output goes into the
sealed verify log, and the threat model. A small core-Lean prototype of the
discharge rule, with the real output of `lake comparator`, is in
[0003/prototype](0003/prototype/) and [below](#prototype).

## Motivation

Research papers depend on results that are not formalized yet: a cited
estimate, a classical theorem missing from Mathlib, or a lemma the authors
prove on paper but have not formalized. The honest formal product is "C,
under A and B". Lean represents it exactly as
`theorem main (hA : A) (hB : B) : C`. Today lean-pkg can gate such a theorem
and compare its statement, but it records the condition only in prose:

- `coverage.yaml` row class `inputs-supplied` has `inputs: [TEXT, ...]`.
  Those are free text, linked to no declaration and no claim
  ([page.md](../page.md), `coverage.py`).
- `claims.yaml` status `experimental` is the advice for "conditional or
  provisional" results, "with the hypothesis in the summary"
  ([index-catalog.md](../index-catalog.md)). That page and the
  [FAQ](../faq.md) promise conditional certificates as "a planned schema
  extension".
- When someone later proves A, nothing records it. The author has to edit
  the free text, and nothing checks the edit. A stale `inputs` line can claim
  that A is still open after it is proved, or, worse, that A is proved when
  the proof never reached the composing build.
- `depends_on` propagates `blocked` inside one package. A dependency on
  another package's claim is `depends-on-unknown`
  ([lifecycle.md](../lifecycle.md#claims-ledger)).

Lean has three ways to say "conditional", and only one is honest:

1. **Hypotheses as arguments.** `#print axioms` stays within the standard set
   because the hypotheses are part of the statement. The comparator checks the
   full type, so a dropped or weakened hypothesis fails. This is the only form
   lean-pkg can gate.
2. **Hypotheses as `axiom`s.** `gate` refuses them unless the package widens
   `gate.axioms_allowed`, and the comparator refuses them unless they are in
   `permitted_axioms`. They are unsafe: one inconsistent axiom proves
   everything.
3. **`sorry`.** `gate` refuses `sorryAx`. A `sorry` does not record what is
   assumed, so it is not a conditional result at all.

This proposal builds on form 1 only, and keeps refusing forms 2 and 3 exactly
as today.

## Design

### Semantics

The definitions below are the contract. The schema and output sections only
spell them out.

**Assumption.** An assumption is a named declaration of type `Prop`: a
`def A : Prop := ...`, or an inductive `Prop` (a `structure` or `class`),
possibly with parameters (`def A (σ : ℝ → ℝ) : Prop := ...`). The theorem
takes it as an explicit hypothesis argument whose type is headed by that
constant (`(hA : A)` or `(hA : A σ)`). An assumption is never an `axiom` and
never an `opaque` constant. A theorem that uses `sorry` is refused, as today.
Free text about the assumption stays in `summary` and in the coverage row's
note.

Why a named declaration rather than an inline proposition: lean-pkg can then
identify the hypothesis by a constant that the kernel resolves. Matching an
inline binder type such as `∀ x, ‖f x‖ ≤ C` across two theorems would mean
comparing expressions up to unfolding, and that comparison is fragile.

**Conditional claim.** A claim with a non-empty `hypotheses` list. Each entry
is the fully qualified name of an assumption that the claim's declaration
takes as an explicit binder. That fact is checked in Lean, at the bound commit
(below). A conditional claim whose own evidence is clean has ledger status
`conditional`, together with the list of its outstanding hypotheses. It is
never `verified`.

**Discharge.** Hypothesis `A` of a conditional claim `T` is discharged at the
bound commit X only when all of the following hold:

1. **A proof exists.** A claim `P` has a declaration `hA` whose type is
   exactly the constant `A`, compared strictly (no unfolding). For an
   assumption with parameters, the corollary alone is the evidence (open
   question 3).
2. **A corollary exists.** A claim `T'` with
   `discharges: {claim: T, hypotheses: [A]}` has a declaration whose type is
   `T`'s type with the binder of type `A` removed and nothing else changed,
   compared strictly. The intended proof is `T' := T hA`. The proof itself is
   not constrained: a proof of `T'`'s type that does not use `T` at all is
   just as good, because what is verified is the corollary's statement.
3. **One build checks all three.** `T`, `P` and `T'` are listed in comparator
   configurations at X, and the comparator receipt for X passed. The
   Challenge, Solution and kernels are therefore one build, at one toolchain
   and one Mathlib pin: the ones `lean-toolchain` and `lake-manifest.json`
   record at X. The lower-tier receipt binds the same X, as both tiers do
   today.

When the rule holds, `T'` is `verified`, or `conditional` on the hypotheses
it still has. `T` stays `conditional`, because its statement still carries
`A`, and its row shows `A` discharged by `P` through `T'`. Lean decides
whether the types match, in the bound build. lean-pkg compares no names,
strings or hashes to conclude that a discharge happened. It only reads the
verdicts of a Lean-side check whose output is part of the sealed evidence.

**Across packages.** Hypothesis `Hyp.A` of `T` in package P counts as
discharged by a proof in package Q only when:

- **The same constant.** `Hyp.A` has the same fully qualified name and the
  identical type on both sides. In practice that means it is declared once,
  in a statement module that both packages import: a shared upstream such as
  Mathlib, a small "statements" package, or P's own statement module imported
  by Q. Two packages that each declare their own `A`, even with the same
  short name or the same body, have not discharged each other's hypotheses
  ([prototype refusal 3](#prototype)).
- **Q is pinned.** At X, P's `lake-manifest.json` requires Q at a revision
  that Q's own evidence binds, or at an evidence-only descendant of it.
- **The composing build re-checks.** The corollary and the proof are listed
  in the composing package's comparator configurations and checked in its
  build. Q's receipts from Q's own build do not transfer: they were made
  under Q's pins, and a constant with the same name can mean something else
  at another pin ([prototype, last step](#prototype)). Q's index entry and
  claim id are used for attribution only ("proved in Q/proof-a"), not as
  evidence.
- **No duplicate constants.** The composing build's import closure has no
  in-scope duplicate of a hypothesis constant
  (`python3 -m lean_pkg.dup_constants`,
  [duplicate-constants.md](../duplicate-constants.md)). Lean's
  `importModules` can otherwise keep one of two same-named copies without
  comparing them.

**Recomputed, never stored.** Discharge is a derived fact about the evidence
at X. No file and no registry field says that a hypothesis is discharged.
`claims.yaml` says only which corollary claims to discharge what, and the
ledger checks that claim on every call. When the evidence goes stale (drift
after X, a new pin without receipts, a failing validator), the discharge
disappears on the next call. The history survives in git and in the index
history, which append and never rewrite.

**Propagation through `depends_on`.** A claim U that `depends_on` a
conditional claim T is at most `conditional`. It inherits T's outstanding
hypotheses, marked "via T", with the new code `depends-on-conditional`. A
dependency that is not closed still makes U `blocked`, as today.
Propagation runs to a fixed point, like the current `blocked`
propagation. If U's own Lean type has no hypotheses and U is
comparator-verified, then Lean has proved U unconditionally and the declared
edge is stale. The ledger still reports U as `conditional` (fail closed) and
names the edge to remove. A cross-package `depends_on` stays
`depends-on-unknown` until cross-package resolution through the index is
designed (strategy item 3, a separate proposal). That later proposal reuses
the pinning rule above.

**Mathlib bumps and renames.**

- **A new Mathlib or toolchain pin** is a new X. Every discharge is recomputed
  from the receipts at the new X. A discharge proved under the old pin does
  not carry over. If the bump changes a definition that `Hyp.A` unfolds to,
  the Lean check still runs on the new build, but the meaning of `Hyp.A` may
  have moved. The page shows `Hyp.A`'s source at X, so the reader rereads it
  (open question 5).
- **Claim ids survive renames.** The slug names the mathematics. `hypotheses`
  and `discharges` name declarations, so a rename edits those entries, as it
  edits `decl` today; `claims rebase` would update them. A hypothesis renamed
  in Lean but not in `claims.yaml` is `hypothesis-not-in-type`, which fails
  closed. A `discharges.claim` that names a superseded claim follows
  `superseded_by`, as coverage rows do.

### The Lean-side check

The ledger never runs Lean. The facts it needs come from the build that the
receipts bind:

- the declaration's explicit binders headed by `Prop` constants;
- each constant's kind;
- whether the corollary's type is the theorem's type minus one binder;
- whether the proof's type is the constant itself.

So the gate run (`verify` and the lower-tier receipt writers) runs a small
read-only probe, `HypCheck` in the prototype, for every claim with
`hypotheses` or `discharges`. It runs on the **Challenge** module, whose types
the comparator ties to the Solution: for a claim `decl: Paper.main` it reads
the Challenge theorem the ledger already credits to that claim
(`Audit.Paper.main`, [lifecycle.md](../lifecycle.md#claims-ledger)). Its JSON lines go into the redacted
verify log, which `report_sha256` already seals. The receipt schema does not
change: the log gains a section `== lean-pkg hypotheses ==`. The ledger
reads that section from the sealed log at X. A receipt whose log has no
section, while the claims file at X declares hypotheses, gets
`hypotheses-unchecked` (open): re-gate.

The probe compares types with `Expr.equal`, which allows no unfolding. A
corollary that unfolds a definition, or reorders or renames binders beyond
removing the discharged one, does not match. The fix is to state the
corollary in the expected form. That strictness is intentional (open
question 4).

### Schema: `claims/1`, additive keys

```yaml
schema: claims/1
package: demo
claims:
  - id: demo/main
    decl: Paper.main
    kind: theorem
    summary: "C holds under assumptions A and B."
    file: Conditional.lean
    status: reusable
    hypotheses: [Hyp.A, Hyp.B]          # new, optional: assumptions taken as explicit binders
  - id: demo/proof-a
    decl: Lib.proofA
    kind: theorem
    summary: "Assumption A holds."
    file: ProofA.lean
    status: reusable
  - id: demo/main-of-b
    decl: Paper.main_of_B
    kind: theorem
    summary: "C holds under assumption B alone."
    file: Discharge.lean
    status: reusable
    hypotheses: [Hyp.B]
    discharges:                         # new, optional: this corollary removes hypotheses of a claim
      claim: demo/main
      hypotheses: [Hyp.A]
      proofs: [demo/proof-a]            # one claim per discharged hypothesis, in the same order
```

| Key | Rule |
|---|---|
| `hypotheses` | optional, theorem and lemma claims only; a list of distinct fully qualified declaration names (the `decl` grammar). Absent or `[]` means the claim is not conditional, and normalizes exactly as today |
| `discharges.claim` | a claim id in this claims file (cross-package: see open question 2) whose `hypotheses` contain every listed name |
| `discharges.hypotheses` | a non-empty subset of that claim's `hypotheses`; this claim's own `hypotheses` must equal the rest |
| `discharges.proofs` | claim ids, one per discharged hypothesis, each with a theorem or lemma `decl`; a proof claim in another package is written `Q/slug`, and its declaration must still appear in this package's comparator configurations |

The schema string stays `claims/1`, as it did when `statement_basis` and
`manuscript_findings` were added: absent keys mean exactly what they mean
today. An older lean-pkg refuses a file with the keys, because `claims/1` is
strict about unknown keys. `conditional` is **not** a value of
`claims.yaml` `status`. It is a ledger status, derived and never written.
The author keeps choosing `reusable`, `internal`, `experimental` or
`superseded`.

### The ledger (`claims-ledger/1`, additive)

New status order, first match wins:

1. `out-of-scope`
2. `blocked`
3. `unknown`
4. `failed` codes, which make the claim `open` and the package `failed` as
   today
5. `conditional`: own evidence clean, with outstanding hypotheses (own or
   inherited)
6. `verified`
7. `open`

New codes:

| Code | Class | Meaning |
|---|---|---|
| `hypotheses-unchecked` | open | the claims file at X declares hypotheses or discharges, but the sealed verify log has no `lean-pkg hypotheses` section: re-gate |
| `hypothesis-not-in-type` | failed | a listed hypothesis is not an explicit binder of the declaration's type at X (renamed, dropped, or implicit) |
| `hypothesis-inadmissible` | failed | a listed hypothesis is an `axiom`, `opaque` or `theorem` constant, not a `def` or an inductive `Prop` |
| `hypothesis-open` | conditional | a listed hypothesis has no discharge at X; detail names it |
| `discharge-type-mismatch` | failed | the corollary's type is not the claim's type minus the discharged binders, or a proof's type is not the hypothesis constant |
| `discharge-not-compared` | open | the corollary or a proof is not in a comparator configuration at X (promote stage) |
| `depends-on-conditional` | conditional | a `depends_on` claim is `conditional`; detail lists the inherited hypotheses |

A discharged hypothesis is not a diagnostic (a claim is `verified` only
with no diagnostics, as today); it appears in the row instead. Rows gain
`hypotheses` (each `{decl, state: open|discharged, by: {proof,
corollary}|null, via: claim-id|null}`) and `discharges`. `counts` gains
`conditional`. `ledger_digest` covers the new row keys only when they are
set, so a ledger without conditional claims keeps its digest, as with
`statement_basis`.

**Package status.** Today `ready` needs every required claim `verified`.
This proposal recommends that a required claim that is `conditional` also
counts as closed. The package is then `ready` with the qualifier
`conditional: N`, and `promote` and `publish --dry-run` print the
outstanding hypotheses on their checklist line. A paper whose honest product
is a conditional theorem can then be published, labelled as conditional.
This is open question 1, because it changes what `promote` accepts.

### `coverage/1`: inputs that name hypotheses

An `inputs-supplied` row may name hypotheses instead of free text. Each
entry of `inputs` is either a string (as today) or `{hypothesis: Hyp.A,
text: "the tail bound of Section 3"}`.

```yaml
  - paper: {number: "Theorem 1.2", title: "Main estimate"}
    class: inputs-supplied
    claims: [demo/main, demo/main-of-b]
    inputs:
      - {hypothesis: Hyp.A, text: "the a priori bound, Lemma 3.1"}
      - {hypothesis: Hyp.B, text: "the tail estimate of [12, Theorem 2]"}
```

`coverage check` refuses a `hypothesis` that is not in the `hypotheses` of
some claim the row names. An older lean-pkg refuses a mapping where it
expects a string, as it refuses any new coverage key (`coverage/1` is strict). A row
whose inputs are all discharged stays `inputs-supplied` in the file. The
page derives the wording (below), and the class is never rewritten by a tool.

### What each output says

The one rule: the word *verified* alone never appears on a row, headline or
claim that has an outstanding hypothesis.

- **`lean-pkg page`**: the row's status capsule reads **Conditional**, and
  the row says

  > Verified under assumptions `Hyp.A`, `Hyp.B`. Discharged: `Hyp.A` (proved
  > by `demo/proof-a`; corollary `demo/main-of-b`, compared at `3f2a9c1e0b7d`).
  > Outstanding: `Hyp.B` (the tail estimate of [12, Theorem 2]).

  Each hypothesis links to its declaration's source at X, so the reader reads
  what `Hyp.B` says. A row with nothing outstanding reads "Verified (all
  assumptions discharged: ...)". The page's fixed paragraph gains one
  sentence: "A conditional result says nothing when an outstanding
  assumption is false."
- **`status`, `next`**: `claims: 3 (1 verified, 2 conditional; outstanding
  hypotheses: Hyp.B)`. `next` names the first outstanding hypothesis as a
  possible next formalization step, never as an error.
- **`claims ledger`**: per claim, the status and the hypothesis table above;
  `--json` as specified.
- **Index**: derived `claims` carry `status: conditional` and
  `open_hypotheses`. `index-entry/2` gains these as optional derived keys,
  recomputed by `index check` like every derived key. Trust tiers (`none`,
  `lower-tier`, `comparator`, `palomar`) are unchanged, since they grade the
  receipts and not the hypotheses. A headline that is conditional is rendered
  "comparator, conditional on 1 assumption".
- **`receipt check`**: a new check, `hypotheses`, reads the sealed log
  section at X and recomputes the discharge states. It prints, for example,
  `PASS hypotheses: 2 conditional claim(s); discharged Hyp.A (demo/main-of-b);
  outstanding Hyp.B`, and FAILs on any failed-class code. `--json` adds
  `hypotheses`.

No existing `--json` key is removed or changes meaning. `counts.verified`
keeps counting only `verified` claims.

## Compatibility and migration

- **Existing packages** have no `hypotheses` key. Their ledger, digest, page,
  index entry and `receipt check` output are byte for byte as before. An
  `experimental` claim with its condition in `summary` keeps working. The
  page then has nothing to link, as today.
- **Migration** is by hand, and only for authors who want it:
  1. move each assumption into a named `def ... : Prop` (often it already is);
  2. list the assumptions under `hypotheses`;
  3. turn free-text `inputs` into `{hypothesis, text}`;
  4. re-gate, so the sealed log gains the hypotheses section.

  A helper, `claims mark NAME --hypotheses-from-challenge`, could propose the
  list from the probe's output. It would never write it without
  confirmation, because which binders are assumptions rather than ordinary
  hypotheses (`0 < n`, `Nat.Prime p`) is the author's call. No receipt and no
  index entry needs rewriting.
- **An older lean-pkg** refuses `claims.yaml` and `coverage.yaml` files that
  use the new keys, because both schemas are strict. A package that uses
  them needs this release in its CI pin. That was also true for every earlier
  additive key.
- **Golden CLI contract** (`tests/golden`):
  - `claims ledger --json` (the row keys, `counts.conditional`);
  - `receipt check` text and `--json` (the `hypotheses` check);
  - `status --json` (the counts);
  - `index` derived keys;
  - the help of any new flag.
- **Docs**: [lifecycle.md](../lifecycle.md) (statuses and codes),
  [prior-art.md](../prior-art.md) (the claims key table),
  [page.md](../page.md) (coverage `inputs`), and
  [trust-model.md](../trust-model.md) (a row for the hypotheses section).
  The "planned" sentences in [index-catalog.md](../index-catalog.md),
  [faq.md](../faq.md) and [motivation.md](../motivation.md) are replaced.

## Trust model

Yes, this changes two things: what the ledger accepts as closed, if open
question 1 is accepted, and what the page, the index and `receipt check`
say about a claim. What a receipt proves does not change.

**What a dishonest or mistaken author could get accepted that they could not
before, and the refusal that pins it.**

- **A discharge by `sorry`.** The proof of A is `sorry`. The corollary builds,
  with a warning. The comparator refuses `sorryAx`
  ([prototype](#prototype), refusal 1), and the lower tier refuses
  `sorry_count` > 0. Test: a corollary whose proof claim is `sorry` leaves
  `Hyp.A` `hypothesis-open` and the package `failed`.
- **A discharge by an extra axiom.** `axiom proofA : Hyp.A`. The comparator
  refuses an axiom outside `permitted_axioms` (refusal 2), and the gate
  refuses it outside `gate.axioms_allowed`. A package that widens
  `axioms_allowed` to admit it still fails the comparator, whose permitted set
  is checked against the same allowed set. Test: as above, with an axiom.
- **A different constant with the same name.** A proof of `Other.A`, a
  trivially true declaration also called `A`, offered for `Hyp.A`. The
  corollary does not elaborate (refusal 3), and the ledger never matches
  hypotheses by short name: `hypotheses` entries are fully qualified, and
  the probe compares constants. Test: a claims file whose `discharges.proofs`
  names a claim whose declaration has type `Other.A` gets
  `discharge-type-mismatch`.
- **A "discharge" that keeps the hypothesis.** The corollary is stated with
  `Hyp.A` still assumed. The comparator sees a statement mismatch against the
  Challenge (refusal 4). If the Challenge itself states it that way, the
  probe reports `type_matches: false` (`discharge-type-mismatch`).
- **The same name at another pin.** Package Q proved `Hyp.A` against a
  version of the statement module where `Hyp.A := True`. The same source does
  not build at P's pin (the prototype's last step). The rule never imports
  Q's verdict from Q's build: only the composing build counts.
- **Definitional unfolding.** A proof of `Copy.A`, where `Copy.A` has the same
  body as `Hyp.A` under another name, makes `Paper.main Copy.proofA`
  typecheck, and the comparator accepts it ([prototype](#prototype),
  "defeq copy"). That is sound: the kernel checked a proof of `Hyp.A`. But
  `Copy.proofA`'s declared type is `Copy.A`, not `Hyp.A`, so as a
  `discharges.proofs` entry it fails the strict proof-type check. The author
  must state the proof at `Hyp.A`. Lean decides what is proved; the strict
  check decides what lean-pkg calls it.
- **A hypothesis smuggled in as an axiom constant.** `axiom Q : Prop` used as
  a hypothesis type. The probe reports `kind: axiom, admissible: false`
  (`hypothesis-inadmissible`, failed). `#print axioms` would also list `Q`,
  so the gate refuses it.

**Vacuous, trivially true and trivially false hypotheses.** No tool can rule
these out.

- **Trivially true.** A hypothesis like `def A : Prop := True` is harmless for
  soundness, but it overstates what the result depends on. It is discharged
  at once, and the page shows `A`'s source.
- **Trivially false or jointly unsatisfiable.** `A := False`, or `A` and `B`
  contradicting each other, makes `T` vacuous: it proves any `C`. This is the
  vacuous-API point that [comparator.md](../comparator.md#do-not-weaken-the-statement)
  (rule 5) already puts on the human reviewer. For assumptions without
  parameters, a proof that `A` is consistent is a proof of `A` itself, so it
  cannot be required in advance. For assumptions with parameters, a
  non-vacuity witness is checkable: a theorem `∃ σ, A σ ∧ B σ` stating that
  the hypotheses hold together for some instance. Open question 6 proposes an
  optional `witness` claim, which the page shows ("non-vacuity witness:
  ...", or "no witness: the assumptions may be unsatisfiable").
- **On every page.** The fixed paragraph says that a conditional result says
  nothing when an assumption is false.

**Unchanged.**

- what a receipt proves about X;
- the axiom and `sorry` refusals;
- the comparator's statement check;
- the honesty assumption that the Challenge says what the paper says;
- the fact that the classification of which binders are "assumptions" is
  the author's.

An author can still leave an assumption unlisted. It is not hidden, since the
Lean statement on the page shows it and the comparator checks it, but the
ledger then reports a plain `verified` claim whose statement has an extra
hypothesis. That is today's situation (open question 7).

**Unknown.** Whether readers of the index read "comparator, conditional on 1
assumption" as weaker than "comparator". Whether Palomar has, or will have,
its own notion of a conditional result: this draft did not examine Palomar's
format for one, and its maintainers have not been asked.

## Relation to prior art

- **Lean blueprints** (`leanblueprint`, used by PFR, FLT and others): a LaTeX
  dependency graph whose nodes carry hand-written states (`\leanok` for a
  formalized statement or proof, `\mathlibok`, `\uses{...}` for edges), and
  `checkdecls` checks that the named declarations exist. The graph colours
  propagate along `\uses`. This is the obvious prior art for "status
  propagates through a dependency graph of assumptions", and this proposal
  does not claim the idea. What a blueprint does not do:
  - bind a node's state to a commit and a receipt;
  - recompute the state from evidence;
  - decide that an assumption was discharged by a kernel-checked composition.

  A blueprint cross-check (`\lean{}` names against `claims.yaml`) has since
  shipped as `lean-pkg blueprint check`, which runs checkdecls
  ([page.md](../page.md#blueprint-cross-check)); it would carry over to
  `hypotheses`.
- **Palomar** (the registry of record for verified results): it takes
  comparator configurations and `formalization.yaml`, and lean-pkg prepares
  packages for it (`palomar check`, `palomar prepare`). lean-pkg's index
  stays a re-derivable catalogue that feeds Palomar, not a competing registry
  of claims. Its submission layout (Challenge, Solution, configuration) is
  the one this proposal uses, so there a conditional result is a theorem
  whose statement has hypotheses. Whether Palomar wants a marker for that is
  unknown. This proposal adds no Palomar field.
- **Comparator** (`lake comparator`, with NanoDa and con-ron): it checks that
  each Solution theorem has its Challenge namesake's exact type with
  permitted axioms. It is the engine of the discharge rule. Nothing here
  modifies or reimplements it; the probe only reads types in the build it
  judged.
- **`lake check --paranoid`** (on the Lean FRO's year-4 roadmap): independent
  kernel replay as a Lake feature. The v4.35.0-rc2 toolchain already ships
  `leanchecker` and `leanchecker-paranoid`. Once such a check exists, it can
  produce receipts (a separate item). Conditional status sits above it and
  does not compete.
- **What lean-pkg adds:**
  - conditional status that is commit-bound and recomputed from evidence;
  - discharge decided by the kernel through a composed corollary, in one
    build;
  - the cross-package identity rule (the same constant, a manifest pin, a
    re-check in the composing build);
  - page and index wording that cannot print a conditional result as an
    unconditional one.

  As research this is a modest tool contribution, worth writing up once it
  has one outside user.

## Alternatives

- **Do nothing.** Conditions stay free text, and discharge is a manual
  `inputs` edit that nothing checks.
- **A `conditional` value of `claims.yaml` `status`.** That would be simple,
  but it is a stored flag: an author could forget to change it after a
  discharge, or change it without one. Rejected: status is derived.
- **Assumptions as claims of a new `kind: assumption`.** This gives each
  assumption a stable id that survives a rename. It was considered, since a
  hypothesis named only by its declaration changes on rename. It is kept as
  open question 8, because it adds a claim kind that is not a result.
- **Match hypotheses by comparing binder types** (no named `Prop`). This
  breaks under unfolding, binder renaming and elaboration differences, and
  gives no stable handle for the page. Rejected.
- **Trust Q's receipts for a cross-package discharge.** That would be
  cheaper, but unsound across pins (refusal at another pin). Rejected: the
  composing build re-checks.
- **Allow `axiom` hypotheses with a "conditional" label.** One inconsistent
  axiom proves everything, and `#print axioms` stops meaning "standard
  axioms only". Rejected; the refusal stays.

## Prototype

[0003/prototype](0003/prototype/) is a core-Lean project (no Mathlib, about
300 lines of Lean, most of it the probe and the refusal cases). It is not
built by lean-pkg's CI. `bash run.sh` copies it into a new temporary
directory, runs everything there, and deletes the copy.

| File | Role |
|---|---|
| `Statements.lean` | the assumptions `Hyp.A`, `Hyp.B` and the conclusion `Hyp.C`, each a `def ... : Prop` |
| `Conditional.lean` | `Paper.main : Hyp.A → Hyp.B → Hyp.C` |
| `ProofA.lean` | `Lib.proofA : Hyp.A`, in another module |
| `Discharge.lean` | the corollary `Paper.main_of_B : Hyp.B → Hyp.C := main Lib.proofA hB` |
| `Challenge.lean`, `Solution.lean`, `configs/comparator.json` | the Challenge states the conditional theorem, the proof of A and the discharged corollary (`Audit.Paper.main`, `Audit.Lib.proofA`, `Audit.Paper.main_of_B`, the `Audit.<decl>` form the ledger credits); it imports only `Statements` |
| `Bad/Solution*.lean`, `configs/<case>.json` | one Solution per refusal: sorry, axiom, other constant, not discharged, and the defeq copy (accepted) |
| `Bad/AxiomHypothesis.lean` | a theorem whose hypothesis type is an `axiom` constant (refused by the probe) |
| `HypCheck.lean` | the Lean-side probe: hypotheses with their kinds, the corollary-type rule, the proof-type rule |
| `other-pin/` | the discharging module at another pin of `Statements`, where `Hyp.A := True` |

The output of `bash run.sh` on 2026-10-17, unedited except that the two
per-module timing lines (`✔ [n/13] Built ...`) are left out. The toolchain is `leanprover/lean4:v4.35.0-rc2`, and
`lake comparator` ran in its `bwrap` sandbox:

```text
== toolchain: Lean (version 4.35.0-rc2, x86_64-unknown-linux-gnu, commit 11acb17ec6b07a8f9e9173e6845197929540936b, Release)
== lake build (default targets)
Build completed successfully (13 jobs).
== #print axioms
'Paper.main' does not depend on any axioms
'Paper.main_of_B' does not depend on any axioms
Paper.main : Hyp.A → Hyp.B → Hyp.C
Paper.main_of_B : Hyp.B → Hyp.C
== lake comparator --config configs/axiom.json
error: Illegal axiom detected: 'Bad.proofA_axiom'
   exit 1
== lake comparator --config configs/comparator.json
con-ron kernel accepts the solution
nanoda kernel accepts the solution
Lean default kernel accepts the solution
Your solution is okay!
   exit 0
== lake comparator --config configs/defeq-copy.json
con-ron kernel accepts the solution
nanoda kernel accepts the solution
Lean default kernel accepts the solution
Your solution is okay!
   exit 0
== lake comparator --config configs/not-discharged.json
error: Challenge and solution theorem statement do not match: 'Audit.Paper.main_of_B'
   exit 1
== lake comparator --config configs/other-constant.json
error: Bad/SolutionOtherConstant.lean:19:61: Application type mismatch: The argument
   exit 1
== lake comparator --config configs/sorry.json
error: Illegal axiom detected: 'sorryAx'
   exit 1
== HypCheck on the Challenge (the trusted statements): hypotheses, and the discharge rule
{"theorem": "Audit.Paper.main", "binder": 0, "hypothesis": "Hyp.A", "kind": "def", "admissible": true}
{"theorem": "Audit.Paper.main", "binder": 1, "hypothesis": "Hyp.B", "kind": "def", "admissible": true}
{"corollary": "Audit.Paper.main_of_B", "discharges": "Hyp.A", "of": "Audit.Paper.main", "type_matches": true}
{"proof": "Audit.Lib.proofA", "proves": "Hyp.A", "type_matches": true}
   exit 0
{"theorem": "Audit.Paper.main", "binder": 0, "hypothesis": "Hyp.A", "kind": "def", "admissible": true}
{"theorem": "Audit.Paper.main", "binder": 1, "hypothesis": "Hyp.B", "kind": "def", "admissible": true}
{"corollary": "Audit.Paper.main_of_B", "discharges": "Hyp.B", "of": "Audit.Paper.main", "type_matches": false}
   exit 1
Build completed successfully (4 jobs).
{"theorem": "Bad.useQ", "binder": 0, "hypothesis": "Bad.Q", "kind": "axiom", "admissible": false}
{"theorem": "Bad.useQ", "binder": 1, "hypothesis": "Hyp.A", "kind": "def", "admissible": true}
{"theorem": "Bad.useQ", "binder": 2, "hypothesis": "Hyp.B", "kind": "def", "admissible": true}
   exit 1
== the discharging package at another pin (Hyp.A := True there)
Build completed successfully (5 jobs).
== the same ProofA.lean in this build, at this pin
error: ProofA.lean:6:26: Type mismatch
error: build failed
```

The two type errors in full (from `lake build` of the same files):

```text
error: Bad/SolutionOtherConstant.lean:19:61: Application type mismatch: The argument
  Other.proofA
has type
  Other.A
but is expected to have type
  Hyp.A
in the application
  Paper.main Other.proofA

error: ProofA.lean:6:26: Type mismatch
  trivial
has type
  True
but is expected to have type
  Hyp.A
```

What the run shows:

- **The conditional theorem and its corollary use no axiom.** The
  hypotheses are in the types, so `#print axioms` reports none.
- **The honest discharge passes.** The comparator accepts the conditional
  theorem, the proof of A and the corollary in one build, in three kernels.
- **The comparator refuses sorry, an extra axiom, a different constant, and a
  corollary that keeps A** (refusals 1 to 4).
- **A defeq copy is accepted by the kernel.** That is sound, and it is why
  the proof-type rule is a separate, strict check.
- **The probe reads the hypotheses and checks the corollary's type.** It
  passes the honest corollary for `Hyp.A`, refuses the same corollary as a
  discharge of `Hyp.B`, and refuses an `axiom` hypothesis.
- **A proof made at another pin does not transfer.** It builds there and is
  a type error here.

The probe also handles hypotheses with parameters (`(h : PA n)` under
`∀ n`). That was checked in a scratch project and is not part of the
committed prototype. Still missing: the ledger side (reading the log
section, the new codes), the page wording, and any test in lean-pkg's suite.
Those are the implementation pull requests, after acceptance.

## Open questions

1. **Does a `conditional` required claim count toward `ready`?** If it does,
   `promote` and `publish` accept it, with the hypotheses on their checklist
   line. Recommendation: yes. Otherwise a Track P paper whose honest result
   is conditional can never be published through lean-pkg. The page and the
   index already cannot call it verified.
2. **Cross-package discharge in the first implementation.** The rule above is
   sound, but it needs `discharges.proofs` entries of the form `Q/slug` and
   the manifest-pin check. Recommendation: ship the same-package rule first,
   and add cross-package discharge together with cross-package `depends_on`
   (strategy item 3), which needs the same index lookup.
3. **Assumptions with parameters.** For `A σ`, require a proof of
   `∀ σ, A σ` with its binders in the theorem's order, or accept the
   corollary as the only evidence? Recommendation: the corollary only, with
   `proofs` used for attribution.
4. **Strict `Expr.equal` or definitional equality** for the corollary-type
   check. Strict equality is predictable and cannot be gamed by unfolding; it
   may reject corollaries that Lean elaborated with different binder names.
   Recommendation: strict, comparing binder types and the body only, not
   binder names (the prototype compares binder names too).
5. **Should the ledger record a digest of each hypothesis's definition**, so
   that a change of meaning under the same name across a Mathlib bump is
   flagged ("Hyp.A changed since the last pin")? `Expr` hashes are not stable
   across Lean versions, so this would hash the pretty-printed source.
   Recommendation: defer; the page shows the source.
6. **An optional non-vacuity `witness` claim** for assumptions with
   parameters (`∃ σ, A σ ∧ B σ`), shown on the page. Recommendation: yes, as
   an optional key in the first implementation, never required.
7. **Warn about unlisted assumption-like binders**: a `same-assumptions`
   row whose theorem takes a binder headed by a `Prop` `def` declared in the
   package. Recommendation: a `coverage check` warning, never an error.
8. **Assumption ids**: name hypotheses by declaration (this draft), or by
   claim id of a new `kind: assumption` claim, which keeps its id across
   renames.
9. **Should the probe run as part of the comparator receipt instead of the
   lower-tier log?** Its input is the Challenge, which the comparator tier
   owns. Recommendation: the lower-tier log, so the check exists at
   `--stage gate` too, but this is a judgement call.

## AI assistance

Drafted by Claude (Anthropic), the lean-pkg operations agent, from a review
of an outside strategy report (its "precise semantics" section) and from the
source of the claims ledger, the coverage checker and the comparator
documentation. Claude also wrote the prototype and ran it locally. To be
reviewed and decided by the maintainer.
