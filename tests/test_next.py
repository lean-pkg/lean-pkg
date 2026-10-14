#!/usr/bin/env python3
"""#42: `next NAME`, the single recommended next command for a package (read-only, offline)."""

from __future__ import annotations

import json
import shlex

import pytest

from .conftest import (_git, base_pkg, commit_claims, make_receipt, make_repo, read_events, read_reg,
                       repo_head, set_lease, write_reg)

PAYLOAD_KEYS = {"ok", "name", "command", "reason", "blockers", "notes", "state"}
STATE_KEYS = {"status", "visibility", "lease", "gate_commit", "comparator_commit", "head", "checkout",
              "dirty", "receipt_candidate", "rejected_receipts", "comparator_configs", "claims_ledger"}


def nxt(lp, env, capsys, name, *extra):
    rc = lp.main(["--registry", str(env.registry), "next", name, "--json", *extra])
    data = json.loads(capsys.readouterr().out)
    return rc, data


def set_status(env, name, status, **kw):
    reg = read_reg(env)
    reg["packages"][name]["status"] = status
    reg["packages"][name].update(kw)
    write_reg(env, reg)


def drop_receipt(repo, receipt, name="slurm-1.json"):
    d = repo / ".lean-receipts"
    d.mkdir(exist_ok=True)
    (d / name).write_text(json.dumps(receipt))
    return f".lean-receipts/{name}"


@pytest.fixture(autouse=True)
def _no_gh(lp, monkeypatch):
    """next never consults GitHub."""
    def refuse(*a):
        pytest.fail(f"next called gh {a}")
    monkeypatch.setattr(lp, "gh", refuse)


def test_json_shape_and_read_only(lp, env, capsys):
    before = env.registry.read_bytes()
    rc, data = nxt(lp, env, capsys, "beta")
    assert set(data) == PAYLOAD_KEYS and set(data["state"]) == STATE_KEYS
    assert rc == 0 and data["ok"] is True and data["blockers"] == []
    assert data["state"]["head"] == repo_head(env.repo_b)
    assert env.registry.read_bytes() == before and read_events(env) == []


def test_text_output_names_command_and_reason(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "next", "beta"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "beta (verified-private): lean-pkg publish beta --dry-run"
    assert out[1].startswith("  why: ")


def test_usage_and_unknown_package(lp, env, capsys):
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(env.registry), "next"])
    assert e.value.code == 2
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "next", "nope", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("status", ["scratch", "active"])
def test_unleased_scratch_or_active_claims(lp, env, capsys, status):
    set_status(env, "alpha", status)
    rc, data = nxt(lp, env, capsys, "alpha", "--agent", "vera")
    assert rc == 0 and data["command"].startswith("lean-pkg claim alpha --workspace W --agent vera")


def test_claimed_scratch_moves_to_active(lp, env, capsys):
    set_status(env, "alpha", "scratch")
    set_lease(env, "alpha", agent="vera")
    rc, data = nxt(lp, env, capsys, "alpha", "--agent", "vera")
    assert rc == 0
    assert data["command"] == 'lean-pkg update alpha --status active --reason "work started" --agent vera'


def test_foreign_lease_blocks_with_agent_and_is_noted_without(lp, env, capsys):
    set_status(env, "alpha", "active")
    set_lease(env, "alpha", agent="otto", workspace="w9")
    rc, data = nxt(lp, env, capsys, "alpha", "--agent", "vera")
    assert rc == 1 and data["ok"] is False and data["command"] is None
    assert [b["kind"] for b in data["blockers"]] == ["foreign-lease"]
    assert "otto" in data["reason"] and "foreign live lease" in data["reason"]
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 0 and any("leased by otto" in n for n in data["notes"])


def test_expired_lease_reads_as_unleased(lp, env, capsys):
    set_status(env, "alpha", "active")
    set_lease(env, "alpha", agent="otto", hours_ago=30, ttl=24)
    rc, data = nxt(lp, env, capsys, "alpha", "--agent", "vera")
    assert rc == 0 and data["command"].startswith("lean-pkg claim alpha")
    assert any("expired" in n for n in data["notes"])


