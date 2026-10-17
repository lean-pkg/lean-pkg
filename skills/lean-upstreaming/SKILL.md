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

### Pre-PR review checklist (Mathlib leaf lane)

Before a human contributor opens a Mathlib PR, review the branch against what
Mathlib reviewers ask for. Most of it is library integration, not surface
style. Each bucket names where Mathlib states the rule; re-read those pages
at use time ([contributing], [style], [naming], [doc], [pr-review],
[commit]).

1. **Surface style.** Lines of at most 100 characters, `fun x ↦`, the
   formatting and tactic layout of the style guide ([style]). Fix these
   first; they are cheap and reviewers notice them first.
2. **Naming and namespaces.** Proofs and theorem names `snake_case`, types,
   structures and classes `UpperCamelCase`, other terms `lowerCamelCase`,
   file names `UpperCamelCase`; a namespace that makes dot notation work for
   callers ([naming]). Strip every paper-specific name. A rename or move of a
   declaration that already exists upstream keeps a dated `@[deprecated]`
   alias ([style], § Deprecation).
3. **Documentation.** A docstring on every definition and major theorem, a
   `/-! … -/` module docstring after the header and imports, cross references,
   a proof sketch in comments for a long proof, and a literature reference for
   a result taken from a paper ([doc], [pr-review]). No development history
   ("we first tried", "the agent found") in docstrings or comments.
4. **Placement and imports.** Search for an existing or more general result
   first (`exact?`, `apply?`, Loogle; `search-before-building`). Put each
   declaration in the lowest module that makes sense (`#find_home`), keep new
   imports light, and split a file that grows past about 1000 lines or mixes
   topics ([pr-review]). The file header is copyright, `module`, then public
   imports and imports in their own blocks ([style]).
5. **Weakest assumptions and API.** The weakest reasonable hypotheses,
   arguments left of the colon, hypotheses that are not conjunctions and
   usually not disjunctions ([style]); a definition that is the best way to formalize the concept and
   comes with basic lemmas about it ([pr-review]). A public declaration whose
   conclusion is vacuous (`∃ N, ∀ n ≥ N, True`) is not API: delete it or state
   the real result (compare `lean-weakened-hypothesis-vacuity`).
6. **Attributes and `simp`.** `@[simp]`, `@[ext]` and `@[simps]` exactly where
   they belong ([pr-review]): a simp lemma's left side is in simp normal form,
   its right side is simpler, and it creates no loop. Batteries' `simpNF`
   linter, run by `#lint`, checks the first.
7. **Instances.** No new diamond, either non-defeq or non-propeq
   ([pr-review]); no instance that loops or forces hard unification; a
   temporary structure that must not become a global instance is a `def`
   with a docstring saying so. `Type*`, not `Type _`, for arbitrary universes.
8. **Generated files and the module system.** After adding, renaming or
   deleting a file, `lake exe mk_all` updates `Mathlib.lean`; the file uses
   the `module` header and the right `public import`; `lake exe lint-style`
   and `#lint` are clean against current Mathlib master.
9. **Metadata and disclosure.** A PR title of the form `feat(Area): …`,
   `fix`, `chore` and so on, without the `Mathlib/` prefix ([commit]); the
   PR template's `Moves:` and `Deletions:` lines and `- [ ] depends on: #…`
   lines when they apply; and the AI paragraph: which tools were used and
   how, plus the `LLM-generated` label for substantial generated code
   ([contributing]). lean-pkg and its agents may draft the description; the
   human contributor rewrites it in their own words, opens the PR, adds the
   label and answers every review comment without AI help.

The checklist prepares a branch; it is not a review verdict. A green pass by
an agent does not stand in for the human Lean expert Mathlib expects behind
every PR.

[contributing]: https://leanprover-community.github.io/contribute/index.html
[style]: https://leanprover-community.github.io/contribute/style.html
[naming]: https://leanprover-community.github.io/contribute/naming.html
[doc]: https://leanprover-community.github.io/contribute/doc.html
[pr-review]: https://leanprover-community.github.io/contribute/pr-review.html
[commit]: https://leanprover-community.github.io/contribute/commit.html

The nine buckets follow `mathlib-review-taxonomy.md` in Cameron Freer's
[lean4-skills](https://github.com/cameronfreer/lean4-skills) (MIT), read at
commit `b6243b8`; the wording and the disclosure bucket are ours, checked
against the Mathlib pages above in October 2026.

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
- Opening a Mathlib PR, adding its label or posting its description from an
  agent; the agent prepares, the contributor opens.

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
