---
title: Agent skills
---

[&larr; lean-pkg documentation](index.md)

*Don't build wheels. Depend on them.*

`lean-pkg` ships a set of **skills** for coding agents that formalize
mathematics in Lean 4. A skill is a folder with a `SKILL.md` file: a short,
named lesson that an agent such as Claude Code or Codex loads when a task
matches its description. These twenty skills are lessons from real
formalization failures, rewritten for general use.

They cover what a green `lake build` does not:

- **fidelity**: does the Lean statement say what the paper says?
- **evidence**: is the result bound to exact bytes, and can a stranger
  re-derive it?
- **publication**: does the public claim stay within the evidence?

Source: [`skills/`](https://github.com/lean-pkg/lean-pkg/tree/main/skills)
(Apache-2.0).

## How they relate to llms.txt and the sample workflow

[llms.txt](llms.txt) is the contract for the `lean-pkg` tool: subcommands,
transitions, receipt schemas, exit codes. The skills never restate it; the
`lean-pkg-agent` skill tells the agent to run `lean-pkg llms` first and to
trust it over anything else. The [sample workflow](sample-workflow.md) shows
the tool on a real package; the skills cover the judgment around it: what to
formalize, whether a statement is faithful, what a receipt proves, and what
may be published.

## Install

From a checkout of the repository, paste this into your agent:

```text
Read skills/README.md in this lean-pkg checkout and install its skills for the agent I am using with scripts/install_skills.py, first with --dry-run; do not replace any existing skill, and tell me how to invoke lean-verification-workflow.
```

Or run the installer:

```bash
python3 scripts/install_skills.py --agent claude --dry-run
python3 scripts/install_skills.py --agent codex
python3 scripts/install_skills.py --agent both --project /path/to/lean-project
```

| Agent | User level | Project level (`--project PATH`) |
|---|---|---|
| Claude Code | `~/.claude/skills` | `PATH/.claude/skills` |
| Codex | `~/.agents/skills` | `PATH/.agents/skills` |

The installer symlinks each folder (`--copy` copies), never replaces an
existing skill that is not its own link unless `--force` is given (the old
entry is moved to a `skills.bak` directory beside it), and prints a table of
what it did.

## The skills

| Skill | Use it when |
|---|---|
| `lean-verification-workflow` | choosing a skill (the router) |
| `search-before-building` | before any new definition, lemma or library: Loogle, Mathlib, Reservoir, Palomar, GitHub |
| `lean-fidelity-audit-taxonomy` | checking a Lean statement or definition against the paper |
| `lean-weakened-hypothesis-vacuity` | a relaxed hypothesis is claimed to cover a new case |
| `lean-partial-formalization-boundary` | analytic facts enter as hypotheses or contract fields |
| `statement-vs-use-span-audit` | a lemma may be stated more narrowly than it is used |
| `lean-honest-named-hypotheses` | an input cannot be proved now |
| `lean-prop-data-witnesses` | `Classical.choose` lost a fact, or an `Or` must become data |
| `lean-elaboration-boundaries` | the producer compiles and the consumer does not |
| `lean-large-finite-computation` | a closed Finset, table or `decide` certificate blows up |
| `lean-vendor-namespace-isolation` | two libraries export the same names |
| `lean-reserved-name-mint-site` | `X.congr_simp` or `X.eq_1` appears in the wrong module |
| `lean-axiom-audit` | writing the exact-inventory `#print axioms` audit |
| `lean-receipt-discipline` | binding a result to a commit; choosing a replay layer |
| `lean-shared-cluster-verification` | running a gate or heavy build on a shared cluster without wasted jobs |
| `lean-claim-ledger` | recording what is verified, conditional, partial, excluded |
| `lean-paper-companion` | formalizing alongside a paper in progress |
| `lean-pkg-agent` | using the lean-pkg registry and trust gate |
| `lean-publishing` | Palomar, Zenodo DOI, Reservoir, citation |
| `lean-upstreaming` | extracting a library or contributing to Mathlib |

Mathlib search, naming, elaboration performance, project architecture and
multi-agent orchestration are deliberately left to Scott Armstrong's
[LeanAutoformalizationSkills](https://github.com/scottnarmstrong/LeanAutoformalizationSkills);
the two sets install side by side (only the `lean-verification-workflow` routers share a
name). The full index, with links to each skill, is
[skills/README.md](https://github.com/lean-pkg/lean-pkg/blob/main/skills/README.md).

Written with AI assistance and reviewed by the maintainer; see
[CONTRIBUTING](https://github.com/lean-pkg/lean-pkg/blob/main/CONTRIBUTING.md).
