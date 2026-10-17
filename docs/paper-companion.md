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
  progress/                     progress log, wave notes
  lean/                         the package: registered local_path
    lakefile.toml  lean-toolchain  lake-manifest.json  claims.yaml
    coverage.yaml               the paper-to-Lean map (below)
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

Record how each numbered result of the paper relates to the companion in
`coverage.yaml` (schema `coverage/1`) in the package directory: one row per
paper result, with its correspondence class (`same-assumptions`,
`inputs-supplied` with the named inputs, or `outside`) and the claim ids that
formalize it. Do not keep a hand-written coverage table:
`lean-pkg coverage check NAME` validates the file, and `lean-pkg page NAME --write` renders the
map, the "Not formalized" list and the status into the companion's README
from it, the claims ledger and the receipts. A row is called *verified* only
when the ledger closes its claims. See [The verification page](page.md) for
the schema and an example.

Update the file at every checkpoint, and run `lean-pkg page NAME --check`:
the map is what tells a reader where the certificate stops.

### Anchoring to an arXiv version

A companion is released against one posted version of the paper, and a reader
should be able to check that the TeX the claims were written against is that
version's source. `lean-pkg formalization anchor` does the comparison and
records it:

```bash
# offline: the e-print you downloaded from https://arxiv.org/e-print/<id>v<N>
lean-pkg formalization anchor NAME --arxiv <id>v<N> --from-file <id>v<N>.tar.gz --compare-tex paper.tex
# or let lean-pkg download it (the only network access; HTTPS, 60 s timeout, 64 MiB cap)
lean-pkg formalization anchor NAME --arxiv <id>v<N> --fetch --compare-tex paper.tex
```

The version is required: an unversioned id is whatever arXiv serves today.
The e-print is read in memory, never unpacked to disk. A gzip'd tar, a gzip'd
single `.tex` and a plain tar are read; a PDF-only submission is refused (it
has no source to anchor to; pin `manuscript.version` by hand). An archive with
an absolute path, a `..` component, a link or a device member, or more members
or bytes than the caps allow, is refused whole.

The command prints the e-print's SHA-256, its main `.tex` (the file with
`\documentclass`; `--main-tex PATH` when several have one), how it relates to
the manuscript `coverage.yaml` is bound to, and, with `--compare-tex`, whether
your `.tex` is identical after normalising line endings, trailing whitespace
and trailing blank lines, or the differing lines. Without `--write` nothing is
written.

`--write` writes three files and commits none of them:

- `data/source-baselines/arXiv-<id>v<N>.json` (`source-baseline/1`,
  deterministic JSON): the id and version; where the bytes came from (the
  file name, or the URL and `retrieved_at` when fetched); the e-print's
  SHA-256, size, format and member count; the SHA-256 of every `.tex`, `.bib`,
  `.bbl` and `.sty`; `main_tex`; the comparison (`identical`,
  `differing_lines`, `accepted_diff`); and, when fetched, `arxiv_api`
  `{published, updated}` as the arXiv API reports them (submission times:
  arXiv's announce date is not in the API, so it is not recorded);
- `coverage.yaml`: `manuscript.version: "arXiv:<id>v<N>"` and
  `manuscript.eprint_sha256`, every other line (comments included) kept;
  `coverage check` compares the hash with the committed record;
- `formalization.yaml`: the `id` of the source entry that names the paper
  (or the one `formalizes` entry without an id).

It refuses an anchor nothing ties to the ledger: `--write` needs
`coverage.yaml` already bound to that version, a bound
`{repo, commit, sha256}` whose SHA-256 is one of the e-print's files, or
`--compare-tex` showing the files identical. When they differ (a changed
`\bibliographystyle` line, say), read the differences and pass
`--accept-diff "REASON"`; the reason goes into the record. A second record for
the same version with other e-print bytes is refused: posted versions do not
change. The registry is never written; when its `source_paper.arxiv` differs,
the command prints the `lean-pkg update NAME --source-paper-arxiv <id>v<N>` to
run.

Moving to a later version (`v2` after `v1`) is a re-anchor: the old record
stays, the files are re-pointed, and the command prints the claims-ledger
step, `lean-pkg claims rebase NAME --from-tex OLD.tex --to-tex NEW.tex`, so
theorem numbers and labels follow the new text.

## 7. Finish

At submission the deliverable is:

- the manuscript;
- the companion repository at a tagged commit, with exact toolchain and
  Mathlib pins and the receipts in `.lean-receipts/`;
- the claims file and `lean-pkg claims ledger` output for the gated commit;
- `coverage.yaml` and the README section `lean-pkg page` renders from it,
  stating plainly which results are formal, which are conditional on named
  hypotheses, and which are not formalized.

Publishing the companion follows the usual path: [scan for private strings](how-to.md#scan-for-private-strings-before-publishing),
the comparator tier, and `promote`.

### Credits

`lean-pkg page NAME --write` writes both credits below into the README's
Acknowledgments (the Easley sentence only with a backing Slurm receipt), and
`--check` fails when the lean-pkg credit is missing or an Easley sentence has
no backing ([The verification page](page.md#the-easley-acknowledgment)). By
hand: give the companion's README an `## Acknowledgments` (or `## Tools`) section
that names the tool used to develop and verify it, with the repository link
and the concept DOI (which always resolves to the latest release):

```markdown
Developed and verified with [lean-pkg](https://github.com/lean-pkg/lean-pkg) ([doi:10.5281/zenodo.23272098](https://doi.org/10.5281/zenodo.23272098)).
```

To name the exact version, add it with its version DOI, taken from lean-pkg's
[CITATION.cff](../CITATION.cff) (`identifiers`) or the README's
[Citation](../README.md#citation) section for the version that produced the
receipts (`lean-pkg --version`). The compute acknowledgment below goes in the
same section.

### Acknowledge the compute

When the receipts show the gates ran on the Auburn University Easley Cluster
(a `receipt slurm` receipt in `.lean-receipts/` whose job ran on that
cluster), put the official sentence, with the cluster name linked, in the
companion's README:

```markdown
This work was completed in part with resources provided by the [Auburn University Easley Cluster](https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html).
```

Keep the wording verbatim ("in part" unless Easley was the only compute
used). A companion whose receipts came from other hosts carries no such
sentence: the acknowledgment is a statement about where the evidence was
produced. Other clusters have their own required wording.

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
