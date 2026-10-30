"""`receipt slurm --contracts-preflight` (LO-20261030-04): before a job is submitted, the contracts-only diagnostic
of every comparator configuration of the checkout. A configuration that differs, one whose modules are not built,
and a diagnostic that cannot run all refuse (exit 1); nothing is written. The fabricated reports below stand in for
`comparator_contracts.run`; the last test runs the real diagnostic on a tiny Lean project when the toolchain is
installed (receipt systemd --run --contracts-preflight is tested in test_receipt_systemd.py)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_pkg import comparator_contracts as cc
from lean_pkg import comparator_receipt as cr

from .conftest import _git

TC = "leanprover/lean4:v4.35.0"
TC_DIR = "leanprover--lean4---v4.35.0"


def _report(*verdicts):
    return {"ok": all(v == "match" for v, _ in verdicts), "acceptance": False, "evidence": False,
            "configs": [{"config": f"comparator/config-{i}.json", "verdict": v, "detail": d, "hint": None}
                        for i, (v, d) in enumerate(verdicts)]}


def _tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(d).parts and ".lake" not in p.relative_to(d).parts}


@pytest.fixture
def checkout(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    _git(d, "init", "-q")
    (d / "comparator").mkdir()
    (d / "comparator" / "config-a.json").write_text("{}\n")
    return d


def preflight(lp, d: Path, *extra) -> int:
    return lp.main(["receipt", "slurm", "--contracts-preflight", "--repo", str(d), *extra])


def test_every_configuration_matching_passes_and_writes_nothing(lp, checkout, monkeypatch, capsys):
    monkeypatch.setattr(cc, "run", lambda repo, *a, **k: _report(("match", None), ("match", None)))
    before = _tree(checkout)
    assert preflight(lp, checkout, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["written"] == [] and data["refusals"] == []
    assert data["checks"] == ["✓ contracts preflight: 2 comparator configuration(s) match (never acceptance: only "
                              "lake comparator in a gate judges)"]
    assert _tree(checkout) == before


@pytest.mark.parametrize("report, needle", [
    (_report(("match", None), ("differs", "Const does not match between challenge and target 'Heat.f'")),
     "comparator/config-1.json: differs: Const does not match between challenge and target 'Heat.f'"),
    (_report(("error", "module Challenge is not built: run `lake build Challenge` first")),
     "comparator/config-0.json: error: module Challenge is not built: run `lake build Challenge` first"),
])
def test_a_configuration_that_does_not_match_refuses(lp, checkout, monkeypatch, capsys, report, needle):
    monkeypatch.setattr(cc, "run", lambda repo, *a, **k: report)
    before = _tree(checkout)
    assert preflight(lp, checkout) == 1
    cap = capsys.readouterr()
    assert needle in cap.err and "nothing was run" in cap.err
    assert "contracts preflight REFUSED (never acceptance; nothing written)" in cap.out
    assert _tree(checkout) == before


def test_a_diagnostic_that_cannot_run_refuses(lp, checkout, monkeypatch, capsys):
    def boom(repo, *a, **k):
        raise cc.ContractsError("the toolchain at x has no leanexport (Lean v4.35.0 or later ships it)")
    monkeypatch.setattr(cc, "run", boom)
    assert preflight(lp, checkout, "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["report"] is None and data["written"] == []
    assert data["refusals"] == ["--contracts-preflight: the contracts-only diagnostic could not run (the toolchain "
                                "at x has no leanexport (Lean v4.35.0 or later ships it)); nothing was run"]


def test_the_current_directory_is_the_default_checkout(lp, checkout, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cc, "run", lambda repo, *a, **k: seen.append(Path(repo)) or _report(("match", None)))
    monkeypatch.chdir(checkout)
    assert lp.main(["receipt", "slurm", "--contracts-preflight"]) == 0
    assert seen == [checkout.resolve()]


@pytest.mark.parametrize("extra, needle", [
    (("--check",), "--check and --contracts-preflight do not combine"),
    (("--template",), "--template and --contracts-preflight do not combine"),
    (("--preflight-deps", "--deps-dir", "x"), "--preflight-deps and --contracts-preflight do not combine"),
    (("--job", "7"), "--job: not with --contracts-preflight (it runs before the job exists)"),
])
def test_usage_errors(lp, checkout, monkeypatch, capsys, extra, needle):
    monkeypatch.setattr(cc, "run", lambda *a, **k: pytest.fail("the diagnostic must not run on a usage error"))
    assert preflight(lp, checkout, *extra) == 2
    assert needle in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------
# Real Lean: the reported shape, a Challenge statement keeping a `let` the Solution's statement unfolds

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if all((b / x).exists() for x in ("lake", "lean", "leanexport")) else None


@pytest.mark.timeout(1800)  # real Lean: two small builds and three diagnostics
def test_real_unbuilt_then_let_mismatch_then_fixed(lp, tmp_path, capsys):
    tc_bin = _toolchain_bin()
    if tc_bin is None:
        pytest.skip(f"{TC} is not installed")
    if shutil.which("bwrap") is None:
        pytest.skip("bwrap is not installed (the preflight exports only in its sandbox)")
    d = tmp_path / "pkg"
    (d / "Challenge").mkdir(parents=True)
    (d / "comparator").mkdir()
    (d / "lean-toolchain").write_text(TC + "\n")
    (d / "lakefile.toml").write_text('name = "fix"\ndefaultTargets = ["Challenge", "Solution"]\n\n'
                                     '[[lean_lib]]\nname = "Challenge"\nglobs = ["Challenge.+"]\n\n'
                                     '[[lean_lib]]\nname = "Solution"\n')
    (d / "Challenge" / "Let.lean").write_text(
        "theorem audit_let (p : Nat) : let q := p + 1; q * q = q * q := sorry\n")
    (d / "Solution.lean").write_text(
        "theorem audit_let (p : Nat) : (p + 1) * (p + 1) = (p + 1) * (p + 1) := rfl\n")
    (d / "comparator" / "config-let.json").write_text(json.dumps(
        {"challenge_module": "Challenge.Let", "solution_module": "Solution", "theorem_names": ["audit_let"],
         "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"]}))
    _git(d, "init", "-q")

    def build():
        p = subprocess.run([str(tc_bin / "lake"), "build"], cwd=d, capture_output=True, text=True, timeout=900)
        assert p.returncode == 0, (p.stdout + p.stderr)[-2000:]

    before = _tree(d)
    assert preflight(lp, d) == 1                                     # 1. not built
    assert _tree(d) == before
    cap = capsys.readouterr()
    assert "error: module Challenge.Let, Solution is not built: run `lake build Challenge.Let Solution` first" \
        in cap.err, cap.err
    build()
    before = _tree(d)                                                 # after Lake wrote lake-manifest.json
    assert preflight(lp, d) == 1                                     # 2. built: the `let` mismatch
    cap = capsys.readouterr()
    assert "differs: Challenge and solution theorem statement do not match: 'audit_let'" in cap.err
    assert "(hint: the Challenge keeps a `let` in its type" in cap.err
    assert _tree(d) == before                                         # nothing written in the checkout
    (d / "Challenge" / "Let.lean").write_text(                        # 3. the alias inlined: a match
        "theorem audit_let (p : Nat) : (p + 1) * (p + 1) = (p + 1) * (p + 1) := sorry\n")
    build()
    assert preflight(lp, d) == 0
    assert "contracts preflight PASS" in capsys.readouterr().out
