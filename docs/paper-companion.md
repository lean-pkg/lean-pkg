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
For the full discover, pin, check and decide cycle, see
[Prior-art search: while the paper is being written](prior-art.md#while-the-paper-is-being-written).

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

## Consolidate several checkpoint packages into one

A long project can leave several verified-private packages behind, one per
round or checkpoint, each with its own gate. Publishing one companion then
means consolidating them into a new package. There is no single command for
this: `release --fresh-repo` moves one existing record to one new repository,
while consolidation creates a new record from several inputs. The recipe
below uses only existing commands. It assumes the inputs share one Lean
toolchain and one Mathlib revision; align the pins first if they do not,
since that is a change to every input.

When the Lean code lives inside the paper's own repository rather than in
registered packages, [`lean-pkg-extract plan`](extract.md) plans the public
tree instead: every file classified (private material default-denied),
traced to a blob of one source commit, with its module and claim maps
(P1: plan only; write comes next).

**The rules.** The old packages, their repositories and their histories stay
as they are. The new package gets a new name, because an old name already
denotes its own round. Its evidence is new: no old receipt is copied or
reused, because a receipt binds one commit of one repository and the
consolidated commit descends from none of them. Old receipts and commits
appear in the new repository only as provenance.

### Assemble the tree without renaming anything

Build the new tree in a new repository and commit the result as one
consolidation commit that relocates code and changes nothing else:

- keep every namespace and declaration name as it was;
- where possible keep the module paths too (one `lean_lib` per input in a
  single `lakefile.toml` keeps each file byte-identical); where files must
  move, the only content change allowed is the `import` lines;
- do not fix, golf or reformat anything in this commit.

Renames, merged helpers and cleanups come later as separate commits, each
reviewed and gated on its own. A reviewer can then check the consolidation
commit mechanically against the hash map, and the later commits as ordinary
diffs.

### Record the source and claim hash map

Commit a hash map in the new package directory, in the consolidation commit
itself, so that the first receipt binds it. `lean-pkg` does not read this
file; it is your record, and a reviewer re-checks it. One possible shape,
`provenance/consolidation.yaml`:

```yaml
inputs:
  - package: example-round-1            # the old registry name
    repo_commit: 0123456789abcdef0123456789abcdef01234567
    gate_receipt: .lean-receipts/0123456789ab-job123.json   # path in the OLD repository; provenance only
    comparator_receipt: .lean-receipts/0123456789ab-comparator.json
files:
  - from: {package: example-round-1, path: Example/Round1/Basic.lean,
           sha256: 9f2c...}
    to: {path: Example/Round1/Basic.lean, sha256: 9f2c...}
    change: none                        # or: imports-only
claims:
  - from: example-round-1/main-bound
    to: example-companion/main-bound
```

Take each `from` hash from the old commit, not from a working copy
(`git -C OLD show COMMIT:PATH | sha256sum`), and each `to` hash from the
committed new tree (`git show HEAD:PATH | sha256sum`). Every relocated file
and every old claim gets a row; a claim dropped on purpose gets a row that
says so. For an `imports-only` row, the diff between the two blobs with the
`import` lines removed must be empty. If the new package is meant to become
public, check that the old names and paths the map cites may be public:
`lean-pkg public-safety NAME --strict` scans the map like any other file.

### Audit the collisions before the first gate

Several inputs that each passed their own gate can still clash once they
share one environment. Check before the first verify run:

- **Owned names.** Two inputs that define the same fully qualified name
  build fine as long as no module imports both, so a plain `lake build` of
  the separate roots can miss the clash. Add a root module that imports every
  module of the package and build it: a clash is then a Lean error. List the
  declarations of each input and compare them as well, so the report names
  both owners.
- **Claim ids.** Merge the claims into one `claims.yaml` for the new package
  name and run `lean-pkg claims check --file claims.yaml`; it refuses
  duplicate ids. Two old ids that name the same declaration, or one id
  reused across rounds for different statements, need a decision recorded
  in the map's `claims` rows.
- **Negative controls and witnesses.** Each input's checker may carry
  negative controls (a file that must fail, an axiom list that must not
  grow) and witness definitions. Merge them into one checker for the new
  package, check that each control still targets the name it was written
  for, and keep deliberately failing files out of every `lean_lib` (a file a
  library owns is built, so a failing control breaks the build). Witnesses
  with the same name from two inputs are an owned-name clash like any other.
- **Comparator configuration.** Do not concatenate old `comparator.json`
  files: their Challenge modules can clash too. Scaffold a new configuration
  from the merged claims once they are synced
  (`lean-pkg comparator config --from-claims NAME`), then carry the
  hand-written statements over entry by entry.

### Register, gate and run the comparator on the new commit

```bash
lean-pkg register example-companion --like example-round-1 \
  --repo git@github.com:you/example-companion.git --path src/example-companion
lean-pkg claim example-companion --workspace laptop --agent you
lean-pkg claims sync example-companion
```

`register --like` copies the sibling's repository URL, owner project,
upstream target and automation note, and reports whether the toolchain and
Mathlib pins match; `--repo` names the new repository instead of the copied one.
The new package shares root namespaces with the old ones, so `audit` reports
`duplicate-namespace`; record why with
`lean-pkg update NAME --namespace-note TEXT` (the finding becomes `info`
once every package sharing the namespace carries a note).

Then produce a fresh lower-tier receipt and a fresh comparator receipt at
the consolidation commit (or a later commit), exactly as for any package:
step 4 above and
[Produce the comparator receipt](how-to.md#produce-the-comparator-receipt).
The old packages need no change. If you want their records to point
forward, `lean-pkg update NAME --append-note TEXT --agent A` adds a note
event to an old record without touching its repository or its evidence.

### Two environment pitfalls

- **File modes in a fresh checkout.** Git records only whether a file is
  executable (`100644` or `100755`), not its full mode. A clone made under
  `umask 002` writes regular files as `0664`, so a verifier that binds the
  source by comparing modes against `0644` refuses a correct tree. Run such
  verifiers in a checkout made under `umask 022`, or compare only the mode
  bits Git records. Do not weaken the check itself.
- **Which `lake`.** If a runtime contract hashes the Lake binary, run the
  receipt with the exact binary of the pinned toolchain (`elan which lake`,
  run in the checkout, prints its path), not through elan's `lake` proxy on
  `PATH`: the proxy is a different file from the binary that does the work,
  so its hash says nothing about the toolchain.
  (`comparator-receipt` records which `lake` it used in
  `toolchain_resolution`; see [Runtime](comparator.md#runtime).)

## What this does not claim

- Partial formal coverage is not a certificate for the whole paper.
- An unchanged proof does not cover a changed application of it.
- The workflow makes reuse auditable and review more focused; it has not been
  shown to make papers faster to write or free of errors.
