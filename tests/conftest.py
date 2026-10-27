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
    for name in ("LEAN_PKG_REGISTRY", "LEAN_PKG_REGISTRY_SNAPSHOT", "LEAN_PKG_HOME", "LEAN_PKG_EVENTS", "LEAN_PKG_INDEX", "LEAN_PKG_NOTIFY_CMD", "LEAN_PKG_COMMIT", "LEAN_PKG_PUSH",
                 "LEAN_PKG_PUSH_RETRIES", "LEAN_PKG_GZIP_LIMIT",
                 "COMPARATOR_CHECK",
                 "COMPARATOR_RECEIPT_ALLOW_HOST", "COMPARATOR_RECEIPT_ALLOWED_HOSTS",
                 "SLURM_JOB_ID",
                 # lean-pkg's own CI runs on GitHub-hosted runners: the comparator's GitHub Actions context
                 # (proposal 0008, S5) is entered only by the tests that build it
                 "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT"):
        os.environ.pop(name, None)
    # The comparator host policy's workstation default depends on whether this machine has Slurm; tests that
    # want it enable it themselves (test_comparator_host_default.py).
    os.environ["COMPARATOR_RECEIPT_WORKSTATION_DEFAULT"] = "0"
    # A committed-mode push that fails transiently retries with backoff (#68); tests never sleep for it.
    os.environ["LEAN_PKG_PUSH_RETRY_DELAY"] = "0"


_scrub_git_local_env()

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CLI = [sys.executable, "-m", "lean_pkg.cli"]
RECEIPT_CLI = [sys.executable, "-m", "lean_pkg.comparator_receipt"]


def cli_reference_text() -> str:
    """The CLI reference as published: docs/cli.md (global flags, shared sections) followed by every
    generated page of docs/commands/ (per-command notes, flags, examples; scripts/command_docs.py)."""
    pages = [ROOT / "docs" / "cli.md", *sorted((ROOT / "docs" / "commands").glob("*.md"))]
    return "\n".join(p.read_text() for p in pages)


# No bytecode in the checkout (#310): when the session itself writes none under src/ (PYTHONPYCACHEPREFIX or
# PYTHONDONTWRITEBYTECODE, as `make test` and CI set), a __pycache__ that appears there came from a test's
# subprocess whose environment dropped the setting, and the run fails. Checked in the xdist controller only.
_PYCACHE_BEFORE = pytest.StashKey[set]()


def _src_pycache() -> set[Path]:
    return set(SRC.rglob("__pycache__"))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    if not hasattr(session.config, "workerinput"):
        session.config.stash[_PYCACHE_BEFORE] = _src_pycache()


def pytest_sessionfinish(session, exitstatus):
    before = session.config.stash.get(_PYCACHE_BEFORE, None)
    if before is None or not (sys.pycache_prefix or sys.dont_write_bytecode):
        return
    new = sorted(_src_pycache() - before)
    if new:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = (f"bytecode written into the checkout during the run (#310): {', '.join(str(p.relative_to(ROOT)) for p in new)}"
               "; a test's subprocess environment dropped PYTHONPYCACHEPREFIX / PYTHONDONTWRITEBYTECODE")
        if tr is not None:
            tr.ensure_newline()
            tr.write_line(msg, red=True)
        else:
            print(msg, file=sys.stderr)
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


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


# The modules cut out of cli.py that import core's names by name (#317 stage 3): each holds its own binding.
SPLIT_MODULES = ("lean_pkg.gate", "lean_pkg.audit", "lean_pkg.cli_parser")


def patch_core(monkeypatch, name: str, value) -> None:
    """Set NAME to VALUE on lean_pkg.core, on its lean_pkg.cli re-export, and on every module cut out of
    cli.py that imports it by name (gate.py, audit.py, cli_parser.py; #317). Code in each module reads its
    own globals, so a name used on several sides is patched on all of them; a patch on cli alone would miss
    every caller that moved to core.py, gate.py or audit.py."""
    monkeypatch.setattr(importlib.import_module("lean_pkg.core"), name, value)
    monkeypatch.setattr(load_module(), name, value)
    for mod in map(importlib.import_module, SPLIT_MODULES):
        if name in vars(mod):
            monkeypatch.setattr(mod, name, value)


