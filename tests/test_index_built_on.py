#!/usr/bin/env python3
"""index-entry/2 `built_on` (proposal 0007): optional credits, validated strictly, rendered, kept on re-pin."""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from .conftest import ROOT, MATHLIB_REV, index_add, make_index_package

DEP = {"name": "CoolLib", "url": "https://github.com/example/cool-lib", "authors": ["Ada Lovelace"],
       "license": "Apache-2.0", "pin": "a" * 40, "relation": "depends-on",
       "note": "Supplies the semigroup."}


def v2_entry(idx):
    """A valid index-entry/2: the repository's markovprocess-pilot entry, without its credits."""
    e = yaml.safe_load((ROOT / "index" / "markovprocess-pilot" / "entry.yaml").read_text())
    e.pop("built_on", None)
    return e


def test_entry_without_built_on_stays_valid(idx):
    e = v2_entry(idx)
    assert idx.validate_entry(e, "markovprocess-pilot") == []
    assert "built_on" not in yaml.safe_load(idx.dump_entry(e))


def test_built_on_accepted_and_dumped_after_formalization_yaml(idx):
    e = v2_entry(idx)
    e["built_on"] = [dict(reversed(list(DEP.items()))), {"name": "Other", "url": "https://gitlab.com/x/y",
                                                         "authors": ["A. B.", "C. D."]}]
    e["built_on"] = idx.normalize_built_on(e["built_on"])
    assert idx.validate_entry(e, "markovprocess-pilot") == []
    keys = list(yaml.safe_load(idx.dump_entry(e)))
    assert keys.index("built_on") == keys.index("formalization_yaml") + 1
    assert list(yaml.safe_load(idx.dump_entry(e))["built_on"][0]) == list(idx.BUILT_ON_KEYS)


@pytest.mark.parametrize("credits,needle", [
    ([], "non-empty list"),
    ("MarkovProcess", "non-empty list"),
    (["MarkovProcess"], "built_on[0] must be a mapping"),
    ([{**DEP, "homepage": "https://x.org/a"}], "unknown key(s) ['homepage']"),
    ([{k: v for k, v in DEP.items() if k != "authors"}], "missing key(s) ['authors']"),
    ([{k: v for k, v in DEP.items() if k != "url"}], "missing key(s) ['url']"),
    ([{**DEP, "name": " "}], "built_on[0].name"),
    ([{**DEP, "url": "http://github.com/example/cool-lib"}], "built_on[0].url"),
    ([{**DEP, "url": "https://user:token@github.com/example/demo"}], "built_on[0].url"),
    ([{**DEP, "authors": []}], "built_on[0].authors"),
    ([{**DEP, "authors": "Ada Lovelace"}], "built_on[0].authors"),
    ([{**DEP, "authors": ["Ada", ""]}], "built_on[0].authors"),
    ([{**DEP, "authors": ["Ada", 7]}], "built_on[0].authors"),
    ([{**DEP, "pin": "v1.0.0"}], "built_on[0].pin"),
    ([{**DEP, "relation": "inspired-by"}], "built_on[0].relation"),
    ([{**DEP, "license": None}], "must not be null"),
    ([{**DEP, "note": "two\nlines"}], "built_on[0].note"),
    ([DEP, dict(DEP)], "listed twice"),
    ([{**DEP, "note": "copied from /home/someone/src"}], "private local path"),
])
def test_built_on_rejects(idx, credits, needle):
    e = v2_entry(idx)
    e["built_on"] = copy.deepcopy(credits)
    errs = idx.validate_entry(e, "markovprocess-pilot")
    assert any(needle in x for x in errs), errs


def test_built_on_refused_in_index_entry_1(idx):
    e = yaml.safe_load((ROOT / "tests" / "fixtures" / "schemas" / "index-entry-1.yaml").read_text())
    e["built_on"] = [DEP]
    assert any("unknown key" in x and "built_on" in x for x in idx.validate_entry(e, "demo"))


def test_static_check_refuses_an_invalid_credit(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    f = root / "demo" / "entry.yaml"
    e = yaml.safe_load(f.read_text())
    e["built_on"] = [{**DEP, "authors": []}]
    f.write_text(yaml.safe_dump(e, sort_keys=False))
    assert lp.main(["index", "check", "--index", str(root)]) == 1
    assert "built_on[0].authors" in capsys.readouterr().out


def credits_file(tmp_path, credits, name="credits.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(credits, sort_keys=False))
    return str(p)


def test_add_built_on_file_and_render(lp, idx, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, [DEP])) == 0
    assert "built_on: 1 credit(s)" in capsys.readouterr().out
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert e["built_on"] == [DEP] and idx.validate_entry(e, "demo") == []
    text = idx.render_index(root)
    assert "| Built on |" in text
    assert "[CoolLib](https://github.com/example/cool-lib) by Ada Lovelace" in text
    assert "## Built on" in text
    assert ("| demo | [CoolLib](https://github.com/example/cool-lib) | Ada Lovelace | depends-on | "
            "Apache-2.0 | `aaaaaaaaaaaa` | Supplies the semigroup. |") in text


def test_render_without_credits_has_no_built_on_section(lp, idx, tmp_path):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    text = idx.render_index(root)
    assert "| Built on |" in text and "## Built on" not in text


def test_render_joins_several_authors(lp, idx, tmp_path):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    many = {**DEP, "authors": ["A", "B", "C"]}
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, [many])) == 0
    assert "by A, B and C" in idx.render_index(root)


