# lean-pkg verify -- both receipts and the gate, in one command, on this machine
"""`lean-pkg verify NAME [--work-dir DIR] [--unit UNIT] [--script FILE] [--judges N] [--agent A] [--reason TEXT]
[--dry-run] [--json]`: the newcomer's one step from a committed package to `verified-private` (1.0 command tree,
docs/design/1.0-command-tree.md, decision 4).

A pure orchestrator. Each step is the lean-pkg command a person would type, run as its own invocation (its own
checks, refusals, registry lock and committed mode), and printed before it runs:

1. the comparator receipt: `lean-pkg receipt comparator --project CHECKOUT` (the `comparator-receipt` program);
2. the lower-tier receipt on this workstation: `lean-pkg receipt systemd --unit U --receipt-commit X --log
   W/verify.log --stdout W/unit.out --stderr W/unit.err --repo CHECKOUT --run -- bash verify.sh W/verify.log`,
   W a work directory outside the checkout;
3. for a package still `active`, the status move the gate needs: `lean-pkg update NAME --status gate-pending
   --reason R` (the move the by-hand path makes);
4. the gate: `lean-pkg gate NAME --receipt LOWER --comparator COMPARATOR` (`--regate` for a package already
   verified-private or public).

It writes nothing these commands would not write, and it commits nothing in the package checkout: the receipts
are left for the author to commit (`git add .lean-receipts`), which is the next line it prints.

The order is the binding: both receipts must bind one commit X, the checkout's HEAD when verify starts, so
nothing is committed between them. The comparator receipt runs first because `comparator-receipt` refuses any
untracked file in the checkout, while `receipt systemd` allows the untracked receipts under .lean-receipts/; the
other order would need a commit between the two receipts, which binds them to two commits (the gate refuses
that: both tiers must bind to one commit). A receipt of this commit already present (an earlier verify that
stopped later) is reused, not re-run; the gate validates it as it validates any receipt.

Refused before any step runs: an unknown package, a checkout that is not at a clean commit (a modified tracked
file, or an untracked file other than this commit's receipts under .lean-receipts/), no verify script, no
comparator configuration, a status the gate cannot reach (scratch, archived, upstreamed), --registry-snapshot.
A step that fails stops verify with that step's exit code; the steps before it stay done (their receipts are
reused by the next verify at the same commit).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import re
import shlex
import sys

from . import cli as core

GATE_FROM = ("gate-pending", "active")              # a first gate
REGATE_FROM = ("verified-private", "public")         # a re-gate on a new commit (public: evidence, status kept)
NO_MATHLIB_LINE = re.compile(r"^\s*echo\s+[\"']?mathlib-rev:\s*none[\"']?\s*(?:>>\S+)?\s*$", re.MULTILINE)
STATE_ENV = "XDG_STATE_HOME"


def work_root() -> pathlib.Path:
    base = os.environ.get(STATE_ENV, "").strip()
    return (pathlib.Path(base) if base else pathlib.Path.home() / ".local" / "state") / "lean-pkg" / "verify"


def _git(repo: pathlib.Path, *a: str) -> str:
    p = core.git(repo, *a)
    return p.stdout if p.returncode == 0 else ""


def tracked_at(repo: pathlib.Path, commit: str, rel: str) -> str | None:
    p = core.git(repo, "show", f"{commit}:{rel}")
    return p.stdout if p.returncode == 0 else None


def own_receipts(rel: str, c12: str) -> bool:
    """An untracked path verify may find in a clean checkout: this commit's receipts and their files."""
    return rel.startswith(f".lean-receipts/{c12}-")


