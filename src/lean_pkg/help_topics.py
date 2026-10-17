# lean-pkg help topics -- `lean-pkg help exit-codes|environment|formatting`
"""Help topics in the manner of `gh help <topic>`: reference text that belongs to no one command.

Pure data and rendering: nothing here reads the registry, the environment or the network. The
environment table is checked against the source by tests/test_help_topics.py, which fails when a
variable is read anywhere under src/lean_pkg or scripts/ but is not listed in ENVIRONMENT.
"""

from __future__ import annotations

import textwrap

WIDTH = 100

# --------------------------------------------------------------------------
# environment

# (section, [(VARIABLE, meaning, default)]). Every variable lean-pkg, comparator-receipt, lean-exercise, the
# scripts they run and the repository's maintainer scripts read. A flag always wins over its variable.
ENVIRONMENT: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("LEAN-PKG: REGISTRY, EVENT LOG, COMMITTED MODE", [
        ("LEAN_PKG_REGISTRY",
         "the registry file, or a directory holding lean-packages.yaml; --registry wins",
         "~/.lean-pkg/registry when it exists, else ./lean-packages.yaml"),
        ("LEAN_PKG_HOME",
         "base directory of the user-level default registry (<base>/registry)",
         "~/.lean-pkg"),
        ("LEAN_PKG_EVENTS",
         "the event log; --events wins, and so does lean-package-events.jsonl beside an explicit --registry",
         "lean-package-events.jsonl beside the registry in use"),
        ("LEAN_PKG_REGISTRY_SNAPSHOT",
         "run read-only against the registry as of this commit, like --registry-snapshot (the flag wins); "
         "every write exits 2",
         "unset (the working-tree registry)"),
        ("LEAN_PKG_COMMIT",
         "1/true/yes/on: committed mode for every write (pull --ff-only, write, commit the registry and "
         "event log together); --no-commit overrides it",
         "off"),
        ("LEAN_PKG_PUSH",
         "1/true/yes/on: committed mode and a push to origin after each write; --no-commit overrides it",
         "off"),
        ("LEAN_PKG_PUSH_RETRIES",
         "retries of a push that failed with a transient transport error (reset, timeout, HTTP 5xx)",
         "4"),
        ("LEAN_PKG_PUSH_RETRY_DELAY",
         "seconds before the first push retry, doubled for each later one",
         "5"),
        ("LEAN_PKG_DEBUG",
         "1/true/yes/on: print the Python traceback of an internal error on stderr",
         "off (a one-line `internal error: ...`)"),
        ("LEAN_PKG_NO_ALIAS_NOTE",
         "1/true/yes/on: no `note: 'lean-pkg OLD' is now 'lean-pkg NEW'` line on stderr when an old spelling "
         "is used (old spellings are permanent aliases; --json never prints the note)",
         "off (one note per old spelling used, text mode only)"),
        ("LEAN_PKG_NOTIFY_CMD",
         "command run (split shell-style, no shell) with a notification on stdin when `promote --yes` "
         "makes a package public",
         "unset (no notification)"),
    ]),
    ("LEAN-PKG: INDEX, AUDIT, SEARCH", [
        ("LEAN_PKG_INDEX",
         "root of the public index catalog (index commands); --index wins",
         "index/ beside the registry when present, else ./index"),
        ("LEAN_PKG_TOOLCHAIN_FLOOR",
         "the lowest Lean toolchain `audit` and `next` accept; --toolchain-floor wins",
         "leanprover/lean4:v4.35.0-rc2"),
        ("LEAN_PKG_GZIP_LIMIT",
         "decoded bytes of one gzip file or blob that `public-safety` (and promote, publish, release "
         "--fresh-repo) decodes and scans; K, M, G suffixes; 0 turns decoding off; --gzip-limit wins",
         "512M"),
        ("LEAN_PKG_RESERVOIR_INDEX",
         "an unpacked Reservoir index directory, used offline by `search` and `reservoir check`",
         "the index tarball fetched with gh into the cache"),
        ("XDG_CACHE_HOME",
         "base of the cache directory <base>/lean-pkg (Palomar snapshots, Reservoir index); "
         "--cache-dir wins",
         "~/.cache"),
        ("TERM",
         "`board` opens the curses TUI only on a terminal whose TERM is not `dumb`",
         "the terminal's"),
        ("USER",
         "a user name `receipt` refuses or warns about when it appears in a receipt (with LOGNAME and "
         "the login name)",
         "the login's"),
        ("LOGNAME",
         "as USER",
         "the login's"),
    ]),
    ("COMPARATOR-RECEIPT", [
        ("COMPARATOR_RECEIPT_ALLOWED_HOSTS",
         "comma-separated host names or labels allowed to run outside Slurm (host policy, recorded in "
         "host_policy by the label)",
         "none (only a Slurm job may run)"),
        ("COMPARATOR_RECEIPT_ALLOW_HOST",
         "1: allow this host whatever its name (an override, visible in the receipt's host_policy)",
         "unset"),
        ("SLURM_JOB_ID",
         "inside a Slurm job: the host policy allows the run and the receipt's job_id is this value",
         "job_id local-<host>-<pid>-<time>"),
        ("COMPARATOR_RECEIPT_HOST_LABEL",
         "label that names the host in the receipt and in the runner's output (the host name never does)",
         "h- + 12 hex of the SHA-256 of the lower-cased host name"),
        ("COMPARATOR_CHECK",
         "comparator-check.sh to run; --check wins",
         "the copy shipped inside the lean_pkg package"),
        ("COMPARATOR_VERIFY",
         "verify-comparator.sh for the toolchain mechanism; --verify wins",
         "the copy shipped inside the lean_pkg package"),
        ("COMPARATOR_LAKE",
         "the lake the setup stages run",
         "lake from elan's bin/, else from PATH"),
        ("COMPARATOR_BWRAP",
         "lean-exercise: the real bwrap its --project wrapper and its --forbid export sandbox run",
         "bwrap on PATH"),
        ("ELAN_HOME",
         "elan's directory: toolchains and bin/ (comparator-receipt, lean-exercise, the types driver)",
         "~/.elan"),
        ("COMPARATOR_RECEIPT_STACK",
         "unlimited or a size in kB: the soft stack limit of the lake/lean children",
         "the inherited limit"),
        ("COMPARATOR_RECEIPT_LEAN_THREADS",
         "a positive integer: LEAN_NUM_THREADS for the lake/lean children",
         "unset (Lean's own default)"),
        ("COMPARATOR_RECEIPT_BUILD_SOURCE",
         "free text naming where the package's oleans were built; recorded in build_provenance",
         "unset"),
        ("COMPARATOR_RECEIPT_NO_MATHLIB",
         "1: the project knowingly has no Mathlib (the toolchain mechanism refuses such a project "
         "otherwise, and with it `lake exe cache get` is skipped)",
         "unset"),
        ("LEAN_PKG_COMPARATOR_JOBS",
         "judge processes run at once; --judges wins",
         "1"),
        ("LEAN_PKG_COMPARATOR_UNIT_SIZE",
         "theorem names per judge unit; --unit-size wins",
         "the whole configuration, cut into --judges shards"),
        ("COMPARATOR_HEARTBEAT_SECONDS",
         "seconds between heartbeat lines while a judge is silent",
         "60"),
        ("COMPARATOR_FAKE_LAKE_OK",
         "tests only: 1/0 replaces the toolchain probe; a receipt written with it carries test_only: true",
         "unset"),
        ("VERIFY_COMPARATOR_SKIP_CACHE_GET",
         "verify-comparator.sh: 1 skips `lake exe cache get` (the runner sets it for a project without "
         "Mathlib)",
         "unset"),
    ]),
    ("COMPARATOR-CHECK.SH (THE SCRIPT COMPARATOR-RECEIPT RUNS)", [
        ("COMPARATOR_COMMIT",
         "commit of leanprover/comparator to build",
         "the pinned commit in the script"),
        ("NANODA_COMMIT",
         "commit of the nanoda kernel checker to build",
         "the pinned commit in the script"),
        ("LANDRUN_COMMIT",
         "commit of landrun to build",
         "the pinned commit in the script"),
        ("LEAN4EXPORT_COMMIT",
         "commit of lean4export to build",
         "the release tag named after the project's Lean version"),
        ("LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH",
         "1, with LEAN4EXPORT_COMMIT: build that source under the project's toolchain (point-release gap)",
         "0"),
        ("TOOLS_DIR",
         "where the checker tools are cloned and built",
         "~/.cache/lean-ci-tools"),
        ("LOG_DIR",
         "where the comparator logs go (comparator-receipt sets it to its stage-log directory)",
         "./.lake/comparator-logs"),
    ]),
    ("COMPARATOR TYPES DRIVER (WRITTEN BY `comparator config --challenge-from-type` / `--emit-script`)", [
        ("LEAN_PKG_TYPES_JOBS",
         "Lean processes the driver runs at once",
         "the driver's configured jobs, else min(2, the CPUs this process may use)"),
        ("LEAN_PKG_TYPES_TIMEOUT",
         "seconds per declaration",
         "the driver's configured timeout"),
        ("LEAN_PKG_TYPES_BACKOFF_SECONDS",
         "pause before a retry with fewer workers after `too many open files`",
         "5"),
        ("LEAN_PKG_TYPES_RESUME",
         "0: ignore OUT.json.checkpoint.jsonl and run every declaration again",
         "1 (resume from a matching checkpoint)"),
    ]),
    ("SHARED", [
        ("HOME",
         "the home directory: `~` in paths, the user-level registry, and the prefix redacted to <home>/~ "
         "in stage logs, receipts and reports",
         "the login's"),
        ("PATH",
         "where git, gh, lake, elan, jq and bwrap are found; comparator-receipt puts elan's bin/ first "
         "for its children",
         "the caller's"),
        ("TMPDIR",
         "temporary files; the prefix redacted to <tmp> in stage logs; lean-exercise's work directory",
         "/tmp"),
        ("XDG_STATE_HOME",
         "lean-exercise's work directory when TMPDIR is unset",
         "the system temporary directory"),
        ("GIT_DIR",
         "repository-selecting Git variables (also GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, "
         "GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_COMMON_DIR, GIT_NAMESPACE): removed from every git call on "
         "a package checkout; comparator-receipt's host check warns when one is set",
         "unset"),
        ("GIT_WORK_TREE", "as GIT_DIR", "unset"),
        ("GIT_INDEX_FILE", "as GIT_DIR", "unset"),
        ("GIT_OBJECT_DIRECTORY", "as GIT_DIR", "unset"),
        ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "as GIT_DIR", "unset"),
        ("GIT_COMMON_DIR", "as GIT_DIR", "unset"),
        ("GIT_NAMESPACE", "as GIT_DIR", "unset"),
    ]),
    ("MAINTAINER SCRIPTS (scripts/ IN A CHECKOUT; NOT INSTALLED)", [
        ("LEAN_PKG_RELEASE_RETRIES",
         "release.sh, cut-release.py, record-doi.py: attempts per network step",
         "4"),
        ("LEAN_PKG_RELEASE_RETRY_DELAY",
         "seconds before the first retry of a release step, doubled for each later one",
         "10"),
        ("LEAN_PKG_COMMIT_TRAILER",
         "cut-release.py, record-doi.py: `Key: value` trailers, one per line, for the commits they make",
         "none"),
        ("LEAN_PKG_RELEASE_DATE",
         "cut-release.py: the YYYY-MM-DD date to stamp in CHANGELOG.md and CITATION.cff (tests only)",
         "today's local date"),
        ("LEAN_PKG_ZENODO_API",
         "record-doi.py: the Zenodo API base",
         "https://zenodo.org/api"),
        ("LEAN_PKG_ZENODO_RECORDS_FILE",
         "record-doi.py: read the Zenodo search pages from JSON files instead (tests)",
         "unset"),
        ("LEAN_PKG_SRC",
         "bootstrap-collaborator.sh: checkout directory, under $HOME",
         "~/src"),
        ("LEAN_PKG_REGISTRY_DIR",
         "bootstrap-collaborator.sh: registry clone directory",
         "~/.lean-pkg/registry"),
        ("LEAN_PKG_GIT_BASE",
         "bootstrap-collaborator.sh: clone URL base",
         "https://github.com"),
        ("PIPX_BIN_DIR",
         "bootstrap-collaborator.sh: where pipx puts lean-pkg, added to PATH",
         "~/.local/bin"),
        ("PRIVATE_BOUNDARY_EXTRA",
         "private-boundary-check.sh: a file of extra patterns to refuse",
         "unset"),
    ]),
]


