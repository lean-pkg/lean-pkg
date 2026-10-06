<p align="center">
  <img src="docs/assets/logo-256.png" width="160" height="160" alt="lean-pkg logo: a secretary bird in profile, its crest feathers swept back like quill pens">
</p>

<h1 align="center">lean-pkg</h1>

<p align="center">
  <strong><em>Prove it. Then prove you proved it.</em></strong><br>
  A registry and trust gate for Lean 4 formalization packages: know which packages exist, who holds each one, and what evidence binds it to a verified commit, before anything becomes public.
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

*A green build is a claim. A receipt is evidence.*

A Lean project that builds with zero `sorry` is not yet a verified theorem.
The statement may have drifted from the paper's claim, the check ran in the
same kernel that elaborated the proof, and "CI was green" is a status word
nobody can re-derive. `lean-pkg` requires two receipts before a package can
go public: a build-and-axioms receipt bound to one commit, and a
**comparator** receipt in which trusted statements are checked against
untrusted proofs and replayed in an independent kernel. At promotion time it
re-derives both from bytes.

### Research loop: discover, formalize, verify

Research mathematics is rarely linear. A symbolic computation may suggest a
formula; Lean may expose a missing hypothesis; the manuscript changes; the
formalization changes with it. `lean-pkg` is designed to keep the verification
state attached to exact commits while that loop remains private.

The trust boundary is deliberately asymmetric:

- Python, SymPy, Sage, Mathematica, numerical experiments, or hand calculations
  may **discover** a candidate claim;
- Lean states and proves the mathematical claim;
- `lean-pkg` records which exact source and evidence were actually verified.

A computational discovery is therefore provenance, not proof. If the final Lean
theorem no longer depends on trusting the computation that suggested it, the
certificate rests on the Lean proof, not on the exploratory tool.

As formal libraries mature, a result that was initially conditional can be
re-certified with fewer external dependencies. The roadmap treats that
progression as certificate lineage rather than overwriting history.

**Develop privately. Discover freely. Verify explicitly. Publish reproducibly.**

## Who is this for

- **A research group** formalizing its own papers: run the private lifecycle
  (register, claim, gate, promote), mostly through agents, with a human
  reading each dry run.
- **A mathematician** asking "is this already verified in Lean?": read the
  field index, with hypotheses and paper correspondence, keyed to Palomar ids.
- **An LLM agent** proving something: fetch [llms.txt](llms.txt) and the index
  first; import instead of re-deriving.
- **A contributor**: send code to the tool, or an index entry for a result
  you verified.

## Not Palomar, not Mathlib

Public verified results are registered in [Palomar](https://palomar-registry.org/),
which archives them and assigns stable ids. Every package depends on
[Mathlib](https://github.com/leanprover-community/mathlib4). Public packages
are indexed by [Reservoir](https://reservoir.lean-lang.org/), automatically
(no registration step), which checks that they build but not what they prove;
search it before building anything new. `lean-pkg`
covers what those leave out: the months *before* a result is public, and a
field-curated index *after*. A package that passes its comparator tier is
already in the shape Palomar accepts. The full comparison is in
[Motivation](docs/motivation.md#lean-pkg-palomar-reservoir-and-mathlib) and the
[FAQ](docs/faq.md).

## In sixty seconds

Install the CLI once, then use one **shared private registry** to coordinate
many independent Lean/Git research repositories.

<p align="center">
  <img src="docs/assets/global-registry.svg" alt="Architecture diagram: one private global registry coordinates many independent Lean project repositories; a chosen public snapshot can later flow through index review into the lean-pkg public index" width="1100">
</p>

```text
~/.lean-pkg/registry/          # recommended shared coordination repo
  .git/
  lean-packages.yaml
  lean-package-events.jsonl

~/src/
  paper-a/                     # its own Git repo and Lean project
  paper-b/                     # another independent Git repo
  paper-c/
```

Today, bootstrap the shared registry explicitly:

```bash
pipx install git+https://github.com/chenle02/lean-pkg.git

mkdir -p ~/.lean-pkg/registry
git -C ~/.lean-pkg/registry init
lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml init
git -C ~/.lean-pkg/registry add lean-packages.yaml lean-package-events.jsonl
git -C ~/.lean-pkg/registry commit -m "registry: initialize lean-pkg"

export LEAN_PKG_REGISTRY=~/.lean-pkg/registry/lean-packages.yaml

lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof
lean-pkg claim my-proof --workspace laptop --agent you --reason "first receipt"
cd ~/src/my-proof && lake exe cache get
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt
lean-pkg comparator my-proof --check --receipt .lean-receipts/<c12>-comparator.json
lean-pkg promote my-proof                      # dry run: the checklist says what is missing
```

The registry repository is the cross-project coordination history; each research
repository keeps its own mathematics and committed `.lean-receipts/` evidence.
Keep the shared registry private unless you intentionally want to expose
pre-publication project names, paths, notes, and status history.

A future one-command bootstrap such as `lean-pkg init --global` is proposed in
the [roadmap](ROADMAP.md); it is not implemented yet.

## Learn the workflow

| If you are | Start with |
|---|---|
| new to Lean | [Start here](docs/newcomers.md): a one-to-two-week reading path (Functional Programming in Lean, Chapter 1, and the Math 7800 lectures), then the sample |
| ready to try it | [Sample workflow](docs/sample-workflow.md): a real package, register to promotion dry run, every command and output |
| looking something up | [How to](docs/how-to.md) · [CLI reference](docs/cli.md) · [Receipts and trust](docs/receipts.md) · [The comparator tier](docs/comparator.md) · [The index](docs/index-catalog.md) · [Lifecycle](docs/lifecycle.md) |
| an agent | [llms.txt](llms.txt) · [AGENTS.md](AGENTS.md) · [Using lean-pkg with agents](docs/agents.md) · [Agent skills](skills/README.md) |
| stuck | [FAQ](docs/faq.md) |

Documentation site: <https://chenle02.github.io/lean-pkg/>.

## Requirements

Python 3.11+ and PyYAML. `git` for anything that reads a repository; `gh`,
authenticated, for `promote` and `audit`. `comparator-receipt` needs Linux,
`elan`, `go`, `cargo`, and `jq`; its first run builds the comparator tools
once (about 25 minutes), later runs take seconds.

## AI assistance

Built with AI assistance: the implementation, tests, and documentation were
written with Anthropic's Claude, directed and reviewed by the maintainer,
who is responsible for every merged change. The secretary-bird logo and the
mascot illustration were generated with AI image tools by the maintainer. Contributions must disclose AI
assistance; see [CONTRIBUTING.md](CONTRIBUTING.md).

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

Releases are listed in [CHANGELOG.md](CHANGELOG.md); the process is in
[RELEASING.md](RELEASING.md). Proposed future directions are in
[ROADMAP.md](ROADMAP.md). Design choices are recorded in
[docs/decisions](docs/decisions/README.md); security reports go by
[SECURITY.md](SECURITY.md).

## License

Apache-2.0. See [LICENSE](LICENSE). Contributions are accepted under the
same license; see [CONTRIBUTING.md](CONTRIBUTING.md).
