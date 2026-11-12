#!/usr/bin/env python3
"""Proposal 0015: `x-` extension keys in coverage/1, at the top level and on rows (paper and library). lean-pkg
checks their shape (name, value type, size, nesting, how many) and never reads, shows or judges their values:
no judged check sees them, the page never shows them, `coverage check` names them after its verdict and in
`--json` `extension_keys`, and the writers of coverage.yaml keep them. Every other unknown key is refused as
before."""

from __future__ import annotations

import json

import yaml

from lean_pkg import coverage as cov
from .conftest import _git
from .test_coverage_offline import offline, package, status
from .test_page import COVERAGE, LIBRARY, cov_check, prepare, run as page_run

TEX = "the bound holds for $t \\le 1$ only"
SECRET = "EXTENSION-VALUE-NEVER-SHOWN"


def with_ext(base: dict | None = None, **row0) -> dict:
    doc = json.loads(json.dumps(base or COVERAGE))
    doc["x-appendix"] = {"chapter": 7, "labels": ["a", "b"]}
    doc["rows"][0]["x-appendix-scope-tex"] = TEX
    doc["rows"][0].update(row0)
    return doc


def errors_of(doc) -> list[str]:
    return cov.validate(doc, "beta")


# --- the shape rules -------------------------------------------------------------------------------------------

def test_extension_keys_at_the_top_level_and_on_rows_are_accepted():
    assert errors_of(with_ext()) == []
    lib = json.loads(json.dumps(LIBRARY))
    lib["x-site"] = "kept"
    lib["rows"][0]["x-book-label"] = "thm:main"
    assert errors_of(lib) == []


def test_a_misspelled_real_key_is_still_refused():
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0]["conclusion_scop"] = "a typo"
    assert any("rows[0]: unknown key(s) conclusion_scop" in e for e in errors_of(doc))


def test_an_extension_key_elsewhere_is_refused_naming_where_they_are_allowed():
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0]["paper"]["x-tex"] = "x"
    doc["manuscript"]["x-note"] = "y"
    errs = errors_of(doc)
    assert any(e.startswith("rows[0].paper: extension keys (x-...) are allowed only at the top level and on rows: "
                            "x-tex") for e in errs)
    assert any(e.startswith("manuscript: extension keys (x-...) are allowed only") for e in errs)


def test_the_name_rule():
    for bad in ("x-Upper", "x-", "x-_lead", "x-" + "a" * 63):
        errs = errors_of(with_ext(**{bad: "v"}))
        assert any(e.startswith(f"rows[0].{bad}: an extension key is x- followed by") for e in errs), bad
    assert errors_of(with_ext(**{"x-a_b-1": "v", "x-" + "a" * 62: "v"})) == []


def test_the_value_rules():
    errs = errors_of(with_ext(**{"x-big": "z" * 4001}))
    assert any(e.startswith("rows[0].x-big: 4003 bytes as JSON; at most 4000") for e in errs)
    assert errors_of(with_ext(**{"x-fits": "z" * 3998})) == []                     # 4000 bytes with the quotes
    errs = errors_of(with_ext(**{"x-deep": [[["three"]]]}))
    assert any(e == "rows[0].x-deep: nested 3 levels; at most 2" for e in errs)
    assert errors_of(with_ext(**{"x-two": {"a": [1, None, True, 2.5]}})) == []
    errs = errors_of(with_ext(**{"x-intkeys": {1: "a"}}))
    assert any(e.startswith("rows[0].x-intkeys: must be a string, number, boolean or null") for e in errs)


def test_at_most_16_extension_keys_on_one_object():
    keys = {f"x-k{i}": i for i in range(16)}
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0].update(keys)
    assert errors_of(doc) == []
    doc["rows"][0]["x-k16"] = 16
    assert any(e == "rows[0]: 17 extension keys (x-...); at most 16 on one object" for e in errors_of(doc))


def test_extension_values_are_outside_every_judged_check():
    """Placeholder text and a multi-line, over-long value: no problem in an extension value."""
    long = "FILL-ME " + "w" * 600 + "\nsecond line TODO"
    doc = with_ext(**{"x-draft": long})
    doc["x-top"] = "TBD"
    assert errors_of(doc) == [] and cov.placeholder_problems(doc) == []
    doc["summary"] = "FILL-ME"                                         # the real keys are still judged
    assert any("summary: carries the placeholder" in e for e in errors_of(doc))