def test_add_refuses_an_invalid_built_on_file(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, [{"name": "x"}])) == 1
    assert "missing key(s)" in capsys.readouterr().err
    assert not (root / "demo").exists()


def test_built_on_file_accepts_a_block_copied_from_an_entry(lp, idx, tmp_path):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, {"built_on": [DEP]})) == 0
    assert yaml.safe_load((root / "demo" / "entry.yaml").read_text())["built_on"] == [DEP]


def test_existing_entry_needs_replace(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    assert index_add(lp, root, pkg) == 1
    assert "--replace" in capsys.readouterr().err
    pkg2 = make_index_package(tmp_path, name="other")
    assert index_add(lp, root, pkg2, "--replace") == 1
    assert "no index entry 'other'" in capsys.readouterr().err


def test_replace_carries_built_on_over(lp, idx, tmp_path, capsys):
    """A re-pin with --replace keeps the credits; the replaced entry is not a duplicate of itself."""
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, [DEP])) == 0
    capsys.readouterr()
    assert index_add(lp, root, pkg, "--replace") == 0
    out = capsys.readouterr().out
    assert "carried over from the replaced entry: CoolLib" in out and "replaced the previous entry" in out
    e = yaml.safe_load((root / "demo" / "entry.yaml").read_text())
    assert e["built_on"] == [DEP] and idx.validate_entry(e, "demo") == []


def test_replace_with_a_file_overrides_and_empty_drops(lp, tmp_path, capsys):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, [DEP])) == 0
    other = {**DEP, "name": "Other", "relation": "adapted-from"}
    assert index_add(lp, root, pkg, "--replace", "--built-on-file",
                     credits_file(tmp_path, [other], "b.yaml")) == 0
    assert yaml.safe_load((root / "demo" / "entry.yaml").read_text())["built_on"] == [other]
    assert index_add(lp, root, pkg, "--replace", "--built-on-file", credits_file(tmp_path, [], "c.yaml")) == 0
    assert "built_on" not in yaml.safe_load((root / "demo" / "entry.yaml").read_text())


def test_replace_swaps_the_receipt_copies(lp, tmp_path):
    pkg = make_index_package(tmp_path)
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    stale = root / "demo" / "receipts" / "000000000000-old.json"
    stale.write_text("{}")
    assert index_add(lp, root, pkg, "--replace") == 0
    assert not stale.exists()
    assert sorted(p.name for p in (root / "demo" / "receipts").iterdir()) == sorted(
        r.split("/")[-1] for r in (pkg.lower_rel, pkg.comp_rel))


def manifest(url, rev):
    return json.dumps({"packages": [{"name": "mathlib", "rev": MATHLIB_REV},
                                    {"name": "CoolLib", "url": url, "rev": rev}]})


@pytest.mark.parametrize("credit,warn", [
    ({**DEP, "url": "https://github.com/example/cool-lib", "pin": "b" * 40}, "differs from lake-manifest"),
    ({**DEP, "url": "https://github.com/example/elsewhere"}, "is not a package in lake-manifest"),
    ({**DEP, "url": "https://github.com/example/elsewhere", "relation": "adapted-from"}, None),
    ({**DEP}, None),
])
def test_check_clone_compares_pins_with_the_manifest_and_only_warns(lp, tmp_path, capsys, credit, warn):
    pkg = make_index_package(tmp_path, extra_files={
        "lake-manifest.json": manifest("https://github.com/example/cool-lib.git", "a" * 40)})
    root = tmp_path / "index"
    assert index_add(lp, root, pkg, "--built-on-file", credits_file(tmp_path, [credit])) == 0
    capsys.readouterr()
    assert lp.main(["index", "check", "--index", str(root), "demo", "--repo", str(pkg.repo)]) == 0
    out = capsys.readouterr().out
    if warn:
        assert "warning: built_on[0] CoolLib" in out and warn in out
    else:
        assert "built_on" not in out


def test_repository_credits(idx):
    """The rendered index credits MarkovProcess for markovprocess-pilot."""
    e = yaml.safe_load((ROOT / "index" / "markovprocess-pilot" / "entry.yaml").read_text())
    (mp,) = e["built_on"]
    assert mp["url"] == "https://github.com/scottnarmstrong/MarkovProcess"
    assert mp["authors"] == ["Scott Armstrong"] and mp["relation"] == "depends-on"
    assert mp["pin"] == "666cda029098a1106914fc9fa7abd1727573284a"
    assert "[MarkovProcess](https://github.com/scottnarmstrong/MarkovProcess) by Scott Armstrong" in \
        (ROOT / "index" / "README.md").read_text()


