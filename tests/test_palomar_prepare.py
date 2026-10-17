#!/usr/bin/env python3
"""#22.3: `palomar prepare NAME` builds a submission bundle (no network): formalization.yaml, comparator
configs, receipts bound to HEAD, README.txt, MANIFEST.sha256; event palomar-prepared; refusals."""

from __future__ import annotations

import hashlib
import importlib
import json

import pytest
import yaml

from .conftest import (_git, make_comparator_receipt, make_receipt, read_events, read_reg, repo_head,
                       write_reg)
from .test_palomar_check import files, make_pkg


def cli(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def with_consent(env):
    reg = read_reg(env)
    reg["packages"]["beta"]["consent"] = [{"who": "Formalizer One", "role": "owner",
                                           "pointer": "consent/one.md", "recorded_at": "2026-10-09T00:00:00+00:00"}]
    reg["packages"]["beta"]["license_spdx"] = "MIT"
    write_reg(env, reg)


@pytest.fixture()
def assume_ready(monkeypatch):
    """beta (conftest) pins Lean v4.32.0 and has no module headers, so `palomar check` finds error gaps;
    the bundle tests below take its readiness as given. The readiness refusal is tested on beta as it is,
    and on gamma (tests/test_palomar_check.py), which is ready for real."""
    pc = importlib.import_module("lean_pkg.palomar_check")
    real = pc.check

    def ready(pkg, repo, commit, policy, source, config=None):
        rep = real(pkg, repo, commit, policy, source, config)
        gaps = [g for g in rep["gaps"] if g["severity"] != "error"]
        return {**rep, "ready": True, "gaps": gaps, "counts": {"error": 0, "warn": len(gaps)}}
    monkeypatch.setattr(pc, "check", ready)


@pytest.fixture(autouse=True)
def no_network(lp, monkeypatch):
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("palomar prepare must not touch the network")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(lp, "gh", refuse)


def test_prepare_writes_the_bundle_and_records_the_event(lp, env, capsys, assume_ready):
    with_consent(env)
    head = repo_head(env.repo_b)
    assert cli(lp, env, "palomar", "prepare", "beta", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    bundle = env.repo_b / ".lean-receipts" / f"palomar-{head[:12]}"
    assert payload["bundle"] == f".lean-receipts/palomar-{head[:12]}" and payload["commit"] == head
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())
    assert files == sorted(["formalization.yaml", "configs/comparator.json", "receipts/seed.json",
                            f"receipts/{head[:12]}-comparator.json", "README.txt", "MANIFEST.sha256"])
    assert (bundle / "configs/comparator.json").read_bytes() == (env.repo_b / "comparator.json").read_bytes()
    manifest = (bundle / "MANIFEST.sha256").read_text()
    for line in manifest.splitlines():
        digest, rel = line.split("  ", 1)
        assert hashlib.sha256((bundle / rel).read_bytes()).hexdigest() == digest
    assert payload["bundle_sha256"] == hashlib.sha256(manifest.encode()).hexdigest()
    readme = (bundle / "README.txt").read_text()
    assert "https://palomar-registry.org/" in readme and f"palomar-submitted --field commit={head}" in readme
    ev = read_events(env)[-1]
    assert ev["event"] == "palomar-prepared" and ev["bundle_sha256"] == payload["bundle_sha256"]
    assert read_reg(env)["packages"]["beta"]["history"][-1]["event"] == "palomar-prepared"
    # never overwritten
    assert cli(lp, env, "palomar", "prepare", "beta") == 1
    assert "never overwritten" in capsys.readouterr().err


def test_dry_run_writes_nothing(lp, env, capsys, assume_ready):
    with_consent(env)
    before = env.registry.read_text()
    assert cli(lp, env, "palomar", "prepare", "beta", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and "MANIFEST.sha256" in out
    assert not list((env.repo_b / ".lean-receipts").glob("palomar-*"))
    assert env.registry.read_text() == before and read_events(env) == []


def test_out_dir(lp, env, tmp_path, capsys, assume_ready):
    with_consent(env)
    out = tmp_path / "bundle-out"
    assert cli(lp, env, "palomar", "prepare", "beta", "--out", str(out)) == 0
    assert (out / "MANIFEST.sha256").is_file()


def test_refuses_without_consent(lp, env, capsys):
    assert cli(lp, env, "palomar", "prepare", "beta") == 1
    assert "no consent recorded" in capsys.readouterr().err
    assert not list((env.repo_b / ".lean-receipts").glob("palomar-*")) and read_events(env) == []


def test_refuses_without_receipts(lp, env, capsys):
    with_consent(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = None
    write_reg(env, reg)
    assert cli(lp, env, "palomar", "prepare", "beta") == 1
    assert "no comparator receipt recorded" in capsys.readouterr().err


def test_refuses_receipts_not_bound_to_head(lp, env, capsys):
    with_consent(env)
    (env.repo_b / "Extra.lean").write_text("theorem x : True := trivial\n")
    _git(env.repo_b, "add", "Extra.lean")
    _git(env.repo_b, "commit", "-q", "-m", "source change")
    assert cli(lp, env, "palomar", "prepare", "beta") == 1
    assert "not bound to HEAD" in capsys.readouterr().err


def test_refuses_blocking_public_safety_hits_in_the_bundle(lp, env, capsys, assume_ready):
    with_consent(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["automation"] = "drafted from /home/jdoe/private-notes"
    write_reg(env, reg)
    assert cli(lp, env, "palomar", "prepare", "beta") == 1
    err = capsys.readouterr().err
    assert "blocking hit(s) in the bundle" in err and "jdoe" not in err
    assert not list((env.repo_b / ".lean-receipts").glob("palomar-*")) and read_events(env) == []


def test_refuses_error_gaps_and_writes_nothing(lp, env, tmp_path, capsys):
    """beta as it is: receipts bound, consent recorded, but Lean v4.32.0 and no module headers."""
    with_consent(env)
    before = env.registry.read_text()
    out = tmp_path / "bundle-out"
    for extra in ([], ["--dry-run"], ["--out", str(out)]):
        assert cli(lp, env, "palomar", "prepare", "beta", *extra) == 1
        err = capsys.readouterr().err
        assert "not Palomar-ready at HEAD" in err and "nothing written" in err
        assert "✗  source.module_required: " in err and "✗  toolchain.unsupported: " in err
        assert f"lean-pkg palomar check beta --rev {repo_head(env.repo_b)[:12]}" in err
    assert not out.exists() and not list((env.repo_b / ".lean-receipts").glob("palomar-*"))
    assert env.registry.read_text() == before and read_events(env) == []
    assert cli(lp, env, "palomar", "prepare", "beta", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "source.module_required" in data["error"]


def ready_gamma(tmp_path, **over):
    """gamma (a Palomar-ready checkout) with a HEAD-bound gate and comparator receipt and consent."""
    repo, reg = make_pkg(tmp_path, files(**over), lean_toolchain="leanprover/lean4:v4.35.0")
    rdir = repo / ".lean-receipts"
    rdir.mkdir()
    (rdir / "seed.json").write_text(json.dumps(make_receipt(repo), indent=2))
    comp_rel = f".lean-receipts/{repo_head(repo)[:12]}-comparator.json"
    (repo / comp_rel).write_text(json.dumps(make_comparator_receipt(repo), indent=2))
    data = yaml.safe_load(reg.read_text())
    data["packages"]["gamma"].update(
        upstream_target="palomar",
        gate={"receipt": ".lean-receipts/seed.json", "comparator": comp_rel,
              "axioms_allowed": ["propext", "Quot.sound", "Classical.choice"]},
        consent=[{"who": "Formalizer One", "role": "owner", "pointer": "consent/one.md",
                  "recorded_at": "2026-10-09T00:00:00+00:00"}])
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    return repo, reg


def test_ready_package_prepares_and_warn_gaps_never_refuse(lp, tmp_path, capsys):
    """gamma is ready (no error gap); a non-GitHub repo is only a warn gap, and prepare still builds."""
    repo, reg = ready_gamma(tmp_path)
    data = yaml.safe_load(reg.read_text())
    data["packages"]["gamma"]["visibility"] = "private"           # repository.visibility: a warn gap
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    assert lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json"]) == 0
    check = json.loads(capsys.readouterr().out)
    assert check["counts"]["error"] == 0 and check["counts"]["warn"] >= 1, check["gaps"]
    rc = lp.main(["--registry", str(reg), "palomar", "prepare", "gamma", "--json"])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    payload = json.loads(cap.out)
    view = payload["palomar_check"]
    assert view["ready"] is True and view["errors"] == 0 and view["warns"] == check["counts"]["warn"]
    assert view["commit"] == repo_head(repo)
    assert any(w.startswith("palomar check: ") for w in payload["warnings"])
    assert "lean-pkg: warning: palomar check: " in cap.err
    assert (repo / payload["bundle"] / "MANIFEST.sha256").is_file()
