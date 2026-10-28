# lean-pkg core -- the shared library core under the command line (cli.py)
"""The shared core of lean-pkg: what every command module builds on.

`Fail` and `UsageError`, the registry constants and the package-record schema, time policy
(now_iso, parse_ts), the git and gh helpers (git, scrubbed_env, git_show_bytes, bound_tree, ...),
the building blocks of the docs-only rule (docs_build_input, nested_exclusions, ...), registry IO
(resolution, load, lock, save), the event log (one line per event, EVENT_KEYS) and the pure
lease-time helpers.

It never imports cli: cli.py and the command modules import it (`from . import core`; a module that
also needs a name still defined in cli.py imports cli under its own name). Its only lean_pkg import is
build_scope, a leaf module, inside the functions that use it. Moved here from cli.py (#317); cli.py
re-exports every name for at least one release, for outside scripts. The supported interface is the
CLI and its --json outputs, not `import lean_pkg` (AGENTS.md).
"""

from __future__ import annotations

import collections
import contextlib
import datetime as dt
import fnmatch
import fcntl
import hashlib
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
import urllib.error
import urllib.request

import yaml


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

# The event-line schema (1.0 command tree, section 6, item 2; docs/formats.md "Event log"). Every line
# carries EVENT_LINE_KEYS; `v` is the line's version, EVENT_LINE_VERSION since 0.22.0, and a line without
# it (written before) is version 0, read exactly as before. A reader that interprets events skips a line
# of a newer (or unreadable) version and says so; it never guesses at it.
EVENT_LINE_VERSION = 1
EVENT_LINE_KEYS = ("ts", "event", "name", "v")
# Events written only to the log (no history[] entry, no `actor` unless listed); every other event is a
# package event written by record(), which may also carry `actor` (the --agent of the invocation, #21.3).
LOG_ONLY_EVENTS = ("registry-created", "registry-migrated", "index-added", "prior-art-searched")
# `updated` carries <field>_from (the old value) for each field it changed: these fields.
UPDATED_FROM_FIELDS = ("lean_toolchain", "mathlib_pin", "status")   # plus every UPDATABLE field
_GATE_EVENT_KEYS = ("commit", "job_id", "checker", "receipt", "descendant_of", "docs_paths", "opt_in",
                    "comparator", "comparator_cleared", "status_unchanged", "pins")
_LEASE_EVENT_KEYS = ("agent", "workspace", "ttl_hours", "reason", "contact")
# Every event name lean-pkg writes, with the keys it may carry beyond EVENT_LINE_KEYS. docs/formats.md
# documents the same table and tests/test_formats.py checks both against every emitting call in src/.
EVENT_KEYS: dict[str, tuple[str, ...]] = {
    "registry-created": ("registry", "version", "forced"),
    "registry-migrated": ("from_version", "to_version", "changes"),
    "registered": ("repo", "local_path", "status", "visibility", "lean_toolchain", "mathlib_pin", "workspace",
                   "agent", "reason", "like", "license_spdx"),
    "updated": ("fields", "reason", "forced", "notes_replaced", "<field>_from"),
    "claimed": _LEASE_EVENT_KEYS,
    "claim-forced": _LEASE_EVENT_KEYS,
    "claim-refreshed": ("agent", "workspace", "ttl_hours", "reason", "contact", "contact_cleared"),
    "released": ("agent", "workspace", "released_by", "reason"),
    "release-forced": ("agent", "workspace", "released_by", "reason"),
    "lease-expired": ("agent", "workspace", "claimed_at", "ttl_hours"),
    "gate-passed": _GATE_EVENT_KEYS,
    "regated": ("previous_commit", *_GATE_EVENT_KEYS, "reason", "non_descendant"),
    "comparator-attached": ("commit", "comparator", "host", "job_id", "descendant_of", "docs_paths", "opt_in"),
    "comparator-replaced": ("commit", "comparator", "host", "job_id", "replaced", "descendant_of", "docs_paths",
                            "opt_in"),
    "promoted": ("commit", "visibility"),
    "publication-withdrawn": ("status_from", "status_to", "visibility_from", "visibility_to", "reason", "by",
                              "github_visibility", "promoted_commit", "forced"),
    "publication-hold-released": ("reason", "hold", "by", "forced"),
    "audit-fix": ("field", "old", "new", "source"),
    "relocated": ("old_path", "new_path", "reason", "cleared", "agent", "redetected"),
    "status-changed": ("status_from", "status_to", "reason"),
    "palomar-prepared": ("commit", "bundle", "bundle_sha256"),
    "claims-synced": ("commit", "file", "claims"),
    "index-added": ("repo", "commit", "trust", "index"),
    "prior-art-searched": ("term", "sources", "hits", "record", "decision", "actor"),
    "evidence-retracted": ("retracted_event", "retracted_ts", "retracted_entry", "reason"),
    **{e: (*keys, "reason", *(("concept", "track") if e == "doi-minted" else ()))
       for e, keys in EVIDENCE_EVENTS.items()},
}