def tree_problems(repo: pathlib.Path, c12: str) -> list[str]:
    """What keeps the checkout from being a clean commit for both receipts (paths only)."""
    status = core.git(repo, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return [f"git status failed: {status.stderr.strip()}"]
    out = []
    for line in status.stdout.splitlines():
        code, rel = line[:2], line[3:].strip('"')
        if code == "??" and own_receipts(rel, c12):
            continue
        out.append(f"{'untracked' if code == '??' else 'modified'} {rel}")
    return out


def lower_receipts(repo: pathlib.Path, c12: str, unit: str) -> list[pathlib.Path]:
    """This commit's lower-tier receipts from unit U, newest first."""
    d = repo / ".lean-receipts"
    if not d.is_dir():
        return []
    slug = re.escape(f"{c12}-systemd-user-{unit}.service-")
    rx = re.compile(rf"^{slug}[0-9a-f]{{32}}-[A-Za-z0-9._-]+\.json$")
    found = [p for p in d.iterdir() if p.is_file() and rx.match(p.name)]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def knowingly_no_mathlib(repo: pathlib.Path, commit: str, script: str) -> bool:
    """The committed verify script declares `mathlib-rev: none` (what `new --no-mathlib` writes) and the
    manifest has no Mathlib: the package knowingly has none, which comparator-receipt needs said explicitly
    (COMPARATOR_RECEIPT_NO_MATHLIB=1)."""
    from . import comparator_receipt as cr
    text = tracked_at(repo, commit, script) or ""
    return bool(NO_MATHLIB_LINE.search(text)) and not cr.manifest_has_mathlib(repo)


def _current(name: str) -> str | None:
    return os.environ.get(name)


def invoke(argv: list[str], capture_json: bool = False, quiet_stdout: bool = False) -> tuple[int, dict | None]:
    """Run one lean-pkg invocation in this process, exactly as `lean-pkg ARGV` would (its own parse, checks,
    registry lock and committed mode). capture_json: ARGV ends in --json and its stdout is parsed, not printed;
    quiet_stdout: its stdout goes to stderr (the caller's stdout carries one JSON object)."""
    saved_json = core._Parser.json_requested
    buf = io.StringIO()
    try:
        redirect = contextlib.redirect_stdout(buf) if capture_json else \
            contextlib.redirect_stdout(sys.stderr) if quiet_stdout else contextlib.nullcontext()
        with redirect:
            rc = core.main(list(argv))
    finally:
        core._Parser.json_requested = saved_json
    payload = None
    if capture_json:
        try:
            payload = json.loads(buf.getvalue())
        except ValueError:
            payload = None
    return rc, payload


class Steps:
    """Runs and records the steps: each a lean-pkg argv, printed first (text mode) and run in-process as its own
    invocation. In --json mode a step's stdout goes to stderr, except the JSON of the steps that print one."""

    def __init__(self, args, globals_: list[str]):
        self.json_out = bool(getattr(args, "json_out", False))
        self.globals = globals_
        self.done: list[dict] = []

    def say(self, line: str) -> None:
        print(line, file=sys.stderr if self.json_out else sys.stdout, flush=True)

    def run(self, label: str, argv: list[str], registry: bool = False, want_json: bool = False,
            env: dict | None = None) -> tuple[int, dict | None]:
        full = (self.globals if registry else []) + argv + (["--json"] if want_json and self.json_out else [])
        shown = "lean-pkg " + shlex.join(full)
        self.say(f"verify: {label}: {shown}")
        saved_env = {k: _current(k) for k in (env or {})}
        try:
            os.environ.update(env or {})
            rc, payload = invoke(full, capture_json=want_json and self.json_out, quiet_stdout=self.json_out)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.done.append({"step": label, "command": shown, "rc": rc, **({"env": env} if env else {}),
                          **({"output": payload} if payload is not None else {})})
        return rc, payload

    def skip(self, label: str, why: str) -> None:
        self.say(f"verify: {label}: skipped: {why}")
        self.done.append({"step": label, "skipped": why})


def global_flags(args) -> list[str]:
    """The registry flags the registry steps repeat (update, gate)."""
    out = []
    for flag, dest in (("--registry", "registry"), ("--events", "events")):
        v = getattr(args, dest, None)
        if v:
            out += [flag, str(v)]
    for flag, dest in (("--no-commit", "no_commit"), ("--push", "push"), ("--commit", "commit")):
        if getattr(args, dest, False):
            out.append(flag)
    return out


def plan(args) -> dict:
    """Everything decided before a step runs; refusals here leave nothing behind."""
    if core.registry_snapshot_arg(args):
        raise core.UsageError("verify writes receipts and the gate; it does not run against --registry-snapshot")
    reg = core.load_registry(core.registry_path(args))
    pkg = core.get_pkg(reg, args.name)
    status = pkg.get("status")
    if status not in GATE_FROM + REGATE_FROM:
        hint = (f"`lean-pkg update {args.name} --status active --reason R` first" if status == "scratch"
                else "a package in this status is not gated again")
        raise core.Fail(f"{args.name}: status {status}: verify gates active, gate-pending, verified-private or "
                        f"public packages; {hint}")
    if args.agent:
        core.lease_actor(reg, args.name, args.agent, "verify")   # a foreign live lease refuses before any run
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{args.name}: local_path does not exist: {repo}")
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{args.name}: {repo} is not a git checkout with a commit")
    c12 = head[:12]
    gate = pkg.get("gate") or {}
    bound = core.recorded_json(repo, gate.get("receipt"))
    already = None   # the gated commit, when HEAD is it or an evidence-only descendant of it
    if isinstance(bound, dict) and gate.get("comparator") and isinstance(bound.get("commit"), str):
        if bound["commit"] == head:
            already = head
        else:
            ok, _note, _docs = core.package_descendant(repo, bound["commit"], head, pkg, checker=bound.get("checker"))
            already = bound["commit"] if ok else None
    if already:
        return {"reg": reg, "pkg": pkg, "status": status, "repo": repo, "head": head, "c12": c12,
                "already": already, "work": None}
    problems = tree_problems(repo, c12)
    if problems:
        shown = "; ".join(problems[:10]) + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else "")
        raise core.Fail(f"{args.name}: the checkout is not a clean commit ({shown}); both receipts bind HEAD "
                        f"{c12}, so commit or remove these first; nothing run")
    script = args.script
    if tracked_at(repo, head, script) is None:
        raise core.Fail(f"{args.name}: no {script} committed at {c12} (the lower-tier check `lean-pkg new` "
                        f"writes; --script FILE names another); nothing run")
    from . import comparator_receipt as cr
    if not cr.list_configs(repo):
        raise core.Fail(f"{args.name}: no comparator configuration in {repo} (comparator*.json or "
                        f"comparator/config*.json); nothing run")
    unit = args.unit or f"{args.name}-verify"
    if not re.fullmatch(r"[A-Za-z0-9:_.\\-]+", unit):
        raise core.UsageError(f"--unit {unit!r}: a systemd unit name is letters, digits and :_.-\\ only")
    if args.work_dir:
        work = pathlib.Path(args.work_dir).expanduser().resolve()
    else:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        work = work_root() / args.name / f"{c12}-{stamp}"
    try:
        work.relative_to(repo.resolve())
        raise core.UsageError(f"--work-dir {work} is inside the checkout; the run's log and unit files go outside "
                              f"it (the run refuses an untracked file in the package)")
    except ValueError:
        pass
    comparator = repo / ".lean-receipts" / f"{c12}-comparator.json"
    return {"reg": reg, "pkg": pkg, "status": status, "repo": repo, "head": head, "c12": c12, "unit": unit,
            "work": work, "script": script, "already": already, "comparator": comparator,
            "no_mathlib": knowingly_no_mathlib(repo, head, script)}


