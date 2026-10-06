---
name: lean-honest-named-hypotheses
description: "When a Lean development needs an input it cannot prove now (a deep analytic estimate, a published theorem missing from Mathlib), state it as a named hypothesis in the theorem signature or a hypothesis structure with a precise statement and provenance, never as an axiom, a sorry, or a suggestively named definition, and never as the open target itself. Trigger phrases: assume this lemma for now, deferred input, conditional theorem, hypothesis structure, axiom vs hypothesis, sorry placeholder, standard fact, adapted from paper X, verified conditional on, missing from Mathlib."
license: Apache-2.0
---

# Honest deferment through named hypotheses

## Problem

Load-bearing inputs fall into three groups: proved here, available in a
library with matching hypotheses, or needed but not provable now. The third
group is where formalizations go wrong. The tempting moves are an `axiom`,
a `sorry`, a definition named after the paper's formula, or a hypothesis that
is (a restatement of) the open target. All of them let the build go green
while claiming more than was checked.

## Why it happens

An axiom enters every downstream `#print axioms` but is easy to overlook in a
summary; a `sorry` becomes `sorryAx`; a definition copied from a displayed
formula checks only later algebra. A hypothesis in the signature is the only
form in which the condition stays visible at every use.

## Rule

- Put an unproved input in the theorem signature or in a named hypothesis
  structure, with a docstring giving its precise source (paper, theorem
  number) and why it is deferred.
- Report "the deduction is verified conditional on `H`", never "the theorem
  is verified", until `H` has a kernel proof.
- A named hypothesis must not be stronger than the result it stands for;
  check it clause by clause against the source.
- Never assume the open target (or an equivalent) and claim progress on it.
- Check the hypothesis is satisfiable: give an instance, or at least verify
  it at two parameter points that are not the classical special case it was
  copied from.

## How to apply

```lean
/-- Decoupling estimate for kernel `G`. Source: [paper, Lemma 4.1], proved
there for the heat kernel only; deferred because our kernel decays
polynomially. -/
structure DecouplingHyp (G : ℝ → ℝ → ℝ) : Prop where
  bound : ∃ C > 0, ∀ n (u : Fin n → ℝ), (∀ i, 0 < u i) →
    decouplingIntegral G u ≤ C ^ n * ∏ i, Θ (u i)

/-- Conditional on `DecouplingHyp G`. -/
theorem solvable_of_decoupling (hG : DecouplingHyp G) (hH : 1/4 < H) :
    Solvable G H := by
  ...
```

Decide which form to use:

| Situation | Form |
|---|---|
| a library statement applies with our exact hypotheses | import and cite it |
| provable within budget | prove it |
| provable in principle, out of scope now | named hypothesis with provenance |
| might be false | an explicit open question, not a hypothesis that the main theorem relies on silently |

## Anti-patterns

- `axiom decoupling : ...` in a package meant for a trust gate.
- "Follows from standard arguments" with no citable statement.
- A hypothesis whose formula was copied from a special case and is wrong in
  general.
- Burying the hypothesis inside a proof instead of the signature.

## Verification

- `#print axioms` on every advertised theorem shows only the permitted set
  (no project axioms, no `sorryAx`).
- Every named hypothesis appears in the claim ledger with status
  "conditional" and a source pin.
- Each hypothesis has a satisfiability witness or a recorded check at
  non-special parameters.

Related: `lean-claim-ledger`, `lean-fidelity-audit-taxonomy`.

Learned from: a proof campaign where a deferred decoupling inequality kept
the conditional structure honest, and a later hypothesis copied from a
special case was misstated in general and passed several reviews, May 2026.
