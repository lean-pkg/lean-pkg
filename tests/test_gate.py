#!/usr/bin/env python3
"""gate: receipt validation and the gate-pending -> verified-private transition."""

from __future__ import annotations

import json

from .conftest import make_receipt, read_events, read_reg, repo_head, write_receipt


def run_gate(lp, env, receipt, tmp_path):
    p = write_receipt(tmp_path, receipt)
    return lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(p)])


def test_gate_rejects_bad_sha(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, report_sha256="not-a-sha")
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "report_sha256" in capsys.readouterr().err


def test_gate_rejects_sorry_count(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, sorry_count=1)
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "sorry_count" in capsys.readouterr().err


def test_gate_rejects_disallowed_axiom(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, axioms=["propext", "sorryAx"])
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "sorryAx" in capsys.readouterr().err


def test_gate_rejects_commit_not_head(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, commit="0" * 40)
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "HEAD" in capsys.readouterr().err


def test_gate_rejects_toolchain_mismatch(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, lean_toolchain="leanprover/lean4:v4.0.0")
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "toolchain mismatch" in capsys.readouterr().err


def test_gate_rejects_wrong_status(lp, env, tmp_path, capsys):
    # beta is verified-private, not gate-pending; use a receipt that differs
    # from the recorded seed so the status check (not the no-op path) fires
    r = make_receipt(env.repo_b, job_id="slurm-99999")
    p = write_receipt(tmp_path, r)
    rc = lp.main(["--registry", str(env.registry), "gate", "beta",
                  "--receipt", str(p)])
    assert rc == 1
    assert "gate-pending" in capsys.readouterr().err


def test_gate_accepts_valid(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    assert run_gate(lp, env, r, tmp_path) == 0
    out = capsys.readouterr().out
    assert "NOT committed" in out

    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "verified-private"
    assert pkg["gate"]["receipt"].startswith(".lean-receipts/")

    copied = env.repo_a / pkg["gate"]["receipt"]
    assert copied.is_file()
    assert json.loads(copied.read_text())["commit"] == r["commit"]

    assert pkg["history"][-1]["event"] == "gate-passed"
    assert pkg["history"][-1]["commit"] == r["commit"]
    events = read_events(env)
    assert events[-1]["event"] == "gate-passed"
    assert events[-1]["name"] == "alpha"
    assert events[-1]["commit"] == repo_head(env.repo_a)


def test_gate_preserves_hosted_terminal_job_identity(lp, env, tmp_path):
    job_id = "github-actions-123-attempt-1-job-456"
    checker = "leanchecker-serial-59-modules"
    r = make_receipt(env.repo_a, job_id=job_id, checker=checker)
    assert run_gate(lp, env, r, tmp_path) == 0

    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["history"][-1]["job_id"] == job_id
    assert pkg["history"][-1]["checker"] == checker
    copied = env.repo_a / pkg["gate"]["receipt"]
    assert json.loads(copied.read_text())["job_id"] == job_id
    events = read_events(env)
    assert events[-1]["job_id"] == job_id
    assert events[-1]["checker"] == checker


def test_gate_rejects_blank_terminal_job_identity(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, job_id="   ")
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "job_id" in capsys.readouterr().err


def test_gate_receipt_copy_is_idempotent(lp, env, tmp_path):
    r = make_receipt(env.repo_a)
    assert run_gate(lp, env, r, tmp_path) == 0
    dest = env.repo_a / read_reg(env)["packages"]["alpha"]["gate"]["receipt"]
    dest.write_text('{"sentinel": true}')
    # the tampered recorded receipt is no longer identical, so the no-op
    # "already gated" path is skipped and the status refusal fires first
    assert run_gate(lp, env, r, tmp_path) == 1
    assert json.loads(dest.read_text()) == {"sentinel": True}


def test_gate_identical_receipt_is_noop_success(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    p = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(p)]) == 0
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(p)]) == 0
    assert "already gated" in capsys.readouterr().out
    events = read_events(env)
    assert [e["event"] for e in events].count("gate-passed") == 1
    # a DIFFERENT receipt for the now-verified package is still refused
    r2 = make_receipt(env.repo_a, job_id="slurm-99999")
    p2 = write_receipt(tmp_path, r2, name="receipt2.json")
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(p2)]) == 1


