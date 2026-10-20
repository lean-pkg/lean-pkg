#!/usr/bin/env python3
"""Smoke comparator configurations (lean-pkg-signoff.json `configs[<path>].smoke`): a configuration declared
a smoke check is exempt from the coverage rule that every compared theorem is the declaration of a claim a
row names, is shown on the page as a smoke check (never as a result), and never counts toward any claim
being verified. It still has to pass: it is in the receipt like any other configuration."""

from __future__ import annotations

import hashlib
import json

import pytest

from lean_pkg import holes

from .conftest import _git, read_reg, write_comparator_log
from .test_page import page_json, prepare, run

SMOKE_CFG = "comparator/config-smoke.json"
SMOKE_THM = "two_mul_le"
REASON = "a one-line sanity theorem that tests the comparator set-up"


def cfg_text(theorems) -> str:
    return json.dumps({"challenge_module": "Smoke.Challenge", "solution_module": "Smoke.Solution",
                       "theorem_names": list(theorems), "definition_names": [],
                       "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
                       "enable_nanoda": True}, indent=2) + "\n"


def sidecar_text(marks: dict[str, str]) -> str:
    return json.dumps({"schema": holes.SIDECAR_SCHEMA,
                       "configs": {rel: {"smoke": {"reason": why}} for rel, why in marks.items()}}, indent=2) + "\n"


def prepare_smoke(env, *, mark: bool = True) -> str:
    """beta made page-ready with a second configuration, comparator/config-smoke.json (one sanity theorem no
    claim names), committed before the receipts, and (mark) the sidecar declaring it a smoke check. The
    comparator receipt lists both configurations, both passing."""
    extra = {SMOKE_CFG: cfg_text([SMOKE_THM])}
    if mark:
        extra[holes.SIDECAR] = sidecar_text({SMOKE_CFG: REASON})
    head = prepare(env, extra=extra)
    repo = env.repo_b
    rel = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    comp = json.loads((repo / rel).read_text())
    blob = (repo / SMOKE_CFG).read_bytes()
    log = write_comparator_log(repo, head, SMOKE_CFG, passing=True)
    comp["configs"].append({**comp["configs"][0], "path": SMOKE_CFG, "sha256": hashlib.sha256(blob).hexdigest(),
                            "challenge_module": "Smoke.Challenge", "solution_module": "Smoke.Solution",
                            "theorem_names": [SMOKE_THM], "log": log,
                            "log_sha256": hashlib.sha256((repo / log).read_bytes()).hexdigest()})
    comp["configs"].sort(key=lambda c: c["path"])
    (repo / rel).write_text(json.dumps(comp, indent=2))
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence: both configurations")
    return head


def coverage(lp, env, capsys) -> dict:
    rc, out, _err = run(lp, env, capsys, "--json", "coverage", "check", "beta")
    data = json.loads(out)
    assert rc == (0 if data["ok"] else 1)
    return data


def ledger(lp, env, capsys) -> dict:
    _rc, out, _err = run(lp, env, capsys, "--json", "claims", "ledger", "beta")
    return json.loads(out)


# --- the sidecar key --------------------------------------------------------------------------------

def test_the_smoke_mark_is_strict():
    doc = holes.parse_sidecar(sidecar_text({SMOKE_CFG: REASON}).encode())
    assert holes.smoke_paths(doc) == {SMOKE_CFG: REASON}
    assert holes.smoke_paths(holes.parse_sidecar(None)) == {}
    for bad in ({"reason": ""}, {"reason": "  "}, {}, {"reason": REASON, "by": "x"}, True, "yes"):
        raw = json.dumps({"schema": holes.SIDECAR_SCHEMA, "configs": {SMOKE_CFG: {"smoke": bad}}}).encode()
        with pytest.raises(ValueError, match=r"\.smoke: must be an object with exactly the key reason"):
            holes.parse_sidecar(raw)


# --- coverage: the exemption, and only for a declared smoke check -------------------------------------

def test_a_marked_smoke_configuration_passes_coverage(lp, env, capsys):
    prepare_smoke(env)
    data = coverage(lp, env, capsys)
    assert data["ok"] and data["errors"] == []
    led = ledger(lp, env, capsys)
    assert led["package_status"] == "ready"                 # the receipt with both configurations validates


def test_the_same_theorem_unmarked_still_fails_coverage(lp, env, capsys):
    prepare_smoke(env, mark=False)
    errs = coverage(lp, env, capsys)["errors"]
    assert len(errs) == 1 and f"'{SMOKE_THM}' ({SMOKE_CFG}" in errs[0] and "no claim a row names" in errs[0]


def test_a_mark_committed_after_the_receipt_is_read_at_head(lp, env, capsys):
    prepare_smoke(env, mark=False)
    (env.repo_b / holes.SIDECAR).write_text(sidecar_text({SMOKE_CFG: REASON}))
    _git(env.repo_b, "add", holes.SIDECAR)
    _git(env.repo_b, "commit", "-q", "-m", "declare the smoke check")
    assert coverage(lp, env, capsys)["errors"] == []


