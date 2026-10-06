#!/usr/bin/env python3
"""index-entry/1: keys in order, unknown keys refused, private paths refused, trust computed."""

from __future__ import annotations

import copy

import pytest
import yaml

from .conftest import ROOT

FIXTURE = ROOT / "tests" / "fixtures" / "schemas" / "index-entry-1.yaml"


def entry():
    return yaml.safe_load(FIXTURE.read_text())


def test_fixture_entry_is_valid(idx):
    assert idx.validate_entry(entry(), "demo") == []


def test_dump_keeps_schema_key_order_and_round_trips(idx):
    text = FIXTURE.read_text()
    e = entry()
    assert idx.dump_entry(e) == text
    shuffled = dict(reversed(list(e.items())))
    assert idx.dump_entry(shuffled) == text
    assert list(yaml.safe_load(idx.dump_entry(e))) == list(idx.ENTRY_KEYS)


def test_unknown_top_level_key_refused(idx):
    e = entry()
    e["local_path"] = "src/demo"
    errs = idx.validate_entry(e, "demo")
    assert any("unknown key" in x and "local_path" in x for x in errs)


@pytest.mark.parametrize("path,value,needle", [
    (("schema",), "index-entry/2", "schema"),
    (("name",), "other", "directory"),
    (("repo",), "git@github.com:example/demo.git", "https"),
    (("repo",), "https://user:token@github.com/example/demo", "https"),
    (("pin", "commit"), "abc", "pin.commit"),
    (("trust",), "gold", "trust"),
    (("receipts", "lower"), "/abs/receipt.json", "receipts.lower"),
    (("receipts", "lower"), "../x/receipt.json", "receipts.lower"),
    (("license_spdx",), "", "license_spdx"),
    (("palomar",), [{"id": "P-1", "version": 1}], "palomar[0].id"),
    (("palomar",), [{"id": "PALOMAR-2026-10-05-000008", "version": 0}], "version"),
    (("reservoir",), "https://example.invalid/x", "reservoir"),
    (("fields",), ["Probability Theory"], "fields"),
    (("consent",), {"recorded": True, "pointer": "consent/a.md"}, "consent"),
    (("consent", "recorded"), "yes", "consent.recorded"),
    (("added",), {"at": "2026-10-07"}, "added"),
    (("notes",), "see /home/someone/checkout", "private local path"),
    (("headline",), [], "headline"),
])
def test_bad_values_refused(idx, path, value, needle):
    e = entry()
    target = e
    for k in path[:-1]:
        target = target[k]
    target[path[-1]] = value
    errs = idx.validate_entry(e, "demo")
    assert errs and any(needle in x for x in errs), errs


def test_headline_summary_must_be_one_line(idx):
    e = entry()
    e["headline"][0]["summary"] = "First line.\nSecond line."
    assert any("one sentence" in x for x in idx.validate_entry(e, "demo"))


def test_missing_key_reported(idx):
    e = entry()
    del e["consent"]
    assert any("missing key" in x and "consent" in x for x in idx.validate_entry(e, "demo"))


@pytest.mark.parametrize("lower,comp,palomar,expected", [
    (False, False, [], None),
    (True, False, [], "lower-tier"),
    (True, True, [], "comparator"),
    (False, True, [], "comparator"),
    (False, False, [{"id": "PALOMAR-2026-10-05-000008", "version": 1}], "palomar"),
])
def test_trust_is_computed_from_evidence(idx, lower, comp, palomar, expected):
    assert idx.compute_trust(lower, comp, palomar) == expected


def test_comparator_mechanism(idx):
    assert idx.comparator_mechanism({"schema": "comparator-receipt/1"}) == "legacy"
    assert idx.comparator_mechanism({"schema": "comparator-receipt/2", "mechanism": "toolchain"}) == "toolchain"
    assert idx.comparator_mechanism(None) is None


def test_index_root_resolution(idx, tmp_path, monkeypatch):
    from types import SimpleNamespace
    assert idx.index_root(SimpleNamespace(index=str(tmp_path / "a"))) == tmp_path / "a"
    monkeypatch.setenv("LEAN_PKG_INDEX", str(tmp_path / "b"))
    assert idx.index_root(SimpleNamespace(index=None)) == tmp_path / "b"
    monkeypatch.delenv("LEAN_PKG_INDEX")
    monkeypatch.chdir(tmp_path)
    assert idx.index_root(SimpleNamespace(index=None)) == tmp_path / "index"


def test_entry_names_lists_only_directories_with_an_entry(idx, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "entry.yaml").write_text("schema: index-entry/1\n")
    (tmp_path / "b").mkdir()
    (tmp_path / "README.md").write_text("x\n")
    assert idx.entry_names(tmp_path) == ["a"]
    assert idx.entry_names(tmp_path / "missing") == []


def test_load_entry_refuses_non_mapping(idx, lp, tmp_path):
    f = tmp_path / "entry.yaml"
    f.write_text("- a\n- b\n")
    with pytest.raises(lp.Fail):
        idx.load_entry(f)