@pytest.fixture(autouse=True)
def documented_event_lines(monkeypatch):
    """Every event line any test makes lean-pkg write must match the documented event-line schema
    (EVENT_KEYS, docs/formats.md "Event log"): an undocumented event name or key fails the test that
    wrote it, so the table cannot fall behind the code (1.0 command tree, section 6, item 2)."""
    cli = load_module()
    real = cli._event_line

    def checked(ts, name, event, **fields):
        line = real(ts, name, event, **fields)
        problems = cli.event_line_problems(json.loads(line))
        assert not problems, "undocumented event line: " + "; ".join(problems)
        return line
    patch_core(monkeypatch, "_event_line", checked)   # its callers (append_event, record) are in core.py


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True)


def make_repo(path: Path, toolchain: str = TOOLCHAIN, extra_files=None) -> Path:
    path.mkdir(parents=True)
    _git(path, "init")
    # Git >= 2.4x starts `git maintenance run --auto --detach` after each commit; a background repack
    # can delete .git/objects/XX while a test copies the repository (shutil.copytree, ENOENT; seen in
    # CI on PR #94). Off for fixture repositories only, never for the caller's own config.
    _git(path, "config", "maintenance.auto", "false")
    _git(path, "config", "gc.auto", "0")
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


FIXTURE_REPORT_SHA = "a" * 64      # make_receipt's placeholder; write_receipt replaces it with a sealed log's hash


def write_report_log(where: Path, receipt: dict, text: str = "fixture verify log\n") -> Path | None:
    """Write the redacted verify log a lower-tier receipt binds (<c12>-<job slug>-verify.redacted.log) into
    `where` and set the receipt's report_sha256 to its hash: `gate` refuses a receipt whose log is missing.
    None (nothing written) when the receipt has no usable commit or job_id."""
    import hashlib
    from lean_pkg.cli import Fail, report_log_name
    try:
        name = report_log_name(receipt)
    except (Fail, KeyError, TypeError):
        return None
    log = where / name
    log.write_text(text)
    receipt["report_sha256"] = hashlib.sha256(log.read_bytes()).hexdigest()
    return log


def put_receipt(path: Path, receipt: dict, indent: int | None = None, tail: str = "") -> Path:
    """Write a lower-tier receipt to `path` with its sealed log beside it (when it carries the placeholder
    report_sha256): the layout `receipt slurm` writes and `gate` copies into .lean-receipts/."""
    if receipt.get("report_sha256") == FIXTURE_REPORT_SHA:
        write_report_log(path.parent, receipt)
    path.write_text(json.dumps(receipt, indent=indent) + tail)
    return path


def write_receipt(tmp_path: Path, receipt: dict, name: str = "receipt.json", log: bool = True) -> Path:
    """Write the receipt; a lower-tier receipt still carrying the placeholder report_sha256 also gets its
    sealed log beside it (log=False: none, the missing-log refusal)."""
    if log and receipt.get("report_sha256") == FIXTURE_REPORT_SHA and "sorry_count" in receipt:
        write_report_log(tmp_path, receipt)
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
                          theorem_names=("BetaAudit.Demo.foo_le_bar",),
                          permitted_axioms=("propext", "Quot.sound",
                                            "Classical.choice"),
                          definition_names=(), judged_as: dict | None = None) -> str:
    cfg = {
        "challenge_module": "Challenge",
        "solution_module": "Solution",
        "theorem_names": list(theorem_names),
        "definition_names": list(definition_names),
        "permitted_axioms": list(permitted_axioms),
        "enable_nanoda": True,
    }
    if judged_as is not None:
        cfg["judged_as"] = judged_as
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
        "host": "h-1f2e3d4c5b6a",           # a host label: a receipt never names its host
        "host_label": "h-1f2e3d4c5b6a",
        "job_id": "local-h-1f2e3d4c5b6a-4242-20261005T120000Z",
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
    repo carries LICENSE + lake-manifest.json + a HEAD-bound gate receipt, and a claims.yaml whose one
    reusable theorem comparator.json covers, so its claims ledger is ready and promote can pass, #80)."""
    repo_a = make_repo(tmp_path / "alpha-repo")
    repo_b = make_repo(tmp_path / "beta-repo",
                       extra_files={"LICENSE": "MIT\n",
                                    "lake-manifest.json": "{}\n",
                                    "claims.yaml": CLAIMS_YAML,
                                    "Demo/Main.lean": DEMO_LEAN})
    cfg_b = add_comparator_config(repo_b)          # commits comparator.json
    receipt_b = make_receipt(repo_b)               # bound to the new HEAD
    dest = repo_b / ".lean-receipts" / "seed.json"
    dest.parent.mkdir(exist_ok=True)
    write_report_log(dest.parent, receipt_b)       # the sealed log the receipt binds, beside it
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


