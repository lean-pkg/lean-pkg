#!/usr/bin/env python3
"""index-entry/2: claims[] derived from the headline at `index add`; checked, rendered, searchable."""

from __future__ import annotations

import json

import yaml

from .conftest import ROOT, index_add, make_index_package

V2 = ROOT / "tests" / "fixtures" / "schemas" / "index-entry-2.yaml"
V1 = ROOT / "tests" / "fixtures" / "schemas" / "index-entry-1.yaml"


def test_v2_fixture_is_valid_and_round_trips(idx):
    text = V2.read_text()
    e = yaml.safe_load(text)
    assert idx.validate_entry(e, "demo") == []
    assert idx.dump_entry(e) == text
    assert list(e) == list(idx.ENTRY_KEYS) and list(e).index("claims") == list(e).index("headline") + 1


def test_v1_entries_still_validate(idx):
    assert idx.validate_entry(yaml.safe_load(V1.read_text()), "demo") == []
    assert idx.SUPPORTED_ENTRY_SCHEMAS == ("index-entry/1", "index-entry/2")


def test_add_writes_index_entry_2_with_claims_derived_from_the_headline(lp, tmp_path):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert e["schema"] == "index-entry/2"
    assert e["claims"] == [{"id": "demo/demo-foo-le-bar", "decl": "Demo.foo_le_bar", "kind": "theorem",
                            "summary": "A trivial demonstration theorem.", "judged_as": "foo_le_bar",
                            "trust": "comparator"}]


def test_lower_tier_entry_claims_carry_lower_tier(lp, tmp_path):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path, comparator=False)) == 0
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert [c["trust"] for c in e["claims"]] == ["lower-tier"]


def test_a_hand_typed_claim_is_refused(idx):
    e = yaml.safe_load(V2.read_text())
    e["claims"][0]["trust"] = "palomar"
    assert any("derived from the headline" in x for x in idx.validate_entry(e, "demo"))
    e = yaml.safe_load(V2.read_text())
    e["claims"].append(dict(e["claims"][0], id="demo/extra"))
    assert any("derived from the headline" in x for x in idx.validate_entry(e, "demo"))


def test_claim_keys_are_exact(idx):
    e = yaml.safe_load(V2.read_text())
    e["claims"][0]["aliases"] = ["foo"]
    assert any("claims[0]: unknown key" in x for x in idx.validate_entry(e, "demo"))
    e = yaml.safe_load(V2.read_text())
    e["claims"][0]["id"] = "Demo Foo"
    assert any("claims[0].id" in x for x in idx.validate_entry(e, "demo"))


def test_v1_entry_with_claims_key_is_refused(idx):
    e = yaml.safe_load(V1.read_text())
    e["claims"] = []
    assert any("unknown key" in x and "claims" in x for x in idx.validate_entry(e, "demo"))


def test_check_passes_v1_and_v2_side_by_side(lp, idx, tmp_path, capsys):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    e = yaml.safe_load(V1.read_text())                   # a lower-tier index-entry/1 next to it
    e.update(name="legacy", repo="https://github.com/example/legacy", trust="lower-tier")
    e["receipts"]["comparator"] = None
    (root / "legacy" / "receipts").mkdir(parents=True)
    (root / "legacy" / "entry.yaml").write_text(idx.dump_entry(e))
    (root / "legacy" / "receipts" / e["receipts"]["lower"].split("/")[-1]).write_text(
        json.dumps({"commit": e["pin"]["commit"]}))
    capsys.readouterr()
    assert lp.main(["index", "check", "--index", str(root)]) == 0
    out = capsys.readouterr().out
    assert "PASS demo (comparator)" in out and "PASS legacy (lower-tier)" in out


