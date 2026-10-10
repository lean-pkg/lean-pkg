---
title: Using lean-pkg with agents
---

[&larr; lean-pkg documentation](index.md)

*Agents claim before they work, and never flip to public.*

Coding agents are first-class users of `lean-pkg`. The tool never prompts,
every subcommand has `--json`, and the contract an agent needs is one file.

## The entry points

| Entry | Use |
|---|---|
| [llms.txt](llms.txt) | the exact output of `lean-pkg llms`: standing rules, transitions with required evidence, every subcommand with an example, both receipt schemas, the event schema, exit codes. Fetch this first. |
| `lean-pkg llms` | the same text from the installed tool, always current |
| `lean-pkg llms --short` | about 40 lines cut from it: purpose, standing rules, the first line of each subcommand. Enough to start a session; read the full text before writing evidence |
| [AGENTS.md](https://github.com/lean-pkg/lean-pkg/blob/main/AGENTS.md) | the modification contract for agents working **on** the tool (layout, tests, conventions) |

## The rules an agent must follow

1. Never hand-edit the registry YAML. Use `register` and `update`.
2. Claim a package before working on it with `claim --workspace --agent --reason`,
   and release it when done. A refusal names the holder; do not
   `--force` without a human reason. Work longer than the TTL (24 h by
   default): `renew --agent` before the lease lapses;
   `leases --agent A --expiring 2` shows what is about to lapse.
3. Pass flags and `--json`. Nothing prompts; a prompt path exits 2.
4. `promote` is a dry run unless `--yes`. Never pass `--yes` unless a human
   has read the checklist: the flip to public cannot be undone.
5. Run `comparator-receipt` only where the host policy allows (a Slurm job,
   a host in `COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or an explicit override),
   and only on a clean tree.
6. Treat every refusal as a named fact to report, not an obstacle to work
   around. Refusals are the product.

## A typical agent session

```bash
lean-pkg list --json                                   # what exists, who holds what
lean-pkg next my-proof --agent vera --json             # the one recommended next command, and why (read-only)
lean-pkg claim my-proof --workspace w3 --agent vera --reason "comparator evidence" --json
cd ~/src/my-proof && git status --short                 # must be empty
lake exe cache get && comparator-receipt                 # inside Slurm, or on an allowed host
lean-pkg comparator my-proof --check --receipt .lean-receipts/<c12>-comparator.json --json
git add .lean-receipts && git commit -m "evidence: comparator receipt for <c12>"
lean-pkg comparator my-proof --receipt .lean-receipts/<c12>-comparator.json --json
lean-pkg promote my-proof --json                        # dry run; hand the checklist to a human
lean-pkg leases --agent vera --expiring 2 --json        # long session: renew before the lease lapses
lean-pkg renew my-proof --agent vera --json
lean-pkg release my-proof --agent vera
```

## Disclosure

Work done with AI assistance must be disclosed in pull requests to this
repository and, for packages targeting Mathlib, in the package's
`automation` field (`lean-pkg update NAME --automation "..."`), which the
promotion checklist requires when `upstream_target` is `mathlib`. See
[CONTRIBUTING](https://github.com/lean-pkg/lean-pkg/blob/main/CONTRIBUTING.md).
