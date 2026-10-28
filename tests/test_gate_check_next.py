#!/usr/bin/env python3
"""#386: a passing `gate --check` prints the writing `gate` to run next (`next:` and JSON next_command),
with the evidence flags it was given and --agent A (else the AGENT placeholder); null when the check
fails or the status would refuse the writing gate. --check with --refresh-formalization stays refused."""

from __future__ import annotations

import json
import shlex

from .conftest import make_receipt, read_events, read_reg, write_reg, write_receipt


def run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_passing_check_prints_the_writing_command_first_line_stays_the_verdict(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc, out, err = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--refuse-dirty")
    lines = out.splitlines()
    assert rc == 0 and lines[0].startswith("alpha: receipt valid at commit")
    assert lines[-1] == f"next: lean-pkg gate alpha --receipt {p} --refuse-dirty --agent AGENT"
    assert "takes no lease" not in err


def test_next_command_in_json_runs_as_the_writing_gate(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--agent", "vera", "--json")
    nxt = json.loads(out)["next_command"]
    assert rc == 0 and nxt == f"lean-pkg gate alpha --receipt {p} --agent vera"
    argv = shlex.split(nxt)
    assert argv[:2] == ["lean-pkg", "gate"]
    rc, _, err = run(lp, env, capsys, *argv[1:])
    assert rc == 0, err
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"
    assert [e["event"] for e in read_events(env)] == ["gate-passed"]


def test_next_command_quotes_paths(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a), name="my receipt.json")
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--json")
    assert rc == 0 and json.loads(out)["next_command"] == \
        f"lean-pkg gate alpha --receipt {shlex.quote(str(p))} --agent AGENT"


def test_failing_check_has_a_null_next_command(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    # --regate on a gate-pending package: the check runs and fails (ok false)
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--regate", "--json")
    data = json.loads(out)
    assert rc == 1 and data["ok"] is False and data["next_command"] is None
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--regate")
    assert rc == 1 and "next:" not in out


def test_status_the_writing_gate_refuses_has_a_null_next_command(lp, env, capsys, tmp_path):
    reg = read_reg(env)
    reg["packages"]["alpha"]["status"] = "active"
    write_reg(env, reg)
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--json")
    data = json.loads(out)
    assert rc == 0 and data["ok"] is True and data["next_command"] is None
    assert any("first: `lean-pkg update alpha --status gate-pending" in c for c in data["checks"])


def test_check_with_refresh_formalization_is_still_refused(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    before = env.registry.read_bytes()
    rc, _, err = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p),
                     "--refresh-formalization", "--agent", "vera")
    assert rc == 2 and "--refresh-formalization does not apply" in err
    assert "--agent" not in err.split("does not apply")[0]       # --agent is no longer refused
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_repo_elsewhere_notes_that_the_writing_gate_binds_the_registered_path(lp, env, capsys, tmp_path):
    reg = read_reg(env)
    registered = tmp_path / "elsewhere"
    reg["packages"]["alpha"]["local_path"] = str(registered)
    write_reg(env, reg)
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--repo", str(env.repo_a))
    lines = out.splitlines()
    assert rc == 0 and lines[-2] == f"next: lean-pkg gate alpha --receipt {p} --agent AGENT"
    assert f"binds the registered local_path {registered}" in lines[-1]
    assert "lean-pkg update alpha --path DIR --reason TEXT" in lines[-1]
    # the writing gate's own --repo refusal says the same
    rc, _, err = run(lp, env, capsys, "gate", "alpha", "--receipt", str(p), "--repo", str(env.repo_a))
    assert rc == 2 and "binds the registered local_path" in err
    assert "lean-pkg update alpha --path DIR --reason TEXT" in err


def test_repo_at_the_registered_path_adds_no_note(lp, env, capsys, tmp_path):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    rc, out, _ = run(lp, env, capsys, "gate", "alpha", "--check", "--receipt", str(p), "--repo", str(env.repo_a))
    assert rc == 0 and out.splitlines()[-1].startswith("next: lean-pkg gate alpha")
