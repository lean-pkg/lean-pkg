---
title: Develop a paper with its Lean companion
---

[&larr; lean-pkg documentation](index.md)

*Check intermediate results while the argument evolves, not only the finished paper.*

A Lean companion does not have to wait until the manuscript is done. You can
formalize a selected intermediate step while the argument is still changing,
record exactly what was checked at a stable commit, and carry that evidence
forward as the paper evolves. At the end you ship the manuscript, a
reproducible companion, and an explicit record of what is and is not covered.

This page is a workflow guide with a small synthetic example. It describes
capabilities, not measured results: auditable reuse and more focused review
are what the workflow supports; a net speedup or a general reliability gain
has not been measured.

## Who does what

| Layer | Responsibility |
|---|---|
| Lean | checks the encoded deduction: the formal statement and its proof |
| `lean-pkg` | the package, claim and evidence lifecycle: registry, leases, claims, receipts bound to commits, gate, ledger, search |
| your manuscript workflow (external) | writing the paper, keeping statements and formal counterparts aligned, reviewing statement correspondence and the arguments Lean does not cover |

`lean-pkg` does not compare a paper's prose with a Lean statement, and it does
not review mathematics outside the formal statements. Those steps belong to
your own review process, whatever tools it uses.

A certificate covers the exact formal statements it records, under their exact
hypotheses. It does not certify the surrounding paper.

## The layout

Keep the Lean package in its own directory and the working notes outside it
(see [Progress files live outside the package directory](receipts.md#progress-files-live-outside-the-package-directory)):

```text
telescoping-paper/              the manuscript repository
  paper/telescoping.tex
  COVERAGE.md                   the paper-to-Lean map (below)
  progress/                     progress log, wave notes
  lean/                         the package: registered local_path
    lakefile.toml  lean-toolchain  lake-manifest.json  claims.yaml
    Telescoping/Basic.lean
```

## 1. Register the package and take the lease

```bash
lean-pkg register telescoping-companion --repo git@github.com:you/telescoping-paper.git \
  --path src/telescoping-paper/lean --owner-project telescoping-paper
lean-pkg claim telescoping-companion --workspace laptop --agent you
```

## 2. Pick an intermediate step and search before building

Choose a step the rest of the argument leans on, or one you are unsure of.
In the example paper, the main estimate uses the partial sums of
`1/((k+1)(k+2))`. Search before writing anything:

```bash
lean-pkg search "telescoping partial sums" --package telescoping-companion
```

If the search reports `incomplete: searched N of M sources`, a zero is not a
negative result; search again later or look by hand.

## 3. State it exactly, with its hypotheses

Write the formal statement with every hypothesis the paper uses, and no more:

```lean
theorem Telescoping.sum_inv_mul_succ (n : ℕ) :
    ∑ k ∈ Finset.range n, (1 : ℚ) / ((k + 1) * (k + 2)) = 1 - 1 / (n + 1)
```

Never assume the result you want in order to get coverage: a hypothesis that
restates the conclusion makes the certificate say nothing. When a formal
attempt fails, say why before moving on. It may be a wrong formulation, a
missing hypothesis, a library gap, or a problem with the build, and only the
first two say something about the mathematics.

Declare the result in `claims.yaml`, and check the file before committing:

```yaml
schema: claims/1
package: telescoping-companion
claims:
  - id: telescoping-companion/sum-inv-mul-succ
    decl: Telescoping.sum_inv_mul_succ
    kind: theorem
    summary: "For every n, the sum over k < n of 1/((k+1)(k+2)) equals 1 - 1/(n+1)."
    file: Telescoping/Basic.lean
    status: reusable
    keywords: [telescoping, partial-sums]
  - id: telescoping-companion/partial-fraction
    decl: Telescoping.inv_mul_succ_eq_sub
    kind: lemma
    summary: "1/((k+1)(k+2)) = 1/(k+1) - 1/(k+2), the step the main sum uses."
    file: Telescoping/Basic.lean
    status: internal
```

```bash
lean-pkg claims check --file claims.yaml
```

`reusable` claims are the ones a later gate requires; `internal` helpers are
still part of the package and its evidence, but are not offered for reuse.

## 4. Freeze a checkpoint and record evidence

When the step is proved, commit the source and produce a receipt bound to that
commit, for example from a capped local run (or `receipt slurm` on a cluster):

```bash
lean-pkg receipt systemd --unit tele-gate --commit "$(git rev-parse HEAD)" --log /tmp/tele/verify.log \
  --stdout /tmp/tele/unit.out --stderr /tmp/tele/unit.err --checker "verify.sh" --run -- bash verify.sh /tmp/tele/verify.log
lean-pkg claims sync telescoping-companion
lean-pkg gate telescoping-companion --check --receipt .lean-receipts/<commit12>-<job>.json
lean-pkg update telescoping-companion --status gate-pending --reason "receipt for <commit12> ready"
lean-pkg gate telescoping-companion --receipt .lean-receipts/<commit12>-<job>.json
lean-pkg claims ledger telescoping-companion
```

The receipt binds the package directory at one commit. The gate refuses, for
example, a receipt whose verify log declares fewer `sorry` warnings or axioms
than Lean itself printed, or a commit where the package has changed since.

## 5. Keep developing; reuse what did not change

The paper keeps changing. Each later checkpoint asks one question per claim:
did its formal statement, its proof, or the way the paper uses it change?

- **Nothing changed** in the package directory: the recorded evidence still
  binds (`gate --check` confirms it), and review can concentrate on what is new.
- **Any file in the package changed**, even a docstring: a new receipt and
  `gate --regate`. The evidence-only rule allows only `.lean-receipts/` to move.
- **Only the paper's use changed**, for example it now applies the theorem
  under a different hypothesis: the kernel proof still stands, but nothing in
  `lean-pkg` checks the new application. That review is yours.

A refusal is information. If `gate` refuses, read which binding it names
before changing anything.

## 6. Keep a coverage map

Next to the manuscript (not inside the package), record how each statement in
the paper relates to the companion:

| Paper | Claim | Coverage |
|---|---|---|
| Lemma 2.1 (partial sums) | `telescoping-companion/sum-inv-mul-succ` | formal, verified at `<commit12>` |
| Proposition 2.3 (the estimate) | none | paper proof only; uses Lemma 2.1 |
| Section 4 (asymptotics) | none | not formalized |

Update the map at every checkpoint; it is what tells a reader where the
certificate stops.

## 7. Finish

At submission the deliverable is:

- the manuscript;
- the companion repository at a tagged commit, with exact toolchain and
  Mathlib pins and the receipts in `.lean-receipts/`;
- the claims file and `lean-pkg claims ledger` output for the gated commit;
- the coverage map, stating plainly which results are formal, which are
  conditional on named hypotheses, and which are not formalized.

Publishing the companion follows the usual path: [scan for private strings](how-to.md#scan-for-private-strings-before-publishing),
the comparator tier, and `promote`.

## What this does not claim

- Partial formal coverage is not a certificate for the whole paper.
- An unchanged proof does not cover a changed application of it.
- The workflow makes reuse auditable and review more focused; it has not been
  shown to make papers faster to write or free of errors.
