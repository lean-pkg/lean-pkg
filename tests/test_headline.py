#!/usr/bin/env python3
"""The headline comparator configuration (headline.py): formalization.yaml status.main_results lists the
paper's main results (a headline configuration) while the receipt and the gate bind the full comparison.
Subset rule against the gate-bound comparator receipt, sign-off borrowing for definition holes, fallback
when nothing can judge it, `palomar check` gaps and the `palomar prepare` bundle.

Every registry here is a throwaway file under tmp_path; nothing touches the network."""

from __future__ import annotations

import hashlib
import importlib
import json

import pytest
import yaml

from .conftest import (put_receipt, _git, add_comparator_config, make_comparator_receipt, make_receipt, read_events,
                       read_reg, repo_head, write_reg)
from .test_palomar_check import CONFIG, files, make_pkg

MAIN = ("BetaAudit.Demo.foo_le_bar", "BetaAudit.A.one", "BetaAudit.A.two")
HEADLINE = "paper/headline.json"


def cli(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def write_cfg(repo, rel, names, *, challenge="Challenge", solution="Solution", holes=(), **extra):
    cfg = {"challenge_module": challenge, "solution_module": solution, "theorem_names": list(names),
           "definition_names": list(holes), "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
           "enable_nanoda": True, **extra}
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"add {rel}")


def rebind(env, configs) -> str:
    """Gate and comparator receipts bound to HEAD, the comparator one listing `configs`."""
    repo = env.repo_b
    head = repo_head(repo)
    put_receipt(repo / ".lean-receipts" / "seed.json", make_receipt(repo), indent=2)
    comp = make_comparator_receipt(repo, configs[0])
    for c in configs[1:]:
        comp["configs"] += make_comparator_receipt(repo, c)["configs"]
    rel = f".lean-receipts/{head[:12]}-comparator.json"
    (repo / rel).write_text(json.dumps(comp, indent=2))
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = rel
    write_reg(env, reg)
    return rel


def set_field(env, value):
    reg = read_reg(env)
    reg["packages"]["beta"]["headline_config"] = value
    write_reg(env, reg)


def main_three(env):
    add_comparator_config(env.repo_b, theorem_names=MAIN)        # comparator.json, now three theorems


def render(lp, env, capsys, *extra) -> tuple[dict, dict, str]:
    assert cli(lp, env, "formalization", "beta", "--json", *extra) == 0
    out, err = capsys.readouterr()
    payload = json.loads(out)
    return payload, yaml.safe_load(payload["yaml"]), err


def decls(doc) -> list[tuple[str, str]]:
    return [(r["comparator_config"], r["declaration"]) for r in doc["status"]["main_results"]]


# --- formalization ----------------------------------------------------------

