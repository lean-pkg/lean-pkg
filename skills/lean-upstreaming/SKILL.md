---
name: lean-upstreaming
description: "After a Lean theorem project closes, extract reusable declarations through a declaration-level ledger into exactly one lane (dependency upstream, Mathlib leaf, standalone library, or stays as a paper wrapper), keep the verified artifact frozen as the downstream regression test, and follow Mathlib's current contribution, AI-disclosure and own-words norms (re-fetched at use time). Trigger phrases: upstream to Mathlib, contribute to Mathlib, Mathlib PR, extract a reusable Lean library, standalone Lean package, Mathlib AI policy, LLM-generated label, Zulip first, post-closure library extraction."
license: Apache-2.0
---

# Upstreaming after closure

## Problem

A closed theorem project contains reusable mathematics, but it lives in a
paper namespace, imports a large umbrella, depends on an old pin, or sits on
top of vendored code. Submitting it wholesale obscures provenance, duplicates
existing APIs, couples review to paper-specific choices, and can destabilize
the exact artifact that established the theorem. Separately, Mathlib closes
low-effort AI-assisted pull requests and has explicit disclosure rules.

## Why it happens

Proof closure and library publication have different boundaries. The proof
artifact optimizes for a fixed theorem, fixed pins and an exact receipt; a
library needs a generic API, minimal imports, current compatibility,
documentation and maintainers. A declaration that looks generic can hide
manuscript constants or local instances in its dependency cone.

## Rule

- Freeze the verified artifact (commit, toolchain, pins, receipts). Port in a
  separate branch or repository; the frozen project stays the downstream
  regression test.
- Build a declaration ledger and assign each candidate exactly one lane:
  **dependency upstream** (fixes to vendored code go to its origin),
  **Mathlib leaf** (generic, small, extends an existing theory),
  **standalone library** (a coherent research API better served by its own
  Lake package), or **paper wrapper** (stays).
- For Mathlib: re-read the contributing page before every submission. As read
  in September 2026 it required disclosing AI tools and how they were used in
  the PR description, an `LLM-generated` label for substantial generated
  code, comments and replies in the contributor's own words, and that the
  contributor understands and can defend every choice without AI help. Open
  one PR at a time, after a community discussion, never a batch.
- The license (Apache-2.0) allows contributing, a standalone library and
  forking equally; the choice is strategic, not legal. A fork is an upload
  mechanism, not a place to diverge.

## How to apply

Ledger columns per candidate: source path and names; direct and transitive
imports; weakest assumptions in its type; paper-specific names, constants and
instances that must disappear; consumers in the project; authorship, license
and provenance; existing upstream declarations that may subsume it; lane.

Start with modules whose direct imports are Mathlib only. Extract bottom-up,
one semantic layer per PR: move, rename to the target's conventions, minimize
imports, document, add compatibility wrappers in the frozen project, and build
both sides before the next layer.

```bash
curl -s https://leanprover-community.github.io/contribute/index.html | grep -i -A3 'artificial intelligence'
gh api 'repos/leanprover-community/mathlib4/labels?per_page=100' --paginate --jq '.[].name' | grep -i llm
```

For a standalone package: OSI license, provenance, a canonical umbrella
module, minimal library targets, CI, documentation, release tags; it can be
listed on Reservoir and archived with a DOI (`lean-publishing`).

## Anti-patterns

- Copying vendored code into a new package as if it were new work.
- Replacing a verified vendor snapshot before the upstream revision is
  accepted and the downstream receipt regenerated.
- Posting agent-written text on GitHub or Zulip.
- Treating a sorry-free build as sufficient for Mathlib review (generality,
  integration, maintainability and style are reviewed too).

## Verification

An extracted contribution is ready when it builds clean against the target's
current revision, passes the target's lints and import checks, has a live
axiom audit for new endpoints, and the frozen project still builds through
its wrappers with byte-identical endpoint types.

For Mathlib naming, style and search see the `lean-naming-style` and
`lean-search-discovery` skills linked from [the index](../README.md).

Learned from: planning a staged series of Mathlib contributions from a
closed probability formalization, and extracting libraries from a closed
geometry formalization, September 2026.