def event_line_version(line: dict) -> int | None:
    """The version of one parsed event line: 0 without `v` (written before 0.22.0), the integer `v`
    otherwise; None when `v` is not a non-negative integer (unreadable, never guessed at)."""
    if "v" not in line:
        return 0
    v = line["v"]
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def event_line_problems(line: dict) -> list[str]:
    """Why a line lean-pkg writes would not match the documented event-line schema (EVENT_KEYS): an
    undocumented event name, or a key that event does not document. Empty when it matches."""
    event = line.get("event")
    if event not in EVENT_KEYS:
        return [f"event {event!r} is not documented in EVENT_KEYS (docs/formats.md)"]
    allowed = set(EVENT_LINE_KEYS) | set(EVENT_KEYS[event])
    if event not in LOG_ONLY_EVENTS:
        allowed.add("actor")
    from_ok = ("<field>_from" in allowed and
               {f"{f}_from" for f in (*UPDATED_FROM_FIELDS, *UPDATABLE)})
    return [f"event {event!r} key {k!r} is not documented in EVENT_KEYS (docs/formats.md)"
            for k in sorted(line) if k not in allowed and not (from_ok and k in from_ok)]


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
                                     "docs/receipts.md), unless lean-pkg-binding.yaml committed at C decides; false "
                                     "vetoes the file's opt-in; absent = false (update --accept-docs-descendants)"),
    ("build_inputs", "optional", "[glob]: extra package-relative paths the docs-only rule treats as build inputs, "
                                 "added to those of lean-pkg-binding.yaml at C (fnmatch; a glob without / also "
                                 "matches a basename) (update --build-inputs)"),
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
# The lower-tier receipt's schema marker (1.0 command tree, section 6, item 1): every writer (receipt slurm,
# receipt systemd/local) puts `schema` first since 0.22.0. A receipt without the key is version 0 and is
# judged exactly as before, forever: receipts are evidence bound to bytes and never rewritten. A newer
# lower-receipt/N is refused (upgrade lean-pkg), as a newer registry version is.
LOWER_RECEIPT_SCHEMAS = ("lower-receipt/1",)
LOWER_RECEIPT_SCHEMA = LOWER_RECEIPT_SCHEMAS[-1]
_LOWER_RECEIPT_SCHEMA_RE = re.compile(r"lower-receipt/([1-9][0-9]*)")
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


