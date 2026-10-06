#!/usr/bin/env python3
"""Fixtures for lean-pkg tests: tmp registry, fake git repos, receipt builder."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


def _scrub_git_local_env() -> None:
    """Drop the repository-local variables Git exports to hooks (GIT_DIR,
    GIT_WORK_TREE, GIT_INDEX_FILE, ...). When the suite runs from a git hook,
    leaving them set would make every fixture `git init` / `git commit` act on
    the enclosing repository instead of the tmp fixture repo."""
    names = {"GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
             "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
             "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE",
             "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
             "GIT_INTERNAL_SUPER_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR"}
    try:
        p = subprocess.run(["git", "rev-parse", "--local-env-vars"],
                           capture_output=True, text=True, check=False)
        names.update(x.strip() for x in p.stdout.splitlines() if x.strip())
    except OSError:
        pass
    for name in names:
        os.environ.pop(name, None)
    # Registry/notification/host-policy settings from the caller's shell must
    # not leak into the tests either.
    for name in ("LEAN_PKG_REGISTRY", "LEAN_PKG_NOTIFY_CMD", "COMPARATOR_CHECK",
                 "COMPARATOR_RECEIPT_ALLOW_HOST", "COMPARATOR_RECEIPT_ALLOWED_HOSTS",
                 "SLURM_JOB_ID"):
        os.environ.pop(name, None)


_scrub_git_local_env()

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLI = [sys.executable, "-m", "lean_pkg.cli"]
RECEIPT_CLI = [sys.executable, "-m", "lean_pkg.comparator_receipt"]


def subprocess_env(extra: dict | None = None) -> dict:
    """os.environ with src/ importable, so `python -m lean_pkg...` works from
    a plain checkout as well as from an installed package."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if extra:
        env.update(extra)
    return env
TOOLCHAIN = "leanprover/lean4:v4.32.0"
MATHLIB_REV = "81a5d257c8e410db227a6665ed08f64fea08e997"
MATHLIB_MANIFEST = json.dumps(
    {"packages": [{"name": "mathlib", "rev": MATHLIB_REV}]})

CANDIDATES_YAML = """\
version: 1
candidates:
  cool-lib:
    upstream: https://github.com/example/cool-lib
    license: Apache-2.0
    status: adopted
    adopted_at: 2026-09-20
    upstream_pin: abc123def456
    consumers: [alpha]
    notes: ""
  watch-lib:
    upstream: https://github.com/example/watch-lib
    license: MIT
    status: new
    notes: ""
"""


@pytest.fixture()
def candidates_file(tmp_path: Path) -> Path:
    """Adoption ledger at the default resolution path (registry's directory)."""
    p = tmp_path / "lean-adoption-candidates.yaml"
    p.write_text(CANDIDATES_YAML)
    return p


def load_module():
    return importlib.import_module("lean_pkg.cli")


@pytest.fixture()
def lp():
    return load_module()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True)


def make_repo(path: Path, toolchain: str = TOOLCHAIN, extra_files=None) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    (path / "lean-toolchain").write_text(toolchain + "\n")
    for rel, content in (extra_files or {}).items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path


def repo_head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def make_receipt(repo: Path, **overrides) -> dict:
    receipt = {
        "commit": repo_head(repo),
        "lean_toolchain": (repo / "lean-toolchain").read_text().strip(),
        "mathlib_rev": "81a5d257c8e410db227a6665ed08f64fea08e997",
        "sorry_count": 0,
        "axioms": ["propext", "Quot.sound", "Classical.choice"],
        "report_sha256": "a" * 64,
        "job_id": "slurm-12345",
        "checker": "leancheck",
        "generated_at": "2026-09-19T00:00:00+00:00",
    }
    receipt.update(overrides)
    return receipt


