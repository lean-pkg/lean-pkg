#!/usr/bin/env python3
"""#22.3: `palomar prepare NAME` builds a submission bundle (no network): formalization.yaml, comparator
configs, receipts bound to HEAD, README.txt, MANIFEST.sha256; event palomar-prepared; refusals."""

from __future__ import annotations

import hashlib
import json

import pytest

from .conftest import _git, read_events, read_reg, repo_head, write_reg


def cli(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def with_consent(env):
    reg = read_reg(env)
    reg["packages"]["beta"]["consent"] = [{"who": "Formalizer One", "role": "owner",
                                           "pointer": "consent/one.md", "recorded_at": "2026-10-09T00:00:00+00:00"}]
    reg["packages"]["beta"]["license_spdx"] = "MIT"
    write_reg(env, reg)


@pytest.fixture(autouse=True)
def no_network(lp, monkeypatch):
    import urllib.request

    def refuse(*a, **k):
        raise AssertionError("palomar prepare must not touch the network")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(lp, "gh", refuse)


def test_prepare_writes_the_bundle_and_records_the_event(lp, env, capsys):
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


def test_dry_run_writes_nothing(lp, env, capsys):
    with_consent(env)
    before = env.registry.read_text()
    assert cli(lp, env, "palomar", "prepare", "beta", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and "MANIFEST.sha256" in out
    assert not list((env.repo_b / ".lean-receipts").glob("palomar-*"))
    assert env.registry.read_text() == before and read_events(env) == []


def test_out_dir(lp, env, tmp_path, capsys):
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


def test_refuses_blocking_public_safety_hits_in_the_bundle(lp, env, capsys):
    with_consent(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["automation"] = "drafted from /home/jdoe/private-notes"
    write_reg(env, reg)
    assert cli(lp, env, "palomar", "prepare", "beta") == 1
    err = capsys.readouterr().err
    assert "blocking hit(s) in the bundle" in err and "jdoe" not in err
    assert not list((env.repo_b / ".lean-receipts").glob("palomar-*")) and read_events(env) == []
