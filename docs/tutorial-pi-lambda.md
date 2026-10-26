---
title: "Tutorial: Dynkin's π-λ theorem, end to end"
---

[&larr; lean-pkg documentation](index.md)

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/lean-pkg/lean-pkg?devcontainer_path=.devcontainer/tutorial/devcontainer.json)
Nothing to install: [try it in your browser](#try-it-in-your-browser).

*Lean checks every step; you check the statement.*

This tutorial takes one classical theorem from its textbook statement to a
verified page, on your own machine. It is written for a mathematician who
knows measure theory, has never used lean-pkg, and has read little or no
Lean. Each idea (statement and proof, the kernel, axioms, the comparator,
receipts, the gate, the page) is explained at the step that first needs it.

The theorem is Dynkin's π-λ theorem, with its standard corollary, the
uniqueness of measures. Both are textbook material (Billingsley, *Probability
and Measure*, 3rd ed., Section 3, Theorems 3.2 and 3.3; Durrett, *Probability:
Theory and Examples*, the π-λ theorem and its uniqueness corollary). Both are
already in Mathlib, Lean's mathematics library. That is the point: a
mathematician's real work here is not to re-prove the theorem but to say
precisely, in Lean, *which* theorem was proved, and to let lean-pkg record the
evidence that the proof checks.

Every command below was run on one Linux workstation (16 cores, 2026-10-17,
lean-pkg 0.9.0, and 0.10.0 for the reader's check; Lean `v4.35.0-rc2`) and the outputs are real, trimmed. Home
directories, host names and run ids are replaced by `<home>`, `<host>` and
`<id>`.

| Step | Wall-clock time measured |
|---|---|
| `lean-pkg new` | under a second |
| `lake update && lake exe cache get` (Mathlib's build cache already on the machine) | 3 min 22 s |
| `lake build` of the two proofs | 11 s |
| the comparator receipt | 1 min 26 s |
| the lower-tier receipt (a systemd unit) | 12 s |
| gate, page, publish checklist | a few seconds |
| **total machine time** | **about 5.5 minutes**, plus the time you spend writing the statements |

The first `lake exe cache get` on a new machine also downloads Mathlib's
build cache (several GB); [the quickstart](quickstart.md) measured about 6
minutes for that path. The whole tutorial, including writing and checking the
Lean below, took 15 minutes of wall clock.

You need what [the quickstart](quickstart.md) needs: Linux, `git`, Python
3.11 or newer, [elan](https://github.com/leanprover/elan), and lean-pkg
installed (`pipx install git+https://github.com/lean-pkg/lean-pkg.git`).
`lean-pkg comparator host-check` should end with a `verdict:` line saying the
host can produce comparator receipts.

## Try it in your browser

The **Open in GitHub Codespaces** badge above starts a container in the cloud
with everything this tutorial needs, and VS Code in your browser. It is
defined by [`.devcontainer/tutorial/`](https://github.com/lean-pkg/lean-pkg/tree/main/.devcontainer/tutorial)
in this repository, and any dev-container tool (VS Code's Dev Containers
extension, the `devcontainer` CLI) builds the same container on your own
machine.

**What you get.** Ubuntu 24.04 with Lean v4.35.0 (through elan), lean-pkg
0.12.0 installed from its release tag, `bwrap` for the comparator's sandbox,
systemd for the lower-tier receipt's unit, and the Lean 4 extension. When the
container is created, a script runs step 2 for you:
`lean-pkg new pi-lambda --kind library` in `~/src/pi-lambda`, the toolchain and Mathlib pinned to
v4.35.0 (the scaffold of 0.12.0 writes v4.35.0-rc2), `lake update`,
`lake exe cache get` and a first `lake build`. Nothing of Mathlib is
compiled. `LEAN_PKG_REGISTRY` already points at a throwaway registry inside
the container: skip step 6's `export` and `mkdir`, and run its
`registry init`. Open the package with `code ~/src/pi-lambda` and
continue at step 3. To skip the typing, the finished files of steps 3 and 4
(both Lean files, `Axioms.lean`, `comparator.json`, `claims.yaml`,
`coverage.yaml`) are in `.devcontainer/tutorial/pi-lambda/`:
`cp -r .devcontainer/tutorial/pi-lambda/. ~/src/pi-lambda/`.

**Time to ready.** Measured with the `devcontainer` CLI and Docker on a Linux
workstation (2026-10-17, two runs from an empty Docker cache), not in a
Codespace: building the image took 1 min 40 s and 3 min 2 s, and the
creation script 1 min 19 s and 1 min 36 s (`lake update`, Mathlib's cache,
the first build), so the container was ready in 3 min 49 s and 4 min 24 s.
Then, inside it, the two proofs built in 15 s, `lean-pkg verify` (both
receipts and the gate) took 1 min 43 s, and steps 6 to 8 about 2 minutes in
all. Download speed decides most of the first numbers.

**Disk.** The image is 4.0 GB (Lean's toolchain is 3.1 GB of it), and the
package with Mathlib's prebuilt files takes 7.3 GB more, plus 0.5 GB of
Mathlib's download cache: about 12 GB in all.

**Cost.** A Codespace runs on your GitHub account, not on lean-pkg's. Mathlib
needs a machine of **at least 4 cores and 16 GB of memory** (the
devcontainer asks for it, with 32 GB of disk); a smaller one is not offered.
A personal GitHub Free account includes, each month, 120 core-hours (30 hours
of a 4-core machine) and 15 GB-months of storage; beyond that GitHub bills
the account, so stop the Codespace when you are done and delete it when you
no longer need it (a stopped one still counts toward storage). GitHub's
Codespaces billing page has the current quotas and rates.

**How far it goes.** Steps 2 to 8 all run in the container: the comparator
receipt and the lower-tier receipt from a systemd unit, the gate
(`verified-private`), the page, `lean-pkg receipt check`, and the publish
checklist as a dry run. The container runs privileged, because systemd
needs it and Docker's default seccomp profile stops `bwrap` from creating
its user namespace. All of this was run with Docker from the same
definition the badge uses; it has not yet been run in a live Codespace. The
checklist's `gh` line is a warning there (`gh` is not in the image). What a
Codespace cannot do, or should not:

- **Keep the evidence.** The registry and the package live inside the
  container (`/var/lib/lean-pkg-trial` and the home directory), which a
  rebuild discards. Push the package to a repository of your own if you
  want to keep it; the registry is a throwaway on purpose.
- **Publish.** `lean-pkg publish --yes` makes a real GitHub repository
  public; do that from your own machine, after `lean-pkg public-safety`. A
  receipt made in a Codespace records the container as its host (`h-...`),
  which is a fair record of where it ran.
- **Use a cluster or the lab registry.** There is no Slurm (`receipt slurm`)
  and no lab registry; a Codespace is a single machine, like the
  workstation of this tutorial.
- **Run the comparator in an unprivileged container.** If you rebuild the
  container without `"privileged": true`, `lean-pkg comparator host-check`
  reports `bwrap` cannot create a user namespace and the comparator
  refuses; there is no unsandboxed judge.

## 1. The paper statement

Billingsley's definitions, which we will formalize as written:

- A class 𝒫 of subsets of Ω is a **π-system** if
  A, B ∈ 𝒫 implies A ∩ B ∈ 𝒫.
- A class ℒ is a **λ-system** if (λ1) Ω ∈ ℒ;
  (λ2) A ∈ ℒ implies Aᶜ ∈ ℒ; (λ3)
  A₁, A₂, … ∈ ℒ pairwise disjoint implies
  ⋃ₙ Aₙ ∈ ℒ.

**Theorem A (Dynkin's π-λ theorem).** If 𝒫 is a π-system,
ℒ is a λ-system and 𝒫 ⊆ ℒ, then
σ(𝒫) ⊆ ℒ.

**Theorem B (uniqueness).** Let 𝒫 be a π-system that generates the
σ-algebra of Ω. Two probability measures that agree on 𝒫 are
equal.

The proof of B from A is the one in every textbook: the sets on which the two
measures agree form a λ-system containing 𝒫.

## 2. A new package

```bash
cd ~/src
lean-pkg new pi-lambda --kind library --author "A. Mathematician"
```

```text
created pi-lambda/: package pi-lambda (kind library), library PiLambda, v4.35.0-rc2, Mathlib v4.35.0-rc2; 15 files, git initialized, nothing committed, no registry read or written
author: A. Mathematician (from --author; CITATION.cff, REUSE.toml)
next:
  cd pi-lambda
  lake update && lake exe cache get
  lake build PiLambda.Solution PiLambda.Axioms
  git add -A && git commit -m "new package"
(the first `lake exe cache get` downloads Mathlib's build cache: several GB)
then verify and render the page: docs/quickstart.md (https://lean-pkg.github.io/lean-pkg/quickstart.html)
```

`--kind library` because a textbook theorem is not a paper of yours: the Lean
package is the product (section 10 shows the paper kind, the default).
([`lean-pkg new`](commands/new.md) in the command reference.) Run the
`lake update && lake exe cache get` line now, in the new directory: it fetches
Mathlib at the pinned version and its prebuilt files, so nothing of Mathlib
is compiled on your machine.

The package has two Lean files that matter, and the split between them is the
whole idea of lean-pkg:

- `PiLambda/Challenge.lean` holds the **statements**, each with the proof
  replaced by `sorry` (Lean's placeholder for a missing proof). This is the
  file a reader must check by eye. It is short on purpose.
- `PiLambda/Solution.lean` holds the same statements with **proofs**. Nobody
  needs to read it: Lean checks it.

The scaffold fills both with a toy theorem (`n ≤ 2 * n`). We replace it.

## 3. The statements in Lean, line by line

This is `PiLambda/Challenge.lean`. The `module` and `public` lines are Lean's
module system (the theorems are exported to other files); `import Mathlib`
makes Mathlib's definitions available.

```lean
module

public import Mathlib

public section

open MeasureTheory

/-- Dynkin's π-λ theorem (Billingsley, *Probability and Measure*, Theorem 3.2).
If `P` is a π-system, `L` is a λ-system and `P ⊆ L`, then `σ(P) ⊆ L`. -/
theorem PiLambda.pi_lambda {Ω : Type*} (P L : Set (Set Ω))
    (hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P)
    (hL_univ : Set.univ ∈ L)
    (hL_compl : ∀ A ∈ L, Aᶜ ∈ L)
    (hL_iUnion : ∀ f : ℕ → Set Ω, (∀ i j, i ≠ j → Disjoint (f i) (f j)) →
      (∀ i, f i ∈ L) → (⋃ i, f i) ∈ L)
    (hPL : P ⊆ L) :
    ∀ A, MeasurableSet[MeasurableSpace.generateFrom P] A → A ∈ L := by
  sorry

/-- Uniqueness of measures (Billingsley, *Probability and Measure*, Theorem 3.3).
Two probability measures that agree on a π-system generating the σ-algebra are equal. -/
theorem PiLambda.measure_eq_of_eq_on_pi_system {Ω : Type*} [m : MeasurableSpace Ω]
    (P : Set (Set Ω)) (hgen : m = MeasurableSpace.generateFrom P)
    (hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P)
    (μ ν : Measure Ω) [IsProbabilityMeasure μ] [IsProbabilityMeasure ν]
    (h : ∀ A ∈ P, μ A = ν A) : μ = ν := by
  sorry

end
```

A theorem's hypotheses are the named arguments in parentheses before the
colon; what follows the last colon is the conclusion. Read against the paper:

| Paper | Lean | Comment |
|---|---|---|
| Ω a set | `{Ω : Type*}` | a type plays the role of the ambient set |
| 𝒫, ℒ classes of subsets | `(P L : Set (Set Ω))` | |
| 𝒫 is a π-system | `hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P` | Billingsley's clause, written out (see trap 2) |
| (λ1) Ω ∈ ℒ | `hL_univ : Set.univ ∈ L` | `Set.univ` is all of `Ω` |
| (λ2) closed under complements | `hL_compl : ∀ A ∈ L, Aᶜ ∈ L` | |
| (λ3) countable disjoint unions | `hL_iUnion : ∀ f : ℕ → Set Ω, (∀ i j, i ≠ j → Disjoint (f i) (f j)) → (∀ i, f i ∈ L) → (⋃ i, f i) ∈ L` | a sequence Aₙ is a function `f : ℕ → Set Ω` (see trap 4) |
| 𝒫 ⊆ ℒ | `hPL : P ⊆ L` | |
| σ(𝒫) ⊆ ℒ | `∀ A, MeasurableSet[MeasurableSpace.generateFrom P] A → A ∈ L` | `generateFrom P` is σ(P); "A is measurable for σ(P)" is how Lean says A ∈ σ(𝒫) (see trap 3) |
| the σ-algebra ℱ of Ω | `[m : MeasurableSpace Ω]` | a σ-algebra in Mathlib is a `MeasurableSpace` structure |
| ℱ = σ(𝒫) | `hgen : m = MeasurableSpace.generateFrom P` | |
| μ, ν probability measures | `(μ ν : Measure Ω) [IsProbabilityMeasure μ] [IsProbabilityMeasure ν]` | square brackets: hypotheses Lean finds automatically when the measures are concrete |
| μ = ν on 𝒫 | `h : ∀ A ∈ P, μ A = ν A` | |
| μ = ν | `μ = ν` | equality of measures, that is, on every measurable set |

The comments in `/-- ... -/` are documentation. Lean does not read them, and
neither does any check below: the citation is your claim, not Lean's.

### The traps

Most of the work of formalizing a classical statement is not in the proof. It
is in noticing where the library's definitions differ from the book's. Here
there are five places.

1. **Mathlib's Dynkin system is not Billingsley's λ-system.**
   `MeasurableSpace.DynkinSystem` asks for `∅` (not `Ω`), complements, and
   disjoint unions of sequences. Durrett's λ-system is a third variant: Ω,
   proper differences B ∖ A for A ⊆ B, and increasing
   unions. The three are equivalent, but not by definition. We state the
   theorem with Billingsley's three clauses written out, so that a reader can
   compare it with the book without knowing Mathlib, and the *proof* converts
   to Mathlib's notion (one line: ∅ = Ωᶜ). Had we written
   `(d : MeasurableSpace.DynkinSystem Ω)` in the statement, the reader would
   have to know Mathlib's definition to know what was proved.
2. **`IsPiSystem` does not mean what most books mean.** Mathlib's
   `IsPiSystem C` says: if `s, t ∈ C` and `s ∩ t` is *nonempty*, then
   `s ∩ t ∈ C`. It does not require `C` to be nonempty, and it does not
   require `∅ ∈ C`. Billingsley's clause implies it, not conversely, so a
   theorem stated with `IsPiSystem P` is formally stronger (it assumes less).
   Some books also require a π-system to be nonempty; neither statement here
   needs that. We kept the book's clause; step 5 shows what the comparator
   says if the proof file quietly switches to `IsPiSystem`.
3. **σ(P) is a structure, not a set of sets.** Mathlib's
   `MeasurableSpace.generateFrom P` is the σ-algebra generated by `P`,
   packaged as a `MeasurableSpace Ω`; membership is
   `MeasurableSet[generateFrom P] A`. The square-bracket form names the
   σ-algebra explicitly, which matters because `Ω` may carry another one.
4. **Countable unions are sequences.** "Countably many disjoint sets" becomes
   a function `f : ℕ → Set Ω`. Finite disjoint unions are the case where all
   but finitely many `f i` are `∅`, which is in `L` by (λ1) and (λ2), so
   nothing is lost. We wrote disjointness as `∀ i j, i ≠ j → Disjoint (f i) (f j)`;
   Mathlib's own spelling is `Pairwise (Disjoint on f)`. They mean the same
   thing, and the comparator (step 5) requires the Challenge and the Solution
   to use the same one.
5. **Uniqueness needs a normalization, and σ-finiteness is relative to P.**
   Theorem B is false for finite measures without the total mass: on
   Ω = {1, 2} with 𝒫 = {{1}}, the measures
   δ₁ and δ₁ + δ₂ agree on 𝒫 but not on
   {2}. (Mathlib's `MeasureTheory.ext_of_generate_finite` therefore takes
   `μ univ = ν univ` as a separate hypothesis; "probability measure" builds it
   in.) For infinite measures, σ-finiteness of each measure is not enough: on
   Ω = ℕ the π-system 𝒫 = {[n, ∞) : n ∈ ℕ}
   generates every subset (since {n} = [n,∞) ∖ [n + 1,∞)),
   and counting measure and twice counting measure are both σ-finite and
   agree on 𝒫 (every set in it has measure ∞), yet differ on
   {0}. What the σ-finite version needs is a sequence *in* 𝒫
   of finite measure covering Ω; that is Mathlib's
   `MeasureTheory.ext_of_generateFrom_of_iUnion`. We state the probability
   version, which is the book's.

No check in lean-pkg can find a mistake of this kind for you. If the Lean
statement is not the book's statement, every later step will still pass:
it will certify the Lean statement. This is the one step that is yours.

## 4. The proofs, Lean's kernel, and axioms

`PiLambda/Solution.lean` repeats the two statements exactly and replaces each
`sorry` by a proof. Theorem A is Mathlib's theorem behind
`MeasurableSpace.induction_on_inter`, reached through the Dynkin-system API;
Theorem B is proved from Theorem A, as in the book. (Mathlib also proves B
directly; `MeasureTheory.ext_of_generate_finite` would be a one-line proof.)

```lean
theorem PiLambda.pi_lambda ... := by
  -- The textbook λ-system is a Mathlib `DynkinSystem` (which asks for `∅` instead of `Ω`).
  let d : MeasurableSpace.DynkinSystem Ω :=
    { Has := (· ∈ L)
      has_empty := by simpa using hL_compl _ hL_univ
      has_compl := fun h => hL_compl _ h
      has_iUnion_nat := fun hd hf => hL_iUnion _ (fun _ _ hij => hd hij) hf }
  -- The textbook π-system is a Mathlib `IsPiSystem` (which asks less).
  have hπ : IsPiSystem P := fun A hA B hB _ => hP A hA B hB
  intro A hA
  rw [MeasurableSpace.DynkinSystem.generateFrom_eq hπ] at hA
  exact MeasurableSpace.DynkinSystem.generate_le d (fun t ht => hPL ht) A hA

theorem PiLambda.measure_eq_of_eq_on_pi_system ... := by
  -- The good sets: measurable sets on which μ and ν agree. They form a λ-system containing P.
  have key := PiLambda.pi_lambda P {A | MeasurableSet A ∧ μ A = ν A} hP
    ⟨MeasurableSet.univ, by simp⟩
    (fun A ⟨hA, hμν⟩ => ⟨hA.compl, by rw [prob_compl_eq_one_sub hA, prob_compl_eq_one_sub hA, hμν]⟩)
    (fun f hd hf => ⟨MeasurableSet.iUnion fun i => (hf i).1, by
      rw [measure_iUnion (fun i j hij => hd i j hij) fun i => (hf i).1,
        measure_iUnion (fun i j hij => hd i j hij) fun i => (hf i).1]
      exact tsum_congr fun i => (hf i).2⟩)
    (fun A hA => ⟨hgen ▸ MeasurableSpace.measurableSet_generateFrom hA, h A hA⟩)
  ext A hA
  exact (key A (hgen ▸ hA)).2
```

(The `...` stands for the statement, repeated from step 3.) You do not need
to follow this. What matters is who checks it.

When Lean builds a file, the steps written above (`rw`, `simpa`, `exact`)
are expanded into a complete proof term, and a small program, the
**kernel**, checks that term against the statement using only the rules of
Lean's logic. The kernel is the trusted part; the tactics, Mathlib and your
proof are not trusted, because the kernel re-checks what they produce.

The scaffold's `PiLambda/Axioms.lean` asks Lean which **axioms** each proof
ultimately rests on. Edit its `#print axioms` lines to name the two
theorems, then build:

```bash
lake build PiLambda.Solution PiLambda.Axioms
```

```text
✔ [8931/8932] Built PiLambda.Solution (3.5s)
ℹ [8932/8932] Built PiLambda.Axioms (2.9s)
info: PiLambda/Axioms.lean:6:0: 'PiLambda.pi_lambda' depends on axioms: [propext, Classical.choice, Quot.sound]
info: PiLambda/Axioms.lean:7:0: 'PiLambda.measure_eq_of_eq_on_pi_system' depends on axioms: [propext, Classical.choice, Quot.sound]
Build completed successfully (8932 jobs).
```

These three are the standard axioms of classical mathematics in Lean
(propositional extensionality, the axiom of choice, and quotient soundness).
A proof that still had a gap would list `sorryAx`; a proof that used
`native_decide` or a home-made `axiom` would list that too. "Builds" is not
"proved": this line is what tells them apart.

Now record the rest of the package in its data files (all of them are in the
scaffold; we edit them):

- `comparator.json`: `"theorem_names": ["PiLambda.pi_lambda", "PiLambda.measure_eq_of_eq_on_pi_system"]`
  (the theorems step 5 compares) and the permitted axioms, the three above.
- `claims.yaml`: one entry per theorem, with an id (`pi-lambda/pi-lambda`,
  `pi-lambda/measure-uniqueness`), the declaration, a one-line summary and
  the file. `lean-pkg claims check --file claims.yaml` checks it.
- `coverage.yaml`: what the page will say was formalized; see step 8.

Commit:

```bash
git add -A && git commit -m "pi-lambda: statements, proofs, claims, coverage"
```

## 5. The comparator: same statement, or no receipt

The kernel checks that the Solution's proofs prove the Solution's
statements. Nothing so far checks that the Solution's statements are the ones
you read in the Challenge. A Solution could state a slightly different
theorem under the same name. The **comparator** closes that gap: it compiles
both files, and for every theorem in `comparator.json` it checks that the
Solution's statement is exactly the Challenge's, that the proof uses only the
permitted axioms, and that three independent kernels (Lean's own, and the
NanoDa and con-ron reimplementations) accept it.

What "exactly" means is worth seeing once. Change only the π-system
hypothesis in the Solution to Mathlib's `IsPiSystem P` (trap 2: a
*stronger* theorem, since it assumes less) and adjust the two proof lines
that use it. Everything still builds. Ask the comparator, here through
[`lean-exercise check`](exercise.md), which runs the same judge without a
registry, a receipt or a commit. In a scratch directory beside the package,
with copies of `Challenge.lean` and the edited `Solution.lean`
(`--project` points at the package, whose Mathlib is already built):

```bash
lean-exercise check --challenge Challenge.lean --solution Solution.lean --project ../pi-lambda
```

```text
judging PiLambda.pi_lambda (1/2) ...
judging PiLambda.measure_eq_of_eq_on_pi_system (2/2) ...
statement_mismatch PiLambda.pi_lambda: Challenge and solution theorem statement do not match: 'PiLambda.pi_lambda' (30.0s)
accepted           PiLambda.measure_eq_of_eq_on_pi_system: Your solution is okay! [con-ron, nanoda, lean] (57.2s)
FAIL: 1/2 theorem(s) accepted (sandboxed, leanprover/lean4:v4.35.0-rc2)
```

The comparator does not decide whether one statement implies the other. It
asks whether they are the same, so the Challenge you read is the theorem that
was proved, word for word. (See [Do not weaken the
statement](comparator.md#do-not-weaken-the-statement).) It cannot tell you
whether the Challenge says what Billingsley says. That was step 3.

## 6. Receipts and the gate: `verify`

A **receipt** is a JSON file, committed to the repository, that records one
run of a check: which commit, which toolchain, what was run, and what came
out, with hashes of the logs. It is not a certificate that something is true.
It is a record that something specific was run on specific bytes, so that
anyone can re-run it and compare. lean-pkg has two kinds:

- the **comparator receipt** of step 5 (`.lean-receipts/<commit>-comparator.json`);
- the **lower-tier receipt**: a fresh build from nothing, the `sorry` count
  and the axioms, run by the scaffold's `verify.sh` inside a capped systemd
  user unit, so that the operating system, not your script, reports how it
  ran ([`receipt systemd`](commands/receipt-systemd.md), [the
  details](receipts.md#a-lower-tier-receipt-from-a-systemd-unit)).


First a registry of your own (the registry is the file that records your
packages and their evidence; [the
quickstart](quickstart.md#3-a-registry-of-your-own) keeps it under
`~/.lean-pkg/registry`). For a trial run, a throwaway one is enough:

```bash
export LEAN_PKG_REGISTRY=/tmp/pi-lambda-trial/lean-packages.yaml && mkdir -p /tmp/pi-lambda-trial
lean-pkg registry init
lean-pkg register pi-lambda --url https://github.com/you/pi-lambda --path src/pi-lambda --license-spdx Apache-2.0
```

`--path` is the checkout's path relative to your home directory, and
`--url` is where the repository would live; it need not exist.

Then one command makes both receipts and records the result
([`verify`](commands/verify.md)):

```bash
lean-pkg verify pi-lambda
```

It prints and runs three lean-pkg commands at the commit you just made. (The
outputs shown are those of the same three commands run one by one on the
measured workstation, with lean-pkg 0.9.0; `verify`'s own `verify: N/3 ...`
lines, which name each command before it runs, are left out.) First
the comparator receipt ([`receipt comparator`](commands/receipt-comparator.md),
also the program `comparator-receipt`):

```text
comparator-receipt: stage 01-checkout: ok (dependency checkout per lake-manifest.json)
comparator-receipt: stage 02-cache-get: ok (Mathlib build cache)
comparator-receipt: stage 03-build: ok (build of PiLambda.Challenge, PiLambda.Solution)
...
Running con-ron kernel on solution
con-ron: accepted 15825 declarations (--verified)
con-ron kernel accepts the solution
Running nanoda kernel on solution
nanoda kernel accepts the solution
Running Lean default kernel on solution
Lean default kernel accepts the solution
Your solution is okay!
slot 1-2/2 comparator.json[2 names] done in 60.8s (2/2 done)
comparator-receipt: stage 04-comparator: ok (1 config(s) accepted (toolchain; 2 slot(s)))
comparator-receipt: stage 05-receipt: ok (.lean-receipts/ff6e3c27425b-comparator.json)
```

Then the lower-tier receipt at the same commit ([`receipt systemd`](commands/receipt-systemd.md)),
with its log and unit output in a work directory outside the checkout:

```text
lean-pkg: receipt systemd: started pi-lambda-verify.service; waiting for it to finish
receipt systemd: ok, job systemd-user:pi-lambda-verify.service:<id>@h-<host label> at commit ff6e3c27425b; 0 redaction(s)
  ✓ lake cache: a fresh empty LAKE_CACHE_DIR, LAKE_ARTIFACT_CACHE=false
  ✓ unit: pi-lambda-verify.service invocation <id> on h-<host label>: Result=success, exited 0
  · resources: runtime 00:00:11.18, CPU 00:00:14.55, memory peak 758.9 MiB; caps MemoryMax 8.0 GiB, MemorySwapMax 0 B, CPUQuota 400%, RuntimeMax 4h
  ✓ seal: job stdout verify-log-sha256 = sha256(raw verify log) 503a3fdf54f0…
  ✓ manifest: pre = post = lake-manifest.json at ff6e3c27425b (42713b6f1e5a…)
  ✓ stderr: empty
  ✓ lake: no root-package module shown Reused, Replayed or Unpacked in the build log
  ✓ log: `commit:` line = --commit ff6e3c27425b
  ✓ public-safety: no blocking hit after 0 redaction(s)
  receipt     .lean-receipts/ff6e3c27425b-systemd-user-pi-lambda-verify.service-<id>-h-<host label>.json
```

The `lake:` line matters: the package's own modules were compiled in this run,
not restored from a cache, so the `sorry` count and the axioms in the log come
from this build. Last the gate ([`gate`](commands/gate.md)): the step that reads both receipts,
re-checks them against the committed bytes, and records the package as
verified in your registry. `verify` first moves the new package from
`active` to `gate-pending`, the move the gate expects:

```text
✓ claims.yaml at ff6e3c27425b: schema ok (2 claims), declarations found
✓ report_sha256 = sha256 of ff6e3c27425b-systemd-user-pi-lambda-verify.service-<id>-h-<host label>-verify.redacted.log (beside the receipt, tracked at HEAD)
pi-lambda: gate-pending -> verified-private (commit ff6e3c27425b) with comparator evidence .lean-receipts/ff6e3c27425b-comparator.json
```

Both receipts name the same commit because nothing is committed between
them: `verify` runs the comparator first (it refuses any untracked file in the
checkout) and the lower-tier run second (it allows the receipts under
`.lean-receipts/`). By hand, committing the comparator receipt before the
lower-tier run binds the two to different commits, and the gate refuses them
(`both tiers must bind to one commit`). The comparator needs no host
configuration on your own machine: with no Slurm installed and
`COMPARATOR_RECEIPT_ALLOWED_HOSTS` unset, it runs as the machine running the
command, recorded by its label `h-...`. Commit both receipts:

```bash
git add .lean-receipts && git commit -m "evidence: receipts"
```

`verified-private`: verified, and not yet public. The gate binds the package
to commit `ff6e3c27425b`. The receipts were committed in a later commit; that
is allowed, because a commit that adds only receipts does not change what was
built ([the evidence-only descendant rule](receipts.md#evidence-binding-the-evidence-only-descendant-rule)).

## 7. The page

The **page** is a section of `README.md` that lean-pkg renders from the data:
`coverage.yaml` (what you say was formalized), `claims.yaml`, the claims
ledger, and the receipts. Nothing on it is typed by hand except the titles,
the statements in words, the notes and the one-line summary in
`coverage.yaml`.

Our `coverage.yaml`:

```yaml
schema: coverage/1
package: pi-lambda
kind: library
summary: "Dynkin's pi-lambda theorem and the uniqueness of probability measures, in textbook form, each compared against its trusted statement."
rows:
  - title: "Dynkin's pi-lambda theorem: sigma(P) is contained in every lambda-system containing the pi-system P"
    claims: [pi-lambda/pi-lambda]
    note: "Billingsley, Probability and Measure, 3rd ed., Theorem 3.2; textbook definitions of pi- and lambda-system"
    statement: |
      If $\mathcal{P}$ is a $\pi$-system and $\mathcal{L}$ is a $\lambda$-system,
      then $\mathcal{P} \subset \mathcal{L}$ implies $\sigma(\mathcal{P}) \subset \mathcal{L}$.
  - title: "Uniqueness: probability measures agreeing on a generating pi-system are equal"
    claims: [pi-lambda/measure-uniqueness]
    note: "Billingsley, Probability and Measure, 3rd ed., Theorem 3.3; proved from the pi-lambda theorem"
    statement: |
      Let $\mathcal{P}$ be a $\pi$-system and $\mu_1$, $\mu_2$ probability measures
      on $\sigma(\mathcal{P})$. If $\mu_1 = \mu_2$ on $\mathcal{P}$, then
      $\mu_1 = \mu_2$ on $\sigma(\mathcal{P})$.
```

Why `kind: library` for a textbook theorem: the other kind, `paper`, maps
the numbered results of a *posted manuscript* (an arXiv version, or a public
repository commit with the file's hash) so that a reader can check the
numbers against a fixed text. A textbook has no such anchor, so the honest
kind is `library`, with the book cited in the notes. Each `statement` is
the result in TeX, transcribed from the book: the page shows it as source,
under **In words**, beside the Lean statement, so a reader can compare the
two without the book at hand. It is optional, and nothing checks it against
the book. Section 9 shows the `paper` version.

```bash
lean-pkg coverage check pi-lambda
lean-pkg page pi-lambda --write
git add README.md && git commit -m "README: verification page"
lean-pkg page pi-lambda --check
```

```text
pi-lambda: coverage: OK; library; 2 main result(s); no manuscript
pi-lambda: updated 1 field(s)
  docs_descendants: None -> True
pi-lambda: wrote the page block into <home>/.../pi-lambda/README.md — not committed; commit it yourself
pi-lambda: turned on the docs-only exception (`lean-pkg update pi-lambda --accept-docs-descendants --reason ...`): ...
pi-lambda: page: current (README.md)
```

([`coverage`](commands/coverage.md), [`page`](commands/page.md).) The first
`page --write` turns on the docs-only exception (the `updated` lines, one
registry event): a documentation-only commit (this README) keeps the
receipts bound to the gated commit. An excerpt of the
rendered page today:

````markdown
## Verification at a glance

**How to read this page.** Lean has checked every step of the proofs listed here. Each result is exactly its Lean statement: read the statements below to see what is proved. The titles, the words and the notes are the authors' description, which Lean does not check.

Dynkin's pi-lambda theorem and the uniqueness of probability measures, in textbook form, each compared against its trusted statement.

- Main results: 2; verified: 2 of 2.

## Main results

### Dynkin's pi-lambda theorem: sigma(P) is contained in every lambda-system containing the pi-system P

**Verified.** Lean accepts the proof, and lean-pkg's record shows every check of this result passed (the build, and the statement comparison).

**In words.**

```latex
If $\mathcal{P}$ is a $\pi$-system and $\mathcal{L}$ is a $\lambda$-system,
then $\mathcal{P} \subset \mathcal{L}$ implies $\sigma(\mathcal{P}) \subset \mathcal{L}$.
```

**In Lean.** `PiLambda.pi_lambda` in [PiLambda/Challenge.lean](https://github.com/you/pi-lambda/blob/ff6e3c27425b1dc921921aa75cc7a2ee1b796e05/PiLambda/Challenge.lean): the statement the proof was checked against (the proof is in a separate file, and a second checker confirmed it proves exactly this).

```lean
theorem PiLambda.pi_lambda {Ω : Type*} (P L : Set (Set Ω))
    (hP : ∀ A ∈ P, ∀ B ∈ P, A ∩ B ∈ P)
    (hL_univ : Set.univ ∈ L)
    (hL_compl : ∀ A ∈ L, Aᶜ ∈ L)
    (hL_iUnion : ∀ f : ℕ → Set Ω, (∀ i j, i ≠ j → Disjoint (f i) (f j)) →
      (∀ i, f i ∈ L) → (⋃ i, f i) ∈ L)
    (hPL : P ⊆ L) :
    ∀ A, MeasurableSet[MeasurableSpace.generateFrom P] A → A ∈ L
```

**Axioms.** At most `Classical.choice`, `Quot.sound`, `propext` (what the build check records for the whole package).

**Note.** Billingsley, Probability and Measure, 3rd ed., Theorem 3.2; textbook definitions of pi- and lambda-system

### Uniqueness: probability measures agreeing on a generating pi-system are equal

...
````

The block of each result is what a reader compares with the book: the
title, the words and the note are yours, the Lean statement is what was
proved. The page
goes on with *What the checks establish* (in plain words: every proof is
checked by Lean, the statements shown are the ones proved, and no tool
checks that a title describes its statement), then *How this was checked*
(the claims ledger, the two receipts with their SHA-256, and the commands to
rebuild the gated commit), *Citation*, *License* and *Acknowledgments*.
[The verification page](page.md) explains every section, and the
[trust model](trust-model.md) says what a reader may and may not conclude.

### A reader's check

A referee who clones the repository does not need your registry. Since
lean-pkg 0.10.0, `lean-pkg receipt check` re-checks the committed receipts
against the committed bytes, with no registry, no network and no Lean run:

```bash
lean-pkg receipt check --repo pi-lambda --commit ff6e3c27425b --docs-descendants
```

```text
receipt check: PASS pi-lambda at ff6e3c27425b (tier comparator; 14 passed, 0 skipped)
  PASS receipts: 1 lower-tier and 1 comparator receipt(s) bind ff6e3c27425b
  PASS binding: docs-only descendant of ff6e3c27425b (1 non-build file(s): README.md; opted in) (accepted with --docs-descendants)
  ...
  PASS coverage: coverage.yaml: 2 row(s); every compared theorem is the declaration of a claim a row names (R11)
  PASS page: trust boundary: sorry count, axioms and comparator counts as the receipts record them
```

Rebuilding and re-running the comparator are the remaining steps of [Re-verify
a page yourself](trust-model.md#re-verify-a-page-yourself). And the referee,
like you, still reads `Challenge.lean` against the book.

## 8. Before publishing: the checklist

Nothing so far has left your machine. [`publish`](commands/publish.md)
lists what a public release would still need, and writes nothing (`--yes`
would make the repository public):

```bash
lean-pkg claims sync pi-lambda
lean-pkg publish pi-lambda
```

```text
publish checklist for pi-lambda (<home>/.../pi-lambda) — dry run, nothing written:
  ✓  status verified-private
  ✓  reuse lint passed
  ✓  gate receipt bound to HEAD 7d1a2365638b (docs-only descendant of ff6e3c27425b (1 non-build file(s): README.md; opted in by the registry record))
  ✓  comparator PASS bound to HEAD 7d1a2365638b (1 config(s): config and log bytes match the receipt, PASS line present; not re-run)
  ✓  claims ledger ready: 2 required claim(s) verified (sha256:7b772a33c624…)
  ✗  no origin/main in local refs — push the branch first (local refs; nothing fetched)
  ✗  no consent recorded (`lean-pkg record pi-lambda consent-recorded --field who=W --field role=R --field pointer=P`)
  ✓  claims synced at 7d1a2365638b
  ✓  page block in README.md is current (`lean-pkg page pi-lambda --check`)
  ...
not ready: the checklist has failures
```

The remaining ✗ lines are about publishing, not about the mathematics: a
pushed GitHub repository and a recorded consent of the author. (The REUSE
licence lint passes because `lean-pkg new` wrote `REUSE.toml` and
`LICENSES/`, which cover every file, the receipts included.) Run `lean-pkg public-safety pi-lambda` too
([`public-safety`](commands/public-safety.md)): on this run it flagged the
comparator receipt's `host`, `host_policy` and `job_id` fields, which record
the machine's name. Decide about those before anything is public; [Scan for
private strings](how-to.md#scan-for-private-strings-before-publishing) and
[the publish checklist](how-to.md#get-listed-publish-checklist-reservoir-palomar)
take it from here. This tutorial stops at the checklist.

## 9. The other track: a paper

lean-pkg leads to one of [two products](index.md#choose-your-track): a
*library* page, as here, or a *paper* page. A paper author differs in one
file. If Theorems A and B were Theorems 3.2 and 3.3 of your posted arXiv
paper, `coverage.yaml` would be:

```yaml
schema: coverage/1
package: pi-lambda
manuscript:
  title: "Your paper's title"
  version: "arXiv:<id>v<N>"
summary: "Theorems 3.2 and 3.3 are formalized with the paper's hypotheses."
rows:
  - paper:
      number: "Theorem 3.2"
      title: "Dynkin's pi-lambda theorem"
      statement: |
        If $\mathcal{P}$ is a $\pi$-system and $\mathcal{L}$ is a $\lambda$-system,
        then $\mathcal{P} \subset \mathcal{L}$ implies $\sigma(\mathcal{P}) \subset \mathcal{L}$.
    class: same-assumptions
    claims: [pi-lambda/pi-lambda]
  - paper: {number: "Theorem 3.3", title: "Uniqueness of probability measures"}
    class: same-assumptions
    claims: [pi-lambda/measure-uniqueness]
nothing_excluded: {reason: "Section 3 has no other numbered results"}
```

The page then shows *The paper's results in Lean* (each numbered result,
with the paper's statement, `paper.statement` copied from the manuscript,
under **In the paper** beside its Lean statement, and its hypotheses in
words) and a *Not formalized* section in place of *Main results*. `same-assumptions` says the Lean statement carries
the paper's hypotheses; a result proved under an extra hypothesis is
`inputs-supplied`, with the hypothesis named, and an unformalized one is
`outside`. Everything else (Challenge, Solution, receipts, gate) is the same.
`lean-pkg new` writes this kind by default (`--kind paper`, which this
tutorial overrode in step 2): a `kind: paper` file with
`manuscript.version: unposted` and `FILL-ME` markers, refused by
`coverage check` until you fill them in, then bound to the posted version
by `formalization anchor`:
`lean-pkg formalization anchor NAME --arxiv <id>v<N> ... --write`.
[Develop a paper with its Lean companion](paper-companion.md) is the paper
track in full, and [A library with no manuscript](page.md#a-library-with-no-manuscript)
the library rules.

## What you checked, and what Lean checked

| Question | Who answers it | Where it is recorded |
|---|---|---|
| Is the Lean statement Billingsley's theorem? | you, by reading `Challenge.lean` against the book (step 3) | nowhere: the page says so |
| Does the proof prove that statement? | the kernel, three times | the comparator receipt |
| Is the proved statement the one in `Challenge.lean`? | the comparator | the comparator receipt |
| No `sorry`, only standard axioms, a fresh build? | `verify.sh` in a systemd unit | the lower-tier receipt |
| Do the receipts match the committed bytes? | the gate | the registry |
| What does the page claim? | `coverage.yaml`, rendered | the README block |

Lean checks every step; you check the statement.
