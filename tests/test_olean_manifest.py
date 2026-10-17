#!/usr/bin/env python3
"""comparator-receipt's olean manifest: the package's compiled modules the judge reads are hashed after the
setup stages, re-checked before and after the comparison, and bound into the receipt; a changed olean with
an unchanged HEAD is refused."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .conftest import RECEIPT_CLI, subprocess_env
from .test_comparator_receipt import (LAKE_STUB, STUB, STUB_TC, _ignored_lake, _receipt, _stages, cr,  # noqa: F401
                                      git, project, run_staged)

OLEANS = {"LAKE_STUB_OLEANS": "Challenge Solution"}
STAGES = Path(".lake") / "comparator-receipt" / "comparator-stages"


def _olean(project: Path, mod: str) -> Path:
    return project / ".lake" / "build" / "lib" / "lean" / f"{mod}.olean"


def _verify(project: Path, *flags: str) -> subprocess.CompletedProcess:
    env = subprocess_env({"COMPARATOR_FAKE_LAKE_OK": "1"})
    return subprocess.run([*RECEIPT_CLI, "--verify-oleans", *flags], cwd=project, env=env,
                          capture_output=True, text=True)


def test_manifest_lines_and_digest(cr, tmp_path: Path):
    p = tmp_path / "p"
    lib = p / ".lake" / "build" / "lib" / "lean"
    (lib / "A").mkdir(parents=True)
    (lib / "A" / "B.olean").write_bytes(b"bb")
    (lib / "C.olean").write_bytes(b"c")
    (lib / "C.olean.private").write_bytes(b"cp")
    (lib / "C.ilean").write_bytes(b"not consumed by the judge")
    (lib / "C.trace").write_bytes(b"{}")
    text = cr.olean_manifest_text(p)
    sha = hashlib.sha256
    assert text.decode().splitlines() == [
        f"lean/A/B.olean 2 {sha(b'bb').hexdigest()}",
        f"lean/C.olean 1 {sha(b'c').hexdigest()}",
        f"lean/C.olean.private 2 {sha(b'cp').hexdigest()}"]
    summary, again = cr.olean_manifest(p, "leanprover/lean4:v4.32.0", "build")
    assert again == text and summary["files"] == 3 and summary["sha256"] == sha(text).hexdigest()
    assert summary["lake_manifest_sha256"] is None and summary["captured"] == "build"
    assert cr.olean_manifest_text(tmp_path / "none") == b""        # no build tree: an empty manifest


def test_manifest_diff_names_the_files(cr):
    old = b"lean/A.olean 1 aa\nlean/B.olean 1 bb\n"
    new = b"lean/A.olean 1 ax\nlean/C.olean 1 cc\n"
    d = cr.olean_manifest_diff(old, new)
    assert "1 changed (lean/A.olean)" in d and "1 missing (lean/B.olean)" in d and "1 new (lean/C.olean)" in d


def test_slot_key_binds_the_olean_manifest():
    from lean_pkg.comparator_stage import Slot, slot_key
    s = Slot(1, "comparator.json", "foo")
    base = {"commit": "c" * 40, "config_hash": "h", "lean_toolchain": "t"}
    plain = slot_key(base, "toolchain", "j", s)
    assert "olean_manifest_sha256" not in plain                  # unchanged without a manifest
    a = slot_key({**base, "olean_manifest_sha256": "a" * 64}, "toolchain", "j", s)
    b = slot_key({**base, "olean_manifest_sha256": "b" * 64}, "toolchain", "j", s)
    assert a != b and a["olean_manifest_sha256"] == "a" * 64


def test_receipt_and_stages_record_the_manifest(project: Path):
    _ignored_lake(project)
    r, _ = run_staged(project, extra_env=OLEANS)
    assert r.returncode == 0, r.stderr
    om = _stages(project)["olean_manifest"]
    text = (project / STAGES / "03-build.oleans.sha256").read_bytes()
    assert om["files"] == 2 and om["sha256"] == hashlib.sha256(text).hexdigest()
    assert om["captured"] == "build" and om["schema"] == "olean-manifest/1"
    assert om["lake_manifest_sha256"] == hashlib.sha256((project / "lake-manifest.json").read_bytes()).hexdigest()
    assert _stages(project)["olean_manifest_sha256"] == om["sha256"]
    assert _receipt(project)["olean_manifest"] == om
    assert "olean manifest: 2 compiled module file(s)" in r.stderr
    assert str(project) not in text.decode()                     # paths under .lake/build/lib only


def test_a_changed_olean_after_prepare_is_refused(project: Path):
    _ignored_lake(project)
    r, _ = run_staged(project, "--prepare", extra_env=OLEANS)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    _olean(project, "Solution").write_text("tampered\n")        # HEAD, toolchain and configs unchanged
    assert git(project, "rev-parse", "HEAD") == head and git(project, "status", "--porcelain") == ""
    r, calls = run_staged(project)
    assert r.returncode == 1
    assert "compiled oleans changed since the setup was prepared" in r.stderr
    assert "1 changed (lean/Solution.olean)" in r.stderr
    assert not list(project.glob(".lean-receipts/*-comparator.json"))
    assert not any("comparator" in c for c in calls)              # no judge ran
    r, _ = run_staged(project, "--prepare")                      # nor is the state reported as prepared
    assert r.returncode == 1 and "already prepared" not in r.stdout


def test_negative_control_unchanged_oleans_after_prepare_pass(project: Path):
    _ignored_lake(project)
    r, _ = run_staged(project, "--prepare", extra_env=OLEANS)
    assert r.returncode == 0, r.stderr
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert "unchanged since then" in r.stderr
    assert _receipt(project)["olean_manifest"]["captured"] == "build"


def test_from_prepared_elsewhere_refuses_a_changed_olean(project: Path, tmp_path: Path):
    _ignored_lake(project)
    other = tmp_path / "elsewhere"
    r, _ = run_staged(project, "--prepare", "--evidence-dir", str(other), extra_env=OLEANS)
    assert r.returncode == 0, r.stderr
    _olean(project, "Challenge").unlink()
    r, _ = run_staged(project, "--from-prepared", str(other))
    assert r.returncode == 1 and "1 missing (lean/Challenge.olean)" in r.stderr
    assert not list(project.glob(".lean-receipts/*-comparator.json"))


def test_an_olean_changed_during_the_comparison_fails_the_stage(project: Path, tmp_path: Path):
    _ignored_lake(project)
    judge = tmp_path / "verify-wrapper.sh"
    judge.write_text(f'#!/usr/bin/env bash\necho rewritten > "{_olean(project, "Challenge")}"\n'
                     f'exec "{STUB_TC}" "$@"\n')
    judge.chmod(0o755)
    r, _ = run_staged(project, extra_env={**OLEANS, "COMPARATOR_VERIFY": str(judge)})
    assert r.returncode == 1
    assert "stage 04-comparator failed" in r.stderr and "changed during the comparison" in r.stderr
    assert _stages(project)["failed_stage"] == "comparator"
    assert not list(project.glob(".lean-receipts/*-comparator.json"))


def test_a_symlinked_olean_is_refused(project: Path, tmp_path: Path):
    _ignored_lake(project)
    real = tmp_path / "Solution.olean"
    real.write_text("elsewhere\n")
    link = _olean(project, "Solution")
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    r, _ = run_staged(project)
    assert r.returncode == 1 and "not regular files: lean/Solution.olean" in r.stderr
    assert not list(project.glob(".lean-receipts/*-comparator.json"))


def test_an_older_prepared_state_is_captured_at_the_comparison(project: Path):
    _ignored_lake(project)
    r, _ = run_staged(project, "--prepare", extra_env=OLEANS)
    assert r.returncode == 0, r.stderr
    sj = project / STAGES / "stages.json"
    data = json.loads(sj.read_text())
    for k in ("olean_manifest", "olean_manifest_sha256"):
        data.pop(k)
    sj.write_text(json.dumps(data))
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert _receipt(project)["olean_manifest"]["captured"].startswith("reuse")


def test_verify_oleans_read_only(project: Path):
    _ignored_lake(project)
    r, _ = run_staged(project, "--prepare", extra_env=OLEANS)
    assert r.returncode == 0, r.stderr
    before = (project / STAGES / "stages.json").read_bytes()
    v = _verify(project)
    assert v.returncode == 0 and "same as recorded: 2 file(s)" in v.stdout, v.stderr
    _olean(project, "Challenge").write_text("tampered\n")
    v = _verify(project, "--json")
    assert v.returncode == 1
    out = json.loads(v.stdout)
    assert out["ok"] is False and "lean/Challenge.olean" in out["problem"]
    assert (project / STAGES / "stages.json").read_bytes() == before      # wrote nothing
    git(project, "commit", "--allow-empty", "-m", "later")
    v = _verify(project, str(project / STAGES))
    assert v.returncode == 1 and "HEAD is" in v.stdout
    assert _verify(project, "--prepare").returncode == 2
    assert _verify(project, str(project / "nowhere")).returncode == 1
