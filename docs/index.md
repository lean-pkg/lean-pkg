---
title: lean-pkg
---

# lean-pkg

**A registry and trust gate for Lean 4 formalization packages.** One
append-only registry says which packages exist, who is working on each one,
and what evidence binds a package to a verified commit. Nothing becomes
public without two receipts, and the second one is re-derived from bytes.

Source and issues: <https://github.com/chenle02/lean-pkg>. License:
Apache-2.0. Machine-readable entry: [llms.txt](llms.txt).

## Choose your door

| You are | Start with |
|---|---|
| **new here** | [Motivation](motivation.md), then [Getting started](getting-started.md) |
| **using the tool** | [How to](how-to.md) and the [CLI reference](cli.md) |
| **auditing a package** | [Receipts and trust](receipts.md), [The comparator tier](comparator.md), [Lifecycle](lifecycle.md) |
| **a coding agent** | [Using lean-pkg with agents](agents.md) and [llms.txt](llms.txt) |
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
