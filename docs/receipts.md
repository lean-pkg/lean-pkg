---
title: Receipts and trust
---

[&larr; lean-pkg documentation](index.md)

*Evidence you can re-derive, or it isn't evidence.*

## Two tiers

| Tier | Produced by | Validated by | Proves |
|---|---|---|---|
| **Lower** | any executor: `lake build`, sorry scan, `#print axioms`, frozen report | `gate --receipt`; from git alone, `receipt check` | the package builds at the pinned toolchain with `sorry_count = 0` and only the permitted axioms, at one commit |
| **Comparator** | `comparator-receipt` running `comparator-check.sh` | `gate --comparator`, `comparator`, `promote`; from git alone, `receipt check` | each `Solution` theorem has exactly the statement of its trusted `Challenge` namesake, uses only the permitted axioms, and replays in the independent `nanoda` kernel |

The comparator tier is optional at `gate`, so exploratory packages do not pay
its cost. It is required at `promote`, so nothing becomes public without it.
Both tiers must bind to the same commit.

Either tier may run as a Slurm job (see [Host policy](comparator.md#host-policy)).
The lab's Slurm receipts were produced on the [Auburn University Easley Cluster](https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html);
a package whose receipts show its gates ran there carries, in its README, the
acknowledgment with that link:

```markdown
This work was completed in part with resources provided by the [Auburn University Easley Cluster](https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html).
```

## What a passing pair does and does not prove

The full account, with the trusted parties, the threats covered and not
covered, and how a third party re-verifies a page, is the
[Trust model](trust-model.md).

| Proves | Does not prove |
|---|---|
| the listed theorems compile, sorry-free, with `propext`, `Quot.sound`, `Classical.choice` only | that the Lean statement matches the paper's intent (human fidelity review) |
| statement equality between `Challenge` and `Solution` for every listed name | anything about theorems not listed in the comparator configuration |
| replay in a second, independent kernel | soundness of dependencies beyond the pinned Mathlib |
| evidence bytes on disk hash to what the receipt recorded | public availability, license, or documentation (separate checklist lines) |

## Lower-tier receipt schema

| Field | Rule |
|---|---|
| `commit` | 40 lowercase hex; HEAD, or HEAD is an evidence-only descendant of it (or, for a package that opted in, a [docs-only descendant](#docs-only-descendants-opt-in)) |
| `lean_toolchain` | equals the `lean-toolchain` file committed at `commit`, stripped (an uncommitted edit never counts) |
| `mathlib_rev` | nonempty; prefix-equal to the `mathlib` rev in `lake-manifest.json` committed at `commit` (not checked when that file has no readable `mathlib` entry) |
| `sorry_count` | integer, must be 0 |
| `axioms` | list of strings, subset of `gate.axioms_allowed` (default `propext`, `Quot.sound`, `Classical.choice`) |
| `report_sha256` | 64 hex characters: the frozen verification report; when the redacted verify log `<commit12>-<job slug>-verify.redacted.log` that `receipt slurm`/`systemd` writes is found (beside the receipt file, in the package's `.lean-receipts/`, or tracked there at HEAD), every copy must hash to it, else `gate` refuses; none found (or a copy that cannot be read) is refused too, as `report log missing: ...` naming where it looked and the path to restore it to (see [Restoring preserved evidence](#restoring-preserved-evidence)). The same rule holds wherever a recorded lower-tier receipt is judged: the claims ledger (and so `promote`), `next`, `comparator`, `palomar` and `index check`. A compressed copy `<...>-verify.redacted.log.gz` is checked the same way on its uncompressed bytes ([compressed sealed log](#a-compressed-sealed-log---compress-log)); report_sha256 is never the hash of the .gz |
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
suffice; the entry then says `unknown`). Output saved with other columns
needs its header line (run `sacct` with `-P` but without `-n`): a line
naming `JobID`, `State` and `ExitCode` locates them in any order. Without a
header, a second column that is not a Slurm state or a third that is not an
`N:N` exit code is refused as `unexpected sacct format`, never misread as an
unclean finish. `--job` takes `123` or `slurm-123`.

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
flag the check is skipped, and the output says so. Nothing on it is
filtered or waived: a locale warning
(`setlocale: LC_ALL: cannot change locale`) refuses the receipt like any other byte. Fix the job's runtime
instead; the `--template` wrapper below sets the C locale before any tool
runs.

The commit's files (`lean-toolchain`, `lake-manifest.json`, the lakefile, the tracked tree) are read at the path of
`--repo` inside its git repository, so a package in a subdirectory of a manuscript repository is read there. A copy
of the package placed at another path of the same repository (a frozen review packet, say) is not the committed
package and is refused; the refusal names the path that was read and where the commit holds a `lean-toolchain`.

**The fields.**

| Receipt field | Source |
|---|---|
| `commit` | `--commit`, resolved to 40 hex in `--repo` (default the current directory) |
| `lean_toolchain` | `lean-toolchain` committed at the commit |
| `mathlib_rev` | the `mathlib` package's `rev` in `lake-manifest.json` committed at the commit, else the log's `mathlib-rev:` line |
| `sorry_count` | the log's `sorry-count:` line (refused when the same log has more ``declaration uses `sorry` `` lines (Lean's backtick form; the older `'sorry'` counts too) than it declares: a script's count, often from an inventory of declarations, may not hide Lean's own warnings), else the number of `declaration uses 'sorry'` lines, counted only when the log shows the build finished (Lake's `Build completed successfully`, or a line starting `lake build: ok`); neither a count nor a completion line is refused, since a cut-short log would otherwise read as 0; anything but 0 is refused |
| `axioms` | the log's `axioms:` line (commas and/or spaces, brackets optional, `none` = `[]`), else the union of the log's `#print axioms` output (`... depends on axioms: [...]`, `... does not depend on any axioms`); neither present is refused; a declared `axioms:` line is refused when it omits an axiom the log's own `#print axioms` output reports; names are compared without a universe suffix (`Classical.choice.{u}` is `Classical.choice`) |
| `checker` | `--checker TEXT`, else the log's `checker:` line; neither is refused |
| `job_id` | `slurm-<J>` |
| `report_sha256` | SHA-256 of the redacted log as written (below) |
| `generated_at` | the log's `generated-at:` line when present (must parse), else the time of writing; a re-run whose receipt already exists and matches it in every other field (same keys, order and values) keeps the stored `generated_at`, so it is an identical no-op rather than a refusal |

Log key lines are `key: value` with the key at the start of the line, each
at most once. A log `lean-toolchain:` or `mathlib-rev:` line that disagrees
with the commit refuses, and so do optional `commit:` (at least 7 hex, a
prefix of `--commit`) and `job-id:` lines that disagree with the flags.
The receipt carries the nine schema fields, plus additive keys `gate`
ignores (`log_commit_bound`, and the seal and evidence hashes).

**The log's own binding to the commit.** A `commit:` line binds the log to
`--commit` (the `--template` prints one). Without it, the in-log fallback
seal binds the log to nothing, so it is refused there (no `--stdout`); with
`--stdout` the log is bound only through the `lake-manifest.json` hashes, and
the run prints a stderr WARN that the verify log has no `commit:` line and is
bound to the commit only through `lake-manifest.json` (JSON
`log_commit_bound: false`). The written receipt keeps that value
(`log_commit_bound`: `true` with a matching `commit:` line, `false`
without one), and a `false` adds a `- WARN:` line to the README entry, so
the limitation survives in the sealed files alone.
`receipt slurm --check --json` reports the same key (`null` without `--commit`). The key is
informational: `gate` never reads it, so a receipt claiming `true` is
judged exactly like one without the key (a receipt written before the key
existed is still accepted).

**Redaction and the public-safety check.** Every field but `checker` is
read from the raw log, so a redaction can never delete the evidence it is
read from; `checker` is published as redacted. A redaction that removes or
alters a line the parser reads (Lean's sorry warnings, `#print axioms`
output, a build-completion line, or any `key:` line but `checker:`) refuses:
narrow the pattern. The text published is the redacted one: the per-job temporary account prefix
(`/tmp/user-job-123/` or `/tmp/user-job123/`, regex
`/tmp/[^/\s<>]+?-job(?=[-\d])`) becomes `/tmp/<USER>-job...`,
`/scratch/user/` becomes `/scratch/<USER>/`, and every `--redact REGEX`
match becomes `<redacted>`. The public-safety scanner (the built-in
families, plus the checkout's `.lean-pkg-private-patterns` when it has one;
every e-mail address is a hit; this machine's user name (`getpass.getuser()`,
`$USER`, `$LOGNAME`) and host name (full and its first label), derived at
receipt time, at a word boundary, except names shorter than 3 characters and
common ones such as `root`, `runner`, `localhost`; a home directory
`/home/<x>` or `/Users/<x>` with or without a trailing slash) then runs over
the redacted log, the receipt JSON and the README entry. A blocking hit
refuses and names the families, never the string: add a `--redact` for it.

A committed symlink at the commit that resolves into `.lean-receipts/` is
refused (gate refuses it too: see [the rule](#evidence-binding-the-evidence-only-descendant-rule)).

**What is written.** The redacted log
`<commit12>-slurm-<J>-verify.redacted.log` and the receipt
`<commit12>-slurm-<J>.json`, the name `gate` stores a receipt with that
`job_id` under, go to `<repo>/.lean-receipts/` (or the receipt to
`--out PATH` and the log beside it). The raw log stays with the job; its
hash is in the entry. A differing file already at either path is refused;
an identical one is left alone. The command prints the README entry and
the next step, `lean-pkg gate NAME --check --receipt .lean-receipts/<commit12>-slurm-<J>.json`.

### Before sbatch

On the login node, before submitting, check the job's own runtime: run
`lean-pkg comparator host-check --routes --json` inside the job's image
with the job's binds and `PATH` export:

```bash
singularity exec --bind "$WORK" "$IMAGE" bash -c \
  'export PATH=...; lean-pkg comparator host-check --routes --json'
```

It resolves every executable the template needs the way `type -P` does,
names a set locale the image does not have (the stderr warning the receipt
refuses), reports the open-file limits with the `LEAN_NUM_THREADS` advice,
the CPUs and the cgroup memory limit, and says which comparator routes
(judge, prepare, transfer) the host supports. See
[Planning routes before sbatch](comparator.md#planning-routes-before-sbatch).
Check a dependency tarball with `receipt slurm --preflight-deps` (below).

### A compressed sealed log (`--compress-log`)

GitHub refuses a file over 100 MiB, and a full declaration audit can make the
redacted log larger than that (a field report: 740 MiB, about 15 MB as gzip).
`receipt slurm --compress-log` (and `receipt systemd --compress-log`) writes
the redacted log as `<commit12>-<job slug>-verify.redacted.log.gz` instead of
the plain `.log`: deterministic gzip (level 9, header mtime 0, no file name,
so the same log gives the same bytes on every run with the same zlib). Without the flag
nothing changes, except that a redacted log over 90 MiB prints a stderr
`WARN` naming `--compress-log`; the output is never compressed silently.

- `report_sha256` stays the SHA-256 of the **uncompressed** redacted log.
- The receipt gains the additive key `report_compressed`:
  `{"format": "gzip", "sha256": "<SHA-256 of the .gz>", "bytes": <uncompressed length>}`.
  Older receipts are unchanged and still verify.
- `--json` `log` names the `.gz`; `sealed[]` lists it with role
  `redacted-log.gz`, its own `sha256`, `receipt_key`
  `report_compressed.sha256` and `uncompressed_sha256` (= `report_sha256`);
  `report_compressed` is in the payload too. The README entry gains a
  `- Stored compressed:` line with the restore command.

`gate` (and `gate --check`) reads `X.log.gz` wherever it looks for `X.log`
(beside the receipt, in `.lean-receipts/`, tracked there at HEAD). It
stream-decompresses it and refuses unless the uncompressed bytes hash to
`report_sha256`; when `report_compressed` is present the `.gz` must also hash
to `report_compressed.sha256`, the output must be exactly
`report_compressed.bytes` long, and decompression stops and refuses as soon
as the output passes that length (a gzip bomb is never expanded). A `.gz`
for an older receipt without `report_compressed` (compressed by hand) is
checked against `report_sha256` under a fixed 4 GiB cap. A
`report_compressed.sha256` equal to `report_sha256`, or a `report_sha256`
that is the hash of the `.gz`, never passes. A plain `.log` and a `.gz`
side by side are both checked.

Restore the plain log and check it against the receipt:

```bash
gzip -dc <commit12>-slurm-<J>-verify.redacted.log.gz > <commit12>-slurm-<J>-verify.redacted.log
sha256sum <commit12>-slurm-<J>-verify.redacted.log   # must print report_sha256
```

**The canonical copy keeps its log.** `gate --receipt PATH` stores the
receipt as `.lean-receipts/<commit12>-<job slug>.json`. When the sealed log
(plain or `.gz`) was found beside `PATH` and `.lean-receipts/` holds no copy
of it, `gate` copies it there too, so the stored receipt verifies on its own
(output `sealed log copied to ...`, JSON `report_log.copy[]`; `gate --check`
prints `· gate would also copy ...` and writes nothing). A symlink at either
log name in `.lean-receipts/` refuses, nothing copied or recorded.

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
A `README.md` that is a symlink or not UTF-8 text, or a `.lean-receipts/`
that is a symlink, refuses before anything is written.

**A job that meets the contract (`--template`).**
`lean-pkg receipt slurm --template` prints a minimal sbatch wrapper (bash)
whose files carry every line above; it needs no other flag and no registry:

```bash
lean-pkg receipt slurm --template > verify.sbatch   # edit VERIFY_CMD, AXIOMS_CMD, CHECKER and the #SBATCH lines
sbatch verify.sbatch
```

The wrapper copies the tree committed at `COMMIT` (default `HEAD` of the
submitting checkout) into a node-local work directory
(`${SLURM_TMPDIR:-/tmp/$USER-job-<J>}`), prints `pre-manifest-sha256` to
stdout, sends every tool's output into `verify-<J>.log` (so `--error` stays
empty), writes the `commit:`, `job-id:`, `lean-toolchain:` and `checker:`
lines, runs the package's build and `#print axioms` commands, derives
`sorry-count:` and `axioms:` from Lean's own output in the log, and only
then prints `post-manifest-sha256` and `verify-log-sha256` (nothing may
write to the log after that). It builds with `LAKE_CACHE_DIR` an empty
directory of its own in the work directory and `LAKE_ARTIFACT_CACHE=false`
(log lines `lake-cache-dir: fresh (empty, job-local)` and
`lake-artifact-cache: false`, no path), refuses a committed `.lake/build`,
brackets the build with `lean-pkg-build: begin` / `lean-pkg-build: end`
lines and fails the job when that section shows a root-package module
`Reused`, `Replayed` or `Unpacked`. Before the audit it runs
`LEAN_PATH_CMD` (default `lake env printenv LEAN_PATH`) and fails when an
entry other than the package's own `.lake/build/lib/lean` holds one of its
root namespaces (`lean-path-shadow:` lines; on success one line with the
entry count and the namespaces, no path); the check runs outside `RUN`, so a
container must bind the work directory at the same path. The default
`AXIOMS_CMD` is `lake lean Verify/Axioms.lean`, never `lake env lean`. `LEAN_NUM_THREADS` defaults to 3:
Mathlib-heavy packages can exhaust the node's file table (`fs.file-max`) at
higher values, and `/proc/sys/fs/file-nr` goes into the log before and
after the build. Comments say where to stage the Lean toolchain and
`MATHLIB_CACHE_DIR` node-local and how to run the tools inside the job's
container (`RUN=...`). A work directory outside `/tmp/<user>-job<J>` and
`/scratch/<user>/` that appears in the log needs a `--redact` at receipt
time.

**Exact-runtime preflight.** Before any tool runs, the wrapper exports
`LC_ALL=C LANG=C PYTHONUTF8=1` (and unsets `LANGUAGE`); an
`#SBATCH --export=ALL,LC_ALL=C,LANG=C,PYTHONUTF8=1` line does the same for
the job's own shell, which otherwise starts under the submitting shell's
locale (a C.UTF-8 the node lacks made bash warn on stderr, and the strict
receipt rightly refused 70 bytes of it). An `sbatch --export=...` on the
command line replaces that line, so keep the three variables in it. Then,
before the work, every executable the wrapper needs must resolve with
`type -P` (an executable, never a shell keyword or function): the host tools
(`git`, `tar`, `sha256sum`, `find`, `sort`, `xargs`, `awk`, `sed`, `grep`,
`cut`, `tr`, `cp`, `mkdir`, `rm`, `ls`, `cat`, plus `EXTRA_TOOLS`), and,
under `$RUN`, the first word of `VERIFY_CMD`, `AXIOMS_CMD` and
`LEAN_PATH_CMD` (with `lean` beside a `lake` one, plus `RUN_TOOLS`, e.g.
`RUN_TOOLS=python3`). A missing one fails the job at once, named on stderr
(`error: runtime preflight: executable(s) not found: ...`), with nothing
built or logged. The log gets
`runtime-preflight: ok (LC_ALL=C LANG=C PYTHONUTF8=1; tools: ...)` (the probed command names, never a path). Stage
the toolchain on `PATH` (the comment above) before the probe if you move it.

Three optional settings (environment variables when submitting, or edits at
the top of the wrapper):

- **`SRC_MODE=clone`** (default `archive`). `archive` extracts
  `git archive COMMIT`, so the job's tree has no `.git` and a verify script
  that binds its evidence to `git rev-parse HEAD` fails. `clone` makes the
  work directory a new repository (`git init`, no template hooks) holding
  only `COMMIT` and its history, fetched over the file transport from the
  submitting checkout (`GIT_ALLOW_PROTOCOL=file`: never the network; its own
  object store, so nothing is shared with the checkout and no untracked or
  ignored file comes along), and checks it out detached at `COMMIT` (a short
  `COMMIT` is resolved to the full SHA first; a package in a subdirectory of
  its repository is built from that subdirectory, as with `archive`). The
  job fails unless `git rev-parse HEAD` is `COMMIT` and
  `git status --porcelain --untracked-files=all --ignored` is empty. The log
  gets `src-mode: clone` and `src-head: <sha>` (`src-mode: archive`
  otherwise); `receipt slurm` and `--check` refuse a `src-head:` line that
  is not `--commit`.
- **`DEPS_TAR=PATH`**: a tarball (any compression `tar` reads) holding
  `.lake/packages`, the dependencies' checkouts and builds, restored before
  the fresh-build check so the job need not fetch and build Mathlib again.
  It is extracted into a staging directory and only its `.lake/packages`
  directory is moved into the tree; anything else in it (a `.lake/build`,
  sources) is ignored. The job fails when the tree already has
  `.lake/packages`, when the tarball has none (a symlink is not one), when
  a listing and the content hashes of the tree outside `.lake/packages`
  differ before and after the restore, or when a dependency's
  `build/lib/lean` holds one of the package's root namespaces
  (`deps-restore-shadow: <relative path>` lines; the `LEAN_PATH` check
  before the audit still runs). The log gets `deps-restore: tar <sha256>`
  (never the path; `deps-restore: none` without it) and
  `deps-restore-check: ok (...)`. The package itself is still compiled
  fresh: the empty Lake cache, the `.lake/build` refusal and the
  `Reused`/`Replayed`/`Unpacked` check are unchanged. The sha256 identifies
  the tarball; it is not what makes the build trustworthy.

  The job restores the tarball and builds the package; it does not build
  what only the audit imports. A tarball holding the dependency builds of
  the library target's import cone passes `lake build` and then fails at
  the audit when an audit file imports more, such as the `Mathlib`
  umbrella (Lean reports that the object file `Mathlib.olean` of module
  `Mathlib` does not exist). Check the tarball before submitting, on the
  login node:

  ```bash
  lean-pkg receipt slurm --preflight-deps --deps-tar deps.tar.gz --commit SHA \
    --entries 'Verify/**/*.lean' --entries 'Audit/**/*.lean'
  ```

  The rule is fail-closed. The entry files are the committed `.lean` files
  at `SHA` matching `--entries` (the default list covers `Verify/`,
  `Audit/`, `OwnedAudit/` and the `AxiomCheck`, `TypeCheck` and `Examples`
  files). Their `import` lines are followed through the package's own
  committed modules and then through the dependencies' sources inside the
  tarball, so `import Mathlib` requires `Mathlib.olean` and the `.olean` of
  every module `Mathlib.lean` imports, transitively. Every external module
  in that closure must have its `.olean` and `.ilean` as regular files in
  exactly one package of `lake-manifest.json` (a symlink does not count),
  and its source must be there too, or its imports could not be followed.
  A module no package provides is a problem unless its root is a toolchain
  one (`Init`, `Std`, `Lean`, `Lake`). Every git package of the manifest
  must be in the tarball, checked out at the manifest's `rev`
  (`.git/HEAD`): a stripped checkout or another rev is a problem. Nothing
  is built, extracted or written; the exit code is 0 or 1 and every
  problem is listed (`--json` lists the missing modules, the first 500).
  `--deps-dir DIR` checks a directory holding `.lake/packages` instead.
- **`ARTIFACTS_DIR=DIR`**: the artifact seal, for a job whose build or
  audit leaves files that matter beyond the log (native executables,
  generated certificates). `DIR` is relative to the job's package tree
  (relative to the node-local work directory with `ARTIFACTS_BASE=work`);
  an absolute path, a `..` component or a symlinked path component fails the
  job. After the audit, every regular file under `DIR` is listed in
  `artifacts.sha256`, one line per file sorted by path in the C locale:
  `<relative path> <size> <sha256>`. The directory and the manifest are
  copied to `ARTIFACTS_OUT` (default `$OUT_DIR/artifacts-<J>`, which must
  not exist yet: a job never overwrites artifacts), and the copy is rehashed
  against the manifest before the job may finish. A symlink or special file
  under `DIR`, a file name holding a newline, or a file already named
  `artifacts.sha256` at its top fails the job. The manifest's SHA-256 goes
  to stdout as `artifacts-manifest-sha256 <hex>`, before
  `post-manifest-sha256`, so the stdout seal (and through it sacct) binds
  it; the log gets `artifacts-manifest: N file(s) from src/DIR, sha256 <hex>`
  (never the work directory's path). Without `ARTIFACTS_DIR` nothing of
  this runs and no such line is printed.

### Fresh builds and LEAN_PATH

Two Lake behaviours can make a verify log say less than it seems to (checked
against the Lake and Lean sources of v4.35.0-rc2):

- **The local artifact cache.** A package reads Lake's artifact cache unless
  it is switched off: its own `enableArtifactCache`, else the workspace's
  (`LAKE_ARTIFACT_CACHE`, then the root package's setting), else on; a
  package lakefile's `enableArtifactCache = true` wins over
  `LAKE_ARTIFACT_CACHE=false`. The cache lives in `LAKE_CACHE_DIR`, else
  under the toolchain, else `$XDG_CACHE_HOME/lake` or `~/.cache/lake`;
  `LAKE_CACHE_DIR=""` does not disable it (`.lake/cache` is used), and
  `--no-cache` / `LAKE_NO_CACHE` govern downloads only. A module restored
  from it is reported `Reused` and its ``declaration uses `sorry` `` warning
  is not printed: a cached module with a sorry builds with exit 0 and no
  warning, so a sorry count scraped from the log reads 0. Lake prints a
  `Reused` (or `Replayed`, `Unpacked`) job only when it has output or with
  `-v`, so the defence is a fresh tree and an empty cache directory of the
  job's own with `LAKE_ARTIFACT_CACHE=false`, which the `--template` and
  `receipt systemd --run` set. `receipt slurm` and `receipt systemd`
  (and `--check`) refuse a log showing a root-package module (its source
  tracked at the commit, or its first name component a `lean_lib` name,
  root or glob of the lakefile) `Reused`, `Replayed` or `Unpacked`, inside
  the `lean-pkg-build:` markers when the log has them (the audit's
  `lake lean` replays the modules it imports), and one whose
  `lake-artifact-cache:` line says anything but `false`. A cache line for a
  module that an EARLIER line of the judged section shows `Built` is not a
  restore: when a later step runs `lake lean FILE`, Lake re-prints the
  stored warnings of the just-built modules it imports as `Replayed`, so a
  verify script without the markers that builds and then audits passes. Only
  an earlier `Built` excuses it: a module only ever `Reused`, `Replayed` or
  `Unpacked`, or shown so before its `Built`, is still refused, and so is
  the template's own shell check. Emit the markers around the build anyway:
  then only the build is judged.
- **LEAN_PATH order.** Lake lists the root package's lib directory last on
  `LEAN_PATH`, and Lean loads a module whose first component is `R` from the
  first entry holding `R/` or `R.olean`. Under `lake env lean FILE` a
  dependency's build directory holding the package's namespace is loaded
  instead of the package's own build (a planted copy of a module answered
  `#print axioms` with its own axioms). `lake build` and `lake lean FILE`
  pass explicit module paths and are not affected; run the audit with
  `lake lean`. The template, `comparator-receipt` (after its build stage)
  and both comparator scripts refuse when an entry other than the package's
  own `.lake/build/lib/lean` holds a root namespace, naming the path.

**Check the files first (`--check`).** A long job whose files miss a line
cannot be receipted and must be run again. `--check` runs every check
above that the job's own files decide, on the files of any finished job
(a short trial run is enough), and names every problem at once:

```bash
lean-pkg receipt slurm --check --commit "$(git rev-parse HEAD)" \
  --log verify-123.log --stdout verify-123.out --stderr verify-123.err
```

It checks the stdout seal (each key exactly once, 64 hex, `verify-log-sha256`
equal to the log's SHA-256, pre = post), the empty stderr, the log's key
lines and fields (`checker:` or `--checker`, `sorry-count:` or a completion
line, `axioms:` or `#print axioms` output, the declared-versus-printed
rules), and the public-safety scan of the redacted log. With `--commit` it
also compares the manifest hash, `lean-toolchain:`, `mathlib-rev:` and
`commit:` with the commit; without it those lines say `not checked`. sacct
is not run, so the job state is `not checked` unless `--sacct-file F` and
`--job J` are given. It writes nothing; exit 0 when the files are
acceptable, 1 otherwise; `--json` prints
`{ok, check, written: [], raw_log_sha256, checks[], problems[]}`.

**The artifact seal (`--artifacts DIR`).** For a job run with
`ARTIFACTS_DIR` (above), `--artifacts DIR` names the collected copy of its
`ARTIFACTS_OUT` (with `--check` too; it needs `--stdout`).
`--artifacts-manifest NAME` names the manifest inside `DIR` when it is not
`artifacts.sha256`. The manifest's SHA-256 must equal the job stdout's one
`artifacts-manifest-sha256` line, and every file under `DIR` is rehashed
in 1 MiB chunks, never following a symlink. A missing file (listed, not
present), an extra file (present, not listed), a changed file (size or
SHA-256), a symlink, a special file, a path-traversal entry (absolute, `..`,
`.` or empty components), a duplicate entry, unsorted lines or a manifest
without a final newline each refuse, every one named (the first five paths
per kind). The receipt gains the additive key `artifacts_manifest_sha256`
(`gate` ignores it, as it ignores `job_stdout_sha256`), the manifest is
written beside the receipt as `<c12>-slurm-<J>-artifacts.sha256` and listed
in `--json` `sealed[]` with role `artifacts.sha256`, the README entry gets a
`- Artifacts:` line with the file count and bytes, and `--json` gains
`artifacts` {ok, dir, files, bytes, manifest, manifest_sha256}. Without
`--artifacts`, nothing changes: the same checks, keys and files as before.

### Recollecting a finished job

A collection that stopped part-way (a dropped connection while copying a
job's artifacts back) leaves files whose only hashes are the collector's
own. Those hashes prove nothing about the job. Recollect from what the job
sealed:

1. Copy the job's `.log`, `.out`, `.err` and its `ARTIFACTS_OUT` directory
   again into a new attempt directory (`attempt-2/`, never over an earlier
   attempt), and keep the copy's exit status. Numbered no-overwrite attempts
   and that exit status belong to the project's harness, not to lean-pkg:
   lean-pkg only judges the files it is given.
2. Preflight the recollected files, which writes nothing:

   ```bash
   lean-pkg receipt slurm --check --commit "$(git rev-parse HEAD)" --log attempt-2/verify-123.log \
     --stdout attempt-2/verify-123.out --stderr attempt-2/verify-123.err --artifacts attempt-2/artifacts-123
   ```

3. Then write the receipt from the same files:

   ```bash
   lean-pkg receipt slurm --job 123 --commit "$(git rev-parse HEAD)" --log attempt-2/verify-123.log \
     --stdout attempt-2/verify-123.out --stderr attempt-2/verify-123.err --artifacts attempt-2/artifacts-123
   ```

Files an interrupted attempt already copied may be reused (moved or
hard-linked into the new attempt instead of copied again): `--artifacts`
rehashes every file against the job's own manifest, whose hash the job's
stdout carries, so a reused file is adopted only when it matches what the
job sealed. Never trust another attempt's hashes in its place. A job run
without the artifact seal (no `ARTIFACTS_DIR`, so no
`artifacts-manifest-sha256` line) can only cite its artifacts as
provenance: the receipt binds the log, not the files beside it.

**The collection barrier.** Compose the steps in this order, and stop at
the first one that fails; a partial, stale or corrupt collection is
refused, never repaired in place:

1. **Collect** into a fresh attempt directory (step 1 above), only after
   `sacct` reports the job terminal (`COMPLETED`, `0:0`): files copied from
   a running job are partial by construction.
2. **Write a terminal marker atomically** when the copy finished with exit
   status 0: write `attempt-2/COLLECTED.tmp`, then `mv` it to
   `attempt-2/COLLECTED` (a rename is atomic on one filesystem). An attempt
   directory without the marker is partial: never read from it, never
   reuse its hashes. The marker belongs to the harness; lean-pkg does not
   write or read it.
3. **Check the expected roles, each exactly once**: one `verify-<J>.log`,
   one `verify-<J>.out`, one `verify-<J>.err` and, for a sealed job, one
   `artifacts-<J>/` with its `artifacts.sha256`. A missing role or a
   duplicate (two logs, an `.out` of another job) refuses; never pick one
   of several candidates.
4. **Read back full hashes and byte counts, the source and the job.** These
   come from the job, never from the collector: the log's SHA-256 against
   the stdout's `verify-log-sha256` line, every artifact's size and SHA-256
   against the manifest whose hash the stdout carries (`--artifacts DIR`),
   the log's `commit:` and `job-id:` lines against `--commit` and `--job`,
   and `sacct` against the job (with `--sacct-file` on a host without
   Slurm). `receipt slurm --check ... --artifacts DIR` runs all of them and
   writes nothing; a stale attempt (another job's or another commit's
   files) fails here.
5. **Validate and write**: `receipt slurm --job J ... --artifacts DIR`
   (add `--compress-log` for a large log: the receipt still binds the
   uncompressed log's SHA-256), then 
   `lean-pkg gate NAME --check --receipt PATH` before `gate`.

Steps 1 to 3 are the harness's; steps 4 and 5 are lean-pkg's checks, which
judge only the files they are given.

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
`--setenv NAME=VALUE` adds a variable (`PATH` is always passed). Lake's
local artifact cache is isolated by default: unless `--setenv` names them,
the unit gets `LAKE_CACHE_DIR` set to a fresh, empty temporary directory
(removed after the run) and `LAKE_ARTIFACT_CACHE=false` (see
[Fresh builds and LEAN_PATH](#fresh-builds-and-lean_path)). An explicit
`--setenv LAKE_CACHE_DIR=DIR` is used as given (keep it empty);
`LAKE_CACHE_DIR=` (empty: Lake then uses `.lake/cache`, it does not disable
the cache) and a `LAKE_ARTIFACT_CACHE` other than `false`/`0` are usage
errors. The unit builds in the working tree, so start from a clean build
(`lake clean`): a log showing a root-package module `Replayed` is refused
like one showing it `Reused`. A unit
started with `--wait --collect` vanishes the moment it finishes, and its
exit status and resource accounting with it; `RemainAfterExit=yes` keeps
the finished unit loaded instead. lean-pkg polls `systemctl --user show`
until the unit is terminal, writes its properties and this host's name to
`--properties-out` (default `<log>.unit.properties`, kept even when the
receipt is refused, so the run's evidence is never lost), and only then
stops and resets the unit. `--log`, `--stdout`, `--stderr` and that file
must not exist before the run. A local run verifies the working tree, so
the checkout must be at `--commit` with no change to a tracked file and no
untracked file under the package directory (`--repo`) before the run (else
nothing is run) and still after it: an untracked file there can be imported
by committed code, so the build would not be the commit's. Untracked means
listed by `git ls-files --others --exclude-standard`, less `.lean-receipts/`,
`.lake/` (Lake's build state) and the run's own `--log`, `--stdout`,
`--stderr` and properties files. Files git ignores are not checked, so they
are not bound: keep sources out of ignored paths. For a package in a subdirectory of a manuscript repository,
edits elsewhere in that repository do not count, as for the evidence-only
rule below. Over ssh, when the
session lacks them, `XDG_RUNTIME_DIR=/run/user/$UID` and
`DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus` are set for
`systemd-run` and `systemctl`.

**An interrupted `--run`.** Ctrl-C (SIGINT), SIGTERM or SIGHUP while
lean-pkg waits for the unit does not stop the unit: it keeps running under
the user manager. lean-pkg reads the unit's state, keeps the temporary
`LAKE_CACHE_DIR` while the unit is (or may be) still running, since the
build may still be using it, and prints where it is with the `rm -rf`
to run once the unit has finished; it removes the directory itself when the
unit is gone or already finished. It writes nothing to `.lean-receipts/`,
exits 128 + the signal number (130 for Ctrl-C) and prints the recovery
command, the same command without `--run` (add any other flags of the
first run, such as `--out` or `--append-readme`):

```bash
lean-pkg receipt systemd --unit audit-gate.service --log /tmp/gate/verify.log --commit <sha> \
  --repo <repo> --stdout /tmp/gate/unit.out --stderr /tmp/gate/unit.err --checker "gate.sh (lake build; #print axioms)"
```

Run it once `systemctl --user show audit-gate.service -p ActiveState,SubState`
says the unit finished (RemainAfterExit keeps it loaded until then), then
`systemctl --user stop` and `reset-failed` it. Recovery through `--unit`
reads the unit live and skips one `--run` check: that the source tree was
unchanged during the run (HEAD at `--commit`, no tracked change, no
untracked file); the interrupted run prints a WARN saying so. Check
`git status` yourself before relying on such a receipt.

**A unit that has already run.** `--properties-file F` reads saved
properties, captured before the unit was collected:

```bash
{ systemctl --user show audit-gate.service -p Id,LoadState,ActiveState,SubState,Result,InvocationID,\
ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,ExecMainStartTimestampMonotonic,\
ExecMainExitTimestampMonotonic,NRestarts,MemoryPeak,CPUUsageNSec,MemoryMax,MemorySwapMax,CPUQuotaPerSecUSec,\
RuntimeMaxUSec,OOMPolicy; echo "Hostname=$(hostname)"; } > unit.properties
```

The `Hostname=` line names the host the unit ran on; its label is part of
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
`systemd-user:<unit>:<InvocationID>@<host label>`, the host label being
`h-` and the first 12 hex digits of the SHA-256 of the lower-cased host
name: distinct hosts stay distinct and anyone who knows the name can
recompute the label, but the name itself (often a user or site identifier,
which the scan blocks) is never written. The receipt, the redacted log
and `<commit12>-<slug>-unit.properties` (the recognised properties only,
in a fixed order, the host last, as its label) go to `.lean-receipts/`, where `<slug>`
is the job_id as `gate` names the stored file (`:` and `@` become `-`).
The receipt gains the additive key `unit_properties_sha256`, which `gate`
ignores, beside the seal keys. The README entry's first lines name the
unit, the invocation, the host, the result, the runtime, CPU time, memory
peak and the caps, and a `- Unit properties:` line names the file and its
SHA-256.

**The producer (`--run`).** A `--run` lives as long as the verification,
hours for a large package, and writes the receipt only at the end. Before
it starts the unit, lean-pkg fingerprints itself: the SHA-256 of every file
of the imported `lean_pkg` package directory (its Python sources and the
scripts, schemas and templates it ships; `__pycache__/` and `*.pyc` are
skipped), of the installed distribution's `*.dist-info` files (`RECORD`,
`METADATA`, `direct_url.json`, `entry_points.txt`) when it is the one the
package was imported from, of the Python interpreter and of the entry-point
script. It checks the fingerprint again about once a minute while it waits
(the first change prints a `WARN` naming the files and saying the receipt
will be refused; the unit keeps running) and always at final collection,
before anything is written: any entry added, removed or changed refuses,
naming it:

```text
✗ lean-pkg itself changed during the run: 1 file(s) of the running lean-pkg differ from the run start:
  lean_pkg/receipt_check.py; the receipt would be written by other code than the one that started the run. ...
```

A change outside the package directory (the rest of an editable checkout:
README, tests, `.git/`) does not count. A passing run seals what produced
it into the receipt as the additive key `producer`, which `gate` and
`receipt check` ignore (an older lean-pkg reads such a receipt unchanged,
so this is no schema change):

```json
"producer": {"lean_pkg": "0.11.0", "install": "installed", "commit": null, "dirty": null,
             "sources_sha256": "<64 hex>", "source_files": 57, "python": "3.13.1"}
```

`install` is what `lean-pkg --version` detects (`editable`, `installed` or
`unknown`); `commit` and `dirty` are the editable checkout's HEAD and
whether a tracked file differed, both as of the run start; no path is
written. The README entry gains a `- Producer:` line. `sources_sha256` can
be recomputed from any install of the same release:

```bash
cd "$(python3 -c 'import lean_pkg, os; print(os.path.dirname(lean_pkg.__file__))')" &&
  find . -type f ! -path '*/__pycache__/*' ! -name '*.pyc' | sed 's|^\./||' | LC_ALL=C sort |
  xargs -d '\n' sha256sum | sha256sum
```

The forms without `--run` (`receipt slurm`, `receipt systemd --unit` or
`--properties-file`) only post-process evidence lean-pkg did not run, in
seconds, and carry no `producer` key.

### Long runs: freeze the producer

Run a long `receipt local --run` from a lean-pkg nobody else updates while
it runs. A shared editable checkout (`pip install -e` of a clone other
agents `git pull` in) moves under a running job: the field report that led
to the check above lost a capped full run when such a checkout went from
0.9.0 to 0.10.0 mid-run. Hashing only the version string or the
`lean-pkg` entry-point script would have missed it: the version changes only
at a release, and the entry point is a short wrapper that imports the package;
what moved was the package's internal sources.

- **Install from a tag, per run or per release.** Install with pipx or
  into a venv of its own, and call that install's `lean-pkg` by its full
  path in the job. Install a new tag next to the old one rather than
  upgrading in place under a running job:

  ```bash
  pipx install "git+https://github.com/lean-pkg/lean-pkg.git@vX.Y.Z"
  # or a venv per release
  python3 -m venv /opt/lean-pkg-X.Y.Z
  /opt/lean-pkg-X.Y.Z/bin/pip install "git+https://github.com/lean-pkg/lean-pkg.git@vX.Y.Z"
  ```

- **Never a shared editable checkout for a long run.** If you must run from
  a checkout, use a private `git worktree` at a tag that only this job uses.
- **Check before you start.** `lean-pkg --version` prints
  `lean-pkg X.Y.Z (installed <dir>)` for a frozen install and
  `lean-pkg X.Y.Z (<sha>[+dirty], editable <checkout>)` for an editable
  one; `lean-pkg doctor` shows the same and how far the checkout is behind.
- **Your own guard, if you keep one.** A manifest of the producer's files
  hashed before and after the run is a good second check; hash the package
  directory's files (as above), not only the version or the entry point.
  When the manifest covers a git checkout, inspect it with
  `git --no-optional-locks status` (or set `GIT_OPTIONAL_LOCKS=0`): a plain
  `git status` refreshes the stat cache in `.git/index` and rewrites the
  file (always so in a copied checkout, whose files' inodes and change
  times differ from those the index records), so an index hashed before it
  is changed by the check itself. Byte-compare such a file before and after
  a git command when in doubt. `lean-pkg --version`, `doctor` and the
  producer fingerprint probe an editable checkout with
  `--no-optional-locks`.

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
| `slurm_sacct` | optional, additive: the sacct seal of a Slurm claim (`job_id` J, `host_policy` `Slurm job J ...`), written after the job by `comparator-receipt --seal-sacct`: `{schema: slurm-sacct-seal/1, job, file, sha256, state, exit_code, start, end, elapsed, checks, source}`, `file` the normalized record `.lean-receipts/<commit12>-comparator-sacct.txt` it hashes. Only a sealed claim backs a `coverage.yaml` `compute` job; see [Comparator](comparator.md#seal-the-slurm-claim-with-sacct) |
| `stack_limit` | optional, additive, not validated: `{soft_kb, hard_kb, source}`, the soft stack limit the runner's `lake`/`lean` children ran under (`soft_kb`/`hard_kb` an integer or `unlimited`; `source` `inherited` or `COMPARATOR_RECEIPT_STACK`); see [Comparator](comparator.md#stages-and---prepare) |
| `lean_threads` | optional, additive, not validated: the `LEAN_NUM_THREADS` the runner gave its `lake`/`lean` children (`COMPARATOR_RECEIPT_LEAN_THREADS`), or `null` when unset; see [Comparator](comparator.md#stages-and---prepare) |
| `judges`, `unit_size` | optional, additive, not validated (toolchain mechanism): the judge pool width (`--judges`, `LEAN_PKG_COMPARATOR_JOBS`) and the names per judge unit (`--unit-size`, `LEAN_PKG_COMPARATOR_UNIT_SIZE`), `unit_size` `null` when each configuration was cut into `judges` shards (the default); see [Comparator](comparator.md#stages-and---prepare) |
| `build_provenance` | optional, additive, not validated: `{compiled, modules_built, prebuilt, source}`, whether the `build` stage compiled any of the package's oleans (`modules_built` an integer, or `null` when the stage was skipped) or found them prebuilt; `source` is `COMPARATOR_RECEIPT_BUILD_SOURCE` (free text, redacted) or `null`; carried from `stages.json` when the setup was reused; printed by `comparator --check` and `gate --check`; see [Comparator](comparator.md#stages-and---prepare) |
| `olean_manifest` | optional, additive, not validated: `{schema, files, sha256, lean_toolchain, lake_manifest_sha256, captured}`, the digest of the sorted `<path> <size> <sha256>` lines of the package's own compiled modules under `.lake/build/lib` the judge read (`comparator-stages/03-build.oleans.sha256`); `captured` is `build`, or `reuse (...)` for a setup prepared before the field existed; the runner refused the run had they changed between the setup and the end of the comparison; printed by `comparator --check` and `gate --check`; see [Olean manifest](comparator.md#stages-and---prepare) |
| `stages` | optional, additive, not validated: the runner's stage summary (`stage`, `status`, `exit`, `wall_s` for `checkout`, `cache-get`, `build`, `comparator`); the full logs stay in the runner's evidence directory, see [The comparator tier](comparator.md) |
| `test_only` | present and true only on receipts produced under the runner's test-only digest override; such a receipt is refused by every validator |
| `result` | `"pass"` (the runner writes a receipt only on PASS) |
| `configs[]` | exactly the `comparator*.json` / `comparator/config*.json` files tracked at the bound commit, no omissions, no extras. Each entry: `path`; `sha256` of the file's bytes at the bound commit; `challenge_module`, `solution_module`, `theorem_names` (non-empty), `definition_names`, `permitted_axioms` (each equal to the file); `signoff` (present when the configuration has definition holes: `{source: sidecar|inline, definitions: {<hole>: {reviewer, date, judged_as}}}`, the runner's copy of the sign-off it linted); `judged_as` (only for a deprecated inline sign-off, copied from the file); `log` = `.lean-receipts/<commit12>-comparator-<cfgname>.log` where `cfgname` is the path without `.json` and with `/` replaced by `_`; `log_sha256`; `result: "pass"`. The log must contain the full line `Your solution is okay!` for both mechanisms (`lake comparator` prints it last, after the per-kernel acceptance lines; the runner and the validator share one predicate). Symlinked evidence is refused |

A config whose file at the bound commit has a non-empty `definition_names` must be signed off in `lean-pkg-signoff.json` at that commit, its `config_sha256` equal to the config's bytes (deprecated fallback, with a warning: an inline `judged_as` in the config; see [Comparator](comparator.md#definition-holes-need-a-human-sign-off)); the rule is judged against the tracked bytes, never the receipt's copy, and a `signoff` copy in the receipt must equal it. Because the receipt binds each config's bytes, migrating a config's inline keys to the sidecar (`comparator config --migrate-signoff`) needs a new comparator receipt.

Validation fails closed on the first miss and names the field. A file already
at the canonical `.lean-receipts/` path that differs from the receipt being
recorded is refused, so a registry pointer can never silently refer to
different bytes. Every check on both copies (the lower-tier receipt and the
comparator receipt) runs before either is written, so a refusal leaves
`.lean-receipts/` as it was; a `.lean-receipts` or destination file that is
a symlink is refused, never written through.

## Evidence binding: the evidence-only descendant rule

A receipt names commit X, but the receipt file is committed afterwards, so it
can never be tracked at the commit it names. The rule:

> A receipt bound to commit X is accepted at HEAD H when H = X, or when H
> descends from X and `git diff --name-only X H` is non-empty and contains
> only paths under `.lean-receipts/`. Any other difference is drift.

Three exceptions keep "evidence" from changing what the build reads:

- A Lake build cache or compiled artifact is never evidence, even under
  `.lean-receipts/`: a path with a `.lake` component at any depth, or a file
  ending in one of Lake's artifact suffixes (`.olean`, `.olean.server`,
  `.olean.private`, `.ilean`, `.ir`, `.ir.sig`, `.trace`, `.hash`, `.c`,
  `.c.o.export`, `.c.o.noexport`, `.bc`, `.bc.o`, `.o`, `.ltar`, `.so`,
  `.dylib`, `.dll`, `.a`, `.lib`, `.exe`; compared case-insensitively). The
  refusal names the path and says `build cache/artifact`.

- For a package in a subdirectory of its repository, a `.gitattributes`,
  `.gitmodules` or `.lfsconfig` changed at the repository root or in any
  directory between it and the package is drift, named in the refusal, even
  when nothing under the package directory changed (filters, eol and LFS
  change the bytes the build sees).
- No committed symlink at X may resolve into `.lean-receipts/` or to the
  recorded `formalization_yaml` (chains and directory symlinks are followed):
  `gate`, `comparator` and `receipt` refuse such a symlink at X, naming it,
  and a changed evidence path that a symlink at X points to is drift.

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

### Docs-only descendants (opt-in)

An agent that commits only notes inside the package directory (a README, a
`docs/` page, notes on the cluster run) after the source commit X moves HEAD
away from X, and the evidence-only rule then refuses the receipt: the only
ways forward were to rerun the check at the new HEAD or to rebind by hand. A
package can opt in to a second rule:

```
lean-pkg update NAME --accept-docs-descendants --reason "notes are committed beside the code"
lean-pkg update NAME --no-accept-docs-descendants --reason "back to evidence-only"
lean-pkg update NAME --build-inputs 'data/*.csv,*.json' --reason "read at elaboration time"
```

The record field is `docs_descendants` (boolean; absent means false) and the
optional `build_inputs` is a list of globs. Each flag needs `--reason`,
records an `updated` event (the field in `fields`, its old value as
`<field>_from`), follows the lease rule and accepts `--dry-run`.

> With `docs_descendants: true`, a receipt bound to commit X is also accepted
> at HEAD H when H descends from X and no build input under the package
> directory changed between X and H.

Build inputs, matched on paths relative to the package directory:

- every `*.lean` file a `lean_lib` or `lean_exe` of the package owns, read
  from `lakefile.toml` at X and at H, following Lake: a library owns every
  module that has one of its `roots` as a prefix (the roots default to the
  library's name) or matches one of its `globs` (`Foo`, `Foo.*`, `Foo.+`, all
  read as prefixes), under the package `srcDir` joined with the library's
  `srcDir`; an executable owns its `root` module (default: its name) and the
  modules below it. A `.lean` file outside every target (a `#check` /
  `#print axioms` generator under `verification/`, a scratch file) is not a
  build input unless the checker names it or `build_inputs` matches it.
  Every `*.lean` file at any depth counts when the package has a
  `lakefile.lean` (Lake uses it over `lakefile.toml`; it is code, so it is not
  read), has no `lakefile.toml`, or has one this rule cannot read precisely
  (it does not parse, declares no `lean_lib`/`lean_exe`, declares an unknown
  `[[...]]` table, or a `srcDir` that leaves the package); the refusal then
  says which;
- every file under an `input_file` / `input_dir` target's path and under a
  `require` path dependency's directory;
- `lakefile.*` (`lakefile.lean`, `lakefile.toml`), `lake-manifest.json` and
  `lean-toolchain`, at any depth;
- every path matching a glob in the record's `build_inputs` (`fnmatch`, so
  `*` also crosses `/`; a glob without `/` also matches a file name at any
  depth), except a path under `.lean-receipts/`: that directory is never a
  build input whatever `build_inputs` says. It holds the evidence the gate
  reads (receipt JSON, redacted logs, `.log.gz`, unit properties), which the
  evidence-only rule already admits, so an extension-wide glob (`*.json`,
  `**/*.json`) or an "everything but `README.md`" glob such as `[!R]*` does
  not turn a newly committed receipt into a build input. A symlink into
  `.lean-receipts/` stays refused (below);
- best effort: each word of the receipt's `checker` text that names a file
  tracked at X (a `scripts/check.sh` named there, for example).

Always refused under the opt-in, whatever `build_inputs` says:

- a Lake build cache or compiled artifact (#5), checked before any other
  admission, `.lean-receipts/` included: any path with a `.lake` component at
  any depth (`.lake/build/lib/lean/X.olean`, a `.trace` or `.hash` there,
  `.lake/packages/dep/...`, a nested package's `sub/.lake/...`), and any file
  ending in a Lake artifact suffix anywhere (`Foo.olean`, `libFoo.so`,
  `build/ir/Foo.c`; the list is the one above). Lake reads committed caches
  in place of rebuilding, so such a commit could change what a build loads
  while every `.lean` file stays the same. The refusal names the path and
  says `build cache/artifact`; a `.lake` path inside `.lean-receipts/` is
  refused too, while ordinary evidence there stays admitted. A nested
  registered package this package does not depend on stays ignored as a
  whole, its `.lake/` included;
- a symlink (mode 120000) or a gitlink/submodule (mode 160000) added,
  changed or removed anywhere under the package directory;
- a change to a file (or under a directory) that a symlink committed at X
  points to: the build reads it through the symlink;
- any change to `.gitattributes`, `.gitmodules` or `.lfsconfig`;
- for a package in a subdirectory of its repository, any change to a
  `.gitattributes`, `.gitmodules` or `.lfsconfig` at the repository root or in
  any directory between the root and the package directory (`lean/` for a
  package at `lean/contrib/`): smudge/clean filters, eol conversion and LFS
  change the bytes the build sees without changing a `.lean` blob. The refusal
  names the file;
- any change to `claims.yaml` (and to the recorded `claims_file`): `gate`
  reads the claims at the receipt commit, so they stay part of what it binds.
  When it is the only change, the refusal says
  `claims.yaml changed after the receipt commit`: commit the claims before
  the verify run, or re-gate (see
  [Commit the claims before the gate](lifecycle.md#commit-the-claims-before-the-gate)).

Deletions and renames of build inputs count: the diff is read with
`git diff --no-renames --raw`, so renaming `Main.lean` to `Main.md` is a
deleted `.lean` file. The recorded `formalization_yaml` keeps the
derived-keys-only rule above. Apart from those attribute files above the
package, paths outside the package directory stay irrelevant, as for the
evidence-only rule. The evidence-only rule is always
tried first; the docs-only rule only applies when it refuses.

The gated commit stays X, never H: the receipt is copied under X's name and
the event records `commit: X`. When the docs-only rule admitted H, the text
output says

```
✓ HEAD <H12> is a docs-only descendant of <X12> (3 non-build file(s): README.md, docs/x.md, evidence/notes.md; opted in)
```

and the `gate-passed`, `regated`, `comparator-attached` (or
`comparator-replaced`) event and the `--json` payloads (also of
`gate --check` and `comparator --check`, which write nothing) carry the
additive fields `descendant_of: X` and `docs_paths` (the changed non-build
paths, at most 50). The same rule decides `promote`'s binding line, `audit`'s
`receipt-drift`/`comparator-drift`, `next`, `palomar prepare`, `update --path`
and the claims-synced checks. A refusal under the opt-in names the first
changed build input(s) and says the receipt must be rebuilt at HEAD. Without
the opt-in nothing changes, except that a refusal the docs-only rule would have
admitted ends with the hint that the package can opt in with
`lean-pkg update NAME --accept-docs-descendants`.

Why it is opt-in: the receipt then no longer certifies that HEAD's package
directory is byte-identical to X. It certifies X; what H adds is limited to
files the build is assumed not to read. That assumption holds for a package
whose build reads only the listed inputs, and fails for one that reads other
files while elaborating (`include_str`, a data file loaded by a macro or
`initialize`, a script outside the list called from the lakefile): such a
package must list them in `build_inputs`, or stay with the evidence-only rule.
lean-pkg cannot check that the list is complete, so the maintainer decides per
package.

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
target only descends stays inside, and so does any chain of them. Reading
the tree fails closed: a git error is a refusal, never an empty tree.

## Sibling packages in one repository

A manuscript repository can hold several packages side by side, for example
`lean/` for a first round and `lean-r2/` for the next. Each is registered on
its own and gated on its own receipt, and because the binding is scoped to
the package directory, commits to `lean-r2/` are never drift for `lean/`.
Register the second with `register NAME --path DIR --like OTHER`: it takes
the repository and project fields from OTHER and notes whether the toolchain
and Mathlib pin match. Prefer siblings to nesting (`register` warns about a
package directory inside another). A nested package that is registered
itself (`lean/contrib/` inside `lean/`) is not drift for the outer one: both
binding rules (and so `gate`, `gate --check`, `comparator`, `next`, `audit`,
the claims ledger and `promote`) leave out the changes under the root of
every other registered package that lies inside the outer package's
directory, as the declaration scan does. That holds only while the outer
package does not read it. The inner directory stays a build input of the
outer one, and the refusal names it with the reason, when, at X or at H, the
outer `lake-manifest.json` has a `path` package at, inside or above it (or
cannot be read), the outer `lakefile.toml` requires it by path or has a
`lean_lib`, `lean_exe` or input target whose sources lie there, the outer
`lakefile.lean` mentions its name at all (read conservatively), or a symlink
committed at X outside it resolves into it. An unregistered nested directory
is part of the outer package as before. To reuse the first package's results,
depend on it from git at a pinned rev, not by path (see above).

## Progress files live outside the package directory

Any change inside the package directory, other than `.lean-receipts/`, needs
a new receipt and a replay. That is deliberate and has no opt-out. So files
that change after every replay (a progress log, a state file, a statement
ledger, a roadmap, wave notes) belong outside the package directory, where
they are never drift:

```text
Article-X/                      manuscript repository
  PROGRESS.md                   progress log, state file, ledgers, roadmap
  .lean-dev-state.json
  STATEMENT_LEDGER.md  BOUNDARIES.md  ROADMAP.md
  verification/<wave>/          wave notes and replay transcripts
  lean/                         the package: registered local_path
    lakefile.toml  lean-toolchain  lake-manifest.json  claims.yaml
    Pkg/ ...
    .lean-receipts/             receipts (`receipt slurm` writes here)
```

When the package is its own repository checked out inside the manuscript
repository (as `lean/` above), the progress files go in the manuscript
repository beside it. A companion repository that holds both keeps the Lake
package in a subdirectory, registers that subdirectory as `local_path`, and
keeps the progress files at its root. `claims.yaml` stays inside the package:
`gate` reads it at the receipt's commit. A change to a `.lean` file, even to a
docstring, always needs a new receipt.

## Where evidence lives

Receipts and logs are copied into `<repo>/.lean-receipts/` by `gate` and
`comparator` (never committed by the tool; you commit them). The registry
stores only the relative paths; `promote` re-reads the bytes, and refuses
unless the receipts and every comparator log are committed at HEAD unmodified
and HEAD is contained in `origin/<branch>` (judged on local refs; nothing is
fetched). An
[index entry](index-catalog.md) keeps verbatim copies of the receipts (not
the logs) and `index check --clone` re-derives them at the pin, so the logs,
the lower-tier receipt's sealed verify log among them, must be committed at
the pin or an evidence-only descendant.

`lean-pkg formalization` reports the receipts under `status.evidence` of the
package's `formalization.yaml` (commit, gate receipt, comparator receipt) and
lists every tracked comparator configuration under `status.main_results`; see
[formalization.yaml](formalization.md).

### Checking a published package's receipts (`receipt check`)

`gate`, `comparator` and `promote` read the author's registry record. A
reader who has only the public repository runs the same validators with
`receipt check`, which reads no registry, opens no network connection and never
runs Lean:

```bash
git clone https://github.com/OWNER/REPO && cd REPO
lean-pkg receipt check --repo . --commit <X>              # X: the page's `git checkout` commit
lean-pkg receipt check --repo . --commit <X> --json       # every check with its verdict
```

It clones `--repo` privately (local transport only), checks out the published
ref (`--at REF`, default HEAD) and reads everything from git there and at X;
the working tree of `--repo` is never read or changed. It checks:

| Check | What passes |
|---|---|
| `receipts` | at least one lower-tier receipt tracked under `.lean-receipts/` at REF binds X; a receipt file named for X that binds another commit fails |
| `binding` | REF is X or an evidence-only descendant of it; with `--docs-descendants`, also a docs-only one ([Docs-only descendants](#docs-only-descendants-opt-in)) |
| `lower <receipt>` | `gate`'s validator: every receipt key, the binding, `lean_toolchain` and `mathlib_rev` against `lean-toolchain` and `lake-manifest.json` at X, no path dependency outside the package, `sorry_count` 0, axioms within `propext`, `Quot.sound`, `Classical.choice` |
| `... report log` | `report_sha256` is the SHA-256 of the committed redacted log (or of its `.gz`, decompressed); a missing log fails |
| `... evidence files` | `manifest_sha256`, `artifacts_manifest_sha256`, `unit_properties_sha256` against the files they name, when present; `job_stdout_sha256` is not checkable (the job's stdout is not published) |
| `claims` | `claims.yaml` at X passes the claims/1 schema and the declaration check `gate` runs |
| `comparator <receipt>` | `gate`'s validator: the exact set of configurations tracked at X, each one's SHA-256, theorem names and sign-off, each log's SHA-256 and PASS line, the lower tier's commit |
| `... olean manifest` | the receipt's `olean_manifest` names the toolchain and `lake-manifest.json` of X (the manifest file itself is not published) |
| `claimed theorems` | every `reusable` theorem or lemma claim at X is named by a comparator configuration at X |
| `coverage` | `coverage.yaml` at REF passes `coverage check`, R11 included: each compared theorem is the declaration of a claim a row names |
| `page: ...` | the README page block at REF states X, the pins of X, each Evidence row's path, commit and SHA-256 as committed, and the receipts' sorry count, axioms and comparator counts |

A check that does not apply (no `coverage.yaml`, no page block, no olean
manifest) is `skip`, with the reason. Exit 0 only when no check fails, 1
otherwise, 2 on a usage error. The docs-only opt-in is a field of the
author's registry record, which the reader cannot see, so `--docs-descendants`
is the reader's own choice; the result prints which rule was applied.
Re-running Lean is not part of it: see
[Re-verify a page yourself](trust-model.md#re-verify-a-page-yourself).

### Sealed logs and whitespace checks

A receipt binds each log by its sha256, so a committed log must keep its
bytes exactly. Checker output can contain lines of only spaces, which
`git diff --check` (and editors or hooks that strip trailing whitespace)
flag. Never normalize a sealed log: the receipt would no longer match it.
Narrow the whitespace check instead, for example in CI:

```bash
git diff --check origin/main...HEAD -- . ':(exclude).lean-receipts/'
```

lean-pkg does not write a `.gitattributes` (such as `*.log -whitespace`) for
you: `gate` treats a changed `.gitattributes` as a build input, so adding one
would itself require a new gate. Add it yourself, before gating, if you prefer
it to a pathspec.

### Restoring preserved evidence

Two lookup rules apply, and they differ on purpose:

- **The redacted verify log** of a lower-tier receipt (`report_sha256`) is
  found beside the receipt file, in the package's `.lean-receipts/`, or
  tracked there at HEAD. An archived receipt with its log next to it checks
  wherever the pair lives.
- **Comparator logs** resolve relative to the package checkout only (the
  registered `local_path`, or `--repo DIR` for `--check`): each
  `configs[].log` is `.lean-receipts/<commit12>-comparator-<cfgname>.log`
  under that checkout, never beside the comparator receipt. A log that is
  not there is refused as `comparator log missing: ...`, naming the
  checkout and the absolute path tried.

So evidence preserved outside the checkout (a paper repository's evidence
folder, a backup) has to be put back before `gate --check` or
`comparator --check` passes. Restore it; never rerun or reseal. This copy uses only what
the comparator receipt records, refuses a file whose bytes differ from its
`log_sha256`, and never overwrites a file already at the target:

```bash
python3 - PRESERVED/<commit12>-comparator.json PRESERVED PKG <<'EOF'
import hashlib, json, pathlib, shutil, sys
receipt, src, repo = json.loads(pathlib.Path(sys.argv[1]).read_text()), *map(pathlib.Path, sys.argv[2:4])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
bad = 0
for c in receipt["configs"]:
    rel, want = c["log"], c["log_sha256"].lower()
    if not rel.startswith(".lean-receipts/") or ".." in rel.split("/"):
        print("REFUSED (log path outside .lean-receipts/):", rel); bad = 1; continue
    s, d = src / pathlib.PurePosixPath(rel).name, repo / rel
    if not s.is_file() or s.is_symlink() or sha(s) != want:
        print("REFUSED (preserved copy missing or not the receipt's bytes):", s); bad = 1; continue
    if d.exists():
        print("present" if d.is_file() and sha(d) == want else "REFUSED (target differs):", d)
        bad |= not (d.is_file() and sha(d) == want); continue
    d.parent.mkdir(exist_ok=True); shutil.copyfile(s, d)
    print("restored" if sha(d) == want else "REFUSED (copy differs):", d); bad |= sha(d) != want
sys.exit(bad)
EOF
```

`PRESERVED` is the folder holding the comparator receipt and its logs (flat,
by file name); `PKG` is the package checkout the check reads (the registered
`local_path`, which is the package directory, not necessarily the repository
root, or the `--repo DIR` you pass). Then rerun
`lean-pkg gate NAME --check --receipt ... --comparator ...`, which rehashes
every log itself: the copy's own checks are a convenience, the validator is
the judge. A log that was preserved with different bytes (re-encoded,
whitespace-stripped, truncated) cannot be restored; only a new comparator
run can replace it.

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
   commit, scans it, and moves the registry entry there. The patterns file's
   `allow:` lines, and only those, are carried into a committed
   `.lean-pkg-public-allow`, so the new repository's own `public-safety`
   still exempts the benign strings the export scan exempted (a fixture
   address under `.invalid`, a generic CI runner path in an archived log);
   an allow line that itself holds a private string refuses the export. Gate and comparator
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
```

Theorem numbers quoted from an
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

### Before freezing a public candidate

A field report: a public candidate was about to be frozen from private
history. A read-only review found 113 references to the project's private
working citations in each of five ancestor commits, yet
`lean-pkg public-safety NAME --history --strict` reported 0 hits, and the tree
at HEAD was clean. The built-in patterns know generic private strings (home
paths, internal directories, e-mail addresses, LaTeX-label shapes, the
record's own agent and host names); they cannot know a project's own terms.
Before freezing a candidate made from private history:

1. Write the project's own terms into `.lean-pkg-private-patterns` as
   `block:` lines. The kinds that matter: private citation-key prefixes and
   key formats, internal note and review file names or directories, reviewer
   and agent handles, private repository names and working titles. Write
   them as regular expressions that match the family, not as a list of
   every string, and never quote the strings themselves in public
   documentation or issues; the shapes below are placeholders:

   ```text
   # private citation keys of the working bibliography
   block: \b<key-prefix>[a-z]+:[0-9]{2}:
   # internal review and note files, by name
   block: (?i)\b<internal-review-file>\b
   # reviewer and agent handles
   block: \b(?:<handle-1>|<handle-2>)\b
   # private repositories and working titles
   block: (?i)<private-repo-name>|<working-title>
   ```

2. Run `lean-pkg public-safety NAME --history --strict` and read the whole
   report, including `skipped[]` (`--json`) or the `skipped:` lines: a file
   or blob listed there was not scanned in full, so its content is not
   covered by the verdict. Gzip files and blobs are decoded and their
   decoded content scanned (`decoded:` lines, JSON `decoded[]`; a hit
   inside one reads `path (decoded):LINE`), so a `.gz` report or log needs
   no `allow:` line for chance matches in its compressed bytes; one that
   could not be decoded in full (corrupt, truncated, over `--gzip-limit`,
   default 512M) is a blocking `unscanned` hit until you raise the limit or
   allow it explicitly. A `.tar.gz` is decoded but not unpacked: its tar is
   scanned through its printable runs.
3. Treat 0 hits as "none of the patterns matched", not as "no private
   content". When the patterns file has no blocking line the report says
   so (`note: no project-specific patterns: built-ins only`, JSON
   `builtins_only: true`). Sample-check the history with the project's own
   key formats before trusting the result, for example
   `git log --all -p | grep -cE '<your-key-regex>'` for content and
   `git log --all --format=%B | grep -cE '<your-key-regex>'` for commit
   messages. A non-zero count with 0 scan hits means a pattern is missing.
4. When the history holds private material, do not try to clean it commit by
   commit: prefer `lean-pkg release NAME --fresh-repo DIR` (one fresh commit
   of the gate-bound tree, or of `--source-commit SHA`) or, for a tree cleaned
   by hand, `--fresh-repo DIR --from-tree SRCDIR`. The export is scanned with
   the source checkout's patterns file. After the export the registry entry
   points at DIR, which has no patterns file (the export drops it), so copy
   the file into DIR, keep it out of git there (`.git/info/exclude` or
   `.gitignore`), and run `lean-pkg public-safety NAME --history --strict`
   again on the fresh repository.
5. Keep `.lean-pkg-private-patterns` itself out of the published tree. It
   lists the private strings: when it is tracked, `public-safety` reports a
   blocking `patterns-file` hit and a `WARN` naming the fix
   (`git rm --cached .lean-pkg-private-patterns`, then add it to
   `.gitignore`), and `release --fresh-repo` never exports it. A copy left
   in an old commit is a history hit like any other.

Next: [The comparator tier](comparator.md).
