#!/usr/bin/env python3
"""#62: the toolchain mechanism needs a Mathlib dependency. A project whose lake-manifest.json has none
is refused before stage 04 with a message saying so, unless COMPARATOR_RECEIPT_NO_MATHLIB=1 declares it
knowingly; then the vendored verify-comparator.sh skips `lake exe cache get` (it used to fail there with
`unknown executable cache`, reported as a rejected proof). A judge that fails before judging is reported
as a tool failure, never as "did not accept the solution"."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from .conftest import RECEIPT_CLI, subprocess_env
from .test_comparator_receipt import _lake_project, _stages, git, project, run_staged  # noqa: F401

REFUSAL = ("the toolchain mechanism needs a Mathlib dependency (lake-manifest.json has none); "
           "add it and run lake update")


def _exe(p: Path, body: str) -> None:
    p.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


def _fake_toolchain(root: Path) -> Path:
    """bin/ with a fake lake, lean (whose prefix bundles the five judging tools) and bwrap, for running the
    real vendored verify-comparator.sh. The fake lake logs each call to $FAKE_LAKE_CALLS and fails
    `lake exe cache get` as Lake does without a Mathlib: always (FAKE_NO_CACHE=1) or only once the build
    stage has run, i.e. inside the judge (FAKE_NO_CACHE=judge)."""
    bindir, prefix = root / "fakebin", root / "prefix"
    (prefix / "bin").mkdir(parents=True)
    bindir.mkdir()
    for tool in ("lake", "leanexport", "leanchecker", "nanoda_bin", "con-ron"):
        _exe(prefix / "bin" / tool, "exit 0\n")
    _exe(bindir / "lean", f'[ "${{1:-}}" = --print-prefix ] && echo "{prefix}"\nexit 0\n')
    _exe(bindir / "bwrap", "exit 0\n")
    _exe(bindir / "lake", """echo "lake $*" >> "$FAKE_LAKE_CALLS"
if [ "$*" = "exe cache get" ]; then
  if [ "${FAKE_NO_CACHE:-}" = 1 ] || { [ "${FAKE_NO_CACHE:-}" = judge ] && grep -q "^lake build" "$FAKE_LAKE_CALLS"; }; then
    echo "error: unknown executable cache" >&2; exit 1
  fi
fi
if [ "${1:-}" = comparator ]; then echo "Your solution is okay!"; fi
exit 0
""")
    return bindir


def run_vendored(project: Path, tmp: Path, extra: dict | None = None):
    """The runner with the real vendored verify-comparator.sh (no COMPARATOR_VERIFY) on a fake toolchain."""
    bindir = _fake_toolchain(tmp)
    calls = tmp / "lake-calls.txt"
    env = subprocess_env({"COMPARATOR_RECEIPT_ALLOW_HOST": "1", "COMPARATOR_FAKE_LAKE_OK": "1",
                          "COMPARATOR_LAKE": str(bindir / "lake"), "FAKE_LAKE_CALLS": str(calls),
                          "PATH": os.pathsep.join([str(bindir), os.environ.get("PATH", "")])})
    for k in ("SLURM_JOB_ID", "COMPARATOR_RECEIPT_ALLOWED_HOSTS", "COMPARATOR_VERIFY",
              "COMPARATOR_RECEIPT_NO_MATHLIB"):
        env.pop(k, None)
    env.update(extra or {})
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "toolchain"], cwd=project, env=env,
                       capture_output=True, text=True)
    return r, (calls.read_text().splitlines() if calls.exists() else [])


def _receipt_written(project: Path) -> bool:
    return any((project / ".lean-receipts").glob("*-comparator.json")) if (project / ".lean-receipts").is_dir() \
        else False


def test_no_mathlib_is_refused_before_anything_runs(project: Path):
    _lake_project(project, mathlib=False)
    r, calls = run_staged(project, extra_env={"COMPARATOR_RECEIPT_NO_MATHLIB": ""})
    assert r.returncode == 1
    assert REFUSAL in r.stderr and "COMPARATOR_RECEIPT_NO_MATHLIB=1" in r.stderr
    assert "nothing written" in r.stderr
    assert "did not accept" not in r.stderr
    assert calls == []                                       # no setup stage, no judge
    assert not _receipt_written(project)


def test_vendored_script_without_mathlib_is_refused_not_misjudged(project: Path, tmp_path: Path):
    _lake_project(project, mathlib=False)
    r, calls = run_vendored(project, tmp_path, {"FAKE_NO_CACHE": "1"})
    assert r.returncode == 1
    assert REFUSAL in r.stderr
    assert "did not accept" not in r.stderr and "unknown executable cache" not in r.stderr + r.stdout
    assert calls == []


def test_escape_hatch_skips_cache_get_in_the_vendored_script(project: Path, tmp_path: Path):
    _lake_project(project, mathlib=False)
    r, calls = run_vendored(project, tmp_path, {"FAKE_NO_CACHE": "1", "COMPARATOR_RECEIPT_NO_MATHLIB": "1"})
    assert r.returncode == 0, r.stderr
    assert "lake exe cache get" not in calls                 # neither stage 02 nor the judge ran it
    assert any(c.startswith("lake comparator --config") for c in calls)
    assert _stages(project)["stages"][1]["status"] == "skipped"
    assert _receipt_written(project)


def test_vendored_script_still_runs_cache_get_with_mathlib(project: Path, tmp_path: Path):
    _lake_project(project, mathlib=True)
    r, calls = run_vendored(project, tmp_path)
    assert r.returncode == 0, r.stderr
    # stage 02 once, then the judge's own call before `lake comparator` (upstream behaviour)
    assert calls.count("lake exe cache get") == 2
    assert calls[-1].startswith("lake comparator --config")


def test_judge_failing_before_judging_is_a_tool_failure(project: Path, tmp_path: Path):
    """The judge exits non-zero before `lake comparator` runs: not a verdict on the proof."""
    _lake_project(project, mathlib=True)
    r, _ = run_vendored(project, tmp_path, {"FAKE_NO_CACHE": "judge"})
    assert r.returncode == 1
    assert "the judge tool failed before judging" in r.stderr
    assert "unknown executable cache" in r.stderr             # the judge's last line is quoted
    assert "did not accept the solution" not in r.stderr
    assert not _receipt_written(project)


def test_judge_that_judged_and_rejected_still_says_did_not_accept(project: Path):
    _lake_project(project, mathlib=True)
    r, _ = run_staged(project, stub_mode="fail")
    assert r.returncode == 1
    assert "did not accept the solution" in r.stderr
    assert "failed before judging" not in r.stderr


def test_legacy_check_failing_before_judging_is_a_tool_failure(project: Path, tmp_path: Path):
    crash = tmp_path / "check-crash.sh"
    _exe(crash, 'echo "error: go is required to build the comparator" >&2\nexit 1\n')
    env = subprocess_env({"COMPARATOR_CHECK": str(crash), "COMPARATOR_RECEIPT_ALLOW_HOST": "1",
                          "COMPARATOR_FAKE_LAKE_OK": "0"})
    env.pop("SLURM_JOB_ID", None)
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "legacy"], cwd=project, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "comparator-check: the judge tool failed before judging" in r.stderr
    assert "go is required" in r.stderr
    assert "did not accept" not in r.stderr