def environment_names() -> list[str]:
    return [name for _, rows in ENVIRONMENT for name, _, _ in rows]


def environment_rows() -> list[dict]:
    """The table as data, for `help environment --json`."""
    return [{"name": name, "section": section, "meaning": meaning, "default": default}
            for section, rows in ENVIRONMENT for name, meaning, default in rows]


def _render_environment() -> str:
    out = [
        "lean-pkg, comparator-receipt and lean-exercise read these environment variables. A command-line",
        "flag always wins over its variable. A boolean variable is on for 1, true, yes or on (any case).",
        "",
    ]
    for section, rows in ENVIRONMENT:
        out.append(section)
        for name, meaning, default in rows:
            body = f"{meaning}. Default: {default}."
            out.append(textwrap.fill(body, WIDTH, initial_indent=f"  {name}: ",
                                     subsequent_indent="      ", break_on_hyphens=False))
        out.append("")
    out.append("Variables read by the programs lean-pkg runs (git, gh, lake, elan, jq) work as those programs")
    out.append("document; for example gh's GH_TOKEN reaches every `gh` call (promote, audit, report, search, ...).")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# exit codes

EXIT_CODES = """\
lean-pkg uses three exit codes. They are never renumbered.

- 0: success. Dry runs, read-only checks that pass, and no-ops ("already gated", "already recorded")
  also exit 0.

- 1: refusal or failure. The reason is one `lean-pkg: ...` line on stderr; with --json, stdout also gets
  {"ok": false, "error": "..."}. Nothing was written unless the message says so. An unexpected internal
  error also exits 1 (`lean-pkg: internal error: ...`; LEAN_PKG_DEBUG=1 adds the traceback).

- 2: usage error. An unknown flag, a bad value, a missing argument, an interactive prompt attempted off a
  terminal, or a write under --registry-snapshot. With --json, stdout also gets
  {"ok": false, "error": "...", "usage": true}, argparse's own errors included.

Some read-only commands use 1 for a negative answer rather than a refusal:

  next NAME                      1: the package is blocked (`blockers[]` names why)
  doctor                         1: the registry is unreadable or its schema version is unsupported
  validate                       1: schema, enum or history errors
  gate NAME --check              1: `gate` would refuse this receipt
  comparator host-check          1: this host cannot run the comparator
  publish NAME --dry-run         1: not ready to publish
  public-safety NAME             1: a blocking hit (--strict: any hit)
  reservoir check NAME           1: not eligible for Reservoir
  claims check [NAME]            1: invalid claims, or none synced
  claims ledger NAME             1: the ledger is not ready
  index check / render --check   1: an entry fails its checks / README.md is stale
  search TERM --require-complete 1: a source was skipped or answered in part
  receipt slurm --check          1: a problem with the job's files
  receipt slurm --preflight-deps 1: the staged dependency cache misses a module the entries import
  report --yes                   1: a blocking public-safety hit left, or gh missing or logged out

The other programs:

  comparator-receipt   0 receipt written (or --prepare / --estimate done); 1 a configuration failed or
                       the host policy refuses; 2 usage; 128+N when a signal N stopped the comparator
                       stage part-way (a partial receipt may remain: diagnostic only, never evidence)
  lean-exercise check  0 every theorem accepted; 1 a theorem rejected (or the judge failed); 2 usage or
                       host refusal
  lean-exercise batch  0 every Solution passed; 1 a Solution failed or was refused; 2 usage or host
                       refusal
  types driver         0 every declaration filled; 1 an extraction error; 2 none filled

A process killed by a signal N ends with the shell's 128+N (130 for Ctrl-C).

Examples:

  # Gate only when the dry check passes
  $ lean-pkg gate my-lean --receipt r.json --check && lean-pkg gate my-lean --receipt r.json

  # Tell "blocked" from "lean-pkg failed" in a script
  $ lean-pkg next my-lean --json > next.json; rc=$?
  $ [ "$rc" -eq 2 ] && echo "usage error" || jq -r '.command // .reason' next.json
"""


