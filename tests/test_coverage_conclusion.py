#!/usr/bin/env python3
"""Proposal 0010: `conclusion` (full | weaker | special-case) and `conclusion_scope` on the paper rows of
coverage/1. coverage check refuses a value outside the enum, a weaker or special-case row without a scope, a
scope without such a conclusion, either key on an outside row and on a library row; the page never calls a
weaker or special-case result the bare "Verified.", carries a Conclusion line with the scope and counts them
in the header; a row without the key (or with conclusion: full) renders byte-identically."""

from __future__ import annotations

import json

import yaml

from .conftest import read_reg, write_reg
from .test_coverage_offline import offline, package
from .test_outputs_golden import _normalize as _norm
from .test_page import COVERAGE, LIBRARY, TWO_CLAIMS, _block, _refused, cov_check, mutate, page_json, prepare

SCOPE = "the modewise algebraic inequality only; the semigroup and nonlinear conclusions are not formalized"
SPECIAL = "the one-dimensional case only"
HEAD = "### Theorem 1: Main bound\n"


def _row(**keys) -> dict:
    row = json.loads(json.dumps(COVERAGE["rows"][0]))
    row.update(keys)
    return row


def _doc(**keys) -> dict:
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0].update(keys)
    return doc


def _errs(lp, env, capsys, tmp_path, *rows) -> list[str]:
    return _refused(lp, env, capsys, tmp_path, mutate(rows=list(rows)))


def _outputs(lp, env, capsys, tmp_path, tag: str) -> tuple[dict, dict[str, str]]:
    site, html = tmp_path / f"site-{tag}", tmp_path / f"v-{tag}.html"
    rc, a = page_json(lp, env, capsys, "--site", str(site), "--html", str(html))
    assert rc == 0, a.get("problems")
    return a, {"readme": a["markdown"], "verification": (site / "verification.md").read_text(),
               "coverage": (site / "coverage.md").read_text(), "html": html.read_text()}


# --- validation ---------------------------------------------------------------------------------

def test_conclusion_is_a_closed_enum(lp, env, capsys, tmp_path):
    prepare(env)
    for bad in ("partial", "Weaker", True, ["weaker"]):
        errs = _errs(lp, env, capsys, tmp_path, _row(conclusion=bad, conclusion_scope=SCOPE), COVERAGE["rows"][1])
        assert any("rows[0].conclusion: must be one of full, weaker, special-case" in e for e in errs), (bad, errs)


def test_a_weaker_or_special_case_row_needs_its_scope(lp, env, capsys, tmp_path):
    prepare(env)
    for c in ("weaker", "special-case"):
        errs = _errs(lp, env, capsys, tmp_path, _row(conclusion=c), COVERAGE["rows"][1])
        assert any(f"rows[0]: a row with conclusion: {c} names its conclusion_scope" in e for e in errs), errs
        for bad in ("", "two\nlines", "x" * 401, ["a list"]):
            errs = _errs(lp, env, capsys, tmp_path, _row(conclusion=c, conclusion_scope=bad), COVERAGE["rows"][1])
            assert any("rows[0].conclusion_scope: must be one line of at most 400" in e for e in errs), (bad, errs)
    errs = _errs(lp, env, capsys, tmp_path, _row(conclusion="weaker", conclusion_scope="TODO say what"),
                 COVERAGE["rows"][1])
    assert any("rows[0].conclusion_scope: carries the placeholder 'TODO'" in e for e in errs), errs


def test_a_scope_is_refused_without_a_weaker_or_special_case_conclusion(lp, env, capsys, tmp_path):
    prepare(env)
    for row in (_row(conclusion_scope=SCOPE), _row(conclusion="full", conclusion_scope=SCOPE)):
        errs = _errs(lp, env, capsys, tmp_path, row, COVERAGE["rows"][1])
        assert any("rows[0].conclusion_scope: only with conclusion: weaker or special-case" in e for e in errs), errs


