#!/usr/bin/env python3
"""R11 accounts for supporting lemmas: a compared theorem that no row names passes `coverage check` when it is
the declaration of a claim claims.yaml marks internal or reusable (a helper the package declares as such needs
no row). It still fails when it is the declaration of no claim, or of an experimental or superseded claim (the
error names the claim and its status). Smoke configurations are unchanged."""

from __future__ import annotations

import json

import pytest

from lean_pkg import coverage as cov
from .conftest import CLAIMS_YAML, DEMO_LEAN
from .test_coverage_offline import CONFIG, offline, package, status

LEAN = DEMO_LEAN.replace("end Demo", "theorem helper : True := trivial\n\nend Demo")


def claims_with_helper(helper_status: str) -> str:
    extra = ("  superseded_by: beta/foo-le-bar\n" if helper_status == "superseded" else "  superseded_by: null\n")
    return CLAIMS_YAML + (
        "- id: beta/helper\n"
        "  decl: Demo.helper\n"
        "  kind: lemma\n"
        "  summary: A supporting lemma no row names.\n"
        "  file: Demo/Main.lean\n"
        f"  status: {helper_status}\n" + extra +
        "  citation: null\n"
        "  depends_on: []\n")


def config_with(*names: str) -> dict:
    cfg = json.loads(json.dumps(CONFIG))
    cfg["theorem_names"] = list(cfg["theorem_names"]) + list(names)
    return cfg


@pytest.mark.parametrize("helper_status", ["internal", "reusable"])
def test_a_compared_internal_or_reusable_claim_needs_no_row(lp, capsys, tmp_path, helper_status):
    d = package(tmp_path, claims=claims_with_helper(helper_status), config=config_with("BetaAudit.Demo.helper"),
                lean=LEAN)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert status(data, "comparator theorems") == "pass", data["errors"]
    assert rc == 0 and not any("Demo.helper" in e for e in data["errors"])
    detail = next(c["detail"] for c in data["checks"] if c["check"] == "comparator theorems")
    assert "each of 2 theorem(s)" in detail and "or of an internal or reusable claim (R11)" in detail


@pytest.mark.parametrize("helper_status", ["experimental", "superseded"])
def test_a_compared_experimental_or_superseded_claim_still_fails_and_says_why(lp, capsys, tmp_path,
                                                                            helper_status):
    d = package(tmp_path, claims=claims_with_helper(helper_status), config=config_with("BetaAudit.Demo.helper"),
                lean=LEAN)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    errs = [e for e in data["errors"] if "'BetaAudit.Demo.helper'" in e]
    assert rc == 1 and status(data, "comparator theorems") == "fail" and len(errs) == 1
    assert f"is the declaration of claim 'beta/helper', whose status is '{helper_status}'" in errs[0]
    assert "lean-pkg claims mark" in errs[0]


def test_a_compared_theorem_of_no_claim_still_fails(lp, capsys, tmp_path):
    d = package(tmp_path, claims=claims_with_helper("internal"), config=config_with("BetaAudit.Demo.orphan"),
                lean=LEAN)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    errs = [e for e in data["errors"] if "'BetaAudit.Demo.orphan'" in e]
    assert rc == 1 and len(errs) == 1
    assert "of no claim a row names and of no internal or reusable claim" in errs[0]


def test_an_internal_claim_counts_only_when_its_file_declares_it(lp, capsys, tmp_path):
    """The decl rule is the same as for a row's claim: a decl its file does not declare credits nothing."""
    d = package(tmp_path, claims=claims_with_helper("internal"), config=config_with("BetaAudit.Demo.helper"),
                lean=DEMO_LEAN)                     # Demo/Main.lean declares no Demo.helper
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and any("'BetaAudit.Demo.helper'" in e for e in data["errors"])


def test_an_unreadable_internal_claim_file_skips_the_check_offline(lp, capsys, tmp_path):
    claims = claims_with_helper("internal").replace("summary: A supporting lemma no row names.\n  file: "
                                                    "Demo/Main.lean", "summary: A supporting lemma no row "
                                                    "names.\n  file: Demo/Helpers.lean")
    d = package(tmp_path, claims=claims, config=config_with("BetaAudit.Demo.helper"), lean=LEAN)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and status(data, "comparator theorems") == "skipped"
    detail = next(c["detail"] for c in data["checks"] if c["check"] == "comparator theorems")
    assert "Demo/Helpers.lean" in detail


def _doc() -> dict:
    return {"rows": [{"claims": ["beta/foo-le-bar"]}]}


def _claims(helper_status: str) -> dict:
    return {"beta/foo-le-bar": {"decl": "Demo.foo_le_bar", "file": "Demo/Main.lean", "status": "reusable"},
            "beta/helper": {"decl": "Demo.helper", "file": "Demo/Main.lean", "status": helper_status}}


def test_a_smoke_configuration_is_unchanged():
    """A smoke check may compare an internal claim's theorem (it credits nothing), but never a row's claim."""
    read = {"Demo/Main.lean": LEAN}.get
    cfgs = [{"path": "smoke.json", "theorem_names": ["BetaAudit.Demo.helper"]}]
    assert cov.theorem_mapping_problems(_doc(), _claims("internal"), cfgs, {"smoke.json"}, read, "t") == []
    cfgs = [{"path": "smoke.json", "theorem_names": ["BetaAudit.Demo.foo_le_bar"]}]
    errs = cov.theorem_mapping_problems(_doc(), _claims("internal"), cfgs, {"smoke.json"}, read, "t")
    assert len(errs) == 1 and "marks as a smoke check" in errs[0]


def test_accounted_statuses_are_internal_and_reusable_only():
    from lean_pkg.claims import CLAIM_STATUSES
    assert set(cov.ACCOUNTED_STATUSES) == {"internal", "reusable"} <= set(CLAIM_STATUSES)