# Proposal 0007, open question 2 (decided 2026-10-18): index check warns, never fails, on a non-Mathlib
# Lake dependency with no built_on credit.

MATHLIB_FAMILY = [
    {"name": "mathlib", "url": "https://github.com/leanprover-community/mathlib4", "rev": MATHLIB_REV,
     "inherited": False},
    {"name": "batteries", "url": "https://github.com/leanprover-community/batteries", "rev": "c" * 40,
     "inherited": True},
    {"name": "Qq", "url": "https://github.com/leanprover-community/quote4", "rev": "c" * 40, "inherited": True},
    {"name": "Cli", "url": "https://github.com/leanprover/lean4-cli", "rev": "c" * 40, "inherited": True},
    {"name": "someFutureMathlibDep", "url": "https://github.com/leanprover-community/future-dep",
     "rev": "c" * 40, "inherited": True},
]
COOL = {"name": "CoolLib", "url": "https://github.com/example/cool-lib.git", "rev": "a" * 40, "inherited": False}
TRANSITIVE = {"name": "Helper", "url": "https://github.com/example/helper", "rev": "d" * 40, "inherited": True}


def check_with_manifest(lp, tmp_path, capsys, packages, credits=None):
    pkg = make_index_package(tmp_path, extra_files={"lake-manifest.json": json.dumps({"packages": packages})})
    root = tmp_path / "index"
    extra = ("--built-on-file", credits_file(tmp_path, credits)) if credits else ()
    assert index_add(lp, root, pkg, *extra) == 0
    capsys.readouterr()
    rc = lp.main(["index", "check", "--index", str(root), "demo", "--repo", str(pkg.repo)])
    out = capsys.readouterr().out
    assert lp.main(["index", "check", "--index", str(root), "demo", "--repo", str(pkg.repo), "--json"]) == rc
    payload = json.loads(capsys.readouterr().out)
    return rc, out, payload


def test_uncredited_lake_dependency_warns_without_changing_the_verdict(lp, tmp_path, capsys):
    rc, out, payload = check_with_manifest(lp, tmp_path, capsys, MATHLIB_FAMILY + [COOL, TRANSITIVE])
    assert rc == 0 and payload["ok"] is True and payload["entries"][0]["ok"] is True
    assert out.count("has no built_on credit") == 2
    assert "warning: Lake dependency CoolLib (https://github.com/example/cool-lib.git)" in out
    assert "warning: Lake dependency Helper" in out
    assert payload["entries"][0]["uncredited_dependencies"] == [
        {"name": "CoolLib", "url": "https://github.com/example/cool-lib.git"},
        {"name": "Helper", "url": "https://github.com/example/helper"}]


@pytest.mark.parametrize("credit", [
    {**DEP},                                                         # url without .git, manifest with
    {**DEP, "url": "https://github.com/example/cool-lib.git"},       # url with .git
    {**DEP, "url": "https://github.com/example/moved"},              # name only
])
def test_credited_lake_dependency_does_not_warn(lp, tmp_path, capsys, credit):
    rc, out, payload = check_with_manifest(lp, tmp_path, capsys, MATHLIB_FAMILY + [COOL], [credit])
    assert rc == 0 and "has no built_on credit" not in out
    assert payload["entries"][0]["uncredited_dependencies"] == []


def test_mathlib_and_its_dependencies_do_not_warn(lp, tmp_path, capsys):
    """Mathlib, the packages it pulls in (listed or inherited from its organizations) and Lean core."""
    direct_batteries = {"name": "batteries", "url": "https://github.com/leanprover-community/batteries.git",
                        "rev": "c" * 40, "inherited": False}
    core = {"name": "lean4", "url": "https://github.com/leanprover/lean4", "rev": "e" * 40}
    rc, out, payload = check_with_manifest(lp, tmp_path, capsys, MATHLIB_FAMILY + [direct_batteries, core])
    assert rc == 0 and "has no built_on credit" not in out
    assert payload["entries"][0]["uncredited_dependencies"] == []


def test_direct_dependency_from_a_mathlib_org_still_warns(lp, tmp_path, capsys):
    """Only an inherited package is taken to come through Mathlib; a direct require is the package's own."""
    direct = {"name": "duper", "url": "https://github.com/leanprover-community/duper", "rev": "f" * 40,
              "inherited": False}
    rc, out, payload = check_with_manifest(lp, tmp_path, capsys, MATHLIB_FAMILY + [direct])
    assert rc == 0 and [d["name"] for d in payload["entries"][0]["uncredited_dependencies"]] == ["duper"]


def test_static_check_has_an_empty_uncredited_list(lp, idx, tmp_path, capsys):
    pkg = make_index_package(tmp_path, extra_files={
        "lake-manifest.json": json.dumps({"packages": MATHLIB_FAMILY + [COOL]})})
    root = tmp_path / "index"
    assert index_add(lp, root, pkg) == 0
    capsys.readouterr()
    assert lp.main(["index", "check", "--index", str(root), "demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"][0]["uncredited_dependencies"] == []
