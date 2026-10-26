#!/usr/bin/env python3
"""#341: `formalization render NAME --write` refreshes a file that differs from the render only in derived
keys (a re-gate moved the receipts) without --force, and says which; a kept (hand-written) key or a YAML
comment that differs still needs --force, and the refusal names it. publish's stale-formalization line gives
the complete repair command, mentioning --force only when hand-written content differs."""

from __future__ import annotations

import json

import yaml

from .conftest import read_reg, write_reg
from .test_publish import labels, make_ready, pub


def fz(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), "formalization", "render", "beta", *argv])
    return rc, capsys.readouterr()


def first_write(lp, env, capsys):
    rc, _cap = fz(lp, env, capsys, "--write")
    assert rc == 0
    return env.repo_b / "formalization.yaml"


def move_license(env, spdx="MIT"):
    """A derived key changes: project.license follows the registry's license_spdx."""
    reg = read_reg(env)
    reg["packages"]["beta"]["license_spdx"] = spdx
    write_reg(env, reg)


def drop_sources(target):
    """A kept key differs from the render: without `sources` the renderer seeds a placeholder row."""
    doc = yaml.safe_load(target.read_text())
    doc.pop("sources")
    target.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_derived_only_difference_is_refreshed_without_force(lp, env, capsys):
    target = first_write(lp, env, capsys)
    move_license(env)
    rc, cap = fz(lp, env, capsys, "--write")
    assert rc == 0, cap.err
    assert "refreshed derived keys: project.license; every kept key unchanged" in cap.out
    assert yaml.safe_load(target.read_text())["project"]["license"] == "MIT"
    move_license(env, "Apache-2.0")
    rc, cap = fz(lp, env, capsys, "--write", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["written"] is True
    assert data["refreshed_derived"] == ["project.license"] and data["kept_overwritten"] == []


def test_derived_key_and_evidence_receipt_change_name_each_path(lp, env, capsys):
    target = first_write(lp, env, capsys)
    doc = yaml.safe_load(target.read_text())
    doc["status"]["evidence"]["gate_receipt"] = ".lean-receipts/older.json"   # what a re-gate leaves behind
    doc["status"]["sorry_count"] = 7
    target.write_text(yaml.safe_dump(doc, sort_keys=False))
    rc, cap = fz(lp, env, capsys, "--write", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["refreshed_derived"] == ["status.sorry_count", "status.evidence"]
    assert yaml.safe_load(target.read_text())["status"]["evidence"]["gate_receipt"] == ".lean-receipts/seed.json"


def test_kept_key_difference_is_refused_naming_it_and_the_file_is_unchanged(lp, env, capsys):
    target = first_write(lp, env, capsys)
    drop_sources(target)
    move_license(env)                                # a derived difference too: still refused
    before = target.read_text()
    rc, cap = fz(lp, env, capsys, "--write")
    assert rc == 1
    assert "differs from the render in kept key(s) sources" in cap.err
    assert "pass --force" in cap.err and "nothing written" in cap.err
    assert target.read_text() == before
    rc, cap = fz(lp, env, capsys, "--write", "--author", "Someone Else")
    assert rc == 1 and "kept key(s) " in cap.err and "project.authors" in cap.err and "sources" in cap.err
    assert target.read_text() == before


def test_force_overwrites_kept_keys_and_reports_them(lp, env, capsys):
    target = first_write(lp, env, capsys)
    drop_sources(target)
    move_license(env)
    rc, cap = fz(lp, env, capsys, "--write", "--force", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["written"] is True
    assert data["kept_overwritten"] == ["sources"] and data["refreshed_derived"] == ["project.license"]
    assert yaml.safe_load(target.read_text())["sources"]        # the seeded row
    move_license(env, "Apache-2.0")
    rc, cap = fz(lp, env, capsys, "--write", "--force")      # --force on a derived-only difference: fine
    assert rc == 0 and "every kept key unchanged" in cap.out


def test_a_hand_written_comment_needs_force(lp, env, capsys):
    target = first_write(lp, env, capsys)
    move_license(env)
    text = target.read_text()
    lines = text.splitlines()
    at = next(i for i, x in enumerate(lines) if x.startswith("project:"))
    lines.insert(at + 1, "  # checked with the authors on 2026-10-01")
    target.write_text("\n".join(lines) + "\n")
    rc, cap = fz(lp, env, capsys, "--write")
    assert rc == 1 and f"YAML comment(s) at line(s) {at + 2}" in cap.err
    rc, cap = fz(lp, env, capsys, "--write", "--force")
    assert rc == 0 and "checked with the authors" not in target.read_text()


def test_identical_file_is_a_no_op_and_missing_file_is_written(lp, env, capsys):
    target = env.repo_b / "formalization.yaml"
    assert not target.exists()
    rc, cap = fz(lp, env, capsys, "--write", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["written"] is True and data["refreshed_derived"] == []
    assert "refreshed derived keys" not in cap.out
    before = target.stat().st_mtime_ns, target.read_text()
    rc, cap = fz(lp, env, capsys, "--write", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["unchanged"] is True and data["written"] is False
    assert data["refreshed_derived"] == [] and data["kept_overwritten"] == []
    assert (target.stat().st_mtime_ns, target.read_text()) == before


def test_publish_hint_derived_only_names_the_plain_write(lp, env, fake_gh, capsys):
    make_ready(lp, env, capsys)
    move_license(env, "Apache-2.0")
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    stale = [x for x in labels(json.loads(cap.out), "fail") if "is stale" in x]
    assert rc == 1 and len(stale) == 1
    line = stale[0]
    assert "only derived keys differ (project.license)" in line
    assert "`lean-pkg formalization render beta --write` refreshes them" in line
    assert "`lean-pkg gate beta --receipt R --refresh-formalization` after a new receipt" in line
    assert "--force" not in line


def test_publish_hint_kept_difference_names_force(lp, env, fake_gh, capsys):
    make_ready(lp, env, capsys)
    drop_sources(env.repo_b / "formalization.yaml")
    rc, cap = pub(lp, env, capsys, "--dry-run", "--json")
    stale = [x for x in labels(json.loads(cap.out), "fail") if "is stale" in x]
    assert rc == 1 and len(stale) == 1
    line = stale[0]
    assert "differs from the render in kept key(s) sources" in line
    assert "`lean-pkg formalization render beta --write --force`" in line
