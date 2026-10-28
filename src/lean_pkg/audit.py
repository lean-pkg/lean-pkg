# lean-pkg -- registry audit and validate
"""`lean-pkg registry audit` (audit_registry: expired leases, GitHub visibility, paths, drift, the toolchain
floor; --fix reconciles) and `lean-pkg registry validate` (validate_registry: schema, enums, history sanity,
also run before every registry write).

Moved verbatim out of cli.py (#317, stage 3). cli.py re-exports every name defined here for at least one
release; lean-pkg's own modules import this module (`from . import audit`). It imports core.py and gate.py;
the field validators and display helpers that stay in cli.py with the commands they belong to (update,
record, the publication hold) are imported from cli.py inside the two functions that use them, so importing
this module never imports cli.py.
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

import yaml

from .core import (
    current_branch, events_path, Fail, gh, gh_nwo, git, git_head, _HEX40, HOME, lease_expiry, LEASE_FIELDS,
    lease_live, LEASE_PARSE_ERRORS, load_registry, local_path_key, namespace_clashes, parse_ts, print_json,
    record, reference_branch, registry_not_found, registry_path, repo_path, REQUIRED_FIELDS,
    retracted_entries, save_registry, scrubbed_env, STATUSES, SUPPORTED_REGISTRY_VERSIONS, TRACKS,
    UPSTREAM_TARGETS, UsageError, V2_PACKAGE_DEFAULTS, V3_PACKAGE_DEFAULTS, VISIBILITIES,
)
from .gate import _same_remote, package_descendant, recorded_json


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
    from .cli import consent_unique, hold_text   # update/record helpers; cli imports audit
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
    from .cli import (   # the field validators of `update` (cli.py); cli imports audit
        ai_assistance_errors, namespace_notes_errors, paper_errors, publication_hold_errors,
        source_paper_errors,
    )
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["top level is not a mapping"]
    version = data.get("version")
    if version not in SUPPORTED_REGISTRY_VERSIONS:
        errors.append(f"version must be one of {SUPPORTED_REGISTRY_VERSIONS}, got {version!r}")
    from .recommended import shape_errors   # the top-level lean_pkg key (#388)
    errors += shape_errors(data)
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
# the audit and validate commands

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


def cmd_validate(args) -> int:
    reg_path = registry_path(args)
    try:
        data = yaml.safe_load(reg_path.read_text())
    except FileNotFoundError:
        raise Fail(registry_not_found(reg_path)) from None
    except yaml.YAMLError as e:
        raise Fail(f"registry is not valid YAML: {e}") from e
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