def test_gate_success_prints_rl_reminder(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    assert run_gate(lp, env, r, tmp_path) == 0
    out = capsys.readouterr().out
    assert "results-ledger: record the outcome for alpha in your results ledger" in out


def test_gate_success_json_includes_rl_reminder(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    p = write_receipt(tmp_path, r)
    rc = lp.main(["--registry", str(env.registry), "--json", "gate", "alpha",
                  "--receipt", str(p)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rl_reminder"] is True


def test_gate_noop_has_no_rl_reminder(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    p = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(p)]) == 0
    capsys.readouterr()  # drain the first gate's output (incl. the reminder)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(p)]) == 0
    out = capsys.readouterr().out
    assert "already gated" in out
    assert "results-ledger" not in out


def test_gate_accepts_receipt_at_evidence_only_descendant(lp, env, tmp_path):
    from .conftest import _git
    r = make_receipt(env.repo_a)
    (env.repo_a / ".lean-receipts").mkdir()
    (env.repo_a / ".lean-receipts" / "earlier.json").write_text("{}\n")
    _git(env.repo_a, "add", "-f", ".lean-receipts")
    _git(env.repo_a, "commit", "-m", "evidence only")
    assert repo_head(env.repo_a) != r["commit"]
    assert run_gate(lp, env, r, tmp_path) == 0
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_gate_rejects_receipt_when_descendant_changes_source(lp, env, tmp_path, capsys):
    from .conftest import _git
    r = make_receipt(env.repo_a)
    (env.repo_a / "Main.lean").write_text("-- changed\n")
    _git(env.repo_a, "add", "Main.lean")
    _git(env.repo_a, "commit", "-m", "source change")
    assert run_gate(lp, env, r, tmp_path) == 1
    assert "HEAD" in capsys.readouterr().err


def test_gate_records_evidence_for_already_public_package(lp, env, tmp_path, capsys):
    """A package that became public before it was ever gated (for example a
    collaborator-owned repository) can still receive a receipt: the status stays
    public, gate.receipt and the gate-passed event are recorded."""
    from .conftest import write_reg
    reg = read_reg(env)
    reg["packages"]["alpha"]["status"] = "public"
    reg["packages"]["alpha"]["visibility"] = "public"
    write_reg(env, reg)
    r = make_receipt(env.repo_a)
    assert run_gate(lp, env, r, tmp_path) == 0
    out = capsys.readouterr().out
    assert "status unchanged" in out and "verified-private" not in out
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "public"
    assert pkg["gate"]["receipt"].startswith(".lean-receipts/")
    assert pkg["history"][-1]["event"] == "gate-passed"
    assert pkg["history"][-1]["status_unchanged"] is True
    assert read_events(env)[-1]["event"] == "gate-passed"


def test_regating_public_package_clears_a_comparator_bound_elsewhere(lp, env, tmp_path, capsys):
    """beta is gated with a receipt+comparator pair. Once public, a fresh receipt at a
    new commit without --comparator must not leave the old comparator pointer in
    place (the pair would bind to two commits)."""
    from .conftest import write_reg, _git
    reg = read_reg(env)
    reg["packages"]["beta"]["status"] = "public"
    reg["packages"]["beta"]["visibility"] = "public"
    write_reg(env, reg)
    assert reg["packages"]["beta"]["gate"]["comparator"]
    (env.repo_b / "More.lean").write_text("theorem more : True := trivial\n")
    _git(env.repo_b, "add", "More.lean")
    _git(env.repo_b, "commit", "-m", "more")
    r = make_receipt(env.repo_b)
    p = write_receipt(tmp_path, r, name="beta-receipt.json")
    assert lp.main(["--registry", str(env.registry), "gate", "beta", "--receipt", str(p)]) == 0
    out = capsys.readouterr().out
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["status"] == "public"
    assert pkg["gate"]["comparator"] is None
    assert "comparator" in out and "cleared" in out
