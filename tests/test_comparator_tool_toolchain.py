#!/usr/bin/env python3
"""comparator-check.sh builds its tools (comparator, lean4export) under each tool's own lean-toolchain, not
the project's. The runner (#41) exports ELAN_TOOLCHAIN=<the project's toolchain> to every child so that the
project's lake/lean resolve to it; elan's proxies let an exported ELAN_TOOLCHAIN override every directory's
lean-toolchain, so before the fix the pinned comparator (lean-toolchain v4.34.0-rc1) was built under a v4.32
project's Lean and failed (`Lean.Kernel.Environment.replay` does not exist). The script now unsets it for the
two tool builds only; the project's own steps (`lake env`, `lean --print-prefix`, the judge) keep it.

No Lean, network or Go/cargo build: fake git, go, cargo, lean and lake on PATH record the cwd and the
ELAN_TOOLCHAIN each call saw. jq is real (the script needs it)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import ROOT

SCRIPT = ROOT / "src" / "lean_pkg" / "scripts" / "comparator-check.sh"
PROJECT_TC = "leanprover/lean4:v4.32.0"
COMPARATOR_TC = "leanprover/lean4:v4.34.0-rc1"
COMPARATOR_COMMIT = "575674928e239f5bc452aab72d1dd7b0f1326494"
LEAN4EXPORT_COMMIT = "1" * 40
NANODA_COMMIT = "68d5ca9db226849b41a6fff59d796ff19d0a8840"
LANDRUN_COMMIT = "811cfff51ceaf3d9843708aa6d22e9b84ccac8b4"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


def fake_host(tmp_path: Path) -> tuple[dict, Path, Path, Path]:
    """(env, project, tools_dir, calls file). Tool checkouts are pre-made at their pinned commits (fake git
    answers rev-parse from .git/COMMIT), so checkout_at clones and fetches nothing."""
    calls = tmp_path / "calls.txt"
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    prefix = tmp_path / "toolchain-prefix"
    (prefix / "bin").mkdir(parents=True)
    _exe(bindir / "git", 'if [ "$1" = -C ] && [ "$3" = rev-parse ]; then cat "$2/.git/COMMIT"; exit 0; fi\n'
                         'echo "unexpected git $*" >&2; exit 1\n')
    _exe(bindir / "go", "exit 0\n")
    _exe(bindir / "cargo", 'for a; do case "$prev" in --manifest-path) d=$(dirname "$a");; esac; prev=$a; done\n'
                           'mkdir -p "$d/target/release" && printf "#!/bin/sh\\n" > "$d/target/release/nanoda_bin"'
                           ' && chmod +x "$d/target/release/nanoda_bin"\n')
    _exe(bindir / "lean", f'echo "lean ${{ELAN_TOOLCHAIN-UNSET}} $PWD $*" >> {calls}\n'
                          f'[ "$1" = --print-prefix ] && echo {prefix}\nexit 0\n')
    _exe(bindir / "lake", f'echo "lake ${{ELAN_TOOLCHAIN-UNSET}} $PWD $*" >> {calls}\n'
                          'case "$1" in\n'
                          '  build) mkdir -p .lake/build/bin && printf "#!/bin/sh\\n" > ".lake/build/bin/$2"'
                          ' && chmod +x ".lake/build/bin/$2";;\n'
                          '  env) shift; if [ "$1" = printenv ]; then echo; else echo "Your solution is okay!"; fi;;\n'
                          'esac\nexit 0\n')
    tools = tmp_path / "tools"
    for name, commit, tc in (("comparator", COMPARATOR_COMMIT, COMPARATOR_TC),
                             ("lean4export", LEAN4EXPORT_COMMIT, PROJECT_TC),
                             ("nanoda", NANODA_COMMIT, None)):
        (tools / name / ".git").mkdir(parents=True)
        (tools / name / ".git" / "COMMIT").write_text(commit + "\n")
        if tc:
            (tools / name / "lean-toolchain").write_text(tc + "\n")
    (tools / "bin").mkdir()
    _exe(tools / "bin" / "landrun", "exit 0\n")
    (tools / "bin" / "landrun.commit").write_text(LANDRUN_COMMIT + "\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "lean-toolchain").write_text(PROJECT_TC + "\n")
    (project / "lakefile.toml").write_text('name = "proj"\n\n[[lean_lib]]\nname = "Solution"\n')
    (project / "comparator.json").write_text(json.dumps(
        {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": ["foo"],
         "permitted_axioms": ["propext"], "enable_nanoda": True}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPARATOR_", "LEAN4EXPORT_", "ELAN"))}
    env.update({"PATH": f"{bindir}:{env.get('PATH', '')}", "TOOLS_DIR": str(tools),
                "LOG_DIR": str(tmp_path / "logs"), "LEAN4EXPORT_COMMIT": LEAN4EXPORT_COMMIT,
                "ELAN_TOOLCHAIN": PROJECT_TC})        # what the runner exports with elan (#41)
    return env, project, tools, calls


def rows(calls: Path) -> list[tuple[str, str, str, str]]:
    """(program, ELAN_TOOLCHAIN or UNSET, cwd, args) per recorded call."""
    out = []
    for line in calls.read_text().splitlines():
        prog, tc, cwd, *args = line.split(" ")
        out.append((prog, tc, cwd, " ".join(args)))
    return out


def test_tool_builds_drop_the_projects_elan_toolchain_project_steps_keep_it(tmp_path: Path):
    env, project, tools, calls = fake_host(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT)], cwd=project, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "All 1 comparator configuration(s) accepted." in r.stdout
    got = rows(calls)
    builds = {(cwd, args): tc for prog, tc, cwd, args in got if args.startswith("build ")}
    # each tool is built in its own checkout without the project's pin: elan then reads its lean-toolchain
    assert builds == {(str(tools / "comparator"), "build comparator"): "UNSET",
                      (str(tools / "lean4export"), "build lean4export"): "UNSET"}, got
    # the project's own steps keep the project's toolchain, exactly as #41 set it
    project_steps = [(prog, tc, args) for prog, tc, cwd, args in got if cwd == str(project)]
    assert ("lake", PROJECT_TC, "env printenv LEAN_PATH") in project_steps, got
    assert ("lean", PROJECT_TC, "--print-prefix") in project_steps, got
    judge = [tc for prog, tc, args in project_steps if prog == "lake" and args.startswith("env ") and
             "comparator" in args and "printenv" not in args]
    assert judge == [PROJECT_TC], got
    # the log names the toolchain each tool was built under; the pins the receipt parses are unchanged
    assert f"== comparator toolchain: {COMPARATOR_TC} (its own lean-toolchain)" in r.stdout
    assert f"== lean4export toolchain: {PROJECT_TC} (its own lean-toolchain)" in r.stdout
    assert f"== comparator @ {COMPARATOR_COMMIT}" in r.stdout


def test_mismatch_override_still_builds_lean4export_under_the_project_toolchain(tmp_path: Path):
    """LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH=1 rewrites the dedicated checkout's lean-toolchain to the
    project's, so unsetting ELAN_TOOLCHAIN does not change what that build uses."""
    env, project, tools, calls = fake_host(tmp_path)
    (tools / "lean4export" / "lean-toolchain").write_text("leanprover/lean4:v4.31.0\n")
    override = tools / f"lean4export-override-{PROJECT_TC.split(':v')[1]}"
    (override / ".git").mkdir(parents=True)
    (override / ".git" / "COMMIT").write_text(LEAN4EXPORT_COMMIT + "\n")
    (override / "lean-toolchain").write_text("leanprover/lean4:v4.31.0\n")
    env["LEAN4EXPORT_ALLOW_TOOLCHAIN_MISMATCH"] = "1"
    r = subprocess.run(["bash", str(SCRIPT)], cwd=project, env=env, capture_output=True, text=True, timeout=60)
    # the fake binary prints no version, so the post-build version check refuses: the build itself is the point
    assert r.returncode == 1 and "reports Lean version" in r.stderr, r.stdout + r.stderr
    assert (override / "lean-toolchain").read_text().strip() == PROJECT_TC
    assert f"== lean4export toolchain: {PROJECT_TC} (its own lean-toolchain)" in r.stdout
    builds = [(cwd, tc) for prog, tc, cwd, args in rows(calls) if args == "build lean4export"]
    assert builds == [(str(override), "UNSET")]