def write_receipt(tmp_path: Path, receipt: dict, name: str = "receipt.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(receipt))
    return p


COMPARATOR_PASS_LINE = "Your solution is okay!"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def cfg_name(cfg_rel: str) -> str:
    """Same rule as comparator-check.sh: strip .json, '/' -> '_'."""
    base = cfg_rel[:-5] if cfg_rel.endswith(".json") else cfg_rel
    return base.replace("/", "_")


def add_comparator_config(repo: Path, name: str = "comparator.json",
                          theorem_names=("foo_le_bar",),
                          permitted_axioms=("propext", "Quot.sound",
                                            "Classical.choice")) -> str:
    cfg = {
        "challenge_module": "Challenge",
        "solution_module": "Solution",
        "theorem_names": list(theorem_names),
        "definition_names": [],
        "permitted_axioms": list(permitted_axioms),
        "enable_nanoda": True,
    }
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")
    return name


def write_comparator_log(repo: Path, commit: str, cfg_rel: str,
                         passing: bool = True) -> str:
    rel = f".lean-receipts/{commit[:12]}-comparator-{cfg_name(cfg_rel)}.log"
    p = repo / rel
    p.parent.mkdir(exist_ok=True)
    body = ["Building Challenge", "Building Solution",
            "Running nanoda kernel on solution",
            "nanoda kernel accepts the solution"]
    if passing:
        body += [COMPARATOR_PASS_LINE, f"PASS {cfg_rel}"]
    else:
        body += ["::error::comparator did not accept"]
    p.write_text("\n".join(body) + "\n")
    return rel


def make_comparator_receipt(repo: Path, cfg_rel: str = "comparator.json",
                            **overrides) -> dict:
    commit = repo_head(repo)
    cfg_bytes = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{cfg_rel}"],
        check=True, capture_output=True).stdout
    cfg = json.loads(cfg_bytes)
    log_rel = write_comparator_log(repo, commit, cfg_rel, passing=True)
    log_bytes = (repo / log_rel).read_bytes()
    receipt = {
        "schema": "comparator-receipt/1",
        "commit": commit,
        "lean_toolchain": (repo / "lean-toolchain").read_text().strip(),
        "tools": {"comparator": "5" * 40, "lean4export": "6" * 40,
                  "nanoda": "7" * 40, "landrun": "8" * 40},
        "host": "build-host",
        "job_id": "local-build-host-4242-20261005T120000Z",
        "generated_at": "2026-10-05T12:00:00+00:00",
        "result": "pass",
        "configs": [{
            "path": cfg_rel,
            "sha256": sha256(cfg_bytes),
            "challenge_module": cfg["challenge_module"],
            "solution_module": cfg["solution_module"],
            "theorem_names": cfg["theorem_names"],
            "definition_names": cfg["definition_names"],
            "permitted_axioms": cfg["permitted_axioms"],
            "log": log_rel,
            "log_sha256": sha256(log_bytes),
            "result": "pass",
        }],
    }
    receipt.update(overrides)
    return receipt


def base_pkg(name: str, repo_path: Path, status: str, **kw) -> dict:
    pkg = {
        "name": name,
        "repo": f"git@github.com:example/{name}.git",
        "local_path": str(repo_path),  # absolute local_path passes the $HOME join
        "visibility": "private",
        "status": status,
        "owner_project": None,
        "workspace": None,
        "agent": None,
        "lease": None,
        "lean_toolchain": TOOLCHAIN,
        "mathlib_pin": "81a5d257c8",
        "upstream_target": "none",
        "automation": "",
        "gate": {"receipt": None,
                 "axioms_allowed": ["propext", "Quot.sound", "Classical.choice"]},
        "promoted_at": None,
        "promoted_commit": None,
        "reaudit_after": None,
        "notes": "",
        "history": [],
        "license_spdx": None,
        "formalization_yaml": None,
        "palomar": [],
        "doi": None,
        "reservoir": None,
        "consent": [],
    }
    pkg.update(kw)
    return pkg


class Env(SimpleNamespace):
    registry: Path
    events: Path
    repo_a: Path
    repo_b: Path
    receipt_b: dict
    comp_b: dict


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    """Two packages: alpha (gate-pending, bare repo), beta (verified-private,
    repo carries LICENSE + lake-manifest.json + a HEAD-bound gate receipt)."""
    repo_a = make_repo(tmp_path / "alpha-repo")
    repo_b = make_repo(tmp_path / "beta-repo",
                       extra_files={"LICENSE": "MIT\n",
                                    "lake-manifest.json": "{}\n"})
    cfg_b = add_comparator_config(repo_b)          # commits comparator.json
    receipt_b = make_receipt(repo_b)               # bound to the new HEAD
    dest = repo_b / ".lean-receipts" / "seed.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(receipt_b, indent=2))
    comp_b = make_comparator_receipt(repo_b, cfg_b)
    comp_rel = f".lean-receipts/{repo_head(repo_b)[:12]}-comparator.json"
    (repo_b / comp_rel).write_text(json.dumps(comp_b, indent=2))
    reg = {
        "version": 2,
        "packages": {
            "alpha": base_pkg("alpha", repo_a, "gate-pending"),
            "beta": base_pkg("beta", repo_b, "verified-private",
                             gate={"receipt": ".lean-receipts/seed.json",
                                   "comparator": comp_rel,
                                   "axioms_allowed": ["propext", "Quot.sound",
                                                      "Classical.choice"]}),
        },
    }
    reg_path = tmp_path / "lean-packages.yaml"
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False))
    return Env(registry=reg_path,
               events=tmp_path / "lean-package-events.jsonl",
               repo_a=repo_a, repo_b=repo_b, receipt_b=receipt_b,
               comp_b=comp_b)


