#!/usr/bin/env python3
"""The docs-only classifier preflight documented in docs/receipts.md ("Before opting in: inventory every
file the checker reads") runs as written and reports what the docs-only rule would decide.

The snippet calls lean_pkg.cli.docs_build_input, lean_pkg.cli.git_show_bytes and
lean_pkg.build_scope.lake_scope; this test fails when one of them changes in a way that breaks it."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADING = "#### Before opting in: inventory every file the checker reads"


def snippet() -> str:
    text = (ROOT / "docs" / "receipts.md").read_text(encoding="utf-8")
    section = text.split(HEADING, 1)[1].split("\n### ", 1)[0]
    blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
    assert blocks, "the preflight block is missing from docs/receipts.md"
    return blocks[0]


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


def package(tmp_path, lakefile: str, name="lakefile.toml") -> pathlib.Path:
    """A package in a subdirectory of its repository, as the snippet runs from the package directory."""
    pkg = tmp_path / "repo" / "pkg"
    files = {name: lakefile, "Foo.lean": "-- foo\n", "Foo/Bar.lean": "-- bar\n",
             "tests/EscapeA.lean": "-- runtime-compiled\n", "EXPECTED-PRODUCTION-OWNERS.txt": "Foo.x\n",
             "README.md": "notes\n", "lean-toolchain": "leanprover/lean4:v4.35.0\n"}
    for rel, body in files.items():
        f = pkg / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    git(tmp_path / "repo", "init", "-q")
    git(tmp_path / "repo", "add", "-A")
    git(tmp_path / "repo", "commit", "-qm", "init")
    return pkg


def run(pkg: pathlib.Path, script: str) -> tuple[dict[str, str], str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PATH"] = str(pathlib.Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    p = subprocess.run(["bash", "-c", script.replace("python3 -c", f"'{sys.executable}' -c", 1)],
                       cwd=pkg, capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    out = {}
    for line in p.stdout.splitlines():
        reason, path = line.split("\t")
        out[path] = reason
    return out, p.stderr


def test_documented_globs_protect_the_inventory(tmp_path):
    pkg = package(tmp_path, 'name = "foo"\n\n[[lean_lib]]\nname = "Foo"\n')
    out, _ = run(pkg, snippet())
    assert out["tests/EscapeA.lean"] == "build_inputs tests/Escape*.lean"
    assert out["EXPECTED-PRODUCTION-OWNERS.txt"] == "build_inputs EXPECTED-PRODUCTION-OWNERS.txt"
    assert out["Foo/Bar.lean"] == "lean_lib Foo"
    assert out["lakefile.toml"] == "lakefile.*"
    assert out["lean-toolchain"] == "lean-toolchain"
    assert out["README.md"] == "NOT A BUILD INPUT"


def test_without_the_globs_the_runtime_inputs_are_unprotected(tmp_path):
    pkg = package(tmp_path, 'name = "foo"\n\n[[lean_lib]]\nname = "Foo"\n')
    script = snippet()
    without = script.rsplit("' 'tests/Escape*.lean' 'EXPECTED-PRODUCTION-OWNERS.txt'", 1)[0] + "'\n"
    assert without != script
    out, _ = run(pkg, without)
    assert out["tests/EscapeA.lean"] == "NOT A BUILD INPUT"
    assert out["EXPECTED-PRODUCTION-OWNERS.txt"] == "NOT A BUILD INPUT"
    assert out["Foo.lean"] == "lean_lib Foo"


def test_a_lakefile_lean_makes_every_lean_file_a_build_input(tmp_path):
    pkg = package(tmp_path, "-- lakefile code\n", name="lakefile.lean")
    out, err = run(pkg, snippet())
    assert out["tests/EscapeA.lean"] == "*.lean"
    assert out["Foo/Bar.lean"] == "*.lean"
    assert "every .lean file counts" in err