# Docs-only descendants (opt-in per package: lean-pkg-binding.yaml committed at the bound commit, or the
# record field docs_descendants, `update NAME --accept-docs-descendants`). Build inputs, matched on
# package-relative paths at any depth.
DOCS_BUILD_BASENAMES = ("lake-manifest.json", "lean-toolchain")
# Changes the docs rule never admits, whatever the record's build_inputs say. lean-pkg-binding.yaml is the
# committed opt-in (binding_file, proposal 0002): read at the bound commit only, so a change to it after that
# commit is refused here and a reader at HEAD sees the very bytes that bound it (or none).
DOCS_ALWAYS_REFUSED = (".gitattributes", ".gitmodules", ".lfsconfig", "claims.yaml", "lean-pkg-binding.yaml")
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
        raise Fail(f"registry is not valid YAML: {e}") from e
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
        raise Fail(f"cannot open the registry lock {lp}: {e.strerror or e}") from e
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
    <lean_pkg_home>/registry "user-default", else ./lean-packages.yaml "cwd". A flag or env value naming a
    directory means its lean-packages.yaml, as the user-level default does."""
    if getattr(args, "registry", None) or os.environ.get(REGISTRY_ENV, "").strip():
        path, src = (pathlib.Path(args.registry).expanduser(), "flag") if getattr(args, "registry", None) \
            else (pathlib.Path(os.environ[REGISTRY_ENV].strip()).expanduser(), "env")
        if path.is_dir():
            path = path / DEFAULT_REGISTRY_NAME
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


def registry_sync_counts(root: pathlib.Path, branch: str) -> tuple[int, int] | None:
    """(behind, ahead) of HEAD against the local ref refs/remotes/origin/BRANCH, as last fetched (this never
    fetches); None when there is no such ref. The one count behind doctor's registry-repo and install lines
    and registry_state."""
    counts = registry_git(root, "rev-list", "--left-right", "--count", f"refs/remotes/origin/{branch}...HEAD")
    if counts.returncode != 0:
        return None
    try:
        behind, ahead = (int(x) for x in counts.stdout.split())
    except ValueError:
        return None
    return behind, ahead


def registry_state(reg_p: pathlib.Path) -> dict | None:
    """The registry commit a reader's lease state comes from (#385), from local refs only (no fetch, no
    network): {root, commit, committed_at, age_seconds, branch, upstream, behind, ahead, dirty}. branch is
    null on a detached HEAD; upstream, behind and ahead are null without a local origin/<branch> ref; dirty
    says the registry file has uncommitted changes (null when git cannot tell). None when git is not on
    PATH, the registry is not inside a git work tree or HEAD has no commit: never an error."""
    if shutil.which("git") is None:
        return None
    reg_p = pathlib.Path(os.path.realpath(pathlib.Path(reg_p).expanduser()))
    if reg_p.is_dir():
        reg_p = reg_p / DEFAULT_REGISTRY_NAME
    if not reg_p.parent.is_dir():
        return None
    top = registry_git(reg_p.parent, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or not top.stdout.strip():
        return None
    root = pathlib.Path(top.stdout.strip())
    log = registry_git(root, "log", "-1", "--format=%H %cI")
    fields = log.stdout.split() if log.returncode == 0 else []
    if len(fields) != 2:
        return None    # an unborn HEAD: nothing committed yet
    sha, committed_at = fields
    try:
        age = int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(committed_at)).total_seconds())
    except ValueError:
        age = None
    br = registry_git(root, "symbolic-ref", "-q", "--short", "HEAD")
    branch = br.stdout.strip() if br.returncode == 0 and br.stdout.strip() else None
    counts = registry_sync_counts(root, branch) if branch else None
    st = registry_git(root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no", "--",
                      str(reg_p))
    return {"root": str(root), "commit": sha, "committed_at": committed_at,
            "age_seconds": None if age is None else max(age, 0), "branch": branch,
            "upstream": f"origin/{branch}" if counts is not None else None,
            "behind": counts[0] if counts is not None else None,
            "ahead": counts[1] if counts is not None else None,
            "dirty": bool(st.stdout.strip()) if st.returncode == 0 else None}


def registry_state_of(args) -> dict | None:
    """registry_state for the invocation's registry; None under --registry-snapshot (the reader named the
    commit itself) and on any internal error (a stamp must never fail the command)."""
    if registry_snapshot_arg(args):
        return None
    try:
        return registry_state(registry_path(args))
    except Exception:   # noqa: BLE001 -- best effort: the stamp is information, never a refusal
        return None


def registry_behind_warning(state: dict | None) -> str | None:
    """The one stderr line a lease reader prints when its registry clone is behind the origin/<branch> it last
    fetched (#385); None when it is not, or cannot be told. Local refs only: a clone that was never fetched
    looks up to date, which is why every lease line also names the commit it was read from."""
    if not state or not state.get("behind"):
        return None
    root = shlex.quote(state["root"])
    return (f"lean-pkg: WARN: registry clone is {state['behind']} commit(s) behind {state['upstream']} as last "
            f"fetched (lease state may be stale): git -C {root} pull --ff-only, or lean-pkg doctor --fetch")


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
        raise Fail(f"--registry-snapshot: cannot run git: {e.strerror or e}") from e
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






def _event_line(ts: str, name, event: str, **fields) -> str:
    """One event-log line: EVENT_LINE_KEYS (`v` last, so no field can replace it) and the event's fields."""
    return json.dumps({"ts": ts, "event": event, "name": name, **fields, "v": EVENT_LINE_VERSION},
                      sort_keys=True) + "\n"


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
# leases: the pure lease-time helpers (the lease commands stay in cli.py)

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


# --------------------------------------------------------------------------
# fields `update` may set (event_line_problems reads them)

# Fields `update` may set. Everything else is owned by a dedicated subcommand
# and is refused by name, so `update` can never become a way around the trust
# gate: `gate`/`promoted_at`/`promoted_commit`/`visibility` belong to `gate`
# and `promote`, and `lease`/`workspace`/`agent` to `claim`/`release`.
UPDATABLE = {"automation", "notes", "upstream_target", "reaudit_after",
             "owner_project", "repo", "license_spdx", "formalization_yaml", "paper", "namespace_notes",
             "source_paper", "ai_assistance", "track", "docs_descendants", "build_inputs", "headline_config"}


# --------------------------------------------------------------------------
# git object helpers (the gate and the docs-only rule read the bound commit through them)

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