# --------------------------------------------------------------------------
# formatting

FORMATTING = """\
By default lean-pkg prints line-based text for people. Every command takes --json, before or after the
command name (`lean-pkg --json status x` and `lean-pkg status x --json` are the same), and then prints
one JSON object on stdout: two-space indent, keys sorted. --json never changes the exit code.

STDOUT AND STDERR

- stdout carries the result: the table, the record, or the JSON payload. With --json, stdout is always
  valid JSON, also on failure: {"ok": false, "error": "..."} (and "usage": true for a usage error).
- stderr carries everything else: refusals (`lean-pkg: ...`), warnings (`WARN ...`), lease notices,
  committed-mode reports (`committed <sha> on <branch>`, `pushed ...`), `NOT COMMITTED: ...` and progress.
  Read stderr for why; read stdout for what.

THE JSON PAYLOAD

- Most payloads carry "ok" (true or false). A payload's own fields are described with its command
  (`lean-pkg COMMAND --help`, `lean-pkg llms`, docs/cli.md).
- A write in committed mode adds "registry_commit": {sha, pushed, error, push_error, push_attempts,
  rebased} (also as "commit" when the payload has no "commit" of its own).
- A write outside committed mode that leaves the registry files modified adds "registry_uncommitted".
- A run under --registry-snapshot adds "registry_snapshot": "<full sha>".

NAMED SCHEMAS

Payloads and files with a versioned "schema" field:

  comparator-receipt/2   the receipt comparator-receipt writes (/1 still validates)
  comparator-estimate/2  comparator-receipt --estimate --json
  comparator-types/1     the output file of the comparator types driver
  claims/1               claims.yaml at a package repository's root
  claims-ledger/1        lean-pkg claims ledger NAME --json
  index-entry/2          index/<name>/entry.yaml (/1 still validates)
  exercise-check/1       lean-exercise check --json
  exercise-batch/1       lean-exercise batch --json

The lower-tier receipt `gate` reads is described under RECEIPT SCHEMA in `lean-pkg llms`.

CHOOSING FIELDS

`board`, `list` and `status` take --fields a,b,c to keep only those keys (dotted paths such as
gate.receipt and lease.agent work; an unknown field exits 2 listing the valid keys). `list --only NAME`
keeps only that package. The other commands print their whole payload.

FILTERING WITH JQ

lean-pkg has no --jq flag; pipe --json into jq:

  # Names of the packages waiting for the gate
  $ lean-pkg list --json | jq -r '.packages | to_entries[] | select(.value.status == "gate-pending") | .key'

  # One field of one package
  $ lean-pkg status my-lean --json --fields gate.receipt | jq -r '.package.gate.receipt'

  # The recommended next command, else the blockers
  $ lean-pkg next my-lean --json | jq -r '.command // (.blockers[] | "\\(.kind): \\(.detail)")'

  # Branch on the payload's ok: refused (exit 1) or usage error (exit 2) both print ok: false
  $ lean-pkg gate my-lean --receipt r.json --json | jq -e '.ok' >/dev/null || echo refused
"""


# --------------------------------------------------------------------------

def _command_tree_text(name: str) -> str:
    from . import command_tree
    return command_tree.newcomer_text() if name == "start" else command_tree.aliases_text()


TOPICS: dict[str, tuple[str, object]] = {
    "start": ("New here: the five verbs and the two product tracks",
              lambda: _command_tree_text("start")),
    "aliases": ("Old command and flag spellings: permanent aliases", lambda: _command_tree_text("aliases")),
    "environment": ("Environment variables lean-pkg, comparator-receipt and lean-exercise read",
                    _render_environment),
    "exit-codes": ("Exit codes, and what 1 means for the commands that answer yes or no", lambda: EXIT_CODES),
    "formatting": ("--json output, stdout and stderr, named schemas, filtering with jq", lambda: FORMATTING),
}


def topic_text(name: str) -> str:
    return TOPICS[name][1]()
