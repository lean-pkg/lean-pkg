#!/usr/bin/env python3
"""index add: an entry built from a clone at the pin, receipts validated and copied verbatim."""

from __future__ import annotations

import json

import yaml

from .conftest import (_git, index_add, make_index_package, read_events, read_reg,
                       repo_head, write_reg)


def test_add_writes_entry_and_verbatim_receipts(lp, idx, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert idx.validate_entry(e, "demo") == []
    assert e["trust"] == "comparator"
    assert e["pin"] == {"commit": pkg.pin, "tag": None}
    assert e["repo"] == pkg.url and e["license_spdx"] == "MIT"
    assert e["lean_toolchain"] == (pkg.repo / "lean-toolchain").read_text().strip()
    assert e["mathlib_rev"] == "81a5d257c8e410db227a6665ed08f64fea08e997"
    assert e["receipts"] == {"lower": pkg.lower_rel, "comparator": pkg.comp_rel}
    for rel in (pkg.lower_rel, pkg.comp_rel):
        copy = root / "demo" / "receipts" / rel.split("/")[-1]
        assert copy.read_bytes() == (pkg.repo / rel).read_bytes()
    assert list(e) == list(idx.ENTRY_KEYS)
    assert "trust comparator" in capsys.readouterr().out


def test_add_lower_only_is_lower_tier(lp, tmp_path):
    pkg = make_index_package(tmp_path, comparator=False)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    assert yaml.safe_load((root / "demo" / "entry.yaml").read_text())["trust"] == "lower-tier"


def test_add_refuses_invalid_receipt_and_writes_nothing(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path, comparator=False)
    bad = json.loads((pkg.repo / pkg.lower_rel).read_text())
    bad["sorry_count"] = 1
    (pkg.repo / pkg.lower_rel).write_text(json.dumps(bad))
    _git(pkg.repo, "commit", "-am", "planted defect")
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 1
    assert "sorry_count" in capsys.readouterr().err
    assert not (root / "demo").exists()


def test_add_refuses_uncommitted_evidence(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path, comparator=False)
    _git(pkg.repo, "rm", "-q", "--cached", pkg.lower_rel)
    _git(pkg.repo, "commit", "-m", "untrack the receipt")
    _git(pkg.repo, "reset", "-q", "--hard", pkg.pin)          # no ref holds the evidence commit now
    _git(pkg.repo, "reflog", "expire", "--expire=now", "--all")
    (pkg.repo / ".lean-receipts").mkdir(exist_ok=True)
    (pkg.repo / pkg.lower_rel).write_text("{}")               # present on disk, never committed
    assert index_add(lp, tmp_path / "index", pkg) == 1
    assert "not committed" in capsys.readouterr().err


def test_add_refuses_source_change_after_pin(lp, tmp_path, capsys):
    """A receipt whose only descendant changes source is not evidence at any pin."""
    pkg = make_index_package(tmp_path, comparator=False)
    _git(pkg.repo, "reset", "-q", "--soft", pkg.pin)
    (pkg.repo / "Demo" / "Main.lean").write_text("theorem foo_le_bar : True := by trivial\n")
    _git(pkg.repo, "add", "-A")
    _git(pkg.repo, "commit", "-m", "evidence together with a source change")
    assert index_add(lp, tmp_path / "index", pkg) == 1
    assert "evidence-only" in capsys.readouterr().err


def test_add_refuses_duplicate_repo_and_commit(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    pkg.name = "demo-again"
    assert index_add(lp, root, pkg) == 1
    err = capsys.readouterr().err
    assert "duplicate" in err and "demo" in err
    assert not (root / "demo-again").exists()


def test_add_refuses_existing_name(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    assert index_add(lp, root, pkg) == 1
    assert "already exists" in capsys.readouterr().err


def test_add_refuses_non_https_repo(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    pkg.url = "/srv/git/demo.git"
    assert index_add(lp, tmp_path / "index", pkg) == 1
    assert "https" in capsys.readouterr().err


def test_add_converts_github_ssh_url(lp, idx, tmp_path):
    assert idx.https_repo("git@github.com:example/demo.git") == "https://github.com/example/demo"
    assert idx.https_repo("https://gitlab.example.org/g/demo.git") == "https://gitlab.example.org/g/demo"


def test_add_refuses_headline_not_in_file(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    rc = lp.main(["index", "add", "demo", "--index", str(tmp_path / "index"),
                  "--repo-dir", str(pkg.repo), "--repo", pkg.url, "--license", "MIT",
                  "--by", "maintainer", "--lower", pkg.lower_rel, "--consent-recorded",
                  "--headline", "Demo.no_such_theorem :: Demo/Main.lean :: Missing."])
    assert rc == 1
    assert "no_such_theorem" in capsys.readouterr().err


def test_add_refuses_license_mismatch(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    assert index_add(lp, tmp_path / "index", pkg, "--license", "Apache-2.0") == 1
    assert "Apache-2.0" in capsys.readouterr().err


def test_add_from_registry_never_writes_local_path(lp, env, tmp_path):
    """Review focus: a private local_path must never appear in entry.yaml."""
    pkg = make_index_package(tmp_path, comparator=False)
    reg = read_reg(env)
    reg["packages"]["demo"] = dict(reg["packages"]["beta"], name="demo",
                                   repo="git@github.com:example/demo.git",
                                   local_path=str(pkg.repo), license_spdx="MIT",
                                   consent=[{"who": "a", "role": "coauthor", "pointer": "p", "recorded_at": "t"}],
                                   gate={"receipt": pkg.lower_rel, "comparator": None,
                                         "axioms_allowed": ["propext", "Quot.sound",
                                                            "Classical.choice"]})
    write_reg(env, reg)
    root = tmp_path / "index"
    rc = lp.main(["--registry", str(env.registry), "index", "add", "demo", "--from-registry",
                  "--index", str(root), "--by", "maintainer",
                  "--headline", "Demo.foo_le_bar :: Demo/Main.lean :: A trivial demonstration theorem."])
    assert rc == 0
    text = (root / "demo" / "entry.yaml").read_text()
    assert "local_path" not in text and str(pkg.repo) not in text and str(tmp_path) not in text
    e = yaml.safe_load(text)
    assert e["repo"] == "https://github.com/example/demo" and e["trust"] == "lower-tier"
    ev = read_events(env)[-1]
    assert ev["event"] == "index-added" and ev["name"] == "demo" and ev["commit"] == pkg.pin


def test_add_without_registry_records_no_event(lp, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    pkg = make_index_package(tmp_path)
    assert index_add(lp, tmp_path / "index", pkg) == 0
    assert not (tmp_path / "lean-package-events.jsonl").exists()
    assert "no event log" in capsys.readouterr().out


def test_add_json_payload(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    assert index_add(lp, tmp_path / "index", pkg, "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] and p["event"] == "index-added" and p["trust"] == "comparator"
    assert p["mechanism"] == "legacy" and p["commit"] == pkg.pin and len(p["receipts"]) == 2


def test_add_does_not_touch_the_source_checkout(lp, tmp_path):
    pkg = make_index_package(tmp_path)
    head = repo_head(pkg.repo)
    status = _git(pkg.repo, "status", "--porcelain").stdout
    assert index_add(lp, tmp_path / "index", pkg) == 0
    assert repo_head(pkg.repo) == head and _git(pkg.repo, "status", "--porcelain").stdout == status


def test_index_without_action_is_a_usage_error(lp, capsys):
    assert lp.main(["index"]) == 2
    assert "add | check | render" in capsys.readouterr().err


def test_add_refuses_without_recorded_consent(lp, tmp_path, capsys):
    """Ruling Q3 (ADR-0070 D4): a public listing needs every coauthor's recorded consent."""
    pkg = make_index_package(tmp_path)
    rc = lp.main(["index", "add", "demo", "--index", str(tmp_path / "index"),
                  "--repo-dir", str(pkg.repo), "--repo", pkg.url, "--license", "MIT",
                  "--by", "maintainer", "--lower", pkg.lower_rel,
                  "--headline", "Demo.foo_le_bar :: Demo/Main.lean :: A trivial demonstration theorem."])
    assert rc == 1
    assert "consent" in capsys.readouterr().err
    assert not (tmp_path / "index" / "demo").exists()
