#!/usr/bin/env python3
"""Field report: after a deliberate pin bump, gated at a commit whose receipt names the new toolchain and
Mathlib revision, `list` still showed the old pin. `gate` and `gate --regate` record the receipt's
`lean_toolchain` and `mathlib_rev` into the package's `lean_toolchain` and `mathlib_pin` when they agree with
the files committed at the receipt's commit, and otherwise keep the recorded pin and say so; `gate --check`
only reports what would happen."""

from __future__ import annotations

import json

from .conftest import _git, make_receipt, read_events, read_reg, repo_head, write_receipt

NEW_TC = "leanprover/lean4:v4.35.0"
NEW_REV = "cfc55fc5" + "0" * 32


def gate(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), "gate", *argv])


def bump(repo, manifest_rev=NEW_REV):
    (repo / "lean-toolchain").write_text(NEW_TC + "\n")
    (repo / "lake-manifest.json").write_text(json.dumps(
        {"version": 7, "packages": [{"name": "mathlib", "type": "git", "rev": manifest_rev}]}) + "\n")
    _git(repo, "add", "lean-toolchain", "lake-manifest.json")
    _git(repo, "commit", "-m", "bump toolchain")
    return repo_head(repo)


def test_regate_records_the_receipt_pins(lp, env, tmp_path, capsys):
    old = read_reg(env)["packages"]["beta"]
    bump(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2", mathlib_rev=NEW_REV))
    n = len(read_events(env))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    want = {"lean_toolchain": {"from": old["lean_toolchain"], "to": NEW_TC},
            "mathlib_pin": {"from": old["mathlib_pin"], "to": NEW_REV[:10]}}
    assert data["pins"] == want
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["lean_toolchain"] == NEW_TC and pkg["mathlib_pin"] == "cfc55fc500"
    (ev,) = read_events(env)[n:]
    assert ev["event"] == "regated" and ev["pins"] == want
    assert pkg["history"][-1]["pins"] == want
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_gate_prints_one_line_when_pins_change(lp, env, tmp_path, capsys):
    bump(env.repo_a)
    p = write_receipt(tmp_path, make_receipt(env.repo_a, mathlib_rev=NEW_REV))
    assert gate(lp, env, "alpha", "--receipt", str(p)) == 0
    out = capsys.readouterr().out
    lines = [x for x in out.splitlines() if "pins updated" in x]
    assert len(lines) == 1 and "lean_toolchain " in lines[0] and f"-> {NEW_TC}" in lines[0]
    assert "mathlib_pin 81a5d257c8 -> cfc55fc500" in lines[0]
    assert read_events(env)[-1]["event"] == "gate-passed" and "pins" in read_events(env)[-1]


def test_unchanged_pins_print_nothing_and_add_no_field(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert gate(lp, env, "alpha", "--receipt", str(p), "--json") == 0
    assert json.loads(capsys.readouterr().out)["pins"] == {}
    assert "pins" not in read_events(env)[-1]
    # identical re-gate stays a no-op
    before = env.registry.read_text()
    n = len(read_events(env))
    assert gate(lp, env, "alpha", "--receipt", str(p), "--json") == 0
    assert json.loads(capsys.readouterr().out)["event"] == "already-gated"
    assert env.registry.read_text() == before and len(read_events(env)) == n


def test_a_full_rev_matching_the_short_pin_is_unchanged(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a, mathlib_rev="81a5d257c8e4"))
    assert gate(lp, env, "alpha", "--receipt", str(p), "--json") == 0
    assert json.loads(capsys.readouterr().out)["pins"] == {}
    assert read_reg(env)["packages"]["alpha"]["mathlib_pin"] == "81a5d257c8"


def test_gate_check_reports_pins_and_writes_nothing(lp, env, tmp_path, capsys):
    bump(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2", mathlib_rev=NEW_REV))
    before = env.registry.read_text()
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--check") == 0
    out = capsys.readouterr().out
    assert "· pins: gate would record " in out and "mathlib_pin 81a5d257c8 -> cfc55fc500" in out
    assert gate(lp, env, "beta", "--receipt", str(p), "--check", "--json") == 0
    assert json.loads(capsys.readouterr().out)["pins"]["lean_toolchain"]["to"] == NEW_TC
    assert env.registry.read_text() == before and read_events(env) == []


OTHER_REV = "0123abcd" + "f" * 32


def test_a_rev_not_in_the_committed_manifest_keeps_the_old_pin(lp, env, tmp_path, capsys):
    """Coordinator review: gate must not copy an unverified pin into the record; it keeps the recorded one,
    says why, and still gates."""
    bump(env.repo_b, manifest_rev=OTHER_REV)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2", mathlib_rev=NEW_REV))
    n = len(read_events(env))
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "regated"
    assert data["pins"] == {"lean_toolchain": {"from": "leanprover/lean4:v4.32.0", "to": NEW_TC}}
    assert data["pins_kept"] == {"mathlib_pin": {"receipt": NEW_REV, "manifest": OTHER_REV}}
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["mathlib_pin"] == "81a5d257c8" and pkg["lean_toolchain"] == NEW_TC
    (ev,) = read_events(env)[n:]
    assert "mathlib_pin" not in ev["pins"]


def test_pins_kept_line_in_text_and_check(lp, env, tmp_path, capsys):
    bump(env.repo_b, manifest_rev=OTHER_REV)
    head = repo_head(env.repo_b)
    p = write_receipt(tmp_path, make_receipt(env.repo_b, job_id="slurm-2", mathlib_rev=NEW_REV))
    want = (f"pins kept: receipt mathlib_rev {NEW_REV} does not match lake-manifest.json at {head[:12]} "
            f"({OTHER_REV})")
    before = env.registry.read_text()
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate", "--check") == 0
    out = capsys.readouterr().out
    assert f"· {want}" in out and "mathlib_pin 81a5d257c8 ->" not in out
    assert env.registry.read_text() == before
    assert gate(lp, env, "beta", "--receipt", str(p), "--regate") == 0
    assert f"beta: {want}" in capsys.readouterr().out


def test_no_manifest_at_the_commit_keeps_the_pin_as_unreadable(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a, mathlib_rev=NEW_REV))   # alpha tracks no manifest
    assert gate(lp, env, "alpha", "--receipt", str(p), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "gate-passed" and data["pins"] == {}
    assert data["pins_kept"] == {"mathlib_pin": {"receipt": NEW_REV, "manifest": None}}
    assert read_reg(env)["packages"]["alpha"]["mathlib_pin"] == "81a5d257c8"
