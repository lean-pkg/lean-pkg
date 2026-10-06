---
name: lean-vendor-namespace-isolation
description: "When two Lean libraries each build but fail together because they export the same global declaration names (or a broad import Mathlib revives a conflicting API), vendor the exact transitive source closure of the smaller side, alpha-rename its root namespace, narrow its imports leaf to root through Lake, and keep every dependency pin unchanged. Trigger phrases: environment already contains, declaration already exists, namespace collision, import Mathlib collision, vendored Lean closure, backport a theorem to an older Mathlib, leaf builds but umbrella fails, stale olean hides missing import."
license: Apache-2.0
---

# Isolate colliding Lean libraries without moving pins

## Problem

Two developments each compile and pass an axiom audit, yet the umbrella that
imports both fails:

```text
environment already contains 'Graph.vertexSet' from OtherLibrary
```

This is common when backporting a theorem closure to an older pinned Lean or
Mathlib. Reordering imports cannot fix two genuinely overlapping names, and
upgrading the whole project may be undesirable.

## Why it happens

Declaration names are global within an environment. Independent projects pick
the same short root namespace, or Mathlib later adopts one. A broad
`import Mathlib` loads the conflicting module even if no visible code uses it.
A direct `lean File.lean` check can also read a stale `.olean` for a local
import that still carries the broad import, so narrowed imports look fine
until Lake rebuilds the graph.

## Rule

Fix the collision on the smaller, vendored side by renaming, with exact
provenance and unchanged pins; prove integration with the umbrella, not with
leaf builds.

## How to apply

1. Prove the failure is joint: build each leaf, then the umbrella; record both
   fully qualified names and the modules that introduce them.
2. Record upstream repository, commit, entry module, license, and Lean and
   Mathlib targets. Vendor the exact transitive local source closure.
3. Alpha-rename the vendored root namespace (`Graph` to `VendorGraph`),
   including scoped notation and `_root_` references; keep module paths in
   `import` lines. Preserve license and authorship headers. Expose the result
   through a small project-facing wrapper.
4. Replace `import Mathlib` in vendored files with the minimal imports (the
   `#min_imports` command reports them), leaves first; rebuild each leaf and
   its dependents through Lake, not `lean` on a single file.
5. Add a lexical gate:

   ```bash
   # no stray old root namespace outside import lines
   ! grep -rnE '(^|[^A-Za-z_.])Graph\.' Vendor/ | grep -v '^[^:]*:[0-9]*:import '
   # no broad umbrella import in vendored code
   ! grep -rn '^import Mathlib$' Vendor/
   ```

## Anti-patterns

- Treating a green leaf build as integration evidence.
- Bumping project pins as a side effect of a collision fix.
- Copying vendored code into a new package and presenting it as new work.

## Verification

On the final tree: the lexical gate, a build of the vendored theorem and
wrapper, the whole umbrella containing both former colliders, the full
package build, the axiom audit of promoted theorems, and `git diff --check`.

Learned from: making a Jordan-curve closure built for a newer Lean coexist on
an older pinned toolchain with an independently built Jordan-curve
development, August 2026.
