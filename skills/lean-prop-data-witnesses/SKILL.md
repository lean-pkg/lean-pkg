---
name: lean-prop-data-witnesses
description: "Two Lean seams at the boundary between propositions and data: a value picked by Classical.choose keeps only the properties of the existential it was chosen from (not what the producer proved), and an Or of propositions cannot be eliminated into a Type-valued field (use a two-constructor Type witness). Trigger phrases: Classical.choose loses the property, choose_spec too weak, chosen constant, witness from the producer, equation lemma names choose, Sum of propositions, elimination from Or into Type, large elimination, direction tag, StrictMonoOn or StrictAntiOn."
license: Apache-2.0
---

# Proposition-to-data witnesses

## Problem

Two failures share one cause.

1. **Chosen witness loses facts.** `def h τ := Classical.choose (thm τ)` with
   `thm : ∃ η, P η`. The proof of `thm` built `η` from a producer that proved
   more (`W η`). A downstream proof needs `W (h τ)` and cannot get it.
   Changing the proof of `thm` does not help.
2. **Or into Type.** A record needs a data-valued tag recording which of two
   propositions holds. `Sum (StrictMonoOn f s) (StrictAntiOn f s)` is
   rejected (both are `Prop`), and eliminating the `Or` into a `Type` field
   is rejected as large elimination.

## Why it happens

Proof irrelevance. `Classical.choose` depends only on the proposition, so it
returns *some* witness of `P`, unrelated to the one the producer built. And a
proof of a disjunction can be eliminated only into another proposition.

## Rule

- To keep a property of a chosen value, choose from an existential that
  states it: `∃ η, P η ∧ W η`, with `W` copied verbatim from the producer.
- To carry a direction as data, define a `Type` whose constructors hold the
  proofs, and eliminate the `Or` only under a `Prop` goal such as `Nonempty`.

## How to apply

Chosen witness (a statement change, so get the owner's approval):

```lean
theorem thm_strong (τ) : ∃ η, P η ∧ W η := by
  obtain ⟨η, hP, hW⟩ := producer τ   -- the producer's facts, verbatim
  exact ⟨η, hP, hW⟩

noncomputable def h (τ) : ℝ := Classical.choose (thm_strong τ)
theorem h_spec (τ) : P (h τ) := (Classical.choose_spec (thm_strong τ)).1
theorem h_weak (τ) : W (h τ) := (Classical.choose_spec (thm_strong τ)).2
```

A body-only change is not enough if an equation lemma literally states
`h τ = Classical.choose (thm τ)`: restate it and move consumers to the spec
lemmas. If consumers only `obtain` from the existential, the change is purely
additive.

Direction witness:

```lean
inductive Direction (f : ℝ → ℝ) (s : Set ℝ) : Type where
  | mono (h : StrictMonoOn f s)
  | anti (h : StrictAntiOn f s)

def Direction.isMono : Direction f s → Bool
  | .mono _ => true
  | .anti _ => false

theorem Direction.nonempty (h : StrictMonoOn f s ∨ StrictAntiOn f s) :
    Nonempty (Direction f s) := by
  rcases h with h | h
  exacts [⟨.mono h⟩, ⟨.anti h⟩]

noncomputable def Direction.ofOr (h : StrictMonoOn f s ∨ StrictAntiOn f s) :
    Direction f s := Classical.choice (Direction.nonempty h)
```

## Anti-patterns

- Trying to recover `W (h τ)` by unfolding the proof of `thm`.
- Strengthening `W` beyond what the producer proved.
- Encoding a proposition decision as a Boolean `if` and losing the proof.

## Verification

- A diff of declaration types shows only the intended equation and spec
  lemma changes; both conjuncts match the producer verbatim.
- If `Classical.choice` is newly used, `#print axioms` still shows only the
  permitted set.

Learned from: a random-matrix formalization where a chosen height lost the
producer's bound and a worker then proved the needed property false at the
exposed value, while the paper was fine, October 2026; and a geometry
formalization needing a monotonicity tag, September 2026.
