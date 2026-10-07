# lean-pkg -- Lean 4 package registry: board, leases, trust gate, promotion
"""Manage Lean 4 formalization packages from one YAML registry.

The source of truth is a registry YAML file; GitHub (`gh`) is only the
visibility MIRROR. Every state transition appends BOTH a history[] entry in the
registry and one JSON line to the event log -- history is append-only, never
rewritten. See DESIGN.md for the lifecycle.

Registry resolution: --registry PATH, else env LEAN_PKG_REGISTRY, else
./lean-packages.yaml in the current directory. The event log is --events PATH,
else a sibling file <registry dir>/lean-package-events.jsonl.

A package's local_path is resolved RELATIVE to $HOME, so a registry can be
shared between machines that keep checkouts at the same home-relative place.
An absolute local_path is accepted transparently. Mutating operations that
touch the outside world are dry-run by default.

    lean-pkg [--registry PATH] [--events PATH] [--json] [subcommand]

    board     (default) curses TUI on a tty; static aligned table otherwise
    list      static table, pipe-friendly
    status    <name>                        full record + last 5 history entries
    register  <name> --repo URL --path P    the ONLY write path for new packages;
              [--dry-run]                   validated, deduping, auto-detects
                                            toolchain + mathlib pin
    update    <name> [--notes ...] [--status S --reason T]
                                            fields of a registered package
    claim     <name> --workspace W --agent A [--ttl H] [--reason T] [--force]
    release   <name> [--agent A] [--force]
    gate      <name> --receipt PATH         validate a lower-tier receipt, then
              [--comparator PATH]           gate-pending -> verified-private;
                                            optional comparator evidence;
                                            identical re-gate = no-op
    comparator <name> --receipt PATH        attach comparator evidence to a gated
              [--check]                     package; required before promote
    promote   <name> [--yes]                dry-run default; --yes flips GitHub
                                            visibility to public (ONE-WAY)
    audit     [--fix]                       sweep expired leases, reconcile gh
                                            visibility, paths, drift
    validate                                schema + enum + history sanity
    llms                                    agent-oriented usage contract
    wiki      [--out PATH]                  render a Markdown status page

--json emits machine-readable JSON on stdout (exit codes unchanged).
Everything is non-interactive: off a tty, no code path may block on input;
prompt paths refuse with exit 2.

Constraints enforced here:
  - agents never hand-edit the yaml: register is validated and deduping
  - one package = one workspace: CAS lease, 24h default TTL, audit sweeps
  - the gate binds the receipt to HEAD; axioms must be a subset of
    {propext, Quot.sound, Classical.choice}; sorry_count must be 0
  - promotion requires the Reservoir-criteria checklist plus the trust gate
    and a comparator PASS; the GitHub flip is one-way and therefore dry-run
    by default
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

from .holes import check_definition_holes
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
}
_HEX40 = re.compile(r"[0-9a-f]{40}")
RESERVOIR_URL_RE = re.compile(r"^https://reservoir\.lean-lang\.org/@([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")
RESERVOIR_API = "https://reservoir.lean-lang.org/api/v1/packages/{owner}/{name}"


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
UPSTREAM_TARGETS = ["none", "mathlib", "palomar"]
DEFAULT_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]
DEFAULT_TTL_HOURS = 24

REQUIRED_FIELDS = ["name", "repo", "local_path", "visibility", "status",
                   "owner_project", "workspace", "agent", "lease",
                   "lean_toolchain", "mathlib_pin", "upstream_target",
                   "automation", "gate", "promoted_at", "promoted_commit",
                   "reaudit_after", "notes", "history"]
LEASE_FIELDS = ["workspace", "agent", "claimed_at", "ttl_hours", "reason"]
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
LICENSE_NAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt",
                 "COPYING", "COPYING.md", "COPYING.txt"]

BOARD_HEADER = ("NAME", "STATUS", "VIS", "LEASE", "PIN", "REAUDIT")


class Fail(Exception):
    """User-facing refusal: printed to stderr, exit 1."""


class UsageError(Exception):
    """Interactive path attempted off-tty, or other usage-level error: exit 2."""


# --------------------------------------------------------------------------
# small utilities

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_ts(s: str) -> dt.datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    v = dt.datetime.fromisoformat(s)
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """The one subprocess helper. Never raises on nonzero exit."""
    return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=cwd)


def git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(repo), *args])


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


def reference_branch(repo: pathlib.Path) -> tuple[str | None, str | None]:
    """The branch a package's evidence is judged against, and its local tip, from local refs only.
    Order: the remote default branch (refs/remotes/origin/HEAD), else `main`; the tip is
    refs/heads/<b>, else refs/remotes/origin/<b>. With neither, (None, HEAD).
    The registry has no per-package branch field, so none is consulted."""
    candidates = []
    p = git(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if p.returncode == 0 and p.stdout.strip().startswith("refs/remotes/origin/"):
        candidates.append(p.stdout.strip()[len("refs/remotes/origin/"):])
    candidates.append("main")
    for b in candidates:
        tip = _rev(repo, f"refs/heads/{b}") or _rev(repo, f"refs/remotes/origin/{b}")
        if tip:
            return b, tip
    return None, git_head(repo)


def current_branch(repo: pathlib.Path) -> str:
    p = git(repo, "symbolic-ref", "-q", "--short", "HEAD")
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else "detached HEAD"


def evidence_only_descendant(repo: pathlib.Path, bound: str,
                             head: str) -> tuple[bool, str]:
    """Evidence-only descendant rule (see DESIGN.md): a receipt bound to commit `bound` is
    accepted at `head` when head == bound, or when head descends from bound and
    the only paths that differ live under .lean-receipts/ (the evidence itself).
    Returns (ok, note)."""
    if bound == head:
        return True, ""
    p = git(repo, "merge-base", "--is-ancestor", bound, head)
    if p.returncode != 0:
        return False, f"{bound[:12]} is not an ancestor of HEAD {head[:12]}"
    p = git(repo, "diff", "--no-renames", "--name-only", bound, head)   # a rename into .lean-receipts/ is a source change
    paths = [x for x in p.stdout.splitlines() if x.strip()]
    foreign = [x for x in paths if not x.startswith(".lean-receipts/")]
    if foreign or not paths:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} outside "
                       f".lean-receipts/: {foreign[:5]}")
    return True, f"evidence-only descendant of {bound[:12]} (+{len(paths)} evidence file(s))"


def ensure_interactive() -> None:
    """Every prompt path must call this first: off a tty, refuse (exit 2)
    instead of blocking on input. Agents always use flags or --json."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UsageError("interactive mode needs a tty on stdin+stdout; "
                         "use flags or --json instead")


