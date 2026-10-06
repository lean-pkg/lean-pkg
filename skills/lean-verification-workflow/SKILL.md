---
name: lean-verification-workflow
description: "Router for LLM-assisted Lean verification: pick the right skill for statement fidelity, proof engineering seams, evidence and receipts, publication, or the lean-pkg registry, and start from lean-pkg llms whenever a package is registered with lean-pkg. Trigger phrases: verify a paper in Lean, which Lean skill, Lean verification workflow, formalize and publish, lean-pkg, trust gate, is this formalization faithful, prepare receipts, Palomar."
license: Apache-2.0
---

# Lean verification workflow (router)

## Problem

A Lean formalization meant to support a paper can fail in three different
places: the statement does not say what the paper says, the evidence does not
bind to what was built, or the publication claims more than the evidence.
Each has its own skill. Picking the wrong one, or none, is the common failure.

## Why it happens

A green build feels like the end. It is the start of three separate checks:
fidelity (statement versus paper), evidence (receipts versus bytes) and
publication (prose versus evidence).

## Rule

- If the package is registered with lean-pkg, run `lean-pkg llms` first and
  follow it; it is the authoritative contract and overrides any skill.
- Before claiming anything about a paper's theorem, consult a fidelity skill.
- Before reporting a pass, consult an evidence skill.
- Before anything public or permanent, consult `lean-publishing` and leave
  the decision to a human.

## How to apply

| Task | Skill |
|---|---|
| before writing a new definition, lemma or library: does it already exist (Loogle, Mathlib, Reservoir, Palomar, GitHub)? | `search-before-building` |
| use the registry: register, claim, gate, comparator, promote | `lean-pkg-agent` (after `lean-pkg llms`) |
| decide what to formalize while the paper is being written; pin Lean and Mathlib | `lean-paper-companion` |
| record which paper claims are verified, conditional, partial or excluded; cold second read | `lean-claim-ledger` |
| does this Lean statement or definition say what the paper says? | `lean-fidelity-audit-taxonomy` |
| I relaxed a hypothesis to cover a new case | `lean-weakened-hypothesis-vacuity` |
| analytic facts enter as hypotheses or structure fields | `lean-partial-formalization-boundary` |
| is each lemma stated as broadly as it is used? | `statement-vs-use-span-audit` |
| I need an input I cannot prove now | `lean-honest-named-hypotheses` |
| `Classical.choose` lost a property; `Or` into a `Type` field | `lean-prop-data-witnesses` |
| producer compiles, consumer fails | `lean-elaboration-boundaries` |
| a closed computation over a big Finset or certificate is too slow or froze the machine | `lean-large-finite-computation` |
| two libraries collide on declaration names | `lean-vendor-namespace-isolation` |
| an ownership audit flags `X.congr_simp` or `X.eq_1` downstream | `lean-reserved-name-mint-site` |
| write or fix the `#print axioms` audit; negative controls | `lean-axiom-audit` |
| bind results to commits; leaf vs full builds; replay layers | `lean-receipt-discipline` |
| Palomar, DOI, Reservoir, citation | `lean-publishing` |
| extract a library or contribute to Mathlib | `lean-upstreaming` |

Typical order for one paper: search before building, companion, ledger, fidelity skills as each
statement is written, axiom audit and receipts at each milestone, lean-pkg
gate and comparator, then publishing.

Not covered here (see the index for a linked set that covers them well):
Mathlib search, naming and style, elaboration performance, project
architecture, multi-agent orchestration, source dependency graphs.

## Anti-patterns

- Treating this set as a replacement for `lean-pkg llms`.
- Running publishing steps before the ledger and receipts exist.
- Reporting "verified" without naming the ledger status and the receipt.

## Verification

For the task at hand, the chosen skill's own Verification section has been
satisfied, and the claim you report names its ledger row and receipt commit.

Learned from: routing questions that recurred across several formalization
campaigns once the individual lessons existed, October 2026.
