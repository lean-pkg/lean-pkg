#!/usr/bin/env python3
"""#23.5: the evidence-only descendant rule admits a regenerated formalization.yaml (the recorded path)
whose only changes are derived keys; a changed kept key, any other path, or claims.yaml still refuse."""

from __future__ import annotations

import json

import yaml

from .conftest import _git, make_comparator_receipt, make_receipt, publish_evidence, read_reg, repo_head, write_reg

MIT = "MIT License\n\nPermission is hereby granted, free of charge, ...\n"
LAKEFILE = "import Lake\nopen Lake DSL\n\npackage «beta» where\n"


def fz_write(lp, env, *extra):
    assert lp.main(["--registry", str(env.registry), "formalization", "beta", "--write", *extra]) == 0


def gated_with_formalization(lp, env, capsys) -> str:
    """G: source + LICENSE + lakefile + a first formalization.yaml committed together; receipts bound
    to G recorded in the registry (untracked, as the gate leaves them). Returns G."""
    repo = env.repo_b
    (repo / "LICENSE").write_text(MIT)
    (repo / "lakefile.lean").write_text(LAKEFILE)
    reg = read_reg(env)
    pkg = reg["packages"]["beta"]
    pkg["license_spdx"] = "MIT"
    pkg["consent"] = [{"who": "Ada", "role": "owner", "pointer": "https://example.org/c"}]
    write_reg(env, reg)
    fz_write(lp, env, "--author", "Ada")
    _git(repo, "add", "LICENSE", "lakefile.lean", "formalization.yaml")
    _git(repo, "commit", "-q", "-m", "gated source")
    g = repo_head(repo)
    (repo / ".lean-receipts" / "g.json").write_text(json.dumps(make_receipt(repo), indent=2))
    comp_rel = f".lean-receipts/{g[:12]}-comparator.json"
    (repo / comp_rel).write_text(json.dumps(make_comparator_receipt(repo), indent=2))
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(receipt=".lean-receipts/g.json", comparator=comp_rel)
    write_reg(env, reg)
    capsys.readouterr()
    return g


def regenerate_and_commit(lp, env, capsys, edit=None) -> str:
    """H: the receipts plus formalization.yaml regenerated to cite them (and `edit`, if given)."""
    repo = env.repo_b
    fz_write(lp, env, "--force")
    if edit:
        edit(repo)
    _git(repo, "add", "-A")
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence + regenerated formalization.yaml")
    capsys.readouterr()
    return repo_head(repo)


def test_regenerated_derived_keys_are_evidence(lp, env, capsys):
    g = gated_with_formalization(lp, env, capsys)
    h = regenerate_and_commit(lp, env, capsys)
    changed = _git(env.repo_b, "diff", "--name-only", g, h).stdout.split()
    assert "formalization.yaml" in changed                      # the circularity: it cites the receipt
    doc = yaml.safe_load((env.repo_b / "formalization.yaml").read_text())
    assert doc["status"]["evidence"]["commit"] == g
    ok, note = lp.evidence_only_descendant(env.repo_b, g, h, "formalization.yaml")
    assert ok, note
    ok, note = lp.evidence_only_descendant(env.repo_b, g, h)     # without the recorded path: the old rule
    assert not ok and "formalization.yaml" in note


def test_promote_dry_run_is_green_on_such_a_descendant(lp, env, fake_gh, capsys):
    gated_with_formalization(lp, env, capsys)
    regenerate_and_commit(lp, env, capsys)
    publish_evidence(env.repo_b, stage=False)
    reg = read_reg(env)
    reg["packages"]["beta"]["formalization_yaml"] = "formalization.yaml"
    write_reg(env, reg)
    rc = lp.main(["--registry", str(env.registry), "promote", "beta", "--json"])
    data = json.loads(capsys.readouterr().out)
    fails = [c["label"] for c in data["checklist"] if c["status"] == "fail"]
    assert rc == 0 and not fails, fails
    assert any("gate receipt bound to HEAD" in c["label"] for c in data["checklist"])
    rc = lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt",
                  str(env.repo_b / reg["packages"]["beta"]["gate"]["comparator"]), "--check"])
    assert rc == 0, capsys.readouterr().err


def test_a_changed_kept_key_refuses_naming_it(lp, env, capsys):
    g = gated_with_formalization(lp, env, capsys)

    def edit(repo):
        p = repo / "formalization.yaml"
        doc = yaml.safe_load(p.read_text())
        doc["project"]["description"] = "a hand edit after the gate"
        p.write_text(yaml.safe_dump(doc, sort_keys=False))
    h = regenerate_and_commit(lp, env, capsys, edit)
    ok, note = lp.evidence_only_descendant(env.repo_b, g, h, "formalization.yaml")
    assert not ok and "kept (hand-authored) key(s) project.description" in note


def test_another_changed_file_still_refuses(lp, env, capsys):
    g = gated_with_formalization(lp, env, capsys)
    h = regenerate_and_commit(lp, env, capsys, lambda repo: (repo / "Notes.lean").write_text("-- x\n"))
    ok, note = lp.evidence_only_descendant(env.repo_b, g, h, "formalization.yaml")
    assert not ok and "Notes.lean" in note and "formalization.yaml" not in note.split("outside")[-1]


def test_claims_yaml_is_never_evidence(lp, env, capsys):
    g = gated_with_formalization(lp, env, capsys)
    h = regenerate_and_commit(lp, env, capsys, lambda repo: (repo / "claims.yaml").write_text("x: 1\n"))
    ok, note = lp.evidence_only_descendant(env.repo_b, g, h, "formalization.yaml")
    assert not ok and "claims.yaml" in note


def test_a_formalization_yaml_new_since_the_bound_commit_refuses(lp, env, capsys):
    repo = env.repo_b
    g = repo_head(repo)                                          # the fixture's bound commit: no yaml yet
    (repo / "formalization.yaml").write_text("version: v0.4\nproject: {name: beta}\n")
    _git(repo, "add", "formalization.yaml")
    _git(repo, "commit", "-q", "-m", "first formalization.yaml")
    ok, note = lp.evidence_only_descendant(repo, g, repo_head(repo), "formalization.yaml")
    assert not ok and "not tracked at" in note


def test_an_unrecorded_path_gets_no_exemption(lp, env, capsys):
    g = gated_with_formalization(lp, env, capsys)
    h = regenerate_and_commit(lp, env, capsys)
    ok, note = lp.evidence_only_descendant(env.repo_b, g, h, "other/formalization.yaml")
    assert not ok and "formalization.yaml" in note
