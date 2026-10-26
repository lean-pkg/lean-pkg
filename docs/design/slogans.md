---
title: Slogans
---

# Slogans: short lines that say what lean-pkg is for

Status: working note, 2026-10-17. Anyone may add a candidate or argue with a
verdict by pull request. The set in use is in [In use](#in-use); swapping one
is a one-line change.

A slogan here has one job: to make lean-pkg's purpose easy to remember
without saying anything the [trust model](../trust-model.md) would have to
take back. The tests every candidate must pass:

- **True.** lean-pkg proves nothing itself, does not re-run Lean when it
  validates a receipt, does not check that a Lean statement says what the
  paper says, and does not make an AI assistant's output trustworthy. A
  receipt is an unsigned record of a run, not a proof.
- **The division of labour.** An AI assistant drafts or assists; Lean (its
  kernel, and the independent kernels of the comparator tier) checks proofs;
  lean-pkg checks that the evidence agrees with itself and with one commit,
  and records it; the mathematician owns the statement and the judgment.
  lean-pkg records a Mathlib pull request a person opened; it never opens
  one.
- **Short and plain.** Ideally under twelve words, in English a mathematician
  would not wince at; no marketing cliché, no exclamation mark, no AI hype.

## In use

| Where | Slogan |
|---|---|
| README, under the title; the docs site description (`docs/_config.yml`) | Lean checks the proofs. You check the statements. lean-pkg checks the receipts. |
| docs/index.md, top | What was verified, against which statement, at which commit. |
| Trust model, *Re-verify a page yourself* | Do not take a receipt's word for it: run the checks again. |
| π-λ tutorial, opening and close (unchanged) | Lean checks every step; you check the statement. |
| Two tracks, under the title | One gate, two products: a verified paper or a reusable library. |
| README, *Why lean-pkg*, research loop | Mathematics, Lean and an AI assistant: one workflow, and a record of who checked what. |
| GOVERNANCE.md, the AI operations agent | The agent drafts; the maintainer decides, and answers for it. |

Lines already in the docs before this note, and kept: *A green build is a
claim. A receipt is evidence.* (README, *Why lean-pkg*), **Develop privately.
Discover freely. Verify explicitly. Publish reproducibly.** (README), and
*Mathlib holds the theorems. Palomar keeps the record. lean-pkg keeps the
trust.* (Motivation).

Retired: *Prove it. Then prove you proved it.* (the README and site tagline
until this note). It is memorable, but its second half calls a receipt a
proof, which the trust model denies in its first paragraph. Kept below as
B1 so the decision can be revisited.

## Candidates

Verdicts: **exact** (true as stated), **close** (true with a reading a
skeptic would accept), **stretch** (needs a caveat; not for use alone),
**false** (overclaims). Rank is within the whole list, 1 best; unranked
lines are not recommended.

### A. The core idea

| # | Slogan | What it claims | Verdict | Rank |
|---|---|---|---|---|
| A1 | Lean checks the proofs. You check the statements. lean-pkg checks the receipts. | three checkers, three objects: the kernel checks proofs, the reader checks statement fidelity, lean-pkg checks that receipts agree with the bytes at one commit | exact: `gate` validates receipts against the commit (`gate.validate_receipt`), and statement fidelity is the reader's (page trust line) | 1 |
| A2 | What was verified, against which statement, at which commit. | the three facts a page records | exact: the page reads the statement from the bound commit, the receipts name the commit, the comparator receipt names the theorems compared | 2 |
| A3 | Lean checks every step; you check the statement. | the division between kernel and reader | exact; already the tutorial's motto. Says nothing about lean-pkg's own job | 3 |
| A4 | Verified means: this statement, this proof, this commit. | a definition of the word *verified* on a page | close: on a page *verified* also needs the claims ledger to close the row (`page`), which this leaves out | 15 |
| A5 | Evidence you can re-check, not a status word to believe. | receipts replace "CI was green" | exact: receipts are files bound to a commit; the trust model shows how to re-run them | 11 |
| A6 | The record of what Lean checked, and where. | lean-pkg is a record keeper | close: "where" is the commit and toolchain, not a host (hosts are hashed labels) | 18 |

### B. Trust and receipts

| # | Slogan | What it claims | Verdict | Rank |
|---|---|---|---|---|
| B1 | Prove it. Then prove you proved it. | a receipt proves the proof was checked | false in its second half: "A receipt is an unsigned record of a run, not a proof" (trust model). Retired from the tagline | – |
| B2 | A green build is a claim. A receipt is evidence. | the difference between a status word and a record | exact; in use in the README | 4 |
| B3 | Do not take a receipt's word for it: run the checks again. | the reader's way to remove trust in whoever ran the checks | exact: it is the trust model's own conclusion and final section | 5 |
| B4 | No receipts, no public flip. | `promote` refuses a package without both receipts | exact: the two-receipt rule of `promote` | 10 |
| B5 | Prove it in Lean. Keep the receipt. | the author's two duties | close: "keep" means commit it under `.lean-receipts/`; lean-pkg does the checking, not the keeping | 16 |
| B6 | Receipts, not reassurances. | evidence over status words | exact but thin: says nothing about what a receipt binds | 22 |
| B7 | A receipt records a run; the proof is the Lean source. | what a receipt is, and is not | exact, nearly a quotation of the trust model; long for a motto | 13 |
| B8 | Checked twice, by independent kernels. | kernel replay | stretch: true of the comparator tier only, not of the lower-tier receipt | – |
| B9 | Trust nothing you cannot re-run. | a reader's rule | false as a description: the kernels, the toolchain and Mathlib's definitions are trusted, not re-derived | – |

### C. Mathematics, Lean and an AI assistant

| # | Slogan | What it claims | Verdict | Rank |
|---|---|---|---|---|
| C1 | Mathematics, Lean and an AI assistant: one workflow, and a record of who checked what. | one workflow for the three, and the page's "who checked what" table | close: "one workflow" is what lean-pkg proposes, not a standard others have adopted; "who checked what" is the tutorial's closing table | 7 |
| C2 | An AI assistant drafts, Lean checks, lean-pkg records, you own the statement. | four parties, four verbs | exact; long (twelve words) and close to A1 | 12 |
| C3 | An AI can write the proof. Only you can sign off the statement. | the sign-off is human | exact: `holes.check_reviewer` refuses a model or agent name; but a statement can be read and judged by any human reader, not only "you" the author | 14 |
| C4 | The assistant is fast; the kernel is strict; the statement is yours. | speed, rigour, responsibility | close; the adjectives are characterisation, not checks | 24 |
| C5 | The agent drafts; the maintainer decides, and answers for it. | GOVERNANCE.md's model for the operations agent | exact for the project's own governance (the agent acts through the maintainer's account) | 8 |
| C6 | Let the assistant write the proof; let Lean judge it. | AI proofs are safe once Lean checks them | stretch: silent on the statement, which is exactly where a drafted formalization goes wrong | – |
| C7 | A standard workflow for mathematics with Lean and an AI assistant. | Le's original phrasing of the aim | close as an aim; as a description "standard" claims adoption that does not exist yet | 26 |
| C8 | lean-pkg makes AI-written proofs trustworthy. | lean-pkg is the source of trust in an assistant's output | false: trust in the proof comes from the kernel, and in the statement from the reader | – |

### D. Two tracks: a paper or a library

| # | Slogan | What it claims | Verdict | Rank |
|---|---|---|---|---|
| D1 | One gate, two products: a verified paper or a reusable library. | the shared machinery and the two final products | exact: Tracks P and L share the gate and the receipts and differ in what the page is anchored to | 6 |
| D2 | Every numbered result: formalized, given its inputs, or honestly not. | Track P's page accounts for every numbered result | close: the classes are `same-assumptions`, `inputs-supplied`, `outside` and *Not formalized*; "honestly" is the authors' word, not a check | 20 |
| D3 | Same evidence, whether the result lives in a paper or a library. | the receipts do not depend on the track | exact | 25 |
| D4 | Verify the paper, or grow the library. | the two choices | close; says nothing about trust | 28 |

### E. The reader's and the referee's view

| # | Slogan | What it claims | Verdict | Rank |
|---|---|---|---|---|
| E1 | Compare two statements; the kernel checked the proofs. | what is left to a referee | exact for the comparator tier; for a lower-tier page the referee also relies on the executor's log | 21 |
| E2 | Here is what was checked, and here is what is left to you. | the page's honesty about its limits | exact: the page's trust line and *What the checks establish* | 19 |
| E3 | Read the paper's statement beside Lean's. The rest is checked. | everything but statement fidelity is machine-checked | stretch: "the rest" includes the executor's honesty and the authors' coverage classes, which are not checked | – |
| E4 | A referee compares statements, not proofs. | the referee's job on a page | close, but a referee still judges correctness of the transcription and significance | 30 |

### F. Standardizing the workflow

| # | Slogan | What it claims | Verdict | Rank |
|---|---|---|---|---|
| F1 | Develop privately. Discover freely. Verify explicitly. Publish reproducibly. | the lifecycle | exact; in use in the README | 9 |
| F2 | Private while you work, explicit when you publish. | the private registry and the public flip | exact | 27 |
| F3 | From conjecture to a citable, checked result, one way every time. | the lifecycle ends in a DOI | close: "checked" is the receipts; "one way" is aspiration across groups | 29 |
| F4 | The paperwork of formal verification, done the same way every time. | lean-pkg is the bookkeeping around Lean | exact and modest; "paperwork" may undersell the comparator | 23 |
| F5 | lean-pkg prepares the evidence; people make the decisions. | promote, Mathlib PRs and sign-offs are human | close: a definition-hole sign-off may not name a model or agent, and lean-pkg records a Mathlib pull request a person opened (`record mathlib-pr-opened`) without opening one; but nothing stops an agent running `promote --yes`, so "people" is the documented practice, not a check | 17 |

## Runners-up

The next five, in rank order, for swapping into a slot above: B4 *No
receipts, no public flip.* (README or the lifecycle page); A5 *Evidence you
can re-check, not a status word to believe.* (docs index); C2 *An AI
assistant drafts, Lean checks, lean-pkg records, you own the statement.*
(README, *Why lean-pkg*); B7 *A receipt records a run; the proof is the Lean
source.* (trust model); C3 *An AI can write the proof. Only you can sign off
the statement.* (definition holes, or the agents page).

## Rejected patterns

- Anything that says lean-pkg *proves*, *guarantees* or *certifies the
  paper*: it certifies exact formal statements at one commit.
- Anything that makes the AI assistant the subject of trust ("AI-verified",
  "trustworthy AI proofs").
- Superlatives and urgency ("the only", "finally", "never again").
