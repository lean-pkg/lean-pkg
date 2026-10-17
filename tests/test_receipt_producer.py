#!/usr/bin/env python3
"""The producer fingerprint (producer.py): a `receipt local --run` records the running lean-pkg at run start
(its package sources, the interpreter, the entry point, its dist-info) and refuses at collection, naming the
files, when it changed during the run (field report: a shared editable checkout moved from 0.9.0 to 0.10.0
under a capped full run). Negative controls: an unchanged producer passes and is sealed into the receipt as
the additive key `producer`; a change outside the package directory, or in `__pycache__`, does not trigger.
The running package is a copy under tmp_path (core.package_dir is monkeypatched); no registry is read."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .test_receipt_systemd import (  # noqa: F401  (fixtures)
    RUNNING, _fixed_identity, fake_systemd, props_text, rdir, repo, run_live, assert_nothing_written)


@pytest.fixture()
def pkgcopy(lp, tmp_path, monkeypatch) -> Path:
    """A copy of the lean_pkg package inside a fake checkout (tmp/checkout/src/lean_pkg), used as the running
    package's directory."""
    real = lp.package_dir()
    dest = tmp_path / "checkout" / "src" / "lean_pkg"
    shutil.copytree(real, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (tmp_path / "checkout" / "README.md").write_text("unrelated\n")
    monkeypatch.setattr(lp, "package_dir", lambda: dest)
    return dest


# --------------------------------------------------------------------------
# the fingerprint itself

def test_unchanged_producer_has_no_changes(pkgcopy):
    from lean_pkg import producer as pr
    a = pr.fingerprint()
    assert a["sources"] and "receipt.py" in a["sources"] and "scripts/" not in a["sources"]
    assert pr.changes(a, pr.fingerprint()) == []


def test_a_changed_internal_source_is_named(pkgcopy):
    from lean_pkg import producer as pr
    a = pr.fingerprint()
    with open(pkgcopy / "receipt_check.py", "a") as f:
        f.write("# moved\n")
    (pkgcopy / "brand_new.py").write_text("x = 1\n")
    (pkgcopy / "palomar.py").unlink()
    assert pr.changes(a, pr.fingerprint()) == [
        "lean_pkg/brand_new.py (added)", "lean_pkg/palomar.py (removed)", "lean_pkg/receipt_check.py"]


def test_a_shipped_data_file_is_part_of_the_producer(pkgcopy):
    from lean_pkg import producer as pr
    script = next(p for p in (pkgcopy / "scripts").iterdir() if p.suffix == ".sh")
    a = pr.fingerprint()
    script.write_text(script.read_text() + "\n# edited\n")
    assert pr.changes(a, pr.fingerprint()) == [f"lean_pkg/scripts/{script.name}"]


def test_caches_and_files_outside_the_package_do_not_trigger(pkgcopy):
    from lean_pkg import producer as pr
    a = pr.fingerprint()
    (pkgcopy / "__pycache__").mkdir(exist_ok=True)
    (pkgcopy / "__pycache__" / "receipt.cpython-313.pyc").write_bytes(b"\0" * 8)
    (pkgcopy / "stale.pyc").write_bytes(b"\0")
    (pkgcopy.parent.parent / "README.md").write_text("moved on\n")           # same checkout, not the package
    (pkgcopy.parent.parent / "tests").mkdir()
    (pkgcopy.parent.parent / "tests" / "test_x.py").write_text("")
    assert pr.changes(a, pr.fingerprint()) == []


def test_sources_sha256_is_the_coreutils_recipe(pkgcopy):
    """The documented shell recipe (sha256sum listing, sorted bytewise, hashed) gives sources_sha256."""
    from lean_pkg import producer as pr
    if shutil.which("sha256sum") is None:
        pytest.skip("coreutils sha256sum not on PATH")
    cmd = ("find . -type f ! -path '*/__pycache__/*' ! -name '*.pyc' | sed 's|^\\./||' | LC_ALL=C sort | "
           "xargs -d '\\n' sha256sum | sha256sum")
    out = subprocess.run(["bash", "-c", cmd], cwd=pkgcopy, capture_output=True, text=True, check=True).stdout
    assert out.split()[0] == pr.sources_sha256(pr.fingerprint()["sources"])


def test_the_receipt_key_names_no_path(pkgcopy, tmp_path):
    from lean_pkg import producer as pr
    p = pr.receipt_producer(pr.fingerprint())
    assert set(p) == {"lean_pkg", "install", "commit", "dirty", "sources_sha256", "source_files", "python"}
    assert str(tmp_path) not in json.dumps(p)
    assert p["install"] == "installed" and p["commit"] is None            # the copy is not a git checkout


def test_install_probe_does_not_rewrite_the_checkout_index(lp, tmp_path):
    """--version / doctor / the producer key read an editable checkout with `git --no-optional-locks status`: a
    stat-stale .git/index (e.g. a copied checkout) is not rewritten by the probe (field report)."""
    root = tmp_path / "ed"
    (root / "lean_pkg").mkdir(parents=True)
    (root / "lean_pkg" / "__init__.py").write_text("")
    for a in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@example.com", "-c", "user.name=T",
                                               "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)
    f = root / "lean_pkg" / "__init__.py"
    st = f.stat()
    import os
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))               # stat info now differs from the index
    before = hashlib.sha256((root / ".git" / "index").read_bytes()).hexdigest()
    info = lp.install_detect(root / "lean_pkg")
    assert info["install"] == "editable" and info["dirty"] is False
    assert hashlib.sha256((root / ".git" / "index").read_bytes()).hexdigest() == before