def print_json(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def normalize_local_path(p) -> str:
    """Comparison form for dedupe: collapse `//`, `.`, trailing slashes."""
    return os.path.normpath(str(p).strip())


def detect_toolchain(repo: pathlib.Path) -> tuple[str | None, str | None]:
    """(toolchain, warning) from the first line of <repo>/lean-toolchain."""
    f = repo / "lean-toolchain"
    if not f.is_file():
        return None, f"no lean-toolchain at {f}"
    lines = f.read_text().splitlines()
    if not lines or not lines[0].strip():
        return None, f"empty lean-toolchain at {f}"
    return lines[0].strip(), None


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

def load_registry(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise Fail(f"registry not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("packages"), dict):
        raise Fail(f"registry has no top-level 'packages' map: {path}")
    return data


def save_registry(path: pathlib.Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False,
                                  allow_unicode=True, width=120))
    os.replace(tmp, path)


def registry_path(args) -> pathlib.Path:
    """--registry, else $LEAN_PKG_REGISTRY, else ./lean-packages.yaml."""
    if getattr(args, "registry", None):
        return pathlib.Path(args.registry).expanduser()
    env = os.environ.get(REGISTRY_ENV, "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return pathlib.Path.cwd() / DEFAULT_REGISTRY_NAME


EVENTS_ENV = "LEAN_PKG_EVENTS"


def events_path(args) -> pathlib.Path:
    """--events, else env LEAN_PKG_EVENTS, else a sibling lean-package-events.jsonl of the registry."""
    if getattr(args, "events", None):
        return pathlib.Path(args.events).expanduser()
    env = os.environ.get(EVENTS_ENV, "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return registry_path(args).parent / EVENTS_NAME


def append_event(events_p: pathlib.Path, ts: str, name, event: str, **fields) -> None:
    events_p.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": ts, "event": event, "name": name, **fields}
    with open(events_p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


def record(reg: dict, events_p: pathlib.Path, pkg: dict, event: str, **fields) -> None:
    """One transition = one history[] entry + one event-log line. Append-only."""
    ts = now_iso()
    pkg.setdefault("history", []).append({"ts": ts, "event": event, **fields})
    append_event(events_p, ts, pkg.get("name"), event, **fields)


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


def lease_live(lease: dict | None, now: dt.datetime) -> bool:
    return bool(lease) and now < lease_expiry(lease)


def do_claim(reg_path, reg, events_p, name, workspace, agent, ttl, reason, force):
    if ttl <= 0:
        raise Fail("--ttl must be a positive number of hours")
    if force and not reason:
        raise Fail("--force requires --reason")
    pkg = get_pkg(reg, name)
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    if lease and lease_live(lease, now):
        if lease.get("agent") == agent and not force:
            lease["claimed_at"] = now_iso()
            lease["ttl_hours"] = ttl
            if workspace:
                lease["workspace"] = workspace
            if reason:
                lease["reason"] = reason
            pkg["workspace"] = lease.get("workspace")
            record(reg, events_p, pkg, "claim-refreshed", agent=agent,
                   workspace=lease.get("workspace"), ttl_hours=ttl)
            save_registry(reg_path, reg)
            payload = {"ok": True, "name": name, "event": "claim-refreshed",
                       "lease": dict(lease),
                       "expires_at": lease_expiry(lease).isoformat()}
            return ([f"{name}: lease refreshed for {agent} "
                     f"until {lease_expiry(lease).isoformat()}"], payload)
        if not force:
            raise Fail(f"{name}: lease held by {lease.get('agent')} "
                       f"(workspace {lease.get('workspace')}) until "
                       f"{lease_expiry(lease).isoformat()} — refusing; "
                       f"override with --force --reason")
        event = "claim-forced"
    else:
        event = "claim-forced" if force else "claimed"
    pkg["lease"] = {"workspace": workspace, "agent": agent,
                    "claimed_at": now_iso(), "ttl_hours": ttl,
                    "reason": reason or ""}
    pkg["workspace"] = workspace
    pkg["agent"] = agent
    record(reg, events_p, pkg, event, agent=agent, workspace=workspace,
           ttl_hours=ttl, reason=reason or "")
    save_registry(reg_path, reg)
    payload = {"ok": True, "name": name, "event": event,
               "lease": dict(pkg["lease"]),
               "expires_at": lease_expiry(pkg["lease"]).isoformat()}
    return ([f"{name}: lease {event} for {agent}@{workspace} "
             f"until {lease_expiry(pkg['lease']).isoformat()}"], payload)


def do_release(reg_path, reg, events_p, name, agent, force):
    pkg = get_pkg(reg, name)
    lease = pkg.get("lease")
    if not lease:
        raise Fail(f"{name}: no lease held")
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
           workspace=lease.get("workspace"), released_by=agent or "force")
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
             "owner_project", "repo", "license_spdx", "formalization_yaml"}
SPDX_ID_RE = re.compile(r"^[A-Za-z0-9.+-]+$")
OWNED_ELSEWHERE = {
    "gate": "lean-pkg gate", "promoted_at": "lean-pkg promote",
    "promoted_commit": "lean-pkg promote", "visibility": "lean-pkg promote",
    "lease": "lean-pkg claim/release", "workspace": "lean-pkg claim",
    "agent": "lean-pkg claim", "status": "lean-pkg update --status",
    "name": "(immutable)", "local_path": "(immutable; re-register instead)",
    "history": "(append-only)",
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
}


def do_update(reg_path, reg, events_p, name, *, sets, status, reason,
              redetect, agent, force):
    pkg = get_pkg(reg, name)

    for field in sets:
        if field in OWNED_ELSEWHERE:
            raise Fail(f"{name}: field {field!r} is not settable here — "
                       f"use {OWNED_ELSEWHERE[field]}")
        if field not in UPDATABLE:
            raise Fail(f"{name}: unknown field {field!r} "
                       f"(settable: {', '.join(sorted(UPDATABLE))})")
        value = sets[field]
        if field == "license_spdx" and not SPDX_ID_RE.match(str(value)):
            raise Fail(f"license_spdx must be an SPDX identifier such as Apache-2.0 or MIT, got {value!r}")
        if field == "formalization_yaml":
            pp = pathlib.PurePosixPath(str(value))
            if pp.is_absolute() or ".." in pp.parts or str(value).startswith("~"):
                raise Fail(f"formalization_yaml must be a repository-relative path, got {value!r}")

    # Lease discipline, the same rule `release` applies: a live lease held by
    # someone else blocks a write unless it is forced with a reason.
    lease = pkg.get("lease")
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)):
        holder = lease.get("agent")
        if not force and agent != holder:
            raise Fail(f"{name}: lease held by {holder} — pass --agent {holder}, "
                       f"or --force with --reason")
    if force and not reason:
        raise UsageError("--force requires --reason")

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

    if status is not None:
        old = pkg.get("status")
        if status not in STATUSES:
            raise Fail(f"bad status {status!r} (allowed: {', '.join(STATUSES)})")
        if status != old:
            if not reason:
                raise UsageError("--status requires --reason")
            if (old, status) not in MANUAL_TRANSITIONS:
                hint = {"verified-private": "lean-pkg gate --receipt R",
                        "public": "lean-pkg promote --yes"}.get(status)
                raise Fail(f"{name}: {old} -> {status} is not a manual transition"
                           + (f" — use {hint}" if hint else ""))
            changes["status"] = (old, status)

    if not changes:
        # Idempotent: no history entry, no event, no write. `gate` behaves the
        # same way for an already-recorded receipt.
        payload = {"ok": True, "name": name, "event": "update-noop", "changes": {}}
        return ([f"{name}: already up to date; nothing written"], payload)

    for field, (_old, new) in changes.items():
        pkg[field] = new

    errors = validate_registry(reg)
    if errors:
        # Never persist a registry that fails its own schema.
        raise Fail(f"{name}: update would break the registry schema:\n  "
                   + "\n  ".join(errors))

    record(reg, events_p, pkg, "updated",
           fields=sorted(changes), reason=reason or "", forced=bool(force),
           **{f"{f}_from": o for f, (o, _n) in changes.items()})
    save_registry(reg_path, reg)

    out = [f"{name}: updated {len(changes)} field(s)"]
    for field, (old, new) in sorted(changes.items()):
        out.append(f"  {field}: {old!r} -> {new!r}")
    payload = {"ok": True, "name": name, "event": "updated",
               "changes": {f: {"from": o, "to": n} for f, (o, n) in changes.items()},
               "reason": reason or "", "forced": bool(force)}
    return (out, payload)


# --------------------------------------------------------------------------
# gate

RL_REMINDER = "results-ledger: record the outcome for {name} in your results ledger"

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
    ok, note = evidence_only_descendant(repo, commit, head)
    if not ok:
        raise Fail(f"receipt commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the receipt must bind to HEAD or an evidence-only descendant "
                   f"({note})")
    tc_file = repo / "lean-toolchain"
    if not tc_file.is_file():
        raise Fail(f"{repo}: no lean-toolchain file")
    toolchain = tc_file.read_text().strip()
    if str(receipt["lean_toolchain"]).strip() != toolchain:
        raise Fail(f"toolchain mismatch: receipt={receipt['lean_toolchain']!r} "
                   f"file={toolchain!r}")
    if not str(receipt["mathlib_rev"]).strip():
        raise Fail("receipt mathlib_rev is empty")
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


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_show_bytes(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> bytes | None:
    """Bytes of <rev>:<rel>, or None when the path is not tracked there."""
    p = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{rel}"],
                       capture_output=True)
    return p.stdout if p.returncode == 0 else None


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
    ok, note = evidence_only_descendant(repo, commit, head)
    if not ok:
        raise Fail(f"comparator commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the comparator receipt must bind to HEAD or an evidence-only "
                   f"descendant ({note})")
    if receipt_commit and commit != receipt_commit:
        raise Fail(f"comparator commit {commit[:12]}… != lower-tier receipt commit "
                   f"{receipt_commit[:12]}… — both tiers must bind to one commit")
    tc_file = repo / "lean-toolchain"
    if not tc_file.is_file():
        raise Fail(f"{repo}: no lean-toolchain file")
    toolchain = tc_file.read_text().strip()
    if str(comp["lean_toolchain"]).strip() != toolchain:
        raise Fail(f"comparator toolchain mismatch: receipt={comp['lean_toolchain']!r} "
                   f"file={toolchain!r}")
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
        for f in ("challenge_module", "solution_module", "theorem_names",
                  "definition_names", "permitted_axioms"):
            if cfg[f] != blob_cfg.get(f):
                raise Fail(f"comparator config {path}: receipt {f} differs from the "
                           f"config at {commit[:12]} ({cfg[f]!r} vs {blob_cfg.get(f)!r})")
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
        # Definition holes (value-blind in comparator) need a human judged_as sign-off,
        # judged against the config bytes at the bound commit, never the receipt's copy.
        try:
            check_definition_holes(blob_cfg, f"comparator config {path}")
        except ValueError as e:
            raise Fail(str(e)) from None
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
            raise Fail(f"comparator log missing: {log_rel}")
        log_bytes = log_p.read_bytes()
        if sha256_bytes(log_bytes) != str(cfg["log_sha256"]).lower():
            raise Fail(f"comparator log_sha256 mismatch for {log_rel}")
        lines = log_bytes.decode("utf-8", "replace").splitlines()
        if not comparator_pass("\n".join(lines), mechanism):
            raise Fail(f"comparator log {log_rel} has no PASS line for the {mechanism} mechanism "
                       f"({COMPARATOR_PASS_LINE!r} as a full line)")


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


def copy_comparator(repo: pathlib.Path, commit: str,
                    src: pathlib.Path) -> tuple[str, str]:
    dest_rel = f".lean-receipts/{commit[:12]}-comparator.json"
    dest = repo / dest_rel
    if dest.exists():
        if recorded_json(repo, dest_rel) != json.loads(src.read_text()):
            raise Fail(f"{dest_rel} already holds a different comparator receipt; "
                       f"remove it or pass that file instead")
        return dest_rel, f"comparator receipt already present at {dest_rel} (not re-copied)"
    dest.parent.mkdir(exist_ok=True)
    shutil.copy2(src, dest)
    return dest_rel, (f"comparator receipt copied to {dest} — NOT committed there; "
                      f"commit it with its logs in the verified repo yourself")


def receipt_private_strings(blobs: list[tuple[str, bytes]], refuse: bool) -> list[dict]:
    """Scan receipt bytes for private strings before they are copied into the repository.
    Each hit is {"field", "kind"} (the value itself is never echoed). Without `refuse` every hit
    is a WARN line on stderr (a receipt may legitimately be private); with it, a refusal."""
    from .index import private_string_hits
    hits = [{"field": f, "kind": k} for where, blob in blobs
            for f, k, _ in private_string_hits(blob, where)]
    if hits and refuse:
        raise Fail("--refuse-private: receipt holds private strings, nothing copied: "
                   + "; ".join(f"{h['field']} ({h['kind']})" for h in hits))
    for h in hits:
        print(f"WARN: {h['field']} looks like a {h['kind']}; the receipt is copied into the "
              f"repository as is (--refuse-private refuses)", file=sys.stderr)
    return hits


def recorded_json(repo: pathlib.Path, rel) -> dict | None:
    if not rel:
        return None
    try:
        return json.loads((repo / rel).read_text())
    except Exception:
        return None


def do_gate(reg_path, reg, events_p, name, receipt_arg,
            comparator_arg=None, refuse_private: bool = False) -> tuple[list[str], dict]:
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    rp, receipt = load_json_object(receipt_arg, "receipt")
    cp = comp = None
    if comparator_arg is not None:
        cp, comp = load_json_object(comparator_arg, "comparator receipt")
    private = receipt_private_strings([("receipt", rp.read_bytes())]
                                      + ([("comparator", cp.read_bytes())] if cp else []),
                                      refuse_private)
    gate = pkg.get("gate") or {}
    # Idempotent retry: an identical receipt (pair) already recorded is a
    # no-op success, so agent retries are safe.
    recorded = recorded_json(repo, gate.get("receipt"))
    if recorded is not None and recorded == receipt:
        rec_comp = recorded_json(repo, gate.get("comparator"))
        if comp is None or (rec_comp is not None and rec_comp == comp):
            payload = {"ok": True, "name": name, "event": "already-gated",
                       "status": pkg.get("status"), "gate": pkg.get("gate"),
                       "receipt": gate.get("receipt"), "private_strings": private}
            return ([f"{name}: already gated (receipt {gate.get('receipt')}); "
                     f"nothing to do"], payload)
        raise Fail(f"{name}: already gated with this receipt; to add or replace "
                   f"comparator evidence use `lean-pkg comparator {name} --receipt ...`")
    # gate-pending is the designed entry. A package that was public before it was
    # ever gated (for example a collaborator-owned repository registered as public)
    # may also receive evidence: the status stays public, the receipt and the
    # gate-passed event are recorded. verified-private re-certifies through the
    # manual move back to gate-pending.
    if pkg.get("status") not in ("gate-pending", "public"):
        raise Fail(f"{name}: gate requires status gate-pending (or public, for "
                   f"evidence on an already-public package), got {pkg.get('status')!r}")
    was_public = pkg.get("status") == "public"
    validate_receipt(pkg, repo, receipt)
    commit = receipt["commit"]
    if comp is not None:
        validate_comparator(pkg, repo, comp, commit)

    job = re.sub(r"[^A-Za-z0-9._-]+", "-", str(receipt["job_id"]))
    dest_rel = f".lean-receipts/{commit[:12]}-{job}.json"
    dest = repo / dest_rel
    out: list[str] = []
    if dest.exists():
        if recorded_json(repo, dest_rel) != receipt:
            raise Fail(f"{dest_rel} already holds a different receipt; "
                       f"remove it or pass that file instead")
        out.append(f"receipt already present at {dest_rel} (not re-copied)")
    else:
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(rp, dest)
        out.append(f"receipt copied to {dest} — NOT committed there; "
                   f"commit it in the verified repo yourself")
    pkg.setdefault("gate", {})["receipt"] = dest_rel
    fields = dict(commit=commit, job_id=str(receipt["job_id"]),
                  checker=str(receipt["checker"]), receipt=dest_rel)
    comp_rel = None
    if comp is not None:
        comp_rel, note = copy_comparator(repo, commit, cp)
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
    record(reg, events_p, pkg, "gate-passed", **fields)
    save_registry(reg_path, reg)
    if was_public:
        out.append(f"{name}: public, evidence recorded, status unchanged (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    else:
        out.append(f"{name}: gate-pending -> verified-private (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    out.append(RL_REMINDER.format(name=name))
    payload = {"ok": True, "name": name, "event": "gate-passed",
               "status": new_status, "gate": pkg["gate"],
               "receipt": dest_rel, "commit": commit, "rl_reminder": True,
               "status_unchanged": was_public, "private_strings": private}
    if comp_rel:
        payload["comparator"] = comp_rel
    return out, payload


def do_comparator(reg_path, reg, events_p, name, receipt_arg,
                  check_only: bool = False, refuse_private: bool = False) -> tuple[list[str], dict]:
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    cp, comp = load_json_object(receipt_arg, "comparator receipt")
    private = receipt_private_strings([("comparator", cp.read_bytes())], refuse_private)
    gate = pkg.get("gate") or {}
    if check_only:
        lower = recorded_json(repo, gate.get("receipt"))
        lower_commit = str(lower.get("commit") or "") if lower else None
        validate_comparator(pkg, repo, comp, lower_commit or None)
        n = len(comp["configs"])
        msg = (f"{name}: comparator receipt valid at commit {comp['commit'][:12]} "
               f"({n} config(s)) — not recorded (--check)"
               + ("" if lower_commit else "; no lower-tier receipt recorded yet"))
        payload = {"ok": True, "name": name, "event": "comparator-checked",
                   "recorded": False, "commit": comp["commit"], "configs": n,
                   "lower_receipt_commit": lower_commit, "private_strings": private}
        return [msg], payload
    rec_comp = recorded_json(repo, gate.get("comparator"))
    if rec_comp is not None and rec_comp == comp:
        payload = {"ok": True, "name": name, "event": "already-attached",
                   "status": pkg.get("status"), "gate": gate,
                   "comparator": gate.get("comparator"), "private_strings": private}
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
    commit = comp["commit"]
    comp_rel, note = copy_comparator(repo, commit, cp)
    pkg.setdefault("gate", {})["comparator"] = comp_rel
    record(reg, events_p, pkg, "comparator-attached", commit=commit,
           comparator=comp_rel, host=str(comp["host"]), job_id=str(comp["job_id"]))
    save_registry(reg_path, reg)
    out = [note, f"{name}: comparator-attached ({comp_rel}, commit {commit[:12]}, "
                 f"{len(comp['configs'])} config(s))"]
    payload = {"ok": True, "name": name, "event": "comparator-attached",
               "status": pkg.get("status"), "gate": pkg["gate"],
               "comparator": comp_rel, "commit": commit, "private_strings": private}
    return out, payload


# --------------------------------------------------------------------------
# register — the ONLY write path for new packages (agents never hand-edit yaml)

def do_register(reg_path, reg, events_p, name, repo, local_path, owner_project,
                visibility, status, upstream_target, automation, notes,
                workspace, agent, dry_run) -> tuple[list[str], dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise Fail(f"bad package name {name!r} — use kebab-case-ish "
                   f"[A-Za-z0-9._-]+ not starting with a punctuation mark")
    pkgs = reg["packages"]
    if name in pkgs:
        raise Fail(f"name already registered: {name!r} — refusing to "
                   f"overwrite; inspect with `lean-pkg status {name}`")
    norm_new = normalize_local_path(local_path)
    for existing, pkg in pkgs.items():
        if normalize_local_path(pkg.get("local_path") or "") == norm_new:
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
               "warnings": warnings}
    if dry_run:
        lines = [f"warn: {w}" for w in warnings]
        lines.append(yaml.safe_dump({name: entry}, sort_keys=False,
                                    default_flow_style=False, width=120).rstrip())
        lines.append("DRY RUN — nothing written. Re-run without --dry-run "
                     "to register.")
        return lines, payload
    pkgs[name] = entry
    errors = validate_registry(reg)  # safety net: never write an invalid registry
    if errors:
        del pkgs[name]
        raise Fail("register would produce an invalid registry: "
                   + "; ".join(errors))
    record(reg, events_p, entry, "registered", repo=repo,
           local_path=local_path, status=status, visibility=visibility,
           lean_toolchain=toolchain, mathlib_pin=mathlib_pin,
           workspace=workspace, agent=agent)
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
    if reg_path.exists():
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
    reg_path.write_text(yaml.safe_dump(data, sort_keys=False))
    append_event(events_p, now_iso(), None, "registry-created", registry=str(reg_path))
    out = [f"registry created: {reg_path} (version {REGISTRY_VERSION}); "
           f"events: {events_p}"]
    return out, {"ok": True, "event": "registry-created", "registry": str(reg_path),
                 "events": str(events_p), "version": REGISTRY_VERSION}


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


def do_record(reg_path, reg, events_p, name, event, fields: dict, reason: str) -> tuple[list[str], dict]:
    """Record a D5 evidence event: required fields checked, registry effect applied, event appended."""
    if event not in EVIDENCE_EVENTS:
        raise Fail(f"unknown evidence event {event!r}; one of {sorted(EVIDENCE_EVENTS)}")
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
    pkg = get_pkg(reg, name)
    for k, d in V2_PACKAGE_DEFAULTS.items():      # tolerate a v1 registry
        pkg.setdefault(k, copy.deepcopy(d))
    ts = now_iso()
    extra_out: list[str] = []
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
            try:
                e["version"] = int(fields["version"])
            except ValueError:
                raise Fail(f"version must be an integer, got {fields['version']!r}")
            e["id"] = fields["id"]
            e["comparator_config"] = fields["comparator_config"]
            e["registered_at"] = ts
    elif event == "palomar-withdrawn":
        for e in pkg["palomar"]:
            if e.get("id") == fields["id"] and str(e.get("version")) == str(fields["version"]):
                e["withdrawn_at"] = ts
                break
        else:
            raise Fail(f"no palomar entry {fields['id']} v{fields['version']} to withdraw")
    elif event == "doi-minted":
        d = pkg.get("doi") or {"concept": None, "versions": []}
        if fields.get("concept"):
            d["concept"] = fields["concept"]
        d["versions"].append({"doi": fields["doi"], "tag": fields["tag"],
                              "commit": fields["commit"], "minted_at": ts})
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
    record(reg, events_p, pkg, event, reason=reason or "", **fields)
    save_registry(reg_path, reg)
    return [f"{name}: {event} recorded", *extra_out], {"ok": True, "name": name, "event": event,
                                                       "fields": fields, "notes": extra_out}

def promote_checklist(pkg: dict, repo: pathlib.Path) -> list[tuple[str, str]]:
    """(mark, label) pairs; mark in {'✓', '✗', 'WARN'}."""
    checks: list[tuple[str, str]] = []
    reuse = find_reuse()
    if reuse is None:
        checks.append(("WARN", "reuse not on PATH — REUSE license lint skipped"))
    else:
        p = run([reuse, "lint"], cwd=str(repo))
        if p.returncode == 0:
            checks.append(("✓", "reuse lint passed"))
        else:
            detail = (p.stderr or p.stdout).strip().splitlines()
            checks.append(("✗", f"reuse lint failed: {(detail or [''])[0][:120]}"))
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
        ok, note = (evidence_only_descendant(repo, rcommit, head)
                    if rcommit and head else (False, "no receipt commit"))
        if ok:
            checks.append(("✓", f"gate receipt bound to HEAD {head[:12]}"
                                + (f" ({note})" if note else "")))
        else:
            checks.append(("✗", f"gate receipt/HEAD drift: "
                                f"receipt={str(rcommit)[:12]} HEAD={str(head)[:12]} ({note})"))
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
                                    f"({len(comp['configs'])} config(s), nanoda replay)"))
            except Fail as e:
                checks.append(("✗", f"comparator receipt invalid: {str(e)[:140]}", str(e)))
    nwo = gh_nwo(pkg.get("repo") or "") or str(pkg.get("repo"))
    p = gh("repo", "view", nwo, "--json", "visibility", "-q", ".visibility")
    if p.returncode != 0:
        checks.append(("WARN", f"gh visibility query failed: "
                               f"{(p.stderr or p.stdout).strip()[:120]}"))
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


ONEWAY_WARNING = ("WARNING: a GitHub private→public flip is effectively ONE-WAY — "
                  "the code and its full history become permanently public; "
                  "flipping back does not unpublish forks, clones, or caches.")


_MARK_TO_STATUS = {"✓": "pass", "✗": "fail", "WARN": "warn"}


def do_promote(reg_path, reg, events_p, name, yes) -> tuple[list[str], int, dict]:
    pkg = get_pkg(reg, name)
    if pkg.get("status") != "verified-private":
        raise Fail(f"{name}: promote requires status verified-private, "
                   f"got {pkg.get('status')!r}")
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    lines = [f"promotion checklist for {name} ({repo}):"]
    checks = promote_checklist(pkg, repo)
    lines += [f"  {c[0]}  {c[1]}" for c in checks]
    payload = {"name": name, "dry_run": not yes,
               "checklist": [dict({"status": _MARK_TO_STATUS[c[0]], "label": c[1]},
                                  **({"reason": c[2]} if len(c) > 2 else {}))
                             for c in checks],
               "one_way_warning": ONEWAY_WARNING}
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
                   notified=notified, rl_reminder=True)
    return lines, 0, payload


