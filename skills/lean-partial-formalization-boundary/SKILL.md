---
name: lean-partial-formalization-boundary
description: "A partial or contract-based Lean formalization, where analytic facts enter as structure fields or hypotheses, can faithfully transcribe the scope defect it was meant to catch, so read the coverage ledger before trusting a green build and attack the contract interface itself. Trigger phrases: certified fragment, explicit contract, hypothesis structure, the contract has the same narrowness, formalization encoded the bug, why did Lean miss this, what does our Lean layer actually cover, conclusion formalized but justification not, green build false comfort."
license: Apache-2.0
---

# A partial formalization can encode the defect it was meant to catch

## Problem

In a complete formalization, a lemma stated more narrowly than its use is
caught automatically: the types do not unify. A partial formalization inverts
that guarantee. When an analytic fact is imported as a structure field or a
theorem hypothesis, someone hand-writes its interface, and naturally copies
the paper's phrasing, including its scope error. The build is green because
the encoding is internally consistent. It is consistent and wrong.

## Why it happens

Three failure modes recur:

1. **The contract reproduces the narrowness.** The interface cannot even
   express the general form the paper later uses, so the mismatch is
   unrepresentable rather than detected. Example: a moment bound for a
   stochastic convolution exposed as a unary operator with no kernel
   argument, while the paper applies the bound to *differences* of kernels.
2. **The conclusion is formalized; the justification is not.** A false
   intermediate step under a true conclusion is invisible to any
   conclusion-directed certificate.
3. **A status word implies more than it delivers.** "Certified fragment"
   attached to a label whose fragment excludes the part that broke.

## Rule

A green build proves the encoding compiled, not that the encoding was
faithful. Report coverage as a fraction of the ledger, quote every qualifier
wherever a status is reported, and review the contract, not only the proofs
that consume it.

## How to apply

1. Read the coverage ledger before reading the certificate. Count the rows
   by status (verified, partial, contract, outside scope) and report the
   fraction.
2. Treat every qualifier ("algebra only", "final step only") as a carve-out.
   Ask whether the part under question lies inside or outside it.
3. Attack the interface: what does the paper do with this fact that the
   interface cannot express? Parameterize the interface at the generality
   actually used:

   ```lean
   -- narrow: cannot state a bound for a kernel difference
   structure MomentContract (F : Type*) where
     conv : F → F
     bound : ∀ p f, 2 ≤ p → momentNorm p (conv f) ≤ rhs p f

   -- general: the kernel is an argument, so differences are expressible
   structure MomentContract' (K F : Type*) [Sub K] where
     conv : K → F → F
     bound : ∀ p (k : K) f, 2 ≤ p → momentNorm p (conv k f) ≤ rhs p k f
   ```

4. Distinguish a regression test for an already-known defect from a
   detector. A layer built only from past findings cannot find a new class.

## Anti-patterns

- Saying "verified in Lean" when most rows are contracts.
- Reviewing only the proofs that use a hypothesis structure.
- Dropping a qualifier when copying a status into a summary.

## Verification

Take a known defect in a contracted area and ask whether the contract can
even express it. If it cannot, the contract is a transcription, not a check.

Related: `statement-vs-use-span-audit` (the paper-side detector for the same
class), `lean-honest-named-hypotheses`.

Learned from: a sorry-free, axiom-clean companion that could not have caught
any of three defects a coauthor later found, because its contracts copied the
paper's narrow statements, August 2026.
