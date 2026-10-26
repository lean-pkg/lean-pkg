---
title: Quickstart
---

[&larr; lean-pkg documentation](index.md)

*From `pip install` to a verified page, on your own machine, with no lab account.*

This is the shortest honest path. It needs Linux (the comparator sandbox and
the local receipt use Linux features), `git`, Python 3.11 or newer, and
[elan](https://github.com/leanprover/elan) with network access for the first
Lean and Mathlib downloads. No GitHub account, cluster or AI agent is needed
until you publish.

Measured on one Linux workstation (16 cores, 2026-10-17), from a fresh home
directory with elan already installed: about **6 minutes** with Mathlib
the first time (4.6 of them the first `lake update` and `lake exe cache get`),
under 4 minutes once Mathlib's cache is on the machine, and under one minute
with `--no-mathlib`. Installing elan and the Lean toolchain
adds a few minutes more on a new machine.

Every command below is one you run; `verify` runs three more for you and
prints each one first, so nothing is hidden. Steps marked *optional* are only
for a lab or a cluster.

The same path with a real theorem, every step explained for a mathematician:
[Tutorial: Dynkin's π-λ theorem, end to end](tutorial-pi-lambda.md).

## 1. Install

```bash
pipx install git+https://github.com/lean-pkg/lean-pkg.git     # or: uv tool install ..., pip install ...
lean-pkg comparator host-check                                # exit 0: this machine can judge
```

The host check ends with a line such as
`verdict: this host can produce comparator receipts with: toolchain`. If it
fails, read its `fail` lines first; [Checking a host
first](comparator.md#checking-a-host-first) explains each one.

## 2. A new package

```bash
cd ~/src
lean-pkg new my-proof --kind library
```

```text
created my-proof/: package my-proof (kind library), library MyProof, v4.35.0-rc2, Mathlib v4.35.0-rc2; 15 files, git initialized, nothing committed, no registry read or written
author: Ada Lovelace (from git config user.name; CITATION.cff, REUSE.toml)
next:
  cd my-proof
  lake update && lake exe cache get
  lake build MyProof.Solution MyProof.Axioms
  git add -A && git commit -m "new package"
```

`lean-pkg new` writes the [getting-started project](getting-started.md#start-from-scratch)
(a trusted statement in `MyProof/Challenge.lean`, its proof in
`MyProof/Solution.lean`, `comparator.json`) plus the files the later steps
read: `MyProof/Axioms.lean`, `claims.yaml`, `coverage.yaml`, `CITATION.cff`
(the author is `git config user.name` unless you pass `--author`; a name
with no space gets a warning, since it looks like a login, so fix it then), an
Apache-2.0 `LICENSE` (`--license MIT` for MIT), `REUSE.toml` and
`LICENSES/` (so the REUSE licence lint passes before you publish), a
`README.md` with the lean-pkg credit, and `verify.sh`. It writes into a new
or empty directory only, and it touches no registry. Replace the example
theorem later; keep the files in step. Run the four commands it prints:

```bash
cd my-proof
lake update && lake exe cache get
lake build MyProof.Solution MyProof.Axioms
git add -A && git commit -m "new package"
```

The build ends with
`info: MyProof/Axioms.lean:6:0: 'MyProof.two_mul_le' depends on axioms: [propext, Quot.sound]`
and `Build completed successfully`. The first `lake exe cache get` downloads
Mathlib's build cache, several GB.

**A paper, or a library?** This walk-through passes `--kind library`: the
example theorem belongs to no paper, so the Lean package is the product and
the page in step 6 renders from the `coverage.yaml` as written. Without
`--kind`, `lean-pkg new` writes the paper track (`--kind paper`, the
default): the paper is the product, and its `coverage.yaml` is
`kind: paper` with `manuscript.version: unposted` and `FILL-ME` markers in
place of the title and the rows. Steps 3 to 5 (registry, receipts, gate) are the
same; `coverage check` and the page in step 6 refuse the file until you
write the paper's title and one row per numbered result, and bind it to the
posted version:

```bash
lean-pkg formalization anchor my-proof --arxiv <id>v<N> --from-file E-PRINT --compare-tex PAPER.tex --write
```

[Develop a paper with its Lean companion](paper-companion.md) is that track
in full.

**No Mathlib?** `lean-pkg new my-proof --kind library --no-mathlib` writes the same project
in core Lean: `lake update` then fetches nothing and the build takes seconds.
`verify` (step 4) sees the `mathlib-rev: none` line of its `verify.sh` and
tells the comparator so (`COMPARATOR_RECEIPT_NO_MATHLIB=1`, which you set
yourself when you run `lean-pkg receipt comparator` by hand). Palomar expects
statements in Mathlib's vocabulary, so keep Mathlib for anything you will
submit there.

## 3. A registry of your own

The registry is a YAML file that records your packages and their evidence.
Keep it private; it is yours, not the lab's.

```bash
mkdir -p ~/.lean-pkg/registry
lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml registry init
lean-pkg register my-proof --url https://github.com/you/my-proof --path src/my-proof --license-spdx Apache-2.0
```

```text
my-proof: registered (status=active, visibility=private, toolchain=leanprover/lean4:v4.35.0-rc2, pin=065356127b)
```

`~/.lean-pkg/registry` is the default location, so later commands need no
`--registry`. `--path` is your checkout's path relative to your home
directory. `--url` is where the repository will live; it may not exist yet,
and the page uses it for its clone command. Putting the registry under Git
(`git -C ~/.lean-pkg/registry init`) is recommended but not required here.

## 4. Verify

```bash
lean-pkg verify my-proof
```

`verify` runs three lean-pkg commands at the commit you just made, in this
order, printing each one before it runs:

1. the **comparator receipt** (`lean-pkg receipt comparator`, also installed
   as the program `comparator-receipt`): the trusted statements in
   `Challenge.lean` against the proofs in `Solution.lean`, replayed in
   independent kernels;
2. the **lower-tier receipt** (`lean-pkg receipt systemd ... --run -- bash verify.sh LOG`):
   a fresh build, the `sorry` count and the axioms, run by `verify.sh` in a
   capped systemd user unit, with its log outside the checkout;
3. the **gate** (`lean-pkg gate my-proof --receipt ... --comparator ...`),
   after moving the package from `active` to `gate-pending`.

```text
verify: 1/3 comparator receipt: lean-pkg receipt comparator --repo .../my-proof
...
Your solution is okay!
comparator-receipt: stage 04-comparator: ok (1 config(s) accepted (toolchain; 1 slot(s)))
...
verify: 2/3 lower-tier receipt: lean-pkg receipt systemd --unit my-proof-verify --receipt-commit a0a4fe68e46c... --run -- bash verify.sh .../verify.log
  ✓ unit: my-proof-verify.service invocation <id> on h-<host label>: Result=success, exited 0
...
verify: 3/3 gate: lean-pkg gate my-proof --receipt .../.lean-receipts/a0a4fe68e46c-systemd-user-my-proof-verify.service-<id>-h-<host label>.json --comparator .../.lean-receipts/a0a4fe68e46c-comparator.json
✓ claims.yaml at a0a4fe68e46c: schema ok (1 claims), declarations found
my-proof: gate-pending -> verified-private (commit a0a4fe68e46c) with comparator evidence .lean-receipts/a0a4fe68e46c-comparator.json
verify my-proof: verified-private at a0a4fe68e46c: lower-tier .lean-receipts/a0a4fe68e46c-systemd-user-....json, comparator .lean-receipts/a0a4fe68e46c-comparator.json
next:
  git -C .../my-proof add .lean-receipts && git -C .../my-proof commit -m "evidence: receipts at a0a4fe68e46c"
  lean-pkg page my-proof --write
```

Both receipts must name the same commit, so `verify` commits nothing between
them (the comparator runs first: it refuses any untracked file in the
checkout, while the lower-tier run allows the receipts in `.lean-receipts/`).
It writes nothing the three commands would not write, and it commits nothing
in your checkout. On your own machine the comparator needs no
configuration: with `COMPARATOR_RECEIPT_ALLOWED_HOSTS` unset and no Slurm
installed, it runs as "the machine running the command", recorded by its
label `h-...`, never its name. A package made with `--no-mathlib` is
recognised from the `mathlib-rev: none` line `verify.sh` carries. Stopped
halfway? Run `verify` again: a receipt of the same commit is reused.
`lean-pkg verify my-proof --dry-run` prints the steps and runs nothing.

Commit the receipts, as it says:

```bash
git add .lean-receipts && git commit -m "evidence: receipts"
```

A commit that adds only receipts keeps them bound to the verified commit
([the evidence-only descendant rule](receipts.md#evidence-binding-the-evidence-only-descendant-rule)).

## 5. The page

The page is a section of `README.md` rendered from `coverage.yaml`, the
claims ledger and the receipts.

```bash
lean-pkg page my-proof --write
git add README.md && git commit -m "README: verification page"
lean-pkg page my-proof --check
```

```text
my-proof: updated 1 field(s)
  docs_descendants: None -> True
my-proof: wrote the page block into .../my-proof/README.md — not committed; commit it yourself
my-proof: turned on the docs-only exception (`lean-pkg update my-proof --accept-docs-descendants --reason ...`): the record now says docs_descendants: true, so a commit after a0a4fe68e46c that changes only non-build files, such as this README, keeps the receipts bound to a0a4fe68e46c; ...
my-proof: page: current (README.md)
```

Committing the README moves `HEAD` past the verified commit. The first
`page --write` therefore records the docs-only exception for you (the
`update` line above, its own registry event): a later commit that changes no
build input keeps the receipts bound. A record that already says either way
is left as it is.

The README now says, among other things:

````text
## Main results

### n &lt;= 2 \* n for every natural number n

**Verified.** Lean accepts the proof, and lean-pkg's record shows every check of this result passed (the build, and the statement comparison).

**In Lean.** `MyProof.two_mul_le` in MyProof/Challenge.lean (linked at the gated commit): the statement the proof was checked against (...).

```lean
theorem MyProof.two_mul_le (n : Nat) : n ≤ 2 * n
```
````

after a first section that tells the reader what to compare, and before what
the checks establish, how this was checked (the receipts, their hashes and
the reproduce commands), the citation, the license and the credits. Run `lean-pkg page my-proof --check`
in CI. [The verification page](page.md) explains every section, and
`--site docs --html docs/verification.html` writes the same page for a
documentation site.

## An existing Lean project

Your project needs, at one commit, what `lean-pkg new` writes:

1. The Challenge/Solution layout and a `comparator.json`
   ([Getting started](getting-started.md), worked example step 1). A project
   written without Lean's module system: `lean-pkg palomar port-modules NAME`
   after registering. A project with many results:
   `lean-pkg comparator config --from-claims NAME` drafts the configuration from `claims.yaml`.
2. `claims.yaml` (check it with `lean-pkg claims check --file claims.yaml`),
   `coverage.yaml` (`kind: library`, or a paper's rows; see [The
   verification page](page.md#coverageyaml)), `CITATION.cff`, a license
   file, and REUSE information (`REUSE.toml`, `LICENSES/`) for the
   licence lint.
3. A verify script. The quickest is to copy `verify.sh` and `MOD/Axioms.lean`
   from a scratch scaffold
   (`lean-pkg new scratch-copy --kind library --dir ../scratch-copy`) and edit `MODULES` and the `#print axioms` lines to name
   your modules and theorems.

Then steps 3 to 5 above, with your package name. [Add the comparator tier to
an existing package](existing-package-comparator.md) walks through step 1 in
detail.

## Optional: lab and cluster setups

None of these are needed for the page above.

- *A shared, Git-backed registry* for a group: [Getting
  started](getting-started.md#recommended-layout-one-registry-many-projects).
- *Leases* (`lean-pkg lease claim`, `lease release`) when several people or agents work on
  one package; a single author can skip them.
- *Slurm receipts* from a cluster (`lean-pkg receipt slurm --template`):
  [Receipts and trust](receipts.md#a-lower-tier-receipt-from-a-slurm-job).
  The Easley acknowledgment on the page needs a Slurm receipt from that
  cluster ([the Easley rule](page.md#the-easley-acknowledgment)).
- *Making the repository public*: `lean-pkg publish my-proof` (the read-only
  checklist), then `lean-pkg publish my-proof --yes`
  ([How to](how-to.md#get-listed-publish-checklist-reservoir-palomar)).
  `--yes` flips GitHub visibility, which cannot be undone in practice.
- *The steps of `verify` by hand*, for a cluster or a custom check:
  [Receipts and trust](receipts.md) and [The comparator tier](comparator.md).