# --------------------------------------------------------------------------
# audit

def render_finding(f: dict) -> str:
    return f"{f['check']:<20}{f['name']}: {f['detail']}"


def audit_registry(reg_path, reg, events_p, fix: bool = False) -> list[dict]:
    """Structured findings: {"check", "name", "detail", "fixed"}. May mutate
    (lease sweeps always; visibility/archive reconciliation only with fix)."""
    findings: list[dict] = []
    now = dt.datetime.now(dt.timezone.utc)
    mutated = False

    def note(check, name, detail, fixed=False):
        findings.append({"check": check, "name": name,
                         "detail": detail, "fixed": fixed})

    for name in sorted(reg["packages"]):
        pkg = reg["packages"][name]
        lease = pkg.get("lease")
        if lease and not lease_live(lease, now):
            note("lease-expired", name,
                 f"was {lease.get('agent')}@{lease.get('workspace')} "
                 f"until {lease_expiry(lease).isoformat()} — cleared",
                 fixed=True)
            record(reg, events_p, pkg, "lease-expired", agent=lease.get("agent"),
                   workspace=lease.get("workspace"),
                   claimed_at=lease.get("claimed_at"),
                   ttl_hours=lease.get("ttl_hours"))
            pkg["lease"] = None
            pkg["workspace"] = None
            pkg["agent"] = None
            mutated = True
        if pkg.get("upstream_target") == "palomar" and pkg.get("status") in ("verified-private", "public") \
                and not any(e.get("preflight_run") for e in (pkg.get("palomar") or []) if isinstance(e, dict)):
            note("palomar-preflight-missing", name,
                 "upstream_target=palomar but no palomar-preflight-pass recorded "
                 "(lean-pkg record NAME palomar-preflight-pass --field commit=… --field run=…)")
        repo = repo_path(pkg) if pkg.get("local_path") else None
        if repo is None or not repo.is_dir():
            note("missing-path", name, str(repo))
            continue
        tc = repo / "lean-toolchain"
        if tc.is_file():
            actual = tc.read_text().strip()
            if actual != str(pkg.get("lean_toolchain") or "").strip():
                note("toolchain-drift", name,
                     f"registry={pkg.get('lean_toolchain')} file={actual}")
        else:
            note("toolchain-missing", name, str(tc))
        gate_rec = pkg.get("gate") or {}
        ref_branch = ref_tip = None
        off_ref = ""
        if gate_rec.get("receipt") or gate_rec.get("comparator"):
            ref_branch, ref_tip = reference_branch(repo)
            here = current_branch(repo)
            if ref_branch and here != ref_branch:
                off_ref = f"checkout on {here}; judged against {ref_branch}"
                note("checkout-branch", name, off_ref)
        ref_label = ref_branch or "HEAD"
        suffix = f" ({off_ref})" if off_ref else ""
        receipt_rel = gate_rec.get("receipt")
        if receipt_rel:
            rp = repo / receipt_rel
            if not rp.is_file():
                note("receipt-missing", name, str(receipt_rel))
            else:
                try:
                    rcommit = json.loads(rp.read_text()).get("commit")
                except Exception:
                    rcommit = None
                if rcommit and ref_tip and not evidence_only_descendant(repo, rcommit, ref_tip)[0]:
                    note("receipt-drift", name,
                         f"receipt={str(rcommit)[:12]} {ref_label}={ref_tip[:12]}{suffix}")
        comp_rel = gate_rec.get("comparator")
        if comp_rel:
            cpth = repo / comp_rel
            if not cpth.is_file():
                note("comparator-missing", name, str(comp_rel))
            else:
                ccommit = (recorded_json(repo, comp_rel) or {}).get("commit")
                if ccommit and ref_tip and not evidence_only_descendant(repo, ccommit, ref_tip)[0]:
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
            note("warn", name,
                 f"cannot parse GitHub owner/repo from {pkg.get('repo')!r}")
            continue
        p = gh("repo", "view", nwo, "--json", "visibility,isArchived")
        if p.returncode != 0:
            note("warn", name,
                 f"gh query failed: {(p.stderr or p.stdout).strip()[:120]}")
            continue
        try:
            info = json.loads(p.stdout)
        except ValueError:
            note("warn", name, "gh returned non-JSON")
            continue
        gvis = str(info.get("visibility") or "").lower()
        if gvis and gvis != str(pkg.get("visibility")).lower():
            note("visibility-mismatch", name,
                 f"registry={pkg.get('visibility')} gh={info.get('visibility')}"
                 + (" — fixed" if fix else ""), fixed=fix)
            if fix:
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
                for v in doi.get("versions") or []:
                    c = str((v or {}).get("commit") or "")
                    if c and c not in pal_commits:
                        known = ", ".join(str(x)[:12] for x in pal_commits) or "none"
                        errors.append(f"{pfx}: doi {v.get('doi')} commit {c[:12]} matches no "
                                      f"palomar entry (palomar commits: {known})")
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
        ra = pkg.get("reaudit_after")
        if ra is not None:
            try:
                dt.date.fromisoformat(str(ra))
            except ValueError:
                errors.append(f"{pfx}: reaudit_after is not an ISO date: {ra!r}")
    return errors


