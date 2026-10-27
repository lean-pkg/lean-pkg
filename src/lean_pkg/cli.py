# lean-pkg -- Lean 4 package registry: board, leases, trust gate, promotion
"""Manage Lean 4 formalization packages from one YAML registry.

The source of truth is a registry YAML file; GitHub (`gh`) is only the
visibility MIRROR. Every state transition appends BOTH a history[] entry in the
registry and one JSON line to the event log -- history is append-only, never
rewritten. See DESIGN.md for the lifecycle.

This module is the command line: main, dispatch, committed mode, the registry lock and the commands not
yet in a module of their own. The shared core they build on (Fail, the registry constants, time policy,
the git helpers, registry IO and the event log) is in core.py, which the command modules import directly.
The argparse tree is built by cli_parser.build_parser; the trust gate (and the comparator evidence path,
the descendant rules, --repo DIR) is gate.py; registry audit and validate are audit.py. This module
re-exports every name that moved out of it (#317), for outside scripts, for at least one release.

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
    register  <name> --url URL --path P     the ONLY write path for new packages;
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
    blueprint check <name> [--decls PATH]   blueprint \\lean{} names via checkdecls (read-only)
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
import fnmatch  # noqa: F401  a cli attribute for one release (#317); core.py uses it
import fcntl  # noqa: F401  a cli attribute for one release (#317); core.py uses it
import gzip  # noqa: F401  a cli attribute for one release (#317); gate.py uses it
import hashlib  # noqa: F401  a cli attribute for one release (#317); gate.py uses it
import io
import json
import os
import pathlib
import posixpath  # noqa: F401  a cli attribute for one release (#317); gate.py uses it
import re
import shlex
import shutil
import stat  # noqa: F401  a cli attribute for one release (#317); core.py uses it
import subprocess
import sys
import tempfile
import traceback
import textwrap
import unicodedata
import urllib.error
import urllib.request  # noqa: F401  a cli attribute for one release (#317); core.py uses it
import zlib  # noqa: F401  a cli attribute for one release (#317); gate.py uses it

import yaml

from .holes import SIDECAR as SIGNOFF_SIDECAR, check_definition_holes, parse_sidecar  # noqa: F401  cli attributes for one release (#317); gate.py uses them
if __name__ == "__main__":
    # `python -m lean_pkg.cli`: lean_pkg.index imports this very module (one Fail class).
    sys.modules.setdefault("lean_pkg.cli", sys.modules[__name__])


# The shared core is core.py (#317). Every name that moved there is re-exported here as the same object,
# so outside scripts and `import lean_pkg.cli` keep working; the re-exports stay for at least one release.
# Callers inside lean-pkg no longer use them: since stage 2 every module imports core.py directly
# (`from . import core`) and reaches cli.py only for the names still defined here. `import lean_pkg` is not
# the supported interface (AGENTS.md), so removing them later is not a deprecation under RELEASING.md.
# Code in core.py and in the command modules reads core's globals: rebinding or monkeypatching one of these
# names here is not seen there (tests patch both: conftest.patch_core).
from .core import (  # noqa: F401  re-exports, one release (#317)
    _ACTOR, ancestor_attribute_changes, append_event, BOARD_HEADER, bound_tree, branch_notice, branch_state,
    BULK_FIELDS, check_event_logs_appendable, checker_paths, CLAIMS_COMMIT_FIRST, COMPARATOR_CONFIG_FIELDS,
    COMPARATOR_FIELDS, COMPARATOR_LIST_FIELDS, COMPARATOR_MECHANISMS, comparator_pass, COMPARATOR_PASS_LINE,
    COMPARATOR_SCHEMA, COMPARATOR_SCHEMAS, COMPARATOR_TOOLCHAIN_DIGESTS, COMPARATOR_TOOLS, current_branch,
    DEFAULT_AXIOMS, default_branch, DEFAULT_REGISTRY_NAME, DEFAULT_TTL_HOURS, detect_mathlib_pin,
    detect_toolchain, DOCS_ALWAYS_REFUSED, DOCS_ANCESTOR_FILES, DOCS_BUILD_BASENAMES, docs_build_input,
    DOCS_PATHS_MAX, DOI_RE, ensure_interactive, _err_key, EVENT_KEYS, _event_line, EVENT_LINE_KEYS,
    event_line_problems, EVENT_LINE_VERSION, event_line_version, _event_log_problem, EVENTS_ENV, EVENTS_NAME,
    events_path, events_source, EVIDENCE_EVENTS, evidence_symlinks, Fail, find_reuse, _fingerprint,
    flush_events, formalization_rel, _GATE_EVENT_KEYS, get_pkg, gh, gh_nwo, git, git_env_set, git_head,
    GIT_REPO_ENV, _git_show_bytes, git_show_bytes, GITHUB_URL_RE, _HEX40, HOME, _is_ancestor, is_evidence_path,
    is_naive_ts, lake_artifact, LAKE_ARTIFACT_SUFFIXES, LEAN_LIB_RE, lean_pkg_home, LEAN_PKG_HOME_ENV,
    lean_root_namespaces, lease_actor, LEASE_CONTACT_MAX, _LEASE_EVENT_KEYS, lease_expiry,
    lease_expiry_or_none, LEASE_FIELDS, lease_invalid, lease_live, lease_live_or_none, LEASE_OPTIONAL_FIELDS,
    LEASE_PARSE_ERRORS, LICENSE_NAMES, linked_changes, linked_evidence_changes, live_history, load_registry,
    local_path_key, lock_path, LOCK_SUFFIX, _LOCKS_HELD, LOG_ONLY_EVENTS,
    LOWER_RECEIPT_SCHEMA, _LOWER_RECEIPT_SCHEMA_RE, LOWER_RECEIPT_SCHEMAS, materialize_snapshot, MAX_TTL_HOURS,
    naive_timestamps, namespace_clashes, namespace_notes, nested_exclusions, nested_label,
    normalize_local_path, notify, NOTIFY_ENV, now_iso, PACKAGE_FIELDS, PALOMAR_REGISTERED_STATUSES, parse_ts,
    _PENDING_EVENTS, print_json, PRIVATE_EVIDENCE_HINT, RECEIPT_FIELDS, record, reference_branch,
    refuse_invalid_lease, REGISTER_STATUSES, registered_nested_dirs, REGISTRY_ENV, registry_git,
    registry_home_dir, registry_lock, registry_not_found, registry_path, registry_snapshot_arg,
    registry_source, REGISTRY_VERSION, repo_path, report_preexisting, require_nonempty, REQUIRED_FIELDS,
    RESERVOIR_API, reservoir_listing_status, RESERVOIR_URL_RE, resolve_tree_path, _RESOLVED_SOURCE,
    RETRACT_EVENT, retracted_entries, _rev, RULE_HINTS, run, save_registry, scrubbed_env, sha256_bytes,
    SNAPSHOT_ENV, SNAPSHOT_REFUSAL, split_new_errors, STATUSES, SUPPORTED_REGISTRY_VERSIONS, symlink_targets,
    tracked_at, TRACKS, _umask, under_any, UPDATABLE, UPDATED_FROM_FIELDS, UPSTREAM_TARGETS, UsageError,
    user_default_registry, utc_today, V2_PACKAGE_DEFAULTS, V3_PACKAGE_DEFAULTS, VISIBILITIES, warn_lease,
    _write_event_lines,
)
from . import core as _core

# Names core.py rebinds at run time (a `global` statement there): a re-export above would keep the first
# object, so lean_pkg.cli reads them from core.py on every access instead (PEP 562 module __getattr__).
# tests/test_core_split.py keeps this tuple equal to the `global` statements in core.py.
_CORE_REBOUND = ("_LOADED_PACKAGES",)


def __getattr__(name: str):
    if name in _CORE_REBOUND:
        return getattr(_core, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# gate.py, audit.py and cli_parser.py (#317, stage 3): the gate, audit/validate and the argument parser
# moved out of this module; the same re-exports as core's above (the same objects, one release), and the
# same caveat: code there reads its own module's globals, so patch a name where the code reading it lives.
from .gate import (  # noqa: F401  re-exports, one release (#317)
    apply_comparator_copy, attached_ignored_evidence, binding_lines, build_provenance_line, cfg_name,
    check_evidence_symlinks_at, check_lower_receipt_schema, check_path_deps_at, check_timestamp,
    check_toolchain_at, _CHUNK, ci_check_lines, claim_axioms_lines, claims_problems, claims_schema_at,
    claims_schema_line, cmd_gate, committed_mathlib, committed_mathlib_rev, comparator_check_lines,
    COMPARATOR_CONFIG_RE, copy_comparator, dirty_guard, dirty_tracked, do_comparator, _do_comparator, do_gate,
    _do_gate, do_gate_check, _do_gate_check, do_gate_refresh, docs_admission, docs_admission_line,
    docs_only_descendant, escaping_symlinks, evidence_dest_problem, evidence_only_descendant, _file_sha256,
    formalization_derived_only, gunzip_digest, gzip_deterministic, head_comparator_configs, load_json_object,
    locate_evidence_arg, manifest_mathlib, NO_MATHLIB, outside_path_deps, package_binding, package_descendant,
    path_dep_tree_problems, pin_changes_text, pins_kept_lines, plan_comparator_copy, plan_report_log_copy,
    PRIVATE_WARN_OUTCOME, receipt_check_lines, receipt_job_slug, receipt_pin_changes, receipt_private_strings,
    recorded_json, recorded_report_log_problem, refresh_preflight, regate_lineage, regate_status_problem,
    REGATE_STATUSES, _remote_abs_path, replaced_comparator_rel, repo_override, repo_override_note,
    report_compressed_bound, REPORT_GZ_DEFAULT_CAP, REPORT_GZ_LEVEL, REPORT_GZ_SUFFIX, report_log_check,
    REPORT_LOG_MISSING, report_log_missing, report_log_name, report_log_rels, REPORT_LOG_RESTORE,
    REPORT_LOG_SUFFIX, _report_log_verify, RL_REMINDER, sacct_seal_lines, SACCT_SEAL_RESTORE, _safe_rel,
    _same_remote, _same_rev, sealed_log_text, SEALED_LOG_TEXT_CAP, sidecar_at, to_gate_pending_cmd,
    toolchain_advisories_of, validate_comparator, validate_receipt, warn_once, warn_private, _WARNED,
    _with_private_warnings,
)
from .audit import (  # noqa: F401  re-exports, one release (#317)
    audit_count_line, audit_counts, AUDIT_INFO_CHECKS, AUDIT_LEVEL_WIDTH, AUDIT_LEVELS, audit_registry,
    build_inputs_errors, CLAIMS_COVERAGE_EXEMPT_STATUSES, classify_gh_failure, cmd_audit, cmd_validate,
    DEFAULT_TOOLCHAIN_FLOOR, find_moved_checkout, floor_applies, FLOOR_EXEMPT_STATUSES, FLOOR_WARN_STATUSES,
    gh_check_code, _GH_NO_ACCESS_RE, _GH_NOT_FOUND_RE, _GH_UNAUTH_RE, missing_path_detail,
    NO_GITHUB_MIRROR_DETAIL, parse_lean_toolchain, remote_reachable, render_finding, render_findings,
    resolve_toolchain_floor, status_visibility_mismatch, TOOLCHAIN_FLOOR_ENV, _TOOLCHAIN_RE,
    validate_registry,
)
from .cli_parser import (  # noqa: F401  re-exports, one release (#317)
    build_parser, _defaults_except, _hidden_copies, _Parser,
)


# --------------------------------------------------------------------------
# registry snapshot: run one read-only invocation against the registry as of a commit
# (materialize_snapshot, which builds it, is in core.py)

SNAPSHOT_REFUSED = ("verify",)   # not a write itself, but its steps write (their own invocations)


def run_snapshot(args, commit: str) -> int:
    """Run a read-only invocation against the registry as of COMMIT (#34). Every invocation that would
    write the registry or its event log (is_write_invocation; hence also every commit and push) is refused
    with exit 2 before anything is read; a --json payload that is one object gains registry_snapshot."""
    json_out = getattr(args, "json_out", False)
    args.snapshot_read_only = True   # `board` on a tty prints the static table, never the (writing) TUI
    if is_write_invocation(args) or getattr(args, "cmd", None) in SNAPSHOT_REFUSED:
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


# --------------------------------------------------------------------------
# leases (the pure lease-time helpers, lease_expiry to lease_live_or_none, are in core.py)

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
        return (f"lease: EXPIRED {human_delta(-left)} ago ({agent}); renew no longer works: lean-pkg lease claim {name} "
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
        raise argparse.ArgumentTypeError(f"not a number of hours: {v!r}") from None
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
        raise Fail(f"{name}: no lease held — claim it: lean-pkg lease claim {name} --workspace W --agent {agent}")
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
    skipped = 0
    for line in events_p.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
            if not isinstance(e, dict):
                continue
            v = event_line_version(e)
            if v is None or v > EVENT_LINE_VERSION:
                skipped += 1        # a newer lean-pkg wrote it: not read, and said so below
                continue
            if e.get("event") == "prior-art-searched" \
                    and e.get("name") == name and parse_ts(e.get("ts")) >= cutoff:
                return True
        except (ValueError, TypeError):
            continue
    if skipped:
        print(f"lean-pkg: warning: {events_p}: {skipped} event line(s) of a version newer than "
              f"{EVENT_LINE_VERSION} (or an unreadable `v`) not read; upgrade lean-pkg to read them",
              file=sys.stderr)
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
    ("verified-private", "public"): "lean-pkg publish --yes",
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
            require_nonempty("--url" if field == "repo" else "--owner-project", value)
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
                        "public": "lean-pkg publish --yes"}.get(status)
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
        nxt += [f"comparator-receipt --repo {repo}   # at HEAD {h12}",
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
# register — the ONLY write path for new packages (agents never hand-edit yaml)

def do_register(reg_path, reg, events_p, name, repo, local_path, owner_project,
                visibility, status, upstream_target, automation, notes,
                workspace, agent, dry_run, reason: str = "", like: str | None = None,
                like_copied: list[str] | None = None, license_spdx: str | None = None) -> tuple[list[str], dict]:
    if license_spdx is not None and not spdx_expression_ok(license_spdx):
        raise Fail(f"--license-spdx must be an SPDX identifier such as Apache-2.0 or MIT, or an SPDX "
                   f"expression such as 'MIT OR Apache-2.0', got {license_spdx!r}")
    if status not in REGISTER_STATUSES:
        raise Fail(f"register cannot set status {status!r} (allowed: "
                   f"{', '.join(REGISTER_STATUSES)}); verified-private is reached through "
                   f"`lean-pkg gate`, public through `lean-pkg publish --yes`")
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
    require_nonempty("--url", repo)
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
    if license_spdx is not None:
        entry["license_spdx"] = license_spdx
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
           **({"like": like} if like is not None else {}),
           **({"license_spdx": license_spdx} if license_spdx is not None else {}))
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
            mapped = cc.mapped_names(claims)
            ccommit = str((comp or {}).get("commit") or "")
            try:
                smoke = cc.smoke_configs(repo, ccommit if _HEX40.fullmatch(ccommit) else None, "HEAD")
            except Fail:                                    # a malformed sidecar: count no coverage
                smoke = {c.get("path") for c in cfgs}
            judging = {n for c in cfgs if c.get("path") not in smoke   # a smoke check credits no claim
                       for n in (c.get("theorem_names") or []) if isinstance(n, str)}
            for c in decl_claims:
                hit = cc.claim_covered(judging, c, qualified, mapped)
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


def status_producer_skew(pkg: dict, repo: pathlib.Path | None) -> dict | None:
    """producer_skew.skew for status: the lean-pkg that wrote each gate receipt against the running one. Never
    raises (status never refuses over it): an internal error yields None, and a debug traceback."""
    from . import producer_skew
    try:
        return producer_skew.skew(pkg, repo)
    except Exception as e:   # noqa: BLE001 -- an informational check never breaks status
        if _env_on(DEBUG_ENV):
            traceback.print_exception(type(e), e, e.__traceback__)
        return None


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
    skew = status_producer_skew(pkg, repo)
    if args.json_out:
        print_json({"ok": True, "name": args.name, "package": pkg,
                    "history_count": len(hist), **omitted, "branch": branch,
                    "lease_state": {"state": state, "seconds_left": None if left is None else int(left)},
                    "scope": scope, "producer_skew": skew})
        if skew is not None:
            from . import producer_skew
            for w in producer_skew.warn_lines(skew):
                print(f"lean-pkg: {w}", file=sys.stderr)
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
    if skew is not None:
        from . import producer_skew
        for line in producer_skew.info_lines(skew):
            print(line)
        if skew.get("data_problem"):
            print(f"  WARN {skew['data_problem']}")
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
        raise Fail(f"ttl must be a whole number of hours, got {t!r}") from None
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
            raise Fail(f"cannot write {out_p}: {e.strerror or e}") from e
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
- Command names are the 1.0 tree (`lean-pkg help aliases`): old spellings (claim, release, leases, init, audit, board, promote, comparator NAME --receipt R, release --fresh-repo, comparator-receipt, ...) are permanent aliases with the same output and exit codes; text mode notes the new spelling on stderr (LEAN_PKG_NO_ALIAS_NOTE=1 silences).
- One package on one machine: `lean-pkg verify NAME` runs the comparator receipt, the lower-tier receipt (systemd user unit) at the same commit and the gate, in that order, each as its own invocation; commit .lean-receipts/ afterwards; `page NAME --write`; `publish NAME` (read-only checklist), `publish NAME --yes` (public; ONE-WAY).
- Before starting Lean work on a package: `lean-pkg registry board --json` (or `list --json`; one package: `status NAME --json [--fields ...]` or `list --json --only NAME`). Writing a paper alongside: check selected intermediate results as they stabilise (docs/paper-companion.md); never assume the target theorem to gain coverage; a certificate covers its exact formal statements, not the whole paper.
- Claim before working (`lease claim`): one package = one workspace. Default TTL 24h; `registry audit --write` sweeps expired leases. Work longer than the TTL: `lease renew` before it lapses (`lease list --agent A --expiring 2`). gate, comparator attach, record, publish --yes, claims sync, search --record, verify: pass --agent A (another agent's live lease refuses; A is the event's `actor`); without it a live lease only warns.
- Mutating ops are dry-run by default unless a flag says otherwise: publish --yes, registry audit --write.
- Fully non-interactive: pass flags, never wait for a prompt. Use --json for machine parsing.
- Shared registry in git: --commit (env LEAN_PKG_COMMIT=1) pulls --ff-only, writes, commits the yaml + event log together; --push (LEAN_PKG_PUSH=1) also pushes (a transient transport error such as a connection reset, timeout or HTTP 5xx is retried with backoff: LEAN_PKG_PUSH_RETRIES, default 4, delays LEAN_PKG_PUSH_RETRY_DELAY doubling from 5 s; rejected as non-fast-forward: fetch + `rebase --autostash` of that one commit + push again, an event-log-only conflict merges as a union in timestamp order, any other conflict aborts the rebase with the clone as before, `; rebase conflict in FILES` in the error; a rebased registry with a schema error neither side had is not pushed (reset to the local commit, `; rebased registry fails validation (...)`); a failed push: `push failed: origin/BRANCH: ERR`, commit kept, exit 1, recovery printed (after a rejection: git pull --rebase origin BRANCH, then git push origin BRANCH); a write whose event log cannot be appended to (read-only, a directory) refuses before the registry is replaced, nothing written; a --push write refuses while the clone is ahead of origin/BRANCH); --no-commit overrides; a committed-mode write refuses over modified registry files, naming the uncommitted event (event, package, agent, time), the package's lease holder and the git commit that clears it; a write without committed mode that leaves the registry files in a git clone modified ends with stderr `NOT COMMITTED: FILES modified in CLONE; every --commit/--push write to this registry refuses until they are committed: ...` (--json + registry_uncommitted [files]). stderr: `committed SHA on BRANCH: MSG` (+ `(committed mode: env VAR=1)` when the env, not a flag, turned it on), `pushed origin/BRANCH (SHA)`; `CMD --help` says `set now` when a variable is set. Every write is covered (index add, formalization --write, claims sync, search --record, board TUI keys); dry runs and read-only commands never fetch (an explicit --commit there: stderr "ignored"). Reports on stderr; a --json payload gains "registry_commit": {sha, pushed, error, push_error, push_attempts, rebased} (also as "commit" unless the payload has its own, e.g. gate's source commit).

STATUS ENUM AND TRANSITIONS (evidence required)
scratch -> active                  owner_project named
active -> gate-pending             lower-tier check submitted (terminal executor job identity in notes); also verified-private -> gate-pending to re-gate a new commit (one step instead: `gate --regate`)
gate-pending -> verified-private   `gate` accepts a receipt (schema below)
public -> public                   `gate` on an already-public package records evidence; status unchanged
verified-private -> public         `publish --yes` after a clean `publish --yes --dry-run` checklist; ONE-WAY flip
public -> upstreamed               `update --status`: a recorded mathlib-pr-merged / palomar-registered event, or a --reason with the URL
any -> archived                    `update --status archived --reason` or `registry audit --write` (gh isArchived); public returns to a private status only this way: public -> verified-private by `update --withdraw-publication --reason` only: the owner made the repository private (gh must not report it public; active when never gated); visibility private + publication_hold {reason, at, by}; promotion history kept; publish --yes refuses while held; gate/regate/registry audit --write keep it; only `update --release-publication-hold --reason` lifts it

SUBCOMMANDS (global flags: --registry PATH --events PATH --json --commit --push --no-commit, also after the subcommand; env LEAN_PKG_REGISTRY, LEAN_PKG_HOME, LEAN_PKG_EVENTS (ignored when --registry is given: its sibling log); --version: `lean-pkg X.Y.Z (<sha>[+dirty], editable <checkout>)` or `(installed <dir>)`, local only; --json {version, commit, dirty, install, path}; --registry-snapshot COMMIT (env LEAN_PKG_REGISTRY_SNAPSHOT): read-only, the registry + event log as of COMMIT of its git repo (git show into a temp copy); reads work (status/list/registry board/registry audit/dry runs/comparator config --dry-run), every write exits 2 `read-only: --registry-snapshot`; --json +registry_snapshot sha)
registry board  TUI on a tty, static table otherwise; --only, --fields, --no-claims as for list (any: never the TUI). ex: lean-pkg registry board --json --only my-lean
list      static table; --only NAME[,NAME] filters packages (full records, shape unchanged); --fields a,b.c trims each record as status --fields (+ `fields` list; text: YAML); --no-claims as status. ex: lean-pkg list --json --only my-lean --fields status
status    full record + last 5 history entries (a lease adds derived expires_at, UTC; a lapsed lease ends the output with `lease: EXPIRED ...`, one lapsing within 2h with `lease: expires in ...`; --json lease_state {state live|expiring|expired|invalid|none, seconds_left}); --fields a,b.c: only those keys (dotted paths; lease.contact: null when unset; aliases = the table columns: pin, vis, reaudit, expires|lease.expires, lease.claimed; --json keeps nesting, text YAML; unknown: exit 2 listing keys); record keys: status --help, docs/cli.md#package-record. --no-claims: drop [bulk] keys (claims; also list/board; +omitted[]). Without --fields: a `scope:` text block and --json scope {gate {receipt, commit, head, head_is_gated_commit, readable}, publication {status, visibility, publication_hold, github}, product {kind paper|library|null, source, note}, comparator null|{receipt, readable, commit, mechanism, configs[], theorem_names[], definition_names[], claimed_theorems, claimed_theorems_covered, reusable_theorems, reusable_theorems_covered, whole_package false, note}, claims {total, by_status, synced_commit, note}}: the gated source commit, recorded privacy + hold (offline: gh not queried), the product (kind in coverage.yaml at HEAD; paper when absent; not the DOI track), the exact declarations the comparator receipt compared and the claimed theorems/lemmas they cover (never the whole package), claim counts by status (not completion percentages). Also without --fields: a `producer` text block and --json producer_skew {running, receipts[] {role lower-tier|comparator, receipt, readable, producer (its producer.lean_pkg, or unknown), producer_record, direction unknown|same|older|newer, breaking[] {id, version, pr, kind, area}, other_breaking[], level info|warn, message, command}, warnings[], skew, data_problem}, null on an internal error: the lean-pkg that wrote each gate receipt against the running one; WARN (text block, and stderr `lean-pkg: WARN: producer skew: ...` with --json) when an older producer lies before a breaking upgrade-record entry in an evidence area (receipts, gate, claims; src/lean_pkg/data/upgrade-breaking.yaml), naming the entry ids and `lean-pkg doctor --upgrade-from vPRODUCER --source DIR`, and always for a producer newer than the running lean-pkg (it may ignore evidence it does not know); an older producer with no such entry, the same version or no producer is info only; never a refusal. ex: lean-pkg status my-lean --json --fields status,gate.receipt,lease.agent
new       scaffold a minimal Lean package in a new directory (refused when it exists and is not empty) and git init it; no registry read or written, nothing committed, fetched or built: lean-toolchain (--stack NAME|current|none: a declared LTS stack's toolchain and Mathlib tag, docs/stacks/, plus `stack: NAME` in coverage.yaml; default current for --kind paper (no stack current: the newest pins, warned), none for --kind library: the newest pins, the newest stable Lean release lean-pkg ships with its Mathlib tag (v4.35.0), a release candidate only when no stable release is newer, and then said in the output; an undeclared stack exits 2 listing the declared ones), .gitignore, lakefile.toml (library MOD = NAME in CamelCase, --module MOD, defaultTargets [MOD] so a bare `lake build` builds it, globs [MOD, MOD.+]; requires Mathlib unless --no-mathlib), MOD.lean (the library's root module, empty), MOD/Challenge.lean (sorry) + MOD/Solution.lean (module-headed, the getting-started template) + MOD/Axioms.lean (#print axioms), comparator.json, claims.yaml (one reusable claim), coverage.yaml (--kind paper, the default: kind paper, manuscript version unposted and FILL-ME markers that coverage check refuses until the author names the paper and `formalization anchor --write` binds the posted version, no manuscript invented; --kind library: one main result, passes coverage check), CITATION.cff (--author, else git user.name; a name with no space warns: it looks like a handle), LICENSE + LICENSES/<SPDX>.txt (--license Apache-2.0|MIT), REUSE.toml (every file, receipts and logs included, under that license: reuse lint passes), README.md (the lean-pkg credit), verify.sh (the log `receipt systemd --run` reads; --no-mathlib: a `mathlib-rev: none` line, which the receipt also derives from a lake-manifest.json with no mathlib package), lean-pkg-binding.yaml (docs_descendants: true with a comment: the committed docs-only opt-in, see gate; --evidence-only leaves it out); NAME [a-z][a-z0-9]*(-[a-z0-9]+)*, at most 64 (else exit 2); --json {ok, name, dir, kind, module, mathlib, lean_toolchain, mathlib_rev, release_candidate, stack, stack_source, license_spdx, author, author_source, files[], git_init, committed, registry_touched, coverage_complete, next_commands[], warnings[], binding_file (lean-pkg-binding.yaml or null)}. ex: lean-pkg new my-proof (a paper), lean-pkg new my-lib --kind library; the path to a page: docs/quickstart.md
registry init  bootstrap an empty registry + event log (event: file name + version; --force: empty only, forced: true). ex: lean-pkg --registry ./lean-packages.yaml init
registry migrate  upgrade the registry schema in place to v3 (v2 adds license_spdx, formalization_yaml, palomar[], doi, reservoir, consent[];
          v3 adds claims_file, claims[], claims_synced_commit; additive, idempotent; a legacy literal ~/x local_path is rewritten $HOME-relative). ex: lean-pkg migrate --dry-run
record    record a D5 evidence event; validated before writing (a schema error it introduces refuses, rule named, nothing written); --field keys (all required, others refused; commit 40-hex, version integer):
          consent-recorded: who role pointer (a /home, /Users, ~ path: WARN + private_strings; same who+role+pointer again: already recorded, exit 0) | palomar-preflight-pass: commit run (same commit+run again: already recorded, exit 0)
          palomar-submitted: commit submission_id comparator_config | palomar-review: commit outcome(neutral|revision_required|rejected)
          palomar-registered: commit id version(int) comparator_config (status public|upstreamed; config tracked at HEAD) | palomar-withdrawn: id version(int) (of a recorded entry) | mathlib-pr-opened, mathlib-pr-merged: url (https://github.com/...)
          doi-minted: doi(10.NNNN/x) tag commit [concept] (rule doi-in-palomar: commit already recorded by a palomar-* event; skipped on the DOI-only track, --track doi-only or the record's track: doi-only, status public only, rule doi-only-public; the event carries track) | reservoir-listed: url (the
          package's https://reservoir.lean-lang.org/@owner/name page; Reservoir API: 404 refuses, unreachable records verified=false; a url naming neither package nor repo warns)
          retract: event ts + --reason: append-only correction (event evidence-retracted); validate and the upstreamed check ignore the entry
          ex: lean-pkg record my-lean doi-minted --field doi=10.5281/zenodo.1 --field tag=v1.0.0 --field commit=<40hex>
register  add a package; dedupes by name AND resolved local_path (~/ expanded, stored $HOME-relative; empty --path/--url refused); auto-detects toolchain + mathlib pin; a Lean root namespace (lakefile lean_lib, else top-level *.lean) another package's checkout also uses: stderr note + `notes`, never a refusal. --like OTHER: a sibling package; copies repo/owner-project/upstream-target/automation from OTHER (flags win; never visibility), refused when DIR is in another git repo and --url is absent; notes whether toolchain/Mathlib pin match OTHER's (JSON like {name, copied[], toolchain, mathlib_pin}). warn: DIR inside or containing another package's dir; a lake path dependency outside DIR.
  flags: --url URL (required; old spelling --repo) --path HOME-RELATIVE (required) [--license-spdx SPDX] [--owner-project N]
         [--visibility private|public] [--status scratch|active|gate-pending|public (public: --visibility public --reason R)]
         [--upstream-target none|mathlib|palomar] [--automation TEXT] [--notes TEXT] [--workspace W] [--agent A] [--dry-run]
  --workspace/--agent are metadata only; they do NOT claim. Claiming is a separate act.
  ex: lean-pkg register my-lean --url git@github.com:you/my-lean.git --path src/my-lean --license-spdx Apache-2.0
update    set fields (--notes over non-empty notes: exit 2, nothing written, unless --replace-notes, which replaces, prints the old value, event notes_replaced + notes_from; --append-note TEXT --agent A appends `YYYY-MM-DD (A): TEXT` and prints only `appended note: <line>` (--json changes.notes keeps from/to), not with --notes); manual status moves and --force need --reason (else exit 1; a refusal lists the allowed moves); empty --url/--owner-project refused; --license-spdx takes an SPDX expression (MIT OR Apache-2.0); --paper-arxiv ID / --paper-status unpublished|preprint|published set the optional paper {arxiv, status} (an id alone: preprint). --source-paper-title T --source-paper-author A (repeatable) --source-paper-arxiv ID --source-paper-doi DOI / --ai-tool T --ai-role R (repeatable): optional source_paper {title, authors[], arxiv, doi} / ai_assistance {tools[], roles[]}, rendered as formalization.yaml acknowledgements lines (never project.authors; no consent needed). --namespace-note TEXT [--namespace NS]: namespace_notes {NS: TEXT} (default NS: those shared with another package); audit duplicate-namespace is info once all sharers have one. --track palomar|doi-only --reason R: the optional DOI track (see record doi-minted). --accept-docs-descendants | --no-accept-docs-descendants --reason R: the optional docs_descendants true/false (opt in to the docs-only rule, see gate; --accept-docs-descendants is deprecated: stderr "WARN: deprecated: ..." and JSON deprecations[] {id registry-docs-descendants, message, replacement, earliest_removal 1.0.0, removal_rule}; the veto is not); --write-binding-file: write lean-pkg-binding.yaml into the package dir from the record's docs_descendants: true and build_inputs (the migration; never staged, the registry not written, not a registry write for committed mode; record not opted in: nothing written, exit 0; identical file: no-op; a different file, a symlink or a dir: exit 1, untouched; --dry-run prints it; with a field, --status or --path: exit 2; JSON {ok, name, event binding-file-written|binding-file-unchanged|binding-file-dry-run|binding-file-not-opted-in|binding-file-refused, state, path, written, dry_run, docs_descendants, build_inputs, content, advice, problem}; commit it on its own right before the next verify run: a file added after the receipt commit unbinds the current receipts until then); --build-inputs GLOB,GLOB --reason R: the optional build_inputs list ('' clears); --headline-config REL: the optional headline_config (the headline comparator configuration formalization renders as status.main_results; '' clears); these three accept --dry-run (event update-dry-run, nothing written). register/update/record: schema errors already in the registry (e.g. another package's) go to stderr as pre-existing, not blocking.
  ex: lean-pkg update my-lean --status gate-pending --reason "check job 4242 submitted"
  --withdraw-publication --reason TEXT [--agent A] [--dry-run]: record the owner's privacy withdrawal of a public package (see the transitions; event publication-withdrawn {status_from, status_to, visibility_from, visibility_to, reason, by, github_visibility, promoted_commit, forced}; JSON status/visibility {from, to}, publication_hold, github_visibility, github_check). --release-publication-hold --reason TEXT [--agent A] [--dry-run]: lift it, status/visibility unchanged (event publication-hold-released {reason, hold, by, forced}). Each on its own (other fields: exit 2).   |   --path NEW --reason TEXT [--agent A] [--redetect] [--dry-run]: relocate (same name, history, claims; event relocated). Same path: no-op,
  exit 0. Refuses another package's path, a foreign live lease (no --force). Gate/claims not bound to NEW's HEAD are cleared (`cleared`). Output ends with a binding block (receipt commit vs new HEAD, evidence-only verdict, formalization.yaml exception) + `next:` commands (or "nothing to re-gate"); JSON binding{} + next_commands[].
lease claim  (old spellings: claim, lease NAME) CAS lease; refuses if another agent holds an unexpired lease (names holder + expiry). Any other command naming a package whose lease has lapsed, or lapses within 2h, prints stderr `WARN: NAME lease: EXPIRED ... | expires in ...` with the claim/renew that keeps it (not claim/renew/release/leases/audit/status). Writes take a file lock on <registry>.lock (sibling); safe across processes on one host.
  flags: --workspace W --agent A (required) [--ttl HOURS: 1..8760, 24 new; a holder's re-claim keeps its TTL; above 8760: exit 2, nothing written] [--reason TEXT] [--contact TEXT: address of the holding session (e.g. lab-desk/n14-paper, @lean-ops), stored as lease.contact; one line, <= 200 chars, else exit 2; a re-claim without it keeps it] [--force (needs --reason)]
  ex: lean-pkg lease claim my-lean --workspace w3 --agent vera --reason "chapter 2"
lease release  (old spelling: release) holder-only unless --force --reason (released_by: --agent, else force). ex: lean-pkg lease release my-lean --agent vera   |   repo export NAME DIR (old spelling: release NAME --fresh-repo DIR) [--remote URL] [--keep-receipts] [--source-commit SHA] [--author 'NAME <EMAIL>'] [--allow-author-email] [--dry-run] (no lease involved): commit identity (author = committer) from --author, else user.name/user.email of the source repo's LOCAL git config, never the global identity (none: refused "no identity for the export commit: pass --author ..."); an email that is not a GitHub noreply address (ID+LOGIN@users.noreply.github.com) refused unless --allow-author-email; plan line `commit identity: ...`, JSON identity {author, committer, source, noreply} -> git archive of the gate-bound commit (package dir; drops .lean-pkg-private-patterns, internal paths, .lean-receipts/ unless --keep-receipts, listed) -> public-safety scan of the tree, the commit message and the identity (blocking hit: refused, nothing written; warnings listed) -> carries ONLY the allow: lines that scan used into a committed .lean-pkg-public-allow, merged with one in the tree (unused ones dropped and counted, never printed; none used: no file), plan line `public allow: N allow line(s) ... carried`, JSON public_allow {file, carried, lines[], unused, present}; a carried allow line a private block pattern matches refuses, naming its line number -> new repo at DIR, ONE commit "fresh-history export of <sha12>; receipts and gate must be re-run on this commit" (+ source commit, export date), origin = --remote -> relocated as update --path DIR --reason "fresh-history release" (gate/comparator cleared, verified-private -> gate-pending, dated note keeps old receipt paths + source sha; --remote sets repo); refuses public/upstreamed/archived, a non-empty DIR; --dry-run: plan + next commands (gate, comparator-receipt --prepare, publish), writes nothing   |   repo export NAME DIR --from-tree SRCDIR (not with --source-commit): exports the WORKING TREE at SRCDIR (hand-cleaned, not a commit; in a checkout `git ls-files --cached --others --exclude-standard`, else every file but .git/ .lake/), same drops + scan (+ the commit message), message "fresh-history export of working tree <basename>; ..." + "source commit: none (hand-cleaned tree)" + "source package commit for provenance: <gate-bound sha|none>"   |   repo adopt NAME DIR (old spelling: release NAME --adopt-repo DIR) [--remote URL] [--allow-history] [--dry-run]: adopt an existing repo as the public history, no export, no new commit; refuses >1 commit on all refs (unless --allow-history), DIR not its top dir, the package's own source repo, modified tracked files, an origin other than --remote, blocking public-safety hits in its tree or full history; then the same relocation (reason "adopted-repository release"; note names the adopted commit + previous gate-bound sha)
lease renew  (old spelling: renew) holder-only restart of a LIVE lease (event claim-refreshed); a lapsed lease is refused: claim again. --ttl HOURS: 1..8760 (above: exit 2, nothing written). --contact TEXT sets/replaces lease.contact, omitted keeps it; --no-contact clears it (event contact_cleared: true when one was removed; with --contact: exit 2). leases [--agent A] [--expiring H]: held leases, soonest first, last column CONTACT (blank when unset; JSON contact|null); doctor warns on leases lapsing within 2h or expired-unswept. Unparseable lease fields: shown INVALID, writes refused; `release NAME --force --reason TEXT` clears.
gate      validate receipt; gate-pending -> verified-private; --receipt/--comparator PATH: a path that exists as given (cwd-relative or absolute) is used as given; a relative one missing from the cwd but present under the package checkout (or --check --repo DIR) is resolved there with a stderr note, a `· receipt R resolved against the package checkout: P` line and JSON resolved_paths[] {what, arg, path, base} (the same relative path, never another matching receipt; also comparator NAME); otherwise refused naming the caller cwd, the checkout, both paths tried and the fix (absolute path, run from the checkout, or --repo DIR with --check). --check takes no lease and checks none, so --agent with --check is a usage error (exit 2) naming the writing form. identical re-gate = no-op "already gated". A receipt whose lean_toolchain is on lean-pkg's toolchain advisories list (known Lean kernel/runtime soundness bugs, src/lean_pkg/data/toolchain-advisories.yaml): one stderr line "WARN: toolchain advisory ID: TOOLCHAIN has a known Lean soundness bug, fixed in vX.Y.Z; LINK ..." per entry and JSON toolchain_advisories[] {id, toolchain, summary, fixed_in, links[], date_added} ([] when none; the same in gate --check, receipt check and palomar check, there after the verdict line), never a refusal. gate/regate record the receipt's lean_toolchain and mathlib_rev[:10] into lean_toolchain/mathlib_pin when each agrees with lean-toolchain / lake-manifest.json at the receipt commit (a prefix match is unchanged; one "pins updated" line; event and JSON pins {field: {from, to}}); a receipt whose lean_toolchain or mathlib_rev (prefix-equal) disagrees with lean-toolchain / the mathlib rev in lake-manifest.json committed at its commit is refused; with no readable mathlib entry the rev is not checked and the pin is kept: "NAME: pins kept: receipt mathlib_rev X does not match lake-manifest.json at <c12> (unreadable)", JSON pins_kept {field: {receipt, manifest}}; --check reports both, nothing written. report_sha256 must equal the SHA-256 of <c12>-<job slug>-verify.redacted.log wherever found (beside the receipt, .lean-receipts/, tracked at HEAD), else refused; a <name>.gz copy (receipt --compress-log) is stream-decompressed and its UNCOMPRESSED bytes must hash to report_sha256 (never the .gz hash), the .gz to report_compressed.sha256 and the length equal report_compressed.bytes when recorded, output past that refused (gzip-bomb guard); none found (or a copy unreadable) is refused too: "report log missing: <name> ...", naming where it looked and the path to restore it to (docs/receipts.md "Restoring preserved evidence"); the same check runs wherever a recorded lower-tier receipt is judged (ledger/promote, next, comparator, palomar, index check); JSON report_log {checked, log, where[], copy[]}; the sealed log beside --receipt is copied beside the canonical .lean-receipts/<c12>-<job>.json when no copy is there, so it verifies on its own. Every check of both copies runs before either is written (a refusal writes nothing); a symlinked .lean-receipts or destination file is refused. --regate [--reason T]: re-gate verified-private (or public) in one step, status unchanged, event regated {previous_commit, commit, job_id, reason[, non_descendant]}; the commit must descend from the gated one unless --reason; plain gate on verified-private is refused naming --regate; --check --regate writes nothing. claims.yaml tracked at the receipt commit (never the working tree) must pass the claims/1 schema and every claim's decl must occur in its file at that commit (claims sync's check), else refused, nothing copied (JSON claims_schema {checked, commit, file, ok, claims, errors[], declarations {checked, ok, problems[]}}). Judged on the receipt commit's tree, refused (sources not bound): a committed symlink with an absolute or `..` target, or one resolving into .lean-receipts/ or to the formalization.yaml pointer; a lake-manifest.json `path` package whose dir leaves the package dir, or is (or passes through) a symlink, a submodule gitlink or an untracked dir (.lake/), or contains a gitlink. A tracked in-package path dependency is fine. Progress files (PROGRESS.md, state file, ledgers) live outside the package dir: any other change inside it needs a new receipt, and so does a .gitattributes/.gitmodules/.lfsconfig changed at the repo root or between it and a subdirectory package, unless the package opted in: lean-pkg-binding.yaml (schema: lean-pkg-binding/1, docs_descendants: true, optional build_inputs) committed in the package dir and read at the receipt commit C only (never the working tree, HEAD or a commit between; strict: an unknown or repeated key, an anchor/alias/tag, a non-literal true/false, a symlink/gitlink/dir or > 64 KiB does not opt in and is named; a copy under .lean-receipts/, at the repo root above a subdir package or in a nested package is not this package's), or, with no file at C, the record (update --accept-docs-descendants --reason R; docs_descendants: true); a record docs_descendants: false vetoes a committed true, and once the file exists at C it decides (a record true is then ignored): then HEAD may also be a docs-only descendant of the receipt commit C, where no build input under the package dir changed (*.lean, lakefile.*, lake-manifest.json, lean-toolchain at any depth, the build_inputs globs of the file and the record, files the receipt's checker names; always refused: a symlink or gitlink added/changed/removed, a changed file a committed symlink points to, .gitattributes, .gitmodules, .lfsconfig, claims.yaml, lean-pkg-binding.yaml at any depth, and for a subdirectory package a .gitattributes/.gitmodules/.lfsconfig at the repo root or in any directory between it and the package dir; formalization.yaml keeps its derived-keys rule). The gated commit stays C; text "✓ HEAD <c12> is a docs-only descendant of <c12> (N non-build file(s): ...; opted in at <c12> by lean-pkg-binding.yaml)" (or "...; opted in by the registry record)"); gate/regate/comparator events and --json (and --check) add descendant_of C, docs_paths[] (<= 50) and opt_in {source: committed|registry, commit: C, path: lean-pkg-binding.yaml|null}; an evidence-only admission adds none. The registry route is deprecated (removed no earlier than 1.0.0): when it admitted HEAD, gate, gate --check and comparator (attach, --check) print one stderr line "WARN: deprecated: NAME: the docs-only rule admitted HEAD through the registry record's docs_descendants: true, with no lean-pkg-binding.yaml at <c12>; replacement: `lean-pkg update NAME --write-binding-file`, then commit ..." and add JSON deprecations[] {id registry-docs-descendants, message, replacement, earliest_removal, removal_rule} (never in the event); publish (read-only checklist) does so, plus a WARN item, whenever the opt-in exists only in the record; page --write too, which then also writes lean-pkg-binding.yaml into the working tree, never staged (docs/page.md); no file and no record opt-in, or the file at C: no such line or key. A refusal under the opt-in says "docs-only rule (<source>)". Without the opt-in a refusal the docs rule would have admitted adds the hint (commit lean-pkg-binding.yaml, or `update NAME --accept-docs-descendants`); a refusal also names a file at C that does not opt in, the record's veto, and "lean-pkg-binding.yaml was added after <c12>; the opt-in is read at the bound commit only". Readers without a registry read the same file at C: `receipt check --repo DIR --commit C` (the outside referee) applies it by default (a row "PASS opt-in: lean-pkg-binding.yaml at <c12> opts in to docs-only descendants (build_inputs: N glob(s))", or SKIP naming a false/invalid file, one added after C, or --evidence-only; rule line "docs-only: the author's opt-in, lean-pkg-binding.yaml committed at <c12>"; JSON docs_descendants, evidence_only, opt_in {source: committed|referee|null, commit, path, build_inputs, file, problems}); --evidence-only ignores it; --docs-descendants is the referee's choice for a package without the file (a committed false/invalid file overrides it); both flags: exit 2; and index add / index check without a registry read it at the entry's pin (with a registry, the record precedence above).
  flags: --receipt PATH (required), --comparator PATH (optional), --refuse-private (private paths in a receipt: refuse, exit 1,
  nothing copied; default a stderr WARN "receipt field F contains a ... (kept: ...; --refuse-private refuses such a receipt)" + JSON private_strings[{field, kind}]), --refuse-dirty (modified tracked files; default WARN + JSON dirty[]), --refresh-formalization [--agent A] (then regenerate formalization.yaml + pointer in one step, JSON "formalization": {...}; lease + schema checked BEFORE the gate; identical re-gate with a current file: refused, nothing written; no pointer recorded yet: the derived-only formalization.yaml exception applies to the path it will own), --check (read-only, any status: every gate validation vs HEAD, both tiers with --comparator; ✓ lines or gate's refusal; claims.yaml at the receipt commit: ✓ schema ok / ✗ first error (exit 1) / · not tracked; no write, no event; exit 0/1; JSON event gate-checked, checks[], claims_schema; comparator --check prints the same binding ✓ lines), --check --repo DIR (validate against the checkout DIR, not the registered local_path, this call only: never saved; stderr note; JSON repo_override {path, registered_path, remote_match}, null without it; DIR must be a git work tree and, when it has remotes, one must be the package's repo (a local-path repo /p also matches Host:/p, ssh://Host/p, file:///p; DIR = the registered local_path needs no remote), else the caller vouches; a writing gate refuses --repo, exit 2: it binds the registered checkout, move that with update --path). Success (and promote --yes) ends with a results-ledger: line (JSON rl_reminder, rl_hint): lean-pkg keeps no results ledger, the outcome is in `status NAME` and `claims ledger NAME`; add it to the project's own results ledger if it keeps one (docs/lifecycle.md#results-ledger)
  ex: lean-pkg gate my-lean --receipt /tmp/receipt.json --comparator /tmp/comparator.json   |   receipt slurm --job J --log PATH --receipt-commit SHA [--stdout JOB.out] [--stderr JOB.err] [--checker TEXT] [--repo DIR] [--redact REGEX]... [--sacct-file F] [--out PATH] [--append-readme] (no registry): writes the lower-tier receipt from a finished Slurm job; refuses (exit 1, nothing written) unless sacct's main job line is COMPLETED 0:0, the stdout's verify-log-sha256 = sha256(raw log) and pre-manifest-sha256 = post-manifest-sha256 = sha256(lake-manifest.json at SHA) (no --stdout: the log's own `verify-log-sha256:` line = sha256 of the log minus that line), stderr is empty, lean_toolchain/mathlib_rev from SHA agree with any log line, sorry 0 and axioms from the log (sorry-count:/axioms: lines, else 'sorry' warnings/#print axioms output; a sorry-count: below the log's own 'sorry' warnings, or an axioms: line omitting an axiom its #print axioms output reports, is refused; universe suffixes .{u} ignored), a checker (--checker or checker:), a log commit: line equal to SHA (none: refused without --stdout; with --stdout a WARN "the verify log has no commit: line; it is bound to the commit only through lake-manifest.json", JSON log_commit_bound false, also in the written receipt, and a `- WARN:` line in the README entry), fields read from the RAW log (a redaction that removes or alters a sorry warning, #print axioms output, a build-completion line or a key: line other than checker: is refused; checker is published redacted), no committed symlink at SHA into .lean-receipts/, and no public-safety blocking hit is left after redacting /tmp/<account>-job, /scratch/<user>/ and --redact (blocking also this machine's user and host names and a bare /home/<x>, /Users/<x>); --append-readme: a symlinked or non-UTF-8 README.md refuses before writing; job_id slurm-<J>; writes .lean-receipts/<c12>-slurm-<J>.json + <c12>-slurm-<J>-verify.redacted.log (report_sha256; --compress-log: <...>.log.gz instead, deterministic gzip, report_sha256 still of the uncompressed log, additive report_compressed {format gzip, sha256 of .gz, bytes}, restore gzip -dc X.log.gz > X.log; without it a log over 90 MiB WARNs naming the flag; log_commit_bound true/false; with --stdout also job_stdout_sha256 + manifest_sha256; additive keys gate ignores: it never reads log_commit_bound), prints the README entry (with --stdout a `- Seal:` line naming both; --append-readme: appended to .lean-receipts/README.md) and `gate NAME --check --receipt PATH`; --json {ok, receipt, log, report_sha256, job_id, sealed[] {role, path, sha256, receipt_key} (the exact files bound; systemd's `properties` is the raw unsealed capture), checks[], refusals[], ...}; --template (no other flag, no registry): print a minimal sbatch wrapper emitting every line the receipt reads (stdout seal lines, empty stderr, checker:/sorry-count:/axioms: in the log; exact-runtime preflight before any tool runs: LC_ALL=C LANG=C PYTHONUTF8=1 (#SBATCH --export line + export; stderr stays strict, never filtered) and a `type -P` probe of the host tools (+EXTRA_TOOLS) and, under $RUN, the first word of VERIFY_CMD/AXIOMS_CMD/LEAN_PATH_CMD (+lean beside lake, +RUN_TOOLS): a missing one fails the job before the work, log line runtime-preflight: ok (...); optional artifact seal ARTIFACTS_DIR=<dir relative to the job's tree, or to $WORK with ARTIFACTS_BASE=work>: after the audit the job copies every regular file under it to ARTIFACTS_OUT (default $OUT_DIR/artifacts-<J>, never overwritten) with artifacts.sha256, sorted `<path> <size> <sha256>` lines (a symlink, special file or newline name fails the job), and prints `artifacts-manifest-sha256 <hex>` on stdout before post-manifest-sha256); --check --log PATH [--stdout] [--stderr] [--receipt-commit SHA] [--job J --sacct-file F]: preflight a job's files, every problem named, no sacct, nothing written, exit 0/1, --json {ok, log_commit_bound (true/false; null without --receipt-commit), checks[], problems[]}; --preflight-deps (--deps-tar T | --deps-dir D) [--receipt-commit SHA, default HEAD] [--entries GLOB]... (before sbatch, reads only, builds and writes nothing, exit 0/1): the import closure of the entry files committed at SHA (default Verify/**/*.lean, Audit/**/*.lean, OwnedAudit/**/*.lean, **/AxiomCheck*.lean, **/TypeCheck*.lean, **/Examples*.lean, **/OwnedAudit*.lean), followed through the package's modules and the dependency sources in the cache, must have every external module's .olean and .ilean (regular files) in exactly one manifest package (import Mathlib = Mathlib.olean and everything Mathlib.lean imports, transitively; toolchain roots Init/Std/Lean/Lake aside), every manifest git package present at its rev (.git/HEAD); --json {ok, preflight_deps, commit, entries[], required_external_modules, missing_count, missing[] {module, package, files, required_by}, checks[], problems[]}; + --fill-plan DONOR [--plan-out FILE] [--base-list FILE] (read-only; steps 1-4 of docs/receipts.md "Filling missing dependency artifacts from a pinned donor"): every missing module (no 500 cap), each with every regular file DONOR holds under its path in the package's .lake/build or build (lib/lean, lib, ir: <path>.<any extension>) with size and sha256; refuses (exit 1, refusals[] {code, reason}) on base-problem (a preflight problem other than missing files: not fillable), donor-cache, donor-toolchain (DONOR/lean-toolchain not the commit's byte for byte), donor-manifest (a manifest package not at its rev, the preflight's pin check), donor-source-edits (git status --porcelain --untracked-files=no non-empty, an index entry marked assume-unchanged or skip-worktree, an untracked or ignored *.lean/lakefile/lean-toolchain/lake-manifest.json outside .lake/, no .git directory or unreadable; GIT_OPTIONAL_LOCKS=0, the checkout's core.fsmonitor off), olean-header (a candidate .olean's first 80 bytes (46 for format 1) differ from the base's, which must agree), donor-lacks-module (no .olean/.ilean in DONOR either), base-overlap (an addition is a base member, a --base-list name, or under a non-directory member), package-build (under the package's own .lake/build or a root namespace), plan-out (exists, inside DONOR/the base/.lake, unwritable); --plan-out FILE, only when ok: FILE = additions one per line (relative to the directory holding .lake, for tar -T) + FILE.provenance.json (schema lean-pkg-fill-provenance/1: commit, lean_toolchain, manifest_revs, base, donor, donor_checks, plan, additions, created_utc; collector provenance, never a job seal), never overwritten; the archive is written by the user and the preflight rerun on it confirms the fill; --json {ok, preflight_deps, fill_plan, commit, entries[], required_external_modules, missing_count, missing[], base, donor, donor_checks[] {check, ok, detail}, plan[] {module, package, lacking, required_by, fillable, files[] {path, size, sha256}, bytes}, not_fillable[], refusals[], totals {missing_modules, fillable_modules, files, bytes}, plan_out, checks[], problems[]}; --artifacts DIR [--artifacts-manifest NAME] (needs --stdout; also with --check): sha256(DIR/NAME, default artifacts.sha256) must equal the stdout's one artifacts-manifest-sha256 line, and every file under DIR is rehashed in 1 MiB chunks against it (missing, extra, changed files, symlinks, special files, path traversal, duplicate or unsorted entries refuse; previously copied files are adopted only by this rehash, never by another attempt's hashes); the receipt gains artifacts_manifest_sha256 (additive, gate ignores it) and the manifest is written beside it as <c12>-slurm-<J>-artifacts.sha256 (sealed[] role artifacts.sha256), JSON artifacts {ok, dir, files, bytes, manifest, manifest_sha256}; without --artifacts nothing changes; --seal-sacct [--user NAME] (optional, additive; not with --check/--template/--preflight-deps; --job a number J or J_T; --out only inside <repo>/.lean-receipts/): seal the job's sacct record as `receipt comparator --seal-sacct` does: live sacct runs `SLURM_TIME_FORMAT=%Y-%m-%dT%H:%M:%S%z sacct -j J --format=JobID,State,ExitCode,Start,End,Elapsed,TotalCPU,User,NodeList -P` (or --sacct-file F, that output with its header); refuses unless Start..End (zoned) contain the log's generated-at: line (none: refused) and User = this account (live) or --user (not checked otherwise; node: not checked, the receipt names none); writes <c12>-slurm-<J>-sacct.txt (JobID|State|ExitCode|Start|End|Elapsed, UTC; no user or node names) and the additive slurm_sacct {schema slurm-sacct-seal/1, job, file, sha256, state, exit_code, start, end, elapsed, checks, source} into the receipt, a `- Seal: sacct record ...` README line, sealed[] role sacct.txt (receipt_key slurm_sacct.sha256), JSON slurm_sacct (its sacct object without user/node names); gate, gate --check, receipt check and every core.validate_receipt caller then refuse the receipt unless that record is committed at HEAD and still hashes and parses to the block ("sacct seal record missing or changed: ..." / "sacct seal: ..."); a receipt without the block is judged as before   |   receipt systemd (alias local) --unit U --log PATH --receipt-commit SHA [--properties-file F | --run [--memory-max 8G] [--memory-swap-max 0] [--cpu-quota 400%] [--runtime-max 4h] [--setenv K=V]... [--require-tools A,B,...]... [--properties-out PATH] -- CMD...] + the same --stdout/--stderr/--checker/--repo/--redact/--out/--append-readme/--json: the same receipt from a finished systemd --user service unit; the unit state comes from --run (lean-pkg runs CMD bounded via systemd-run --user -p RemainAfterExit=yes, WorkingDirectory=--repo, so the finished unit stays loaded: properties are captured to <log>.unit.properties before it is stopped and reset; --require-tools: each named executable resolved with type -P in the unit's PATH before the unit is created (paths and sha256 to stderr and --json run_tools; a missing one refuses, nothing run); the checkout must be at SHA with no tracked change and no untracked file (less .lean-receipts/, .lake/ and the run's own files; ignored files are not bound) under the package dir before and after), --properties-file (saved `systemctl --user show U -p ...` + a Hostname= line) or live `systemctl --user show U`; refuses unless LoadState=loaded, Id=U, terminal (failed/inactive or active+exited), Result=success (oom-kill, timeout, signal named), ExecMainCode exited with ExecMainStatus 0, NRestarts 0, plus every receipt slurm seal/field/redaction check (optional stdout invocation-id = InvocationID); job_id systemd-user:<U>:<InvocationID>@<host label> (label h-<12 hex of sha256(lower-cased host name)>; the name itself is never written); also writes <c12>-<slug>-unit.properties (additive unit_properties_sha256); built-in redaction <repo>, <HOME>, /run/user/<UID>/; --run interrupted (SIGINT/SIGTERM/SIGHUP) while waiting: the unit is not stopped, its temporary LAKE_CACHE_DIR is kept while the unit may still run (removed when it is gone or finished), exit 128+N, and the recovery command `lean-pkg receipt systemd --unit U --log ... --receipt-commit ...` (no --run) is printed with a WARN that recovery skips the source-unchanged-during-the-run check   |   receipt ci --log PATH [--receipt-commit SHA, default GITHUB_SHA] [--attest: the job attests (verify.yml, S4): ci.attestation = <c12>-gha-R-A.sigstore.json, the Sigstore bundle the next step (actions/attest-build-provenance) signs over the receipt and its log; refused without an OIDC token] + the same --checker/--repo/--redact/--out/--compress-log/--append-readme/--json (no --stdout: the log carries its own verify-log-sha256: seal; proposal 0008 S2; inside a GitHub Actions job, the last step of lean-pkg's reusable .github/workflows/verify.yml; no registry, no token): the same receipt with job_id gha-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT> and the additive ci block built from the runner's variables (job_workflow = the OIDC token's job_workflow_ref claim when the job has id-token: write, else null; the token is never printed or written); refuses (exit 1, nothing written) outside GitHub Actions (GITHUB_ACTIONS != true), on a self-hosted runner (naming receipt local), a missing GITHUB_SERVER_URL/GITHUB_REPOSITORY/GITHUB_REPOSITORY_ID/GITHUB_RUN_ID/GITHUB_RUN_ATTEMPT/GITHUB_JOB/GITHUB_EVENT_NAME/GITHUB_SHA/GITHUB_WORKFLOW_REF/GITHUB_WORKFLOW_SHA/RUNNER_ENVIRONMENT/RUNNER_OS/RUNNER_ARCH, an event other than push/workflow_dispatch, a server other than https://github.com, --receipt-commit != GITHUB_SHA, the workflow file missing at GITHUB_WORKFLOW_SHA (shallow clone named), a failed OIDC token request, a block failing level 1, plus every receipt slurm seal/field/redaction check; built-in redaction <repo>, <GITHUB_WORKSPACE>, <RUNNER_TEMP>, <RUNNER_TOOL_CACHE>, <HOME> (also /home/runner), /run/user/<UID>/; after writing prints exactly one `::notice title=lean-pkg-receipt::receipt_sha256=<hex> report_sha256=<hex>` (stderr under --json) and appends to GITHUB_STEP_SUMMARY; --json adds ci, producer; --collect RUN [--attempt N] [--repo DIR] [--json] (on your machine; never commits or pushes): gh run download of artifact lean-pkg-receipt-RUN-N (your gh login; no --attempt: the run's only lean-pkg-receipt-RUN-* artifact), the receipt checked as gate --check does against the checkout (validator incl. ci level 1, report_sha256 against the log), receipt + log copied into .lean-receipts/, and the Sigstore bundle ci.attestation names (refused when the artifact lacks it or its subjects are not exactly the receipt and the log); refuses (exit 1, nothing copied) on no gh, no artifact, several artifacts without --attempt, not exactly one <c12>-gha-R-A.json, ci.run_id != RUN or run_attempt != N, a receipt the gate refuses, other bytes at the destination, a symlinked .lean-receipts/; --json {ok, run_id, attempt, receipt, log, copied[] {path, sha256}, checks[], next, refusals[]}; --template (no other flag): print the caller workflow (on push + workflow_dispatch, permissions contents: read + id-token: write, uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex: the installed lean-pkg's commit when pip recorded one, else a placeholder>), --json {ok, template, pinned}
verify    NAME [--work-dir DIR] [--unit U] [--script verify.sh] [--judges N] [--agent A] [--reason T] [--dry-run]: the newcomer's one step to verified-private on this machine. Refuses first (nothing run) an unknown package, a checkout that is not a clean commit (untracked files other than this commit's receipts), no committed verify script, no comparator config, status scratch/archived/upstreamed, --registry-snapshot. Then step 0, the preflight (#325; read-only, also under --dry-run): the claims/1 check of `claims check --file` on the claims file committed at HEAD, and, when coverage.yaml is committed, the offline coverage check against the files and comparator configs committed at HEAD (placeholders and an unposted manuscript only warn there); a refusal stops verify with that message, nothing run, nothing written; the working tree is never read. Then, each printed and run as its own invocation, at HEAD X: (1) `receipt comparator --repo CHECKOUT` (COMPARATOR_RECEIPT_NO_MATHLIB=1 when verify.sh declares `mathlib-rev: none` and the manifest has no Mathlib), (2) `receipt systemd --unit NAME-verify --receipt-commit X --log W/verify.log ... --run -- bash verify.sh W/verify.log` (W outside the checkout: $XDG_STATE_HOME/lean-pkg/verify/NAME/<c12>-<stamp>), (3) an active package: `update NAME --status gate-pending --reason ...`, (4) `gate NAME --receipt LOWER --comparator COMP` (--regate for verified-private/public). Comparator first: it refuses untracked files, the lower tier allows .lean-receipts/; nothing is committed between them, so both bind X. A receipt of X already present is reused. Writes only what those commands write, commits nothing in the checkout; HEAD an evidence-only descendant of the gated commit: `already verified`, nothing run. coverage.yaml at HEAD with `stack: S`: lean-toolchain and the lake-manifest.json Mathlib commit checked against stack S, then the comparator receipt's tool digests; `on stack S (supported until D)` or a warning per difference, a stack past its window or undeclared here warns too; never refuses (proposal 0004). --json {ok, name, commit, checkout, dry_run, status_before, status, work_dir, steps[{step, command, rc, skipped?, output?}] (first {step "0/3 preflight", rc, output {ok, claims, coverage}}), receipts{lower, comparator}, gate, next[], stack (null without the key: {name, declared, matches, supported_until, window, toolchain, mathlib, comparator_tools, lean_pkg, mismatches[], warnings[]}), already?, error?}
comparator attach NAME --receipt PATH   (old spelling: comparator NAME --receipt PATH) attach comparator evidence to a verified-private/public package;
  no status change; event comparator-attached; identical re-attach = no-op; another receipt for the same commit: --replace keeps the old one as <c12>-comparator.replaced-<stamp>.json, event comparator-replaced {replaced}. It is required by promote.
  The receipt is written by `lean-pkg receipt comparator` (the comparator-receipt program: the same code and flags) on a host its policy allows (a GitHub-hosted runner, checked first: GITHUB_ACTIONS=true and RUNNER_ENVIRONMENT=github-hosted, recorded as host_policy "GitHub Actions run R attempt A", job_id gha-R-A and the ci block of receipt ci less verify_log_sha256, refusing (exit 1, nothing written) on a runner variable missing or malformed, an event other than push/workflow_dispatch, a server other than github.com, the workflow file missing at GITHUB_WORKFLOW_SHA or GITHUB_SHA != the checkout's HEAD; GITHUB_ACTIONS without RUNNER_ENVIRONMENT, or a self-hosted runner, is not recognized and the other rules apply; proposal 0008 S5, verify.yml with comparator: true runs it after the AppArmor userns sysctl and comparator host-check --routes; a Slurm job, a host in
  COMPARATOR_RECEIPT_ALLOWED_HOSTS, COMPARATOR_RECEIPT_ALLOW_HOST=1, or the workstation default: no allow-list set, no Slurm on the machine, COMPARATOR_RECEIPT_WORKSTATION_DEFAULT not 0; recorded as `host h-LABEL: the machine running the command (workstation default ...)`). Stages checkout, cache-get, build, comparator, receipt log to <evidence-dir>/comparator-stages/NN-<stage>.log + stages.json (kept on failure; the error names the stage and log; --evidence-dir DIR, default .lake/comparator-receipt); readiness before any expensive stage: a .lake/.lake/packages(/*) symlink resolving outside the project is refused (the lake comparator sandbox cannot read it), and the checkout stage ends with `lake --no-build build M` per configured module (unknown target: fails naming the missing [[lean_lib]]); a judge that stops before judging (sandboxed dependency resolution) is a tool/environment error, not a rejection; --prepare runs only the setup stages (idempotent); a later run at the same commit/toolchain/config hash skips them (--from-prepared DIR: reuse DIR's, refuse a mismatch); receipt gains an additive stages[] summary; a run replacing an earlier run's stage files first keeps them read-only as comparator-stages/attempts/NNN/ + attempt.json {n, verdict, failed_stage, started_at, finished_at, olean_manifest_sha256, files sha256} (numbers never reused, never deleted or read back: resume/setup reuse/receipts use the current run only; prune by hand), `comparator-receipt --attempts [DIR] [--json]` lists them (exit 1 if a kept file changed). A receipt written in a Slurm job claims it only through SLURM_JOB_ID (job_id J, host_policy "Slurm job J on H"), which any shell can set: after the job finished, `comparator-receipt --seal-sacct R [--sacct-file F] [--user NAME] [--json]` (no host policy) corroborates it with sacct (main line J COMPLETED, 0:0; Start..End, with zone, containing generated_at; User = this account with live sacct or --user; NodeList = the receipt's host: its h- label against the node's label (or its first DNS label's), an older receipt's host name against the node name; an operator --host-label or a node range is not compared), refuses otherwise (exit 1, nothing written), and writes .lean-receipts/<c12>-comparator-sacct.txt (JobID|State|ExitCode|Start|End|Elapsed, UTC; no user or node names) plus the additive slurm_sacct {schema slurm-sacct-seal/1, job, file, sha256, state, exit_code, start, end, elapsed, checks, source} into R; only a sealed comparator receipt (seal committed at HEAD, rehashed and reparsed) backs a coverage.yaml compute job.
  flags: --receipt PATH (required); `comparator check NAME --receipt PATH` (old spelling: --check) validates only, records nothing ( works at any status; stderr `WARN: palomar: ...` when `palomar check` finds error gaps at the receipt's commit, JSON palomar_check as in publish, never a refusal), --check --repo DIR (validate against the checkout DIR, e.g. a worktree or feature branch, not the registered local_path; same rules as gate --check --repo: never saved, stderr note, JSON repo_override {path, registered_path, remote_match}, null without it; attaching refuses --repo, exit 2), --refuse-private, --refuse-dirty; `comparator check NAME --contracts-only [--config PATH]... [--export MODULE=FILE]... [--no-sandbox] [--repo DIR]` (no receipt; read-only, never acceptance): every comparator*.json and comparator/config*.json of the checkout at once, one leanexport per module (--ignore-missing, bwrap unless --no-sandbox), then Lake's own compareAt and checkAxioms per configuration (as lake comparator's compare step); no kernel, no receipt, no event, nothing in the checkout written; per configuration match/differs/error, a differing one with Lake's message, breadcrumb[] (target to the refused constant), first_field {field, path, challenge, solution} and a display-only hint; JSON {schema lean-pkg-comparator-contracts/1, ok, acceptance false, evidence false, verdict, counts, configs[], exports[]}; exit 0 all match / 1
  ex: lean-pkg comparator attach my-lean --receipt .lean-receipts/<c12>-comparator.json   |   comparator config --from-claims NAME [--out comparator.json] [--dry-run]: one TODO theorem_names entry per synced theorem/lemma claim not yet configured (name <Pkg>Audit.<decl>; the TODO markers go to lean-pkg-signoff.json configs[<config>].scaffold.todo, never into the config; a config still carrying the deprecated inline lean_pkg_scaffold is refused until migrated); never overwrites; counts existing/added/skipped; --challenge-from-type pre-fills Challenge (elaborated type) + Solution (`exact @_root_.<decl>`) via `lake env lean` [--lake-env DIR --jobs N --timeout S], or split it: --emit-script F (bash F OUT.json on a Slurm node, JSON comparator-types/1; ENFILE/EMFILE: halves jobs, retries) then --from-output OUT.json (commit must be HEAD; driver exits 1 on extraction errors, 2 if none filled; ingest refuses those unless --allow-partial: failures kept TODO, extraction_failed; --checkout DIR instead of local_path; --claims-file PATH: claims from that working-copy claims.yaml instead of the synced registry cache (no claims sync; schema-checked like claims check --file, package must be NAME; never written to the registry; same config as the registry route for the same claims; output line `claims source:` and JSON claims_source {kind registry|file, path, sha256, synced_commit, claims}); --no-registry --out FILE: scratch config, no lease check); per entry: type_error keeps the TODO, needs_restatement flags project vocabulary; each printed type re-elaborated vs the decl (example : (T) := @decl), failure keeps TODO, round_trip_failed   |   comparator config --coverage NAME: read-only; claims covered/not/TODO by the config tracked at the gate-bound commit (never a candidate); a changed or uncommitted config cannot certify that tree, a new gate at a commit tracking it is required; prints the exact next commands (JSON next_commands[])   |   comparator config --migrate-signoff PATH [--write]: no registry; moves the deprecated inline judged_as (-> configs[<config>].definitions, one {reviewer, date, judged_as} per hole, config_sha256 bound to the migrated bytes) and lean_pkg_scaffold (-> configs[<config>].scaffold) of that configuration into lean-pkg-signoff.json at the package root; dry run by default (prints both would-be files); refuses an invalid inline sign-off or a conflicting sidecar entry; the Palomar keys and their values never change, but the file's bytes do: a gated package needs a new comparator receipt (receipt impact + next commands printed; JSON {moved[], dropped[], config_sha256_before/after, receipt_impact, next_commands[]})   |   comparator host-check: read-only, no registry: kernel, landlock ABI, bwrap, go/cargo/jq/git/lean/lake/python3 ok|warn|fail, then `mechanism toolchain|legacy: ok|fail` (toolchain = bwrap+lean+lake+python3, what Palomar runs; legacy = Landlock ABI>=1 + go/cargo/git/jq/lean/lake) + verdict naming only usable ones (exit 0 can / 1 cannot); warn git-env (JSON env_warnings[]) when GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_OBJECT_DIRECTORY/GIT_ALTERNATE_OBJECT_DIRECTORIES/GIT_COMMON_DIR/GIT_NAMESPACE is set (comparator-receipt and package-checkout git drop them; stages.json git_env_scrubbed[]); --routes (field report #5; run it before sbatch inside the job's image with the job's binds and PATH): per route judge (`lake comparator` in bwrap: the toolchain mechanism's needs; no unsandboxed route ever) / prepare (comparator-receipt --prepare: kernel+lake/lean/git/python3) / transfer (build here, judge elsewhere: prepare + tar/sha256sum) `route R: yes|no (reason)` + `run:` lines, executables resolved like type -P (the receipt slurm --template HOST_TOOLS list included; time: binary only), locale (LANG/LC_ALL/LC_CTYPE, C.UTF-8 installed, warn when a set locale is not installed: stderr warning; export LC_ALL=C LANG=C PYTHONUTF8=1), limits (ulimit -n soft/hard, /proc/sys/fs/file-nr; tight: LEAN_NUM_THREADS=3), resources (sched_getaffinity CPUs, cgroup v1/v2 memory limit, MemAvailable, SLURM_* allocation), advice[]; JSON additive routes_schema host-routes/1, routes[{route, supported, needs, failing, reason, commands, notes}], executables{name: {path, level, required_by[], note?}}, locale, limits, resources, advice[]; read-only, no network; exit 0 when any route is supported
publish NAME --yes  (old spelling: promote NAME --yes; `publish NAME --yes --dry-run` is the old `promote NAME`) flips GitHub visibility to public (ONE-WAY) + one notification
  (via LEAN_PKG_NOTIFY_CMD when set). Needs an authenticated `gh` (--yes: a failed gh query is a failure). Refuses without a byte-
  valid comparator receipt bound to HEAD, a claims ledger that is not ready (`claims ledger NAME`: every reusable claim verified or conditional, theorems/lemmas by the comparator too; a conditional package passes with the line 'claims ledger ready: N required claim(s) verified or conditional (conditional: K; outstanding hypotheses: ...)'; no claims = unknown = refused; JSON claims_ledger {package_status, counts, required, reasons, ledger_digest}), both receipts + comparator logs committed unmodified at HEAD, HEAD in origin/<branch> (local refs; --yes first runs git fetch --prune --tags origin incl. +refs/pull/*/head:refs/remotes/origin/pr/*, 120 s; a failed fetch is a ✗), (paper field) a paper unpublished with no arXiv id, and blocking public-safety hits (the whole repository when the package is a subdirectory of it; warn-only hits: a WARN line; skip: --skip-public-safety, a WARN). Evidence on disk that git ignores (receipts, comparator logs, comparator-stages/ files) is a WARN naming each path + rule (.gitignore:3:*.log) and the remedy (!.lean-receipts/ + !**/.lean-receipts/** after it, or git add -f); a not-committed ✗ names the rule; JSON ignored_evidence[{path, rule}] (also publish, and gate/comparator after attaching: stderr WARN).
  ex: lean-pkg publish my-lean --yes --dry-run   # always read the checklist first   |   publish NAME [--dry-run] (read-only, the default without --yes): promote checklist (claims ledger included; JSON claims_ledger) + consent, license_spdx vs LICENSE, formalization.yaml current+valid, claims synced, the README page block (`page --check`; JSON page {status, adopted, problems[]}; not adopted: WARN, an unbacked Easley sentence: ✗; --corroborate (not with --yes): rendered as page --check --corroborate, the checklist's only network use), lakefile package name, tag at HEAD, a WARN item `palomar: not submittable ...` when `palomar check` finds error gaps at HEAD (JSON palomar_check {ready, commit, errors, warns, codes[], command}; never a ✗) (--public-safety: + the scan below, implies --dry-run); prints the next promote/tag/record commands (the tag push names the GitHub remote, found by URL); exit 0 ready / 1 not   |   public-safety NAME [--history|--no-history] [--strict] [--package-only] [--quiet] [--gzip-limit BYTES] (read-only; --quiet: summary + verdict, no hit lines): tree git would ship + (default) every blob/commit message/author+committer identity/annotated tag/branch+tag name on any ref; the whole repository when the package dir is a subdirectory (promote publishes it all; --package-only: the package dir alone); symlinks by target string; UTF-16/32 decoded; long lines in overlapping chunks; binary via printable runs; up to 64 MB per file, the rest listed in skipped[] and a blocking `unscanned` hit unless `allow: GLOB: ^unscanned$`; gzip content (bytes 1f 8b, any name; tree and history) stream-decoded up to --gzip-limit (env LEAN_PKG_GZIP_LIMIT, default 512M, K/M/G; 0 = off) and the DECODED text scanned instead of the compressed bytes (hit encoding gzip-decoded, location `path (decoded):LINE`; header file name/comment: gzip-header; decoded[] lists each; a binary decode such as a tar is scanned by printable runs, skipped reason `binary (gzip-decoded)`, not blocking); corrupt/truncated/over the limit/over the 500:1 ratio guard: hits of the decoded part kept, compressed bytes scanned as before, skipped[] `gzip ...` reason, a blocking `unscanned` hit unless allowed; for .lean-pkg-private-patterns regexes (# comments; bare or `block: RE` blocks, `warn: RE` warns, `allow: RE` exempts; `TAG: GLOB: RE` scopes any of the three to files whose package-relative path matches GLOB (* ** ?; no / = file name at any depth; trailing / = under that dir; never commit messages); tracked = a hit), a committed .lean-pkg-public-allow (allow: lines only, public on purpose; any other line ignored with a warning; inside it every allow line applies whatever its scope, and a line a private block pattern or registry-derived name matches is the blocking hit public-allow-private, tree and history; patterns_source public_allow_file, public_allow), built-ins (/home/<u>/, /Users/<u>/, ~/Dropbox/, approvals/, notes/, *.private, TODO-private, emails not declared: ordinary commit/tag addresses, consent[] or allow: lines; a host-like commit/tag address, user@hostname or a local TLD such as .lan or .invalid, is not declared and is an identity-email hit; a fixture address under .invalid in a file needs an allow line), names from the record's local_path/agent/workspace and its lease's agent/workspace/contact (an allow line exempts one only where its match in the token's non-space run covers it), internal paths (approvals/ notes/ drafts/ scratch/ *.priv), warn-only latex-label (lem:/thm:/eq:/sec:... keys, main.tex, \\ref{ \\cite{ \\label{, Overleaf, arXiv ids; Lean-calibrated: never inside a name like foo_eq_bar, eq/def/ex only with `:`, key- only with a digit/capital/inner `.`, built-in allow def-eq defeq eq-of eq-iff ex-falso; the summary counts its warnings); hit = location (path:line, history: commit + path) + pattern label + severity (block|warn; warn printed as WARN) + excerpt with each match as […] (raw strings never printed); --json {ok, hits[], tree_hits, history_hits, block_hits, warn_hits, latex_label_hits, strict, scope, package_prefix, skipped[], skipped_blocking, decoded[] {where, path, compressed_bytes, decoded_bytes, content, complete, commit}, gzip_limit, patterns_source, warnings[]}; exit 1 on blocking hits (--strict: on any hit)   |   reservoir check NAME [--offline] (read-only): public, not a fork, >=2 stars (gh), lake-manifest.json + lean-toolchain tracked, LICENSE vs license_spdx, lakefile package + no `reservoir := false` (+ WARN without description/keywords/versionTags), lean-toolchain a release (rc/nightly: WARN), tag at HEAD; not yet public: GitHub items `pending promote: ...` (still ✗) + a `pre-promote:` line; then the Reservoir API (200: prints the `record ... reservoir-listed` command; never records itself); exit 0 eligible / 1 not   |   palomar prepare NAME [--out DIR] [--dry-run] (no network; submits nothing): bundle at <checkout>/.lean-receipts/palomar-<HEAD12>/ (never overwritten): formalization.yaml (rendered, schema-valid), configs/<every comparator config at HEAD, and the headline configuration (README.txt names it for the main-results entry; JSON headline_config)>, receipts/<gate + comparator receipts, validated against HEAD>, README.txt (manual steps + Palomar URL + the record palomar-submitted command), MANIFEST.sha256; refused without receipts bound to HEAD, without consent, on any error gap `palomar check` finds at HEAD (each listed as `✗  CODE: detail` + fix; nothing written, --dry-run too; its warn gaps are `lean-pkg: warning: palomar check: ...` lines, never a refusal; JSON palomar_check {ready, commit, errors, warns, codes[], command}), on blocking public-safety hits in the bundle; event palomar-prepared {commit, bundle, bundle_sha256}   |   palomar check NAME [--rev REV] [--config PATH] [--rules FILE] [--repo DIR] (read-only, offline; reads git objects at the commit: --rev, else the gate-bound commit, else HEAD): gaps to Palomar's mechanical rules, pinned at PalomarSubmission d4e41c1d5b0d (read 2026-10-16; --rules FILE: a newer browser-preflight-policy.json): every tracked .lean file of the repository starts with `module` (comments may precede; lakefile.lean exempt; .git/.lake excluded; <= 10,000 lines; no symlinks), lean-toolchain leanprover/lean4:vX.Y.Z[-rcN] >= v4.35.0-rc2, lakefile, no submodules, each comparator config (or --config PATH): required keys, no keys outside challenge_module/solution_module/theorem_names/definition_names/permitted_axioms/enable_nanoda (judged_as and lean_pkg_scaffold are refused), standard axioms, module sources tracked, Challenge <= 1,000 lines and 100 KiB, definition holes (warn); the headline configuration (registry headline_config, else comparator.headline.json): the same config checks, comparator.headline_uncovered (error: names not covered or holes unsigned by the gate-bound comparator receipt), comparator.headline_unjudged (warn: no receipt validates at HEAD), comparator.headline_missing/_invalid (error), JSON headline {config, source, judged, covered} or null; formalization.yaml tracked, v0.4-valid, no placeholders, Palomar's metadata minimum; one root licence file matching license_spdx; GitHub repo; visibility public and commit pushed (warn); gap {code, severity error|warn, paths[], detail, fix}; verdict first and last; JSON {ok, ready, commit, commit_source gate|head|rev, project_path, lean_toolchain, toolchain_advisories[], configs[], lean_files, missing_module, counts{error, warn}, gaps[], rules{}, not_checked[], repo_override}; exit 0 no error gap / 1 error gaps / 2 usage   |   palomar port-modules NAME [--dry-run (default) | --write] [--agent A] (reads git objects at HEAD; never commits, never the registry): the mechanical port of the package's tracked .lean files to the module system: `module` first (after ordinary comments; before /-! -/ and /-- -/), `import X` -> `public import X` (`meta import` -> `public meta import`; `import all`/private/public kept; never `public import all`), body in `public section` ... `end` (no `end` when a scope stays open); module-headed files skipped (a re-run is a no-op); refused per file: prelude, non-UTF-8, symlink; review items (never auto-fixed, lines of the ported file): expose (a def another file unfolds/simp/rw/rfl/decide: add @[expose]), private, meta (#eval/native_decide/initialize), imports-non-module, scope-balance; .lake/, lake-packages/, nested Lake projects, path deps excluded, lakefile.lean exempt; dry run: per-file report + unified diff; --write: clean working tree only (dirty/untracked, a refused file, or a foreign live lease without --agent: exit 1, nothing written), then follow-ups (lake build, @[expose] review, palomar check after the commit, a new gate for a gated package); JSON {ok, name, dry_run, written[], commit, project_path, files[{path, action port|skip|refused, reason, edits[], reviews[{code, line, detail}]}], counts{port, skip, refused, review}, expose[], excluded[], vendored_dirs[], outside_package, diff, follow_ups[]}; exit 0 / 1 a refused file
blueprint check NAME [--decls PATH] (read-only; clean checkout): lake build, then lake exe checkdecls PATH (default blueprint/lean_decls, leanblueprint's list of the \\lean{} names; PatrickMassot/checkdecls, a Lake dependency the package adds, pinned rev 3d425859e73f; never edits the lakefile: without it, refused with the require line); every `NAME is missing.` line is reported; then claims.yaml at HEAD: a claim whose decl the blueprint names must be accepted by checkdecls (else problems[]); claims outside the blueprint and blueprint names without a claim are notes; no blueprint/ and no list: skipped, exit 0; JSON {ok, commit, skipped, reason, decls_file, declarations, missing[], checkdecls {rev, url, pinned}, claims {file, present, checked, problems[], outside_blueprint[], blueprint_without_claim[]}}; exit 0 nothing missing / 1
registry audit  (old spelling: audit) read-only without --write (old spelling --fix) (expired leases reported); --fix: sweep leases, gh -> registry visibility; toolchain/formalization.yaml drift; receipt/comparator drift judged
  at the reference branch tip (origin/HEAD, else main, else master; local refs; a checkout elsewhere: checkout-branch finding).
  --toolchain-floor V (env LEAN_PKG_TOOLCHAIN_FLOOR; default leanprover/lean4:v4.35.0-rc2): below-toolchain-floor, rc < release,
  nightly -> toolchain-unparsed, scratch/archived exempt; floor is warn only for public/comparator-evidence packages or --promotion, else level info; --json findings carry level warn|info; repo not on GitHub -> no-github-mirror (human: one line); informational: receipt-untracked (receipt file not tracked at the tip), duplicate-path (two packages, one checkout), duplicate-namespace (two checkouts, one Lean root namespace), consent-duplicate (one who+role recorded twice; shown once), claims-coverage-none (claims coverage none|empty|unsynced: search falls back to declarations; --json coverage; archived/upstreamed skipped); a failed gh query: check gh-OUTCOME, gh = gh-missing|not-authenticated|no-access|not-found|error, detail names the remedy (archived + not-found: info); human line `LEVEL  CHECK  NAME: DETAIL`, the line "N finding(s): W warn, I info" first and last (--quiet: it alone); --json counts {warn, info}; --no-gh: gh-skipped (info); missing-path: recorded path + `update NAME --path DIR --reason .. --dry-run` (+ "found at DIR": a sibling with lakefile + same remote); palomar-not-ready (warn; only packages aiming at Palomar: upstream_target palomar or a palomar entry, not scratch/archived/upstreamed): `palomar check`'s error gaps at its commit (gate-bound, else HEAD), detail = count + codes + `lean-pkg palomar check NAME`, --json palomar {errors, codes[], commit, command}; warn gaps alone: no finding. ex: lean-pkg audit --json
registry validate  schema + enum + history sanity.   ex: lean-pkg registry validate
next      NAME [--agent A] [--repo DIR] read-only, offline (never gh, never writes): the ONE recommended next command + a one-line reason, from the record + checkout (claim / update --status / gate --check / gate / gate --regate (verified-private, HEAD moved) / comparator-receipt / comparator config --from-claims / comparator / publish --dry-run (only when the claims ledger is ready; else its first fix or `claims ledger NAME`) / reservoir check / palomar prepare / record ...); without --agent a live lease is only noted; a checkout off its default branch (or detached) is noted, HEAD still judged; --repo DIR evaluates the checkout DIR instead of the registered local_path for this call (never saved; stderr note; JSON repo_override {path, registered_path, remote_match}, null without it; DIR a git work tree whose remotes, if any, include the package's repo (Host:/p matches a local-path repo /p; the registered local_path itself needs no remote); carried into a recommended gate --check, any other recommended command that reads the checkout gets a note that it uses the registered path); a comparator step (comparator-receipt, comparator config) whose lean-toolchain at HEAD is below audit's comparator floor (--toolchain-floor default / LEAN_PKG_TOOLCHAIN_FLOOR) is blocked first: command null, blocker below-toolchain-floor {lean_toolchain, floor, deferred_command}, reason says bump the toolchain, commit, re-gate; exit 0 command (or nothing left: command null), 1 blocked; --json {ok, name, command, reason, blockers[{kind: foreign-lease|invalid-lease|missing-checkout|no-commit|dirty-tree|invalid-receipt|unreadable-receipt|below-toolchain-floor, detail}], notes[], repo_override, state{status, lease, gate_commit, comparator_commit, head, dirty[], receipt_candidate, claims_ledger, ...}}. ex: lean-pkg next my-lean --agent vera --json
doctor    ok/warn/n/a: version, install, registry [source: flag|env|user-default|cwd], user-default, events [source: flag|registry-sibling|env|sibling], registry-repo (behind/ahead of origin or no origin remote, dirty registry files: warn = do not write yet; --fetch first), index, host (comparator host-check verdict; warn = cannot produce comparator receipts or a leaked GIT_* repository variable), tools (n/a = optional; the detail says what it is for and when it is needed; tool comparator without bwrap: run `comparator host-check`), gh auth (account; warn = not logged in, promote needs it), python, stack (n/a: the declared LTS stacks and the current one; doctor NAME: the package's committed lean-toolchain, Mathlib commit and recorded comparator receipt's tool digests against the stack its coverage.yaml declares, warn on a difference, a stack past its window or one this lean-pkg does not declare; --json adds stack {name, declared, matches, supported_until, window, toolchain, mathlib, comparator_tools, lean_pkg, mismatches[], warnings[]} or null); doctor --package NAME: one `producer <role>` line per gate receipt of NAME, the producer skew `status NAME` reports (warn: an older producer before a breaking evidence change, or a newer producer; ok: same version or no such change; n/a: unknown), --json adds producer_skew (the status object, null when NAME is not in the registry), never an exit 1, not with --upgrade-from (exit 2); exit 1 if the registry is unreadable or its schema version is unsupported (every other command refuses a newer version: upgrade lean-pkg). doctor --upgrade-from OLD [--to NEW] [--source DIR] (read-only, no registry, no network): instead of the checks, what changed for a package author from OLD (a version: tag vX.Y.Z or the `release: X.Y.Z` commit; or any commit) to NEW (default HEAD) of a lean-pkg git checkout (default: the one running), read from git objects and never executed: requires-python, dependencies, executables, format markers (x/N constants, registry version, formalization.yaml version), on-disk key lists, commands/options from the help goldens (renamed = alias kept), --json key sets, aliases, packaged files; plus the curated docs/upgrades/<version>.yaml entries whose PR (#N) lies in OLD..NEW (behavior[], breaking[]) and the range's PRs no record names (uncurated[]); --json schema lean-pkg-upgrade/1. docs/design/upgrade-preflight.md
llms      this contract (--json: {"ok": true, "text": ...}); --short: ~40 lines cut from it by section (title, PURPOSE head, STANDING RULES, the first line of each subcommand; --json adds "short": true; the rest, e.g. publish, public-safety, reservoir check, palomar prepare, comparator config/host-check, release --fresh-repo/--adopt-repo: the full text). ex: lean-pkg llms --short   |   help [TOPIC|COMMAND ...]  gh-style help topics, no registry read: exit-codes (0/1/2, what 1 means per command, comparator-receipt/lean-exercise codes), environment (every variable lean-pkg, comparator-receipt and lean-exercise read, with its default; --json variables[{name, section, meaning, default}]), formatting (--json, stdout vs stderr, named schemas, jq); no argument: commands + topics; COMMAND: exactly its --help; unknown: exit 2. ex: lean-pkg help environment
registry wiki  render a Markdown status page.   ex: lean-pkg registry wiki --out status/index.md   |   report (read-only): a bug report in the shape of .github/ISSUE_TEMPLATE/bug_report.md: version + doctor ok/warn lines + host-check verdict, --command "ARGS" re-run in a subprocess (+ --json when the subcommand has it; a writing command is not re-run), --body TEXT (no re-run) and/or --from-log FILE[:N] (last N lines, default 200, as Log excerpt) first in What happened (one of --command/--body/--from-log required, else exit 2), --package NAME record (status, lease, receipt commits); every captured text redacted (in the attached sections, record and lease agent/workspace/contact values -> <agent>/<workspace>/<contact>; registry/checkout paths -> <registry>/<registry-dir>/<checkout>, the other local paths doctor and the version line name -> <lean-pkg-install>/<lean-pkg-checkout> (editable)/<python>/<registry-repo>/<index-dir>/<events-path> (not the registry's sibling), home -> ~, public-safety built-ins + .lean-pkg-private-patterns of the checkout and registry dir + record-derived names -> […]); a blocking hit left in the user's text or an attached section (the report's own prose is not scanned): exit 1, nothing filed, the section named (--json + sections[] {section, origin user|attached, families}). Dry run unless --yes: gh issue create -R lean-pkg/lean-pkg (--repo OWNER/NAME) --label bug|enhancement (--enhancement), prints the URL; no gh or gh not logged in: exit 1 (gh auth login). --json {ok, filed, url, title, labels, body, redactions, log_excerpt {path, lines} with --from-log}. ex: lean-pkg report --command "status my-lean" --package my-lean
index     public catalog <index>/<name>/entry.yaml + receipts/ (root: --index, env LEAN_PKG_INDEX, index/ beside the registry if present, ./index; check/render/migrate exit 1 when it is missing).
  add NAME --by HANDLE --headline 'Name :: File.lean :: One sentence.[ :: ChallengeTheorem]' [--from-registry] [--url URL]
      [--repo-dir P] [--lower REL] [--comparator REL] [--license SPDX] [--tag T] [--field-tag T] [--consent-recorded]
      [--allow-unpushed]  (default: the pin and evidence must be on a branch of the public repo; consent must be recorded)
      validates the receipts at the pin (their commit) in a clone; trust computed; receipts copied verbatim;
      duplicate repo+commit refused; never writes local_path; event index-added (event log only).
  check [NAME ...] [--clone | --repo DIR]   static without a source; else re-derive from bytes at the pin.
      Palomar/Reservoir verified by API when reachable, else "skipped" (never failed, never passed). exit 0/1/2.
  render [--out PATH] [--check]   Markdown table, default <index>/README.md; --check exits 1 when stale.
  migrate [--check]   rewrite index-entry/1 entries as index-entry/2 (claims derived from the headline).
claims    claims.yaml (schema claims/1) at the package repo root: named reusable results, cached in the registry.
  sync NAME   read claims.yaml at HEAD (committed bytes only); every decl must occur in its file; event claims-synced; a pin on no remote-tracking branch (unpushed) prints a stderr WARN (--json pin_published true|false|null).
  mark NAME --from-comparator CONFIG.json --status reusable|internal|experimental [--write|--dry-run] [--agent A]: set status on exactly the claims whose decl the config's theorem_names cover (the --coverage/ledger rule: full name or <Prefix>.<decl> of a fully qualified decl), in the checkout's working copy; default prints the plan; --write edits only those status values (else re-dumps, reformatted true), never commits, never the registry; lists unmatched names, then the rule and a hint per name that differs from exactly one claim's decl only in its leading namespace component (never applied); --json {ok, name, file, config, status, matched[{id, decl, from, to, changed}], changed, unmatched[], written, reformatted, rule, hints[{name, decl, shape, rename_to, hint}]}. sync and check print `WARN NAME: gated, N claim(s), none with status reusable: ...` (--json warnings[]) for a gated package with no reusable claim.   |   list [NAME] | check [NAME] [--file PATH]   cached claims with status and derived trust | re-validate; exit 0/1 (--file: schema-check a working-copy file, schema only: no checkout, so declarations unchecked; no registry; --json {ok, path, claims, errors[]}). With NAME both report coverage synced|empty|unsynced|none (--json: coverage, cached_claims, claims_yaml present|absent|unknown, legacy_claims[], hint); check NAME exits 1 on none/unsynced (no coverage is not ok) and names any legacy *claims.json in the checkout   |   migrate NAME --from PATH [--out claims.yaml] [--dry-run] [--agent A]: legacy JSON rows -> a claims/1 skeleton in the checkout (mapped fields; TODO markers, TODO.lean for the rest, which sync refuses); never overwrites, never writes the registry, grants nothing: commit it, then claims sync.   |   ledger NAME [--stage gate|promote]: read-only, nothing stored: each claim (claims.yaml at the gate-bound commit, else the cache) with named diagnostics from the recorded receipts, re-checked with gate's and promote's validators; a claim is verified only with zero diagnostics. Claim status: out-of-scope (superseded; internal unless judged: an internal theorem/lemma a configuration names, with the comparator tier clean, gets the reusable checks and can be verified, judged_internal true, never required) | blocked (a depends_on claim not closed) | unknown (unreadable evidence) | open (an open/failed code) | conditional (proposal 0003: the claim lists hypotheses, or inherits outstanding ones via depends_on, depends-on-conditional; never verified, even with every hypothesis discharged) | verified | open. Required = status reusable; --stage promote (default) also needs the comparator tier for reusable theorems/lemmas. Package status fails closed: unknown (no claims, none reusable, no checkout, unreadable evidence) | failed (evidence contradicts a required claim) | open | ready (every required claim verified or conditional). Conditional claims are judged from the hyp-probe/1 section of the gate receipt's sealed verify log at the bound commit, recomputed on every call: each hypothesis an explicit binder of the decl (and, promote stage, of its Challenge theorem) headed by a def or inductive Prop (else hypothesis-not-in-type / hypothesis-inadmissible, failed); discharged only at the promote stage with a clean comparator tier, through a corollary claim (discharges) whose Challenge type is the target's minus exactly the discharged binders (hyp-corollary) and proof claims of exactly the hypothesis (hyp-proof; a proof quantifying over data is attribution, the corollary alone the evidence), all three credited by exactly one non-smoke comparator name and clean (else discharge-type-mismatch failed / discharge-not-compared open); an outstanding hypothesis is hypothesis-open (class conditional, never blocks); missing facts (no section, a part not run, ambiguous credit) are hypotheses-unchecked (open); witness {claim, state checked|unchecked|mismatch}, a mismatch witness-type-mismatch (failed). Codes: docs/lifecycle.md. Exit 0 only when ready, else 1; --json {ok, package_status, reasons[], stage, bound_commit, receipt_sha256, comparator_sha256, counts, claims[{id, decl, kind, claim_status, required, judged_internal, status, statement_basis, manuscript_findings[], diagnostics[{code, detail}], hypotheses[{decl, state open|discharged, by {proof, corollary}|null, via}]?, discharges?, witness?}], manuscript_findings {total, open, fixed, by_kind, corrected_statements[], findings[], source}, ledger_digest; only when a claim uses the keys: counts.conditional, conditional_required[], outstanding_hypotheses[]} (sha256 over the canonical claims, codes and receipt hashes: recompute, never trust a stored one). Manuscript findings change no status (publish --dry-run and palomar prepare print the same one-line summary, JSON manuscript_findings); a lean_evidence decl missing at the bound commit is finding-evidence-missing.   |   rebase NAME --from-tex OLD.tex --to-tex NEW.tex --to-aux NEW.aux [--from-aux OLD.aux] [--from-sha256 HEX] [--to-version arXiv:IDvN | --to-commit HEX [--to-repo URL]] [--unwrap-macro NAME ...] [--claims-file PATH] [--coverage-file PATH] [--out-dir DIR] (#6): move claims.yaml + coverage.yaml (committed at HEAD) to a new manuscript version; never writes the registry or the checkout, never changes a claim status, never sets fixed_in. Refused: OLD.tex's sha256 differs from coverage.yaml's manuscript sha256, or is none of the .tex hashes of the data/source-baselines record of its arXiv version (none recorded: --from-sha256 must state it; the proposal drops eprint_sha256), TeX not UTF-8 or with unbalanced begin/end, a referenced label defined twice (OLD, NEW or NEW.aux). References: coverage rows (paper.label, else paper.number via --from-aux), manuscript_findings location.label (a TeX label, else a number via --from-aux), TeX labels named in a citation. Per label, the enclosing statement (theorem-like incl. \\newtheorem names) or equation environment in OLD vs NEW, normalized (comments, label commands, \\textcolor/\\colorbox/\\color, --unwrap-macro, ~, whitespace, trailing . , ; :; braces kept): unchanged (carried; number token re-keyed from NEW.aux, finding page from NEW.aux, tex_line shifted only when the environment is byte-identical, else null) | changed | missing | moved-into-proof | not-in-old | unsupported | unnumbered (no NEW.aux number); all but unchanged need review and are left as they were. --out-dir (new or empty; files read-only): claims.yaml, coverage.yaml (manuscript.version = --to-version or {repo, commit, sha256 of NEW.tex}; required when coverage.yaml exists), rebase-report.json (schema claims-rebase/1), rebase-report.md (old/new excerpts), review/NNN-LABEL.{old.tex,new.tex,diff}, MANIFEST.sha256; deterministic. Without --out-dir: report only. --json {ok, name, written, out_dir, counts, labels[], rows[], findings[], citations[], needs_review[{where, label, status, reason}], proposed_errors[], statuses_changed, normalizations[], inputs, to_version, warnings[], files[{path, sha256}]}; exit 0 everything carried / 1 something needs review or refused / 2 usage. docs/prior-art.md#move-to-a-new-manuscript-version
formalization NAME [--write] [--no-registry] [--out PATH] [--force [--reason TEXT]] [--author A ...] [--headline-config PATH] [--repo DIR] [--agent A]   render formalization.yaml (mathlib-initiative
  v0.4, what Palomar reads) from the registry + the repo at HEAD. Derived every run: project.name/license (license_spdx),
  automation.notes, status.sorry_count/axioms/evidence (gate receipt), status.main_results (every tracked comparator config:
  comparator_config, declaration, file; definition holes carry judged_as, from lean-pkg-signoff.json or the deprecated inline key, null when unsigned; with a headline configuration (--headline-config PATH, else registry headline_config, else comparator.headline.json when tracked; Palomar's six keys only, else refused) its theorems only, when every name is covered (the --coverage rule) by a config with the same challenge/solution modules in the gate-bound comparator receipt validated at HEAD and every hole is signed in lean-pkg-signoff.json by such a config, else refused naming them; no receipt validating at HEAD: not applied, stderr warning, the default; --write records --headline-config; JSON headline {config, source, applied, reason, receipt, theorems} or null), alignment (claims.yaml). Every other
  key is KEPT from the file; first render seeds authors from consent[] owner/author, else "[PLACEHOLDER] ..." + stderr warning (never
  invented: no paper titles). --write: idempotent; lease + update checked BEFORE writing (foreign lease: --force --reason); a file differing only in derived keys is refreshed (JSON refreshed_derived), one whose kept keys differ needs --force (named; JSON kept_overwritten);
  --write also sets the registry pointer (a registry write); --write --no-registry: the file only, registry untouched (no lock/--commit). --repo DIR: read that checkout instead of local_path for this call (never saved; not with --write, exit 2; the checkout rules and stderr note of page --repo): claims, comparator configs, the kept keys and the receipts at DIR's committed HEAD, never its working tree; the registry's gate receipt feeds status.sorry_count/axioms/evidence only when it validates at that HEAD, else they are omitted with a warning; JSON repo_override {path, registered_path, remote_match, head, source committed-head, receipt_facts {receipt, included, reason}}, null without it. Validated against the vendored v0.4 schema (jsonschema, else structural). audit: formalization-missing/-drift. docs/formalization.md   |   coverage check NAME [--file PATH] [--allow-private-manuscript]   read-only; exit 0/1. coverage.yaml (schema coverage/1, package dir, committed at HEAD; --file: a working copy) is the paper-centric map: manuscript {title, version: "arXiv:IDvN" | {repo https, commit 40hex, sha256 64hex} | "unposted" (refused without --allow-private-manuscript)}, summary (one line), rows[{paper {number, title, label?}, class same-assumptions|inputs-supplied|outside, claims[] (ids in claims.yaml at HEAD; none for outside; superseded refused), inputs[] (required for inputs-supplied, refused for same-assumptions; each a one-line string or {hypothesis, text} (exact keys; hypothesis a name in the hypotheses of a claim the row names, each once; checked in both forms)), note?}], nothing_excluded {reason} (only when no outside row; one of the two is required), compute[{cluster: easley, jobs: [slurm-J]}] (each job a Slurm receipt committed under .lean-receipts/; cluster github-actions: jobs gha-<run_id>-<run_attempt>, each a lower-tier or comparator receipt with that job_id whose ci block passes level 1; a gha- receipt without the block, or with a failing one, is named in the refusal), lean_pkg_version {version, doi} (checked against lean-pkg's own CITATION.cff when readable), stack lts-YYYY.04|10 (optional, both kinds: the LTS stack the package opts into, name shape checked here; doctor NAME, verify and page check the pins against it, never failing a check). The lab's lean-scope-page rules that apply: no placeholder text (FILL-ME, TODO, TBD, FIXME, XXX), every named claim's file committed at HEAD, every theorem of the recorded comparator receipt the decl of a claim some row names (a configuration lean-pkg-signoff.json marks smoke {reason}, at the receipt's commit or HEAD, is exempt, its theorems the decl of no row's claim; a smoke theorem credits no claim anywhere and the page lists it as a smoke check, never a result), every configuration there with definition_names carrying judged_as.reviewer. Strict: exact key sets, closed enums, duplicate keys refused. --json {ok, name, file, rows, counts, manuscript, private_manuscript, errors[], warnings[]}. Offline form (#328): coverage check --file PATH [--claims PATH] [--comparator PATH ...] [--allow-private-manuscript], no NAME: no registry, no receipt; runs the schema and length limits, manuscript version, lean_pkg_version, the claims file's claims/1 schema and the coverage package equal to its package, row claim ids (present, not superseded), a warning per reusable claim no row names, each --comparator theorem the decl of a claim a row names (lean-pkg-signoff.json smoke beside the coverage file exempts; skipped when a named claim's Lean file is not readable under the coverage file's directory); lists as skipped what needs the registry, HEAD or a receipt (compute receipts, claim files at HEAD, definition sign-off, e-print baseline) or a missing input; verdict first and last; --claims/--comparator with NAME, or neither NAME nor --file: exit 2; --json adds offline true, claims_file, comparator[], checks[{check, status pass|fail|warn|skipped, detail}], skipped[]. docs/page.md   |   page NAME [--write | --check | --out PATH] [--site DIR] [--html PATH] [--repo DIR] [--strict-preview] [--corroborate] [--attested [--trusted-root FILE]] [--allow-private-manuscript] [--agent A]   the README verification section, rendered between <!-- lean-pkg:page:begin --> and <!-- lean-pkg:page:end --> in the package README.md from coverage.yaml, claims.yaml, the claims ledger (promote stage: a row is "verified" only when the ledger closes every claim it names), the receipts committed under .lean-receipts/ (allow-listed fields only: commit, toolchain, Mathlib rev, sorry count, axioms, theorem/permitted-axiom/definition names, date, SHA-256, slurm-J job ids; never hosts, paths, users), lean-toolchain, lake-manifest.json, CITATION.cff (authors required), registry repo/status/license_spdx/doi. Sections: status capsule, paper-Lean map, Not formalized, trust boundary, reproduce, evidence, citation, license, acknowledgments (always the lean-pkg credit with the concept DOI; the Easley sentence only when coverage.yaml compute names easley jobs backed by committed Slurm receipts; a site's sentence needs its jobs backed AND their corroboration level >= its scheduler's sentence_level: offline every job is level 1, Slurm's level, so the GitHub Actions sentence (level 2, a corroborated run) is rendered only under --corroborate and a README carrying it fails --check and the publish --dry-run page line without it). --corroborate: ask the public GitHub REST API (api.github.com only, https, a timeout; GH_TOKEN or GITHUB_TOKEN only raises the rate limit, never printed or written) about every listed gha- job, as receipt check --corroborate does, against the registry's repository (by numeric id); the GitHub Actions sentence renders, {runs} filled with [run R attempt A](https://github.com/OWNER/REPO/actions/runs/R/attempts/A) links, only when every job reaches level 2; a failure (404, 403/429, network error, redirect off api.github.com) fails closed, is a warning naming why, and leaves the sentence out; JSON sites.github-actions gains corroboration {job: rows[]} and runs[]. --attested [--trusted-root FILE]: level 3 for every listed gha- job (receipt check --attested's checks: the committed Sigstore bundle verified by gh attestation verify); the sentence renders at level 3 too, so it survives the run's records expiring; sites.<name>.level/levels reach 3 (both flags: level 3 needs both answers). Deterministic; every rendered string passes the public-safety scanner + the package's .lean-pkg-private-patterns (a blocking hit refuses). Default: print the block. --write: replace only the marked block (append when absent; prose outside untouched; never commits; a foreign live lease refuses without --agent HOLDER); not a registry write (no lock/--commit). --check: exit 1 when README.md is missing, the block is missing or stale, the lean-pkg credit is missing, or the Easley sentence appears without backing; writes nothing. --site DIR: also write DIR/verification.md (the full page, one H1) and DIR/coverage.md (every row: claims, decls, each claim's ledger status, inputs, note) for MkDocs; --html PATH: one static HTML file of both (no script, no external asset, inline CSS, every text escaped, links only https/#anchor); the same bytes whatever DIR; atomic writes, symlink targets refused; with --check they are compared instead (missing/stale: exit 1). User text is escaped in every output (&<> as entities, Markdown markup backslash-escaped). --repo DIR: read that checkout instead of local_path for this call (CI; not with --write), at its committed HEAD: when the registry's gate receipt does not validate there (bound to HEAD or an evidence-only descendant), the links and Lean statements are read at HEAD, no receipt fact is shown, a 'Preview of commit' note names both commits, and repo_override gains head, anchor_commit, receipt_facts {receipt, commit, included, binds_head, reason}; a receipt that binds HEAD but does not validate is a failed check ('The recorded build check does not validate', never called drift by --check). --check --repo on a preview: its differences from the receipt-bound README block and --site/--html files are warnings (exit 0, JSON preview true, current null); real problems (block missing, markers, credit, unbacked site sentence, an invalid receipt binding HEAD) still exit 1; --strict-preview (only with --check --repo, else exit 2) makes the differences fail. publish --dry-run carries the same check as one checklist line (JSON `page`). --json {ok, name, readme, markdown, written, unchanged, current, preview, problems[], warnings[], site_files[{path, status written|unchanged|current|missing|stale|refused}], repo_override, rows, counts, verified_rows, ledger_status, ledger_digest, private_manuscript, manuscript, easley {backed, jobs} (the legacy key, easley only), sites {NAME: {backed, jobs, level (lowest corroboration level of its jobs; null when none listed), levels {job: level}, sentence}}, receipts}. docs/page.md   |   formalization anchor NAME --arxiv <id>v<N> (--from-file E-PRINT | --fetch) [--compare-tex LOCAL.tex [--accept-diff REASON]] [--main-tex PATH] [--write] [--agent A]   pin the companion to one arXiv version: the version is required (unversioned: exit 2); network only with --fetch (https, 60 s, 64 MiB); the e-print (tar.gz|gz|tar; PDF-only refused; absolute/../link/device members refuse it) is read in memory and recorded in data/source-baselines/arXiv-<id>v<N>.json (source-baseline/1, deterministic: e-print sha256, .tex/.bib/.bbl/.sty sha256, main_tex, compare, retrieved_at/arxiv_api only when fetched); without --write a dry run; --write refuses unless tied (coverage.yaml already that version, a bound {sha256} equal to an e-print file, or --compare-tex identical / --accept-diff REASON, recorded), then sets coverage.yaml manuscript.version + eprint_sha256 and the formalization.yaml sources[].id; never the registry or a commit; a move from another version prints rebase_command (claims rebase).   |   formalization kept [--repo DIR] [--file REL] [--at REV] [--against REV2]   read-only, no registry: the kept view (formalization.DERIVED_PATHS and the renderer's acknowledgements lines removed; YAML 1.1 safe, timestamps kept as strings) as canonical JSON (sort_keys, separators ',' ':', ensure_ascii false, UTF-8, no trailing newline) + its sha256, for a release-input audit; --at REV: the committed file; --against REV2: changed_kept_keys, exit 1 when any; --json {schema formalization-kept/1, kept_view, canonical, sha256, commit, derived_paths, against {rev, commit, sha256}, changed_kept_keys, same}. docs/formalization.md#the-kept-view
search    TERM [--package NAME] [--offline] [--sources a,b,c] [--loogle] [--loogle-query PATTERN] [--require-complete] [--cache-dir DIR] [--record [--repo DIR]] [--json]   prior-art search. Routed by shape: a Lean pattern (→ -> ∀ fun : ( ? _ ⊢ " math operators, Nat.x dotted names) is `signature` and goes to Loogle; prose is `natural-language` and Loogle is skipped ("natural-language query; Loogle takes declaration patterns ..."); --loogle forces it; --json: top-level and per-source route. Loogle parses Lean, not prose: a term mixing the two fails there ("query not understood: Unknown identifier ...; Loogle takes Lean names, terms and patterns ... --loogle-query"); keep the prose as TERM and pass the Lean part as --loogle-query PATTERN (Loogle gets PATTERN whatever the route, every other source the term; --json loogle_query; usage error when --sources leaves out loogle). --require-complete: exit 1 when complete is false (a source skipped, failed in part, capped or incomplete); the output is printed in full first, --json gets ok false + error "search incomplete (--require-complete): skipped: ...; <why>", stderr the same line; without it an incomplete search still exits 0. Cache: Palomar snapshots and the Reservoir index are cached under $XDG_CACHE_HOME/lean-pkg (default ~/.cache/lean-pkg) and refreshed by any online search, --record or not (the registry is never written without --record); each block's cache_writes[] and --json cache {root: --cache-dir|XDG_CACHE_HOME|default, writes[] (paths relative to the cache root)} say what was written, and the text adds "cache: wrote ... under ..."; --cache-dir DIR uses DIR as the lean-pkg cache for that search only (or set XDG_CACHE_HOME for a whole session).
  order: registry, index, loogle, leansearch, reservoir, palomar, github; each "ok" or "skipped: <reason>" (never zero hits).
  local score: exact alias or package name 3, keyword 2, every term word 1; registry hits: packages (name, notes, owner_project, repo; hit_kind package) and claims; a package without synced claims is searched by declaration in its checkout at HEAD (tracked *.lean names + docstrings; hit_kind "declaration (unclaimed)", trust none, file PATH:LINE, after claim hits), and a "registry coverage: N of M packages have no synced claims: D searched by declaration only, K not searched (no checkout: ...)" line precedes consulted: (--json coverage {claims, declaration_only, not_searched[], not_searched_reasons[] {package, code no-path|no-checkout|no-git-head|scan-error, remedy (read-only, no host path), detail (scan-error only, else null: "git grep exit N: <fixed cause>" or "git grep killed by signal N ...", never git's stderr), the coverage line names it: "scan error: NAME (git grep exit 128: ...)"}, declaration_hits_omitted}; coverage is computed per run from the registry file (read once, written atomically) and the checkouts at HEAD, with no shared cache, so two runs differ only when a checkout, its HEAD or a git grep outcome differs); any skipped source adds "incomplete: searched N of M sources; skipped: ..." before consulted: (--json complete, searched, skipped_sources[]); a source that answered only in part (GitHub code or repository search failed) stays ok with partial[] in its block, is listed in truncated_sources and named on the incomplete: line; a part that returned its result limit (GitHub 30) is listed in capped[] (cap_limit) and truncated_sources ("github: code search returned its 30-result limit"). exit 0 even with zero hits; 2 on usage errors.
  --record: docs/prior-art/<date>-<slug>.md in the package checkout (or --repo; never the registry dir; never
  overwritten; "decision: pending" for a human) + event prior-art-searched; prints "note: <path> is new; a strict MkDocs build needs a nav entry (or a literate-nav glob) for it" when the repository has an mkdocs.yml (--json nav_hint {path, reason}, else null; mkdocs.yml is never edited). claim/index add print advisory notes on stderr (`notes` in --json).

RECEIPT SCHEMA (JSON object; `gate` enforces every line; every on-disk format and its versions: docs/formats.md)
schema         "lower-receipt/1", written first by receipt slurm/systemd since 0.22.0; absent (a receipt written before) = version 0, judged as before (receipts are never rewritten); a newer "lower-receipt/N" is refused ("... upgrade lean-pkg"), any other value is refused
commit         40 lowercase hex; MUST equal HEAD, or HEAD must be an evidence-only descendant of it
               (only .lean-receipts/ paths differ between the two commits)
lean_toolchain string; MUST equal the lean-toolchain file committed at `commit`, stripped (the working tree never counts)
mathlib_rev    nonempty string (full mathlib rev; `none` for a package whose lake-manifest.json lists no mathlib)
sorry_count    integer; MUST be 0
axioms         list of strings; subset of gate.axioms_allowed (default exactly: propext, Quot.sound, Classical.choice)
report_sha256  64 hex chars; SHA-256 of the verification report
job_id         nonempty terminal executor job identity, unique within that executor (stored as <c12>-<job_id sanitised>.json)
checker        nonempty exact checker command/mode (e.g. serial leanchecker manifest)
generated_at   ISO-8601 timestamp; MUST parse (optional slurm_sacct, receipt slurm --seal-sacct: its file MUST be committed at HEAD and corroborate it). Optional producer, not validated (gate ignores it): the lean-pkg that WROTE the receipt {lean_pkg, install, commit, dirty, sources_sha256, source_files, python, bytecode_tag, bytecode_files, bytecode_sha256, other_bytecode_files}; receipt slurm always (the collector, not the lean-pkg inside the job), receipt systemd only with --run; status NAME and doctor --package NAME compare it with the running lean-pkg. Optional ci, additive (proposal 0008; docs/formats.md "The ci block"; written by receipt ci): the GitHub Actions run {provider github-actions, server_url https://github.com, repository OWNER/REPO, repository_id int>0, run_id int>0, run_attempt int>=1, job, event push|workflow_dispatch, head_sha = commit, workflow_ref OWNER/REPO/.github/workflows/F.yml@REF (same OWNER/REPO), workflow_sha 40hex, workflow_file_sha256 64hex (the file at workflow_sha rehashed when that commit is in the clone, else a shown skip), job_workflow null|OWNER/REPO/.github/workflows/F.yml@REF, runner {environment github-hosted (self-hosted refused, naming receipt local), os, arch, image|null, image_version|null}, verify_log_sha256 = report_sha256, attestation? file name}; job_id MUST be gha-<run_id>-<run_attempt>; every failing rule refused and named ("ci block (a GitHub Actions run, level 1; ...): ..."); unknown keys inside ignored. Level 1 = offline consistency, as forgeable as slurm-J: backs coverage compute github-actions, never the page sentence. Level 2 = `receipt check --corroborate [--github OWNER/REPO]` / `page --corroborate` / `publish --dry-run --corroborate`: the public REST API (GET /repos/R/actions/runs/ID/attempts/N: completed, success, head_sha = commit, path = the workflow_ref file, event, repository.id = repository_id = the package's; the attempt's jobs: exactly one lean-pkg-receipt annotation, receipt_sha256 = SHA-256 of the committed receipt bytes and report_sha256 = the receipt's, its job success on the GitHub Actions runner group; the workflow file at workflow_sha hashes to workflow_file_sha256 and calls lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex> = job_workflow, whose content SHA-256 is in the shipped data/verify-workflow-pins.yaml); one row per check; 404, 403/429 (naming GH_TOKEN), network error, redirect off api.github.com fail closed; never stored; receipt check --json gains ci {level, rows[]}, github, network true. Level 3 (S4) = `receipt check --attested [--trusted-root FILE]` / `page --attested`: the bundle .lean-receipts/<ci.attestation> committed at the ref, its in-toto subjects exactly {SHA-256 of the committed receipt bytes, report_sha256}, `gh attestation verify RECEIPT --bundle B --repo ci.repository --signer-workflow lean-pkg/lean-pkg/.github/workflows/verify.yml --deny-self-hosted-runners --format json` exits 0 (gh: an optional outside tool, missing = a failure under --attested and a SKIP row `ci attestation` without it, when gh never runs; --trusted-root FILE adds --custom-trusted-root FILE, offline), and the verified certificate's source repository URI and id = ci.repository/repository_id, source digest = commit, run invocation = run R attempt A, build signer = https://github.com/<job_workflow>, runner github-hosted; rows `ci level 3 CHECK`, exit 1 below level 3; with --corroborate too, level 3 needs both; --json gains ci, github, attested true, trusted_root (network false only with --trusted-root and no --corroborate). Files *.sigstore.json under .lean-receipts/ are bundles, not receipts. A gha- receipt without ci validates and backs nothing. A comparator receipt written on a GitHub-hosted runner (S5) carries the same block less verify_log_sha256 (refused when present) and needs host_policy "GitHub Actions run <run_id> attempt <run_attempt>"; gate, gate --check and receipt check refuse a failing one; it backs a github-actions compute job only with a passing one.

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
host_label      optional, not validated: the label in host (always written since host labels); producer optional, not validated: the lean-pkg that wrote the receipt, the lower-tier receipt's producer shape (older receipts carry none and still validate)
host_policy     optional; the runner's host-policy reason (an override run is visible here); a "Slurm job J" claim backs coverage compute only with slurm_sacct, comparator-receipt --seal-sacct; stack_limit optional, not validated: {soft_kb, hard_kb, source} the lake/lean children ran under (COMPARATOR_RECEIPT_STACK=unlimited|<kB> raises it; WARN at <= 8 MiB); build_provenance optional, not validated: {compiled, modules_built, prebuilt, source} whether the build stage compiled any of the package's oleans or found them prebuilt (source: COMPARATOR_RECEIPT_BUILD_SOURCE, redacted); comparator --check and gate --check print it; lean_threads optional, not validated: N or null, the LEAN_NUM_THREADS given to the lake/lean children (COMPARATOR_RECEIPT_LEAN_THREADS=N); olean_manifest optional, not validated: {schema olean-manifest/1, files, sha256, lean_toolchain, lake_manifest_sha256, captured} the digest of the sorted `<path> <size> <sha256>` lines of the package's own compiled modules under .lake/build/lib (comparator-stages/03-build.oleans.sha256), recorded after setup and rechecked by the runner before and after the comparison (a difference fails the stage, nothing written; it also keys the slot cache); `comparator-receipt --verify-oleans [DIR] [--json]` is the read-only check (exit 0 same, 1 different/none recorded)
test_only, partial  if present and true (a test-only override; a --slots subset run) the receipt is REFUSED
result          "pass" (the runner writes a receipt only on PASS)
configs[]       MUST list exactly the comparator*.json / comparator/config*.json files tracked at HEAD
                (no omissions, no extras). Per config: path, sha256 of its HEAD bytes; challenge_module,
                solution_module, theorem_names[] (non-empty), definition_names[], permitted_axioms[]
                (subset of gate.axioms_allowed) — each MUST equal the config file at HEAD;
                non-empty definition_names MUST be signed off in lean-pkg-signoff.json at HEAD: configs[<path>] {config_sha256 (the config's bytes), definitions {<every hole>: {reviewer (human, never a model/agent token), date YYYY-MM-DD, judged_as text}}, smoke? {reason text} (a smoke check: still judged and in the receipt, credits no claim)}
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
(required iff superseded; acyclic), citation|null, depends_on[]?, statement_basis? null|as-printed|corrected, manuscript_findings[]? [{id (slug, unique in the package), kind misprint|proof-gap, location{label, tex_line?, page?}, summary, lean_evidence[Lean name (in the claim's file) | {decl, file}] (each must occur at the checked commit, like decl), fixed_in null|arXiv:<id>v<N>}] (exact keys; a misprint needs statement_basis corrected, corrected needs a misprint; findings never change a status), judged_as? null|<comparator theorem name> (theorem/lemma only; one claim per name; replaces the naming rule for that claim; must be a configured theorem_names entry), hypotheses[]? (theorem/lemma only; distinct fully qualified names of the assumptions the decl takes as explicit binders, never TODO; [] = absent), discharges? {claim, hypotheses[], proofs[]} (exact keys; theorem/lemma only; claim: a theorem/lemma of this file, not itself, superseded_by followed, whose hypotheses include every discharged name; a Q/slug claim or proof is refused: cross-package discharge is not implemented (proposal 0003, open question 2); this claim's hypotheses = the target's minus the discharged, as sets; proofs: one theorem/lemma claim per discharged name, not itself), witness? <claim id> (only with non-empty hypotheses; a theorem/lemma claim of this file, not itself)}]; trust is derived, never typed. Conditional claims (proposal 0003): the claims ledger judges them from the sealed verify log's hyp-probe/1 facts; a claim with hypotheses is conditional, never verified (page, status, receipt check and index do not render conditional results yet: the page says Not yet verified); claims list marks a claim with hypotheses (conditional) (--json conditional true); an older lean-pkg refuses a file using these keys

EXECUTOR EVIDENCE
The gate is executor-neutral. The frozen report named by report_sha256 must bind the exact
candidate commit/tree, source archive, toolchain/dependency pins, complete checker coverage,
terminal state/exit, and executor-specific identity/resource evidence. Slurm reports bind
terminal scheduler accounting; hosted reports bind run/attempt/job identity, terminal
conclusion, immutable workflow/control revision, runner identity, and resources. A pilot,
partial manifest, pre-terminal marker, or prior-candidate receipt is not gate evidence.

EVENT LINE SCHEMA (one JSON object per line, keys sorted; package events also go to history[]; index-added, prior-art-searched, registry-* are log-only; every event name and its keys: docs/formats.md "Event log")
v       1 (the line's version, since 0.22.0); a line without v is version 0, read as before; a newer or unreadable v is not interpreted (the claim prior-art note skips it with a stderr warning naming the upgrade)
ts      UTC ISO-8601 with offset, second precision (all written times; dates are UTC; same-second order = file order; naive = legacy UTC, doctor warns)
event   one of: registry-created, registry-migrated, registered, updated, claimed, claim-refreshed, claim-forced,
        released, release-forced, lease-expired, gate-passed, regated, promoted, publication-withdrawn, publication-hold-released, audit-fix, relocated, status-changed,
        comparator-attached, comparator-replaced, palomar-prepared, index-added, claims-synced, prior-art-searched, evidence-retracted, and the evidence events under `record`
name    package name or null
plus event-specific fields (agent, workspace, ttl_hours, reason, contact, commit, receipt, ...); a package event may also carry actor (the --agent of a gate, comparator, record, publish, claims sync or verify run)

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
    from . import command_tree
    out = [textwrap.fill(root.description or "", 100), "",
           "New here? `lean-pkg help start`: the five verbs and the two product tracks.",
           "", "USAGE", "  lean-pkg [global flags] <command> [flags]",
           "  lean-pkg help <command> | <topic>"]
    tiers = command_tree.tier_rows(root)
    width = max(len(n) for _t, rows in tiers for n, _h in rows) + 2
    for tier, rows in tiers:
        out += ["", command_tree.TIER_TITLES[tier].upper()]
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
    and in different git repositories (a sibling shares the repository; pass --url otherwise)."""
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
                           f"repo {base.get('repo')} is not this package's; pass --url; nothing written")
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
        raise UsageError("register needs --url URL (or --like OTHER to take a sibling's)")
    out, payload = do_register(reg_path, reg, events_p,
                               args.name, f["repo"], args.path,
                               f["owner_project"], args.visibility, args.status,
                               f["upstream_target"] or "none", f["automation"] or "", args.notes,
                               args.workspace, args.agent, args.dry_run, reason=args.reason,
                               like=args.like, like_copied=copied, license_spdx=args.license_spdx)
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
                             ("--author", getattr(args, "author", None) is not None),
                             ("--allow-author-email", getattr(args, "allow_author_email", False)),
                             ("--allow-history", args.allow_history)) if on]
    if extra:
        raise UsageError(f"{', '.join(extra)} go(es) with `repo export NAME DIR` (or `repo adopt NAME DIR`), not with "
                         f"a lease release")
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
    if getattr(args, "write_binding_file", False):   # proposal 0002, stage G3: the migration off the registry route
        if sets or args.status is not None or args.path is not None or args.redetect or args.replace_notes:
            raise UsageError("--write-binding-file writes the package's lean-pkg-binding.yaml on its own; set "
                             "fields, --status or --path in a separate update")
        out, payload, rc = do_write_binding_file(load_registry(reg_path), args.name, agent=args.agent,
                                                 force=args.force, reason=args.reason, dry_run=bool(args.dry_run))
        if args.json_out:
            print_json(payload)
        else:
            print("\n".join(out))
        return rc
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
    if docs_flag is True:   # proposal 0002, stage G3: the registry route is deprecated (the veto is not)
        from . import binding_file
        payload["deprecations"] = [binding_file.accept_flag_deprecation(args.name)]
        binding_file.print_deprecations(payload)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def do_write_binding_file(reg, name, *, agent, force, reason, dry_run=False) -> tuple[list[str], dict, int]:
    """`update NAME --write-binding-file` (proposal 0002, stage G3): write lean-pkg-binding.yaml into the
    package directory from the record's docs_descendants: true and build_inputs. Nothing when the record does
    not opt in (exit 0); an identical file is a no-op; a different file, a symlink or a non-file is refused
    (exit 1, untouched). Never staged or committed, and the registry is not written; the lease rule of a
    registry write still applies, since the file changes what the package's next receipts bind."""
    from . import binding_file
    pkg = get_pkg(reg, name)
    lease = pkg.get("lease")
    refuse_invalid_lease(name, lease)
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)) and not force and agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}, or --force "
                   f"with --reason; nothing written")
    if force and not str(reason or "").strip():
        raise Fail(f"{name}: --force requires --reason")
    repo = repo_path(pkg)
    rel = binding_file.FILE
    base = {"ok": True, "name": name, "path": str(repo / rel), "dry_run": dry_run, "written": False,
            "docs_descendants": pkg.get("docs_descendants"), "build_inputs": None, "content": None}
    if pkg.get("docs_descendants") is not True:
        why = ("the record vetoes the docs-only rule (docs_descendants: false)"
               if pkg.get("docs_descendants") is False else "the record does not opt in (no docs_descendants)")
        return ([f"{name}: {why}; nothing written ({rel} with docs_descendants: true opts in by itself: "
                 f"docs/receipts.md#docs-only-descendants-opt-in)"],
                {**base, "state": "not-opted-in", "event": "binding-file-not-opted-in"}, 0)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}; nothing written")
    globs = [g for g in (pkg.get("build_inputs") or []) if isinstance(g, str) and g.strip()]
    text = binding_file.render(True, globs)
    state, problem = binding_file.write_into(repo, text, dry_run=dry_run)
    payload = {**base, "build_inputs": globs, "content": text, "state": state}
    x = str((recorded_json(repo, (pkg.get("gate") or {}).get("receipt")) or {}).get("commit") or "")
    x12 = x[:12] or "the gated commit"
    if state == "refused":
        payload.update(ok=False, event="binding-file-refused", problem=problem)
        return [f"{name}: {problem}"], payload, 1
    advice = binding_file.commit_advice(x12)
    payload["advice"] = advice
    if state == "unchanged":
        payload["event"] = "binding-file-unchanged"
        return [f"{name}: {repo / rel} already says docs_descendants: true"
                + (f" with build_inputs {', '.join(globs)}" if globs else "") + "; nothing written",
                f"  if it is not committed yet: {advice}"], payload, 0
    if state == "would-write":
        payload["event"] = "binding-file-dry-run"
        return ([f"{name}: would write {repo / rel} (--dry-run, nothing written):"]
                + [f"  {line}" for line in text.splitlines()] + [f"  then: {advice}"], payload, 0)
    payload.update(event="binding-file-written", written=True)
    return [f"{name}: wrote {repo / rel} from the record (docs_descendants: true"
            + (f", build_inputs: {', '.join(globs)}" if globs else "") + "); not staged, not committed; the "
            "registry is unchanged",
            f"  next: {advice}",
            "  the record's docs_descendants may stay: once the file is committed at a receipt's commit, the file "
            "decides (a record false still vetoes)"], payload, 0


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