def test_check_clone_passes_a_v2_entry(lp, tmp_path, git_url_map, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    git_url_map(pkg.url, pkg.repo)
    assert lp.main(["index", "check", "--index", str(root), "--clone"]) == 0
    assert "PASS demo (comparator, legacy)" in capsys.readouterr().out


def test_check_fails_when_claims_drift_from_the_headline(lp, idx, tmp_path, capsys):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    f = root / "demo" / "entry.yaml"
    e = yaml.safe_load(f.read_text())
    e["claims"][0]["summary"] = "A different sentence."
    f.write_text(idx.dump_entry(e))
    assert lp.main(["index", "check", "--index", str(root)]) == 1
    assert "derived from the headline" in capsys.readouterr().out


def test_render_lists_claims_only_for_v2_entries(lp, idx, tmp_path):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    text = idx.render_index(root)
    assert "## Claims" in text
    assert "| `demo/demo-foo-le-bar` | `Demo.foo_le_bar` | theorem | comparator |" in text
    real = idx.render_index(ROOT / "index")
    assert ("## Claims" in real) == any(
        "claims:" in (ROOT / "index" / n / "entry.yaml").read_text() for n in idx.entry_names(ROOT / "index"))


def test_search_reads_claims_of_a_v2_entry(lp, idx, tmp_path, capsys):
    root = tmp_path / "index"
    assert index_add(lp, root, make_index_package(tmp_path)) == 0
    f = root / "demo" / "entry.yaml"
    e = yaml.safe_load(f.read_text())
    e["claims"][0]["id"] = "demo/renamed-claim"          # search reads claims[], not the headline
    f.write_text(idx.dump_entry(e))
    capsys.readouterr()
    assert lp.main(["--registry", str(tmp_path / "none.yaml"), "search", "trivial demonstration",
                    "--offline", "--index", str(root), "--json"]) == 0
    hits = json.loads(capsys.readouterr().out)["sources"][1]["hits"]
    assert [h["id"] for h in hits] == ["demo/renamed-claim"]


def test_add_refuses_a_local_path_in_a_headline_summary(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    argv = ["index", "add", "demo", "--index", str(root), "--repo-dir", str(pkg.repo), "--repo", pkg.url,
            "--license", "MIT", "--by", "maintainer", "--consent-recorded", "--allow-unpushed",
            "--headline", "Demo.foo_le_bar :: Demo/Main.lean :: Proved in /home/someone/demo. :: foo_le_bar",
            "--lower", pkg.lower_rel, "--comparator", pkg.comp_rel]
    assert lp.main(argv) == 1
    assert "private local path" in capsys.readouterr().err
    assert not (root / "demo").exists()


def _legacy_entry(idx, tmp_path, name="legacy"):
    root = tmp_path / "index"
    e = yaml.safe_load(V1.read_text())
    e.update(name=name, repo=f"https://github.com/example/{name}", trust="lower-tier")
    e["receipts"]["comparator"] = None
    (root / name / "receipts").mkdir(parents=True)
    (root / name / "entry.yaml").write_text(idx.dump_entry(e))
    (root / name / "receipts" / e["receipts"]["lower"].split("/")[-1]).write_text(
        json.dumps({"commit": e["pin"]["commit"]}))
    return root, e


def test_migrate_rewrites_a_v1_entry_as_v2_with_derived_claims(lp, idx, tmp_path, capsys):
    root, e = _legacy_entry(idx, tmp_path)
    assert lp.main(["index", "migrate", "--index", str(root)]) == 0
    out = capsys.readouterr().out
    assert "legacy: index-entry/1 -> index-entry/2" in out
    m = yaml.safe_load((root / "legacy" / "entry.yaml").read_text())
    assert m["schema"] == "index-entry/2" and list(m) == list(idx.ENTRY_KEYS)
    assert m["claims"] == idx.index_claims("legacy", e["headline"], "lower-tier")
    assert {k: v for k, v in m.items() if k not in ("schema", "claims")} == \
        {k: v for k, v in e.items() if k != "schema"}
    assert idx.validate_entry(m, "legacy") == []
    assert lp.main(["index", "check", "--index", str(root)]) == 0


def test_migrate_is_idempotent_and_reports_current_entries(lp, idx, tmp_path, capsys):
    root, _ = _legacy_entry(idx, tmp_path)
    assert lp.main(["index", "migrate", "--index", str(root)]) == 0
    before = (root / "legacy" / "entry.yaml").read_text()
    capsys.readouterr()
    assert lp.main(["index", "migrate", "--index", str(root)]) == 0
    assert "legacy: already index-entry/2" in capsys.readouterr().out
    assert (root / "legacy" / "entry.yaml").read_text() == before


def test_migrate_check_mode_exits_1_and_writes_nothing(lp, idx, tmp_path, capsys):
    root, _ = _legacy_entry(idx, tmp_path)
    before = (root / "legacy" / "entry.yaml").read_text()
    assert lp.main(["index", "migrate", "--index", str(root), "--check"]) == 1
    assert "legacy: index-entry/1 (run `lean-pkg index migrate`)" in capsys.readouterr().out
    assert (root / "legacy" / "entry.yaml").read_text() == before
