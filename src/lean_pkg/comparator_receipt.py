# comparator-receipt -- run comparator-check.sh on a Lake project and write
# the comparator receipt that `lean-pkg gate --comparator` / `lean-pkg
# comparator` consume. Writes NOTHING unless every comparator configuration
# passed, so a failed run can never be mistaken for evidence.
"""Usage (from a Lake project directory):

    comparator-receipt [--project DIR] [--check SCRIPT]

Host policy: the run is allowed iff SLURM_JOB_ID is set (inside a Slurm job),
OR this host's name is listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS
(comma-separated), OR COMPARATOR_RECEIPT_ALLOW_HOST=1. The reason is recorded
in the receipt's host_policy field.

Env: COMPARATOR_CHECK (script path; default: the comparator-check.sh shipped
inside the lean_pkg package), SLURM_JOB_ID (job identity when under Slurm).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import importlib.resources
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile

SCHEMA = "comparator-receipt/1"
PASS_LINE = "Your solution is okay!"
PIN_RE = re.compile(r"^== (comparator|lean4export|nanoda_lib|landrun) @ ([0-9a-f]{40})",
                    re.MULTILINE)
PIN_KEYS = {"comparator": "comparator", "lean4export": "lean4export",
            "nanoda_lib": "nanoda", "landrun": "landrun"}


class RunnerError(Exception):
    pass


def default_check_script():
    """The comparator-check.sh shipped as package data (a Traversable)."""
    return importlib.resources.files("lean_pkg") / "scripts" / "comparator-check.sh"


# ---------------------------------------------------------------------------
# Host policy. Heavy Lean work should run where the operator intends it to:
# inside a Slurm job, or on a host the operator lists explicitly. No host or
# user names are built in. COMPARATOR_RECEIPT_ALLOW_HOST=1 overrides for tests
# and explicitly authorized runs; the reason is recorded in the receipt's
# host_policy field.

ALLOWED_HOSTS_ENV = "COMPARATOR_RECEIPT_ALLOWED_HOSTS"
ALLOW_HOST_ENV = "COMPARATOR_RECEIPT_ALLOW_HOST"


def parse_allowed_hosts(value: str | None) -> set[str]:
    """Comma-separated host names; blanks and surrounding spaces ignored."""
    return {h.strip() for h in (value or "").split(",") if h.strip()}


def host_policy_ok(slurm_job: str | None, allow: bool,
                   hostname: str | None = None,
                   allowed_hosts: set[str] | None = None) -> tuple[bool, str]:
    hostname = hostname or socket.gethostname()
    allowed_hosts = allowed_hosts or set()
    if allow:
        return True, f"allowed by {ALLOW_HOST_ENV}=1"
    if slurm_job:
        return True, f"Slurm job {slurm_job} on {hostname}"
    if hostname in allowed_hosts:
        return True, f"host {hostname} listed in {ALLOWED_HOSTS_ENV}"
    return False, (f"{hostname} is not an allowed host: run inside a Slurm job "
                   f"(SLURM_JOB_ID set), list this host in {ALLOWED_HOSTS_ENV} "
                   f"(comma-separated), or set {ALLOW_HOST_ENV}=1 for an "
                   f"explicitly authorized run")

# ---------------------------------------------------------------------------


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg_name(path: str) -> str:
    """Same rule as comparator-check.sh: strip .json, '/' -> '_'."""
    base = path[:-5] if path.endswith(".json") else path
    return base.replace("/", "_")


def parse_pins(text: str) -> dict:
    pins = {PIN_KEYS[m.group(1)]: m.group(2) for m in PIN_RE.finditer(text)}
    missing = [k for k in ("comparator", "lean4export", "nanoda", "landrun")
               if k not in pins]
    if missing:
        raise RunnerError(f"comparator-check output lacks tool pins for {missing}")
    return pins


def list_configs(project: pathlib.Path) -> list[str]:
    cwd = os.getcwd()
    os.chdir(project)
    try:
        found = (sorted(glob.glob("comparator*.json"))
                 + sorted(glob.glob("comparator/config*.json")))
    finally:
        os.chdir(cwd)
    return found


def git(project: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True)


def run(project: pathlib.Path, check: pathlib.Path, host_policy: str = "") -> dict:
    if not (project / "lean-toolchain").is_file():
        raise RunnerError(f"{project}: not a Lake project (no lean-toolchain)")
    if not check.is_file():
        raise RunnerError(f"comparator-check script not found: {check}")
    st = git(project, "status", "--porcelain")
    if st.returncode != 0:
        raise RunnerError(f"{project}: not a git repository ({st.stderr.strip()})")
    if st.stdout.strip():
        files = ", ".join(line[3:] for line in st.stdout.splitlines()[:10])
        raise RunnerError(f"dirty tree — commit or remove before a receipt run: {files}")
    commit = git(project, "rev-parse", "HEAD").stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RunnerError(f"cannot resolve HEAD in {project}")
    configs = list_configs(project)
    if not configs:
        raise RunnerError("no comparator configuration found "
                          "(comparator*.json or comparator/config*.json)")
    toolchain = (project / "lean-toolchain").read_text().strip()
    with tempfile.TemporaryDirectory(prefix="comparator-receipt.") as tmp:
        log_dir = pathlib.Path(tmp) / "logs"
        env = os.environ.copy()
        env["LOG_DIR"] = str(log_dir)
        p = subprocess.run(["bash", str(check)], cwd=project, env=env,
                           capture_output=True, text=True)
        sys.stdout.write(p.stdout)
        if p.returncode != 0:
            sys.stderr.write(p.stderr)
            raise RunnerError(f"comparator-check exited {p.returncode}; nothing written")
        pins = parse_pins(p.stdout)
        entries = []
        staged: list[tuple[pathlib.Path, pathlib.Path]] = []
        for cfg_rel in configs:
            name = cfg_name(cfg_rel)
            src_log = log_dir / f"{name}.log"
            if not src_log.is_file():
                raise RunnerError(f"no log for {cfg_rel} at {src_log}; nothing written")
            lines = src_log.read_text(encoding="utf-8", errors="replace").splitlines()
            if PASS_LINE not in lines:
                raise RunnerError(f"{cfg_rel}: log lacks the PASS line; nothing written")
            cfg = json.loads((project / cfg_rel).read_bytes())
            log_rel = f".lean-receipts/{commit[:12]}-comparator-{name}.log"
            staged.append((src_log, project / log_rel))
            entries.append({
                "path": cfg_rel,
                "sha256": sha256_file(project / cfg_rel),
                "challenge_module": cfg.get("challenge_module"),
                "solution_module": cfg.get("solution_module"),
                "theorem_names": list(cfg.get("theorem_names") or []),
                "definition_names": list(cfg.get("definition_names") or []),
                "permitted_axioms": list(cfg.get("permitted_axioms") or []),
                "log": log_rel,
                "log_sha256": sha256_file(src_log),
                "result": "pass",
            })
        host = socket.gethostname()
        now = dt.datetime.now(dt.timezone.utc)
        job = (os.environ.get("SLURM_JOB_ID")
               or f"local-{host}-{os.getpid()}-{now.strftime('%Y%m%dT%H%M%SZ')}")
        receipt = {
            "schema": SCHEMA, "commit": commit, "lean_toolchain": toolchain,
            "tools": pins, "host": host, "host_policy": host_policy, "job_id": job,
            "generated_at": now.isoformat(timespec="seconds"),
            "result": "pass", "configs": entries,
        }
        rec_tmp = pathlib.Path(tmp) / "receipt.json"
        rec_tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        rec_dir = project / ".lean-receipts"
        rec_path = rec_dir / f"{commit[:12]}-comparator.json"
        staged.append((rec_tmp, rec_path))
        # Everything passed: now, and only now, write into the repo — all or nothing.
        created_dir = not rec_dir.exists()
        written: list[pathlib.Path] = []
        try:
            rec_dir.mkdir(exist_ok=True)
            for src, dst in staged:
                fd, part_s = tempfile.mkstemp(dir=rec_dir, prefix=dst.name + ".",
                                              suffix=".tmp")
                os.close(fd)
                part = pathlib.Path(part_s)
                written.append(part)
                shutil.copy2(src, part)
                os.replace(part, dst)
                written[-1] = dst
        except OSError as e:
            for w in written:
                try:
                    w.unlink()
                except OSError:
                    pass
            if created_dir:
                try:
                    rec_dir.rmdir()
                except OSError:
                    pass
            raise RunnerError(f"could not write evidence into .lean-receipts/ ({e}); "
                              f"nothing written")
    return {"receipt": str(rec_path), "commit": commit, "configs": len(entries)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="comparator-receipt",
        description="run comparator-check.sh and write a comparator receipt")
    ap.add_argument("--project", default=".",
                    help="Lake project directory (default: cwd)")
    ap.add_argument("--check",
                    default=os.environ.get("COMPARATOR_CHECK") or None,
                    help="comparator-check.sh to run (env COMPARATOR_CHECK; "
                         "default: the copy shipped with lean-pkg)")
    args = ap.parse_args(argv)
    ok, why = host_policy_ok(os.environ.get("SLURM_JOB_ID"),
                             os.environ.get(ALLOW_HOST_ENV) == "1",
                             hostname=socket.gethostname(),
                             allowed_hosts=parse_allowed_hosts(
                                 os.environ.get(ALLOWED_HOSTS_ENV)))
    if not ok:
        print(f"comparator-receipt: refusing: {why}", file=sys.stderr)
        return 1
    try:
        if args.check:
            res = run(pathlib.Path(args.project).resolve(),
                      pathlib.Path(args.check).resolve(), host_policy=why)
        else:
            with importlib.resources.as_file(default_check_script()) as check:
                res = run(pathlib.Path(args.project).resolve(),
                          pathlib.Path(check).resolve(), host_policy=why)
    except RunnerError as e:
        print(f"comparator-receipt: {e}", file=sys.stderr)
        return 1
    print(f"comparator receipt written: {res['receipt']} ({res['configs']} config(s), "
          f"commit {res['commit'][:12]}, host policy: {why})")
    print("commit .lean-receipts/ in this repo, then record it:")
    print(f"  lean-pkg comparator <name> --receipt {res['receipt']}")
    print(f"  (or at gate time: lean-pkg gate <name> --receipt R.json "
          f"--comparator {res['receipt']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