CONTRACTS_FLAGS = (("contracts_configs", "--config"), ("contracts_exports", "--export"),
                   ("contracts_no_sandbox", "--no-sandbox"))


def cmd_comparator_contracts(args) -> int:
    """`comparator check NAME --contracts-only` (#280): read-only; the registry is read for local_path, never
    written, and no receipt, event or file in the checkout is written."""
    from . import comparator_contracts as cc
    if not args.check:
        raise UsageError("--contracts-only goes with `lean-pkg comparator check NAME --contracts-only`")
    if args.receipt is not None or args.refuse_private or args.refuse_dirty or getattr(args, "replace", False):
        raise UsageError("--contracts-only reads no receipt: drop --receipt, --refuse-private, --refuse-dirty "
                         "and --replace")
    try:
        exports = cc.parse_exports(args.contracts_exports)
    except ValueError as e:
        raise UsageError(str(e)) from None
    reg = load_registry(registry_path(args))
    override = None
    if args.repo is not None:
        reg, override = repo_override(reg, args.name, args.repo)
        if note := repo_override_note(override):
            print(note, file=sys.stderr)
    repo = repo_path(get_pkg(reg, args.name))
    try:
        res = cc.run(repo, args.contracts_configs or None, exports, not args.contracts_no_sandbox)
    except cc.ContractsError as e:
        raise Fail(f"{args.name}: contracts-only diagnostic: {e}") from None
    if args.json_out:
        print_json({"name": args.name, **res, "repo_override": override})
    else:
        print("\n".join(cc.text_lines(args.name, res)))
    return 0 if res["ok"] else 1


