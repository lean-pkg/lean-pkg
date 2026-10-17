#!/usr/bin/env python3
"""#67: `gate NAME --receipt R --regate [--reason TEXT]` re-gates a verified-private (or public) package in one
step: one transition, one `regated` event, one registry commit (and push in committed mode)."""

from __future__ import annotations

import json
import shlex

from .conftest import _git, make_receipt, read_events, read_reg, repo_head, write_receipt, write_reg, write_report_log


def gate(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), "gate", *argv])


def advance(repo, rel="New.lean", text="-- more\n"):
    (repo / rel).write_text(text)
    _git(repo, "add", rel)
    _git(repo, "commit", "-m", f"edit {rel}")
    return repo_head(repo)


def test_regate_happy_path_one_event_status_unchanged(lp, env, tmp_path, capsys):
    prev = env.receipt_b["commit"]
    head = advance(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2"))
    n = len(read_events(env))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "regated" and data["status"] == "verified-private"
    assert data["previous_commit"] == prev and data["commit"] == head and data["non_descendant"] is False
    events = read_events(env)[n:]
    assert [e["event"] for e in events] == ["regated"]
    ev = events[0]
    assert (ev["previous_commit"], ev["commit"], ev["job_id"], ev["reason"]) == (prev, head, "slurm-2", "")
    assert "non_descendant" not in ev
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["status"] == "verified-private"
    assert pkg["gate"]["receipt"] == f".lean-receipts/{head[:12]}-slurm-2.json"
    assert pkg["history"][-1]["event"] == "regated"
    assert json.loads((env.repo_b / pkg["gate"]["receipt"]).read_text())["commit"] == head


def test_regate_text_output(lp, env, tmp_path, capsys):
    prev = env.receipt_b["commit"]
    head = advance(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2"))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate") == 0
    assert f"beta: verified-private, re-gated {prev[:12]} -> {head[:12]}" in capsys.readouterr().out


def test_gate_without_regate_on_verified_private_names_regate(lp, env, tmp_path, capsys):
    advance(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2"))
    before = env.registry.read_text()
    assert gate(lp, env, "beta", "--receipt", str(p)) == 1
    err = capsys.readouterr().err
    assert "gate requires status gate-pending" in err and "lean-pkg gate beta --receipt R --regate" in err
    assert env.registry.read_text() == before and read_events(env) == []


def _non_descendant(env):
    """Rewrite beta's history: HEAD becomes a sibling of the gated commit (amended), not a descendant."""
    (env.repo_b / "New.lean").write_text("-- rewritten\n")
    _git(env.repo_b, "add", "New.lean")
    _git(env.repo_b, "commit", "--amend", "-m", "rewritten")
    return repo_head(env.repo_b)


def test_regate_non_descendant_refused_without_reason(lp, env, tmp_path, capsys):
    prev = env.receipt_b["commit"]
    head = _non_descendant(env)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-3"))
    before = env.registry.read_text()
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate") == 1
    err = capsys.readouterr().err
    assert f"receipt commit {head[:12]} is not a descendant of the gated commit {prev[:12]}" in err
    assert "--reason" in err and "nothing copied or recorded" in err
    assert env.registry.read_text() == before and read_events(env) == []
    assert not (env.repo_b / f".lean-receipts/{head[:12]}-slurm-3.json").exists()


def test_regate_non_descendant_accepted_with_reason(lp, env, tmp_path, capsys):
    prev = env.receipt_b["commit"]
    head = _non_descendant(env)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-3"))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--reason", "history rewritten") == 0
    capsys.readouterr()
    ev = read_events(env)[-1]
    assert ev["event"] == "regated" and ev["non_descendant"] is True
    assert (ev["previous_commit"], ev["commit"], ev["reason"]) == (prev, head, "history rewritten")
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


def test_reason_without_regate_and_regate_on_gate_pending_are_refused(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert gate(lp, env, "alpha", "--receipt", str(p), "--reason", "x") == 2
    assert "--reason goes with --regate" in capsys.readouterr().err
    assert gate(lp, env, "alpha", "--receipt", str(p), "--regate") == 1
    assert "--regate re-gates a gated package" in capsys.readouterr().err
    assert read_events(env) == [] and read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"


def test_regate_runs_the_gate_validations(lp, env, tmp_path, capsys):
    advance(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2", sorry_count=1))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate") == 1
    assert "sorry_count" in capsys.readouterr().err
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2", axioms=["sorryAx"]))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate") == 1
    assert "sorryAx" in capsys.readouterr().err
    assert read_events(env) == []


def test_check_regate_writes_nothing(lp, env, tmp_path, capsys):
    prev = env.receipt_b["commit"]
    head = advance(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2"))
    before = env.registry.read_text()
    assert gate(lp, env, "beta", "--receipt", str(p), "--check", "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["recorded"] is False
    assert data["regate"] == {"previous_commit": prev, "non_descendant": False}
    assert any(c.startswith(f"✓ regate: {head[:12]} descends from") for c in data["checks"])
    assert env.registry.read_text() == before and read_events(env) == []
    assert not (env.repo_b / f".lean-receipts/{head[:12]}-slurm-2.json").exists()
    _git(env.repo_b, "reset", "-q", "--hard", prev)
    _non_descendant(env)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-3"))
    assert gate(lp, env, "beta", "--receipt", str(p), "--check", "--regate", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and any(c.startswith("✗ regate:") for c in data["checks"])
    assert env.registry.read_text() == before and read_events(env) == []


def test_next_recommends_regate_and_the_command_succeeds(lp, env, tmp_path, capsys):
    advance(env.repo_b)
    receipt = make_receipt(env.repo_b, job_id="slurm-4")
    write_report_log(env.repo_b / ".lean-receipts", receipt)
    (env.repo_b / ".lean-receipts" / "slurm-4.json").write_text(json.dumps(receipt))
    assert lp.main(["--registry", str(env.registry), "next", "beta", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert " --regate" in data["command"] and data["command"].startswith("lean-pkg gate beta --receipt ")
    assert "gate beta --check --regate --receipt" in data["reason"]
    assert lp.main(["--registry", str(env.registry), *shlex.split(data["command"])[1:]]) == 0
    capsys.readouterr()
    assert read_events(env)[-1]["event"] == "regated"
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


def test_regate_on_public_keeps_public(lp, env, tmp_path, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"].update(status="public", visibility="public")
    write_reg(env, reg)
    advance(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-5"))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "regated" and data["status"] == "public"
    assert read_reg(env)["packages"]["beta"]["status"] == "public"