# --- what coverage check reports --------------------------------------------------------------------------------

def test_offline_check_lists_the_keys_and_names_them_after_the_verdict(lp, capsys, tmp_path):
    doc = with_ext()
    doc["rows"][1]["x-appendix-scope-text"] = "a typo of the intended key"
    d = package(tmp_path, coverage=doc)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and data["ok"] and status(data, "schema") == "pass"
    assert data["extension_keys"] == ["<root>.x-appendix", "rows[0].x-appendix-scope-tex",
                                      "rows[1].x-appendix-scope-text"]
    rc, out, _err = offline(lp, capsys, tmp_path, d, as_json=False)
    lines = out.splitlines()
    assert rc == 0 and lines[0].startswith(f"{d / 'coverage.yaml'}: coverage: OK")
    assert lines[1] == ("  extension keys (not checked, never shown): x-appendix at the top level; "
                        "x-appendix-scope-tex on 1 row(s); x-appendix-scope-text on 1 row(s)")
    assert lines[-1] == lines[0]


def test_without_extension_keys_the_output_is_unchanged(lp, capsys, tmp_path):
    d = package(tmp_path)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and data["extension_keys"] == []
    _rc, out, _err = offline(lp, capsys, tmp_path, d, as_json=False)
    assert "extension keys" not in out


def test_registered_check_and_the_page_never_show_a_value(lp, env, capsys):
    doc = with_ext(**{"x-secret": SECRET})
    prepare(env, doc)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["ok"], data["errors"]
    assert data["extension_keys"] == ["<root>.x-appendix", "rows[0].x-appendix-scope-tex", "rows[0].x-secret"]
    rc, out, _err = page_run(lp, env, capsys, "coverage", "check", "beta")
    assert rc == 0 and out.splitlines()[1].startswith("  extension keys (not checked, never shown): x-appendix at")
    rc, out, err = page_run(lp, env, capsys, "page", "beta")
    assert rc == 0, err
    assert SECRET not in out and "x-secret" not in out and "appendix" not in out and TEX not in out


def test_a_library_row_extension_key_passes_the_registered_check(lp, env, capsys):
    lib = json.loads(json.dumps(LIBRARY))
    lib["rows"][0]["x-book-label"] = "thm:main"
    prepare(env, lib)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["extension_keys"] == ["rows[0].x-book-label"]


# --- the writers keep them --------------------------------------------------------------------------------------

def test_formalization_anchor_edit_keeps_extension_lines_byte_for_byte():
    from lean_pkg import anchor
    doc = with_ext()
    doc["manuscript"]["version"] = "unposted"
    text = "# a comment kept as is\n" + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    out = anchor.coverage_edit(text, "arXiv" + ":2501.01234v2", "0" * 64)
    ext_lines = [x for x in text.splitlines() if "x-appendix" in x or x.startswith("  - a") or "chapter" in x]
    assert ext_lines and all(x in out.splitlines() for x in ext_lines)
    assert yaml.safe_load(out)["rows"][0]["x-appendix-scope-tex"] == TEX


def test_claims_rebase_keeps_extension_keys_and_values(lp, env, tmp_path, capsys):
    from .test_claims_rebase import COMMIT, rebase_setup, run as rebase_run
    d = rebase_setup(env, tmp_path)
    path = env.repo_b / "coverage.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["x-appendix"] = {"chapter": 7}
    doc["rows"][0]["x-appendix-scope-tex"] = TEX                        # carried (unchanged label)
    doc["rows"][2]["x-appendix-scope-tex"] = "left as it was"           # a row that needs review
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    _git(env.repo_b, "commit", "-q", "-am", "extension keys")
    out = tmp_path / "out"
    _rc, p = rebase_run(lp, env, d, "--from-aux", str(d / "old.aux"), "--to-commit", COMMIT, out=out, capsys=capsys)
    new = yaml.safe_load((out / "coverage.yaml").read_text())
    assert new["x-appendix"] == {"chapter": 7}
    assert new["rows"][0]["x-appendix-scope-tex"] == TEX and new["rows"][0]["paper"]["number"] == "Theorem 2.1"
    assert new["rows"][2]["x-appendix-scope-tex"] == "left as it was"
    assert p["proposed_errors"] == []
