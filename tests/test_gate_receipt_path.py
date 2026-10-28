#!/usr/bin/env python3
"""Field report #5: a valid package-relative receipt path (`.lean-receipts/...`) failed when `gate` /
`gate --check` ran from a parent directory and passed from the registered checkout. A relative path that
exists from the cwd is used as given; one that does not, but exists under the package checkout (or
`--repo DIR`), is resolved there and the resolution is announced (stderr note, a check line, JSON
resolved_paths); the same relative path, never another matching receipt. Otherwise the refusal names the
caller's cwd, the checkout, both paths tried and how to pass the file."""

from __future__ import annotations

import json

from .conftest import make_receipt, read_events, read_reg, write_report_log

REL = ".lean-receipts/trial.json"


def put(repo, receipt, rel=REL):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    write_report_log(p.parent, receipt)           # the sealed log the receipt binds, beside it
    p.write_text(json.dumps(receipt))
    return p


def gate(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), "gate", *argv])


def test_check_from_a_parent_cwd_resolves_against_the_checkout_and_says_so(lp, env, capsys, monkeypatch):
    p = put(env.repo_a, make_receipt(env.repo_a))
    monkeypatch.chdir(env.repo_a.parent)
    assert gate(lp, env, "alpha", "--receipt", REL, "--check", "--json") == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["resolved_paths"] == [{"what": "receipt", "arg": REL, "path": str(p), "base": str(env.repo_a)}]
    assert any(c.startswith(f"· receipt {REL} resolved against the package checkout") for c in data["checks"])
    assert "does not exist relative to the cwd" in cap.err and str(p) in cap.err
    assert read_events(env) == []


def test_check_from_the_checkout_needs_no_resolution(lp, env, capsys, monkeypatch):
    put(env.repo_a, make_receipt(env.repo_a))
    monkeypatch.chdir(env.repo_a)
    assert gate(lp, env, "alpha", "--receipt", REL, "--check", "--json") == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["resolved_paths"] == [] and "resolved against" not in cap.err


def test_a_path_that_exists_from_the_cwd_wins(lp, env, tmp_path, capsys, monkeypatch):
    """Negative control: the file as given is used even when the checkout holds one at the same path."""
    put(env.repo_a, make_receipt(env.repo_a))
    work = tmp_path / "work"
    put(work, make_receipt(env.repo_a, sorry_count=2))      # a different receipt at the same relative path
    monkeypatch.chdir(work)
    assert gate(lp, env, "alpha", "--receipt", REL, "--check") == 1
    cap = capsys.readouterr()
    assert "sorry_count must be 0" in cap.err and "resolved against" not in cap.err


def test_missing_everywhere_names_cwd_checkout_paths_and_the_fix(lp, env, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert gate(lp, env, "alpha", "--receipt", REL, "--check") != 0
    err = capsys.readouterr().err
    for needle in (f"receipt not found: {REL}", f"caller cwd {tmp_path}", f"package checkout {env.repo_a}",
                   str(tmp_path / REL), str(env.repo_a / REL), "absolute path", "--repo DIR"):
        assert needle in err, needle


def test_an_absolute_missing_path_is_not_resolved_elsewhere(lp, env, tmp_path, capsys):
    put(env.repo_a, make_receipt(env.repo_a))
    assert gate(lp, env, "alpha", "--receipt", str(tmp_path / REL), "--check") != 0
    err = capsys.readouterr().err
    assert f"receipt not found: {tmp_path / REL}" in err and "resolved against" not in err


def test_writing_gate_from_a_parent_cwd_records_the_resolved_receipt(lp, env, capsys, monkeypatch):
    put(env.repo_a, make_receipt(env.repo_a))
    monkeypatch.chdir(env.repo_a.parent)
    assert gate(lp, env, "alpha", "--receipt", REL, "--json") == 0, capsys.readouterr().err
    data = json.loads(capsys.readouterr().out)
    assert data["resolved_paths"][0]["path"] == str(env.repo_a / REL)
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_check_with_agent_runs_and_notes_it(lp, env, tmp_path, capsys):
    """#386: --check takes --agent (it was exit 2): a stderr note, and the agent goes into next:."""
    put(env.repo_a, make_receipt(env.repo_a))
    assert gate(lp, env, "alpha", "--receipt", str(env.repo_a / REL), "--check", "--agent", "vera") == 0
    cap = capsys.readouterr()
    assert "gate --check takes no lease" in cap.err and "--agent vera is used by the writing gate" in cap.err
    assert cap.out.splitlines()[-1] == f"next: lean-pkg gate alpha --receipt {env.repo_a / REL} --agent vera"
    assert read_events(env) == []
