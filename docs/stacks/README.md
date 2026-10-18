---
title: "LTS stacks"
---

[&larr; lean-pkg documentation](../index.md)

# LTS stacks

A stack is a dated, tested combination of a Lean toolchain, a Mathlib tag
and a range of lean-pkg releases, which those releases keep working on for
a stated window ([proposal 0004](../proposals/0004-math-lts-stack.md),
accepted 2026-10-17). Stacks are named `lts-YYYY.MM` and cut in April and
October; each is supported for about 18 months. Each has a machine-readable
file (schema `lean-pkg-stack/1`, validated by `tests/test_stacks.py`) and a
page.

| Stack | Lean | Mathlib | lean-pkg | Supported until | Status |
|---|---|---|---|---|---|
| [lts-2026.10](lts-2026.10.md) ([yaml](lts-2026.10.yaml)) | `v4.35.0` | `v4.35.0` (`56a005855ea1`) | `>=0.12.0,<1.0` | 2028-04-30 | declared 2026-10-18 (five matrix entries; gromov-filling-lean a migration case) |

## How to opt in

Nothing changes for a package that does not opt in. A package opts in with
one optional key in its `coverage.yaml` (both kinds; lean-pkg 0.14.0 and
older refuse it, since coverage/1 is strict):

```yaml
stack: lts-2026.10
```

- **A new package**: `lean-pkg new NAME --stack lts-2026.10` writes the
  stack's `lean-toolchain` and Mathlib `rev`, and the key. `--stack current`
  takes the newest declared stack whose window covers today; `--stack none`
  the newest pins, with no key. Without `--stack`, `--kind paper` takes the
  current stack and `--kind library` the newest pins (libraries aimed at
  Mathlib follow its newest release). A name that is not declared exits 2
  and lists the declared stacks.
- **An existing package**: move the pins as the stack's page says
  (`lean-toolchain`, the Mathlib `rev` in the lakefile, `lake update`), add
  the key, commit, and verify the new commit.
- **The check**: `lean-pkg doctor NAME` and `lean-pkg verify NAME` compare
  the committed `lean-toolchain`, the Mathlib commit in `lake-manifest.json`
  (the stack's commit, not only its tag name: a tag can be moved) and the
  comparator receipt's tool digests (the stack's Linux x86_64 digests) with
  the stack. They print `on stack lts-2026.10 (supported until 2028-04-30)`
  or a warning naming each difference, and `--json` carries `stack`
  {name, declared, matches, supported_until, window, toolchain, mathlib,
  comparator_tools, lean_pkg, mismatches, warnings}. A stack past its
  window, one this lean-pkg does not declare, or a lean-pkg outside the
  stack's range is a warning too. Neither command refuses because of the
  stack: the key is a claim the author makes, and it never makes a check
  pass or fail ([proposal 0004](../proposals/0004-math-lts-stack.md),
  "Trust model").
- **The page**: when the pins match, *How this was checked* carries "Built on
  lean-pkg stack lts-2026.10", linking the stack's page.

The comparator tools of a stack are the ones its toolchain bundles (the
`toolchain` mechanism), so the `lean-toolchain` pin is their pin. Moving
between stacks (`doctor --stack-from A --stack-to B`, migration notes)
comes with the second stack.

## Where the files live

The files in this directory are the source of truth. The lean-pkg wheel
carries a byte-identical copy of each under `lean_pkg/data/stacks/`
(written by `make docs`, checked by `tests/test_stacks.py`), which is what
an installed lean-pkg reads, inside or outside a lean-pkg checkout.
