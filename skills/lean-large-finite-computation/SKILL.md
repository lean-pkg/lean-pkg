---
name: lean-large-finite-computation
description: "When a closed Lean theorem over a large concrete Finset, enumeration, table or certificate times out, exhausts memory or freezes the machine, prove the symbolic family statement first and specialize, bound the size of each declaration rather than the whole module, and run kernel decide over thousands of objects only under an explicit memory cap. Trigger phrases: decide over a large enumeration, concrete Finset heartbeat timeout, mem_iff on a closed Finset, closed normalization blowup, build froze the machine, exit 137, generated certificate module, table definition too large, recursion depth in decide, maxHeartbeats keeps going up."
license: Apache-2.0
---

# Large finite computations in Lean

## Problem

A statement about one completely concrete object (a `Finset` built from
unions, filters and maps; a generated table of hundreds of rows; a certificate
checked by `decide`) looks easier than its general form, yet becomes the most
expensive declaration in the package: tens of minutes, tens of gigabytes, or
a frozen workstation. Raising `maxHeartbeats` or memory attacks the symptom.

## Why it happens

Closed data invites Lean to normalize the entire construction: nested set
operations, decidable equality, fixed numerals, large rational literals. The
intended argument usually does not depend on the concrete values; it is a
structural fact about how a constructor contributes members. Kernel `decide`
evaluates in-process without a memory cap, so a few thousand objects can take
the whole machine with it. Implicit argument inference in `Eq.trans` or
`congrArg` can also unfold a large lookup function you never meant to unfold.

## Rule

- Prove the relational statement over symbolic inputs, then specialize.
  Keep the concrete definition, its element order and counts unchanged.
- Bound the size of each declaration. Split a huge table definition into
  typed leaves; prove counts per chunk and sum them.
- Run heavy `decide` or certificate modules under an explicit memory and
  CPU limit, never as an unconstrained build on a shared desktop.
- A timeout is not evidence that the claim is false.

## How to apply

Symbolic family first:

```lean
private theorem mem_family_iff (xs ys : Finset Desc) (d : Desc) :
    d ∈ family xs ys ↔ Structural xs ys d := by
  simp only [family, Finset.mem_union, Finset.mem_image, Finset.mem_filter]
  ...

theorem mem_descriptors_iff (d : Desc) : d ∈ descriptors ↔ Concrete d := by
  unfold descriptors
  exact mem_family_iff _ _ d
```

Explicit helper instead of implicit transport, so the lookup is not unfolded:

```lean
theorem sum_split (f : ℕ → ℚ) (a b c : ℕ) (x y : ℚ)
    (h₁ : ∑ i ∈ Finset.Ico a b, f i = x) (h₂ : ∑ i ∈ Finset.Ico b c, f i = y)
    (hab : a ≤ b) (hbc : b ≤ c) :
    ∑ i ∈ Finset.Ico a c, f i = x + y := by
  rw [← Finset.sum_Ico_consecutive f hab hbc, h₁, h₂]
```

Supply the actual proved child sums; never pass the desired total as a
hypothesis.

Capped run on Linux with systemd (adjust limits to the machine):

```bash
systemd-run --user --scope -p MemoryMax=24G -p CPUQuota=600% \
  lake build MyPkg.Certificate 2>&1 | tee build-certificate.log
```

If one run froze or OOM-killed the machine, do not retry the same command on
the same machine: locate the heaviest declaration first.

## Anti-patterns

- Doubling `maxHeartbeats` or recursion depth as the main strategy.
- Changing the enumeration, its order or its count to make a proof cheaper.
- Replacing a structural theorem by a decision procedure because all inputs
  happen to be closed.
- Reporting a performance refactor as mathematical progress.

## Verification

- `git diff` shows no change to the concrete definition, order, count or the
  public theorem statement.
- The symbolic helper has no fixture-specific premise.
- The whole package builds and the axiom audit is unchanged.

For general elaboration-performance work (profiling, import narrowing,
`simp only`, instance caches) see the `lean-elaboration` skill in the set
linked from [the index](../README.md).

Learned from: a graph-coloring census whose closed membership theorem ran 41
minutes at about 54 GiB before a symbolic rewrite, a certificate module whose
kernel decide froze a workstation twice, and an eight-theorem block that went
from minutes to about a second with an explicit helper, August to October 2026.