# --------------------------------------------------------------------------
# board / list / status

def row_fields(pkg: dict, now: dt.datetime) -> tuple[str, str, str, str, str, str]:
    lease = pkg.get("lease")
    if lease:
        label = f"{lease.get('agent')}@{lease.get('workspace') or '-'}"
        if not lease_live(lease, now):
            label += " EXPIRED"
    else:
        label = "-"
    return (str(pkg.get("name")), str(pkg.get("status")),
            str(pkg.get("visibility")), label,
            str(pkg.get("mathlib_pin") or "-"),
            str(pkg.get("reaudit_after") or "-"))


def table_lines(reg: dict, now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = [row_fields(reg["packages"][k], now) for k in sorted(reg["packages"])]
    widths = [max([len(BOARD_HEADER[i])] + [len(r[i]) for r in rows])
              for i in range(len(BOARD_HEADER))]
    fmt = lambda r: "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(BOARD_HEADER)))
    return [fmt(BOARD_HEADER)] + [fmt(r) for r in rows]


def cmd_board(args) -> int:
    reg_path = registry_path(args)
    if args.json_out:
        print_json(load_registry(reg_path))
        return 0
    interactive = (sys.stdin.isatty() and sys.stdout.isatty()
                   and os.environ.get("TERM", "") != "dumb")
    if interactive:
        try:
            import curses
        except ImportError:
            pass
        else:
            _Tui(curses, reg_path, events_path(args)).start()
            return 0
    print("\n".join(table_lines(load_registry(reg_path))))
    return 0


def cmd_list(args) -> int:
    reg = load_registry(registry_path(args))
    if args.json_out:
        print_json(reg)
        return 0
    print("\n".join(table_lines(reg)))
    return 0


def cmd_status(args) -> int:
    reg = load_registry(registry_path(args))
    pkg = get_pkg(reg, args.name)
    hist = pkg.get("history") or []
    if args.json_out:
        print_json({"ok": True, "name": args.name, "package": pkg,
                    "history_count": len(hist)})
        return 0
    view = dict(pkg)
    view["history"] = f"<{len(hist)} entries; last 5 below>"
    print(yaml.safe_dump({args.name: view}, sort_keys=False,
                         default_flow_style=False, width=120).rstrip())
    print(f"history (last {min(5, len(hist))} of {len(hist)}):")
    for e in hist[-5:]:
        print("  " + json.dumps(e, sort_keys=True))
    return 0


# --------------------------------------------------------------------------
# TUI

TUI_HELP = """\
lean-pkg board — keys

  j / DOWN   move selection down
  k / UP     move selection up
  c          claim package (CAS lease; prompts workspace/agent/ttl/reason)
  r          release lease (holder agent; blank agent = --force)
  g          gate: validate a lower-tier receipt (gate-pending -> verified-private)
  p          promote checklist (DRY-RUN ONLY here; execute via CLI --yes)
  a          audit (sweeps expired leases, reconciles gh visibility)
  e          show the last event-log entry for the selected package
  ?          this help
  q / ESC    quit

Colors: green=public/upstreamed  yellow=active/gate-pending
        magenta=verified-private  cyan=scratch  dim=archived  red=expired lease
"""


class _Tui:
    STATUS_PAIRS = {"public": 1, "upstreamed": 1, "active": 2, "gate-pending": 2,
                    "scratch": 4, "verified-private": 5}

    def __init__(self, curses, reg_path, events_p):
        self.curses = curses
        self.reg_path = reg_path
        self.events_p = events_p
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
        if lease and not lease_live(lease, now):
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
        ttl_s = self.prompt(f"ttl hours [{DEFAULT_TTL_HOURS}]: ")
        ttl = int(ttl_s) if ttl_s.isdigit() and int(ttl_s) > 0 else DEFAULT_TTL_HOURS
        reason = self.prompt("reason (optional): ")
        if not self.confirm(f"claim {name} for {agent}@{ws} ({ttl}h)?"):
            self.msg = "claim cancelled"
            return
        try:
            out, _ = do_claim(self.reg_path, self.reg, self.events_p,
                              name, ws, agent, ttl, reason, False)
            self.msg = out[-1]
        except Fail as e:
            self.msg = f"refused: {e}"
        self.reload()

    def key_release(self):
        name = self.selected()
        if not name:
            return
        agent = self.prompt(f"release {name} — holder agent (blank = FORCE): ")
        force = agent == ""
        if not self.confirm(f"release {name}{' (FORCE)' if force else ''}?"):
            self.msg = "release cancelled"
            return
        try:
            out, _ = do_release(self.reg_path, self.reg, self.events_p,
                                name, agent or None, force)
            self.msg = out[-1]
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
            out, _ = do_gate(self.reg_path, self.reg, self.events_p, name, path)
            self.show_text(f"gate {name}", "\n".join(out))
            self.msg = out[-1]
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
        if not self.confirm("run audit? (sweeps expired leases; read-only otherwise)"):
            self.msg = "audit cancelled"
            return
        try:
            findings = audit_registry(self.reg_path, self.reg, self.events_p,
                                      fix=False)
            text = "\n".join(render_finding(f) for f in findings) or "no findings"
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
    today = today or dt.date.today().isoformat()
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
        label = (f"{lease.get('agent')}@{lease.get('workspace') or '-'}"
                 if lease else "-")
        L.append(f"| {name} | {pkg.get('status')} | {pkg.get('visibility')} "
                 f"| {label} | {pkg.get('mathlib_pin') or '-'} "
                 f"| {pkg.get('reaudit_after') or '-'} |")
    L += ["", "## Active leases", ""]
    live = [(n, p["lease"]) for n, p in sorted(reg["packages"].items())
            if p.get("lease") and lease_live(p["lease"], now)]
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
                 else reg_path.parent / "lean-adoption-candidates.yaml")
    candidates = load_candidates(cand_path)
    text = render_wiki(reg, candidates)
    if args.out:
        out_p = pathlib.Path(args.out).expanduser()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(text)
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
Registry resolution: --registry PATH, else env LEAN_PKG_REGISTRY, else ./lean-packages.yaml.

