#!/usr/bin/env python3
"""The project template a newcomer pastes (docs/getting-started.md, "Start from scratch") writes
module-headed Lean files, as Palomar requires of every tracked `.lean` file: `module` first,
`public import Mathlib`, the theorems inside `public section` ... `end` (lake comparator cannot export a
private theorem of a module). `palomar check` on a project made from it reports no source gap.

The build test runs the real toolchain when leanprover/lean4:v4.35.0-rc2 is installed (a Mathlib-free
projection of the template: `public import Mathlib` dropped, `ℕ` written `Nat`; a few seconds), and
`lake comparator` on it only with LEAN_EXERCISE_INTEGRATION=1 on a host whose toolchain mechanism
passes `comparator host-check`. The Mathlib template itself was built and judged by hand (see the PR)."""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from lean_pkg import cli
from lean_pkg import comparator_receipt as cr
from lean_pkg.palomar_check import has_module_header

from .conftest import ROOT
from .test_palomar_check import FORMALIZATION, MIT, codes, make_pkg, run

TC = "leanprover/lean4:v4.35.0-rc2"
TC_DIR = "leanprover--lean4---v4.35.0-rc2"
HEREDOC = re.compile(r"^cat > (\S+) <<'EOF'\n(.*?)^EOF\n", re.S | re.M)


def template() -> dict[str, str]:
    """The files the "Start from scratch" block writes, path -> text."""
    text = (ROOT / "docs" / "getting-started.md").read_text()
    block = text.split("#### Start from scratch", 1)[1].split("```bash\n", 1)[1].split("```", 1)[0]
    out = {path: body for path, body in HEREDOC.findall(block)}
    m = re.search(r"^echo '([^']+)' > lean-toolchain$", block, re.M)
    assert m, "the template no longer writes lean-toolchain with echo"
    out["lean-toolchain"] = m.group(1) + "\n"
    return out


def test_the_template_writes_module_headed_lean_files():
    files = template()
    lean = {p: t for p, t in files.items() if p.endswith(".lean")}
    assert sorted(lean) == ["MyProof/Challenge.lean", "MyProof/Solution.lean"]
    for path, text in lean.items():
        assert text.startswith("module\n"), path
        assert has_module_header(text), path
        assert "public import Mathlib\n" in text and "\nimport Mathlib" not in text, path
        assert "\npublic section\n" in text and text.rstrip().endswith("\nend"), path
    assert files["lean-toolchain"] == TC + "\n"


def _project(files: dict[str, str]) -> dict[str, str | None]:
    return {**files, "lake-manifest.json": "{}\n", "LICENSE": MIT, "formalization.yaml": FORMALIZATION}


def test_palomar_check_on_the_template_reports_no_source_gap(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, _project(template()))
    rc, data = run(lp, reg, capsys)
    assert data["lean_files"] == 2 and data["missing_module"] == 0
    assert not [c for c in codes(data) if c.startswith(("source.", "toolchain."))], data["gaps"]
    assert rc == 0 and data["counts"]["error"] == 0, data["gaps"]


def test_the_pre_module_template_would_be_refused(lp, tmp_path, capsys):
    """The control: the same project with the old headers (`import Mathlib`, no `module`) has the gap."""
    files = template()
    for p in ("MyProof/Challenge.lean", "MyProof/Solution.lean"):
        body = files[p].replace("module\n\npublic import Mathlib\n\npublic section\n\n", "import Mathlib\n\n")
        files[p] = body[:body.rindex("\nend\n")] + "\n"
        assert not has_module_header(files[p])
    rc, data = run(lp, reg := make_pkg(tmp_path, _project(files))[1], capsys)
    assert rc == 1 and codes(data)["source.module_required"]["paths"] == ["MyProof/Challenge.lean",
                                                                          "MyProof/Solution.lean"]


# ------------------------------------------------------------------------------- the real toolchain

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


def _core_projection(tmp_path):
    """The template without Mathlib: same headers, sections and theorem, core Lean only."""
    files = template()
    for p in ("MyProof/Challenge.lean", "MyProof/Solution.lean"):
        files[p] = files[p].replace("public import Mathlib\n\n", "").replace("ℕ", "Nat")
    files["lakefile.toml"] = 'name = "my-proof"\n\n[[lean_lib]]\nname = "MyProof"\nglobs = ["MyProof.+"]\n'
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return files


def _env(tc_bin, tmp_path):
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(tmp_path / "lake-cache")})
    return env


def _build(tc_bin, tmp_path):
    p = subprocess.run([str(tc_bin / "lake"), "build", "MyProof.Challenge", "MyProof.Solution"], cwd=tmp_path,
                       env=_env(tc_bin, tmp_path), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=600)
    return p, p.stdout + p.stderr


@pytest.mark.skipif(_toolchain_bin() is None, reason=f"{TC} is not installed")
@pytest.mark.timeout(900)   # the build's own limit is 600 s; the suite default (300 s) would cut it short
def test_the_template_headers_build_on_the_real_toolchain(tmp_path):
    tc_bin = _toolchain_bin()
    _core_projection(tmp_path)
    p, out = _build(tc_bin, tmp_path)
    assert p.returncode == 0 and "Build completed successfully" in out, out[-2000:]
    assert out.count("declaration uses `sorry`") == 1 and "Challenge.lean" in out


def _comparator_ready() -> str | None:
    if os.environ.get("LEAN_EXERCISE_INTEGRATION") != "1":
        return "set LEAN_EXERCISE_INTEGRATION=1 to run the real `lake comparator` on the template"
    if _toolchain_bin() is None or not (_toolchain_bin() / "nanoda_bin").exists():
        return f"{TC} is not installed"
    if not cr.host_checks()["mechanism_detail"]["toolchain"]["ok"]:
        return "comparator host-check: the toolchain mechanism fails here"
    return None


@pytest.mark.skipif(_comparator_ready() is not None, reason=str(_comparator_ready()))
@pytest.mark.timeout(1800)  # build (600 s) and lake comparator (900 s), each with its own limit
def test_lake_comparator_accepts_the_template_headers(tmp_path):
    tc_bin = _toolchain_bin()
    files = _core_projection(tmp_path)
    p, out = _build(tc_bin, tmp_path)         # lake comparator needs the lake-manifest.json a build writes
    assert p.returncode == 0, out[-2000:]
    cfg = json.loads(files["comparator.json"])
    cfg.pop("enable_nanoda")      # lake comparator: the kernels are named instead
    cfg["external_kernels"] = {"nanoda": [str(tc_bin / "nanoda_bin")], "con-ron": [str(tc_bin / "con-ron")]}
    (tmp_path / "run.json").write_text(json.dumps(cfg))
    p = subprocess.run([str(tc_bin / "lake"), "comparator", "--config", "run.json"], cwd=tmp_path,
                       env=_env(tc_bin, tmp_path), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=900)
    out = p.stdout + p.stderr
    assert p.returncode == 0 and "Your solution is okay!" in out.splitlines(), out[-2000:]