def publish_evidence(repo: Path, stage: bool = True) -> str:
    """Commit .lean-receipts/ (an evidence-only descendant of the bound commit) and push the
    branch to a local bare origin: the tracked, pushed state `promote` requires. Returns HEAD.
    stage=False only pushes what is already committed."""
    if stage and (repo / ".lean-receipts").is_dir():
        _git(repo, "add", "-f", ".lean-receipts")
        if _git(repo, "diff", "--cached", "--name-only").stdout.strip():
            _git(repo, "commit", "-q", "-m", "evidence")
    origin = repo.parent / f"{repo.name}-origin.git"
    if not origin.exists():
        subprocess.run(["git", "init", "-q", "--bare", str(origin)],
                       check=True, capture_output=True)
        _git(repo, "remote", "add", "origin", str(origin))
    branch = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    _git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    return repo_head(repo)


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
    patch_core(monkeypatch, "gh", fake)
    patch_core(monkeypatch, "find_reuse", lambda: None)
    return fake


@pytest.fixture()
def fake_notify(lp, monkeypatch) -> list[str]:
    calls: list[str] = []

    def _notify(msg):
        calls.append(msg)
        return "sent"
    patch_core(monkeypatch, "notify", _notify)
    return calls

# --- index fixtures (lean-pkg index) ---------------------------------------

@pytest.fixture()
def idx(lp):
    return importlib.import_module("lean_pkg.index")


MIT_TEXT = ("MIT License\n\nPermission is hereby granted, free of charge, to any person "
            "obtaining a copy of this software.\n")
INDEX_URL = "https://github.com/example/{name}"


def make_index_package(root: Path, name: str = "demo", *, lower: bool = True,
                       comparator: bool = True, extra_files: dict | None = None,
                       **comp_overrides) -> SimpleNamespace:
    """A package repo whose evidence is committed in an evidence-only descendant
    of the pin: pin = the commit the receipts bind; evidence = pin + .lean-receipts/."""
    repo = make_repo(root / f"{name}-repo", extra_files={
        "LICENSE": MIT_TEXT,
        "lake-manifest.json": MATHLIB_MANIFEST,
        "Demo/Main.lean": "theorem foo_le_bar : True := trivial\n", **(extra_files or {})})
    if comparator:
        add_comparator_config(repo, theorem_names=("foo_le_bar",))   # index judged_as: the headline
    pin = repo_head(repo)
    lower_rel = comp_rel = None
    if lower:
        lower_rel = f".lean-receipts/{pin[:12]}-slurm-12345.json"
        (repo / ".lean-receipts").mkdir(exist_ok=True)
        put_receipt(repo / lower_rel, make_receipt(repo), indent=2, tail="\n")
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
        "--allow-unpushed",
        "--headline", "Demo.foo_le_bar :: Demo/Main.lean :: A trivial demonstration theorem. :: foo_le_bar"]
    if pkg.lower_rel:
        argv += ["--lower", pkg.lower_rel]
    if pkg.comp_rel:
        argv += ["--comparator", pkg.comp_rel]
    return lp.main(argv + list(extra))


@pytest.fixture()
def git_url_map(monkeypatch):
    """Map a public https URL to a local repo for every git call (url.<path>.insteadOf),
    so `index check --clone` never touches the network."""
    pairs: list[tuple[str, str]] = []

    def add(url: str, path: Path) -> None:
        i = len(pairs)
        pairs.append((url, str(path)))
        monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(pairs)))
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", f"url.{path}.insteadOf")
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", url)
    return add

# --- claims fixtures (lean-pkg claims) --------------------------------------

@pytest.fixture()
def cl(lp):
    return importlib.import_module("lean_pkg.claims")


