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

A package opts in with a `stack:` key in `coverage.yaml` once stage 1 of the
proposal is released; until then, move its pins by hand as the stack's page
says. Nothing changes for a package that does not opt in.