def test_invalid_lease_blocks(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["lease"] = {"workspace": "w", "agent": "x", "claimed_at": "zzz",
                                         "ttl_hours": 24, "reason": ""}
    write_reg(env, reg)
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 1 and data["blockers"][0]["kind"] == "invalid-lease"
    assert data["command"].startswith("lean-pkg release alpha --force")


def test_missing_checkout_blocks_and_names_the_fix(lp, env, capsys, tmp_path):
    reg = read_reg(env)
    reg["packages"]["alpha"]["local_path"] = str(tmp_path / "gone")
    write_reg(env, reg)
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 1 and data["blockers"][0]["kind"] == "missing-checkout"
    assert "missing checkout" in data["reason"]
    assert data["command"].startswith("lean-pkg update alpha --path DIR") and "--dry-run" in data["command"]


def test_active_without_receipt_names_gate_check(lp, env, capsys):
    set_status(env, "alpha", "active")
    set_lease(env, "alpha", agent="vera")
    rc, data = nxt(lp, env, capsys, "alpha", "--agent", "vera")
    assert rc == 0 and data["command"] == "lean-pkg gate alpha --check --receipt R"
    assert "produce the lower-tier receipt" in data["reason"]


def test_active_with_receipt_moves_to_gate_pending(lp, env, capsys):
    set_status(env, "alpha", "active")
    set_lease(env, "alpha", agent="vera")
    rel = drop_receipt(env.repo_a, make_receipt(env.repo_a, job_id="slurm-77"))
    rc, data = nxt(lp, env, capsys, "alpha", "--agent", "vera")
    assert rc == 0 and data["state"]["receipt_candidate"]["path"] == rel
    assert data["command"] == ('lean-pkg update alpha --status gate-pending --reason '
                               '"check job slurm-77 submitted" --agent vera')


def test_gate_pending_without_receipt_points_to_gate_check(lp, env, capsys):
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 0 and data["command"] == "lean-pkg gate alpha --check --receipt R"


def test_gate_pending_with_receipt_recommends_the_gate_that_then_succeeds(lp, env, capsys):
    rel = drop_receipt(env.repo_a, make_receipt(env.repo_a))
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 0 and data["command"] == f"lean-pkg gate alpha --receipt {env.repo_a / rel}"
    assert "gate alpha --check --receipt" in data["reason"]
    argv = shlex.split(data["command"])[1:]
    assert lp.main(["--registry", str(env.registry), *argv]) == 0
    capsys.readouterr()
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_gate_pending_dirty_tree_blocks(lp, env, capsys):
    (env.repo_a / "lean-toolchain").write_text("leanprover/lean4:v4.99.0\n")
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 1 and data["blockers"][0]["kind"] == "dirty-tree"
    assert data["state"]["dirty"] == ["lean-toolchain"]


def test_gate_pending_invalid_receipt_blocks(lp, env, capsys):
    rel = drop_receipt(env.repo_a, make_receipt(env.repo_a, sorry_count=2))
    rc, data = nxt(lp, env, capsys, "alpha")
    assert rc == 1 and data["blockers"][0]["kind"] == "invalid-receipt"
    assert "sorry_count" in data["reason"] and data["command"] == f"lean-pkg gate alpha --check --receipt {env.repo_a / rel}"


def test_verified_private_with_comparator_goes_to_publish(lp, env, capsys):
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"] == "lean-pkg publish beta --dry-run"
    assert "promote beta" in data["reason"]
    assert data["state"]["gate_commit"] == data["state"]["comparator_commit"] == repo_head(env.repo_b)


def test_verified_private_attaches_a_comparator_receipt_on_disk(lp, env, capsys):
    reg = read_reg(env)
    comp_rel = reg["packages"]["beta"]["gate"]["comparator"]
    reg["packages"]["beta"]["gate"]["comparator"] = None
    write_reg(env, reg)
    rc, data = nxt(lp, env, capsys, "beta", "--agent", "vera")
    assert rc == 0 and data["command"] == f"lean-pkg comparator beta --receipt {env.repo_b / comp_rel} --agent vera"


def test_verified_private_without_comparator_receipt_runs_comparator_receipt(lp, env, capsys):
    reg = read_reg(env)
    comp_rel = reg["packages"]["beta"]["gate"]["comparator"]
    reg["packages"]["beta"]["gate"]["comparator"] = None
    write_reg(env, reg)
    (env.repo_b / comp_rel).unlink()
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"] == f"comparator-receipt --project {env.repo_b}"
    assert data["state"]["comparator_configs"] == ["comparator.json"]


def _gated_without_config(tmp_path, env, claims: bool):
    repo = make_repo(tmp_path / "gamma-repo")
    if claims:
        commit_claims(repo, text=_claims_for("gamma"))
    receipt = make_receipt(repo)
    rel = drop_receipt(repo, receipt, "seed.json")
    reg = read_reg(env)
    reg["packages"]["gamma"] = base_pkg("gamma", repo, "verified-private",
                                        gate={"receipt": rel, "comparator": None,
                                              "axioms_allowed": ["propext", "Quot.sound", "Classical.choice"]})
    write_reg(env, reg)
    return repo


def _claims_for(pkg: str) -> str:
    from .conftest import CLAIMS_YAML
    return CLAIMS_YAML.replace("package: beta", f"package: {pkg}").replace("id: beta/", f"id: {pkg}/")


def test_verified_private_without_config_scaffolds_from_claims(lp, env, capsys, tmp_path):
    _gated_without_config(tmp_path, env, claims=True)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "gamma"]) == 0
    capsys.readouterr()
    rc, data = nxt(lp, env, capsys, "gamma")
    assert rc == 0 and data["command"] == "lean-pkg comparator config --from-claims gamma"
    assert "re-gate" in data["reason"]


