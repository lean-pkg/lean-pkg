#!/usr/bin/env python3
"""#40: evidence on disk that git ignores (a default `*.log` hides the comparator logs) is named,
with its ignore rule and the remedy, by publish --dry-run, promote, gate and comparator."""

from __future__ import annotations

import json

from .conftest import (_git, add_comparator_config, make_comparator_receipt, make_receipt,
                       repo_head, write_receipt)


def reg_args(env, *argv):
    return ["--registry", str(env.registry), *argv]


def add_stages(repo):
    d = repo / ".lean-receipts" / "comparator-stages"
    d.mkdir(parents=True, exist_ok=True)
    (d / "stages.json").write_text("{}\n")
    (d / "01-checkout.log").write_text("ok\n")


def logs_of(env):
    return sorted(str(p.relative_to(env.repo_b)) for p in (env.repo_b / ".lean-receipts").rglob("*.log"))


def test_publish_names_each_log_hidden_by_star_log_and_its_rule(lp, env, fake_gh, capsys):
    add_stages(env.repo_b)
    (env.repo_b / ".gitignore").write_text("*.log\n")
    rc = lp.main(reg_args(env, "publish", "beta", "--dry-run", "--json"))
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    ignored = data["ignored_evidence"]
    assert sorted(i["path"] for i in ignored) == logs_of(env)
    assert {i["rule"] for i in ignored} == {".gitignore:1:*.log"}
    warns = [c["label"] for c in data["checklist"] if c["status"] == "warn"
             and c["label"].startswith("evidence on disk is git-ignored")]
    assert len(warns) == 1
    for path in logs_of(env):
        assert f"{path} (.gitignore:1:*.log)" in warns[0]
    assert "`!**/.lean-receipts/**`" in warns[0] and "`git add -f PATH`" in warns[0]
    assert ".lean-receipts/seed.json" not in warns[0]           # not ignored: plain untracked
    fails = " | ".join(c["label"] for c in data["checklist"] if c["status"] == "fail")
    assert "(git-ignored by .gitignore:1:*.log)" in fails


def test_a_directory_rule_hides_receipts_too_and_promote_names_it(lp, env, fake_gh, capsys):
    add_stages(env.repo_b)
    (env.repo_b / ".gitignore").write_text("*.log\n.lean-receipts/\n")
    rc = lp.main(reg_args(env, "promote", "beta"))
    out = capsys.readouterr().out
    assert rc == 1 and "refusing: checklist has failures" in out
    rule = ".gitignore:2:.lean-receipts/"
    assert f"not committed: .lean-receipts/seed.json (git-ignored by {rule})" in out
    assert "`git add` skips git-ignored files; un-ignore it" in out
    assert "WARN  evidence on disk is git-ignored, so `git add` skips it: " in out
    assert f".lean-receipts/comparator-stages/stages.json ({rule})" in out
    rc = lp.main(reg_args(env, "promote", "beta", "--json"))
    data = json.loads(capsys.readouterr().out)
    paths = {i["path"] for i in data["ignored_evidence"]}
    assert ".lean-receipts/seed.json" in paths and ".lean-receipts/comparator-stages/stages.json" in paths
    assert all(i["rule"] == rule for i in data["ignored_evidence"])


def test_the_printed_remedy_clears_the_warning(lp, env, fake_gh, capsys):
    add_stages(env.repo_b)
    (env.repo_b / ".gitignore").write_text("*.log\n.lean-receipts/\n!.lean-receipts/\n!**/.lean-receipts/**\n")
    lp.main(reg_args(env, "publish", "beta", "--dry-run", "--json"))
    data = json.loads(capsys.readouterr().out)
    assert data["ignored_evidence"] == []
    assert not any("git-ignored" in c["label"] for c in data["checklist"])


def test_force_added_evidence_is_not_reported(lp, env, fake_gh, capsys):
    (env.repo_b / ".gitignore").write_text("*.log\n")
    _git(env.repo_b, "add", "-f", ".lean-receipts")
    _git(env.repo_b, "commit", "-q", "-m", "evidence")
    lp.main(reg_args(env, "promote", "beta", "--json"))
    data = json.loads(capsys.readouterr().out)
    assert data["ignored_evidence"] == []
    assert not any("git-ignored" in c["label"] for c in data["checklist"])


def test_a_public_package_gets_the_warning_too(lp, env, fake_gh, capsys):
    import yaml
    reg = yaml.safe_load(env.registry.read_text())
    reg["packages"]["beta"]["status"] = reg["packages"]["beta"]["visibility"] = "public"
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    (env.repo_b / ".gitignore").write_text("*.log\n")
    lp.main(reg_args(env, "publish", "beta", "--dry-run", "--json"))
    data = json.loads(capsys.readouterr().out)
    assert [i["path"] for i in data["ignored_evidence"]] == logs_of(env)


def test_gate_warns_when_the_receipt_it_copied_is_ignored(lp, env, tmp_path, capsys):
    (env.repo_a / ".gitignore").write_text(".lean-receipts/\n")
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(reg_args(env, "gate", "alpha", "--receipt", str(p), "--json")) == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    rel = data["receipt"]
    sealed = rel.replace(".json", "-verify.redacted.log")          # the sealed log gate copied beside it
    assert data["ignored_evidence"] == [{"path": rel, "rule": ".gitignore:1:.lean-receipts/"},
                                        {"path": sealed, "rule": ".gitignore:1:.lean-receipts/"}]
    assert (f"WARN: {rel} is git-ignored (.gitignore:1:.lean-receipts/): `git add` skips it and "
            f"`promote` needs it committed; un-ignore it") in cap.err


def test_gate_without_ignore_rules_reports_nothing(lp, env, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(reg_args(env, "gate", "alpha", "--receipt", str(p), "--json")) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["ignored_evidence"] == [] and "git-ignored" not in cap.err


def test_comparator_warns_when_its_log_is_ignored(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    (env.repo_a / ".gitignore").write_text("*.log\n")
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    assert lp.main(reg_args(env, "gate", "alpha", "--receipt", str(p))) == 0
    capsys.readouterr()
    cp = write_receipt(tmp_path, make_comparator_receipt(env.repo_a), name="comp.json")
    assert lp.main(reg_args(env, "comparator", "alpha", "--receipt", str(cp), "--json")) == 0
    cap = capsys.readouterr()
    log = f".lean-receipts/{repo_head(env.repo_a)[:12]}-comparator-comparator.log"
    sealed = f".lean-receipts/{repo_head(env.repo_a)[:12]}-slurm-12345-verify.redacted.log"   # the gate's
    assert json.loads(cap.out)["ignored_evidence"] == [{"path": log, "rule": ".gitignore:1:*.log"},
                                                       {"path": sealed, "rule": ".gitignore:1:*.log"}]
    assert f"WARN: {log} is git-ignored (.gitignore:1:*.log)" in cap.err
