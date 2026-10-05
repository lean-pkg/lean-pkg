---
title: How to
---

# How to

Task recipes. Every command accepts `--json` for scripts and never prompts
off a terminal.

## See what exists

```bash
lean-pkg board            # curses TUI on a terminal, static table otherwise
lean-pkg list --json      # the same table for scripts
lean-pkg status my-proof  # full record plus the last five history entries
lean-pkg validate         # schema, enum and history sanity; exit 0 or 1
```

## Add a package

```bash
lean-pkg register my-proof --repo git@github.com:you/my-proof.git --path src/my-proof \
    [--owner-project paper-7] [--visibility private] [--status active] \
    [--upstream-target none|mathlib|palomar] [--automation "..."] [--notes "..."] [--dry-run]
```

`register` is the only way to add a package. It refuses a duplicate name and
a duplicate `local_path`, and it auto-detects the toolchain and the Mathlib
pin from the checkout when it exists on this host.

## Work on a package without stepping on someone

```bash
lean-pkg claim my-proof --workspace laptop --agent alice --reason "chapter 2"   # CAS lease, 24 h default
lean-pkg claim my-proof --workspace laptop --agent alice --ttl 72                 # same agent: refresh
lean-pkg release my-proof --agent alice
lean-pkg claim my-proof --workspace w9 --agent bob --force --reason "alice is away"   # logged as claim-forced
```

A refusal names the current holder and the expiry. `audit` clears expired
leases.

## Move a package through the lifecycle by hand

```bash
lean-pkg update my-proof --status gate-pending --reason "lower-tier check job 4242 submitted"
lean-pkg update my-proof --status active       --reason "check abandoned"
lean-pkg update my-proof --upstream-target mathlib --automation "Proofs drafted with <model>, reviewed by <name>"
lean-pkg update my-proof --reaudit-after 2027-01-15
```

`verified-private` is reachable only through `gate`, and `public` only
through `promote --yes`. See [Lifecycle](lifecycle.md).

## Produce the lower-tier receipt

`lean-pkg` validates this receipt but does not produce it. Any executor
works: a Slurm job, a CI run, a workstation. The receipt must bind the
commit, the toolchain, the Mathlib rev, `sorry_count` (must be 0), the
axioms, the SHA-256 of a frozen report, and the executor's job identity.
Schema: [Receipts and trust](receipts.md).

## Produce the comparator receipt

```bash
cd ~/src/my-proof && git status --short     # must be empty
lake exe cache get
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt
```

Or inside Slurm: `srun ... comparator-receipt`. The runner refuses on a dirty
tree, naming the files, and writes nothing unless every configuration passes.
Details and the host policy: [The comparator tier](comparator.md).

## Gate, with or without comparator evidence

```bash
lean-pkg gate my-proof --receipt receipt.json                                  # lower tier only
lean-pkg gate my-proof --receipt receipt.json --comparator .lean-receipts/<c12>-comparator.json
```

Re-running `gate` with an identical receipt (pair) is a no-op success, so
retries are safe. A different comparator receipt for an already-gated
package goes through `comparator`, not `gate`.

## Attach or re-check comparator evidence later

```bash
lean-pkg comparator my-proof --receipt .lean-receipts/<c12>-comparator.json          # record it (event comparator-attached)
lean-pkg comparator my-proof --receipt .lean-receipts/<c12>-comparator.json --check  # validate only, any status
```

## Commit the evidence without invalidating it

Commit only the `.lean-receipts/` files after the run. The new HEAD is then
an *evidence-only descendant* of the certified commit and both receipts stay
valid. Any other change (a source edit, a README tweak in the same commit)
is drift and must be re-certified.

```bash
git add .lean-receipts && git commit -m "evidence: receipts for <c12>"
```

## Publish

```bash
lean-pkg promote my-proof        # dry run: read every line of the checklist
lean-pkg promote my-proof --yes  # ONE-WAY: gh repo edit --visibility public
```

Optional notification after the flip: set `LEAN_PKG_NOTIFY_CMD` to a command
that reads the message from stdin.

## Find drift, expired leases, and GitHub mismatches

```bash
lean-pkg audit            # report only: lease sweep, receipt-drift, comparator-drift, visibility mismatch
lean-pkg audit --fix      # also copy GitHub's visibility and archived flag into the registry
```

## Share one registry across machines

Keep checkouts at the same `$HOME`-relative paths on every machine and
commit the registry file plus the event log to a repository the machines
pull. `register` and the drift checks resolve `local_path` against the local
`$HOME`. A package whose checkout is absent on this host is reported as
`missing-path` by `audit` and refused by the commands that need the files.

## Render a status page

```bash
lean-pkg wiki --out status/index.md
```

## Use lean-pkg from a coding agent

See [Using lean-pkg with agents](agents.md). Short version: the agent reads
[llms.txt](llms.txt), always passes `--json`, claims before working, and
never calls `promote --yes` without a human reading the dry run.