def test_headline_subset_is_rendered_as_main_results(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one"])          # not a configuration path: not compared alone
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    payload, doc, err = render(lp, env, capsys)
    assert decls(doc) == [(HEADLINE, "BetaAudit.A.one")]
    entry = doc["status"]["main_results"][0]
    assert entry["file"] == "Solution.lean" and entry["challenge_file"] == "Challenge.lean"
    assert payload["main_results"] == 1
    assert payload["headline"] == {"config": HEADLINE, "source": "registry", "applied": True, "reason": None,
                                   "receipt": read_reg(env)["packages"]["beta"]["gate"]["comparator"],
                                   "theorems": 1}
    assert "not applied" not in err


def test_convention_headline_that_is_itself_receipted_covers_itself(lp, env, capsys):
    """The field layout: comparator.json (everything compared) and comparator.headline.json (the main
    results, own Challenge/Solution modules), both in the comparator receipt."""
    main_three(env)
    write_cfg(env.repo_b, "comparator.headline.json", ["BetaHeadline.h1", "BetaHeadline.h2"],
              challenge="HeadlineChallenge", solution="HeadlineSolution")
    rebind(env, ["comparator.headline.json", "comparator.json"])
    payload, doc, _err = render(lp, env, capsys)
    assert decls(doc) == [("comparator.headline.json", "BetaHeadline.h1"),
                          ("comparator.headline.json", "BetaHeadline.h2")]
    assert doc["status"]["main_results"][0]["file"] == "HeadlineSolution.lean"
    assert payload["headline"]["source"] == "convention" and payload["headline"]["applied"] is True


def test_headline_theorem_not_covered_by_the_gated_receipt_is_refused(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one", "BetaAudit.Missing.thm"])
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    assert cli(lp, env, "formalization", "beta") == 1
    err = capsys.readouterr().err
    assert "not a subset of the comparison the gate binds" in err
    assert "1 theorem name(s)" in err and "BetaAudit.Missing.thm" in err
    assert not (env.repo_b / "formalization.yaml").exists()


def test_same_names_in_other_modules_are_not_covered(lp, env, capsys):
    """A different module pair compares a different statement: names alone never cover."""
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one"], challenge="OtherChallenge")
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    assert cli(lp, env, "formalization", "beta") == 1
    assert "BetaAudit.A.one" in capsys.readouterr().err


def test_suffix_names_are_covered_by_the_exact_covers_rule(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["A.two", "Demo.foo_le_bar"])         # <Prefix>.<name> covers <name>
    write_cfg(env.repo_b, "paper/bad.json", ["Audit.A.two"])               # a shared tail never does
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    _payload, doc, _err = render(lp, env, capsys)
    assert [d for _c, d in decls(doc)] == ["A.two", "Demo.foo_le_bar"]
    set_field(env, "paper/bad.json")
    assert cli(lp, env, "formalization", "beta") == 1
    assert "Audit.A.two" in capsys.readouterr().err


def test_no_headline_keeps_the_old_behaviour(lp, env, capsys):
    main_three(env)
    add_comparator_config(env.repo_b, "comparator/config-second.json", theorem_names=("second_a",))
    rebind(env, ["comparator.json", "comparator/config-second.json"])
    payload, doc, _err = render(lp, env, capsys)
    assert decls(doc) == [("comparator.json", n) for n in MAIN] + [("comparator/config-second.json", "second_a")]
    assert payload["headline"] is None


def test_unjudged_headline_falls_back_with_a_warning(lp, env, capsys):
    """No receipt validates at HEAD (the headline was committed after it): never applied unjudged."""
    main_three(env)
    rebind(env, ["comparator.json"])
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one"])           # a non-evidence commit after the receipt
    set_field(env, HEADLINE)
    payload, doc, err = render(lp, env, capsys)
    assert decls(doc) == [("comparator.json", n) for n in MAIN]
    assert payload["headline"]["applied"] is False and "does not validate at HEAD" in payload["headline"]["reason"]
    assert f"headline configuration {HEADLINE} (registry) not applied" in err
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = None
    write_reg(env, reg)
    payload, _doc, err = render(lp, env, capsys)
    assert "no comparator receipt is recorded" in payload["headline"]["reason"]


def test_headline_key_outside_palomars_six_is_refused(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one"], judged_as={"reviewer": "Jane Roe"})
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    assert cli(lp, env, "formalization", "beta") == 1
    err = capsys.readouterr().err
    assert "key(s) outside Palomar's six: judged_as" in err


def test_untracked_registry_headline_is_refused(lp, env, capsys):
    set_field(env, "paper/nowhere.json")
    assert cli(lp, env, "formalization", "beta") == 1
    assert "paper/nowhere.json is not tracked at HEAD" in capsys.readouterr().err


def test_render_is_deterministic(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.two", "BetaAudit.A.one"])
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    first, doc, _ = render(lp, env, capsys)
    second, _doc, _ = render(lp, env, capsys)
    assert first == second
    assert [d for _c, d in decls(doc)] == ["BetaAudit.A.two", "BetaAudit.A.one"]   # the headline's own order


# --- definition holes ---------------------------------------------------------

SIGN = {"reviewer": "Jane Roe", "date": "2026-10-01", "judged_as": "the body is the paper's definition"}


def holes_setup(env, headline_holes):
    repo = env.repo_b
    add_comparator_config(repo, theorem_names=MAIN, definition_names=("BetaAudit.D.d",))
    raw = (repo / "comparator.json").read_bytes()
    side = {"schema": "lean-pkg-signoff/1", "configs": {"comparator.json": {
        "config_sha256": hashlib.sha256(raw).hexdigest(), "definitions": {"BetaAudit.D.d": SIGN}}}}
    (repo / "lean-pkg-signoff.json").write_text(json.dumps(side, indent=2) + "\n")
    _git(repo, "add", "lean-pkg-signoff.json")
    _git(repo, "commit", "-q", "-m", "sign-off")
    write_cfg(repo, HEADLINE, ["BetaAudit.A.one"], holes=headline_holes)
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)


def test_headline_holes_borrow_the_main_configs_sign_off(lp, env, capsys):
    holes_setup(env, ["BetaAudit.D.d"])
    _payload, doc, _err = render(lp, env, capsys)
    entry = doc["status"]["main_results"][0]
    assert entry["definition_holes"] == ["BetaAudit.D.d"]
    assert entry["judged_as"] == {"reviewer": "Jane Roe", "date": "2026-10-01", "definitions": ["BetaAudit.D.d"],
                                  "basis": SIGN["judged_as"]}


def test_headline_hole_no_receipted_config_signed_is_refused(lp, env, capsys):
    holes_setup(env, ["BetaAudit.D.d", "BetaAudit.D.e"])
    assert cli(lp, env, "formalization", "beta") == 1
    err = capsys.readouterr().err
    assert "1 definition hole(s) without a sign-off" in err and "BetaAudit.D.e" in err


# --- declaring it ---------------------------------------------------------------

def test_flag_previews_and_write_records_the_registry_field(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one"])
    rebind(env, ["comparator.json"])
    payload, doc, _err = render(lp, env, capsys, "--headline-config", HEADLINE)
    assert payload["headline"]["source"] == "flag" and decls(doc) == [(HEADLINE, "BetaAudit.A.one")]
    assert "headline_config" not in read_reg(env)["packages"]["beta"]          # a preview writes nothing
    assert cli(lp, env, "formalization", "beta", "--write", "--headline-config", HEADLINE) == 0
    capsys.readouterr()
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["headline_config"] == HEADLINE and pkg["formalization_yaml"] == "formalization.yaml"
    ev = read_events(env)[-1]
    assert ev["event"] == "updated" and "headline_config" in ev["fields"]
    # the recorded field makes a plain re-render (what audit compares) agree
    assert cli(lp, env, "formalization", "beta", "--write") == 0
    assert "already what the generator emits" in capsys.readouterr().out


def test_update_sets_validates_and_clears_the_field(lp, env, capsys):
    assert cli(lp, env, "update", "beta", "--headline-config", HEADLINE) == 0
    assert read_reg(env)["packages"]["beta"]["headline_config"] == HEADLINE
    for bad in ("/abs/headline.json", "../up.json", "paper/headline.yaml", "lean-pkg-signoff.json"):
        assert cli(lp, env, "update", "beta", "--headline-config", bad) == 1
        assert "headline configuration" in capsys.readouterr().err
    assert cli(lp, env, "update", "beta", "--headline-config", "") == 0
    assert read_reg(env)["packages"]["beta"]["headline_config"] is None


def test_empty_flag_is_a_usage_error(lp, env, capsys):
    assert cli(lp, env, "formalization", "beta", "--headline-config", " ") == 2


# --- palomar check / prepare ------------------------------------------------------

def check(lp, reg, capsys):
    rc = lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json"])
    return rc, json.loads(capsys.readouterr().out)


def test_palomar_check_flags_an_extra_key_in_the_headline_config(lp, tmp_path, capsys):
    bad = {**CONFIG, "theorem_names": ["Gamma.foo"], "judged_as": {"reviewer": "Jane Roe"}}
    _repo, reg = make_pkg(tmp_path, files(**{"comparator.headline.json": json.dumps(bad) + "\n"}))
    rc, data = check(lp, reg, capsys)
    assert rc == 1 and data["headline"]["config"] == "comparator.headline.json"
    gaps = [g for g in data["gaps"] if "comparator.headline.json" in g["paths"]]
    assert [g["code"] for g in gaps] == ["comparator.unknown_key"]          # reported once, by the config loop
    assert "judged_as" in gaps[0]["detail"]


def test_palomar_check_runs_the_config_checks_on_a_non_config_path_headline(lp, tmp_path, capsys):
    bad = {**CONFIG, "note": "x"}
    _repo, reg = make_pkg(tmp_path, files(**{HEADLINE: json.dumps(bad) + "\n"}), headline_config=HEADLINE)
    rc, data = check(lp, reg, capsys)
    assert rc == 1
    unknown = [g for g in data["gaps"] if g["code"] == "comparator.unknown_key"]
    assert unknown and unknown[0]["paths"] == [HEADLINE] and "note" in unknown[0]["detail"]


def test_palomar_check_without_receipts_warns_that_the_headline_is_unjudged(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(**{"comparator.headline.json": json.dumps(CONFIG) + "\n"}))
    rc, data = check(lp, reg, capsys)
    assert rc == 0 and data["ready"] is True
    codes = [g["code"] for g in data["gaps"]]
    assert codes == ["comparator.headline_unjudged"]
    assert data["headline"] == {"config": "comparator.headline.json", "source": "convention", "judged": False,
                                "covered": None}


def test_palomar_check_reports_uncovered_headline_names(lp, env, capsys):
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.Missing.thm"])
    rebind(env, ["comparator.json"])
    set_field(env, HEADLINE)
    lp.main(["--registry", str(env.registry), "palomar", "check", "beta", "--json"])
    data = json.loads(capsys.readouterr().out)
    gap = [g for g in data["gaps"] if g["code"] == "comparator.headline_uncovered"]
    assert gap and gap[0]["severity"] == "error" and "BetaAudit.Missing.thm" in gap[0]["detail"]
    assert data["headline"]["judged"] is True and data["headline"]["covered"] is False


@pytest.fixture()
def assume_ready(monkeypatch):
    """beta has no module headers and an old toolchain; take readiness (error gaps) as given here."""
    pc = importlib.import_module("lean_pkg.palomar_check")
    real = pc.check

    def ready(pkg, repo, commit, policy, source, config=None):
        rep = real(pkg, repo, commit, policy, source, config)
        gaps = [g for g in rep["gaps"] if g["severity"] != "error"]
        return {**rep, "ready": True, "gaps": gaps, "counts": {"error": 0, "warn": len(gaps)}}
    monkeypatch.setattr(pc, "check", ready)


def test_prepare_bundles_the_headline_config_and_names_it(lp, env, capsys, assume_ready, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")))
    main_three(env)
    write_cfg(env.repo_b, HEADLINE, ["BetaAudit.A.one"])
    rebind(env, ["comparator.json"])
    reg = read_reg(env)
    pkg = reg["packages"]["beta"]
    pkg.update(headline_config=HEADLINE, license_spdx="MIT",
               consent=[{"who": "Formalizer One", "role": "owner", "pointer": "consent/one.md",
                         "recorded_at": "2026-10-09T00:00:00+00:00"}])
    write_reg(env, reg)
    head = repo_head(env.repo_b)
    assert cli(lp, env, "palomar", "prepare", "beta", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["headline_config"] == HEADLINE and payload["configs"] == ["comparator.json"]
    bundle = env.repo_b / ".lean-receipts" / f"palomar-{head[:12]}"
    assert (bundle / "configs" / HEADLINE).read_bytes() == (env.repo_b / HEADLINE).read_bytes()
    readme = (bundle / "README.txt").read_text()
    assert f"{HEADLINE}   (headline: the paper's main results" in readme
    assert f"--field comparator_config={HEADLINE}" in readme
    # #402: the approval fields pre-fill the headline configuration; the others go separately
    assert f"     comparator_config_path: {HEADLINE}   (headline: the paper's main results;" in readme
    assert "submit each other one separately" in readme and "\n     comparator.json\n" in readme
    assert payload["handoff"]["approval_fields"]["comparator_config_path"] == HEADLINE
    fz = yaml.safe_load((bundle / "formalization.yaml").read_text())
    assert decls(fz) == [(HEADLINE, "BetaAudit.A.one")]