DEMO_LEAN = "namespace Demo\n\ntheorem foo_le_bar : True := trivial\n\nend Demo\n"   # claims say Demo.foo_le_bar
CLAIMS_YAML = """\
schema: claims/1
package: beta
claims:
- id: beta/foo-le-bar
  decl: Demo.foo_le_bar
  kind: theorem
  summary: A trivial demonstration bound used by the white-noise examples.
  aliases: [foo bound, trivial bound]
  keywords: [demo, inequality]
  file: Demo/Main.lean
  status: reusable
  superseded_by: null
  citation: Demo 2026, Theorem 1
  depends_on: []
"""


def drop_claims(repo: Path) -> str:
    """Remove the env fixture's claims.yaml in a new commit (for tests of a package without one)."""
    _git(repo, "rm", "-q", "claims.yaml")
    _git(repo, "commit", "-m", "no claims")
    return repo_head(repo)


def commit_claims(repo: Path, text: str = CLAIMS_YAML, rel: str = "claims.yaml",
                  lean: str = DEMO_LEAN) -> str:
    """Commit Demo/Main.lean and a claims file; return the new HEAD."""
    (repo / "Demo").mkdir(exist_ok=True)
    (repo / "Demo" / "Main.lean").write_text(lean)
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "claims")
    return repo_head(repo)


# --- search fixtures (lean-pkg search) --------------------------------------

@pytest.fixture()
def srch(lp):
    return importlib.import_module("lean_pkg.search")


@pytest.fixture()
def searchable(lp, env, tmp_path) -> SimpleNamespace:
    """beta's claims synced into the registry, and an index with one entry (demo)."""
    commit_claims(env.repo_b)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    pkg = make_index_package(tmp_path / "pkgs")
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    return SimpleNamespace(env=env, index=root, pkg=pkg)


def run_search(lp, s: SimpleNamespace, *argv: str) -> int:
    return lp.main(["--registry", str(s.env.registry), "search", *argv, "--index", str(s.index)])


# The autouse isolation fixtures below have public names on purpose: `from tests.conftest import *` (a
# scratch conftest) imports only names without a leading underscore, so an underscore name would silently
# drop the isolation and let that scratch suite write to the caller's real registry.
@pytest.fixture(autouse=True)
def no_user_default_registry(monkeypatch, tmp_path):
    """No test resolves the user's real ~/.lean-pkg/registry: LEAN_PKG_HOME points at an absent per-test
    directory (subprocesses inherit it through os.environ)."""
    monkeypatch.setenv("LEAN_PKG_HOME", str(tmp_path / "lean-pkg-home"))


@pytest.fixture(autouse=True)
def home_outside_tmp_path(monkeypatch, tmp_path):
    """local_path is stored $HOME-relative when it lies under $HOME (#17). Tests pass absolute paths
    under tmp_path and expect them back verbatim, which holds only when tmp_path is outside $HOME; on a
    host whose TMPDIR is under the real home it would not. HOME is an empty per-test directory (in core.py,
    where repo_path and normalize_local_path read it, and on its cli.py re-export)."""
    patch_core(monkeypatch, "HOME", tmp_path / "home")


STACK_DAY = __import__("datetime").date(2026, 11, 2)   # inside lts-2026.10's window


@pytest.fixture(autouse=True)
def stack_day_is_fixed(monkeypatch):
    """`new --kind paper` takes the current LTS stack, which depends on the date: every test sees the same day
    (STACK_DAY, when lts-2026.10 is current); a test of another day patches lean_pkg.stacks.today itself."""
    monkeypatch.setattr(importlib.import_module("lean_pkg.stacks"), "today", lambda: STACK_DAY)


@pytest.fixture(autouse=True)
def no_real_io_in_search_adapters(monkeypatch, tmp_path):
    """No test reaches the network or runs `gh` through lean_pkg.adapters, and no test
    reads the user's cache: every adapter test patches its module's fetch or runner."""
    adapters = importlib.import_module("lean_pkg.adapters")

    def refuse(*args, **kwargs):
        pytest.fail("a test reached real network or subprocess I/O through lean_pkg.adapters")
    monkeypatch.setattr(adapters, "urlopen", refuse)
    monkeypatch.setattr(adapters, "subprocess_run", refuse)
    monkeypatch.delenv("LEAN_PKG_RESERVOIR_INDEX", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