STANDING RULES
- NEVER hand-edit the registry yaml. Use `lean-pkg register` / `lean-pkg update`.
- Before starting Lean work on a package: `lean-pkg board --json` (or `list --json`).
- Claim before working: one package = one workspace. Default TTL 24h; `audit` sweeps expired leases.
- Mutating ops are dry-run by default unless a flag says otherwise: promote --yes, audit --fix.
- Fully non-interactive: pass flags, never wait for a prompt. Use --json for machine parsing.

STATUS ENUM AND TRANSITIONS (evidence required)
scratch -> active                  owner_project named
active -> gate-pending             lower-tier check submitted (terminal executor job identity in notes)
gate-pending -> verified-private   `gate` accepts a receipt (schema below)
public -> public                   `gate` on an already-public package records evidence; status unchanged
verified-private -> public         `promote --yes` after a clean dry-run checklist; ONE-WAY flip
public -> upstreamed               mathlib PR merged or Palomar entry; record link in notes
any -> archived                    `audit --fix` (gh isArchived) or maintainer decision

SUBCOMMANDS (global flags: --registry PATH --events PATH --json; env LEAN_PKG_REGISTRY, LEAN_PKG_EVENTS)
board     TUI on a tty, static table otherwise.       ex: lean-pkg board
list      static table.                                ex: lean-pkg list --json
status    full record + last 5 history entries.        ex: lean-pkg status my-lean
init      bootstrap an empty registry + event log.    ex: lean-pkg --registry ./lean-packages.yaml init
migrate   upgrade the registry schema in place to v3 (v2 adds license_spdx, formalization_yaml, palomar[], doi, reservoir, consent[];
          v3 adds claims_file, claims[], claims_synced_commit; additive, idempotent). ex: lean-pkg migrate --dry-run
