---
title: How to
---

[&larr; lean-pkg documentation](index.md)

*Dry run first. `--yes` is one-way.*

Task recipes. Every command accepts `--json` for scripts and never prompts
off a terminal.

## See what exists

```bash
lean-pkg registry board            # curses TUI on a terminal, static table otherwise
lean-pkg list --json      # the same table for scripts
lean-pkg status my-proof  # full record plus the last five history entries
lean-pkg registry validate         # schema, enum and history sanity; exit 0 or 1
```

New here? `lean-pkg help start` prints the five verbs and the two product
tracks. Coordination and maintenance commands live under three nouns:
`lean-pkg lease claim|renew|release|list` (agents sharing a registry),
`lean-pkg registry board|init|migrate|validate|audit|wiki` (the registry
file) and `lean-pkg repo move NAME PATH` (the package's checkout). The
short spellings above (`board`, `validate`, `claim`, ...) are permanent
aliases: `lean-pkg help aliases` lists them.

## Keep the receipts bound after the page commit

Commit `lean-pkg-binding.yaml` (`docs_descendants: true`) with the package
before the verify run, and a later README or `coverage.yaml` commit keeps
the receipts bound; an outside `receipt check` then passes without flags.
`lean-pkg new` writes the file. For an older package that opted in through
the registry (deprecated: `gate` and `publish` print `WARN: deprecated:`):

```bash
lean-pkg update my-proof --write-binding-file --dry-run   # the file the record gives
lean-pkg update my-proof --write-binding-file             # write it; nothing staged
git add lean-pkg-binding.yaml && git commit -m "Opt in to docs-only descendants"
lean-pkg verify my-proof                                  # new receipts, at a commit with the file
```

Commit the file on its own, right before the verify run: until that run,
the current receipts do not bind a commit that added it
([docs-only descendants](receipts.md#docs-only-descendants-opt-in)).

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
lean-pkg claims check my-proof                  # the cached claims at the synced commit (commit + sync first)
lean-pkg claims check --file claims.yaml        # schema-check the working copy, no registry
lean-pkg claims migrate my-proof --from old-claims.json --dry-run   # legacy JSON to a claims/1 skeleton
lean-pkg claims mark my-proof --from-comparator comparator.json --status reusable          # plan: the compared claims
lean-pkg claims mark my-proof --from-comparator comparator.json --status reusable --write  # edit claims.yaml (not committed)
```

Only `reusable` claims are required by the claims ledger, so a package whose
claims are all `experimental` has a ledger that is `unknown` and can never be
promoted; `claims sync` and `claims check` warn about it. `claims mark` sets
the status of exactly the claims a comparator configuration's `theorem_names`
cover, by the rule `comparator coverage` uses, and lists the names
that cover no claim. Then commit, `claims sync`, and re-gate.

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

`coverage.yaml` has the same kind of check before registration:

```bash
lean-pkg coverage check --file coverage.yaml --claims claims.yaml --comparator comparator.json
```

reads no registry and no receipt, runs the schema and length limits, the row
claim ids against `claims.yaml` and the comparator theorems against the rows,
and lists the checks it skipped ([coverage check](commands/coverage-check.md)).
`lean-pkg verify` runs both checks on the files committed at `HEAD` as its step
0, and stops before any receipt when one refuses.

## Add a package

```bash
lean-pkg register my-proof --url git@github.com:you/my-proof.git --path src/my-proof \
    [--owner-project paper-7] [--visibility private] [--status active] \
    [--upstream-target none|mathlib|palomar] [--automation "..."] [--notes "..."] [--dry-run]
```

`register` is the only way to add a package. It refuses a duplicate name and
a duplicate `local_path`, and it auto-detects the toolchain and the Mathlib
pin from the checkout when it exists on this host.

## Work on a package without stepping on someone

```bash
lean-pkg lease claim my-proof --workspace laptop --agent alice --reason "chapter 2"   # CAS lease, 24 h default
lean-pkg lease claim my-proof --workspace laptop --agent alice --ttl 72                 # same agent: refresh
lean-pkg lease renew my-proof --agent alice                                            # restart a live lease from now
lean-pkg lease renew my-proof --agent alice --contact "@alice-desk"                    # where to reach the holder (`leases` CONTACT)
lean-pkg lease list --agent alice --expiring 2                                        # what lapses within 2 h
lean-pkg lease release my-proof --agent alice
lean-pkg lease claim my-proof --workspace w9 --agent bob --force --reason "alice is away"   # logged as claim-forced
```

A refusal names the current holder and the expiry. Work that outlasts the
TTL renews before the lease lapses; a lapsed lease is refused by `lease renew`
(claim again). `registry audit` reports expired leases; `registry audit --write`
clears them. `status` ends with `lease: EXPIRED ...` once a lease has lapsed and
with `lease: expires in ...` in its last two hours, and any other command on the
package says the same on stderr, so a holder that went idle hears it on its
next command. The lease commands are `lean-pkg lease claim`, `lease renew`,
`lease release` and `lease list`.

## Move a package through the lifecycle by hand

```bash
lean-pkg update my-proof --status gate-pending --reason "lower-tier check job 4242 submitted"
lean-pkg update my-proof --status active       --reason "check abandoned"
lean-pkg update my-proof --upstream-target mathlib --automation "Proofs drafted with <model>, reviewed by <name>"
lean-pkg update my-proof --reaudit-after 2027-01-15
```

`verified-private` is reachable only through `gate`, and `public` only
through `publish --yes`. See [Lifecycle](lifecycle.md).

## Withdraw a publication the owner made private

```bash
lean-pkg update my-proof --withdraw-publication --reason "owner made it private" --dry-run
lean-pkg update my-proof --withdraw-publication --reason "owner made it private"
lean-pkg update my-proof --release-publication-hold --reason "owner approved publishing again"   # later, if ever
```

The record becomes private with a publication hold that `promote` refuses on;
the promotion stays in the history. See
[Withdrawing publication](lifecycle.md#withdrawing-publication).

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

## Verify a package in one step

On a workstation with systemd, `verify` produces both receipts at the
checkout's HEAD and gates the package, printing each command before it runs:

```bash
lean-pkg verify my-proof --dry-run     # the three steps and their exact commands; nothing runs
lean-pkg verify my-proof               # receipt comparator, receipt systemd --run, gate (in that order)
git add .lean-receipts && git commit -m "evidence: receipts"
```

It needs a clean commit, the scaffold's `verify.sh` (`--script FILE` names
another) and a comparator configuration; an `active` package is moved to
`gate-pending` first, a gated one is re-gated (`--reason` for a commit that
does not descend from the gated one). It commits nothing in the checkout, and
writes only what the three commands write. The sections below are the same
steps by hand, for a cluster, CI or a custom check
([`verify`](commands/verify.md)).

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
lean-pkg receipt slurm ... --seal-sacct             # optional: keep the sacct record; gate rechecks it at HEAD
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
With `--seal-sacct` the job's accounting record is sealed beside the receipt and `gate` refuses the receipt
unless that record is committed and unchanged:
[Sealing the sacct record](receipts.md#sealing-the-sacct-record---seal-sacct).

Light checks run locally in a capped systemd unit instead: `receipt systemd`
runs the verify script bounded (`systemd-run --user`, `MemoryMax`,
`CPUQuota`, `RuntimeMaxSec`), captures the unit's exit and resource
accounting before the unit is collected, and writes the same receipt:

```bash
lean-pkg receipt systemd --unit audit-gate --receipt-commit "$(git rev-parse HEAD)" --log /tmp/gate/verify.log \
  --stdout /tmp/gate/unit.out --stderr /tmp/gate/unit.err --checker "gate.sh" --run -- bash gate.sh /tmp/gate/verify.log
lean-pkg receipt systemd --unit audit-gate ... --properties-file unit.properties   # a unit that already ran
```

It refuses a unit that is not found, not finished, killed by the OOM
killer, a signal or the runtime cap, or that exited non-zero, and a
checkout that changed during the run:
[A lower-tier receipt from a systemd unit](receipts.md#a-lower-tier-receipt-from-a-systemd-unit).

No cluster and a public GitHub repository: let GitHub Actions run the
verify script and write the receipt (`receipt ci`, run by lean-pkg's
reusable `verify.yml`), then fetch it:

```bash
lean-pkg receipt ci --template > .github/workflows/lean-pkg.yml   # commit and push it
lean-pkg receipt ci --collect 11622958301   # after a green run: download, check, copy into .lean-receipts/
```

`--collect` never commits or pushes; commit the two files yourself. The
receipt backs `compute: [{cluster: github-actions, jobs: [gha-R-A]}]`:
[Writing the receipt in CI](sites.md#writing-the-receipt-in-ci) and
[A lower-tier receipt from GitHub Actions](receipts.md#a-lower-tier-receipt-from-github-actions).

## Check that this machine can produce comparator receipts

```bash
lean-pkg comparator host-check          # needs no registry; exit 0 when a mechanism is usable
```

One `ok`/`warn`/`fail` line per requirement, then a verdict naming the usable
mechanisms. Run it before a long `lean-pkg receipt comparator` run. Details:
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
lean-pkg comparator coverage my-proof                           # covered / not covered at the gate-bound commit
```

Review every pre-filled statement by hand. Details, including `--checkout`
and the scratch mode under another agent's lease:
[Scaffolding entries from claims.yaml](comparator.md#scaffolding-entries-from-claimsyaml).

## Plan a long comparator run

```bash
lean-pkg receipt comparator --estimate           # time a sample of slots; no receipt
lean-pkg receipt comparator --judges 4           # shard the slots (helps only for disjoint closures)
lean-pkg receipt comparator --slots sample:5     # a partial run: never accepted as evidence
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
lean-pkg comparator attach my-proof --receipt .lean-receipts/<c12>-comparator.json          # record it (event comparator-attached)
lean-pkg comparator check my-proof --receipt .lean-receipts/<c12>-comparator.json  # validate only, any status
```

## Render formalization.yaml

```bash
lean-pkg formalization render my-proof            # print to stdout
lean-pkg formalization render my-proof --write    # write it and set the registry pointer
```

The mathlib-initiative v0.4 report Palomar reads, derived from the registry,
the comparator configurations and the receipts; `audit` reports drift. Derived
versus kept keys: [formalization.yaml](formalization.md).

## Check a blueprint against the Lean code

```bash
lean-pkg blueprint check my-proof          # exit 0 nothing missing, 1 otherwise
```

Runs [checkdecls](https://github.com/PatrickMassot/checkdecls) on the
`blueprint/lean_decls` that leanblueprint writes, after `lake build` at a
clean HEAD, and checks that every claim the blueprint names is a declaration
checkdecls accepts. The package adds checkdecls as a pinned Lake dependency
itself; a package without a blueprint is skipped:
[Blueprint cross-check](page.md#blueprint-cross-check).

## Anchor a companion to an arXiv version

```bash
lean-pkg formalization anchor my-proof --arxiv <id>v<N> --from-file e-print.tar.gz --compare-tex paper.tex
lean-pkg formalization anchor my-proof --arxiv <id>v<N> --from-file e-print.tar.gz --compare-tex paper.tex --write
```

Hashes the posted e-print, compares its main `.tex` with yours and records
both under `data/source-baselines/`; `--write` pins `coverage.yaml` and
`formalization.yaml` to that version. `--fetch` downloads the e-print instead:
[Anchoring to an arXiv version](paper-companion.md#anchoring-to-an-arxiv-version).

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
lean-pkg public-safety my-proof            # tree plus every reachable blob, commit message, identity and tag
lean-pkg public-safety my-proof --package-only   # only the package directory of a larger repository
lean-pkg public-safety my-proof --strict   # warn-only hits block too
lean-pkg public-safety my-proof --gzip-limit 1G   # decode each gzip file up to 1 GiB (default 512M; 0: off)
```

Package-specific patterns go in an untracked `.lean-pkg-private-patterns`.
Each line is a regular expression: bare or `block:` blocks, `warn:` warns,
`allow:` exempts, each optionally scoped to a path glob; the built-in
`latex-label` family is warn-only.
`promote` runs the same scan and refuses while blocking hits remain.
The built-ins catch generic private strings only: a scan with no project-specific
`block:` line says `note: no project-specific patterns: built-ins only`, and its
0 hits do not mean the history is clean. Before freezing a public candidate made
from private history, follow [Before freezing a public candidate](receipts.md#before-freezing-a-public-candidate).

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

Strings that are public on purpose (a fixture address under `.invalid`, which
is never declared by being used, or a generic CI runner path) can be allowed
in a committed `.lean-pkg-public-allow` instead: `allow:` lines only (any
other line there is ignored with a warning), read by every scan, and never
able to exempt a private string written into the file itself (that is the
blocking hit `public-allow-private`). `repo export` writes it from
the patterns file's allow lines that its own scan used (an unused one is
dropped: an allow pattern names what it allows).

## Publish a package incubated beside a paper

When a hit is in the history, export a fresh-history repository instead of
rewriting the old one:

```bash
lean-pkg repo export my-proof ~/src/my-proof-public --dry-run
lean-pkg repo export my-proof ~/src/my-proof-public
lean-pkg repo adopt my-proof ~/src/my-proof-public    # or: adopt one built by hand
lean-pkg repo export my-proof ~/src/my-proof-public --from-tree ~/src/my-proof-cleaned   # or: a hand-cleaned tree
```

The export commit's author and committer come from `--author 'NAME <EMAIL>'`
or the source repository's local git config, never your global identity, and
must be a GitHub noreply address unless `--allow-author-email` is given:

```bash
git -C ~/src/my-proof config user.name "Ada"
git -C ~/src/my-proof config user.email "12345+ada@users.noreply.github.com"
lean-pkg repo export my-proof ~/src/my-proof-public --author 'Ada <12345+ada@users.noreply.github.com>'   # or explicitly
```

Evidence is cleared (a receipt binds one commit), so gate and run the
comparator again on the new commit (`lean-pkg verify my-proof`), then `publish` and `publish --yes`.
The full sequence: [Publishing a package incubated beside private work](receipts.md#publishing-a-package-incubated-beside-private-work).
Before the flip, give the new repository's README its credits (lean-pkg, and
the compute when the receipts show where it ran): [Credits](paper-companion.md#credits).

## Publish

```bash
lean-pkg publish my-proof --yes --dry-run   # dry run: read every line of the promotion checklist
lean-pkg publish my-proof --yes             # ONE-WAY: gh repo edit --visibility public
```

Optional notification after the flip: set `LEAN_PKG_NOTIFY_CMD` to a command
that reads the message from stdin.

## Get listed: publish checklist, Reservoir, Palomar

```bash
lean-pkg publish my-proof                    # read-only checklist from verified-private to listed
lean-pkg reservoir check my-proof            # would Reservoir list it? exit 0 eligible, 1 not
lean-pkg palomar check my-proof              # read-only: every gap to Palomar's rules; exit 0 ready, 1 gaps
lean-pkg palomar port-modules my-proof       # dry run: the module-system port as a diff; --write applies it
lean-pkg palomar prepare my-proof --dry-run  # the submission bundle; nothing is sent
lean-pkg record my-proof palomar-submitted --field KEY=VALUE   # each external step, its required fields
```

`record` takes the fields each event requires (see the
[command reference](commands/record.md)). Reservoir's criteria are in the
[FAQ](faq.md#reservoir).

Run `palomar check` early, before the gated waves, not only before the
release: Palomar's verifier needs a `module` header in every `.lean` file of
the repository, and porting to the module system (`module`, `public import`,
`@[expose]`) changes build inputs, so it forces a re-gate.
`palomar port-modules` plans that port (a diff, plus the definitions likely
to need `@[expose]`) and `--write` applies it to a clean tree, uncommitted. `gate --check` (for a package
aiming at Palomar) and `publish --dry-run` print a `palomar:` warning with the gap count. The rules,
their source and their date: [FAQ](faq.md#palomar-readiness).

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
lean-pkg registry audit            # report only: expired leases, receipt-drift, comparator-drift, visibility mismatch
lean-pkg registry audit --write      # clear expired leases; copy GitHub's visibility and archived flag into the registry
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
lean-pkg lease claim my-lean --workspace w3 --agent vera
# or per command:
lean-pkg lease claim my-lean --workspace w3 --agent vera --commit --push
```

A clone that has diverged from `origin`, or registry files with uncommitted
edits, is refused before anything is written. A push that hits a transient
network error is retried with backoff (`LEAN_PKG_PUSH_RETRIES`, default 4),
and one rejected because another writer pushed first is rebased onto
`origin/<branch>` and pushed again. A push that still fails exits 1 and keeps
the local commit; run the recovery it prints (`git pull --rebase origin <branch>`
then `git push origin <branch>` after a rejection). `--no-commit`
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
lean-pkg registry migrate --dry-run
lean-pkg registry migrate                             # additive and idempotent
```

## Render a status page

```bash
lean-pkg registry wiki --out status/index.md
```

## Find the next command for a package

```bash
lean-pkg next NAME            # one recommended command and a one-line reason
lean-pkg next NAME --json     # {command, reason, blockers[], state{}}
```

Read-only and offline: it decides from the registry record and the checkout, with the same checks
`gate`, `comparator` and `publish --dry-run` use, and prints the one command to run next (`claim`,
`gate --check`, `lean-pkg receipt comparator`, `publish --dry-run`, ...). Exit 1 names what blocks the package
(a foreign lease with `--agent`, a dirty tree while evidence is produced, a receipt `gate` would refuse).
A `note:` line (and `--json` `notes[]`) says when the checkout is on a branch other than the
default, or detached; the current HEAD is still what is evaluated.
See [`lean-pkg next`](commands/next.md) in the command reference.

## Recover an interrupted campaign, or check old receipts after an upgrade

`next --repo DIR` and `gate --check --repo DIR` cover one checkout. Across several candidate checkouts and
collected job directories, `git`, `receipt check --json`, `receipt slurm --check --json`, `squeue` and `sacct`
compose into a read-only recovery report that sorts each receipt into *passes now*, *needs restoration only*
(which file) or *needs a new run*, and each job into collectible, pending or failed; the same loop, run with a
new lean-pkg, says which old receipts still pass. See
[Recovering an interrupted campaign](receipts.md#recovering-an-interrupted-campaign-read-only) and
[Will my old receipts still pass?](receipts.md#will-my-old-receipts-still-pass-upgrade-impact).

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
`--from-log` is required: friction inside a long stage (a failed `lean-pkg receipt comparator` build) has no cheap
re-run, so describe it with `--body` and attach the tail of its log with `--from-log`. See the
[`lean-pkg report` page](commands/report.md).

## Use lean-pkg from a coding agent

See [Using lean-pkg with agents](agents.md). Short version: the agent reads
[llms.txt](llms.txt) (the same text as `lean-pkg llms`; `lean-pkg llms --short` is the 40-line version), always passes `--json`, asks `lean-pkg next NAME` when unsure what comes next, claims before working, and
never calls `publish --yes` without a human reading the dry run.

## Look up exit codes, environment variables and output formats

```bash
lean-pkg help                # the commands and the help topics
lean-pkg help exit-codes     # 0/1/2, and what 1 means for each command that answers yes or no
lean-pkg help environment    # every environment variable read, with its default
lean-pkg help formatting     # --json, stdout vs stderr, named schemas, filtering with jq
lean-pkg help gate           # the same text as `lean-pkg gate --help`
```
