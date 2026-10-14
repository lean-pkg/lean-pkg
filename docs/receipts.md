---
title: Receipts and trust
---

[&larr; lean-pkg documentation](index.md)

*Evidence you can re-derive, or it isn't evidence.*

## Two tiers

| Tier | Produced by | Validated by | Proves |
|---|---|---|---|
| **Lower** | any executor: `lake build`, sorry scan, `#print axioms`, frozen report | `gate --receipt` | the package builds at the pinned toolchain with `sorry_count = 0` and only the permitted axioms, at one commit |
| **Comparator** | `comparator-receipt` running `comparator-check.sh` | `gate --comparator`, `comparator`, `promote` | each `Solution` theorem has exactly the statement of its trusted `Challenge` namesake, uses only the permitted axioms, and replays in the independent `nanoda` kernel |

The comparator tier is optional at `gate`, so exploratory packages do not pay
its cost. It is required at `promote`, so nothing becomes public without it.
Both tiers must bind to the same commit.

Either tier may run as a Slurm job (see [Host policy](comparator.md#host-policy)).
The lab's Slurm receipts were produced on the Auburn University Easley
Cluster; packages verified there carry the acknowledgment "This work was completed in part with resources provided by the Auburn University Easley Cluster."

## What a passing pair does and does not prove

| Proves | Does not prove |
|---|---|
| the listed theorems compile, sorry-free, with `propext`, `Quot.sound`, `Classical.choice` only | that the Lean statement matches the paper's intent (human fidelity review) |
| statement equality between `Challenge` and `Solution` for every listed name | anything about theorems not listed in the comparator configuration |
| replay in a second, independent kernel | soundness of dependencies beyond the pinned Mathlib |
| evidence bytes on disk hash to what the receipt recorded | public availability, license, or documentation (separate checklist lines) |

## Lower-tier receipt schema

| Field | Rule |
|---|---|
| `commit` | 40 lowercase hex; HEAD, or HEAD is an evidence-only descendant of it |
| `lean_toolchain` | equals the `lean-toolchain` file committed at `commit`, stripped (an uncommitted edit never counts) |
| `mathlib_rev` | nonempty |
| `sorry_count` | integer, must be 0 |
| `axioms` | list of strings, subset of `gate.axioms_allowed` (default `propext`, `Quot.sound`, `Classical.choice`) |
| `report_sha256` | 64 hex characters: the frozen verification report |
| `job_id`, `checker`, `generated_at` | nonempty: the executor's terminal job identity, the exact checker mode, and an ISO-8601 time that must parse. The stored copy is named `<commit12>-<job_id>.json`, with characters outside `[A-Za-z0-9._-]` replaced by `-` and leading dots and dashes dropped |

The frozen report named by `report_sha256` is expected to bind the candidate
commit or tree, the toolchain and dependency pins, complete checker
coverage, the terminal state, and executor identity. A pilot run, a partial
manifest, or a receipt for a previous candidate is not gate evidence.

## A lower-tier receipt from a Slurm job

`lean-pkg receipt slurm` writes the receipt above from a finished Slurm
verification job: its accounting, its raw verify log, and the stdout and
stderr files Slurm wrote for it. It reads and writes no registry.

```bash
lean-pkg receipt slurm --job 123 --commit "$(git rev-parse HEAD)" \
  --log verify.log --stdout job-123.out --stderr job-123.err \
  --checker "verify.sh (lake build; #print axioms; negative controls)" --append-readme
```

Every check runs before anything is written; all failures are listed
together and any one refuses (exit 1, nothing written).

**The job.** `sacct -j J --format=JobID,State,ExitCode,Elapsed,TotalCPU -P -n`
runs on this host; its main job line (JobID exactly `J`, not the `.batch`
or `.extern` step) must say `COMPLETED` and `0:0`. Elapsed and TotalCPU go
into the README entry. On a host without Slurm, save that command's output
on the cluster and pass it with `--sacct-file` (the first three columns
suffice; the entry then says `unknown`). `--job` takes `123` or `slurm-123`.

**The seal, from the job's stdout (`--stdout`).** The verify job prints, in
its Slurm `.out` file, one line each of

| Stdout line | Must equal |
|---|---|
| `verify-log-sha256 <64 hex>` | SHA-256 of the raw verify log given with `--log`, every byte |
| `pre-manifest-sha256 <64 hex>` | `post-manifest-sha256`, and SHA-256 of `git show <commit>:lake-manifest.json` |
| `post-manifest-sha256 <64 hex>` | `pre-manifest-sha256` |

A line is `KEY VALUE` or `KEY: VALUE`, the key first on the line; a missing
or repeated key refuses. A log edited after the job, or a manifest that
changed during it or is not the committed one, is refused. The evidence is
kept: the receipt gains the additive keys `job_stdout_sha256` (SHA-256 of the
`.out` file) and `manifest_sha256` (of `lake-manifest.json` at the commit),
which `gate` ignores, and the README entry a `- Seal:` line naming both.

**Fallback seal, without `--stdout`.** The verify log itself must then hold
exactly one line `verify-log-sha256: <64 hex>`, equal to the SHA-256 of the
raw log with that one line and its newline removed, everything else byte
for byte. A script that writes its log and then runs

```bash
echo "verify-log-sha256: $(sha256sum < verify.log | cut -d' ' -f1)" >> verify.log
```

satisfies it. The manifest check is skipped, and the output says so.

**Stderr (`--stderr`).** The job's `.err` file must be empty. Without the
flag the check is skipped, and the output says so.

**The fields.**

| Receipt field | Source |
|---|---|
| `commit` | `--commit`, resolved to 40 hex in `--repo` (default the current directory) |
| `lean_toolchain` | `lean-toolchain` committed at the commit |
| `mathlib_rev` | the `mathlib` package's `rev` in `lake-manifest.json` committed at the commit, else the log's `mathlib-rev:` line |
| `sorry_count` | the log's `sorry-count:` line, else the number of `declaration uses 'sorry'` lines, counted only when the log shows the build finished (Lake's `Build completed successfully`, or a line starting `lake build: ok`); neither a count nor a completion line is refused, since a cut-short log would otherwise read as 0; anything but 0 is refused |
| `axioms` | the log's `axioms:` line (commas and/or spaces, brackets optional, `none` = `[]`), else the union of the log's `#print axioms` output (`... depends on axioms: [...]`, `... does not depend on any axioms`); neither present is refused |
| `checker` | `--checker TEXT`, else the log's `checker:` line; neither is refused |
| `job_id` | `slurm-<J>` |
| `report_sha256` | SHA-256 of the redacted log as written (below) |
| `generated_at` | the log's `generated-at:` line when present (must parse), else the time of writing; a re-run whose receipt already exists and matches it in every other field (same keys, order and values) keeps the stored `generated_at`, so it is an identical no-op rather than a refusal |

Log key lines are `key: value` with the key at the start of the line, each
at most once. A log `lean-toolchain:` or `mathlib-rev:` line that disagrees
with the commit refuses, and so do optional `commit:` (at least 7 hex, a
prefix of `--commit`) and `job-id:` lines that disagree with the flags.
The receipt carries exactly the nine schema fields.

**Redaction and the public-safety check.** The log text is redacted before
any field is read: the per-job temporary account prefix
(`/tmp/user-job-123/` or `/tmp/user-job123/`, regex
`/tmp/[^/\s<>]+?-job(?=[-\d])`) becomes `/tmp/<USER>-job...`,
`/scratch/user/` becomes `/scratch/<USER>/`, and every `--redact REGEX`
match becomes `<redacted>`. The public-safety scanner (the built-in
families, plus the checkout's `.lean-pkg-private-patterns` when it has one;
every e-mail address is a hit) then runs over the redacted log, the receipt
JSON and the README entry. A blocking hit refuses and names the families,
never the string: add a `--redact` for it.

**What is written.** The redacted log
`<commit12>-slurm-<J>-verify.redacted.log` and the receipt
`<commit12>-slurm-<J>.json`, the name `gate` stores a receipt with that
`job_id` under, go to `<repo>/.lean-receipts/` (or the receipt to
`--out PATH` and the log beside it). The raw log stays with the job; its
hash is in the entry. A differing file already at either path is refused;
an identical one is left alone. The command prints the README entry and
the next step, `lean-pkg gate NAME --check --receipt .lean-receipts/<commit12>-slurm-<J>.json`.

**The README entry.** With `--append-readme` the entry is appended to
`README.md` beside the receipt (created with a `# Receipts` heading when
absent; an entry already naming the receipt is not repeated):

```markdown
## `abc1234`: lower-tier receipt, Slurm job 123

- Receipt: `abc1234def56-slurm-123.json`. Slurm job `123`: COMPLETED, exit `0:0`,
  elapsed 00:12:05, TotalCPU 01:10:22; tar SHA-256 `<hex>`.
- Checker: <checker>; sorry_count 0; axioms propext, Classical.choice, Quot.sound.
- Log: `abc1234def56-slurm-123-verify.redacted.log`, SHA-256 `<hex>` (= `report_sha256`);
  raw log SHA-256 `<hex>` (3 line(s) differ: per-job paths redacted).
```

`--readme-heading TEXT` replaces the heading after the short commit,
`--tar-sha256 HEX` adds the frozen source tarball's hash (the clause is
left out without it), and each `--readme-line TEXT` adds a line at the end.

