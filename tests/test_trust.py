#!/usr/bin/env python3
"""Trust holes (#15): the gate is judged at the bound commit, not the working tree; promote needs
tracked, pushed evidence; generated_at is parsed; register cannot mint verified/upstreamed."""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from .conftest import (_git, add_comparator_config, make_comparator_receipt, make_receipt,
                       make_repo, publish_evidence, read_events, read_reg, repo_head,
                       write_receipt, write_reg)

DIRTY_TC = "leanprover/lean4:v4.99.0"


def gate(lp, env, receipt, tmp_path, *extra, name="alpha"):
    p = write_receipt(tmp_path, receipt)
    return lp.main(["--registry", str(env.registry), *extra, "gate", name, "--receipt", str(p)])


def promote_json(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "--json", "promote", "beta", *extra])
    return rc, json.loads(capsys.readouterr().out)


def items(payload, status=None):
    return [c["label"] for c in payload["checklist"] if status is None or c["status"] == status]


# --- R6: lean-toolchain at the bound commit; dirty-tree WARN / --refuse-dirty ----------------

def test_gate_refuses_toolchain_that_matches_only_the_working_tree(lp, env, tmp_path, capsys):
    (env.repo_a / "lean-toolchain").write_text(DIRTY_TC + "\n")      # uncommitted edit
    r = make_receipt(env.repo_a, lean_toolchain=DIRTY_TC)           # bound to HEAD (v4.32.0)
    assert gate(lp, env, r, tmp_path) == 1
    err = capsys.readouterr().err
    assert "toolchain mismatch" in err and "committed at" in err
    assert DIRTY_TC in err and "leanprover/lean4:v4.32.0" in err
    assert "uncommitted files do not count" in err
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"


def test_gate_dirty_tree_is_a_warn_by_default(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)                                    # honest: committed toolchain
    (env.repo_a / "lean-toolchain").write_text(DIRTY_TC + "\n")
    rc = gate(lp, env, r, tmp_path, "--json")
    assert rc == 0
    out = capsys.readouterr()
    assert "WARN:" in out.err and "tracked file(s) modified" in out.err
    assert "--refuse-dirty" in out.err
    assert json.loads(out.out)["dirty"] == ["lean-toolchain"]
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_gate_refuse_dirty_refuses_and_copies_nothing(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    (env.repo_a / "lean-toolchain").write_text(DIRTY_TC + "\n")
    p = write_receipt(tmp_path, r)
    rc = lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p),
                  "--refuse-dirty"])
    assert rc == 1
    assert "--refuse-dirty" in capsys.readouterr().err
    assert not (env.repo_a / ".lean-receipts").exists()
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"
    assert read_events(env) == []


def test_gate_clean_tree_has_no_dirty_warn(lp, env, tmp_path, capsys):
    assert gate(lp, env, make_receipt(env.repo_a), tmp_path, "--json") == 0
    out = capsys.readouterr()
    assert "WARN" not in out.err and json.loads(out.out)["dirty"] == []


def test_comparator_toolchain_checked_at_the_bound_commit(lp, env):
    add_comparator_config(env.repo_a)
    (env.repo_a / "lean-toolchain").write_text(DIRTY_TC + "\n")
    comp = make_comparator_receipt(env.repo_a)                      # reads the dirty file
    assert comp["lean_toolchain"] == DIRTY_TC
    pkg = read_reg(env)["packages"]["alpha"]
    with pytest.raises(lp.Fail) as e:
        lp.validate_comparator(pkg, pathlib.Path(env.repo_a), comp, None)
    assert "comparator toolchain mismatch" in str(e.value) and "committed at" in str(e.value)


