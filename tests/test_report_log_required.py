#!/usr/bin/env python3
"""Soundness: a lower-tier receipt whose sealed verify log is missing binds nothing (report_sha256 hashes
a file nobody can see), so every consumer refuses it, as `gate` does: gate and gate --check (in
test_gate_binding), the claims ledger (test_claims_ledger: report-log-missing), promote/publish, next,
comparator, palomar prepare and index add/check. Each refusal names the path to restore the log to.
Negative controls: the same receipt with its log present, hash-matching, passes."""

from __future__ import annotations

import json

from .conftest import (_git, index_add, make_comparator_receipt, make_index_package, make_receipt, put_receipt,
                       read_events, repo_head)
from .test_palomar_prepare import ready_gamma


def seed_log(env):
    """The sealed log beside beta's recorded gate receipt (conftest's env writes it)."""
    logs = list((env.repo_b / ".lean-receipts").glob("*-verify.redacted.log"))
    assert len(logs) == 1, logs
    return logs[0]


def run(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# --- next ---------------------------------------------------------------------------------------------

def test_next_blocks_a_recorded_receipt_whose_log_is_gone(lp, env, capsys):
    rc, out, _ = run(lp, env, capsys, "next", "beta", "--json")
    assert "report log missing" not in out                        # control: the log is there
    log = seed_log(env)
    log.unlink()
    rc, out, _ = run(lp, env, capsys, "next", "beta", "--json")
    data = json.loads(out)
    assert rc == 1 and data["blockers"][0]["kind"] == "invalid-receipt"
    assert "report log missing" in data["blockers"][0]["detail"] and log.name in data["blockers"][0]["detail"]
    assert "Restoring preserved evidence" in data["reason"]
    assert data["command"].startswith("lean-pkg gate beta --check --receipt ")


def test_next_never_recommends_gating_a_candidate_without_its_log(lp, env, capsys):
    d = env.repo_a / ".lean-receipts"
    d.mkdir()
    r = make_receipt(env.repo_a, report_sha256="b" * 64)             # no log anywhere
    (d / "slurm-1.json").write_text(json.dumps(r))
    rc, out, _ = run(lp, env, capsys, "next", "alpha", "--json")
    data = json.loads(out)
    assert rc == 1 and data["blockers"][0]["kind"] == "invalid-receipt"
    assert "report log missing" in data["blockers"][0]["detail"]
    assert data["command"] == f"lean-pkg gate alpha --check --receipt {d / 'slurm-1.json'}"


# --- comparator ---------------------------------------------------------------------------------------

def test_comparator_refuses_to_pair_with_a_gate_receipt_whose_log_is_gone(lp, env, tmp_path, capsys):
    comp = make_comparator_receipt(env.repo_b)
    cp = tmp_path / "comp.json"
    cp.write_text(json.dumps({**comp, "generated_at": "2026-09-20T00:00:00+00:00"}))
    rc, _, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(cp))
    assert rc == 0, err                                            # control
    seed_log(env).unlink()
    rc, _, err = run(lp, env, capsys, "comparator", "beta", "--check", "--receipt", str(cp))
    assert rc == 1 and "the recorded gate receipt .lean-receipts/seed.json: report log missing" in err
    before = env.registry.read_bytes()
    rc, _, err = run(lp, env, capsys, "comparator", "beta", "--receipt", str(cp), "--replace")
    assert rc == 1 and "report log missing" in err and "nothing recorded" in err
    assert env.registry.read_bytes() == before and read_events(env) == []


# --- promote / publish --------------------------------------------------------------------------------

def test_promote_checklist_fails_on_a_missing_gate_log(lp, env, fake_gh, capsys):
    seed_log(env).unlink()
    rc, out, _ = run(lp, env, capsys, "--json", "promote", "beta")
    data = json.loads(out)
    bad = [c["label"] for c in data["checklist"] if c["status"] == "fail"]
    assert rc == 1 and any(b.startswith("gate receipt .lean-receipts/seed.json: report log missing") for b in bad)


# --- palomar prepare ----------------------------------------------------------------------------------

def test_palomar_prepare_refuses_a_gate_receipt_without_its_log(lp, tmp_path, capsys):
    repo, reg = ready_gamma(tmp_path)
    for p in (repo / ".lean-receipts").glob("*-verify.redacted.log"):
        p.unlink()
    rc = lp.main(["--registry", str(reg), "palomar", "prepare", "gamma"])
    err = capsys.readouterr().err
    assert rc == 1 and "gate receipt .lean-receipts/seed.json: report log missing" in err


# --- index add / check --------------------------------------------------------------------------------

def _without_log(tmp_path, keep_log=False):
    """An index package whose evidence commit carries the lower-tier receipt but not its sealed log
    (keep_log: the control, with it)."""
    pkg = make_index_package(tmp_path, comparator=False)
    _git(pkg.repo, "reset", "-q", "--hard", pkg.pin)
    (pkg.repo / ".lean-receipts").mkdir(exist_ok=True)
    r = make_receipt(pkg.repo)
    put_receipt(pkg.repo / pkg.lower_rel, r, indent=2, tail="\n")
    if not keep_log:
        for p in (pkg.repo / ".lean-receipts").glob("*-verify.redacted.log"):
            p.unlink()
    _git(pkg.repo, "add", "-A")
    _git(pkg.repo, "commit", "-m", "evidence")
    pkg.evidence = repo_head(pkg.repo)
    return pkg


def test_index_add_refuses_a_lower_tier_receipt_whose_log_is_not_committed(lp, tmp_path, capsys):
    assert index_add(lp, tmp_path / "control", _without_log(tmp_path / "a", keep_log=True)) == 0
    capsys.readouterr()
    pkg = _without_log(tmp_path / "b")
    assert index_add(lp, tmp_path / "index", pkg) == 1
    err = capsys.readouterr().err
    assert "report log missing: .lean-receipts/" in err and "is not committed at the pin" in err
    assert "Restoring preserved evidence" in err
    assert not (tmp_path / "index" / "demo").exists()


def test_index_check_fails_when_the_log_is_dropped_after_the_entry(lp, tmp_path, capsys):
    """An entry added while the log was committed fails `index check --repo-dir` against a clone whose
    history no longer has it (rewritten): the check re-derives from bytes, not from the entry."""
    pkg = _without_log(tmp_path / "a", keep_log=True)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    assert lp.main(["index", "check", "demo", "--index", str(root), "--repo-dir", str(pkg.repo)]) == 0
    capsys.readouterr()
    receipt = (pkg.repo / pkg.lower_rel).read_bytes()
    _git(pkg.repo, "reset", "-q", "--hard", pkg.pin)
    (pkg.repo / ".lean-receipts").mkdir(exist_ok=True)
    (pkg.repo / pkg.lower_rel).write_bytes(receipt)               # the same receipt bytes, no log
    _git(pkg.repo, "add", "-A")
    _git(pkg.repo, "commit", "-m", "evidence without the log")
    _git(pkg.repo, "reflog", "expire", "--expire=now", "--all")
    _git(pkg.repo, "gc", "-q", "--prune=now")
    rc = lp.main(["index", "check", "demo", "--index", str(root), "--repo-dir", str(pkg.repo)])
    out = capsys.readouterr()
    assert rc == 1 and "report log missing" in out.out + out.err
