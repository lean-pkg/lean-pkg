---
name: lean-claim-ledger
description: "Keep a paper-to-Lean claim ledger that binds each formal target to its numbered manuscript environment and gives every claim exactly one status (verified, conditional, partial, not formalized, open feasible), then run a cold second-reader statement-fidelity crosswalk before any public claim. Trigger phrases: statement ledger, which results are formalized, verified versus partially verified, coverage table, Theorem 1.2 in Lean, corollary instead of theorem, cold read, second-model statement audit, what does a pass mean, verification paragraph in the paper."
license: Apache-2.0
---

# The claim ledger

## Problem

The most dangerous outcome of a formalization is an accurate proof of a
narrower statement whose documentation advertises the full theorem. It
happens quietly: "Theorem 1.2" drifts from the all-parameter theorem to the
numerical corollary that follows it; a definition named after a published
formula is counted as a proof of that formula; a count of declarations is
reported as a percentage of the paper.

## Why it happens

Six authorities get conflated: the manuscript's numbered claims, the formal
primitive definitions, the Lean derivation, the live build and axiom
transcript, the receipt for exact bytes, and the public prose. Each can be
correct while the mapping between them is wrong.

## Rule

- Every manuscript claim that matters has one row and one status:
  **verified** (exact statement, receipt-backed), **conditional** (deduction
  verified from named hypotheses), **partial** (a precisely named core, with
  the excluded part stated), **not formalized** (with a reason), or **open
  feasible** (work remains that is reasonably formalizable).
- Each row binds to the manuscript environment by label and quotes its
  hypotheses and conclusion; the Lean side is the printed type of a fully
  qualified declaration.
- A fixed-parameter corollary is not its all-parameter parent.
- "Not formalized" never means "false" or "suspect".
- Counts are inventories, not percentages of the paper.

## How to apply

Ledger row format (Markdown or YAML; keep it in the repository):

```text
| Paper            | Lean declaration              | Status      | Boundary / hypotheses              |
|------------------|-------------------------------|-------------|------------------------------------|
| Thm 1.2 (all p)  | MyPkg.main_bound              | conditional | DecouplingHyp (Lemma 4.1 of [X])   |
| Cor 1.3 (p=1/2)  | MyPkg.main_bound_half         | verified    | numeric specialization of Thm 1.2  |
| Prop 3.1         | -                             | not formal. | needs SPDE solution theory         |
```

Before a public claim, run a **cold statement-fidelity read**: a second
reader (a person, or a different model with read-only access) who did not
write the code compares each Lean statement with the manuscript. The brief
must:

1. state the intended scope boundary verbatim ("the analytic input X is a
   hypothesis field; object Y is never constructed"), or intended design gets
   reported as defects;
2. give exact manuscript anchors and require a field-by-field crosswalk for
   hypothesis structures;
3. use the classes of `lean-fidelity-audit-taxonomy` and severities
   CRITICAL / MAJOR / MINOR / NOTE;
4. require a "what I verified positively" section and a one-word verdict.

A statement edit after a receipt invalidates the receipt; carry NOTE-level
docstring fixes into the next content change and re-gate rather than editing
ad hoc.

For readers, publish a short "what a pass means" section beside the ledger:
which theorems, under which axioms and kernels, and what remains informal.

## Anti-patterns

- A coverage table whose rows say "verified" for corollaries of unverified
  theorems.
- Promoting a downstream row because one of its ingredients became verified.
- A verification paragraph in the paper that is stronger than the ledger.

## Verification

Every row's status agrees with the receipts and the printed types; no open
feasible item remains when the formalization is called complete; the cold
read has zero CRITICAL or MAJOR findings, and each NOTE is fixed or disclosed.

Related: `lean-paper-companion`, `lean-honest-named-hypotheses`,
`lean-publishing` (the ledger feeds Palomar's `fidelity` block).

Learned from: manuscript companions whose public tables needed verified,
conditional and excluded rows kept apart, and two cold second-model audits
whose briefs needed a predeclared scope boundary, August to September 2026.
