---
name: lean-weakened-hypothesis-vacuity
description: "Before claiming that a generalized Lean theorem covers a newly admitted case (strict to non-strict inequality, dropped positivity or nonzero hypothesis, admitted boundary value), check that the remaining hypotheses are satisfiable at that case, because they are often contradictory there and the theorem becomes vacuously true. Trigger phrases: relax 0 < a to 0 ≤ a, drop the positivity hypothesis, cover the boundary case, generalize the theorem, degenerate case, vacuously true, hypotheses unsatisfiable, division by zero threshold, non-vacuity witness."
license: Apache-2.0
---

# Weakening a hypothesis can make the new case vacuous

## Problem

A theorem excludes an awkward case with a side condition such as `0 < a j`.
To cover that case you weaken it to `0 ≤ a j`. The proof still goes through,
the build is green, the axiom audit passes, and the docstring now says "for
all `j`". It may cover nothing new: at `a j = 0` the other hypotheses can be
jointly unsatisfiable, so the theorem holds there only vacuously.

## Why it happens

A side condition often does two jobs: it excludes a bad case, and it keeps
another hypothesis satisfiable. Removing it for the first reason silently
breaks the second. A common trigger is a total operation whose junk value
replaces the paper's convention at the boundary: `x / 0 = 0` where the paper
means "no threshold" (infinity), `Real.log 0 = 0`, `Real.sqrt` of a negative.
No gate checks consistency of hypotheses at a particular parameter value.

## Rule

A widened theorem `T` with a newly admitted case `c` is credited with
covering `c` only if one of these is compiled:

- a **non-vacuity witness**: a concrete instance satisfying every hypothesis
  of `T` at `c`; or
- a **vacuity proof**: `False` derived from the hypotheses at `c`, which
  records why `T` was *not* widened that way and justifies a different fix.

"The proof still compiles" is evidence of neither.

## How to apply

1. Instantiate the hypotheses at the new case. Compute what each total
   operation actually returns there (in Lean, `n ^ c / 0 = 0`).
2. Try to prove `False`:

   ```lean
   theorem not_isFamily_at_zero_weight
       (h : IsFamily (threshold 0) W) : False := by
     -- threshold 0 = 0 truncates everything, so the tail moment is 1,
     -- contradicting the family's bound tail ≤ 1/4.
     ...
   ```

3. If it succeeds, the honest fix is a new encoding: a case-split definition,
   an extended-real value (`EReal`, `ℝ≥0∞`), or an explicit convention
   hypothesis. State which and why.
4. If it fails, build the witness:

   ```lean
   example : ∃ W, IsFamily (threshold 0) W ∧ Weight W = 0 := ⟨witnessW, by ..., by ...⟩
   ```

5. Treat each conclusion separately: one bound can be trivially true at the
   boundary while another genuinely needs a convention hypothesis.

## Anti-patterns

- Writing "non-vacuous" in a docstring instead of compiling a witness.
- Relaxing a strict inequality because a reviewer asked for "more
  generality" without checking the boundary.
- Encoding a paper's `∞` convention with a finite junk value.

## Verification

For each widened theorem, the repository contains either a compiled witness
at the new case or a compiled `False` from the hypotheses there, and the
claim ledger cites it.

Learned from: a probability formalization where relaxing a positive weight
to a nonnegative one would have produced theorems vacuous at exactly the
newly covered columns, September 2026.
