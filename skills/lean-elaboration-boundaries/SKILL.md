---
name: lean-elaboration-boundaries
description: "When a Lean producer compiles but its first downstream consumer fails with a misleading type mismatch, missing instance, unsolved side condition or rewrite failure, pin the elaboration boundary explicitly (result-type instances, module-local instances, typed lets and binders, local aliases unfolded in named side conditions, change to expose the rewrite surface) instead of retrying tactics. Trigger phrases: producer compiles consumer fails, type mismatch downstream, local instance missing in importing module, theorem result needs letI, overloaded infinity, explicit Measure type, local let alias finrank, rw cannot find pattern after lambda, autoImplicit created a parameter."
license: Apache-2.0
---

# Pin elaboration boundaries

## Problem

A declaration compiles in its own module; the first theorem that imports and
uses it fails with a type mismatch, a missing instance, a leftover side goal
or a rewrite that cannot find its pattern. Tactic changes do not help because
the failure happened at an earlier elaboration boundary.

## Why it happens

- A theorem's type is elaborated before its proof. An instance introduced
  in the body is too late for the result type.
- `local instance` is not exported; each importing module must provide it.
- Expected types vanish across polymorphic `let`s, untyped binders and
  overloaded notation (`∞` can be an extended real or a smoothness order).
- A local alias (`let E := EuclideanSpace ℝ (Fin 2)`) can stay opaque in an
  implicit side condition such as `Fintype.card (Fin 2) = Module.finrank ℝ E`
  that a bare `simp` would solve for the unfolded type.
- `rw` is syntactic up to reducible unfolding; extensionally equal terms can
  expose different rewrite surfaces.
- With `autoImplicit` on, a missing import or a typo becomes a new implicit
  parameter instead of an error, silently changing the statement.

## Rule

Apply the smallest explicit pin at the boundary where inference becomes
ambiguous, and test against the first real consumer, not the producer.

## How to apply

Result-type instance, bound in the type and repeated in the body:

```lean
theorem downstream (data : D) :
    letI : Nonempty (Atlas M) := atlasOf data
    target := by
  let : Nonempty (Atlas M) := atlasOf data
  ...
```

In the type, `letI` inlines the instance into the statement. In a tactic
proof, use `let` (or `have`): in Lean 4 both register a local instance, and
the only extra effect of `letI`/`haveI`, inlining the value, cannot matter in
a proof of a proposition. Mathlib's `linter.style.haveILetI`, on by default
for every project that imports Mathlib, warns on a `letI`/`haveI` tactic whose
goal is a `Prop`. For a data-carrying instance keep `let`, not `have`: `have`
forgets the value, so the body's instance is no longer definitionally the one
inlined in the statement and a later `show` or `exact` can fail.

Module-local fact, redeclared in each consumer module:

```lean
local instance : Fact (Module.finrank ℝ (EuclideanSpace ℝ (Fin 2)) = 2) := ⟨by simp⟩
```

Typed lets, binders and notation:

```lean
let μ : MeasureTheory.Measure X := volume.restrict s
∀ i (y : ℝ), ...
[IsManifold I (⊤ : WithTop ℕ∞) M]
```

Local alias in a named side condition:

```lean
have hdim : Fintype.card (Fin 2) = Module.finrank ℝ E := by simp [E]
exact (Orientation.ne_iff_eq_neg _ _ hdim).mp hne
```

Expose the rewrite surface first:

```lean
change (f ∘ g) y = _
have hval : F (axis y) = curve (phase y) := htrace.self_of_nhds
```

In new paper-facing modules, put `set_option autoImplicit false` at the top
and inspect `#check @thm` for unexpected implicit parameters.

## Anti-patterns

- Making an emergency global instance (it can create diamonds elsewhere).
- Raising heartbeats or swapping `simp` variants before locating the
  boundary.
- Accepting a producer-only build as evidence that exported elaboration
  works.

## Verification

Build the smallest downstream module that imports the changed declaration and
exercises it, then the whole package, then rerun the axiom audit on the
affected theorems.

Learned from: a differential-geometry formalization where several
consumer-only failures (result-level instances, module-local finrank facts,
an overloaded infinity, an opaque local alias) were each fixed by one
explicit pin, September 2026.
