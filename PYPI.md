# lean-pkg

*Lean checks the proofs. You check the statements. lean-pkg checks the receipts.*

A registry and trust gate for Lean 4 formalization packages: know which packages exist, who holds each
one, and what evidence binds it to a verified commit, before anything becomes public.

A Lean project that builds with zero `sorry` is not yet a verified theorem. The statement may have
drifted from the paper's claim, the check ran in the same kernel that elaborated the proof, and "CI was
green" is a status word nobody can re-derive. lean-pkg requires two receipts before a package goes
public: a build-and-axioms receipt bound to one commit, and a comparator receipt in which trusted
statements are checked against the proofs and replayed in an independent kernel. At publication it
re-derives both from bytes.

## Install

```bash
pipx install lean-pkg          # or: uv tool install lean-pkg, or pip install lean-pkg
lean-pkg doctor                # what this machine has, and what it needs
```

Python 3.11 or newer on Linux. Lean itself comes from
[elan](https://github.com/leanprover/elan), as for any Lean project.

## From a new package to a verified page

```bash
lean-pkg new my-proof --kind library   # scaffold a package (Mathlib by default), then lake build it
lean-pkg registry init                 # a private registry of your own (~/.lean-pkg/registry)
lean-pkg register my-proof --url https://github.com/you/my-proof --path src/my-proof --license-spdx Apache-2.0
lean-pkg verify my-proof               # both receipts and the gate, in one step
lean-pkg page my-proof --write         # the verification page in README.md
lean-pkg publish my-proof --dry-run    # the checklist before the repository goes public
```

The [Quickstart](https://lean-pkg.github.io/lean-pkg/quickstart.html) walks through each step and its
output.

## Learn more

- [Documentation](https://lean-pkg.github.io/lean-pkg/), starting with the
  [Quickstart](https://lean-pkg.github.io/lean-pkg/quickstart.html) and the
  [π-λ tutorial](https://lean-pkg.github.io/lean-pkg/tutorial-pi-lambda.html), Dynkin's π-λ theorem end to
  end for a mathematician who has barely used Lean.
- [Video series](https://www.youtube.com/playlist?list=PLG2OKUkl58LM): eight short episodes, from why a
  green build is not a verified theorem to a paper with a Lean companion.
- [Receipts and trust](https://lean-pkg.github.io/lean-pkg/receipts.html): what each receipt binds and
  what it lets you believe.
- For coding agents: `lean-pkg llms` prints the agent contract
  ([llms.txt](https://github.com/lean-pkg/lean-pkg/blob/main/llms.txt)).
- [Source](https://github.com/lean-pkg/lean-pkg),
  [changelog](https://github.com/lean-pkg/lean-pkg/blob/main/CHANGELOG.md),
  [issues](https://github.com/lean-pkg/lean-pkg/issues).

Cite a release by its Zenodo DOI (concept DOI
[10.5281/zenodo.23272098](https://doi.org/10.5281/zenodo.23272098)). Apache-2.0.
