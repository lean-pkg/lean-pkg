---
name: lean-paper-companion
description: "Run a Lean companion alongside a research paper's proof development: pin the latest stable compatible Lean and Mathlib, inventory claims by dependency level (unconditional, conditional, tracked gap), formalize the highest-value stable seams first (constants, signs, changes of variables, finite inequalities), classify every Lean failure before retrying tactics, and stop at the first honest interface. Trigger phrases: Lean companion for a paper, verify along the way, what should I formalize first, set up lean directory, latest Lean and Mathlib, pin toolchain, is this worth formalizing, Lean failure means the lemma is false, formalization value."
license: Apache-2.0
---

# Lean companion for a paper in progress

## Problem

A paper mixes elementary algebra Lean can check today, deep published inputs,
and foundations Mathlib does not have. Treating this as all-or-nothing either
wastes a valuable audit or produces an axiomatized shell that claims to
verify the paper. Waiting until the paper is "done" loses most of Lean's
diagnostic value; formalizing every scratch idea turns the companion into a
tax.

## Why it happens

What is missing is usually not tactics but an explicit claim boundary, a
dependency map, a trust gate, and a cadence.

## Rule

- Pin the latest stable Lean and the Mathlib commit whose `lean-toolchain`
  matches it, plus every transitive dependency, unless there is a stated
  reason for another version. Exact pins, not an old default.
- Classify each claim: **unconditional** (Lean and Mathlib suffice),
  **conditional** (deduction from named hypotheses), or **tracked gap**.
- Work in value order: (1) exact constants, signs, normalizations, changes of
  variables, finite inequalities, optimizers, regime conversions; (2)
  load-bearing conditional deductions; (3) faithful interfaces that pin a
  real seam; (4) restatements and assumed conclusions are worth little; (5)
  missing foundations are out of scope unless adopted as a goal.
- Stop at the first honest named interface when further work would mostly
  build foundations rather than test the paper's own deductions.

## How to apply

```bash
# choose the pair: Mathlib's own lean-toolchain decides the compiler
curl -s https://raw.githubusercontent.com/leanprover-community/mathlib4/<tag-or-rev>/lean-toolchain
mkdir lean && cd lean && lake new MyPaper math
# pin: lakefile.toml requires mathlib at an exact rev; commit lake-manifest.json
lake update && lake exe cache get && lake build
```

For each stable lemma in the active proof:

1. Write the micro-contract: objects, hypotheses, quantifier order,
   conclusion.
2. Formalize the smallest high-value seam before downstream prose depends on
   it. Keep deep inputs in the signature (`lean-honest-named-hypotheses`).
3. When Lean refuses, classify before retrying: the statement may be false, a
   hypothesis may be missing, or the API is awkward. The first two are
   mathematical findings; record a counterexample as a result.
4. After it compiles, print the type and compare it with the paper's wording
   (`#check @MyPaper.lemma_3_2`).
5. Update the ledger (`lean-claim-ledger`).

Upgrade toolchains in an isolated change with a full re-verification; keep
old receipts under their old pins.

When checking whether an API exists, read Mathlib at the pinned revision
(`git -C .lake/packages/mathlib rev-parse HEAD` must equal the manifest), not
a newer checkout.

On shared machines, warm the cache once (`lake exe cache get`) before running
several builds, never let a cold `lake update` build Mathlib from source, and
check free disk before a campaign: each warm build tree is several GiB.

## Anti-patterns

- An implication whose hypothesis is the open target, counted as progress.
- A definition copied from the final displayed formula, counted as a proof.
- Inheriting an old pin because a cache happened to exist.
- Retrying tactics on a statement that is false.

## Verification

The ledger agrees with the manuscript; no open theorem or published input is
called kernel-verified; the pinned project builds from the candidate bytes;
every promoted theorem passes the sorry scan and the axiom audit; each
substantive Lean failure is classified.

For project architecture, Mathlib search and orchestration of many workers,
see the skills linked from [the index](../README.md).

Learned from: several paper companions where early seams caught sign and
quantifier errors cheaply, and where formalizing missing foundations would
have displaced the research goal, May to October 2026.