record    record a D5 evidence event; required --field keys per event:
          consent-recorded: who role pointer | palomar-preflight-pass: commit run
          palomar-submitted: commit submission_id comparator_config | palomar-review: commit outcome(neutral|revision_required|rejected)
          palomar-registered: commit id version comparator_config | palomar-withdrawn: id version
          doi-minted: doi tag commit [concept] | reservoir-listed: url (the package's https://reservoir.lean-lang.org/@owner/name page;
          verified against Reservoir's API: 404 refuses, unreachable records verified=false) | mathlib-pr-opened: url
          ex: lean-pkg record my-lean doi-minted --field doi=10.5281/zenodo.1 --field tag=v1.0.0 --field commit=<40hex>
register  add a package; dedupes by name AND local_path; auto-detects toolchain + mathlib pin.
  flags: --repo URL (required) --path HOME-RELATIVE (required) [--owner-project N]
         [--visibility private|public] [--status ENUM] [--upstream-target none|mathlib|palomar]
         [--automation TEXT] [--notes TEXT] [--workspace W] [--agent A] [--dry-run]
  --workspace/--agent are metadata only; they do NOT claim. Claiming is a separate act.
  ex: lean-pkg register my-lean --repo git@github.com:you/my-lean.git --path src/my-lean
update    set fields of a registered package; manual status moves need --reason.
  ex: lean-pkg update my-lean --status gate-pending --reason "check job 4242 submitted"
claim     CAS lease; refuses if another agent holds an unexpired lease (names holder + expiry).
  flags: --workspace W --agent A (required) [--ttl HOURS=24] [--reason TEXT] [--force (needs --reason)]
  ex: lean-pkg claim my-lean --workspace w3 --agent vera --reason "chapter 2"
release   holder-only unless --force.                  ex: lean-pkg release my-lean --agent vera
gate      validate receipt; gate-pending -> verified-private; identical re-gate = no-op "already gated".
  flags: --receipt PATH (required), --comparator PATH (optional), --refuse-private (private paths in a receipt:
  refuse, exit 1, nothing copied; default a stderr WARN naming the field + JSON private_strings[{field, kind}])
  ex: lean-pkg gate my-lean --receipt /tmp/receipt.json --comparator /tmp/comparator.json
comparator <name> --receipt PATH   attach comparator evidence to a verified-private/public package;
  no status change; event comparator-attached; identical re-attach = no-op. It is required by promote.
  The receipt is written by comparator-receipt on a host its policy allows (a Slurm job, a host in
  COMPARATOR_RECEIPT_ALLOWED_HOSTS, or COMPARATOR_RECEIPT_ALLOW_HOST=1).
  flags: --receipt PATH (required), --check (validate only, record nothing; works at any status), --refuse-private
  ex: lean-pkg comparator my-lean --receipt .lean-receipts/<c12>-comparator.json
promote   dry-run default; --yes flips GitHub visibility to public (ONE-WAY) + one notification
  (via LEAN_PKG_NOTIFY_CMD when set). Needs an authenticated `gh`.
  promote refuses without a byte-valid comparator receipt bound to HEAD.
  ex: lean-pkg promote my-lean          # always read the dry-run checklist first
audit     sweep expired leases; reconcile gh visibility; receipt/comparator/toolchain/formalization.yaml drift. --fix applies gh -> registry.
  ex: lean-pkg audit --json
validate  schema + enum + history sanity.              ex: lean-pkg validate
llms      this contract.                               ex: lean-pkg llms
wiki      render a Markdown status page.               ex: lean-pkg wiki --out status/index.md
index     public catalog <index>/<name>/entry.yaml + receipts/ (root: --index, env LEAN_PKG_INDEX, ./index).
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
  sync NAME   read claims.yaml at HEAD (committed bytes only); every decl must occur in its file; event claims-synced.
  list [NAME] | check [NAME]   cached claims with status and derived trust | re-validate; exit 0/1.
formalization NAME [--write] [--out PATH] [--force] [--author A ...] [--agent A]   render formalization.yaml (mathlib-initiative
  v0.4, what Palomar reads) from the registry + the repo at HEAD. Derived every run: project.name/license (license_spdx),
  automation.notes, status.sorry_count/axioms/evidence (gate receipt), status.main_results (every tracked comparator config:
  comparator_config, declaration, file; definition holes carry judged_as, null when unsigned), alignment (claims.yaml). Every other
  key is KEPT from the file; first render seeds authors from consent[] owner/author, else "[PLACEHOLDER] ..." + stderr warning (never
  invented: no paper titles). --write: idempotent, refuses a differing file without --force, sets formalization_yaml via update.
  Validated against the vendored v0.4 schema (jsonschema, else structural). audit: formalization-missing/-drift. docs/formalization.md
search    TERM [--package NAME] [--offline] [--sources a,b,c] [--record [--repo-dir P]] [--json]   prior-art search.
  order: registry, index, loogle, leansearch, reservoir, palomar, github; each "ok" or "skipped: <reason>" (never zero hits).
  local score: exact alias 3, keyword 2, every term word 1. exit 0 even with zero hits; 2 on usage errors.
  --record: docs/prior-art/<date>-<slug>.md in the package checkout (or --repo-dir; never the registry dir; never
  overwritten; "decision: pending" for a human) + event prior-art-searched. claim/index add print advisory notes.

RECEIPT SCHEMA (JSON object; `gate` enforces every line)
commit         40 lowercase hex; MUST equal HEAD, or HEAD must be an evidence-only descendant of it
               (only .lean-receipts/ paths differ between the two commits)
lean_toolchain string; MUST equal the repo's lean-toolchain file content, stripped
mathlib_rev    nonempty string (full mathlib rev)
sorry_count    integer; MUST be 0
axioms         list of strings; subset of gate.axioms_allowed (default exactly: propext, Quot.sound, Classical.choice)
report_sha256  64 hex chars; SHA-256 of the verification report
job_id         nonempty terminal executor job identity, unique within that executor
checker        nonempty exact checker command/mode (e.g. serial leanchecker manifest)
generated_at   nonempty ISO-8601 timestamp

COMPARATOR RECEIPT SCHEMA ("comparator-receipt/2"; "comparator-receipt/1" still validates; written by comparator-receipt; `promote` re-derives every line from bytes)
schema          "comparator-receipt/2" (or "/1": legacy mechanism implied)
mechanism       "toolchain" (lake comparator + the toolchain's bundled NanoDa/con-ron; Lean v4.35.0-rc2+; what Palomar runs)
                or "legacy" (pinned comparator/lean4export/nanoda/landrun stack); comparator-receipt --mechanism auto|toolchain|legacy
commit          40 lowercase hex; MUST equal the lower-tier receipt commit, and equal HEAD or have HEAD as
                an evidence-only descendant (only .lean-receipts/ paths differ)
lean_toolchain  MUST equal the repo's lean-toolchain file, stripped
tools           legacy: {comparator, lean4export, nanoda, landrun}: 40-hex commits actually used
                toolchain: {lean_toolchain, lake_sha256, leanexport_sha256, leanchecker_sha256, nanoda_bin_sha256, con-ron_sha256}:
                64-hex sha256 of the judging binaries under `lean --print-prefix`/bin
host, job_id, generated_at   nonempty (Slurm job id, or local-<host>-<pid>-<utc-stamp>)
host_policy     optional; the runner's host-policy reason (an override run is visible here)
test_only       if present and true the receipt was produced under a test-only override and is REFUSED
result          "pass" (the runner writes a receipt only on PASS)
configs[]       MUST list exactly the comparator*.json / comparator/config*.json files tracked at HEAD
                (no omissions, no extras). Per config: path, sha256 of its HEAD bytes; challenge_module,
                solution_module, theorem_names[] (non-empty), definition_names[], permitted_axioms[]
                (subset of gate.axioms_allowed) — each MUST equal the config file at HEAD;
                a config with non-empty definition_names MUST carry judged_as {reviewer (a human, never
                a model/agent token), date YYYY-MM-DD, definitions ⊇ definition_names, basis?} in the
                file at HEAD — comparator never compares hole bodies (checked by the runner before a run
                and by every validator; judged_as is copied into the entry when present);
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
(required iff superseded; acyclic), citation|null, depends_on[]?}]; trust is derived, never typed

