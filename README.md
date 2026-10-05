<p align="center">
  <img src="docs/assets/lean-pkg-mark.svg" width="112" height="112" alt="lean-pkg mark: a shield with a check mark and two stacked receipt lines">
</p>

<h1 align="center">lean-pkg</h1>

<p align="center">
  <strong>A registry and trust gate for Lean 4 formalization packages.</strong><br>
  Know which packages exist, who is working on each one, and what evidence binds a package to a verified commit, before anything becomes public.
</p>

<p align="center">
  <a href="https://github.com/chenle02/lean-pkg/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/chenle02/lean-pkg/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB.svg">
  <a href="https://chenle02.github.io/lean-pkg/"><img alt="Documentation" src="https://img.shields.io/badge/docs-site-0A7E8C.svg"></a>
  <a href="llms.txt"><img alt="LLM entry: llms.txt" src="https://img.shields.io/badge/LLM%20entry-llms.txt-6f42c1.svg"></a>
</p>

---

## Why lean-pkg

A Lean project that compiles with zero `sorry` is not yet a verified result.
Three things still go wrong in practice, and `lean-pkg` exists to catch them:

- **Statement drift.** The theorem that compiles is not quite the theorem the
  paper claims: a hypothesis moved into a definition, a quantifier changed, a
  constant was absorbed. `lean-pkg` requires a *comparator* receipt: trusted
  statements in `Challenge.lean`, untrusted proofs in `Solution.lean`, checked
  for statement equality and replayed in an independent kernel (`nanoda`),
  following the [Palomar](https://github.com/PalomarRegistry/PalomarSubmission)
  acceptance bar.
- **Evidence that cannot be re-derived.** "CI was green last month" is a
  status word, not evidence. Every receipt here binds to a commit, a toolchain,
  tool pins, file hashes, and a log line, and `promote` re-derives the PASS
  from bytes before the one-way flip to public.
- **Many packages, many hands.** Research formalization runs across several
  repositories, machines, and increasingly coding agents. One append-only
  registry with time-limited leases keeps one package in one workspace at a
  time and records every transition.

`lean-pkg` is not a library and not a Mathlib fork: every package depends on
Mathlib, and general results are meant to be upstreamed. The longer-term aim
is a catalog of verified research results that other projects can search,
import under a stable identifier, and cite. See [Motivation](docs/motivation.md),
including [lean-pkg and Mathlib](docs/motivation.md#lean-pkg-and-mathlib).

## What a receipt proves, and what it does not

| A passing receipt pair proves | It does not prove |
|---|---|
| The named theorems compile at the pinned toolchain with `sorry_count = 0` and only `propext`, `Quot.sound`, `Classical.choice` | that the Lean statement matches the paper's intent (a human fidelity review is still needed) |
| Each `Solution` theorem has exactly the statement of its trusted `Challenge` namesake | anything about theorems not listed in `comparator.json` |
| The proofs replay in an independent kernel inside a sandbox | that dependencies beyond the pinned Mathlib are sound |
| The evidence bytes on disk hash to what the receipt recorded, at the certified commit or an evidence-only descendant of it | that the repository is public, licensed, or documented (those are separate checklist items) |

## Quick start

```bash
pipx install git+https://github.com/chenle02/lean-pkg.git     # or: uv tool install ...

mkdir -p ~/lean-registry && printf 'version: 1\npackages: {}\n' > ~/lean-registry/lean-packages.yaml
export LEAN_PKG_REGISTRY=~/lean-registry/lean-packages.yaml

lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof
lean-pkg claim my-proof --workspace laptop --agent you --reason "comparator tier"

cd ~/src/my-proof && lake exe cache get
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt     # writes .lean-receipts/<c12>-comparator.json on PASS
lean-pkg comparator my-proof --check --receipt .lean-receipts/<c12>-comparator.json
lean-pkg promote my-proof                                            # dry run: the checklist tells you what is missing
```

The full walk-through, including the lower-tier receipt and the gate, is in
[Getting started](docs/getting-started.md).

## How to use

```
scratch -> active -> gate-pending -> verified-private -> public -> upstreamed
   register   update       gate (receipt [+ comparator])   promote --yes
```

| I want to... | Run | Notes |
|---|---|---|
| see every package and lease | `lean-pkg board` (`list --json` in scripts) | TUI on a terminal, table otherwise |
| add a package | `lean-pkg register NAME --repo URL --path HOME-RELATIVE` | the only write path; never edit the YAML |
| work on a package | `lean-pkg claim NAME --workspace W --agent A` ... `release` | one package, one workspace; 24 h TTL |
| certify a commit | `lean-pkg gate NAME --receipt R.json [--comparator C.json]` | moves to `verified-private` |
| add comparator evidence later | `lean-pkg comparator NAME --receipt C.json` (`--check` to validate only) | required before `promote` |
| produce the comparator receipt | `comparator-receipt` in the Lake project, on an allowed host | writes nothing unless every config passes |
| publish | `lean-pkg promote NAME` then `--yes` | one-way; read the dry run first |
| find drift and expired leases | `lean-pkg audit` (`--fix` to copy GitHub state back) | report-only by default |

Task recipes, including multi-machine registries and use with coding agents,
are in [How to](docs/how-to.md).

## Documentation

The documentation site is <https://chenle02.github.io/lean-pkg/>. The same
pages live in [`docs/`](docs/index.md):

| For | Start here |
|---|---|
| **Users** | [Getting started](docs/getting-started.md) · [How to](docs/how-to.md) · [CLI reference](docs/cli.md) |
| **Auditors** | [Receipts and trust](docs/receipts.md) · [The comparator tier](docs/comparator.md) · [Lifecycle](docs/lifecycle.md) |
| **Contributors** | [CONTRIBUTING.md](CONTRIBUTING.md) · [MAINTAINERS.md](MAINTAINERS.md) · [DESIGN.md](DESIGN.md) |
| **LLM agents** | [llms.txt](llms.txt) (the exact `lean-pkg llms` contract) · [AGENTS.md](AGENTS.md) · [Using lean-pkg with agents](docs/agents.md) |

## Requirements

Python 3.11 or newer; PyYAML is the only Python dependency. `git` is needed
for everything that reads a package repository. `gh`, authenticated, is
needed for `promote` and `audit`. `comparator-receipt` needs Linux, `elan`
(`lean`, `lake`), `go`, `cargo`, and `jq`; its first run builds the pinned
comparator toolchain into `~/.cache/lean-ci-tools` (about 25 minutes cold,
seconds afterwards).

## AI assistance

This tool was built with AI assistance: the implementation, tests, and
documentation were written with Anthropic's Claude, directed and reviewed by
the maintainer, who is responsible for every merged change. The vendored
`comparator-check.sh` comes unchanged from the Palomar verifier workflow.
Contributions must disclose AI assistance; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Citation

A Zenodo DOI will be minted for the first public release. Until then cite the
repository at a commit:

```bibtex
@software{lean_pkg,
  author  = {Chen, Le},
  title   = {lean-pkg: a registry and trust gate for Lean 4 formalization packages},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/chenle02/lean-pkg}
}
```

## License

Apache-2.0. See [LICENSE](LICENSE).
