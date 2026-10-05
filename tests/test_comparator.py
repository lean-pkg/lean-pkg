#!/usr/bin/env python3
"""validate_comparator: byte-level re-derivation of a comparator PASS."""

from __future__ import annotations

import json
import pathlib

import pytest

from .conftest import (COMPARATOR_PASS_LINE, _git, add_comparator_config,
                       make_comparator_receipt, read_reg, repo_head, sha256)


def pkg_of(lp, env, name="alpha"):
    return read_reg(env)["packages"][name]


def validate(lp, env, repo, comp, receipt_commit=None):
    lp.validate_comparator(pkg_of(lp, env), pathlib.Path(repo), comp, receipt_commit)


def test_valid_comparator_receipt_passes(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    validate(lp, env, env.repo_a, comp)  # no exception


def test_valid_against_matching_lower_receipt_commit(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    validate(lp, env, env.repo_a, comp, receipt_commit=repo_head(env.repo_a))


@pytest.mark.parametrize("field,value,needle", [
    ("schema", "comparator-receipt/0", "schema"),
    ("commit", "0" * 40, "HEAD"),
    ("lean_toolchain", "leanprover/lean4:v4.0.0", "toolchain"),
    ("result", "fail", "result"),
    ("configs", [], "configs"),
    ("host", "", "host"),
    ("job_id", " ", "job_id"),
    ("generated_at", "", "generated_at"),
])
def test_rejects_bad_top_level_field(lp, env, field, value, needle):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a, **{field: value})
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert needle in str(e.value)


def test_rejects_commit_not_equal_lower_receipt(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp, receipt_commit="1" * 40)
    assert "lower-tier receipt" in str(e.value)


def test_rejects_bad_tool_pin(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["tools"]["nanoda"] = "main"
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "nanoda" in str(e.value)


def test_rejects_missing_tool(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    del comp["tools"]["landrun"]
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "landrun" in str(e.value)


def test_rejects_config_result_not_pass(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["result"] = "fail"
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "comparator.json" in str(e.value)


def test_rejects_config_sha_mismatch(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["sha256"] = "f" * 64
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "sha256" in str(e.value)


def test_rejects_config_absent_at_head(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["path"] = "comparator/config-missing.json"
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "HEAD" in str(e.value)


def test_comparator_uses_bytes_at_head_not_worktree(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    # dirty edit after the receipt: HEAD bytes are unchanged, so still valid
    (env.repo_a / "comparator.json").write_text("{}\n")
    validate(lp, env, env.repo_a, comp)
    # a receipt hashed from the dirty bytes must NOT pass
    comp["configs"][0]["sha256"] = sha256(b"{}\n")
    with pytest.raises(lp.Fail):
        validate(lp, env, env.repo_a, comp)


def test_comparator_rejects_path_outside_repo(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    for bad in ("../comparator.json", "/etc/passwd", "comparator/../../x.json"):
        comp["configs"][0]["path"] = bad
        with pytest.raises(lp.Fail) as e:
            validate(lp, env, env.repo_a, comp)
        assert "path" in str(e.value)


def test_rejects_empty_theorem_names(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["theorem_names"] = []
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "theorem_names" in str(e.value)


def test_rejects_axiom_outside_allowed(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["permitted_axioms"] = ["propext", "sorryAx"]
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "sorryAx" in str(e.value)


def test_rejects_log_missing(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    (env.repo_a / comp["configs"][0]["log"]).unlink()
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "log" in str(e.value)


def test_rejects_log_outside_lean_receipts(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["log"] = "comparator.json"
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert ".lean-receipts" in str(e.value)


def test_rejects_log_sha_mismatch(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    log = env.repo_a / comp["configs"][0]["log"]
    log.write_text(log.read_text() + "tampered\n")
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "log_sha256" in str(e.value)


def test_comparator_rejects_pass_line_substring(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    log = env.repo_a / comp["configs"][0]["log"]
    text = log.read_text().replace(COMPARATOR_PASS_LINE, "# " + COMPARATOR_PASS_LINE)
    log.write_bytes(text.encode())
    comp["configs"][0]["log_sha256"] = sha256(text.encode())
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "PASS line" in str(e.value)


def test_rejects_missing_config_field(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    del comp["configs"][0]["challenge_module"]
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "challenge_module" in str(e.value)


# --- gate --comparator ----------------------------------------------------

from .conftest import make_receipt, read_events, write_receipt  # noqa: E402


def gate_with_comparator(lp, env, tmp_path, receipt, comp):
    rp = write_receipt(tmp_path, receipt)
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    return lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(rp), "--comparator", str(cp)])


def test_gate_with_comparator_records_it(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    assert gate_with_comparator(lp, env, tmp_path, r, comp) == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "verified-private"
    head = repo_head(env.repo_a)
    assert pkg["gate"]["comparator"] == f".lean-receipts/{head[:12]}-comparator.json"
    assert json.loads((env.repo_a / pkg["gate"]["comparator"]).read_text()) == comp
    assert pkg["history"][-1]["event"] == "gate-passed"
    assert pkg["history"][-1]["comparator"] == pkg["gate"]["comparator"]
    assert read_events(env)[-1]["comparator"] == pkg["gate"]["comparator"]
    assert "comparator" in capsys.readouterr().out


def test_gate_without_comparator_leaves_null(lp, env, tmp_path):
    r = make_receipt(env.repo_a)
    rp = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(rp)]) == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["gate"]["comparator"] is None
    assert "comparator" not in pkg["history"][-1]


def test_gate_with_invalid_comparator_changes_nothing(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    comp = make_comparator_receipt(env.repo_a, result="fail")
    assert gate_with_comparator(lp, env, tmp_path, r, comp) == 1
    assert "result" in capsys.readouterr().err
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "gate-pending"
    assert pkg["gate"]["receipt"] is None
    assert read_events(env) == []


def test_gate_identical_pair_is_noop(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    assert gate_with_comparator(lp, env, tmp_path, r, comp) == 0
    assert gate_with_comparator(lp, env, tmp_path, r, comp) == 0
    assert "already gated" in capsys.readouterr().out
    assert len([e for e in read_events(env) if e["event"] == "gate-passed"]) == 1


def test_gate_same_receipt_new_comparator_points_to_subcommand(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    rp = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(rp)]) == 0
    comp = make_comparator_receipt(env.repo_a)
    assert gate_with_comparator(lp, env, tmp_path, r, comp) == 1
    assert "lean-pkg comparator" in capsys.readouterr().err


# --- comparator subcommand ------------------------------------------------

from .conftest import write_reg  # noqa: E402


def attach(lp, env, tmp_path, name, comp):
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    return lp.main(["--registry", str(env.registry), "comparator", name,
                    "--receipt", str(cp)])


def detach_beta(env):
    """Fixture beta already carries a comparator; null it to test attaching."""
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = None
    write_reg(env, reg)


def test_comparator_attaches_to_verified_private(lp, env, tmp_path, capsys):
    detach_beta(env)
    comp = make_comparator_receipt(env.repo_b)
    assert attach(lp, env, tmp_path, "beta", comp) == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["status"] == "verified-private"
    head = repo_head(env.repo_b)
    assert pkg["gate"]["comparator"] == f".lean-receipts/{head[:12]}-comparator.json"
    assert pkg["history"][-1]["event"] == "comparator-attached"
    assert pkg["history"][-1]["commit"] == head
    ev = read_events(env)[-1]
    assert ev["event"] == "comparator-attached" and ev["name"] == "beta"
    assert "comparator-attached" in capsys.readouterr().out


def test_comparator_identical_reattach_is_noop(lp, env, tmp_path, capsys):
    # the fixture's recorded receipt equals env.comp_b byte-for-byte
    assert attach(lp, env, tmp_path, "beta", env.comp_b) == 0
    assert "already attached" in capsys.readouterr().out
    assert [e for e in read_events(env) if e["event"] == "comparator-attached"] == []


def test_comparator_refuses_gate_pending(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    assert attach(lp, env, tmp_path, "alpha", comp) == 1
    assert "gate-pending" in capsys.readouterr().err
    assert read_events(env) == []


def test_comparator_refuses_commit_mismatch_with_lower_receipt(lp, env, tmp_path, capsys):
    detach_beta(env)
    # advance HEAD: seed.json binds to the old commit, the new comparator to HEAD
    (env.repo_b / "NEW.md").write_text("x\n")
    _git(env.repo_b, "add", "NEW.md")
    _git(env.repo_b, "commit", "-m", "advance HEAD")
    comp = make_comparator_receipt(env.repo_b)
    assert attach(lp, env, tmp_path, "beta", comp) == 1
    assert "lower-tier receipt" in capsys.readouterr().err
    assert read_reg(env)["packages"]["beta"]["gate"].get("comparator") is None


# --- promote requires comparator; audit reports drift ---------------------

def promote(lp, env, name="beta", yes=False):
    argv = ["--registry", str(env.registry), "promote", name] + (["--yes"] if yes else [])
    return lp.main(argv)


def test_promote_checklist_shows_comparator_pass(lp, env, fake_gh, capsys):
    assert promote(lp, env) == 0
    out = capsys.readouterr().out
    assert "comparator PASS bound to HEAD" in out
    assert "nanoda" in out


def test_promote_refuses_without_comparator(lp, env, fake_gh, capsys):
    detach_beta(env)
    assert promote(lp, env) == 1
    out = capsys.readouterr().out
    assert "no comparator receipt recorded" in out and "required before public" in out
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


def test_promote_refuses_comparator_commit_mismatch(lp, env, fake_gh, capsys):
    (env.repo_b / "NEW.md").write_text("x\n")
    _git(env.repo_b, "add", "NEW.md")
    _git(env.repo_b, "commit", "-m", "advance HEAD")
    assert promote(lp, env) == 1
    assert "comparator receipt invalid" in capsys.readouterr().out


def test_promote_refuses_tampered_log(lp, env, fake_gh, capsys):
    log = env.repo_b / env.comp_b["configs"][0]["log"]
    log.write_text(log.read_text() + "x\n")
    assert promote(lp, env) == 1
    assert "log_sha256" in capsys.readouterr().out


def test_promote_yes_still_works_with_valid_comparator(lp, env, fake_gh, fake_notify):
    assert promote(lp, env, yes=True) == 0
    assert read_reg(env)["packages"]["beta"]["status"] == "public"


def test_audit_reports_comparator_drift_and_missing(lp, env, fake_gh, capsys):
    (env.repo_b / "NEW.md").write_text("x\n")
    _git(env.repo_b, "add", "NEW.md")
    _git(env.repo_b, "commit", "-m", "advance HEAD")
    assert lp.main(["--registry", str(env.registry), "audit"]) in (0, 1)
    out = capsys.readouterr().out
    assert "comparator-drift" in out
    comp_path = env.repo_b / read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    comp_path.unlink()
    lp.main(["--registry", str(env.registry), "audit"])
    assert "comparator-missing" in capsys.readouterr().out


# --- review fix pass: completeness from HEAD, field fidelity, pointer honesty

def test_rejects_receipt_missing_a_tracked_config(lp, env):
    add_comparator_config(env.repo_a, "comparator/config-a.json")
    add_comparator_config(env.repo_a, "comparator/config-b.json")
    comp = make_comparator_receipt(env.repo_a, "comparator/config-a.json")
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "config-b.json" in str(e.value) and "HEAD" in str(e.value)


def test_rejects_receipt_fields_differ_from_config_blob(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    comp["configs"][0]["theorem_names"] = ["something_else"]
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "theorem_names" in str(e.value) and "config" in str(e.value)


def test_rejects_log_not_bound_to_config_name(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    head = repo_head(env.repo_a)
    other = env.repo_a / ".lean-receipts" / f"{head[:12]}-comparator-other.log"
    other.write_bytes((env.repo_a / comp["configs"][0]["log"]).read_bytes())
    comp["configs"][0]["log"] = f".lean-receipts/{head[:12]}-comparator-other.log"
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "comparator-comparator.log" in str(e.value)


def test_rejects_config_that_is_a_directory_at_head(lp, env):
    add_comparator_config(env.repo_a, "comparator/config-a.json")
    comp = make_comparator_receipt(env.repo_a, "comparator/config-a.json")
    comp["configs"][0]["path"] = "comparator"
    with pytest.raises(lp.Fail):
        validate(lp, env, env.repo_a, comp)


def test_comparator_refuses_when_recorded_file_differs(lp, env, tmp_path, capsys):
    detach_beta(env)
    comp = make_comparator_receipt(env.repo_b, job_id="local-build-host-9999-20261005T130000Z")
    # the fixture already left a DIFFERENT receipt at the canonical path
    assert attach(lp, env, tmp_path, "beta", comp) == 1
    assert "different" in capsys.readouterr().err
    assert read_reg(env)["packages"]["beta"]["gate"]["comparator"] is None
    assert read_events(env) == []


def test_gate_refuses_when_recorded_lower_receipt_file_differs(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    dest = env.repo_a / ".lean-receipts" / f"{r['commit'][:12]}-{r['job_id']}.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(dict(r, checker="someone-else"), indent=2))
    rp = write_receipt(tmp_path, r)
    assert lp.main(["--registry", str(env.registry), "gate", "alpha",
                    "--receipt", str(rp)]) == 1
    assert "different" in capsys.readouterr().err
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"


# --- follow-ups: check-only mode, symlink refusal, full reason in JSON ------

def test_comparator_check_validates_without_recording(lp, env, tmp_path, capsys):
    # alpha is only `active`/gate-pending: --check must still validate and record nothing
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    rc = lp.main(["--registry", str(env.registry), "comparator", "alpha",
                  "--receipt", str(cp), "--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "valid" in out and "not recorded" in out
    assert read_reg(env)["packages"]["alpha"]["gate"].get("comparator") is None
    assert read_events(env) == []
    assert not (env.repo_a / ".lean-receipts" / f"{comp['commit'][:12]}-comparator.json").exists()


def test_comparator_check_reports_invalid(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a, result="fail")
    cp = write_receipt(tmp_path, comp, name="comparator-receipt.json")
    rc = lp.main(["--registry", str(env.registry), "comparator", "alpha",
                  "--receipt", str(cp), "--check"])
    assert rc == 1
    assert "result" in capsys.readouterr().err


def test_rejects_symlinked_log(lp, env, tmp_path):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    log = env.repo_a / comp["configs"][0]["log"]
    outside = tmp_path / "outside.log"
    outside.write_bytes(log.read_bytes())
    log.unlink()
    log.symlink_to(outside)
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "symlink" in str(e.value)


def test_rejects_symlinked_receipts_dir(lp, env, tmp_path):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    real = env.repo_a / ".lean-receipts"
    moved = tmp_path / "elsewhere"
    real.rename(moved)
    real.symlink_to(moved, target_is_directory=True)
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "symlink" in str(e.value)


def test_promote_json_carries_full_comparator_reason(lp, env, fake_gh, capsys):
    log = env.repo_b / env.comp_b["configs"][0]["log"]
    log.write_text(log.read_text() + "x\n")
    rc = lp.main(["--registry", str(env.registry), "--json", "promote", "beta"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    bad = [c for c in payload["checklist"] if "comparator receipt invalid" in c["label"]]
    assert len(bad) == 1
    assert "reason" in bad[0] and "log_sha256" in bad[0]["reason"]
    assert bad[0]["reason"].endswith(".log")  # full text, not truncated to 140


# --- evidence-only descendant rule ----------------------------------------

def commit_evidence(repo, *extra_paths):
    _git(repo, "add", "-f", ".lean-receipts")
    for p in extra_paths:
        (repo / p).write_text("x\n")
        _git(repo, "add", p)
    _git(repo, "commit", "-m", "evidence")


def test_comparator_accepted_at_evidence_only_descendant(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    bound = comp["commit"]
    commit_evidence(env.repo_a)
    assert repo_head(env.repo_a) != bound
    validate(lp, env, env.repo_a, comp)  # accepted: HEAD only adds .lean-receipts/


def test_comparator_refused_when_descendant_changes_source(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    commit_evidence(env.repo_a, "NEW.md")
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "NEW.md" in str(e.value) and "HEAD" in str(e.value)


def test_comparator_refused_when_bound_commit_is_not_an_ancestor(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    # a sibling commit that is not an ancestor of HEAD
    _git(env.repo_a, "checkout", "-q", "-b", "side")
    (env.repo_a / "SIDE.md").write_text("s\n")
    _git(env.repo_a, "add", "SIDE.md")
    _git(env.repo_a, "commit", "-m", "side")
    side = repo_head(env.repo_a)
    _git(env.repo_a, "checkout", "-q", "-")
    comp["commit"] = side
    with pytest.raises(lp.Fail) as e:
        validate(lp, env, env.repo_a, comp)
    assert "HEAD" in str(e.value)


def test_promote_passes_after_evidence_commit_on_both_tiers(lp, env, fake_gh, capsys):
    commit_evidence(env.repo_b)   # seed.json + comparator receipt + log now tracked
    assert promote(lp, env) == 0
    out = capsys.readouterr().out
    assert "evidence-only descendant" in out
    assert "comparator PASS" in out


def test_audit_no_drift_after_evidence_only_commit(lp, env, fake_gh, capsys):
    commit_evidence(env.repo_b)
    lp.main(["--registry", str(env.registry), "audit"])
    out = capsys.readouterr().out
    assert "receipt-drift" not in out and "comparator-drift" not in out