EXECUTOR EVIDENCE
The gate is executor-neutral. The frozen report named by report_sha256 must bind the exact
candidate commit/tree, source archive, toolchain/dependency pins, complete checker coverage,
terminal state/exit, and executor-specific identity/resource evidence. Slurm reports bind
terminal scheduler accounting; hosted reports bind run/attempt/job identity, terminal
conclusion, immutable workflow/control revision, runner identity, and resources. A pilot,
partial manifest, pre-terminal marker, or prior-candidate receipt is not gate evidence.

EVENT LINE SCHEMA (one JSON object per line; the same entry is appended to history[])
ts      ISO-8601 timestamp
event   one of: registry-created, registry-migrated, registered, updated, claimed, claim-refreshed, claim-forced,
        released, release-forced, lease-expired, gate-passed, promoted, audit-fix,
        comparator-attached, index-added, claims-synced, prior-art-searched, and the evidence events listed under `record`
name    package name or null
plus event-specific fields (agent, workspace, ttl_hours, reason, commit, receipt, ...)

EXIT CODES
0  success, including dry-runs and the "already gated" no-op
1  refused or validation failed (stderr has the reason; with --json stdout also gets {"ok": false, "error": ...})
2  usage error (argparse, or an interactive prompt attempted off-tty)
"""


# --------------------------------------------------------------------------
# CLI

def cmd_llms(args) -> int:
    print(LLMS_TEXT, end="")
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
    out, payload = do_record(reg_path, load_registry(reg_path), events_p,
                             args.name, args.event, fields, args.reason)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_register(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_register(reg_path, load_registry(reg_path), events_p,
                               args.name, args.repo, args.path,
                               args.owner_project, args.visibility, args.status,
                               args.upstream_target, args.automation, args.notes,
                               args.workspace, args.agent, args.dry_run)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_claim(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    reg = load_registry(reg_path)
    out, payload = do_claim(reg_path, reg, events_p,
                            args.name, args.workspace, args.agent, args.ttl,
                            args.reason, args.force)
    note = prior_art_note(get_pkg(reg, args.name), events_p)
    payload["notes"] = [note] if note else []
    if note:
        out.append(note)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_release(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_release(reg_path, load_registry(reg_path), events_p,
                              args.name, args.agent, args.force)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_update(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    sets = {}
    for field in ("automation", "notes", "upstream_target", "reaudit_after",
                  "owner_project", "repo", "license_spdx", "formalization_yaml"):
        value = getattr(args, field.replace("-", "_"), None)
        if value is not None:
            sets[field] = value
    out, payload = do_update(reg_path, load_registry(reg_path), events_p,
                             args.name, sets=sets, status=args.status,
                             reason=args.reason, redetect=args.redetect,
                             agent=args.agent, force=args.force)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_gate(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_gate(reg_path, load_registry(reg_path), events_p,
                           args.name, args.receipt, args.comparator,
                           refuse_private=args.refuse_private)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_comparator(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, payload = do_comparator(reg_path, load_registry(reg_path), events_p,
                                 args.name, args.receipt, args.check,
                                 refuse_private=args.refuse_private)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_promote(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    out, rc, payload = do_promote(reg_path, load_registry(reg_path), events_p,
                                  args.name, args.yes)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return rc


def cmd_audit(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    findings = audit_registry(reg_path, load_registry(reg_path), events_p,
                              fix=args.fix)
    if args.json_out:
        print_json({"ok": True, "fix": args.fix, "findings": findings})
    else:
        print("\n".join(render_finding(f) for f in findings)
              if findings else "no findings")
    return 0


def cmd_validate(args) -> int:
    reg_path = registry_path(args)
    try:
        data = yaml.safe_load(reg_path.read_text())
    except FileNotFoundError:
        raise Fail(f"registry not found: {reg_path}")
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}")
    errors = validate_registry(data)
    if args.json_out:
        print_json({"ok": not errors, "valid": not errors, "errors": errors,
                    "registry": str(reg_path)})
    else:
        for e in errors:
            print(f"  FAIL {e}")
        print("\nVALID" if not errors else f"\nINVALID ({len(errors)} error(s))")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lean-pkg",
        description="Lean 4 package registry: board, leases, trust gate, "
                    "promotion")
    ap.add_argument("--registry", default=None,
                    help=f"registry yaml (default: ${REGISTRY_ENV}, else "
                         f"./{DEFAULT_REGISTRY_NAME})")
    ap.add_argument("--events", default=None,
                    help=f"event log jsonl (default: {EVENTS_NAME} next to the "
                         f"registry)")
    ap.add_argument("--json", dest="json_out", action="store_true",
                    help="machine-readable JSON on stdout (exit codes unchanged)")
    sub = ap.add_subparsers(dest="cmd")

    def add_json(s):
        # Repeat --json on every subparser so BOTH `lean-pkg --json list` and
        # `lean-pkg list --json` work. SUPPRESS keeps the global value when
        # the flag is absent after the subcommand.
        s.add_argument("--json", dest="json_out", action="store_true",
                       default=argparse.SUPPRESS,
                       help="machine-readable JSON on stdout")

    s = sub.add_parser("board", help="curses TUI on a tty; static table otherwise")
    add_json(s)
    s.set_defaults(fn=cmd_board)
    s = sub.add_parser("list", help="static table, pipe-friendly")
    add_json(s)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("status", help="full record + last 5 history entries")
    s.add_argument("name")
    add_json(s)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("init", help="bootstrap an empty registry and its event log")
    s.add_argument("--force", action="store_true",
                   help="re-create only if the existing registry is empty")
    add_json(s)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("migrate", help="upgrade the registry schema in place (additive; idempotent)")
    s.add_argument("--dry-run", action="store_true", help="print the changes; write nothing")
    add_json(s)
    s.set_defaults(fn=cmd_migrate)

    s = sub.add_parser("record", help="record a publication evidence event (consent, Palomar, DOI, Reservoir, Mathlib PR)")
    s.add_argument("name")
    s.add_argument("event", help="one of: " + ", ".join(EVIDENCE_EVENTS))
    s.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="event field (repeatable); required keys per event are listed by `lean-pkg llms`")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    add_json(s)
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("register", help="the ONLY write path for new packages: "
                                        "validated, deduping, auto-detecting")
    s.add_argument("name")
    s.add_argument("--repo", required=True, help="git URL (or local bare path)")
    s.add_argument("--path", required=True,
                   help="local checkout path, $HOME-relative")
    s.add_argument("--owner-project", default=None)
    s.add_argument("--visibility", choices=VISIBILITIES, default="private")
    s.add_argument("--status", choices=STATUSES, default="active")
    s.add_argument("--upstream-target", choices=UPSTREAM_TARGETS, default="none")
    s.add_argument("--automation", default="")
    s.add_argument("--notes", default="")
    s.add_argument("--workspace", default=None,
                   help="metadata only; does NOT claim")
    s.add_argument("--agent", default=None, help="metadata only; does NOT claim")
    s.add_argument("--ttl", type=int, default=None,
                   help="accepted for forward compatibility; unused")
    s.add_argument("--dry-run", action="store_true",
                   help="print the would-be entry as YAML without writing")
    add_json(s)
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("claim", help="CAS lease on a package (24h default TTL)")
    s.add_argument("name")
    s.add_argument("--workspace", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=DEFAULT_TTL_HOURS, metavar="HOURS")
    s.add_argument("--reason", default="")
    s.add_argument("--force", action="store_true",
                   help="override a live lease; requires --reason")
    add_json(s)
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("release", help="release a lease (holder-only unless --force)")
    s.add_argument("name")
    s.add_argument("--agent", default=None)
    s.add_argument("--force", action="store_true")
    add_json(s)
    s.set_defaults(fn=cmd_release)

    s = sub.add_parser("update", help="set fields of an already-registered "
                                      "package (register is for NEW ones)")
    s.add_argument("name")
    s.add_argument("--automation", help="AI-disclosure note; required by mathlib "
                                        "when upstream_target is mathlib")
    s.add_argument("--notes")
    s.add_argument("--upstream-target", dest="upstream_target",
                   choices=UPSTREAM_TARGETS)
    s.add_argument("--reaudit-after", dest="reaudit_after", metavar="ISO8601")
    s.add_argument("--owner-project", dest="owner_project")
    s.add_argument("--repo", help="remote URL (e.g. after publishing a private dep)")
    s.add_argument("--license-spdx", dest="license_spdx", metavar="SPDX",
                   help="the package's license as an SPDX id (what the index and Reservoir expect)")
    s.add_argument("--formalization-yaml", dest="formalization_yaml", metavar="REL",
                   help="repository-relative path of the package's formalization.yaml")
    s.add_argument("--status", choices=STATUSES,
                   help="manual transitions only; verified-private is gate's "
                        "and public is promote's")
    s.add_argument("--reason", default="", help="required for --status and --force")
    s.add_argument("--redetect", action="store_true",
                   help="re-read lean_toolchain and mathlib_pin from the repo")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--force", action="store_true",
                   help="override a live lease held by another agent; needs --reason")
    add_json(s)
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("gate", help="validate a lower-tier receipt; "
                                    "gate-pending -> verified-private")
    s.add_argument("name")
    s.add_argument("--receipt", required=True, help="path to receipt JSON")
    s.add_argument("--comparator", default=None, metavar="PATH",
                   help="optional comparator receipt JSON; "
                        "required later by promote")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when a receipt holds private strings; "
                        "default is a WARN line")
    add_json(s)
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("comparator", help="attach a comparator receipt to a gated "
                                          "package; no status change")
    s.add_argument("name")
    s.add_argument("--receipt", required=True, help="path to comparator receipt JSON")
    s.add_argument("--check", action="store_true",
                   help="validate only; record nothing (works at any status)")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when the receipt holds private strings; "
                        "default is a WARN line")
    add_json(s)
    s.set_defaults(fn=cmd_comparator)

    s = sub.add_parser("promote", help="promotion checklist; DRY-RUN unless --yes")
    s.add_argument("name")
    s.add_argument("--yes", action="store_true",
                   help="execute: gh repo edit --visibility public (ONE-WAY)")
    add_json(s)
    s.set_defaults(fn=cmd_promote)

    s = sub.add_parser("audit", help="sweep expired leases; reconcile gh; drift checks")
    s.add_argument("--fix", action="store_true",
                   help="update registry visibility/archive state from gh")
    add_json(s)
    s.set_defaults(fn=cmd_audit)

    s = sub.add_parser("validate", help="schema + enum + history sanity; exit 0/1")
    add_json(s)
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("llms", help="print the agent-oriented usage contract")
    add_json(s)
    s.set_defaults(fn=cmd_llms)

    s = sub.add_parser("wiki", help="render a Markdown status page")
    s.add_argument("--out", default=None,
                   help="write the Markdown here instead of stdout")
    s.add_argument("--candidates", default=None,
                   help="adoption ledger path (default: lean-adoption-candidates.yaml "
                        "next to the registry)")
    add_json(s)
    s.set_defaults(fn=cmd_wiki)

    from . import index as index_mod
    index_mod.add_parsers(sub, add_json)
    from . import claims as claims_mod
    claims_mod.add_parsers(sub, add_json)
    from . import search as search_mod
    search_mod.add_parsers(sub, add_json)
    from . import formalization as formalization_mod
    formalization_mod.add_parsers(sub, add_json)

    ap.set_defaults(fn=cmd_board)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except UsageError as e:
        print(f"lean-pkg: {e}", file=sys.stderr)
        return 2
    except Fail as e:
        if getattr(args, "json_out", False):
            print_json({"ok": False, "error": str(e)})
        print(f"lean-pkg: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
