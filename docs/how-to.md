---
title: How to
---

[&larr; lean-pkg documentation](index.md)

*Dry run first. `--yes` is one-way.*

Task recipes. Every command accepts `--json` for scripts and never prompts
off a terminal.

## See what exists

```bash
lean-pkg board            # curses TUI on a terminal, static table otherwise
lean-pkg list --json      # the same table for scripts
lean-pkg status my-proof  # full record plus the last five history entries
lean-pkg validate         # schema, enum and history sanity; exit 0 or 1
```

## Read one field, or the registry as it was

```bash
lean-pkg status my-proof --fields gate.receipt,lease.agent      # only those keys; dotted paths reach in
lean-pkg list --no-claims --json                                  # drop the bulk cached claims
lean-pkg --registry-snapshot HEAD~3 status my-proof               # read-only, the registry at a commit
lean-pkg --version                                                # which build runs
```

`lean-pkg status --help` lists every key of the package record; so does the
[CLI reference](cli.md#package-record).

## Declare reusable results and search before building

```bash
lean-pkg search "stochastic Fubini" --offline   # registry and index only
lean-pkg search "stochastic Fubini"             # plus Loogle, LeanSearch, Reservoir, Palomar, GitHub
lean-pkg claims sync my-proof                   # read claims.yaml at HEAD, validate, cache
lean-pkg claims list my-proof
lean-pkg claims check my-proof
lean-pkg claims check --file claims.yaml        # schema-check the working copy, no registry
lean-pkg claims migrate my-proof --from old-claims.json --dry-run   # legacy JSON to a claims/1 skeleton
```

`claims.yaml` (schema `claims/1`) at the repository root names the results
someone might reuse. The schema, the search order and the search record:
[Prior-art search](prior-art.md). Packages without synced claims are searched
by declaration name and docstring in their checkout, and a zero now says which
packages were searched that way and which were not searched at all.

Validate `claims.yaml` with `lean-pkg claims check --file claims.yaml` before the
commit you will gate: `gate` refuses a claims file that fails the schema, or
names a declaration missing from its file, at the receipt's commit, and the fix
then costs a new receipt. `--file` checks the schema only; `claims sync` checks
the declarations at `HEAD`.

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
lean-pkg renew my-proof --agent alice                                            # restart a live lease from now
lean-pkg renew my-proof --agent alice --contact "@alice-desk"                    # where to reach the holder (`leases` CONTACT)
lean-pkg leases --agent alice --expiring 2                                        # what lapses within 2 h
lean-pkg release my-proof --agent alice
lean-pkg claim my-proof --workspace w9 --agent bob --force --reason "alice is away"   # logged as claim-forced
```

A refusal names the current holder and the expiry. Work that outlasts the
TTL renews before the lease lapses; a lapsed lease is refused by `renew`
(claim again). `audit` reports expired leases; `audit --fix` clears them.

## Move a package through the lifecycle by hand

```bash
lean-pkg update my-proof --status gate-pending --reason "lower-tier check job 4242 submitted"
lean-pkg update my-proof --status active       --reason "check abandoned"
lean-pkg update my-proof --upstream-target mathlib --automation "Proofs drafted with <model>, reviewed by <name>"
lean-pkg update my-proof --reaudit-after 2027-01-15
```

`verified-private` is reachable only through `gate`, and `public` only
through `promote --yes`. See [Lifecycle](lifecycle.md).

## Move a package to another checkout

When a package registered from an in-paper `lean/` tree moves to a dedicated
repository, relocate it instead of registering a second name. The name,
history and claims cache stay with it:

```bash
lean-pkg update my-proof --path src/my-proof --reason "split into its own repo" --dry-run
lean-pkg update my-proof --path src/my-proof --reason "split into its own repo" --agent vera --redetect
```

The path is `$HOME`-relative, like `register --path`. `update` refuses the
current path, a path another package already holds, and a live lease held by
another agent (the holder passes `--agent`; `--force` is not accepted). It
never touches the file system; a path absent on this host is recorded with a
note. Evidence bound to a commit survives only when the new checkout's HEAD
is that commit (or an evidence-only descendant of it): otherwise the gate
receipt, the comparator receipt and the cached claims are cleared, a
`verified-private` package returns to `gate-pending`, and the `relocated`
event lists what was cleared. Without `--redetect`, `lean_toolchain` and
`mathlib_pin` are left as recorded.

## Produce the lower-tier receipt

Any executor works: a Slurm job, a CI run, a workstation. The receipt must
bind the commit, the toolchain, the Mathlib rev, `sorry_count` (must be 0),
the axioms, the SHA-256 of a frozen report, and the executor's job identity.
Schema: [Receipts and trust](receipts.md).

From a finished Slurm job, `lean-pkg receipt` writes it for you:

```bash
lean-pkg receipt slurm --job 123 --commit "$(git rev-parse HEAD)" --log verify.log \
  --stdout job-123.out --stderr job-123.err --checker "verify.sh (lake build; #print axioms)" --append-readme
lean-pkg receipt slurm ... --sacct-file sacct.txt   # a host without Slurm: sacct output saved on the cluster
```

It checks that the job finished `COMPLETED` with `0:0`, that the stdout's
`verify-log-sha256` equals the raw log's hash and its pre- and post-job
manifest hashes equal the committed `lake-manifest.json`, that stderr is
empty, and that the toolchain, Mathlib rev, sorry count and axioms hold. It
then redacts the per-job temporary paths, refuses while a private string
remains, and writes `<c12>-slurm-<J>.json`, the redacted log and (with
`--append-readme`) the `.lean-receipts/README.md` entry; then run the
`gate NAME --check --receipt` command it prints. The lines it reads:
[A lower-tier receipt from a Slurm job](receipts.md#a-lower-tier-receipt-from-a-slurm-job).

Light checks run locally in a capped systemd unit instead: `receipt systemd`
runs the verify script bounded (`systemd-run --user`, `MemoryMax`,
`CPUQuota`, `RuntimeMaxSec`), captures the unit's exit and resource
accounting before the unit is collected, and writes the same receipt:

```bash
lean-pkg receipt systemd --unit audit-gate --commit "$(git rev-parse HEAD)" --log /tmp/gate/verify.log \
  --stdout /tmp/gate/unit.out --stderr /tmp/gate/unit.err --checker "gate.sh" --run -- bash gate.sh /tmp/gate/verify.log
lean-pkg receipt systemd --unit audit-gate ... --properties-file unit.properties   # a unit that already ran
```

It refuses a unit that is not found, not finished, killed by the OOM
killer, a signal or the runtime cap, or that exited non-zero, and a
checkout that changed during the run:
[A lower-tier receipt from a systemd unit](receipts.md#a-lower-tier-receipt-from-a-systemd-unit).

## Check that this machine can produce comparator receipts

```bash
lean-pkg comparator host-check          # needs no registry; exit 0 when a mechanism is usable
```

One `ok`/`warn`/`fail` line per requirement, then a verdict naming the usable
mechanisms. Run it before a long `comparator-receipt` run. Details:
[Checking a host first](comparator.md#checking-a-host-first).

## Produce the comparator receipt

```bash
cd ~/src/my-proof && git status --short     # must be empty
lake exe cache get
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt
```

Or inside Slurm: `srun ... comparator-receipt`. The runner refuses on a dirty
tree, naming the files, and writes nothing unless every configuration passes.
Details and the host policy: [The comparator tier](comparator.md). Adding
the comparator tier to a package that already builds, step by step:
[Add the comparator tier to an existing package](existing-package-comparator.md).

## Scaffold comparator configurations from the claims

```bash
lean-pkg comparator config --from-claims my-proof --dry-run              # one entry per uncovered theorem claim
lean-pkg comparator config --from-claims my-proof --challenge-from-type  # pre-fill the elaborated statements
lean-pkg comparator config --coverage my-proof                           # covered / not covered at the gate-bound commit
```

Review every pre-filled statement by hand. Details, including `--checkout`
and the scratch mode under another agent's lease:
[Scaffolding entries from claims.yaml](comparator.md#scaffolding-entries-from-claimsyaml).

## Plan a long comparator run

```bash
comparator-receipt --estimate           # time a sample of slots; no receipt
comparator-receipt --judges 4           # shard the slots (helps only for disjoint closures)
comparator-receipt --slots sample:5     # a partial run: never accepted as evidence
```

A rerun resumes from the slots already accepted (`--no-resume` judges every
slot again); each stage logs under `comparator-stages/`. When the flags pay
off: [Runtime](comparator.md#runtime).

## Gate, with or without comparator evidence

```bash
lean-pkg gate my-proof --receipt receipt.json                                  # lower tier only
lean-pkg gate my-proof --receipt receipt.json --comparator .lean-receipts/<c12>-comparator.json
```

Re-running `gate` with an identical receipt (pair) is a no-op success, so
retries are safe. A different comparator receipt for an already-gated
package goes through `comparator`, not `gate`.

A `verified-private` package whose HEAD moved is re-gated in one step, one
registry commit, with the receipt for the new commit:

```bash
lean-pkg gate my-proof --check --regate --receipt receipt.json                 # read-only
lean-pkg gate my-proof --receipt receipt.json --regate                         # event regated, status unchanged
```

The new commit must descend from the gated one; otherwise pass `--reason TEXT`
(recorded as `non_descendant: true`).

## Attach or re-check comparator evidence later

```bash
lean-pkg comparator my-proof --receipt .lean-receipts/<c12>-comparator.json          # record it (event comparator-attached)
lean-pkg comparator my-proof --receipt .lean-receipts/<c12>-comparator.json --check  # validate only, any status
```

## Render formalization.yaml

```bash
lean-pkg formalization my-proof            # print to stdout
lean-pkg formalization my-proof --write    # write it and set the registry pointer
```

The mathlib-initiative v0.4 report Palomar reads, derived from the registry,
the comparator configurations and the receipts; `audit` reports drift. Derived
versus kept keys: [formalization.yaml](formalization.md).

## Commit the evidence without invalidating it

Commit only the `.lean-receipts/` files after the run. The new HEAD is then
an *evidence-only descendant* of the certified commit and both receipts stay
valid. Any other change (a source edit, a README tweak in the same commit)
is drift and must be re-certified.

```bash
git add .lean-receipts && git commit -m "evidence: receipts for <c12>"
```

## Scan for private strings before publishing

```bash
lean-pkg public-safety my-proof            # tree plus every reachable blob and commit message
lean-pkg public-safety my-proof --strict   # warn-only hits block too
```

Package-specific patterns go in an untracked `.lean-pkg-private-patterns`.
Each line is a regular expression: bare or `block:` blocks, `warn:` warns,
`allow:` exempts, each optionally scoped to a path glob; the built-in
`latex-label` family is warn-only.
`promote` runs the same scan and refuses while blocking hits remain.

The scan also blocks names taken from the package's own record: its agent,
workspace and lease holder. A progress file that records who holds the lease
is therefore a hit. Write the line in its redacted form, which never goes
stale either:

```text
- lease: see `lean-pkg status my-proof --fields lease`
```

or keep the name and exempt it in that one file with a scoped allow of the
exact name, `allow: PROGRESS.md: ^<agent-name>$` (escape `.` as `\.`); the
same name anywhere else is still a hit.

## Publish a package incubated beside a paper

When a hit is in the history, export a fresh-history repository instead of
rewriting the old one:

```bash
lean-pkg release my-proof --fresh-repo ~/src/my-proof-public --dry-run
lean-pkg release my-proof --fresh-repo ~/src/my-proof-public
lean-pkg release my-proof --adopt-repo ~/src/my-proof-public    # or: adopt one built by hand
lean-pkg release my-proof --fresh-repo ~/src/my-proof-public --from-tree ~/src/my-proof-cleaned   # or: a hand-cleaned tree
```

Evidence is cleared (a receipt binds one commit), so gate and run the
comparator again on the new commit, then `publish --dry-run` and `promote`.
The full sequence: [Publishing a package incubated beside private work](receipts.md#publishing-a-package-incubated-beside-private-work).

## Publish

```bash
lean-pkg promote my-proof        # dry run: read every line of the checklist
lean-pkg promote my-proof --yes  # ONE-WAY: gh repo edit --visibility public
```

Optional notification after the flip: set `LEAN_PKG_NOTIFY_CMD` to a command
that reads the message from stdin.

## Get listed: publish checklist, Reservoir, Palomar

```bash
lean-pkg publish my-proof --dry-run          # read-only checklist from verified-private to listed
lean-pkg reservoir check my-proof            # would Reservoir list it? exit 0 eligible, 1 not
lean-pkg palomar prepare my-proof --dry-run  # the submission bundle; nothing is sent
lean-pkg record my-proof palomar-submitted --field KEY=VALUE   # each external step, its required fields
```

`record` takes the fields each event requires (see the
[CLI reference](cli.md) row). Reservoir's criteria are in the
[FAQ](faq.md#reservoir).

## Add an entry to the index

```bash
lean-pkg index add my-proof --by you --headline 'NAME :: FILE :: SUMMARY' --from-registry
lean-pkg index check --clone my-proof        # re-derive everything from bytes at the pin
lean-pkg index render
```

Trust is computed from the evidence, never typed. Schema and review:
[The index](index-catalog.md).

## Find drift, expired leases, and GitHub mismatches

```bash
lean-pkg audit            # report only: expired leases, receipt-drift, comparator-drift, visibility mismatch
lean-pkg audit --fix      # clear expired leases; copy GitHub's visibility and archived flag into the registry
```

## Share one registry across machines

Keep checkouts at the same `$HOME`-relative paths on every machine and
keep the registry file plus the event log in their own git repository that
every machine clones. `register` and the drift checks resolve `local_path`
against the local `$HOME`. A package whose checkout is absent on this host is
reported as `missing-path` by `audit` and refused by the commands that need
the files.

Let lean-pkg do the git part. With committed mode on, every write pulls
(`--ff-only`) before it touches the files and commits the two files together
afterwards; with push on, it also pushes:

```bash
export LEAN_PKG_COMMIT=1 LEAN_PKG_PUSH=1     # e.g. in the shell profile of each machine
lean-pkg claim my-lean --workspace w3 --agent vera
# or per command:
lean-pkg claim my-lean --workspace w3 --agent vera --commit --push
```

A clone that has diverged from `origin`, or registry files with uncommitted
edits, is refused before anything is written. A push that hits a transient
network error is retried with backoff (`LEAN_PKG_PUSH_RETRIES`, default 4),
and one rejected because another writer pushed first is rebased onto
`origin/<branch>` and pushed again. A push that still fails exits 1 and keeps
the local commit; run the `git push origin <branch>` it prints. `--no-commit`
turns the mode off for one command, and `lean-pkg doctor --fetch` shows how
the clone stands against `origin`.

Writers on one host serialize on a lock file next to the registry,
`lean-packages.yaml.lock`. It is created on first use and never deleted;
keep it out of git:

```bash
echo lean-packages.yaml.lock >> .gitignore    # in the registry repository
```

On each machine, link the user-level default to the clone once, so every
shell, including non-interactive SSH, finds the registry without
`LEAN_PKG_REGISTRY` or `--registry`:

```bash
mkdir -p ~/.lean-pkg && ln -s /path/to/registry-repo ~/.lean-pkg/registry
lean-pkg doctor      # registry: ... [source: user-default]
```

## Upgrade an old registry

```bash
lean-pkg migrate --dry-run
lean-pkg migrate                             # additive and idempotent
```

## Render a status page

```bash
lean-pkg wiki --out status/index.md
```

## Find the next command for a package

```bash
lean-pkg next NAME            # one recommended command and a one-line reason
lean-pkg next NAME --json     # {command, reason, blockers[], state{}}
```

Read-only and offline: it decides from the registry record and the checkout, with the same checks
`gate`, `comparator` and `publish --dry-run` use, and prints the one command to run next (`claim`,
`gate --check`, `comparator-receipt`, `publish --dry-run`, ...). Exit 1 names what blocks the package
(a foreign lease with `--agent`, a dirty tree while evidence is produced, a receipt `gate` would refuse).
A `note:` line (and `--json` `notes[]`) says when the checkout is on a branch other than the
default, or detached; the current HEAD is still what is evaluated.
See the [`next` row](cli.md) in the CLI reference.

## Report a problem

```bash
lean-pkg report --command "status demo --fields pin"     # dry run: prints the issue body
lean-pkg report --command "..." --package NAME --yes    # files it on GitHub with gh
lean-pkg report --body "what happened" --from-log stage.log:80   # no re-run: free text + the last 80 log lines
```

The body follows the repository's bug-report template (version, command, output, `doctor` and
`host-check` lines, the package record with `--package`). Every captured text is redacted first:
home and checkout paths, e-mail addresses, the public-safety families and the names derived from the
registry record; a hit the redaction cannot replace refuses to file. `--enhancement` labels a feature
request; `--json` gives `{url, title, labels, body, redactions}`. One of `--command`, `--body` or
`--from-log` is required: friction inside a long stage (a failed `comparator-receipt` build) has no cheap
re-run, so describe it with `--body` and attach the tail of its log with `--from-log`. See the
[`report` row](cli.md).

## Use lean-pkg from a coding agent

See [Using lean-pkg with agents](agents.md). Short version: the agent reads
[llms.txt](llms.txt) (the same text as `lean-pkg llms`; `lean-pkg llms --short` is the 40-line version), always passes `--json`, asks `lean-pkg next NAME` when unsure what comes next, claims before working, and
never calls `promote --yes` without a human reading the dry run.