def test_an_outside_row_carries_neither_key(lp, env, capsys, tmp_path):
    prepare(env)
    outside = COVERAGE["rows"][1]
    for keys in ({"conclusion": "weaker", "conclusion_scope": SCOPE}, {"conclusion": "full"},
                 {"conclusion_scope": SCOPE}):
        errs = _errs(lp, env, capsys, tmp_path, COVERAGE["rows"][0], dict(outside, **keys))
        assert any("rows[1]: an outside row proves nothing, so it has no conclusion: drop "
                   + ", ".join(keys) in e for e in errs), (keys, errs)


def test_a_library_row_refuses_both_keys(lp, env, capsys, tmp_path):
    prepare(env, LIBRARY)
    for keys in ({"conclusion": "weaker", "conclusion_scope": SCOPE}, {"conclusion": "full"}):
        doc = json.loads(json.dumps(LIBRARY))
        doc["rows"][0].update(keys)
        errs = _refused(lp, env, capsys, tmp_path, yaml.safe_dump(doc, sort_keys=False))
        assert any("a library row is a main result of the library" in e and "drop " + ", ".join(keys) in e
                   for e in errs), (keys, errs)


def test_the_conclusion_is_independent_of_the_class_and_counted_in_json(lp, env, capsys, tmp_path):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0].update(conclusion="weaker", conclusion_scope=SCOPE)          # same-assumptions + weaker
    doc["rows"].insert(1, {"paper": {"number": "Theorem 3", "title": "Side fact"}, "class": "inputs-supplied",
                           "inputs": ["the tail bound of Section 3"], "claims": ["beta/baz-true"],
                           "conclusion": "special-case", "conclusion_scope": SPECIAL})
    prepare(env, doc, extra=TWO_CLAIMS)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["ok"], data["errors"]
    assert data["conclusions"] == {"full": 0, "weaker": 1, "special-case": 1}
    prepare(env)                                                                # no key: every row full
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["conclusions"] == {"full": 1, "weaker": 0, "special-case": 0}
    prepare(env, LIBRARY)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["conclusions"] is None
    rc, data = cov_check(lp, env, capsys, "--file", str(tmp_path / "missing.yaml"))
    assert rc == 1 and data["conclusions"] is None


def test_the_offline_check_validates_and_counts_the_keys(lp, capsys, tmp_path):
    d = package(tmp_path, _doc(conclusion="special-case", conclusion_scope=SPECIAL))
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and data["conclusions"] == {"full": 0, "weaker": 0, "special-case": 1}
    d = package(tmp_path, _doc(conclusion="weaker"))
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and data["conclusions"] is None
    assert any("names its conclusion_scope" in e for e in data["errors"])


# --- rendering ----------------------------------------------------------------------------------

def test_a_weaker_row_is_never_the_bare_verified_and_shows_its_scope(lp, env, capsys, tmp_path):
    prepare(env, _doc(conclusion="weaker", conclusion_scope=SCOPE))
    a, out = _outputs(lp, env, capsys, tmp_path, "weaker")
    assert a["verified_rows"] == 1 and a["verified_conclusions"] == {"weaker": 1, "special-case": 0}
    block = _block(out["readme"], "Theorem 1: Main bound")
    assert block.startswith("\n**Verified: a weaker statement.** Lean accepts the proof, and lean-pkg's record")
    assert f"\n\n**Conclusion.** A weaker statement than the paper's: {SCOPE}\n\n**In the paper.**" in block
    for name, text in out.items():
        assert "**Verified.**" not in text and "<strong>Verified.</strong>" not in text, name
    assert f"**Conclusion.** A weaker statement than the paper's: {SCOPE}" in out["verification"]
    assert "<strong>Verified: a weaker statement.</strong>" in out["html"]
    assert f"<strong>Conclusion.</strong> A weaker statement than the paper's: {SCOPE}" in out["html"]
    assert f"Same assumptions<br>Weaker conclusion: {SCOPE}</td>" in out["html"]
    assert f"| Same assumptions<br>Weaker conclusion: {SCOPE} |" in out["coverage"]
    assert ("- Verified: 1 of the 1 result(s) in Lean; of the verified, 1 prove(s) a weaker statement than the "
            "paper's.\n") in out["readme"]


