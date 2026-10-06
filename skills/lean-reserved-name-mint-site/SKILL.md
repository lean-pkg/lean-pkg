---
name: lean-reserved-name-mint-site
description: "Locate the declaration that realizes a reserved-name lemma (X.congr_simp, X.eq_1, X.eq_def) in a module other than X's home, when a namespace or ownership audit flags it and fixing one use site only moves the problem one module down the import graph; probe the environment with constants.map₂ before each declaration rather than guessing. Trigger phrases: congr_simp minted downstream, eq_1 realized in wrong module, constant outside its module namespace, ownership audit violation moves downstream, linarith mints congr lemma, Environment.contains false, realizeConst."
license: Apache-2.0
---

# Locate where Lean mints a reserved-name lemma

## Problem

Lean realizes reserved-name lemmas (`X.congr_simp`, `X.eq_1`, `X.eq_def`,
match splitters) on demand, in whichever module first needs them. If an audit
requires every constant to live in its home module's namespace, or `X`'s home
module is frozen, a downstream use site that needs the lemma mints it under
`X`'s namespace and the audit fails. Fixing that site moves the minting to the
next module down. Chains of a dozen rounds are possible.

## Why it happens

The site is usually not where `X` appears textually:

- `linarith` and `nlinarith` preprocess every comparison hypothesis in
  context, so any hypothesis mentioning `X` can mint `X.congr_simp`;
  `linarith only [h]` still mints if `h` mentions `X`.
- `simp`, `simp only`, `simpa`, `field_simp`, `norm_num`, `gcongr` descending
  into `X`'s arguments, including after unfolding a definition that contains
  `X`; default arguments that run `simp` (for example a `finiteness`
  auto-param).
- `rw`, `exact`, `dsimp only` and `generalize` were observed not to mint.

Observed on Lean 4 toolchains up to v4.32; recheck on newer releases.

## Rule

Locate first; never guess. Fix proof bodies only; statements stay untouched.

## How to apply

Copy the module outside the project tree (a gate that enumerates root files
may reject extras), disable async elaboration, and insert a probe before each
top-level declaration:

```lean
set_option Elab.async false

open Lean Elab Command in
run_cmd do
  let env ← getEnv
  -- constants realized in the current module are visible only via map₂
  logInfo m!"minted: {env.constants.map₂.contains ``Foo.congr_simp}"
```

The first probe reporting `true` follows the culprit. Inside a long proof, use
the same check with `run_tac` before tactic lines. Do not insert a probe
between `set_option ... in` or `open ... in` and its declaration.

Typical fixes:

```lean
-- explicit terms instead of linarith over a context mentioning X
exact h1.trans ((le_add_of_nonneg_left h2).trans h3)
-- hide X from arithmetic
generalize hv : ‖X a‖ = v at h ⊢
-- unfold with an rfl equation instead of simp [X]
rw [X_def]
```

## Anti-patterns

- Using `Environment.contains` for the probe (false for constants realized in
  the current module).
- Probing without `Elab.async false` (the probe sees nothing).
- Running two probes of the same module at once with the same file name.

## Verification

The ownership audit reports zero violations; a declaration-type diff shows no
statement changes; the axiom audit is unchanged. Each round advances a chain
by one module: parallelize across chains, serialize the builds.

Learned from: a long ownership-audit repair in a random-matrix formalization
where one minting chain took about twenty one-module rounds, October 2026.
