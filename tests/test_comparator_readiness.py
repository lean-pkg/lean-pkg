#!/usr/bin/env python3
"""Comparator readiness before any expensive stage (field reports C1, C2).

C1: a configured challenge/solution module outside every [[lean_lib]]'s roots/globs is refused at the end of
stage 01 (after the dependency checkout, before the Mathlib cache and the build), naming the module and the
fix; a module that only needs a rebuild is not refused. C2: a .lake/packages symlink (or an entry in it)
resolving outside the project is refused before any setup stage, since the `lake comparator` sandbox cannot
read it; a `lake comparator` run that stops while resolving the workspace is reported as a tool/environment
error, never as a rejected proof. No Lean needed: fake lake and fake judges."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from .test_comparator_receipt import _lake_project, git, project, run_staged  # noqa: F401
from lean_pkg import comparator_receipt as cr


def _ignore_lake(project: Path) -> None:
    (project / ".gitignore").write_text(".lake/\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "ignore .lake")


def _probes(project: Path) -> list[str]:
    f = project.parent / "lake-probes.txt"
    return f.read_text().splitlines() if f.exists() else []


def _run(project: Path, *flags: str, **env):
    return run_staged(project, *flags, extra_env={"LAKE_STUB_PROBES": str(project.parent / "lake-probes.txt"),
                                                  **env})


# --- C1: Lake target readiness -----------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [("--prepare",), ()])
def test_unknown_target_is_refused_before_cache_and_build(project: Path, flags):
    _lake_project(project)
    r, calls = _run(project, *flags, LAKE_STUB_UNKNOWN="Challenge")
    assert r.returncode == 1
    assert "Lake target readiness: Challenge is not a Lake target" in r.stderr
    assert "[[lean_lib]]" in r.stderr and "nothing written" in r.stderr
    assert "stage 01-checkout failed" in r.stderr
    assert calls == ["lake env true"]                      # no cache-get, no build, no judge
    assert _probes(project) == ["lake --no-build build Challenge", "lake --no-build build Solution"]
    assert not (project / ".lean-receipts").exists()
    log = (project / ".lake/comparator-receipt/comparator-stages/01-checkout.log").read_text()
    assert "lake-target: Challenge: unknown (exit 1)" in log and "lake-target: Solution: ready (exit 0)" in log


def test_needs_rebuild_is_not_refused(project: Path):
    _lake_project(project)
    r, calls = _run(project, "--prepare", LAKE_STUB_STALE="Challenge Solution")
    assert r.returncode == 0, r.stderr
    assert "lake build Challenge Solution" in calls
    log = (project / ".lake/comparator-receipt/comparator-stages/01-checkout.log").read_text()
    assert "lake-target: Challenge: rebuild (exit 3)" in log


def test_classify_target_probe_on_lake_4_35_output():
    # Output recorded from `lake --no-build build M` on Lean v4.35.0-rc2 (toy project).
    assert cr.classify_target_probe("Toy.A", 3, "error: target is out-of-date and needs to be rebuilt") \
        == "rebuild"
    assert cr.classify_target_probe("Audit.Challenge", 1, "error: unknown target `Audit.Challenge`") == "unknown"
    assert cr.classify_target_probe("A.B", 1, "error: unknown module `A.B`") == "unknown"
    assert cr.classify_target_probe("Toy.Missing", 1, "error: no such file or directory (error code: 2)") \
        == "other"
    assert cr.classify_target_probe("Toy", 0, "") == "ready"
    assert cr.classify_target_probe("Toy", 1, "error: unknown target `Other`") == "other"


# --- C2: dependency symlinks outside the project ------------------------------------------------------------

def _outside(tmp: Path) -> Path:
    d = tmp / "other-checkout" / "packages"
    (d / "mathlib").mkdir(parents=True)
    return d


def test_packages_dir_linked_outside_is_refused_before_setup(project: Path):
    _lake_project(project)
    _ignore_lake(project)
    (project / ".lake").mkdir()
    (project / ".lake" / "packages").symlink_to(_outside(project.parent))
    r, calls = _run(project, "--prepare")
    assert r.returncode == 1
    assert "dependency symlink(s) resolving outside the project: .lake/packages -> " in r.stderr
    assert "copy the dependencies inside the project" in r.stderr and "nothing written" in r.stderr
    assert calls == [] and _probes(project) == []          # nothing ran


def test_package_entry_linked_outside_is_refused(project: Path):
    _lake_project(project)
    _ignore_lake(project)
    (project / ".lake" / "packages").mkdir(parents=True)
    (project / ".lake" / "packages" / "mathlib").symlink_to(_outside(project.parent) / "mathlib")
    r, calls = _run(project)
    assert r.returncode == 1
    assert ".lake/packages/mathlib -> " in r.stderr and calls == []


def test_symlink_inside_the_project_is_allowed(project: Path):
    _lake_project(project)
    _ignore_lake(project)
    real = project / ".lake" / "store" / "mathlib"
    real.mkdir(parents=True)
    (project / ".lake" / "packages").mkdir()
    (project / ".lake" / "packages" / "mathlib").symlink_to(real)
    r, _ = _run(project, "--prepare")
    assert r.returncode == 0, r.stderr


def _judge(tmp: Path, body: str) -> Path:
    p = tmp / "verify-judge.sh"
    p.write_text("#!/usr/bin/env bash\necho \"== lake comparator (toolchain) $1\"\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_sandbox_resolution_failure_is_a_tool_error_not_a_rejection(project: Path, tmp_path: Path):
    _lake_project(project)
    judge = _judge(tmp_path, 'echo "Resolving dependencies"\n'
                             'echo "error: .lake/packages: file exists (error code: 17, EEXIST)" >&2\n'
                             'echo "error: Child exited with 1" >&2\nexit 1\n')
    r, _ = _run(project, COMPARATOR_VERIFY=str(judge))
    assert r.returncode == 1
    assert "the judge tool failed before judging" in r.stderr
    assert "while resolving the workspace in its sandbox" in r.stderr
    assert "tool/environment error, not a verdict on the proof" in r.stderr
    assert "did not accept the solution" not in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_failure_after_building_starts_is_still_a_rejection(project: Path, tmp_path: Path):
    _lake_project(project)
    judge = _judge(tmp_path, 'echo "Resolving dependencies"\necho "Building Challenge"\n'
                             'echo "Exporting #[foo] from Challenge"\necho "error: mismatch" >&2\nexit 1\n')
    r, _ = _run(project, COMPARATOR_VERIFY=str(judge))
    assert r.returncode == 1
    assert "did not accept the solution" in r.stderr
    assert "failed before judging" not in r.stderr


def test_judged_predicate():
    mark = "== lake comparator (toolchain) comparator.json"
    assert not cr.judged(f"{mark}\nResolving dependencies\nerror: Child exited with 1\n", "toolchain", 1)
    assert not cr.judged(f"{mark}\nerror: could not read the configuration\n", "toolchain", 2)
    assert cr.judged(f"{mark}\nResolving dependencies\nBuilding Challenge\nerror: x\n", "toolchain", 1)
    assert cr.judged(f"{mark}\nerror: comparator rejected\n", "toolchain", 1)
    assert not cr.judged("error: no mark\n", "toolchain", 1)
    assert cr.judged("", "toolchain", 0)