# --- a smoke check verifies nothing -----------------------------------------------------------------

def test_a_smoke_marked_configuration_cannot_verify_a_claim(lp, env, capsys):
    """The env fixture's only configuration, comparator.json, judges the claim's theorem; marked as a smoke
    check, it no longer does: the claim is open, the page says not verified, and coverage refuses the mark,
    since a configuration that judges a row's claim is no smoke check."""
    prepare(env)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and a["ledger_status"] == "ready" and a["verified_rows"] == 1      # unmarked: verified
    prepare(env, extra={holes.SIDECAR: sidecar_text({"comparator.json": REASON})})
    led = ledger(lp, env, capsys)
    row = led["claims"][0]
    assert led["package_status"] == "open" and row["status"] == "open"
    assert [d["code"] for d in row["diagnostics"]] == ["no-comparator-config"]
    assert "only a smoke configuration names it" in row["diagnostics"][0]["detail"]
    errs = coverage(lp, env, capsys)["errors"]
    assert len(errs) == 1 and "marks as a smoke check" in errs[0] and "'beta/foo-le-bar'" in errs[0]


def test_a_judged_as_naming_a_smoke_theorem_credits_nothing(lp, env, capsys):
    prepare_smoke(env)
    claims = (env.repo_b / "claims.yaml").read_text().replace(
        "  kind: theorem\n", f"  kind: theorem\n  judged_as: {SMOKE_THM}\n", 1)
    (env.repo_b / "claims.yaml").write_text(claims)
    from lean_pkg import comparator_config as cc
    from lean_pkg import claims as claims_mod
    doc = claims_mod.load_claims_text(claims, "claims.yaml")
    cfgs = {"comparator.json": (env.repo_b / "comparator.json").read_bytes(),
            SMOKE_CFG: (env.repo_b / SMOKE_CFG).read_bytes()}
    sidecar = holes.parse_sidecar((env.repo_b / holes.SIDECAR).read_bytes())
    cls = cc._classify(doc["claims"], cfgs, {"Demo.foo_le_bar"}, sidecar)
    assert cls["covered"] == [] and [c["id"] for c in cls["not_covered"]] == ["beta/foo-le-bar"]
    assert any("a theorem of a smoke configuration only" in p for p in cls["judged_as_problems"])
    del sidecar["configs"][SMOKE_CFG]                        # the same name, unmarked, credits the claim
    assert [c["id"] for c in cc._classify(doc["claims"], cfgs, {"Demo.foo_le_bar"}, sidecar)["covered"]] == \
        ["beta/foo-le-bar"]


# --- the page shows it as a smoke check -------------------------------------------------------------

def test_the_page_labels_a_smoke_check_and_never_as_a_result(lp, env, capsys, tmp_path):
    prepare_smoke(env)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and a["verified_rows"] == 1
    assert "2 configuration(s), 2 theorem(s) compared" in md        # what the receipt holds, as before
    assert "For 1 statement(s), a second checker" in md             # results only
    assert (f"- Smoke checks (not results): `{SMOKE_THM}` in `{SMOKE_CFG}` ({REASON}). Each passed the "
            f"comparison") in md
    results = md.split("## The paper's results in Lean", 1)[1].split("## Not formalized", 1)[0]
    assert SMOKE_THM not in results
    rc, _a = page_json(lp, env, capsys, "--html", str(tmp_path / "page.html"))
    assert rc == 0 and "Smoke checks (not results)" in (tmp_path / "page.html").read_text()


def test_formalization_main_results_leave_a_smoke_check_out(env):
    from lean_pkg import formalization
    prepare_smoke(env)
    warnings: list[str] = []
    out = formalization.main_results(env.repo_b, warnings)
    assert [r["declaration"] for r in out] == ["BetaAudit.Demo.foo_le_bar"]
    assert any(SMOKE_CFG in w and "smoke check" in w for w in warnings)


# --- claims mark ------------------------------------------------------------------------------------

def test_claims_mark_refuses_a_smoke_configuration_for_reusable(lp, env, capsys):
    from .test_claims_mark import mark, setup
    rel = setup(env)
    (env.repo_b / holes.SIDECAR).write_text(sidecar_text({rel: REASON}))
    rc = lp.main(["--registry", str(env.registry), "claims", "mark", "beta", "--from-comparator", rel,
                  "--status", "reusable"])
    assert rc == 1 and "marks this configuration as a smoke check" in capsys.readouterr().err
    rc, data = mark(lp, env, capsys, "--status", "internal")        # a status that credits nothing: allowed
    assert rc == 0 and data["status"] == "internal"
    (env.repo_b / holes.SIDECAR).unlink()                            # unmarked: the same config marks claims
    rc, data = mark(lp, env, capsys)
    assert rc == 0 and {m["id"] for m in data["matched"]} == {"beta/foo-le-bar", "beta/mass-le"}