def cmd_verify(args) -> int:
    p = plan(args)
    name, repo, head, c12 = args.name, p["repo"], p["head"], p["c12"]
    steps = Steps(args, global_flags(args))
    payload = {"ok": False, "name": name, "commit": head, "checkout": str(repo), "dry_run": bool(args.dry_run),
               "status_before": p["status"], "work_dir": str(p["work"]) if p["work"] else None, "steps": steps.done,
               "receipts": {},
               "next": []}
    if p["already"]:
        at = p["already"][:12]
        steps.say(f"{name}: already verified at {at}" + ("" if p["already"] == head else
                                                          f" (HEAD {c12} is an evidence-only descendant)")
                  + f": gate receipt {p['pkg']['gate'].get('receipt')}, comparator "
                    f"{p['pkg']['gate'].get('comparator')}; nothing to do")
        payload.update(ok=True, already=p["already"], status=p["status"])
        return _finish(args, payload, 0)
    work, unit, script = p["work"], p["unit"], p["script"]
    comp_argv = ["receipt", "comparator", "--project", str(repo)] + (["--judges", str(args.judges)] if args.judges else [])
    comp_env = {"COMPARATOR_RECEIPT_NO_MATHLIB": "1"} if p["no_mathlib"] else None
    log = work / "verify.log"
    lower_argv = ["receipt", "systemd", "--unit", unit, "--receipt-commit", head, "--log", str(log),
                  "--stdout", str(work / "unit.out"), "--stderr", str(work / "unit.err"), "--repo", str(repo),
                  "--run", "--", "bash", script, str(log)]
    regate = p["status"] in REGATE_FROM
    if args.dry_run:
        steps.say(f"verify {name}: dry run at {c12}; nothing run, nothing written. The steps:")
        env = "".join(f"{k}={v} " for k, v in (comp_env or {}).items())
        steps.say(f"  1/3 comparator receipt: {env}lean-pkg {shlex.join(comp_argv)}")
        steps.say(f"  2/3 lower-tier receipt: lean-pkg {shlex.join(lower_argv)}")
        if p["status"] == "active":
            steps.say(f"  status: lean-pkg update {name} --status gate-pending --reason \"verify: receipts at {c12}\"")
        steps.say(f"  3/3 gate: lean-pkg gate {name} --receipt .lean-receipts/{c12}-systemd-user-{unit}.service-"
                  f"<invocation>-<host label>.json --comparator .lean-receipts/{c12}-comparator.json"
                  + (" --regate" if regate else ""))
        payload.update(ok=True)
        return _finish(args, payload, 0)

    # 1. the comparator receipt (first: it refuses any untracked file, and step 2 writes some)
    comparator = p["comparator"]
    if comparator.is_file():
        steps.skip("1/3 comparator receipt", f"{comparator.relative_to(repo)} already binds {c12} (an earlier "
                                             f"run); the gate checks it")
    else:
        if comp_env:
            steps.say(f"verify: {script} declares `mathlib-rev: none` and lake-manifest.json has no Mathlib: the "
                      f"comparator runs with COMPARATOR_RECEIPT_NO_MATHLIB=1")
        rc, _ = steps.run("1/3 comparator receipt", comp_argv, env=comp_env)
        if rc != 0 or not comparator.is_file():
            return _finish(args, payload, rc or 1, f"the comparator receipt failed (exit {rc}); nothing gated")
    payload["receipts"]["comparator"] = comparator.relative_to(repo).as_posix()

    # 2. the lower-tier receipt, at the same commit (nothing committed in between)
    lower = lower_receipts(repo, c12, unit)
    if lower:
        steps.skip("2/3 lower-tier receipt", f"{lower[0].relative_to(repo)} already binds {c12} (an earlier "
                                             f"run); the gate checks it")
    else:
        work.mkdir(parents=True, exist_ok=True)
        rc, _ = steps.run("2/3 lower-tier receipt", lower_argv)
        lower = lower_receipts(repo, c12, unit)
        if rc != 0 or not lower:
            return _finish(args, payload, rc or 1, f"the lower-tier receipt failed (exit {rc}); the comparator "
                                                   f"receipt stays and is reused by the next verify at {c12}")
    payload["receipts"]["lower"] = lower[0].relative_to(repo).as_posix()

    # 3. the gate (an `active` package first moves to gate-pending, as by hand)
    agent = ["--agent", args.agent] if args.agent else []
    if p["status"] == "active":
        rc, _ = steps.run("status", ["update", name, "--status", "gate-pending", "--reason",
                                     f"verify: both receipts bind {c12}"] + agent, registry=True, want_json=True)
        if rc != 0:
            return _finish(args, payload, rc, "the status move to gate-pending failed; nothing gated")
    gate_argv = ["gate", name, "--receipt", str(lower[0]), "--comparator", str(comparator)] + agent
    if regate:
        gate_argv.append("--regate")
        if args.reason:
            gate_argv += ["--reason", args.reason]
    rc, out = steps.run("3/3 gate", gate_argv, registry=True, want_json=True)
    if out is not None:
        payload["gate"] = out
    if rc != 0:
        return _finish(args, payload, rc, "the gate refused; the receipts stay and are reused by the next verify "
                                          f"at {c12}")
    reg = core.load_registry(core.registry_path(args))
    payload["status"] = core.get_pkg(reg, name).get("status")
    payload["ok"] = True
    payload["next"] = [f"git -C {shlex.quote(str(repo))} add .lean-receipts && git -C {shlex.quote(str(repo))} "
                       f"commit -m \"evidence: receipts at {c12}\"",
                       f"lean-pkg page {name} --write"]
    return _finish(args, payload, 0)


