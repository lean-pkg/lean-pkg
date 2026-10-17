---
title: lean-pkg
---

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23272098.svg)](https://doi.org/10.5281/zenodo.23272098)

<img src="assets/logo-256.png" width="140" height="140" alt="lean-pkg logo: a secretary bird in profile with quill-like crest feathers" style="float:right;margin:0 0 1rem 1.5rem">

*What was verified, against which statement, at which commit.* **A registry and trust gate for Lean 4 formalization packages.** One
append-only registry says which packages exist, who is working on each one,
and what evidence binds a package to a verified commit. Nothing becomes
public without two receipts, and the second one is re-derived from bytes.

Source and issues: <https://github.com/lean-pkg/lean-pkg>. License:
Apache-2.0. Machine-readable entry: [llms.txt](llms.txt).

**Start here:** [Tutorial: Dynkin's π-λ theorem, end to end](tutorial-pi-lambda.md)
takes one textbook theorem from its statement to a verified page on your own
machine, and explains each idea (statement and proof, the kernel, axioms, the
comparator, receipts, the gate, the page) where it is first needed. *Lean
checks every step; you check the statement.*

## Choose your track

lean-pkg leads to one of two final products, and the page certifies each one:

- **Track P, a paper**: an arXiv or journal paper whose results are formally
  verified. The page maps every numbered result of the paper to Lean, says
  which are not formalized, and is anchored to a posted version. Start with
  [Develop a paper with its Lean companion](paper-companion.md).
- **Track L, a Lean library**: a reusable library, published on Reservoir or
  upstreamed to Mathlib. The page certifies the library's main results
  (`kind: library`). Start with [A library with no manuscript](page.md#a-library-with-no-manuscript).

[Two tracks](design/two-tracks.md) maps the commands to each track, gives each
its success gate, and lists what is still missing.

## Choose your door

| You are | Start with |
|---|---|
| **a mathematician who wants a verified page now** | [Quickstart](quickstart.md): `lean-pkg new`, two receipts, the gate and the page on your own machine, no lab account (about 6 minutes measured, Mathlib included) |
| **new here** | [Start here](newcomers.md): the reading path, then the [sample workflow](sample-workflow.md) |
| **evaluating it** | [Motivation](motivation.md) and the [FAQ](faq.md) |
| **writing a paper with a Lean companion** | [Develop a paper with its Lean companion](paper-companion.md): intermediate checkpoints, reuse, a coverage map |
| **writing the public README of a companion** | [The verification page](page.md): `lean-pkg coverage check` and `lean-pkg page --write/--check` render, from data, each result with the paper's statement beside the Lean statement, what the checks establish, the receipts and the credits |
| **using the tool** | [How to](how-to.md), the [command reference](commands/index.md) (every command and subcommand, with examples), the [CLI overview](cli.md) (global flags) and [Prior-art search](prior-art.md) |
| **refereeing or auditing a package** | [Trust model](trust-model.md): what each artifact certifies, what it does not, and how to re-verify a page yourself; then [Receipts and trust](receipts.md), [The comparator tier](comparator.md), [formalization.yaml](formalization.md), [The index](index-catalog.md), [Lifecycle](lifecycle.md) |
| **adding the comparator tier to your package** | [Add the comparator tier to an existing package](existing-package-comparator.md), step by step |
| **joining someone's package** | `scripts/bootstrap-collaborator.sh OWNER/PACKAGE`, then [Add the comparator tier to an existing package](existing-package-comparator.md) |
| **about to publish** | [Scan for private strings](how-to.md#scan-for-private-strings-before-publishing), [a fresh public history for a paper-incubated package](receipts.md#publishing-a-package-incubated-beside-private-work), and [the publish checklist, Reservoir and Palomar](how-to.md#get-listed-publish-checklist-reservoir-palomar) |
| **publishing the Lean part of a paper repository** | [`lean-pkg-extract plan`](extract.md): plan a standalone public Lean-verification repository from a mixed TeX+Lean repository, every file traced to a blob of one commit, private material default-denied (P1: plan only; write comes next) |
| **writing a negative test (a file that must fail)** | [Negative tests that fail for the right reason](negative-tests.md): `python3 -m lean_pkg.lean_diag LOG --error-code CODE` checks that a Lean log holds exactly one error, the expected one, coded or uncoded |
| **auditing a constant two modules both declare** | [Constants that two modules both declare](duplicate-constants.md): `python3 -m lean_pkg.dup_constants --project DIR MOD` lists every name in more than one module's constant table, with its owners and the `env.find?` winner, and refuses any duplicate that is not exactly equal |
| **teaching or self-checking an exercise** | [`lean-exercise check`](exercise.md): judge a `Solution.lean` against a `Challenge.lean` with `lake comparator`, sandboxed, no registry |
| **checking a machine** | [`comparator host-check`](how-to.md#check-that-this-machine-can-produce-comparator-receipts) before a long comparator run |
| **declaring or searching results** | [`claims.yaml` and `lean-pkg search`](how-to.md#declare-reusable-results-and-search-before-building), and [formalization.yaml](how-to.md#render-formalizationyaml) |
| **testing it from scratch** | [Stranger test](stranger-test.md): run it from the docs alone and record what happened |
| **a coding agent** | [Using lean-pkg with agents](agents.md), [llms.txt](llms.txt), and the [agent skills](skills.md) |
| **contributing** | [CONTRIBUTING](https://github.com/lean-pkg/lean-pkg/blob/main/CONTRIBUTING.md), [GOVERNANCE](https://github.com/lean-pkg/lean-pkg/blob/main/GOVERNANCE.md) and [MAINTAINERS](https://github.com/lean-pkg/lean-pkg/blob/main/MAINTAINERS.md); a format or command change starts as a [design proposal](proposals/README.md); a soundness bug goes privately by [SECURITY](https://github.com/lean-pkg/lean-pkg/blob/main/SECURITY.md) |

## In one paragraph

A package moves through
`scratch → active → gate-pending → verified-private → public → upstreamed`. The move to `verified-private` requires a lower-tier
receipt (`lake build`, sorry scan, `#print axioms`, bound to one commit). The
move to `public` additionally requires a comparator receipt: trusted
`Challenge.lean` statements checked against untrusted `Solution.lean` proofs
by `leanprover/comparator`, with every proof replayed in the independent
`nanoda` kernel. `promote` re-derives that PASS from file hashes and log
lines before it flips the GitHub repository to public, which cannot be
undone in practice.

## Quick start

The shortest path, from `pip install` to a verified page with no lab account, is the
[Quickstart](quickstart.md). The shared-registry setup for a group:

Install the CLI once, then use one private Git-backed registry to coordinate
many independent research repositories:

<img src="assets/global-registry.svg" alt="Architecture diagram: one private global registry coordinates many independent Lean project repositories; a chosen public snapshot can later flow through index review into the lean-pkg public index" style="max-width:100%;height:auto">

```bash
pipx install git+https://github.com/lean-pkg/lean-pkg.git
mkdir -p ~/.lean-pkg/registry
git -C ~/.lean-pkg/registry init
lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml registry init
# ~/.lean-pkg/registry is the default registry location: no LEAN_PKG_REGISTRY needed
lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof
```

Continue with [Getting started](getting-started.md) for the initial registry
commit, receipts, and promotion dry run. A future `lean-pkg registry init --global`
will automate the registry bootstrap; it is not implemented yet.

## Pages

- [Quickstart](quickstart.md): from `pip install` to a verified page, for a new package (`lean-pkg new`) or an existing one.
- [Start here](newcomers.md): the newcomer path, including the Lean reading list.
- [Sample workflow](sample-workflow.md): a real package from registration to a promotion dry run, with the workflow diagram: the seven shared steps and the paper and library tracks.
- [Develop a paper with its Lean companion](paper-companion.md): check intermediate results while the paper evolves, record checkpoints, reuse unchanged results, keep a coverage map, consolidate several checkpoint packages into one new package.
- [FAQ](faq.md): common questions, including how this differs from Palomar and Mathlib.
- [Motivation](motivation.md): the problem this solves and where it is going.
- [Getting started](getting-started.md): install and a complete worked example.
- [How to](how-to.md): task recipes.
- [Lifecycle](lifecycle.md): status enum, transitions, leases.
- [Trust model](trust-model.md): what each receipt, the page and the index
  certify and do not, the trusted parties, the threats covered and not, and
  the steps to re-verify a published page from the public repository alone.
- [Receipts and trust](receipts.md): both receipt schemas, the evidence-only
  descendant rule, and the trust boundary.
- [The comparator tier](comparator.md): the Palomar layout, the runner, the
  host policy, the tool pins.
- [formalization.yaml](formalization.md): render the mathlib-initiative v0.4
  report Palomar reads from the registry, the comparator configurations and
  the receipts; derived versus kept keys; placeholders; the audit drift check.
- [The verification page](page.md): `coverage.yaml` and the README section
  `lean-pkg page` renders from it, the claims ledger and the receipts; the
  `--check` staleness and credits gate.
- [Compute sites](sites.md): site profiles, the acknowledgment of the cluster
  that ran the gates, and how to add a site of your own.
- [The index](index-catalog.md): the public catalog of trust-graded packages, its
  entry schema, and how to add and check an entry.
- [Prior-art search](prior-art.md): claims files, `lean-pkg search` across the
  registry, the index, Loogle, LeanSearch, Reservoir, Palomar and GitHub, and the
  search record.
- [Command reference](commands/index.md): every command and subcommand on its own page, with its
  usage, flags, exit codes, `--json` keys and examples; generated from the parser.
- [CLI overview](cli.md): the global flags, committed mode, leases, the package record and the exit codes.
- [Using lean-pkg with agents](agents.md): the LLM entry.
- [Agent skills](skills.md): lessons for agents on statement fidelity, evidence and publication, with an installer.
- [Decisions](decisions/README.md): dated records of the choices that shape the tool.
- [LTS stacks](stacks/README.md): dated Lean + Mathlib + lean-pkg combinations that later releases keep working on (first: lts-2026.10).
- [Design proposals](proposals/README.md): how a schema, command-removal or trust-model change is proposed, decided and implemented.

## Citation

Releases are archived on Zenodo: the concept DOI
[10.5281/zenodo.23272098](https://doi.org/10.5281/zenodo.23272098) resolves to
the latest release, and `CITATION.cff` lists it with the version DOIs. To cite
a state between releases, cite the repository at a commit:

```bibtex
@software{lean_pkg,
  author  = {Chen, Le},
  title   = {lean-pkg: a registry and trust gate for Lean 4 formalization packages},
  year    = {2026},
  version = {0.12.0},
  url     = {https://github.com/lean-pkg/lean-pkg},
  note    = {commit <sha>}
}
```

For a verified result, cite the package's Zenodo version DOI (cut at the
Palomar-registered commit) together with its `PALOMAR-…` id and version;
see the [FAQ](faq.md#about-the-workflow).

## License

The tool and this documentation are released under the
[Apache License 2.0](https://github.com/lean-pkg/lean-pkg/blob/main/LICENSE).
Each verified package carries its own license; the promotion checklist
requires one that GitHub can detect.

