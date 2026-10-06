---
name: lean-fidelity-audit-taxonomy
description: "Classify every divergence between a Lean statement or definition and the paper sentence it claims to formalize into four attack classes (over-claim, under-claim, definitional divergence, anchor fraud) and check the two deep traps (literal versus operational definitions, encoding narrowing) plus junk-value totality. Trigger phrases: fidelity audit, definition layer audit, does the Lean statement match the paper, over-claim, under-claim, literal vs operational definition, spanning vs general substructure, anchor check, docstring cites the wrong line, named hypothesis stronger than the paper, totalized deriv or division, notation borrowed from another paper."
license: Apache-2.0
---

# Fidelity audit taxonomy: Lean statement versus paper

## Problem

A definition layer can compile, carry no `sorry`, pass an axiom audit, and
still prove something other than what the paper states. The divergences are
not random. In practice almost every serious finding falls into a small set
of recurring classes, and a fast reviewer tends to rate several of them as
nits when they are fatal to the claim.

## Why it happens

The kernel checks that a proof matches its statement. Nothing checks that the
statement matches the paper. A formalizer under time pressure transcribes the
paper's *operational* use of a notion rather than its *definition*, narrows a
quantifier domain to something convenient, or lets a total function's junk
value stand in for a mathematical convention. Each choice compiles.

## Rule

Every fidelity finding must be classified into exactly one base class, and
every audit must explicitly check the deep traps:

1. **Over-claim.** The Lean statement is stronger than the paper sentence it
   anchors to: it drops a hypothesis or proves more.
2. **Under-claim.** The Lean statement silently narrows scope: simple graphs
   for multigraphs, spanning substructures for all substructures, one fixed
   parameter for all admissible parameters.
3. **Definitional divergence.** Quantifier order, parity or strictness,
   deletion of edges versus vertices, a changed normalization convention.
4. **Anchor fraud.** The docstring cites a theorem number or line that does
   not contain the claimed sentence. Check that the anchor exists and says it.

Deep traps:

- **Literal versus operational definition.** The paper defines a class by an
  equality (for example `χ' = Δ + 1`) but uses it as an inequality
  (`χ' > Δ`). Formalizing the operational reading is acceptable only if the
  bridging theorem is a named, tracked hypothesis or is proved.
- **Encoding narrowing.** For every `∃` or `∀` over substructures, states or
  indices, compare the Lean domain with the paper's. A paper may collapse the
  two for the objects that matter; until that collapse is formalized, the
  predicate is weaker than the paper's.
- **Junk values.** `deriv f x` is defined even where `f` is not
  differentiable; `x / 0 = 0`; `Real.sqrt` of a negative is `0`. A formula
  about the totalized value is not a statement about the mathematical object.
  Add `HasDerivAt`, nonzero-denominator, or domain hypotheses before
  interpreting a value.
- **Constraint by definition.** Defining a constrained quantity to be zero and
  proving it by `rfl` does not show that the constraint forces it.
- **Borrowed notation.** A symbol in the target statement must resolve in the
  paper being formalized. If it was copied from a comparison paper, check
  which object and which limit it denotes there.

Named hypotheses obey a soundness rule: a named hypothesis must not be
stronger than the published result it stands for. Compare it with the cited
statement hypothesis by hypothesis. A false named hypothesis is worse than no
formalization.

## How to apply

Give the reviewer the exact Lean declaration (printed, not paraphrased) and
the exact paper environment, and require a row per finding:

```text
FINDING  CLASS            LEAN                      PAPER                 SEVERITY
F1       under-claim      ∀ H ≤ G (spanning)         all subgraphs H ⊆ G   MAJOR
F2       definitional     IsClass2 G := χ' G > Δ G   χ' = Δ + 1            MAJOR unless bridge named
F3       anchor fraud     "Lemma 3.2"                 that is Lemma 3.4     MINOR
```

Print the declaration so implicit binders and coercions are visible:

```lean
set_option pp.explicit false in
#check @MyPaper.main_theorem
#print MyPaper.IsClass2
```

## Anti-patterns

- Accepting "the proof compiles" as evidence of fidelity.
- Letting a finding go unclassified ("minor wording issue") without asking
  which class it is.
- Fixing an over-claim by adding a hypothesis that the paper does not have
  (that converts it into a definitional divergence).
- Treating a docstring anchor as checked because it has the right shape.

## Verification

- Every finding has one class; the deep traps are each marked checked or
  not applicable, in writing.
- Every named hypothesis was compared with its source, clause by clause.
- Where two reviewers disagree on severity, the more severe verdict stands
  until refuted with an argument.

Related: `lean-weakened-hypothesis-vacuity`, `lean-partial-formalization-boundary`,
`lean-claim-ledger`. For a complete binder-by-binder statement audit
procedure, see the `lean-statement-audit` skill in the set linked from
[the index](../README.md).

Learned from: a definition-layer gate on a graph-theory formalization where
every serious defect fit these classes and a fast reviewer under-rated them, August 2026.
