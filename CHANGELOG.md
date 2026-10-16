# Changelog

All notable changes to lean-pkg are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) (while 0.x, a minor bump may
change the registry schema or receipt format, always with a migration).

## [Unreleased]

### Added

- docs/paper-companion.md "Consolidate several checkpoint packages into one": a recipe for merging several
  verified-private packages into one new package under a new name, with existing commands only: a
  relocation-only consolidation commit (no renames), a committed source and claim hash map (sha256 per relocated
  file, old claim id to new claim id, old receipts cited as provenance only), a collision audit before the first
  gate (owned names, claim ids, negative controls and witnesses, comparator configuration), `register --like`,
  fresh gate and comparator receipts, and two environment pitfalls (file modes under `umask 002`, hashing elan's
  `lake` proxy instead of the toolchain binary) (field report).

## [0.7.50] - 2026-10-16

### Added

- `comparator NAME --check --receipt R --repo DIR`: pre-check a comparator receipt made in a worktree or on a
  feature branch against that checkout instead of the registered `local_path`, with the rules of
  `gate --check --repo` (this call only, never saved; stderr note; remote matching; JSON `repo_override`, `null`
  without it). Attaching refuses `--repo` (exit 2) (#13).
- `comparator config --from-claims NAME --claims-file PATH` (all modes, `--challenge-from-type` included): read
  the claims from a working-copy `claims.yaml` instead of the synced registry cache, so a worker without registry
  rights needs no `claims sync`. Checked like `claims check --file PATH NAME`; never written to the registry; the
  configuration equals the registry route's for the same claims. The output names the source (`claims source:`
  line, JSON `claims_source` with the file's sha256, also on the registry route) (#13).
- `receipt slurm --template`: `SRC_MODE=clone` (default `archive`) builds from a new repository holding only
  `COMMIT` (file transport only, its own object store, no untracked or ignored files), refuses unless HEAD is
  `COMMIT` and `git status` is empty, and logs `src-mode:` / `src-head:`; `receipt slurm` and `--check` refuse a
  `src-head:` that is not `--commit`. `DEPS_TAR=PATH` restores `.lake/packages` from a tarball before the
  fresh-build check, refusing a restore that changed the tree outside `.lake/packages` or brings the package's
  root namespace, and logs `deps-restore: tar <sha256>`; the package still compiles fresh (field reports).

### Fixed

- Docs-only rule: a `.lean` file is a build input only when a `lean_lib` or `lean_exe` of the package owns it
  (roots, globs and `srcDir`, read from `lakefile.toml` at the bound commit and at HEAD), the receipt's checker
  names it, or `build_inputs` matches it; `input_file`/`input_dir` targets and `require` path dependencies make
  their paths build inputs. A `#check`/`#print axioms` generator outside every library no longer forces a re-gate.
  A `lakefile.lean` (or an unreadable `lakefile.toml`) keeps every `*.lean` as a build input, and the refusal
  says why (field report).
- A `claims.yaml` changed after the receipt commit, alone, is still refused (gate binds the claims), now with
  `claims.yaml changed after the receipt commit ...: commit claims.yaml before the verify run, or re-gate`;
  docs/lifecycle.md "Commit the claims before the gate" (field report).
- HEAD binding of a package with another registered package nested inside it (`lean/contrib` in `lean/`):
  changes under the nested root are no longer drift for the outer package (gate, `gate --check`, comparator,
  `next`, `audit`, the claims ledger, promote), unless the outer package reads it: a `path` package in its
  `lake-manifest.json`, a `lakefile.toml` require/target there, a `lakefile.lean` naming it, or a symlink into it
  keeps it a build input, and the refusal names the reason. Unregistered nested directories count as before
  (field report).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23426107](https://doi.org/10.5281/zenodo.23426107)

## [0.7.49] - 2026-10-16

### Added

- `comparator-receipt` Lake target readiness: at the end of the `checkout` stage (dependencies present, before
  `cache-get` and `build`), `lake --no-build build M` per configured challenge/solution module; Lake's `unknown
  target` fails the stage naming the module and the `[[lean_lib]]` fix, exit 3 (needs rebuild) passes; each probe is
  a `lake-target:` line in the checkout log (field report).
- `comparator-receipt` (and `--prepare`) refuses, before any stage, a `.lake`, `.lake/packages` or
  `.lake/packages/*` symlink resolving outside the project, naming it: the `lake comparator` sandbox cannot read it
  and failed with `EEXIST` after the setup passed. Copy the dependencies inside the project (field report).
- docs/comparator.md "Challenge types mention only trusted constants": lifted `_proof_N` constants and project proofs
  inside statement types, two rewrite recipes, and a tested `Expr.getUsedConstants` listing (field report).
- docs/receipts.md "Sealed logs and whitespace checks": never normalize sealed logs; exclude `.lean-receipts/` from
  `git diff --check`; lean-pkg writes no `.gitattributes` (a gate build input) (field report).
- `lean-pkg help [TOPIC|COMMAND]`, in the manner of `gh help`: `help exit-codes` (0/1/2, what 1 means for each
  command that answers yes or no, and the codes of comparator-receipt, lean-exercise and the types driver),
  `help environment` (every environment variable lean-pkg, comparator-receipt, lean-exercise and the scripts
  read, with its default; `--json` adds `variables[]`), `help formatting` (`--json`, stdout vs stderr, the
  named schemas, filtering with jq). `help` alone lists the commands and topics; `help COMMAND` prints exactly
  `COMMAND --help`. Reads no registry. A test fails when the source reads an environment variable the
  environment topic does not list.

### Fixed

- A `lake comparator` that fails while resolving the workspace in its sandbox (after `Resolving dependencies`, before
  `Building`) or exits 2 (could not start) is reported as a tool/environment error before judging, not as "did not
  accept the solution" (field report).
### Changed

- Tests: a golden CLI contract (CONTRIBUTING.md, "The golden CLI contract"). It pins the `--help` text of every
  command, the top-level keys of 30 `--json` payloads, an argv table of which invocations write and lock the
  registry (checked against `is_write_invocation` and `locks_registry`), a scrubbed corpus of 97 quoted
  invocations that must keep parsing, the reserved verbs no package may be named, and the options that share the
  `--registry` abbreviation prefix. `make goldens` re-records the help and JSON goldens after an intended change.
  No behaviour change.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23425404](https://doi.org/10.5281/zenodo.23425404)

## [0.7.48] - 2026-10-16

### Added

- `search --loogle-query PATTERN`: Loogle gets PATTERN (a Lean name, term or pattern) instead of the term, whatever
  the route, and every other source still gets the term (`--json` `loogle_query`). A Loogle parse error ("Unknown
  identifier exponential", "expected end of input") now says that Loogle takes Lean syntax, not prose, and names the
  option; the natural-language skip reason names it too (field report).
- `search --require-complete`: exit 1 when the search is incomplete (`complete: false`), after printing the full
  output; `--json` gets `ok: false` and `error` naming the skipped and truncated sources. Default unchanged (field
  report).
- `search` discloses cache writes: an online search refreshes the Palomar snapshot and the Reservoir index under
  `$XDG_CACHE_HOME/lean-pkg` even without `--record`; each block's `cache_writes[]`, `--json` `cache` {`root`,
  `writes[]`} (relative paths) and a `cache: wrote ...` line say so. `--cache-dir DIR` keeps one search's cache in
  DIR (field report).
- `search` `coverage.not_searched_reasons[].detail`: a `scan-error` package names git grep's exit status and a
  fixed cause (`object missing or unreadable`, `pack file unreadable`, `repository owned by another user`, `too many
  open files`, `out of memory`, a signal, ...), never git's stderr; the coverage line shows it. docs/prior-art.md
  explains why coverage can differ between runs (checkouts and git grep outcomes; no shared cache) (field report).
- docs/prior-art.md: "When a public library is not found": what the index, Reservoir and GitHub sources each
  cover, and why a construction inside a third-party library is found by the library's own words (field report).
- `lean-exercise check --challenge FILE --solution FILE`: a new console script (beside `comparator-receipt`) that
  judges a learner's Solution against a Challenge with the toolchain's `lake comparator`, one run per `sorry`'d
  theorem, with a verdict each: `accepted`, `statement_mismatch`, `illegal_axiom` (axiom named, `sorry` for
  `sorryAx`), `kernel_rejected`, `build_error`, `missing`, `timeout`, `tool_error`; exit 0/1/2; `--json` schema
  `exercise-check/1`. Registry-free: it writes only a temporary Lake project (removed unless `--keep DIR`). The
  learner's code is built only inside `lake comparator`'s `bwrap` sandbox; a host that cannot sandbox is refused
  (exit 2) before anything is written. `--no-sandbox` is a labelled self-check (`"trust": "self-check-only"`).
  Refused: a Challenge that declares definitions (shared ones go in `--defs FILE`), axioms outside the standard
  three, Mathlib imports (v1 is Mathlib-free). See docs/exercise.md.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23424170](https://doi.org/10.5281/zenodo.23424170)

## [0.7.47] - 2026-10-16

### Added

- docs/comparator.md: "A cluster without unprivileged user namespaces". Both comparator sandboxes are kernel
  features (a user namespace or setuid `bwrap` for `toolchain`, Landlock for `legacy`), so no Singularity or
  Apptainer recipe supplies them and none is supported; the section names the in-image `host-check` to run on
  a compute node, the supported split (build or `--prepare` on the cluster, copy `.lake/build`, judge on a host
  that passes `host-check` with `COMPARATOR_RECEIPT_BUILD_SOURCE`), and never bypassing the sandbox (field report).

### Fixed

- Soundness: the promote claims ledger (#80), `comparator config --coverage`, `next` and `comparator config
  --from-claims` credited a claim to any comparator `theorem_names` entry with the same last name component, so
  `RMTFoundations.TracyWidom.monotone_TW1R` passed as verified because comparator.json compared
  `RMTFoundations.TWOneMonotone.monotone_TW1R`, a different declaration (field report). A name now covers a claim
  only as its `decl` or `<Prefix>.<decl>` (the scaffold's `<Package>Audit.<decl>`), and only when `decl` is the
  fully qualified name of a declaration its file declares at the gate-bound commit; a bare or partial `decl`
  is credited to no name, and its `no-comparator-config` detail says why. This is a behaviour change: a ledger
  that was `ready` can turn `open`, and packages whose hand-written Challenge names are `<Ns>Audit.<short>`
  for `<ProjectNs>.<short>` lose that credit until the entries name the full declaration (rename to
  `<Ns>Audit.<full decl>` or re-scaffold). docs/lifecycle.md and docs/comparator.md state the rule.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23423420](https://doi.org/10.5281/zenodo.23423420)

## [0.7.46] - 2026-10-16

### Fixed

- docs/comparator.md recommended a whole node (`--exclusive`) for type extraction on a shared cluster, against
  the shipped lean-shared-cluster-verification skill; it now says lower threads and workers, log
  `/proc/sys/fs/file-nr`, and size the allocation from a pilot's `sacct` (field report).
- `--repo DIR` naming the registered local_path no longer prints the note "using --repo P instead of the
  registered local_path P" with the same path twice; JSON `repo_override` is unchanged (field report).
- `receipt slurm` / `receipt systemd` refused a fresh-build log with no `lean-pkg-build:` markers as "Lake
  restored root-package module(s)" when later `lake lean FILE` steps re-printed the warnings of just-built
  modules as `Replayed`. A Reused/Replayed/Unpacked line now counts as a restore only when no earlier `Built`
  line names the same module; a module only ever restored, or shown restored before its `Built`, is still
  refused. The `--template` shell check and comparator `build_provenance.cache_reused` use the same order
  rule (field report).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23422277](https://doi.org/10.5281/zenodo.23422277)

## [0.7.45] - 2026-10-16

### Fixed

- `gate --check --repo DIR` and `next --repo DIR` refused a checkout whose remote is the scp-style
  `Host:/path/repo.git` when the registry's `repo` is that bare repo's local path `/path/repo.git` (field
  report: same repository, reached from another host). A local-path repo now matches `Host:/p`,
  `ssh://[user@]Host[:port]/p` and `file:///p` with the same absolute path; two remotes that both name a host
  still compare exactly, and a relative or `~` path never matches. `--repo` naming the registered local_path
  itself no longer needs a matching remote.
- `comparator-receipt --estimate` found no olean weight in Lake's real layout: Lake writes the package's oleans
  under `.lake/build/lib/lean/`, and the module names were read as `lean.Pkg.Mod`, so every sampled row had
  `weight_bytes: 0`, no slot was heavy and the heaviest-first part of the sample silently did nothing (a field run
  sampled 5 of 16 slots, one of them 19,403 s, all at weight 0). The `lean/` layout component is dropped now
  (`.lake/build/lib/` is still read). A slot's module is also found by a syntactic scan of the package's sources
  for the declaration (following `namespace`/`section`/`mutual`/`end`), so a theorem whose namespace is not its
  module path is weighed; the namespace-prefix rule is the fallback (field report).
- `gate --receipt PATH` checked the sealed log beside `PATH` but copied only the receipt JSON to
  `.lean-receipts/<c12>-<job>.json`, so the stored copy lost its log. The log (plain or `.gz`) now follows it when
  `.lean-receipts/` holds no copy (`sealed log copied to ...`; JSON `report_log.copy[]`; `--check`:
  `· gate would also copy ...`); a symlink at either log name there refuses, nothing written.
- Lake's local artifact cache could defeat a "fresh build": a module restored from it is reported `Reused`
  without its ``declaration uses `sorry` `` warning, so a sorry count scraped from the verify log could read 0
  (checked against the Lake source of v4.35.0-rc2: the cache is read unless switched off, a package lakefile's
  `enableArtifactCache = true` wins over `LAKE_ARTIFACT_CACHE=false`, `LAKE_CACHE_DIR=""` does not disable it).
  `receipt slurm --template` builds with `LAKE_CACHE_DIR` a fresh empty job-local directory and
  `LAKE_ARTIFACT_CACHE=false` (path-free `lake-cache-dir:` / `lake-artifact-cache:` log lines), refuses a committed
  `.lake/build`, brackets the build with `lean-pkg-build:` markers and fails the job when it shows a root-package
  module `Reused`, `Replayed` or `Unpacked`. `receipt systemd --run` gives the unit the same isolation unless
  `--setenv` names the variables (`LAKE_CACHE_DIR=` and `LAKE_ARTIFACT_CACHE=true` are usage errors).
  `receipt slurm` / `receipt systemd` (and `--check`) refuse a log showing a root-package module restored, naming
  it. `comparator-receipt`'s `build_provenance` counts only Lake's `Built` lines as compiled; an olean written
  without one is `restored`, and `Reused` / `Replayed` / `Unpacked` targets are `cache_reused` (additive keys,
  present when non-zero).
- A dependency's build directory could shadow the package's own modules for `lake env lean`: Lake lists the root
  package's lib directory last on `LEAN_PATH` and Lean loads `<R>/` from the first entry holding it. The template's
  default `AXIOMS_CMD` is `lake lean Verify/Axioms.lean` (explicit module paths); the template, `comparator-receipt`
  (after its build stage), `verify-comparator.sh` and `comparator-check.sh` refuse when a `LEAN_PATH` entry other
  than the package's own `.lake/build/lib/lean` holds one of its root namespaces, naming the path (new module
  `lake_hygiene`; one shell function, `lake_shadow_check`, carried verbatim by the template and both scripts). The
  `lean-receipt-discipline` and `lean-axiom-audit` skills use `lake lean` and show the preflight.

### Added

- docs/comparator.md: plan comparator slots from the kernel's classification, not source keywords (a
  Prop-valued `instance` is a theorem constant), with a tested snippet listing a module's theorem constants
  (field report).
- The estimate (still schema `comparator-estimate/2`; additive fields) records `weights_found`, `olean_modules`
  and `weighted_slots`; when no slot has a weight the runner warns on stderr that the heaviest-first part was
  skipped and the text output says `weights_found false`. It also records `remaining_estimate_seconds` with
  `unsampled_slots`, `remaining_per_slot_seconds` and `remaining_estimate_model`: the unsampled slots at the mean
  of the timed non-heavy sampled slots, divided by the judges (the sampled slots are cached for the real run), and
  prints it. `--help` and docs/comparator.md name the schema and what `/1` to `/2` changed (field report).
- `receipt slurm|systemd --compress-log` (field report: a 481-declaration audit made a 740 MiB redacted log, over
  GitHub's 100 MiB file limit): the sealed log is written as deterministic gzip
  `<c12>-<job slug>-verify.redacted.log.gz` (level 9, mtime 0, no file name) instead of the plain `.log`.
  `report_sha256` stays the SHA-256 of the uncompressed log; the receipt gains the additive
  `report_compressed` {format: gzip, sha256 of the .gz, bytes uncompressed}, `sealed[]` lists the `.gz` with its
  own hash (role `redacted-log.gz`), the README entry names the restore command
  (`gzip -dc X.log.gz > X.log`, then `sha256sum`). Without the flag nothing changes but a stderr WARN naming it
  when the redacted log is over 90 MiB. Old receipts are unchanged.
- `gate` and `gate --check` check `X.log.gz` wherever they look for `X.log`: stream-decompressed, the
  uncompressed bytes must hash to `report_sha256` (the `.gz` hash never stands in for it), and with
  `report_compressed` the `.gz` must hash to its `sha256` and decompress to exactly `bytes`; output past that
  length refuses without expanding further (4 GiB without `report_compressed`); not gzip refuses.

### Changed

- `receipt slurm` and `receipt systemd` write `log_commit_bound` into the receipt JSON too (`true` when the verify
  log's `commit:` line names `--commit`, `false` when the log has none and `--stdout` binds it only through
  `lake-manifest.json`); a `false` also adds a `- WARN:` line to the README entry, printed and appended. A
  consumer keeping only the sealed files used to lose the limitation, which only the `--json` response and a
  stderr WARN stated (field report). The key is additive and informational: `gate` never reads it, so a receipt
  claiming `true` is judged exactly like one without it, and receipts written before it existed still pass. A
  re-run over a receipt written by 0.7.44 differs by this key and is refused as a differing file, as before.
- `receipt slurm --check --json` reports `log_commit_bound` as well: `true`/`false` against `--commit`, `null`
  without `--commit` (field report).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23420751](https://doi.org/10.5281/zenodo.23420751)

## [0.7.44] - 2026-10-16

### Fixed

- `claim`/`renew --ttl` is bounded at 8760 hours (one year); a larger value is a usage error (exit 2) with nothing
  written. `--ttl 100000000` used to save the lease and its event, then fail on the expiry computation
  (`OverflowError`, an internal error) and, in committed mode, leave the registry files uncommitted, blocking every
  later committed writer. The board's TTL prompt has the same bound. `audit` and `audit --fix` report a lease whose
  expiry overflows as `lease-invalid` instead of crashing (review finding).
- A write checks that its event log can be appended to before the registry file is replaced: a read-only log or
  an `--events` path that is a directory refuses (exit 1, nothing written). The YAML change used to persist while
  the event line was lost; a failed event-log write now also keeps the queued lines (review finding).
- Committed mode with a registry path that git quotes (a space or a non-ASCII character in a directory name)
  failed with `pathspec ... did not match any files`; the modified files are read from `git status --porcelain -z`
  now (review finding).
- Push recovery rebases with `--autostash`: an unrelated modified file in the registry clone (allowed by the
  preflight) made the rebase refuse with `You have unstaged changes`. After a rejected push the printed recovery is
  `git pull --rebase origin <branch>, then git push origin <branch>` (a bare `git push` would be rejected again).
  The rebased registry is validated before the push: a schema error that neither side had refuses the push and
  resets the clone to the local commit (review finding).
- Tests: the conftest's autouse isolation fixtures have public names (`no_user_default_registry`,
  `home_outside_tmp_path`, `no_real_io_in_search_adapters`), so a scratch `from tests.conftest import *` keeps
  them; with underscore names it silently dropped them and could write to the caller's real registry (review
  finding).
- `search`'s declaration fallback reads Lean with a small scanner (`lean_pkg.lean_scan`) instead of a line regex:
  a theorem inside a `/- ... -/` block comment (nested ones too), a line comment or a string is no longer
  reported; `/-- doc -/ theorem x` on one line, `class inductive`, `instance (priority := n) name`, `axiom`,
  `opaque`, `irreducible_def`, `local`/`scoped instance` and `theorem «my theorem»` are found; an attribute with a
  string or nested brackets (`@[to_additive "See [Foo.add]"]`) and a multi-line attribute keep the declaration and
  its docstring. Declarations carry their fully qualified name (`namespace`/`section`/`mutual` ... `end` tracked),
  so `search DemoA.heatFlowMass_le` finds the namespaced theorem; a plain query still matches the name as written
  (review finding).
- `search` folds accents on both sides: the `git grep` prefilter accepts accented forms of every query letter
  (precomposed or decomposed), so `ito` finds `Itô` and `Gödel` finds `Godel`; file names with non-ASCII
  characters are no longer dropped (`core.quotePath=false`, NUL-separated output); a candidate file that cannot
  be read is counted (`declaration_files_capped[].unreadable`) and makes the search incomplete instead of being
  skipped silently. Palomar and Reservoir `matches()` now fold the text as well as the term, and search and the
  adapters share one `fold` (review finding).
- A local source that did not read everything is reported as incomplete, never as "not found": the registry
  (declaration file cap or unreadable files) and the index (an entry that is not valid YAML or not UTF-8, which
  used to crash the search) set the block's `incomplete`, join `truncated_sources` and make `complete: false`; the
  `--record` page marks them `incomplete:` in its Sources table, leaves them out of `Not found in ...`, names
  them in a `Not fully searched` sentence and counts the capped packages in the coverage sentence (review finding).
- Palomar (30 listed), Reservoir (30) and Loogle (20) report when they found more matches than they list:
  `capped: ["results"]` with `cap_limit` and `total`, so the search is incomplete (`incomplete: palomar: 50
  matches found, first 30 listed`) as GitHub's 30-result cap already was. Loogle's old `N found, first 20 kept`
  note is replaced by this (review finding).
- `search --record --package P --repo-dir DIR` into a folder that is not P's own checkout no longer lists a
  private P's own claims there: they are withheld like any other private package's unless P is public, and the
  `private-claims:` header stays truthful (review finding).
- `claims sync` / `claims check` refuse a claim still carrying a `claims migrate` placeholder (`decl: TODO`, or a
  `summary` starting with `TODO`), naming the claim; before, a `-- TODO` comment in the file let `decl: TODO`
  pass the declaration lookup. A `decl` with «quoted» components (`Demo.«foo bar»`, `Demo.«a.b»`) is accepted
  and split respecting the guillemets (also for `index` headline checks) (review finding).
- `scrub_paths` (search reasons, excerpts, package repos) also redacts cluster and system roots where a path
  starts: `/scratch/<x>`, `/work/<x>`, `/gpfs/<x>`, `/srv/<x>`, `/mnt/<x>`, `/tmp/<x>`, `/lustre/<x>`; URLs and
  relative paths are left alone (review finding).
- Trust boundary of receipts and `gate` (review findings):
  - A committed symlink that resolves into `.lean-receipts/` (or to the recorded `formalization_yaml`) let a later
    commit change a source file as an "evidence-only" edit. `gate`, `comparator` and `receipt slurm|systemd`
    refuse such a symlink at the bound commit, naming it (chains and directory symlinks followed); the
    evidence-only and docs-only rules refuse a changed path that a symlink at the bound commit points to.
  - The plain evidence-only rule accepted, for a package in a subdirectory, a changed `.gitattributes`,
    `.gitmodules` or `.lfsconfig` at the repository root or in a directory between it and the package. It now
    refuses it, naming the file, as the docs-only rule already did (tightening for every package).
  - `receipt` parsed `sorry-count`, the sorry warnings and the axioms after `--redact`, so a broad redaction could
    delete a sorry warning. Every field but `checker` is read from the raw log now, and a redaction that removes or
    alters a line the parser reads refuses.
  - `receipt systemd --run` checked only tracked files, so an untracked file imported by committed code built a
    tree that was not the commit. Untracked files under the package directory refuse now, before and after the
    run (`.lean-receipts/`, `.lake/` and the run's own output files excepted; ignored files are not bound).
  - The receipt public-safety scan did not know this machine's user and host names and missed a home directory
    without a trailing slash. It blocks the local user name and host name (full and short, at a word boundary;
    names under 3 characters and common ones such as `root`, `runner`, `localhost` excepted) and
    `/home/<x>`, `/Users/<x>` with or without the slash.
  - `gate` never checked `report_sha256` against a file. When the redacted verify log
    `<c12>-<job slug>-verify.redacted.log` is beside the receipt, in `.lean-receipts/` or tracked there at HEAD,
    every copy must hash to `report_sha256`; when none is found `gate` says
    `· report_sha256 not checked (no redacted log beside the receipt)`. JSON `report_log` {checked, log, where}.
  - `gate --comparator` copied the lower-tier receipt into `.lean-receipts/` before a comparator refusal. Every
    check on both copies runs first now, so a refusal writes nothing.
  - `receipt slurm|systemd --append-readme` crashed on a non-UTF-8 `README.md` after writing the receipt. The README
    is decoded in the pre-write checks and refuses before anything is written; a symlinked `.lean-receipts/` refuses
    too.
  - `gate` and `comparator` wrote through a planted (even dangling) symlink at the destination in
    `.lean-receipts/`, outside the repository. A symlinked destination or `.lean-receipts` is refused.

- `comparator-receipt` no longer writes an `--evidence-dir` outside the checkout into a committed log. When the
  toolchain judge runs a partial unit (`--unit-size`, `--judges` above 1, a resumed run) it is handed a
  configuration copy under `<evidence>/comparator-stages/04-units/`, and that absolute path reached the receipt log
  unredacted when the evidence directory was not under the checkout, `HOME` or `TMPDIR`. It is now written as
  `<evidence>/...` in every log and receipt field; an evidence directory inside the checkout keeps `<checkout>/...`
  (review finding).
- `comparator-receipt` refuses two configurations whose receipt logs would share a name: `comparator_config_a.json`
  and `comparator/config_a.json` both map to `comparator_config_a.log`, so one configuration's log overwrote the
  other's. The refusal (exit 1, nothing written) names both files; `comparator config --out` refuses to create
  such a pair. Log names of non-colliding configurations are unchanged (review finding).
- `make cut-release` runs `make check` on the release tree before the release commit. It used to push
  `release: X.Y.Z` to `main` before the gate ran (inside `scripts/release.sh`), so a failing gate left
  `origin/main` at X.Y.Z with no tag and a re-run refused (`X.Y.Z is not above the current version`). A failing
  check now restores the version files and pushes nothing. A re-run also resumes, instead of refusing, when the
  version files already say X.Y.Z and `release: X.Y.Z` is anywhere in `HEAD`'s history (a fix pushed on top after a
  failed step 5 is tagged with the release), and refuses when origin already has the tag at another commit
  (review finding).
- `make record-doi` retries a failed push on transient errors (as `scripts/release.sh` does, same
  `LEAN_PKG_RELEASE_RETRIES` / `LEAN_PKG_RELEASE_RETRY_DELAY`) and, when it still fails, says the commit is local
  instead of a traceback. A re-run of `make record-doi` or `make cut-release` recognises the unpushed
  `citation: Zenodo version DOI for X.Y.Z` commit as its own and pushes it; both refused before (`main differs from
  origin/main`, `more than this release's own commits`). `make cut-release` retries its own push the same way
  (review finding).
- `promote` (and `public-safety`, the promote/publish checklist) scans the whole repository when the package is a
  subdirectory of it: the flip makes the whole GitHub repository public, but the scan covered only the package
  directory (its tree, the blobs under it, the commits touching it). The report says "scanned the repository root";
  `public-safety --package-only` keeps the old scope (what `release --fresh-repo` exports). Path-scoped pattern
  lines stay package-relative and never apply outside the package (review finding).
- public-safety scans a tracked symbolic link's target string (what git stores and publishes) in the tree and the
  history; links were skipped, and their blobs with them (review finding).
- public-safety scans every commit's author and committer identity, every annotated tag (name, tagger identity,
  message) and every branch and tag name. A host-like commit or tag address, one without a dotted domain
  (`user@hostname`) or under a local TLD (`lan`, `local`, `localdomain`, `localhost`, `home`, `internal`,
  `intranet`, `corp`, `private`, `test`, `invalid`, `example`), is an `identity-email` hit and is no longer declared
  by being used (it was published and allowed, in content too); an ordinary dotted address is still declared by use,
  and `consent[]` entries and `allow:` lines still declare any address. `release --fresh-repo` checks the identity
  its export commit will carry the same way (review finding).
- public-safety no longer reports a partial scan as clean: UTF-8/16/32 text with a byte-order mark and BOM-less
  UTF-16 are decoded (they read as binary and were skipped); lines over 20000 characters are scanned whole in
  overlapping chunks (they were cut); a binary file is scanned through its printable runs; files are read up to
  64 MB (was 5 MB, silently). Everything not scanned in full is listed in `skipped[]` {where, path, reason} and
  counted on the summary line; bytes past the cap are a blocking `unscanned` hit unless an
  `allow: GLOB: ^unscanned$` line covers the file (review finding).
- `promote --yes` first runs `git fetch --prune --tags origin` (branches, tags and GitHub's `refs/pull/*/head`
  into `refs/remotes/origin/pr/*`, 120 s timeout), so the scan covers what the remote holds, not only what was last
  fetched; a failed fetch is a `✗` and refuses. The dry run stays offline (review finding).
- `scripts/private-boundary-check.sh`: the re-check after the public-URL exceptions is case-insensitive like the
  first match (`grep -qiF`, and the exceptions are removed case-insensitively), so a case variant of a forbidden
  string no longer escapes; binary files and blobs are scanned through their printable runs instead of skipped;
  tracked symbolic links by their target; `--history` also scans author and committer identities and annotated tag
  objects. `PRIVATE_BOUNDARY_EXTRA=FILE` adds forbidden strings (for tests) (review finding).

### Changed

- `gate` refuses a receipt whose `mathlib_rev` is not prefix-equal to the `mathlib` rev in `lake-manifest.json`
  committed at its commit, like `lean_toolchain` (review finding). With no readable `mathlib` entry it is not
  checked and the recorded pin is kept, so `pins_kept` for `mathlib_pin` is now only that case.
- The verify log's own binding to the commit (review finding): a `commit:` line must equal `--commit` as before;
  without one, `receipt` refuses in the in-log fallback mode (no `--stdout`), and with `--stdout` prints
  `WARN the verify log has no commit: line; it is bound to the commit only through lake-manifest.json`. `--json`
  gains `log_commit_bound` (true, false; null when no commit resolved). The `--template` prints `commit:` already.
- `receipt systemd` publishes the host as a label, never its name: `job_id` is
  `systemd-user:<U>:<InvocationID>@h-<12 hex>`, the first 12 hex digits of the SHA-256 of the lower-cased host
  name, and the stored `unit.properties` carries the label in `Hostname=` (the receipt scan now blocks this host's
  name, review finding). Receipts already written keep their names.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23413293](https://doi.org/10.5281/zenodo.23413293)

## [0.7.43] - 2026-10-16

### Fixed

- public-safety: Lean's own build artifacts (`X.olean.private`, `X.ilean.private`, written by the module system) are
  no longer reported as references to a private file (`private-file-ref`); a docs line explaining open-file counts
  blocked a release. Any other `*.private` name still hits.

- `receipt slurm|systemd` counts Lean's real sorry warning. Lean 4 prints ``declaration uses `sorry` `` with
  backticks (every toolchain checked, v4.29 to v4.35-rc2); only `declaration uses 'sorry'` was matched, so a log
  with a real sorry warning was counted as 0, a declared `sorry-count: 0` could hide it (#108's check never fired on
  real output), and `receipt slurm --template` wrote `sorry-count: 0`. Both quote styles are counted now. An audit
  of the 39 receipt logs in the lab registry found sorry warnings only in comparator Challenge modules (which
  contain `sorry` by design), none in a lower-tier verify log (review finding).

### Added

- Skill `lean-shared-cluster-verification`: using a shared cluster (Slurm or similar) for Lean gates without
  wasted allocations. Iterate locally and submit only compiled trees; preflight the job's container and `PATH` on
  the login node with `type -P` (`command -v time` passes on the shell keyword); run a trial job through
  `receipt slurm --check`; read `Too many open files in system` as the job's own thread count, never a bad node;
  size CPUs and memory from a pilot's `sacct`. Listed in the router, `skills/README.md` and `docs/skills.md`
  (field report).
- Docs-only descendants, opt-in per package (field reports: a docs-only commit after the source commit forced a
  rerun of a long cluster job or a hand rebind). `lean-pkg update NAME --accept-docs-descendants --reason TEXT`
  sets the new optional record field `docs_descendants: true` (`--no-accept-docs-descendants` clears it; both
  need `--reason`, record an `updated` event and accept `--dry-run`); `--build-inputs GLOB,GLOB --reason TEXT`
  sets the optional `build_inputs` list. Under the opt-in, a receipt bound to commit X is also accepted at a
  descendant H of X where no build input under the package directory changed: `*.lean`, `lakefile.*`,
  `lake-manifest.json`, `lean-toolchain` at any depth, the `build_inputs` globs and the files the receipt's
  `checker` names; a symlink or gitlink change, `.gitattributes`, `.gitmodules`, `.lfsconfig` and `claims.yaml`
  are always refused (for a package in a subdirectory also those three files at the repository root and in
  every directory between it and the package), deletions and renames count, and `formalization.yaml` keeps its derived-keys rule. The gated commit
  stays X. `gate`, `gate --regate` and `comparator` print `✓ HEAD <c12> is a docs-only descendant of <c12>
  (N non-build file(s): ...; opted in)` and their events and `--json` (also `--check`, which writes nothing)
  carry `descendant_of` and `docs_paths` (up to 50). The same rule applies to `promote`, `audit`, `next`,
  `palomar prepare`, `update --path` and the claims-synced checks. Without the opt-in the behaviour is unchanged,
  except that a refusal the docs-only rule would have admitted ends with a hint naming the flag. `validate`
  checks both fields. See docs/receipts.md (Docs-only descendants).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23411091](https://doi.org/10.5281/zenodo.23411091)

## [0.7.42] - 2026-10-16

### Added

- `next --repo DIR` and `gate --check --repo DIR`: evaluate another checkout than the registered `local_path` for
  one call, as on a host whose registered path holds another branch. Never written to the registry; a stderr note
  names both paths and `--json` carries `repo_override` {path, registered_path, remote_match} (`null` without
  it). `DIR` must be a git work tree, and when it has remotes one must be the package's repository. A writing
  `gate` refuses `--repo` (exit 2): a gate binds the registered checkout, which `update --path` moves (field
  report).
- `receipt slurm --template` prints a minimal sbatch wrapper whose `.out`, empty `.err` and verify log carry every
  line `receipt slurm` reads: one `pre-manifest-sha256`, `post-manifest-sha256` and `verify-log-sha256` line in
  stdout, and `checker:`, `sorry-count:`, `axioms:` (derived from Lean's own output in the log), `lean-toolchain:`,
  `commit:`, `job-id:` and `generated-at:` in the log. The package's commands are placeholders; the work directory is
  node-local, `LEAN_NUM_THREADS` defaults to 3 and `/proc/sys/fs/file-nr` is logged. It needs no registry (field
  report).
- `receipt slurm --check` preflights the files of a finished job, such as a short trial run before the long one:
  the seal, stderr, log-line, field and public-safety checks of the receipt, every problem named at once, nothing
  written, exit 0/1, `--json` `problems[]`. sacct is not run (a saved `--sacct-file` with `--job` is checked);
  the commit-side checks run only with `--commit` (field report).
- `gate` and `gate --regate` record the receipt's `lean_toolchain` and `mathlib_rev` (first 10 characters) into
  the package's `lean_toolchain` and `mathlib_pin`, which are receipt-bound facts at the gated commit; after a
  deliberate pin bump `list` showed the old Mathlib pin. Each is adopted only when it agrees with the file
  committed at the receipt's commit (`lean-toolchain`; the mathlib rev in `lake-manifest.json`, prefix-equal);
  otherwise the recorded value is kept, the gate still passes, and `NAME: pins kept: receipt mathlib_rev X does
  not match lake-manifest.json at <c12> (Y)` is printed (JSON `pins_kept` {field: {receipt, manifest}}). When either changes, one line `NAME: pins updated from
  the receipt: ...` is printed and the `gate-passed`/`regated` event gains `pins` {field: {from, to}} (JSON
  `pins`, `{}` when unchanged). A recorded pin that is a prefix of the receipt's rev, or the reverse, is
  unchanged; an identical re-gate stays a no-op; `gate --check` reports `· pins: gate would record ...` and
  `· pins kept: ...` and writes nothing (field report).

### Changed

- `next` blocks a comparator step (`comparator-receipt`, `comparator config`) when the package's `lean-toolchain`
  at HEAD is below the comparator floor `audit` checks, instead of recommending it: `command` is null, the
  blocker kind is `below-toolchain-floor` {lean_toolchain, floor, deferred_command}, and the reason says to bump
  the toolchain, commit and re-gate first (field report).
- The `results-ledger:` line after `gate` and `promote --yes` names what to run and where the term is defined:
  `lean-pkg status NAME`, `lean-pkg claims ledger NAME`, and the new "Results ledger" section of
  docs/lifecycle.md (lean-pkg keeps no results ledger; it is the project's own record). `--json` adds the line
  as `rl_hint` (field report).
- The stderr `WARN` for a private-looking local path in a receipt field now says what happened to the receipt:
  `WARN: receipt field F contains a private local path (kept: ...; --refuse-private refuses such a receipt)`.
  It read `nothing copied (refused; reason below)` under `gate --check` (and after `comparator --replace`)
  although the receipt was accepted; `nothing copied: refused for another reason, below` is now printed only
  when the gate refused. The `--refuse-private` refusal text is unchanged (field report).

### Fixed

- `search`: a GitHub sub-search that returns exactly its result limit (`--limit 30`) is reported as truncated.
  The source block gains `capped[]` (the parts at the cap, e.g. `["code"]`) and `cap_limit`, distinct from
  `partial[]` (a part failed); the source is added to `truncated_sources` (`complete: false`), the `incomplete:`
  line reads `github: code search returned its 30-result limit`, `consulted:` says `github (capped, 30 hits)`,
  and the `--record` page marks it `capped: ...` in its Sources table with an `Incomplete: ... more matches may
  exist than the ones listed.` sentence. A query that returned 30 code hits read as complete (field report).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23408819](https://doi.org/10.5281/zenodo.23408819)

## [0.7.41] - 2026-10-15

### Fixed

- `search`: a registered package whose checkout lies inside another's (a `contrib/` package inside a companion's
  `lean/`) is no longer scanned as part of the outer package, so its declarations are attributed to it alone
  (field report).
- `claims sync` / `claims check`: a length error names the limit and the cause. A single-line citation of 301
  characters was refused with only `citation must be null or one line`; it now reads `... one line of at most 300
  characters; it is 301 characters, over the 300-character limit`, a multi-line value says `it spans N lines`, and
  `summary` and each `aliases[j]` (120) name theirs the same way (field report).

### Changed

- `claims sync` warns when the commit it pins is on no remote-tracking branch of the checkout (as of its last
  fetch): readers of the registered repository cannot inspect such claims. The sync still succeeds; `--json`
  carries `pin_published` (`true`, `false`, `null` without a remote) (field report).
- `search --record` prints the MkDocs nav note only when the repository has an `mkdocs.yml`; otherwise `nav_hint`
  is `null` and `record.mkdocs` is `false` (field report: noise in plain Lean repositories).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: none. Zenodo's GitHub archive was unreachable when this release was published, so no
version record exists; cite 0.7.42 or the concept DOI [10.5281/zenodo.23272098](https://doi.org/10.5281/zenodo.23272098).

## [0.7.40] - 2026-10-15

### Added

- `lease` is accepted as the name of `claim` (the registry's own word for what it takes); it is a write in every
  respect, including committed mode and the registry lock (field report).
- A lease that lapses mid-workflow is said out loud (field report: a release lease lapsed overnight while its
  holder waited on a decision). `status NAME` ends with `lease: EXPIRED <age> ago (<agent>); renew no longer works:
  lean-pkg claim ...` for a lapsed lease and `lease: expires in <time> (<agent>); keep it: lean-pkg renew ...` for
  one lapsing within 2 h, and `--json` carries `lease_state` {state, seconds_left}. Any other command naming that
  package prints the same as a stderr `WARN`, so the holder hears it on its next command.

### Changed

- A write that runs without committed mode no longer leaves a shared registry clone dirty in silence. When the
  registry sits in a git work tree and the write leaves its file or event log modified, stderr ends with
  `NOT COMMITTED: <files> modified in <clone>; every --commit/--push write to this registry refuses until they are
  committed: git -C <clone> commit ...`, and a `--json` payload gains `registry_uncommitted`. A regate run that
  way was reported done while other agents' committed writes were refused for about four hours (field report).
- The committed-mode refusal over modified registry files names who left them: the last uncommitted event
  (event, package, agent, time, `+N more`), the package's lease holder and whether that lease is live, and the
  `git commit` that clears it, so the blocked writer need not read `git diff`.

### Fixed

- `search` no longer reports a complete search when GitHub answered only in part. When `gh search code` failed
  (for example HTTP 403, rate limit) and `gh search repos` worked, the github source read `ok, 0 hits` with the
  failure only in its note, and `--json` said `complete: true` (field report). Every source block now carries
  `partial[]` (`["code"]` or `["repository"]` for GitHub, empty otherwise); a source with a failed part is added to
  `truncated_sources`, makes `complete` false, and is named on the `incomplete:` line
  (`incomplete: github: code search failed; a zero here is not a negative result`); the `consulted:` line reads
  `github (partial, 0 hits)`. A `--record` page shows `partial: code search failed` in its Sources table and no
  longer writes `Not found in github at live` as if the code had been searched.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23406651](https://doi.org/10.5281/zenodo.23406651)

## [0.7.39] - 2026-10-15

### Changed

- docs/prior-art.md: "While the paper is being written", a synthetic co-development example of the discover, pin,
  check-compatibility, decide (require, reuse, vendor, contribute, build) and look-again cycle, including how to
  tell a bounded search from an absence (`complete`, `truncated_sources`, `not_searched_reasons`) and that Loogle
  and LeanSearch answer for Mathlib master, not your pin. The co-development page links to it.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23401596](https://doi.org/10.5281/zenodo.23401596)

## [0.7.38] - 2026-10-15

### Added

- `search` says why a package was not searched by declaration: `--json` `coverage.not_searched_reasons[]`
  {package, code, remedy} with codes `no-path` (no local_path recorded), `no-checkout` (not a directory on this
  host), `no-git-head` (no commit, or not a git work tree) and `scan-error` (git grep failed). The remedy names only
  read-only commands keyed by the package name, never a host path or git's stderr. `coverage.not_searched` stays
  the list of names. The coverage line groups by reason when several apply (`no checkout: a, b; no git HEAD: c`),
  and a `--record` page counts them (`no checkout: 1, no git HEAD: 1`) (field report).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23400407](https://doi.org/10.5281/zenodo.23400407)

## [0.7.37] - 2026-10-15

### Fixed

- `search` no longer misses a declaration silently when a package has many candidate files. The declaration search
  read only the first 200 files that matched the `git grep` prefilter and reported nothing about the rest, so a
  theorem in the 201st file returned zero hits (field report with a synthetic 201-file fixture). Files are now read
  through one `git cat-file --batch`, the cap is 2000, and a package past it is reported: `--json`
  `coverage.declaration_files_capped[]` {package, candidates, inspected}, `truncated_sources: ["registry"]` and
  `complete: false`; the coverage line names the package and the `incomplete:` line says a file cap was hit. The
  10-per-package display cap is separate and still counted as `declaration_hits_omitted`.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23398491](https://doi.org/10.5281/zenodo.23398491)

## [0.7.36] - 2026-10-15

### Added

- Documentation: [Develop a paper with its Lean companion](docs/paper-companion.md), a workflow guide with a synthetic
  example for checking intermediate results while a paper evolves (layout, exact statements, checkpoints, reuse
  and its limits, a coverage map, the final deliverable). It separates what Lean, `lean-pkg` and an external
  manuscript workflow each do, and states that a certificate covers its exact formal statements, not the paper.
  README gains the use case; the llms rules gain one sentence.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23396606](https://doi.org/10.5281/zenodo.23396606)

## [0.7.35] - 2026-10-15

### Changed

- `receipt slurm|systemd` refuses a verify log whose `axioms:` line omits an axiom that the log's own
  `#print axioms` output reports, the axioms counterpart of #108's `sorry-count:` check. Axiom names are compared
  without a universe suffix (`Classical.choice.{u}` is `Classical.choice`), also in the list derived when the log
  has no `axioms:` line. None of the 20 committed verify logs checked is affected, including five whose output
  prints universe suffixes.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23393868](https://doi.org/10.5281/zenodo.23393868)

## [0.7.34] - 2026-10-15

### Changed

- `receipt slurm|systemd` refuses a verify log whose `sorry-count:` line declares fewer than the log's own
  `declaration uses 'sorry'` warnings. A script's count often comes from an inventory of declarations; one that
  lists only `theorem`s printed `sorry-count: 0` while Lean warned about a `sorry` in a `lemma` (field report, G7).
  None of the 20 committed verify logs checked (every registered package with a checkout on the maintainer's
  host) has such a warning.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23384932](https://doi.org/10.5281/zenodo.23384932)

## [0.7.33] - 2026-10-15

### Added

- `search` says when it was incomplete: `--json` gains `complete` (no source skipped), `searched` and
  `skipped_sources[]`, and the text output adds `incomplete: searched N of M sources; skipped: ...; a zero here is
  not a negative result` before the `consulted:` line. A LeanSearch outage left only `skipped: HTTP 500` on one
  source line, and a copied record said "0 hits" (field report).

### Fixed

- `receipt --append-readme --readme-heading` no longer prints the commit twice: a heading that already starts with
  this receipt's commit (`4095a30: ...`, backquoted or not) has that prefix dropped, since the entry adds it
  itself; any other SHA is kept (field report).
- `gate` and `gate --check` on an `active` package now print the exact move it needs first,
  `lean-pkg update NAME --status gate-pending --reason "receipt for <c12> ready"`. The receipt's `next:` hint led
  to a refusal that did not say how to reach gate-pending (field report).
- `test_make_test_removes_its_bytecode_cache_on_sigint` gives its `make test` child the default SIGINT disposition.
  A suite started as `( make test ) &` from a script, or under nohup, runs with SIGINT ignored, which is inherited;
  the recipe's trap then never ran and the test failed with "make test still running 15 s after SIGINT". A new
  test runs it in a pytest that starts with SIGINT ignored.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23384445](https://doi.org/10.5281/zenodo.23384445)

## [0.7.32] - 2026-10-14

### Added

- `receipt slurm|systemd --json` gains `sealed[]` {role, path, sha256, receipt_key}: every file the run wrote and
  bound (the receipt, the redacted log with `report_sha256`, and for `receipt systemd` the normalised unit
  properties with `unit_properties_sha256`), empty on a refusal. A caller takes a sidecar from it instead of
  guessing by file name; `receipt systemd`'s `properties` key stays the raw, unsealed capture, whose bytes can
  differ in order from the sealed file (field report, SWE round 3). Additive; no receipt bytes change.

### Changed

- Documented the layout for workflows that edit progress files after every replay: those files live outside
  the package directory (docs/receipts.md, "Progress files live outside the package directory"), and the
  evidence-only rule is unchanged. The opt-in `evidence_paths` of #101 is reverted before any release.
- CI's private-boundary job also runs `scripts/private-boundary-check.sh --history` on a full-depth checkout, so
  a private string in a commit message (or in any blob of the pull request's history) fails the pull request
  before it reaches `main`. The tree scan alone let one through in #94's commit messages.
- `search`: a LeanSearch answer of 500-599 is now skipped as `HTTP <status>: service error at leansearch.net;
  retry later`, and a 404 as `HTTP 404: API moved (https://leansearch.net/search not found); the leansearch adapter
  needs updating`, instead of a bare `HTTP <status>` (other statuses unchanged). On 2026-10-14 every query
  answered 500 while the request matched the site's own page, and the bare status left open which side was at fault.
- docs/how-to.md: `public-safety` blocks a lease holder's name recorded in a progress file, because it derives
  that name from the package record. Write the lease line in its redacted form, pointing at
  `lean-pkg status NAME --fields lease`, or keep the name and exempt that one file with a scoped
  `allow: PROGRESS.md: ^<agent-name>$` (#99; behaviour unchanged).

### Fixed

- `claims check FILE`, with FILE an existing `.yaml`, `.yml` or `.json` file, is a usage error naming
  `claims check --file FILE` (exit 2) instead of "unknown package" (field report from a lean-dev wave).
- `receipt slurm --sacct-file` saved with other columns was read by position, so `JobID|JobName|Partition|...`
  reported the job name as the State and the partition as the ExitCode ("not a clean finish"). A header line
  naming `JobID`, `State` and `ExitCode` (sacct `-P` without `-n`) now locates them in any order; without one, a
  second column that is not a Slurm state or a third that is not `N:N` is refused as `unexpected sacct format`.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23383032](https://doi.org/10.5281/zenodo.23383032)

## [0.7.31] - 2026-10-14

### Added

- `register NAME --path DIR --like OTHER`: register a sibling of a registered package (for example `lean-r2/`
  beside `lean/` in one manuscript repository). `repo`, `owner_project`, `upstream_target` and `automation` come
  from OTHER unless given; visibility is never copied. Copying OTHER's repo is refused when the two checkouts are
  in different git repositories. A note says whether the toolchain and Mathlib pin match OTHER's; `--json` gains
  `like`, the `registered` event `like`. `--repo` is now optional when `--like` gives it.
- `register` warns when the checkout is inside, or contains, another registered package's directory, and when its
  `lake-manifest.json` has a path dependency outside the package directory.

### Changed

- `gate` (and every reader that re-validates a receipt: `gate --check`, `--regate`, `claims ledger`, `next`,
  `palomar`, `index check`) refuses a receipt whose `lake-manifest.json` at the bound commit has a `path` package
  whose `dir` is absolute or leaves the package directory: the receipt does not bind those sources, so an edit to
  them was never drift. Judged on the bound commit's tree, a path dependency is also refused when it is, or passes
  through, a symlink, a submodule or an untracked directory (`.lake/`), or contains a submodule; and any committed
  symlink whose target is absolute or has a `..` component is refused, path dependency or not. A tracked
  directory inside the package is accepted; a manifest that is not valid JSON is refused. `gate --check` adds
  `✓ lake-manifest.json` and `✓ no symlink` lines. The tree read fails closed: a `git ls-tree` or `git cat-file`
  error, or a record it cannot parse, is a refusal, never an empty or a safe tree.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23379162](https://doi.org/10.5281/zenodo.23379162)

## [0.7.30] - 2026-10-14

### Changed

- `promote` now refuses unless `claims ledger NAME` at the promote stage is `ready`: every `reusable` claim
  verified, each theorem and lemma among the declarations the comparator PASS compared. A comparator PASS
  alone no longer suffices, and a package without claims, or with none `reusable`, is refused as `unknown`.
  `publish --dry-run` shows the same checklist line; `promote --json` and `publish --json` gain
  `claims_ledger`. `next` recommends `publish --dry-run` only once the ledger is ready, otherwise the
  ledger's first fix (`comparator config --from-claims` for an uncompared claim, else `claims ledger NAME`);
  its `--json` state gains `claims_ledger` (#80).

### Added

- `lean-pkg receipt systemd` (alias `receipt local`): the lower-tier receipt from a finished systemd `--user`
  service unit, for light checks run locally in a capped unit, through the same seal, field, redaction and
  public-safety pipeline as `receipt slurm` (now shared, `receipt.produce`). `--run -- CMD ...` starts the command
  bounded (`systemd-run --user`, `MemoryMax=8G`, `MemorySwapMax=0`, `CPUQuota=400%`, `RuntimeMaxSec=4h` by default,
  `OOMPolicy=stop`) with `RemainAfterExit=yes`, so the finished unit is not collected before its properties are
  captured to `<log>.unit.properties`; then it is stopped and reset. The checkout must be at `--commit` with no
  tracked change under the package directory before and after the run (edits elsewhere in a manuscript
  repository that holds the package in a subdirectory do not count). `--properties-file` reads saved `systemctl --user show` output plus a
  `Hostname=` line; without either the unit is read live. Refused: a unit not found, not terminal, `Result` other
  than `success` (`oom-kill`, `timeout`, `signal` named), a non-zero exit or a signal, restarts, a stdout
  `invocation-id` from another run, and every `receipt slurm` refusal. `job_id` is
  `systemd-user:<unit>:<InvocationID>@<host>`, the host taken from the running system; the normalised properties
  are written beside the receipt (additive key `unit_properties_sha256`). The checkout's path, `$HOME` and
  `/run/user/N/` are redacted to `<repo>`, `<HOME>`, `/run/user/<UID>/` before the scan.
- `lean-pkg claims ledger NAME [--stage gate|promote]`: per claim, named diagnostics from the recorded
  receipts, re-checked with `gate`'s and `promote`'s validators; a claim is `verified` only with zero
  diagnostics. The package status fails closed (`unknown` with no claims or no `reusable` claim; `failed` when
  evidence contradicts a required claim; `ready` only when every required claim is verified), and at the
  promote stage a lower-tier receipt alone never closes a reusable theorem or lemma. Read-only; exit 0 only
  when ready; `--json` carries a `ledger_digest`. Codes in `docs/lifecycle.md` (#80).

### Changed

- `llms.txt` is now generated whole: `make docs` writes it and `docs/llms.txt` from `LLMS_TXT_HEADER` plus the
  `lean-pkg llms` text, and re-renders `index/README.md`. The test now requires byte equality (it only checked
  that the contract appeared somewhere in the file), and CI runs `make docs` then `git diff --exit-code`, so a
  stale generated file fails CI (#81).
- The pull request template asks for evidence as well as checkboxes: the commands that actually ran with
  their observed results (a gate that could not run is listed as not run, with the reason), regression
  evidence for a fix, and a new "Remaining proof gaps" section. An unknown effect is written "unknown", never
  "no" or "none". `CONTRIBUTING.md` points to them (#83).

### Fixed

- Committed mode no longer overwrites a `--json` payload's own `"commit"`. `gate`, `comparator` and other
  writes report the certified source commit under `"commit"`; committed mode replaced it with the registry
  transaction object, so the key was a string without the mode and an object with it, and the source commit
  was lost. The transaction is now always under `"registry_commit"` {sha, pushed, error, push_error,
  push_attempts, rebased}, and still also under `"commit"` when the payload has no `"commit"` of its own.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23377856](https://doi.org/10.5281/zenodo.23377856)

## [0.7.29] - 2026-10-14

### Changed

- `make cut-release` now waits up to 300 s (was 900 s) for the Zenodo version DOI by default; Zenodo listed
  0.7.28 about a minute after its tag. `DOI_WAIT=seconds` still overrides it, and a timeout still only warns, so
  `make record-doi VERSION=X.Y.Z` can finish step 7 later.

### Fixed

- `receipt slurm` no longer reads a log without a `sorry-count:` line as `sorry_count 0` unless the log shows
  the build finished (Lake's `Build completed successfully`, or a line starting `lake build: ok`). A cut-short or
  wrong log is now refused as "the sorry count is unknown", matching how a log without axioms output is refused
  (#82).
- `receipt slurm` re-run for the same job and log is no longer refused once the clock has moved: when the log
  has no `generated-at:` line and the receipt already written matches in every other field, its `generated_at` is
  kept and the re-run is an identical no-op. Any other difference is still refused. This also fixes the flaky
  `test_append_readme_writes_the_block_once`, whose clock is now frozen (#85).

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23366554](https://doi.org/10.5281/zenodo.23366554)

## [0.7.28] - 2026-10-13

### Added

- `make cut-release VERSION=X.Y.Z` (`scripts/cut-release.py`) runs the RELEASING.md checklist end to end:
  citation bump, public-safety scan, version files, release commit and push, `scripts/release.sh`, and the
  Zenodo version DOI. Re-running after a failure resumes where it stopped. `make record-doi VERSION=X.Y.Z`
  (`scripts/record-doi.py`) is step 7 on its own: it follows every page of Zenodo's search (anonymous pages
  hold 25 records, oldest first), checks the record's concept and tag, and is idempotent.
- `LEAN_PKG_COMMIT_TRAILER` adds `Key: value` trailers (one per line, e.g. `Co-Authored-By: Name <email>`) to every
  commit `make cut-release` and `make record-doi` make; a malformed value is refused before anything is written.

### Changed

- RELEASING.md gains checklist step 2a: point the BibTeX examples in `README.md` and `docs/index.md` at the new
  version in their own `citation:` commit before the release commit. Existing step numbers are unchanged.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23344843](https://doi.org/10.5281/zenodo.23344843)

## [0.7.27] - 2026-10-12

### Changed

- `scripts/release.sh` (`make release`) retries the tag push, the GitHub release and the Pages build request on
  transient network errors (10, 20, 40 s; tune with `LEAN_PKG_RELEASE_RETRIES` and `LEAN_PKG_RELEASE_RETRY_DELAY`)
  and stops at once on a permanent refusal. Re-running after a partial failure completes only the missing steps;
  a tag at another commit or object is refused, never forced. It now requests the Pages build itself. (#73)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23320040](https://doi.org/10.5281/zenodo.23320040)

## [0.7.26] - 2026-10-11

### Changed

- `receipt slurm --stdout` now persists the seal evidence it checked: the receipt gains the additive keys
  `job_stdout_sha256` (the job stdout file) and `manifest_sha256` (`lake-manifest.json` at the commit), which
  `validate_receipt` and `gate` ignore, the README block a `- Seal:` line naming both, and `--json` a `seal` object
  (null without `--stdout`). `--log` help now says `--checker TEXT` is required when the log has no `checker:` line;
  `--redact` help says the built-in prefixes become `<USER>` while `--redact` matches become `<redacted>`. (#72)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23290206](https://doi.org/10.5281/zenodo.23290206)

## [0.7.25] - 2026-10-11

### Fixed

- Committed mode now treats a push that lost a concurrent ref update as a rejection (origin moved on), so #68's
  fetch, rebase and retry run: GitHub's `! [remote rejected] main -> main (cannot lock ref 'refs/heads/main': is at
  <sha> but expected <sha>)` and the `--force-with-lease` form `(stale info)` were classified as permanent failures.
  A `[remote rejected]` with any other reason (`pre-receive hook declined`, a protected branch) stays permanent and is
  not retried. (#71)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23289837](https://doi.org/10.5281/zenodo.23289837)

## [0.7.24] - 2026-10-10

### Added

- `gate NAME --receipt R --regate [--reason TEXT]` re-gates a `verified-private` (or `public`) package in one step:
  the same validations as `gate`, status unchanged, one `regated` event {previous_commit, commit, job_id, reason},
  so one registry commit and push in committed mode instead of two. The receipt's commit must descend from the gated
  commit unless `--reason` is given (recorded as `non_descendant: true`); `gate --check --regate` validates without
  writing. Plain `gate` on a `verified-private` package is still refused, and the message now names `--regate`;
  `next` recommends `gate --regate` where it recommended `update --status gate-pending` for a moved HEAD. (#67)
- Committed mode retries a failed push (#68): a transient transport error (connection reset,
  `kex_exchange_identification`, `Could not read from remote`, timeout, HTTP 5xx) is retried with backoff (env
  `LEAN_PKG_PUSH_RETRIES`, default 4; `LEAN_PKG_PUSH_RETRY_DELAY`, default 5 s, doubled: 5/10/20/40 s), each retry
  reported on stderr. The `--json` `commit` object gains `push_attempts` and `rebased`.

### Changed

- A push rejected as non-fast-forward fetches and rebases the registry commit as often as the retry budget allows
  (was: once). A conflict only in the event log is resolved as a union of both sides' lines in timestamp order (was:
  a `merge=union` attribute, which kept file order); any other conflict aborts the rebase, leaves the clone as it
  was (commit kept, ahead) and the #54 failure line ends `; rebase conflict in <files>`. (#68)
- `update --append-note` prints only the line it appended (`appended note: YYYY-MM-DD (A): TEXT`) instead of the
  whole notes field before and after, which buried the result for a long-lived package. The `updated` event's
  `notes_from` and the `--json` `changes.notes` {from, to} are unchanged. (#69)
- `lean-pkg receipt slurm --job J --log PATH --commit SHA [--repo DIR] [--redact REGEX ...] [--sacct-file F]
  [--out PATH] [--json]` writes a lower-tier receipt from a finished Slurm job: the main `sacct` line must say
  `COMPLETED` and `0:0` (`--sacct-file` reads saved output on a host without Slurm); the verify log's single
  `verify-log-sha256:` line must equal the SHA-256 of the log without that line; `lean-toolchain:`, `mathlib-rev:`,
  `checker:`, `sorry-count:` (0) and `axioms:` are read from it, and the toolchain must match the one committed at
  `--commit`; per-job `/tmp/<name>-job<N>/` and `/scratch/<user>/` prefixes and `--redact` matches are redacted, and
  any public-safety blocking hit left in the log or the receipt refuses. Every refusal writes nothing. On success it
  writes `<c12>-<job>.verify.log` (redacted; its SHA-256 is `report_sha256`) and `<c12>-<job>.json` under
  `.lean-receipts/`, and prints the README line and the `gate NAME --check --receipt` command. docs/receipts.md
  documents the log lines and the seal rule. (#70)
- `lean-pkg receipt slurm --job J --log PATH --commit SHA [--stdout PATH] [--stderr PATH] [--checker TEXT]
  [--repo DIR] [--redact REGEX ...] [--sacct-file F] [--out PATH] [--append-readme] [--json]` writes a lower-tier
  receipt from a finished Slurm job. The main `sacct` line must say `COMPLETED` and `0:0` (`--sacct-file` reads saved
  output on a host without Slurm). The job stdout's `verify-log-sha256` must equal the raw verify log's SHA-256, its
  `pre-manifest-sha256` and `post-manifest-sha256` must be equal and equal the committed `lake-manifest.json`'s, and
  `--stderr` must be empty; without `--stdout` the log must carry its own `verify-log-sha256:` seal. `lean_toolchain`
  and `mathlib_rev` come from the commit (a disagreeing log line refuses); sorry count and axioms come from the log
  (`sorry-count:`/`axioms:` lines, else its `declaration uses 'sorry'` warnings and `#print axioms` output); `checker`
  from `--checker` or the log. The per-job `/tmp/<account>-job` prefix, `/scratch/<user>/` and `--redact` matches
  are redacted, and any public-safety blocking hit left in the log, the receipt or the README entry refuses. Every
  refusal writes nothing. On success it writes `<c12>-slurm-<J>.json` (`job_id` `slurm-<J>`) and
  `<c12>-slurm-<J>-verify.redacted.log` (its SHA-256 is `report_sha256`) under `.lean-receipts/`, prints the README
  entry (`--append-readme` appends it to `.lean-receipts/README.md`: state, exit, elapsed, TotalCPU, log name and
  hashes) and the `gate NAME --check --receipt` command. docs/receipts.md documents the seals and the log lines.
  (#70)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23289295](https://doi.org/10.5281/zenodo.23289295)

## [0.7.23] - 2026-10-10

### Added

- `comparator-receipt --unit-size K` (env `LEAN_PKG_COMPARATOR_UNIT_SIZE`) decouples the judge unit size from
  concurrency: each configuration's pending slots are cut into contiguous units of at most K names, and `--judges N`
  is then only how many units run at once (default 1). `--unit-size 1 --judges 1` judges each name alone, one at a
  time, with every accepted slot cached at once, so a capped run resumes exactly where it stopped. Without
  `--unit-size`, `--judges N` behaves as before. The runner prints `units: M of at most K names, width N` once; the
  receipt and `stages.json` gain the additive `unit_size` (`K` or `null`) beside `judges`; a value that is not a
  positive integer exits 2, and the legacy mechanism refuses the flag. (#60)
### Changed

- `comparator-receipt --estimate`: the sample has a floor of `max(5, ceil(0.25 * slots))` (capped at the slot
  count), which is also the default `--sample`; a smaller `--sample` or `--slots` subset is refused (exit 2,
  nothing written) unless `--force-sample`. `recommended_cap` uses the per-slot max model
  (`per_slot_max_seconds * slots / judges`) instead of the mean when the sample covers less than half the slots.
  The estimate JSON is schema `comparator-estimate/2`. A field run had capped a 16-slot configuration at 3000 s from
  a sample of 2; the unsampled 14-name unit outran it. (#59)

### Added

- `comparator-receipt --estimate` samples the heaviest slots first: each slot is weighted by the size of the olean
  of the module its name's longest namespace prefix names (0 when none matches), and the `max(1, ceil(0.2 * K))`
  heaviest are taken before the namespace-stratified fill. The estimate gains `estimated_seconds_max`, `cap_model`
  (`max` or `mean`), `sample_floor`, `sample_forced`, per-row `weight_bytes`, `weight_module` and `heavy`, and a
  `whole_unit_warning` (a unit's export pays its names' union closure, which isolated timings do not bound; per-name
  units with `--unit-size` keep the model valid and the run resumable). `--force-sample` accepts a sample below the
  floor with a warning. docs/comparator.md documents the floor, the weight rule, both cap models and the warning.
  (#59)
### Fixed

- `comparator --check` (and `gate --comparator`, `index add`/`index check`, which share the validation) no longer
  refuses a receipt the runner wrote from a configuration that omits `definition_names`: a list field missing (or
  `null`) in the committed configuration now means `[]` on the check side too, as the runner records it, so the
  `receipt definition_names differs from the config ([] vs None)` refusal is gone; a real difference (`["a"]` vs
  `[]`) is still refused. The runner names a missing `permitted_axioms` up front instead of calling it "not a list
  of strings". docs/comparator.md says which list fields may be omitted. (#61)
- `comparator-receipt` (toolchain mechanism) on a project without Mathlib no longer fails as
  `lake comparator did not accept the solution`: the vendored `verify-comparator.sh` ran `lake exe cache get`,
  which fails there with `unknown executable cache`. The runner refuses such a project before any stage with
  `the toolchain mechanism needs a Mathlib dependency (lake-manifest.json has none); add it and run lake update`.
  `COMPARATOR_RECEIPT_NO_MATHLIB=1` declares a project that knowingly has none; the runner then sets
  `VERIFY_COMPARATOR_SKIP_CACHE_GET=1`, which the vendored script reads to skip its `lake exe cache get` (a local
  change recorded in its header; the slot cache, bound to the script's sha256, is invalidated once). A judge that
  exits non-zero before judging (no `== lake comparator` line from `verify-comparator.sh`, no rejection line from
  `comparator-check.sh`) is now reported as `the judge tool failed before judging`, with its last log line, not
  as a rejected proof. docs/getting-started.md and docs/comparator.md state the Mathlib requirement. (#62)

### Changed

- Documentation gaps from the first stranger test: getting-started step 1 builds a minimal Challenge/Solution
  project from scratch (module names are file paths; every `comparator.json` field, `definition_names: []`
  included); README marks the `register` placeholders and says `NAME` is the registered name and `--path` is
  `$HOME`-relative; the pre-public install text is gone; the worked example pins Lean v4.35.0-rc2 with
  toolchain-mechanism output; "about 25 minutes" is the legacy mechanism only; README lists `bwrap` and
  explains `COMPARATOR_RECEIPT_ALLOWED_HOSTS`; commands and expected output are separate blocks.
  Decision 0006 records the G1 transcript. (#63, #64, #65, #66)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23287981](https://doi.org/10.5281/zenodo.23287981)

## [0.7.22] - 2026-10-10

### Added

- `comparator-receipt` records whether the `build` stage compiled anything: the receipt and `stages.json` gain the
  additive `build_provenance` `{compiled, modules_built, prebuilt, source}`, decided from the mtimes of the package's
  own oleans under `.lake/build/lib/` before and after the stage, with Lake's `[i/n] Built` lines as a second witness.
  `COMPARATOR_RECEIPT_BUILD_SOURCE` declares where a prebuilt `.lake/build` came from (free text, redacted). A reused
  or `--from-prepared` setup carries the provenance recorded when it was built. The runner, `comparator --check` and
  `gate --check` print `build: compiled N module(s)` or `build: prebuilt (source: ...)`; validators ignore the key.
  docs/comparator.md says when oleans built elsewhere are acceptable. (#56)
- `COMPARATOR_RECEIPT_LEAN_THREADS=N` sets `LEAN_NUM_THREADS=N` for the `lake`/`lean` children of the setup and
  comparator stages (the children `COMPARATOR_RECEIPT_STACK` reaches), so a memory-tight host serializes the build
  without a `COMPARATOR_LAKE` wrapper (Lake 5 has no `-j`; the wrapper stays documented as the fallback). The runner
  prints the applied value and the receipt records it as the additive `lean_threads` (`N` or `null`); a value that
  is not a positive integer exits 2. (#55)
- `report --body TEXT` and `report --from-log FILE[:N]`: describe friction that has no cheap re-run (a failure
  inside a long stage, a timeout). `--body` is free text and `--from-log` the last N lines (default 200) of a stage
  log under a `Log excerpt` line; both go first in What happened, before the `--command` re-run when that is given
  too, and are redacted like every captured text (the final redaction gate still refuses on a residual blocking
  hit). At least one of `--command`, `--body`, `--from-log` is now required (exit 2 otherwise); a missing log file
  or an `N` that is not a positive integer is a usage error. `--json` gains `log_excerpt` {path, lines} with
  `--from-log`. (#55)

### Changed

- `report` now requires one of `--command`, `--body` or `--from-log` (exit 2 otherwise); a bare
  `lean-pkg report` used to produce a host-only report. (#55)
### Changed

- The `make test` SIGINT test is deterministic: readiness wait, own `waitpid(WNOHANG)` deadline loop. (#57)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23283225](https://doi.org/10.5281/zenodo.23283225)

## [0.7.21] - 2026-10-10

### Added

- `gate` and `gate --check` run the `claims/1` schema check (the one `claims sync` applies) on the package's claims
  file as tracked at the receipt's commit, never the working tree, then `claims sync`'s declaration check with each
  claim's file read at that same commit. `--check` prints
  `✓ claims.yaml at <commit12>: schema ok (N claims), declarations found`, or
  `✗ claims.yaml at <commit12>: <first problem> (+k more)` (schema errors first, then missing declarations in
  `claims sync`'s wording) and exits 1 (`ok: false`); `gate` refuses either kind before anything is copied or
  recorded, naming `claims check --file` (schema) or `claims sync` (declarations) and the new-receipt cost of the fix.
  No claims file tracked there: a neutral `·` line. Both add `claims_schema` {checked, commit, file, ok, claims,
  errors[], declarations {checked, ok, problems[]}} to `--json`. (#58)
- `claims check --file PATH`: schema-check a working-copy claims file before it is committed (schema only: no
  checkout, so declarations are not looked up), without the registry
  (`NAME` optional; if given it must be the file's `package`); exit 0/1, `--json` `{ok, path, claims, errors[]}`.
  (#58)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23282388](https://doi.org/10.5281/zenodo.23282388)

## [0.7.20] - 2026-10-10

### Added

- DOI-only track for `record doi-minted`: an optional record field `track: palomar | doi-only` (absent = palomar),
  set by `update NAME --track doi-only|palomar --reason R`, checked by `validate`, readable with `--fields track`.
  On the DOI-only track (the record's `track`, or `record ... doi-minted --track doi-only`) a public package records
  its DOI without a `palomar-*` event (rule doi-in-palomar skipped); a non-public package is refused (rule
  doi-only-public). The `doi-minted` event carries `track`. (#51)
- `comparator-receipt` stack limit: before the `build` stage (and before the comparator stage of a reused or
  `--from-prepared` setup) it warns when the soft `RLIMIT_STACK` is 8 MiB or less, since a `lean` SIGSEGV (exit 139)
  in `lean::sharecommon_quick_fn::visit_ctor` on a large module is a stack overflow;
  `COMPARATOR_RECEIPT_STACK=unlimited|<kB>` raises the soft limit for the `lake`/`lean` children of the setup and
  comparator stages (never above the hard limit; an invalid value exits 2), and the receipt records it as the additive
  `stack_limit` `{soft_kb, hard_kb, source}`. docs/comparator.md adds serial builds on a memory-tight host
  (`COMPARATOR_LAKE` wrapper exporting `LEAN_NUM_THREADS=1`, since Lake 5 has no `-j`; `systemd-run --user --scope -p MemoryMax=...`; the estimate reports time, not
  memory) and hosts without user namespaces (EL7 compute nodes cannot run the toolchain mechanism; run
  `comparator host-check` on every candidate host first). (#52)
- `search`: a registered package without synced claims is searched by declaration in its checkout at HEAD
  (`git grep` over tracked `*.lean` files: declaration names and their docstrings), reported with
  `hit_kind: declaration (unclaimed)`, trust `none`, `PATH:LINE`, after the claim hits; a package without a git
  checkout is not searched. A `registry coverage:` line before `consulted:` names the blind sources, so a zero is
  not read as absence; `--json` gains `coverage` and the `--record` page states the counts. (#49)
- `search --record`: after writing the prior-art page it prints `note: <path> is new; a strict MkDocs build needs a
  nav entry (or a literate-nav glob) for it`, and `--json` carries `nav_hint` (`path`, `reason`). The consumer's
  `mkdocs.yml` is not edited. (#50)
- `audit`: informational finding `claims-coverage-none` (level `info`, also under `--promotion`; never fixed by
  `--fix`), one per package whose claims coverage is not `synced` (`none`, `empty` or `unsynced`), skipping
  `archived` and `upstreamed`, with detail ``claims: COVERAGE (search falls back to declarations; run `lean-pkg
  claims sync NAME`)``; `--json` findings carry `coverage`.
### Changed

- **Breaking for scripts that relied on silent replacement:** `update NAME --notes TEXT` over non-empty notes is
  refused (exit 2, nothing written, no event; `--json` `{"ok": false, "usage": true, ...}`) unless the new
  `--replace-notes` is given. The refusal names `--append-note` (the ordinary way to add a line),
  `--replace-notes`, and that replaced text stays recoverable from history (the `updated` event's `notes_from`).
  A replacement made with `--replace-notes` records `notes_replaced: true` on its `updated` event. `--notes` on
  empty notes, the same text again, and `--append-note` are unchanged; clearing non-empty notes (`--notes ''`)
  is a replacement and needs the flag. (#53)

### Fixed

- Committed mode: a push that fails now prints `push failed: origin/<branch>: <git error>` (git's reason, not the
  `To <url>` header a rejection prints first, and without a remote's trailing padding) and a second line naming
  the kept commit and `git push origin <branch>`; exit 1 as before; `--json` `commit` gains `push_error` (null
  unless the push failed). A `--push` write now refuses while the clone is ahead of `origin/<branch>` (an
  unpushed commit, from a failed push or a commit made outside lean-pkg), naming the commit and the `git push`;
  before, the unpushed commit was carried silently into the next write. `--commit` without push is unchanged.
  (#54)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23281638](https://doi.org/10.5281/zenodo.23281638)

## [0.7.19] - 2026-10-10

### Added

- `public-safety --quiet` and `audit --quiet`: only the summary and verdict lines with their counts, no per-hit or
  per-finding line; exit codes and `--json` unchanged. (#44)

### Changed

- AGENTS.md and docs/agents.md state that `import lean_pkg` is not a supported interface: the CLI and its `--json`
  outputs are; internal function signatures may change in any release, and a change gets a CHANGELOG line. (#46)
- `--fields` (`status`, `list`, `board`) accepts every table column as an alias, beside 0.7.15's `pin` and
  `lease.expires`: `vis` (= `visibility`), `reaudit` (= `reaudit_after`), `expires` (= `lease.expires_at`, the
  `EXPIRES` column of `list`/`board`) and `lease.claimed` (= `lease.claimed_at`, the `CLAIMED` column of `leases`);
  output and the `fields` list carry the record key. `tests/test_columns.py` reads the headers of `list`, `board`,
  `leases` and the labels of the text `status` record from the rendered output and fails when a column has no
  `--fields` key or `--json` does not emit it (`leases`' derived `STATE` column maps to `live`/`invalid`). (#45)
- Verdict first (#44), now an AGENTS.md rule tested by `tests/test_verdict_first.py`: `audit` prints its count line
  "N finding(s): W warn, I info" before the findings as well as last; `index check` prints "N entries checked: all
  pass|FAILED" before the per-entry lines as well as last; `validate` prints `INVALID (N error(s))` before the
  `FAIL` lines as well as last. The last line of each is unchanged. `public-safety` already led with its summary.
- For callers of the Python module (not a supported API): `public_safety.report_lines(name, result, quiet=False)`
  gains the keyword `quiet`; existing two-argument calls are unaffected.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23280169](https://doi.org/10.5281/zenodo.23280169)

## [0.7.18] - 2026-10-10

### Added

- `next`, `status`, `gate` and `comparator` say when the package checkout is not on its default branch
  (`refs/remotes/origin/HEAD`, else `main`/`master`; local refs only) or is on a detached HEAD: a `notes[]` entry and
  a `note:` line in `next` (`evaluating branch X, not the default D`), a `branch:` line and an always-present
  `"branch": {current, default, detached, is_default}` object in `status` (`--json`), and a stderr `WARN` beside the
  dirty-tree one in `gate`/`comparator`. Never a refusal: HEAD is still what is evaluated. (#48)

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23279442](https://doi.org/10.5281/zenodo.23279442)

## [0.7.17] - 2026-10-10

### Added

- `next NAME [--agent A]`: read-only and offline (no `gh`, nothing written), the single recommended next command for a
  package and a one-line reason, decided from the registry record and the checkout with the validators the writers
  use (`gate`'s receipt check, the comparator check) and the existing next-command producers (`comparator config
  --coverage`, `publish --dry-run`): `claim`, `update --status ...`, `gate --check` / `gate`, `comparator-receipt`,
  `comparator config --from-claims`, `comparator`, `publish --dry-run`, `reservoir check`, `palomar prepare`,
  `record ...`, or nothing for `upstreamed`/`archived`. Exit 1 when blocked: a live lease held by another agent
  (with `--agent`), an unparseable lease, a missing checkout, a dirty tree while evidence is produced, a receipt
  `gate` would refuse or a recorded receipt that cannot be read. `--json`: `{ok, name, command, reason, blockers[],
  notes[], state{}}`. (#42)
- `llms --short`: a compact agent contract of about 40 lines (the title, the first lines of `PURPOSE`, `STANDING
  RULES`, and the first line of each subcommand), cut from the full contract by its section headers so it cannot
  drift; every line is a line of the full text. `--json` adds `"short": true`; plain `llms --json` is unchanged.
  (#43)
- `lean-pkg report [--command "ARGS"] [--package NAME] [--title T] [--enhancement] [--repo OWNER/NAME]
  [--dry-run|--yes] [--json]` (#47): a bug report in the shape of `.github/ISSUE_TEMPLATE/bug_report.md`
  (version, doctor and host-check summaries, the command re-run with its exit code and output, the
  package record), every captured text redacted (`<registry>`/`<checkout>` placeholders, home `~`, the
  public-safety families, patterns files and record-derived names); a dry run unless `--yes`, which
  files it with `gh issue create`.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23278906](https://doi.org/10.5281/zenodo.23278906)

## [0.7.16] - 2026-10-10

### Added

- Optional `lease.contact`: the address of the holding session (free text, typically an amsg address
  such as `lab-desk/n14-paper` or `@lean-ops`), so a reader of `leases` can tell who is live behind a
  lease. `claim NAME --contact TEXT` stores it; `renew NAME --contact TEXT` sets or replaces it, and
  `renew` (or the holder's re-claim) without `--contact` keeps it; `release` drops it with the lease.
  One line of at most 200 characters (empty, a newline or another control character, or longer: exit 2,
  nothing written). `leases` gains a last `CONTACT` column (blank when unset) and `--json` rows a
  `contact` (null when unset); `status NAME` shows it in text and `--json`, and `status`/`list`/`board
  --fields lease.contact` select it (null for a lease without one, not a usage error). The events
  `claimed`, `claim-forced` and `claim-refreshed` carry `contact` when the lease has one. Additive:
  schema version unchanged, no `migrate` step; `validate` accepts leases with or without `contact` and
  rejects a non-string. `public-safety` derives patterns from the contact's tokens as it does from
  `agent` and `workspace`. The CLI reference's "Lease holders" section now points at `--contact`.
- `renew NAME --no-contact` clears the lease's contact while renewing (holder-only, like `renew`); the
  `claim-refreshed` event records `contact_cleared: true` when a contact was removed. With `--contact`
  it is a usage error (exit 2, nothing written). `claim` does not take it.

### Changed

- Note for callers of the Python module (not the CLI): `public_safety.report_lines(name, result)`
  dropped its `root` argument in 0.7.14 ("summary line names no path", `52142de`); the old
  three-argument call raises `TypeError`. The module is not a supported API, but the change was
  unannounced.
- Docs: a documentation gap pass for the features added since 0.5.0. README.md gains "What it does"
  (register, claim, gate, comparator, publish, index; the safety nets `public-safety`, `release
  --fresh-repo`/`--adopt-repo` and `comparator host-check`; `claims.yaml`, `formalization.yaml`,
  Reservoir, Palomar, Zenodo) and "For collaborators"; docs/index.md and docs/how-to.md gain the
  matching routes and recipes; both citation paragraphs name the concept DOI; RELEASING.md gains
  checklist step 0 (the gap pass) and AGENTS.md the rule; the index-entry issue template is on
  `index-entry/2` and the bug report asks for `lean-pkg --version`.
- Tests: `tests/test_docs.py` checks that every subcommand, nested ones included, read from the
  parser, has a docs/cli.md row, and that every top-level subcommand appears as `lean-pkg <cmd>` in
  README.md, docs/index.md or docs/how-to.md.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23273938](https://doi.org/10.5281/zenodo.23273938)

## [0.7.15] - 2026-10-10

### Changed

- `status NAME` (JSON, text and `--fields`) and `list`/`board --fields` in text mode show a lease's derived
  `expires_at` (`claimed_at` + `ttl_hours`, ISO 8601 UTC; null when the lease fields are unparseable;
  never stored), as `list`/`board --json` already did, so `status NAME --fields lease.expires_at` reads a
  lease's expiry instead of exiting 2 and leaving it to be computed by hand.
- `status`/`list`/`board --fields` accept the table names as aliases: `pin` (the `PIN` column) for
  `mathlib_pin` and `lease.expires` (the `EXPIRES` column) for `lease.expires_at`. The output and the
  `--json` `fields` list carry the record key; the record keys work as before, and an unknown field's
  usage error lists the aliases.
- Committed mode says when the environment turned it on: the stderr commit line ends `(committed mode:
  env LEAN_PKG_PUSH=1)` (or `LEAN_PKG_COMMIT=1`) when no `--commit`/`--push` flag was given, the push line
  carries the sha (`pushed origin/<branch> (<sha>)`), and every subcommand's `--commit`/`--push` help
  names its variable and adds `set now` when it is set. A wrapper that exports the variable no longer
  makes `renew` (or any write) commit and push without a word about why.
- `public-safety`: with hits, the human report prints the verdict line right after the summary and
  patterns lines as well as last, lists blocking hits before warn-only ones, and puts the warn-only hits
  under a `warn-only hit(s): N (families); they do not block` line, so dozens of `latex-label` `WARN`
  lines no longer push the verdict out of a truncated view. `--json` is unchanged.
- Docs: CLI reference section "Lease holders": a lease stores no session address; find your own leases
  with `leases --agent A` (the `--agent` you claimed with) and one package's holder with `status NAME
  --fields lease.agent,lease.workspace,lease.reason,lease.expires_at`.

public-safety (make public-safety, --strict, history): 0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)

Zenodo version DOI: [10.5281/zenodo.23273258](https://doi.org/10.5281/zenodo.23273258)

## [0.7.14] - 2026-10-10

### Added

- `public-safety`: path-scoped pattern lines. `allow: GLOB: REGEX` exempts a match only in files whose
  package-relative path matches GLOB, and `block: GLOB: REGEX` / `warn: GLOB: REGEX` report only there
  (`*`, `?` within a segment, `**` across directories; no `/` matches the file name at any depth; a
  trailing `/` matches everything under the directory). Scoped lines never apply to commit messages.
  A line keeps its old global meaning unless the text before its first `: ` looks like a path (no
  space, colon, backslash or parenthesis; one of `/ * ? .`). `patterns_source` gains `allow_scoped`
  and `scoped_patterns`; the summary's patterns line counts the path-scoped lines. (#38)
- `comparator NAME --receipt NEW --replace`: when `.lean-receipts/` already holds a different comparator
  receipt for the same commit, the older file is kept as `<commit12>-comparator.replaced-<UTC
  stamp>.json` (never overwritten), the new one is attached, and the event is `comparator-replaced`
  with `replaced` (the kept path) in history, the event log and `--json`. Without `--replace` the
  refusal now names the flag instead of asking for a hand move. (#21.1)
- `--agent A` on `gate` (no longer only with `--refresh-formalization`), `comparator`, `record`,
  `promote --yes`, `claims sync` and `search --record`: another agent's live lease refuses (exit 1,
  nothing written) and `A` is recorded as `actor` on the history entry and the event line (additive).
  Without `--agent` nothing new is refused; a live lease prints a warning naming the holder. (#21.3)
- Authorship (option A, schema-conformant): optional registry fields `source_paper` {title, authors[],
  arxiv, doi} and `ai_assistance` {tools[], roles[]} (validated by `validate`, listed in the package
  record), set with `update NAME --source-paper-title T --source-paper-author A... --source-paper-arxiv
  ID --source-paper-doi DOI --ai-tool T... --ai-role R...`. `formalization` renders them into the v0.4
  `acknowledgements` string only, as `Formalizes results of TITLE by AUTHORS (arXiv:ID / doi:DOI).` and
  `AI assistance: TOOLS for ROLES.` lines after any hand-written text, refreshed on every run and never
  duplicated; they count as derived for the evidence-only rule. `project.authors` stays the formalizers
  (consent[] owners/authors), so a paper coauthor is credited without a consent row. (#22.6)
- `lean-pkg palomar prepare NAME [--out DIR] [--dry-run] [--json]`: builds a Palomar submission bundle,
  bundle only, with no network request: `formalization.yaml` (rendered, schema-valid), every comparator
  configuration tracked at HEAD, the gate and comparator receipts validated against HEAD, a
  `README.txt` with the manual submission steps and the Palomar URL, and `MANIFEST.sha256`. Default
  directory `<checkout>/.lean-receipts/palomar-<HEAD12>/`, never overwritten. Refused (nothing
  written) without receipts bound to HEAD or an evidence-only ancestor, without consent, when the
  render fails the schema, or on blocking public-safety hits in the bundle. Records the additive
  event `palomar-prepared` {commit, bundle, bundle_sha256}; `--dry-run` prints the plan. (#22.3)

### Changed

- `public-safety`: the human summary line names no path (`public-safety for NAME: N hit(s) ...`), so it
  can be pasted into release notes verbatim; it used to carry the scanned checkout's absolute path in
  parentheses. RELEASING step 3 no longer asks to strip it. (#38)
- `public-safety`: a registry-derived user or host token is judged inside its surrounding non-space run.
  An `allow:` line exempts an occurrence only when one of its matches in that run covers the token, so
  `allow: https://host\.example/~user/` exempts that URL and nothing else; before, the allow line saw
  only the bare token, and the only way to allow the URL was to exempt the token everywhere. An
  anchored bare-token allow (`allow: ^user$`) now exempts only a standalone occurrence. The reported
  span and the redaction are still the token itself. (#38)

### Fixed

- A package in a subdirectory of its git repository had a comparator configuration (and every other
  file read through the same helper: claims.yaml, formalization.yaml, index sources) read from the
  repository root when the path was bare: git resolves `<rev>:<path>` from the root. Every such read
  is now relative to the package directory (`<rev>:./<path>`), as the toolchain and config-list reads
  already were. (#21.2)
- `register --status public` without `--reason` exited 2 (usage error, no JSON) while every other missing
  `--reason` has been a refusal since #17; it is now one too: exit 1, and `{ok: false, error}` under
  `--json`. (#21.6)
- The TUI claim prompt turned a blank TTL into 24 h, so a holder's re-claim from the TUI shortened a
  longer lease. A blank TTL now means what no `--ttl` means on the CLI: 24 h for a new claim, the
  lease's own TTL for the holder's re-claim; the prompt and the confirmation say so. (#21.5)
- Rows stored before #17 with a literal `~/x` `local_path` still resolved to `$HOME/~/x`. `migrate` now
  rewrites each through the `--path` normaliser to its `$HOME`-relative form and lists it as a change
  (`--dry-run` shows it first); `~user/...` is left alone. (#21.4)
- `audit`'s "gh query failed" detail and the promote/publish "reuse lint failed" line cut the tool's
  message at 120 characters; both now keep its whole first line, as promote's gh visibility line has
  since #19. (#21.7)
- Under `--json`, an argparse-level error (unknown flag, invalid choice, missing argument) printed only
  argparse's usage text on stderr. It now also prints the usage-refusal JSON `{ok: false, error,
  usage: true}` on stdout, like every other usage error; the exit status stays 2. (#21.7)
- `gate`/`comparator` (and every caller of the comparator validator) read a comparator log from the
  working tree even when it was already committed, so a log edited after its commit passed until
  `promote` refused it. Once a log is tracked at HEAD, a working copy that differs from the committed
  bytes is refused, naming the file and `git checkout -- PATH`. An uncommitted log is read as
  before: it is written after the bound commit, so it cannot be read at that commit. (#21.2)

Zenodo version DOI: [10.5281/zenodo.23272910](https://doi.org/10.5281/zenodo.23272910)

## [0.7.13] - 2026-10-10

### Fixed

- Git-ignored evidence is reported before promote time. A default `.gitignore` with `*.log` silently hid the
  comparator logs (and `comparator-stages/` files) that `promote` requires committed, and owners found out only
  when promote refused them as untracked. `publish --dry-run`, the `promote` checklist, and `gate`/`comparator`
  after attaching evidence now run `git check-ignore -v` on each evidence file on disk and print a `WARN` naming
  every ignored path, its rule (`.gitignore:3:*.log`) and the remedy (`!.lean-receipts/` and
  `!**/.lean-receipts/**`, or `git add -f`); promote still refuses, but its `✗` names the rule. `--json` gains
  `ignored_evidence[]` {path, rule}. (#40)

### Added

- `comparator-receipt` stage 04 progress: `slot i/N <name> started` / `done in Ss` / `failed` lines,
  a `heartbeat:` line every 60 s of judge silence, `04-comparator.partial.json` (slots done, pending,
  failed; rewritten after every unit) and `stages.json` `progress {done, total, failed,
  partial_report}`; on TERM/INT/HUP the stage is `partial` and the runner exits 128+signal with the
  resume command. (#39)
- `comparator-receipt` resume: accepted slots are cached under `comparator-stages/04-slots/`, keyed
  by commit, config hash, toolchain, mechanism, judge digest, config and slot; a rerun judges only the
  rest. `--no-resume` judges everything again. (#39)
- `comparator-receipt --judges N` (env `LEAN_PKG_COMPARATOR_JOBS`): contiguous shards of each
  configuration's slots judged in parallel on partial configurations; exact merge; the receipt
  records `judges` and per config `units[]`. Toolchain mechanism only. (#39)
- `comparator-receipt --slots SPEC` (names, `<config>:<name>`, `i-j`, `sample:K`): a subset run
  writes a receipt marked `partial: true` into the stage directory; every validator refuses it as
  evidence. (#39)
- `comparator-receipt --estimate [--sample K] [--json]`: times a stratified sample of slots, each
  judged alone, and reports `per_slot_seconds`, `slots`, `estimated_seconds`,
  `lower_bound_seconds` and `recommended_cap`; no receipt. docs/comparator.md gains a Runtime
  section. (#39)

### Fixed

- `comparator-receipt` evidence no longer names the host: absolute paths under the checkout,
  `HOME` and `TMPDIR` become `<checkout>/`, `<home>/`, `<tmp>/` in every stage log, unit log,
  receipt log and receipt field; `--host-label LABEL` (or `COMPARATOR_RECEIPT_HOST_LABEL`) replaces
  the host name and is recorded as `host_label`. The setup stages now stream through the same
  runner as the judge (stdin `/dev/null`, heartbeats, killed with the runner). (#41)
- `comparator-receipt` resolves `lake`/`lean` through elan per the checkout's `lean-toolchain`
  (elan's `bin/` first on `PATH`, `ELAN_TOOLCHAIN` set) for every stage, probe and judge, instead of
  a bare PATH lookup that could pick a system `lake` of another Lean; without elan, a `PATH` lake
  whose version differs from `lean-toolchain` is refused. `stages.json` and the receipt record
  `toolchain_resolution`; `comparator host-check` warns (`lake-path`) when `PATH`'s lake is not
  elan's. (#41)
- `comparator-receipt`: the stage-04 judge's output is streamed to `04-comparator.log` as it
  arrives (stdin `/dev/null`, one merged pipe drained by a thread, `stdbuf -oL -eL`,
  `PYTHONUNBUFFERED=1`, own process group killed on TERM). It was captured in memory and written
  only when the judge exited, so a run killed by an external timeout left an empty log. (#39)

Zenodo version DOI: [10.5281/zenodo.23272332](https://doi.org/10.5281/zenodo.23272332)

## [0.7.12] - 2026-10-09

### Changed

- The repository is public. Decision 0006 (public flip) is accepted: G5, G7 and G8 signed,
  G1 waived and G2 accepted with its documented residue by the maintainer; `MAINTAINERS.md`
  names a deputy (operations, releases). No code change since 0.7.11; this is the release
  that matches the public documentation.

Zenodo version DOI: [10.5281/zenodo.23272099](https://doi.org/10.5281/zenodo.23272099)

## [0.7.11] - 2026-10-09

### Added

- `make public-safety`: lean-pkg's own `public-safety --strict` scan of this repository, history
  included, over a fresh clone registered in a throwaway registry; needs the untracked
  `.lean-pkg-private-patterns` (now in `.gitignore`). A release checklist step (RELEASING.md) and
  gate G8 of decision 0006.
- `release NAME --fresh-repo DIR --from-tree SRCDIR`: export a hand-cleaned working tree (not a commit
  of the source repository) with the same exclusions, public-safety scan and single commit; in a git
  checkout the source's `.gitignore` applies. The message records `source commit: none (hand-cleaned
  tree)` and the gate-bound commit for provenance. (#37)
- `release NAME --adopt-repo DIR [--remote URL] [--allow-history] [--dry-run]`: adopt an existing
  repository that already is the intended public history (exactly one commit unless
  `--allow-history`; public-safety on its tree and full history; no export, no new commit), then the
  same relocation as `--fresh-repo` with a dated note naming the adopted commit. (#37)

### Changed

- `public-safety`: the `latex-label` family is calibrated for Lean sources. A key is never matched
  inside a longer name (`foo_eq_bar`, `eq_comm`, `Nat.eq:`); `eq`, `def` and `ex` match only with
  `:`; a `key-` form needs a digit, a capital or an inner `.` in the label, so all-lowercase hyphen
  prose (`eq-of-foo`, `def-eq`) is silent; a built-in allow list covers `def-eq`, `defeq`, `eq-of`,
  `eq-iff`, `ex-falso`. The human summary says how many hits came from `latex-label`; JSON gains
  `latex_label_hits` and `patterns_source.latex_label_allow`. (#37)

## [0.7.10] - 2026-10-09

### Added

- `public-safety`: a warn-only built-in family `latex-label` for references to a LaTeX manuscript
  (cross-reference keys such as `lem:`/`thm:`/`eq:`/`sec:`, `main.tex`, `\ref{`/`\cite{`/`\label{`
  fragments, Overleaf, arXiv ids), which leak an unposted paper with no path in them. Every hit gains
  `severity` (`block` or `warn`); warn hits print as `WARN` lines, keep the exit status 0 and do not
  block `promote` or `release --fresh-repo` on their own (promote shows a `WARN` checklist line).
  `.lean-pkg-private-patterns` accepts `block: REGEX` (as a bare line) and `warn: REGEX`;
  `--strict` makes warn hits blocking. JSON gains `block_hits`, `warn_hits`, `strict` (and
  `block_hits`/`warn_hits` in promote's and publish's `public_safety`); `hits` keeps every hit. (#36)

## [0.7.9] - 2026-10-09

### Added

- `lean-pkg public-safety NAME [--history|--no-history] [--json]`: read-only scan of the package's
  tree (what git would ship) and, by default, every blob and commit message reachable from any ref
  under the package directory, for private patterns: the package's `.lean-pkg-private-patterns`
  (regexes, `#` comments, `allow: REGEX` exemptions; the file itself is a hit when tracked), built-in
  defaults (absolute home paths, `~/Dropbox/`, `approvals/`, `notes/`, `*.private`, `TODO-private`,
  undeclared email addresses), user and host names derived at scan time from the record's
  `local_path`/`agent`/`workspace`, and internal files by path (`approvals/`, `notes/`, `drafts/`,
  `scratch/`, `*.priv`). Each hit names its location (path and line; history adds the commit), a
  pattern label and a redacted excerpt (every match replaced by `[…]`; raw private strings are never
  printed). Exit 1 on hits; `--json` `{ok, hits, tree_hits, history_hits, patterns_source, ...}`.
  `promote` runs it and refuses while hits remain (`--skip-public-safety` turns it into a `WARN`);
  `publish NAME --public-safety` adds it to the publish checklist. (#35)
- `lean-pkg release NAME --fresh-repo DIR [--remote URL] [--keep-receipts] [--source-commit SHA]
  [--dry-run]`: export the gate-bound commit's tree (package directory only; the patterns file,
  internal paths and `.lean-receipts/` dropped and listed) into a new single-commit repository at
  DIR, after a public-safety scan of the exported tree (any hit refuses, nothing written). The commit
  message records the source commit and the export date. The registry entry is then relocated with
  the `update --path` machinery: gate and comparator evidence cleared, `verified-private` becomes
  `gate-pending`, and a dated note keeps the old receipt paths and the source commit. `--dry-run`
  prints the plan and the next commands (gate, `comparator-receipt --prepare`, publish) and writes
  nothing. docs/receipts.md gains "Publishing a package incubated beside private work". (#35)

## [0.7.8] - 2026-10-09

### Added

- `--registry-snapshot COMMIT` global option (env `LEAN_PKG_REGISTRY_SNAPSHOT`): run a read-only
  command against the registry file and event log as of `COMMIT` of the git repository holding
  them (read with `git show` into a temporary copy). Every write is refused with exit 2
  (`read-only: --registry-snapshot`); `--json` objects gain `registry_snapshot`. Replaces pointing
  `LEAN_PKG_REGISTRY` at a hand-made historical copy, for instance to run
  `comparator config --checkout DIR --dry-run` on a copy at an earlier claims-synced commit. (#34)

### Changed

- The `comparator config --emit-script` driver now runs `export LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"`
  near its top: one Lean thread per process on a cluster node unless the job script sets a value,
  which is kept. The one-shot `--challenge-from-type` run is unchanged. docs/comparator.md gains
  "On shared cluster nodes": the file table is per node, so request a whole node or one job per
  node, set `LEAN_NUM_THREADS`, and keep `LEAN_PKG_TYPES_JOBS` at 2 or less. (#34)

## [0.7.7] - 2026-10-09

### Fixed

- `audit` human output led with the check code even for `info` findings (an archived package with a
  vanished upstream printed `warn    NAME: archived; ... [info]`). Each line is now `LEVEL  CHECK
  NAME: DETAIL`: the level (`warn`/`info`) first, the check code aligned after it; the trailing
  ` [info]` suffix is dropped (the column carries it, TTY or not). A last line counts the findings
  by level ("N finding(s): W warn, I info"), and `audit --json` gains top-level `counts`
  `{warn, info}`. (#33)
- `audit`: a failed gh query reported the check as the literal `warn`, which was never a documented
  check value. Its `check` is now `gh-missing`, `gh-not-authenticated`, `gh-no-access`,
  `gh-not-found` or `gh-error` (also for "gh returned non-JSON", which now carries `gh: error`); the
  `gh` key is unchanged. (#33)

## [0.7.6] - 2026-10-09

### Added

- `comparator config --from-claims NAME --checkout DIR`: use another checkout of the package
  (for instance the clone that ran the type driver) instead of the registry's `local_path`;
  refused, with both commits and the differing paths, unless its HEAD is the claims-synced commit
  or an evidence-only descendant, and, with a types flag, unless it has the lakefile. (#31)
- `comparator config --from-claims NAME --from-output F --no-registry --out FILE` (also with
  `--challenge-from-type`): scratch mode for a package under another agent's lease; writes the
  checkout's `comparator.json` merged with the new entries to FILE outside the checkout, never
  consults the lease, and lists what it did not do (JSON `no_registry`, `not_done[]`). (#31)

### Fixed

- `update --notes TEXT` silently replaced the notes. It now prints the replaced value and "use
  --append-note to keep the previous note" (JSON `hint`); new `update NAME --append-note TEXT --agent A`
  appends `YYYY-MM-DD (A): TEXT` (UTC), creating `notes` if absent; it cannot be combined with
  `--notes`. (#32)
- docs/comparator.md "Host policy" gains "Which hosts can produce receipts": run `lean-pkg comparator
  host-check` on the candidate host; one that fails `mechanism toolchain` cannot produce `toolchain`
  receipts. `doctor`'s `tool comparator` line says to run `host-check` when `bwrap` is missing. (#32)
- `audit` `duplicate-namespace` had no way to record an intended collision (a comparator
  `Challenge`/`Solution` layout, say). New `lean-pkg update NAME --namespace-note TEXT [--namespace NS]`
  stores `namespace_notes: {NS: TEXT}` (optional, additive; `validate` checks its shape); the finding is
  `info` with the note once every package sharing the namespace has one, else `warn` naming the
  command. (#32)
- `audit` `missing-path`: the detail gives the recorded path and the fix (`lean-pkg update NAME --path
  DIR --reason "checkout moved" --dry-run`, then without `--dry-run`), and says "found at DIR" when a
  directory next to the recorded one has a lakefile and the package's git remote. (#32)
- `audit`: a failed gh query says why. The `warn` finding gains `gh` (`gh-missing`,
  `not-authenticated`, `no-access`, `not-found`, `error`) and a detail naming the remedy ("token lacks
  access to OWNER/REPO: re-run `gh auth login` with a token that can read it" vs "repository not
  found"). A GraphQL "Could not resolve to a Repository" is checked against `gh auth status` and the
  checkout's own git remote, since GitHub reports a repository the token cannot read as not found.
  An `archived` package whose upstream is gone is `info`. New `audit --no-gh` skips the gh checks
  (`gh-skipped`, info). (#32)
- `audit`: the toolchain floor is the comparator/promotion requirement, so `below-toolchain-floor` is a
  `warn` only for `public`/`upstreamed` packages, packages with comparator evidence, or under the new
  `audit --promotion`; elsewhere it is `info` ("toolchain below the comparator floor FLOOR (matters only
  for promote/comparator)"). Every `audit --json` finding gains `level` (`warn` | `info`); human output
  marks info lines `[info]`. (#32)
- `comparator config --challenge-from-type` / `--emit-script` driver: a `lake env lean` process that
  exits non-zero (or times out) is an extraction failure, not a per-declaration type error. Each
  result records `rc`, `failure` (`declaration` or `extraction`) and `output_tail`; the output gains
  a `summary` `{ok, total, filled, errors, declaration_errors, jobs}`; the driver exits 1 when any
  extraction failed and 2 when nothing was filled (it exited 0 after 224 of 225 extractions failed).
  `--from-output` and `--challenge-from-type` refuse such a run (exit 1, counts named, nothing
  written) unless `--allow-partial`, which uses the filled entries and keeps the rest as TODO with
  `restatement_reason: extraction_failed`. (#30)
- The type driver no longer runs more Lean processes than the CPUs it may use (default
  `min(2, CPUs)`, decided on the host that runs it, Slurm allocation included), and when a
  declaration fails with "too many open files" (ENFILE/EMFILE, typical on Lustre/NFS) it halves
  the workers and retries those declarations, at least two rounds down to one worker, recording
  the rounds in `summary.backoff`. `LEAN_PKG_TYPES_BACKOFF_SECONDS` sets the pause. (#30)
- `comparator config --challenge-from-type`: a pretty-printed type is no longer trusted as is.
  The driver re-elaborates each printed type in the declaration's module context
  (`example : (T) := @_root_.<decl>`, `autoImplicit false`) and only an exit status of 0 makes the
  result `ok`; on a real package 5 of 24 entries marked ready had lost a coercion target, an
  integral domain or a `Matrix` type. A failure keeps the TODO lines with `needs_restatement: true`,
  `restatement_reason: round_trip_failed`, the error in `type_error` and the text in
  `printed_type`; `summary.round_trip_failed` and the JSON `types.round_trip_failed` count them.
  Output without the check is pre-filled but always `needs_restatement`. (#31)

## [0.7.5] - 2026-10-09

### Added

- `list` and `board` accept `--fields a,b,c` (JSON and text), the same validation and dotted
  paths as `status --fields` (one implementation): each package record is cut to those keys,
  `--json` keeps the dump's shape and adds a `fields` list, text prints YAML; `board --fields`
  never opens the TUI. `--only` stays a pure package filter (full records). `list --json --only
  NAME --fields status` used to exit 2. (#29)
- `--no-claims` on `status`, `list` and `board`: drops the bulk cached arrays (the package-record
  keys tagged `[bulk]` in `status --help` and docs/cli.md; today `claims`, which can be hundreds
  of KB) from each record, every other key and the output shape unchanged; `--json` adds an
  `omitted` list. The `--help` of the three commands and docs/cli.md now say precisely that
  `--only` filters packages and `--fields` trims fields. (#29)

### Changed

- `lean-pkg --version` says which build runs: `lean-pkg X.Y.Z (<short-sha>[+dirty], editable
  <checkout>)` for an editable install, `(installed <package dir>)` for a wheel or pipx install,
  `(unknown build, <package dir>)` without git. The same local detection as `doctor`'s `install`
  line (which now also marks a dirty checkout `+dirty`); no network. `--version --json` prints
  `{"ok": true, "version", "commit", "dirty", "install", "path"}`. (#29)

## [0.7.4] - 2026-10-09

### Added

- `status NAME --fields a,b,c` (JSON and text): only those keys of the package record; dotted
  paths reach into mappings (`gate.receipt`, `lease.agent`). `--json` keeps the nesting and adds
  a `fields` list; text prints YAML. An unknown field exits 2 listing the valid keys.
  `list --json --only NAME[,NAME]` and `board --json --only NAME` restrict the registry dump to
  those packages with the shape unchanged (also the static table; `board --only` never opens the
  TUI); an unknown name exits 1. (#28)
- `lean-pkg status --help` lists every top-level key of the package record with a one-line
  meaning, and docs/cli.md gains a "Package record" section. Both come from one table in the
  code (`PACKAGE_FIELDS`), which also defines the keys `validate` requires. (#28)

### Changed

- `doctor`: an optional tool's `n/a ... not on PATH` line says what the tool is for and when it
  is needed (`comparator`, `nanoda`, `bwrap`, `go`, `cargo`, `jq`: only to produce comparator
  receipts on this host; `reuse`: release gate only; `gh`: promote, audit, reservoir check), so a
  clean doctor on a read-only host no longer reads as a partial failure. Levels are unchanged. (#28)

## [0.7.3] - 2026-10-09

### Added

- `comparator-receipt` runs in stages (`checkout`, `cache-get`, `build`, `comparator`,
  `receipt`), each logged to `<evidence-dir>/comparator-stages/<NN>-<stage>.log` with a
  `stages.json` (status, exit, wall time). Both are kept on failure, and the error names the
  failed stage and its log. `--evidence-dir DIR` defaults to `.lake/comparator-receipt`.
  `--prepare` runs only the setup stages and is idempotent. A later run whose commit,
  toolchain and config hash match a completed setup skips straight to the comparison.
  `--from-prepared DIR` reuses another directory's setup and refuses a mismatch. The receipt
  gains an additive `stages` summary. (#26.1)
- `lean-pkg gate NAME --receipt R [--comparator C] --check`: read-only validation against HEAD
  (binding, the derived-only `formalization.yaml` rule, toolchain at the bound commit, sorry
  count, axioms; with `--comparator` both tiers). It prints `✓` lines or the refusal `gate`
  would print, writes nothing, records no event and works at any status (exit 0/1).
  `comparator --check` now prints the same binding `✓` lines and adds `checks[]` and `head` to
  its JSON. (#26.2)
- `update NAME --path NEW [--dry-run]` (relocate) ends with a `binding at the new path` block
  that groups the binding diagnostics with the next command(s). The block covers each
  recorded receipt's commit against the new HEAD with the evidence-only verdict, the
  `formalization.yaml` exception (pointer, changed, derived-only) and any other changes since
  the receipt commit. The next commands are `gate NAME --receipt R [--refresh-formalization]`,
  `update NAME --formalization-yaml REL` when a missing pointer is the only reason a
  derived-only regeneration is cleared, `comparator-receipt` + `comparator`, `claims sync`, or
  "nothing to re-gate". JSON gains `binding` and `next_commands[]`. (#26.3)
- `lean-pkg comparator config --coverage NAME`: a read-only explainer. At the gate-bound commit
  it reports how many theorem/lemma claims the comparator configuration tracked there covers,
  does not cover, or lists only as TODO. It names configurations changed since then and keeps
  the candidate's counts apart, never substituted. It says plainly that a changed or
  uncommitted config cannot certify the bound tree, so a new gate at a commit that tracks the
  config is required, and it prints the exact next commands. (#26.5)

### Fixed

- A launcher that leaked `GIT_INDEX_FILE` (or `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`,
  `GIT_NAMESPACE`) made `comparator-receipt`'s dependency checkout use the wrong index. The
  runner now removes these from every `lake`, `git` and judge it runs and lists them in
  `stages.json` (`git_env_scrubbed`); `lean-pkg` does the same for its `git` calls in a
  package checkout (not in the registry repository). `comparator host-check` warns
  (`git-env`; additive `env_warnings[]` in `--json`), and so does the `doctor` `host` line,
  when any is set. (#27)
- `gate NAME --receipt R --refresh-formalization` refused at an evidence-only descendant
  whose `formalization.yaml` had only derived keys regenerated when no `formalization_yaml`
  pointer was recorded yet, because the derived-only exception follows the pointer. The
  refresh owns that path and records the pointer right after the gate, so it now judges the
  exception for that path. A kept-key change still refuses. A new end-to-end test covers a
  clean checkout: refresh, commit as an evidence-only descendant, then `gate --check` and
  `comparator --check` pass; it also checks that kept keys are byte-equal across the
  refresh. (#26.4)

## [0.7.2] - 2026-10-09

### Added

- `lean-pkg comparator config --from-claims NAME --challenge-from-type [--lake-env DIR] [--jobs N]
  [--timeout S]`: pre-fills each scaffolded Challenge with the declaration's elaborated type
  (pretty-printed by `lake env lean` on the build host) and its Solution with
  `exact @_root_.<decl>`; `needs_restatement: true` marks types that use project definitions,
  and a declaration that fails keeps its `TODO:` lines with `type_error`. `--emit-script F`
  and `--from-output F` split it across hosts (driver on a Slurm node, JSON
  `comparator-types/1` checked against HEAD). (#25)
- `lean-pkg claims migrate NAME --from PATH [--out claims.yaml] [--dry-run]`:
  turns a legacy claims JSON into a `claims/1` skeleton in the checkout,
  copying the fields it can map and marking the rest `TODO`
  (`file: TODO.lean`, which `claims sync` refuses). It never overwrites, never writes
  the registry, and grants nothing: commit the file, then `claims sync`. (#24.1)

### Changed

- `reservoir check` before promote: when every ✗ is pending promote, the closing line reads
  "not eligible yet (pre-promote): promote --yes will satisfy the ✗ items above" instead of
  "not eligible: fix every ✗ above"; exit codes and JSON are unchanged.

### Fixed

- `lean-pkg search` no longer sends natural-language terms to Loogle, which
  parses only declaration and type patterns. The term is routed by shape: a
  Lean pattern (arrows, binders, `:`, `(`, `?`, `_`, `⊢`, math operators, a
  `Nat.`-style dotted name) goes to Loogle; prose skips Loogle with a one-line
  reason and an example pattern, and every other source still runs.
  `--loogle` forces Loogle. `--json` gains `route` (`signature` or
  `natural-language`) at the top and on every source. (#24.3)
- `claims check NAME` no longer passes a package with no claims coverage. No
  cached claims and no `claims.yaml` at HEAD (`coverage: none`), or a
  committed but unsynced `claims.yaml` (`coverage: unsynced`), now exits 1
  with a next-step hint naming any legacy `*claims.json` in the checkout;
  `claims list NAME` states the same coverage. `--json` gains `coverage`,
  `cached_claims`, `claims_yaml`, `legacy_claims` and `hint`. (#24.1)

## [0.7.1] - 2026-10-09

### Added

- `lean-pkg comparator config --from-claims NAME [--out comparator.json]
  [--dry-run]`: adds one `theorem_names` entry (`<Package>Audit.<decl>`)
  per synced `theorem`/`lemma` claim that no tracked comparator
  configuration covers, with `TODO:` Challenge and Solution lines under
  `lean_pkg_scaffold.todo`. It never removes or reorders existing entries,
  prints already-configured/added/skipped counts, and refuses when
  `claims.yaml` is not tracked or not synced at HEAD. Names still marked TODO
  are left out of `formalization.yaml`'s `status.main_results`. (#23.6)

### Fixed

- Consent re-recorded with the same `who`, `role` and `pointer` is now a
  no-op (`already recorded`, exit 0, `"already_recorded": true`), like a
  repeated `palomar-preflight-pass`; a changed pointer is still a new record.
  `publish --dry-run` and the human `status` view show each (who, role)
  once (`status --json` keeps every record), and `audit` gains the
  informational finding `consent-duplicate`. (#23.1)
- The `publish --dry-run` tag hint no longer assumes `origin` is GitHub: it
  names the checkout's GitHub remote, found by URL among `git remote -v`
  (preferring the one that names the package's `repo`), adds "after
  promote" while the package is not public, and says so when no remote
  points at GitHub. (#23.2)
- `reservoir check` before promote: while the package is not `public` or
  `upstreamed`, the GitHub-dependent items `promote --yes` will satisfy are
  labelled `pending promote: ...` (still failing, exit 1) and a closing
  `pre-promote:` line says what promote will fix; `--json` gains
  `pre_promote` and `pending_promote[]`. A new `WARN` flags a
  `lean-toolchain` that is a release candidate or nightly. (#23.3)
- `comparator host-check` judges each mechanism against its own
  requirements and prints a `mechanism toolchain|legacy: ok|fail` line
  for each, naming what fails. The verdict names only the usable mechanisms
  and says that Palomar runs only `toolchain`: a host where `bwrap` cannot
  create a user namespace can still produce `legacy` receipts (landrun does
  not use bwrap), and the verdict now says why `toolchain` fails there and
  that `legacy` receipts do not predict Palomar. `toolchain` now also needs
  `python3`, which `verify-comparator.sh` requires. The Landlock thresholds
  are unchanged and now cite their source: the pinned comparator calls
  landrun with `--best-effort`, so any ABI from 1 runs (ABI 9 is landrun's
  strict default, not a requirement), and below ABI 4 the sandbox has no
  network rules. `--json` gains `mechanism_detail` and `palomar`;
  docs/comparator.md has a per-mechanism requirements table. (#23.4)
- Evidence circularity: the evidence-only descendant rule now also admits
  the package's recorded `formalization.yaml` when it was tracked at the
  bound commit and only the keys the renderer derives changed (compared as
  YAML data). Committing a receipt together with the regenerated file that
  cites it no longer breaks gate, comparator, promote, relocate, audit and
  the publish claims check. A changed kept key or any other path still
  refuses, naming it; `claims.yaml` is never exempt. (#23.5)

## [0.7.0] - 2026-10-09

### Added

- Committed mode: a `--push` rejected as non-fast-forward is retried once:
  fetch, rebase the one new commit onto `origin/<branch>` (the event log
  merges as a union of appended lines), push again. Skipped when other local
  commits are unpushed; a rebase conflict is aborted and the local commit is
  kept, reported as before. (#22.9)
- `formalization NAME --write --no-registry` writes the file (lease checked,
  same refusals) without touching the registry: no pointer update, no event,
  no lock, `--commit` ignored; `--json` gains `"registry_skipped": true`. The
  docs now say plainly that `--write` alone is a registry write. (#22.8)
- `register` notes (stderr, and `notes` in `--json`) when another registered
  package's checkout uses the same Lean root namespace (each `lean_lib` in
  the lakefile, else the top-level `*.lean` files); a warning, never a
  refusal. `audit` gains the informational finding `duplicate-namespace`.
  (#22.7)
- `lean-pkg comparator host-check`: a read-only check (no registry needed)
  of kernel, Landlock ABI, bwrap user namespaces and go/cargo/jq/git/lean/lake,
  with a verdict naming the usable mechanisms (exit 0 can, 1 cannot).
  `comparator-receipt` prints the verdict on stderr when it starts, and
  `doctor` gains a `host` line. `comparator NAME` without `--receipt` is now
  a usage error raised by lean-pkg rather than by argparse (exit 2 either
  way). (#22.5)
- Paper-coupled packages: an optional registry field `paper: {arxiv, status}`
  (status `unpublished`, `preprint` or `published`; additive, no migration),
  set with `update NAME --paper-arxiv ID` (status becomes `preprint`) and
  `--paper-status S`. `promote` gains a `✗` line while the paper is
  unpublished and no arXiv id is recorded; `validate` checks the field. (#22.10)
- `gate NAME --receipt R --refresh-formalization [--agent A]` gates and then
  regenerates `formalization.yaml` (with `--write --force` semantics: kept
  keys survive) and its registry pointer in one step. Another agent's live
  lease and a kept key failing the schema refuse before the gate; an
  identical re-gate whose file is current is refused with nothing written.
  `--json` gains `"formalization"`. (#22.4)
- `lean-pkg publish NAME --dry-run`: one read-only checklist from
  verified-private to listed (the promote checklist, consent, license_spdx
  against the LICENSE text, formalization.yaml current and valid, claims
  synced, lakefile package name, tag at HEAD) followed by the exact next
  `promote`/`git tag`/`record` commands; exit 0 ready, 1 not. Named `publish`
  because `release` already releases a lease; without `--dry-run` it is
  refused in this version. (#22.1)
- `lean-pkg reservoir check NAME [--offline]`: read-only checks of the
  documented Reservoir listing criteria (public, not a fork, 2 stars,
  root manifest and toolchain tracked, license, lakefile package without
  `reservoir := false`, display metadata, tag) and of Reservoir's package
  API, printing the `record ... reservoir-listed` command once listed. No
  automatic poller or recording. (#22.2)

## [0.6.0] - 2026-10-09

### Added

- `record` validates evidence fields before any effect, each refusal naming
  its rule: a `doi`/`concept` must look like a DOI (doi-format), a
  `mathlib-pr-*` `url` must be an `https://github.com/...` URL
  (mathlib-pr-url), `palomar-registered` needs status `public` or
  `upstreamed` (palomar-registered-public) and a `comparator_config` tracked
  at the package's HEAD (palomar-config-tracked), and a non-integer
  `version` is refused cleanly while `palomar-withdrawn version=01` matches
  version 1 (palomar-version). A `reservoir-listed` url naming neither the
  package nor its repository warns (stderr and `notes`); an identical
  `palomar-preflight-pass` is an exit-0 no-op (`"already_recorded": true`);
  a field holding a local absolute path warns and is listed in
  `private_strings`. (#20)
- `search` scores the registry's packages themselves (name exact 3; notes,
  owner_project and repo by token), so `search NAME --offline` finds a
  registered package with no cached claims; package hits print as
  `package: NAME (status) — repo`, and every local hit gains `hit_kind`
  (`package` or `claim`) and `repo` in `--json`. (#20)
- `doctor` gains a `gh auth` line (the github.com account; `warn` when gh is
  not logged in, `n/a` without gh) and says "no origin remote" when the
  registry repository (or an editable install) has none. (#20)
- `audit` gains two informational findings: `receipt-untracked` (a recorded
  gate or comparator receipt file is present but not tracked at the
  reference branch tip) and `duplicate-path` (two packages resolve to the
  same checkout). (#20)
- `lean-pkg update NAME --path NEW --reason TEXT [--agent A] [--redetect]
  [--dry-run]` relocates a package to another checkout without losing its
  name, history or claims cache (event `relocated` with `old_path`,
  `new_path`, `reason`, `cleared`). It refuses the current path, another
  package's path and a foreign live lease (no `--force`), never touches the
  file system, and clears gate receipts and cached claims not bound to the
  new checkout's HEAD; a `verified-private` package then returns to
  `gate-pending` (event `status-changed`). (#13)
- Committed mode for a registry kept in git: global `--commit` (env
  `LEAN_PKG_COMMIT=1`) makes every write command fetch `origin` and
  `pull --ff-only` first (refusing a diverged clone or registry files that
  are already modified), then commit `lean-packages.yaml` and
  `lean-package-events.jsonl` together as `lean-pkg: <event> <name>`;
  `--push` (env `LEAN_PKG_PUSH=1`) also pushes, exiting 1 with the manual
  `git push` command when that fails. `--no-commit` overrides both. Reports
  go to stderr; stdout and `--json` payloads are unchanged. (#7)
- Registry resolution gains a user-level default between the environment
  and the current directory: `--registry`, then `LEAN_PKG_REGISTRY`, then
  `~/.lean-pkg/registry` (base directory overridable with `LEAN_PKG_HOME`; a
  directory or symlink to one holding `lean-packages.yaml`, or the registry
  file itself), then `./lean-packages.yaml`. Non-interactive shells no longer
  need a wrapper to find the registry. `doctor`'s `registry` line ends with
  `[source: flag|env|user-default|cwd]`, `doctor --json` adds `"source"` to
  that check, and a new `user-default` line shows what the default points at
  (`n/a` when not set up). Set it up with
  `mkdir -p ~/.lean-pkg && ln -s /path/to/registry-repo ~/.lean-pkg/registry`. (#12)
- `lean-pkg renew NAME --agent A [--ttl H]`: holder-only restart of a live
  lease (event `claim-refreshed`); refuses when there is no lease, another
  agent holds it, or it has already lapsed, instead of silently recording a
  fresh claim. `lean-pkg leases [--agent A] [--expiring H]` lists held leases
  soonest-expiry first. `doctor` adds a `leases` line that warns about leases
  lapsing within 2 h and expired-but-unswept ones. (#10)
- `lean-pkg doctor` reports `registry-repo`: the git work tree holding the
  registry, its branch, behind/ahead counts against `origin/<branch>` and
  uncommitted tracked files, naming `lean-packages.yaml` and
  `lean-package-events.jsonl` when they are among them; `warn` means do not
  write from this clone yet (pull, push, or commit first). `doctor --fetch`
  fetches `origin` in both the tool checkout and the registry repo before
  comparing. (#11)
- `board`/`list` have an `EXPIRES` column (`in 2h13m` while a lease is live,
  `3h ago` once it lapsed and `audit --fix` has not swept it yet); their
  `--json` output adds `expires_at` to every lease. (#8)

### Changed

- `lean-pkg audit` is read-only without `--fix`: an expired lease is reported
  (`lease-expired ... expired; \`audit --fix\` clears it`, `fixed: false`)
  instead of being cleared and logged. `audit --fix` sweeps as before and now
  says on stderr how many changes it wrote so the two registry files get
  committed together; `--json` carries a `fixed` count. The TUI's `a` is
  read-only. (#9)
- Advisory notes from `claim` and `index add` (missing prior-art search or
  record) go to stderr, so the last stdout line is the result and
  `lean-pkg claim ... | tail -1` scripts see it; `--json` keeps `notes`. (#6)
- Index: `gromov-filling-lean` re-pinned at `ba787a3eb26e` with a comparator
  receipt for Theorem 1.1 (legacy mechanism), whose Challenge copies the
  project's surface-area definitions into a Mathlib-only `Defs.lean`; trust is
  now `comparator`. All three index entries are at comparator trust.
- Index: `markovprocess-pilot` re-pinned at `05d99d75df82`, the commit that
  credits Scott Armstrong for the lemma adapted from MarkovProcess and cites
  MarkovProcess; fresh lower-tier and comparator receipts (toolchain
  mechanism).
- One time policy, documented in `lean-pkg llms` and DESIGN.md: every
  timestamp written is UTC ISO-8601 with an offset at second precision,
  every date written is the UTC date (the wiki's `updated:` used the local
  date), and same-second events keep file order. `leases --json` `now` is at
  second precision. `doctor` adds a `time` line that warns about naive
  (legacy) timestamps, which are still read as UTC and accepted by
  `validate`. (#19)
- Refusals name the fix: a missing registry says where the path came from
  (`resolved from flag|env|user-default|cwd`) and how to create one
  (`lean-pkg init`, `LEAN_PKG_REGISTRY`, `~/.lean-pkg/registry`); `gate` at
  `verified-private` points to `update NAME --status gate-pending --reason
  ...` then `gate`; a different comparator or lower-tier receipt already at
  the canonical `.lean-receipts/` path is named by its full path with the
  file to pass instead. (#19)
- Docs: `audit --fix` (not `audit`) clears expired leases; `renew` and
  `leases` are described in README, how-to, lifecycle, DESIGN, the agents
  page and the `lean-pkg-agent` skill (renew before the TTL lapses); the
  `cli.py` usage block lists every subcommand; `llms` states which events
  are log-only and the remaining `record` field rules. `register --ttl`
  help says it is accepted and ignored. (#19)

### Fixed

- `claims sync` without a `claims.yaml` says the file is missing in the
  checkout instead of "not committed at HEAD ... untracked files do not
  count", which stays for a file that exists but is not committed. (#19)
- `leases --expiring` refuses a negative window (exit 2). (#19)
- `audit` aligns check names to the longest one shown instead of a fixed 20
  columns; `board`/`list` rows carry no trailing spaces; the `promote` gh
  visibility WARN keeps the whole first line of gh's message instead of
  cutting it at 120 characters. (#19)

- `record` validates the would-be registry before writing: an evidence event
  that introduces a schema error is refused with the rule named (for example
  rule doi-in-palomar: a `doi-minted` commit must already be recorded by a
  `palomar-*` event), nothing is written, and `--json` prints
  `{"ok": false, ...}`. Before, such an event was saved and then blocked every
  later `update`/`register`. (#16)
- `update`, `register` and `record` no longer refuse because of schema errors
  already in the registry (another package's, say): those are reported on
  stderr as `pre-existing (not caused by this change): ...` and in `--json` as
  `preexisting_errors`; an error the change itself introduces still refuses.
  (#16)
- `record NAME retract --field event=E --field ts=T --reason TEXT` is the
  append-only correction path for a wrong evidence entry: the entry stays in
  `history[]`, an `evidence-retracted` entry names it, and `validate` and the
  upstreamed check ignore it. (#16)
- The documented transitions now exist: `update --status upstreamed` from
  `public` (needs a recorded `mathlib-pr-merged`, a new evidence event, or
  `palomar-registered`, or a `--reason` containing the URL) and
  `update --status archived` from any status, including `public` and
  `upstreamed`; public still never returns to a private status. A refused
  move lists the moves allowed from the current status. (#16)
- Every command except `doctor` refuses a registry whose schema version is
  newer than this lean-pkg supports (`registry schema version 9 is not
  supported (supported: 1, 2, 3)`, exit 1) instead of showing an empty table;
  `doctor` still reports it as a warning, and `migrate` of older versions is
  unchanged. (#16)
- Registry writes are safe across processes on one host. Every write command
  (and the TUI's claim/release/gate keys) holds an exclusive `flock` on
  `<registry>.lock`, a sibling file created on first use, from load through
  save, event append and the `--commit` step, so 8 parallel `claim`s have one
  winner and parallel `update`s lose nothing (`search --record` and
  `index add` lock only around their event append). `save_registry` writes a
  private temp file in the registry's directory, fsyncs it and replaces the
  registry atomically, keeping the old file's mode (no shared `.tmp`). An
  event line is appended only after the registry that records it is saved,
  in one write + fsync, so a failure in between leaves no orphan event.
  `audit --fix` reports an unparsable lease as `lease-invalid` instead of a
  traceback. An unexpected exception still lets `--commit` report (and not
  commit), then exits 1 with `lean-pkg: internal error: <type>: <msg>`
  (`{"ok": false, "error": ...}` under `--json`; traceback with
  `LEAN_PKG_DEBUG=1`). The TUI re-reads the registry under the lock before
  acting and refuses a TTL that is not a positive whole number instead of
  defaulting it to 24. Add `lean-packages.yaml.lock` to the registry
  repository's `.gitignore`. (#14)
- A hand-broken lease (`claimed_at: zzz`, `ttl_hours: 'x'`) no longer crashes
  `list`, `board`, `leases`, `claim`, `release`, `renew`, `update` or
  `doctor`: tables show `?` / `INVALID` in the lease cells, `leases` lists it
  as invalid (`--json`: `"invalid": true`, `expires_at` and
  `remaining_seconds` null), `doctor`'s `leases` line warns, and the writers
  refuse with `lease fields unparseable (claimed_at=..., ttl_hours=...)`;
  `release NAME --force --reason TEXT` clears it. (#18)
- `doctor` runs each check on its own: one that raises prints
  `warn <item>: internal error ...` and the rest still print. A missing
  binary is exit 127 from the subprocess helper (`promote` without `gh`
  reports `gh not on PATH`), `wiki --out` to an unwritable path fails with
  `cannot write <path>: <reason>`, and any other `OSError` is a one-line
  refusal (exit 1) rather than an internal error. (#18)
- Committed mode covers every write: `index add`, `formalization --write`,
  `claims sync`, `search --record` and the TUI's claim/release/gate keys
  (each committed after the write, inside the lock). One predicate,
  `is_write_invocation`, decides from the parsed arguments both the registry
  lock and committed mode, so dry runs (`register`/`update`/`migrate
  --dry-run`, `promote` without `--yes`, `audit` without `--fix`,
  `comparator --check`) never fetch or pull. `--commit`/`--push` on a
  read-only command print `ignored: read-only command` on stderr;
  `--no-commit` is accepted after the subcommand; a `--json` payload gains
  `"commit": {"sha", "pushed", "error"}` and says `"ok": false` when the
  commit or push failed; the fallback identity is reported on stderr. (#18)
- An explicit `--registry` uses its sibling event log even when
  `LEAN_PKG_EVENTS` is set (precedence: `--events`, the sibling of
  `--registry`, `LEAN_PKG_EVENTS`, the sibling of the resolved registry), so a
  scratch registry never writes into a shared log; `doctor`'s `events` line
  ends `[source: flag|registry-sibling|env|sibling]`. (#18)
- `formalization NAME --write` checks the lease and dry-runs the registry
  update before it writes `formalization.yaml`, so a refusal leaves the file
  untouched; `--force --reason TEXT` overrides another agent's live lease
  (`--force` alone still only overwrites a differing file). (#18)
- The index root is `--index`, else `LEAN_PKG_INDEX`, else `index/` beside
  the registry when it exists, else `./index`, so `doctor`'s `index` line
  (now ending `[source: ...]`) no longer changes with the working directory.
  `index check`, `render` and `migrate` exit 1 with `no index at <path>`
  when the root is missing (`render` creates it only for an explicit
  `--index` or `--out`), and `index migrate` on an empty index says so. (#18)

- `gate` and `comparator` compared a receipt's `lean_toolchain` with the
  working-tree `lean-toolchain`, so an uncommitted edit could make a receipt
  pass. Both now read `lean-toolchain` at the receipt's bound commit and the
  refusal names both values. Modified tracked files in the checkout print a
  stderr WARN (`dirty` in `--json`); the new `--refuse-dirty` flag refuses
  instead. (#15)
- The `promote` checklist passed with untracked or unpushed evidence. It now
  also requires the gate receipt, the comparator receipt and every comparator
  log it names to be committed at HEAD, unmodified, and HEAD to be contained
  in `origin/<branch>`, judged on local refs (nothing is fetched). Under
  `--yes`, a failed `gh` visibility query is a ✗, not a WARN. The comparator
  line now says what was checked (config and log bytes match the receipt,
  PASS line present) and no longer says "nanoda replay". (#15)
- A receipt's `generated_at`, and a comparator receipt's, must now parse as an
  ISO-8601 timestamp; being non-empty is not enough. The stored receipt name
  now drops leading dots and dashes from the job id, so `../../evil` is stored
  as `<c12>-evil.json`, and a job id left with no usable characters is
  refused. (#15)
- `register --status` only accepts `scratch`, `active` and `gate-pending`.
  `public` is accepted only together with `--visibility public` and a
  non-empty `--reason` (the reason is stored on the `registered` event);
  `verified-private` and `upstreamed` are refused. `validate` now refuses a
  `verified-private` package with no `gate.receipt`. A status/visibility
  mismatch (`verified-private` but not private, `public`/`upstreamed` but not
  public) is reported by `audit` as `status-visibility-mismatch` rather than
  refused by `validate`, because existing registries hold such rows. (#15)
- One `local_path` normaliser for `register`, `update --path` and
  relocation: a leading `~/` is expanded against `$HOME` (`~user` is
  refused), a relative path is taken relative to `$HOME`, and duplicates
  and collisions are found by comparing the resolved absolute paths, so
  `register --path $HOME/pkgs/alpha` no longer adds a second row on a
  checkout stored as `pkgs/alpha`. The stored form is `$HOME`-relative
  (absolute only outside `$HOME`), without a trailing slash. An empty or
  whitespace `--path`, or one resolving to `$HOME` itself, is refused
  instead of auto-detecting `$HOME/lean-toolchain`. (#17)
- Empty or whitespace values for `--repo` and `--owner-project` are refused
  by `register` and `update` (exit 1); `update --notes ''` still clears the
  notes. `update --license-spdx` accepts SPDX expressions (identifiers
  joined by `AND`/`OR`, `WITH` an exception, parentheses), such as
  `MIT OR Apache-2.0`. (#17)
- A missing `--reason` is always a refusal: `update --force` and
  `update --status` without one now exit 1 with `{"ok": false, "error": ...}`
  under `--json` (were usage errors, exit 2, no JSON). Usage errors print
  `{"ok": false, "error": ..., "usage": true}` under `--json` (exit stays 2).
  `release --force` requires `--reason` (the TUI asks for one) and records
  `released_by` as the acting `--agent` when given, else `force`, with the
  reason. `update --path` to the current checkout is a no-op ("already at
  <path>; nothing written", exit 0; was exit 1). `llms --json` prints
  `{"ok": true, "text": ...}`. (#17)
- A holder's re-`claim` without `--ttl` keeps the lease's TTL (it reset a
  72 h lease to 24 h), as `renew` does; a new claim still defaults to 24 h.
  `claim-refreshed` records the `reason` when one is given. (#17)
- `init` records only the registry file name and the schema version in the
  shared event log (it wrote this machine's absolute path), and
  `init --force` marks its `registry-created` event `"forced": true`
  (`--json` adds `forced`). (#17)

## [0.5.0] - 2026-10-08

### Added

- `lean-pkg doctor` also reports `lean`, `go`, `cargo` and `jq`, and warns
  when the `lake` or `lean` on PATH is not elan's proxy (`$ELAN_HOME/bin`,
  default `~/.elan/bin`), since such a binary ignores `lean-toolchain`.
- Index: `markovprocess-pilot` re-pinned at `9bb873e7aa31` after its history
  was cleaned and the repository made public; fresh lower-tier and comparator
  receipts (toolchain mechanism). Every index entry is now public and passes
  `index check --clone`.
- Decision 0006 (proposed): the public-flip gates G1 to G7 with their
  evidence and what is still open.
- `docs/existing-package-comparator.md`: a section for packages that forbid
  `sorry`, set `warningAsError`, or run `mk_all --check` in CI: a scoped
  Challenge exception with a tested placeholder scan, the `Audit`
  `leanOptions` override, and `mk_all --check --lib`.
- Index: `total-coloring-lean` re-pinned at `533f9430cb45` with a comparator
  receipt for both headline theorems (legacy mechanism); trust is now
  `comparator`.
- `scripts/bootstrap-collaborator.sh OWNER/PACKAGE`: one-command collaborator
  setup (machine and GitHub access checks, pipx install, checkouts under
  `~/src`, a committed personal registry with the package registered, Codex
  skills, `doctor`), idempotent, ending READY or NOT READY; tested offline
  under a fake HOME. It is step 0 of the comparator guide, which also gains a
  note on starting the setup from Codex.
- `docs/existing-package-comparator.md`: step-by-step guide to adding the
  comparator tier to a package that already builds, for a collaborator who
  knows little Lean.
- `docs/comparator.md`: how to write the Challenge and the Solution, a
  checklist against weakened statements, and what a package below the
  toolchain floor gets.
- `docs/getting-started.md`: installing while the repository is private,
  Linux-only sandboxing and disk needs, and the `lean_lib` entries the
  Challenge and Solution modules need.
- `docs/stranger-test.md`: who may run the gate G1 stranger test, what counts
  as success, and the transcript template.
- `lean-pkg formalization NAME [--write] [--out PATH] [--force] [--author NAME]`:
  render the package's `formalization.yaml` (mathlib-initiative schema v0.4,
  the file Palomar reads) from the registry and the repository at HEAD.
  Derived keys are regenerated every run (`project.name`, `project.license`,
  `automation.notes`, `status.sorry_count`/`axioms`/`evidence` from the gate
  receipt, `status.main_results` from every tracked comparator configuration
  with `judged_as` carried for definition holes, `alignment` from
  `claims.yaml`); every other key is kept from the existing file, seeded on
  first render from registry data (authors from `consent[]` owners/authors,
  Mathlib `builds-on`) or with a marked `[PLACEHOLDER]` and a stderr warning.
  Nothing is invented: the registry holds no paper metadata, so `sources` is
  a placeholder until a human fills it. `--write` is idempotent, refuses a
  differing file without `--force`, and sets `formalization_yaml` through the
  `update` path. Validated against the vendored v0.4 JSON schema
  (`src/lean_pkg/schemas/`, upstream commit `99c678e5`, fetched 2026-10-07)
  with `jsonschema` when importable, else a structural check (docs/formalization.md).
- `lean-pkg audit`: `formalization-missing` and `formalization-drift`
  findings for packages whose `formalization_yaml` is set (the file no longer
  equals what the generator emits); `audit` never rewrites it.

- Definition holes need a human sign-off: a comparator configuration with a
  non-empty `definition_names` is refused unless it carries
  `judged_as {reviewer, date, definitions ⊇ definition_names, basis?}` naming
  a human (model/agent tokens refused). Enforced by `comparator-check.sh`, by
  `comparator-receipt` before anything is built (nothing written), and by
  every validator against the file at the bound commit. The runner copies
  `judged_as` into the receipt entry when present (optional field; old
  receipts still validate). Rationale: comparator compares holes by name and
  type only, never by body (`src/lean_pkg/holes.py`, docs/comparator.md).
- `comparator-check.sh --configs <glob>` (repeatable discovery override) and
  `--lint-configs` (configuration rules only, jq-only, no build).
- `comparator-check.sh`: `LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1` with
  `LEAN4EXPORT_COMMIT` builds lean4export under the project toolchain in a
  dedicated checkout and verifies the binary's reported Lean version, for
  point releases lean4export never tagged (v4.34.1); the deviation is printed
  on its own `== lean4export toolchain override:` line.

- `scripts/private-boundary-check.sh --history` and `make history`: scan every
  blob and commit message reachable from any ref (the public-flip prerequisite).
- REUSE 3.3 compliance: `REUSE.toml`, `LICENSES/Apache-2.0.txt`, `make reuse`
  (part of `make check`; skipped with a notice when the tool is absent).

- `lean-pkg gate` and `lean-pkg comparator` scan the receipt bytes for
  private local paths (the `index add` scan) before copying them into
  `.lean-receipts/`: each hit is a stderr `WARN` naming the field and the
  kind of string (never the value) and an entry of `"private_strings"` in the
  JSON payload. `--refuse-private` turns the warning into a refusal (exit 1,
  nothing copied). Mapping keys are scanned as well as values; a private key
  is labelled by position (`receipt.artifacts.<key#0>`), never by its text
  (also in the `index add` scan).

- `lean-pkg audit --toolchain-floor VERSION` (env `LEAN_PKG_TOOLCHAIN_FLOOR`,
  default `leanprover/lean4:v4.35.0-rc2`, Palomar's floor): a
  `below-toolchain-floor` finding for every package whose `lean_toolchain` is
  older, except `scratch` and `archived` ones. Versions compare as
  `vMAJOR.MINOR.PATCH[-rcN]` with an rc older than its release; nightly and
  other strings are reported as `toolchain-unparsed`, never compared.

- `lean-pkg --version` prints `lean-pkg <version>` and exits 0.
- `lean-pkg doctor [--json]`: one `ok`/`warn`/`n/a` line each for the
  version, the install (an editable checkout, one whose git index tracks the
  package, adds short HEAD, branch, and behind/ahead counts against `origin/<branch>` from local refs only, never
  fetched), the registry and its schema version, the event log, the index and
  its entry count, `lake`/`elan`/`git`/`gh`/`reuse`/`bwrap`/`comparator`/`nanoda`
  on PATH, and the Python version. Exit 1 when the registry is unreadable or
  its schema version is unsupported, else 0.

### Changed

- Copyright notice is now "Copyright (c) 2026 Le Chen and Auburn University",
  confirmed with Auburn IPX for release under Apache-2.0 (decision 0004);
  new `NOTICE` file.
- Until a deputy is named, succession is covered by the maintainer's GitHub
  account successor (decision 0005); `MAINTAINERS.md` points to it.
- Decision 0003 accepted (rewrite history once before the public flip);
  `scripts/rewrite-history.sh` performs and verifies the rewrite.
- The repository moved to the `lean-pkg` organization:
  <https://github.com/lean-pkg/lean-pkg>, documentation at
  <https://lean-pkg.github.io/lean-pkg/>. GitHub redirects the old repository
  URL; the old documentation URL does not redirect.
- The vendored `scripts/comparator-check.sh` now carries the lab additions
  above and is no longer byte-for-byte the Palomar verifier script; tool pins
  are unchanged.

### Fixed

- Packages whose `local_path` is a subdirectory of their git repository
  (`lean/`, `lean/contrib/`): the evidence-only-descendant rule compared
  repository-root paths with `.lean-receipts/`, so `lean/.lean-receipts/...`
  never matched and any later commit, even a paper edit outside the package,
  was drift for `gate`, `promote` and `audit`. Paths are now judged relative
  to the package directory: only changes under it count, and within it only
  `.lean-receipts/` is evidence (DESIGN.md).

- `make test` runs pytest with a private bytecode cache
  (`PYTHONPYCACHEPREFIX` in a fresh `mktemp -d` directory, removed
  afterwards by an EXIT trap, also on Ctrl-C; `-p no:cacheprovider` kept), so concurrent runs no longer share
  pytest's rewritten `.pyc` files (a segmentation fault inside
  `_pytest/assertion/rewrite.py` was observed with two sessions at once). The
  gate is safe to run concurrently (RELEASING.md).

- `lean-pkg audit` no longer prints one `warn` line per package whose `repo`
  is not a GitHub URL (the line echoed the repo path, which may be private).
  Each such package gets a `no-github-mirror` finding with the fixed detail
  "repo is not on GitHub; visibility not mirrored"; the human output collapses
  them into one line "N package(s) without a GitHub mirror (visibility not
  checked): a, b, c", and `--json` keeps one finding per package. `gh query
  failed` warnings are unchanged. Check names longer than the column (such as
  `below-toolchain-floor`) are now separated from the package name by a space.

- `lean-pkg audit` judges `receipt-drift` and `comparator-drift` against the
  package's reference branch (the remote default branch from
  `refs/remotes/origin/HEAD`, else `main`, else `master`, else HEAD; local refs
  only; when the local branch and `origin/<b>` both exist, the tip that contains
  the other, and origin with a note when they diverged), not
  the checkout's HEAD, so a checkout parked on a feature branch no longer
  reports false drift. The evidence-only-descendant rule is unchanged. When
  the checkout is on another branch, a `checkout-branch` finding says
  "checkout on X; judged against B" and drift details carry the same note.

- Prior-art record slugs longer than 60 characters get an 8-hex suffix of the
  full slug, so two long terms sharing a prefix no longer share a record file.

- `index migrate [--check]`: rewrite `index-entry/1` entries as `index-entry/2`
  with `claims` derived from the headline; the two first entries migrated.

## [0.4.1] - 2026-10-06

### Added
- `update --license-spdx SPDX` and `update --formalization-yaml REL` set the
  registry row's publication facts (previously only `register` could).
- Index: third entry `total-coloring-lean` (lower-tier at 55edc814; the
  example of an unfinished project: two proved headlines, goal and gaps in
  notes).
- Docs: how an unfinished project is listed honestly (headlines are what is
  proved, goals and gaps in `notes`, partial results as `experimental` claims,
  progress as a new pin), with `total-coloring-lean` as the example; FAQ entry.

## [0.4.0] - 2026-10-06

Claim-level prior-art search (issue #4): search before building, across the
group's own packages and the public sources, with a citable record.

### Added
- `claims.yaml` (schema `claims/1`) at a package repository root: named,
  reusable results with stable ids `<package>/<slug>`, kinds, statuses,
  aliases, keywords, supersession (acyclic) and dependencies.
- `lean-pkg claims sync NAME` reads the file at `HEAD`, checks every
  declaration occurs in its file, and caches the claims with the commit in
  the registry (event `claims-synced`); `claims list` and `claims check`.
- `lean-pkg search TERM`: registry claims, the public index, Loogle,
  LeanSearch, Reservoir (the index data, cached by commit, never the
  name-only site search), Palomar (results API, cached by revision) and
  GitHub, in that fixed order; every source `ok` or `skipped: <reason>`,
  never zero hits when unreachable; `--offline`, `--sources`, `--json`.
- `lean-pkg search --record` writes `docs/prior-art/<date>-<slug>.md` in the
  research repository (`decision: pending` for a human) and one
  `prior-art-searched` event; claims of other private packages are listed
  only in a private package's record, marked `private-claims: yes`.
- Index: `index-entry/2` adds `claims[]`, derived from the headline at
  `index add`; `index check` refuses claims that drift from the headline;
  `index render` lists them. `index-entry/1` entries stay valid.
- Advisory notes: `claim` when a package has no recent search and no claims,
  `index add` when no record at the pin names a headline.
- Docs page `docs/prior-art.md`.

### Changed
- Registry version 3: `claims_file`, `claims`, `claims_synced_commit`
  (additive; `lean-pkg migrate` moves v1 and v2 files forward; both stay
  readable).

### Fixed (fresh-model review of the branch)
- A record never says "Not found in registry" when registry hits were
  withheld for privacy; the Hits cell says `0 shown (+N withheld)`.
- Search terms are folded (accents stripped, any script's letters count):
  `Itô` matches `Ito`; `ε` and `黎曼` are valid terms.
- `--repo-dir` outside the package's own checkout is treated as an unknown
  target, so other private packages' claims are withheld.
- Skip reasons are scrubbed of local paths before they reach output or a record.
- The record is created atomically and never follows a symlink; a malformed
  registry entry makes the registry source `skipped: unreadable` instead of a traceback.

## [0.3.2] - 2026-10-06

### Added
- Index: `markovprocess-pilot` now at comparator trust; its headline theorem
  is judged through a Mathlib-only Challenge (`exit_mean`) by `lake comparator`.

### Fixed
- `COMPARATOR_RECEIPT_HOST_LABEL` now also replaces the host name in the
  receipt's `host_policy` sentence (a copied receipt leaked it).

## [0.3.1] - 2026-10-06

### Changed
- Index: `comparator` trust requires coverage. Each headline item may carry
  `judged_as`, the Challenge theorem name; comparator trust needs every
  headline judged by a configuration in the receipt, else the entry is
  lower-tier (with a lower-tier receipt) or refused.
- The comparator PASS predicate is the full line `Your solution is okay!`
  for both mechanisms, as observed in the first live `lake comparator` run
  (2026-10-06); the kernel acceptance phrase alone no longer counts.
- The sample workflow page says which steps were run and which are
  illustrative; it shows the live toolchain run.

## [0.3.0] - 2026-10-06

The index schema (milestone M1's catalog layer, pulled forward): a public,
forkable list of trust-graded packages whose every row can be re-derived
from bytes at its pin.

### Added
- `index/<name>/entry.yaml`, schema `index-entry/1` (keys in a fixed order;
  unknown keys refused; no local paths), with verbatim receipt copies under
  `index/<name>/receipts/`. The index root is `--index PATH`, env
  `LEAN_PKG_INDEX`, or `./index`.
- `lean-pkg index add NAME`: builds an entry from the registry row
  (`--from-registry`) or from flags, reads the receipts from a clone at the
  pin, validates them with the `gate` and `comparator` validators, computes
  the trust level (`lower-tier`, `comparator`, `palomar`), refuses a
  duplicate repository and commit, and appends `index-added` to the event log.
- `lean-pkg index check [NAME ...] [--clone | --repo-dir PATH]`: static
  checks, or a re-derivation from bytes at the pin (receipts, logs,
  toolchain, `lake-manifest.json`, license, tag, headline files) with trust
  recomputed; Palomar ids and Reservoir listings are verified by API when
  reachable and reported as skipped otherwise. Exit 0, 1 or 2; `--json`.
- `lean-pkg index render [--out PATH] [--check]`: the Markdown table
  `index/README.md`, with a do-not-hand-edit banner; a test keeps it current.
- Docs: [The index](docs/index-catalog.md); CONTRIBUTING, the pull-request
  template and the index-entry issue template use the schema's field names.
- First index entry: `gromov-filling-lean` (lower-tier; Lean v4.29.0; pin
  1c4ccb9c; receipt produced on the Auburn University Easley Cluster).
- Safety of the index (from the fresh-model review): evidence files are
  written with symlinks refused at the pin and on disk; a rename into
  `.lean-receipts/` is no longer evidence-only (`gate` benefits too); a
  Palomar id grades an entry only at its registered commit and `check`
  compares the Palomar entry's source with the index entry; `index add`
  verifies that the pin and evidence are on a public branch unless
  `--allow-unpushed`; receipt copies with local-path strings are refused;
  `COMPARATOR_RECEIPT_HOST_LABEL` gives receipts a public-safe host; `render`
  fails cleanly on a malformed entry; one broken entry no longer blocks
  checking another; the private-boundary check is case-insensitive.

- `LEAN_PKG_EVENTS`: environment variable for the event-log path (after
  `--events`, before the registry's sibling file).

### Fixed
- `lean_pkg.__version__` now agrees with `pyproject.toml` (it still read 0.1.0).
- Documentation site: complete dark-mode palette (header, links, syntax
  colours, tables, footer, search, copy buttons) and light-mode contrast,
  measured to WCAG AA on every page; the workflow diagram is a light card in
  both themes.
- A comparator receipt produced under the test-only digest override now
  carries `test_only: true` and is refused by every validator.
- The toolchain probe requires the judging binaries to be executable, as the
  vendored script does.


## [0.2.0] - 2026-10-06

Milestone M0 of the charter: a tool the lab can consume instead of its own
copy. Checks were run locally with `make check` (hosted CI turns on at the
public flip).

### Added
- `lean-pkg init`: bootstrap an empty registry and its event log; refuses an
  existing registry unless `--force` and it holds no packages.
- Registry schema v2 and `lean-pkg migrate`: additive publication fields per
  package (`license_spdx`, `formalization_yaml`, `palomar[]`, `doi`,
  `reservoir`, `consent[]`); v1 registries stay readable by every command;
  `migrate` is idempotent and has `--dry-run`.
- `lean-pkg record NAME EVENT --field K=V`: evidence events with required
  fields and registry effects (`consent-recorded`, `palomar-preflight-pass`,
  `palomar-submitted`, `palomar-review`, `palomar-registered`,
  `palomar-withdrawn`, `doi-minted`, `reservoir-listed`, `mathlib-pr-opened`);
  `validate` refuses a DOI whose commit no Palomar entry names; `audit` finds
  `palomar-preflight-missing`.
- `comparator-receipt --mechanism auto|toolchain|legacy`: the toolchain
  mechanism runs `lake comparator` with the toolchain's bundled NanoDa and
  con-ron through the vendored PalomarTemplate `verify-comparator.sh`
  (Lean v4.35.0-rc2 or later, what Palomar runs); legacy keeps the pinned
  stack. Receipt schema `comparator-receipt/2` with `mechanism` and per-
  mechanism `tools`; schema 1 still validates.
- `gate` on an already-public package records the receipt and the
  `gate-passed` event without changing the status.
- `record NAME reservoir-listed` verifies the listing against Reservoir's
  package API (a 404 refuses; an unreachable network records `verified: false`).
- The toolchain mechanism checks that `bwrap` can create a user namespace
  before judging and names the Ubuntu 24.04 AppArmor fix when it cannot.
- Governance: `SECURITY.md`, issue and pull-request templates with a required
  AI-assistance line, `docs/decisions/` (0001 files and git only; 0002
  benevolent maintainer with deputies), deputy and succession lines in
  `MAINTAINERS.md`.
- Private-boundary check scans only files git would ship.
- Agent skill `search-before-building`: Loogle, Mathlib at the pin, the
  Reservoir index (not the name-only site search), Palomar, known authors and
  the GitHub long tail, with every query recorded and one decision per need;
  step 0 of `lean-paper-companion` and a row in the router.
- FAQ section on Reservoir: how it differs from lean-pkg and Palomar, why
  there is nothing to register (listing is automatic), and searching it
  before formalizing.

## [0.1.0] - 2026-10-05

First tagged release: the tool extracted from its private lab origin into its own
repository, with the comparator tier and the documentation site. The
repository is private at this version; checks were run locally (see
RELEASING.md).

### Added
- `lean-pkg` registry CLI: statuses `scratch → active → gate-pending →
  verified-private → public → upstreamed`, leases (`claim`/`release`),
  lower-tier receipts (`lake build`, sorry scan, `#print axioms`) bound to a
  commit, `gate`, `promote` with a dry-run checklist and a one-way `--yes`,
  `audit`, `llms` contract output.
- Comparator tier: `comparator-receipt` runner wrapping the vendored Palomar
  `comparator-check.sh`; all-or-nothing evidence write; host policy;
  `lean-pkg comparator --receipt [--check]`; `gate --comparator`;
  audit findings `comparator-drift` and `comparator-missing`.
- Evidence-only descendant rule: a receipt bound to commit X is accepted at
  HEAD H when H descends from X and only `.lean-receipts/` paths differ.
- Eighteen agent skills under `skills/` with an installer for Claude Code
  and Codex (`scripts/install_skills.py`).
- Documentation site (`docs/`, Jekyll Cayman): motivation, getting started,
  how-to, lifecycle, receipts, comparator, CLI, agents, newcomers, sample
  workflow with a seven-step diagram, FAQ, skills; client-side search and a
  dark/light toggle; `llms.txt`.
- Private-boundary check (`scripts/private-boundary-check.sh`) and a test
  suite of 240 tests.
- Secretary-bird logo and favicon; `CITATION.cff`.