def test_a_special_case_row_is_never_the_bare_verified_and_shows_its_scope(lp, env, capsys, tmp_path):
    prepare(env, _doc(conclusion="special-case", conclusion_scope=SPECIAL))
    a, out = _outputs(lp, env, capsys, tmp_path, "special")
    assert a["verified_conclusions"] == {"weaker": 0, "special-case": 1}
    block = _block(out["readme"], "Theorem 1: Main bound")
    assert block.startswith("\n**Verified: a special case.** Lean accepts the proof")
    assert f"**Conclusion.** A special case of the paper's result: {SPECIAL}" in block
    for name, text in out.items():
        assert "**Verified.**" not in text and "<strong>Verified.</strong>" not in text, name
    assert f"| Same assumptions<br>Special case: {SPECIAL} |" in out["coverage"]
    assert ("- Verified: 1 of the 1 result(s) in Lean; of the verified, 1 prove(s) a special case of the paper's "
            "result.\n") in out["readme"]


def test_a_row_without_the_key_or_with_full_renders_byte_identically(lp, env, capsys, tmp_path):
    # each fixture is its own commit, so the commit and digest strings are normalized (as the output goldens do)
    prepare(env)
    base, base_out = _outputs(lp, env, capsys, tmp_path, "base")
    assert "verified_conclusions" not in base and "Conclusion." not in base_out["readme"]
    prepare(env, _doc(conclusion="full"))
    full, full_out = _outputs(lp, env, capsys, tmp_path, "full")
    for name in base_out:
        assert _norm(full_out[name]) == _norm(base_out[name]), name     # every output, byte for byte
    assert full.pop("verified_conclusions") == {"weaker": 0, "special-case": 0}
    assert _norm(json.dumps(full, sort_keys=True), tmp_path).replace("-full", "-base") == \
        _norm(json.dumps(base, sort_keys=True), tmp_path)
    # the weaker render differs from the same fixture without the key only by its three proposal-0010 pieces
    prepare(env, _doc(conclusion="weaker", conclusion_scope=SCOPE))
    _w, weak_out = _outputs(lp, env, capsys, tmp_path, "weak")
    undone = (weak_out["readme"].replace("**Verified: a weaker statement.**", "**Verified.**")
              .replace(f"**Conclusion.** A weaker statement than the paper's: {SCOPE}\n\n", "")
              .replace("; of the verified, 1 prove(s) a weaker statement than the paper's", ""))
    assert _norm(undone) == _norm(base_out["readme"])


def test_the_header_counts_weaker_and_special_case_results(lp, env, capsys, tmp_path):
    doc = _doc(conclusion="weaker", conclusion_scope=SCOPE)
    doc["rows"].insert(1, {"paper": {"number": "Theorem 3", "title": "Side fact"}, "class": "same-assumptions",
                           "claims": ["beta/baz-true"], "conclusion": "special-case", "conclusion_scope": SPECIAL})
    prepare(env, doc, extra=TWO_CLAIMS)
    a, out = _outputs(lp, env, capsys, tmp_path, "both")
    assert a["verified_rows"] == 2 and a["verified_conclusions"] == {"weaker": 1, "special-case": 1}
    assert ("- Verified: 2 of the 2 result(s) in Lean (for 1 of them a second checker also compared the "
            "statement); of the verified, 1 prove(s) a weaker statement than the paper's and 1 a special case of "
            "it.\n") in out["readme"]
    assert _block(out["readme"], "Theorem 3: Side fact").startswith("\n**Verified: a special case.**")


def test_an_unverified_weaker_row_keeps_its_status_and_shows_the_scope(lp, env, capsys, tmp_path):
    prepare(env, _doc(conclusion="weaker", conclusion_scope=SCOPE))
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = None             # promote stage needs the comparator tier
    write_reg(env, reg)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and a["verified_rows"] == 0 and a["verified_conclusions"] == {"weaker": 0, "special-case": 0}
    block = _block(a["markdown"], "Theorem 1: Main bound")
    assert block.startswith("\n**Not yet verified.**") and "Verified: a weaker" not in block
    assert f"**Conclusion.** A weaker statement than the paper's: {SCOPE}" in block
    assert "- Verified: 0 of the 1 result(s) in Lean.\n" in a["markdown"]