def _finish(args, payload: dict, rc: int, why: str | None = None) -> int:
    if why:
        payload["error"] = why
    if getattr(args, "json_out", False):
        core.print_json(payload)
        return rc
    if rc == 0 and payload.get("receipts", {}).get("lower") and not payload.get("dry_run"):
        print(f"verify {payload['name']}: {payload.get('status')} at {payload['commit'][:12]}: lower-tier "
              f"{payload['receipts']['lower']}, comparator {payload['receipts']['comparator']}")
        print("next:")
        for c in payload["next"]:
            print(f"  {c}")
    elif why:
        print(f"verify {payload['name']}: stopped: {why}", file=sys.stderr)
    return rc


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("verify", help="both receipts and the gate in one step on this machine: the comparator "
                                      "receipt, the lower-tier receipt (systemd user unit) at the same commit, then "
                                      "the gate; prints each command it runs",
                       description="Run, at the checkout's HEAD, `receipt comparator`, `receipt systemd --run -- bash "
                                   "verify.sh` and `gate` (an active package first moves to gate-pending; a gated "
                                   "one is re-gated), each as its own lean-pkg invocation with its own checks. "
                                   "Writes only what those commands write; commits nothing in the checkout.")
    s.add_argument("name")
    s.add_argument("--work-dir", default=None, metavar="DIR",
                   help=f"where the run's log and unit output go, outside the checkout (default "
                        f"${STATE_ENV}/lean-pkg/verify/NAME/<commit>-<UTC stamp>, else ~/.local/state/...)")
    s.add_argument("--unit", default=None, metavar="UNIT", help="the systemd user unit (default NAME-verify)")
    s.add_argument("--script", default="verify.sh", metavar="FILE",
                   help="the committed lower-tier check, run as `bash FILE LOG` (default verify.sh)")
    s.add_argument("--judges", type=int, default=None, metavar="N",
                   help="comparator judge processes (`receipt comparator --judges N`)")
    s.add_argument("--agent", default=None, help="the agent acting: passed to the status move and the gate")
    s.add_argument("--reason", default=None, metavar="TEXT",
                   help="re-gate only: accept a commit that does not descend from the gated one (`gate --regate "
                        "--reason`)")
    s.add_argument("--dry-run", action="store_true", help="print the steps and their commands; run nothing")
    add_json(s)
    s.set_defaults(fn=cmd_verify)
