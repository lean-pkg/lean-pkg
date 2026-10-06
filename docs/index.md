---
title: lean-pkg
---

<img src="assets/logo-256.png" width="140" height="140" alt="lean-pkg logo: a secretary bird in profile with quill-like crest feathers" style="float:right;margin:0 0 1rem 1.5rem">

*Prove it. Then prove you proved it.* **A registry and trust gate for Lean 4 formalization packages.** One
append-only registry says which packages exist, who is working on each one,
and what evidence binds a package to a verified commit. Nothing becomes
public without two receipts, and the second one is re-derived from bytes.

Source and issues: <https://github.com/chenle02/lean-pkg>. License:
Apache-2.0. Machine-readable entry: [llms.txt](llms.txt).

## Choose your door

| You are | Start with |
|---|---|
| **new here** | [Start here](newcomers.md): the reading path, then the [sample workflow](sample-workflow.md) |
| **evaluating it** | [Motivation](motivation.md) and the [FAQ](faq.md) |
| **using the tool** | [How to](how-to.md) and the [CLI reference](cli.md) |
| **auditing a package** | [Receipts and trust](receipts.md), [The comparator tier](comparator.md), [Lifecycle](lifecycle.md) |
| **a coding agent** | [Using lean-pkg with agents](agents.md), [llms.txt](llms.txt), and the [agent skills](skills.md) |
| **contributing** | [CONTRIBUTING](https://github.com/chenle02/lean-pkg/blob/main/CONTRIBUTING.md) and [MAINTAINERS](https://github.com/chenle02/lean-pkg/blob/main/MAINTAINERS.md) |

## In one paragraph

A package moves `scratch → active → gate-pending → verified-private →
public → upstreamed`. The move to `verified-private` requires a lower-tier
receipt (`lake build`, sorry scan, `#print axioms`, bound to one commit). The
move to `public` additionally requires a comparator receipt: trusted
`Challenge.lean` statements checked against untrusted `Solution.lean` proofs
by `leanprover/comparator`, with every proof replayed in the independent
`nanoda` kernel. `promote` re-derives that PASS from file hashes and log
lines before it flips the GitHub repository to public, which cannot be
undone in practice.

## Quick start

```bash
pipx install git+https://github.com/chenle02/lean-pkg.git
mkdir -p ~/lean-registry && printf 'version: 1\npackages: {}\n' > ~/lean-registry/lean-packages.yaml
export LEAN_PKG_REGISTRY=~/lean-registry/lean-packages.yaml
lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof
```

Continue with [Getting started](getting-started.md) for the receipts and the
promotion dry run.

## Pages

- [Start here](newcomers.md): the newcomer path, including the Lean reading list.
- [Sample workflow](sample-workflow.md): a real package from registration to a promotion dry run.
- [FAQ](faq.md): common questions, including how this differs from Palomar and Mathlib.
- [Motivation](motivation.md): the problem this solves and where it is going.
- [Getting started](getting-started.md): install and a complete worked example.
- [How to](how-to.md): task recipes.
- [Lifecycle](lifecycle.md): status enum, transitions, leases.
- [Receipts and trust](receipts.md): both receipt schemas, the evidence-only
  descendant rule, and the trust boundary.
- [The comparator tier](comparator.md): the Palomar layout, the runner, the
  host policy, the tool pins.
- [CLI reference](cli.md): every subcommand.
- [Using lean-pkg with agents](agents.md): the LLM entry.
- [Agent skills](skills.md): lessons for agents on statement fidelity, evidence and publication, with an installer.

## Citation

A Zenodo DOI will be minted for the first public release. Until then, cite
the repository at a commit:

```bibtex
@software{lean_pkg,
  author  = {Chen, Le},
  title   = {lean-pkg: a registry and trust gate for Lean 4 formalization packages},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/chenle02/lean-pkg},
  note    = {commit <sha>}
}
```

For a verified result, cite the package's Zenodo version DOI (cut at the
Palomar-registered commit) together with its `PALOMAR-…` id and version;
see the [FAQ](faq.md#about-the-workflow).

## License

The tool and this documentation are released under the
[Apache License 2.0](https://github.com/chenle02/lean-pkg/blob/main/LICENSE).
Each verified package carries its own license; the promotion checklist
requires one that GitHub can detect.

