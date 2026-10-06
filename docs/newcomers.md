---
title: Start here
---

[← lean-pkg documentation](index.md)

*Read one chapter. Run one sample.*

<img src="assets/mascot-clerk.png" width="150" alt="cartoon secretary bird holding a receipt with a check mark, one foot on a snake" style="float:right;margin:0 0 1rem 1.5rem">

The mascot is a secretary bird: the bird named for a clerk, because its crest
feathers look like quill pens tucked behind an ear, that keeps the records
and stamps out the snakes.

You are new to Lean, or new to verified research, or both. This page is the
shortest honest path from zero to a certified package. Budget: one or two
weeks of part-time reading, then an afternoon for the sample workflow.

## 1. Learn enough Lean (one to two weeks)

The reading list below is the one Le Chen gives graduate probability
students in Math 7800 at Auburn. Do it in order; stop when the sample
workflow makes sense to you.

| Step | Read or do | Why |
|---|---|---|
| 1 | [Functional Programming in Lean](https://lean-lang.org/functional_programming_in_lean/), Chapter 1 "Getting to Know Lean" | Lean is a programming language first. Chapter 1 teaches the syntax, types, and structures you will read in every package. One evening. |
| 2 | [From Python to Lean: a quick start](https://webhome.auburn.edu/~lzc0090/teaching/2026_Fall_Math7800/Talk-Python-to-Lean.pdf) and [Lean for Math 7800](https://webhome.auburn.edu/~lzc0090/teaching/2026_Fall_Math7800/Talk-Lean-Math7800.pdf) (slides) | If you already program, this maps what you know onto Lean, then shows a probability proof (the π-λ theorem) being checked. |
| 3 | [live.lean-lang.org](https://live.lean-lang.org) | Try things in the browser before installing anything. Paste a theorem from step 2 and break it on purpose. |
| 4 | [Mathematics in Lean](https://leanprover-community.github.io/mathematics_in_lean/), the chapters on sets, functions, and measure | How Mathlib states mathematics. You need this to *read* a `Challenge.lean`, which is the whole point of the comparator tier. |
| 5 | [Loogle](https://loogle.lean-lang.org) and [Mathlib docs](https://leanprover-community.github.io/mathlib4_docs/) | How to find the lemma you need by its type. You will use these more than any book. |

Install when step 3 stops being enough: `elan` (the Lean toolchain manager),
then `lake` comes with it. The
[Lean community install page](https://leanprover-community.github.io/get_started.html)
is current; this site does not duplicate it.

## 2. Learn what "verified" means here (one hour)

Read, in this order:

1. [Motivation](motivation.md): why a green build is not yet a verified
   theorem, and where lean-pkg sits next to Mathlib, Palomar, and Reservoir.
2. [Receipts and trust](receipts.md): the two receipt tiers, what a passing
   pair proves and does not prove, and why evidence is re-derived from bytes.
3. [Lifecycle](lifecycle.md): the status enum. You will mostly care about
   `active → gate-pending → verified-private → public`.

If you remember one sentence: *the comparator tier checks that the theorem
you proved is the theorem you claimed, in a second kernel; everything else
is bookkeeping that makes that check reproducible.*

## 3. Do the sample workflow (one afternoon)

[Sample workflow](sample-workflow.md) walks through a real package, from
registering it to a promotion dry run, with every command and its output.
Do it on your own machine with your own small project. The first comparator
run builds its tools once (about 25 minutes); every later run takes seconds.

## 4. Pick up a workflow habit

- **Claim before you work.** `lean-pkg claim` is how two people, or a person
  and an agent, never edit the same package at once.
- **Commit evidence, never results.** `.lean-receipts/` is committed; the
  registry stores only paths. A receipt you cannot re-derive is not evidence.
- **Read every dry run.** `promote` prints a checklist before it does
  anything. Refusals are named facts, not obstacles.
- **Use the tool from your agent.** If you work with Claude Code, Codex, or
  a similar agent, point it at [llms.txt](llms.txt) first. The contract is
  written for it.

## 5. Where to ask

- Questions about this tool: open an issue in the repository.
- Questions about Lean or Mathlib: the
  [Lean Zulip](https://leanprover.zulipchat.com), streams `new members`
  and `Is there code for X?`. Read the stream rules first; the community
  asks that review discussion be written in your own words.
- See the [FAQ](faq.md) before asking; the common ones are there.

Next: [Sample workflow](sample-workflow.md).
