#!/usr/bin/env python3
"""comparator-receipt (#397): a refusal because Lake reported a statement or constant mismatch ends with one
`next: lean-pkg comparator check NAME --contracts-only` line (+ `--repo DIR` off the registered checkout), and
stages.json (the run's JSON record) and a kept attempt's `--attempts --json` row carry it as next_command.
Display only: the verdict, the exit code and what is written are unchanged; other failures get no such line."""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from .conftest import RECEIPT_CLI, subprocess_env
from .test_comparator_receipt import _lake_project, _stages, cr, git, project, run_staged  # noqa: F401

STATEMENT = "error: Challenge and solution theorem statement do not match: 'foo'"
CONST = "error: Const does not match between challenge and target 'Fix.helper'"


def _registry(tmp: Path, name: str, local_path: Path | str, repo: str = "") -> Path:
    reg = tmp / "registry" / "lean-packages.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    pkg = {"local_path": str(local_path), "status": "scratch"}
    if repo:
        pkg["repo"] = repo
    reg.write_text(json.dumps({"version": 3, "packages": {name: pkg}}, indent=2) + "\n")   # JSON is YAML
    return reg


def _next_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if ln.startswith("next: ")]


@pytest.mark.parametrize("message", [STATEMENT, CONST])
def test_mismatch_refusal_points_to_contracts_only(project: Path, tmp_path: Path, message: str):
    _lake_project(project)
    reg = _registry(tmp_path, "my-proof", project)
    r, _calls = run_staged(project, stub_mode="fail",
                           extra_env={"STUB_FAIL_MESSAGE": message, "LEAN_PKG_REGISTRY": str(reg)})
    assert r.returncode == 1
    assert "did not accept the solution" in r.stderr          # the verdict is unchanged
    assert not (project / ".lean-receipts").exists()            # nothing written
    want = "lean-pkg comparator check my-proof --contracts-only"
    assert _next_lines(r.stderr) == [f"next: {want}"]
    assert r.stderr.rstrip().splitlines()[-1] == f"next: {want}"   # the output ends with it
    st = _stages(project)
    assert st["failed_stage"] == "comparator" and st["next_command"] == want


def test_mismatch_off_the_registered_checkout_adds_repo(project: Path, tmp_path: Path):
    """A checkout other than the registered one (a worktree, another clone), matched by its git remote."""
    _lake_project(project)
    url = "https://github.com/example/my-proof.git"
    git(project, "remote", "add", "origin", url)
    reg = _registry(tmp_path, "my-proof", tmp_path / "elsewhere", repo="https://github.com/example/my-proof")
    r, _ = run_staged(project, stub_mode="fail",
                      extra_env={"STUB_FAIL_MESSAGE": STATEMENT, "LEAN_PKG_REGISTRY": str(reg)})
    assert r.returncode == 1
    want = f"lean-pkg comparator check my-proof --contracts-only --repo {shlex.quote(str(project.resolve()))}"
    assert _next_lines(r.stderr) == [f"next: {want}"]
    assert _stages(project)["next_command"] == want


def test_mismatch_with_no_registered_package_names_the_docs(project: Path, tmp_path: Path):
    _lake_project(project)
    r, _ = run_staged(project, stub_mode="fail",
                      extra_env={"STUB_FAIL_MESSAGE": CONST,
                                 "LEAN_PKG_REGISTRY": str(tmp_path / "no-such-registry.yaml")})
    assert r.returncode == 1
    want = f"lean-pkg comparator check NAME --contracts-only --repo {shlex.quote(str(project.resolve()))}"
    (line,) = _next_lines(r.stderr)
    assert line.startswith(f"next: {want}  (NAME: ") and "docs/comparator.md#a-contracts-only-diagnostic" in line
    assert _stages(project)["next_command"] == want


def test_other_failures_get_no_next_line(project: Path, tmp_path: Path):
    _lake_project(project)
    reg = _registry(tmp_path, "my-proof", project)
    for message in ("error: Illegal axiom detected: 'sorryAx'", "error: build failed"):
        r, _ = run_staged(project, stub_mode="fail",
                          extra_env={"STUB_FAIL_MESSAGE": message, "LEAN_PKG_REGISTRY": str(reg)})
        assert r.returncode == 1, message
        assert _next_lines(r.stderr) == [], message
        assert _stages(project)["next_command"] is None


def test_a_passing_run_gets_no_next_command(project: Path, tmp_path: Path):
    _lake_project(project)
    reg = _registry(tmp_path, "my-proof", project)
    r, _ = run_staged(project, extra_env={"LEAN_PKG_REGISTRY": str(reg)})
    assert r.returncode == 0, r.stderr
    assert "--contracts-only" not in r.stdout + r.stderr
    assert _stages(project)["next_command"] is None


def test_several_failing_units_print_it_once(project: Path, tmp_path: Path):
    cfg = json.loads((project / "comparator.json").read_text())
    cfg["theorem_names"] = ["foo", "bar", "baz"]
    (project / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    _lake_project(project)
    reg = _registry(tmp_path, "my-proof", project)
    r, _ = run_staged(project, "--unit-size", "1", "--judges", "3", stub_mode="fail",
                      extra_env={"STUB_FAIL_MESSAGE": STATEMENT, "LEAN_PKG_REGISTRY": str(reg)})
    assert r.returncode == 1
    assert _next_lines(r.stderr) == ["next: lean-pkg comparator check my-proof --contracts-only"]


def test_a_kept_attempt_lists_its_next_command(project: Path, tmp_path: Path):
    _lake_project(project)
    reg = _registry(tmp_path, "my-proof", project)
    env = {"STUB_FAIL_MESSAGE": STATEMENT, "LEAN_PKG_REGISTRY": str(reg)}
    run_staged(project, stub_mode="fail", extra_env=env)
    run_staged(project, stub_mode="fail", extra_env=env)   # keeps the first run as attempts/001/
    p = subprocess.run([*RECEIPT_CLI, "--attempts", "--json"], cwd=project, capture_output=True, text=True,
                       env=subprocess_env({"COMPARATOR_FAKE_LAKE_OK": "1"}))
    assert p.returncode == 0, p.stderr
    rows = json.loads(p.stdout)["attempts"]
    assert rows and rows[0]["next_command"] == "lean-pkg comparator check my-proof --contracts-only"


def test_contract_mismatch_matches_only_lakes_two_messages(cr):
    assert cr.contract_mismatch(f"x\n{STATEMENT}\n") == STATEMENT
    assert cr.contract_mismatch(CONST) == CONST
    for other in ("error: Illegal axiom detected: 'sorryAx'", "Solution constant is not a theorem 'foo'",
                  "error: Constant foo not found in solution", "error: build failed", ""):
        assert cr.contract_mismatch(other) is None, other