def read_reg(env: Env) -> dict:
    return yaml.safe_load(env.registry.read_text())


def write_reg(env: Env, reg: dict) -> None:
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))


def read_events(env: Env) -> list[dict]:
    if not env.events.is_file():
        return []
    return [json.loads(line) for line in env.events.read_text().splitlines()
            if line.strip()]


def set_lease(env: Env, pkg_name: str, agent: str = "vera", workspace: str = "w3",
              hours_ago: float = 1, ttl: int = 24) -> None:
    reg = read_reg(env)
    claimed = (datetime.now(timezone.utc)
               - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    reg["packages"][pkg_name]["lease"] = {
        "workspace": workspace, "agent": agent, "claimed_at": claimed,
        "ttl_hours": ttl, "reason": "test",
    }
    reg["packages"][pkg_name]["workspace"] = workspace
    reg["packages"][pkg_name]["agent"] = agent
    write_reg(env, reg)


class FakeGh:
    """Records calls; answers `gh repo view` (both -q and JSON shapes) and
    `gh repo edit`. Never touches the network."""

    def __init__(self, visibility: str = "PRIVATE", archived: bool = False):
        self.visibility = visibility
        self.archived = archived
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        if "view" in args:
            if "-q" in args:
                return SimpleNamespace(returncode=0,
                                       stdout=self.visibility + "\n", stderr="")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"visibility": self.visibility,
                                   "isArchived": self.archived}),
                stderr="")
        if "edit" in args:
            if "--visibility" in args:
                self.visibility = args[args.index("--visibility") + 1].upper()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected gh call")


@pytest.fixture()
def fake_gh(lp, monkeypatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(lp, "gh", fake)
    monkeypatch.setattr(lp, "find_reuse", lambda: None)
    return fake


@pytest.fixture()
def fake_notify(lp, monkeypatch) -> list[str]:
    calls: list[str] = []

    def _notify(msg):
        calls.append(msg)
        return "sent"
    monkeypatch.setattr(lp, "notify", _notify)
    return calls

# --- index fixtures (lean-pkg index) ---------------------------------------

@pytest.fixture()
def idx(lp):
    return importlib.import_module("lean_pkg.index")


MIT_TEXT = ("MIT License\n\nPermission is hereby granted, free of charge, to any person "
            "obtaining a copy of this software.\n")
INDEX_URL = "https://github.com/example/{name}"


def make_index_package(root: Path, name: str = "demo", *, lower: bool = True,
                       comparator: bool = True, **comp_overrides) -> SimpleNamespace:
    """A package repo whose evidence is committed in an evidence-only descendant
    of the pin: pin = the commit the receipts bind; evidence = pin + .lean-receipts/."""
    repo = make_repo(root / f"{name}-repo", extra_files={
        "LICENSE": MIT_TEXT,
        "lake-manifest.json": MATHLIB_MANIFEST,
        "Demo/Main.lean": "theorem foo_le_bar : True := trivial\n"})
    if comparator:
        add_comparator_config(repo)
    pin = repo_head(repo)
    lower_rel = comp_rel = None
    if lower:
        lower_rel = f".lean-receipts/{pin[:12]}-slurm-12345.json"
        (repo / ".lean-receipts").mkdir(exist_ok=True)
        (repo / lower_rel).write_text(json.dumps(make_receipt(repo), indent=2) + "\n")
    if comparator:
        comp = make_comparator_receipt(repo, **comp_overrides)   # writes the log as well
        comp_rel = f".lean-receipts/{pin[:12]}-comparator.json"
        (repo / comp_rel).write_text(json.dumps(comp, indent=2) + "\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "evidence")
    return SimpleNamespace(name=name, repo=repo, pin=pin, evidence=repo_head(repo),
                           lower_rel=lower_rel, comp_rel=comp_rel,
                           url=INDEX_URL.format(name=name))


def index_add(lp, root: Path, pkg: SimpleNamespace, *extra: str, registry: Path | None = None) -> int:
    argv = (["--registry", str(registry)] if registry else []) + [
        "index", "add", pkg.name, "--index", str(root), "--repo-dir", str(pkg.repo),
        "--repo", pkg.url, "--license", "MIT", "--by", "maintainer", "--consent-recorded",
        "--headline", "Demo.foo_le_bar :: Demo/Main.lean :: A trivial demonstration theorem."]
    if pkg.lower_rel:
        argv += ["--lower", pkg.lower_rel]
    if pkg.comp_rel:
        argv += ["--comparator", pkg.comp_rel]
    return lp.main(argv + list(extra))
