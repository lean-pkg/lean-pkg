# Agent skills for LLM-assisted Lean verification

Eighteen skills for coding agents (Claude Code, Codex, or any harness that
reads `SKILL.md` folders) that formalize mathematics in Lean 4 for a paper.
They focus on three things a green build does not give you: **fidelity** of
each statement to the paper, **evidence** that binds results to exact bytes,
and **publication** that claims no more than the evidence. Each skill is a
lesson learned from a real failure, written as Problem, Why it happens, Rule,
How to apply, Anti-patterns, Verification.

For the `lean-pkg` tool itself, the authoritative contract is
[`llms.txt`](../llms.txt) (the output of `lean-pkg llms`); the skills point
there rather than restating it.

## Install

Paste this into your agent, from a checkout of this repository:

```text
Read skills/README.md in this lean-pkg checkout and install its skills for the agent I am using with scripts/install_skills.py, first with --dry-run; do not replace any existing skill, and tell me how to invoke lean-workflow.
```

Or run the installer directly:

```bash
python3 scripts/install_skills.py --agent claude --dry-run     # ~/.claude/skills
python3 scripts/install_skills.py --agent codex                # ~/.agents/skills
python3 scripts/install_skills.py --agent both --project /path/to/lean-project
```

It links each skill folder (use `--copy` to copy), never replaces an existing
skill that is not its own link unless you pass `--force` (which moves the old
one aside), and prints a table of what it did. Start a new agent session
afterwards; invoke `/lean-workflow` in Claude Code or `$lean-workflow` in
Codex, or just describe the task.

## Index

| Skill | Use it when |
|---|---|
| [lean-workflow](lean-workflow/SKILL.md) | you need to pick a skill; the router |
| **Fidelity** | |
| [lean-fidelity-audit-taxonomy](lean-fidelity-audit-taxonomy/SKILL.md) | checking a Lean statement or definition against the paper |
| [lean-weakened-hypothesis-vacuity](lean-weakened-hypothesis-vacuity/SKILL.md) | a relaxed hypothesis is claimed to cover a new case |
| [lean-partial-formalization-boundary](lean-partial-formalization-boundary/SKILL.md) | analytic facts enter as hypotheses or contract fields |
| [statement-vs-use-span-audit](statement-vs-use-span-audit/SKILL.md) | a lemma may be stated more narrowly than it is used |
| [lean-honest-named-hypotheses](lean-honest-named-hypotheses/SKILL.md) | an input cannot be proved now |
| [lean-prop-data-witnesses](lean-prop-data-witnesses/SKILL.md) | `Classical.choose` lost a fact, or an `Or` must become data |
| **Proof engineering** | |
| [lean-elaboration-boundaries](lean-elaboration-boundaries/SKILL.md) | the producer compiles and the consumer does not |
| [lean-large-finite-computation](lean-large-finite-computation/SKILL.md) | a closed Finset, table or `decide` certificate blows up |
| [lean-vendor-namespace-isolation](lean-vendor-namespace-isolation/SKILL.md) | two libraries export the same names |
| [lean-reserved-name-mint-site](lean-reserved-name-mint-site/SKILL.md) | `X.congr_simp` or `X.eq_1` appears in the wrong module |
| **Evidence** | |
| [lean-axiom-audit](lean-axiom-audit/SKILL.md) | writing the exact-inventory `#print axioms` audit |
| [lean-receipt-discipline](lean-receipt-discipline/SKILL.md) | binding a result to a commit; choosing a replay layer |
| [lean-claim-ledger](lean-claim-ledger/SKILL.md) | recording what is verified, conditional, partial, excluded |
| [lean-paper-companion](lean-paper-companion/SKILL.md) | formalizing alongside a paper in progress |
| **Publication** | |
| [lean-pkg-agent](lean-pkg-agent/SKILL.md) | using the lean-pkg registry and trust gate |
| [lean-publishing](lean-publishing/SKILL.md) | Palomar, Zenodo DOI, Reservoir, citation |
| [lean-upstreaming](lean-upstreaming/SKILL.md) | extracting a library or contributing to Mathlib |

Each folder also has `agents/openai.yaml` (display metadata for Codex).
Lean snippets are illustrative shapes; check names against Mathlib at your
pinned revision.

## Deliberately not here

Mathlib search and discovery, naming and style, elaboration performance,
project architecture, multi-agent orchestration and source dependency graphs
are covered well by Scott Armstrong's
[LeanAutoformalizationSkills](https://github.com/scottnarmstrong/LeanAutoformalizationSkills)
(its `lean-search-discovery`, `lean-naming-style`, `lean-elaboration`,
`lean-project-architecture`, `lean-orchestrator`, `lean-statement-audit` and
`build-proof-dependency-graph` skills). The two sets can be installed side
by side. Only the routers share a name (`lean-workflow`): the installer
refuses the second one and reports the conflict, so keep whichever router you
prefer, or pass `--force` to move the other aside.

## License and AI disclosure

Apache-2.0, as the rest of this repository ([LICENSE](../LICENSE)). These
skills were written with AI assistance (Anthropic's Claude) from lessons
recorded during the maintainer's formalization projects, and reviewed by the
maintainer, who is responsible for them.