## A lower-tier receipt from a systemd unit

`lean-pkg receipt systemd` (alias `receipt local`) writes the same receipt
from a verify script run locally in a capped systemd `--user` service unit,
for the light checks that do not go to a cluster. Everything after the
unit's terminal state is the Slurm pipeline above: the seal, the fields,
redaction, the public-safety scan, what is written and the README entry.
It reads and writes no registry.

```bash
lean-pkg receipt systemd --unit audit-gate --commit "$(git rev-parse HEAD)" \
  --log /tmp/gate/verify.log --stdout /tmp/gate/unit.out --stderr /tmp/gate/unit.err \
  --checker "gate.sh (lake build; #print axioms)" --run -- bash gate.sh /tmp/gate/verify.log
```

**Running the unit (`--run`).** lean-pkg starts the command after `--`
itself, as the transient unit `audit-gate.service`:

```bash
systemd-run --user --unit=audit-gate.service -p RemainAfterExit=yes \
  -p WorkingDirectory=<repo> -p OOMPolicy=stop -p MemoryMax=8G -p MemorySwapMax=0 \
  -p CPUQuota=400% -p RuntimeMaxSec=4h \
  -p StandardOutput=truncate:<stdout> -p StandardError=truncate:<stderr> --setenv=PATH=... -- CMD ...
```