def cmd_comparator(args) -> int:
    repo_arg = getattr(args, "repo", None)
    if getattr(args, "contracts_only", False):
        return cmd_comparator_contracts(args)
    for dest, flag in CONTRACTS_FLAGS:
        if getattr(args, dest, None):
            raise UsageError(f"{flag} goes with `lean-pkg comparator check NAME --contracts-only`")
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
    from . import binding_file   # proposal 0002, stage G3: the registry route
    binding_file.print_deprecations(payload)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0


def cmd_promote(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    reg = load_registry(reg_path)
    if args.yes:
        warn_lease(lease_actor(reg, args.name, args.agent, "publish --yes"))
    out, rc, payload = do_promote(reg_path, reg, events_p,
                                  args.name, args.yes, args.skip_public_safety)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return rc


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
    if getattr(args, "cmd", None) in COMMIT_FLAGS_PASSED_ON:   # verify: to its registry steps (status move, gate)
        return
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
            raise Fail(f"--commit needs {f} inside the registry's git work tree {root}") from None
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
    guarded("stack", lambda: _doctor_stack(add, args, state, checks))
    if getattr(args, "skew_package", None):
        guarded("producer", lambda: _doctor_producer(add, args.skew_package, state, checks))
    return checks, state["reg_ok"]


def _doctor_producer(add, name: str, state: dict, checks: list[dict]) -> None:
    """doctor --package NAME: one `producer <role>` line per gate receipt of NAME, the lean-pkg that wrote it
    against the running one (producer_skew.py): warn on a skew across a breaking change to evidence or on a
    newer producer, ok on the same version or an older one with no such change, n/a when unknown. Never an
    exit 1."""
    from . import producer_skew
    data = state["data"]
    if data is None or name not in (data.get("packages") or {}):
        add("warn", "producer", f"{name}: not a package of the registry; nothing checked")
        checks[-1]["result"] = None
        return
    pkg = data["packages"][name]
    try:
        repo = repo_path(pkg)
    except (KeyError, TypeError, ValueError):
        repo = None
    res = producer_skew.skew(pkg, repo)
    if not res["receipts"]:
        add("n/a", "producer", f"{name}: no gate receipt recorded (gate.receipt and gate.comparator are empty); "
                               f"nothing to compare with lean-pkg {res['running']}")
    for r in res["receipts"]:
        level = r["level"] if r["level"] == "warn" else ("n/a" if r["direction"] == "unknown" else "ok")
        add(level, f"producer {r['role']}", f"{name}: {r['message']}")
    if res["data_problem"]:
        add("warn", "producer", f"{name}: {res['data_problem']}")
    checks[-1]["result"] = res   # cmd_doctor moves it to the payload's top-level `producer_skew`


def _doctor_stack(add, args, state: dict, checks: list[dict]) -> None:
    """The LTS stack line (proposal 0004). Without NAME: the declared stacks and the current one. With NAME:
    the package's committed pins against the stack its coverage.yaml declares; never more than a warn."""
    from . import stacks
    name = getattr(args, "name", None)
    if not name:
        cur = stacks.current()
        add("n/a", "stack", f"declared: {stacks.declared_list()}; current: "
            + (f"{cur['name']}" if cur else f"none on {stacks.today()}")
            + " (doctor NAME checks a package's pins against its stack)")
        return
    data = state["data"]
    if data is None or name not in (data.get("packages") or {}):
        add("warn", "stack", f"{name}: not a package of the registry; nothing checked")
        return
    pkg = data["packages"][name]
    repo = repo_path(pkg)
    if git_head(repo) is None:
        add("warn", "stack", f"{name}: {repo} is not a git checkout with a commit; nothing checked")
        return
    declared_name = stacks.declared_key(repo)
    if declared_name is None:
        add("n/a", "stack", f"{name}: coverage.yaml at HEAD declares no stack (opt in with `stack: NAME`; "
            f"declared: {stacks.declared_list()})")
        return
    comp_rel = (pkg.get("gate") or {}).get("comparator")
    res = stacks.check(repo, declared_name, comparator=recorded_json(repo, comp_rel), comparator_path=comp_rel)
    level, detail = stacks.summary_line(res)
    add(level, "stack", f"{name}: {detail}")
    checks[-1]["result"] = res   # cmd_doctor moves it to the payload's top-level `stack`


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
    if getattr(args, "name", None) and getattr(args, "upgrade_from", None):
        raise UsageError("doctor NAME (the stack check) does not go with --upgrade-from")
    if getattr(args, "skew_package", None) and getattr(args, "upgrade_from", None):
        raise UsageError("doctor --package NAME (the producer check) does not go with --upgrade-from")
    if getattr(args, "upgrade_from", None):   # the upgrade preflight (upgrade.py); never the diagnostics
        from . import upgrade
        return upgrade.run(args)
    if getattr(args, "upgrade_to", None) or getattr(args, "upgrade_source", None):
        raise UsageError("--to and --source go with --upgrade-from OLD")
    checks, ok = doctor_checks(args)
    if args.json_out:
        stack = next((c.pop("result") for c in checks if c["item"] == "stack" and "result" in c), None)
        skew = next((c.pop("result") for c in checks if c["item"].startswith("producer") and "result" in c), None)
        payload = {"ok": ok, "checks": checks, "stack": stack}
        if getattr(args, "skew_package", None):
            payload["producer_skew"] = skew       # additive, only with --package
        print_json(payload)
    else:
        for c in checks:
            print(f"{c['level']} {c['item']}: {c['detail']}")
    return 0 if ok else 1


# Commands that write nothing themselves but pass the commit flags on to the invocations they run
# (`verify`: the status move and the gate); their help shows the flags, and they are never "ignored".
COMMIT_FLAGS_PASSED_ON = ("verify",)


def add_commit_flags(s) -> None:
    """--commit, --push and --no-commit on subparser s (after the command name, as well as before it). Every
    command accepts them, so a script may pass them to any command; show_commit_flags_on_writes hides them
    from the help of a command that never writes (is_write_invocation), where they do nothing."""
    acts = [
        s.add_argument("--commit", action="store_true", default=argparse.SUPPRESS,
                       help=f"write commands: pull --ff-only, then commit the registry files (default: "
                            f"${COMMIT_ENV}=1{'; set now: writes commit unless --no-commit' if _env_on(COMMIT_ENV) else ''})"),
        s.add_argument("--push", action="store_true", default=argparse.SUPPRESS,
                       help=f"--commit, then push to origin (default: ${PUSH_ENV}=1"
                            f"{'; set now: writes commit and push unless --no-commit' if _env_on(PUSH_ENV) else ''})"),
        s.add_argument("--no-commit", dest="no_commit", action="store_true", default=argparse.SUPPRESS,
                       help="never commit or push (overrides the env)"),
    ]
    for a in acts:
        a.commit_flag = True


def _subparser_items(parser):
    """(name, subparser) of parser's subcommands, each parser once (an alias name shares its parser)."""
    seen = set()
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                if id(sp) not in seen:
                    seen.add(id(sp))
                    yield a.dest, name, sp


def _probe_defaults(parser, ns: dict) -> dict:
    """ns updated as argparse updates the namespace when parser is entered: its options' defaults, then its
    set_defaults (a subparser's values overwrite its parent's)."""
    ns = dict(ns)
    for a in parser._actions:
        if a.dest is not argparse.SUPPRESS and a.default is not argparse.SUPPRESS \
                and not isinstance(a, argparse._SubParsersAction):
            ns[a.dest] = a.default
    ns.update(parser._defaults)
    return ns


def may_write(parser, ns: dict) -> bool:
    """Can an invocation of the command `parser` write, by is_write_invocation itself? Probed on the namespace
    the command parses to with no flag given, then with each of its on/off flags given alone (`gate` writes
    unless --check; `publish` with --yes; `search` with --record; the board as if on a tty). A command with
    subcommands may write when one of them may; COMMIT_FLAGS_PASSED_ON count as writes."""
    ns = _probe_defaults(parser, ns)
    subs = list(_subparser_items(parser))
    if subs:
        return any(may_write(sp, {**ns, dest: name}) for dest, name, sp in subs)
    if ns.get("cmd") in COMMIT_FLAGS_PASSED_ON:
        return True
    flags = [a.dest for a in parser._actions if isinstance(a, argparse._StoreTrueAction)
             and not getattr(a, "commit_flag", False) and a.dest != "json_out"]
    for flag in [None] + flags:
        probe = argparse.Namespace(**ns)
        if flag is not None:
            setattr(probe, flag, True)
        if is_write_invocation(probe, interactive=True):
            return True
    return False


def show_commit_flags_on_writes(root) -> None:
    """Hide the commit flags (add_commit_flags) from the help of every command that never writes. They stay
    accepted there: `lean-pkg list --commit` parses as before and prints its `ignored: read-only command`
    note. The split is derived from is_write_invocation (may_write), never kept by hand."""
    def walk(parser, ns):
        ns = _probe_defaults(parser, ns)
        acts = [a for a in parser._actions if getattr(a, "commit_flag", False)]
        if acts and not may_write(parser, ns):
            for a in acts:
                a.help = argparse.SUPPRESS
        for dest, name, sp in _subparser_items(parser):
            walk(sp, {**ns, dest: name})
    walk(root, {})


def main(argv: list[str] | None = None) -> int:
    _ACTOR["agent"] = None   # #21.3: per invocation
    raw = list(sys.argv[1:] if argv is None else argv)
    _Parser.json_requested = "--json" in (raw[:raw.index("--")] if "--" in raw else raw)
    ap = build_parser()
    from . import command_tree
    args = ap.parse_args(argv)
    args.command_argv = getattr(ap, "command_argv", None)
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
    if getattr(args, "json_out", False) or getattr(args, "json", None) is True \
            or not command_tree.notes_enabled():   # json: `receipt comparator`'s own --json
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


def is_write_invocation(args, interactive: bool | None = None) -> bool:
    """Does this invocation, as parsed, write the registry or its event log? The one predicate behind the
    registry lock (locks_registry) and committed mode (commit_mode). Dry runs and read-only forms are not
    writes: register/update/migrate --dry-run, update --write-binding-file (the package's file only), publish without --yes (or with --yes --dry-run), audit without --fix, gate --check, comparator
    --check, formalization without --write or with --no-registry (and `formalization anchor`), claims list/check, index check/render/migrate, search without
    --record, board off a tty, with --json or with --only. `page` (even --write: README.md only) and `coverage check`
    never write the registry. `verify` is not a write itself: its registry steps (the status move, the gate) are
    invocations of their own, each with its own lock and committed mode. `interactive` stands in for
    board_interactive() (the help's commit-flag split, may_write, asks as if on a tty)."""
    cmd = getattr(args, "cmd", None)
    if cmd == "gate":
        return not getattr(args, "check", False)
    if cmd == "update" and getattr(args, "write_binding_file", False):   # the package's file, never the registry
        return False
    if cmd in ("init", "record", "register", "claim", "release", "renew", "update", "migrate"):
        return not getattr(args, "dry_run", False)
    if cmd == "comparator":
        return not getattr(args, "check", False) and not is_host_check(args) \
            and not is_comparator_config(args)
    if cmd in ("promote", "publish"):   # `promote` is `publish` since 1.0; --yes --dry-run is promote's dry run
        return bool(getattr(args, "yes", False)) and not getattr(args, "dry_run", False)
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
        return getattr(args, "fn", None) is cmd_board and not static_view(args) \
            and (board_interactive() if interactive is None else interactive)
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
