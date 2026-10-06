#!/usr/bin/env python3
"""index check: re-derive every entry from bytes at the pin; exit 0/1/2; --json reasons."""

from __future__ import annotations

import json
import subprocess

import yaml

from .conftest import (CLI, _git, index_add, make_index_package, repo_head,
                       subprocess_env)

PAL = {"id": "PALOMAR-2026-10-05-000008", "version": 1}


def added(lp, tmp_path, **kw):
    pkg = make_index_package(tmp_path, **kw)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    return pkg, root


def check(lp, root, *argv):
    return lp.main(["index", "check", "--index", str(root), *argv])


def edit_entry(root, name, fn):
    f = root / name / "entry.yaml"
    e = yaml.safe_load(f.read_text())
    fn(e)
    f.write_text(yaml.safe_dump(e, sort_keys=False))


def test_check_clone_passes_for_a_fresh_entry(lp, tmp_path, git_url_map, capsys):
    pkg, root = added(lp, tmp_path)
    git_url_map(pkg.url, pkg.repo)
    assert check(lp, root, "--clone") == 0
    out = capsys.readouterr().out
    assert "PASS demo (comparator, legacy)" in out and "all pass" in out


def test_check_passes_when_head_moved_on_past_the_pin(lp, tmp_path, git_url_map):
    """Review focus: the pin is the claim; later source commits do not matter."""
    pkg, root = added(lp, tmp_path)
    (pkg.repo / "Demo" / "Main.lean").write_text("theorem foo_le_bar : True := by trivial\n")
    _git(pkg.repo, "commit", "-am", "later work")
    assert repo_head(pkg.repo) != pkg.pin
    git_url_map(pkg.url, pkg.repo)
    assert check(lp, root, "--clone") == 0


def test_check_repo_dir_mode(lp, tmp_path):
    pkg, root = added(lp, tmp_path)
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 0


def test_check_repo_dir_needs_exactly_one_name(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    assert check(lp, root, "--repo-dir", str(pkg.repo)) == 2
    assert "exactly one NAME" in capsys.readouterr().err


def test_static_check_without_clone_says_so(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    assert check(lp, root) == 0
    assert "static check only" in capsys.readouterr().out


def test_check_refuses_unknown_key(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.update(maintainer="someone"))
    assert check(lp, root) == 1
    assert "unknown key" in capsys.readouterr().out


def test_check_refuses_trust_above_evidence_static(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path, comparator=False)
    edit_entry(root, "demo", lambda e: e.update(trust="comparator"))
    assert check(lp, root) == 1
    assert "exceeds its evidence" in capsys.readouterr().out


def test_check_detects_a_tampered_receipt_copy(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    f = root / "demo" / "receipts" / pkg.lower_rel.split("/")[-1]
    data = json.loads(f.read_text())
    data["job_id"] = "slurm-99999"
    f.write_text(json.dumps(data, indent=2) + "\n")
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 1
    assert "differs from the bytes" in capsys.readouterr().out


def test_check_detects_a_tampered_log_in_the_repository(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    comp = json.loads((pkg.repo / pkg.comp_rel).read_text())
    log = pkg.repo / comp["configs"][0]["log"]
    log.write_text(log.read_text() + "tampered\n")
    _git(pkg.repo, "commit", "-am", "tamper with the log")
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 1
    out = capsys.readouterr().out
    assert "log_sha256" in out and "exceeds its evidence" in out


def test_check_detects_toolchain_and_manifest_drift(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.update(lean_toolchain="leanprover/lean4:v4.99.0",
                                                 mathlib_rev="f" * 40))
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 1
    out = capsys.readouterr().out
    assert "lean-toolchain at the pin" in out and "lake-manifest.json at the pin" in out


def test_check_detects_a_missing_tag(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e["pin"].update(tag="v9.9.9"))
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 1
    assert "v9.9.9" in capsys.readouterr().out


def test_check_accepts_a_tag_on_the_evidence_commit(lp, tmp_path):
    pkg, root = added(lp, tmp_path)
    _git(pkg.repo, "tag", "v1.0.0", pkg.evidence)
    edit_entry(root, "demo", lambda e: e["pin"].update(tag="v1.0.0"))
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 0


def test_check_refuses_duplicate_entries(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    import shutil
    shutil.copytree(root / "demo", root / "demo-copy")
    edit_entry(root, "demo-copy", lambda e: e.update(name="demo-copy"))
    assert check(lp, root) == 1
    assert "duplicate" in capsys.readouterr().out


def test_check_network_unreachable_is_skipped_not_failed(lp, idx, tmp_path, monkeypatch, capsys):
    """Review focus: unreachable Palomar/Reservoir is `skipped`, never failed or passed."""
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.update(
        trust="palomar", palomar=[PAL], reservoir="https://reservoir.lean-lang.org/@example/demo"))
    monkeypatch.setattr(idx, "palomar_entry_fetch", lambda i, v: None)
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda o, n: None)
    capsys.readouterr()
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo), "--json") == 0
    rep = json.loads(capsys.readouterr().out)["entries"][0]
    assert rep["network"] == {"palomar": "skipped", "reservoir": "skipped"}
    assert rep["ok"] is True and any("skipped" in n for n in rep["notes"])


def test_check_network_verified_and_failed(lp, idx, tmp_path, monkeypatch, capsys):
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.update(
        trust="palomar", palomar=[PAL], reservoir="https://reservoir.lean-lang.org/@example/demo"))
    calls = []
    monkeypatch.setattr(idx, "palomar_entry_fetch", lambda i, v: calls.append((i, v)) or
                        {"source": {"commit": pkg.pin, "repository_url": pkg.url}})
    monkeypatch.setattr(lp, "reservoir_listing_status", lambda o, n: 404)
    capsys.readouterr()
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo), "--json") == 1
    rep = json.loads(capsys.readouterr().out)["entries"][0]
    assert calls == [(PAL["id"], 1)]
    assert rep["network"] == {"palomar": "verified", "reservoir": "failed"}
    assert any("reservoir" in f and "404" in f for f in rep["failures"])