The caps are the defaults; `--memory-max`, `--memory-swap-max`,
`--cpu-quota` and `--runtime-max` change them, and each must be finite.
`--setenv NAME=VALUE` adds a variable (`PATH` is always passed). A unit
started with `--wait --collect` vanishes the moment it finishes, and its
exit status and resource accounting with it; `RemainAfterExit=yes` keeps
the finished unit loaded instead. lean-pkg polls `systemctl --user show`
until the unit is terminal, writes its properties and this host's name to
`--properties-out` (default `<log>.unit.properties`, kept even when the
receipt is refused, so the run's evidence is never lost), and only then
stops and resets the unit. `--log`, `--stdout`, `--stderr` and that file
must not exist before the run. A local run verifies the working tree, so
the checkout must be at `--commit` with no change to a tracked file under
the package directory (`--repo`) before the run (else nothing is run) and
still after it. For a package in a subdirectory of a manuscript repository,
edits elsewhere in that repository do not count, as for the evidence-only
rule below. Over ssh, when the
session lacks them, `XDG_RUNTIME_DIR=/run/user/$UID` and
`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus` are set for
`systemd-run` and `systemctl`.

**A unit that has already run.** `--properties-file F` reads saved
properties, captured before the unit was collected:

```bash
{ systemctl --user show audit-gate.service -p Id,LoadState,ActiveState,SubState,Result,InvocationID,\
ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainStartTimestampMonotonic,\
ExecMainExitTimestampMonotonic,NRestarts,MemoryPeak,CPUUsageNSec,MemoryMax,MemorySwapMax,CPUQuotaPerSecUSec,\
RuntimeMaxUSec,OOMPolicy; echo "Hostname=$(hostname)"; } > unit.properties
```

The `Hostname=` line names the host the unit ran on; it is part of
`job_id`. Without either flag the unit is read now with
`systemctl --user show`, and the host is this system's name, never a
configured one.

**The unit.** Every failure is listed and any one refuses (exit 1,
nothing written to `.lean-receipts/`):

| Property | Must be |
|---|---|
| `LoadState` | `loaded`; `not-found` means the unit is gone (collected) |
| `Id` | `--unit`, with `.service` added when absent |
| `InvocationID` | 32 hex characters |
| `ActiveState`, `SubState` | terminal: `failed`, `inactive`, or `active` with `exited`; `activating`, `running`, `deactivating` refuse |
| `Result` | `success`; `oom-kill` (the memory cap), `timeout` (the runtime cap), `signal`, `core-dump`, `exit-code` are named |
| `ExecMainCode`, `ExecMainStatus` | exited (`1`) with status `0`; killed (`2`) or dumped (`3`) names the signal |
| `ExecMainExitTimestampMonotonic` | nonzero: the main process exited |
| `NRestarts` | `0`: the receipt binds one run |
| `Hostname` | a host name |

`MemoryPeak`, `CPUUsageNSec`, the runtime (exit minus start, monotonic)
and the caps `MemoryMax`, `MemorySwapMax`, `CPUQuotaPerSecUSec`,
`RuntimeMaxUSec` are recorded, not judged. The job's stdout may also carry
`invocation-id $INVOCATION_ID` (systemd sets the variable in the unit);
when present it must equal the unit's InvocationID, so a stdout from
another run is refused. A log `job-id:` line must name the job_id or the
InvocationID.

**Redaction.** A local run prints absolute paths. Before the scan the
checkout's absolute path becomes `<repo>`, `$HOME` becomes `<HOME>`,
`/run/user/N/` becomes `/run/user/<UID>/`, and every `--redact REGEX`
match `<redacted>`; a home path left over refuses until a `--redact`
clears it.

**What is written.** `job_id` is
`systemd-user:<unit>:<InvocationID>@<host>`; the receipt, the redacted log
and `<commit12>-<slug>-unit.properties` (the recognised properties only,
in a fixed order, the host last) go to `.lean-receipts/`, where `<slug>`
is the job_id as `gate` names the stored file (`:` and `@` become `-`).
The receipt gains the additive key `unit_properties_sha256`, which `gate`
ignores, beside the seal keys. The README entry's first lines name the
unit, the invocation, the host, the result, the runtime, CPU time, memory
peak and the caps, and a `- Unit properties:` line names the file and its
SHA-256.

## Comparator receipt schema (`comparator-receipt/2`; `/1` still validates)

| Field | Rule |
|---|---|
| `schema` | `"comparator-receipt/2"` (a `"comparator-receipt/1"` receipt is read as the legacy mechanism) |
| `mechanism` | `"toolchain"` (`lake comparator` with the toolchain's bundled kernels, Lean v4.35.0-rc2+) or `"legacy"` (pinned stack); see [Comparator](comparator.md#mechanisms) |
| `commit` | equals the lower-tier receipt commit; HEAD, or HEAD is an evidence-only descendant |
| `lean_toolchain` | equals the `lean-toolchain` file committed at `commit` |
| `tools` | legacy: `comparator`, `lean4export`, `nanoda`, `landrun` as 40-hex commits; toolchain: `lean_toolchain` and the 64-hex sha256 of `lake`, `leanexport`, `leanchecker`, `nanoda_bin`, `con-ron` |
| `host`, `job_id`, `generated_at` | nonempty; `generated_at` must parse as ISO-8601 |
| `host_policy` | the runner's host-policy reason (an override run is visible here) |
| `stack_limit` | optional, additive, not validated: `{soft_kb, hard_kb, source}`, the soft stack limit the runner's `lake`/`lean` children ran under (`soft_kb`/`hard_kb` an integer or `unlimited`; `source` `inherited` or `COMPARATOR_RECEIPT_STACK`); see [Comparator](comparator.md#stages-and---prepare) |
| `lean_threads` | optional, additive, not validated: the `LEAN_NUM_THREADS` the runner gave its `lake`/`lean` children (`COMPARATOR_RECEIPT_LEAN_THREADS`), or `null` when unset; see [Comparator](comparator.md#stages-and---prepare) |
| `judges`, `unit_size` | optional, additive, not validated (toolchain mechanism): the judge pool width (`--judges`, `LEAN_PKG_COMPARATOR_JOBS`) and the names per judge unit (`--unit-size`, `LEAN_PKG_COMPARATOR_UNIT_SIZE`), `unit_size` `null` when each configuration was cut into `judges` shards (the default); see [Comparator](comparator.md#stages-and---prepare) |
| `build_provenance` | optional, additive, not validated: `{compiled, modules_built, prebuilt, source}`, whether the `build` stage compiled any of the package's oleans (`modules_built` an integer, or `null` when the stage was skipped) or found them prebuilt; `source` is `COMPARATOR_RECEIPT_BUILD_SOURCE` (free text, redacted) or `null`; carried from `stages.json` when the setup was reused; printed by `comparator --check` and `gate --check`; see [Comparator](comparator.md#stages-and---prepare) |
| `stages` | optional, additive, not validated: the runner's stage summary (`stage`, `status`, `exit`, `wall_s` for `checkout`, `cache-get`, `build`, `comparator`); the full logs stay in the runner's evidence directory, see [The comparator tier](comparator.md) |
| `test_only` | present and true only on receipts produced under the runner's test-only digest override; such a receipt is refused by every validator |
| `result` | `"pass"` (the runner writes a receipt only on PASS) |
| `configs[]` | exactly the `comparator*.json` / `comparator/config*.json` files tracked at the bound commit, no omissions, no extras. Each entry: `path`; `sha256` of the file's bytes at the bound commit; `challenge_module`, `solution_module`, `theorem_names` (non-empty), `definition_names`, `permitted_axioms` (each equal to the file); `judged_as` (optional; copied from the file when present); `log` = `.lean-receipts/<commit12>-comparator-<cfgname>.log` where `cfgname` is the path without `.json` and with `/` replaced by `_`; `log_sha256`; `result: "pass"`. The log must contain the full line `Your solution is okay!` for both mechanisms (`lake comparator` prints it last, after the per-kernel acceptance lines; the runner and the validator share one predicate). Symlinked evidence is refused |

A config whose file at the bound commit has a non-empty `definition_names` must carry a human `judged_as` sign-off there (see [Comparator](comparator.md#definition-holes-need-a-human-sign-off)); the rule is judged against the tracked bytes, never the receipt's copy.

Validation fails closed on the first miss and names the field. A file already
at the canonical `.lean-receipts/` path that differs from the receipt being
recorded is refused, so a registry pointer can never silently refer to
different bytes.

## Evidence binding: the evidence-only descendant rule

A receipt names commit X, but the receipt file is committed afterwards, so it
can never be tracked at the commit it names. The rule:

> A receipt bound to commit X is accepted at HEAD H when H = X, or when H
> descends from X and `git diff --name-only X H` is non-empty and contains
> only paths under `.lean-receipts/`. Any other difference is drift.

One more path may differ (#23.5): the package's recorded `formalization_yaml`
(usually `formalization.yaml`). Its `status.evidence` cites the receipt, so,
like the receipt, it cannot be inside X. It is accepted when it was already
tracked at X and, compared as YAML data, only the keys the
formalization renderer derives changed (`version`, `project.name`,
`project.license`, `automation.notes`, `status.sorry_count`,
`status.axioms`, `status.evidence`, `status.main_results`, `alignment`). A
changed kept key (a hand-authored one such as `project.description` or
`project.authors`) is drift, and the refusal names it. `claims.yaml` is
hand-authored and never exempt. A `formalization.yaml` first added after X
is drift too: render it (`lean-pkg formalization NAME --write`) and commit
it with the source before gating, then regenerate it after the gate.

With no `formalization_yaml` pointer recorded, the exception does not apply,
except under `gate NAME --receipt R --refresh-formalization` (#26.4). That
command owns the file at the pointer path, or `formalization.yaml` when no
pointer is set, and records the pointer right after the gate, so it judges
the exception for that path. The tested sequence is: on a clean checkout at
X, `gate --receipt R --refresh-formalization`; commit the regenerated file
with `.lean-receipts/` as H; then `gate --check` and `comparator --check`
both pass at H. Every kept key's text is byte-equal before and after the
refresh, as long as the file was in the renderer's own layout (the renderer
rewrites the whole file, so comments and hand formatting do not survive;
the values do).

The source closure stays byte-identical to the certified commit while the
evidence travels with the code. The rule applies to both tiers, to the
`promote` checklist, and to the `receipt-drift` / `comparator-drift`
findings of `audit`. Comparator configurations and `lean-toolchain` are read
at X, not at H, and never from the working tree. Modified tracked files in
the checkout print a WARN at `gate` and `comparator`; `--refuse-dirty` turns
the WARN into a refusal.

### Path dependencies

A receipt binds the package directory at one commit. A Lake
`require ... from "../lean"` makes the build read sources outside that directory: they appear in
`lake-manifest.json` as a `path` package, and an edit to them is never drift.
So `gate` (and every reader that re-validates a receipt) refuses a receipt
whose `lake-manifest.json`, as committed at the bound commit, has a `path`
package whose `dir` is absolute or leaves the package directory. Require the
dependency from git pinned to a rev (the manifest then pins the exact
commit), or move it inside the package directory.

A `dir` that looks inside can still lead outside, so the check reads the
tree of the bound commit, never the working tree (the receipt binds a
historical commit). A path dependency is accepted only when its directory,
and every directory on the way to it, is a tracked directory at that commit:
not a symlink, not a submodule (gitlink), not untracked such as `.lake/`;
and nothing under it may be a gitlink. Independently of path dependencies,
any committed symlink whose target is absolute or has a `..` component is
refused, since the build follows it out of the package. A symlink whose
target only descends stays inside, and so does any chain of them.

## Sibling packages in one repository

A manuscript repository can hold several packages side by side, for example
`lean/` for a first round and `lean-r2/` for the next. Each is registered on
its own and gated on its own receipt, and because the binding is scoped to
the package directory, commits to `lean-r2/` are never drift for `lean/`.
Register the second with `register NAME --path DIR --like OTHER`: it takes
the repository and project fields from OTHER and notes whether the toolchain
and Mathlib pin match. Keep them siblings, not nested: a package directory
inside another makes every commit to the inner one drift for the outer one
(`register` warns). To reuse the first package's results, depend on it from
git at a pinned rev, not by path (see above).

## Where evidence lives

Receipts and logs are copied into `<repo>/.lean-receipts/` by `gate` and
`comparator` (never committed by the tool; you commit them). The registry
stores only the relative paths; `promote` re-reads the bytes, and refuses
unless the receipts and every comparator log are committed at HEAD unmodified
and HEAD is contained in `origin/<branch>` (judged on local refs; nothing is
fetched). An
[index entry](index-catalog.md) keeps verbatim copies of the receipts (not
the logs) and `index check --clone` re-derives them at the pin.

`lean-pkg formalization` reports the receipts under `status.evidence` of the
package's `formalization.yaml` (commit, gate receipt, comparator receipt) and
lists every tracked comparator configuration under `status.main_results`; see
[formalization.yaml](formalization.md).

## Publishing a package incubated beside private work

`promote` makes the whole repository public, history included. A package
that grew next to private material (unpublished drafts, approval notes,
absolute paths of a work machine) can carry it in old commits even when the
current tree is clean. The sequence:

1. `lean-pkg public-safety NAME` scans the tree and every blob and commit
   message reachable from any ref. Private strings specific to the package
   go in an untracked `.lean-pkg-private-patterns` (one regular expression
   per line; `allow: REGEX` exempts a public string). Hits are printed
   redacted. `promote` runs the same scan and refuses while blocking hits
   remain. Warn-only hits (the `latex-label` family and `warn:` lines) are
   printed as `WARN` lines and do not block unless `--strict` is given.
2. Fix the hits. A hit only in the tree is fixed with a commit, followed by
   a new gate, since the gate-bound commit changes. A hit in the history
   needs a history rewrite, or, simpler, a fresh-history export:
   `lean-pkg release NAME --fresh-repo DIR [--remote URL] --dry-run`, then
   the same without `--dry-run`. It exports the gate-bound tree (internal
   files and the patterns file dropped) into a new repository with one
   commit, scans it, and moves the registry entry there. Gate and comparator
   evidence is cleared, with a dated note keeping the old receipt paths and
   the source commit. When the publishable tree was cleaned by hand (files
   removed or edited) and is not a commit of the source repository, which
   must stay untouched, export the working tree instead:
   `lean-pkg release NAME --fresh-repo DIR --from-tree SRCDIR`. It applies
   the same exclusions and scan (SRCDIR's `.gitignore` is honoured) and
   records "source commit: none (hand-cleaned tree)" in the one commit. If
   the public repository was already built by hand, adopt it as it is:
   `lean-pkg release NAME --adopt-repo DIR [--remote URL]` scans its tree
   and whole history, refuses more than one commit unless
   `--allow-history`, makes no new commit, and relocates the entry the same
   way.
3. Run a new gate on the new commit: the lower-tier check, then
   `lean-pkg gate NAME --receipt R`. Receipts copied with `--keep-receipts`
   are provenance only, because a receipt binds one exact commit and the
   export commit is not a descendant of the old one.
4. Run the comparator on the new commit:
   `comparator-receipt --project DIR --prepare`, then the run, then
   `lean-pkg comparator NAME --receipt ...`.
5. `lean-pkg publish NAME --dry-run --public-safety`, push the new
   repository, and `lean-pkg promote NAME` (dry run first, then `--yes`).

### Paper-incubated packages

A package that formalizes an unposted manuscript leaks it through content,
not only through paths. A doc-comment such as "the bound of `lem:main_bound`"
or "Theorem 3.2 of the draft" quotes the numbering and labels of a paper that
is not public, and no path appears in it. The built-in `latex-label` family
reports the common forms as warnings: cross-reference keys (`lem:`, `thm:`,
`cor:`, `prop:`, `def:`, `eq:`, `sec:`, `rem:`, `ex:`, `fig:`, `tab:`,
`conj:`, also with `-`), `main.tex`, `\ref{`, `\eqref{`, `\cite{`, `\label{`,
`\input{`, `\include{`, `Overleaf` and arXiv identifiers. They are warnings
because a published paper may be cited legitimately.
The family is calibrated for Lean sources: a key inside a longer name
(`foo_eq_bar`, `eq_comm`), `def-eq`, `defeq`, `eq-of-...`, `ex-falso` and
other all-lowercase hyphen prose are not reported, and the summary line says
how many warnings came from `latex-label`, so you can judge whether a
package needs `allow:` lines at all.

For a package incubated beside a paper, make the paper's own identifiers
blocking. Keep `.lean-pkg-private-patterns` untracked (list it in
`.gitignore`) and give it:

```text
# the manuscript repository and its working title
block: Article-Example
block: (?i)example working title
# the manuscript's label prefixes, and any paper-specific identifiers
block: \b(lem|thm|prop|cor|eq|sec):
block: \bmain\.tex\b
block: SpecialConstantName
# what the public tree may say
allow: arXiv:2401\.01234
```

A bare line is blocking, as before; `block:` says so explicitly, and
`warn: REGEX` adds a warn-only pattern.

Any of the three tags takes a path scope, `TAG: GLOB: REGEX`, which applies
the line only to files whose package-relative path matches GLOB (`*` and `?`
within a path segment, `**` across directories; a GLOB without `/` matches
the file name at any depth; a trailing `/` matches everything under that
directory). Scoped lines never apply to commit messages. A package whose
documentation quotes the built-in patterns on purpose can then allow them
there and nowhere else:

```text
# docs/ explains the private-path rules, so it quotes them
allow: docs/**: ^(?:approvals/|notes/|TODO-private)$
# the test fixtures carry the working title on purpose; nothing else may
allow: tests/fixtures/**: (?i)example working title
``` Theorem numbers quoted from an
unposted manuscript ("Lemma 4.1 of the paper") are a leak even without a
label or a path; add a `block:` line for the phrasing you use, or rewrite
those doc-comments to state the result itself.

Then run `lean-pkg public-safety NAME --strict` before
`lean-pkg release NAME --fresh-repo DIR`: under `--strict` every warning is
blocking, so the run exits 0 only when nothing manuscript-shaped is left.
`release --fresh-repo` itself refuses only on blocking hits and lists the
warnings in its plan.

public-safety is necessary, not sufficient: a second release audit found two
comment-only paper-specific leaks (provenance and proof-narrative wording)
that no pattern can catch. Keep an independent human-style read of comments
and docstrings before adopting the public tree.

Next: [The comparator tier](comparator.md).
