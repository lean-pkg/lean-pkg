# lean-pkg -- Lean 4 package registry: board, leases, trust gate, promotion
"""Manage Lean 4 formalization packages from one YAML registry.

The source of truth is a registry YAML file; GitHub (`gh`) is only the
visibility MIRROR. Every state transition appends BOTH a history[] entry in the
registry and one JSON line to the event log -- history is append-only, never
rewritten. See DESIGN.md for the lifecycle.

Registry resolution: --registry PATH, else env LEAN_PKG_REGISTRY, else the
user-level default ~/.lean-pkg/registry (a directory holding lean-packages.yaml,
or the registry file itself; base directory env LEAN_PKG_HOME), else
./lean-packages.yaml in the current directory. The event log is --events PATH,
else a sibling file <registry dir>/lean-package-events.jsonl.

A package's local_path is resolved RELATIVE to $HOME, so a registry can be
shared between machines that keep checkouts at the same home-relative place.
An absolute local_path is accepted transparently. Mutating operations that
touch the outside world are dry-run by default.

    lean-pkg [--registry PATH] [--events PATH] [--json] [--commit|--push|--no-commit] [subcommand]

    board     (default) curses TUI on a tty; static aligned table otherwise
    list      static table, pipe-friendly
    status    <name>                        full record + last 5 history entries
    init      [--force]                     bootstrap an empty registry + event log
    migrate   [--dry-run]                   upgrade the registry schema in place
    record    <name> EVENT --field K=V ...  publication evidence event, validated
                                            before writing (rules: `lean-pkg llms`)
    register  <name> --repo URL --path P    the ONLY write path for new packages;
              [--dry-run]                   validated, deduping, auto-detects
                                            toolchain + mathlib pin
    update    <name> [--notes ...] [--status S --reason T]
                                            fields of a registered package
    claim     <name> --workspace W --agent A [--ttl H] [--reason T] [--contact C] [--force]
              (alias: lease)
    release   <name> [--agent A] [--force]
    renew     <name> --agent A [--ttl H] [--contact C | --no-contact]
                                            restart your live lease; refuses once lapsed
    leases    [--agent A] [--expiring H]    held leases, soonest expiry first
    gate      <name> --receipt PATH         validate a lower-tier receipt, then
              [--comparator PATH]           gate-pending -> verified-private;
                                            optional comparator evidence;
                                            identical re-gate = no-op
    comparator <name> --receipt PATH        attach comparator evidence to a gated
              [--check]                     package; required before promote
    promote   <name> [--yes]                dry-run default; --yes flips GitHub
                                            visibility to public (ONE-WAY)
    publish   <name> --dry-run              release checklist + next commands (read-only)
    reservoir check <name> [--offline]      Reservoir listing criteria (read-only)
    audit     [--fix]                       report expired leases, gh visibility,
                                            paths, drift; --fix clears/reconciles
    validate                                schema + enum + history sanity
    doctor    [--fetch]                     install, registry source, leases, time,
                                            index and tool diagnostics
    index     add|check|render|migrate      public catalog <index>/<name>/entry.yaml
    claims    sync|list|check|ledger [NAME] claims.yaml at the package repo root
    search    TERM [--record]               prior-art search (registry, index, web)
    formalization <name> [--write]          render formalization.yaml from the registry
    coverage  check <name>                  validate coverage.yaml (the paper-to-Lean map)
    page      <name> [--write|--check]      render the README verification section
    llms                                    agent-oriented usage contract
    wiki      [--out PATH]                  render a Markdown status page

--json emits machine-readable JSON on stdout (exit codes unchanged).
Time: every timestamp written is UTC ISO-8601 with offset, second precision; dates
are UTC dates; same-second events keep file order.
--commit (env LEAN_PKG_COMMIT=1) makes a write command pull --ff-only from origin
first, then commit the registry and the event log together; --push (env
LEAN_PKG_PUSH=1) also pushes; --no-commit overrides both. Read-only commands
ignore them; their reports go to stderr, so stdout is unchanged.
Everything is non-interactive: off a tty, no code path may block on input;
prompt paths refuse with exit 2.

Constraints enforced here:
  - agents never hand-edit the yaml: register is validated and deduping
  - one package = one workspace: CAS lease, 24h default TTL, audit --fix sweeps
  - the gate binds the receipt to HEAD; axioms must be a subset of
    {propext, Quot.sound, Classical.choice}; sorry_count must be 0
  - promotion requires the Reservoir-criteria checklist plus the trust gate
    and a comparator PASS; the GitHub flip is one-way and therefore dry-run
    by default
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import copy
import datetime as dt
import fnmatch
import fcntl
import gzip
import hashlib
import io
import json
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
import textwrap
import unicodedata
import urllib.error
import urllib.request
import zlib

import yaml

from .holes import SIDECAR as SIGNOFF_SIDECAR, check_definition_holes, parse_sidecar
if __name__ == "__main__":
    # `python -m lean_pkg.cli`: lean_pkg.index imports this very module (one Fail class).
    sys.modules.setdefault("lean_pkg.cli", sys.modules[__name__])

HOME = pathlib.Path.home()
REGISTRY_ENV = "LEAN_PKG_REGISTRY"
REGISTRY_VERSION = 3                    # schema version written by `init` and `migrate`
SUPPORTED_REGISTRY_VERSIONS = (1, 2, 3) # readable by every command; older files lack the keys below
V2_PACKAGE_DEFAULTS = {
    "license_spdx": None,        # SPDX id detected or declared (ADR-0070 D5)
    "formalization_yaml": None,  # repo-relative path when the package has one
    "palomar": [],               # [{id, version, commit, comparator_config, registered_at, review_outcome, preflight_run}]
    "doi": None,                 # {"concept": str|None, "versions": [{"doi","tag","commit","minted_at"}]}
    "reservoir": None,           # {"listed_at": iso, "url": str} or None
    "consent": [],               # [{"who","role","recorded_at","pointer"}]; pointer is a private-repo path
}
V3_PACKAGE_DEFAULTS = {
    "claims_file": "claims.yaml",  # repo-relative path of the package's claims/1 file
    "claims": [],                  # last synced copy of that file's claims (see `claims sync`)
    "claims_synced_commit": None,  # 40-hex commit the cached claims were read at
}
# ADR-0070 D5 evidence events: name -> required --field keys. Each has a registry effect (see do_record).
EVIDENCE_EVENTS = {
    "consent-recorded":        ("who", "role", "pointer"),
    "palomar-preflight-pass":  ("commit", "run"),
    "palomar-submitted":       ("commit", "submission_id", "comparator_config"),
    "palomar-review":          ("commit", "outcome"),
    "palomar-registered":      ("commit", "id", "version", "comparator_config"),
    "palomar-withdrawn":       ("id", "version"),
    "doi-minted":              ("doi", "tag", "commit"),
    "reservoir-listed":        ("url",),
    "mathlib-pr-opened":       ("url",),
    "mathlib-pr-merged":       ("url",),
}
RETRACT_EVENT = "retract"   # `record NAME retract`: append-only correction of an evidence entry
_HEX40 = re.compile(r"[0-9a-f]{40}")
RESERVOIR_URL_RE = re.compile(r"^https://reservoir\.lean-lang\.org/@([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
RESERVOIR_API = "https://reservoir.lean-lang.org/api/v1/packages/{owner}/{name}"
DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
GITHUB_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+(?:/\S*)?")
PALOMAR_REGISTERED_STATUSES = ("public", "upstreamed")
PRIVATE_EVIDENCE_HINT = "private path in evidence; use a URL or repo-relative pointer"


def reservoir_listing_status(owner: str, name: str) -> int | None:
    """HTTP status of Reservoir's package endpoint (200 = listed, 404 = not listed),
    or None when the network is unreachable. Tests monkeypatch this function."""
    req = urllib.request.Request(RESERVOIR_API.format(owner=owner, name=name),
                                 headers={"User-Agent": "lean-pkg"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
DEFAULT_REGISTRY_NAME = "lean-packages.yaml"
EVENTS_NAME = "lean-package-events.jsonl"
NOTIFY_ENV = "LEAN_PKG_NOTIFY_CMD"

STATUSES = ["scratch", "active", "gate-pending", "verified-private",
            "public", "upstreamed", "archived"]
VISIBILITIES = ["private", "public"]
# register may only mint pre-evidence statuses; verified-private is gate's, public is promote's
# (register accepts public only for an already-public repository: --visibility public --reason).
REGISTER_STATUSES = ["scratch", "active", "gate-pending", "public"]
UPSTREAM_TARGETS = ["none", "mathlib", "palomar"]
# DOI track (#51): absent = palomar (doi-minted needs a palomar-* event on the commit, rule doi-in-palomar);
# doi-only lets a public package with no Palomar-shaped claim record its DOI without one.
TRACKS = ("palomar", "doi-only")
DEFAULT_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]
DEFAULT_TTL_HOURS = 24
# Upper bound for --ttl (claim/renew) and the board's TTL prompt: one year. A larger value is a usage
# error (exit 2) refused before anything is written; an unbounded one overflowed the expiry computation
# after the lease was already saved.
MAX_TTL_HOURS = 8760

# The package record, one row per top-level key: (key, tier, meaning). The single source of truth for
# `status --help`, `status --fields` validation, docs/cli.md "Package record" (tests keep them equal) and
# validate's REQUIRED_FIELDS. Tiers: v1 (validate requires it at every schema version), v2/v3 (the
# V2/V3_PACKAGE_DEFAULTS keys `migrate` adds), optional (absent unless set). A meaning ending "[bulk]" marks
# a large cached array: `--no-claims` on status/list/board drops exactly those keys (BULK_FIELDS).
PACKAGE_FIELDS = (
    ("name", "v1", "package name; equals its key under packages"),
    ("repo", "v1", "git URL (or local bare path) of the repository"),
    ("local_path", "v1", "checkout path, $HOME-relative"),
    ("visibility", "v1", "private | public; GitHub is the mirror"),
    ("status", "v1", "lifecycle status: " + " | ".join(STATUSES)),
    ("owner_project", "v1", "project that owns the package"),
    ("workspace", "v1", "workspace that registered it"),
    ("agent", "v1", "agent that registered it"),
    ("lease", "v1", "live claim {workspace, agent, claimed_at, ttl_hours, reason, contact?} or null; contact: "
                     "optional address of the holding session (claim/renew --contact); status/list/board "
                     "output adds the derived expires_at (claimed_at + ttl_hours, UTC; never stored)"),
    ("lean_toolchain", "v1", "lean-toolchain detected in the checkout"),
    ("mathlib_pin", "v1", "Mathlib revision from lake-manifest.json"),
    ("upstream_target", "v1", "upstream goal: " + " | ".join(UPSTREAM_TARGETS)),
    ("automation", "v1", "automation disclosure (required when upstream_target is mathlib)"),
    ("gate", "v1", "trust gate {receipt, comparator, axioms_allowed}: receipt paths in the repo"),
    ("promoted_at", "v1", "when promote --yes made it public, or null"),
    ("promoted_commit", "v1", "the commit promoted, or null"),
    ("reaudit_after", "v1", "ISO date after which to re-audit, or null"),
    ("notes", "v1", "free text"),
    ("history", "v1", "append-only event list (status prints the last 5; --json all)"),
    ("license_spdx", "v2", "SPDX license id or expression, or null"),
    ("formalization_yaml", "v2", "repo-relative path of formalization.yaml, or null"),
    ("palomar", "v2", "Palomar evidence entries [{id, version, commit, comparator_config, ...}]"),
    ("doi", "v2", "{concept, versions[{doi, tag, commit, minted_at}]} or null"),
    ("reservoir", "v2", "{listed_at, url} once Reservoir lists it, or null"),
    ("consent", "v2", "consent records [{who, role, recorded_at, pointer}]"),
    ("claims_file", "v3", "repo-relative path of the claims/1 file"),
    ("claims", "v3", "cached claims from the last `claims sync` (can be hundreds of KB) [bulk]"),
    ("claims_synced_commit", "v3", "40-hex commit the cached claims were read at, or null"),
    ("paper", "optional", "{arxiv, status} of the paper behind a paper-coupled package"),
    ("namespace_notes", "optional", "{Lean root namespace: why sharing it with another package is intended}"),
    ("source_paper", "optional", "{title, authors[], arxiv, doi} of the formalized paper (credited in "
                                 "formalization.yaml acknowledgements, never as project.authors)"),
    ("ai_assistance", "optional", "{tools[], roles[]}: AI tools used and for what (rendered into acknowledgements)"),
    ("track", "optional", "DOI track: " + " | ".join(TRACKS) + " (absent = palomar); doi-only lets a public package "
                          "record doi-minted without a palomar-* event (update --track)"),
    ("docs_descendants", "optional", "true: a receipt bound to commit C is also accepted at a descendant of C where "
                                     "no build input under the package directory changed (docs-only rule, "
                                     "docs/receipts.md); absent = false (update --accept-docs-descendants)"),
    ("build_inputs", "optional", "[glob]: extra package-relative paths the docs-only rule treats as build inputs "
                                 "(fnmatch; a glob without / also matches a basename) (update --build-inputs)"),
    ("headline_config", "optional", "repo-relative path of the headline comparator configuration (the paper's "
                                    "main results; formalization.yaml status.main_results lists its theorems); "
                                    "absent = comparator.headline.json when tracked (update --headline-config)"),
    ("publication_hold", "optional", "{reason, at, by}: the owner withdrew publication (update "
                                     "--withdraw-publication); promote refuses while it is set; only update "
                                     "--release-publication-hold lifts it"),
)
REQUIRED_FIELDS = [k for k, tier, _ in PACKAGE_FIELDS if tier == "v1"]
BULK_FIELDS = [k for k, _, meaning in PACKAGE_FIELDS if meaning.endswith("[bulk]")]
LEASE_FIELDS = ["workspace", "agent", "claimed_at", "ttl_hours", "reason"]
LEASE_OPTIONAL_FIELDS = ["contact"]     # absent unless set; `--fields lease.contact` yields null then
LEASE_CONTACT_MAX = 200                 # characters
RECEIPT_FIELDS = ["commit", "lean_toolchain", "mathlib_rev", "sorry_count",
                  "axioms", "report_sha256", "job_id", "checker",
                  "generated_at"]
COMPARATOR_SCHEMAS = ("comparator-receipt/1", "comparator-receipt/2")
COMPARATOR_SCHEMA = COMPARATOR_SCHEMAS[-1]
COMPARATOR_MECHANISMS = ("toolchain", "legacy")
COMPARATOR_TOOLCHAIN_DIGESTS = ["lake_sha256", "leanexport_sha256", "leanchecker_sha256",
                                "nanoda_bin_sha256", "con-ron_sha256"]
COMPARATOR_PASS_LINE = "Your solution is okay!"
# One acceptance predicate for both mechanisms, shared with the runner (comparator_receipt imports
# it). Observed live on 2026-10-06 (Lean v4.35.0-rc2): `lake comparator` prints the per-kernel lines
# ("con-ron kernel accepts the solution", "nanoda kernel accepts the solution", "Lean default kernel
# accepts the solution") and then the full line "Your solution is okay!"; the legacy stack prints the
# same final line. Only that full line counts.


def comparator_pass(log_text: str, mechanism: str) -> bool:
    """True when a comparator log shows acceptance: the full line COMPARATOR_PASS_LINE."""
    return COMPARATOR_PASS_LINE in log_text.splitlines()
COMPARATOR_TOOLS = ["comparator", "lean4export", "nanoda", "landrun"]
COMPARATOR_FIELDS = ["schema", "commit", "lean_toolchain", "tools", "host",
                     "job_id", "generated_at", "result", "configs"]
COMPARATOR_CONFIG_FIELDS = ["path", "sha256", "challenge_module",
                            "solution_module", "theorem_names",
                            "definition_names", "permitted_axioms", "log",
                            "log_sha256", "result"]
# the configuration's list fields: omitted (or null) in a committed config means the empty list (#61)
COMPARATOR_LIST_FIELDS = ("theorem_names", "definition_names", "permitted_axioms")
LICENSE_NAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt",
                 "COPYING", "COPYING.md", "COPYING.txt"]

BOARD_HEADER = ("NAME", "STATUS", "VIS", "LEASE", "EXPIRES", "PIN", "REAUDIT")


class Fail(Exception):
    """User-facing refusal: printed to stderr, exit 1."""


class UsageError(Exception):
    """Interactive path attempted off-tty, or other usage-level error: exit 2."""


# --------------------------------------------------------------------------
# small utilities

# Time policy (one for every timestamp the tool writes): UTC, ISO-8601 with an explicit offset
# (+00:00), second precision. Dates the tool writes (wiki `updated:`, search headers, record file
# names) are the UTC date. Events written in the same second keep their order by position in
# history[] and the event log (file order is the tiebreak; there is no sequence field).
# A naive timestamp (legacy, hand-written) is read as UTC; validate accepts it, doctor counts it.

def now_iso() -> str:
    """Now, under the time policy: UTC, ISO-8601 with offset, second precision."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def utc_today() -> str:
    """Today's UTC date as YYYY-MM-DD (the time policy's date)."""
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def is_naive_ts(s) -> bool:
    """True for a parseable ISO-8601 timestamp without an offset (read as UTC by parse_ts)."""
    try:
        v = dt.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return v.tzinfo is None


def naive_timestamps(reg: dict) -> int:
    """How many lease claimed_at / history ts values in the registry are naive (legacy)."""
    n = 0
    for pkg in (reg.get("packages") or {}).values():
        if not isinstance(pkg, dict):
            continue
        lease = pkg.get("lease")
        if isinstance(lease, dict) and lease.get("claimed_at") and is_naive_ts(lease["claimed_at"]):
            n += 1
        for h in pkg.get("history") or []:
            if isinstance(h, dict) and h.get("ts") and is_naive_ts(h["ts"]):
                n += 1
    return n


def parse_ts(s: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp; a naive one is legacy and read as UTC (see the time policy)."""
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    v = dt.datetime.fromisoformat(s)
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v


# Repository-selecting Git variables (#27). A launcher that leaks one (GIT_INDEX_FILE from a hook, say)
# points every git, and Lake's dependency checkout, at another repository's index or object store.
# Subprocesses that work on a package checkout run without them; the registry repository's own git
# calls (registry_git) keep the caller's environment. GIT_CONFIG_*, GIT_SSH_COMMAND and
# GIT_TERMINAL_PROMPT are deliberately kept.
GIT_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE")


def git_env_set(env=None) -> list[str]:
    """The repository-selecting Git variables set in env (default: os.environ), in GIT_REPO_ENV order."""
    env = os.environ if env is None else env
    return [n for n in GIT_REPO_ENV if n in env]


def scrubbed_env(env=None) -> dict:
    """A copy of env (default: os.environ) without the repository-selecting Git variables."""
    return {k: v for k, v in (os.environ if env is None else env).items() if k not in GIT_REPO_ENV}


def run(cmd: list[str], cwd: str | None = None, scrub: bool = False) -> subprocess.CompletedProcess:
    """The one subprocess helper. Never raises on nonzero exit; a binary that cannot be run (not on
    PATH, not executable, a missing cwd) is returncode 127 with the OSError in stderr. scrub=True drops
    GIT_REPO_ENV (for a package checkout)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd,
                              env=scrubbed_env() if scrub else None)
    except OSError as e:
        missing = isinstance(e, FileNotFoundError) and e.filename in (cmd[0], None) and shutil.which(cmd[0]) is None
        why = f"{cmd[0]} not on PATH" if missing else f"{cmd[0]}: {e.strerror or e}"
        return subprocess.CompletedProcess(cmd, 127, "", why)


def git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """git in a package checkout, without the repository-selecting Git variables (#27)."""
    return run(["git", "-C", str(repo), *args], scrub=True)


def registry_git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """git in the registry repository (or lean-pkg's own checkout): the caller's environment as is."""
    return run(["git", "-C", str(root), *args])


def gh(*args: str) -> subprocess.CompletedProcess:
    return run(["gh", *args])


def find_reuse() -> str | None:
    return shutil.which("reuse")


def notify(message: str) -> str:
    """Optional notification hook. If env LEAN_PKG_NOTIFY_CMD is set, run that
    command (split shell-style, no shell) with the message on stdin.

    Returns "sent" (exit 0), "failed" (nonzero exit or not runnable), or
    "not-configured" (variable unset or empty). Never raises."""
    cmd = os.environ.get(NOTIFY_ENV, "").strip()
    if not cmd:
        return "not-configured"
    try:
        argv = shlex.split(cmd)
        p = subprocess.run(argv, input=message + "\n", capture_output=True,
                           text=True, check=False)
    except (OSError, ValueError):
        return "failed"
    return "sent" if p.returncode == 0 else "failed"


def gh_nwo(repo_url: str) -> str | None:
    """owner/repo from a GitHub git URL (ssh or https), else None."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$",
                  str(repo_url).strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def repo_path(pkg: dict) -> pathlib.Path:
    # $HOME-relative by convention; an absolute local_path passes through.
    return HOME / pathlib.Path(str(pkg["local_path"]))


def git_head(repo: pathlib.Path) -> str | None:
    p = git(repo, "rev-parse", "HEAD")
    return p.stdout.strip() if p.returncode == 0 else None


def _rev(repo: pathlib.Path, ref: str) -> str | None:
    p = git(repo, "rev-parse", "--verify", "-q", ref + "^{commit}")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def _is_ancestor(repo: pathlib.Path, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def reference_branch(repo: pathlib.Path) -> tuple[str | None, str | None, str]:
    """The branch a package's evidence is judged against, its tip, and a note; local refs only.
    Order: the remote default branch (refs/remotes/origin/HEAD), else `main`, else `master`.
    When both refs/heads/<b> and refs/remotes/origin/<b> exist, the tip is the one that contains
    the other (a fetched origin ahead of a never-pulled local branch wins); when they diverged,
    origin wins and the note says so. With no candidate branch, (None, HEAD, "").
    The registry has no per-package branch field, so none is consulted."""
    candidates = []
    p = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if p.returncode == 0 and p.stdout.strip().startswith("refs/remotes/origin/"):
        candidates.append(p.stdout.strip()[len("refs/remotes/origin/"):])
    candidates += [b for b in ("main", "master") if b not in candidates]
    for b in candidates:
        local = _rev(repo, f"refs/heads/{b}")
        remote = _rev(repo, f"refs/remotes/origin/{b}")
        if not (local or remote):
            continue
        if not (local and remote) or local == remote:
            return b, local or remote, ""
        if _is_ancestor(repo, local, remote):
            return b, remote, ""
        if _is_ancestor(repo, remote, local):
            return b, local, ""
        return b, remote, f"local {b} and origin/{b} diverged; judged against origin/{b}"
    return None, git_head(repo), ""


def current_branch(repo: pathlib.Path) -> str | None:
    """The checked-out branch's short name, or None on a detached HEAD (or outside a checkout)."""
    p = git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def default_branch(repo: pathlib.Path) -> str | None:
    """The repository's default branch from local refs only (no network): the target of
    refs/remotes/origin/HEAD, else `main`, else `master`, when that local or origin/ branch exists;
    else None."""
    p = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if p.returncode == 0 and p.stdout.strip().startswith("refs/remotes/origin/"):
        return p.stdout.strip()[len("refs/remotes/origin/"):]
    for b in ("main", "master"):
        if _rev(repo, f"refs/heads/{b}") or _rev(repo, f"refs/remotes/origin/{b}"):
            return b
    return None


def branch_state(repo: pathlib.Path | None) -> dict:
    """{current, default, detached, is_default} for a package checkout (#48); the `status --json`
    "branch" object. Outside a checkout with a commit: all null/false."""
    head = git_head(repo) if repo is not None and repo.is_dir() else None
    if head is None:
        return {"current": None, "default": None, "detached": False, "is_default": False}
    cur, dflt = current_branch(repo), default_branch(repo)
    return {"current": cur, "default": dflt, "detached": cur is None,
            "is_default": cur is not None and cur == dflt}


def branch_notice(repo: pathlib.Path | None, state: dict | None = None) -> str | None:
    """`evaluating branch X, not the default D` / `evaluating a detached HEAD <sha12>, not the
    default D` when the checkout is not on its default branch (#48); None when it is, or when no
    default branch can be determined. Informational only: HEAD is still what is evaluated."""
    st = state if state is not None else branch_state(repo)
    if st["default"] is None or st["is_default"]:
        return None
    if st["detached"]:
        head = git_head(repo) or ""
        return f"evaluating a detached HEAD {head[:12]}, not the default {st['default']}"
    return f"evaluating branch {st['current']}, not the default {st['default']}"


def formalization_rel(pkg: dict | None) -> str | None:
    """The package's recorded formalization.yaml path (package-relative), for the evidence rule."""
    rel = str((pkg or {}).get("formalization_yaml") or "").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel or None


def formalization_derived_only(repo: pathlib.Path, bound: str, head: str, rel: str) -> str | None:
    """None when <rel> differs between bound and head only in keys the formalization renderer derives
    (status.evidence and the rest of formalization.DERIVED_PATHS), compared as YAML data; else the
    reason, naming the changed kept key(s) (#23.5)."""
    from .formalization import changed_kept_keys
    docs = []
    for rev in (bound, head):
        raw = git_show_bytes(repo, f"./{rel}", rev)
        if raw is None:
            return (f"{rel} is not tracked at {rev[:12]}; only a regeneration of a file already "
                    f"committed at the bound commit is evidence")
        try:
            doc = yaml.safe_load(raw.decode("utf-8", errors="replace"))
        except yaml.YAMLError:
            return f"{rel} at {rev[:12]} is not valid YAML"
        docs.append(doc if isinstance(doc, dict) else {})
    changed = changed_kept_keys(*docs)
    if changed:
        return (f"{rel} changed in kept (hand-authored) key(s) {', '.join(changed[:5])}; only its "
                f"derived keys (status.evidence and the rest `lean-pkg formalization` regenerates) may "
                f"change after the bound commit")
    return None


def evidence_only_descendant(repo: pathlib.Path, bound: str,
                             head: str, formalization: str | None = None,
                             exclude: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Evidence-only descendant rule (see DESIGN.md): a receipt bound to commit `bound` is
    accepted at `head` when head == bound, or when head descends from bound and
    the only paths that differ live under .lean-receipts/ (the evidence itself).
    Paths are taken relative to the package directory `repo`: when the package is a
    subdirectory of its git repository (lean/, lean/contrib/), only changes under that
    directory count, and within it only <dir>/.lean-receipts/ is evidence.
    `formalization` (the package's recorded formalization.yaml path, see formalization_rel):
    that file may also differ when only its derived keys changed, since it must cite the
    receipt and so cannot be inside the bound commit (#23.5). claims.yaml never may.
    `exclude`: package-relative directories of nested registered packages this package does not depend
    on (nested_exclusions); changes under them are not this package's (#3).
    Returns (ok, note)."""
    if bound == head:
        return True, ""
    p = git(repo, "merge-base", "--is-ancestor", bound, head)
    if p.returncode != 0:
        return False, f"{bound[:12]} is not an ancestor of HEAD {head[:12]}"
    prefix = git(repo, "rev-parse", "--show-prefix").stdout.strip()
    # --relative: only paths under the package directory, relative to it.
    # --no-renames: a rename into .lean-receipts/ is a source change.
    p = git(repo, "diff", "--no-renames", "--name-only", "--relative", bound, head)
    paths = [x for x in p.stdout.splitlines() if x.strip()]
    ignored = [x for x in paths if under_any(x, exclude)]
    paths = [x for x in paths if not under_any(x, exclude)]
    # A Lake build cache or compiled artifact is never evidence, under .lean-receipts/ included (#5).
    cached = [f"{x} ({lake_artifact(x)})" for x in paths if lake_artifact(x)]
    if cached:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} in {'; '.join(cached[:5])}"
                       + (f" (+{len(cached) - 5} more)" if len(cached) > 5 else "")
                       + "; Lake reads it in place of a build, so it is never evidence")
    foreign = [x for x in paths if not x.startswith(".lean-receipts/")]
    # Outside a subdirectory package: .gitattributes/.gitmodules/.lfsconfig at the repository root or between
    # it and the package still change the bytes the build sees (filters, eol, LFS); never evidence.
    outside = ancestor_attribute_changes(repo, bound, head)
    if outside:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} in {', '.join(outside[:5])} "
                       f"(outside the package directory; can change the bytes the build sees)")
    # A committed symlink at the bound commit that resolves into .lean-receipts/ (or to the formalization file)
    # would let an "evidence" edit change what the build reads.
    linked = linked_evidence_changes(repo, bound, paths, formalization) if paths else []
    if linked:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} in evidence that a committed symlink "
                       f"points to: {', '.join(linked[:5])}; a symlink target is never evidence")
    if (prefix or ignored) and not paths:
        if ignored:
            return True, (f"descendant of {bound[:12]} with no change under {prefix or './'} outside nested "
                          f"registered package(s) {nested_label(ignored, exclude)}")
        return True, f"descendant of {bound[:12]} with no change under {prefix}"
    if formalization and formalization in foreign:
        problem = formalization_derived_only(repo, bound, head, formalization)
        if problem:
            return False, f"HEAD {head[:12]} differs from {bound[:12]}: {problem}"
        foreign.remove(formalization)
    if foreign or not paths:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} outside "
                       f".lean-receipts/: {foreign[:5]}")
    return True, (f"evidence-only descendant of {bound[:12]} (+{len(paths)} evidence file(s))"
                  + (f"; ignored {len(ignored)} change(s) under nested registered package(s) "
                     f"{nested_label(ignored, exclude)}" if ignored else ""))


def under_any(path: str, dirs) -> bool:
    """`path` is one of the package-relative directories `dirs` or lies under one."""
    return any(path == d or path.startswith(d + "/") for d in dirs)


def nested_label(paths, dirs) -> str:
    """The directories among `dirs` that hold at least one of `paths`, as `a/, b/`."""
    return ", ".join(f"{d}/" for d in sorted(dirs) if any(x == d or x.startswith(d + "/") for x in paths))


# The packages of the registry this process loaded last (load_registry): the nested-package rule (#3)
# reads their roots. A caller with no registry loaded sees none, which keeps the stricter rule.
_LOADED_PACKAGES: dict = {}


def registered_nested_dirs(repo: pathlib.Path, packages: dict | None = None) -> tuple[str, ...]:
    """The local_path roots of other registered packages strictly inside the package directory `repo`,
    relative to it, sorted (the same roots the declaration scan skips, #117)."""
    pkgs = _LOADED_PACKAGES if packages is None else packages
    try:
        mine = pathlib.Path(repo).resolve()
    except (OSError, RuntimeError):
        return ()
    out = set()
    for other in (pkgs or {}).values():
        if not isinstance(other, dict) or not other.get("local_path"):
            continue
        try:
            root = repo_path(other).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        if root != mine and mine in root.parents:
            out.add(root.relative_to(mine).as_posix())
    return tuple(sorted(out))


def nested_exclusions(repo: pathlib.Path, bound: str, head: str,
                      nested: tuple[str, ...]) -> tuple[tuple[str, ...], list[str]]:
    """(excluded, kept): the nested registered package directories whose changes between bound and head
    are not this package's, and one `dir/ (why)` note per nested directory that stays a build input
    because this package depends on it or reads it: a `path` package in lake-manifest.json, a lakefile
    that requires or names it, a lean_lib/lean_exe/input whose sources lie there, or a symlink committed
    at bound (outside it) that resolves into it. Read at bound and at head; fails closed (an unreadable
    file or tree keeps the directory)."""
    from .build_scope import nested_dependency, overlaps
    if not nested:
        return (), []
    p = git(repo, "diff", "--no-renames", "--name-only", "--relative", bound, head)
    changed = [x for x in p.stdout.splitlines() if x.strip()]
    touched = [d for d in nested if any(x == d or x.startswith(d + "/") for x in changed)]
    if not touched:
        return (), []
    try:
        tree = bound_tree(repo, bound)
    except Fail as e:
        return (), [f"{d}/ (the tree at {bound[:12]} is unreadable: {e})" for d in touched]
    excluded, kept = [], []
    for d in touched:
        why = None
        for rev in (bound, head):
            why = nested_dependency(lambda rel, rev=rev: git_show_bytes(repo, rel, rev), d)
            if why:
                why = f"{why} at {rev[:12]}"
                break
        if not why:
            for link, mode in sorted(tree.items()):
                if not mode.startswith("120000:") or link == d or link.startswith(d + "/"):
                    continue
                t = resolve_tree_path(tree, link)
                if t is not None and overlaps(t, d):
                    why = f"symlink {link} resolves into it"
                    break
        if why:
            kept.append(f"{d}/ ({why})")
        else:
            excluded.append(d)
    return tuple(excluded), kept


# Docs-only descendants (opt-in per package: record field docs_descendants, `update NAME
# --accept-docs-descendants`). Build inputs, matched on package-relative paths at any depth.
DOCS_BUILD_BASENAMES = ("lake-manifest.json", "lean-toolchain")
# Changes the docs rule never admits, whatever the record's build_inputs say.
DOCS_ALWAYS_REFUSED = (".gitattributes", ".gitmodules", ".lfsconfig", "claims.yaml")
# Files outside a subdirectory package that still change the bytes its build sees (filters, eol, LFS):
# checked at the repository root and in every directory between it and the package directory.
DOCS_ANCESTOR_FILES = (".gitattributes", ".gitmodules", ".lfsconfig")
# Lake's build caches and compiled artifacts (Lake 4.35: Lake/Config/Module.lean, Build/Common.lean,
# Util/NativeLib.lean): a `.lake` directory at any depth (build/, packages/, a dependency's own .lake) and
# any file with one of these suffixes. Lake reads them in place of rebuilding, so a committed one is never
# docs and never evidence, whatever the record's build_inputs say or where it lies (#5). Case-folded.
LAKE_ARTIFACT_SUFFIXES = (".olean", ".olean.server", ".olean.private", ".ilean", ".ir", ".ir.sig",
                          ".trace", ".hash", ".c", ".c.o.export", ".c.o.noexport", ".bc", ".bc.o", ".o",
                          ".ltar", ".so", ".dylib", ".dll", ".a", ".lib", ".exe")


def lake_artifact(path: str) -> str | None:
    """`build cache/artifact ...` when the package-relative `path` has a `.lake` component at any depth
    or ends in a Lake artifact suffix (LAKE_ARTIFACT_SUFFIXES); None otherwise."""
    parts = [x.casefold() for x in path.replace("\\", "/").split("/") if x]
    if ".lake" in parts:
        return "build cache/artifact: under .lake/"
    base = parts[-1] if parts else ""
    for suf in sorted(LAKE_ARTIFACT_SUFFIXES, key=len, reverse=True):
        if base.endswith(suf) and base != suf:
            return f"build cache/artifact: *{suf}"
    return None


def resolve_tree_path(tree: dict[str, str], path: str) -> str | None:
    """The package-relative path `path` reaches at a commit whose tree is `tree` (bound_tree), every
    committed symlink on the way followed (a chain, or a directory symlink part-way, included); None when
    it leaves the package directory (absolute target, `..` above the top) or loops."""
    links = {p: m.split(":", 1)[1].replace("\\", "/") for p, m in tree.items() if m.startswith("120000:")}
    todo = [c for c in path.split("/") if c not in ("", ".")]
    done: list[str] = []
    hops = 0
    while todo:
        c = todo.pop(0)
        if c == "..":
            if not done:
                return None
            done.pop()
            continue
        cand = "/".join(done + [c])
        if cand in links:
            hops += 1
            target = links[cand]
            if hops > 40 or target.startswith("/"):
                return None
            todo = [x for x in target.split("/") if x not in ("", ".")] + todo
            continue
        done.append(c)
    return "/".join(done)


def is_evidence_path(path: str, formalization: str | None = None) -> bool:
    """A package-relative path the evidence-only rule treats as evidence: under .lean-receipts/ (or the
    directory itself), or the package's formalization.yaml (derived keys may change)."""
    return path == ".lean-receipts" or path.startswith(".lean-receipts/") or bool(
        formalization and path == formalization)


def evidence_symlinks(tree: dict[str, str], formalization: str | None = None) -> list[str]:
    """`path -> target` for each committed symlink whose target resolves into .lean-receipts/ (or to the
    formalization file): a later commit could change what it points to as an evidence-only edit."""
    out = []
    for path, mode in sorted(tree.items()):
        if mode.startswith("120000:") and not is_evidence_path(path, formalization):
            r = resolve_tree_path(tree, path)
            if r is not None and is_evidence_path(r, formalization):
                out.append(f"{path} -> {mode.split(':', 1)[1]}")
    return out


def symlink_targets(tree: dict[str, str]) -> dict[str, str]:
    """{resolved package-relative target: symlink path} for every committed symlink that stays inside
    the package directory and does not resolve to the package directory itself."""
    out: dict[str, str] = {}
    for path, mode in sorted(tree.items()):
        if mode.startswith("120000:"):
            r = resolve_tree_path(tree, path)
            if r:
                out.setdefault(r, path)
    return out


def linked_changes(targets: dict[str, str], paths: list[str]) -> list[str]:
    """`path (target of symlink L)` for each changed path that is a symlink target or lies under one."""
    out = []
    for x in paths:
        for t, link in targets.items():
            if x == t or x.startswith(t + "/"):
                out.append(f"{x} (target of symlink {link})")
                break
    return out


def linked_evidence_changes(repo: pathlib.Path, bound: str, paths: list[str],
                            formalization: str | None = None) -> list[str]:
    """The changed evidence paths that a symlink committed at `bound` resolves to (fails closed: an
    unreadable tree counts every changed evidence path)."""
    ev = [x for x in paths if is_evidence_path(x, formalization)]
    if not ev:
        return []
    try:
        tree = bound_tree(repo, bound)
    except Fail:
        return [f"{x} (the tree at {bound[:12]} is unreadable)" for x in ev]
    if not any(m.startswith("120000:") for m in tree.values()):
        return []
    return linked_changes(symlink_targets(tree), ev)


def ancestor_attribute_changes(repo: pathlib.Path, bound: str, head: str) -> list[str]:
    """Repository-relative paths of .gitattributes/.gitmodules/.lfsconfig that changed between bound and
    head at the repository root or in any directory between it and the package directory `repo` (the
    package directory itself is covered by the --relative diff). Empty at the repository root."""
    prefix = git(repo, "rev-parse", "--show-prefix").stdout.strip().strip("/")
    if not prefix:
        return []
    top = git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    parts = prefix.split("/")
    dirs = [""] + ["/".join(parts[:i]) + "/" for i in range(1, len(parts))]
    specs = [f":(literal){d}{f}" for d in dirs for f in DOCS_ANCESTOR_FILES]
    p = git(pathlib.Path(top), "diff", "--no-renames", "--name-only", bound, head, "--", *specs)
    return [x for x in p.stdout.splitlines() if x.strip()]
DOCS_PATHS_MAX = 50          # docs_paths recorded in an event / --json


def checker_paths(repo: pathlib.Path, bound: str, checker) -> list[str]:
    """Best effort: the tokens of a receipt's `checker` text that name a file tracked at `bound`
    (package-relative, no '..', not absolute). Those files are build inputs for the docs rule."""
    if not isinstance(checker, str) or not checker.strip():
        return []
    try:
        tokens = shlex.split(checker)
    except ValueError:
        tokens = checker.split()
    out = []
    for t in tokens:
        t = t.strip()
        while t.startswith("./"):
            t = t[2:]
        pp = pathlib.PurePosixPath(t) if t else None
        if not t or t.startswith("-") or pp.is_absolute() or ".." in pp.parts or t in out:
            continue
        if git(repo, "cat-file", "-e", f"{bound}:./{t}").returncode == 0:
            out.append(t)
    return out


CLAIMS_COMMIT_FIRST = ("commit claims.yaml before the verify run, or re-gate "
                       "(docs/lifecycle.md#commit-the-claims-before-the-gate)")


def docs_build_input(path: str, globs, extra, scopes=None) -> str | None:
    """Why a package-relative path is a build input for the docs rule, or None for a non-build file.
    `scopes`: the Lake scopes (build_scope.LakeScope) read at the bound commit and at HEAD; a `.lean` file
    is a build input when one of them owns it. None (no scope could be read): every `.lean` file is.
    The record's build_inputs globs never match under .lean-receipts/: an extension-wide glob (`*.json`,
    `**/*.json`) or an "everything but README" glob would otherwise turn new evidence into a build input."""
    from .build_scope import scope_reason
    art = lake_artifact(path)   # before every admission, .lean-receipts/ included (#5)
    if art:
        return art
    base = posixpath.basename(path)
    if base in DOCS_ALWAYS_REFUSED:
        return base
    if base.endswith(".lean") and (scopes is None or base.startswith("lakefile.")):
        return "*.lean"
    for sc in scopes or []:
        why = scope_reason(sc, path)
        if why:
            return why
    if base.startswith("lakefile."):
        return "lakefile.*"
    if base in DOCS_BUILD_BASENAMES:
        return base
    if path in extra:
        return "checker"
    if is_evidence_path(path):   # the evidence the gate reads: never a build input by a record's glob (#5)
        return None
    for g in globs or []:
        if fnmatch.fnmatchcase(path, g) or ("/" not in g and fnmatch.fnmatchcase(base, g)):
            return f"build_inputs {g}"
    return None


def docs_only_descendant(repo: pathlib.Path, bound: str, head: str, pkg: dict | None,
                         checker=None, exclude: tuple[str, ...] = ()) -> tuple[bool, str, list[str]]:
    """The docs-only descendant rule (docs/receipts.md): head descends from bound and no build input
    under the package directory changed. Build inputs: the .lean files a lean_lib or lean_exe of the
    package owns (read from lakefile.toml at bound and at head; every *.lean when there is a
    lakefile.lean, no lakefile.toml, or one that cannot be read precisely), input_file/input_dir and
    `require` path targets, lakefile.*, lake-manifest.json,
    lean-toolchain at any depth, the record's build_inputs globs, the files the receipt's checker names;
    always refused: a Lake build cache or compiled artifact (lake_artifact: a .lake component at any depth,
    or an artifact suffix; .lean-receipts/ included), a symlink or gitlink added/changed/removed, .gitattributes, .gitmodules, .lfsconfig,
    claims.yaml (and the recorded claims_file); for a subdirectory package also a .gitattributes,
    .gitmodules or .lfsconfig at the repository root or in any directory between it and the package.
    The formalization.yaml keeps its derived-keys-only rule. `exclude`: as for evidence_only_descendant.
    Returns (ok, note, docs_paths): docs_paths are the changed non-build, non-evidence paths."""
    from .build_scope import lake_scope
    pkg = pkg or {}
    if not _is_ancestor(repo, bound, head):
        return False, f"{bound[:12]} is not an ancestor of HEAD {head[:12]}", []
    p = git(repo, "diff", "--no-renames", "--raw", "-z", "--relative", bound, head)
    fields = p.stdout.split("\0")
    globs = [g for g in (pkg.get("build_inputs") or []) if isinstance(g, str) and g.strip()]
    extra = set(checker_paths(repo, bound, checker))
    scopes, why_conservative = [], ""
    for rev in (bound, head):
        sc, why_sc = lake_scope(lambda rel, rev=rev: git_show_bytes(repo, rel, rev))
        if sc is None:
            scopes, why_conservative = None, why_sc
            break
        scopes.append(sc)
    claims_rel = str(pkg.get("claims_file") or "claims.yaml").strip()
    while claims_rel.startswith("./"):
        claims_rel = claims_rel[2:]
    fz_rel = formalization_rel(pkg) or "formalization.yaml"
    refused: list[str] = []
    claims_changed: list[str] = []   # refused only because they are the claims file
    docs: list[str] = []
    admitted: list[str] = []      # docs, evidence and formalization paths: none may be a symlink target
    ignored = 0
    i = 0
    while i + 1 < len(fields):
        meta, path = fields[i], fields[i + 1]
        i += 2
        if not meta.startswith(":"):
            continue
        if under_any(path, exclude):
            ignored += 1
            continue
        parts = meta[1:].split()
        modes = parts[:2]
        if any(m in ("120000", "160000") for m in modes):
            kind = "gitlink" if "160000" in modes else "symlink"
            refused.append(f"{path} ({kind} {parts[-1][:1]})")
            continue
        why = "claims_file" if path == claims_rel else docs_build_input(path, globs, extra, scopes)
        if why:
            refused.append(f"{path} ({why}{', deleted' if parts[-1].startswith('D') else ''})")
            if why in ("claims_file", "claims.yaml") and not parts[-1].startswith("D"):
                claims_changed.append(refused[-1])
            continue
        if path == fz_rel:
            problem = formalization_derived_only(repo, bound, head, fz_rel)
            if problem:
                refused.append(f"{path} ({problem})")
            admitted.append(path)
            continue
        admitted.append(path)
        if path.startswith(".lean-receipts/"):
            continue
        docs.append(path)
    refused += [f"{x} (outside the package directory; can change the bytes the build sees)"
                for x in ancestor_attribute_changes(repo, bound, head)]
    if admitted:   # a non-build file a committed symlink points to is read through that symlink
        try:
            refused += linked_changes(symlink_targets(bound_tree(repo, bound)), admitted)
        except Fail as e:
            refused.append(f"{len(admitted)} file(s) ({e})")
    if refused:
        if refused == claims_changed:
            names = ", ".join(x.split(" (", 1)[0] for x in claims_changed)
            return False, (f"{names} changed after the receipt commit {bound[:12]} (HEAD {head[:12]}); gate "
                           f"reads the claims at the receipt commit, so they are part of what it binds: "
                           f"{CLAIMS_COMMIT_FIRST}"), []
        lean_note = ""
        if scopes is None and any(" (*.lean" in x for x in refused):
            lean_note = (f" (every *.lean counts as a build input: {why_conservative}; only a lakefile.toml's "
                         f"lean_lib/lean_exe targets narrow it)")
        return False, (f"build input(s) changed between {bound[:12]} and HEAD {head[:12]}: "
                       f"{'; '.join(refused[:3])}" + (f" (+{len(refused) - 3} more)" if len(refused) > 3 else "")
                       + lean_note + "; the receipt must be rebuilt at HEAD"), []
    docs.sort()
    shown = ", ".join(docs[:5]) + (f", +{len(docs) - 5} more" if len(docs) > 5 else "")
    return True, (f"docs-only descendant of {bound[:12]} ({len(docs)} non-build file(s): {shown}; "
                  f"opted in" + (f"; ignored {ignored} change(s) under nested registered package(s) "
                                 f"{', '.join(d + '/' for d in exclude)}" if ignored else "") + ")"), docs


def package_descendant(repo: pathlib.Path, bound: str, head: str, pkg: dict | None,
                       checker=None, formalization: str | None = None,
                       nested: tuple[str, ...] | None = None) -> tuple[bool, str, list[str] | None]:
    """The binding rule for a package's commit-bound evidence: the evidence-only rule, then, when the
    record opts in (docs_descendants: true), the docs-only rule. (ok, note, docs_paths): docs_paths is
    a list only when the docs rule admitted head. Without the opt-in the verdict and note are the
    evidence-only rule's, plus one hint clause when the docs rule would have admitted head.
    `formalization` overrides the record's pointer (gate --refresh-formalization); `checker` defaults
    to the recorded gate receipt's. `nested`: the package-relative roots of nested registered packages
    (default: from the registry this process loaded, registered_nested_dirs); changes under one are not
    this package's unless it depends on it (nested_exclusions, #3)."""
    pkg = pkg or {}
    fz = formalization if formalization is not None else formalization_rel(pkg)
    if nested is None:
        nested = registered_nested_dirs(repo)
    exclude, kept = nested_exclusions(repo, bound, head, nested) if bound != head else ((), [])
    kept_note = (f"; nested registered package(s) still counted as build input(s): {'; '.join(kept)}"
                 if kept else "")
    ok, note = evidence_only_descendant(repo, bound, head, fz, exclude)
    if ok or not _is_ancestor(repo, bound, head):
        return ok, note + ("" if ok else kept_note), None
    note += kept_note
    if checker is None:
        checker = (recorded_json(repo, (pkg.get("gate") or {}).get("receipt")) or {}).get("checker")
    dpkg = {**pkg, "formalization_yaml": fz} if fz else pkg
    dok, dnote, docs = docs_only_descendant(repo, bound, head, dpkg, checker, exclude)
    if pkg.get("docs_descendants") is True:
        if dok:
            return True, dnote, docs
        return False, f"{note}; docs-only rule (opted in): {dnote}", None
    if dok:
        return False, (f"{note}; every changed path is a non-build file, so the package can opt in with "
                       f"`lean-pkg update {pkg.get('name') or 'NAME'} --accept-docs-descendants --reason TEXT`"), None
    if CLAIMS_COMMIT_FIRST in dnote:
        return False, f"{note}; {dnote}", None
    return False, note, None


def docs_admission_line(head: str, bound: str, note: str) -> str:
    """The text line of a docs-only admission: `✓ HEAD <c12> is a docs-only descendant of ...`."""
    return f"✓ HEAD {head[:12]} is a {note}"


def docs_admission(repo: pathlib.Path, commit: str, pkg: dict, checker=None) -> tuple[dict, str | None]:
    """({descendant_of, docs_paths} or {}, text line or None): the additive event/JSON fields when the
    docs-only rule (not the evidence-only rule) admitted HEAD for evidence bound to `commit`."""
    head = git_head(repo) or ""
    if not head or head == commit:
        return {}, None
    ok, note, docs = package_descendant(repo, commit, head, pkg, checker=checker)
    if not ok or docs is None:
        return {}, None
    return ({"descendant_of": commit, "docs_paths": docs[:DOCS_PATHS_MAX]},
            docs_admission_line(head, commit, note))


def ensure_interactive() -> None:
    """Every prompt path must call this first: off a tty, refuse (exit 2)
    instead of blocking on input. Agents always use flags or --json."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UsageError("interactive mode needs a tty on stdin+stdout; "
                         "use flags or --json instead")


def print_json(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def require_nonempty(flag: str, value) -> None:
    """Refuse an empty or whitespace-only value for a flag that names something (a path, a repo)."""
    if value is not None and not str(value).strip():
        raise Fail(f"{flag} must not be empty")


def local_path_key(stored) -> str:
    """Comparison form of a RECORDED local_path as this host resolves it: $HOME-joined (an absolute
    path passes through, as in repo_path), normalised, symlinks resolved."""
    return os.path.realpath(os.path.normpath(str(HOME / pathlib.Path(str(stored).strip()))))


def normalize_local_path(p, flag: str = "--path") -> tuple[str, str]:
    """The one normaliser for a --path given to register / update --path: (stored form, comparison
    key). A leading `~/` is expanded against $HOME (`~user` is refused); a relative path is taken
    relative to $HOME; the stored form is $HOME-relative when the path lies under $HOME, absolute
    otherwise, with `.`/`..`/`//`/trailing slashes collapsed. Empty or $HOME itself is refused."""
    require_nonempty(flag, p)
    s = str(p).strip()
    if s == "~" or s.startswith("~/"):
        s = str(HOME) + s[1:]
    elif s.startswith("~"):
        raise Fail(f"{flag} {p!r}: only a leading ~/ is expanded (to $HOME); give the path explicitly")
    home = os.path.normpath(str(HOME))
    absolute = os.path.normpath(os.path.join(home, s))
    if absolute == home:
        raise Fail(f"{flag} {p!r} resolves to $HOME itself; name the package checkout")
    rel = os.path.relpath(absolute, home)
    stored = absolute if rel == ".." or rel.startswith("../") else rel
    return stored, os.path.realpath(absolute)


def detect_toolchain(repo: pathlib.Path) -> tuple[str | None, str | None]:
    """(toolchain, warning) from the first line of <repo>/lean-toolchain."""
    f = repo / "lean-toolchain"
    if not f.is_file():
        return None, f"no lean-toolchain at {f}"
    lines = f.read_text().splitlines()
    if not lines or not lines[0].strip():
        return None, f"empty lean-toolchain at {f}"
    return lines[0].strip(), None


LEAN_LIB_RE = re.compile(r"^\s*lean_lib\s+(«[^»\n]+»|[A-Za-z_][A-Za-z0-9_'!?.]*)", re.MULTILINE)


def lean_root_namespaces(repo: pathlib.Path) -> set[str]:
    """Root namespaces (top-level module names) a checkout's Lean libraries claim, from the working
    tree: every `lean_lib` in lakefile.lean, else every [[lean_lib]] name in lakefile.toml, else the
    top-level *.lean files (lakefile.lean excluded). Empty when nothing is found or readable (#22.7)."""
    names: list[str] = []
    try:
        text = (repo / "lakefile.lean").read_text(encoding="utf-8", errors="replace")
        names = [m.group(1) for m in LEAN_LIB_RE.finditer(text)]
    except OSError:
        pass
    if not names:
        try:
            import tomllib
            data = tomllib.loads((repo / "lakefile.toml").read_text(encoding="utf-8"))
            libs = data.get("lean_lib") or []
            names = [str(lib["name"]) for lib in libs if isinstance(lib, dict) and lib.get("name")]
        except (OSError, ValueError, TypeError):
            pass
    if not names:
        try:
            names = [p.stem for p in repo.glob("*.lean") if p.name != "lakefile.lean"]
        except OSError:
            names = []
    return {n.strip("«»").split(".")[0] for n in names if n.strip("«»")}


def namespace_clashes(reg: dict, repo: pathlib.Path, skip: str | None = None) -> dict[str, list[str]]:
    """{root namespace: [other package names]} for the namespaces of `repo` that another registered
    package's checkout (on this host) also claims."""
    mine = lean_root_namespaces(repo) if repo.is_dir() else set()
    out: dict[str, list[str]] = {}
    if not mine:
        return out
    for other in sorted(reg.get("packages") or {}):
        pkg = reg["packages"][other] or {}
        if other == skip or not pkg.get("local_path"):
            continue
        try:
            theirs = repo_path(pkg)
            if theirs.resolve() == repo.resolve() or not theirs.is_dir():
                continue
        except (OSError, ValueError, TypeError):
            continue
        for ns in sorted(mine & lean_root_namespaces(theirs)):
            out.setdefault(ns, []).append(other)
    return out


def namespace_notes(clashes: dict[str, list[str]]) -> list[str]:
    return [f"note: Lean root namespace {ns} is also used by {', '.join(others)}; two packages with one "
            f"root namespace cannot be built or imported together (rename one lean_lib)"
            for ns, others in clashes.items()]


def detect_mathlib_pin(repo: pathlib.Path) -> tuple[str | None, str | None]:
    """(short pin, warning): rev of the mathlib package in lake-manifest.json."""
    mf = repo / "lake-manifest.json"
    if not mf.is_file():
        return None, "no lake-manifest.json — mathlib_pin set to null"
    try:
        data = json.loads(mf.read_text())
    except ValueError:
        return None, "lake-manifest.json is not valid JSON — mathlib_pin set to null"
    for p in data.get("packages") or []:
        if str(p.get("name", "")).lower() in ("mathlib", "«mathlib»"):
            rev = p.get("rev")
            if rev:
                return str(rev)[:10], None
            return None, "mathlib is a path dep or has no rev in " \
                          "lake-manifest.json — mathlib_pin set to null"
    return None, "no mathlib package in lake-manifest.json — mathlib_pin set to null"


# --------------------------------------------------------------------------
# registry IO, events

def registry_not_found(path: pathlib.Path) -> str:
    """The refusal for a missing registry: where the path came from and how to make one."""
    src = _RESOLVED_SOURCE.get(str(path))
    return (f"registry not found: {path} (" + (f"resolved from {src}; " if src else "")
            + f"create one with `lean-pkg init`, or set {REGISTRY_ENV} / ~/.lean-pkg/registry)")


def load_registry(path: pathlib.Path, *, check_version: bool = True) -> dict:
    if not path.is_file():
        raise Fail(registry_not_found(path))
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
        raise Fail(f"registry has no top-level 'packages' map: {path}")
    v = data.get("version")
    if check_version and isinstance(v, int) and not isinstance(v, bool) \
            and v > max(SUPPORTED_REGISTRY_VERSIONS):
        # A newer schema read by an older lean-pkg: refuse rather than show a silently partial view.
        raise Fail(f"registry schema version {v} is not supported (supported: "
                   f"{', '.join(map(str, SUPPORTED_REGISTRY_VERSIONS))}); this lean-pkg is older than the "
                   f"registry: upgrade lean-pkg (`lean-pkg migrate` upgrades only older registries)")
    global _LOADED_PACKAGES
    _LOADED_PACKAGES = data["packages"]
    return data


LOCK_SUFFIX = ".lock"
_LOCKS_HELD: dict[str, int] = {}   # lock file -> nesting depth held by THIS process


def lock_path(reg_path: pathlib.Path) -> pathlib.Path:
    """<registry>.lock, beside the registry file (a symlinked registry locks beside its target)."""
    p = pathlib.Path(reg_path).resolve()
    return p.with_name(p.name + LOCK_SUFFIX)


@contextlib.contextmanager
def registry_lock(reg_path: pathlib.Path):
    """Exclusive fcntl.flock on <registry>.lock for load -> check -> mutate -> save -> append.

    Every writer takes it, so concurrent processes on one host serialize; the lock file is created if
    missing and never deleted. Reentrant within a process (save_registry takes it too)."""
    lp = lock_path(reg_path)
    key = str(lp)
    if key in _LOCKS_HELD:
        _LOCKS_HELD[key] += 1
        try:
            yield lp
        finally:
            _LOCKS_HELD[key] -= 1
        return
    try:
        fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o666)
    except OSError as e:
        raise Fail(f"cannot open the registry lock {lp}: {e.strerror or e}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _LOCKS_HELD[key] = 1
        try:
            yield lp
        finally:
            del _LOCKS_HELD[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


def save_registry(path: pathlib.Path, data: dict) -> None:
    """Atomic save (private temp file in the same directory, fsync, os.replace; the old file's mode is
    kept), then the event lines record() queued on `data` are appended. Under the registry lock. Every
    event log with queued lines is checked appendable first: one that is not (read-only, a directory)
    refuses the save with nothing written, so a YAML change never lands without its event line."""
    path = pathlib.Path(path)
    target = path.resolve() if path.is_symlink() else path
    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True, width=120)
    with registry_lock(target):
        check_event_logs_appendable(data)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            try:
                mode = stat.S_IMODE(os.stat(target).st_mode)
            except FileNotFoundError:
                mode = 0o666 & ~_umask()
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        flush_events(data)


LEAN_PKG_HOME_ENV = "LEAN_PKG_HOME"


def lean_pkg_home() -> pathlib.Path:
    """The user-level base directory: $LEAN_PKG_HOME, else ~/.lean-pkg (read at call time, not import time)."""
    env = os.environ.get(LEAN_PKG_HOME_ENV, "").strip()
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".lean-pkg"


def user_default_registry() -> tuple[pathlib.Path | None, str]:
    """(registry file or None, detail) for the user-level default <lean_pkg_home>/registry.

    A directory (or a symlink to one) holding lean-packages.yaml yields that file; a file (or a symlink to one) IS
    the registry file, resolved so that saves rewrite the target, not the link, and the event log lands beside it."""
    p = lean_pkg_home() / "registry"
    if p.is_dir():
        f = p / DEFAULT_REGISTRY_NAME
        if f.is_file():
            return f, f"{f}" + (f" ({p} -> {p.resolve()})" if p.is_symlink() else "")
        return None, f"{p} is a directory without {DEFAULT_REGISTRY_NAME}; not used"
    if p.is_file():
        f = p.resolve() if p.is_symlink() else p
        return f, f"{p}" + (f" -> {f}" if f != p else "") + " (registry file)"
    if p.is_symlink():
        return None, f"{p} is a dangling symlink; not used"
    return None, (f"{p} not configured (mkdir -p {p.parent} && ln -s /path/to/registry-repo {p}; "
                  f"base dir: env {LEAN_PKG_HOME_ENV})")


_RESOLVED_SOURCE: dict[str, str] = {}   # registry path -> how registry_source found it (for the not-found hint)


def registry_source(args) -> tuple[pathlib.Path, str]:
    """(registry path, source): --registry "flag", else $LEAN_PKG_REGISTRY "env", else the user-level default
    <lean_pkg_home>/registry "user-default", else ./lean-packages.yaml "cwd"."""
    if getattr(args, "registry", None):
        path, src = pathlib.Path(args.registry).expanduser(), "flag"
    elif os.environ.get(REGISTRY_ENV, "").strip():
        path, src = pathlib.Path(os.environ[REGISTRY_ENV].strip()).expanduser(), "env"
    else:
        user, _ = user_default_registry()
        path, src = (user, "user-default") if user is not None \
            else (pathlib.Path.cwd() / DEFAULT_REGISTRY_NAME, "cwd")
    _RESOLVED_SOURCE[str(path)] = src
    return path, src


def registry_path(args) -> pathlib.Path:
    """--registry, else $LEAN_PKG_REGISTRY, else ~/.lean-pkg/registry, else ./lean-packages.yaml."""
    return registry_source(args)[0]


EVENTS_ENV = "LEAN_PKG_EVENTS"


def events_source(args) -> tuple[pathlib.Path, str]:
    """(event log path, source): --events "flag"; else, when --registry is given, its sibling
    lean-package-events.jsonl "registry-sibling" (an explicit registry never writes into the log env
    LEAN_PKG_EVENTS names); else env LEAN_PKG_EVENTS "env"; else the resolved registry's sibling "sibling"."""
    if getattr(args, "events", None):
        return pathlib.Path(args.events).expanduser(), "flag"
    if getattr(args, "registry", None):
        return registry_path(args).parent / EVENTS_NAME, "registry-sibling"
    env = os.environ.get(EVENTS_ENV, "").strip()
    if env:
        return pathlib.Path(env).expanduser(), "env"
    return registry_path(args).parent / EVENTS_NAME, "sibling"


def events_path(args) -> pathlib.Path:
    """--events, else the sibling of an explicit --registry, else env LEAN_PKG_EVENTS, else the sibling
    lean-package-events.jsonl of the resolved registry."""
    return events_source(args)[0]


SNAPSHOT_ENV = "LEAN_PKG_REGISTRY_SNAPSHOT"
SNAPSHOT_REFUSAL = "read-only: --registry-snapshot"


def registry_snapshot_arg(args) -> str | None:
    """--registry-snapshot COMMIT, else env LEAN_PKG_REGISTRY_SNAPSHOT, else None."""
    flag = (getattr(args, "registry_snapshot", None) or "").strip()
    return flag or os.environ.get(SNAPSHOT_ENV, "").strip() or None


def registry_home_dir(args) -> pathlib.Path:
    """The directory the registry file lives in: under --registry-snapshot the working-tree directory, not
    the temporary copy (siblings such as index/ and the adoption ledger are read from there)."""
    origin = getattr(args, "snapshot_origin_dir", None)
    return pathlib.Path(origin) if origin else registry_path(args).parent


def _git_show_bytes(top: pathlib.Path, rev: str, rel: str) -> bytes | None:
    """The blob `git show REV:REL` in the repository at `top`, or None when REL is not in REV."""
    try:
        p = subprocess.run(["git", "-C", str(top), "show", f"{rev}:{rel}"],
                           capture_output=True, check=False)
    except OSError as e:
        raise Fail(f"--registry-snapshot: cannot run git: {e.strerror or e}")
    return p.stdout if p.returncode == 0 else None


def materialize_snapshot(args, commit: str, tmp: pathlib.Path) -> str:
    """Write the registry file (and its event log, when that commit has one) as of COMMIT into `tmp` and
    point args at the copies; returns the full commit sha. The registry must live in a git work tree."""
    reg_p, _ = registry_source(args)
    reg_p = pathlib.Path(os.path.realpath(reg_p.expanduser()))
    if reg_p.is_dir():
        reg_p = reg_p / DEFAULT_REGISTRY_NAME
    ev_p = pathlib.Path(os.path.realpath(events_path(args)))
    d = reg_p.parent
    top_p = registry_git(d, "rev-parse", "--show-toplevel") if d.is_dir() else None
    if top_p is None or top_p.returncode != 0 or not top_p.stdout.strip():
        raise Fail(f"--registry-snapshot: the registry {reg_p} is not inside a git work tree")
    top = pathlib.Path(os.path.realpath(top_p.stdout.strip()))
    rv = registry_git(top, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    sha = rv.stdout.strip()
    if rv.returncode != 0 or not sha:
        raise Fail(f"--registry-snapshot: {commit!r} is not a commit in {top}")
    rel = reg_p.relative_to(top).as_posix()
    blob = _git_show_bytes(top, sha, rel)
    if blob is None:
        raise Fail(f"--registry-snapshot: {rel} does not exist at {sha[:12]} in {top}")
    snap_reg = tmp / DEFAULT_REGISTRY_NAME
    snap_reg.write_bytes(blob)
    snap_ev = tmp / EVENTS_NAME   # absent unless the commit has the log: an empty history, never the live log
    try:
        ev_rel = ev_p.relative_to(top).as_posix()
    except ValueError:
        ev_rel = None
    ev_blob = _git_show_bytes(top, sha, ev_rel) if ev_rel else None
    if ev_blob is not None:
        snap_ev.write_bytes(ev_blob)
    args.snapshot_origin_dir = str(d)
    args.registry, args.events = str(snap_reg), str(snap_ev)
    args.no_commit = True
    args.registry_snapshot_sha = sha
    return sha


def run_snapshot(args, commit: str) -> int:
    """Run a read-only invocation against the registry as of COMMIT (#34). Every invocation that would
    write the registry or its event log (is_write_invocation; hence also every commit and push) is refused
    with exit 2 before anything is read; a --json payload that is one object gains registry_snapshot."""
    json_out = getattr(args, "json_out", False)
    args.snapshot_read_only = True   # `board` on a tty prints the static table, never the (writing) TUI
    if is_write_invocation(args):
        cmd = getattr(args, "cmd", None) or "board"
        msg = (f"{SNAPSHOT_REFUSAL}: `{cmd}` would write the registry; a snapshot is read-only "
               f"(drop --registry-snapshot / unset {SNAPSHOT_ENV} to write the current registry)")
        if json_out:
            print_json({"ok": False, "error": msg, "usage": True})
        print(f"lean-pkg: {msg}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="lean-pkg-snapshot-") as tmp:
        try:
            sha = materialize_snapshot(args, commit, pathlib.Path(tmp))
        except Fail as e:
            _report_fail(args, e)
            return 1
        print(f"lean-pkg: registry snapshot {sha[:12]} (read-only)", file=sys.stderr)
        if not json_out:
            return run_command(args)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_command(args)
    text = buf.getvalue()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        payload["registry_snapshot"] = sha
        print_json(payload)
    else:
        sys.stdout.write(text)
    return rc


def _event_line(ts: str, name, event: str, **fields) -> str:
    return json.dumps({"ts": ts, "event": event, "name": name, **fields}, sort_keys=True) + "\n"


def _write_event_lines(events_p: pathlib.Path, lines: list[str]) -> None:
    events_p.parent.mkdir(parents=True, exist_ok=True)
    with open(events_p, "a", encoding="utf-8") as f:
        f.write("".join(lines))
        f.flush()
        os.fsync(f.fileno())


def append_event(events_p: pathlib.Path, ts: str, name, event: str, **fields) -> None:
    """Append one event line now: for events with no registry change to save (registry-created after
    the file is written, registry-migrated after the save, index-added, prior-art-searched)."""
    _write_event_lines(events_p, [_event_line(ts, name, event, **fields)])


# Event lines record() queued, per registry object (id -> (the object, [(events path, line)])); the
# object is held so its id cannot be reused. save_registry flushes them AFTER the YAML is replaced, so
# a failure before the save leaves no orphan event line.
_PENDING_EVENTS: dict[int, tuple[dict, list[tuple[pathlib.Path, str]]]] = {}


def _event_log_problem(p: pathlib.Path) -> str | None:
    """Why the event log `p` cannot be appended to, or None. Nothing is created: an existing file is
    opened for append and closed (its content and mtime unchanged); a missing one needs its nearest
    existing ancestor to be a writable directory (_write_event_lines creates the rest)."""
    try:
        if p.exists() or p.is_symlink():
            if p.is_dir():
                return "is a directory"
            with open(p, "a", encoding="utf-8"):
                pass
            return None
        anc = p.parent
        while not anc.exists():
            if anc.parent == anc:
                break
            anc = anc.parent
        if not anc.is_dir():
            return f"{anc} is not a directory"
        if not os.access(anc, os.W_OK | os.X_OK):
            return f"{anc} is not writable"
        return None
    except OSError as e:
        return e.strerror or type(e).__name__


def check_event_logs_appendable(reg: dict) -> None:
    """Fail (nothing written) when an event log with lines queued on `reg` cannot be appended to."""
    entry = _PENDING_EVENTS.get(id(reg))
    for p in dict.fromkeys(p for p, _ in (entry[1] if entry else [])):
        why = _event_log_problem(p)
        if why:
            raise Fail(f"event log {p} cannot be appended to ({why}); nothing written — fix the event "
                       f"log (or pass --events PATH) and re-run")


def flush_events(reg: dict) -> None:
    """Append the lines queued on `reg` (one write + fsync per event log) and forget them. A log that
    cannot be written raises with its lines (and those of the logs not yet reached) still queued."""
    entry = _PENDING_EVENTS.get(id(reg))
    if not entry:
        return
    by_path: dict[pathlib.Path, list[str]] = {}
    for p, line in entry[1]:
        by_path.setdefault(p, []).append(line)
    for p, lines in by_path.items():
        _write_event_lines(p, lines)
        entry[1][:] = [(q, line) for q, line in entry[1] if q != p]
    _PENDING_EVENTS.pop(id(reg), None)


# #21.3: the agent a gate/comparator/record/promote --yes/claims sync/search --record invocation acts as
# (its --agent), set by lease_actor() for this invocation; record() stores it as `actor`.
_ACTOR: dict = {"agent": None}


def lease_actor(reg: dict, name: str | None, agent: str | None, what: str) -> str | None:
    """The lease-holder rule for evidence writers that take an optional --agent (#21.3). With --agent:
    another agent's live lease refuses (an unparseable lease too), and the agent is recorded as the
    event's `actor`. Without it nothing is refused (the behaviour before #21.3); a live lease returns
    a warning naming the holder, which the caller prints on stderr."""
    _ACTOR["agent"] = agent or None
    pkg = (reg.get("packages") or {}).get(name) if name else None
    if not isinstance(pkg, dict):
        return None
    lease = pkg.get("lease")
    live = lease_live_or_none(lease, dt.datetime.now(dt.timezone.utc))
    if live is None:
        if agent:
            refuse_invalid_lease(name, lease)
        return None
    if not live:
        return None
    holder = lease.get("agent")
    if not agent:
        return (f"{name}: lease held by {holder}; {what} without --agent does not check the holder "
                f"(pass --agent {holder} to record the actor; another agent is then refused)")
    if agent != holder:
        raise Fail(f"{name}: lease held by {holder}, not {agent} — {what} refused; ask {holder} to "
                   f"release it, or run as --agent {holder}")
    return None


def warn_lease(msg: str | None) -> None:
    if msg:
        print(f"lean-pkg: warning: {msg}", file=sys.stderr)


def record(reg: dict, events_p: pathlib.Path, pkg: dict, event: str, **fields) -> None:
    """One transition = one history[] entry + one event-log line. Append-only. The line is queued and
    written by the save_registry(reg) that persists the history entry, never before it."""
    if _ACTOR["agent"] and "agent" not in fields and "actor" not in fields:
        fields = {**fields, "actor": _ACTOR["agent"]}
    ts = now_iso()
    pkg.setdefault("history", []).append({"ts": ts, "event": event, **fields})
    _PENDING_EVENTS.setdefault(id(reg), (reg, []))[1].append(
        (pathlib.Path(events_p), _event_line(ts, pkg.get("name"), event, **fields)))


# Validator rules a refusal names, keyed by a substring of the validator's message.
RULE_HINTS = {
    "matches no palomar entry": "rule doi-in-palomar: a doi-minted commit must already be recorded by a "
                                "palomar-* event on the package (record that first; a public package with no Palomar claim: "
                                "update NAME --track doi-only, or record ... --track doi-only)",
}


def _err_key(e: str) -> str:
    """Comparison form of a validator message: a trailing parenthetical (a context listing) is dropped."""
    return re.sub(r" \([^()]*\)$", "", e)


def split_new_errors(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    """(errors this change introduces or repeats beyond the count before, pre-existing errors it leaves)."""
    left = collections.Counter(_err_key(e) for e in before)
    new, old = [], []
    for e in after:
        k = _err_key(e)
        if left[k] > 0:
            left[k] -= 1
            old.append(e)
        else:
            new.append(next((f"{e} [{h}]" for m, h in RULE_HINTS.items() if m in e), e))
    return new, old


def report_preexisting(errors: list[str]) -> None:
    """Pre-existing schema errors do not block an unrelated write; they are reported on stderr."""
    for e in errors:
        print(f"lean-pkg: pre-existing (not caused by this change): {e}", file=sys.stderr)


def _fingerprint(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, default=str)


def retracted_entries(pkg: dict) -> list[dict]:
    """Copies of the history entries that evidence-retracted entries retract (each stores its target verbatim,
    so a later entry with the same event and ts is not swept up)."""
    hist = pkg.get("history") if isinstance(pkg, dict) else None
    return [h["retracted_entry"] for h in (hist if isinstance(hist, list) else [])
            if isinstance(h, dict) and h.get("event") == "evidence-retracted"
            and isinstance(h.get("retracted_entry"), dict)]


def live_history(pkg: dict) -> list[dict]:
    """history[] without the entries an evidence-retracted entry retracts."""
    gone = {_fingerprint(e) for e in retracted_entries(pkg)}
    hist = pkg.get("history") if isinstance(pkg, dict) else None
    return [h for h in (hist if isinstance(hist, list) else []) if isinstance(h, dict)
            and _fingerprint(h) not in gone]


def get_pkg(reg: dict, name: str) -> dict:
    pkg = (reg.get("packages") or {}).get(name)
    if pkg is None:
        known = ", ".join(sorted(reg.get("packages") or {})) or "(none)"
        raise Fail(f"unknown package: {name!r} (known: {known})")
    return pkg


# --------------------------------------------------------------------------
# leases

def lease_expiry(lease: dict) -> dt.datetime:
    ttl = int(lease.get("ttl_hours") or DEFAULT_TTL_HOURS)
    return parse_ts(lease["claimed_at"]) + dt.timedelta(hours=ttl)


LEASE_PARSE_ERRORS = (TypeError, ValueError, KeyError, AttributeError, OverflowError)


def lease_expiry_or_none(lease) -> dt.datetime | None:
    """lease_expiry, or None when the lease's fields cannot be parsed (a hand-edited claimed_at: zzz or
    ttl_hours: 'x'): readers render such a lease as INVALID instead of raising."""
    try:
        return lease_expiry(lease)
    except LEASE_PARSE_ERRORS:
        return None


def lease_invalid(lease) -> bool:
    return bool(lease) and lease_expiry_or_none(lease) is None


def refuse_invalid_lease(name: str, lease) -> None:
    """Writers refuse a package whose lease cannot be parsed; `release --force` is the way out."""
    if lease_invalid(lease):
        get = lease.get if isinstance(lease, dict) else (lambda _k: None)
        raise Fail(f"{name}: lease fields unparseable (claimed_at={get('claimed_at')!r}, "
                   f"ttl_hours={get('ttl_hours')!r}); clear it with "
                   f"`lean-pkg release {name} --force --reason TEXT`")


def lease_live(lease: dict | None, now: dt.datetime) -> bool:
    """True while a lease runs. Raises on unparseable fields: writers call refuse_invalid_lease first,
    readers use lease_expiry_or_none."""
    return bool(lease) and now < lease_expiry(lease)


def lease_live_or_none(lease, now: dt.datetime) -> bool | None:
    """lease_live for readers: None when the lease cannot be parsed."""
    exp = lease_expiry_or_none(lease) if lease else None
    if lease and exp is None:
        return None
    return bool(lease) and now < exp


def human_delta(seconds: float) -> str:
    """Compact duration: 45s, 12m, 2h13m, 1d2h (largest two units)."""
    s = int(abs(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d{h}h" if h else f"{d}d"
    if h:
        return f"{h}h{m}m" if m else f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"


def lease_expires_label(lease: dict | None, now: dt.datetime) -> str:
    """Board cell: `in 2h13m` while the lease is live, `3h ago` once it has lapsed
    (until `audit --fix` sweeps it), `-` without a lease."""
    if not lease:
        return "-"
    exp = lease_expiry_or_none(lease)
    if exp is None:
        return "?"
    delta = (exp - now).total_seconds()
    return f"in {human_delta(delta)}" if delta > 0 else f"{human_delta(delta)} ago"


LEASE_WARN_HOURS = 2    # doctor warns about leases that lapse within this window
# Old command and flag spellings (claim, lease NAME, init, --track, ...) are permanent aliases: command_tree.
# Commands that act on a lease themselves, or print its state, get no lapse warning (lease_warning).
LEASE_WARNING_SKIP = {"claim", "renew", "release", "leases", "audit", "status", "new"}


def lease_state(lease: dict | None, now: dt.datetime) -> tuple[str, float | None]:
    """(state, seconds left) of a lease: `none`, `invalid` (unparseable fields), `expired` (seconds < 0),
    `expiring` (within LEASE_WARN_HOURS) or `live`."""
    if not lease:
        return "none", None
    exp = lease_expiry_or_none(lease)
    if exp is None:
        return "invalid", None
    left = (exp - now).total_seconds()
    if left <= 0:
        return "expired", left
    return ("expiring" if left <= LEASE_WARN_HOURS * 3600 else "live"), left


def lease_state_line(name: str, lease: dict | None, now: dt.datetime) -> str | None:
    """`status`'s lease line: EXPIRED or expiring with the command that keeps it; None for a live lease or none."""
    state, left = lease_state(lease, now)
    agent = (lease or {}).get("agent", "?")
    if state == "expired":
        return (f"lease: EXPIRED {human_delta(-left)} ago ({agent}); renew no longer works: lean-pkg claim {name} "
                f"--workspace W --agent {agent} (audit --fix sweeps it; another agent may claim it first)")
    if state == "expiring":
        return (f"lease: expires in {human_delta(left)} ({agent}); keep it: lean-pkg renew {name} --agent {agent}")
    if state == "invalid":
        return "lease: unreadable claimed_at/ttl_hours; audit --fix sweeps it"
    return None


def lease_warning(args) -> None:
    """Before a command on one package: a stderr WARN when that package's lease has lapsed or lapses within
    LEASE_WARN_HOURS, so a holder that went idle mid-workflow hears it on its next command (#53's report: a
    release lease lapsed unnoticed overnight). Best effort: an unreadable registry says nothing here."""
    name = getattr(args, "name", None)
    if not isinstance(name, str) or getattr(args, "cmd", None) in LEASE_WARNING_SKIP:
        return
    try:
        pkg = (load_registry(registry_path(args), check_version=False).get("packages") or {}).get(name)
    except Exception:   # noqa: BLE001 -- a warning must never turn into the command's failure
        return
    line = lease_state_line(name, (pkg or {}).get("lease"), dt.datetime.now(dt.timezone.utc))
    if line:
        print(f"lean-pkg: WARN: {name} {line}", file=sys.stderr)


def nonneg_hours(v: str) -> float:
    """argparse type for an hours window: a number >= 0 (a negative window is a usage error, exit 2)."""
    try:
        h = float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of hours: {v!r}")
    if h < 0 or h != h:
        raise argparse.ArgumentTypeError(f"hours must be >= 0, got {v}")
    return h


def lease_rows(reg: dict, now: dt.datetime, agent: str | None = None,
               expiring_hours: float | None = None) -> list[dict]:
    """Every held lease as a dict (name, agent, workspace, contact, claimed_at, ttl_hours, expires_at, live,
    remaining_seconds, invalid; contact null when unset), soonest expiry first; filtered by holder and by `expires within N hours
    or already lapsed`. A lease whose fields cannot be parsed is listed first with invalid: true,
    expires_at and remaining_seconds null, live false (it always passes the --expiring filter)."""
    rows = []
    for name in sorted(reg.get("packages", {})):
        lease = reg["packages"][name].get("lease")
        if not lease:
            continue
        get = lease.get if isinstance(lease, dict) else (lambda _k: None)
        if agent is not None and get("agent") != agent:
            continue
        exp = lease_expiry_or_none(lease)
        remaining = (exp - now).total_seconds() if exp is not None else None
        if remaining is not None and expiring_hours is not None and remaining > expiring_hours * 3600:
            continue
        rows.append({"name": name, "agent": get("agent"), "workspace": get("workspace"),
                     "contact": get("contact"), "claimed_at": get("claimed_at"), "ttl_hours": get("ttl_hours"),
                     "expires_at": exp.isoformat() if exp is not None else None,
                     "live": remaining is not None and remaining > 0,
                     "remaining_seconds": int(remaining) if remaining is not None else None,
                     "invalid": exp is None})
    rows.sort(key=lambda r: (not r["invalid"], r["remaining_seconds"] or 0))
    return rows


LEASES_HEADER = ("NAME", "AGENT", "WORKSPACE", "CLAIMED", "EXPIRES", "STATE", "CONTACT")


def leases_lines(rows: list[dict], now: dt.datetime) -> list[str]:
    def expires(r):
        if r.get("invalid"):
            return "?"
        return (f"in {human_delta(r['remaining_seconds'])}" if r["live"]
                else f"{human_delta(r['remaining_seconds'])} ago")

    def state(r):
        if r.get("invalid"):
            return "INVALID (release --force --reason clears)"
        return "live" if r["live"] else "EXPIRED (audit --fix sweeps)"

    table = [(r["name"], str(r["agent"]), str(r["workspace"] or "-"), str(r["claimed_at"])[:16],
              expires(r), state(r), str(r.get("contact") or "")) for r in rows]   # CONTACT last: blank when unset
    widths = [max([len(LEASES_HEADER[i])] + [len(t[i]) for t in table]) for i in range(len(LEASES_HEADER))]
    fmt = lambda t: "  ".join(t[i].ljust(widths[i]) for i in range(len(LEASES_HEADER))).rstrip()
    return [fmt(LEASES_HEADER)] + [fmt(t) for t in table]


def leases_summary(reg: dict, now: dt.datetime) -> tuple[str, str]:
    """(level, detail) for doctor: live / expiring within LEASE_WARN_HOURS / expired-unswept counts with names."""
    rows = lease_rows(reg, now)
    bad = [r for r in rows if r["invalid"]]
    live = [r for r in rows if r["live"]]
    soon = [r for r in live if r["remaining_seconds"] <= LEASE_WARN_HOURS * 3600]
    gone = [r for r in rows if not r["live"] and not r["invalid"]]
    parts = [f"{len(live)} live"]
    if soon:
        parts.append(f"{len(soon)} expiring within {LEASE_WARN_HOURS}h: "
                     + ", ".join(f"{r['name']} ({r['agent']}, in {human_delta(r['remaining_seconds'])})" for r in soon))
    if gone:
        parts.append(f"{len(gone)} expired, not swept: "
                     + ", ".join(f"{r['name']} ({r['agent']}, {human_delta(r['remaining_seconds'])} ago)" for r in gone)
                     + " (audit --fix clears)")
    if bad:
        parts.append(f"{len(bad)} invalid (fields unparseable): " + ", ".join(r["name"] for r in bad)
                     + " (release NAME --force --reason TEXT clears)")
    return ("warn" if soon or gone or bad else "ok"), "; ".join(parts)


def pkg_with_expiry(pkg: dict) -> dict:
    """A copy of one package record whose lease mapping also carries the derived `expires_at` (claimed_at +
    ttl_hours, ISO 8601 UTC; null when the lease fields are unparseable). Never stored in the registry."""
    lease = pkg.get("lease") if isinstance(pkg, dict) else None
    if not (isinstance(lease, dict) and lease):
        return pkg
    exp = lease_expiry_or_none(lease)
    return {**pkg, "lease": {**lease, "expires_at": exp.isoformat() if exp is not None else None}}


def with_expiry(reg: dict) -> dict:
    """A copy of the registry whose lease mappings also carry `expires_at` (ISO, UTC); for --json and
    --fields consumers so nobody recomputes claimed_at + ttl_hours."""
    import copy
    out = copy.deepcopy(reg)
    if isinstance(out.get("packages"), dict):
        out["packages"] = {k: pkg_with_expiry(v) for k, v in out["packages"].items()}
    return out


def check_contact(contact: str | None) -> str | None:
    """`claim/renew --contact TEXT`: the holding session's address (free text, e.g. `lab-desk/n14-paper` or
    `@lean-ops`), stripped. None when the flag is absent; empty, a control character (newline, tab, ...) or
    more than LEASE_CONTACT_MAX characters is a usage error (exit 2), checked before anything is read."""
    if contact is None:
        return None
    text = contact.strip()
    if not text:
        raise UsageError("--contact must not be empty")
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in text):
        raise UsageError("--contact must be one line: no newline, tab or other control character")
    if len(text) > LEASE_CONTACT_MAX:
        raise UsageError(f"--contact is {len(text)} characters; at most {LEASE_CONTACT_MAX}")
    return text


def contact_field(lease: dict) -> dict:
    """The `contact` event field: the lease's contact when it has one, else nothing."""
    return {"contact": lease["contact"]} if lease.get("contact") else {}


def check_ttl(ttl) -> None:
    """--ttl for claim/renew, checked before anything is written: None (no --ttl) passes; <= 0 is a
    refusal (exit 1); above MAX_TTL_HOURS a usage error (exit 2)."""
    if ttl is None:
        return
    if ttl <= 0:
        raise Fail("--ttl must be a positive number of hours")
    if ttl > MAX_TTL_HOURS:
        raise UsageError(f"--ttl {ttl} exceeds the maximum of {MAX_TTL_HOURS} hours (one year); "
                         f"renew the lease instead of claiming it for longer")


def refresh_lease(reg_path, reg, events_p, pkg, lease, agent, ttl, workspace, reason, contact=None,
                  clear_contact=False):
    """Restart a live lease held by `agent` from now with `ttl` hours; event claim-refreshed (with the
    reason when one is given, and the lease's contact when it has one). Callers pass the lease's own
    ttl_hours when no --ttl was given; a contact replaces the lease's, None keeps it. `clear_contact`
    (renew --no-contact) removes the key; the event then records contact_cleared when one was removed."""
    name = pkg["name"]
    lease["claimed_at"] = now_iso()
    lease["ttl_hours"] = ttl
    if workspace:
        lease["workspace"] = workspace
    if reason:
        lease["reason"] = reason
    if contact:
        lease["contact"] = contact
    cleared = clear_contact and lease.pop("contact", None) is not None
    pkg["workspace"] = lease.get("workspace")
    record(reg, events_p, pkg, "claim-refreshed", agent=agent,
           workspace=lease.get("workspace"), ttl_hours=ttl, **({"reason": reason} if reason else {}),
           **contact_field(lease), **({"contact_cleared": True} if cleared else {}))
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": "claim-refreshed",
               "lease": dict(lease),
               "expires_at": lease_expiry(lease).isoformat()}
    return ([f"{name}: lease refreshed for {agent} "
             f"until {lease_expiry(lease).isoformat()}"], payload)


def do_renew(reg_path, reg, events_p, name, agent, ttl, workspace, reason, contact=None, no_contact=False):
    """Holder-only refresh that refuses when there is nothing to renew: no lease, another
    holder, or a lease that has already lapsed (claim again instead; `audit --fix` sweeps it).
    A contact sets or replaces the lease's; None keeps it; `no_contact` (--no-contact) clears it."""
    if no_contact and contact is not None:
        raise UsageError("--contact and --no-contact are mutually exclusive")
    contact = check_contact(contact)
    check_ttl(ttl)
    pkg = get_pkg(reg, name)
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    if not lease:
        raise Fail(f"{name}: no lease held — claim it: lean-pkg claim {name} --workspace W --agent {agent}")
    refuse_invalid_lease(name, lease)
    if lease.get("agent") != agent:
        raise Fail(f"{name}: lease held by {lease.get('agent')}, not {agent} — renew is holder-only "
                   f"(take it over with claim --force --reason)")
    if not lease_live(lease, now):
        raise Fail(f"{name}: lease by {agent} expired at {lease_expiry(lease).isoformat()} "
                   f"({human_delta((now - lease_expiry(lease)).total_seconds())} ago) — not renewable; "
                   f"claim it again (audit --fix sweeps the expired lease)")
    ttl = ttl or int(lease.get("ttl_hours") or DEFAULT_TTL_HOURS)
    return refresh_lease(reg_path, reg, events_p, pkg, lease, agent, ttl, workspace, reason, contact,
                         clear_contact=no_contact)


def do_claim(reg_path, reg, events_p, name, workspace, agent, ttl, reason, force, contact=None):
    """`ttl` None: a fresh claim gets DEFAULT_TTL_HOURS and the holder's re-claim (a refresh) keeps the
    lease's current ttl_hours, as renew does. `contact` is stored in a new lease; on a refresh it
    replaces the lease's, and None keeps it."""
    contact = check_contact(contact)
    check_ttl(ttl)
    if force and not reason:
        raise Fail("--force requires --reason")
    pkg = get_pkg(reg, name)
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease and lease_live(lease, now):
        if lease.get("agent") == agent and not force:
            ttl = ttl or int(lease.get("ttl_hours") or DEFAULT_TTL_HOURS)
            return refresh_lease(reg_path, reg, events_p, pkg, lease, agent, ttl, workspace, reason, contact)
        if not force:
            raise Fail(f"{name}: lease held by {lease.get('agent')} "
                       f"(workspace {lease.get('workspace')}) until "
                       f"{lease_expiry(lease).isoformat()} — refusing; "
                       f"override with --force --reason")
        event = "claim-forced"
    else:
        event = "claim-forced" if force else "claimed"
    ttl = ttl or DEFAULT_TTL_HOURS
    pkg["lease"] = {"workspace": workspace, "agent": agent,
                    "claimed_at": now_iso(), "ttl_hours": ttl,
                    "reason": reason or "", **({"contact": contact} if contact else {})}
    pkg["workspace"] = workspace
    pkg["agent"] = agent
    record(reg, events_p, pkg, event, agent=agent, workspace=workspace,
           ttl_hours=ttl, reason=reason or "", **contact_field(pkg["lease"]))
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": event,
               "lease": dict(pkg["lease"]),
               "expires_at": lease_expiry(pkg["lease"]).isoformat()}
    return ([f"{name}: lease {event} for {agent}@{workspace} "
             f"until {lease_expiry(pkg['lease']).isoformat()}"], payload)


def do_release(reg_path, reg, events_p, name, agent, force, reason=""):
    """Holder-only unless forced; --force needs a reason. released_by is the acting --agent, else
    "force" (the event carries the reason either way)."""
    pkg = get_pkg(reg, name)
    lease = pkg.get("lease")
    if not lease:
        raise Fail(f"{name}: no lease held")
    if force and not reason:
        raise Fail(f"{name}: --force requires --reason")
    if not force:
        refuse_invalid_lease(name, lease)
    if not isinstance(lease, dict):
        lease = {}   # an unparseable non-mapping lease: release --force clears it (never calls lease_live)
    if force:
        event = "release-forced"
    elif agent is None:
        raise Fail(f"{name}: lease held by {lease.get('agent')} — "
                   f"pass --agent {lease.get('agent')} or --force")
    elif agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')}, not {agent} — "
                   f"holder-only (or --force)")
    else:
        event = "released"
    record(reg, events_p, pkg, event, agent=lease.get("agent"),
           workspace=lease.get("workspace"), released_by=agent or "force", reason=reason or "")
    pkg["lease"] = None
    pkg["workspace"] = None
    pkg["agent"] = None
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": event,
               "released_lease": dict(lease)}
    return ([f"{name}: lease {event} "
             f"(was {lease.get('agent')}@{lease.get('workspace')})"], payload)


# --------------------------------------------------------------------------
# prior-art note at claim (advisory, never a gate; spec D4)

PRIOR_ART_WINDOW_DAYS = 30


def recent_prior_art(events_p: pathlib.Path, name: str, now: dt.datetime,
                     days: int = PRIOR_ART_WINDOW_DAYS) -> bool:
    """True when the event log holds a prior-art-searched line for `name` within `days`."""
    if not events_p.is_file():
        return False
    cutoff = now - dt.timedelta(days=days)
    for line in events_p.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
            if isinstance(e, dict) and e.get("event") == "prior-art-searched" \
                    and e.get("name") == name and parse_ts(e.get("ts")) >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    return False


def prior_art_note(pkg: dict, events_p: pathlib.Path, now: dt.datetime | None = None) -> str | None:
    """The advisory line `claim` prints when a package has neither claims (cached, or a claims
    file in its checkout) nor a prior-art search in the last PRIOR_ART_WINDOW_DAYS days."""
    if pkg.get("claims"):
        return None
    rel = pkg.get("claims_file") or "claims.yaml"
    if pkg.get("local_path") and (repo_path(pkg) / rel).is_file():
        return None
    name = pkg.get("name")
    if recent_prior_art(events_p, name, now or dt.datetime.now(dt.timezone.utc)):
        return None
    return (f"note: {name} has no prior-art search recorded in the last {PRIOR_ART_WINDOW_DAYS} days "
            f"and no {rel}; before building, run `lean-pkg search TERM --package {name} --record` "
            f"(advisory)")


# --------------------------------------------------------------------------
# update: the write path for fields of an ALREADY-REGISTERED package

# Fields `update` may set. Everything else is owned by a dedicated subcommand
# and is refused by name, so `update` can never become a way around the trust
# gate: `gate`/`promoted_at`/`promoted_commit`/`visibility` belong to `gate`
# and `promote`, and `lease`/`workspace`/`agent` to `claim`/`release`.
UPDATABLE = {"automation", "notes", "upstream_target", "reaudit_after",
             "owner_project", "repo", "license_spdx", "formalization_yaml", "paper", "namespace_notes",
             "source_paper", "ai_assistance", "track", "docs_descendants", "build_inputs", "headline_config"}
# Paper-coupled packages (#22.10): optional `paper: {arxiv, status}`; promote refuses while the paper is
# unpublished and no arXiv id is recorded (so a public flip cannot disclose results before the preprint).
PAPER_STATUSES = ("unpublished", "preprint", "published")
PAPER_KEYS = ("arxiv", "status")
ARXIV_ID_RE = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")


def namespace_notes_errors(notes, pfx: str) -> list[str]:
    """namespace_notes: absent, null, or a mapping of non-empty namespace -> non-empty text."""
    if notes is None:
        return []
    if not isinstance(notes, dict):
        return [f"{pfx}.namespace_notes: must be a mapping {{namespace: text}} or null"]
    return [f"{pfx}.namespace_notes.{k}: namespace and note must be non-empty strings"
            for k, v in notes.items() if not (isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip())]


def paper_errors(paper, pfx: str) -> list[str]:
    if paper is None:
        return []
    if not isinstance(paper, dict):
        return [f"{pfx}.paper: must be a mapping {{arxiv, status}} or null"]
    errors = [f"{pfx}.paper: unknown key {k!r} (allowed: {', '.join(PAPER_KEYS)})"
              for k in paper if k not in PAPER_KEYS]
    if paper.get("status") not in PAPER_STATUSES:
        errors.append(f"{pfx}.paper.status: must be one of {', '.join(PAPER_STATUSES)}, "
                      f"got {paper.get('status')!r}")
    ax = paper.get("arxiv")
    if ax is not None and not (isinstance(ax, str) and ARXIV_ID_RE.match(ax)):
        errors.append(f"{pfx}.paper.arxiv: must be an arXiv id such as 2410.01234 or math/0601001, "
                      f"got {ax!r}")
    return errors


# #22.6 (option A): the formalized paper and the AI assistance, as registry data. formalization.yaml stays
# v0.4-conformant: both are rendered into its `acknowledgements` string; project.authors stays the
# formalizers (seeded from consent[] owners/authors only), so a paper coauthor needs no consent row.
SOURCE_PAPER_KEYS = ("title", "authors", "arxiv", "doi")
AI_ASSISTANCE_KEYS = ("tools", "roles")


def _str_list_ok(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def source_paper_errors(sp, pfx: str) -> list[str]:
    """source_paper: absent, null, or {title, authors[], arxiv, doi} with a title, an arXiv id or a DOI."""
    if sp is None:
        return []
    if not isinstance(sp, dict):
        return [f"{pfx}.source_paper: must be a mapping {{title, authors, arxiv, doi}} or null"]
    errors = [f"{pfx}.source_paper: unknown key {k!r} (allowed: {', '.join(SOURCE_PAPER_KEYS)})"
              for k in sp if k not in SOURCE_PAPER_KEYS]
    t = sp.get("title")
    if t is not None and not (isinstance(t, str) and t.strip()):
        errors.append(f"{pfx}.source_paper.title: must be a non-empty string or null")
    if sp.get("authors") is not None and not _str_list_ok(sp.get("authors")):
        errors.append(f"{pfx}.source_paper.authors: must be a list of non-empty strings")
    ax = sp.get("arxiv")
    if ax is not None and not (isinstance(ax, str) and ARXIV_ID_RE.match(ax)):
        errors.append(f"{pfx}.source_paper.arxiv: must be an arXiv id such as 2410.01234, got {ax!r}")
    doi = sp.get("doi")
    if doi is not None and not (isinstance(doi, str) and DOI_RE.fullmatch(doi)):
        errors.append(f"{pfx}.source_paper.doi: must be a DOI such as 10.1000/xyz123, got {doi!r}")
    if not any(sp.get(k) for k in ("title", "arxiv", "doi")):
        errors.append(f"{pfx}.source_paper: needs a title, an arXiv id or a DOI")
    return errors


def ai_assistance_errors(ai, pfx: str) -> list[str]:
    """ai_assistance: absent, null, or {tools[] (non-empty), roles[]}."""
    if ai is None:
        return []
    if not isinstance(ai, dict):
        return [f"{pfx}.ai_assistance: must be a mapping {{tools, roles}} or null"]
    errors = [f"{pfx}.ai_assistance: unknown key {k!r} (allowed: {', '.join(AI_ASSISTANCE_KEYS)})"
              for k in ai if k not in AI_ASSISTANCE_KEYS]
    if not (_str_list_ok(ai.get("tools")) and ai.get("tools")):
        errors.append(f"{pfx}.ai_assistance.tools: must be a non-empty list of non-empty strings")
    if ai.get("roles") is not None and not _str_list_ok(ai.get("roles")):
        errors.append(f"{pfx}.ai_assistance.roles: must be a list of non-empty strings")
    return errors


def paper_blocks_promote(pkg: dict) -> bool:
    paper = pkg.get("paper")
    return isinstance(paper, dict) and paper.get("status") == "unpublished" and not paper.get("arxiv")
SPDX_ID_RE = re.compile(r"^[A-Za-z0-9.+-]+$")
SPDX_OPERATORS = ("AND", "OR", "WITH")


def spdx_expression_ok(value) -> bool:
    """A small SPDX license-expression grammar: identifiers (each matching SPDX_ID_RE) joined by
    AND / OR, an identifier optionally followed by WITH <exception id>, and parentheses.
      expr := term (("AND" | "OR") term)* ; term := atom ["WITH" id] ; atom := id | "(" expr ")" """
    tokens = re.findall(r"\(|\)|[^\s()]+", str(value))
    pos = 0

    def ident() -> bool:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] not in SPDX_OPERATORS + ("(", ")") \
                and SPDX_ID_RE.match(tokens[pos]):
            pos += 1
            return True
        return False

    def atom() -> bool:
        nonlocal pos
        if pos < len(tokens) and tokens[pos] == "(":
            pos += 1
            if not expr() or pos >= len(tokens) or tokens[pos] != ")":
                return False
            pos += 1
            return True
        return ident()

    def term() -> bool:
        nonlocal pos
        if not atom():
            return False
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 1
            return ident()
        return True

    def expr() -> bool:
        nonlocal pos
        if not term():
            return False
        while pos < len(tokens) and tokens[pos] in ("AND", "OR"):
            pos += 1
            if not term():
                return False
        return True

    return bool(tokens) and expr() and pos == len(tokens)
OWNED_ELSEWHERE = {
    "gate": "lean-pkg gate", "promoted_at": "lean-pkg promote",
    "promoted_commit": "lean-pkg promote", "visibility": "lean-pkg promote",
    "lease": "lean-pkg claim/release", "workspace": "lean-pkg claim",
    "agent": "lean-pkg claim", "status": "lean-pkg update --status",
    "name": "(immutable)", "local_path": "lean-pkg update NAME --path NEW --reason TEXT",
    "history": "(append-only)",
    "publication_hold": "lean-pkg update NAME --withdraw-publication / --release-publication-hold --reason TEXT",
}

# Status moves `update` may make. The registry's other transitions have
# evidence attached and belong to the subcommand that checks that evidence:
# verified-private is `gate`'s (it validates a lower-tier receipt) and public
# is `promote`'s (it runs the checklist and flips gh visibility).
MANUAL_TRANSITIONS = {
    ("scratch", "active"),
    ("active", "gate-pending"),      # a lower-tier check run was submitted
    ("gate-pending", "active"),       # ... and was abandoned
    ("verified-private", "gate-pending"),  # re-certification after content
                                           # change; `gate` still requires a
                                           # fresh receipt for the new commit
    ("scratch", "archived"), ("active", "archived"),
    ("gate-pending", "archived"), ("verified-private", "archived"),
    ("public", "upstreamed"),        # needs upstream evidence or a --reason URL
                                     # (see upstream_evidence); public stays
                                     # one-way: no move back to a private status
    ("public", "archived"), ("upstreamed", "archived"),
}
# Status moves a dedicated subcommand makes; a refusal names them next to the manual moves.
SUBCOMMAND_TRANSITIONS = {
    ("gate-pending", "verified-private"): "lean-pkg gate --receipt R",
    ("verified-private", "public"): "lean-pkg promote --yes",
}
UPSTREAM_EVIDENCE_EVENTS = ("mathlib-pr-merged", "palomar-registered")
URL_RE = re.compile(r"https?://\S+")


def allowed_moves(old) -> str:
    """The moves out of status `old`, for a refusal: manual ones, then the subcommands' ones."""
    moves = sorted(b for a, b in MANUAL_TRANSITIONS if a == old)
    moves += [f"{b} (via {cmd})" for (a, b), cmd in SUBCOMMAND_TRANSITIONS.items() if a == old]
    return ", ".join(moves) or "none"


def upstream_evidence(pkg: dict) -> list[str]:
    """Unretracted mathlib-pr-merged / palomar-registered events (a withdrawn Palomar entry does not count)."""
    hist = live_history(pkg)
    withdrawn = {(str(h.get("id")), str(h.get("version"))) for h in hist if h.get("event") == "palomar-withdrawn"}
    out = []
    for h in hist:
        if h.get("event") == "mathlib-pr-merged":
            out.append(f"mathlib-pr-merged {h.get('url')}")
        elif h.get("event") == "palomar-registered" \
                and (str(h.get("id")), str(h.get("version"))) not in withdrawn:
            out.append(f"palomar-registered {h.get('id')} v{h.get('version')}")
    return out


def do_update(reg_path, reg, events_p, name, *, sets, status, reason,
              redetect, agent, force, dry_run=False, replace_notes=False):
    """Set fields of a registered package. dry_run runs every check (lease, transitions, schema) on a copy
    and writes nothing: callers that touch other files first (formalization --write) refuse before them."""
    if dry_run:
        reg = copy.deepcopy(reg)
    pkg = get_pkg(reg, name)
    appended = None   # --append-note's dated line: the text output echoes only it (#69)

    for field in sets:
        if field in OWNED_ELSEWHERE:
            raise Fail(f"{name}: field {field!r} is not settable here — "
                       f"use {OWNED_ELSEWHERE[field]}")
        if field not in UPDATABLE:
            raise Fail(f"{name}: unknown field {field!r} "
                       f"(settable: {', '.join(sorted(UPDATABLE))})")
        value = sets[field]
        if field in ("repo", "owner_project"):
            require_nonempty("--" + field.replace("_", "-"), value)
        if field == "license_spdx" and not spdx_expression_ok(value):
            raise Fail(f"license_spdx must be an SPDX identifier such as Apache-2.0 or MIT, or an SPDX "
                       f"expression such as 'MIT OR Apache-2.0', got {value!r}")
        if field == "paper":   # a partial {arxiv?, status?}, merged into the recorded one
            merged = dict(pkg.get("paper") or {})
            merged.update(value)
            if "status" not in merged or ("arxiv" in value and "status" not in value
                                          and merged["status"] == "unpublished"):
                merged["status"] = "preprint" if merged.get("arxiv") else "unpublished"
            merged.setdefault("arxiv", None)
            errs = paper_errors(merged, name)
            if errs:
                raise Fail("; ".join(errs))
            sets[field] = {k: merged[k] for k in PAPER_KEYS}
        if field in ("source_paper", "ai_assistance"):   # partial: given keys replace, the rest kept (#22.6)
            keys = SOURCE_PAPER_KEYS if field == "source_paper" else AI_ASSISTANCE_KEYS
            merged = dict(pkg.get(field) or {})
            merged.update(value)
            merged = {k: merged.get(k) for k in keys if merged.get(k) not in (None, [], "")}
            check = source_paper_errors if field == "source_paper" else ai_assistance_errors
            errs = check(merged, name)
            if errs:
                raise Fail("; ".join(errs))
            sets[field] = merged
        if field == "notes" and isinstance(value, dict):   # --append-note: a dated, signed line (#32)
            line = f"{dt.datetime.now(dt.timezone.utc).date().isoformat()} ({value['agent']}): {value['text']}"
            prev = str(pkg.get("notes") or "").rstrip("\n")
            sets[field] = f"{prev}\n{line}" if prev.strip() else line
            appended = line
        if field == "namespace_notes":   # {ns: text} merged into the recorded notes (#32)
            text = str(value.get("text") or "").strip()
            require_nonempty("--namespace-note", text)
            namespaces = value.get("namespaces")
            if not namespaces:
                try:
                    repo = repo_path(pkg)
                except (KeyError, TypeError, ValueError):
                    repo = None
                clashes = namespace_clashes(reg, repo, skip=name) if repo is not None and repo.is_dir() else {}
                namespaces = sorted(clashes)
                if not namespaces:
                    raise Fail(f"{name}: no Lean root namespace of this checkout is shared with another package; "
                               f"name one with --namespace NS")
            merged = dict(pkg.get("namespace_notes") or {})
            merged.update({ns: text for ns in namespaces})
            sets[field] = merged
        if field == "build_inputs":
            errs = build_inputs_errors(value, "--build-inputs")
            if errs:
                raise Fail("; ".join(errs))
        if field == "track" and value not in TRACKS:
            raise Fail(f"track must be one of {', '.join(TRACKS)}, got {value!r}")
        if field == "headline_config":
            if value is not None and not str(value).strip():
                sets[field] = value = None          # '' clears it: the comparator.headline.json convention applies
            if value is not None:
                from .headline import path_error
                if err := path_error(value):
                    raise Fail(err)
        if field == "formalization_yaml":
            pp = pathlib.PurePosixPath(str(value))
            if pp.is_absolute() or ".." in pp.parts or str(value).startswith("~"):
                raise Fail(f"formalization_yaml must be a repository-relative path, got {value!r}")

    # Lease discipline, the same rule `release` applies: a live lease held by
    # someone else blocks a write unless it is forced with a reason.
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)):
        holder = lease.get("agent")
        if not force and agent != holder:
            raise Fail(f"{name}: lease held by {holder} — pass --agent {holder}, "
                       f"or --force with --reason")
    if force and not reason:
        raise Fail(f"{name}: --force requires --reason")

    changes: dict[str, tuple] = {}

    if redetect:
        repo = repo_path(pkg)
        if not repo.exists():
            raise Fail(f"{name}: local_path does not exist: {repo}")
        tool, _ = detect_toolchain(repo)
        pin, _ = detect_mathlib_pin(repo)
        for field, value in (("lean_toolchain", tool), ("mathlib_pin", pin)):
            if value and pkg.get(field) != value:
                changes[field] = (pkg.get(field), value)

    for field, value in sets.items():
        if pkg.get(field) != value:
            changes[field] = (pkg.get(field), value)
    if "track" in changes and not reason:
        raise Fail(f"{name}: --track requires --reason (it changes which evidence doi-minted needs)")
    for field, flag in (("docs_descendants", "--accept-docs-descendants / --no-accept-docs-descendants"),
                        ("build_inputs", "--build-inputs")):
        if field in changes and not str(reason or "").strip():
            raise Fail(f"{name}: {flag} requires --reason (it changes which descendants of a receipt's "
                       f"commit the receipt binds); nothing written")

    if status is not None:
        old = pkg.get("status")
        if status not in STATUSES:
            raise Fail(f"bad status {status!r} (allowed: {', '.join(STATUSES)})")
        if status != old:
            if not reason:
                raise Fail(f"{name}: --status requires --reason")
            if (old, status) not in MANUAL_TRANSITIONS:
                hint = {"verified-private": "lean-pkg gate --receipt R",
                        "public": "lean-pkg promote --yes"}.get(status)
                raise Fail(f"{name}: {old} -> {status} is not a manual transition"
                           + (f" — use {hint}" if hint else "")
                           + f"; allowed from {old}: {allowed_moves(old)}")
            if status == "upstreamed" and not upstream_evidence(pkg) and not URL_RE.search(reason):
                raise Fail(f"{name}: public -> upstreamed needs evidence: a recorded "
                           f"{' or '.join(UPSTREAM_EVIDENCE_EVENTS)} event (`lean-pkg record {name} "
                           f"mathlib-pr-merged --field url=URL`), or a --reason containing the merged PR / "
                           f"Palomar URL (http...)")
            changes["status"] = (old, status)

    if not changes:
        # Idempotent: no history entry, no event, no write. `gate` behaves the
        # same way for an already-recorded receipt.
        payload = {"ok": True, "name": name, "event": "update-noop", "changes": {}}
        return ([f"{name}: already up to date; nothing written"], payload)

    before = validate_registry(reg)
    for field, (_old, new) in changes.items():
        pkg[field] = new

    errors, preexisting = split_new_errors(before, validate_registry(reg))
    if errors:
        # Never persist a schema error this change introduces; errors already in the
        # registry (another package's, say) are reported and do not block.
        raise Fail(f"{name}: update would break the registry schema:\n  "
                   + "\n  ".join(errors))
    if dry_run:
        return ([f"{name}: would update {len(changes)} field(s); nothing written"],
                {"ok": True, "name": name, "event": "update-dry-run",
                 "changes": {f: {"from": o, "to": n} for f, (o, n) in changes.items()}})
    report_preexisting(preexisting)

    # --replace-notes over non-empty notes is recorded as such (#53); notes_from keeps the old text.
    notes_replaced = replace_notes and bool(str((changes.get("notes") or ("", ""))[0] or "").strip())
    record(reg, events_p, pkg, "updated",
           fields=sorted(changes), reason=reason or "", forced=bool(force),
           **({"notes_replaced": True} if notes_replaced else {}),
           **{f"{f}_from": o for f, (o, _n) in changes.items()})
    save_registry(reg_path, reg)

    out = [f"{name}: updated {len(changes)} field(s)"]
    for field, (old, new) in sorted(changes.items()):
        if field == "notes" and appended is not None:
            # #69: a long-lived package's notes run to thousands of characters; the full before/after stays in
            # the event (notes_from) and in --json `changes`, the text output names the one line added.
            out.append(f"  appended note: {appended}")
            continue
        out.append(f"  {field}: {old!r} -> {new!r}")
    old_notes = str((changes.get("notes") or ("", ""))[0] or "").strip()
    replaced = bool(old_notes) and not str(changes["notes"][1] or "").startswith(old_notes)
    if replaced:
        out.append(f"  replaced notes were: {old_notes!r}")
        out.append("  use --append-note to keep the previous note")
    payload = {"ok": True, "name": name, "event": "updated",
               "changes": {f: {"from": o, "to": n} for f, (o, n) in changes.items()},
               "reason": reason or "", "forced": bool(force)}
    if replaced:
        payload["hint"] = "use --append-note to keep the previous note"
    if preexisting:
        payload["preexisting_errors"] = preexisting
    return (out, payload)


# Privacy withdrawal (#5 field report): the owner made a promoted repository private again. Promotion
# history stays append-only (promoted_at, promoted_commit and the `promoted` event are kept); the record
# says what is true now: visibility private, status back to the gated private status, and a hold that
# `promote` refuses on. gate/regate never touch the hold; only --release-publication-hold lifts it.
HOLD_KEYS = ("reason", "at", "by")
WITHDRAWABLE = ("public",)


def publication_hold_errors(pkg: dict, pfx: str) -> list[str]:
    hold = pkg.get("publication_hold")
    if hold is None:
        return []
    if not isinstance(hold, dict) or set(hold) != set(HOLD_KEYS):
        return [f"{pfx}.publication_hold: must be a mapping with exactly the keys {', '.join(HOLD_KEYS)}"]
    errs = [f"{pfx}.publication_hold.{k}: must be a non-empty string" for k in HOLD_KEYS
            if not isinstance(hold.get(k), str) or not hold[k].strip()]
    if not errs:
        try:
            parse_ts(hold["at"])
        except Exception:
            errs.append(f"{pfx}.publication_hold.at: unparseable timestamp {hold['at']!r}")
    if pkg.get("status") in ("public", "upstreamed") or pkg.get("visibility") == "public":
        errs.append(f"{pfx}: publication_hold is set but the record says status {pkg.get('status')} / visibility "
                    f"{pkg.get('visibility')} (a held package is private)")
    return errs


def hold_text(hold: dict) -> str:
    return f"publication hold since {hold.get('at')} by {hold.get('by')}: {hold.get('reason')}"


def github_visibility(pkg: dict) -> tuple[str | None, str]:
    """(lowercase GitHub visibility or None, how it was obtained) for a GitHub repo; never raises."""
    nwo = gh_nwo(pkg.get("repo") or "")
    if not nwo:
        return None, f"not a GitHub repository ({pkg.get('repo')!r}); visibility not queried"
    try:
        p = gh("repo", "view", nwo, "--json", "visibility", "-q", ".visibility")
    except OSError as e:
        return None, f"gh not runnable ({e}); GitHub visibility not verified"
    if p.returncode != 0:
        err = ((p.stderr or p.stdout or "").strip().splitlines() or [f"exit {p.returncode}"])[0]
        return None, f"gh query for {nwo} failed ({err}); GitHub visibility not verified"
    vis = p.stdout.strip().lower()
    return (vis or None), f"gh reports {nwo} {vis or 'of unknown visibility'}"


def do_withdraw_publication(reg_path, reg, events_p, name, *, reason, agent, force=False,
                            dry_run=False) -> tuple[list[str], dict]:
    """public -> verified-private (gated) or active (never gated), visibility private, publication_hold set,
    one `publication-withdrawn` event. Refused while GitHub still reports the repository public: lean-pkg
    records the owner's withdrawal, it never changes GitHub visibility itself."""
    pkg = get_pkg(reg, name)
    if not str(reason or "").strip():
        raise Fail(f"{name}: --withdraw-publication requires --reason TEXT (who authorized it and why)")
    status = pkg.get("status")
    if pkg.get("publication_hold"):
        raise Fail(f"{name}: already withdrawn ({hold_text(pkg['publication_hold'])}); nothing written")
    if status not in WITHDRAWABLE:
        extra = (" (an upstreamed result lives in Mathlib or Palomar: withdraw it there, e.g. `lean-pkg record "
                 f"{name} palomar-withdrawn ...`)" if status == "upstreamed" else
                 " (it was never published; nothing to withdraw)" if status in ("scratch", "active", "gate-pending",
                                                                              "verified-private") else "")
        raise Fail(f"{name}: --withdraw-publication withdraws a public package, got status {status!r}{extra}")
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)) and not force and agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}, "
                   f"or --force with --reason")
    gvis, how = github_visibility(pkg)
    if gvis == "public":
        raise Fail(f"{name}: {how}: make the repository private first (the owner's step; lean-pkg never changes "
                   f"visibility on withdrawal), then re-run; nothing written")
    new_status = "verified-private" if (pkg.get("gate") or {}).get("receipt") else "active"
    by = (agent or _ACTOR["agent"] or "").strip() or "unknown"
    hold = {"reason": reason.strip(), "at": now_iso(), "by": by}
    view = {"status": {"from": status, "to": new_status},
            "visibility": {"from": pkg.get("visibility"), "to": "private"},
            "publication_hold": hold, "github_visibility": gvis, "github_check": how,
            "promoted_at": pkg.get("promoted_at"), "promoted_commit": pkg.get("promoted_commit")}
    lines = [f"{name}: {'would withdraw' if dry_run else 'withdrew'} publication: status {status} -> {new_status}, "
             f"visibility {pkg.get('visibility')} -> private, publication hold set",
             f"  github: {how}",
             f"  kept (append-only): promoted_at {pkg.get('promoted_at')}, promoted_commit "
             f"{str(pkg.get('promoted_commit') or 'none')[:12]}, the promoted event",
             f"  promote refuses while the hold is set; gate/regate keep it; lift it only with "
             f"`lean-pkg update {name} --release-publication-hold --reason TEXT`"]
    if new_status == "active":
        lines.append("  no gate receipt is recorded, so the status is active (verified-private needs a gate receipt)")
    if dry_run:
        lines.append("DRY RUN — nothing written")
        return lines, {"ok": True, "name": name, "event": "publication-withdrawn", "dry_run": True, **view}
    pkg["status"], pkg["visibility"], pkg["publication_hold"] = new_status, "private", hold
    record(reg, events_p, pkg, "publication-withdrawn", status_from=status, status_to=new_status,
           visibility_from=view["visibility"]["from"], visibility_to="private", reason=hold["reason"],
           by=by, github_visibility=gvis, promoted_commit=pkg.get("promoted_commit"), forced=bool(force))
    save_registry(reg_path, reg)
    return lines, {"ok": True, "name": name, "event": "publication-withdrawn", "dry_run": False, **view}


def do_release_publication_hold(reg_path, reg, events_p, name, *, reason, agent, force=False,
                                dry_run=False) -> tuple[list[str], dict]:
    """Lift a publication hold: status and visibility unchanged (still private); promote's checklist decides
    any later public flip. One `publication-hold-released` event keeps the lifted hold."""
    pkg = get_pkg(reg, name)
    if not str(reason or "").strip():
        raise Fail(f"{name}: --release-publication-hold requires --reason TEXT (who authorized it and why)")
    hold = pkg.get("publication_hold")
    if not hold:
        raise Fail(f"{name}: no publication hold is recorded; nothing to release")
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)) and not force and agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}, "
                   f"or --force with --reason")
    lines = [f"{name}: {'would release' if dry_run else 'released'} the {hold_text(hold)}",
             f"  status {pkg.get('status')} and visibility {pkg.get('visibility')} unchanged; publishing again "
             f"goes through `lean-pkg publish {name} --dry-run` and `lean-pkg promote {name}`"]
    payload = {"ok": True, "name": name, "event": "publication-hold-released", "dry_run": bool(dry_run),
               "released": hold, "reason": reason.strip()}
    if dry_run:
        lines.append("DRY RUN — nothing written")
        return lines, payload
    pkg.pop("publication_hold", None)
    record(reg, events_p, pkg, "publication-hold-released", reason=reason.strip(), hold=hold,
           by=(agent or _ACTOR["agent"] or "").strip() or "unknown", forced=bool(force))
    save_registry(reg_path, reg)
    return lines, payload


def _bound_at(repo: pathlib.Path, head: str | None, rel,
              formalization: str | None = None, pkg: dict | None = None) -> tuple[bool, str | None, str]:
    """(kept, bound commit, verdict) for a recorded receipt path at the new checkout: the file must be
    readable there and its commit must equal HEAD (or HEAD an evidence-only descendant of it)."""
    rec = recorded_json(repo, rel)
    commit = str((rec or {}).get("commit") or "") or None
    if not head:
        return False, commit, "no git HEAD at the new path (or the path is not here); cannot confirm"
    if rec is None:
        return False, commit, f"{rel} is not readable at the new path"
    if not (commit and _HEX40.fullmatch(commit)):
        return False, commit, "the receipt names no 40-hex commit"
    ok, note, _docs = package_descendant(repo, commit, head, pkg, checker=(rec or {}).get("checker"),
                                         formalization=formalization)
    return ok, commit, (note or f"commit {commit[:12]} is HEAD")


def relocate_binding(name: str, pkg: dict, repo: pathlib.Path, head: str | None, new: str,
                     receipt: dict | None, comparator: dict | None, claims_cleared: bool,
                     reason: str) -> tuple[dict, list[str], list[str]]:
    """The binding diagnostics of a relocation grouped with what to run next (#26.3): (binding JSON,
    next_commands, text block). receipt/comparator: {"path", "commit", "kept", "verdict"} or None."""
    rel = formalization_rel(pkg)
    cand = rel or "formalization.yaml"
    fz = {"pointer": rel, "path": cand, "changed": None, "derived_only": None, "verdict": "not applicable"}
    other: list[str] = []
    commit = (receipt or {}).get("commit")
    if head and commit and _HEX40.fullmatch(str(commit)) and commit != head \
            and _is_ancestor(repo, commit, head):
        diff = git(repo, "diff", "--no-renames", "--name-only", "--relative", commit, head).stdout.split()
        other = [x for x in diff if not x.startswith(".lean-receipts/") and x != cand]
        fz["changed"] = cand in diff
        if not fz["changed"]:
            fz["verdict"] = f"{cand} unchanged since {commit[:12]}"
        else:
            problem = formalization_derived_only(repo, commit, head, cand)
            fz["derived_only"] = problem is None
            if problem:
                fz["verdict"] = f"not admitted: {problem}"
            elif rel:
                fz["verdict"] = f"only derived keys changed since {commit[:12]}: admitted (evidence-only rule)"
            else:
                fz["verdict"] = (f"only derived keys changed since {commit[:12]}, but no formalization_yaml "
                                 f"pointer is recorded, so the exception does not apply")
    elif head and commit == head:
        fz["verdict"] = f"HEAD is the receipt commit {commit[:12]}; nothing to admit"
    binding = {"head": head, "receipt": receipt, "comparator": comparator, "formalization": fz,
               "other_changes": other[:10]}
    nxt: list[str] = []
    h12 = (head or "HEAD")[:12]
    fz_tracked = bool(head) and tracked_at(repo, cand, head)
    if receipt and not receipt["kept"]:
        if fz["changed"] and fz["derived_only"] and not rel and not other:
            nxt += [f"lean-pkg update {name} --formalization-yaml {cand}",
                    f"lean-pkg update {name} --path {new} --reason {shlex.quote(reason or 'TEXT')}"]
        else:
            nxt.append(f"lean-pkg gate {name} --receipt R" + (" --refresh-formalization" if fz_tracked else "")
                       + f"   # R: a lower-tier receipt bound to HEAD {h12}")
    if comparator and not comparator["kept"] and not (nxt and nxt[0].startswith(f"lean-pkg update {name} --formalization")):
        nxt += [f"comparator-receipt --project {repo}   # at HEAD {h12}",
                f"lean-pkg comparator {name} --receipt .lean-receipts/{h12}-comparator.json"]
    if claims_cleared:
        nxt.append(f"lean-pkg claims sync {name}")
    block = [f"binding at the new path (HEAD {h12}):"]
    for label, b in (("gate.receipt", receipt), ("gate.comparator", comparator)):
        if b:
            block.append(f"  {label} {b['path']} (commit {str(b['commit'])[:12]}): "
                         f"{'kept' if b['kept'] else 'cleared'}: {b['verdict']}")
    if not (receipt or comparator):
        block.append("  no gate receipt recorded")
    block.append(f"  {cand} (pointer {rel or 'not recorded'}): {fz['verdict']}")
    if other:
        block.append(f"  other changes since the receipt commit: {', '.join(other[:5])}")
    block += ["next:"] + [f"  {c}" for c in nxt] if nxt else ["next: nothing to re-gate"]
    return binding, nxt, block


def do_relocate(reg_path, reg, events_p, name, *, new_path, reason, agent,
                redetect, dry_run) -> tuple[list[str], dict]:
    """update NAME --path NEW: move a package's local_path, keeping its name, history and
    claims cache. Never touches the file system; anything bound to a checkout commit that
    cannot be shown to hold at the new checkout is cleared, and the event lists it."""
    pkg = get_pkg(reg, name)
    if not reason:
        raise Fail(f"{name}: --path requires --reason (why the package moved)")
    new, new_key = normalize_local_path(new_path)
    old = pkg.get("local_path")
    if old and local_path_key(old) == new_key:
        # Idempotent, like update's other no-ops: no history entry, no event, no write.
        payload = {"ok": True, "name": name, "event": "update-noop", "changes": {},
                   "local_path": old, "dry_run": bool(dry_run)}
        return [f"{name}: already at {old}; nothing written"], payload
    for other, opkg in (reg.get("packages") or {}).items():
        if other != name and opkg.get("local_path") \
                and local_path_key(opkg["local_path"]) == new_key:
            raise Fail(f"{name}: local_path {new} is already registered by {other!r} — "
                       f"one registry row per checkout; refusing")
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, now) and agent != lease.get("agent"):
        holder = lease.get("agent")
        raise Fail(f"{name}: lease held by {holder} until "
                   f"{lease_expiry(lease).isoformat(timespec='seconds')} — the holder "
                   f"relocates with --agent {holder} (--force is not accepted for --path)")

    new_abs = HOME / pathlib.Path(new)
    present = new_abs.is_dir()
    head = git_head(new_abs) if present else None
    if redetect and not present:
        raise Fail(f"{name}: cannot --redetect: path not present on this host: {new_abs}")
    notes: list[str] = []
    if not present:
        notes.append(f"path not present on this host: {new_abs} (other machines may hold it); "
                     f"commit-bound evidence cannot be confirmed here")
    elif head is None:
        notes.append(f"no git HEAD at {new_abs}; commit-bound evidence cannot be confirmed here")

    cleared: list[str] = []
    gate = copy.deepcopy(pkg.get("gate") or {})
    b_receipt = b_comp = None
    if gate.get("receipt"):
        kept, commit, verdict = _bound_at(new_abs, head, gate["receipt"], formalization_rel(pkg), pkg)
        b_receipt = {"path": gate["receipt"], "commit": commit, "kept": kept, "verdict": verdict}
        if not kept:
            cleared.append("gate.receipt")
            notes.append(f"gate.receipt cleared: {gate['receipt']} (commit {str(commit)[:12]}) "
                         f"is not bound to HEAD {str(head)[:12]} at the new path")
            gate["receipt"] = None
    if gate.get("comparator"):
        ckept, ccommit, cverdict = _bound_at(new_abs, head, gate["comparator"], formalization_rel(pkg), pkg)
        kept = bool(gate.get("receipt")) and ckept
        b_comp = {"path": gate["comparator"], "commit": ccommit, "kept": kept,
                  "verdict": cverdict if gate.get("receipt") else "the lower-tier receipt is cleared"}
        if not kept:
            cleared.append("gate.comparator")
            notes.append(f"gate.comparator cleared: {gate['comparator']} is not bound to "
                         f"HEAD {str(head)[:12]} at the new path")
            gate["comparator"] = None
    csc = pkg.get("claims_synced_commit")
    if (csc or pkg.get("claims")) and not (csc and head and csc == head):
        cleared += ["claims_synced_commit", "claims"]
        notes.append(f"claims cache cleared (synced at {str(csc)[:12]}, HEAD {str(head)[:12]}); "
                     f"run `lean-pkg claims sync {name}`")

    old_status = pkg.get("status")
    new_status = old_status
    if "gate.receipt" in cleared:
        if old_status == "verified-private":
            new_status = "gate-pending"
            notes.append(f"status verified-private -> gate-pending: re-gate with "
                         f"`lean-pkg gate {name} --receipt R`")
        elif old_status in ("public", "upstreamed"):
            notes.append(f"status {old_status} unchanged (one-way); record fresh evidence "
                         f"with `lean-pkg gate {name} --receipt R`")

    redetected: dict[str, dict] = {}
    if redetect:
        tool, _ = detect_toolchain(new_abs)
        pin, _ = detect_mathlib_pin(new_abs)
        for field, value in (("lean_toolchain", tool), ("mathlib_pin", pin)):
            if pkg.get(field) != value:
                redetected[field] = {"from": pkg.get(field), "to": value}
    else:
        notes.append(f"lean_toolchain/mathlib_pin left as recorded; run "
                     f"`lean-pkg update {name} --redetect` to re-read toolchain/pin")

    binding, next_commands, block = relocate_binding(name, pkg, new_abs, head, new, b_receipt, b_comp,
                                                     "claims" in cleared, reason)
    payload = {"ok": True, "name": name, "event": "relocated", "old_path": old,
               "new_path": new, "cleared": cleared, "notes": notes, "dry_run": bool(dry_run),
               "status": ({"from": old_status, "to": new_status}
                          if new_status != old_status else None),
               "redetected": redetected, "binding": binding, "next_commands": next_commands}
    out = [f"{name}: relocated {old} -> {new}"]
    out += [f"  cleared: {c}" for c in cleared]
    out += [f"  {f}: {v['from']!r} -> {v['to']!r}" for f, v in redetected.items()]
    out += [f"  note: {n}" for n in notes]
    if dry_run:
        out.append("DRY RUN — nothing written. Re-run without --dry-run to relocate.")
        return out + block, payload

    trial = copy.deepcopy(reg)
    tp = trial["packages"][name]
    tp["local_path"], tp["gate"], tp["status"] = new, gate, new_status
    if "claims" in cleared:
        tp["claims_synced_commit"], tp["claims"] = None, []
    for field, v in redetected.items():
        tp[field] = v["to"]
    errors, preexisting = split_new_errors(validate_registry(reg), validate_registry(trial))
    if errors:
        raise Fail(f"{name}: relocation would break the registry schema:\n  "
                   + "\n  ".join(errors))
    report_preexisting(preexisting)
    for field in ("local_path", "gate", "status", "claims_synced_commit", "claims",
                  *redetected):
        if field in tp:
            pkg[field] = tp[field]
    fields = {"old_path": old, "new_path": new, "reason": reason, "cleared": cleared}
    if agent:
        fields["agent"] = agent
    if redetected:
        fields["redetected"] = redetected
    record(reg, events_p, pkg, "relocated", **fields)
    if new_status != old_status:
        record(reg, events_p, pkg, "status-changed", status_from=old_status,
               status_to=new_status, reason=f"relocated: {', '.join(cleared)} cleared")
    save_registry(reg_path, reg)
    return out + block, payload


# --------------------------------------------------------------------------
# gate

# The results ledger is the project's own record of outcomes, outside lean-pkg (field report: name it).
RL_REMINDER = ("results-ledger: lean-pkg recorded this outcome for {name} (`lean-pkg status {name}`; per claim: "
               "`lean-pkg claims ledger {name}`); if your project keeps a results ledger of its own, add it "
               "there (docs/lifecycle.md#results-ledger)")

def validate_receipt(pkg: dict, repo: pathlib.Path, receipt: dict) -> None:
    for f in RECEIPT_FIELDS:
        if f not in receipt:
            raise Fail(f"receipt missing required field {f!r}")
    commit = str(receipt["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Fail(f"receipt commit is not a full lowercase 40-hex sha: {commit!r}")
    head = git_head(repo)
    if head is None:
        raise Fail(f"cannot resolve HEAD in {repo}")
    ok, note, _docs = package_descendant(repo, commit, head, pkg, checker=receipt.get("checker"))
    if not ok:
        raise Fail(f"receipt commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the receipt must bind to HEAD or an evidence-only descendant "
                   f"({note})")
    check_toolchain_at(repo, commit, receipt["lean_toolchain"], "toolchain mismatch")
    check_path_deps_at(repo, commit, formalization_rel(pkg))
    if not str(receipt["mathlib_rev"]).strip():
        raise Fail("receipt mathlib_rev is empty")
    committed = committed_mathlib_rev(repo, commit)
    if committed and not _same_rev(str(receipt["mathlib_rev"]).strip(), committed):
        raise Fail(f"mathlib mismatch: receipt mathlib_rev={str(receipt['mathlib_rev']).strip()!r}, the mathlib "
                   f"rev in lake-manifest.json committed at {commit[:12]}={committed!r}; the receipt must name "
                   f"the Mathlib the bound commit pins")
    sc = receipt["sorry_count"]
    if isinstance(sc, bool) or not isinstance(sc, int):
        raise Fail(f"sorry_count must be an integer, got {sc!r}")
    if sc != 0:
        raise Fail(f"sorry_count must be 0, got {sc} — the trust gate is not clean")
    axioms = receipt["axioms"]
    if not isinstance(axioms, list) or not all(isinstance(a, str) for a in axioms):
        raise Fail("axioms must be a list of strings")
    allowed = set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS)
    extra = sorted(set(axioms) - allowed)
    if extra:
        raise Fail(f"axioms {extra} outside allowed set {sorted(allowed)}")
    sha = str(receipt["report_sha256"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise Fail(f"report_sha256 must be 64 hex chars, got {sha!r}")
    for f in ("job_id", "checker", "generated_at"):
        if not str(receipt[f]).strip():
            raise Fail(f"receipt field {f!r} is empty")
    check_timestamp(receipt["generated_at"], "receipt")
    receipt_job_slug(receipt["job_id"])


def check_timestamp(value, what: str) -> None:
    """generated_at must parse as an ISO-8601 timestamp (parse_ts), not merely be non-empty."""
    try:
        parse_ts(value)
    except (ValueError, TypeError):
        raise Fail(f"{what} field 'generated_at' is not an ISO-8601 timestamp: {value!r}") from None


def receipt_job_slug(job_id) -> str:
    """The job id as it appears in the stored receipt's file name: characters outside
    [A-Za-z0-9._-] become '-', and leading dots and dashes are stripped (so '../../evil' is
    'evil', never a '..'-led name). An empty result is refused."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_id)).lstrip(".-")
    if not slug:
        raise Fail(f"receipt job_id {str(job_id)!r} leaves no usable file-name characters")
    return slug


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_show_bytes(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> bytes | None:
    """Bytes of <rev>:<rel>, or None when the path is not tracked there. `rel` is relative to the
    package directory `repo`, which may be a subdirectory of its git repository: a bare `rel` is read
    as `./rel` (git resolves a bare `<rev>:<path>` from the repository root, #21.2)."""
    spec = rel if rel.startswith(("./", "../")) else f"./{rel}"
    p = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{spec}"],
                       capture_output=True, env=scrubbed_env())
    return p.stdout if p.returncode == 0 else None


def tracked_at(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> bool:
    """True when <rel> (relative to the package directory `repo`) is committed at <rev>."""
    return git(repo, "cat-file", "-e", f"{rev}:./{rel}").returncode == 0


def check_toolchain_at(repo: pathlib.Path, commit: str, claimed, label: str) -> None:
    """The receipt's lean_toolchain must equal the lean-toolchain file committed at the bound
    commit (relative to the package directory). The working-tree file never counts."""
    blob = git_show_bytes(repo, "./lean-toolchain", commit)
    if blob is None:
        raise Fail(f"{repo}: no lean-toolchain committed at {commit[:12]}")
    committed = blob.decode("utf-8", "replace").strip()
    if str(claimed).strip() != committed:
        wt = repo / "lean-toolchain"
        work = wt.read_text().strip() if wt.is_file() else None
        raise Fail(f"{label}: receipt={str(claimed).strip()!r} committed at {commit[:12]}="
                   f"{committed!r}"
                   + (f" (the working-tree file says {work!r}; uncommitted files do not count)"
                      if work is not None and work != committed else ""))


def outside_path_deps(manifest: bytes) -> list[str]:
    """`name (dir)` for each `path` package in lake-manifest.json bytes whose dir leaves the package
    directory (absolute, or `..` after normalising). Its sources are outside what a receipt binds, so an
    edit to them is never drift. Judged lexically on the committed text; ValueError on bad JSON."""
    data = json.loads(manifest.decode("utf-8", "replace"))
    out = []
    for p in (data.get("packages") or []) if isinstance(data, dict) else []:
        if not isinstance(p, dict) or p.get("type") != "path":
            continue
        d = str(p.get("dir") or "")
        norm = posixpath.normpath(d.replace("\\", "/")) if d else "."
        if posixpath.isabs(norm) or norm == ".." or norm.startswith("../"):
            out.append(f"{p.get('name')} ({d})")
    return out


def bound_tree(repo: pathlib.Path, commit: str) -> dict[str, str]:
    """{package-relative path: git mode} of every entry (trees included) under the package directory at
    `commit`; symlink entries map to "120000:<target>" so their committed targets can be judged. Fails
    closed: a git error (or output it cannot parse) is a refusal, never an empty or a safe tree."""
    def run_git(*args: str) -> bytes:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=scrubbed_env())
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace").strip().splitlines()
            raise Fail(f"cannot read the tree at {commit[:12]} (git {args[0]} exited {p.returncode}"
                       + (f": {err[0]}" if err else "") + "); refusing rather than judging it safe")
        return p.stdout
    out: dict[str, str] = {}
    for rec in run_git("ls-tree", "-r", "-t", "-z", commit, ".").decode("utf-8", "replace").split("\0"):
        if not rec:
            continue
        meta, sep, path = rec.partition("\t")
        fields = meta.split()
        if not sep or len(fields) != 3:
            raise Fail(f"cannot read the tree at {commit[:12]}: unexpected git ls-tree record {rec[:80]!r}")
        mode, _kind, sha = fields
        if path in ("./", "."):
            continue
        if mode == "120000":
            mode = "120000:" + run_git("cat-file", "blob", sha).decode("utf-8", "replace")
        out[path.rstrip("/")] = mode
    return out


def escaping_symlinks(tree: dict[str, str]) -> list[str]:
    """`path -> target` for each committed symlink whose target is absolute or has a `..` component. A
    target that only descends cannot leave the directory holding the symlink, so no chain of the
    remaining symlinks can leave the package directory either."""
    out = []
    for path, mode in sorted(tree.items()):
        if mode.startswith("120000:"):
            target = mode.split(":", 1)[1]
            t = target.replace("\\", "/")
            if posixpath.isabs(t) or ".." in t.split("/"):
                out.append(f"{path} -> {target}")
    return out


def path_dep_tree_problems(tree: dict[str, str], manifest: bytes) -> list[str]:
    """For each in-package `path` dependency: its dir, and every directory on the way to it, must be a
    tracked directory at the bound commit (not a symlink, a submodule gitlink, or untracked such as
    .lake/), and nothing under it may be a gitlink. Lexically escaping ones are outside_path_deps'."""
    data = json.loads(manifest.decode("utf-8", "replace"))
    out = []
    for p in (data.get("packages") or []) if isinstance(data, dict) else []:
        if not isinstance(p, dict) or p.get("type") != "path":
            continue
        d = str(p.get("dir") or "")
        norm = posixpath.normpath(d.replace("\\", "/")) if d else "."
        if posixpath.isabs(norm) or norm == ".." or norm.startswith("../") or norm == ".":
            continue
        parts = norm.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            mode = tree.get(prefix)
            if mode != "040000":
                what = ("a symlink" if mode and mode.startswith("120000") else
                        "a submodule (gitlink)" if mode == "160000" else
                        "a file" if mode else "not tracked")
                out.append(f"{p.get('name')} ({d}): {prefix} is {what} at the bound commit")
                break
        else:
            links = [q for q, m in tree.items() if m == "160000" and q.startswith(norm + "/")]
            if links:
                out.append(f"{p.get('name')} ({d}): contains a submodule (gitlink) {links[0]}")
    return out


def check_evidence_symlinks_at(repo: pathlib.Path, commit: str, formalization: str | None = None,
                               tree: dict[str, str] | None = None) -> None:
    """Refuse a bound commit with a committed symlink that resolves into .lean-receipts/ (or to the
    package's formalization.yaml): the evidence-only rule admits later edits there, so the symlink would
    let a source change pass as evidence."""
    links = evidence_symlinks(tree if tree is not None else bound_tree(repo, commit), formalization)
    if links:
        raise Fail(f"symlinks at {commit[:12]} point into evidence the binding rule lets change later: "
                   f"{', '.join(links)}. The receipt would not bind what they point to; replace them with the "
                   f"files (outside .lean-receipts/), then a new commit and a new receipt")


def check_path_deps_at(repo: pathlib.Path, commit: str, formalization: str | None = None) -> None:
    """Refuse a receipt whose bound commit lets the build read sources outside the package directory:
    a `path` dependency in lake-manifest.json (committed at the bound commit) that leaves the directory
    lexically, or that reaches through a symlink, a submodule or an untracked directory; and any
    committed symlink whose target is absolute or climbs with `..`. Judged on the commit's tree, never
    the working tree. A manifest that is absent is left to the other checks; one that is not valid JSON
    is refused."""
    tree = bound_tree(repo, commit)
    links = escaping_symlinks(tree)
    if links:
        raise Fail(f"symlinks at {commit[:12]} can leave the package directory: {', '.join(links)}. The "
                   f"receipt does not bind what they point to; replace them with the files (or a target "
                   f"that only descends, with no `..`), then a new commit and a new receipt")
    check_evidence_symlinks_at(repo, commit, formalization, tree)
    blob = git_show_bytes(repo, "./lake-manifest.json", commit)
    if blob is None:
        return
    try:
        outside = outside_path_deps(blob)
        problems = path_dep_tree_problems(tree, blob)
    except ValueError:
        raise Fail(f"lake-manifest.json at {commit[:12]} is not valid JSON; cannot check its path "
                   f"dependencies") from None
    if outside:
        raise Fail(f"lake-manifest.json at {commit[:12]} has path dependencies outside the package directory: "
                   f"{', '.join(outside)}. The receipt does not bind their sources, so an edit to them would "
                   f"never count as drift. Require them from git pinned to a rev (lake-manifest.json then "
                   f"pins it) or move them inside the package directory; then a new commit and a new receipt")
    if problems:
        raise Fail(f"lake-manifest.json at {commit[:12]} has path dependencies the receipt does not bind: "
                   f"{'; '.join(problems)}. A path dependency must be a directory tracked in the package "
                   f"itself; require it from git pinned to a rev instead; then a new commit and a new receipt")


def dirty_tracked(repo: pathlib.Path) -> list[str]:
    """Tracked files under the package directory that differ from HEAD (staged or not)."""
    p = git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".")
    return [x[3:] for x in p.stdout.splitlines() if x.strip()]


def dirty_guard(repo: pathlib.Path, name: str, refuse: bool) -> list[str]:
    """Evidence is judged at the bound commit, never the working tree; a dirty checkout is a WARN
    on stderr (the lab's workflow edits while gating) or, with --refuse-dirty, a refusal. A checkout
    off its default branch (or detached) is a second stderr WARN, never a refusal (#48)."""
    dirty = dirty_tracked(repo)
    if dirty:
        shown = ", ".join(dirty[:5]) + (", ..." if len(dirty) > 5 else "")
        msg = (f"{name}: {len(dirty)} tracked file(s) modified in the checkout ({shown}); "
               f"receipts are checked against the bound commit, not these edits")
        if refuse:
            raise Fail(f"--refuse-dirty: {msg}; commit or stash them first")
        print(f"WARN: {msg} (--refuse-dirty refuses)", file=sys.stderr)
    notice = branch_notice(repo)
    if notice:   # #48: informational, never a refusal; evidence commits are often made detached
        print(f"WARN: {notice}", file=sys.stderr)
    return dirty


COMPARATOR_CONFIG_RE = re.compile(r"^(comparator[^/]*\.json|comparator/config[^/]*\.json)$")


def cfg_name(path: str) -> str:
    """Same rule as comparator-check.sh / comparator-receipt: strip .json, '/' -> '_'."""
    base = path[:-5] if path.endswith(".json") else path
    return base.replace("/", "_")


def head_comparator_configs(repo: pathlib.Path, rev: str = "HEAD") -> list[str]:
    """Every comparator configuration tracked at <rev> (the set the runner checks)."""
    p = git(repo, "ls-tree", "-r", "--name-only", rev)
    if p.returncode != 0:
        return []
    return sorted(x for x in p.stdout.splitlines() if COMPARATOR_CONFIG_RE.match(x))


def _safe_rel(path, what: str) -> str:
    """A repo-relative path: no leading '/', no '..' component."""
    if not isinstance(path, str) or not path.strip():
        raise Fail(f"comparator {what} path is empty")
    pp = pathlib.PurePosixPath(path)
    if pp.is_absolute() or ".." in pp.parts:
        raise Fail(f"comparator {what} path must be repo-relative without '..': {path!r}")
    return path


_WARNED: set[str] = set()


def warn_once(msg: str) -> None:
    """A `lean-pkg: warning:` line, printed once per process (validators run many times per command)."""
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"lean-pkg: warning: {msg}", file=sys.stderr)


def sidecar_at(repo: pathlib.Path, rev: str = "HEAD") -> dict:
    """lean-pkg-signoff.json tracked at <rev> (holes.py), strictly parsed; empty when not tracked there.
    Raises ValueError on a malformed file."""
    return parse_sidecar(git_show_bytes(repo, SIGNOFF_SIDECAR, rev), f"{SIGNOFF_SIDECAR} at {rev[:12]}")


def validate_comparator(pkg: dict, repo: pathlib.Path, comp: dict,
                        receipt_commit: str | None) -> None:
    """Re-derive a comparator PASS from bytes (see DESIGN.md). Fails closed."""
    if not isinstance(comp, dict):
        raise Fail("comparator receipt must be a JSON object")
    for f in COMPARATOR_FIELDS:
        if f not in comp:
            raise Fail(f"comparator receipt missing required field {f!r}")
    if comp.get("test_only"):
        raise Fail("comparator receipt is marked test_only (produced under the test-only digest "
                   "override); it is not evidence")
    if comp.get("partial"):
        raise Fail("comparator receipt is marked partial (comparator-receipt --slots judged only a subset of "
                   "the slots); it is diagnostic, not evidence: run comparator-receipt without --slots")
    schema = comp["schema"]
    if schema not in COMPARATOR_SCHEMAS:
        raise Fail(f"comparator schema must be one of {COMPARATOR_SCHEMAS}, got {schema!r}")
    if schema == "comparator-receipt/1":
        mechanism = "legacy"
    else:
        mechanism = comp.get("mechanism")
        if mechanism not in COMPARATOR_MECHANISMS:
            raise Fail(f"comparator mechanism must be one of {COMPARATOR_MECHANISMS}, "
                       f"got {mechanism!r}")
    commit = str(comp["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Fail(f"comparator commit is not a full lowercase 40-hex sha: {commit!r}")
    head = git_head(repo)
    if head is None:
        raise Fail(f"cannot resolve HEAD in {repo}")
    ok, note, _docs = package_descendant(repo, commit, head, pkg)
    if not ok:
        raise Fail(f"comparator commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the comparator receipt must bind to HEAD or an evidence-only "
                   f"descendant ({note})")
    if receipt_commit and commit != receipt_commit:
        raise Fail(f"comparator commit {commit[:12]}… != lower-tier receipt commit "
                   f"{receipt_commit[:12]}… — both tiers must bind to one commit")
    check_toolchain_at(repo, commit, comp["lean_toolchain"], "comparator toolchain mismatch")
    check_evidence_symlinks_at(repo, commit, formalization_rel(pkg))
    tools = comp["tools"]
    if not isinstance(tools, dict):
        raise Fail("comparator tools must be a mapping")
    if mechanism == "legacy":
        for tname in COMPARATOR_TOOLS:
            v = str(tools.get(tname) or "")
            if not re.fullmatch(r"[0-9a-f]{40}", v):
                raise Fail(f"comparator tools.{tname} must be a 40-hex commit, got {v!r}")
    else:
        if not str(tools.get("lean_toolchain") or "").strip():
            raise Fail("comparator tools.lean_toolchain is empty (toolchain mechanism)")
        for tname in COMPARATOR_TOOLCHAIN_DIGESTS:
            v = str(tools.get(tname) or "")
            if not re.fullmatch(r"[0-9a-f]{64}", v):
                raise Fail(f"comparator tools.{tname} must be a 64-hex sha256, got {v!r}")
    for f in ("host", "job_id", "generated_at"):
        if not str(comp[f]).strip():
            raise Fail(f"comparator field {f!r} is empty")
    check_timestamp(comp["generated_at"], "comparator receipt")
    if comp["result"] != "pass":
        raise Fail(f"comparator result must be 'pass', got {comp['result']!r}")
    configs = comp["configs"]
    if not isinstance(configs, list) or not configs:
        raise Fail("comparator configs must be a non-empty list")
    allowed = set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS)
    listed = [_safe_rel(c.get("path"), "config") for c in configs if isinstance(c, dict)]
    expected = head_comparator_configs(repo, commit)
    if sorted(listed) != expected:
        missing = sorted(set(expected) - set(listed))
        extra = sorted(set(listed) - set(expected))
        raise Fail(f"comparator receipt must list exactly the configs tracked at "
                   f"{commit[:12]} (bound commit; HEAD {head[:12]}): "
                   f"missing {missing}, extra/duplicate {extra}")
    try:
        sidecar = sidecar_at(repo, commit)
    except ValueError as e:
        raise Fail(str(e)) from None
    for i, cfg in enumerate(configs):
        if not isinstance(cfg, dict):
            raise Fail(f"comparator configs[{i}] is not an object")
        for f in COMPARATOR_CONFIG_FIELDS:
            if f not in cfg:
                raise Fail(f"comparator configs[{i}] missing field {f!r}")
        path = _safe_rel(cfg["path"], "config")
        if cfg["result"] != "pass":
            raise Fail(f"comparator config {path} result must be 'pass', "
                       f"got {cfg['result']!r}")
        blob = git_show_bytes(repo, path, commit)
        if blob is None:
            raise Fail(f"comparator config {path} is not tracked at {commit[:12]} (HEAD {head[:12]})")
        if sha256_bytes(blob) != str(cfg["sha256"]).lower():
            raise Fail(f"comparator config {path} sha256 mismatch against bytes at {commit[:12]}")
        try:
            blob_cfg = json.loads(blob)
        except ValueError:
            blob_cfg = None
        if not isinstance(blob_cfg, dict):
            raise Fail(f"comparator config {path} at {commit[:12]} is not a JSON object")
        # #61: a list field the committed config omits (or sets to null) means [], exactly as the
        # runner records it; any other difference, ["a"] vs [] included, is still a refusal.
        for f in ("challenge_module", "solution_module", "theorem_names",
                  "definition_names", "permitted_axioms"):
            want = blob_cfg.get(f)
            if want is None and f in COMPARATOR_LIST_FIELDS:
                want = []
            if cfg[f] != want:
                raise Fail(f"comparator config {path}: receipt {f} differs from the "
                           f"config at {commit[:12]} ({cfg[f]!r} vs {want!r})")
        names = cfg["theorem_names"]
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) and n for n in names):
            raise Fail(f"comparator config {path}: theorem_names must be a "
                       f"non-empty list of strings")
        ax = cfg["permitted_axioms"]
        if not isinstance(ax, list) or not all(isinstance(a, str) for a in ax):
            raise Fail(f"comparator config {path}: permitted_axioms must be a "
                       f"list of strings")
        extra = sorted(set(ax) - allowed)
        if extra:
            raise Fail(f"comparator config {path}: permitted_axioms {extra} outside "
                       f"allowed set {sorted(allowed)}")
        # Definition holes (value-blind in comparator) need a human sign-off, judged against the
        # config bytes and lean-pkg-signoff.json at the bound commit, never the receipt's copy; a
        # copy the receipt does carry (`signoff`, which the page renders) must equal it.
        try:
            so = check_definition_holes(blob_cfg, f"comparator config {path}", cfg_rel=path,
                                        cfg_bytes=blob, sidecar=sidecar, warn=warn_once)
        except ValueError as e:
            raise Fail(str(e)) from None
        if "signoff" in cfg and cfg["signoff"] != so:
            raise Fail(f"comparator config {path}: the receipt's signoff copy differs from the sign-off "
                       f"in {SIGNOFF_SIDECAR} at {commit[:12]} (the receipt must record what was signed)")
        log_rel = _safe_rel(cfg["log"], "log")
        if not log_rel.startswith(".lean-receipts/"):
            raise Fail(f"comparator log must live under .lean-receipts/: {log_rel!r}")
        bound = f".lean-receipts/{commit[:12]}-comparator-{cfg_name(path)}.log"
        if log_rel != bound:
            raise Fail(f"comparator log for {path} must be {bound}, got {log_rel!r}")
        if (repo / ".lean-receipts").is_symlink():
            raise Fail(f"{repo}/.lean-receipts is a symlink; evidence must live inside the repo")
        log_p = repo / log_rel
        if log_p.is_symlink():
            raise Fail(f"comparator log {log_rel} is a symlink; evidence must be a regular file")
        if not log_p.is_file():
            # Field report #5: a comparator log is looked up only under the package checkout, never
            # beside the receipt (unlike the redacted verify log). The hint names where it looked and
            # how to bring preserved evidence back; the refusal itself is unchanged.
            raise Fail(f"comparator log missing: {log_rel} (package checkout {repo.absolute()}; "
                       f"tried {log_p.absolute()}). Comparator logs resolve relative to the package "
                       f"checkout only, never beside the receipt; if they were preserved elsewhere, "
                       f"restore each to the path its configs[].log names, byte-exact, and check it "
                       f"against its log_sha256 (docs/receipts.md, \"Restoring preserved evidence\"); "
                       f"nothing is rerun or resealed")
        log_bytes = log_p.read_bytes()
        # #21.2: a log produced after the bound commit cannot be read at that commit; once committed
        # (at HEAD, an evidence-only descendant), the committed bytes are the evidence, so a working
        # copy that differs from them is refused here rather than first at promote.
        committed_log = git_show_bytes(repo, log_rel, "HEAD")
        if committed_log is not None and committed_log != log_bytes:
            raise Fail(f"comparator log {log_rel} differs from its copy committed at HEAD {head[:12]}; "
                       f"the committed bytes are the evidence: restore them (git checkout -- {log_rel}) "
                       f"or commit the new log with a receipt that names it")
        if sha256_bytes(log_bytes) != str(cfg["log_sha256"]).lower():
            raise Fail(f"comparator log_sha256 mismatch for {log_rel}")
        lines = log_bytes.decode("utf-8", "replace").splitlines()
        if not comparator_pass("\n".join(lines), mechanism):
            raise Fail(f"comparator log {log_rel} has no PASS line for the {mechanism} mechanism "
                       f"({COMPARATOR_PASS_LINE!r} as a full line)")


def binding_lines(pkg: dict, repo: pathlib.Path, commit: str, toolchain, checker=None) -> list[str]:
    """The ✓ lines of a validated binding (#26.2): commit vs HEAD, the formalization.yaml rule, the
    toolchain at the bound commit. Called only after the validator passed, so every line is a pass."""
    head = git_head(repo) or ""
    _ok, note, docs = package_descendant(repo, commit, head, pkg, checker=checker)
    out = [f"✓ binding: commit {commit[:12]} is HEAD" if commit == head
           else docs_admission_line(head, commit, note) if docs is not None
           else f"✓ binding: HEAD {head[:12]}: {note}"]
    rel = formalization_rel(pkg)
    if rel and commit != head:
        changed = git(repo, "diff", "--no-renames", "--name-only", "--relative", commit, head, "--", rel)
        out.append(f"✓ {rel}: only derived keys changed since {commit[:12]} (evidence-only rule)"
                   if changed.stdout.strip() else f"✓ {rel}: unchanged since {commit[:12]}")
    elif not rel:
        out.append("✓ formalization.yaml: no registry pointer, so no exception was applied")
    out.append(f"✓ lean_toolchain {str(toolchain).strip()} = lean-toolchain committed at {commit[:12]}")
    if git_show_bytes(repo, "./lake-manifest.json", commit) is not None:
        out.append(f"✓ lake-manifest.json at {commit[:12]}: no path dependency outside the package directory")
    out.append(f"✓ no symlink at {commit[:12]} leaves the package directory or points into .lean-receipts/")
    return out


def receipt_check_lines(pkg: dict, repo: pathlib.Path, receipt: dict) -> list[str]:
    allowed = sorted(set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS))
    committed = committed_mathlib_rev(repo, receipt["commit"])
    mathlib = (f"✓ mathlib_rev {str(receipt['mathlib_rev']).strip()} = the mathlib rev in lake-manifest.json "
               f"committed at {receipt['commit'][:12]}" if committed
               else f"· mathlib_rev not checked: no readable mathlib entry in lake-manifest.json at "
                    f"{receipt['commit'][:12]}")
    return binding_lines(pkg, repo, receipt["commit"], receipt["lean_toolchain"], receipt.get("checker")) + [
        mathlib,
        "✓ sorry_count 0",
        f"✓ axioms {sorted(receipt['axioms'])} within {allowed}",
        f"✓ mathlib_rev, report_sha256, job_id {receipt['job_id']}, checker, generated_at well-formed"]


def build_provenance_line(bp: dict) -> str:
    """One line for a comparator receipt's additive build_provenance (#56); never validated."""
    src = bp.get("source")
    tail = f" (source: {src})" if src else ""
    # Lake's artifact cache writes oleans without compiling them: never counted as compiled (lake_hygiene)
    cached = [f"{bp[k]} {what}" for k, what in (("restored", "olean(s) restored, not compiled"),
                                                 ("cache_reused", "module(s) Lake reports reused from a cache"))
              if isinstance(bp.get(k), int) and bp[k]]
    cached_tail = f"; {', '.join(cached)}" if cached else ""
    if bp.get("compiled") is True:
        n = bp.get("modules_built")
        return (f"build: compiled {n} module(s)" if isinstance(n, int) else "build: compiled") + tail + cached_tail
    if bp.get("prebuilt") is True:
        return f"build: prebuilt (source: {src or 'not declared'})" + cached_tail
    if bp.get("compiled") is False:
        return "build: no build stage ran (skipped)" + tail
    return "build: provenance not recorded (the setup was prepared before build_provenance)" + tail


def comparator_check_lines(pkg: dict, repo: pathlib.Path, comp: dict, lower_commit: str | None,
                           with_binding: bool = True) -> list[str]:
    mech = "legacy" if comp.get("schema") == "comparator-receipt/1" else comp.get("mechanism")
    out = binding_lines(pkg, repo, comp["commit"], comp["lean_toolchain"]) if with_binding else []
    out.append(f"✓ comparator: {len(comp['configs'])} config(s) re-derived from bytes at {comp['commit'][:12]}, "
               f"each log PASS ({mech} mechanism)")
    out.append(f"✓ comparator commit = lower-tier receipt commit {lower_commit[:12]}" if lower_commit
               else "· no lower-tier receipt to pair with (commit equality not checked)")
    if isinstance(comp.get("build_provenance"), dict):   # #56: printed, never validated
        out.append(f"· {build_provenance_line(comp['build_provenance'])}")
    om = comp.get("olean_manifest")
    if isinstance(om, dict):                             # printed, never validated (no oleans here to rehash)
        out.append(f"· olean manifest: {om.get('files')} compiled module file(s), sha256 "
                   f"{str(om.get('sha256'))[:12]}… (captured at {om.get('captured')}; checked by the runner "
                   f"before and after the comparison)")
    return out


def claims_schema_at(name: str, pkg: dict, repo: pathlib.Path, commit: str) -> dict:
    """#58: the claims/1 schema check (claims.schema_check_text, the validator `claims sync` uses) on
    the package's claims file as tracked at the gated commit, never the working tree, then
    `claims sync`'s declaration check (claims.decl_problems) with each claim's file read at that same
    commit. {checked, commit, file, ok, claims, errors, declarations: {checked, ok, problems}}; `ok` is
    False on either kind of problem; checked False (ok null) when no file is tracked there."""
    from . import claims as claims_mod
    from . import index as idx
    rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
    blob = git_show_bytes(repo, rel, commit)
    unchecked = {"checked": False, "ok": None, "problems": []}
    if blob is None:
        return {"checked": False, "commit": commit, "file": rel, "ok": None, "claims": None, "errors": [],
                "declarations": unchecked}
    text = blob.decode("utf-8", errors="replace")
    n, errors = claims_mod.schema_check_text(text, rel, name)
    decls = unchecked
    try:
        listed = claims_mod.load_claims_text(text, rel).get("claims")
    except Fail:
        listed = None
    # decl_problems needs a claim's id, decl and file; with schema errors present, only the claims
    # whose three fields are well formed are looked up (no other rule is added).
    usable = [c for c in (listed if isinstance(listed, list) else [])
              if isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("decl"), str)
              and idx._is_rel(c.get("file")) and str(c["file"]).endswith(".lean")]
    if usable:
        read = claims_mod._reader(repo, commit)
        problems = claims_mod.decl_problems(read, usable) + claims_mod.evidence_problems(read, usable)
        decls = {"checked": True, "ok": not problems, "problems": problems}
    return {"checked": True, "commit": commit, "file": rel, "ok": not errors and decls["ok"] is not False,
            "claims": n, "errors": errors, "declarations": decls}


def claims_problems(cs: dict) -> list[str]:
    """Schema errors first, then declaration problems in `claims sync`'s wording."""
    return list(cs["errors"]) + list(cs.get("declarations", {}).get("problems", []))


def claims_schema_line(cs: dict) -> str:
    at = f"{cs['file']} at {str(cs['commit'])[:12]}"
    if not cs["checked"]:
        return f"· no {cs['file']} tracked at {str(cs['commit'])[:12]} (claims schema not checked)"
    if cs["ok"]:
        found = ", declarations found" if cs["declarations"]["checked"] else ""
        return f"✓ {at}: schema ok ({cs['claims']} claims){found}"
    problems = claims_problems(cs)
    more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
    return f"✗ {at}: {problems[0]}{more}"


def load_json_object(path_arg, what: str) -> tuple[pathlib.Path, dict]:
    p = pathlib.Path(str(path_arg)).expanduser()
    if not p.is_file():
        raise Fail(f"{what} not found: {p}")
    try:
        obj = json.loads(p.read_text())
    except ValueError as e:
        raise Fail(f"{what} is not valid JSON: {e}")
    if not isinstance(obj, dict):
        raise Fail(f"{what} must be a JSON object")
    return p, obj


def locate_evidence_arg(path_arg, what: str, name: str, repo: pathlib.Path,
                        resolved: list | None = None) -> pathlib.Path:
    """The file a --receipt / --comparator argument names (field report #5). A path that exists as given
    (relative to the caller's cwd, or absolute) is used as given. A relative path that does not exist
    from the cwd but exists under the package's registered checkout (or --repo DIR) is resolved there,
    and the resolution is announced on stderr and appended to `resolved`: the same relative path anchored
    at the checkout, never another matching receipt. Otherwise the refusal names the caller's cwd, the
    checkout, every path tried and how to pass the file."""
    p = pathlib.Path(str(path_arg)).expanduser()
    if p.is_file() or p.is_absolute():
        return p
    under = repo / p
    if under.is_file():
        note = (f"{name}: {what} {path_arg} does not exist relative to the cwd {pathlib.Path.cwd()}; "
                f"resolved against the package checkout: {under}")
        print(f"lean-pkg: note: {note}", file=sys.stderr)
        if resolved is not None:
            resolved.append({"what": what, "arg": str(path_arg), "path": str(under), "base": str(repo)})
        return under
    raise Fail(f"{what} not found: {path_arg} (caller cwd {pathlib.Path.cwd()}; package checkout {repo}; "
               f"tried {pathlib.Path.cwd() / p} and {under}). Pass an absolute path, run from the checkout, "
               f"or give a path relative to the checkout; for `gate --check` / `comparator --check` on "
               f"another checkout pass --repo DIR")


def replaced_comparator_rel(repo: pathlib.Path, commit: str) -> str:
    """Where `comparator --replace` keeps the older receipt for `commit` (#21.1): beside it, never
    overwritten: .lean-receipts/<c12>-comparator.replaced-<UTC stamp>[-N].json."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f".lean-receipts/{commit[:12]}-comparator.replaced-{stamp}"
    rel, n = f"{base}.json", 1
    while (repo / rel).exists() or (repo / rel).is_symlink():
        n += 1
        rel = f"{base}-{n}.json"
    return rel


def evidence_dest_problem(repo: pathlib.Path, dest_rel: str) -> str | None:
    """Why gate/comparator must not write `dest_rel` (under .lean-receipts/): .lean-receipts itself or the
    destination is a symlink (a planted link would carry the write outside the repository), or the
    directory is not a directory. None when the write may go ahead."""
    d = repo / ".lean-receipts"
    if d.is_symlink():
        return f"{d} is a symlink; refusing to write evidence through it (nothing copied or recorded)"
    if d.exists() and not d.is_dir():
        return f"{d} is not a directory (nothing copied or recorded)"
    dest = repo / dest_rel
    if dest.is_symlink():
        return f"{dest} is a symlink; refusing to write through it (remove it; nothing copied or recorded)"
    if dest.exists() and not dest.is_file():
        return f"{dest} is not a regular file (nothing copied or recorded)"
    return None


def plan_comparator_copy(repo: pathlib.Path, commit: str, src: pathlib.Path,
                         replace: bool = False) -> dict:
    """Every check copy_comparator makes, before anything is written (gate runs it before it copies the
    lower-tier receipt, so a refusal writes nothing): {dest_rel, action: present | copy | replace, moved}."""
    dest_rel = f".lean-receipts/{commit[:12]}-comparator.json"
    dest = repo / dest_rel
    why = evidence_dest_problem(repo, dest_rel)
    if why:
        raise Fail(why)
    if not dest.exists():
        return {"dest_rel": dest_rel, "action": "copy", "moved": None}
    if recorded_json(repo, dest_rel) == json.loads(src.read_text()):
        return {"dest_rel": dest_rel, "action": "present", "moved": None}
    if not replace:
        raise Fail(f"{dest} already holds a different comparator receipt for commit {commit[:12]}; "
                   f"to keep it, pass that file (--receipt {dest}); to record the newer one, re-run "
                   f"with --replace (the older file is kept beside it as "
                   f".lean-receipts/{commit[:12]}-comparator.replaced-<UTC stamp>.json and the event "
                   f"is comparator-replaced)")
    moved = replaced_comparator_rel(repo, commit)
    return {"dest_rel": dest_rel, "action": "replace", "moved": moved}


def apply_comparator_copy(repo: pathlib.Path, plan: dict, src: pathlib.Path) -> tuple[str, str, str | None]:
    """(path of the receipt in the repo, note, where the replaced older receipt now lives or None)."""
    dest_rel, moved = plan["dest_rel"], plan["moved"]
    dest = repo / dest_rel
    if plan["action"] == "present":
        return dest_rel, f"comparator receipt already present at {dest_rel} (not re-copied)", None
    if moved:
        os.replace(dest, repo / moved)
    dest.parent.mkdir(exist_ok=True)
    shutil.copy2(src, dest)
    note = (f"comparator receipt copied to {dest} — NOT committed there; "
            f"commit it with its logs in the verified repo yourself")
    if moved:
        note += f"; the older receipt for this commit is kept at {moved} (commit that too)"
    return dest_rel, note, moved


def copy_comparator(repo: pathlib.Path, commit: str, src: pathlib.Path,
                    replace: bool = False) -> tuple[str, str, str | None]:
    """(path of the receipt in the repo, note, where the replaced older receipt now lives or None)."""
    return apply_comparator_copy(repo, plan_comparator_copy(repo, commit, src, replace), src)


REPORT_LOG_SUFFIX = "-verify.redacted.log"
# A compressed sealed log (`receipt slurm|systemd --compress-log`) is `<log name>.gz`: deterministic gzip
# (level 9, mtime 0, no file name in the header). report_sha256 stays the SHA-256 of the UNCOMPRESSED log;
# the receipt's additive `report_compressed` {format: gzip, sha256: <of the .gz>, bytes: <uncompressed
# length>} binds the .gz itself and bounds its decompression.
REPORT_GZ_SUFFIX = ".gz"
REPORT_GZ_LEVEL = 9
# The decompression bound for a .gz whose receipt records no `report_compressed.bytes` (a log compressed by
# hand for an older receipt): output past it is refused, never hashed further (gzip-bomb guard).
REPORT_GZ_DEFAULT_CAP = 4 << 30
_CHUNK = 1 << 20


def gzip_deterministic(data: bytes) -> bytes:
    """`data` as gzip with a fixed header (no file name, mtime 0, OS byte 255) at level 9: the same bytes
    for the same input and zlib, on every host and at every time."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=REPORT_GZ_LEVEL, fileobj=buf, mtime=0) as g:
        g.write(data)
    return buf.getvalue()


def gunzip_digest(src, limit: int, what: str) -> tuple[str, int]:
    """(sha256, length) of the decompressed stream of `src` (a path or bytes), read in chunks and never
    past `limit` bytes of output: more refuses (gzip-bomb guard), and so does a stream that is not valid
    gzip (bad header, CRC or length check, truncated)."""
    h, n = hashlib.sha256(), 0
    try:
        with (open(src, "rb") if isinstance(src, (str, os.PathLike)) else io.BytesIO(src)) as raw, \
                gzip.GzipFile(fileobj=raw, mode="rb") as g:
            while True:
                chunk = g.read(min(_CHUNK, limit - n + 1))
                if not chunk:
                    break
                n += len(chunk)
                if n > limit:
                    raise Fail(f"{what} decompresses to more than the {limit} bytes the receipt allows "
                               f"(report_compressed.bytes); refused without reading further")
                h.update(chunk)
    except (OSError, EOFError, zlib.error) as e:
        raise Fail(f"{what} is not a valid gzip stream ({e.__class__.__name__}: {e}); the compressed log "
                   f"cannot be checked against report_sha256") from None
    return h.hexdigest(), n


def report_compressed_bound(receipt: dict) -> tuple[int, str | None]:
    """(decompression limit, the .gz SHA-256 or None) from the receipt's optional `report_compressed`;
    a malformed value refuses. It never replaces report_sha256, which is always the uncompressed hash."""
    rc = receipt.get("report_compressed")
    if rc is None:
        return REPORT_GZ_DEFAULT_CAP, None
    if not isinstance(rc, dict) or rc.get("format") != "gzip":
        raise Fail("report_compressed must be {format: \"gzip\", sha256, bytes}, got "
                   f"{json.dumps(rc)[:80]}")
    gz_sha, size = rc.get("sha256"), rc.get("bytes")
    if not (isinstance(gz_sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", gz_sha)):
        raise Fail("report_compressed.sha256 must be 64 hex chars (the SHA-256 of the .gz file)")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise Fail("report_compressed.bytes must be a non-negative integer (the uncompressed log's length)")
    if gz_sha.lower() == str(receipt.get("report_sha256", "")).lower():
        raise Fail("report_compressed.sha256 equals report_sha256: report_sha256 must be the SHA-256 of the "
                   "uncompressed log, never of the .gz")
    return size, gz_sha.lower()


def _file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def report_log_name(receipt: dict) -> str:
    return f"{str(receipt['commit'])[:12]}-{receipt_job_slug(receipt['job_id'])}{REPORT_LOG_SUFFIX}"


def report_log_check(repo: pathlib.Path, receipt: dict, receipt_path: pathlib.Path) -> tuple[str, dict]:
    """report_sha256 against the redacted verify log `receipt slurm|systemd` writes beside the receipt
    (<c12>-<job slug>-verify.redacted.log, or that name + .gz with --compress-log): looked for beside the
    --receipt file, in the package's .lean-receipts/ and tracked there at HEAD. Every copy found must hash
    to report_sha256 (a mismatch refuses); a .gz copy is stream-decompressed (bounded by
    report_compressed.bytes) and its UNCOMPRESSED bytes must hash to report_sha256, with that length, and
    the .gz itself to report_compressed.sha256 when recorded. None found, or a copy that cannot be read,
    is a refusal too (report_log_missing): a receipt whose report_sha256 binds nothing is not evidence.
    (line, {checked, log, where})."""
    name = report_log_name(receipt)
    gz = name + REPORT_GZ_SUFFIX
    want = str(receipt["report_sha256"]).lower()
    limit, gz_want = report_compressed_bound(receipt)
    found: list[tuple[str, str, object]] = []      # (where, file name, path or bytes)
    seen: set[str] = set()
    for where, base in (("beside the receipt", receipt_path.parent), (".lean-receipts/", repo / ".lean-receipts")):
        for fname in (name, gz):
            path = base / fname
            key = os.path.realpath(path)
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            found.append((where, fname, path))
    for fname in (name, gz):
        blob = git_show_bytes(repo, f"./.lean-receipts/{fname}", "HEAD")
        if blob is not None:
            found.append(("tracked at HEAD", fname, blob))
    if not found:
        raise Fail(report_log_missing(repo, receipt, receipt_path))
    try:
        wheres = _report_log_verify(found, name, gz, want, limit, gz_want)
    except OSError as e:
        raise Fail(report_log_missing(repo, receipt, receipt_path,
                                      f"a copy cannot be read ({e.__class__.__name__}: {e.strerror or e})")) from None
    return (f"✓ report_sha256 = sha256 of {name} ({', '.join(wheres)})",
            {"checked": True, "log": name, "where": wheres})


REPORT_LOG_RESTORE = 'docs/receipts.md, "Restoring preserved evidence"'
REPORT_LOG_MISSING = "report log missing: "


def report_log_rels(repo: pathlib.Path, receipt: dict) -> list[str]:
    """The sealed log copies (.log, .log.gz) of a lower-tier receipt present under the package's
    .lean-receipts/ (package-relative): the files `promote` needs committed beside the receipt."""
    try:
        name = report_log_name(receipt)
    except (Fail, KeyError):
        return []
    return [f".lean-receipts/{f}" for f in (name, name + REPORT_GZ_SUFFIX) if (repo / ".lean-receipts" / f).is_file()]


def recorded_report_log_problem(repo: pathlib.Path, receipt: dict, rel: str) -> str | None:
    """report_log_check for a recorded lower-tier receipt (`rel`, package-relative): None when its sealed
    log is there and hashes to report_sha256, else the refusal text (missing, unreadable or mismatched).
    Every consumer that judges a recorded receipt (ledger, next, comparator, palomar, promote) uses this, so
    none accepts a receipt `gate` would refuse."""
    try:
        report_log_check(repo, receipt, repo / rel)
    except Fail as e:
        return str(e)
    return None


def report_log_missing(repo: pathlib.Path, receipt: dict, receipt_path: pathlib.Path,
                       why: str = "none found") -> str:
    """The refusal for a lower-tier receipt whose redacted verify log is not there to hash: where it was
    looked for, the path it must be restored to, and how (byte-exact, never rerun or resealed)."""
    name = report_log_name(receipt)
    want = str(receipt.get("report_sha256") or "").lower()
    return (f"{REPORT_LOG_MISSING}{name} (or {name}{REPORT_GZ_SUFFIX}): {why}; looked beside the receipt "
            f"({receipt_path.parent.absolute()}), in {(repo / '.lean-receipts').absolute()} and tracked there at "
            f"HEAD. Without it report_sha256 binds nothing, so the receipt is not evidence. Restore the sealed "
            f"redacted log byte-exact to .lean-receipts/{name} (a copy kept under another name may be copied "
            f"there when its SHA-256 is the receipt's report_sha256 {want[:16]}...) and commit it ({REPORT_LOG_RESTORE}); "
            f"never rerun or reseal it. A receipt whose log is lost needs a new run")


def _report_log_verify(found: list, name: str, gz: str, want: str, limit: int, gz_want: str | None) -> list[str]:
    """Hash every copy report_log_check found; a mismatch refuses. [where]; OSError propagates."""
    wheres = []
    for where, fname, src in found:
        if fname == name:
            got = _file_sha256(src) if isinstance(src, pathlib.Path) else sha256_bytes(src)
            if got != want:
                raise Fail(f"report_sha256 mismatch: {name} ({where}) hashes to {got}, the receipt says {want}; "
                           f"the redacted log is not the one the receipt binds")
            wheres.append(where)
            continue
        if gz_want is not None:
            got = _file_sha256(src) if isinstance(src, pathlib.Path) else sha256_bytes(src)
            if got != gz_want:
                raise Fail(f"report_compressed.sha256 mismatch: {gz} ({where}) hashes to {got}, the receipt says "
                           f"{gz_want}; the compressed log is not the one the receipt binds")
        got, n = gunzip_digest(src, limit, f"{gz} ({where})")
        if got != want:
            raise Fail(f"report_sha256 mismatch: {gz} ({where}) decompresses to bytes hashing to {got}, the receipt "
                       f"says {want}; the redacted log is not the one the receipt binds (report_sha256 is the "
                       f"SHA-256 of the uncompressed log, never of the .gz)")
        if gz_want is not None and n != limit:
            raise Fail(f"report_compressed.bytes mismatch: {gz} ({where}) decompresses to {n} bytes, the receipt "
                       f"says {limit}")
        wheres.append(f"{where}, decompressed from .gz")
    return wheres


def plan_report_log_copy(repo: pathlib.Path, receipt: dict, receipt_path: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """The sealed log copies `gate` adds beside the canonical receipt .lean-receipts/<c12>-<job>.json, so
    that copy still verifies (field report: a receipt passed from a nested or outside path left its log
    behind): each form (.log, .log.gz) found beside the --receipt file, when .lean-receipts/ holds neither.
    Run after report_log_check (which verified every copy). [(source, dest_rel)]; refusals as for the
    receipt copy (a symlinked .lean-receipts or destination)."""
    name = report_log_name(receipt)
    canon = repo / ".lean-receipts"
    src_dir = receipt_path.parent
    if canon.is_dir() and os.path.realpath(src_dir) == os.path.realpath(canon):
        return []
    forms = (name, name + REPORT_GZ_SUFFIX)
    plan = [(src_dir / f, f".lean-receipts/{f}") for f in forms if (src_dir / f).is_file()]
    if not plan:
        return []
    for f in forms:
        why = evidence_dest_problem(repo, f".lean-receipts/{f}")
        if why:
            raise Fail(why)
    if any((canon / f).exists() for f in forms):
        return []
    return plan


def receipt_private_strings(blobs: list[tuple[str, bytes]], refuse: bool,
                            sink: list | None = None) -> list[dict]:
    """Scan receipt bytes (keys and values) for private strings before they are copied into the
    repository. Each hit is {"field", "kind"} (the private text itself is never echoed). With
    `refuse`, any hit is a refusal; otherwise the hits are returned (and appended to `sink`) and
    the caller prints them with warn_private once it knows what happened to the receipt."""
    from .index import private_string_hits
    hits = [{"field": f, "kind": k} for where, blob in blobs
            for f, k, _ in private_string_hits(blob, where)]
    if hits and refuse:
        raise Fail("--refuse-private: receipt holds private strings, nothing copied: "
                   + "; ".join(f"{h['field']} ({h['kind']})" for h in hits))
    if sink is not None:
        sink.extend(hits)
    return hits


# What happened to the receipt, by outcome event, for the private-string WARN line. A receipt that was
# accepted says "kept"; only a refusal for another reason says nothing was copied (field report: the old
# default read "refused" on a receipt `gate --check` had accepted).
PRIVATE_WARN_OUTCOME = {
    "gate-passed": "kept: the receipt is now in .lean-receipts/ as is",
    "regated": "kept: the receipt is now in .lean-receipts/ as is",
    "comparator-attached": "kept: the receipt is now in .lean-receipts/ as is",
    "comparator-replaced": "kept: the receipt is now in .lean-receipts/ as is",
    "already-gated": "kept: already recorded; nothing copied now",
    "already-attached": "kept: already recorded; nothing copied now",
    "gate-checked": "not refused for it; nothing copied (--check)",
    "comparator-checked": "not refused for it; nothing copied (--check)",
    None: "nothing copied: refused for another reason, below",
}


def warn_private(hits: list[dict], event: str | None) -> None:
    outcome = PRIVATE_WARN_OUTCOME.get(event, PRIVATE_WARN_OUTCOME[None])
    for h in hits:
        print(f"WARN: receipt field {h['field']} contains a {h['kind']} "
              f"({outcome}; --refuse-private refuses such a receipt)", file=sys.stderr)


def _with_private_warnings(fn, *args, **kwargs) -> tuple[list[str], dict]:
    """Run do_gate/do_comparator's body and print the private-string WARN lines worded for
    the outcome: copied, already recorded, --check, or refused for another reason."""
    sink: list[dict] = []
    try:
        out, payload = fn(*args, private_sink=sink, **kwargs)
    except Fail:
        warn_private(sink, None)
        raise
    warn_private(sink, payload.get("event"))
    return out, payload


def recorded_json(repo: pathlib.Path, rel) -> dict | None:
    if not rel:
        return None
    try:
        return json.loads((repo / rel).read_text())
    except Exception:
        return None


REGATE_STATUSES = ("verified-private", "public")


def to_gate_pending_cmd(name: str, commit: str) -> str:
    """The exact move an `active` package needs before its first gate (field report: the receipt's
    `next:` hint led to a gate refusal that did not say how to get to gate-pending)."""
    what = f"receipt for {commit[:12]} ready" if commit else "receipt ready"
    return f'`lean-pkg update {name} --status gate-pending --reason "{what}"`'


def regate_status_problem(name: str, pkg: dict) -> str | None:
    """Why `gate --regate` cannot apply to this package (#67), or None: it re-gates a gated package, so the
    status is verified-private (or public, which `gate` already accepts) and a gate receipt is recorded."""
    status = pkg.get("status")
    if status not in REGATE_STATUSES:
        return (f"{name}: --regate re-gates a gated package (verified-private or public), got {status!r}; "
                f"use `lean-pkg gate {name} --receipt R` without --regate")
    if not (pkg.get("gate") or {}).get("receipt"):
        return (f"{name}: --regate: no gate receipt is recorded, so there is nothing to re-gate; "
                f"use `lean-pkg gate {name} --receipt R` without --regate")
    return None


def regate_lineage(name: str, pkg: dict, repo: pathlib.Path, commit: str,
                   reason: str | None) -> tuple[str | None, bool, str | None]:
    """(previous_commit, non_descendant, problem) for `gate --regate` (#67): the new receipt's commit must
    descend from the commit of the recorded gate receipt (`git merge-base --is-ancestor`); a --reason
    accepts any other commit, recorded as non_descendant. An unreadable recorded receipt has no commit to
    compare, which needs a --reason too."""
    rec = recorded_json(repo, (pkg.get("gate") or {}).get("receipt"))
    prev = str(rec["commit"]) if isinstance(rec, dict) and rec.get("commit") else None
    if prev and _is_ancestor(repo, prev, commit):
        return prev, False, None
    if reason:
        return prev, True, None
    if prev is None:
        return None, False, (f"{name}: --regate: the recorded gate receipt "
                             f"{(pkg.get('gate') or {}).get('receipt')!r} is missing or has no commit, so the "
                             f"new commit cannot be checked as its descendant; pass --reason TEXT to re-gate "
                             f"anyway (recorded as non_descendant)")
    return prev, False, (f"{name}: --regate: receipt commit {commit[:12]} is not a descendant of the gated "
                         f"commit {prev[:12]}; pass --reason TEXT to re-gate it anyway (recorded as "
                         f"non_descendant: true)")


def _same_rev(a: str, b: str) -> bool:
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def committed_mathlib_rev(repo: pathlib.Path, commit: str) -> str | None:
    """The mathlib package's rev in lake-manifest.json committed at `commit`; None when unreadable."""
    blob = git_show_bytes(repo, "./lake-manifest.json", commit)
    try:
        data = json.loads(blob.decode("utf-8")) if blob is not None else None
    except (ValueError, UnicodeDecodeError):
        return None
    for p in (data.get("packages") or []) if isinstance(data, dict) else []:
        if isinstance(p, dict) and str(p.get("name", "")).lower() in ("mathlib", "«mathlib»") and p.get("rev"):
            return str(p["rev"]).strip()
    return None


def receipt_pin_changes(pkg: dict, repo: pathlib.Path, receipt: dict) -> tuple[dict, dict]:
    """(changes, kept) for the package's lean_toolchain and mathlib_pin against the receipt's lean_toolchain and
    mathlib_rev (field report: a deliberate pin bump, gated, left `list` on the old pin). A receipt value is
    adopted only when it agrees with the file committed at the receipt's commit (`lean-toolchain`, the mathlib
    rev in `lake-manifest.json`); otherwise the recorded value is kept and `kept` says why, {field: {receipt,
    manifest}}; never a refusal. validate_receipt already refuses a mathlib_rev that disagrees with a readable
    manifest, so `kept` for mathlib_pin is only the case of no readable mathlib entry (manifest None). mathlib_pin keeps register's 10-character form; revs that are prefixes of one
    another are equal."""
    changes, kept = {}, {}
    commit = str(receipt.get("commit") or "")
    tc = str(receipt.get("lean_toolchain") or "").strip()
    if tc and pkg.get("lean_toolchain") != tc:
        blob = git_show_bytes(repo, "./lean-toolchain", commit) if commit else None
        committed = blob.decode("utf-8", "replace").strip() if blob is not None else None
        if committed == tc:
            changes["lean_toolchain"] = {"from": pkg.get("lean_toolchain"), "to": tc}
        else:
            kept["lean_toolchain"] = {"receipt": tc, "manifest": committed}
    rev = str(receipt.get("mathlib_rev") or "").strip()
    old = str(pkg.get("mathlib_pin") or "")
    if rev and not _same_rev(rev, old):
        committed = committed_mathlib_rev(repo, commit) if commit else None
        if committed is None:
            kept["mathlib_pin"] = {"receipt": rev, "manifest": None}
        elif _same_rev(rev, committed):
            changes["mathlib_pin"] = {"from": pkg.get("mathlib_pin"), "to": rev[:10]}
        # a disagreeing rev never reaches here: validate_receipt refuses it
    return changes, kept


def pins_kept_lines(name: str, kept: dict, commit: str) -> list[str]:
    files = {"lean_toolchain": ("lean_toolchain", "lean-toolchain"), "mathlib_pin": ("mathlib_rev", "lake-manifest.json")}
    out = []
    for field, v in kept.items():
        key, f = files[field]
        found = v["manifest"] if v["manifest"] is not None else "unreadable"
        out.append(f"{name}: pins kept: receipt {key} {v['receipt']} does not match {f} at {commit[:12]} ({found})")
    return out


def pin_changes_text(changes: dict) -> str:
    return ", ".join(f"{f} {v['from'] or '-'} -> {v['to']}" for f, v in changes.items())


def do_gate(reg_path, reg, events_p, name, receipt_arg,
            comparator_arg=None, refuse_private: bool = False,
            refuse_dirty: bool = False, formalization_path: str | None = None,
            regate: bool = False, reason: str | None = None) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_gate, reg_path, reg, events_p, name, receipt_arg,
                                  comparator_arg, refuse_private, refuse_dirty=refuse_dirty,
                                  formalization_path=formalization_path, regate=regate, reason=reason)


def do_gate_check(reg, name, receipt_arg, comparator_arg=None, refuse_private: bool = False,
                  refuse_dirty: bool = False, regate: bool = False,
                  reason: str | None = None) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_gate_check, reg, name, receipt_arg, comparator_arg, refuse_private,
                                  refuse_dirty=refuse_dirty, regate=regate, reason=reason)


def _do_gate_check(reg, name, receipt_arg, comparator_arg=None, refuse_private: bool = False,
                   private_sink=None, refuse_dirty: bool = False, regate: bool = False,
                   reason: str | None = None) -> tuple[list[str], dict]:
    """`gate NAME --receipt R --check` (#26.2): every validation `gate` makes, against HEAD; writes
    nothing, records no event, works at any status. The mirror of `comparator --check`."""
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    dirty = dirty_guard(repo, name, refuse_dirty)
    resolved: list[dict] = []
    rp, receipt = load_json_object(locate_evidence_arg(receipt_arg, "receipt", name, repo, resolved), "receipt")
    cp = comp = None
    if comparator_arg is not None:
        cp, comp = load_json_object(locate_evidence_arg(comparator_arg, "comparator receipt", name, repo,
                                                        resolved), "comparator receipt")
    private = receipt_private_strings([("receipt", rp.read_bytes())]
                                      + ([("comparator", cp.read_bytes())] if cp else []),
                                      refuse_private, private_sink)
    validate_receipt(pkg, repo, receipt)
    commit = receipt["commit"]
    report_line, report_view = report_log_check(repo, receipt, rp)
    log_copy = plan_report_log_copy(repo, receipt, rp)
    report_view["copy"] = [d for _s, d in log_copy]
    checks = [f"· {r['what']} {r['arg']} resolved against the package checkout: {r['path']}" for r in resolved]
    checks += receipt_check_lines(pkg, repo, receipt) + [report_line]
    if log_copy:
        checks.append(f"· gate would also copy {', '.join(report_view['copy'])} beside the canonical receipt "
                      f"(the log beside --receipt; .lean-receipts/ holds no copy)")
    if comp is not None:
        validate_comparator(pkg, repo, comp, commit)
        checks += comparator_check_lines(pkg, repo, comp, commit, with_binding=False)
    cs = claims_schema_at(name, pkg, repo, commit)
    checks.append(claims_schema_line(cs))
    docs_view, _docs_line = docs_admission(repo, commit, pkg, receipt.get("checker"))
    status = pkg.get("status")
    lineage_ok = True
    regate_view = None
    if regate:   # #67: the checks `gate --regate` adds, without writing
        problem = regate_status_problem(name, pkg)
        if problem is None:
            prev, non_desc, problem = regate_lineage(name, pkg, repo, commit, reason)
            regate_view = {"previous_commit": prev, "non_descendant": non_desc}
            if problem is None:
                checks.append(f"✓ regate: {commit[:12]} " + (
                    f"is not a descendant of the gated commit {str(prev)[:12]} (accepted with --reason)"
                    if non_desc and prev else "accepted with --reason (no readable gated commit)"
                    if non_desc else f"descends from the gated commit {str(prev)[:12]}"))
        if problem is not None:
            lineage_ok = False
            checks.append(f"✗ regate: {problem.split(': ', 1)[-1]}")
    elif status not in ("gate-pending", "public"):
        checks.append(f"· status {status}: `gate` itself needs gate-pending (or public)"
                      + (f"; re-gate with `lean-pkg gate {name} --receipt R --regate`"
                         if status == "verified-private" else "")
                      + (f"; first: {to_gate_pending_cmd(name, commit)}" if status == "active" else ""))
    pins, pins_kept = receipt_pin_changes(pkg, repo, receipt)
    if pins:
        checks.append(f"· pins: gate would record {pin_changes_text(pins)} (from the receipt)")
    checks += [f"· {line.split(': ', 1)[1]}" for line in pins_kept_lines(name, pins_kept, commit)]
    ok = cs["ok"] is not False and lineage_ok
    msg = (f"{name}: receipt valid at commit {commit[:12]}"
           + (f" with comparator ({len(comp['configs'])} config(s))" if comp else "")
           + ("" if cs["ok"] is not False else f"; {cs['file']} FAILS the claims "
                  + ("schema" if cs["errors"] else "declaration check") + " (`gate` would refuse)")
           + ("" if lineage_ok else "; `gate --regate` would refuse (see the regate line)")
           + " — not recorded (--check)")
    payload = {"ok": ok, "name": name, "event": "gate-checked", "recorded": False, "commit": commit,
               "head": git_head(repo), "status": status, "checks": checks,
               "comparator": ({"commit": comp["commit"], "configs": len(comp["configs"])} if comp else None),
               "private_strings": private, "dirty": dirty, "claims_schema": cs, "pins": pins,
               "pins_kept": pins_kept, "report_log": report_view, "resolved_paths": resolved, **docs_view}
    if regate:
        payload["regate"] = regate_view
    return [msg] + [f"  {c}" for c in checks], payload


def _do_gate(reg_path, reg, events_p, name, receipt_arg, comparator_arg=None,
             refuse_private: bool = False, private_sink=None,
             refuse_dirty: bool = False, formalization_path: str | None = None,
             regate: bool = False, reason: str | None = None) -> tuple[list[str], dict]:
    """formalization_path (#26.4): the formalization.yaml `--refresh-formalization` is about to own and
    point the registry at; when no pointer is recorded yet, the evidence rule's derived-only exception
    is judged for that path (the refresh records it as the pointer right after the gate)."""
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    vpkg = pkg if (formalization_rel(pkg) or not formalization_path) \
        else {**pkg, "formalization_yaml": formalization_path}
    dirty = dirty_guard(repo, name, refuse_dirty)
    resolved: list[dict] = []
    rp, receipt = load_json_object(locate_evidence_arg(receipt_arg, "receipt", name, repo, resolved), "receipt")
    cp = comp = None
    if comparator_arg is not None:
        cp, comp = load_json_object(locate_evidence_arg(comparator_arg, "comparator receipt", name, repo,
                                                        resolved), "comparator receipt")
    private = receipt_private_strings([("receipt", rp.read_bytes())]
                                      + ([("comparator", cp.read_bytes())] if cp else []),
                                      refuse_private, private_sink)
    gate = pkg.get("gate") or {}
    # Idempotent retry: an identical receipt (pair) already recorded is a
    # no-op success, so agent retries are safe.
    recorded = recorded_json(repo, gate.get("receipt"))
    if recorded is not None and recorded == receipt:
        rec_comp = recorded_json(repo, gate.get("comparator"))
        if comp is None or (rec_comp is not None and rec_comp == comp):
            payload = {"ok": True, "name": name, "event": "already-gated",
                       "status": pkg.get("status"), "gate": pkg.get("gate"),
                       "receipt": gate.get("receipt"), "private_strings": private,
                       "dirty": dirty}
            return ([f"{name}: already gated (receipt {gate.get('receipt')}); "
                     f"nothing to do"], payload)
        raise Fail(f"{name}: already gated with this receipt; to add or replace "
                   f"comparator evidence use `lean-pkg comparator {name} --receipt ...`")
    # gate-pending is the designed entry. A package that was public before it was
    # ever gated (for example a collaborator-owned repository registered as public)
    # may also receive evidence: the status stays public, the receipt and the
    # gate-passed event are recorded. verified-private re-certifies through the
    # manual move back to gate-pending, or in one step with --regate (#67).
    if regate:
        problem = regate_status_problem(name, pkg)
        if problem:
            raise Fail(problem)
    elif pkg.get("status") not in ("gate-pending", "public"):
        raise Fail(f"{name}: gate requires status gate-pending (or public, for "
                   f"evidence on an already-public package), got {pkg.get('status')!r}"
                   + (f"; to re-gate a new commit in one step: `lean-pkg gate {name} --receipt R --regate` "
                      f"(the receipt's commit must descend from the gated one, else add --reason TEXT)"
                      if pkg.get("status") == "verified-private" else "")
                   + (f"; first: {to_gate_pending_cmd(name, str(receipt.get('commit') or ''))}, then gate again"
                      if pkg.get("status") == "active" and isinstance(receipt, dict) else ""))
    was_public = pkg.get("status") == "public"
    validate_receipt(vpkg, repo, receipt)
    commit = receipt["commit"]
    report_line, report_view = report_log_check(repo, receipt, rp)
    if comp is not None:
        validate_comparator(vpkg, repo, comp, commit)
    docs_view, docs_line = docs_admission(repo, commit, vpkg, receipt.get("checker"))
    prev_commit, non_descendant = None, False
    if regate:
        prev_commit, non_descendant, problem = regate_lineage(name, pkg, repo, commit, reason)
        if problem:
            raise Fail(problem + "; nothing copied or recorded")
    cs = claims_schema_at(name, pkg, repo, commit)
    if cs["ok"] is False:
        problems = claims_problems(cs)
        if cs["errors"]:
            what = "fails the claims/1 schema"
            fix = (f"fix the file, check it with `lean-pkg claims check --file {cs['file']}`"
                   + (" (schema only; the declarations are checked by `claims sync` at HEAD)"
                      if cs["declarations"]["problems"] else ""))
        else:
            what = "names declarations missing from their files at that commit"
            fix = (f"fix the declarations or the claims' decl/file entries (`lean-pkg claims sync {name}` "
                   f"runs the same check at HEAD)")
        raise Fail(f"{name}: {cs['file']} at {commit[:12]} {what}, so `claims sync` "
                   f"would refuse what this gate binds: " + "; ".join(problems[:3])
                   + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
                   + f". Nothing copied or recorded. Fixing it is a new commit, which needs a new receipt "
                   f"and a re-gate: {fix}, "
                   f"commit, re-run the receipt at that commit, then `lean-pkg gate {name} --receipt R`")

    job = receipt_job_slug(receipt["job_id"])
    dest_rel = f".lean-receipts/{commit[:12]}-{job}.json"
    dest = repo / dest_rel
    out: list[str] = [f"{r['what']} {r['arg']} resolved against the package checkout: {r['path']}"
                      for r in resolved] + [claims_schema_line(cs), report_line]
    # Every precondition of both copies runs before either is written, so a refusal writes nothing.
    why = evidence_dest_problem(repo, dest_rel)
    if why:
        raise Fail(why)
    log_copy = plan_report_log_copy(repo, receipt, rp)
    report_view["copy"] = [d for _s, d in log_copy]
    present = dest.exists()
    if present and recorded_json(repo, dest_rel) != receipt:
        raise Fail(f"{dest} already holds a different receipt for commit {commit[:12]} and job "
                   f"{receipt['job_id']}; pass that file (--receipt {dest}), or give the new run its own "
                   f"job_id")
    comp_plan = plan_comparator_copy(repo, commit, cp) if comp is not None else None
    if present:
        out.append(f"receipt already present at {dest_rel} (not re-copied)")
    else:
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(rp, dest)
        out.append(f"receipt copied to {dest} — NOT committed there; "
                   f"commit it in the verified repo yourself")
    for src, log_rel in log_copy:   # the sealed log follows the receipt, so the canonical copy verifies
        shutil.copy2(src, repo / log_rel)
        out.append(f"sealed log copied to {repo / log_rel} beside the receipt — NOT committed there; "
                   f"commit it with the receipt")
    pkg.setdefault("gate", {})["receipt"] = dest_rel
    fields = dict(commit=commit, job_id=str(receipt["job_id"]),
                  checker=str(receipt["checker"]), receipt=dest_rel, **docs_view)
    if docs_line:
        out.append(docs_line)
    comp_rel = None
    if comp is not None:
        comp_rel, note, _moved = apply_comparator_copy(repo, comp_plan, cp)
        out.append(note)
        pkg["gate"]["comparator"] = comp_rel
        fields["comparator"] = comp_rel
    else:
        prev_comp = pkg["gate"].get("comparator")
        prev = recorded_json(repo, prev_comp) if prev_comp else None
        if prev_comp and (prev is None or str(prev.get("commit")) != commit):
            pkg["gate"]["comparator"] = None
            fields["comparator_cleared"] = prev_comp
            out.append(f"comparator evidence {prev_comp} cleared: it was bound to another commit; "
                       f"attach a fresh one with `lean-pkg comparator {name} --receipt ...`")
        else:
            pkg["gate"].setdefault("comparator", None)
    if was_public:
        fields["status_unchanged"] = True
        new_status = "public"
    else:
        new_status = "verified-private"
    pkg["status"] = new_status
    pins, pins_kept = receipt_pin_changes(pkg, repo, receipt)
    for field, change in pins.items():
        pkg[field] = change["to"]
    if pins:
        fields["pins"] = pins
    event = "gate-passed"
    if regate:   # #67: one transition, one event naming both commits
        event = "regated"
        fields = {"previous_commit": prev_commit, **fields, "reason": reason or ""}
        if non_descendant:
            fields["non_descendant"] = True
    record(reg, events_p, pkg, event, **fields)
    save_registry(reg_path, reg)
    if regate:
        out.append(f"{name}: {new_status}, re-gated {str(prev_commit or 'unknown')[:12]} -> {commit[:12]}"
                   + (" (not a descendant; --reason given)" if non_descendant else "")
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    elif was_public:
        out.append(f"{name}: public, evidence recorded, status unchanged (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    else:
        out.append(f"{name}: gate-pending -> verified-private (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    if pins:
        out.append(f"{name}: pins updated from the receipt: {pin_changes_text(pins)}")
    out += pins_kept_lines(name, pins_kept, commit)
    out.append(RL_REMINDER.format(name=name))
    ignored = attached_ignored_evidence(pkg, repo)
    payload = {"ok": True, "name": name, "event": event,
               "status": new_status, "gate": pkg["gate"],
               "receipt": dest_rel, "commit": commit, "rl_reminder": True,
               "rl_hint": RL_REMINDER.format(name=name),
               "status_unchanged": was_public or regate, "private_strings": private, "dirty": dirty,
               "ignored_evidence": ignored, "claims_schema": cs, "pins": pins,
               "pins_kept": pins_kept, "report_log": report_view, "resolved_paths": resolved, **docs_view}
    if regate:
        payload.update(previous_commit=prev_commit, non_descendant=non_descendant, reason=reason or "")
    if comp_rel:
        payload["comparator"] = comp_rel
    return out, payload


def attached_ignored_evidence(pkg: dict, repo: pathlib.Path) -> list[dict]:
    """After `gate`/`comparator` attach evidence: [{path, rule}] for the evidence files git ignores,
    each also a stderr WARN, so the gap shows now and not at promote time (#40)."""
    from . import publish as publish_mod
    ignored = publish_mod.ignored_evidence(repo, publish_mod.evidence_paths(pkg, repo))
    publish_mod.warn_ignored_evidence(ignored)
    return ignored


def do_comparator(reg_path, reg, events_p, name, receipt_arg,
                  check_only: bool = False, refuse_private: bool = False,
                  refuse_dirty: bool = False, replace: bool = False) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_comparator, reg_path, reg, events_p, name, receipt_arg,
                                  check_only, refuse_private, refuse_dirty=refuse_dirty, replace=replace)


def _do_comparator(reg_path, reg, events_p, name, receipt_arg, check_only: bool = False,
                   refuse_private: bool = False, private_sink=None,
                   refuse_dirty: bool = False, replace: bool = False) -> tuple[list[str], dict]:
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    dirty = dirty_guard(repo, name, refuse_dirty)
    cp, comp = load_json_object(locate_evidence_arg(receipt_arg, "comparator receipt", name, repo),
                                "comparator receipt")
    private = receipt_private_strings([("comparator", cp.read_bytes())], refuse_private, private_sink)
    gate = pkg.get("gate") or {}
    if check_only:
        lower = recorded_json(repo, gate.get("receipt"))
        lower_commit = str(lower.get("commit") or "") if lower else None
        validate_comparator(pkg, repo, comp, lower_commit or None)
        why = recorded_report_log_problem(repo, lower, gate["receipt"]) if isinstance(lower, dict) else None
        if why:              # the tier this receipt pairs with must still be evidence
            raise Fail(f"{name}: the recorded gate receipt {gate['receipt']}: {why}")
        docs_view, _docs_line = docs_admission(repo, comp["commit"], pkg)
        n = len(comp["configs"])
        msg = (f"{name}: comparator receipt valid at commit {comp['commit'][:12]} "
               f"({n} config(s)) — not recorded (--check)"
               + ("" if lower_commit else "; no lower-tier receipt recorded yet"))
        checks = comparator_check_lines(pkg, repo, comp, lower_commit or None)
        payload = {"ok": True, "name": name, "event": "comparator-checked",
                   "recorded": False, "commit": comp["commit"], "configs": n,
                   "lower_receipt_commit": lower_commit, "head": git_head(repo), "checks": checks,
                   "private_strings": private, "dirty": dirty, **docs_view}
        return [msg] + [f"  {c}" for c in checks], payload
    rec_comp = recorded_json(repo, gate.get("comparator"))
    if rec_comp is not None and rec_comp == comp:
        payload = {"ok": True, "name": name, "event": "already-attached",
                   "status": pkg.get("status"), "gate": gate,
                   "comparator": gate.get("comparator"), "private_strings": private,
                   "dirty": dirty}
        return ([f"{name}: comparator receipt already attached "
                 f"({gate.get('comparator')}); nothing to do"], payload)
    if pkg.get("status") not in ("verified-private", "public"):
        raise Fail(f"{name}: comparator requires status verified-private or public "
                   f"(gate first), got {pkg.get('status')!r}")
    lower = recorded_json(repo, gate.get("receipt"))
    if lower is None:
        raise Fail(f"{name}: no readable lower-tier receipt at {gate.get('receipt')!r}; "
                   f"run `lean-pkg gate` first")
    validate_comparator(pkg, repo, comp, str(lower.get("commit") or ""))
    why = recorded_report_log_problem(repo, lower, gate["receipt"])
    if why:                  # the tier this receipt pairs with must still be evidence
        raise Fail(f"{name}: the recorded gate receipt {gate['receipt']}: {why}; nothing recorded")
    commit = comp["commit"]
    docs_view, docs_line = docs_admission(repo, commit, pkg)
    comp_rel, note, moved = copy_comparator(repo, commit, cp, replace=replace)
    pkg.setdefault("gate", {})["comparator"] = comp_rel
    event = "comparator-replaced" if moved else "comparator-attached"
    extra = {"replaced": moved} if moved else {}
    record(reg, events_p, pkg, event, commit=commit,
           comparator=comp_rel, host=str(comp["host"]), job_id=str(comp["job_id"]), **extra, **docs_view)
    save_registry(reg_path, reg)
    out = [note, *([docs_line] if docs_line else []),
           f"{name}: {event} ({comp_rel}, commit {commit[:12]}, "
           f"{len(comp['configs'])} config(s))" + (f"; older receipt kept at {moved}" if moved else "")]
    payload = {"ok": True, "name": name, "event": event, **extra,
               "status": pkg.get("status"), "gate": pkg["gate"],
               "comparator": comp_rel, "commit": commit, "private_strings": private,
               "dirty": dirty, "ignored_evidence": attached_ignored_evidence(pkg, repo), **docs_view}
    return out, payload


# --------------------------------------------------------------------------
# register — the ONLY write path for new packages (agents never hand-edit yaml)

def do_register(reg_path, reg, events_p, name, repo, local_path, owner_project,
                visibility, status, upstream_target, automation, notes,
                workspace, agent, dry_run, reason: str = "", like: str | None = None,
                like_copied: list[str] | None = None) -> tuple[list[str], dict]:
    if status not in REGISTER_STATUSES:
        raise Fail(f"register cannot set status {status!r} (allowed: "
                   f"{', '.join(REGISTER_STATUSES)}); verified-private is reached through "
                   f"`lean-pkg gate`, public through `lean-pkg promote --yes`")
    if status == "public":
        if visibility != "public":
            raise UsageError("register --status public requires --visibility public")
        if not str(reason or "").strip():
            # #21.6: a missing --reason is a refusal (exit 1, JSON under --json), as everywhere else (#17)
            raise Fail("register --status public requires --reason (why an already-public "
                       "repository is registered without promote)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise Fail(f"bad package name {name!r} — use kebab-case-ish "
                   f"[A-Za-z0-9._-]+ not starting with a punctuation mark")
    pkgs = reg["packages"]
    if name in pkgs:
        raise Fail(f"name already registered: {name!r} — refusing to "
                   f"overwrite; inspect with `lean-pkg status {name}`")
    require_nonempty("--repo", repo)
    require_nonempty("--owner-project", owner_project)
    local_path, new_key = normalize_local_path(local_path)
    for existing, pkg in pkgs.items():
        if pkg.get("local_path") and local_path_key(pkg["local_path"]) == new_key:
            raise Fail(f"local_path already registered by {existing!r}: "
                       f"{pkg.get('local_path')} — one registry row per "
                       f"checkout; refusing (hand-editing missed exactly this)")
    repo_abs = HOME / pathlib.Path(local_path)
    warnings: list[str] = []
    toolchain = mathlib_pin = None
    if not repo_abs.is_dir():
        warnings.append(f"local_path does not exist on this host: {repo_abs} — "
                        f"lean_toolchain and mathlib_pin set to null")
    else:
        toolchain, w = detect_toolchain(repo_abs)
        if w:
            warnings.append(w)
        mathlib_pin, w = detect_mathlib_pin(repo_abs)
        if w:
            warnings.append(w)
    notes_out = namespace_notes(namespace_clashes(reg, repo_abs)) if repo_abs.is_dir() else []
    sep = os.sep
    for other, opkg in sorted(pkgs.items()):
        if not (opkg or {}).get("local_path"):
            continue
        key = local_path_key(opkg["local_path"])
        if new_key.startswith(key + sep):
            warnings.append(f"{local_path} is inside {other}'s package directory: every commit to it is "
                            f"drift for {other}'s receipt (use a sibling directory instead)")
        elif key.startswith(new_key + sep):
            warnings.append(f"{local_path} contains {other}'s package directory: every commit to {other} is "
                            f"drift for this package's receipt")
    mf = repo_abs / "lake-manifest.json"
    if mf.is_file():
        try:
            outside = outside_path_deps(mf.read_bytes())
        except ValueError:
            outside = []
        if outside:
            warnings.append(f"lake-manifest.json has path dependencies outside the package directory: "
                            f"{', '.join(outside)}; `gate` refuses such a receipt (require them from git "
                            f"pinned to a rev, or move them inside)")
    like_info = None
    if like is not None:
        base = get_pkg(reg, like)
        like_info = {"name": like, "copied": list(like_copied or []),
                     "toolchain": {"this": toolchain, "like": base.get("lean_toolchain"),
                                   "match": toolchain == base.get("lean_toolchain")},
                     "mathlib_pin": {"this": mathlib_pin, "like": base.get("mathlib_pin"),
                                     "match": mathlib_pin == base.get("mathlib_pin")}}
        for k, label in (("toolchain", "toolchain"), ("mathlib_pin", "Mathlib pin")):
            v = like_info[k]
            notes_out.append(f"note: {label} {v['this'] or '?'} " + ("matches" if v["match"] else "differs from")
                             + f" {like}'s ({v['like'] or '?'})")
    entry = {
        "name": name, "repo": repo, "local_path": local_path,
        "visibility": visibility, "status": status,
        "owner_project": owner_project, "workspace": workspace, "agent": agent,
        "lease": None, "lean_toolchain": toolchain, "mathlib_pin": mathlib_pin,
        "upstream_target": upstream_target, "automation": automation or "",
        "gate": {"receipt": None, "comparator": None,
                 "axioms_allowed": list(DEFAULT_AXIOMS)},
        "promoted_at": None, "promoted_commit": None, "reaudit_after": None,
        "notes": notes or "", "history": [],
        **copy.deepcopy(V2_PACKAGE_DEFAULTS),
        **copy.deepcopy(V3_PACKAGE_DEFAULTS),
    }
    payload = {"name": name, "dry_run": dry_run, "package": entry,
               "warnings": warnings, "notes": notes_out}
    if like_info is not None:
        payload["like"] = like_info
    if dry_run:
        lines = [f"warn: {w}" for w in warnings]
        lines.append(yaml.safe_dump({name: entry}, sort_keys=False,
                                    default_flow_style=False, width=120).rstrip())
        lines.append("DRY RUN — nothing written. Re-run without --dry-run "
                     "to register.")
        return lines, payload
    before = validate_registry(reg)
    pkgs[name] = entry
    # safety net: never write a schema error this registration introduces; pre-existing
    # errors on other packages are reported on stderr and do not block
    errors, preexisting = split_new_errors(before, validate_registry(reg))
    if errors:
        del pkgs[name]
        raise Fail("register would produce an invalid registry: "
                   + "; ".join(errors))
    report_preexisting(preexisting)
    if preexisting:
        payload["preexisting_errors"] = preexisting
    record(reg, events_p, entry, "registered", repo=repo,
           local_path=local_path, status=status, visibility=visibility,
           lean_toolchain=toolchain, mathlib_pin=mathlib_pin,
           workspace=workspace, agent=agent,
           **({"reason": reason} if str(reason or "").strip() else {}),
           **({"like": like} if like is not None else {}))
    save_registry(reg_path, reg)
    payload["event"] = "registered"
    lines = [f"warn: {w}" for w in warnings]
    lines.append(f"{name}: registered (status={status}, "
                 f"visibility={visibility}, toolchain={toolchain or '?'}, "
                 f"pin={mathlib_pin or '?'})")
    return lines, payload


# --------------------------------------------------------------------------
# promote


def do_init(reg_path: pathlib.Path, events_p: pathlib.Path, force: bool) -> tuple[list[str], dict]:
    """Bootstrap an empty registry and its event log. Refuses to touch a registry that holds packages."""
    existed = reg_path.exists()
    if existed:
        try:
            existing = yaml.safe_load(reg_path.read_text()) or {}
        except yaml.YAMLError:
            existing = None
        if not force:
            raise Fail(f"registry already exists: {reg_path} (use --force only to "
                       f"re-create an EMPTY registry)")
        if not isinstance(existing, dict) or (existing.get("packages") or {}):
            raise Fail(f"refusing --force: {reg_path} holds packages or is not a valid YAML registry")
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": REGISTRY_VERSION, "packages": {}}
    save_registry(reg_path, data)
    # The shared event log records the registry file NAME only: an absolute path is one machine's.
    append_event(events_p, now_iso(), None, "registry-created", registry=reg_path.name,
                 version=REGISTRY_VERSION, **({"forced": True} if force and existed else {}))
    out = [f"registry created: {reg_path} (version {REGISTRY_VERSION}); "
           f"events: {events_p}"]
    return out, {"ok": True, "event": "registry-created", "registry": str(reg_path),
                 "events": str(events_p), "version": REGISTRY_VERSION, "forced": bool(force and existed)}


def migrate_registry(data: dict) -> tuple[dict, list[str]]:
    """Pure: return (migrated copy, change log). v1 -> v2 -> v3 is additive."""
    out = copy.deepcopy(data)
    changes: list[str] = []
    v = out.get("version")
    if v not in SUPPORTED_REGISTRY_VERSIONS:
        raise Fail(f"cannot migrate registry version {v!r}; supported: {SUPPORTED_REGISTRY_VERSIONS}")
    if v != REGISTRY_VERSION:
        out["version"] = REGISTRY_VERSION
        changes.append(f"version: {v} -> {REGISTRY_VERSION}")
    for name, pkg in (out.get("packages") or {}).items():
        for k, default in {**V2_PACKAGE_DEFAULTS, **V3_PACKAGE_DEFAULTS}.items():
            if k not in pkg:
                pkg[k] = copy.deepcopy(default)
                changes.append(f"{name}: + {k}")
        # #21.4: a row stored before #17 as a literal `~/x` resolves to $HOME/~/x; rewrite it through the
        # one normaliser. `~user` and a bare `~` are not rewritten (normalize_local_path refuses them).
        lp_ = pkg.get("local_path")
        if isinstance(lp_, str) and lp_.strip().startswith("~/"):
            try:
                stored, _key = normalize_local_path(lp_)
            except Fail:
                stored = None
            if stored is not None and stored != lp_:
                pkg["local_path"] = stored
                changes.append(f"{name}: local_path {lp_!r} -> {stored!r} (legacy literal ~/, #17)")
    return out, changes


def do_migrate(reg_path, events_p, dry_run: bool) -> tuple[list[str], dict]:
    data = load_registry(reg_path)
    from_v = data.get("version")
    new, changes = migrate_registry(data)
    payload = {"ok": True, "dry_run": dry_run, "from_version": from_v,
               "to_version": new["version"], "changes": changes}
    if not changes:
        return [f"registry already at version {new['version']}; nothing to do"], payload
    lines = [f"  {c}" for c in changes]
    if dry_run:
        lines.append("DRY RUN \u2014 nothing written.")
        return lines, payload
    save_registry(reg_path, new)
    append_event(events_p, now_iso(), None, "registry-migrated",
                 from_version=from_v, to_version=new["version"], changes=len(changes))
    lines.append(f"migrated {reg_path}: version {from_v} -> {new['version']} ({len(changes)} change(s))")
    payload["event"] = "registry-migrated"
    return lines, payload


def _palomar_entry(pkg: dict, commit: str) -> dict:
    pal = pkg.setdefault("palomar", [])
    for e in pal:
        if e.get("commit") == commit:
            return e
    e = {"commit": commit, "id": None, "version": None, "comparator_config": None,
         "registered_at": None, "review_outcome": None, "preflight_run": None}
    pal.append(e)
    return e


def do_retract(reg_path, reg, events_p, name, fields: dict, reason: str) -> tuple[list[str], dict]:
    """record NAME retract --field event=E --field ts=T --reason R: mark an evidence entry retracted.

    Append-only: the entry stays in history[]; an evidence-retracted entry names it, and the validator
    and the upstream-evidence check ignore what it names."""
    missing = [f for f in ("event", "ts") if not str(fields.get(f, "")).strip()]
    if missing:
        raise Fail(f"retract requires --field for: {', '.join(missing)} (the event name and ts of the "
                   f"history entry to retract; see `lean-pkg status {name} --json`)")
    unknown = sorted(set(fields) - {"event", "ts"})
    if unknown:
        raise Fail(f"retract: unknown --field key(s) {', '.join(unknown)}; allowed: event, ts")
    if not (reason or "").strip():
        raise Fail("retract requires --reason (why the evidence entry is wrong)")
    target, ts = str(fields["event"]).strip(), str(fields["ts"]).strip()
    if target not in EVIDENCE_EVENTS:
        raise Fail(f"retract: {target!r} is not an evidence event; one of {sorted(EVIDENCE_EVENTS)}")
    pkg = get_pkg(reg, name)
    hits = [h for h in pkg.get("history") or []
            if isinstance(h, dict) and h.get("event") == target and str(h.get("ts")) == ts]
    if not hits:
        known = [str(h.get("ts")) for h in pkg.get("history") or []
                 if isinstance(h, dict) and h.get("event") == target]
        raise Fail(f"{name}: no {target} history entry at ts {ts} (recorded at: {', '.join(known) or 'none'})")
    gone = {_fingerprint(e) for e in retracted_entries(pkg)}
    hits = [h for h in hits if _fingerprint(h) not in gone]
    if not hits:
        raise Fail(f"{name}: {target} at {ts} is already retracted")
    if len(hits) > 1:
        raise Fail(f"{name}: {len(hits)} {target} entries share ts {ts}; retract cannot tell them apart")
    entry = {"retracted_event": target, "retracted_ts": ts, "retracted_entry": copy.deepcopy(hits[0]),
             "reason": reason}
    trial = copy.deepcopy(reg)
    trial["packages"][name].setdefault("history", []).append(
        {"ts": now_iso(), "event": "evidence-retracted", **entry})
    errors, preexisting = split_new_errors(validate_registry(reg), validate_registry(trial))
    if errors:
        raise Fail(f"{name}: retract would break the registry schema:\n  " + "\n  ".join(errors))
    report_preexisting(preexisting)
    record(reg, events_p, pkg, "evidence-retracted", **entry)
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": "evidence-retracted",
               "retracted": {"event": target, "ts": ts}, "reason": reason}
    if preexisting:
        payload["preexisting_errors"] = preexisting
    return [f"{name}: {target} at {ts} retracted"], payload


def _rule_fail(rule: str, msg: str) -> Fail:
    """A record refusal that names the evidence rule it enforces."""
    return Fail(f"{msg} [rule {rule}]")


def _int_or_none(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _evidence_version(v) -> int:
    n = _int_or_none(v)
    if n is None:
        raise _rule_fail("palomar-version", f"version must be an integer, got {v!r}")
    return n


def consent_unique(pkg: dict) -> list[dict]:
    """consent[] entries deduplicated by (who, role), first record kept: how consent is displayed (#23.1).
    The registry keeps every record; a re-recorded consent is shown once."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for c in pkg.get("consent") or []:
        if not isinstance(c, dict):
            continue
        key = (str(c.get("who")), str(c.get("role")))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def check_evidence_fields(pkg: dict, event: str, fields: dict) -> list[str]:
    """Shape and binding checks on an evidence event's --field values, before any effect.
    Refuses (naming the rule) or returns warning notes, which are also printed on stderr."""
    notes: list[str] = []
    if event == "doi-minted":
        for k in ("doi", "concept"):
            if k in fields and str(fields[k]).strip() and not DOI_RE.fullmatch(str(fields[k]).strip()):
                raise _rule_fail("doi-format", f"{k} must be a DOI (10.<4-9 digits>/<suffix>), "
                                 f"got {fields[k]!r}")
    elif event in ("mathlib-pr-opened", "mathlib-pr-merged"):
        if not GITHUB_URL_RE.fullmatch(str(fields["url"]).strip()):
            raise _rule_fail("mathlib-pr-url", f"url must be an https://github.com/<owner>/<repo>/... "
                             f"pull request URL, got {fields['url']!r}")
    elif event == "reservoir-listed":
        url = str(fields["url"]).strip().casefold()
        nwo = gh_nwo(pkg.get("repo") or "")
        wanted = [str(pkg.get("name") or "")] + ([nwo, nwo.split("/")[1]] if nwo else [])
        if not any(w and w.casefold() in url for w in wanted):
            notes.append(f"WARN: reservoir url names neither the package {pkg.get('name')!r} nor its "
                         f"repository {nwo or '(no GitHub repo)'}; check it is this package's page")
    elif event == "palomar-registered":
        status = pkg.get("status")
        if status not in PALOMAR_REGISTERED_STATUSES:
            raise _rule_fail("palomar-registered-public",
                             f"{pkg.get('name')}: palomar-registered needs status public or upstreamed "
                             f"(Palomar registers public code), got {status!r}")
        rel = str(fields["comparator_config"]).strip()
        repo = repo_path(pkg) if pkg.get("local_path") else None
        if repo is None or not repo.is_dir():
            raise _rule_fail("palomar-config-tracked",
                             f"{pkg.get('name')}: local_path does not exist; cannot check that "
                             f"comparator_config {rel} is tracked at HEAD")
        if os.path.isabs(rel) or git(repo, "cat-file", "-e", f"HEAD:./{rel}").returncode != 0:
            raise _rule_fail("palomar-config-tracked",
                             f"{pkg.get('name')}: comparator_config {rel} is not tracked at the package's "
                             f"HEAD; commit it (a repo-relative path) before recording palomar-registered")
    for n in notes:
        print(f"lean-pkg: {n}", file=sys.stderr)
    return notes


def warn_private_evidence(hits: list[dict]) -> None:
    for h in hits:
        print(f"WARN: {h['field']} looks like a {h['kind']}: {PRIVATE_EVIDENCE_HINT}", file=sys.stderr)


def do_record(reg_path, reg, events_p, name, event, fields: dict, reason: str,
              track: str | None = None) -> tuple[list[str], dict]:
    """Record a D5 evidence event: required fields checked, registry effect applied, the would-be registry
    validated (a schema error this event introduces refuses; nothing is written), event appended.
    track (--track, doi-minted only) overrides the record's DOI track for this event (#51)."""
    if track is not None and event != "doi-minted":
        raise UsageError(f"--track goes with doi-minted, not {event}")
    if track is not None and track not in TRACKS:
        raise UsageError(f"--track must be one of {', '.join(TRACKS)}, got {track!r}")
    if event == RETRACT_EVENT:
        return do_retract(reg_path, reg, events_p, name, fields, reason)
    if event not in EVIDENCE_EVENTS:
        raise Fail(f"unknown evidence event {event!r}; one of {sorted(EVIDENCE_EVENTS)} "
                   f"(or {RETRACT_EVENT})")
    missing = [f for f in EVIDENCE_EVENTS[event] if not str(fields.get(f, "")).strip()]
    if missing:
        raise Fail(f"{event} requires --field for: {', '.join(missing)}")
    allowed = set(EVIDENCE_EVENTS[event]) | ({"concept"} if event == "doi-minted" else set())
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise Fail(f"{event}: unknown --field key(s) {', '.join(unknown)}; allowed: "
                   f"{', '.join(sorted(allowed))} (name, event, ts and reason are reserved; "
                   f"use --reason for free text)")
    if "commit" in fields and not _HEX40.fullmatch(str(fields["commit"])):
        raise Fail(f"commit must be a 40-hex sha, got {fields['commit']!r}")
    current = get_pkg(reg, name)
    notes = check_evidence_fields(current, event, fields)
    event_extra: dict = {}
    if event == "doi-minted":
        track = track or current.get("track") or "palomar"
        if track == "doi-only" and current.get("status") != "public":
            raise _rule_fail("doi-only-public",
                             f"{name}: the DOI-only track needs status public (doi-minted without a palomar-* "
                             f"event is allowed only for a public package), got {current.get('status')!r}")
        event_extra["track"] = track
    from .index import private_string_hits
    private = [{"field": f, "kind": k} for f, k, _ in
               private_string_hits(json.dumps(fields).encode(), "field")]
    if event == "palomar-preflight-pass" and any(
            isinstance(e, dict) and e.get("commit") == fields["commit"]
            and e.get("preflight_run") == fields["run"] for e in current.get("palomar") or []):
        warn_private_evidence(private)
        payload = {"ok": True, "name": name, "event": event, "fields": fields, "notes": notes,
                   "already_recorded": True, "private_strings": private}
        return [f"{name}: {event} already recorded for commit {fields['commit'][:12]} "
                f"(run {fields['run']}); nothing to do", *notes], payload
    if event == "consent-recorded" and any(       # #23.1: re-recording the same consent is a no-op
            isinstance(c, dict) and (str(c.get("who")), str(c.get("role")), str(c.get("pointer")))
            == (str(fields["who"]), str(fields["role"]), str(fields["pointer"]))
            for c in current.get("consent") or []):
        warn_private_evidence(private)
        payload = {"ok": True, "name": name, "event": event, "fields": fields, "notes": notes,
                   "already_recorded": True, "private_strings": private}
        return [f"{name}: {event} already recorded for {fields['who']} ({fields['role']}, same "
                f"pointer); nothing to do", *notes], payload
    before = validate_registry(reg)
    original = reg
    reg = copy.deepcopy(reg)                      # effects go to a copy until the result validates
    pkg = reg["packages"][name]
    for k, d in V2_PACKAGE_DEFAULTS.items():      # tolerate a v1 registry
        pkg.setdefault(k, copy.deepcopy(d))
    ts = now_iso()
    extra_out: list[str] = list(notes)
    if event == "consent-recorded":
        pkg["consent"].append({"who": fields["who"], "role": fields["role"],
                               "pointer": fields["pointer"], "recorded_at": ts})
    elif event in ("palomar-preflight-pass", "palomar-submitted", "palomar-review", "palomar-registered"):
        e = _palomar_entry(pkg, fields["commit"])
        if event == "palomar-preflight-pass":
            e["preflight_run"] = fields["run"]
        elif event == "palomar-submitted":
            e["submission_id"] = fields["submission_id"]
            e["comparator_config"] = fields["comparator_config"]
        elif event == "palomar-review":
            if fields["outcome"] not in ("neutral", "revision_required", "rejected"):
                raise Fail("outcome must be neutral | revision_required | rejected")
            e["review_outcome"] = fields["outcome"]
        else:
            e["version"] = _evidence_version(fields["version"])
            e["id"] = fields["id"]
            e["comparator_config"] = fields["comparator_config"]
            e["registered_at"] = ts
    elif event == "palomar-withdrawn":
        version = _evidence_version(fields["version"])
        for e in pkg["palomar"]:
            if e.get("id") == fields["id"] and _int_or_none(e.get("version")) == version:
                e["withdrawn_at"] = ts
                break
        else:
            raise Fail(f"no palomar entry {fields['id']} v{fields['version']} to withdraw")
    elif event == "doi-minted":
        d = pkg.get("doi") or {"concept": None, "versions": []}
        if fields.get("concept"):
            d["concept"] = fields["concept"]
        d["versions"].append({"doi": fields["doi"], "tag": fields["tag"],
                              "commit": fields["commit"], "minted_at": ts,
                              **({"track": "doi-only"} if event_extra.get("track") == "doi-only" else {})})
        pkg["doi"] = d
    elif event == "reservoir-listed":
        m = RESERVOIR_URL_RE.match(str(fields["url"]).strip())
        if not m:
            raise Fail("url must be the package's Reservoir page, "
                       "https://reservoir.lean-lang.org/@<owner>/<name>")
        owner, repo_name = m.group(1), m.group(2)
        status = reservoir_listing_status(owner, repo_name)
        if status == 404:
            raise Fail(f"{owner}/{repo_name} is not listed on Reservoir (API 404); listing is "
                       f"automatic for a public, non-fork GitHub repository with a root "
                       f"lake-manifest.json, a detected OSI license and at least 2 stars")
        verified = status == 200
        pkg["reservoir"] = {"url": fields["url"], "listed_at": ts, "verified": verified,
                            "verified_at": ts if verified else None}
        if not verified:
            extra_out.append(f"reservoir listing unverified (API status {status!r}; network "
                             f"unreachable or unexpected); re-run `record` later to verify")
    trial = copy.deepcopy(reg)
    trial["packages"][name].setdefault("history", []).append(
        {"ts": ts, "event": event, "reason": reason or "", **fields, **event_extra})
    errors, preexisting = split_new_errors(before, validate_registry(trial))
    if errors:
        raise Fail(f"{name}: {event} would break the registry schema; nothing written:\n  "
                   + "\n  ".join(errors))
    report_preexisting(preexisting)
    original["packages"][name] = pkg
    record(original, events_p, pkg, event, reason=reason or "", **fields, **event_extra)
    save_registry(reg_path, original)
    warn_private_evidence(private)
    payload = {"ok": True, "name": name, "event": event, "fields": fields, "notes": extra_out,
               "already_recorded": False, "private_strings": private, **event_extra}
    if preexisting:
        payload["preexisting_errors"] = preexisting
    return [f"{name}: {event} recorded", *extra_out], payload

def evidence_tracked_item(repo: pathlib.Path, what: str, rels: list[str]) -> tuple[str, str]:
    """✓ when every evidence path is committed at HEAD with the bytes the working tree holds;
    ✗ naming the untracked or modified ones (untracked files do not count, as in `index add`).
    An untracked path that git ignores is named with its ignore rule (#40)."""
    from . import publish as publish_mod
    untracked = [r for r in rels if not tracked_at(repo, r)]
    changed = [r for r in rels if r not in untracked and (repo / r).is_file()
               and git_show_bytes(repo, f"./{r}") != (repo / r).read_bytes()]
    if not untracked and not changed:
        return ("✓", f"{what} tracked at HEAD ({len(rels)} file(s))")
    rules = {i["path"]: i["rule"] for i in publish_mod.ignored_evidence(
        repo, [r for r in untracked if (repo / r).is_file()])}
    named = [f"{r} (git-ignored by {rules[r]})" if r in rules else r for r in untracked[:3]]
    bad = ([f"not committed: {', '.join(named)}"] if untracked else []) \
        + ([f"modified since HEAD: {', '.join(changed[:3])}"] if changed else [])
    fix = (f"`git add` skips git-ignored files; {publish_mod.IGNORE_REMEDY}" if rules
           else "commit .lean-receipts/ first")
    return ("✗", f"{what} not committed at HEAD — {'; '.join(bad)} (untracked files do not "
                 f"count; {fix})")


def head_pushed_item(repo: pathlib.Path) -> tuple[str, str]:
    """HEAD must be contained in origin/<branch>, judged on local refs only (no fetch)."""
    head = git_head(repo)
    branch = current_branch(repo) or reference_branch(repo)[0] or "main"
    ref = f"refs/remotes/origin/{branch}"
    if head is None or _rev(repo, ref) is None:
        return ("✗", f"no origin/{branch} in local refs — push the branch first "
                     f"(local refs; nothing fetched)")
    if _is_ancestor(repo, head, ref):
        return ("✓", f"HEAD {head[:12]} contained in origin/{branch} (local refs; nothing fetched)")
    return ("✗", f"HEAD {head[:12]} not contained in origin/{branch} (local refs; nothing "
                 f"fetched) — push first; the public repository must hold the evidence")


def promote_checklist(pkg: dict, repo: pathlib.Path, yes: bool = False,
                      ignored_sink: list | None = None, ledger_sink: dict | None = None) -> list[tuple[str, str]]:
    """(mark, label) pairs; mark in {'✓', '✗', 'WARN'}. With yes (promote --yes), a failed gh
    visibility query is ✗, not WARN. Evidence on disk that git ignores is a WARN (#40), its
    [{path, rule}] appended to ignored_sink. The claims ledger at the promote stage must be ready
    (#80): its summary goes into ledger_sink."""
    checks: list[tuple[str, str]] = []
    if pkg.get("publication_hold"):   # #5: an owner's withdrawal is never undone by promote
        checks.append(("✗", f"{hold_text(pkg['publication_hold'])}; promote refuses while it is set (lift it "
                            f"only with `lean-pkg update {pkg.get('name')} --release-publication-hold "
                            f"--reason TEXT`)"))
    reuse = find_reuse()
    if reuse is None:
        checks.append(("WARN", "reuse not on PATH — REUSE license lint skipped"))
    else:
        p = run([reuse, "lint"], cwd=str(repo), scrub=True)
        if p.returncode == 0:
            checks.append(("✓", "reuse lint passed"))
        else:
            detail = (p.stderr or p.stdout).strip().splitlines()
            checks.append(("✗", f"reuse lint failed: {(detail or [''])[0]}"))   # whole first line (#21.7)
    if any((repo / f).is_file() for f in LICENSE_NAMES):
        checks.append(("✓", "LICENSE/COPYING present at repo root"))
    else:
        checks.append(("✗", "no LICENSE/COPYING at repo root "
                            "(Reservoir requires a GitHub-detected OSI license)"))
    p = git(repo, "cat-file", "-e", "HEAD:lake-manifest.json")
    if p.returncode == 0:
        checks.append(("✓", "lake-manifest.json tracked at HEAD"))
    else:
        checks.append(("✗", "lake-manifest.json NOT tracked at HEAD "
                            "(Reservoir requires a root manifest)"))
    receipt_rel = (pkg.get("gate") or {}).get("receipt")
    head = git_head(repo)
    rcommit = None
    if not receipt_rel:
        checks.append(("✗", "no gate receipt recorded — run `lean-pkg gate` first"))
    else:
        try:
            rcommit = json.loads((repo / receipt_rel).read_text()).get("commit")
        except Exception:
            rcommit = None
        ok, note, _docs = (package_descendant(repo, rcommit, head, pkg)
                           if rcommit and head else (False, "no receipt commit", None))
        if ok:
            checks.append(("✓", f"gate receipt bound to HEAD {head[:12]}"
                                + (f" ({note})" if note else "")))
        else:
            checks.append(("✗", f"gate receipt/HEAD drift: "
                                f"receipt={str(rcommit)[:12]} HEAD={str(head)[:12]} ({note})"))
        rec = recorded_json(repo, receipt_rel)
        why = recorded_report_log_problem(repo, rec, receipt_rel) if isinstance(rec, dict) else None
        if why:
            checks.append(("✗", f"gate receipt {receipt_rel}: {why}"))
        logs = report_log_rels(repo, rec) if isinstance(rec, dict) and not why else []
        checks.append(evidence_tracked_item(repo, f"gate receipt {receipt_rel}"
                                                  + (" and its sealed log" if logs else ""), [receipt_rel, *logs]))
    comp_rel = (pkg.get("gate") or {}).get("comparator")
    if not comp_rel:
        checks.append(("✗", "no comparator receipt recorded — a comparator PASS "
                            "is required before public (`lean-pkg comparator`)"))
    else:
        comp = recorded_json(repo, comp_rel)
        if comp is None:
            checks.append(("✗", f"comparator receipt unreadable: {comp_rel}"))
        else:
            try:
                validate_comparator(pkg, repo, comp, rcommit if rcommit else None)
                checks.append(("✓", f"comparator PASS bound to HEAD {str(head)[:12]} "
                                    f"({len(comp['configs'])} config(s): config and log bytes "
                                    f"match the receipt, PASS line present; not re-run)"))
            except Fail as e:
                checks.append(("✗", f"comparator receipt invalid: {str(e)[:140]}", str(e)))
            logs = [c["log"] for c in comp.get("configs") or []
                    if isinstance(c, dict) and isinstance(c.get("log"), str) and c["log"]]
            checks.append(evidence_tracked_item(repo, f"comparator receipt {comp_rel} and its "
                                                      f"{len(logs)} log(s)", [comp_rel, *logs]))
    checks.append(ledger_item(pkg, ledger_sink))
    from . import publish as publish_mod
    ignored = publish_mod.ignored_evidence(repo, publish_mod.evidence_paths(pkg, repo))
    if ignored_sink is not None:
        ignored_sink.extend(ignored)
    item = publish_mod.ignored_evidence_item(ignored)
    if item:
        checks.append(item)
    paper = pkg.get("paper")
    if paper_blocks_promote(pkg):
        checks.append(("✗", "paper is unpublished and no arXiv id is recorded: going public would disclose "
                            "its results before the preprint (`lean-pkg update "
                            f"{pkg.get('name')} --paper-arxiv ID` once it is on arXiv)"))
    elif isinstance(paper, dict):
        checks.append(("✓", f"paper {paper.get('status')}"
                            + (f", arXiv {paper['arxiv']}" if paper.get("arxiv") else "")))
    checks.append(head_pushed_item(repo))
    nwo = gh_nwo(pkg.get("repo") or "") or str(pkg.get("repo"))
    p = gh("repo", "view", nwo, "--json", "visibility", "-q", ".visibility")
    if p.returncode != 0:
        checks.append(("✗" if yes else "WARN", "gh visibility query failed: "
                       + ((p.stderr or p.stdout).strip().splitlines() or [""])[0]
                       + (" (--yes needs a working gh)" if yes else "")))
    else:
        gvis = p.stdout.strip().lower()
        if gvis == str(pkg.get("visibility")).lower():
            checks.append(("✓", f"gh visibility matches registry ({gvis})"))
        else:
            checks.append(("✗", f"visibility mismatch: "
                                f"registry={pkg.get('visibility')} gh={gvis}"))
    if pkg.get("upstream_target") == "mathlib":
        if str(pkg.get("automation") or "").strip():
            checks.append(("✓", "automation AI-disclosure note present "
                                "(required for mathlib target)"))
        else:
            checks.append(("✗", "upstream_target=mathlib requires a nonempty "
                                "automation AI-disclosure note"))
    else:
        checks.append(("✓", f"automation disclosure not required "
                            f"(upstream_target={pkg.get('upstream_target')})"))
    return checks


def ledger_item(pkg: dict, sink: dict | None = None) -> tuple:
    """#80: `promote` needs every reusable claim closed by the recorded evidence, each theorem and lemma
    by the comparator tier too: the claims ledger at the promote stage must be `ready`. An empty claims
    set, or none `reusable`, is `unknown` and refuses (fails closed)."""
    from . import ledger
    led = ledger.ledger_for(pkg, "promote")
    if sink is not None:
        sink.update(package_status=led["package_status"], counts=led["counts"], required=led["required"],
                    reasons=led["reasons"], ledger_digest=led["ledger_digest"])
    line = ledger.summary(led)
    if led["package_status"] == "ready":
        return ("✓", line)
    return ("✗", f"{line} (`lean-pkg claims ledger {pkg['name']}` lists every open claim)", line)


ONEWAY_WARNING = ("WARNING: a GitHub private→public flip is effectively ONE-WAY — "
                  "the code and its full history become permanently public; "
                  "flipping back does not unpublish forks, clones, or caches.")


_MARK_TO_STATUS = {"✓": "pass", "✗": "fail", "WARN": "warn"}


PROMOTE_FETCH_TIMEOUT = 120   # seconds for promote --yes's `git fetch origin`
# Everything the flip makes public: branches, tags (--tags) and GitHub's pull-request heads, which
# are part of the repository on GitHub and become readable with it.
PROMOTE_FETCH_ARGS = ("fetch", "--prune", "--tags", "origin", "+refs/heads/*:refs/remotes/origin/*",
                      "+refs/pull/*/head:refs/remotes/origin/pr/*")


def promote_fetch(repo: pathlib.Path) -> str | None:
    """promote --yes: bring every ref the remote holds into local refs, so the public-safety scan
    (which reads every local ref) covers what the flip publishes, not only what was last fetched.
    None on success, else why the fetch failed."""
    env = dict(scrubbed_env(), GIT_TERMINAL_PROMPT="0")
    try:
        p = subprocess.run(["git", "-C", str(repo), *PROMOTE_FETCH_ARGS], capture_output=True, text=True,
                           env=env, timeout=PROMOTE_FETCH_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"timed out after {PROMOTE_FETCH_TIMEOUT}s"
    except OSError as e:
        return str(e)
    if p.returncode != 0:
        return ((p.stderr or p.stdout).strip().splitlines() or [f"exit {p.returncode}"])[-1]
    return None


def do_promote(reg_path, reg, events_p, name, yes, skip_public_safety: bool = False) -> tuple[list[str], int, dict]:
    pkg = get_pkg(reg, name)
    if pkg.get("status") != "verified-private":
        raise Fail(f"{name}: promote requires status verified-private, "
                   f"got {pkg.get('status')!r}")
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    fetch_item = None
    if yes:   # the dry run stays offline: it scans local refs only
        err = promote_fetch(repo)
        fetch_item = (("✗", f"git fetch origin failed ({err}): cannot verify what the public flip would publish "
                            f"(branches, tags and pull-request heads on the remote)") if err else
                      ("✓", "fetched origin's branches, tags and pull-request heads: the scan covers them"))
    lines = [f"promotion checklist for {name} ({repo}):"]
    ignored: list[dict] = []
    led: dict = {}
    checks = ([fetch_item] if fetch_item else []) + promote_checklist(pkg, repo, yes=yes, ignored_sink=ignored,
                                                                      ledger_sink=led)
    # #35: private strings in the tree or the history; the flip publishes both.
    if skip_public_safety:
        checks.append(("WARN", f"public-safety skipped (--skip-public-safety); run "
                               f"`lean-pkg public-safety {name}` yourself"))
        safety = {"skipped": True}
    else:
        from . import public_safety as ps_mod
        item, safety = ps_mod.checklist_item(pkg, repo)
        checks.append(item)
    lines += [f"  {c[0]}  {c[1]}" for c in checks]
    payload = {"name": name, "dry_run": not yes,
               "checklist": [dict({"status": _MARK_TO_STATUS[c[0]], "label": c[1]},
                                  **({"reason": c[2]} if len(c) > 2 else {}))
                             for c in checks],
               "public_safety": safety,
               "one_way_warning": ONEWAY_WARNING,
               "ignored_evidence": ignored,
               "claims_ledger": led}
    if any(c[0] == "✗" for c in checks):
        lines.append("refusing: checklist has failures")
        payload.update(ok=False, refused=True)
        return lines, 1, payload
    lines.append(ONEWAY_WARNING)
    if not yes:
        lines.append("DRY RUN — nothing changed. Re-run with --yes to execute.")
        payload.update(ok=True, refused=False)
        return lines, 0, payload
    nwo = gh_nwo(pkg.get("repo") or "") or str(pkg.get("repo"))
    p = gh("repo", "edit", nwo, "--visibility", "public",
           "--accept-visibility-change-consequences")
    if p.returncode != 0:
        raise Fail(f"gh repo edit failed: {(p.stderr or p.stdout).strip()}")
    head = git_head(repo)
    pkg["visibility"] = "public"
    pkg["status"] = "public"
    pkg["promoted_at"] = now_iso()
    pkg["promoted_commit"] = head
    record(reg, events_p, pkg, "promoted", commit=head, visibility="public")
    save_registry(reg_path, reg)
    lines.append(f"{name}: verified-private -> public at {(head or '?')[:12]}")
    sent = notify(f"[lean-registry] {name} promoted to public: "
                  f"commit {(head or '')[:7]}")
    notified = sent == "sent"
    lines.append({"sent": "notified via LEAN_PKG_NOTIFY_CMD",
                  "failed": "notify command failed; notification skipped",
                  }.get(sent, "notify command not configured; notification skipped"))
    lines.append(RL_REMINDER.format(name=name))
    payload.update(ok=True, refused=False, event="promoted",
                   status="public", visibility="public",
                   promoted_at=pkg["promoted_at"], promoted_commit=head,
                   notified=notified, rl_reminder=True, rl_hint=RL_REMINDER.format(name=name))
    return lines, 0, payload


# --------------------------------------------------------------------------
# audit

TOOLCHAIN_FLOOR_ENV = "LEAN_PKG_TOOLCHAIN_FLOOR"
DEFAULT_TOOLCHAIN_FLOOR = "leanprover/lean4:v4.35.0-rc2"   # Palomar's floor (lake comparator)
FLOOR_EXEMPT_STATUSES = ("scratch", "archived")
# The floor is the comparator/promotion requirement: it is a warning only for packages that are
# public (or past it) or already carry comparator evidence, or under `audit --promotion`; elsewhere info.
FLOOR_WARN_STATUSES = ("public", "upstreamed")
# Finding levels (JSON `level`): warn unless the check is informational, or note() says otherwise.
AUDIT_INFO_CHECKS = ("receipt-untracked", "consent-duplicate", "no-github-mirror", "claims-coverage-none")
# claims-coverage-none skips these: their claims no longer steer search or promotion work.
CLAIMS_COVERAGE_EXEMPT_STATUSES = ("archived", "upstreamed")
_TOOLCHAIN_RE = re.compile(r"^(?:leanprover/lean4:)?v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")


def parse_lean_toolchain(text) -> tuple[int, int, int, int, int] | None:
    """Sort key for a Lean toolchain string `[leanprover/lean4:]vMAJOR.MINOR.PATCH[-rcN]`,
    or None (unparsed: nightlies, channel names, anything else are never compared).
    An rc sorts before the release of the same version: (M, m, p, 0, N) < (M, m, p, 1, 0)."""
    m = _TOOLCHAIN_RE.match(str(text or "").strip())
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch)) + ((0, int(rc)) if rc is not None else (1, 0))


def resolve_toolchain_floor(flag: str | None) -> str:
    # an explicit flag, even an empty one, is used as given; an empty env variable counts as unset
    floor = flag if flag is not None else (os.environ.get(TOOLCHAIN_FLOOR_ENV, "").strip()
                                           or DEFAULT_TOOLCHAIN_FLOOR)
    if parse_lean_toolchain(floor) is None:
        raise UsageError(f"toolchain floor {floor!r} is not [leanprover/lean4:]vMAJOR.MINOR.PATCH[-rcN]")
    return floor


AUDIT_LEVELS = ("warn", "info")
AUDIT_LEVEL_WIDTH = 6        # the level column: "warn  " / "info  "


def render_finding(f: dict, width: int = 20) -> str:
    """One human audit line: the level (warn|info), the check code padded to width, then
    `NAME: DETAIL`. The level column replaces the old trailing ` [info]` suffix (#33)."""
    level = f.get("level") or "warn"
    return f"{level.ljust(AUDIT_LEVEL_WIDTH)}{(f['check'] + ' ').ljust(width)}{f['name']}: {f['detail']}"


def audit_counts(findings: list[dict]) -> dict[str, int]:
    """Findings per level: {"warn": N, "info": M} (a finding without a level counts as warn)."""
    counts = dict.fromkeys(AUDIT_LEVELS, 0)
    for f in findings:
        lvl = f.get("level") or "warn"
        counts[lvl] = counts.get(lvl, 0) + 1
    return counts


NO_GITHUB_MIRROR_DETAIL = "repo is not on GitHub; visibility not mirrored"


def audit_count_line(findings: list[dict]) -> str:
    c = audit_counts(findings)
    return f"{len(findings)} finding(s): {c['warn']} warn, {c['info']} info"


def render_findings(findings: list[dict]) -> list[str]:
    """Human audit output: no-github-mirror and gh-skipped findings collapse into one summary line
    each; the count line ("N finding(s): W warn, I info") comes first (#44: verdict first, so a truncated
    window still shows it) and again last, where scripts read it."""
    mirror = [f["name"] for f in findings if f["check"] == "no-github-mirror"]
    skipped = [f["name"] for f in findings if f["check"] == "gh-skipped"]
    shown = [f for f in findings if f["check"] not in ("no-github-mirror", "gh-skipped")]
    width = max([0] + [len(f["check"]) + 2 for f in shown])   # check column: longest code + 2
    lines = ([audit_count_line(findings)] if findings else []) + [render_finding(f, width) for f in shown]
    if mirror:
        lines.append(f"{len(mirror)} package(s) without a GitHub mirror (visibility not checked): "
                     + ", ".join(mirror))
    if skipped:
        lines.append(f"{len(skipped)} package(s): gh checks skipped (--no-gh): " + ", ".join(skipped))
    if findings:
        lines.append(audit_count_line(findings))
    return lines


def status_visibility_mismatch(pkg: dict) -> str | None:
    """Cross-field rule, audited (warn) rather than validated: verified-private means a private
    repository, public/upstreamed a public one. Registries written before the rule may hold a
    mismatch, so `validate` does not refuse it."""
    status, vis = pkg.get("status"), pkg.get("visibility")
    if status == "verified-private" and vis != "private":
        return f"status verified-private but visibility {vis} (expected private)"
    if status in ("public", "upstreamed") and vis != "public":
        return f"status {status} but visibility {vis} (expected public)"
    return None


def floor_applies(pkg: dict, promotion: bool = False) -> bool:
    """Whether the comparator toolchain floor is a warning for this package: under --promotion, for a
    public/upstreamed package, or for one with comparator evidence recorded (gate.comparator, a palomar
    entry, or a cached claim at comparator/palomar trust). Otherwise being below the floor is info: it
    matters only for promote and comparator receipts."""
    if promotion or pkg.get("status") in FLOOR_WARN_STATUSES:
        return True
    if (pkg.get("gate") or {}).get("comparator") or pkg.get("palomar"):
        return True
    return any(isinstance(c, dict) and c.get("trust") in ("comparator", "palomar")
               for c in pkg.get("claims") or [])


def _remote_abs_path(url: str) -> str | None:
    """The absolute repository path a non-GitHub remote names, whatever host it is reached through:
    `/p`, `file:///p`, scp-style `host:/p` and `ssh://[user@]host[:port]/p` all give `/p` (no
    `.git`, no trailing slash). None for a relative or home-relative path (`host:p`, `host:~/p`)."""
    u = str(url).strip()
    m = (re.fullmatch(r"file://(/.*)", u) or re.fullmatch(r"ssh://[^/]+(/.*)", u)
         or re.fullmatch(r"[^/:\s]+:(/.*)", u) or re.fullmatch(r"(/.*)", u))
    if not m:
        return None
    return re.sub(r"(\.git)?/*$", "", m.group(1)) or None


def _same_remote(a: str, b: str) -> bool:
    """Do two remote URLs name the same repository? GitHub URLs by owner/repo (any scheme, any case);
    otherwise exact match up to `.git`/trailing slash, or (field report) the same absolute path when
    one side is a plain local path: a registry `repo` that is the bare repo's path on the registry host
    matches `Host:<that path>` from a checkout elsewhere. Two remotes that both name a host keep the
    exact comparison."""
    na, nb = gh_nwo(a), gh_nwo(b)
    if na and nb:
        return na.lower() == nb.lower()
    norm = lambda u: re.sub(r"(\.git)?/*$", "", str(u).strip())
    if bool(norm(a)) and norm(a) == norm(b):
        return True
    local = lambda u: str(u).strip().startswith(("/", "file://"))
    if local(a) or local(b):
        pa, pb = _remote_abs_path(a), _remote_abs_path(b)
        return pa is not None and pa == pb
    return False


def find_moved_checkout(recorded: pathlib.Path, repo_url: str, limit: int = 500) -> pathlib.Path | None:
    """A cheap search for a moved checkout: the directories one level under the recorded path's parent
    that hold a lakefile (lean or toml) and a git remote whose URL is the package's repo. First match
    by name; None when the parent is gone, nothing matches, or repo_url is empty."""
    parent = recorded.parent
    if not repo_url or not parent.is_dir():
        return None
    try:
        kids = sorted(d for d in parent.iterdir() if d.is_dir() and d != recorded)[:limit]
    except OSError:
        return None
    for d in kids:
        if not ((d / "lakefile.lean").is_file() or (d / "lakefile.toml").is_file()):
            continue
        p = git(d, "config", "--get-regexp", r"^remote\..*\.url$")
        urls = [line.split(None, 1)[1] for line in p.stdout.splitlines() if len(line.split(None, 1)) == 2]
        if any(_same_remote(u, repo_url) for u in urls):
            return d
    return None


def missing_path_detail(name: str, pkg: dict, repo: pathlib.Path | None) -> str:
    """missing-path: the recorded path, the fix command, and a sibling checkout found by find_moved_checkout."""
    recorded = pkg.get("local_path")
    if repo is None:
        return f"no local_path recorded; set one with `lean-pkg update {name} --path DIR --reason TEXT --dry-run`"
    found = find_moved_checkout(repo, str(pkg.get("repo") or ""))
    target = "DIR"
    if found is not None:
        try:
            target = "~/" + str(found.relative_to(HOME))
        except ValueError:
            target = str(found)
    cmd = f"lean-pkg update {name} --path {shlex.quote(target) if target != 'DIR' else target} " \
          f"--reason \"checkout moved\" --dry-run"
    head = f"recorded path {recorded} is not a directory"
    if found is not None:
        head += f"; found at {found} (same lakefile and git remote)"
    return f"{head}; fix: `{cmd}`, then again without --dry-run"


def repo_override(reg: dict, name: str, repo_arg: str) -> tuple[dict, dict]:
    """`--repo DIR` on a read-only command (field report: one local_path per package, but checkouts
    on different hosts hold different branches): (a copy of the registry whose NAME record points at
    DIR for this one call, {path, registered_path, remote_match}). Never saved. DIR must be a git
    work tree; when it has remotes and the record names a repo, one remote must be that repo
    (remote_match True; `Host:/p` matches a local-path repo `/p`, see _same_remote), unless DIR is the
    registered local_path itself. Otherwise remote_match is None and the caller vouches for DIR."""
    pkg = get_pkg(reg, name)
    d = pathlib.Path(os.path.expanduser(repo_arg)).resolve()
    if not d.is_dir():
        raise Fail(f"--repo {repo_arg}: not a directory")
    p = git(d, "rev-parse", "--is-inside-work-tree")
    if p.returncode != 0 or p.stdout.strip() != "true":
        raise Fail(f"--repo {repo_arg}: not inside a git work tree")
    want = str(pkg.get("repo") or "").strip()
    p = git(d, "config", "--get-regexp", r"^remote\..*\.url$")
    urls = [line.split(None, 1)[1] for line in p.stdout.splitlines() if len(line.split(None, 1)) == 2]
    registered = pkg.get("local_path")
    try:
        registered = str(repo_path(pkg))
    except (KeyError, TypeError, ValueError):
        pass
    is_registered = False
    try:
        is_registered = registered is not None and pathlib.Path(registered).resolve() == d
    except OSError:
        pass
    match = None
    if want and urls and any(_same_remote(u, want) for u in urls):
        match = True
    elif want and urls and not is_registered:      # the registered checkout itself needs no remote proof
        raise Fail(f"--repo {repo_arg}: no git remote of it is the package's repo {want} "
                   f"(remotes: {', '.join(urls)}); pass a checkout of {name}")
    copied = dict(reg)
    copied["packages"] = dict(reg.get("packages") or {})
    copied["packages"][name] = {**copy.deepcopy(pkg), "local_path": str(d)}
    return copied, {"path": str(d), "registered_path": registered, "remote_match": match}


def repo_override_note(view: dict) -> str | None:
    """The stderr note for `--repo DIR`; None when DIR is the registered local_path itself (field
    report: the note then named the same path twice and said nothing)."""
    try:
        if view.get("registered_path") and \
                pathlib.Path(view["registered_path"]).resolve() == pathlib.Path(view["path"]).resolve():
            return None
    except OSError:
        pass
    return (f"lean-pkg: note: using --repo {view['path']} instead of the registered local_path "
            f"{view['registered_path']} (this call only; the registry is unchanged)"
            + ("" if view["remote_match"] else "; no remote to compare with the record's repo: "
               "the caller vouches that it is this package's checkout"))


_GH_NO_ACCESS_RE = re.compile(r"Resource not accessible|HTTP 403|SAML|must have (?:push|admin|read)", re.I)
_GH_NOT_FOUND_RE = re.compile(r"Could not resolve to a Repository|HTTP 404|Not Found", re.I)
_GH_UNAUTH_RE = re.compile(r"HTTP 401|Bad credentials|gh auth login|not logged in|authentication required", re.I)


def remote_reachable(repo: pathlib.Path | None, nwo: str) -> bool | None:
    """Whether a git remote of the checkout that names nwo (by its configured, un-rewritten URL)
    answers `git ls-remote`: True / False, or None when no remote names it. No prompts, 30 s cap."""
    if repo is None or not repo.is_dir():
        return None
    p = git(repo, "config", "--get-regexp", r"^remote\..*\.url$")
    names = [m.group(1) for line in p.stdout.splitlines()
             if (m := re.match(r"remote\.(.+)\.url\s+(\S+)$", line.strip()))
             and (gh_nwo(m.group(2)) or "").lower() == nwo.lower()]
    if not names:
        return None
    env = scrubbed_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
    try:
        q = subprocess.run(["git", "-C", str(repo), "ls-remote", "-q", names[0], "HEAD"], capture_output=True,
                           text=True, check=False, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return q.returncode == 0


def classify_gh_failure(p, nwo: str, repo: pathlib.Path | None, auth_ok) -> tuple[str, str]:
    """(outcome, detail) for a failed `gh repo view`: gh-missing | not-authenticated | no-access |
    not-found | error. auth_ok() is `gh auth status` succeeding (asked only when the text is ambiguous).
    GitHub answers not-found for a private repository the token cannot read, so a not-found whose
    checkout remote still answers ls-remote is no-access."""
    text = (p.stderr or p.stdout or "").strip()
    if p.returncode == 127 and "not on PATH" in text:
        return "gh-missing", "gh is not installed: gh checks skipped (install gh, or pass --no-gh)"
    access = (f"token lacks access to {nwo}: re-run `gh auth login` with a token that can read it "
              "(or pass --no-gh)")
    if _GH_UNAUTH_RE.search(text):
        return "not-authenticated", "gh is not authenticated: run `gh auth login` (or pass --no-gh)"
    if _GH_NO_ACCESS_RE.search(text):
        return "no-access", access
    if _GH_NOT_FOUND_RE.search(text):
        if not auth_ok():
            return "not-authenticated", "gh is not authenticated: run `gh auth login` (or pass --no-gh)"
        reach = remote_reachable(repo, nwo)
        if reach:
            return "no-access", access + "; the checkout's git remote reaches it, so it exists"
        if reach is None:
            return "not-found", (f"repository {nwo} not found on GitHub, or the token lacks access to it "
                                 "(no checkout remote names it to tell which)")
        return "not-found", f"repository {nwo} not found (gh and the checkout's git remote both fail)"
    return "error", f"gh query failed: {(text.splitlines() or [''])[0]}"   # whole first line (#21.7)


def gh_check_code(outcome: str) -> str:
    """The audit check code of a failed gh query: gh-missing | gh-not-authenticated | gh-no-access |
    gh-not-found | gh-error (the `gh` key keeps the bare outcome)."""
    return outcome if outcome.startswith("gh-") else f"gh-{outcome}"


def audit_registry(reg_path, reg, events_p, fix: bool = False,
                   toolchain_floor: str | None = None, promotion: bool = False,
                   use_gh: bool = True) -> list[dict]:
    """Structured findings: {"check", "name", "detail", "fixed", "level"}; level is warn or info.
    Read-only unless fix: lease sweeps and visibility/archive reconciliation both need it (an
    expired lease is reported with fixed=False otherwise).
    toolchain_floor None: the environment's floor, else the default (the TUI path).
    promotion: the floor is a warning for every package (else see floor_applies).
    use_gh False (--no-gh): no gh query; each GitHub package gets a gh-skipped info finding."""
    findings: list[dict] = []
    if toolchain_floor is None:
        try:
            toolchain_floor = resolve_toolchain_floor(None)
        except UsageError:
            toolchain_floor = DEFAULT_TOOLCHAIN_FLOOR
    floor_key = parse_lean_toolchain(toolchain_floor)
    now = dt.datetime.now(dt.timezone.utc)
    mutated = False

    def note(check, name, detail, fixed=False, level=None, **extra):
        if level is None:
            level = "info" if check in AUDIT_INFO_CHECKS else "warn"
        findings.append({"check": check, "name": name,
                         "detail": detail, "fixed": fixed, "level": level, **extra})

    auth_cache: list[bool] = []

    def auth_ok() -> bool:
        if not auth_cache:
            auth_cache.append(gh("auth", "status", "--hostname", "github.com").returncode == 0)
        return auth_cache[0]

    # Parse every lease before mutating anything: a lease that cannot be read is a finding, not a
    # traceback halfway through a --fix.
    bad_lease: set[str] = set()
    for name in sorted(reg["packages"]):
        lease = (reg["packages"][name] or {}).get("lease")
        if lease:
            try:
                lease_expiry(lease)
            except LEASE_PARSE_ERRORS as e:
                bad_lease.add(name)
                note("lease-invalid", name,
                     f"lease cannot be parsed ({type(e).__name__}: {e}); not swept")

    by_path: dict[str, list[str]] = collections.defaultdict(list)
    for name in sorted(reg["packages"]):
        lpath = (reg["packages"][name] or {}).get("local_path")
        if lpath:
            by_path[local_path_key(lpath)].append(name)
    for names in by_path.values():
        for name in names if len(names) > 1 else ():
            # no path echo: local paths are private, and findings are printed
            note("duplicate-path", name, "local_path resolves to the same checkout as "
                 + ", ".join(n for n in names if n != name) + "; one checkout per package")

    for name in sorted(reg["packages"]):   # informational (#22.7): never fixed
        pkg = reg["packages"][name] or {}
        try:
            repo = repo_path(pkg) if pkg.get("local_path") else None
        except (OSError, ValueError, TypeError):
            repo = None
        if repo is None or not repo.is_dir():
            continue
        for ns, others in namespace_clashes(reg, repo, skip=name).items():
            notes = {n: ((reg["packages"].get(n) or {}).get("namespace_notes") or {}).get(ns)
                     for n in [name, *others]}
            base = (f"Lean root namespace {ns} is also used by " + ", ".join(others)
                    + "; the two cannot be built or imported together")
            if all(isinstance(v, str) and v.strip() for v in notes.values()):
                note("duplicate-namespace", name, f"{base}; noted as intended: {notes[name]}", level="info")
            else:
                missing = [n for n, v in notes.items() if not (isinstance(v, str) and v.strip())]
                note("duplicate-namespace", name, f"{base}; rename one lean_lib, or record the collision as "
                     f"intended on both packages: `lean-pkg update {missing[0]} --namespace-note TEXT "
                     f"--namespace {ns}` (no note yet: {', '.join(missing)})")

    for name in sorted(reg["packages"]):   # informational (#23.1): never fixed; the history keeps every record
        pkg = reg["packages"][name] or {}
        allc = [c for c in pkg.get("consent") or [] if isinstance(c, dict)]
        uniq = consent_unique(pkg)
        if len(allc) > len(uniq):
            counts = collections.Counter((str(c.get("who")), str(c.get("role"))) for c in allc)
            dups = [f"{w} ({r}) x{n}" for (w, r), n in counts.items() if n > 1]
            note("consent-duplicate", name, "consent recorded more than once for " + ", ".join(dups)
                 + "; displayed once (re-recording the same consent is now a no-op)")

    # informational: never fixed, info even under --promotion. Coverage other than `synced` (empty,
    # unsynced or none) is what `search` treats as "no synced claims" and falls back to declarations.
    from . import claims as claims_mod
    for name in sorted(reg["packages"]):
        pkg = reg["packages"][name] or {}
        if pkg.get("status") in CLAIMS_COVERAGE_EXEMPT_STATUSES:
            continue
        cov = claims_mod.coverage(name, pkg)["coverage"]
        if cov != "synced":
            note("claims-coverage-none", name, f"claims: {cov} (search falls back to declarations; "
                 f"run `lean-pkg claims sync {name}`)", coverage=cov)

    def untracked(name, repo, tip, rel, label) -> None:
        if tip and git(repo, "cat-file", "-e", f"{tip}:./{rel}").returncode != 0:
            note("receipt-untracked", name, f"{rel} exists in the working tree but is not tracked at "
                 f"{label} {tip[:12]}; commit it so the evidence travels with the repository")

    for name in sorted(reg["packages"]):
        pkg = reg["packages"][name]
        mismatch = status_visibility_mismatch(pkg)
        if mismatch:
            note("status-visibility-mismatch", name, mismatch)
        lease = pkg.get("lease")
        if lease and name not in bad_lease and not lease_live(lease, now):
            who = f"{lease.get('agent')}@{lease.get('workspace')} until {lease_expiry(lease).isoformat()}"
            if fix:
                note("lease-expired", name, f"was {who} — cleared", fixed=True)
                record(reg, events_p, pkg, "lease-expired", agent=lease.get("agent"),
                       workspace=lease.get("workspace"),
                       claimed_at=lease.get("claimed_at"),
                       ttl_hours=lease.get("ttl_hours"))
                pkg["lease"] = None
                pkg["workspace"] = None
                pkg["agent"] = None
                mutated = True
            else:
                note("lease-expired", name, f"held by {who} — expired; `audit --fix` clears it")
        if pkg.get("upstream_target") == "palomar" and pkg.get("status") in ("verified-private", "public") \
                and not any(e.get("preflight_run") for e in (pkg.get("palomar") or []) if isinstance(e, dict)):
            note("palomar-preflight-missing", name,
                 "upstream_target=palomar but no palomar-preflight-pass recorded "
                 "(lean-pkg record NAME palomar-preflight-pass --field commit=… --field run=…)")
        tc_reg = str(pkg.get("lean_toolchain") or "").strip()
        if tc_reg and floor_key and pkg.get("status") not in FLOOR_EXEMPT_STATUSES:
            key = parse_lean_toolchain(tc_reg)
            warn_floor = floor_applies(pkg, promotion)
            if key is None:
                note("toolchain-unparsed", name,
                     f"{tc_reg} is not a release or rc toolchain; not compared with the floor {toolchain_floor}",
                     level="warn" if warn_floor else "info")
            elif key < floor_key and warn_floor:
                note("below-toolchain-floor", name, f"{tc_reg} is older than the floor {toolchain_floor}")
            elif key < floor_key:
                note("below-toolchain-floor", name, f"toolchain below the comparator floor {toolchain_floor} "
                     f"(matters only for promote/comparator); registry has {tc_reg}", level="info")
        repo = repo_path(pkg) if pkg.get("local_path") else None
        if repo is None or not repo.is_dir():
            note("missing-path", name, missing_path_detail(name, pkg, repo))
            continue
        tc = repo / "lean-toolchain"
        if tc.is_file():
            actual = tc.read_text().strip()
            if actual != str(pkg.get("lean_toolchain") or "").strip():
                note("toolchain-drift", name,
                     f"registry={pkg.get('lean_toolchain')} file={actual}")
        else:
            note("toolchain-missing", name, str(tc))
        from . import palomar_check   # only packages aiming at Palomar (upstream_target palomar or a palomar entry)
        if (pal := palomar_check.audit_finding(pkg, repo)) is not None:
            note("palomar-not-ready", name, pal.pop("detail"), palomar=pal)
        gate_rec = pkg.get("gate") or {}
        ref_branch = ref_tip = None
        off_ref = ""
        if gate_rec.get("receipt") or gate_rec.get("comparator"):
            ref_branch, ref_tip, ref_note = reference_branch(repo)
            here = current_branch(repo) or "detached HEAD"
            at_tip = here == "detached HEAD" and git_head(repo) == ref_tip
            if ref_branch and here != ref_branch and not at_tip:
                off_ref = f"checkout on {here}; judged against {ref_branch}"
            if ref_note:
                off_ref = f"{off_ref}; {ref_note}" if off_ref else ref_note
            if off_ref:
                note("checkout-branch", name, off_ref)
        ref_label = ref_branch or "HEAD"
        suffix = f" ({off_ref})" if off_ref else ""
        receipt_rel = gate_rec.get("receipt")
        if receipt_rel:
            rp = repo / receipt_rel
            if not rp.is_file():
                note("receipt-missing", name, str(receipt_rel))
            else:
                untracked(name, repo, ref_tip, receipt_rel, ref_label)
                try:
                    rcommit = json.loads(rp.read_text()).get("commit")
                except Exception:
                    rcommit = None
                if rcommit and ref_tip and not package_descendant(repo, rcommit, ref_tip, pkg)[0]:
                    note("receipt-drift", name,
                         f"receipt={str(rcommit)[:12]} {ref_label}={ref_tip[:12]}{suffix}")
        comp_rel = gate_rec.get("comparator")
        if comp_rel:
            cpth = repo / comp_rel
            if not cpth.is_file():
                note("comparator-missing", name, str(comp_rel))
            else:
                untracked(name, repo, ref_tip, comp_rel, ref_label)
                ccommit = (recorded_json(repo, comp_rel) or {}).get("commit")
                if ccommit and ref_tip and not package_descendant(repo, ccommit, ref_tip, pkg)[0]:
                    note("comparator-drift", name,
                         f"comparator={str(ccommit)[:12]} {ref_label}={ref_tip[:12]}{suffix}")
        if pkg.get("formalization_yaml"):
            # formalization-missing | formalization-drift | warn: the written
            # formalization.yaml must equal what the generator emits today.
            from . import formalization as formalization_mod
            for check, detail in formalization_mod.audit_findings(pkg, repo):
                note(check, name, detail)
        nwo = gh_nwo(pkg.get("repo") or "")
        if not nwo:
            # no path echo: the repo may be a private local path, and findings are printed
            note("no-github-mirror", name, NO_GITHUB_MIRROR_DETAIL)
            continue
        if not use_gh:
            note("gh-skipped", name, "gh checks skipped (--no-gh)", level="info")
            continue
        p = gh("repo", "view", nwo, "--json", "visibility,isArchived")
        if p.returncode != 0:
            outcome, detail = classify_gh_failure(p, nwo, repo, auth_ok)
            level = "warn"
            if outcome == "not-found" and pkg.get("status") == "archived":
                level = "info"
                detail = ("archived; upstream repo not found — expected if the owner removed it; see notes ("
                          + detail + ")")
            note(gh_check_code(outcome), name, detail, level=level, gh=outcome)
            continue
        try:
            info = json.loads(p.stdout)
        except ValueError:
            note("gh-error", name, "gh returned non-JSON", gh="error")
            continue
        gvis = str(info.get("visibility") or "").lower()
        if gvis and gvis != str(pkg.get("visibility")).lower():
            held = bool(pkg.get("publication_hold"))   # #5: a held record is never flipped public by --fix
            hint = ""
            if held:
                hint = (f" — {hold_text(pkg['publication_hold'])}: the repository was made public outside "
                        f"promote; make it private again, or release the hold (`lean-pkg update {name} "
                        f"--release-publication-hold --reason TEXT`); not fixed")
            elif pkg.get("status") == "public" and gvis != "public":
                hint = (f" — the owner made the repository private: record it with `lean-pkg update {name} "
                        f"--withdraw-publication --reason TEXT` (status, visibility and a publication hold)")
            note("visibility-mismatch", name,
                 f"registry={pkg.get('visibility')} gh={info.get('visibility')}" + hint
                 + (" — fixed" if fix and not held else ""), fixed=fix and not held)
            if fix and not held:
                record(reg, events_p, pkg, "audit-fix", field="visibility",
                       old=pkg.get("visibility"), new=gvis, source="gh")
                pkg["visibility"] = gvis
                mutated = True
        if info.get("isArchived") and pkg.get("status") != "archived":
            note("archived-upstream", name,
                 f"gh isArchived=true, registry status={pkg.get('status')}"
                 + (" — fixed" if fix else ""), fixed=fix)
            if fix:
                record(reg, events_p, pkg, "audit-fix", field="status",
                       old=pkg.get("status"), new="archived", source="gh")
                pkg["status"] = "archived"
                mutated = True
    if mutated:
        save_registry(reg_path, reg)
    return findings


# --------------------------------------------------------------------------
# validate

def build_inputs_errors(globs, pfx: str) -> list[str]:
    """Schema of the optional build_inputs: a list of non-empty package-relative globs (no leading /,
    no .. component)."""
    if globs is None:
        return []
    if not isinstance(globs, list):
        return [f"{pfx}.build_inputs: must be a list of globs"]
    errs = []
    for g in globs:
        if not isinstance(g, str) or not g.strip():
            errs.append(f"{pfx}.build_inputs: {g!r} is not a non-empty string")
        elif g.startswith("/") or ".." in pathlib.PurePosixPath(g).parts:
            errs.append(f"{pfx}.build_inputs: {g!r} must be package-relative (no leading /, no ..)")
    return errs


def validate_registry(data) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["top level is not a mapping"]
    version = data.get("version")
    if version not in SUPPORTED_REGISTRY_VERSIONS:
        errors.append(f"version must be one of {SUPPORTED_REGISTRY_VERSIONS}, got {version!r}")
    pkgs = data.get("packages")
    if not isinstance(pkgs, dict):
        errors.append("packages must be a mapping")
        return errors
    for key, pkg in pkgs.items():
        pfx = f"packages.{key}"
        if not isinstance(pkg, dict):
            errors.append(f"{pfx}: not a mapping")
            continue
        for f in REQUIRED_FIELDS:
            if f not in pkg:
                errors.append(f"{pfx}: missing field {f!r}")
        if pkg.get("name") != key:
            errors.append(f"{pfx}: name field {pkg.get('name')!r} != map key {key!r}")
        if version in (2, 3):
            for f in V2_PACKAGE_DEFAULTS:
                if f not in pkg:
                    errors.append(f"{pfx}: missing v2 field {f!r} (run `lean-pkg migrate`)")
        if version == 3:
            for f in V3_PACKAGE_DEFAULTS:
                if f not in pkg:
                    errors.append(f"{pfx}: missing v3 field {f!r} (run `lean-pkg migrate`)")
        if version == 3 or any(f in pkg for f in V3_PACKAGE_DEFAULTS):
            if "claims" in pkg and not isinstance(pkg["claims"], list):
                errors.append(f"{pfx}.claims: must be a list")
            cf = pkg.get("claims_file", "claims.yaml")
            if not isinstance(cf, str) or not cf.strip():
                errors.append(f"{pfx}.claims_file: must be a non-empty repo-relative path")
            csc = pkg.get("claims_synced_commit")
            if csc is not None and not _HEX40.fullmatch(str(csc)):
                errors.append(f"{pfx}.claims_synced_commit: must be 40 lowercase hex or null")
        if version in (2, 3) or any(f in pkg for f in V2_PACKAGE_DEFAULTS):
            for f in ("palomar", "consent"):
                if f in pkg and not isinstance(pkg[f], list):
                    errors.append(f"{pfx}.{f}: must be a list")
            for f in ("doi", "reservoir"):
                if f in pkg and pkg[f] is not None and not isinstance(pkg[f], dict):
                    errors.append(f"{pfx}.{f}: must be a mapping or null")
            for f in ("license_spdx", "formalization_yaml"):
                if f in pkg and pkg[f] is not None and not isinstance(pkg[f], str):
                    errors.append(f"{pfx}.{f}: must be a string or null")
            doi = pkg.get("doi")
            if isinstance(doi, dict):
                pal_commits = [e.get("commit") for e in (pkg.get("palomar") or []) if isinstance(e, dict)]
                retracted = {(str(h.get("doi")), str(h.get("commit")), str(h.get("tag")))   # ignored versions
                             for h in retracted_entries(pkg) if h.get("event") == "doi-minted"}
                for v in doi.get("versions") or []:
                    c = str((v or {}).get("commit") or "")
                    if isinstance(v, dict) and (str(v.get("doi")), c, str(v.get("tag"))) in retracted:
                        continue
                    if pkg.get("track") == "doi-only" or (isinstance(v, dict) and v.get("track") == "doi-only"):
                        continue                  # DOI-only track (#51): no palomar entry expected
                    if c and c not in pal_commits:
                        known = ", ".join(str(x)[:12] for x in pal_commits) or "none"
                        errors.append(f"{pfx}: doi {v.get('doi')} commit {c[:12]} matches no "
                                      f"palomar entry (palomar commits: {known})")
        if "track" in pkg and pkg["track"] not in TRACKS:
            errors.append(f"{pfx}: bad track {pkg.get('track')!r} (allowed: {', '.join(TRACKS)})")
        if "docs_descendants" in pkg and not isinstance(pkg["docs_descendants"], bool):
            errors.append(f"{pfx}.docs_descendants: must be true or false")
        errors += build_inputs_errors(pkg.get("build_inputs"), pfx)
        if pkg.get("headline_config") is not None:
            from .headline import path_error
            if err := path_error(pkg["headline_config"]):
                errors.append(f"{pfx}.headline_config: {err}")
        errors += paper_errors(pkg.get("paper"), pfx)
        errors += namespace_notes_errors(pkg.get("namespace_notes"), pfx)
        errors += source_paper_errors(pkg.get("source_paper"), pfx)
        errors += ai_assistance_errors(pkg.get("ai_assistance"), pfx)
        if pkg.get("status") not in STATUSES:
            errors.append(f"{pfx}: bad status {pkg.get('status')!r} "
                          f"(allowed: {', '.join(STATUSES)})")
        if pkg.get("visibility") not in VISIBILITIES:
            errors.append(f"{pfx}: bad visibility {pkg.get('visibility')!r}")
        if pkg.get("upstream_target") not in UPSTREAM_TARGETS:
            errors.append(f"{pfx}: bad upstream_target "
                          f"{pkg.get('upstream_target')!r}")
        lease = pkg.get("lease")
        if lease is not None:
            if not isinstance(lease, dict):
                errors.append(f"{pfx}.lease: not a mapping")
            else:
                for f in LEASE_FIELDS:
                    if f not in lease:
                        errors.append(f"{pfx}.lease: missing field {f!r}")
                if "claimed_at" in lease:
                    try:
                        parse_ts(lease["claimed_at"])
                    except Exception:
                        errors.append(f"{pfx}.lease: unparseable claimed_at "
                                      f"{lease.get('claimed_at')!r}")
                if not isinstance(lease.get("ttl_hours"), int) \
                        or lease.get("ttl_hours", 0) <= 0:
                    errors.append(f"{pfx}.lease: ttl_hours must be a positive int")
                if "contact" in lease and not isinstance(lease["contact"], str):
                    errors.append(f"{pfx}.lease: contact must be a string, got "
                                  f"{type(lease['contact']).__name__}")
        gate = pkg.get("gate")
        if not isinstance(gate, dict):
            errors.append(f"{pfx}.gate: not a mapping")
        else:
            if "receipt" not in gate:
                errors.append(f"{pfx}.gate: missing key 'receipt'")
            ax = gate.get("axioms_allowed")
            if not isinstance(ax, list) or not all(isinstance(a, str) for a in ax):
                errors.append(f"{pfx}.gate: axioms_allowed must be a list of strings")
            comp = gate.get("comparator")
            if comp is not None and not isinstance(comp, str):
                errors.append(f"{pfx}.gate: comparator must be a string path or null")
            if pkg.get("status") == "verified-private" and not gate.get("receipt"):
                errors.append(f"{pfx}: status verified-private requires gate.receipt "
                              f"(only `lean-pkg gate` reaches verified-private)")
        hist = pkg.get("history")
        if not isinstance(hist, list):
            errors.append(f"{pfx}.history: not a list")
        else:
            for i, e in enumerate(hist):
                if not isinstance(e, dict) or not isinstance(e.get("ts"), str) \
                        or not isinstance(e.get("event"), str):
                    errors.append(f"{pfx}.history[{i}]: needs string ts + event")
                    continue
                try:
                    parse_ts(e["ts"])
                except Exception:
                    errors.append(f"{pfx}.history[{i}]: unparseable ts {e['ts']!r}")
        errors += publication_hold_errors(pkg, pfx)
        ra = pkg.get("reaudit_after")
        if ra is not None:
            try:
                dt.date.fromisoformat(str(ra))
            except ValueError:
                errors.append(f"{pfx}: reaudit_after is not an ISO date: {ra!r}")
    return errors


# --------------------------------------------------------------------------
# board / list / status

def row_fields(pkg: dict, now: dt.datetime) -> tuple[str, str, str, str, str, str, str]:
    lease = pkg.get("lease")
    if lease and lease_invalid(lease):
        label = "? INVALID"   # fields unparseable: `release NAME --force --reason` clears it
    elif lease:
        label = f"{lease.get('agent')}@{lease.get('workspace') or '-'}"
        if not lease_live(lease, now):
            label += " EXPIRED"
    else:
        label = "-"
    return (str(pkg.get("name")), str(pkg.get("status")),
            str(pkg.get("visibility")), label, lease_expires_label(lease, now),
            str(pkg.get("mathlib_pin") or "-"),
            str(pkg.get("reaudit_after") or "-"))


def table_lines(reg: dict, now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = [row_fields(reg["packages"][k], now) for k in sorted(reg["packages"])]
    widths = [max([len(BOARD_HEADER[i])] + [len(r[i]) for r in rows])
              for i in range(len(BOARD_HEADER))]
    fmt = lambda r: "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(BOARD_HEADER))).rstrip()
    return [fmt(BOARD_HEADER)] + [fmt(r) for r in rows]


def csv_names(text: str | None, flag: str) -> list[str]:
    """A comma-separated flag value as a list (blanks dropped); an empty list is a usage error."""
    names = [x.strip() for x in (text or "").split(",") if x.strip()]
    if not names:
        raise UsageError(f"{flag} needs at least one comma-separated value")
    return list(dict.fromkeys(names))


def only_packages(reg: dict, only: str | None) -> dict:
    """`list`/`board --only NAME[,NAME]`: the registry restricted to those packages (registry order; the
    mapping's shape is unchanged). An unknown name refuses like `status` (exit 1)."""
    if only is None:
        return reg
    names = csv_names(only, "--only")
    for n in names:
        get_pkg(reg, n)
    out = dict(reg)
    out["packages"] = {k: v for k, v in (reg.get("packages") or {}).items() if k in names}
    return out


def select_fields(pkg: dict, fields: list[str], also_known: tuple[str, ...] = ()) -> dict:
    """`status/list/board --fields`: the sub-tree of the package record holding just these keys, same nesting
    as the full record. A dotted path walks mappings (gate.receipt, lease.agent); through a null it yields
    null, as does an optional lease key the lease does not set (lease.contact). A top-level key outside PACKAGE_FIELDS (and absent from the record and also_known), or a missing
    key inside a mapping, is a usage error listing the valid keys at that level."""
    table = [k for k, _, _ in PACKAGE_FIELDS]
    extra = [k for k in [*pkg, *also_known] if k not in table]   # plus any additive key a record already has
    known = table + list(dict.fromkeys(extra))
    out: dict = {}
    whole: set[tuple[str, ...]] = set()
    for path in fields:
        parts = path.split(".")
        if any(not x for x in parts):
            raise UsageError(f"--fields: malformed field {path!r}")
        if parts[0] not in known:
            raise UsageError(f"--fields: unknown field {path!r}; top-level keys: {', '.join(known)} (aliases: "
                             + ", ".join(f"{a} = {k}" for a, k in FIELD_ALIASES.items()) + ")")
        value = pkg.get(parts[0])
        for i, part in enumerate(parts[1:], start=1):
            if value is None:
                break
            here = ".".join(parts[:i])
            if not isinstance(value, dict):
                raise UsageError(f"--fields: unknown field {path!r}: {here} is a "
                                 f"{type(value).__name__}, not a mapping")
            if part not in value:
                if here == "lease" and part in LEASE_OPTIONAL_FIELDS:
                    value = None    # an optional lease key that this lease does not set
                    break
                raise UsageError(f"--fields: unknown field {path!r}; keys of {here}: "
                                 f"{', '.join(map(str, value)) or '(none)'}")
            value = value[part]
        key = tuple(parts)
        if any(key[:n] in whole for n in range(1, len(key))):
            continue            # an enclosing key is already selected whole
        cur = out
        for part in parts[:-1]:
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = cur[part] = {}
            cur = nxt
        cur[parts[-1]] = value
        whole.add(key)
    return out


# `--fields` aliases: the names the tables show, lower-cased (list/board's VIS, EXPIRES, PIN and REAUDIT columns;
# leases' CLAIMED and EXPIRES columns under `lease.`), mapped to the record keys. Resolved before validation;
# output and the `fields` list carry the record key. tests/test_columns.py derives every table column from the
# renderers and fails when one has no --fields key.
FIELD_ALIASES = {"pin": "mathlib_pin", "lease.expires": "lease.expires_at", "vis": "visibility",
                 "expires": "lease.expires_at", "reaudit": "reaudit_after", "lease.claimed": "lease.claimed_at"}


def fields_arg(args) -> list[str] | None:
    """The parsed `--fields a,b,c` of status/list/board (aliases resolved), or None when the flag is absent."""
    fields = getattr(args, "fields", None)
    if fields is None:
        return None
    return list(dict.fromkeys(FIELD_ALIASES.get(f, f) for f in csv_names(fields, "--fields")))


def trim_packages(reg: dict, names: list[str]) -> dict:
    """`list`/`board --fields`: every package record cut to select_fields(names); the dump's other keys are
    kept. Validation is status's, against PACKAGE_FIELDS plus any additive key some record has; with no
    package at all the top-level names are still checked."""
    pkgs = reg.get("packages") or {}
    extra = tuple(dict.fromkeys(k for p in pkgs.values() if isinstance(p, dict) for k in p))
    if not pkgs:
        select_fields({}, names)
    out = dict(reg)
    out["packages"] = {k: select_fields(v, names, extra) for k, v in pkgs.items()}
    return out


def drop_bulk(pkg: dict) -> dict:
    """`--no-claims`: the package record without its BULK_FIELDS keys; every other key as it was."""
    return {k: v for k, v in pkg.items() if k not in BULK_FIELDS}


def no_claims_view(reg: dict) -> dict:
    out = dict(reg)
    out["packages"] = {k: drop_bulk(v) for k, v in (reg.get("packages") or {}).items()}
    return out


def print_dump(args, reg: dict) -> int:
    """`list`/`board` off the TUI: --json dumps the (filtered, trimmed) registry; text is the table, or with
    --fields the selected keys of each package as YAML. --no-claims drops BULK_FIELDS from each record."""
    names = fields_arg(args)
    no_claims = bool(getattr(args, "no_claims", False))
    if args.json_out:
        view = with_expiry(reg)
        if names is not None:
            view = trim_packages(view, names)
            view["fields"] = names
        if no_claims:
            view = no_claims_view(view)
            view["omitted"] = list(BULK_FIELDS)
        print_json(view)
    elif names is not None:
        picked = trim_packages(with_expiry(reg), names)
        picked = (no_claims_view(picked) if no_claims else picked)["packages"]
        print(yaml.safe_dump(picked, sort_keys=False, default_flow_style=False, width=120).rstrip()
              if picked else "{}")
    else:
        print("\n".join(table_lines(reg)))
    return 0


def static_view(args) -> bool:
    """`board` prints the static table/dump instead of the TUI: --json, --only, --fields or --no-claims given,
    or a read-only --registry-snapshot."""
    return bool(getattr(args, "json_out", False)) or getattr(args, "only", None) is not None \
        or getattr(args, "fields", None) is not None or bool(getattr(args, "no_claims", False)) \
        or bool(getattr(args, "snapshot_read_only", False))


def cmd_board(args) -> int:
    reg_path = registry_path(args)
    if static_view(args):   # a filtered or trimmed view; the TUI always shows the whole registry
        return print_dump(args, only_packages(load_registry(reg_path), getattr(args, "only", None)))
    if board_interactive():
        try:
            import curses
        except ImportError:
            pass
        else:
            _Tui(curses, reg_path, events_path(args), commit_ctx=getattr(args, "commit_ctx", None)).start()
            return 0
    print("\n".join(table_lines(load_registry(reg_path))))
    return 0


def cmd_list(args) -> int:
    return print_dump(args, only_packages(load_registry(registry_path(args)), getattr(args, "only", None)))


def package_fields_help() -> str:
    """The `status --help` epilog: every top-level key of the package record, from PACKAGE_FIELDS."""
    width = max(len(k) for k, _, _ in PACKAGE_FIELDS)
    rows = [f"  {k.ljust(width)}  {meaning}" + ("" if tier == "v1" else f" [{tier}]")
            for k, tier, meaning in PACKAGE_FIELDS]
    return ("package record (the \"package\" object of status --json, each entry of \"packages\" in list/board --json; [v2]/[v3]: added by that schema "
            "version, [optional]: absent unless set):\n" + "\n".join(rows))


SCOPE_NAMES_SHOWN = 20   # comparator declarations named in status text; --json lists every one


def status_product(repo: pathlib.Path | None) -> dict:
    """What the package's page certifies, read from `kind` in coverage.yaml committed at HEAD
    (docs/design/two-tracks.md): `library` (a reusable Lean library) or `paper` (the default: a paper's
    results). Offline and read-only; never refuses: no checkout, no committed file or unreadable YAML
    gives kind None and says why. Validity is `coverage check`'s job, not this one's. Not the record's
    `track` field, which is the DOI track (palomar / doi-only)."""
    from . import coverage as cov_mod
    where = f"{cov_mod.DEFAULT_COVERAGE_FILE} at HEAD"
    if repo is None:
        return {"kind": None, "source": None, "note": "no checkout: the product kind is read from coverage.yaml"}
    raw = git_show_bytes(repo, cov_mod.DEFAULT_COVERAGE_FILE)
    if raw is None:
        return {"kind": None, "source": None, "note": f"no {cov_mod.DEFAULT_COVERAGE_FILE} committed at HEAD"}
    try:
        doc = cov_mod.load_text(raw.decode("utf-8", errors="replace"), where)
    except Fail as e:
        return {"kind": None, "source": where, "note": str(e)}
    kind = cov_mod.doc_kind(doc)
    note = f"kind: {kind}" if doc.get("kind") in ("paper", "library") else "kind absent: paper is the default"
    return {"kind": kind, "source": where, "note": note}


def status_scope(pkg: dict, repo: pathlib.Path | None) -> dict:
    """#5 P3: what the evidence actually covers, offline and read-only: the gated source commit, the recorded
    privacy (and any publication hold), the exact declarations the recorded comparator receipt compared and
    how many claimed declarations they cover, and the claims' counts by status (counts, never percentages)."""
    gate = pkg.get("gate") or {}
    have = repo is not None and repo.is_dir()
    head = git_head(repo) if have else None
    rec = recorded_json(repo, gate.get("receipt")) if have and gate.get("receipt") else None
    gated = str(rec.get("commit")) if isinstance(rec, dict) and rec.get("commit") else None
    out: dict = {
        "gate": {"receipt": gate.get("receipt"), "commit": gated, "head": head,
                 "head_is_gated_commit": bool(gated and head and gated == head),
                 "readable": isinstance(rec, dict) if gate.get("receipt") else None},
        "publication": {"status": pkg.get("status"), "visibility": pkg.get("visibility"),
                        "publication_hold": pkg.get("publication_hold"),
                        "github": "not queried (status is offline; `lean-pkg audit` asks gh)"},
        "product": status_product(repo if have else None),
        "comparator": None, "claims": None}
    claims = [c for c in (pkg.get("claims") or []) if isinstance(c, dict)]
    counts: dict[str, int] = {}
    for c in claims:
        counts[str(c.get("status"))] = counts.get(str(c.get("status")), 0) + 1
    out["claims"] = {"total": len(claims), "by_status": dict(sorted(counts.items())),
                     "synced_commit": pkg.get("claims_synced_commit"),
                     "note": "counts of declared claims, not completion percentages"}
    comp = recorded_json(repo, gate.get("comparator")) if have and gate.get("comparator") else None
    if gate.get("comparator"):
        cfgs = [c for c in ((comp or {}).get("configs") or []) if isinstance(c, dict)]
        names = sorted({n for c in cfgs for n in (c.get("theorem_names") or []) if isinstance(n, str)})
        defs = sorted({n for c in cfgs for n in (c.get("definition_names") or []) if isinstance(n, str)})
        view = {"receipt": gate.get("comparator"), "readable": isinstance(comp, dict),
                "commit": (comp or {}).get("commit"), "mechanism": (comp or {}).get("mechanism"),
                "configs": [{"path": c.get("path"), "theorem_names": list(c.get("theorem_names") or []),
                             "definition_names": list(c.get("definition_names") or [])} for c in cfgs],
                "theorem_names": names, "definition_names": defs}
        decl_claims = [c for c in claims if c.get("decl") and c.get("kind") in ("theorem", "lemma")]
        covered = reusable = reusable_cov = 0
        if decl_claims and names and have and pkg.get("claims_synced_commit"):
            from . import claims as cl_mod, comparator_config as cc
            qualified = cc.qualified_decls(cl_mod._reader(repo, str(pkg["claims_synced_commit"])), decl_claims)
            for c in decl_claims:
                hit = cc.covers(set(names), str(c["decl"]), str(c["decl"]) in qualified)
                covered += hit
                if c.get("status") == "reusable":
                    reusable += 1
                    reusable_cov += hit
        else:
            reusable = sum(1 for c in decl_claims if c.get("status") == "reusable")
        view.update(claimed_theorems=len(decl_claims), claimed_theorems_covered=covered,
                    reusable_theorems=reusable, reusable_theorems_covered=reusable_cov,
                    whole_package=False,
                    note=("the comparator compares exactly these declarations (theorem_names), never the whole "
                          "package; claims it does not name are not compared"))
        out["comparator"] = view
    return out


def scope_lines(sc: dict) -> list[str]:
    g, pub, comp, cl = sc["gate"], sc["publication"], sc["comparator"], sc["claims"]
    lines = ["scope:"]
    if g["commit"]:
        lines.append(f"  gated source commit: {g['commit'][:12]} ({g['receipt']}); HEAD "
                     f"{(g['head'] or '?')[:12]}" + (" (the gated commit)" if g["head_is_gated_commit"] else
                                                     " (not the gated commit: `gate --check` judges it)"))
    elif g["receipt"]:
        lines.append(f"  gated source commit: unreadable ({g['receipt']} is missing or not JSON in the checkout)")
    else:
        lines.append("  gated source commit: none (no gate receipt recorded)")
    hold = pub["publication_hold"]
    lines.append(f"  privacy: status {pub['status']}, visibility {pub['visibility']} (recorded; GitHub not "
                 f"queried); publication hold: " + (hold_text(hold) if hold else "none"))
    prod = sc.get("product") or {}
    if prod.get("kind"):
        what = "a library's main results" if prod["kind"] == "library" else "a paper's results"
        lines.append(f"  product: {prod['kind']}, the page certifies {what} ({prod['source']}, {prod['note']})")
    else:
        lines.append(f"  product: not declared ({prod.get('note') or 'unknown'})")
    if comp is None:
        lines.append("  comparator: none recorded (no declaration has been compared)")
    elif not comp["readable"]:
        lines.append(f"  comparator: {comp['receipt']} is missing or not JSON in the checkout")
    else:
        names = comp["theorem_names"]
        shown = ", ".join(names[:SCOPE_NAMES_SHOWN]) + (f" (+{len(names) - SCOPE_NAMES_SHOWN} more; --json lists "
                                                         f"all)" if len(names) > SCOPE_NAMES_SHOWN else "")
        lines.append(f"  comparator: compared exactly {len(names)} declaration(s), not the whole package: "
                     f"{shown or 'none'}")
        lines.append(f"    covers {comp['claimed_theorems_covered']} of {comp['claimed_theorems']} claimed "
                     f"theorem/lemma declaration(s) ({comp['reusable_theorems_covered']} of "
                     f"{comp['reusable_theorems']} reusable); the rest are not compared")
    by = ", ".join(f"{n} {k}" for k, n in cl["by_status"].items()) or "none synced"
    lines.append(f"  claim counts: {cl['total']} ({by}); counts, not completion percentages")
    return lines


def cmd_status(args) -> int:
    reg = load_registry(registry_path(args))
    pkg = pkg_with_expiry(get_pkg(reg, args.name))   # lease.expires_at: derived, as in list/board --json
    hist = pkg.get("history") or []
    names = fields_arg(args)
    no_claims = bool(getattr(args, "no_claims", False))
    omitted = {"omitted": list(BULK_FIELDS)} if no_claims else {}
    if names is not None:
        picked = select_fields(pkg, names)
        picked = drop_bulk(picked) if no_claims else picked
        if args.json_out:
            print_json({"ok": True, "name": args.name, "package": picked,
                        "history_count": len(hist), "fields": names, **omitted})
        else:
            print(yaml.safe_dump({args.name: picked}, sort_keys=False,
                                 default_flow_style=False, width=120).rstrip())
        return 0
    if no_claims:
        pkg = drop_bulk(pkg)
    try:
        repo = repo_path(pkg)
    except (KeyError, TypeError, ValueError):
        repo = None
    branch = branch_state(repo)
    now = dt.datetime.now(dt.timezone.utc)
    state, left = lease_state(pkg.get("lease"), now)
    scope = status_scope(pkg if not no_claims else get_pkg(reg, args.name), repo)
    if args.json_out:
        print_json({"ok": True, "name": args.name, "package": pkg,
                    "history_count": len(hist), **omitted, "branch": branch,
                    "lease_state": {"state": state, "seconds_left": None if left is None else int(left)},
                    "scope": scope})
        return 0
    view = dict(pkg)
    view["history"] = f"<{len(hist)} entries; last 5 below>"
    if isinstance(pkg.get("consent"), list) and len(consent_unique(pkg)) < len(pkg["consent"]):
        view["consent"] = consent_unique(pkg)     # #23.1: shown once per (who, role); --json keeps every record
    print(yaml.safe_dump({args.name: view}, sort_keys=False,
                         default_flow_style=False, width=120).rstrip())
    print(f"history (last {min(5, len(hist))} of {len(hist)}):")
    for e in hist[-5:]:
        print("  " + json.dumps(e, sort_keys=True))
    print("\n".join(scope_lines(scope)))
    notice = branch_notice(repo, branch)
    if notice:
        print(f"branch: {notice}")
    line = lease_state_line(args.name, pkg.get("lease"), now)
    if line:
        print(line)
    return 0


# --------------------------------------------------------------------------
# TUI

def parse_ttl_hours(text: str) -> int | None:
    """The TUI's TTL prompt: blank -> None, which do_claim reads as the CLI does without --ttl (a new
    claim gets DEFAULT_TTL_HOURS, the holder's re-claim keeps its lease's TTL, #21.5); otherwise a whole
    number of hours > 0, else Fail (never a silent default: "1.5" or "0" is refused, not turned into 24)."""
    t = (text or "").strip()
    if not t:
        return None
    try:
        ttl = int(t)
    except ValueError:
        raise Fail(f"ttl must be a whole number of hours, got {t!r}")
    if ttl <= 0:
        raise Fail(f"ttl must be a positive number of hours, got {t!r}")
    if ttl > MAX_TTL_HOURS:
        raise Fail(f"ttl must be at most {MAX_TTL_HOURS} hours (one year), got {t!r}")
    return ttl


TUI_HELP = """\
lean-pkg board — keys

  j / DOWN   move selection down
  k / UP     move selection up
  c          claim package (CAS lease; prompts workspace/agent/ttl/reason)
  r          release lease (holder agent; blank agent = --force)
  g          gate: validate a lower-tier receipt (gate-pending -> verified-private)
  p          promote checklist (DRY-RUN ONLY here; execute via CLI --yes)
  a          audit (read-only: reports expired leases, gh visibility, drift)
  e          show the last event-log entry for the selected package
  ?          this help
  q / ESC    quit

Colors: green=public/upstreamed  yellow=active/gate-pending
        magenta=verified-private  cyan=scratch  dim=archived  red=expired lease
"""


class _Tui:
    STATUS_PAIRS = {"public": 1, "upstreamed": 1, "active": 2, "gate-pending": 2,
                    "scratch": 4, "verified-private": 5}

    def __init__(self, curses, reg_path, events_p, commit_ctx=None):
        self.curses = curses
        self.reg_path = reg_path
        self.events_p = events_p
        self.commit_ctx = commit_ctx   # committed mode: commit after each successful write
        self.sel = 0
        self.msg = ""
        self.has_color = False

    def start(self):
        ensure_interactive()  # refuse (exit 2) rather than block on non-tty input
        self.curses.wrapper(self._main)

    def reload(self):
        self.reg = load_registry(self.reg_path)
        self.names = sorted(self.reg["packages"])
        self.sel = min(self.sel, max(0, len(self.names) - 1))

    def _main(self, stdscr):
        c = self.curses
        self.stdscr = stdscr
        try:
            c.curs_set(0)
        except c.error:
            pass
        try:
            c.start_color()
            c.use_default_colors()
            for i, color in enumerate([c.COLOR_GREEN, c.COLOR_YELLOW, c.COLOR_RED,
                                       c.COLOR_CYAN, c.COLOR_MAGENTA], start=1):
                c.init_pair(i, color, -1)
            self.has_color = True
        except c.error:
            self.has_color = False
        self.reload()
        while True:
            self.draw()
            k = stdscr.getch()
            if k in (ord("q"), 27):
                break
            elif k in (ord("j"), c.KEY_DOWN):
                self.sel = min(self.sel + 1, len(self.names) - 1)
            elif k in (ord("k"), c.KEY_UP):
                self.sel = max(self.sel - 1, 0)
            elif k == ord("c"):
                self.key_claim()
            elif k == ord("r"):
                self.key_release()
            elif k == ord("g"):
                self.key_gate()
            elif k == ord("p"):
                self.key_promote()
            elif k == ord("a"):
                self.key_audit()
            elif k == ord("e"):
                self.key_event()
            elif k == ord("?"):
                self.show_text("lean-pkg board — help", TUI_HELP)

    def attr_for(self, pkg, now):
        c = self.curses
        lease = pkg.get("lease")
        if lease and not lease_live_or_none(lease, now):
            return c.color_pair(3) if self.has_color else c.A_BOLD
        if pkg.get("status") == "archived":
            return c.A_DIM
        pair = self.STATUS_PAIRS.get(pkg.get("status"))
        return c.color_pair(pair) if (pair and self.has_color) else 0

    def draw(self):
        c = self.curses
        h, w = self.stdscr.getmaxyx()
        self.stdscr.erase()
        now = dt.datetime.now(dt.timezone.utc)
        self.stdscr.addnstr(0, 0, f"lean-pkg board — {self.reg_path}", w - 1, c.A_BOLD)
        rows = [(n, row_fields(self.reg["packages"][n], now)) for n in self.names]
        widths = [max([len(BOARD_HEADER[i])] + [len(r[1][i]) for r in rows])
                  for i in range(len(BOARD_HEADER))]
        fmt = lambda r: "  ".join(str(r[i]).ljust(widths[i])
                                  for i in range(len(BOARD_HEADER)))
        self.stdscr.addnstr(2, 0, fmt(BOARD_HEADER), w - 1, c.A_UNDERLINE | c.A_BOLD)
        for i, (name, r) in enumerate(rows[: max(0, h - 5)]):
            attr = self.attr_for(self.reg["packages"][name], now)
            if i == self.sel:
                attr |= c.A_REVERSE
            self.stdscr.addnstr(3 + i, 0, fmt(r), w - 1, attr)
        if self.msg:
            self.stdscr.addnstr(h - 2, 0, self.msg, w - 1, c.A_BOLD)
        self.stdscr.addnstr(h - 1, 0,
                            "c claim · r release · g gate · p promote(dry) · "
                            "a audit · e event · ? help · q quit", w - 1, c.A_DIM)
        self.stdscr.refresh()

    def prompt(self, label: str) -> str:
        h, w = self.stdscr.getmaxyx()
        self.stdscr.addnstr(h - 1, 0, " " * (w - 1), w - 1)
        self.stdscr.addnstr(h - 1, 0, label, w - 1)
        self.stdscr.refresh()
        self.curses.echo()
        try:
            s = self.stdscr.getstr(h - 1, min(len(label), w - 2), 200)
        finally:
            self.curses.noecho()
        return s.decode(errors="replace").strip()

    def confirm(self, label: str) -> bool:
        return self.prompt(label + " [y/N] ").lower() == "y"

    def show_text(self, title: str, text: str):
        c = self.curses
        lines = text.splitlines() or [""]
        off = 0
        while True:
            h, w = self.stdscr.getmaxyx()
            body = max(1, h - 2)
            self.stdscr.erase()
            self.stdscr.addnstr(0, 0, title, w - 1, c.A_BOLD)
            for i, line in enumerate(lines[off:off + body]):
                self.stdscr.addnstr(i + 1, 0, line, w - 1)
            self.stdscr.addnstr(h - 1, 0, "j/k scroll · q close", w - 1, c.A_DIM)
            self.stdscr.refresh()
            k = self.stdscr.getch()
            if k in (ord("q"), 27):
                break
            if k in (ord("j"), c.KEY_DOWN):
                off = min(off + 1, max(0, len(lines) - body))
            elif k in (ord("k"), c.KEY_UP):
                off = max(0, off - 1)

    def committed(self, msg: str) -> str:
        """In committed mode, commit the write just made (the caller holds the registry lock)."""
        if self.commit_ctx is None:
            return msg
        note = commit_now(self.commit_ctx)
        return f"{msg} · {note}" if note else msg

    def selected(self) -> str | None:
        return self.names[self.sel] if self.names else None

    def key_claim(self):
        name = self.selected()
        if not name:
            return
        ws = self.prompt(f"claim {name} — workspace: ")
        if not ws:
            self.msg = "claim cancelled"
            return
        agent = self.prompt(f"claim {name} — agent: ")
        if not agent:
            self.msg = "claim cancelled"
            return
        try:
            ttl = parse_ttl_hours(self.prompt(f"ttl hours [{DEFAULT_TTL_HOURS} new; a re-claim keeps its TTL]: "))
        except Fail as e:
            self.msg = f"claim cancelled: {e}"
            return
        reason = self.prompt("reason (optional): ")
        shown = f"{ttl}h" if ttl is not None else "default TTL"
        if not self.confirm(f"claim {name} for {agent}@{ws} ({shown})?"):
            self.msg = "claim cancelled"
            return
        try:
            with registry_lock(self.reg_path):   # act on the registry as it is now, not as last drawn
                self.reload()
                out, _ = do_claim(self.reg_path, self.reg, self.events_p,
                                  name, ws, agent, ttl, reason, False)
                self.msg = self.committed(out[-1])
        except Fail as e:
            self.msg = f"refused: {e}"
        self.reload()

    def key_release(self):
        name = self.selected()
        if not name:
            return
        agent = self.prompt(f"release {name} — holder agent (blank = FORCE): ")
        force = agent == ""
        reason = ""
        if force:
            reason = self.prompt("reason (required for FORCE): ")
            if not reason:
                self.msg = "release cancelled: FORCE needs a reason"
                return
        if not self.confirm(f"release {name}{' (FORCE)' if force else ''}?"):
            self.msg = "release cancelled"
            return
        try:
            with registry_lock(self.reg_path):
                self.reload()
                out, _ = do_release(self.reg_path, self.reg, self.events_p,
                                    name, agent or None, force, reason)
                self.msg = self.committed(out[-1])
        except Fail as e:
            self.msg = f"refused: {e}"
        self.reload()

    def key_gate(self):
        name = self.selected()
        if not name:
            return
        path = self.prompt(f"gate {name} — receipt path: ")
        if not path:
            self.msg = "gate cancelled"
            return
        if not self.confirm(f"run gate on {name} with {path}?"):
            self.msg = "gate cancelled"
            return
        try:
            with registry_lock(self.reg_path):
                self.reload()
                out, _ = do_gate(self.reg_path, self.reg, self.events_p, name, path)
                self.msg = self.committed(out[-1])
            self.show_text(f"gate {name}", "\n".join(out))
        except Fail as e:
            self.msg = f"gate refused: {e}"
        self.reload()

    def key_promote(self):
        name = self.selected()
        if not name:
            return
        try:
            out, _rc, _ = do_promote(self.reg_path, self.reg, self.events_p,
                                     name, yes=False)
            self.show_text(f"promote (dry-run) {name}", "\n".join(out))
        except Fail as e:
            self.msg = f"promote refused: {e}"
        self.reload()

    def key_audit(self):
        if not self.confirm("run audit? (read-only here: expired leases are reported, not cleared)"):
            self.msg = "audit cancelled"
            return
        try:
            findings = audit_registry(self.reg_path, self.reg, self.events_p,
                                      fix=False)
            text = "\n".join(render_findings(findings)) or "no findings"
            self.show_text("audit", text)
            self.msg = f"audit: {len(findings)} finding(s)"
        except Fail as e:
            self.msg = f"audit failed: {e}"
        self.reload()

    def key_event(self):
        name = self.selected()
        if not name:
            return
        last = None
        if self.events_p.is_file():
            for line in self.events_p.read_text().splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("name") == name:
                    last = e
        self.show_text(f"last event — {name}",
                       json.dumps(last, indent=2, sort_keys=True)
                       if last else "no events for this package")


# --------------------------------------------------------------------------
# wiki — render a Markdown status page (e.g. for a static wiki)

WIKI_STATUS_TIERS = ["active", "gate-pending", "verified-private", "scratch",
                     "public", "upstreamed", "archived"]

WIKI_BANNER = ("machine-generated by `lean-pkg wiki` from the lean-pkg "
               "registry — do not hand-edit; regenerate with `lean-pkg wiki`.")


def load_candidates(path: pathlib.Path) -> dict | None:
    if not path or not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        return None
    if isinstance(data, dict) and isinstance(data.get("candidates"), dict):
        return data
    return None


def render_wiki(reg: dict, candidates: dict | None,
                today: str | None = None, now: dt.datetime | None = None) -> str:
    today = today or utc_today()
    now = now or dt.datetime.now(dt.timezone.utc)

    def sort_key(item):
        name, pkg = item
        try:
            tier = WIKI_STATUS_TIERS.index(pkg.get("status"))
        except ValueError:
            tier = len(WIKI_STATUS_TIERS)
        return (tier, name)

    L = ["---", "title: Lean Verification Status", "type: lean-verification",
         f"updated: '{today}'", "---", "",
         f"> {WIKI_BANNER}", "",
         "## Packages", "",
         "| name | status | visibility | lease | mathlib pin | reaudit_after |",
         "|---|---|---|---|---|---|"]
    for name, pkg in sorted(reg["packages"].items(), key=sort_key):
        lease = pkg.get("lease")
        label = ("? (invalid lease)" if lease_invalid(lease) else
                 f"{lease.get('agent')}@{lease.get('workspace') or '-'}" if lease else "-")
        L.append(f"| {name} | {pkg.get('status')} | {pkg.get('visibility')} "
                 f"| {label} | {pkg.get('mathlib_pin') or '-'} "
                 f"| {pkg.get('reaudit_after') or '-'} |")
    L += ["", "## Active leases", ""]
    live = [(n, p["lease"]) for n, p in sorted(reg["packages"].items())
            if p.get("lease") and lease_live_or_none(p["lease"], now)]
    if live:
        for n, lease in live:
            L.append(f"- **{n}** — {lease.get('agent')}@{lease.get('workspace')} "
                     f"until {lease_expiry(lease).isoformat()}")
    else:
        L.append("- none")
    L += ["", "## Third-party adoptions", ""]
    if candidates is None:
        L.append("no adoption ledger found")
    else:
        cands = candidates["candidates"]
        adopted = [(n, c) for n, c in sorted(cands.items())
                   if c.get("status") == "adopted"]
        watch = sum(1 for c in cands.values()
                    if c.get("status") not in ("adopted", "rejected"))
        rejected = sum(1 for c in cands.values()
                       if c.get("status") == "rejected")
        if adopted:
            L += ["| package | upstream_pin | consumers | license |",
                  "|---|---|---|---|"]
            for n, c in adopted:
                consumers = (", ".join(str(x) for x in (c.get("consumers") or []))
                             or "-")
                L.append(f"| {n} | {c.get('upstream_pin') or '-'} "
                         f"| {consumers} | {c.get('license') or '-'} |")
            L.append("")
        L.append(f"radar: {len(adopted)} adopted (table above), "
                 f"{watch} watch-only, {rejected} rejected — full ledger: "
                 f"`lean-adoption-candidates.yaml`")
    L += ["", "## References", "",
          "- lean-pkg `DESIGN.md` (package lifecycle, receipts, promotion)",
          "- `lean-pkg llms` (agent contract)",
          ""]
    return "\n".join(L)


def cmd_wiki(args) -> int:
    reg_path = registry_path(args)
    reg = load_registry(reg_path)
    cand_path = (pathlib.Path(args.candidates).expanduser()
                 if getattr(args, "candidates", None)
                 else registry_home_dir(args) / "lean-adoption-candidates.yaml")
    candidates = load_candidates(cand_path)
    text = render_wiki(reg, candidates)
    if args.out:
        out_p = pathlib.Path(args.out).expanduser()
        try:
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(text)
        except OSError as e:
            raise Fail(f"cannot write {out_p}: {e.strerror or e}")
        if args.json_out:
            adopted = sum(1 for c in (candidates or {}).get("candidates", {}).values()
                          if c.get("status") == "adopted")
            print_json({"ok": True, "out": str(out_p),
                        "packages": len(reg["packages"]), "adopted": adopted})
        else:
            print(f"wrote {out_p}")
    elif args.json_out:
        adopted = sum(1 for c in (candidates or {}).get("candidates", {}).values()
                      if c.get("status") == "adopted")
        print_json({"ok": True, "out": None,
                    "packages": len(reg["packages"]), "adopted": adopted,
                    "markdown": text})
    else:
        print(text, end="")
    return 0


# --------------------------------------------------------------------------
# llms — agent-oriented usage contract (paste into dispatch prompts)

LLMS_TEXT = """\
lean-pkg llms.txt — agent contract for the Lean 4 package registry

PURPOSE
A registry YAML file is the source of truth for a set of Lean 4 formalization repos.
lean-pkg is the ONLY write path; GitHub is just the visibility mirror. history[] and the
event log (lean-package-events.jsonl next to the registry) are append-only.
Registry resolution: --registry PATH, else env LEAN_PKG_REGISTRY, else ~/.lean-pkg/registry (dir with lean-packages.yaml, or the file; base env LEAN_PKG_HOME), else ./lean-packages.yaml.

STANDING RULES
- NEVER hand-edit the registry yaml. Use `lean-pkg register` / `lean-pkg update`.
- Before starting Lean work on a package: `lean-pkg board --json` (or `list --json`; one package: `status NAME --json [--fields ...]` or `list --json --only NAME`). Writing a paper alongside: check selected intermediate results as they stabilise (docs/paper-companion.md); never assume the target theorem to gain coverage; a certificate covers its exact formal statements, not the whole paper.
- Claim before working: one package = one workspace. Default TTL 24h; `audit --fix` sweeps expired leases. Work longer than the TTL: `renew` before it lapses (`leases --agent A --expiring 2`). gate, comparator, record, promote --yes, claims sync, search --record: pass --agent A (another agent's live lease refuses; A is the event's `actor`); without it a live lease only warns.
- Mutating ops are dry-run by default unless a flag says otherwise: promote --yes, audit --fix.
- Fully non-interactive: pass flags, never wait for a prompt. Use --json for machine parsing.
- Shared registry in git: --commit (env LEAN_PKG_COMMIT=1) pulls --ff-only, writes, commits the yaml + event log together; --push (LEAN_PKG_PUSH=1) also pushes (a transient transport error such as a connection reset, timeout or HTTP 5xx is retried with backoff: LEAN_PKG_PUSH_RETRIES, default 4, delays LEAN_PKG_PUSH_RETRY_DELAY doubling from 5 s; rejected as non-fast-forward: fetch + `rebase --autostash` of that one commit + push again, an event-log-only conflict merges as a union in timestamp order, any other conflict aborts the rebase with the clone as before, `; rebase conflict in FILES` in the error; a rebased registry with a schema error neither side had is not pushed (reset to the local commit, `; rebased registry fails validation (...)`); a failed push: `push failed: origin/BRANCH: ERR`, commit kept, exit 1, recovery printed (after a rejection: git pull --rebase origin BRANCH, then git push origin BRANCH); a write whose event log cannot be appended to (read-only, a directory) refuses before the registry is replaced, nothing written; a --push write refuses while the clone is ahead of origin/BRANCH); --no-commit overrides; a committed-mode write refuses over modified registry files, naming the uncommitted event (event, package, agent, time), the package's lease holder and the git commit that clears it; a write without committed mode that leaves the registry files in a git clone modified ends with stderr `NOT COMMITTED: FILES modified in CLONE; every --commit/--push write to this registry refuses until they are committed: ...` (--json + registry_uncommitted [files]). stderr: `committed SHA on BRANCH: MSG` (+ `(committed mode: env VAR=1)` when the env, not a flag, turned it on), `pushed origin/BRANCH (SHA)`; `CMD --help` says `set now` when a variable is set. Every write is covered (index add, formalization --write, claims sync, search --record, board TUI keys); dry runs and read-only commands never fetch (an explicit --commit there: stderr "ignored"). Reports on stderr; a --json payload gains "registry_commit": {sha, pushed, error, push_error, push_attempts, rebased} (also as "commit" unless the payload has its own, e.g. gate's source commit).

STATUS ENUM AND TRANSITIONS (evidence required)
scratch -> active                  owner_project named
active -> gate-pending             lower-tier check submitted (terminal executor job identity in notes); also verified-private -> gate-pending to re-gate a new commit (one step instead: `gate --regate`)
gate-pending -> verified-private   `gate` accepts a receipt (schema below)
public -> public                   `gate` on an already-public package records evidence; status unchanged
verified-private -> public         `promote --yes` after a clean dry-run checklist; ONE-WAY flip
public -> upstreamed               `update --status`: a recorded mathlib-pr-merged / palomar-registered event, or a --reason with the URL
any -> archived                    `update --status archived --reason` or `audit --fix` (gh isArchived); public returns to a private status only this way: public -> verified-private by `update --withdraw-publication --reason` only: the owner made the repository private (gh must not report it public; active when never gated); visibility private + publication_hold {reason, at, by}; promotion history kept; promote refuses while held; gate/regate/audit --fix keep it; only `update --release-publication-hold --reason` lifts it

SUBCOMMANDS (global flags: --registry PATH --events PATH --json --commit --push --no-commit, also after the subcommand; env LEAN_PKG_REGISTRY, LEAN_PKG_HOME, LEAN_PKG_EVENTS (ignored when --registry is given: its sibling log); --version: `lean-pkg X.Y.Z (<sha>[+dirty], editable <checkout>)` or `(installed <dir>)`, local only; --json {version, commit, dirty, install, path}; --registry-snapshot COMMIT (env LEAN_PKG_REGISTRY_SNAPSHOT): read-only, the registry + event log as of COMMIT of its git repo (git show into a temp copy); reads work (status/list/board/audit/dry runs/comparator config --dry-run), every write exits 2 `read-only: --registry-snapshot`; --json +registry_snapshot sha)
board     TUI on a tty, static table otherwise; --only, --fields, --no-claims as for list (any: never the TUI). ex: lean-pkg board --json --only my-lean
list      static table; --only NAME[,NAME] filters packages (full records, shape unchanged); --fields a,b.c trims each record as status --fields (+ `fields` list; text: YAML); --no-claims as status. ex: lean-pkg list --json --only my-lean --fields status
status    full record + last 5 history entries (a lease adds derived expires_at, UTC; a lapsed lease ends the output with `lease: EXPIRED ...`, one lapsing within 2h with `lease: expires in ...`; --json lease_state {state live|expiring|expired|invalid|none, seconds_left}); --fields a,b.c: only those keys (dotted paths; lease.contact: null when unset; aliases = the table columns: pin, vis, reaudit, expires|lease.expires, lease.claimed; --json keeps nesting, text YAML; unknown: exit 2 listing keys); record keys: status --help, docs/cli.md#package-record. --no-claims: drop [bulk] keys (claims; also list/board; +omitted[]). Without --fields: a `scope:` text block and --json scope {gate {receipt, commit, head, head_is_gated_commit, readable}, publication {status, visibility, publication_hold, github}, product {kind paper|library|null, source, note}, comparator null|{receipt, readable, commit, mechanism, configs[], theorem_names[], definition_names[], claimed_theorems, claimed_theorems_covered, reusable_theorems, reusable_theorems_covered, whole_package false, note}, claims {total, by_status, synced_commit, note}}: the gated source commit, recorded privacy + hold (offline: gh not queried), the product (kind in coverage.yaml at HEAD; paper when absent; not the DOI track), the exact declarations the comparator receipt compared and the claimed theorems/lemmas they cover (never the whole package), claim counts by status (not completion percentages). ex: lean-pkg status my-lean --json --fields status,gate.receipt,lease.agent
new       scaffold a minimal Lean package in a new directory (refused when it exists and is not empty) and git init it; no registry read or written, nothing committed, fetched or built: lean-toolchain (v4.35.0-rc2), .gitignore, lakefile.toml (library MOD = NAME in CamelCase, --module MOD; requires Mathlib unless --no-mathlib), MOD/Challenge.lean (sorry) + MOD/Solution.lean (module-headed, the getting-started template) + MOD/Axioms.lean (#print axioms), comparator.json, claims.yaml (one reusable claim), coverage.yaml (kind library), CITATION.cff (--author, else git user.name), LICENSE (--license Apache-2.0|MIT), README.md (the lean-pkg credit), verify.sh (the log `receipt systemd --run` reads; --no-mathlib: a `mathlib-rev: none` line); NAME [a-z][a-z0-9]*(-[a-z0-9]+)*, at most 64 (else exit 2); --json {ok, name, dir, module, mathlib, lean_toolchain, license_spdx, files[], git_init, committed, registry_touched, next_commands[]}. ex: lean-pkg new my-proof; the path to a page: docs/quickstart.md
init      bootstrap an empty registry + event log (event: file name + version; --force: empty only, forced: true). ex: lean-pkg --registry ./lean-packages.yaml init
migrate   upgrade the registry schema in place to v3 (v2 adds license_spdx, formalization_yaml, palomar[], doi, reservoir, consent[];
          v3 adds claims_file, claims[], claims_synced_commit; additive, idempotent; a legacy literal ~/x local_path is rewritten $HOME-relative). ex: lean-pkg migrate --dry-run
record    record a D5 evidence event; validated before writing (a schema error it introduces refuses, rule named, nothing written); --field keys (all required, others refused; commit 40-hex, version integer):
          consent-recorded: who role pointer (a /home, /Users, ~ path: WARN + private_strings; same who+role+pointer again: already recorded, exit 0) | palomar-preflight-pass: commit run (same commit+run again: already recorded, exit 0)
          palomar-submitted: commit submission_id comparator_config | palomar-review: commit outcome(neutral|revision_required|rejected)
          palomar-registered: commit id version(int) comparator_config (status public|upstreamed; config tracked at HEAD) | palomar-withdrawn: id version(int) (of a recorded entry) | mathlib-pr-opened, mathlib-pr-merged: url (https://github.com/...)
          doi-minted: doi(10.NNNN/x) tag commit [concept] (rule doi-in-palomar: commit already recorded by a palomar-* event; skipped on the DOI-only track, --track doi-only or the record's track: doi-only, status public only, rule doi-only-public; the event carries track) | reservoir-listed: url (the
          package's https://reservoir.lean-lang.org/@owner/name page; Reservoir API: 404 refuses, unreachable records verified=false; a url naming neither package nor repo warns)
          retract: event ts + --reason: append-only correction (event evidence-retracted); validate and the upstreamed check ignore the entry
          ex: lean-pkg record my-lean doi-minted --field doi=10.5281/zenodo.1 --field tag=v1.0.0 --field commit=<40hex>
register  add a package; dedupes by name AND resolved local_path (~/ expanded, stored $HOME-relative; empty --path/--repo refused); auto-detects toolchain + mathlib pin; a Lean root namespace (lakefile lean_lib, else top-level *.lean) another package's checkout also uses: stderr note + `notes`, never a refusal. --like OTHER: a sibling package; copies repo/owner-project/upstream-target/automation from OTHER (flags win; never visibility), refused when DIR is in another git repo and --repo is absent; notes whether toolchain/Mathlib pin match OTHER's (JSON like {name, copied[], toolchain, mathlib_pin}). warn: DIR inside or containing another package's dir; a lake path dependency outside DIR.
  flags: --repo URL (required) --path HOME-RELATIVE (required) [--owner-project N]
         [--visibility private|public] [--status scratch|active|gate-pending|public (public: --visibility public --reason R)]
         [--upstream-target none|mathlib|palomar] [--automation TEXT] [--notes TEXT] [--workspace W] [--agent A] [--dry-run]
  --workspace/--agent are metadata only; they do NOT claim. Claiming is a separate act.
  ex: lean-pkg register my-lean --repo git@github.com:you/my-lean.git --path src/my-lean
update    set fields (--notes over non-empty notes: exit 2, nothing written, unless --replace-notes, which replaces, prints the old value, event notes_replaced + notes_from; --append-note TEXT --agent A appends `YYYY-MM-DD (A): TEXT` and prints only `appended note: <line>` (--json changes.notes keeps from/to), not with --notes); manual status moves and --force need --reason (else exit 1; a refusal lists the allowed moves); empty --repo/--owner-project refused; --license-spdx takes an SPDX expression (MIT OR Apache-2.0); --paper-arxiv ID / --paper-status unpublished|preprint|published set the optional paper {arxiv, status} (an id alone: preprint). --source-paper-title T --source-paper-author A (repeatable) --source-paper-arxiv ID --source-paper-doi DOI / --ai-tool T --ai-role R (repeatable): optional source_paper {title, authors[], arxiv, doi} / ai_assistance {tools[], roles[]}, rendered as formalization.yaml acknowledgements lines (never project.authors; no consent needed). --namespace-note TEXT [--namespace NS]: namespace_notes {NS: TEXT} (default NS: those shared with another package); audit duplicate-namespace is info once all sharers have one. --track palomar|doi-only --reason R: the optional DOI track (see record doi-minted). --accept-docs-descendants | --no-accept-docs-descendants --reason R: the optional docs_descendants true/false (opt in to the docs-only rule, see gate); --build-inputs GLOB,GLOB --reason R: the optional build_inputs list ('' clears); --headline-config REL: the optional headline_config (the headline comparator configuration formalization renders as status.main_results; '' clears); these three accept --dry-run (event update-dry-run, nothing written). register/update/record: schema errors already in the registry (e.g. another package's) go to stderr as pre-existing, not blocking.
  ex: lean-pkg update my-lean --status gate-pending --reason "check job 4242 submitted"
  --withdraw-publication --reason TEXT [--agent A] [--dry-run]: record the owner's privacy withdrawal of a public package (see the transitions; event publication-withdrawn {status_from, status_to, visibility_from, visibility_to, reason, by, github_visibility, promoted_commit, forced}; JSON status/visibility {from, to}, publication_hold, github_visibility, github_check). --release-publication-hold --reason TEXT [--agent A] [--dry-run]: lift it, status/visibility unchanged (event publication-hold-released {reason, hold, by, forced}). Each on its own (other fields: exit 2).   |   --path NEW --reason TEXT [--agent A] [--redetect] [--dry-run]: relocate (same name, history, claims; event relocated). Same path: no-op,
  exit 0. Refuses another package's path, a foreign live lease (no --force). Gate/claims not bound to NEW's HEAD are cleared (`cleared`). Output ends with a binding block (receipt commit vs new HEAD, evidence-only verdict, formalization.yaml exception) + `next:` commands (or "nothing to re-gate"); JSON binding{} + next_commands[].
claim     (alias: lease) CAS lease; refuses if another agent holds an unexpired lease (names holder + expiry). Any other command naming a package whose lease has lapsed, or lapses within 2h, prints stderr `WARN: NAME lease: EXPIRED ... | expires in ...` with the claim/renew that keeps it (not claim/renew/release/leases/audit/status). Writes take a file lock on <registry>.lock (sibling); safe across processes on one host.
  flags: --workspace W --agent A (required) [--ttl HOURS: 1..8760, 24 new; a holder's re-claim keeps its TTL; above 8760: exit 2, nothing written] [--reason TEXT] [--contact TEXT: address of the holding session (e.g. lab-desk/n14-paper, @lean-ops), stored as lease.contact; one line, <= 200 chars, else exit 2; a re-claim without it keeps it] [--force (needs --reason)]
  ex: lean-pkg claim my-lean --workspace w3 --agent vera --reason "chapter 2"
release   holder-only unless --force --reason (released_by: --agent, else force). ex: lean-pkg release my-lean --agent vera   |   release NAME --fresh-repo DIR [--remote URL] [--keep-receipts] [--source-commit SHA] [--dry-run] (no lease involved): git archive of the gate-bound commit (package dir; drops .lean-pkg-private-patterns, internal paths, .lean-receipts/ unless --keep-receipts, listed; carries ONLY its allow: lines into a committed .lean-pkg-public-allow, merged with one in the tree, plan line `public allow: N allow line(s) ... carried`, JSON public_allow {file, carried, lines[]}; an allow line a private block pattern matches refuses, naming its line number) -> public-safety scan (blocking hit: refused, nothing written; warnings listed) -> new repo at DIR, ONE commit "fresh-history export of <sha12>; receipts and gate must be re-run on this commit" (+ source commit, export date), origin = --remote -> relocated as update --path DIR --reason "fresh-history release" (gate/comparator cleared, verified-private -> gate-pending, dated note keeps old receipt paths + source sha; --remote sets repo); refuses public/upstreamed/archived, a non-empty DIR; --dry-run: plan + next commands (gate, comparator-receipt --prepare, publish), writes nothing   |   --fresh-repo DIR --from-tree SRCDIR (not with --source-commit): exports the WORKING TREE at SRCDIR (hand-cleaned, not a commit; in a checkout `git ls-files --cached --others --exclude-standard`, else every file but .git/ .lake/), same drops + scan (+ the commit message), message "fresh-history export of working tree <basename>; ..." + "source commit: none (hand-cleaned tree)" + "source package commit for provenance: <gate-bound sha|none>"   |   release NAME --adopt-repo DIR [--remote URL] [--allow-history] [--dry-run]: adopt an existing repo as the public history, no export, no new commit; refuses >1 commit on all refs (unless --allow-history), DIR not its top dir, the package's own source repo, modified tracked files, an origin other than --remote, blocking public-safety hits in its tree or full history; then the same relocation (reason "adopted-repository release"; note names the adopted commit + previous gate-bound sha)
renew     holder-only restart of a LIVE lease (event claim-refreshed); a lapsed lease is refused: claim again. --ttl HOURS: 1..8760 (above: exit 2, nothing written). --contact TEXT sets/replaces lease.contact, omitted keeps it; --no-contact clears it (event contact_cleared: true when one was removed; with --contact: exit 2). leases [--agent A] [--expiring H]: held leases, soonest first, last column CONTACT (blank when unset; JSON contact|null); doctor warns on leases lapsing within 2h or expired-unswept. Unparseable lease fields: shown INVALID, writes refused; `release NAME --force --reason TEXT` clears.
gate      validate receipt; gate-pending -> verified-private; --receipt/--comparator PATH: a path that exists as given (cwd-relative or absolute) is used as given; a relative one missing from the cwd but present under the package checkout (or --check --repo DIR) is resolved there with a stderr note, a `· receipt R resolved against the package checkout: P` line and JSON resolved_paths[] {what, arg, path, base} (the same relative path, never another matching receipt; also comparator NAME); otherwise refused naming the caller cwd, the checkout, both paths tried and the fix (absolute path, run from the checkout, or --repo DIR with --check). --check takes no lease and checks none, so --agent with --check is a usage error (exit 2) naming the writing form. identical re-gate = no-op "already gated". gate/regate record the receipt's lean_toolchain and mathlib_rev[:10] into lean_toolchain/mathlib_pin when each agrees with lean-toolchain / lake-manifest.json at the receipt commit (a prefix match is unchanged; one "pins updated" line; event and JSON pins {field: {from, to}}); a receipt whose lean_toolchain or mathlib_rev (prefix-equal) disagrees with lean-toolchain / the mathlib rev in lake-manifest.json committed at its commit is refused; with no readable mathlib entry the rev is not checked and the pin is kept: "NAME: pins kept: receipt mathlib_rev X does not match lake-manifest.json at <c12> (unreadable)", JSON pins_kept {field: {receipt, manifest}}; --check reports both, nothing written. report_sha256 must equal the SHA-256 of <c12>-<job slug>-verify.redacted.log wherever found (beside the receipt, .lean-receipts/, tracked at HEAD), else refused; a <name>.gz copy (receipt --compress-log) is stream-decompressed and its UNCOMPRESSED bytes must hash to report_sha256 (never the .gz hash), the .gz to report_compressed.sha256 and the length equal report_compressed.bytes when recorded, output past that refused (gzip-bomb guard); none found (or a copy unreadable) is refused too: "report log missing: <name> ...", naming where it looked and the path to restore it to (docs/receipts.md "Restoring preserved evidence"); the same check runs wherever a recorded lower-tier receipt is judged (ledger/promote, next, comparator, palomar, index check); JSON report_log {checked, log, where[], copy[]}; the sealed log beside --receipt is copied beside the canonical .lean-receipts/<c12>-<job>.json when no copy is there, so it verifies on its own. Every check of both copies runs before either is written (a refusal writes nothing); a symlinked .lean-receipts or destination file is refused. --regate [--reason T]: re-gate verified-private (or public) in one step, status unchanged, event regated {previous_commit, commit, job_id, reason[, non_descendant]}; the commit must descend from the gated one unless --reason; plain gate on verified-private is refused naming --regate; --check --regate writes nothing. claims.yaml tracked at the receipt commit (never the working tree) must pass the claims/1 schema and every claim's decl must occur in its file at that commit (claims sync's check), else refused, nothing copied (JSON claims_schema {checked, commit, file, ok, claims, errors[], declarations {checked, ok, problems[]}}). Judged on the receipt commit's tree, refused (sources not bound): a committed symlink with an absolute or `..` target, or one resolving into .lean-receipts/ or to the formalization.yaml pointer; a lake-manifest.json `path` package whose dir leaves the package dir, or is (or passes through) a symlink, a submodule gitlink or an untracked dir (.lake/), or contains a gitlink. A tracked in-package path dependency is fine. Progress files (PROGRESS.md, state file, ledgers) live outside the package dir: any other change inside it needs a new receipt, and so does a .gitattributes/.gitmodules/.lfsconfig changed at the repo root or between it and a subdirectory package, unless the package opted in (update --accept-docs-descendants --reason R; record docs_descendants: true): then HEAD may also be a docs-only descendant of the receipt commit C, where no build input under the package dir changed (*.lean, lakefile.*, lake-manifest.json, lean-toolchain at any depth, the record's build_inputs globs, files the receipt's checker names; always refused: a symlink or gitlink added/changed/removed, a changed file a committed symlink points to, .gitattributes, .gitmodules, .lfsconfig, claims.yaml, and for a subdirectory package a .gitattributes/.gitmodules/.lfsconfig at the repo root or in any directory between it and the package dir; formalization.yaml keeps its derived-keys rule). The gated commit stays C; text "✓ HEAD <c12> is a docs-only descendant of <c12> (N non-build file(s): ...; opted in)"; gate/regate/comparator events and --json (and --check) add descendant_of C and docs_paths[] (<= 50). Without the opt-in a refusal the docs rule would have admitted adds the hint `update NAME --accept-docs-descendants`.
  flags: --receipt PATH (required), --comparator PATH (optional), --refuse-private (private paths in a receipt: refuse, exit 1,
  nothing copied; default a stderr WARN "receipt field F contains a ... (kept: ...; --refuse-private refuses such a receipt)" + JSON private_strings[{field, kind}]), --refuse-dirty (modified tracked files; default WARN + JSON dirty[]), --refresh-formalization [--agent A] (then regenerate formalization.yaml + pointer in one step, JSON "formalization": {...}; lease + schema checked BEFORE the gate; identical re-gate with a current file: refused, nothing written; no pointer recorded yet: the derived-only formalization.yaml exception applies to the path it will own), --check (read-only, any status: every gate validation vs HEAD, both tiers with --comparator; ✓ lines or gate's refusal; claims.yaml at the receipt commit: ✓ schema ok / ✗ first error (exit 1) / · not tracked; no write, no event; exit 0/1; JSON event gate-checked, checks[], claims_schema; comparator --check prints the same binding ✓ lines), --check --repo DIR (validate against the checkout DIR, not the registered local_path, this call only: never saved; stderr note; JSON repo_override {path, registered_path, remote_match}, null without it; DIR must be a git work tree and, when it has remotes, one must be the package's repo (a local-path repo /p also matches Host:/p, ssh://Host/p, file:///p; DIR = the registered local_path needs no remote), else the caller vouches; a writing gate refuses --repo, exit 2: it binds the registered checkout, move that with update --path). Success (and promote --yes) ends with a results-ledger: line (JSON rl_reminder, rl_hint): lean-pkg keeps no results ledger, the outcome is in `status NAME` and `claims ledger NAME`; add it to the project's own results ledger if it keeps one (docs/lifecycle.md#results-ledger)
  ex: lean-pkg gate my-lean --receipt /tmp/receipt.json --comparator /tmp/comparator.json   |   receipt slurm --job J --log PATH --commit SHA [--stdout JOB.out] [--stderr JOB.err] [--checker TEXT] [--repo DIR] [--redact REGEX]... [--sacct-file F] [--out PATH] [--append-readme] (no registry): writes the lower-tier receipt from a finished Slurm job; refuses (exit 1, nothing written) unless sacct's main job line is COMPLETED 0:0, the stdout's verify-log-sha256 = sha256(raw log) and pre-manifest-sha256 = post-manifest-sha256 = sha256(lake-manifest.json at SHA) (no --stdout: the log's own `verify-log-sha256:` line = sha256 of the log minus that line), stderr is empty, lean_toolchain/mathlib_rev from SHA agree with any log line, sorry 0 and axioms from the log (sorry-count:/axioms: lines, else 'sorry' warnings/#print axioms output; a sorry-count: below the log's own 'sorry' warnings, or an axioms: line omitting an axiom its #print axioms output reports, is refused; universe suffixes .{u} ignored), a checker (--checker or checker:), a log commit: line equal to SHA (none: refused without --stdout; with --stdout a WARN "the verify log has no commit: line; it is bound to the commit only through lake-manifest.json", JSON log_commit_bound false, also in the written receipt, and a `- WARN:` line in the README entry), fields read from the RAW log (a redaction that removes or alters a sorry warning, #print axioms output, a build-completion line or a key: line other than checker: is refused; checker is published redacted), no committed symlink at SHA into .lean-receipts/, and no public-safety blocking hit is left after redacting /tmp/<account>-job, /scratch/<user>/ and --redact (blocking also this machine's user and host names and a bare /home/<x>, /Users/<x>); --append-readme: a symlinked or non-UTF-8 README.md refuses before writing; job_id slurm-<J>; writes .lean-receipts/<c12>-slurm-<J>.json + <c12>-slurm-<J>-verify.redacted.log (report_sha256; --compress-log: <...>.log.gz instead, deterministic gzip, report_sha256 still of the uncompressed log, additive report_compressed {format gzip, sha256 of .gz, bytes}, restore gzip -dc X.log.gz > X.log; without it a log over 90 MiB WARNs naming the flag; log_commit_bound true/false; with --stdout also job_stdout_sha256 + manifest_sha256; additive keys gate ignores: it never reads log_commit_bound), prints the README entry (with --stdout a `- Seal:` line naming both; --append-readme: appended to .lean-receipts/README.md) and `gate NAME --check --receipt PATH`; --json {ok, receipt, log, report_sha256, job_id, sealed[] {role, path, sha256, receipt_key} (the exact files bound; systemd's `properties` is the raw unsealed capture), checks[], refusals[], ...}; --template (no other flag, no registry): print a minimal sbatch wrapper emitting every line the receipt reads (stdout seal lines, empty stderr, checker:/sorry-count:/axioms: in the log; exact-runtime preflight before any tool runs: LC_ALL=C LANG=C PYTHONUTF8=1 (#SBATCH --export line + export; stderr stays strict, never filtered) and a `type -P` probe of the host tools (+EXTRA_TOOLS) and, under $RUN, the first word of VERIFY_CMD/AXIOMS_CMD/LEAN_PATH_CMD (+lean beside lake, +RUN_TOOLS): a missing one fails the job before the work, log line runtime-preflight: ok (...); optional artifact seal ARTIFACTS_DIR=<dir relative to the job's tree, or to $WORK with ARTIFACTS_BASE=work>: after the audit the job copies every regular file under it to ARTIFACTS_OUT (default $OUT_DIR/artifacts-<J>, never overwritten) with artifacts.sha256, sorted `<path> <size> <sha256>` lines (a symlink, special file or newline name fails the job), and prints `artifacts-manifest-sha256 <hex>` on stdout before post-manifest-sha256); --check --log PATH [--stdout] [--stderr] [--commit SHA] [--job J --sacct-file F]: preflight a job's files, every problem named, no sacct, nothing written, exit 0/1, --json {ok, log_commit_bound (true/false; null without --commit), checks[], problems[]}; --preflight-deps (--deps-tar T | --deps-dir D) [--commit SHA, default HEAD] [--entries GLOB]... (before sbatch, reads only, builds and writes nothing, exit 0/1): the import closure of the entry files committed at SHA (default Verify/**/*.lean, Audit/**/*.lean, OwnedAudit/**/*.lean, **/AxiomCheck*.lean, **/TypeCheck*.lean, **/Examples*.lean, **/OwnedAudit*.lean), followed through the package's modules and the dependency sources in the cache, must have every external module's .olean and .ilean (regular files) in exactly one manifest package (import Mathlib = Mathlib.olean and everything Mathlib.lean imports, transitively; toolchain roots Init/Std/Lean/Lake aside), every manifest git package present at its rev (.git/HEAD); --json {ok, preflight_deps, commit, entries[], required_external_modules, missing_count, missing[] {module, package, files, required_by}, checks[], problems[]}; --artifacts DIR [--artifacts-manifest NAME] (needs --stdout; also with --check): sha256(DIR/NAME, default artifacts.sha256) must equal the stdout's one artifacts-manifest-sha256 line, and every file under DIR is rehashed in 1 MiB chunks against it (missing, extra, changed files, symlinks, special files, path traversal, duplicate or unsorted entries refuse; previously copied files are adopted only by this rehash, never by another attempt's hashes); the receipt gains artifacts_manifest_sha256 (additive, gate ignores it) and the manifest is written beside it as <c12>-slurm-<J>-artifacts.sha256 (sealed[] role artifacts.sha256), JSON artifacts {ok, dir, files, bytes, manifest, manifest_sha256}; without --artifacts nothing changes   |   receipt systemd (alias local) --unit U --log PATH --commit SHA [--properties-file F | --run [--memory-max 8G] [--memory-swap-max 0] [--cpu-quota 400%] [--runtime-max 4h] [--setenv K=V]... [--properties-out PATH] -- CMD...] + the same --stdout/--stderr/--checker/--repo/--redact/--out/--append-readme/--json: the same receipt from a finished systemd --user service unit; the unit state comes from --run (lean-pkg runs CMD bounded via systemd-run --user -p RemainAfterExit=yes, WorkingDirectory=--repo, so the finished unit stays loaded: properties are captured to <log>.unit.properties before it is stopped and reset; the checkout must be at SHA with no tracked change and no untracked file (less .lean-receipts/, .lake/ and the run's own files; ignored files are not bound) under the package dir before and after), --properties-file (saved `systemctl --user show U -p ...` + a Hostname= line) or live `systemctl --user show U`; refuses unless LoadState=loaded, Id=U, terminal (failed/inactive or active+exited), Result=success (oom-kill, timeout, signal named), ExecMainCode exited with ExecMainStatus 0, NRestarts 0, plus every receipt slurm seal/field/redaction check (optional stdout invocation-id = InvocationID); job_id systemd-user:<U>:<InvocationID>@<host label> (label h-<12 hex of sha256(lower-cased host name)>; the name itself is never written); also writes <c12>-<slug>-unit.properties (additive unit_properties_sha256); built-in redaction <repo>, <HOME>, /run/user/<UID>/; --run interrupted (SIGINT/SIGTERM/SIGHUP) while waiting: the unit is not stopped, its temporary LAKE_CACHE_DIR is kept while the unit may still run (removed when it is gone or finished), exit 128+N, and the recovery command `lean-pkg receipt systemd --unit U --log ... --commit ...` (no --run) is printed with a WARN that recovery skips the source-unchanged-during-the-run check
comparator <name> --receipt PATH   attach comparator evidence to a verified-private/public package;
  no status change; event comparator-attached; identical re-attach = no-op; another receipt for the same commit: --replace keeps the old one as <c12>-comparator.replaced-<stamp>.json, event comparator-replaced {replaced}. It is required by promote.
  The receipt is written by comparator-receipt on a host its policy allows (a Slurm job, a host in
  COMPARATOR_RECEIPT_ALLOWED_HOSTS, or COMPARATOR_RECEIPT_ALLOW_HOST=1). Stages checkout, cache-get, build, comparator, receipt log to <evidence-dir>/comparator-stages/NN-<stage>.log + stages.json (kept on failure; the error names the stage and log; --evidence-dir DIR, default .lake/comparator-receipt); readiness before any expensive stage: a .lake/.lake/packages(/*) symlink resolving outside the project is refused (the lake comparator sandbox cannot read it), and the checkout stage ends with `lake --no-build build M` per configured module (unknown target: fails naming the missing [[lean_lib]]); a judge that stops before judging (sandboxed dependency resolution) is a tool/environment error, not a rejection; --prepare runs only the setup stages (idempotent); a later run at the same commit/toolchain/config hash skips them (--from-prepared DIR: reuse DIR's, refuse a mismatch); receipt gains an additive stages[] summary; a run replacing an earlier run's stage files first keeps them read-only as comparator-stages/attempts/NNN/ + attempt.json {n, verdict, failed_stage, started_at, finished_at, olean_manifest_sha256, files sha256} (numbers never reused, never deleted or read back: resume/setup reuse/receipts use the current run only; prune by hand), `comparator-receipt --attempts [DIR] [--json]` lists them (exit 1 if a kept file changed). A receipt written in a Slurm job claims it only through SLURM_JOB_ID (job_id J, host_policy "Slurm job J on H"), which any shell can set: after the job finished, `comparator-receipt --seal-sacct R [--sacct-file F] [--user NAME] [--json]` (no host policy) corroborates it with sacct (main line J COMPLETED, 0:0; Start..End, with zone, containing generated_at; User = this account with live sacct or --user; NodeList = the receipt's host: its h- label against the node's label (or its first DNS label's), an older receipt's host name against the node name; an operator --host-label or a node range is not compared), refuses otherwise (exit 1, nothing written), and writes .lean-receipts/<c12>-comparator-sacct.txt (JobID|State|ExitCode|Start|End|Elapsed, UTC; no user or node names) plus the additive slurm_sacct {schema slurm-sacct-seal/1, job, file, sha256, state, exit_code, start, end, elapsed, checks, source} into R; only a sealed comparator receipt (seal committed at HEAD, rehashed and reparsed) backs a coverage.yaml compute job.
  flags: --receipt PATH (required), --check (validate only, record nothing; works at any status; stderr `WARN: palomar: ...` when `palomar check` finds error gaps at the receipt's commit, JSON palomar_check as in publish, never a refusal), --check --repo DIR (validate against the checkout DIR, e.g. a worktree or feature branch, not the registered local_path; same rules as gate --check --repo: never saved, stderr note, JSON repo_override {path, registered_path, remote_match}, null without it; attaching refuses --repo, exit 2), --refuse-private, --refuse-dirty
  ex: lean-pkg comparator my-lean --receipt .lean-receipts/<c12>-comparator.json   |   comparator config --from-claims NAME [--out comparator.json] [--dry-run]: one TODO theorem_names entry per synced theorem/lemma claim not yet configured (name <Pkg>Audit.<decl>; the TODO markers go to lean-pkg-signoff.json configs[<config>].scaffold.todo, never into the config; a config still carrying the deprecated inline lean_pkg_scaffold is refused until migrated); never overwrites; counts existing/added/skipped; --challenge-from-type pre-fills Challenge (elaborated type) + Solution (`exact @_root_.<decl>`) via `lake env lean` [--lake-env DIR --jobs N --timeout S], or split it: --emit-script F (bash F OUT.json on a Slurm node, JSON comparator-types/1; ENFILE/EMFILE: halves jobs, retries) then --from-output OUT.json (commit must be HEAD; driver exits 1 on extraction errors, 2 if none filled; ingest refuses those unless --allow-partial: failures kept TODO, extraction_failed; --checkout DIR instead of local_path; --claims-file PATH: claims from that working-copy claims.yaml instead of the synced registry cache (no claims sync; schema-checked like claims check --file, package must be NAME; never written to the registry; same config as the registry route for the same claims; output line `claims source:` and JSON claims_source {kind registry|file, path, sha256, synced_commit, claims}); --no-registry --out FILE: scratch config, no lease check); per entry: type_error keeps the TODO, needs_restatement flags project vocabulary; each printed type re-elaborated vs the decl (example : (T) := @decl), failure keeps TODO, round_trip_failed   |   comparator config --coverage NAME: read-only; claims covered/not/TODO by the config tracked at the gate-bound commit (never a candidate); a changed or uncommitted config cannot certify that tree, a new gate at a commit tracking it is required; prints the exact next commands (JSON next_commands[])   |   comparator config --migrate-signoff PATH [--write]: no registry; moves the deprecated inline judged_as (-> configs[<config>].definitions, one {reviewer, date, judged_as} per hole, config_sha256 bound to the migrated bytes) and lean_pkg_scaffold (-> configs[<config>].scaffold) of that configuration into lean-pkg-signoff.json at the package root; dry run by default (prints both would-be files); refuses an invalid inline sign-off or a conflicting sidecar entry; the Palomar keys and their values never change, but the file's bytes do: a gated package needs a new comparator receipt (receipt impact + next commands printed; JSON {moved[], dropped[], config_sha256_before/after, receipt_impact, next_commands[]})   |   comparator host-check: read-only, no registry: kernel, landlock ABI, bwrap, go/cargo/jq/git/lean/lake/python3 ok|warn|fail, then `mechanism toolchain|legacy: ok|fail` (toolchain = bwrap+lean+lake+python3, what Palomar runs; legacy = Landlock ABI>=1 + go/cargo/git/jq/lean/lake) + verdict naming only usable ones (exit 0 can / 1 cannot); warn git-env (JSON env_warnings[]) when GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_OBJECT_DIRECTORY/GIT_ALTERNATE_OBJECT_DIRECTORIES/GIT_COMMON_DIR/GIT_NAMESPACE is set (comparator-receipt and package-checkout git drop them; stages.json git_env_scrubbed[]); --routes (field report #5; run it before sbatch inside the job's image with the job's binds and PATH): per route judge (`lake comparator` in bwrap: the toolchain mechanism's needs; no unsandboxed route ever) / prepare (comparator-receipt --prepare: kernel+lake/lean/git/python3) / transfer (build here, judge elsewhere: prepare + tar/sha256sum) `route R: yes|no (reason)` + `run:` lines, executables resolved like type -P (the receipt slurm --template HOST_TOOLS list included; time: binary only), locale (LANG/LC_ALL/LC_CTYPE, C.UTF-8 installed, warn when a set locale is not installed: stderr warning; export LC_ALL=C LANG=C PYTHONUTF8=1), limits (ulimit -n soft/hard, /proc/sys/fs/file-nr; tight: LEAN_NUM_THREADS=3), resources (sched_getaffinity CPUs, cgroup v1/v2 memory limit, MemAvailable, SLURM_* allocation), advice[]; JSON additive routes_schema host-routes/1, routes[{route, supported, needs, failing, reason, commands, notes}], executables{name: {path, level, required_by[], note?}}, locale, limits, resources, advice[]; read-only, no network; exit 0 when any route is supported
promote   dry-run default; --yes flips GitHub visibility to public (ONE-WAY) + one notification
  (via LEAN_PKG_NOTIFY_CMD when set). Needs an authenticated `gh` (--yes: a failed gh query is a failure). Refuses without a byte-
  valid comparator receipt bound to HEAD, a claims ledger that is not ready (`claims ledger NAME`: every reusable claim verified, theorems/lemmas by the comparator too; no claims = unknown = refused; JSON claims_ledger {package_status, counts, required, reasons, ledger_digest}), both receipts + comparator logs committed unmodified at HEAD, HEAD in origin/<branch> (local refs; --yes first runs git fetch --prune --tags origin incl. +refs/pull/*/head:refs/remotes/origin/pr/*, 120 s; a failed fetch is a ✗), (paper field) a paper unpublished with no arXiv id, and blocking public-safety hits (the whole repository when the package is a subdirectory of it; warn-only hits: a WARN line; skip: --skip-public-safety, a WARN). Evidence on disk that git ignores (receipts, comparator logs, comparator-stages/ files) is a WARN naming each path + rule (.gitignore:3:*.log) and the remedy (!.lean-receipts/ + !**/.lean-receipts/** after it, or git add -f); a not-committed ✗ names the rule; JSON ignored_evidence[{path, rule}] (also publish, and gate/comparator after attaching: stderr WARN).
  ex: lean-pkg promote my-lean          # always read the dry-run checklist first   |   publish NAME --dry-run (read-only; dry-run only): promote checklist (claims ledger included; JSON claims_ledger) + consent, license_spdx vs LICENSE, formalization.yaml current+valid, claims synced, the README page block (`page --check`; JSON page {status, adopted, problems[]}; not adopted: WARN, an unbacked Easley sentence: ✗), lakefile package name, tag at HEAD, a WARN item `palomar: not submittable ...` when `palomar check` finds error gaps at HEAD (JSON palomar_check {ready, commit, errors, warns, codes[], command}; never a ✗) (--public-safety: + the scan below, implies --dry-run); prints the next promote/tag/record commands (the tag push names the GitHub remote, found by URL); exit 0 ready / 1 not   |   public-safety NAME [--history|--no-history] [--strict] [--package-only] [--quiet] [--gzip-limit BYTES] (read-only; --quiet: summary + verdict, no hit lines): tree git would ship + (default) every blob/commit message/author+committer identity/annotated tag/branch+tag name on any ref; the whole repository when the package dir is a subdirectory (promote publishes it all; --package-only: the package dir alone); symlinks by target string; UTF-16/32 decoded; long lines in overlapping chunks; binary via printable runs; up to 64 MB per file, the rest listed in skipped[] and a blocking `unscanned` hit unless `allow: GLOB: ^unscanned$`; gzip content (bytes 1f 8b, any name; tree and history) stream-decoded up to --gzip-limit (env LEAN_PKG_GZIP_LIMIT, default 512M, K/M/G; 0 = off) and the DECODED text scanned instead of the compressed bytes (hit encoding gzip-decoded, location `path (decoded):LINE`; header file name/comment: gzip-header; decoded[] lists each; a binary decode such as a tar is scanned by printable runs, skipped reason `binary (gzip-decoded)`, not blocking); corrupt/truncated/over the limit/over the 500:1 ratio guard: hits of the decoded part kept, compressed bytes scanned as before, skipped[] `gzip ...` reason, a blocking `unscanned` hit unless allowed; for .lean-pkg-private-patterns regexes (# comments; bare or `block: RE` blocks, `warn: RE` warns, `allow: RE` exempts; `TAG: GLOB: RE` scopes any of the three to files whose package-relative path matches GLOB (* ** ?; no / = file name at any depth; trailing / = under that dir; never commit messages); tracked = a hit), a committed .lean-pkg-public-allow (allow: lines only, public on purpose; any other line ignored with a warning; inside it every allow line applies whatever its scope, and a line a private block pattern or registry-derived name matches is the blocking hit public-allow-private, tree and history; patterns_source public_allow_file, public_allow), built-ins (/home/<u>/, /Users/<u>/, ~/Dropbox/, approvals/, notes/, *.private, TODO-private, emails not declared: ordinary commit/tag addresses, consent[] or allow: lines; a host-like commit/tag address, user@hostname or a local TLD such as .lan or .invalid, is not declared and is an identity-email hit; a fixture address under .invalid in a file needs an allow line), names from the record's local_path/agent/workspace and its lease's agent/workspace/contact (an allow line exempts one only where its match in the token's non-space run covers it), internal paths (approvals/ notes/ drafts/ scratch/ *.priv), warn-only latex-label (lem:/thm:/eq:/sec:... keys, main.tex, \\ref{ \\cite{ \\label{, Overleaf, arXiv ids; Lean-calibrated: never inside a name like foo_eq_bar, eq/def/ex only with `:`, key- only with a digit/capital/inner `.`, built-in allow def-eq defeq eq-of eq-iff ex-falso; the summary counts its warnings); hit = location (path:line, history: commit + path) + pattern label + severity (block|warn; warn printed as WARN) + excerpt with each match as […] (raw strings never printed); --json {ok, hits[], tree_hits, history_hits, block_hits, warn_hits, latex_label_hits, strict, scope, package_prefix, skipped[], skipped_blocking, decoded[] {where, path, compressed_bytes, decoded_bytes, content, complete, commit}, gzip_limit, patterns_source, warnings[]}; exit 1 on blocking hits (--strict: on any hit)   |   reservoir check NAME [--offline] (read-only): public, not a fork, >=2 stars (gh), lake-manifest.json + lean-toolchain tracked, LICENSE vs license_spdx, lakefile package + no `reservoir := false` (+ WARN without description/keywords/versionTags), lean-toolchain a release (rc/nightly: WARN), tag at HEAD; not yet public: GitHub items `pending promote: ...` (still ✗) + a `pre-promote:` line; then the Reservoir API (200: prints the `record ... reservoir-listed` command; never records itself); exit 0 eligible / 1 not   |   palomar prepare NAME [--out DIR] [--dry-run] (no network; submits nothing): bundle at <checkout>/.lean-receipts/palomar-<HEAD12>/ (never overwritten): formalization.yaml (rendered, schema-valid), configs/<every comparator config at HEAD, and the headline configuration (README.txt names it for the main-results entry; JSON headline_config)>, receipts/<gate + comparator receipts, validated against HEAD>, README.txt (manual steps + Palomar URL + the record palomar-submitted command), MANIFEST.sha256; refused without receipts bound to HEAD, without consent, on any error gap `palomar check` finds at HEAD (each listed as `✗  CODE: detail` + fix; nothing written, --dry-run too; its warn gaps are `lean-pkg: warning: palomar check: ...` lines, never a refusal; JSON palomar_check {ready, commit, errors, warns, codes[], command}), on blocking public-safety hits in the bundle; event palomar-prepared {commit, bundle, bundle_sha256}   |   palomar check NAME [--rev REV] [--config PATH] [--rules FILE] [--repo DIR] (read-only, offline; reads git objects at the commit: --rev, else the gate-bound commit, else HEAD): gaps to Palomar's mechanical rules, pinned at PalomarSubmission d4e41c1d5b0d (read 2026-10-16; --rules FILE: a newer browser-preflight-policy.json): every tracked .lean file of the repository starts with `module` (comments may precede; lakefile.lean exempt; .git/.lake excluded; <= 10,000 lines; no symlinks), lean-toolchain leanprover/lean4:vX.Y.Z[-rcN] >= v4.35.0-rc2, lakefile, no submodules, each comparator config (or --config PATH): required keys, no keys outside challenge_module/solution_module/theorem_names/definition_names/permitted_axioms/enable_nanoda (judged_as and lean_pkg_scaffold are refused), standard axioms, module sources tracked, Challenge <= 1,000 lines and 100 KiB, definition holes (warn); the headline configuration (registry headline_config, else comparator.headline.json): the same config checks, comparator.headline_uncovered (error: names not covered or holes unsigned by the gate-bound comparator receipt), comparator.headline_unjudged (warn: no receipt validates at HEAD), comparator.headline_missing/_invalid (error), JSON headline {config, source, judged, covered} or null; formalization.yaml tracked, v0.4-valid, no placeholders, Palomar's metadata minimum; one root licence file matching license_spdx; GitHub repo; visibility public and commit pushed (warn); gap {code, severity error|warn, paths[], detail, fix}; verdict first and last; JSON {ok, ready, commit, commit_source gate|head|rev, project_path, lean_toolchain, configs[], lean_files, missing_module, counts{error, warn}, gaps[], rules{}, not_checked[], repo_override}; exit 0 no error gap / 1 error gaps / 2 usage   |   palomar port-modules NAME [--dry-run (default) | --write] [--agent A] (reads git objects at HEAD; never commits, never the registry): the mechanical port of the package's tracked .lean files to the module system: `module` first (after ordinary comments; before /-! -/ and /-- -/), `import X` -> `public import X` (`meta import` -> `public meta import`; `import all`/private/public kept; never `public import all`), body in `public section` ... `end` (no `end` when a scope stays open); module-headed files skipped (a re-run is a no-op); refused per file: prelude, non-UTF-8, symlink; review items (never auto-fixed, lines of the ported file): expose (a def another file unfolds/simp/rw/rfl/decide: add @[expose]), private, meta (#eval/native_decide/initialize), imports-non-module, scope-balance; .lake/, lake-packages/, nested Lake projects, path deps excluded, lakefile.lean exempt; dry run: per-file report + unified diff; --write: clean working tree only (dirty/untracked, a refused file, or a foreign live lease without --agent: exit 1, nothing written), then follow-ups (lake build, @[expose] review, palomar check after the commit, a new gate for a gated package); JSON {ok, name, dry_run, written[], commit, project_path, files[{path, action port|skip|refused, reason, edits[], reviews[{code, line, detail}]}], counts{port, skip, refused, review}, expose[], excluded[], vendored_dirs[], outside_package, diff, follow_ups[]}; exit 0 / 1 a refused file
audit     read-only without --fix (expired leases reported); --fix: sweep leases, gh -> registry visibility; toolchain/formalization.yaml drift; receipt/comparator drift judged
  at the reference branch tip (origin/HEAD, else main, else master; local refs; a checkout elsewhere: checkout-branch finding).
  --toolchain-floor V (env LEAN_PKG_TOOLCHAIN_FLOOR; default leanprover/lean4:v4.35.0-rc2): below-toolchain-floor, rc < release,
  nightly -> toolchain-unparsed, scratch/archived exempt; floor is warn only for public/comparator-evidence packages or --promotion, else level info; --json findings carry level warn|info; repo not on GitHub -> no-github-mirror (human: one line); informational: receipt-untracked (receipt file not tracked at the tip), duplicate-path (two packages, one checkout), duplicate-namespace (two checkouts, one Lean root namespace), consent-duplicate (one who+role recorded twice; shown once), claims-coverage-none (claims coverage none|empty|unsynced: search falls back to declarations; --json coverage; archived/upstreamed skipped); a failed gh query: check gh-OUTCOME, gh = gh-missing|not-authenticated|no-access|not-found|error, detail names the remedy (archived + not-found: info); human line `LEVEL  CHECK  NAME: DETAIL`, the line "N finding(s): W warn, I info" first and last (--quiet: it alone); --json counts {warn, info}; --no-gh: gh-skipped (info); missing-path: recorded path + `update NAME --path DIR --reason .. --dry-run` (+ "found at DIR": a sibling with lakefile + same remote); palomar-not-ready (warn; only packages aiming at Palomar: upstream_target palomar or a palomar entry, not scratch/archived/upstreamed): `palomar check`'s error gaps at its commit (gate-bound, else HEAD), detail = count + codes + `lean-pkg palomar check NAME`, --json palomar {errors, codes[], commit, command}; warn gaps alone: no finding. ex: lean-pkg audit --json
validate  schema + enum + history sanity.              ex: lean-pkg validate
next      NAME [--agent A] [--repo DIR] read-only, offline (never gh, never writes): the ONE recommended next command + a one-line reason, from the record + checkout (claim / update --status / gate --check / gate / gate --regate (verified-private, HEAD moved) / comparator-receipt / comparator config --from-claims / comparator / publish --dry-run (only when the claims ledger is ready; else its first fix or `claims ledger NAME`) / reservoir check / palomar prepare / record ...); without --agent a live lease is only noted; a checkout off its default branch (or detached) is noted, HEAD still judged; --repo DIR evaluates the checkout DIR instead of the registered local_path for this call (never saved; stderr note; JSON repo_override {path, registered_path, remote_match}, null without it; DIR a git work tree whose remotes, if any, include the package's repo (Host:/p matches a local-path repo /p; the registered local_path itself needs no remote); carried into a recommended gate --check, any other recommended command that reads the checkout gets a note that it uses the registered path); a comparator step (comparator-receipt, comparator config) whose lean-toolchain at HEAD is below audit's comparator floor (--toolchain-floor default / LEAN_PKG_TOOLCHAIN_FLOOR) is blocked first: command null, blocker below-toolchain-floor {lean_toolchain, floor, deferred_command}, reason says bump the toolchain, commit, re-gate; exit 0 command (or nothing left: command null), 1 blocked; --json {ok, name, command, reason, blockers[{kind: foreign-lease|invalid-lease|missing-checkout|no-commit|dirty-tree|invalid-receipt|unreadable-receipt|below-toolchain-floor, detail}], notes[], repo_override, state{status, lease, gate_commit, comparator_commit, head, dirty[], receipt_candidate, claims_ledger, ...}}. ex: lean-pkg next my-lean --agent vera --json
doctor    ok/warn/n/a: version, install, registry [source: flag|env|user-default|cwd], user-default, events [source: flag|registry-sibling|env|sibling], registry-repo (behind/ahead of origin or no origin remote, dirty registry files: warn = do not write yet; --fetch first), index, host (comparator host-check verdict; warn = cannot produce comparator receipts or a leaked GIT_* repository variable), tools (n/a = optional; the detail says what it is for and when it is needed; tool comparator without bwrap: run `comparator host-check`), gh auth (account; warn = not logged in, promote needs it), python; exit 1 if the registry is unreadable or its schema version is unsupported (every other command refuses a newer version: upgrade lean-pkg).
llms      this contract (--json: {"ok": true, "text": ...}); --short: ~40 lines cut from it by section (title, PURPOSE head, STANDING RULES, the first line of each subcommand; --json adds "short": true; the rest, e.g. publish, public-safety, reservoir check, palomar prepare, comparator config/host-check, release --fresh-repo/--adopt-repo: the full text). ex: lean-pkg llms --short   |   help [TOPIC|COMMAND ...]  gh-style help topics, no registry read: exit-codes (0/1/2, what 1 means per command, comparator-receipt/lean-exercise codes), environment (every variable lean-pkg, comparator-receipt and lean-exercise read, with its default; --json variables[{name, section, meaning, default}]), formatting (--json, stdout vs stderr, named schemas, jq); no argument: commands + topics; COMMAND: exactly its --help; unknown: exit 2. ex: lean-pkg help environment
wiki      render a Markdown status page.               ex: lean-pkg wiki --out status/index.md   |   report (read-only): a bug report in the shape of .github/ISSUE_TEMPLATE/bug_report.md: version + doctor ok/warn lines + host-check verdict, --command "ARGS" re-run in a subprocess (+ --json when the subcommand has it; a writing command is not re-run), --body TEXT (no re-run) and/or --from-log FILE[:N] (last N lines, default 200, as Log excerpt) first in What happened (one of --command/--body/--from-log required, else exit 2), --package NAME record (status, lease, receipt commits); every captured text redacted (registry/checkout paths -> <registry>/<registry-dir>/<checkout>, home -> ~, public-safety built-ins + .lean-pkg-private-patterns of the checkout and registry dir + record-derived names -> […]); a blocking hit left: exit 1, nothing filed. Dry run unless --yes: gh issue create -R lean-pkg/lean-pkg (--repo OWNER/NAME) --label bug|enhancement (--enhancement), prints the URL; no gh or gh not logged in: exit 1 (gh auth login). --json {ok, filed, url, title, labels, body, redactions, log_excerpt {path, lines} with --from-log}. ex: lean-pkg report --command "status my-lean" --package my-lean
index     public catalog <index>/<name>/entry.yaml + receipts/ (root: --index, env LEAN_PKG_INDEX, index/ beside the registry if present, ./index; check/render/migrate exit 1 when it is missing).
  add NAME --by HANDLE --headline 'Name :: File.lean :: One sentence.[ :: ChallengeTheorem]' [--from-registry] [--repo URL]
      [--repo-dir P] [--lower REL] [--comparator REL] [--license SPDX] [--tag T] [--field-tag T] [--consent-recorded]
      [--allow-unpushed]  (default: the pin and evidence must be on a branch of the public repo; consent must be recorded)
      validates the receipts at the pin (their commit) in a clone; trust computed; receipts copied verbatim;
      duplicate repo+commit refused; never writes local_path; event index-added (event log only).
  check [NAME ...] [--clone | --repo-dir P]   static without a source; else re-derive from bytes at the pin.
      Palomar/Reservoir verified by API when reachable, else "skipped" (never failed, never passed). exit 0/1/2.
  render [--out PATH] [--check]   Markdown table, default <index>/README.md; --check exits 1 when stale.
  migrate [--check]   rewrite index-entry/1 entries as index-entry/2 (claims derived from the headline).
claims    claims.yaml (schema claims/1) at the package repo root: named reusable results, cached in the registry.
  sync NAME   read claims.yaml at HEAD (committed bytes only); every decl must occur in its file; event claims-synced; a pin on no remote-tracking branch (unpushed) prints a stderr WARN (--json pin_published true|false|null).
  mark NAME --from-comparator CONFIG.json --status reusable|internal|experimental [--write|--dry-run] [--agent A]: set status on exactly the claims whose decl the config's theorem_names cover (the --coverage/ledger rule: full name or <Prefix>.<decl> of a fully qualified decl), in the checkout's working copy; default prints the plan; --write edits only those status values (else re-dumps, reformatted true), never commits, never the registry; lists unmatched names, then the rule and a hint per name that differs from exactly one claim's decl only in its leading namespace component (never applied); --json {ok, name, file, config, status, matched[{id, decl, from, to, changed}], changed, unmatched[], written, reformatted, rule, hints[{name, decl, shape, rename_to, hint}]}. sync and check print `WARN NAME: gated, N claim(s), none with status reusable: ...` (--json warnings[]) for a gated package with no reusable claim.   |   list [NAME] | check [NAME] [--file PATH]   cached claims with status and derived trust | re-validate; exit 0/1 (--file: schema-check a working-copy file, schema only: no checkout, so declarations unchecked; no registry; --json {ok, path, claims, errors[]}). With NAME both report coverage synced|empty|unsynced|none (--json: coverage, cached_claims, claims_yaml present|absent|unknown, legacy_claims[], hint); check NAME exits 1 on none/unsynced (no coverage is not ok) and names any legacy *claims.json in the checkout   |   migrate NAME --from PATH [--out claims.yaml] [--dry-run] [--agent A]: legacy JSON rows -> a claims/1 skeleton in the checkout (mapped fields; TODO markers, TODO.lean for the rest, which sync refuses); never overwrites, never writes the registry, grants nothing: commit it, then claims sync.   |   ledger NAME [--stage gate|promote]: read-only, nothing stored: each claim (claims.yaml at the gate-bound commit, else the cache) with named diagnostics from the recorded receipts, re-checked with gate's and promote's validators; a claim is verified only with zero diagnostics. Claim status: out-of-scope (internal/superseded) | blocked (a depends_on claim not verified) | unknown (unreadable evidence) | verified | open. Required = status reusable; --stage promote (default) also needs the comparator tier for reusable theorems/lemmas. Package status fails closed: unknown (no claims, none reusable, no checkout, unreadable evidence) | failed (evidence contradicts a required claim) | open | ready. Codes: docs/lifecycle.md. Exit 0 only when ready, else 1; --json {ok, package_status, reasons[], stage, bound_commit, receipt_sha256, comparator_sha256, counts, claims[{id, decl, kind, claim_status, required, status, statement_basis, manuscript_findings[], diagnostics[{code, detail}]}], manuscript_findings {total, open, fixed, by_kind, corrected_statements[], findings[], source}, ledger_digest} (sha256 over the canonical claims, codes and receipt hashes: recompute, never trust a stored one). Manuscript findings change no status (publish --dry-run and palomar prepare print the same one-line summary, JSON manuscript_findings); a lean_evidence decl missing at the bound commit is finding-evidence-missing.   |   rebase NAME --from-tex OLD.tex --to-tex NEW.tex --to-aux NEW.aux [--from-aux OLD.aux] [--from-sha256 HEX] [--to-version arXiv:IDvN | --to-commit HEX [--to-repo URL]] [--unwrap-macro NAME ...] [--claims-file PATH] [--coverage-file PATH] [--out-dir DIR] (#6): move claims.yaml + coverage.yaml (committed at HEAD) to a new manuscript version; never writes the registry or the checkout, never changes a claim status, never sets fixed_in. Refused: OLD.tex's sha256 differs from coverage.yaml's manuscript sha256, or is none of the .tex hashes of the data/source-baselines record of its arXiv version (none recorded: --from-sha256 must state it; the proposal drops eprint_sha256), TeX not UTF-8 or with unbalanced begin/end, a referenced label defined twice (OLD, NEW or NEW.aux). References: coverage rows (paper.label, else paper.number via --from-aux), manuscript_findings location.label (a TeX label, else a number via --from-aux), TeX labels named in a citation. Per label, the enclosing statement (theorem-like incl. \\newtheorem names) or equation environment in OLD vs NEW, normalized (comments, label commands, \\textcolor/\\colorbox/\\color, --unwrap-macro, ~, whitespace, trailing . , ; :; braces kept): unchanged (carried; number token re-keyed from NEW.aux, finding page from NEW.aux, tex_line shifted only when the environment is byte-identical, else null) | changed | missing | moved-into-proof | not-in-old | unsupported | unnumbered (no NEW.aux number); all but unchanged need review and are left as they were. --out-dir (new or empty; files read-only): claims.yaml, coverage.yaml (manuscript.version = --to-version or {repo, commit, sha256 of NEW.tex}; required when coverage.yaml exists), rebase-report.json (schema claims-rebase/1), rebase-report.md (old/new excerpts), review/NNN-LABEL.{old.tex,new.tex,diff}, MANIFEST.sha256; deterministic. Without --out-dir: report only. --json {ok, name, written, out_dir, counts, labels[], rows[], findings[], citations[], needs_review[{where, label, status, reason}], proposed_errors[], statuses_changed, normalizations[], inputs, to_version, warnings[], files[{path, sha256}]}; exit 0 everything carried / 1 something needs review or refused / 2 usage. docs/prior-art.md#move-to-a-new-manuscript-version
formalization NAME [--write] [--no-registry] [--out PATH] [--force [--reason TEXT]] [--author A ...] [--headline-config PATH] [--agent A]   render formalization.yaml (mathlib-initiative
  v0.4, what Palomar reads) from the registry + the repo at HEAD. Derived every run: project.name/license (license_spdx),
  automation.notes, status.sorry_count/axioms/evidence (gate receipt), status.main_results (every tracked comparator config:
  comparator_config, declaration, file; definition holes carry judged_as, from lean-pkg-signoff.json or the deprecated inline key, null when unsigned; with a headline configuration (--headline-config PATH, else registry headline_config, else comparator.headline.json when tracked; Palomar's six keys only, else refused) its theorems only, when every name is covered (the --coverage rule) by a config with the same challenge/solution modules in the gate-bound comparator receipt validated at HEAD and every hole is signed in lean-pkg-signoff.json by such a config, else refused naming them; no receipt validating at HEAD: not applied, stderr warning, the default; --write records --headline-config; JSON headline {config, source, applied, reason, receipt, theorems} or null), alignment (claims.yaml). Every other
  key is KEPT from the file; first render seeds authors from consent[] owner/author, else "[PLACEHOLDER] ..." + stderr warning (never
  invented: no paper titles). --write: idempotent; lease + update checked BEFORE writing (foreign lease: --force --reason); a differing file needs --force;
  --write also sets the registry pointer (a registry write); --write --no-registry: the file only, registry untouched (no lock/--commit). Validated against the vendored v0.4 schema (jsonschema, else structural). audit: formalization-missing/-drift. docs/formalization.md   |   coverage check NAME [--file PATH] [--allow-private-manuscript]   read-only; exit 0/1. coverage.yaml (schema coverage/1, package dir, committed at HEAD; --file: a working copy) is the paper-centric map: manuscript {title, version: "arXiv:IDvN" | {repo https, commit 40hex, sha256 64hex} | "unposted" (refused without --allow-private-manuscript)}, summary (one line), rows[{paper {number, title, label?}, class same-assumptions|inputs-supplied|outside, claims[] (ids in claims.yaml at HEAD; none for outside; superseded refused), inputs[] (required for inputs-supplied, refused for same-assumptions), note?}], nothing_excluded {reason} (only when no outside row; one of the two is required), compute[{cluster: easley, jobs: [slurm-J]}] (each job a Slurm receipt committed under .lean-receipts/), lean_pkg_version {version, doi} (checked against lean-pkg's own CITATION.cff when readable). The lab's lean-scope-page rules that apply: no placeholder text (FILL-ME, TODO, TBD, FIXME, XXX), every named claim's file committed at HEAD, every theorem of the recorded comparator receipt the decl of a claim some row names, every configuration there with definition_names carrying judged_as.reviewer. Strict: exact key sets, closed enums, duplicate keys refused. --json {ok, name, file, rows, counts, manuscript, private_manuscript, errors[], warnings[]}. docs/page.md   |   page NAME [--write | --check | --out PATH] [--site DIR] [--html PATH] [--repo DIR] [--allow-private-manuscript] [--agent A]   the README verification section, rendered between <!-- lean-pkg:page:begin --> and <!-- lean-pkg:page:end --> in the package README.md from coverage.yaml, claims.yaml, the claims ledger (promote stage: a row is "verified" only when the ledger closes every claim it names), the receipts committed under .lean-receipts/ (allow-listed fields only: commit, toolchain, Mathlib rev, sorry count, axioms, theorem/permitted-axiom/definition names, date, SHA-256, slurm-J job ids; never hosts, paths, users), lean-toolchain, lake-manifest.json, CITATION.cff (authors required), registry repo/status/license_spdx/doi. Sections: status capsule, paper-Lean map, Not formalized, trust boundary, reproduce, evidence, citation, license, acknowledgments (always the lean-pkg credit with the concept DOI; the Easley sentence only when coverage.yaml compute names easley jobs backed by committed Slurm receipts). Deterministic; every rendered string passes the public-safety scanner + the package's .lean-pkg-private-patterns (a blocking hit refuses). Default: print the block. --write: replace only the marked block (append when absent; prose outside untouched; never commits; a foreign live lease refuses without --agent HOLDER); not a registry write (no lock/--commit). --check: exit 1 when README.md is missing, the block is missing or stale, the lean-pkg credit is missing, or the Easley sentence appears without backing; writes nothing. --site DIR: also write DIR/verification.md (the full page, one H1) and DIR/coverage.md (every row: claims, decls, each claim's ledger status, inputs, note) for MkDocs; --html PATH: one static HTML file of both (no script, no external asset, inline CSS, every text escaped, links only https/#anchor); the same bytes whatever DIR; atomic writes, symlink targets refused; with --check they are compared instead (missing/stale: exit 1). User text is escaped in every output (&<> as entities, Markdown markup backslash-escaped). --repo DIR: read that checkout instead of local_path for this call (CI; not with --write). publish --dry-run carries the same check as one checklist line (JSON `page`). --json {ok, name, readme, markdown, written, unchanged, current, problems[], warnings[], site_files[{path, status written|unchanged|current|missing|stale|refused}], repo_override, rows, counts, verified_rows, ledger_status, ledger_digest, private_manuscript, manuscript, easley {backed, jobs}, receipts}. docs/page.md   |   formalization anchor NAME --arxiv <id>v<N> (--from-file E-PRINT | --fetch) [--compare-tex LOCAL.tex [--accept-diff REASON]] [--main-tex PATH] [--write] [--agent A]   pin the companion to one arXiv version: the version is required (unversioned: exit 2); network only with --fetch (https, 60 s, 64 MiB); the e-print (tar.gz|gz|tar; PDF-only refused; absolute/../link/device members refuse it) is read in memory and recorded in data/source-baselines/arXiv-<id>v<N>.json (source-baseline/1, deterministic: e-print sha256, .tex/.bib/.bbl/.sty sha256, main_tex, compare, retrieved_at/arxiv_api only when fetched); without --write a dry run; --write refuses unless tied (coverage.yaml already that version, a bound {sha256} equal to an e-print file, or --compare-tex identical / --accept-diff REASON, recorded), then sets coverage.yaml manuscript.version + eprint_sha256 and the formalization.yaml sources[].id; never the registry or a commit; a move from another version prints rebase_command (claims rebase).
search    TERM [--package NAME] [--offline] [--sources a,b,c] [--loogle] [--loogle-query PATTERN] [--require-complete] [--cache-dir DIR] [--record [--repo-dir P]] [--json]   prior-art search. Routed by shape: a Lean pattern (→ -> ∀ fun : ( ? _ ⊢ " math operators, Nat.x dotted names) is `signature` and goes to Loogle; prose is `natural-language` and Loogle is skipped ("natural-language query; Loogle takes declaration patterns ..."); --loogle forces it; --json: top-level and per-source route. Loogle parses Lean, not prose: a term mixing the two fails there ("query not understood: Unknown identifier ...; Loogle takes Lean names, terms and patterns ... --loogle-query"); keep the prose as TERM and pass the Lean part as --loogle-query PATTERN (Loogle gets PATTERN whatever the route, every other source the term; --json loogle_query; usage error when --sources leaves out loogle). --require-complete: exit 1 when complete is false (a source skipped, failed in part, capped or incomplete); the output is printed in full first, --json gets ok false + error "search incomplete (--require-complete): skipped: ...; <why>", stderr the same line; without it an incomplete search still exits 0. Cache: Palomar snapshots and the Reservoir index are cached under $XDG_CACHE_HOME/lean-pkg (default ~/.cache/lean-pkg) and refreshed by any online search, --record or not (the registry is never written without --record); each block's cache_writes[] and --json cache {root: --cache-dir|XDG_CACHE_HOME|default, writes[] (paths relative to the cache root)} say what was written, and the text adds "cache: wrote ... under ..."; --cache-dir DIR uses DIR as the lean-pkg cache for that search only (or set XDG_CACHE_HOME for a whole session).
  order: registry, index, loogle, leansearch, reservoir, palomar, github; each "ok" or "skipped: <reason>" (never zero hits).
  local score: exact alias or package name 3, keyword 2, every term word 1; registry hits: packages (name, notes, owner_project, repo; hit_kind package) and claims; a package without synced claims is searched by declaration in its checkout at HEAD (tracked *.lean names + docstrings; hit_kind "declaration (unclaimed)", trust none, file PATH:LINE, after claim hits), and a "registry coverage: N of M packages have no synced claims: D searched by declaration only, K not searched (no checkout: ...)" line precedes consulted: (--json coverage {claims, declaration_only, not_searched[], not_searched_reasons[] {package, code no-path|no-checkout|no-git-head|scan-error, remedy (read-only, no host path), detail (scan-error only, else null: "git grep exit N: <fixed cause>" or "git grep killed by signal N ...", never git's stderr), the coverage line names it: "scan error: NAME (git grep exit 128: ...)"}, declaration_hits_omitted}; coverage is computed per run from the registry file (read once, written atomically) and the checkouts at HEAD, with no shared cache, so two runs differ only when a checkout, its HEAD or a git grep outcome differs); any skipped source adds "incomplete: searched N of M sources; skipped: ..." before consulted: (--json complete, searched, skipped_sources[]); a source that answered only in part (GitHub code or repository search failed) stays ok with partial[] in its block, is listed in truncated_sources and named on the incomplete: line; a part that returned its result limit (GitHub 30) is listed in capped[] (cap_limit) and truncated_sources ("github: code search returned its 30-result limit"). exit 0 even with zero hits; 2 on usage errors.
  --record: docs/prior-art/<date>-<slug>.md in the package checkout (or --repo-dir; never the registry dir; never
  overwritten; "decision: pending" for a human) + event prior-art-searched; prints "note: <path> is new; a strict MkDocs build needs a nav entry (or a literate-nav glob) for it" when the repository has an mkdocs.yml (--json nav_hint {path, reason}, else null; mkdocs.yml is never edited). claim/index add print advisory notes on stderr (`notes` in --json).

RECEIPT SCHEMA (JSON object; `gate` enforces every line)
commit         40 lowercase hex; MUST equal HEAD, or HEAD must be an evidence-only descendant of it
               (only .lean-receipts/ paths differ between the two commits)
lean_toolchain string; MUST equal the lean-toolchain file committed at `commit`, stripped (the working tree never counts)
mathlib_rev    nonempty string (full mathlib rev)
sorry_count    integer; MUST be 0
axioms         list of strings; subset of gate.axioms_allowed (default exactly: propext, Quot.sound, Classical.choice)
report_sha256  64 hex chars; SHA-256 of the verification report
job_id         nonempty terminal executor job identity, unique within that executor (stored as <c12>-<job_id sanitised>.json)
checker        nonempty exact checker command/mode (e.g. serial leanchecker manifest)
generated_at   ISO-8601 timestamp; MUST parse

COMPARATOR RECEIPT SCHEMA ("comparator-receipt/2"; "comparator-receipt/1" still validates; written by comparator-receipt; `promote` re-derives every line from bytes)
schema          "comparator-receipt/2" (or "/1": legacy mechanism implied)
mechanism       "toolchain" (lake comparator + the toolchain's bundled NanoDa/con-ron; Lean v4.35.0-rc2+; what Palomar runs)
                or "legacy" (pinned comparator/lean4export/nanoda/landrun stack); comparator-receipt --mechanism auto|toolchain|legacy
commit          40 lowercase hex; MUST equal the lower-tier receipt commit, and equal HEAD or have HEAD as
                an evidence-only descendant (only .lean-receipts/ differs, plus the recorded formalization.yaml when only its derived keys changed)
lean_toolchain  MUST equal the lean-toolchain file committed at `commit`, stripped
tools           legacy: {comparator, lean4export, nanoda, landrun}: 40-hex commits actually used
                toolchain: {lean_toolchain, lake_sha256, leanexport_sha256, leanchecker_sha256, nanoda_bin_sha256, con-ron_sha256}:
                64-hex sha256 of the judging binaries under `lean --print-prefix`/bin
host, job_id, generated_at   nonempty (Slurm job id, or local-<host>-<pid>-<utc-stamp>); generated_at MUST parse as ISO-8601; host (and <host>) is the host's label, never its name: h- + 12 hex of the SHA-256 of the lower-cased host name, or --host-label (a receipt by lean-pkg 0.10.0 or earlier may name the host: still valid; public-safety reports them as receipt-raw-host)
host_label      optional, not validated: the label in host (always written since host labels)
host_policy     optional; the runner's host-policy reason (an override run is visible here); a "Slurm job J" claim backs coverage compute only with slurm_sacct, comparator-receipt --seal-sacct; stack_limit optional, not validated: {soft_kb, hard_kb, source} the lake/lean children ran under (COMPARATOR_RECEIPT_STACK=unlimited|<kB> raises it; WARN at <= 8 MiB); build_provenance optional, not validated: {compiled, modules_built, prebuilt, source} whether the build stage compiled any of the package's oleans or found them prebuilt (source: COMPARATOR_RECEIPT_BUILD_SOURCE, redacted); comparator --check and gate --check print it; lean_threads optional, not validated: N or null, the LEAN_NUM_THREADS given to the lake/lean children (COMPARATOR_RECEIPT_LEAN_THREADS=N); olean_manifest optional, not validated: {schema olean-manifest/1, files, sha256, lean_toolchain, lake_manifest_sha256, captured} the digest of the sorted `<path> <size> <sha256>` lines of the package's own compiled modules under .lake/build/lib (comparator-stages/03-build.oleans.sha256), recorded after setup and rechecked by the runner before and after the comparison (a difference fails the stage, nothing written; it also keys the slot cache); `comparator-receipt --verify-oleans [DIR] [--json]` is the read-only check (exit 0 same, 1 different/none recorded)
test_only, partial  if present and true (a test-only override; a --slots subset run) the receipt is REFUSED
result          "pass" (the runner writes a receipt only on PASS)
configs[]       MUST list exactly the comparator*.json / comparator/config*.json files tracked at HEAD
                (no omissions, no extras). Per config: path, sha256 of its HEAD bytes; challenge_module,
                solution_module, theorem_names[] (non-empty), definition_names[], permitted_axioms[]
                (subset of gate.axioms_allowed) — each MUST equal the config file at HEAD;
                non-empty definition_names MUST be signed off in lean-pkg-signoff.json at HEAD: configs[<path>] {config_sha256 (the config's bytes), definitions {<every hole>: {reviewer (human, never a model/agent token), date YYYY-MM-DD, judged_as text}}}
                (deprecated, warned: inline judged_as {reviewer, date, definitions ⊇ definition_names, basis?}) — comparator never compares hole bodies (runner before a run + every validator;
                the receipt's copy, signoff {source, definitions}, MUST equal the sidecar's);
                log MUST be .lean-receipts/<commit12>-comparator-<cfgname>.log (cfgname = path minus
                .json, '/' -> '_'); log_sha256; result "pass". The log must contain the full line
                "Your solution is okay!" (both mechanisms; `lake comparator` prints it last, after the
                per-kernel acceptance lines). A file already at the canonical .lean-receipts path that
                differs from the receipt being recorded is refused (no silent pointer drift).

INDEX ENTRY SCHEMA ("index-entry/2"; "index-entry/1" still validates; YAML keys in this order; unknown keys refused; no local paths)
schema name repo(https) pin{commit = the receipts' commit, tag|null} lean_toolchain mathlib_rev|null license_spdx
trust(lower-tier|comparator|palomar; computed, never typed) receipts{lower|null, comparator|null}
headline[{name, file, summary, judged_as?}] (comparator trust needs every headline judged_as in a config)
claims[{id = name/slug-of-decl, decl, kind theorem, summary, judged_as, trust}] (/2 only; derived from headline at add)
palomar[{id, version}] doi|null reservoir|null formalization_yaml|null fields[] consent{recorded} added{at, by} notes

CLAIMS FILE SCHEMA ("claims/1"; claims.yaml at the package repo root; unknown keys refused; no local paths)
schema package claims[{id = package/slug [a-z0-9][a-z0-9-]*, decl, kind definition|lemma|theorem|construction|library,
summary, aliases[]?, keywords[]?, file (.lean), status reusable|internal|experimental|superseded, superseded_by|null
(required iff superseded; acyclic), citation|null, depends_on[]?, statement_basis? null|as-printed|corrected, manuscript_findings[]? [{id (slug, unique in the package), kind misprint|proof-gap, location{label, tex_line?, page?}, summary, lean_evidence[Lean name (in the claim's file) | {decl, file}] (each must occur at the checked commit, like decl), fixed_in null|arXiv:<id>v<N>}] (exact keys; a misprint needs statement_basis corrected, corrected needs a misprint; findings never change a status)}]; trust is derived, never typed

EXECUTOR EVIDENCE
The gate is executor-neutral. The frozen report named by report_sha256 must bind the exact
candidate commit/tree, source archive, toolchain/dependency pins, complete checker coverage,
terminal state/exit, and executor-specific identity/resource evidence. Slurm reports bind
terminal scheduler accounting; hosted reports bind run/attempt/job identity, terminal
conclusion, immutable workflow/control revision, runner identity, and resources. A pilot,
partial manifest, pre-terminal marker, or prior-candidate receipt is not gate evidence.

EVENT LINE SCHEMA (one JSON object per line; package events also go to history[]; index-added, prior-art-searched, registry-* are log-only)
ts      UTC ISO-8601 with offset, second precision (all written times; dates are UTC; same-second order = file order; naive = legacy UTC, doctor warns)
event   one of: registry-created, registry-migrated, registered, updated, claimed, claim-refreshed, claim-forced,
        released, release-forced, lease-expired, gate-passed, regated, promoted, audit-fix, relocated, status-changed,
        comparator-attached, comparator-replaced, palomar-prepared, index-added, claims-synced, prior-art-searched, evidence-retracted, and the evidence events under `record`
name    package name or null
plus event-specific fields (agent, workspace, ttl_hours, reason, contact, commit, receipt, ...)

EXIT CODES
0  success, including dry-runs and the "already gated" no-op
1  refused or validation failed (stderr has the reason; with --json stdout also gets {"ok": false, "error": ...})
2  usage error (argparse, or an interactive prompt attempted off-tty; with --json a usage refusal, argparse's included, also prints {"ok": false, "error": ..., "usage": true}); what 1 means per command, and comparator-receipt's and lean-exercise's codes: lean-pkg help exit-codes
"""


# llms.txt (and its site copy docs/llms.txt) is this header plus LLMS_TEXT, byte for byte (#81):
# `make docs` writes it, tests/test_docs.py compares it, CI fails on any diff after `make docs`.
LLMS_TXT_HEADER = """\
# lean-pkg — llms.txt

> Registry and trust gate for Lean 4 formalization packages. Install: `pipx install git+https://github.com/lean-pkg/lean-pkg.git`.
> Human docs: https://lean-pkg.github.io/lean-pkg/ (source: docs/), README.md, DESIGN.md. Tests: `python3 -m pip install -e '.[test]' && python3 -m pytest -q`.
> The block below is the exact output of `lean-pkg llms` (written by `make docs`; tests/test_docs.py checks it byte for byte).

"""


def llms_txt() -> str:
    """The whole of llms.txt: LLMS_TXT_HEADER then the `lean-pkg llms` text, nothing after it."""
    return LLMS_TXT_HEADER + LLMS_TEXT


# --------------------------------------------------------------------------
# CLI

def llms_short(text: str = LLMS_TEXT) -> str:
    """`llms --short` (#43): a compact contract cut from LLMS_TEXT by its section headers, never a second
    hand-kept text: the title line, PURPOSE's header and first three lines, STANDING RULES whole, and the
    SUBCOMMANDS header with the first line (the column-0 line) of each subcommand; no flag continuations,
    examples, schemas or exit codes. Every line is a line of the full text. A missing header raises."""
    lines = text.splitlines()

    def section(is_header) -> list[str]:
        start = next((k for k, line in enumerate(lines) if is_header(line)), None)
        if start is None:
            raise ValueError("llms_short: section header not found in LLMS_TEXT")
        end = next((k for k in range(start + 1, len(lines)) if not lines[k].strip()), len(lines))
        return lines[start:end]

    purpose = section(lambda line: line == "PURPOSE")
    rules = section(lambda line: line == "STANDING RULES")
    commands = section(lambda line: line.startswith("SUBCOMMANDS "))
    out = [lines[0], "", *purpose[:4], "", *rules, "",
           commands[0], *(line for line in commands[1:] if not line[:1].isspace())]
    return "\n".join(out) + "\n"


def cmd_llms(args) -> int:
    short = getattr(args, "short", False)
    text = llms_short() if short else LLMS_TEXT
    if getattr(args, "json_out", False):
        print_json({"ok": True, "text": text, "short": True} if short else {"ok": True, "text": text})
        return 0
    print(text, end="")
    return 0


def _subparsers_action(parser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def help_index(root) -> str:
    """`lean-pkg help` with no argument: the commands and the help topics, gh-style."""
    from . import help_topics
    out = [root.description or "", "", "New here? `lean-pkg help start`: the five verbs and the two product tracks.",
           "", "USAGE", "  lean-pkg [global flags] <command> [flags]",
           "  lean-pkg help <command> | <topic>", "", "COMMANDS"]
    rows = [(a.dest, a.help or "") for a in _subparsers_action(root)._choices_actions]
    width = max(len(n) for n, _ in rows) + 2
    for name, text in rows:
        out.append(f"  {name.ljust(width)}{textwrap.shorten(text, 96 - width, placeholder=' ...')}")
    out += ["", "HELP TOPICS"]
    width = max(len(n) for n in help_topics.TOPICS) + 2
    for name, (summary, _) in help_topics.TOPICS.items():
        out.append(f"  {name.ljust(width)}{summary}")
    out += ["", "LEARN MORE",
            "  lean-pkg help <command>   that command's flags, the same text as `lean-pkg <command> --help`",
            "  lean-pkg help <topic>     one of the topics above",
            "  lean-pkg llms             the whole agent contract (llms.txt)"]
    return "\n".join(out) + "\n"


def help_command_parser(root, words: list[str]):
    """The parser `help WORDS...` names: a command, or a nested one (`help index add`, `help lease claim`); an
    old spelling names its canonical command (`help claim` is `help lease claim`). Anything else is a usage
    error naming what exists."""
    from . import command_tree
    words, _hits = command_tree.rewrite_words(words, implied=False)
    parser, path = root, ["lean-pkg"]
    for word in words:
        action = _subparsers_action(parser)
        if action is None:
            raise UsageError(f"help: `{' '.join(path[1:])}` has no subcommand {word!r}; "
                             f"see `lean-pkg help {' '.join(path[1:])}`")
        if word not in action.choices:
            if parser is root:
                from . import help_topics
                raise UsageError(f"help: unknown command or topic {word!r}; topics: "
                                 f"{', '.join(help_topics.TOPICS)}; commands: `lean-pkg help`")
            raise UsageError(f"help: `{' '.join(path[1:])}` has no subcommand {word!r}; choose from "
                             f"{', '.join(action.choices)}")
        parser = action.choices[word]
        path.append(word)
    return parser


def cmd_help(args) -> int:
    """`help [TOPIC | COMMAND [SUBCOMMAND]]` (gh's `help`): no argument lists the commands and topics; a
    topic prints its text (help_topics); a command prints exactly its --help. Reads no registry."""
    from . import help_topics
    words = list(getattr(args, "words", None) or [])
    root = args.help_root
    if not words:
        text = help_index(root)
        payload = {"ok": True, "topics": list(help_topics.TOPICS),
                   "commands": list(_subparsers_action(root).choices), "text": text}
    elif words[0] in help_topics.TOPICS:
        if len(words) > 1:
            raise UsageError(f"help {words[0]}: a topic takes no further argument (got {' '.join(words[1:])!r})")
        text = help_topics.topic_text(words[0])
        payload = {"ok": True, "topic": words[0], "text": text}
        if words[0] == "environment":
            payload["variables"] = help_topics.environment_rows()
    else:
        parser = help_command_parser(root, words)
        text = parser.format_help()
        payload = {"ok": True, "command": " ".join(words), "text": text}
    if getattr(args, "json_out", False):
        print_json(payload)
    else:
        print(text, end="")
    return 0


def cmd_init(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_init(reg_path, events_p, args.force)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_migrate(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_migrate(reg_path, events_p, args.dry_run)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_record(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    fields: dict = {}
    for item in args.field or []:
        if "=" not in item:
            raise Fail(f"--field expects KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        fields[k.strip()] = v
    reg = load_registry(reg_path)
    warn_lease(lease_actor(reg, args.name, args.agent, "record"))
    out, payload = do_record(reg_path, reg, events_p,
                             args.name, args.event, fields, args.reason, track=args.track)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def like_fields(reg: dict, like: str, path, given: dict) -> tuple[dict, list[str]]:
    """`register --like OTHER`: the fields `given` leaves unset (None) taken from OTHER's record, and the
    names copied. Copying OTHER's repo is refused when the new checkout and OTHER's are both on this host
    and in different git repositories (a sibling shares the repository; pass --repo otherwise)."""
    base = get_pkg(reg, like)
    out, copied = dict(given), []
    for field in ("repo", "owner_project", "upstream_target", "automation"):
        if out.get(field) is None and base.get(field) is not None:
            out[field] = base[field]
            copied.append(field)
    if "repo" in copied:
        _stored, key = normalize_local_path(path)
        mine, theirs = pathlib.Path(key), repo_path(base)
        if mine.is_dir() and theirs.is_dir():
            top = [git(d, "rev-parse", "--show-toplevel").stdout.strip() for d in (mine, theirs)]
            if top[0] != top[1]:
                raise Fail(f"--like {like}: {path} is not in {like}'s git repository ({top[1] or '?'}), so its "
                           f"repo {base.get('repo')} is not this package's; pass --repo; nothing written")
    return out, copied


def cmd_register(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    reg = load_registry(reg_path)
    f = {"repo": args.repo, "owner_project": args.owner_project, "upstream_target": args.upstream_target,
         "automation": args.automation}
    copied: list[str] = []
    if args.like is not None:
        f, copied = like_fields(reg, args.like, args.path, f)
    if f["repo"] is None:
        raise UsageError("register needs --repo URL (or --like OTHER to take a sibling's)")
    out, payload = do_register(reg_path, reg, events_p,
                               args.name, f["repo"], args.path,
                               f["owner_project"], args.visibility, args.status,
                               f["upstream_target"] or "none", f["automation"] or "", args.notes,
                               args.workspace, args.agent, args.dry_run, reason=args.reason,
                               like=args.like, like_copied=copied)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
        for note in payload.get("notes") or []:
            print(f"lean-pkg: {note}", file=sys.stderr)   # advisory (#22.7): stdout's last line is the result
    return 0


def cmd_claim(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    reg = load_registry(reg_path)
    out, payload = do_claim(reg_path, reg, events_p,
                            args.name, args.workspace, args.agent, args.ttl,
                            args.reason, args.force, args.contact)
    note = prior_art_note(get_pkg(reg, args.name), events_p)
    payload["notes"] = [note] if note else []
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
        if note:
            print(note, file=sys.stderr)   # advisory: stderr, so stdout's last line is the result
    return 0


def cmd_release(args) -> int:
    if args.adopt_repo is not None:   # #37: adopt a hand-built public history, then a relocation
        from . import fresh_repo as fresh_mod
        return fresh_mod.cmd_release_adopt(args)
    if args.fresh_repo is not None:   # #35: a single-commit public export, then a relocation
        from . import fresh_repo as fresh_mod
        return fresh_mod.cmd_release_fresh(args)
    extra = [f for f, on in (("--remote", args.remote is not None), ("--keep-receipts", args.keep_receipts),
                             ("--source-commit", args.source_commit is not None), ("--dry-run", args.dry_run),
                             ("--from-tree", args.from_tree is not None),
                             ("--allow-history", args.allow_history)) if on]
    if extra:
        raise UsageError(f"{', '.join(extra)} go(es) with --fresh-repo DIR (or --adopt-repo DIR)")
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_release(reg_path, load_registry(reg_path), events_p,
                              args.name, args.agent, args.force, args.reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_renew(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_renew(reg_path, load_registry(reg_path), events_p,
                            args.name, args.agent, args.ttl, args.workspace, args.reason, args.contact,
                            args.no_contact)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_leases(args) -> int:
    reg = load_registry(registry_path(args))
    now = dt.datetime.now(dt.timezone.utc)
    rows = lease_rows(reg, now, agent=args.agent, expiring_hours=args.expiring)
    if args.json_out:
        print_json({"ok": True, "now": now.isoformat(timespec="seconds"), "leases": rows})
    elif rows:
        print("\n".join(leases_lines(rows, now)))
    else:
        print("no leases" + (f" held by {args.agent}" if args.agent else "")
              + (f" expiring within {args.expiring:g}h" if args.expiring is not None else ""))
    return 0


def cmd_update(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    sets = {}
    for field in ("automation", "notes", "upstream_target", "reaudit_after",
                  "owner_project", "repo", "license_spdx", "formalization_yaml", "track", "headline_config"):
        value = getattr(args, field.replace("-", "_"), None)
        if value is not None:
            sets[field] = value
    paper = {k: v for k, v in (("arxiv", args.paper_arxiv), ("status", args.paper_status)) if v is not None}
    if paper:
        if "arxiv" in paper:
            require_nonempty("--paper-arxiv", paper["arxiv"])
            paper["arxiv"] = paper["arxiv"].strip().removeprefix("arXiv:").removeprefix("arxiv:")
        sets["paper"] = paper
    sp = {k: v for k, v in (("title", args.source_paper_title), ("authors", args.source_paper_author),
                            ("arxiv", args.source_paper_arxiv), ("doi", args.source_paper_doi)) if v is not None}
    if sp:
        for k, flag in (("title", "--source-paper-title"), ("arxiv", "--source-paper-arxiv"),
                        ("doi", "--source-paper-doi")):
            if k in sp:
                require_nonempty(flag, sp[k])
                sp[k] = sp[k].strip()
        if "arxiv" in sp:
            sp["arxiv"] = sp["arxiv"].removeprefix("arXiv:").removeprefix("arxiv:")
        if "doi" in sp:
            sp["doi"] = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", sp["doi"], flags=re.I)
        if "authors" in sp:
            sp["authors"] = [a.strip() for a in sp["authors"]]
        sets["source_paper"] = sp
    ai = {k: [x.strip() for x in v] for k, v in (("tools", args.ai_tool), ("roles", args.ai_role)) if v is not None}
    if ai:
        sets["ai_assistance"] = ai
    if args.append_note is not None:
        if args.notes is not None:
            raise UsageError("--append-note and --notes are mutually exclusive (--notes replaces the notes)")
        require_nonempty("--append-note", args.append_note)
        if not (args.agent or "").strip():
            raise UsageError("--append-note needs --agent A: the note is signed `YYYY-MM-DD (A): TEXT`")
        sets["notes"] = {"text": args.append_note.strip(), "agent": args.agent.strip()}
    docs_flag = getattr(args, "docs_descendants", None)
    if docs_flag is not None:
        sets["docs_descendants"] = docs_flag
    if getattr(args, "build_inputs", None) is not None:
        sets["build_inputs"] = [g.strip() for g in args.build_inputs.split(",") if g.strip()]
    if args.namespace_note is not None:
        sets["namespace_notes"] = {"text": args.namespace_note, "namespaces": args.namespace or []}
    elif args.namespace:
        raise UsageError("--namespace goes with --namespace-note TEXT")
    withdraw, unhold = bool(getattr(args, "withdraw_publication", False)), bool(getattr(args, "release_hold", False))
    if withdraw or unhold:   # #5: privacy withdrawal and its explicit release, each on its own
        flag = "--withdraw-publication" if withdraw else "--release-publication-hold"
        if sets or args.status is not None or args.path is not None or args.redetect:
            raise UsageError(f"{flag} is a publication-state change on its own; set other fields in a separate "
                             f"update")
        fn = do_withdraw_publication if withdraw else do_release_publication_hold
        out, payload = fn(reg_path, load_registry(reg_path), events_p, args.name, reason=args.reason,
                          agent=args.agent, force=args.force, dry_run=bool(args.dry_run))
        if args.json_out:
            print_json(payload)
        else:
            print("\n".join(out))
        return 0
    if args.path is not None:
        if sets or args.status is not None:
            raise UsageError("--path is a relocation on its own; set other fields in a separate update")
        if args.force:
            raise UsageError("--force is not accepted with --path; the lease holder relocates with --agent")
        out, payload = do_relocate(reg_path, load_registry(reg_path), events_p, args.name,
                                   new_path=args.path, reason=args.reason, agent=args.agent,
                                   redetect=args.redetect, dry_run=args.dry_run)
        if args.json_out:
            print_json(payload)
        else:
            print("\n".join(out))
        return 0
    docs_only = bool(sets) and set(sets) <= {"docs_descendants", "build_inputs"} and args.status is None
    if args.dry_run and not docs_only:
        raise UsageError("update --dry-run is supported only with --path")
    replace_notes = bool(getattr(args, "replace_notes", False))
    if replace_notes and args.notes is None:
        raise UsageError("--replace-notes goes with --notes TEXT")
    reg = load_registry(reg_path)
    if args.notes is not None and not replace_notes:
        old = str(get_pkg(reg, args.name).get("notes") or "")
        if old.strip() and args.notes != old:   # #53: refuse before the write, not a hint after it
            n = len(old.strip().splitlines())
            raise UsageError(f"{args.name}: --notes would replace the existing notes ({n} line(s)); nothing "
                             f"written. Add a line with --append-note TEXT --agent A, or pass --replace-notes "
                             f"to replace them (the previous text stays recoverable from history: the "
                             f"`updated` event's notes_from)")
    out, payload = do_update(reg_path, reg, events_p,
                             args.name, sets=sets, status=args.status,
                             reason=args.reason, redetect=args.redetect,
                             agent=args.agent, force=args.force, replace_notes=replace_notes,
                             dry_run=bool(args.dry_run))
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def refresh_preflight(reg: dict, name: str, agent: str | None) -> None:
    """--refresh-formalization, before the gate writes anything: the package's checkout exists, no other
    agent holds a live lease (the file write needs it), and the current render passes the schema (a kept
    key that fails it would otherwise refuse only after the gate has been recorded)."""
    from . import formalization as fz
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir() or git_head(repo) is None:
        raise Fail(f"{name}: --refresh-formalization needs the git checkout at {repo}")
    refuse_invalid_lease(name, pkg.get("lease"))
    lease = pkg.get("lease")
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)) and agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                   f"nothing written")
    _text, doc, _w, _existing = fz.render_for(pkg, repo)
    mode, errors = fz.validate_doc(doc)
    if errors:
        raise Fail(f"{name}: formalization.yaml would fail the {fz.SCHEMA_VERSION} schema ({mode}); fix "
                   f"its kept keys first; nothing written:\n  " + "\n  ".join(errors))


def do_gate_refresh(reg_path, reg, events_p, args) -> tuple[list[str], dict, list[str]]:
    """`gate NAME --receipt R --refresh-formalization` (#22.4): gate, then regenerate formalization.yaml
    (derived keys, including status.evidence, from the new receipt; kept keys survive) in one step.
    Refuses, writing nothing, when the gate is an identical re-gate and the file is already current."""
    from . import formalization as fz
    refresh_preflight(reg, args.name, args.agent)
    out, payload = do_gate(reg_path, reg, events_p, args.name, args.receipt, args.comparator,
                           refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                           formalization_path=fz.target_rel(get_pkg(reg, args.name)),
                           regate=getattr(args, "regate", False), reason=getattr(args, "reason", None))
    gated = payload.get("event") in ("gate-passed", "regated")
    pkg = get_pkg(reg, args.name)
    if not gated:
        text, _doc, _w, existing = fz.render_for(pkg, repo_path(pkg))
        if existing == text and pkg.get("formalization_yaml") == fz.target_rel(pkg):
            raise Fail(f"{args.name}: nothing to refresh: already gated with this receipt and "
                       f"{fz.target_rel(pkg)} is current; nothing written")
    f_lines, f_payload, warnings = fz.do_formalization(
        reg_path, reg, events_p, args.name, write=True, out=None, force=True, authors=None,
        agent=args.agent, reason="lean-pkg gate --refresh-formalization")
    payload["formalization"] = {k: f_payload[k] for k in ("path", "written", "unchanged", "registry_updated",
                                                         "validation", "placeholders")}
    return out + f_lines, payload, warnings


def cmd_gate(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    regate, reason = getattr(args, "regate", False), getattr(args, "reason", None)
    if reason is not None:
        if not regate:
            raise UsageError("gate --reason goes with --regate (it accepts a non-descendant re-gate)")
        require_nonempty("--reason", reason)
        reason = reason.strip()
    repo_arg = getattr(args, "repo", None)
    if repo_arg is not None and not getattr(args, "check", False):
        # field report: a writing gate binds the registered checkout; move it with `update --path`
        raise UsageError("gate --repo goes with --check: a writing gate binds the registered local_path "
                         f"(validate here with `gate {args.name} --check --repo DIR`, or move the record with "
                         f"`lean-pkg update {args.name} --path DIR --reason TEXT`)")
    if getattr(args, "check", False):
        if getattr(args, "refresh_formalization", False) or getattr(args, "agent", None) is not None:
            raise UsageError("gate --check writes nothing; --refresh-formalization and --agent do not apply: "
                             "--check takes no lease and checks none (anyone may validate a receipt, at any "
                             "status), so drop --agent here and pass it to the writing `gate` "
                             f"(`lean-pkg gate {args.name} --receipt R --agent A`)")
        reg, override = load_registry(reg_path), None
        if repo_arg is not None:
            reg, override = repo_override(reg, args.name, repo_arg)
            if note := repo_override_note(override):
                print(note, file=sys.stderr)
        out, payload = do_gate_check(reg, args.name, args.receipt, args.comparator,
                                     refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                                     regate=regate, reason=reason)
        payload["repo_override"] = override
        from . import palomar_check   # advisory (feature request #6): never changes the verdict
        payload["palomar_check"] = view = palomar_check.hook(reg, args.name, payload.get("commit"))
        if view and (warning := palomar_check.summary_warning(view)):
            print(f"WARN: {warning}", file=sys.stderr)
    elif getattr(args, "refresh_formalization", False):
        _ACTOR["agent"] = getattr(args, "agent", None) or None    # its own lease check runs in do_gate_refresh
        out, payload, warnings = do_gate_refresh(reg_path, load_registry(reg_path), events_p, args)
        for w in warnings:
            print(f"lean-pkg: warning: {w}", file=sys.stderr)
    else:
        reg = load_registry(reg_path)
        warn_lease(lease_actor(reg, args.name, getattr(args, "agent", None), "gate"))
        out, payload = do_gate(reg_path, reg, events_p,
                               args.name, args.receipt, args.comparator,
                               refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                               regate=regate, reason=reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0 if payload.get("ok", True) else 1


HOST_CHECK = "host-check"


def is_host_check(args) -> bool:
    """`lean-pkg comparator host-check` (no --receipt): the read-only host check (#22.5)."""
    return getattr(args, "name", None) == HOST_CHECK and getattr(args, "receipt", None) is None


CONFIG_CMD = "config"


def is_comparator_config(args) -> bool:
    """`lean-pkg comparator config --from-claims NAME` (#23.6): writes only the package's comparator
    configuration, never the registry."""
    return getattr(args, "name", None) == CONFIG_CMD and getattr(args, "receipt", None) is None


def cmd_comparator_host_check(args) -> int:
    from .comparator_receipt import host_checks
    res = host_checks()
    routes = None
    if getattr(args, "routes", False):   # field report #5: judge/prepare/transfer planning, additive keys
        from .host_routes import render_text, routes_report
        routes = routes_report(res)
    if args.json_out:
        print_json({"ok": True, **res, **(routes or {})})
    else:
        for c in res["checks"]:
            print(f"{c['level']} {c['item']}: {c['detail']}")
        for m, d in res.get("mechanism_detail", {}).items():   # #23.4: each mechanism against its own needs
            why = "; ".join(d["failing"]) if d["failing"] else "every requirement met (" + ", ".join(d["needs"]) + ")"
            print(f"mechanism {m}: {'ok' if d['ok'] else 'fail'} ({'Palomar runs this' if d['palomar'] else 'not Palomar-predictive'}): {why}")
        print(f"verdict: {res['verdict']}")
        if routes is not None:
            print("\n".join(render_text(routes)))
    if routes is not None:   # --routes: exit 0 when any route is supported here (can/verdict keep their meaning)
        return 0 if any(r["supported"] for r in routes["routes"]) else 1
    return 0 if res["can"] else 1


def cmd_comparator(args) -> int:
    repo_arg = getattr(args, "repo", None)
    if repo_arg is not None and (is_host_check(args) or is_comparator_config(args)):
        raise UsageError("comparator --repo goes with `comparator NAME --check --receipt R` (`comparator config` "
                         "takes --checkout DIR; host-check reads no checkout)")
    if is_host_check(args):
        return cmd_comparator_host_check(args)
    if getattr(args, "routes", False):
        raise UsageError("--routes belongs to `lean-pkg comparator host-check --routes`")
    if is_comparator_config(args):
        from .comparator_config import cmd_comparator_config
        return cmd_comparator_config(args)
    if args.from_claims or args.coverage or args.out or args.dry_run or args.challenge_namespace \
            or args.challenge_from_type or getattr(args, "claims_file", None) \
            or args.emit_script or args.from_output or args.lake_env or args.jobs is not None \
            or args.timeout is not None or getattr(args, "migrate_signoff", None) or getattr(args, "write", False):
        raise UsageError("--from-claims, --coverage, --migrate-signoff, --write, --out, --dry-run, --challenge-namespace "
                         "and the type flags belong to `lean-pkg comparator config --from-claims NAME` (or `comparator "
                         "config --coverage NAME`, `comparator config --migrate-signoff PATH`)")
    if args.receipt is None:
        raise UsageError("comparator NAME needs --receipt PATH (or: lean-pkg comparator host-check)")
    if repo_arg is not None and not args.check:
        # #13: like gate, a writing comparator binds the registered checkout; move it with `update --path`
        raise UsageError("comparator --repo goes with --check: attaching binds the registered local_path "
                         f"(validate here with `comparator {args.name} --check --receipt R --repo DIR`, or move "
                         f"the record with `lean-pkg update {args.name} --path DIR --reason TEXT`)")
    reg_path, events_p = registry_path(args), events_path(args)
    reg, override = load_registry(reg_path), None
    if not args.check:
        warn_lease(lease_actor(reg, args.name, args.agent, "comparator"))
    elif repo_arg is not None:
        reg, override = repo_override(reg, args.name, repo_arg)
        if note := repo_override_note(override):
            print(note, file=sys.stderr)
    out, payload = do_comparator(reg_path, reg, events_p,
                                 args.name, args.receipt, args.check,
                                 refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                                 replace=args.replace)
    if args.check:
        payload["repo_override"] = override
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_promote(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    reg = load_registry(reg_path)
    if args.yes:
        warn_lease(lease_actor(reg, args.name, args.agent, "promote --yes"))
    out, rc, payload = do_promote(reg_path, reg, events_p,
                                  args.name, args.yes, args.skip_public_safety)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return rc


def cmd_audit(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    floor = resolve_toolchain_floor(args.toolchain_floor)
    findings = audit_registry(reg_path, load_registry(reg_path), events_p,
                              fix=args.fix, toolchain_floor=floor, promotion=args.promotion,
                              use_gh=not args.no_gh)
    fixed = [f for f in findings if f.get("fixed")]
    if args.json_out:
        print_json({"ok": True, "fix": args.fix, "findings": findings, "fixed": len(fixed),
                    "counts": audit_counts(findings)})
    else:
        if not findings:
            print("no findings")
        else:   # --quiet (#44): the count line only
            print(audit_count_line(findings) if args.quiet else "\n".join(render_findings(findings)))
    if fixed:
        print(f"audit --fix wrote {len(fixed)} change(s): commit {reg_path.name} and {events_p.name} together",
              file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# doctor

DOCTOR_TOOLS = ("lake", "lean", "elan", "git", "gh", "reuse", "bwrap", "comparator", "nanoda",
                "go", "cargo", "jq")
DOCTOR_REQUIRED_TOOLS = ("lake", "elan", "git")   # missing -> warn; the others are optional (n/a)
DOCTOR_COMPARATOR_TOOLS = ("go", "cargo", "jq")   # needed only to build the legacy comparator tools
DOCTOR_ELAN_TOOLS = ("lake", "lean")              # must be elan's, or lean-toolchain is ignored
_NOT_NEEDED = "gate/status/claims do not need it"
# What each optional tool is for: the detail of its `n/a ... not on PATH` line, so a clean doctor on a
# host that only reads the registry does not read as a partial failure (levels unchanged).
DOCTOR_TOOL_HINTS = {
    "lean": "provided per project by elan; needed to build Lean projects, not by registry commands",
    "gh": "needed by promote, audit's GitHub checks and reservoir check; read-only commands do not need it",
    "reuse": "release gate only: promote's REUSE lint line, a WARN without it; nothing else needs it",
    "bwrap": "only needed to produce comparator receipts on this host (`lake comparator` sandboxes its judges "
             f"with it) — `lean-pkg comparator host-check`; {_NOT_NEEDED}",
    "comparator": "only needed to produce comparator receipts on this host — `lean-pkg comparator host-check`; "
                  + _NOT_NEEDED,
    "nanoda": "only needed to produce comparator receipts on this host — `lean-pkg comparator host-check`; "
              + _NOT_NEEDED,
}
for _t in DOCTOR_COMPARATOR_TOOLS:
    DOCTOR_TOOL_HINTS[_t] = ("only needed by comparator-receipt to build the legacy comparator tools on this host "
                             f"— `lean-pkg comparator host-check`; {_NOT_NEEDED}")


def elan_bin() -> pathlib.Path:
    """elan's proxy directory: $ELAN_HOME/bin, else ~/.elan/bin."""
    home = os.environ.get("ELAN_HOME", "").strip()
    return (pathlib.Path(home) if home else pathlib.Path.home() / ".elan") / "bin"


def is_elan_proxy(where: str, tool: str) -> bool:
    """True when `where` (a PATH hit for `tool`) is elan's proxy: it sits in elan's bin directory, or
    resolves to the same file as the proxy there (e.g. a symlink to it)."""
    proxy = elan_bin() / tool
    p = pathlib.Path(where)
    if p.parent.resolve() == proxy.parent.resolve():
        return True
    return proxy.exists() and p.resolve() == proxy.resolve()


def package_dir() -> pathlib.Path:
    """Directory of the imported lean_pkg package (tests monkeypatch this)."""
    from . import __file__ as pkg_file
    return pathlib.Path(pkg_file).resolve().parent


def fetch_origin(root: pathlib.Path) -> str | None:
    """`git fetch --quiet --prune origin`; None on success, else the failure (first stderr line)."""
    p = registry_git(root, "fetch", "--quiet", "--prune", "origin")
    if p.returncode == 0:
        return None
    return (p.stderr or p.stdout or "fetch failed").strip().splitlines()[0]


def ref_note(fetched: bool, fetch_error: str | None) -> str:
    if fetch_error:
        return f"local refs; fetch failed: {fetch_error}"
    return "fetched" if fetched else "local refs; --fetch to be sure"


# --------------------------------------------------------------------------
# committed mode (--commit / --push): pull --ff-only, write, commit the two registry files together

COMMIT_ENV = "LEAN_PKG_COMMIT"
PUSH_ENV = "LEAN_PKG_PUSH"
_IDENTITY_ERRORS = ("Author identity unknown", "Please tell me who you are")


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def commit_mode(args) -> str | None:
    """None, "commit" or "push" for this invocation: flags, else env; --no-commit wins; an invocation that
    writes nothing (is_write_invocation: read-only commands and dry runs) is always None."""
    if getattr(args, "no_commit", False) or not is_write_invocation(args):
        return None
    if getattr(args, "push", False) or _env_on(PUSH_ENV):
        return "push"
    if getattr(args, "commit", False) or _env_on(COMMIT_ENV):
        return "commit"
    return None


def commit_mode_source(args) -> str:
    """Why committed mode is on, for the stderr report: the flag, else the env variable that turned it on."""
    if getattr(args, "push", False):
        return "--push"
    if _env_on(PUSH_ENV):
        return f"env {PUSH_ENV}"
    if getattr(args, "commit", False):
        return "--commit"
    return f"env {COMMIT_ENV}"


def note_ignored_commit_flags(args) -> None:
    """--commit/--push given on an invocation that writes nothing: a stderr note, not an error."""
    flags = [f for f in ("commit", "push") if getattr(args, f, False)]
    if flags and not getattr(args, "no_commit", False) and not is_write_invocation(args):
        print(f"lean-pkg: {' and '.join('--' + f for f in flags)} ignored: read-only command", file=sys.stderr)


def _first_line(p: subprocess.CompletedProcess, default: str) -> str:
    """The first non-blank line of stderr (else stdout), stripped: a remote's `remote: ...` line carries
    trailing padding, and an empty first line must not hide the message under it."""
    for text in (p.stderr or "", p.stdout or ""):
        for line in text.splitlines():
            if line.strip():
                return line.strip()
    return default


def _count_lines(p: pathlib.Path) -> int:
    try:
        return len(p.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def _dirty(ctx: dict) -> list[str]:
    """The registry files (repository-relative, as git spells them) with uncommitted changes. Parsed from
    `status --porcelain -z`: plain --porcelain C-quotes a path with a space or a non-ASCII character
    ("my reg/lean-packages.yaml"), and the quoted form is no pathspec git add/commit can match."""
    out = registry_git(ctx["root"], "status", "--porcelain", "-z", "--", *ctx["files"]).stdout or ""
    entries, paths, i = out.split("\0"), [], 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        paths.append(e[3:])
        if e[0] in "RC":   # a rename or copy: the source path follows as its own entry
            i += 1
    return paths


def registry_work_tree(args) -> tuple[pathlib.Path, list[str]] | None:
    """(git top level, the registry yaml and event log relative to it) when both sit in one git work tree;
    None otherwise (no git, not a repository, a file outside it). Never raises."""
    if shutil.which("git") is None:
        return None
    reg_p, ev = registry_path(args), events_path(args)
    if not reg_p.parent.is_dir():
        return None
    top = registry_git(reg_p.parent, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or not top.stdout.strip():
        return None
    root = pathlib.Path(top.stdout.strip())
    try:
        files = [str(f.resolve().relative_to(root.resolve())) for f in (reg_p, ev)]
    except ValueError:
        return None
    return root, files


def pending_events(root: pathlib.Path, events_rel: str) -> list[dict]:
    """Event lines in the working-tree log that HEAD's log does not have, oldest first (unparseable lines
    skipped)."""
    try:
        work = (root / events_rel).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    head = registry_git(root, "show", f"HEAD:{events_rel}")
    committed = set(head.stdout.splitlines()) if head.returncode == 0 else set()
    out = []
    for line in work:
        if line.strip() and line not in committed:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    return out


def pending_summary(root: pathlib.Path, files: list[str], reg_p: pathlib.Path) -> str:
    """` (pending: claimed foo by vera at 2026-10-12T15:12; lease on foo: vera, live)` for a refusal over an
    uncommitted registry (#53's report): who left it, without reading git diff. Empty when nothing parses."""
    evs = pending_events(root, files[1]) if len(files) > 1 else []
    if not evs:
        return ""
    last = evs[-1]
    who = last.get("agent") or last.get("released_by") or "unknown agent"
    parts = [f"pending: {last.get('event', 'event')} {last.get('name') or ''} by {who} at "
             f"{str(last.get('ts') or '?')[:16]}".replace("  ", " ")
             + (f" (+{len(evs) - 1} more)" if len(evs) > 1 else "")]
    name = last.get("name")
    try:
        pkg = (load_registry(reg_p, check_version=False).get("packages") or {}).get(name) if name else None
    except Exception:   # noqa: BLE001 -- a summary for a refusal; the refusal itself must still be raised
        pkg = None
    lease = (pkg or {}).get("lease")
    if lease:
        exp = lease_expiry_or_none(lease)
        state = ("live" if exp is not None and dt.datetime.now(dt.timezone.utc) < exp
                 else "expired" if exp is not None else "unreadable")
        parts.append(f"lease on {name}: {lease.get('agent', '?')}, {state}")
    elif name:
        parts.append(f"no lease on {name}")
    return " (" + "; ".join(parts) + ")"


def uncommitted_notice(args) -> list[str] | None:
    """After a write that ran without committed mode: the registry files left modified in their git work tree
    (None when the registry is not in git or nothing is modified). They block every committed-mode writer
    until someone commits them, so the write must not end quietly (#53's report)."""
    wt = registry_work_tree(args)
    if wt is None:
        return None
    root, files = wt
    dirty = _dirty({"root": root, "files": files})
    if not dirty:
        return None
    print(f"lean-pkg: NOT COMMITTED: {', '.join(dirty)} modified in {root}; every --commit/--push write to this "
          f"registry refuses until they are committed: git -C {root} commit -m 'lean-pkg: ...' -- "
          f"{' '.join(dirty)} (or run writes with --commit)", file=sys.stderr)
    return dirty


def commit_preflight(args, mode: str) -> dict:
    """Before a write in committed mode: the registry sits in a git work tree on a branch, its two
    files carry no uncommitted changes, and the branch is fast-forwarded to origin/<branch> when
    origin is ahead. Returns the context commit_finish needs; raises Fail on a refusal."""
    if shutil.which("git") is None:
        raise Fail("--commit needs git on PATH")
    reg_p, ev = registry_path(args), events_path(args)
    top = registry_git(reg_p.parent, "rev-parse", "--show-toplevel") if reg_p.parent.is_dir() else None
    if top is None or top.returncode != 0 or not top.stdout.strip():
        raise Fail(f"--commit needs the registry inside a git work tree ({reg_p.parent} is not)")
    root = pathlib.Path(top.stdout.strip())
    files = []
    for f in (reg_p, ev):
        try:
            files.append(str(f.resolve().relative_to(root.resolve())))
        except ValueError:
            raise Fail(f"--commit needs {f} inside the registry's git work tree {root}")
    br = registry_git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 else ""
    if not branch:
        raise Fail(f"--commit needs a branch checked out in {root} (detached HEAD)")
    ctx = {"root": root, "files": files, "branch": branch, "events": ev, "push": mode == "push",
           "source": commit_mode_source(args)}
    dirty = _dirty(ctx)
    if dirty:
        who = pending_summary(root, ctx["files"], reg_p)
        raise Fail(f"uncommitted registry changes in {', '.join(dirty)}{who}; the writer that made them commits "
                   f"them: git -C {root} commit -m 'lean-pkg: ...' -- {' '.join(dirty)}")
    if registry_git(root, "remote", "get-url", "origin").returncode != 0:
        print(f"lean-pkg: no origin remote in {root}; committing locally without a pull", file=sys.stderr)
        ctx["origin"] = False
    else:
        ctx["origin"] = True
        err = fetch_origin(root)
        if err:
            print(f"lean-pkg: fetch origin failed: {err}; using local refs", file=sys.stderr)
        counts = registry_git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
        behind, ahead = (int(x) for x in counts.stdout.split()) if counts.returncode == 0 else (0, 0)
        if behind > 0:
            p = registry_git(root, "pull", "--ff-only", "--quiet", "origin", branch)
            if p.returncode != 0:
                raise Fail(f"registry clone has diverged from origin/{branch}; reconcile by hand before "
                           f"writing ({_first_line(p, 'pull --ff-only failed')})")
        elif ahead > 0 and ctx["push"]:
            # #54: an unpushed commit (a push that failed, or a commit made outside lean-pkg) is never carried
            # silently into the next write: the clones would diverge while every write still exits 0.
            tip = registry_git(root, "log", "-1", "--format=%h %s").stdout.strip()
            more = f" (+{ahead - 1} more)" if ahead > 1 else ""
            raise Fail(f"registry clone is {ahead} commit(s) ahead of origin/{branch}: unpushed {tip}{more}; "
                       f"push it first: git -C {root} push origin {branch}")
    ctx["lines_before"] = _count_lines(ev)
    return ctx


def commit_message(ctx: dict) -> str:
    try:
        lines = ctx["events"].read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    new = [line for line in lines[ctx["lines_before"]:] if line.strip()]
    if not new:
        return "lean-pkg: registry update"
    try:
        last = json.loads(new[-1])
    except ValueError:
        return "lean-pkg: registry update"
    msg = f"lean-pkg: {last.get('event', 'update')}"
    if last.get("name"):
        msg += f" {last['name']}"
    if len(new) > 1:
        msg += f" (+{len(new) - 1} more)"
    return msg


FALLBACK_IDENTITY = ("lean-pkg", "lean-pkg@localhost")


_REJECTED_MARKERS = ("non-fast-forward", "[rejected]", "fetch first", "Updates were rejected",
                     "cannot lock ref", "stale info")
# `! [remote rejected] main -> main (<reason>)`: origin moved on only when the reason is a lost ref race
# (GitHub: `cannot lock ref 'refs/heads/main': is at <sha> but expected <sha>`, #71); any other reason
# (`pre-receive hook declined`, a protected branch) is a permanent refusal.
_REMOTE_REJECTED_RE = re.compile(r"\[remote rejected\][^\n(]*\((.*)\)[ \t]*$", re.M)


def _push_rejected(p: subprocess.CompletedProcess) -> bool:
    """A push refused because origin moved on (not an auth or network failure)."""
    text = f"{p.stderr}\n{p.stdout}"
    if any(m in text for m in _REJECTED_MARKERS):
        return True
    return any("cannot lock ref" in r or "but expected" in r for r in _REMOTE_REJECTED_RE.findall(text))


def _rebase_in_progress(root: pathlib.Path) -> bool:
    for d in ("rebase-merge", "rebase-apply"):
        q = registry_git(root, "rev-parse", "--git-path", d)
        if q.returncode == 0 and (root / q.stdout.strip()).exists():
            return True
    return False


PUSH_RETRIES_ENV = "LEAN_PKG_PUSH_RETRIES"
PUSH_RETRY_DELAY_ENV = "LEAN_PKG_PUSH_RETRY_DELAY"
# Transport failures worth a retry (#68): the remote never saw the push. A rejection (origin moved on) is
# handled by a rebase instead; anything else (auth, a hook's refusal, a protected branch) fails at once.
_TRANSIENT_MARKERS = ("connection reset", "kex_exchange_identification", "could not read from remote",
                      "timed out", "timeout", "connection closed by", "connection refused",
                      "the remote end hung up unexpectedly", "early eof", "broken pipe",
                      "could not resolve host", "temporary failure in name resolution",
                      "network is unreachable", "ssh: connect to host")
_TRANSIENT_HTTP_RE = re.compile(r"(?:returned error:|HTTP|status)\s*5\d\d\b", re.I)


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, "").strip()))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "").strip()))
    except ValueError:
        return default


def push_retry_policy() -> tuple[int, float]:
    """(retries, first delay in seconds) for a transient push failure: env LEAN_PKG_PUSH_RETRIES (default 4)
    and LEAN_PKG_PUSH_RETRY_DELAY (default 5; doubled per retry: 5, 10, 20, 40 s)."""
    return _env_int(PUSH_RETRIES_ENV, 4), _env_float(PUSH_RETRY_DELAY_ENV, 5.0)


def _push_transient(p: subprocess.CompletedProcess) -> bool:
    text = f"{p.stderr}\n{p.stdout}"
    return any(m in text.lower() for m in _TRANSIENT_MARKERS) or bool(_TRANSIENT_HTTP_RE.search(text))


def _sleep(seconds: float) -> None:   # tests monkeypatch this
    if seconds > 0:
        import time
        time.sleep(seconds)


def _identity_args(root: pathlib.Path) -> list[str]:
    """`-c user.name=... -c user.email=...` with the fallback identity when the clone has none (a rebase
    re-commits, so it needs a committer as `commit` does)."""
    if registry_git(root, "var", "GIT_COMMITTER_IDENT").returncode == 0:
        return []
    name, email = FALLBACK_IDENTITY
    return ["-c", f"user.name={name}", "-c", f"user.email={email}"]


def _event_ts_key(line: str):
    try:
        return parse_ts(json.loads(line)["ts"])
    except (ValueError, KeyError, TypeError):
        return None


def union_event_lines(base: list[str], ours: list[str], theirs: list[str]) -> list[str] | None:
    """The event log's union merge (#68): both sides only append to `base`, so the result is `base` plus
    both sides' new lines in stable timestamp order (ties: `ours` first, which in a rebase is origin's).
    None when a side did not just append, or a new line carries no readable `ts`."""
    if ours[:len(base)] != base or theirs[:len(base)] != base:
        return None
    new = ours[len(base):] + theirs[len(base):]
    keys = [_event_ts_key(x) for x in new]
    if any(k is None for k in keys):
        return None
    order = sorted(range(len(new)), key=lambda i: (keys[i], i))
    return base + [new[i] for i in order]


def _resolve_event_log_conflict(ctx: dict) -> bool:
    """During a stopped rebase whose only conflict is the event log: write the union of both sides and stage
    it. False when the stages cannot be read or the union does not apply."""
    root, ev_rel = ctx["root"], ctx["files"][-1]

    def stage(n: int) -> list[str] | None:
        q = registry_git(root, "show", f":{n}:{ev_rel}")
        return q.stdout.splitlines() if q.returncode == 0 else (None if n != 1 else [])
    base, ours, theirs = stage(1), stage(2), stage(3)
    if ours is None or theirs is None:
        return False
    merged = union_event_lines(base, ours, theirs)
    if merged is None:
        return False
    (root / ev_rel).write_text("".join(x + "\n" for x in merged), encoding="utf-8")
    if registry_git(root, "add", "--", ev_rel).returncode != 0:
        return False
    print(f"lean-pkg: event log conflict resolved by union ({len(ours) - len(base)} line(s) from origin, "
          f"{len(theirs) - len(base)} ours, timestamp order)", file=sys.stderr)
    return True


def _conflicted(root: pathlib.Path) -> list[str]:
    q = registry_git(root, "diff", "--name-only", "--diff-filter=U")
    return [x for x in q.stdout.splitlines() if x.strip()]


def _rebase_onto_origin(ctx: dict) -> tuple[bool, str | None]:
    """After a non-fast-forward rejection (#22.9, #68): fetch origin and rebase our one commit onto
    origin/<branch>. (True, None) when the rebase finished; (False, why) otherwise, with any stopped
    rebase aborted so the clone is exactly as before (our commit kept, ahead). Only when our commit is the
    single commit ahead of origin/<branch>; an event-log-only conflict merges as a union of lines."""
    root, branch = ctx["root"], ctx["branch"]
    upstream = f"refs/remotes/origin/{branch}"
    ahead = registry_git(root, "rev-list", "--count", f"{upstream}..HEAD")
    if ahead.returncode != 0 or ahead.stdout.strip() != "1":
        return False, None
    print(f"lean-pkg: push rejected (origin/{branch} moved on); fetching and rebasing the commit",
          file=sys.stderr)
    err = fetch_origin(root)
    if err is not None:
        return False, f"fetch failed: {err}"
    ident = _identity_args(root)
    pre = registry_git(root, "rev-parse", "HEAD").stdout.strip()
    # --autostash: commit_preflight allows unrelated modified files in the clone (only the registry files
    # must be clean), and a plain rebase refuses to start over them ("You have unstaged changes").
    rb = registry_git(root, *ident, "rebase", "--autostash", "--quiet", upstream)
    while rb.returncode != 0 and _rebase_in_progress(root):
        files = _conflicted(root)
        if files == [ctx["files"][-1]] and _resolve_event_log_conflict(ctx):
            rb = registry_git(root, *ident, "-c", "core.editor=true", "rebase", "--continue")
            continue
        registry_git(root, "rebase", "--abort")
        why = (f"rebase conflict in {', '.join(files)}" if files
               else f"rebase failed ({_first_line(rb, 'rebase failed')})")
        print(f"lean-pkg: rebase onto origin/{branch} stopped: {why}; aborted, kept the local commit",
              file=sys.stderr)
        return False, why
    if rb.returncode != 0:
        why = f"rebase failed ({_first_line(rb, 'rebase failed')})"
        print(f"lean-pkg: rebase onto origin/{branch}: {why}; kept the local commit", file=sys.stderr)
        return False, why
    bad = rebased_registry_errors(ctx, pre, upstream)
    if bad:
        ctx["rebase_invalid"] = True
        reset = registry_git(root, "reset", "-q", "--keep", pre)
        kept = ("reset to the local commit" if reset.returncode == 0
                else f"could not reset to the local commit {pre[:12]} ({_first_line(reset, 'reset failed')})")
        why = (f"rebased registry fails validation ({len(bad)} new error(s): {'; '.join(bad[:3])}"
               + (" ..." if len(bad) > 3 else "") + ")")
        print(f"lean-pkg: rebase onto origin/{branch}: {why}; not pushed, {kept}", file=sys.stderr)
        return False, why
    short = registry_git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    print(f"lean-pkg: rebased onto origin/{branch} ({short}); pushing again", file=sys.stderr)
    return True, None


def _registry_errors_at(root: pathlib.Path, rev: str, reg_rel: str) -> list[str] | None:
    """validate_registry on the registry file as committed at `rev`; None when it cannot be read or parsed."""
    q = registry_git(root, "show", f"{rev}:{reg_rel}")
    if q.returncode != 0:
        return None
    try:
        return validate_registry(yaml.safe_load(q.stdout))
    except yaml.YAMLError:
        return None


def rebased_registry_errors(ctx: dict, pre: str, upstream: str) -> list[str]:
    """The schema errors the rebased registry (HEAD) has that neither side had: not origin/<branch>'s, not our
    pre-rebase commit's (an error either side already carried is not the merge's doing). An unparseable
    merged file is one error."""
    root, reg_rel = ctx["root"], ctx["files"][0]
    merged = _registry_errors_at(root, "HEAD", reg_rel)
    if merged is None:
        return [f"{reg_rel} at the rebased commit cannot be read or parsed"]
    allowed: collections.Counter = collections.Counter()
    for rev in (upstream, pre):
        errs = _registry_errors_at(root, rev, reg_rel) or []
        allowed |= collections.Counter(_err_key(e) for e in errs)
    new = []
    for e in merged:
        k = _err_key(e)
        if allowed[k] > 0:
            allowed[k] -= 1
        else:
            new.append(e)
    return new


def push_with_recovery(ctx: dict) -> tuple[subprocess.CompletedProcess, str | None]:
    """`git push origin <branch>` in committed mode (#68): a transient transport failure is retried with
    backoff (push_retry_policy); a non-fast-forward rejection fetches, rebases the one registry commit and
    pushes again (as often as the retry budget allows, at least once). Returns the last push and, when a
    rebase stopped the recovery, why. Counts land in ctx["result"]: push_attempts, rebased."""
    root, branch, res = ctx["root"], ctx["branch"], ctx["result"]
    retries, delay = push_retry_policy()
    transient_left, rebases_left = retries, max(1, retries)
    while True:
        res["push_attempts"] += 1
        p = registry_git(root, "push", "--quiet", "origin", branch)
        if p.returncode == 0:
            return p, None
        if _push_rejected(p):
            if rebases_left == 0:
                return p, None
            rebases_left -= 1
            ok, why = _rebase_onto_origin(ctx)
            if not ok:
                return p, why
            res["rebased"] = True
            res["sha"] = registry_git(root, "rev-parse", "HEAD").stdout.strip() or res["sha"]
            continue
        if _push_transient(p) and transient_left > 0:
            wait = delay * (2 ** (retries - transient_left))
            transient_left -= 1
            print(f"lean-pkg: push attempt {res['push_attempts']} failed ({_push_error_line(p)}); retrying in "
                  f"{wait:g}s ({retries - transient_left} of {retries})", file=sys.stderr)
            _sleep(wait)
            continue
        return p, None


def _push_error_line(p: subprocess.CompletedProcess) -> str:
    """git push's reason: its first non-blank stderr line, skipping the `To <url>` header and `hint:` lines
    (a rejection prints `To <url>` first, which says nothing about why)."""
    lines = [x.strip() for x in f"{p.stderr or ''}\n{p.stdout or ''}".splitlines() if x.strip()]
    useful = [x for x in lines if not x.startswith(("To ", "hint:"))]
    return (useful or lines or [f"git push exited {p.returncode}"])[0]


def _push_failed(ctx: dict, why: str, rejected: bool = False) -> int:
    """A push that did not land (#54): the commit is kept, the clone is ahead of origin/<branch>; stderr names
    the remote branch, the git error and the recovery, the --json `commit` gets pushed false + push_error.
    `rejected` (origin moved on): a plain `git push` would be rejected again, so the recovery integrates
    origin first (`git pull --rebase`); a rebase whose result failed validation is reconciled by hand."""
    root, branch, res = ctx["root"], ctx["branch"], ctx["result"]
    res["pushed"] = False
    res["push_error"] = why
    res["error"] = f"push failed: origin/{branch}: {why}"
    short = registry_git(root, "rev-parse", "--short", "HEAD").stdout.strip() or "HEAD"
    if ctx.get("rebase_invalid"):
        fix = (f"reconcile lean-packages.yaml with origin/{branch} by hand (lean-pkg validate), then "
               f"git push origin {branch}")
    elif rejected:
        fix = f"git pull --rebase origin {branch}, then git push origin {branch}"
    else:
        fix = f"git push origin {branch}"
    print(f"lean-pkg: push failed: origin/{branch}: {why}", file=sys.stderr)
    print(f"lean-pkg: kept commit {short}; the clone is ahead of origin/{branch}; run: {fix} "
          f"(until then lean-pkg refuses the next committed-mode push)", file=sys.stderr)
    return 1


def commit_finish(ctx: dict, rc: int) -> int:
    """After the subcommand in committed mode: commit the changed registry files (only those paths),
    then push when asked. Writes only to stderr; stdout stays the subcommand's. The outcome is left in
    ctx["result"] = {"sha", "pushed", "error", "push_error", "push_attempts", "rebased"} (the `commit` key of a
    --json payload). The push goes through push_with_recovery (#68). A failed push keeps the commit, says so
    (`push failed: origin/<branch>: <git error>`) and returns 1 (#54)."""
    root, branch = ctx["root"], ctx["branch"]
    res = ctx["result"] = {"sha": None, "pushed": False, "error": None, "push_error": None,
                           "push_attempts": 0, "rebased": False}
    dirty = _dirty(ctx)
    if rc != 0:
        if dirty:
            res["error"] = f"not committed: the command failed and left {', '.join(dirty)} modified"
            print(f"lean-pkg: not committing: the command failed and left {', '.join(dirty)} modified",
                  file=sys.stderr)
        return rc
    if not dirty:
        return rc
    msg = commit_message(ctx)
    add = registry_git(root, "add", "--", *dirty)
    if add.returncode != 0:
        res["error"] = f"commit failed: {_first_line(add, 'git add failed')}"
        print(f"lean-pkg: commit failed: {_first_line(add, 'git add failed')}; the registry files are "
              f"modified and uncommitted", file=sys.stderr)
        return 1
    argv = ("commit", "-q", "-m", msg, "--", *dirty)
    p = registry_git(root, *argv)
    if p.returncode != 0 and any(s in (p.stderr + p.stdout) for s in _IDENTITY_ERRORS):
        name, email = FALLBACK_IDENTITY
        p = registry_git(root, "-c", f"user.name={name}", "-c", f"user.email={email}", *argv)
        if p.returncode == 0:
            print(f"lean-pkg: committed as {name} <{email}>; set git user.name/email in {root}",
                  file=sys.stderr)
    if p.returncode != 0:
        res["error"] = f"commit failed: {_first_line(p, 'git commit failed')}"
        print(f"lean-pkg: commit failed: {_first_line(p, 'git commit failed')}; the registry files are "
              f"modified and uncommitted", file=sys.stderr)
        return 1
    res["sha"] = registry_git(root, "rev-parse", "HEAD").stdout.strip() or None
    short = registry_git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    via = ctx.get("source", "")
    # A mode the env turned on is named, so a commit nobody passed a flag for is never a surprise.
    print(f"lean-pkg: committed {short} on {branch}: {msg}"
          + (f" (committed mode: {via}=1)" if via.startswith("env ") else ""), file=sys.stderr)
    if ctx["push"]:
        if not ctx["origin"]:
            return _push_failed(ctx, "no origin remote")
        p, why = push_with_recovery(ctx)
        if p.returncode != 0:
            return _push_failed(ctx, _push_error_line(p) + (f"; {why}" if why else ""), _push_rejected(p))
        res["pushed"] = True
        short = registry_git(root, "rev-parse", "--short", "HEAD").stdout.strip() or short
        print(f"lean-pkg: pushed origin/{branch} ({short})", file=sys.stderr)
    return rc


def no_origin_ref_detail(root: pathlib.Path, branch: str, note: str) -> str:
    """Why behind/ahead cannot be counted: no origin remote at all, or no local origin/<branch> ref."""
    if registry_git(root, "remote", "get-url", "origin").returncode != 0:
        return "no origin remote"
    return f"no local origin/{branch} ref to compare ({note})"


GH_AUTH_RE = re.compile(r"Logged in to github\.com (?:account|as) (\S+)")


def gh_auth_check() -> tuple[str, str]:
    """(level, detail) from `gh auth status`: ok with the github.com account, warn when gh is
    present but not logged in (promote and audit query GitHub through it), n/a without gh."""
    if shutil.which("gh") is None:
        return "n/a", "gh not on PATH"
    p = gh("auth", "status", "--hostname", "github.com")
    m = GH_AUTH_RE.search(f"{p.stdout}\n{p.stderr}")
    if m:
        return "ok", m.group(1)
    return "warn", "not logged in to github.com (promote and audit need it): run gh auth login"


def commit_now(ctx: dict) -> str:
    """The TUI's commit after each successful write (called inside its registry lock): commit_finish with
    stderr captured (curses owns the screen); returns its last line for the status bar."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        commit_finish(ctx, 0)
    ctx["lines_before"] = _count_lines(ctx["events"])
    lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    return lines[-1].removeprefix("lean-pkg: ") if lines else ""


def install_detect(d: pathlib.Path) -> dict:
    """Which build is running, local and fast (no network, no fetch): {"install": "editable" | "installed" |
    "unknown", "path", "commit", "dirty"}. editable when the package's __init__.py is tracked by a git checkout
    (path: the checkout; commit: its short HEAD; dirty: modified tracked files); installed for a wheel or pipx
    install (path: the package directory; commit and dirty null); unknown when git is not on PATH. The one
    detection behind `doctor`'s install line and `--version`."""
    info = {"install": "unknown", "path": str(d), "commit": None, "dirty": None}
    if shutil.which("git") is None:
        return info
    top = registry_git(d, "rev-parse", "--show-toplevel")
    tracked = registry_git(d, "ls-files", "--error-unmatch", "__init__.py").returncode == 0
    if top.returncode != 0 or not top.stdout.strip() or not tracked:
        info["install"] = "installed"   # not tracked: e.g. site-packages that sit inside a checkout
        return info
    root = pathlib.Path(top.stdout.strip())
    # --no-optional-locks: a read-only probe must not refresh (rewrite) the checkout's .git/index
    st = registry_git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no")
    info.update(install="editable", path=str(root),
                commit=registry_git(root, "rev-parse", "--short", "HEAD").stdout.strip() or "?",
                dirty=bool(st.stdout.strip()) if st.returncode == 0 else None)
    return info


def version_line(version: str, info: dict) -> str:
    """`lean-pkg --version`: lean-pkg X.Y.Z (<short-sha>[+dirty], <install kind> <path>)."""
    if info["install"] == "editable":
        return (f"lean-pkg {version} ({info['commit']}{'+dirty' if info['dirty'] else ''}, "
                f"editable {info['path']})")
    if info["install"] == "installed":
        return f"lean-pkg {version} (installed {info['path']})"
    return f"lean-pkg {version} (unknown build, {info['path']})"


def install_check(d: pathlib.Path, fetch: bool = False) -> tuple[str, str]:
    """(level, detail) for the install (install_detect): an editable checkout adds HEAD (+dirty), branch and
    behind/ahead against origin/<branch> (local refs unless fetch)."""
    info = install_detect(d)
    if info["install"] == "unknown":
        return "warn", f"{d} (git not on PATH; install kind unknown)"
    if info["install"] == "installed":
        return "ok", f"installed {d}"
    root = pathlib.Path(info["path"])
    short = info["commit"] + ("+dirty" if info["dirty"] else "")
    br = registry_git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 else ""
    head = f"editable {root} @ {short}"
    if not branch:
        return "warn", head + " (detached HEAD)"
    fetch_error = fetch_origin(root) if fetch else None
    note = ref_note(fetch, fetch_error)
    counts = registry_git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
    if counts.returncode != 0:
        return "warn", f"{head} on {branch}; {no_origin_ref_detail(root, branch, note)}"
    behind, ahead = (int(x) for x in counts.stdout.split())
    level = "warn" if behind else "ok"
    return level, f"{head} on {branch}; {behind} behind, {ahead} ahead of origin/{branch} ({note})"


def registry_repo_check(reg_p: pathlib.Path, ev: pathlib.Path, fetch: bool = False) -> tuple[str, str]:
    """(level, detail) for the git work tree holding the registry: HEAD, branch, behind/ahead of
    origin/<branch>, uncommitted tracked files (naming the registry files among them). warn when
    a write from here would conflict (behind), is invisible elsewhere (ahead, or the registry files
    are uncommitted) or sits on a detached HEAD; n/a outside a work tree or without git."""
    if shutil.which("git") is None:
        return "n/a", "git not on PATH"
    if not reg_p.parent.is_dir():
        return "n/a", f"{reg_p.parent} is not a directory"
    top = registry_git(reg_p.parent, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return "n/a", f"{reg_p.parent} is not inside a git work tree"
    root = pathlib.Path(top.stdout.strip())
    short = registry_git(root, "rev-parse", "--short", "HEAD").stdout.strip() or "?"
    br = registry_git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 else ""
    head = f"{root} @ {short}"
    if not branch:
        return "warn", head + " (detached HEAD; check out a branch before writing)"
    fetch_error = fetch_origin(root) if fetch else None
    note = ref_note(fetch, fetch_error)
    porcelain = registry_git(root, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    dirty = [line[3:] for line in porcelain if line.strip()]
    reg_rel = []
    for f in (reg_p, ev):
        try:
            reg_rel.append(str(f.resolve().relative_to(root.resolve())))
        except ValueError:
            pass
    dirty_reg = [f for f in dirty if f in reg_rel]
    advice = []
    level = "ok"
    counts = registry_git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
    if counts.returncode != 0:
        sync = no_origin_ref_detail(root, branch, note)
        level = "warn"
    else:
        behind, ahead = (int(x) for x in counts.stdout.split())
        sync = f"{behind} behind, {ahead} ahead of origin/{branch} ({note})"
        if behind:
            level = "warn"
            advice.append("pull before writing")
        if ahead:
            level = "warn"
            advice.append("push so other machines see it")
    if dirty_reg:
        level = "warn"
        advice.append("commit " + " and ".join(dirty_reg) + " together")
    tree = (f"{len(dirty)} uncommitted tracked file(s)" + (f" including {', '.join(dirty_reg)}" if dirty_reg else "")
            if dirty else "clean tree")
    detail = f"{head} on {branch}; {sync}; {tree}"
    if advice:
        detail += "; " + "; ".join(advice)
    return level, detail


def cmd_version(args) -> int:
    from . import __version__
    info = install_detect(package_dir())
    if getattr(args, "json_out", False):
        print_json({"ok": True, "version": __version__, **info})
    else:
        print(version_line(__version__, info))
    return 0


def doctor_checks(args) -> tuple[list[dict], bool]:
    """Every doctor line as {"level", "item", "detail"}; ok is False when the registry is unreadable or its schema
    version is unsupported. Each check runs on its own: one that raises becomes a `warn <item>: internal
    error ...` line and the others still print."""
    from . import __version__
    from . import index as index_mod
    checks: list[dict] = []

    def add(level, item, detail):
        checks.append({"level": level, "item": item, "detail": detail})

    def guarded(item, fn):
        try:
            fn()
        except Exception as e:   # noqa: BLE001 -- doctor diagnoses; it never stops at one broken check
            add("warn", item, f"internal error {type(e).__name__}: {e} (LEAN_PKG_DEBUG=1 lean-pkg doctor "
                              f"for the traceback)")
            if _env_on(DEBUG_ENV):
                traceback.print_exception(type(e), e, e.__traceback__)

    add("ok", "version", f"lean-pkg {__version__}")
    fetch = bool(getattr(args, "fetch", False))
    guarded("install", lambda: _doctor_pair(add, "install", install_check(package_dir(), fetch=fetch)))
    state = {"reg_ok": True, "data": None}
    reg_p, source = registry_source(args)

    def registry_check():
        try:
            data = load_registry(reg_p, check_version=False)   # doctor reports an unsupported version itself
            ver = data.get("version")
            level = "ok" if ver in SUPPORTED_REGISTRY_VERSIONS else "warn"
            state["reg_ok"] = level == "ok"
            state["data"] = data
            add(level, "registry", f"{reg_p} (schema version {ver}, {len(data['packages'])} package(s))"
                + ("" if state["reg_ok"] else
                   f"; unsupported, supported: {', '.join(map(str, SUPPORTED_REGISTRY_VERSIONS))}")
                + f" [source: {source}]")
        except (Fail, OSError, ValueError) as e:
            state["reg_ok"] = False
            add("warn", "registry", f"{reg_p} unreadable: {e} [source: {source}]")
        checks[-1]["source"] = source

    guarded("registry", registry_check)

    def user_default_check():
        user, detail = user_default_registry()
        add("ok" if user is not None else "n/a", "user-default", detail)

    guarded("user-default", user_default_check)
    ev, ev_source = events_source(args)

    def events_check():
        add("ok" if ev.is_file() else "n/a", "events",
            f"{ev}" + ("" if ev.is_file() else " (not created yet)") + f" [source: {ev_source}]")
        checks[-1]["source"] = ev_source

    guarded("events", events_check)
    guarded("registry-repo", lambda: _doctor_pair(add, "registry-repo", registry_repo_check(reg_p, ev, fetch=fetch)))
    if state["reg_ok"] and state["data"] is not None:
        guarded("leases", lambda: _doctor_pair(add, "leases",
                                               leases_summary(state["data"], dt.datetime.now(dt.timezone.utc))))

    def time_check():
        naive = naive_timestamps(state["data"])
        add("warn" if naive else "ok", "time",
            f"{naive} naive timestamp(s) in leases/history (legacy; treated as UTC)" if naive
            else "every lease/history timestamp carries an offset")

    if state["reg_ok"] and state["data"] is not None:
        guarded("time", time_check)

    def index_check():
        root, isource = index_mod.index_source(args)
        if root.is_dir():
            add("ok", "index", f"{root} ({len(index_mod.entry_names(root))} entries) [source: {isource}]")
        else:
            add("n/a", "index", f"{root} (not present) [source: {isource}]")
        checks[-1]["source"] = isource

    guarded("index", index_check)
    def host_check():
        from .comparator_receipt import host_checks
        res = host_checks()
        failing = [c["item"] for c in res["checks"] if c["level"] == "fail"]
        leaked = res.get("env_warnings") or []
        detail = (res["verdict"] if res["can"] else
                  f"this host cannot produce comparator receipts (failing: {', '.join(failing)}); "
                  f"details: lean-pkg comparator host-check")
        if leaked:   # #27
            detail += f"; {', '.join(leaked)} set in the calling environment (unset them in the launcher)"
        add("ok" if res["can"] and not leaked else "warn", "host", detail)

    guarded("host", host_check)
    for t in DOCTOR_TOOLS:
        guarded(f"tool {t}", lambda t=t: _doctor_tool(add, t))
        if t == "gh":
            guarded("gh auth", lambda: _doctor_pair(add, "gh auth", gh_auth_check()))
    add("ok", "python", f"{sys.version.split()[0]} ({sys.executable})")
    return checks, state["reg_ok"]


def _doctor_pair(add, item: str, pair: tuple[str, str]) -> None:
    add(pair[0], item, pair[1])


def _doctor_tool(add, t: str) -> None:
    where = shutil.which(t)
    if where and t in DOCTOR_ELAN_TOOLS and not is_elan_proxy(where, t):
        add("warn", f"tool {t}", f"{where} is not elan's ({elan_bin() / t}); a {t} outside elan ignores "
            f"each project's lean-toolchain: put {elan_bin()} first on PATH")
    elif where:
        add("ok", f"tool {t}", where + _doctor_bwrap_note(t))
    elif t in DOCTOR_REQUIRED_TOOLS:
        add("warn", f"tool {t}", "not on PATH")
    else:
        hint = DOCTOR_TOOL_HINTS.get(t)
        add("n/a", f"tool {t}", "not on PATH" + (f" ({hint})" if hint else "") + _doctor_bwrap_note(t))


def _doctor_bwrap_note(t: str) -> str:
    """The comparator line's pointer when bwrap is missing: the toolchain mechanism needs it (#32)."""
    if t != "comparator" or shutil.which("bwrap"):
        return ""
    return "; bwrap is missing: run `lean-pkg comparator host-check` to see whether this host can produce receipts"


def cmd_doctor(args) -> int:
    checks, ok = doctor_checks(args)
    if args.json_out:
        print_json({"ok": ok, "checks": checks})
    else:
        for c in checks:
            print(f"{c['level']} {c['item']}: {c['detail']}")
    return 0 if ok else 1


def cmd_validate(args) -> int:
    reg_path = registry_path(args)
    try:
        data = yaml.safe_load(reg_path.read_text())
    except FileNotFoundError:
        raise Fail(registry_not_found(reg_path))
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}")
    errors = validate_registry(data)
    if args.json_out:
        print_json({"ok": not errors, "valid": not errors, "errors": errors,
                    "registry": str(reg_path)})
    elif not errors:
        print("\nVALID")
    else:   # #44: the verdict before the list and again last
        print(f"INVALID ({len(errors)} error(s))")
        for e in errors:
            print(f"  FAIL {e}")
        print(f"\nINVALID ({len(errors)} error(s))")
    return 1 if errors else 0


class _Parser(argparse.ArgumentParser):
    """argparse with the UsageError JSON contract (#21.7): when --json is on the command line, an
    argparse-level error (unknown flag, bad choice, missing argument) also prints
    {ok: false, error, usage: true} on stdout; the stderr usage text and exit 2 are unchanged.
    Subparsers inherit the class."""
    json_requested = False
    alias_root = False   # the root parser: rewrite old spellings to canonical ones first (command_tree)
    alias_hits: list = []

    def parse_known_args(self, args=None, namespace=None):
        if self.alias_root:
            from . import command_tree
            args, self.alias_hits = command_tree.rewrite_argv(list(sys.argv[1:] if args is None else args))
        return super().parse_known_args(args, namespace)

    def error(self, message):
        if _Parser.json_requested:
            print_json({"ok": False, "error": f"{self.prog}: {message}", "usage": True})
        super().error(message)


def _hidden_copies(target, *sources) -> None:
    """Give target every option of sources it lacks, hidden from its help (help=SUPPRESS). A form split out
    of one parser (`comparator config`, `formalization anchor`) then parses any old argv exactly as before:
    a flag of another form still reaches the command, which refuses it with the message naming the right
    form, as it always did."""
    import copy
    for src in sources:
        for a in src._actions:
            if not a.option_strings or isinstance(a, argparse._HelpAction):
                continue
            if any(o in target._option_string_actions for o in a.option_strings) \
                    or any(b.dest == a.dest for b in target._actions):
                continue
            b = copy.copy(a)
            b.option_strings = list(a.option_strings)
            b.help = argparse.SUPPRESS
            target._add_action(b)
        target.set_defaults(**{k: v for k, v in _defaults_except(src, target).items()})


def _defaults_except(source, target) -> dict:
    """source's argument defaults for every dest target does not define: a form split out of a command parses
    to the namespace that command produced (`repo move` = `update --path`, `comparator config` = the old
    keyword form)."""
    own = {a.dest for a in target._actions}
    out = {}
    for a in source._actions:
        if a.dest in own or a.dest in out or a.default is argparse.SUPPRESS or isinstance(a, argparse._HelpAction):
            continue
        out[a.dest] = a.default
    return out


def main(argv: list[str] | None = None) -> int:
    _ACTOR["agent"] = None   # #21.3: per invocation
    raw = list(sys.argv[1:] if argv is None else argv)
    _Parser.json_requested = "--json" in (raw[:raw.index("--")] if "--" in raw else raw)
    ap = _Parser(
        prog="lean-pkg",
        description="Lean 4 package registry: board, leases, trust gate, "
                    "promotion",
        epilog="New here? `lean-pkg help start`: the five verbs (new, verify, status, page, publish) and the two "
               "product tracks. Old spellings (claim, init, audit, ...) keep working: `lean-pkg help aliases`.")
    ap.add_argument("--registry", default=None,
                    help=f"registry yaml (default: ${REGISTRY_ENV}, else "
                         f"~/.lean-pkg/registry, else ./{DEFAULT_REGISTRY_NAME})")
    ap.add_argument("--events", default=None,
                    help=f"event log jsonl (default: {EVENTS_NAME} next to the "
                         f"registry)")
    ap.add_argument("--json", dest="json_out", action="store_true",
                    help="machine-readable JSON on stdout (exit codes unchanged)")
    ap.add_argument("--commit", action="store_true",
                    help=f"write commands: pull --ff-only first, then commit the registry and event log "
                         f"together (default: ${COMMIT_ENV}=1)")
    ap.add_argument("--push", action="store_true",
                    help=f"--commit, then push to origin (default: ${PUSH_ENV}=1)")
    ap.add_argument("--no-commit", action="store_true",
                    help=f"never commit or push, overriding ${COMMIT_ENV} and ${PUSH_ENV}")
    ap.add_argument("--registry-snapshot", metavar="COMMIT", default=None,
                    help=f"read-only: read the registry file (and its event log) as of COMMIT of the git "
                         f"repository holding it (env ${SNAPSHOT_ENV}); every write exits 2; --json adds "
                         f"registry_snapshot")
    from . import __version__
    ap.add_argument("--version", dest="show_version", action="store_true",
                    help=f"print lean-pkg {__version__} and the build that runs: short commit (+dirty) and "
                         f"checkout of an editable install, else the installed package directory; local "
                         f"only (no fetch); with --json: {{ok, version, commit, dirty, install, path}}")
    sub = ap.add_subparsers(dest="cmd")

    def add_json(s):
        # Repeat --json on every subparser so BOTH `lean-pkg --json list` and
        # `lean-pkg list --json` work. SUPPRESS keeps the global value when
        # the flag is absent after the subcommand.
        s.add_argument("--json", dest="json_out", action="store_true",
                       default=argparse.SUPPRESS,
                       help="machine-readable JSON on stdout")
        s.add_argument("--commit", action="store_true", default=argparse.SUPPRESS,
                       help=f"write commands: pull --ff-only, then commit the registry files (default: "
                            f"${COMMIT_ENV}=1{'; set now: writes commit unless --no-commit' if _env_on(COMMIT_ENV) else ''})")
        s.add_argument("--push", action="store_true", default=argparse.SUPPRESS,
                       help=f"--commit, then push to origin (default: ${PUSH_ENV}=1"
                            f"{'; set now: writes commit and push unless --no-commit' if _env_on(PUSH_ENV) else ''})")
        s.add_argument("--no-commit", dest="no_commit", action="store_true", default=argparse.SUPPRESS,
                       help="never commit or push (overrides the env)")

    only_help = ("filters PACKAGES, never fields: only these packages (comma-separated names), each with its "
                 "full record (cached claims included: --no-claims or --fields shrink it); the same table or --json shape, restricted; an unknown "
                 "name exits 1")
    list_fields_help = ("trims FIELDS, never packages: each package record cut to these keys, comma-separated; "
                        "the same validation, dotted paths and aliases as `status --fields` (gate.receipt, "
                        "lease.agent, lease.expires_at; the table columns as aliases: pin, vis, reaudit, expires; an "
                        "unknown field exits 2 listing the valid keys); --json keeps the dump's shape plus a "
                        "`fields` list, text prints the selected keys of each package as YAML")
    no_claims_help = ("drop the bulk cached arrays (" + ", ".join(BULK_FIELDS) + "; the [bulk] keys below) from "
                      "each package record; every other key unchanged; --json adds an `omitted` list")
    # 1.0 command tree (docs/design/1.0-command-tree.md): coordination and maintenance commands live under
    # nouns; each keeps its pre-1.0 `cmd` (set_defaults) so every predicate below is unchanged, and its old
    # top-level spelling is a permanent alias (command_tree.COMMAND_ALIASES).
    s = sub.add_parser("registry", help="the registry file itself: init, migrate, validate, audit, board, wiki "
                                        "(the old top-level spellings keep working)")
    registry_sub = s.add_subparsers(dest="registry_cmd", required=True,
                                    metavar="{board,init,migrate,validate,audit,wiki}")
    s = registry_sub.add_parser("board", help="curses TUI on a tty; static table otherwise",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=package_fields_help())
    s.add_argument("--only", metavar="NAME[,NAME]", default=None,
                   help=only_help + "; never opens the TUI")
    s.add_argument("--fields", metavar="a,b,c", default=None, help=list_fields_help + "; never opens the TUI")
    s.add_argument("--no-claims", dest="no_claims", action="store_true",
                   help=no_claims_help + "; never opens the TUI")
    add_json(s)
    s.set_defaults(fn=cmd_board, cmd="board")
    s = sub.add_parser("list", help="static table, pipe-friendly",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=package_fields_help())
    s.add_argument("--only", metavar="NAME[,NAME]", default=None, help=only_help)
    s.add_argument("--fields", metavar="a,b,c", default=None, help=list_fields_help)
    s.add_argument("--no-claims", dest="no_claims", action="store_true", help=no_claims_help)
    add_json(s)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("status", help="full record + last 5 history entries + scope (gated commit, privacy, "
                                         "the exact comparator-covered declarations)",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog=package_fields_help())
    s.add_argument("name")
    s.add_argument("--fields", metavar="a,b,c", default=None,
                   help="trims FIELDS: only these keys of the package record, comma-separated; dotted paths "
                        "reach into mappings (gate.receipt, lease.agent, the derived lease.expires_at); aliases "
                        "pin (=mathlib_pin), vis (=visibility), reaudit (=reaudit_after), expires and lease.expires "
                        "(=lease.expires_at), lease.claimed (=lease.claimed_at), output keeps the record key; "
                        "--json keeps the nesting, text prints YAML; an unknown field exits 2 listing the valid keys")
    s.add_argument("--no-claims", dest="no_claims", action="store_true", help=no_claims_help)
    add_json(s)
    s.set_defaults(fn=cmd_status)

    from . import scaffold as scaffold_mod   # `new`: placed beside `init`, the other way to start
    scaffold_mod.add_parsers(sub, add_json)

    s = registry_sub.add_parser("init", help="bootstrap an empty registry and its event log")
    s.add_argument("--force", action="store_true",
                   help="re-create only if the existing registry is empty")
    add_json(s)
    s.set_defaults(fn=cmd_init, cmd="init")

    s = registry_sub.add_parser("migrate", help="upgrade the registry schema in place (additive; idempotent)")
    s.add_argument("--dry-run", action="store_true", help="print the changes; write nothing")
    add_json(s)
    s.set_defaults(fn=cmd_migrate, cmd="migrate")

    s = sub.add_parser("record", help="record a publication evidence event (consent, Palomar, DOI, Reservoir, Mathlib PR)")
    s.add_argument("name")
    s.add_argument("event", help="one of: " + ", ".join(EVIDENCE_EVENTS) + f", or {RETRACT_EVENT} "
                   "(--field event=E --field ts=T --reason TEXT: mark an evidence entry retracted)")
    s.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="event field (repeatable); required keys per event are listed by `lean-pkg llms`")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    s.add_argument("--doi-track", dest="track", choices=TRACKS, default=None,
                   help="doi-minted only: the DOI track for this event (default: the record's track, else palomar); "
                        "doi-only skips rule doi-in-palomar for a public package")
    s.add_argument("--agent", default=None,
                   help="the agent acting (recorded as the event's actor); another agent's live lease then refuses")
    add_json(s)
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("register", help="the ONLY write path for new packages: "
                                        "validated, deduping, auto-detecting")
    s.add_argument("name")
    s.add_argument("--url", dest="repo", metavar="URL", default=None,
                   help="git URL (or local bare path); required unless --like gives it")
    s.add_argument("--path", required=True,
                   help="local checkout path, $HOME-relative")
    s.add_argument("--like", default=None, metavar="OTHER",
                   help="a sibling of registered package OTHER: copy its repo, owner-project, upstream-target "
                        "and automation (flags given here win; visibility is never copied, default private), "
                        "and report whether the toolchain and Mathlib pin match OTHER's")
    s.add_argument("--owner-project", default=None)
    s.add_argument("--visibility", choices=VISIBILITIES, default="private")
    s.add_argument("--status", choices=REGISTER_STATUSES, default="active",
                   help="public only with --visibility public and --reason (an already-public repo)")
    s.add_argument("--upstream-target", choices=UPSTREAM_TARGETS, default=None, help="default none")
    s.add_argument("--automation", default=None)
    s.add_argument("--notes", default="")
    s.add_argument("--reason", default="", help="required with --status public")
    s.add_argument("--workspace", default=None,
                   help="metadata only; does NOT claim")
    s.add_argument("--agent", default=None, help="metadata only; does NOT claim")
    s.add_argument("--ttl", type=int, default=None,
                   help="accepted and ignored (register never claims; claim separately with "
                        "`lean-pkg claim NAME --ttl H`)")
    s.add_argument("--dry-run", action="store_true",
                   help="print the would-be entry as YAML without writing")
    add_json(s)
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("lease", help="leases for agents sharing a registry: claim, renew, release, list (the old "
                                     "spellings claim, renew, release, leases keep working)")
    lease_sub = s.add_subparsers(dest="lease_cmd", required=True, metavar="{claim,renew,release,list}")
    s = lease_sub.add_parser("claim", help="CAS lease on a package (24h default TTL)")
    s.add_argument("name")
    s.add_argument("--workspace", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=None, metavar="HOURS",
                   help=f"lease TTL, 1..{MAX_TTL_HOURS} (default: {DEFAULT_TTL_HOURS} for a new claim; a holder's "
                        f"re-claim keeps the lease's current ttl_hours; above {MAX_TTL_HOURS}: exit 2)")
    s.add_argument("--reason", default="")
    s.add_argument("--contact", default=None, metavar="TEXT",
                   help=f"address of the holding session (e.g. an amsg address such as lab-desk/n14-paper or "
                        f"@lean-ops), stored in the lease and shown by leases/status; one line, at most "
                        f"{LEASE_CONTACT_MAX} characters; a holder's re-claim without it keeps the lease's")
    s.add_argument("--force", action="store_true",
                   help="override a live lease; requires --reason")
    add_json(s)
    s.set_defaults(fn=cmd_claim, cmd="claim")

    s = lease_sub.add_parser("release", help="release a lease (holder-only unless --force); with --fresh-repo DIR: "
                                       "export the gated tree (or --from-tree SRCDIR) as a new single-commit "
                                       "repository and move the package there; with --adopt-repo DIR: adopt "
                                       "an existing one")
    s.add_argument("name")
    s.add_argument("--agent", default=None,
                   help="the holder; with --force, the acting agent recorded as released_by")
    s.add_argument("--force", action="store_true", help="release another agent's lease; requires --reason")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    s.add_argument("--fresh-repo", metavar="DIR", default=None,
                   help="export the gate-bound commit's tree (internal files and the patterns file dropped, "
                        "public-safety scanned) into a NEW single-commit repository at DIR, then relocate the "
                        "package there (update --path): evidence cleared, provenance note added")
    s.add_argument("--remote", metavar="URL", default=None,
                   help="with --fresh-repo or --adopt-repo: git remote add origin URL, and set the record's repo")
    s.add_argument("--keep-receipts", action="store_true",
                   help="with --fresh-repo: copy .lean-receipts/ from the source HEAD (they do not bind to "
                        "the new commit)")
    s.add_argument("--source-commit", metavar="SHA", default=None,
                   help="with --fresh-repo: export this commit instead of the gate-bound one")
    s.add_argument("--from-tree", metavar="SRCDIR", default=None,
                   help="with --fresh-repo: export the working tree at SRCDIR (a hand-cleaned tree, not a "
                        "commit; .gitignore honoured in a checkout) instead of the gate-bound commit")
    s.add_argument("--adopt-repo", metavar="DIR", default=None,
                   help="adopt the existing repository at DIR as the public history (exactly one commit unless "
                        "--allow-history; tree and history public-safety scanned; no new commit), then "
                        "relocate the package there")
    s.add_argument("--allow-history", action="store_true",
                   help="with --adopt-repo: accept a repository with more than one commit")
    s.add_argument("--dry-run", action="store_true",
                   help="with --fresh-repo or --adopt-repo: stage and scan, print the plan; nothing written")
    add_json(s)
    s.set_defaults(fn=cmd_release, cmd="release")

    s = lease_sub.add_parser("renew", help="restart your own live lease from now (holder-only; refuses once it "
                                     "has lapsed: claim again)")
    s.add_argument("name")
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=None, metavar="HOURS",
                   help=f"new TTL, 1..{MAX_TTL_HOURS} (default: the lease's current ttl_hours; above "
                        f"{MAX_TTL_HOURS}: exit 2)")
    s.add_argument("--workspace", default=None, help="move the lease to this workspace")
    s.add_argument("--reason", default="")
    s.add_argument("--contact", default=None, metavar="TEXT",
                   help=f"set or replace the lease's contact (address of the holding session; one line, at most "
                        f"{LEASE_CONTACT_MAX} characters); without it the lease keeps its contact")
    s.add_argument("--no-contact", action="store_true",
                   help="clear the lease's contact while renewing (not with --contact)")
    add_json(s)
    s.set_defaults(fn=cmd_renew, cmd="renew")

    s = lease_sub.add_parser("list", help="held leases, soonest expiry first; read-only")
    s.add_argument("--agent", default=None, help="only leases held by this agent")
    s.add_argument("--expiring", type=nonneg_hours, default=None, metavar="HOURS",
                   help="only leases that lapse within HOURS (or have already lapsed)")
    add_json(s)
    s.set_defaults(fn=cmd_leases, cmd="leases")

    s = sub.add_parser("update", help="set fields of an already-registered "
                                      "package (register is for NEW ones)")
    s.add_argument("name")
    s.add_argument("--automation", help="AI-disclosure note; required by mathlib "
                                        "when upstream_target is mathlib")
    s.add_argument("--notes", metavar="TEXT",
                   help="set the notes; refused when notes exist unless --replace-notes (use --append-note)")
    s.add_argument("--replace-notes", dest="replace_notes", action="store_true",
                   help="with --notes: replace non-empty notes (the old text stays in history as notes_from)")
    s.add_argument("--upstream-target", dest="upstream_target",
                   choices=UPSTREAM_TARGETS)
    s.add_argument("--doi-track", dest="track", choices=TRACKS, default=None,
                   help="the DOI track (needs --reason): doi-only lets a public package record doi-minted "
                        "without a palomar-* event; palomar (the default when absent) keeps rule doi-in-palomar")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--accept-docs-descendants", dest="docs_descendants", action="store_const", const=True,
                   default=None,
                   help="opt in (needs --reason): a receipt bound to commit C is also accepted at a descendant "
                        "of C where no build input under the package directory changed (*.lean, lakefile.*, "
                        "lake-manifest.json, lean-toolchain, --build-inputs globs, the checker's files; never a "
                        "symlink/gitlink change, .gitattributes, .gitmodules, .lfsconfig, claims.yaml, nor those "
                        "attribute files in a directory above a subdirectory package); the gated commit "
                        "stays C (docs/receipts.md); --dry-run supported")
    g.add_argument("--no-accept-docs-descendants", dest="docs_descendants", action="store_const", const=False,
                   help="opt out again (needs --reason): only the evidence-only rule applies")
    s.add_argument("--build-inputs", dest="build_inputs", metavar="GLOB,GLOB",
                   help="extra build inputs for the docs-only rule (needs --reason): comma-separated "
                        "package-relative fnmatch globs (`*` crosses /; a glob without / also matches a "
                        "basename), replacing the recorded list; '' clears it")
    s.add_argument("--reaudit-after", dest="reaudit_after", metavar="ISO8601")
    s.add_argument("--owner-project", dest="owner_project")
    s.add_argument("--url", dest="repo", metavar="URL", help="remote URL (e.g. after publishing a private dep)")
    s.add_argument("--license-spdx", dest="license_spdx", metavar="SPDX",
                   help="the package's license as an SPDX id or expression, e.g. 'MIT OR Apache-2.0' (what the index and Reservoir expect)")
    s.add_argument("--formalization-yaml", dest="formalization_yaml", metavar="REL",
                   help="repository-relative path of the package's formalization.yaml")
    s.add_argument("--headline-config", dest="headline_config", metavar="REL",
                   help="repository-relative path of the headline comparator configuration (the paper's main "
                        "results, fed to formalization.yaml status.main_results); '' clears it (then "
                        "comparator.headline.json applies when tracked)")
    s.add_argument("--paper-arxiv", dest="paper_arxiv", metavar="ID",
                   help="arXiv id of the paper this package verifies (sets paper.arxiv; status defaults to "
                        "preprint)")
    s.add_argument("--paper-status", dest="paper_status", choices=PAPER_STATUSES,
                   help="the verified paper's status; promote refuses while unpublished without an arXiv id")
    s.add_argument("--source-paper-title", dest="source_paper_title", metavar="TITLE",
                   help="title of the formalized paper (source_paper.title; rendered into formalization.yaml "
                        "acknowledgements, #22.6)")
    s.add_argument("--source-paper-author", dest="source_paper_author", action="append", metavar="NAME",
                   help="an author of the formalized paper (repeatable; the list replaces the recorded one). "
                        "Credited in acknowledgements, never in project.authors; no consent needed")
    s.add_argument("--source-paper-arxiv", dest="source_paper_arxiv", metavar="ID",
                   help="arXiv id of the formalized paper (source_paper.arxiv)")
    s.add_argument("--source-paper-doi", dest="source_paper_doi", metavar="DOI",
                   help="DOI of the formalized paper (source_paper.doi)")
    s.add_argument("--ai-tool", dest="ai_tool", action="append", metavar="TOOL",
                   help="an AI tool used (repeatable; the list replaces the recorded one; ai_assistance.tools)")
    s.add_argument("--ai-role", dest="ai_role", action="append", metavar="ROLE",
                   help="what the AI tools did (repeatable; replaces; ai_assistance.roles)")
    s.add_argument("--append-note", dest="append_note", metavar="TEXT",
                   help="append a line `YYYY-MM-DD (AGENT): TEXT` (UTC date, --agent) to notes, keeping what is "
                        "there; --notes replaces instead")
    s.add_argument("--namespace-note", dest="namespace_note", metavar="TEXT",
                   help="record why this package shares a Lean root namespace with another (namespace_notes); "
                        "audit's duplicate-namespace is info once both packages carry a note for it")
    s.add_argument("--namespace", action="append", metavar="NS",
                   help="with --namespace-note: the namespace (repeatable); default every root namespace this "
                        "checkout shares with another registered package")
    s.add_argument("--status", choices=STATUSES,
                   help="manual transitions only; verified-private is gate's "
                        "and public is promote's")
    s.add_argument("--reason", default="", help="required for --status and --force")
    s.add_argument("--redetect", action="store_true",
                   help="re-read lean_toolchain and mathlib_pin from the repo")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--force", action="store_true",
                   help="override a live lease held by another agent; needs --reason")
    s.add_argument("--path", default=None, metavar="HOME-RELATIVE",
                   help="relocate: move local_path to another checkout, keeping name, history "
                        "and claims (needs --reason; event relocated; commit-bound evidence "
                        "not bound to the new HEAD is cleared)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--withdraw-publication", dest="withdraw_publication", action="store_true",
                   help="the owner made a promoted (public) repository private again: record it (needs --reason; "
                        "refused while gh still reports it public). status public -> verified-private (active when "
                        "never gated), visibility private, publication_hold {reason, at, by}; promotion history "
                        "kept; promote refuses while held; gate/regate keep the hold; --dry-run supported")
    g.add_argument("--release-publication-hold", dest="release_hold", action="store_true",
                   help="lift a publication hold (needs --reason); status and visibility unchanged; a later "
                        "public flip still goes through promote")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="with --path, --withdraw-publication or --release-publication-hold: print the would-be "
                        "changes, write nothing")
    add_json(s)
    s.set_defaults(fn=cmd_update)
    update_parser = s

    s = sub.add_parser("repo", help="the package's checkout: move (relocate the record to another checkout)")
    repo_sub = s.add_subparsers(dest="repo_cmd", required=True, metavar="{move}")
    s = repo_sub.add_parser("move", help="relocate: point the record at another checkout, keeping name, history "
                                         "and claims (the same as `update NAME --path PATH`)")
    s.add_argument("name")
    s.add_argument("path", metavar="HOME-RELATIVE",
                   help="the new checkout (event relocated; commit-bound evidence not bound to the new HEAD is "
                        "cleared)")
    s.add_argument("--reason", default="", help="required")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--force", action="store_true",
                   help="override a live lease held by another agent; needs --reason")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="print the would-be changes, write nothing")
    add_json(s)
    s.set_defaults(**_defaults_except(update_parser, s), fn=cmd_update, cmd="update")

    s = sub.add_parser("gate", help="validate a lower-tier receipt; "
                                    "gate-pending -> verified-private; records the receipt's "
                                    "lean_toolchain and mathlib_rev as the package's pins")
    s.add_argument("name")
    s.add_argument("--receipt", required=True, help="path to receipt JSON")
    s.add_argument("--comparator", default=None, metavar="PATH",
                   help="optional comparator receipt JSON; "
                        "required later by promote")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when a receipt holds private strings; "
                        "default is a WARN line and the receipt is kept")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN "
                        "line (receipts are always checked against the bound commit)")
    s.add_argument("--refresh-formalization", action="store_true",
                   help="after the gate, regenerate the package's formalization.yaml (derived keys, "
                        "including status.evidence; kept keys survive) and set its registry pointer; "
                        "refuses when nothing would change")
    s.add_argument("--agent", default=None,
                   help="the agent acting (recorded as the event's actor); another agent's live lease then "
                        "refuses; with --refresh-formalization: the live lease holder (the file write needs it)")
    s.add_argument("--check", action="store_true",
                   help="validate only, against HEAD (with --comparator: both tiers); write nothing, record "
                        "nothing (works at any status), like `comparator --check`")
    s.add_argument("--regate", action="store_true",
                   help="re-gate a verified-private (or public) package on a new receipt in one step: event "
                        "regated {previous_commit, commit, job_id, reason}, status unchanged; the receipt's "
                        "commit must descend from the gated commit unless --reason is given")
    s.add_argument("--reason", default=None, metavar="TEXT",
                   help="with --regate: accept a receipt commit that does not descend from the gated commit "
                        "(recorded with non_descendant: true)")
    s.add_argument("--repo", default=None, metavar="DIR",
                   help="with --check only: validate against the checkout DIR instead of the registered "
                        "local_path, for this call (never saved; JSON repo_override). DIR must be a git work "
                        "tree, and when it has remotes one must be the package's repo. A writing gate refuses "
                        "it: it binds the registered checkout (move that with `update --path`)")
    add_json(s)
    s.set_defaults(fn=cmd_gate)

    # `comparator attach|config|host-check`: real subparsers, each with only its flags (config and host-check
    # were values of a positional before 1.0). Each sets `name` as the positional did and the other forms' flags
    # to their defaults, so cmd_comparator sees the namespace it always saw; `comparator NAME ...` is
    # `comparator attach NAME ...` (command_tree.IMPLIED_VERBS).
    s = sub.add_parser("comparator", help="comparator receipts: attach (or --check) one to a gated package; "
                                          "config (write a configuration from claims); host-check")
    comparator_sub = s.add_subparsers(dest="comparator_cmd", required=True, metavar="{attach,config,host-check}")
    s = comparator_sub.add_parser("attach", help="attach a comparator receipt to a gated package; no status change "
                                                 "(`comparator NAME --receipt R` is the same)")
    s.add_argument("name")
    s.add_argument("--receipt", default=None, help="path to comparator receipt JSON (required)")
    s.add_argument("--check", action="store_true",
                   help="validate only; record nothing (works at any status)")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when the receipt holds private strings; "
                        "default is a WARN line and the receipt is kept")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN line")
    s.add_argument("--replace", action="store_true",
                   help="when .lean-receipts/ already holds a different comparator receipt for the same commit: "
                        "keep it as <c12>-comparator.replaced-<UTC stamp>.json, attach the new one, record "
                        "comparator-replaced")
    s.add_argument("--agent", default=None, help="the agent acting (recorded as the event's actor; another "
                                                  "agent's live lease then refuses)")
    s.add_argument("--repo", default=None, metavar="DIR",
                   help="with --check only: validate against the checkout DIR instead of the registered "
                        "local_path, for this call (never saved; JSON repo_override). DIR must be a git work "
                        "tree, and when it has remotes one must be the package's repo. Attaching refuses it: "
                        "it binds the registered checkout (move that with `repo move`)")
    add_json(s)
    attach = s
    s = comparator_sub.add_parser("config", help="write a comparator configuration from claims (--from-claims), "
                                                 "report its coverage (--coverage), or move inline sign-off keys "
                                                 "(--migrate-signoff)")
    s.add_argument("--from-claims", default=None, metavar="NAME",
                   help="add a TODO theorem_names entry per synced claim (theorem/lemma) no comparator "
                        "configuration covers yet; never overwrites")
    s.add_argument("--coverage", default=None, metavar="NAME",
                   help="read-only; claims covered by the comparator config tracked at the gate-bound commit, "
                        "and why a changed config needs a new gate")
    s.add_argument("--migrate-signoff", dest="migrate_signoff", default=None, metavar="PATH",
                   help="move the deprecated inline judged_as / lean_pkg_scaffold keys of that configuration into "
                        "lean-pkg-signoff.json (dry run unless --write); the Palomar keys are unchanged, the file's "
                        "bytes (and so its receipt hash) change")
    s.add_argument("--write", action="store_true", help="with --migrate-signoff: write both files")
    s.add_argument("--out", default=None, help="the configuration to extend or create (default comparator.json)")
    s.add_argument("--dry-run", action="store_true", help="print the would-be JSON; write nothing")
    s.add_argument("--challenge-namespace", default=None,
                   help="namespace of the new Challenge names (default <Package>Audit)")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--challenge-from-type", action="store_true",
                   help="pre-fill each new Challenge/Solution from the declaration's elaborated "
                        "type (runs `lake env lean` in the checkout)")
    s.add_argument("--emit-script", default=None, metavar="F",
                   help="write the type driver to F (run it elsewhere: cd CHECKOUT && bash F OUT.json); "
                        "no Lean runs here")
    s.add_argument("--from-output", default=None, metavar="F",
                   help="fill the scaffold from the driver's comparator-types/1 JSON F")
    s.add_argument("--repo", dest="checkout", default=None, metavar="DIR",
                   help="with --from-claims: use this package checkout instead of the registry's "
                        "local_path (HEAD must be the claims-synced commit or an evidence-only descendant)")
    s.add_argument("--claims-file", dest="claims_file", default=None, metavar="PATH",
                   help="with --from-claims: read the claims from this working-copy claims.yaml instead of "
                        "the registry's synced cache (no `claims sync` needed); schema-checked exactly like "
                        "`claims check --file`; never written to the registry; the output names the file and "
                        "its sha256")
    s.add_argument("--no-registry", action="store_true",
                   help="with --from-output/--challenge-from-type and --out FILE: scratch mode; write "
                        "the merged config to FILE outside the checkout, lease not consulted")
    s.add_argument("--allow-partial", action="store_true",
                   help="with --from-output/--challenge-from-type: ingest a run with extraction failures "
                        "(the failed entries keep TODO, restatement_reason extraction_failed)")
    s.add_argument("--lake-env", default=None, metavar="DIR",
                   help="with the type flags: Lake directory inside the checkout (default: the checkout)")
    s.add_argument("--jobs", type=int, default=None, help="with the type flags: parallel Lean processes (default 2, at most the CPUs; halved and retried on too many open files)")
    s.add_argument("--timeout", type=int, default=None,
                   help="with the type flags: seconds per declaration (default 120)")
    add_json(s)
    config = s
    s = comparator_sub.add_parser("host-check", help="can this host run the comparator; read-only")
    s.add_argument("--routes", action="store_true",
                   help="per route (judge = `lake comparator` in bwrap, prepare = setup "
                        "stages, transfer = build here, judge elsewhere) supported yes/no with the reason, "
                        "executables (type -P), locale, open-file limits, CPUs, cgroup memory and a command "
                        "line per supported route; read-only; exit 0 when any route is supported")
    add_json(s)
    host = s
    for p, name in ((attach, None), (config, CONFIG_CMD), (host, HOST_CHECK)):
        _hidden_copies(p, *(q for q in (attach, config, host) if q is not p))
        p.set_defaults(fn=cmd_comparator, **({"name": name} if name else {}))

    s = sub.add_parser("promote", help="promotion checklist; DRY-RUN unless --yes")
    s.add_argument("name")
    s.add_argument("--yes", action="store_true",
                   help="execute: gh repo edit --visibility public (ONE-WAY); first fetches origin's branches, "
                        "tags and pull-request heads so the public-safety scan covers them (a failed fetch refuses)")
    s.add_argument("--skip-public-safety", action="store_true",
                   help="do not run the public-safety scan of the tree and history (a WARN line instead; the scan "
                        "covers the whole repository when the package is a subdirectory of it)")
    s.add_argument("--agent", default=None,
                   help="with --yes: the agent acting (recorded as the promoted event's actor); another agent's "
                        "live lease then refuses")
    add_json(s)
    s.set_defaults(fn=cmd_promote)

    s = registry_sub.add_parser("audit", help="report expired leases, gh visibility, drift; read-only without --write")
    s.add_argument("--write", dest="fix", action="store_true",
                   help="clear expired leases and update visibility/archive state from gh")
    s.add_argument("--toolchain-floor", default=None, metavar="VERSION",
                   help=f"oldest acceptable lean_toolchain (default: ${TOOLCHAIN_FLOOR_ENV}, "
                        f"else {DEFAULT_TOOLCHAIN_FLOOR})")
    s.add_argument("--promotion", action="store_true",
                   help="judge every package against the toolchain floor as for promote (warn, not info)")
    s.add_argument("--no-gh", action="store_true",
                   help="skip the GitHub (gh) checks: visibility and archived state are not compared")
    s.add_argument("--quiet", action="store_true",
                   help="only the count line (\"N finding(s): W warn, I info\", or \"no findings\"); no "
                        "per-finding line (--json is unchanged)")
    add_json(s)
    s.set_defaults(fn=cmd_audit, cmd="audit")

    s = registry_sub.add_parser("validate", help="schema + enum + history sanity; exit 0/1")
    add_json(s)
    s.set_defaults(fn=cmd_validate, cmd="validate")

    s = sub.add_parser("doctor", help="installation, registry, index and tool diagnostics; "
                                      "exit 1 when the registry is unreadable or its schema "
                                      "version is unsupported")
    s.add_argument("--fetch", action="store_true",
                   help="git fetch origin in the tool checkout and the registry repo before comparing "
                        "(default: local refs)")
    add_json(s)
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("llms", help="print the agent-oriented usage contract")
    s.add_argument("--short", action="store_true",
                   help="a compact contract (~40 lines) cut from the full one: PURPOSE, STANDING RULES and the first "
                        "line of each subcommand; no flag details, schemas or examples")
    add_json(s)
    s.set_defaults(fn=cmd_llms)

    s = sub.add_parser("help", help="help topics (exit-codes, environment, formatting) or a command's --help",
                       description="Without an argument: the commands and help topics. With a topic "
                                   "(exit-codes, environment, formatting): that topic. With a command "
                                   "(`help status`, `help index add`): exactly its --help. Reads no registry.")
    s.add_argument("words", nargs="*", metavar="TOPIC|COMMAND")
    add_json(s)
    s.set_defaults(fn=cmd_help, help_root=ap)

    s = registry_sub.add_parser("wiki", help="render a Markdown status page")
    s.add_argument("--out", default=None,
                   help="write the Markdown here instead of stdout")
    s.add_argument("--candidates", default=None,
                   help="adoption ledger path (default: lean-adoption-candidates.yaml "
                        "next to the registry)")
    add_json(s)
    s.set_defaults(fn=cmd_wiki, cmd="wiki")

    from . import index as index_mod
    index_mod.add_parsers(sub, add_json)
    from . import claims as claims_mod
    claims_mod.add_parsers(sub, add_json)
    from . import search as search_mod
    search_mod.add_parsers(sub, add_json)
    from . import formalization as formalization_mod
    formalization_mod.add_parsers(sub, add_json)
    from . import publish as publish_mod
    publish_mod.add_parsers(sub, add_json)
    from . import coverage as coverage_mod
    coverage_mod.add_parsers(sub, add_json)
    from . import page as page_mod
    page_mod.add_parsers(sub, add_json)
    from . import reservoir as reservoir_mod
    reservoir_mod.add_parsers(sub, add_json)
    from . import public_safety as public_safety_mod
    public_safety_mod.add_parsers(sub, add_json)
    from . import palomar as palomar_mod
    palomar_mod.add_parsers(sub, add_json)
    from . import next_step as next_mod
    next_mod.add_parsers(sub, add_json)
    from . import report as report_mod
    report_mod.add_parsers(sub, add_json)
    from . import receipt as receipt_mod
    receipt_mod.add_parsers(sub, add_json)

    from . import command_tree
    command_tree.apply_flag_aliases(ap)
    command_tree.register_root(ap)
    ap.alias_root = True   # old spellings are rewritten to canonical ones before parsing
    ap.set_defaults(fn=cmd_board)
    args = ap.parse_args(argv)
    notes = alias_notes(args, raw, ap.alias_hits)
    args.alias_note_lines = notes   # run_command prints them before its NOT COMMITTED line (always the last)
    try:
        if getattr(args, "show_version", False):   # before the registry lock: --version reads no registry
            return cmd_version(args)
        if getattr(args, "cmd", None) == "help":   # reads no registry: never a snapshot, never a lock
            return run_command(args)
        if getattr(args, "cmd", None) is None and bare_shows_help(args):   # decision 5 of the 1.0 command tree
            print(command_tree.newcomer_text(ap), end="")
            return 0
        snapshot = registry_snapshot_arg(args)
        if snapshot:   # #34: a historical registry, read-only; no lock (nothing is written)
            return run_snapshot(args, snapshot)
        try:
            with write_lock(args):
                return run_command(args)
        except Fail as e:   # the lock itself could not be taken
            _report_fail(args, e)
            return 1
        except OSError as e:   # init could not create the registry directory for its lock
            _report_fail(args, Fail(f"cannot prepare the registry lock: {e}"))
            return 1
    finally:   # after the command: its own first stderr line (a refusal) stays first
        flush_alias_notes(args)


def alias_notes(args, raw: list[str], hits: list) -> list[str]:
    """The stderr lines main prints after the command, one per old spelling used (text mode only; LEAN_PKG_NO_ALIAS_NOTE=1 silences it). Old
    spellings are permanent aliases: the note never changes output, JSON or the exit code."""
    from . import command_tree
    if getattr(args, "json_out", False) or not command_tree.notes_enabled():
        return []
    hits = list(hits) + command_tree.flag_hits(raw, command_tree.canonical_command(args))
    return command_tree.note_lines(hits)


def flush_alias_notes(args) -> None:
    for line in getattr(args, "alias_note_lines", None) or []:
        print(line, file=sys.stderr)
    args.alias_note_lines = []


def bare_shows_help(args) -> bool:
    """`lean-pkg` with no command: the newcomer page when stdout is not a terminal or no registry exists;
    otherwise the board, as before 1.0. --json still prints the board's JSON."""
    if getattr(args, "json_out", False):
        return False
    if not board_interactive():
        return True
    try:
        return not registry_path(args).is_file()
    except (Fail, OSError, ValueError):
        return True


def _report_fail(args, e: Fail) -> None:
    if getattr(args, "json_out", False):
        print_json({"ok": False, "error": str(e)})
    print(f"lean-pkg: {e}", file=sys.stderr)


def board_interactive() -> bool:
    """`board` opens the curses TUI (which can write) only on a tty; otherwise it prints the table."""
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


# Writes that append an event (or run the TUI) without holding the registry lock for the whole command:
# they lock just around each write (they may use the network, or wait on the keyboard).
UNLOCKED_WRITES = ("index", "search", "board", None)


def is_write_invocation(args) -> bool:
    """Does this invocation, as parsed, write the registry or its event log? The one predicate behind the
    registry lock (locks_registry) and committed mode (commit_mode). Dry runs and read-only forms are not
    writes: register/update/migrate --dry-run, promote without --yes, audit without --fix, gate --check, comparator
    --check, formalization without --write or with --no-registry (and `formalization anchor`), claims list/check, index check/render/migrate, search without
    --record, board off a tty, with --json or with --only. `page` (even --write: README.md only) and `coverage check`
    never write the registry."""
    cmd = getattr(args, "cmd", None)
    if cmd == "gate":
        return not getattr(args, "check", False)
    if cmd in ("init", "record", "register", "claim", "release", "renew", "update", "migrate"):
        return not getattr(args, "dry_run", False)
    if cmd == "comparator":
        return not getattr(args, "check", False) and not is_host_check(args) \
            and not is_comparator_config(args)
    if cmd == "promote":
        return bool(getattr(args, "yes", False))
    if cmd == "audit":
        return bool(getattr(args, "fix", False))
    if cmd == "formalization":   # --write --no-registry writes only the package's file (#22.8); anchor never
        return bool(getattr(args, "write", False)) and not getattr(args, "no_registry", False) \
            and getattr(args, "name", None) != "anchor"
    if cmd == "claims":
        return getattr(getattr(args, "claims_fn", None), "__name__", "") == "run_sync"
    if cmd == "index":
        return getattr(getattr(args, "index_fn", None), "__name__", "") == "run_add"
    if cmd == "search":
        return bool(getattr(args, "record", False))
    if cmd == "palomar":   # prepare records palomar-prepared (#22.3); --dry-run writes nothing
        return getattr(args, "palomar_fn", None) == "prepare" and not getattr(args, "dry_run", False)
    if cmd in ("board", None):
        return getattr(args, "fn", None) is cmd_board and not static_view(args) and board_interactive()
    return False


def locks_registry(args) -> bool:
    """Does this invocation hold the registry lock for the whole command (load -> check -> mutate -> save
    -> append), commit preflight and commit included? Every write except UNLOCKED_WRITES (`index add` and
    `search --record` lock just around their append; the TUI around each key's write)."""
    return is_write_invocation(args) and getattr(args, "cmd", None) not in UNLOCKED_WRITES


def write_lock(args):
    """registry_lock(registry) when this invocation writes and the registry exists (init: its directory
    is created first); otherwise a no-op context."""
    if not locks_registry(args):
        return contextlib.nullcontext()
    reg_p = registry_path(args)
    if getattr(args, "cmd", None) == "init":
        reg_p.parent.mkdir(parents=True, exist_ok=True)
    elif not reg_p.is_file():
        return contextlib.nullcontext()   # the command reports "registry not found" itself
    return registry_lock(reg_p)


DEBUG_ENV = "LEAN_PKG_DEBUG"


def _registry_lock_if_present(args):
    reg_p = registry_path(args)
    return registry_lock(reg_p) if reg_p.is_file() else contextlib.nullcontext()


def _oserror_fail(e: OSError) -> Fail:
    where = f" {e.filename}" if getattr(e, "filename", None) else ""
    return Fail(f"cannot access{where}: {e.strerror or e}")


def _emit_with_commit(text: str, ctx: dict | None, rc_before: int, rc: int,
                      uncommitted: list[str] | None = None) -> None:
    """Re-emit a --json command's captured stdout; in committed mode a single JSON object gains
    "registry_commit": {"sha", "pushed", "error", "push_error", "push_attempts", "rebased"} (and ok: false when the
    commit or push failed). The same object also goes under "commit" when the payload has no "commit" of its
    own (the pre-0.7.30 key, kept for compatibility); a command's own "commit" (gate's source commit) is never
    overwritten."""
    if uncommitted:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            payload["registry_uncommitted"] = list(uncommitted)
            print_json(payload)
            return
    if ctx is not None and "result" in ctx:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            payload["registry_commit"] = dict(ctx["result"])
            payload.setdefault("commit", dict(ctx["result"]))
            if rc_before == 0 and rc != 0:
                payload["ok"] = False
            print_json(payload)
            return
    sys.stdout.write(text)


def run_command(args) -> int:
    """Run the subcommand; refusals map to exit 2/1; an OSError is a clean refusal (exit 1); an unexpected
    exception still lets commit_finish report (and not commit), then exits 1 with a one-line internal
    error (traceback: LEAN_PKG_DEBUG=1). Committed mode: the preflight (fetch, pull --ff-only) runs only
    for an invocation that writes, after argparse has validated it; with --json the command's stdout is
    captured so the payload can carry the commit outcome."""
    note_ignored_commit_flags(args)
    mode = commit_mode(args)
    ctx = None
    crash = None
    writes = is_write_invocation(args)
    buf = io.StringIO() if ((mode or writes) and getattr(args, "json_out", False)) else None
    with contextlib.redirect_stdout(buf) if buf is not None else contextlib.nullcontext():
        try:
            if mode:
                with _registry_lock_if_present(args):
                    ctx = commit_preflight(args, mode)
                args.commit_ctx = ctx   # the TUI commits after each of its writes
            lease_warning(args)
            rc = args.fn(args)
        except UsageError as e:
            if getattr(args, "json_out", False):
                print_json({"ok": False, "error": str(e), "usage": True})
            print(f"lean-pkg: {e}", file=sys.stderr)
            rc = 2
        except Fail as e:
            _report_fail(args, e)
            rc = 1
        except OSError as e:   # an unwritable path, a missing file: a refusal, not an internal error
            _report_fail(args, _oserror_fail(e))
            rc = 1
        except Exception as e:   # noqa: BLE001 -- the top-level handler: report, never a bare traceback
            crash = e
            rc = 1
    rc_before = rc
    if ctx is not None:
        with _registry_lock_if_present(args):
            rc = commit_finish(ctx, rc)
    flush_alias_notes(args)
    uncommitted = uncommitted_notice(args) if (mode is None and writes and not registry_snapshot_arg(args)) else None
    if buf is not None:
        _emit_with_commit(buf.getvalue(), ctx, rc_before, rc, uncommitted)
    if crash is not None:
        if _env_on(DEBUG_ENV):
            traceback.print_exception(type(crash), crash, crash.__traceback__)
        msg = f"internal error: {type(crash).__name__}: {crash}"
        if getattr(args, "json_out", False):
            print_json({"ok": False, "error": msg})
        print(f"lean-pkg: {msg}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
