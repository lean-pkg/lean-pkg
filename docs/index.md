---
title: lean-pkg
---

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23272098.svg)](https://doi.org/10.5281/zenodo.23272098)

<img src="assets/logo-256.png" width="140" height="140" alt="lean-pkg logo: a secretary bird in profile with quill-like crest feathers" style="float:right;margin:0 0 1rem 1.5rem">

*Prove it. Then prove you proved it.* **A registry and trust gate for Lean 4 formalization packages.** One
append-only registry says which packages exist, who is working on each one,
and what evidence binds a package to a verified commit. Nothing becomes
public without two receipts, and the second one is re-derived from bytes.

Source and issues: <https://github.com/lean-pkg/lean-pkg>. License:
Apache-2.0. Machine-readable entry: [llms.txt](llms.txt).

## Choose your door

| You are | Start with |
|---|---|
| **new here** | [Start here](newcomers.md): the reading path, then the [sample workflow](sample-workflow.md) |
| **evaluating it** | [Motivation](motivation.md) and the [FAQ](faq.md) |
| **writing a paper with a Lean companion** | [Develop a paper with its Lean companion](paper-companion.md): intermediate checkpoints, reuse, a coverage map |
| **using the tool** | [How to](how-to.md), the [CLI reference](cli.md) and [Prior-art search](prior-art.md) |
| **auditing a package** | [Receipts and trust](receipts.md), [The comparator tier](comparator.md), [formalization.yaml](formalization.md), [The index](index-catalog.md), [Lifecycle](lifecycle.md) |
| **adding the comparator tier to your package** | [Add the comparator tier to an existing package](existing-package-comparator.md), step by step |
| **joining someone's package** | `scripts/bootstrap-collaborator.sh OWNER/PACKAGE`, then [Add the comparator tier to an existing package](existing-package-comparator.md) |
| **about to publish** | [Scan for private strings](how-to.md#scan-for-private-strings-before-publishing), [a fresh public history for a paper-incubated package](receipts.md#publishing-a-package-incubated-beside-private-work), and [the publish checklist, Reservoir and Palomar](how-to.md#get-listed-publish-checklist-reservoir-palomar) |
| **checking a machine** | [`comparator host-check`](how-to.md#check-that-this-machine-can-produce-comparator-receipts) before a long comparator run |
| **declaring or searching results** | [`claims.yaml` and `lean-pkg search`](how-to.md#declare-reusable-results-and-search-before-building), and [formalization.yaml](how-to.md#render-formalizationyaml) |
| **testing it from scratch** | [Stranger test](stranger-test.md): run it from the docs alone and record what happened |
| **a coding agent** | [Using lean-pkg with agents](agents.md), [llms.txt](llms.txt), and the [agent skills](skills.md) |
| **contributing** | [CONTRIBUTING](https://github.com/lean-pkg/lean-pkg/blob/main/CONTRIBUTING.md) and [MAINTAINERS](https://github.com/lean-pkg/lean-pkg/blob/main/MAINTAINERS.md) |

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

Install the CLI once, then use one private Git-backed registry to coordinate
many independent research repositories:

<img src="assets/global-registry.svg" alt="Architecture diagram: one private global registry coordinates many independent Lean project repositories; a chosen public snapshot can later flow through index review into the lean-pkg public index" style="max-width:100%;height:auto">

```bash
pipx install git+https://github.com/lean-pkg/lean-pkg.git
mkdir -p ~/.lean-pkg/registry
git -C ~/.lean-pkg/registry init
lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml init
# ~/.lean-pkg/registry is the default registry location: no LEAN_PKG_REGISTRY needed
lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof
```

Continue with [Getting started](getting-started.md) for the initial registry
commit, receipts, and promotion dry run. A future `lean-pkg init --global`
will automate the registry bootstrap; it is not implemented yet.

## Pages

- [Start here](newcomers.md): the newcomer path, including the Lean reading list.
- [Sample workflow](sample-workflow.md): a real package from registration to a promotion dry run, with the seven-step diagram.
- [Develop a paper with its Lean companion](paper-companion.md): check intermediate results while the paper evolves, record checkpoints, reuse unchanged results, keep a coverage map.
- [FAQ](faq.md): common questions, including how this differs from Palomar and Mathlib.
- [Motivation](motivation.md): the problem this solves and where it is going.
- [Getting started](getting-started.md): install and a complete worked example.
- [How to](how-to.md): task recipes.
- [Lifecycle](lifecycle.md): status enum, transitions, leases.
- [Receipts and trust](receipts.md): both receipt schemas, the evidence-only
  descendant rule, and the trust boundary.
- [The comparator tier](comparator.md): the Palomar layout, the runner, the
  host policy, the tool pins.
- [formalization.yaml](formalization.md): render the mathlib-initiative v0.4
  report Palomar reads from the registry, the comparator configurations and
  the receipts; derived versus kept keys; placeholders; the audit drift check.
- [The index](index-catalog.md): the public catalog of trust-graded packages, its
  entry schema, and how to add and check an entry.
- [Prior-art search](prior-art.md): claims files, `lean-pkg search` across the
  registry, the index, Loogle, LeanSearch, Reservoir, Palomar and GitHub, and the
  search record.
- [CLI reference](cli.md): every subcommand.
- [Using lean-pkg with agents](agents.md): the LLM entry.
- [Agent skills](skills.md): lessons for agents on statement fidelity, evidence and publication, with an installer.
- [Decisions](decisions/README.md): dated records of the choices that shape the tool.

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
  version = {0.7.41},
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

