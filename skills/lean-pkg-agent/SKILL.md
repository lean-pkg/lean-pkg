---
name: lean-pkg-agent
description: "Drive the lean-pkg registry and trust gate from an agent: read the contract from lean-pkg llms first, never hand-edit the registry, claim before working, gate with a receipt bound to HEAD, attach a comparator receipt, and treat promote as a dry run whose --yes belongs to a human. Trigger phrases: lean-pkg, lean package registry, claim a Lean package, gate receipt, comparator receipt, promote a Lean repo, evidence-only descendant, verified-private, make the formalization public, lean-pkg refused."
license: Apache-2.0
---

# Using lean-pkg from an agent

## Problem

An agent working on a registered Lean package can corrupt shared state
(editing the registry by hand, working without a lease), produce evidence the
gate will refuse (a receipt for the wrong commit), or take an irreversible
step (flipping a repository public) that a human should have read first.

## Why it happens

The registry, the leases and the receipts are a contract between several
agents and humans. Anything learned from memory or an old copy of the docs
drifts from the installed tool's behavior.

## Rule

- **The authoritative contract is `lean-pkg llms`** (the same text is the
  repository's `llms.txt`). Read it at the start of every session; if this
  skill and that output disagree, the output is right.
- Never hand-edit the registry YAML or the event log. Use `register` and
  `update`.
- Claim before working; release when done. A refusal names the holder: report
  it, do not `--force` without a human's reason.
- Work longer than the lease TTL (24 h by default): renew before the TTL
  lapses (`lean-pkg lease renew my-proof --agent my-agent`); check with
  `lean-pkg lease list --agent my-agent --expiring 2`. A lapsed lease cannot be
  renewed: claim again.
- Pass flags and `--json`; nothing prompts. Exit 1 is a refusal with a named
  reason, exit 2 is a usage error.
- `promote` without `--yes` is a dry run. Never pass `--yes`: hand the
  checklist to a human. The flip to public cannot be undone.
- Run `lean-pkg receipt comparator` only where its host policy allows (a Slurm job, a
  host in `COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or `COMPARATOR_RECEIPT_ALLOW_HOST=1`)
  and only on a clean tree.

## How to apply

```bash
lean-pkg llms                                   # the contract; read it
lean-pkg registry board --json                           # or: lean-pkg list --json
lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof --json
lean-pkg lease claim my-proof --workspace w1 --agent my-agent --reason "receipts" --json

# lower tier: build, sorry scan, exact axiom audit at HEAD (lean-receipt-discipline),
# then write receipt.json with the fields lean-pkg llms lists
lean-pkg update my-proof --status gate-pending --reason "build job 4242 finished" --agent my-agent --json
lean-pkg gate my-proof --receipt receipt.json --json
git add .lean-receipts && git commit -m "evidence: gate receipt"   # after gating, not before

# comparator tier, on an allowed host, clean tree
lean-pkg receipt comparator --project .
lean-pkg comparator my-proof --check --receipt .lean-receipts/<c12>-comparator.json --json
git add .lean-receipts && git commit -m "evidence: comparator receipt"
lean-pkg comparator attach my-proof --receipt .lean-receipts/<c12>-comparator.json --json

# long session: renew before the TTL lapses
lean-pkg lease list --agent my-agent --expiring 2 --json
lean-pkg lease renew my-proof --agent my-agent --json

git push                                        # promote needs the evidence committed and HEAD in origin/<branch>
lean-pkg publish my-proof --yes --dry-run --json                # dry run: give the checklist to a human
lean-pkg lease release my-proof --agent my-agent --json
```

Ordering matters: the gate checks that the receipt's commit is HEAD or that
HEAD is an evidence-only descendant (only `.lean-receipts/` differs).
Committing anything else between the receipt and the gate is drift.

Re-certification after new commits: move the package back to `gate-pending`
with `update --status gate-pending --reason ...`, build a fresh receipt at the
new HEAD, gate, then commit the receipt.

When delegating Lean package work to another agent, paste the output of
`lean-pkg llms` into its brief.

## Anti-patterns

- Editing `lean-packages.yaml` "just to fix a typo".
- Committing the receipt before running `gate`.
- Retrying a refused command with `--force` instead of reporting the refusal.
- Running `publish --yes` because the dry run looked clean.

## Verification

`lean-pkg registry validate` exits 0; `lean-pkg status my-proof --json` shows the
expected status and receipt paths; `lean-pkg registry audit --json` reports no
receipt drift; the lease is released.

Learned from: a parallel-registration incident where two hand edits of the
registry diverged, and a first re-gate that committed its receipt before
gating, September 2026.