# --------------------------------------------------------------------------
# receipt local --run

def _gate(lp, env, rp) -> int:
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)])


def test_unchanged_producer_passes_and_is_sealed(lp, env, repo, tmp_path, fake_systemd, pkgcopy, capsys):
    from lean_pkg import producer as pr
    rc, _d = run_live(lp, repo, tmp_path, "--json")
    out = capsys.readouterr()
    assert rc == 0, out.err
    payload = json.loads(out.out)
    rp = next(rdir(repo).glob("*.json"))
    rec = json.loads(rp.read_text())
    want = pr.sources_sha256(pr.fingerprint()["sources"])
    assert rec["producer"]["sources_sha256"] == want
    from lean_pkg import __version__
    assert rec["producer"]["lean_pkg"] == __version__
    assert payload["producer"]["changed"] == [] and payload["producer"]["sources_sha256"] == want
    assert any(c.startswith("✓ producer: lean-pkg") for c in payload["checks"])
    readme = payload["readme_block"]
    assert f"- Producer: lean-pkg {rec['producer']['lean_pkg']} (installed), sources SHA-256 `{want}`" in readme
    assert _gate(lp, env, rp) == 0                                          # gate ignores the additive key


def test_a_producer_changed_during_the_run_is_refused_and_named(lp, repo, tmp_path, fake_systemd, pkgcopy,
                                                                capsys):
    touch = f"open({str(pkgcopy / 'receipt_check.py')!r}, 'a').write('# 0.10.0\\n')"
    rc, _d = run_live(lp, repo, tmp_path, touch=touch)
    err = capsys.readouterr().err
    assert rc == 1
    assert "lean-pkg itself changed during the run: 1 file(s) of the running lean-pkg differ" in err
    assert "lean_pkg/receipt_check.py" in err and "Long runs: freeze the producer" in err
    assert_nothing_written(repo)


def test_a_change_outside_the_package_does_not_trigger(lp, repo, tmp_path, fake_systemd, pkgcopy, capsys):
    readme = pkgcopy.parent.parent / "README.md"
    rc, _d = run_live(lp, repo, tmp_path, touch=f"open({str(readme)!r}, 'a').write('pulled\\n')")
    assert rc == 0, capsys.readouterr().err
    assert "producer" in json.loads(next(rdir(repo).glob("*.json")).read_text())


def test_a_change_is_warned_while_the_unit_still_runs(lp, repo, tmp_path, fake_systemd, pkgcopy, capsys,
                                                      monkeypatch):
    """The early WARN: the first poll sees the unit running and lean-pkg already changed; the run is then
    refused at collection all the same."""
    from lean_pkg import receipt_systemd as rs
    props, _calls = fake_systemd
    props.write_text(RUNNING)
    monkeypatch.setattr(rs, "PRODUCER_CHECK_SECONDS", 0)
    sleeps = []

    def sleep(_s):
        sleeps.append(1)
        props.write_text(props_text(host=None))                           # finished after the first poll
    monkeypatch.setattr(rs.time, "sleep", sleep)
    touch = f"open({str(pkgcopy / 'cli.py')!r}, 'a').write('# pulled\\n')"
    rc, _d = run_live(lp, repo, tmp_path, touch=touch)
    err = capsys.readouterr().err
    assert rc == 1 and sleeps == [1]
    assert "WARN lean-pkg itself changed while lp-verify.service runs" in err
    assert "this run's receipt will be refused at collection" in err
    assert "lean-pkg itself changed during the run" in err
    assert_nothing_written(repo)


def test_receipts_without_run_carry_no_producer_key(lp, repo, tmp_path, capsys):
    """Only --run fingerprints: the other forms post-process evidence lean-pkg did not run (unchanged)."""
    from .test_receipt_systemd import Unit, run
    assert run(lp, repo, Unit(tmp_path)) == 0, capsys.readouterr().err
    assert "producer" not in json.loads(next(rdir(repo).glob("*.json")).read_text())