def test_verified_private_without_config_or_claims_syncs_claims(lp, env, capsys, tmp_path):
    _gated_without_config(tmp_path, env, claims=False)
    rc, data = nxt(lp, env, capsys, "gamma")
    assert rc == 0 and data["command"] == "lean-pkg claims sync gamma"


def test_verified_private_after_a_source_change_re_gates(lp, env, capsys):
    (env.repo_b / "New.lean").write_text("-- more\n")
    _git(env.repo_b, "add", "New.lean")
    _git(env.repo_b, "commit", "-m", "more")
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"] == "lean-pkg gate beta --check --regate --receipt R"   # #67
    assert "gate beta --receipt R --regate" in data["reason"]


def test_verified_private_unreadable_receipt_blocks(lp, env, capsys):
    (env.repo_b / ".lean-receipts" / "seed.json").write_text("{not json")
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 1 and data["blockers"][0]["kind"] == "unreadable-receipt"
    assert "unreadable receipt" in data["reason"]


def make_public(env, **kw):
    set_status(env, "beta", "public", visibility="public", **kw)


def test_public_untagged_tags_first(lp, env, capsys):
    make_public(env)
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"].startswith(f"git -C {env.repo_b} tag -a vX.Y.Z")


def test_public_tagged_checks_reservoir(lp, env, capsys):
    make_public(env)
    _git(env.repo_b, "tag", "v1.0.0")
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"] == "lean-pkg reservoir check beta"
    assert "reservoir-listed" in data["reason"]


def test_public_palomar_target_consent_then_prepare(lp, env, capsys):
    make_public(env, upstream_target="palomar",
                reservoir={"listed_at": "2026-10-01", "url": "https://reservoir.lean-lang.org/@example/beta"})
    _git(env.repo_b, "tag", "v1.0.0")
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"].startswith("lean-pkg record beta consent-recorded")
    set_status(env, "beta", "public", consent=[{"who": "Ada", "role": "owner", "pointer": "https://e.org/c"}])
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"] == "lean-pkg palomar prepare beta"


def test_public_mathlib_target_records_the_pr(lp, env, capsys):
    make_public(env, upstream_target="mathlib",
                reservoir={"listed_at": "2026-10-01", "url": "https://reservoir.lean-lang.org/@example/beta"})
    _git(env.repo_b, "tag", "v1.0.0")
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"].startswith("lean-pkg record beta mathlib-pr-opened")


def test_public_with_upstream_evidence_moves_to_upstreamed(lp, env, capsys):
    make_public(env, history=[{"ts": "2026-10-01T00:00:00+00:00", "event": "mathlib-pr-merged",
                               "url": "https://github.com/leanprover-community/mathlib4/pull/1"}])
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"].startswith("lean-pkg update beta --status upstreamed")


def test_public_with_nothing_left(lp, env, capsys):
    make_public(env, reservoir={"listed_at": "2026-10-01", "url": "https://reservoir.lean-lang.org/@example/beta"})
    _git(env.repo_b, "tag", "v1.0.0")
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["ok"] is True and data["command"] is None


@pytest.mark.parametrize("status", ["upstreamed", "archived"])
def test_terminal_statuses_have_nothing_to_do(lp, env, capsys, status):
    set_status(env, "beta", status)
    rc, data = nxt(lp, env, capsys, "beta")
    assert rc == 0 and data["command"] is None and "nothing" in data["reason"]