def test_comparator_check_refuse_dirty(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    (env.repo_a / "lean-toolchain").write_text(DIRTY_TC + "\n")
    p = write_receipt(tmp_path, comp, "comp.json")
    argv = ["--registry", str(env.registry), "comparator", "alpha", "--receipt", str(p), "--check"]
    assert lp.main(argv) == 0
    assert "WARN:" in capsys.readouterr().err
    assert lp.main(argv + ["--refuse-dirty"]) == 1
    assert "--refuse-dirty" in capsys.readouterr().err


def test_subdir_package_toolchain_read_relative_to_the_package(lp, tmp_path):
    root = make_repo(tmp_path / "paper", extra_files={
        "lean/lean-toolchain": "leanprover/lean4:v4.33.0\n"})
    head = repo_head(root)
    lp.check_toolchain_at(root / "lean", head, "leanprover/lean4:v4.33.0", "x")
    with pytest.raises(lp.Fail):
        lp.check_toolchain_at(root / "lean", head, "leanprover/lean4:v4.32.0", "x")


# --- R16: generated_at parsed; job_id sanitised ------------------------------------------

def test_gate_refuses_unparsable_generated_at(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, generated_at="yesterday-ish")
    assert gate(lp, env, r, tmp_path) == 1
    err = capsys.readouterr().err
    assert "generated_at" in err and "ISO-8601" in err and "yesterday-ish" in err


def test_comparator_refuses_unparsable_generated_at(lp, env):
    add_comparator_config(env.repo_a)
    comp = make_comparator_receipt(env.repo_a, generated_at="soon")
    pkg = read_reg(env)["packages"]["alpha"]
    with pytest.raises(lp.Fail) as e:
        lp.validate_comparator(pkg, pathlib.Path(env.repo_a), comp, None)
    assert "generated_at" in str(e.value) and "ISO-8601" in str(e.value)


def test_gate_job_id_traversal_is_stripped(lp, env, tmp_path):
    r = make_receipt(env.repo_a, job_id="../../evil")
    assert gate(lp, env, r, tmp_path) == 0
    rel = read_reg(env)["packages"]["alpha"]["gate"]["receipt"]
    assert rel == f".lean-receipts/{r['commit'][:12]}-evil.json"
    assert ".." not in rel
    assert (env.repo_a / rel).is_file()
    assert read_events(env)[-1]["job_id"] == "../../evil"        # the identity itself is kept


def test_gate_job_id_with_no_usable_characters_is_refused(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a, job_id="...///")
    assert gate(lp, env, r, tmp_path) == 1
    assert "no usable file-name characters" in capsys.readouterr().err
    assert not (env.repo_a / ".lean-receipts").exists()


# --- R7: promote requires tracked, pushed evidence ---------------------------------------

def test_promote_refuses_untracked_gate_receipt(lp, env, fake_gh, capsys):
    rc, data = promote_json(lp, env, capsys)           # env leaves .lean-receipts/ untracked
    assert rc == 1 and data["ok"] is False and data["refused"] is True
    bad = items(data, "fail")
    assert any("gate receipt .lean-receipts/seed.json and its sealed log not committed at HEAD" in b for b in bad)
    assert any("untracked files do not count" in b for b in bad)


def test_promote_refuses_untracked_comparator_log(lp, env, fake_gh, capsys):
    log = env.comp_b["configs"][0]["log"]
    _git(env.repo_b, "add", "-f", ".lean-receipts")
    _git(env.repo_b, "rm", "-q", "--cached", log)
    _git(env.repo_b, "commit", "-q", "-m", "evidence without the log")
    publish_evidence(env.repo_b, stage=False)
    rc, data = promote_json(lp, env, capsys)
    assert rc == 1
    bad = items(data, "fail")
    assert len(bad) == 1 and "comparator receipt" in bad[0] and log in bad[0]
    assert any("gate receipt .lean-receipts/seed.json and its sealed log tracked at HEAD (2 file(s))" in g
               for g in items(data, "pass"))


def test_promote_refuses_modified_tracked_receipt(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    seed = env.repo_b / ".lean-receipts" / "seed.json"
    seed.write_text(seed.read_text() + "\n")
    rc, data = promote_json(lp, env, capsys)
    assert rc == 1
    assert any("modified since HEAD" in b for b in items(data, "fail"))


def test_promote_refuses_when_no_origin_branch(lp, env, fake_gh, capsys):
    _git(env.repo_b, "add", "-f", ".lean-receipts")
    _git(env.repo_b, "commit", "-q", "-m", "evidence")           # tracked, never pushed
    rc, data = promote_json(lp, env, capsys)
    assert rc == 1
    bad = items(data, "fail")
    assert len(bad) == 1 and "no origin/" in bad[0] and "local refs" in bad[0]


def test_promote_refuses_head_not_contained_in_origin(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    (env.repo_b / ".lean-receipts" / "note.txt").write_text("later\n")
    _git(env.repo_b, "add", "-f", ".lean-receipts/note.txt")
    _git(env.repo_b, "commit", "-q", "-m", "unpushed evidence")
    rc, data = promote_json(lp, env, capsys)
    assert rc == 1
    bad = items(data, "fail")
    assert len(bad) == 1 and "not contained in origin/" in bad[0] and "local refs" in bad[0]


def test_promote_green_with_tracked_pushed_evidence(lp, env, fake_gh, capsys):
    head = publish_evidence(env.repo_b)
    rc, data = promote_json(lp, env, capsys)
    assert rc == 0 and data["ok"] is True and data["refused"] is False
    good = items(data, "pass")
    assert any(g.startswith("gate receipt .lean-receipts/seed.json and its sealed log tracked at HEAD")
               for g in good)
    assert any("and its 1 log(s) tracked at HEAD" in g for g in good)
    assert any(f"HEAD {head[:12]} contained in origin/" in g and "local refs" in g for g in good)
    assert items(data, "fail") == []


def _failing_gh(*args):
    if "view" in args:
        return SimpleNamespace(returncode=1, stdout="", stderr="gh: not logged in")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_promote_gh_failure_is_warn_on_dry_run_and_fail_on_yes(lp, env, monkeypatch, capsys):
    monkeypatch.setattr(lp, "gh", _failing_gh)
    monkeypatch.setattr(lp, "find_reuse", lambda: None)
    publish_evidence(env.repo_b)
    rc, data = promote_json(lp, env, capsys)
    assert rc == 0
    assert any("gh visibility query failed" in w for w in items(data, "warn"))
    rc, data = promote_json(lp, env, capsys, "--yes")
    assert rc == 1 and data["refused"] is True
    assert any("gh visibility query failed" in b and "--yes" in b for b in items(data, "fail"))
    assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"


# --- R4: register cannot mint verified-private / upstreamed; cross-field rules ------------

def register(lp, env, tmp_path, *extra):
    repo = make_repo(tmp_path / "gamma-repo")
    return lp.main(["--registry", str(env.registry), "register", "gamma",
                    "--repo", "git@github.com:example/gamma.git", "--path", str(repo), *extra])


@pytest.mark.parametrize("status", ["verified-private", "upstreamed", "archived"])
def test_register_refuses_statuses_owned_elsewhere(lp, env, tmp_path, capsys, status):
    with pytest.raises(SystemExit) as e:
        register(lp, env, tmp_path, "--status", status, "--visibility", "public")
    assert e.value.code == 2
    assert "gamma" not in read_reg(env)["packages"]


def test_do_register_refuses_verified_private_directly(lp, env, tmp_path):
    with pytest.raises(lp.Fail) as e:
        lp.do_register(env.registry, read_reg(env), env.events, "gamma", "x", str(tmp_path / "g"),
                       None, "private", "verified-private", "none", "", "", None, None, False)
    assert "lean-pkg gate" in str(e.value)


def test_register_public_needs_public_visibility(lp, env, tmp_path, capsys):
    assert register(lp, env, tmp_path, "--status", "public", "--reason", "collaborator repo") == 2
    assert "--visibility public" in capsys.readouterr().err
    assert "gamma" not in read_reg(env)["packages"]


def test_register_public_needs_reason(lp, env, tmp_path, capsys):
    """#21.6: a missing --reason is a refusal (exit 1, JSON under --json) like every other one (#17)."""
    assert register(lp, env, tmp_path, "--status", "public", "--visibility", "public") == 1
    assert "--reason" in capsys.readouterr().err
    assert "gamma" not in read_reg(env)["packages"]
    assert lp.main(["--registry", str(env.registry), "register", "gamma", "--repo", "git@github.com:example/gamma.git",
                    "--path", str(tmp_path / "gamma-repo"), "--status", "public", "--visibility", "public",
                    "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "requires --reason" in payload["error"] and "usage" not in payload
    assert "gamma" not in read_reg(env)["packages"]


def test_register_public_with_visibility_and_reason(lp, env, tmp_path):
    assert register(lp, env, tmp_path, "--status", "public", "--visibility", "public",
                    "--reason", "collaborator-owned, already public") == 0
    pkg = read_reg(env)["packages"]["gamma"]
    assert pkg["status"] == "public" and pkg["visibility"] == "public"
    assert pkg["history"][-1]["reason"] == "collaborator-owned, already public"


def test_register_gate_pending_allowed(lp, env, tmp_path):
    assert register(lp, env, tmp_path, "--status", "gate-pending") == 0
    assert read_reg(env)["packages"]["gamma"]["status"] == "gate-pending"


def test_validate_refuses_verified_private_without_receipt(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["receipt"] = None
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 1
    assert "verified-private requires gate.receipt" in capsys.readouterr().out


def _audit(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "audit", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["findings"]


@pytest.mark.parametrize("pkg,status,vis", [("beta", "verified-private", "public"),
                                            ("alpha", "public", "private"),
                                            ("alpha", "upstreamed", "private")])
def test_status_visibility_mismatch_is_an_audit_warn_not_a_validate_error(
        lp, env, fake_gh, capsys, pkg, status, vis):
    reg = read_reg(env)
    reg["packages"][pkg].update(status=status, visibility=vis)
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0
    capsys.readouterr()
    found = [f for f in _audit(lp, env, capsys) if f["check"] == "status-visibility-mismatch"]
    assert [f["name"] for f in found] == [pkg]
    assert status in found[0]["detail"] and vis in found[0]["detail"]


def test_no_status_visibility_finding_on_consistent_registry(lp, env, fake_gh, capsys):
    assert not [f for f in _audit(lp, env, capsys) if f["check"] == "status-visibility-mismatch"]