def test_check_json_shape(lp, tmp_path, git_url_map, capsys):
    pkg, root = added(lp, tmp_path)
    git_url_map(pkg.url, pkg.repo)
    capsys.readouterr()
    assert check(lp, root, "--clone", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] is True and p["mode"] == "bytes"
    r = p["entries"][0]
    assert set(r) >= {"name", "ok", "trust_stated", "trust_computed", "mechanism",
                      "failures", "notes", "network"}
    assert r["trust_computed"] == "comparator" and r["mechanism"] == "legacy"


def test_check_unknown_name_fails(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    assert check(lp, root, "nope") == 1
    assert "no entry 'nope'" in capsys.readouterr().out


def test_check_via_module_entry_point_exits_1_not_traceback(lp, tmp_path):
    """`python -m lean_pkg.cli` and lean_pkg.index share one Fail class."""
    pkg, root = added(lp, tmp_path)
    (root / "demo" / "entry.yaml").write_text("- not\n- a mapping\n")
    p = subprocess.run([*CLI, "index", "check", "--index", str(root)], env=subprocess_env(),
                       capture_output=True, text=True)
    assert p.returncode == 1, p.stderr
    assert "Traceback" not in p.stderr


def test_check_legacy_and_toolchain_mechanisms_both_validate(lp, tmp_path, git_url_map, capsys):
    tools = {"lean_toolchain": "leanprover/lean4:v4.32.0", "lake_sha256": "1" * 64,
             "leanexport_sha256": "2" * 64, "leanchecker_sha256": "3" * 64,
             "nanoda_bin_sha256": "4" * 64, "con-ron_sha256": "5" * 64}
    pkg = make_index_package(tmp_path, name="tc", schema="comparator-receipt/2",
                             mechanism="toolchain", tools=tools)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    git_url_map(pkg.url, pkg.repo)
    assert check(lp, root, "--clone") == 0
    assert "PASS tc (comparator, toolchain)" in capsys.readouterr().out


def test_check_notes_missing_consent_without_failing(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.update(consent={"recorded": False}))   # ruling Q3: add refuses this; check only notes it
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 0
    assert "consent not recorded" in capsys.readouterr().out
    edit_entry(root, "demo", lambda e: e.update(consent={"recorded": True}))
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 0
    assert "consent not recorded" not in capsys.readouterr().out


# --- review fixes (2026-10-06) ---------------------------------------------

def test_check_palomar_entry_bound_elsewhere_fails(lp, idx, tmp_path, monkeypatch, capsys):
    """Important: a Palomar id must name this repository at this pin."""
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.update(trust="palomar", palomar=[PAL]))
    monkeypatch.setattr(idx, "palomar_entry_fetch", lambda i, v: {"source": {"commit": "f" * 40,
                                                                             "repository_url": pkg.url}})
    capsys.readouterr()
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo), "--json") == 1
    rep = json.loads(capsys.readouterr().out)["entries"][0]
    assert rep["network"]["palomar"] == "failed" and any("ffffffffffff" in f for f in rep["failures"])


def test_check_one_name_ignores_an_unrelated_broken_entry(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    (root / "broken").mkdir()
    (root / "broken" / "entry.yaml").write_text("- not\n- a mapping\n")
    assert check(lp, root, "demo") == 0
    assert check(lp, root) == 1              # asked for everything: the broken one is reported


def test_render_fails_cleanly_on_a_malformed_entry(lp, tmp_path, capsys):
    pkg, root = added(lp, tmp_path)
    edit_entry(root, "demo", lambda e: e.pop("trust"))
    assert lp.main(["index", "render", "--index", str(root)]) == 1
    err = capsys.readouterr().err
    assert "trust" in err and "Traceback" not in err


def test_check_recomputes_trust_from_headline_coverage(lp, tmp_path, capsys):
    """An entry stating comparator trust for a headline the comparator never judged fails check."""
    pkg, root = added(lp, tmp_path)                         # headline Demo.foo_le_bar, judged
    edit_entry(root, "demo", lambda e: e["headline"].__setitem__(0, {"name": "Demo.unjudged",
                                                                    "file": "Demo/Main.lean",
                                                                    "summary": "Never judged."}))
    (pkg.repo / "Demo" / "Main.lean").write_text("theorem foo_le_bar : True := trivial\ntheorem unjudged : True := trivial\n")
    _git(pkg.repo, "commit", "-am", "later work")
    assert check(lp, root, "demo", "--repo-dir", str(pkg.repo)) == 1
    assert "not judged" in capsys.readouterr().out
