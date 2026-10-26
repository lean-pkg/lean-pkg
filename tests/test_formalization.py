#!/usr/bin/env python3
"""lean-pkg formalization: render formalization.yaml (v0.4) from the registry and the
repo at HEAD; --write idempotence and refusal; schema validation paths; audit drift;
definition holes carry judged_as; authors and sources are never invented."""

from __future__ import annotations

import hashlib
import importlib
import json
import pathlib

import pytest
import yaml

from .conftest import (ROOT, _git, add_comparator_config, commit_claims, read_events,
                       read_reg, write_reg)

SCHEMA_SHA256 = "25ff6b25ca4511635aff4443cf20480c15e59dddf19591c730950b442ea54fce"


@pytest.fixture()
def fy(lp):
    return importlib.import_module("lean_pkg.formalization")


def run(lp, env, *argv: str) -> int:
    return lp.main(["--registry", str(env.registry), "formalization", *argv])


def rendered(capsys) -> dict:
    out = capsys.readouterr().out
    doc = yaml.safe_load(out)
    assert out.startswith("# yaml-language-server: $schema=")
    return doc


def two_configs(env) -> None:
    """beta already tracks comparator.json (foo_le_bar); add a second config with two theorems."""
    add_comparator_config(env.repo_b, "comparator/config-second.json",
                          theorem_names=("second_a", "second_b"))


# --- vendored schema ---------------------------------------------------------

def test_vendored_schema_is_the_pinned_upstream_bytes(fy):
    path = ROOT / "src" / "lean_pkg" / "schemas" / "formalization-0.4.schema.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == SCHEMA_SHA256
    source = (ROOT / "src" / "lean_pkg" / "schemas" / "formalization-0.4.schema.source.txt").read_text()
    assert SCHEMA_SHA256 in source and "2026-10-07" in source and "99c678e5" in source
    schema = fy.load_schema()
    assert schema["title"] == "formalization.yaml (v0.4)"
    assert set(schema["required"]) == {"project", "sources", "automation", "review"}


# --- render ------------------------------------------------------------------

def test_render_two_configs_to_stdout(lp, env, capsys):
    two_configs(env)
    assert run(lp, env, "beta") == 0
    doc = rendered(capsys)
    assert doc["version"] == "v0.4"
    assert doc["project"]["name"] == "beta"
    results = doc["status"]["main_results"]
    assert [(r["comparator_config"], r["declaration"]) for r in results] == [
        ("comparator.json", "BetaAudit.Demo.foo_le_bar"),
        ("comparator/config-second.json", "second_a"),
        ("comparator/config-second.json", "second_b")]
    assert all(r["file"] == "Solution.lean" and r["challenge_file"] == "Challenge.lean"
               for r in results)
    assert "definition_holes" not in results[0]
    # the lower-tier receipt feeds the status block
    assert doc["status"]["sorry_count"] == 0
    assert doc["status"]["axioms"] == ["propext", "Quot.sound", "Classical.choice"]
    assert doc["status"]["evidence"]["gate_receipt"] == ".lean-receipts/seed.json"
    assert doc["status"]["evidence"]["comparator_receipt"].endswith("-comparator.json")
    # Mathlib row seeded from mathlib_pin; registry automation is empty -> placeholder
    assert doc["related_formalizations"][0]["id"].endswith("/mathlib4")
    assert doc["automation"]["methods"] == [{"method": "other"}]
    assert doc["automation"]["notes"].startswith("[PLACEHOLDER]")
    assert doc["review"]["status"] == "unchecked"
    # top-level key order follows the template
    assert list(doc)[:4] == ["version", "project", "sources", "related_formalizations"]


def test_placeholders_are_marked_warned_and_never_invented(lp, env, capsys):
    assert run(lp, env, "beta") == 0
    out, err = capsys.readouterr()
    doc = yaml.safe_load(out)
    assert doc["project"]["license"].startswith("[PLACEHOLDER]")       # license_spdx is null
    assert doc["project"]["authors"][0].startswith("[PLACEHOLDER]")    # no consent record
    assert doc["sources"][0]["title"].startswith("[PLACEHOLDER]")      # no paper metadata
    assert doc["sources"][0]["relationship"] == "formalizes"
    for key in ("project.license", "project.authors[0]", "sources[0].title",
                "project.description", "status.scope", "automation.notes"):
        assert f"placeholder at {key}" in err
    assert "warning" in err


def test_json_payload_reports_validation_and_placeholders(lp, env, capsys):
    pytest.importorskip("jsonschema")
    assert run(lp, env, "beta", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["validation"] == "jsonschema"
    assert data["main_results"] == 1 and data["written"] is False
    assert "project.license" in data["placeholders"]
    assert yaml.safe_load(data["yaml"])["project"]["name"] == "beta"


def test_license_automation_and_consent_flow_from_the_registry(lp, env, capsys):
    reg = read_reg(env)
    pkg = reg["packages"]["beta"]
    pkg["license_spdx"] = "Apache-2.0"
    pkg["automation"] = "Proofs drafted with an LLM agent; statements reviewed by hand."
    pkg["consent"] = [{"who": "Ada Lovelace", "role": "owner", "pointer": "c/ada.md",
                       "recorded_at": "2026-10-06T00:00:00+00:00"},
                      {"who": "A Reviewer", "role": "reviewer", "pointer": "c/r.md",
                       "recorded_at": "2026-10-06T00:00:00+00:00"},
                      {"who": "Ada Lovelace", "role": "author", "pointer": "c/ada2.md",
                       "recorded_at": "2026-10-06T00:00:00+00:00"}]
    write_reg(env, reg)
    assert run(lp, env, "beta") == 0
    doc = rendered(capsys)
    assert doc["project"]["license"] == "Apache-2.0"
    assert doc["project"]["authors"] == ["Ada Lovelace"]          # owner/author only, deduplicated
    assert doc["automation"]["notes"].startswith("Proofs drafted")
    # an explicit --author wins over the consent record
    assert run(lp, env, "beta", "--author", "Grace Hopper", "--author", "Ada Lovelace") == 0
    assert rendered(capsys)["project"]["authors"] == ["Grace Hopper", "Ada Lovelace"]


def test_claims_yaml_at_head_becomes_alignment(lp, env, capsys):
    commit_claims(env.repo_b)
    assert run(lp, env, "beta") == 0
    doc = rendered(capsys)
    al = doc["alignment"]
    assert al["generated_from"] == "claims.yaml"
    assert al["statements"] == [{"source": "Demo 2026, Theorem 1", "lean": "Demo.foo_le_bar",
                                 "module": "Demo/Main.lean", "kind": "theorem",
                                 "claim": "beta/foo-le-bar", "status": "reusable",
                                 "note": "A trivial demonstration bound used by the white-noise examples."}]


def test_invalid_claims_file_refuses(lp, env, capsys):
    commit_claims(env.repo_b, text="schema: claims/1\npackage: beta\nclaims: [{id: bad}]\n")
    assert run(lp, env, "beta") == 1
    assert "claims/1" in capsys.readouterr().err


def test_no_comparator_config_yields_empty_main_results_and_a_warning(lp, env, capsys):
    assert run(lp, env, "alpha") == 0   # alpha's repo has no comparator config and no receipt
    out, err = capsys.readouterr()
    doc = yaml.safe_load(out)
    assert doc["status"]["main_results"] == []
    assert "sorry_count" not in doc["status"] and "evidence" not in doc["status"]
    assert "status.main_results is empty" in err


# --- definition holes --------------------------------------------------------

JUDGED = {"reviewer": "Le Chen", "date": "2026-10-07", "definitions": ["Challenge.witness"],
          "basis": "bodies compared side by side"}


def test_definition_holes_carry_the_judged_as_reviewer(lp, env, capsys):
    add_comparator_config(env.repo_b, "comparator/config-holes.json", theorem_names=("holed",),
                          definition_names=("Challenge.witness",), judged_as=JUDGED)
    assert run(lp, env, "beta") == 0
    doc = rendered(capsys)
    holed = [r for r in doc["status"]["main_results"] if r["declaration"] == "holed"][0]
    assert holed["definition_holes"] == ["Challenge.witness"]
    assert holed["judged_as"] == JUDGED
    plain = [r for r in doc["status"]["main_results"] if r["declaration"] == "BetaAudit.Demo.foo_le_bar"][0]
    assert "judged_as" not in plain


def test_definition_holes_without_sign_off_render_null_and_warn(lp, env, capsys):
    add_comparator_config(env.repo_b, "comparator/config-holes.json", theorem_names=("holed",),
                          definition_names=("Challenge.witness",))
    assert run(lp, env, "beta") == 0
    out, err = capsys.readouterr()
    holed = [r for r in yaml.safe_load(out)["status"]["main_results"]
             if r["declaration"] == "holed"][0]
    assert holed["definition_holes"] == ["Challenge.witness"] and holed["judged_as"] is None
    assert "no sign-off" in err and "judged_as is null" in err


# --- --write, --out ----------------------------------------------------------

def test_write_sets_registry_pointer_and_is_idempotent(lp, env, capsys):
    two_configs(env)
    assert run(lp, env, "beta", "--write") == 0
    out = capsys.readouterr().out
    assert "wrote" in out and "not committed" in out
    target = env.repo_b / "formalization.yaml"
    first = target.read_text()
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["formalization_yaml"] == "formalization.yaml"
    ev = read_events(env)
    assert ev[-1]["event"] == "updated" and ev[-1]["fields"] == ["formalization_yaml"]
    assert pkg["history"][-1]["event"] == "updated"
    assert yaml.safe_load(first)["status"]["main_results"][2]["declaration"] == "second_b"

    # idempotent re-run: same bytes, no new event, nothing written
    assert run(lp, env, "beta", "--write") == 0
    assert "nothing written" in capsys.readouterr().out
    assert target.read_text() == first
    assert len(read_events(env)) == len(ev)


def test_write_json_payload_flags(lp, env, capsys):
    assert run(lp, env, "beta", "--write", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["written"] is True and data["registry_updated"] is True and data["unchanged"] is False
    assert run(lp, env, "beta", "--write", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["written"] is False and data["unchanged"] is True and data["registry_updated"] is False


def test_write_no_registry_writes_the_file_and_leaves_the_registry_alone(lp, env, capsys):   # #22.8
    before_reg, before_ev = env.registry.read_bytes(), read_events(env)
    assert run(lp, env, "beta", "--write", "--no-registry", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["written"] is True and data["registry_updated"] is False and data["registry_skipped"] is True
    assert (env.repo_b / "formalization.yaml").is_file()
    assert env.registry.read_bytes() == before_reg and read_events(env) == before_ev
    assert not env.registry.with_name(env.registry.name + ".lock").exists()
    assert run(lp, env, "beta", "--write", "--no-registry") == 0
    out = capsys.readouterr().out
    assert "nothing written" in out and "--formalization-yaml formalization.yaml" in out
    assert env.registry.read_bytes() == before_reg


def test_no_registry_needs_write_and_is_not_a_commit_write(lp, env, capsys):
    assert run(lp, env, "beta", "--no-registry") == 2
    assert "--no-registry needs --write" in capsys.readouterr().err
    assert run(lp, env, "beta", "--write", "--no-registry", "--commit") == 0
    assert "--commit ignored: read-only command" in capsys.readouterr().err


def test_no_registry_still_respects_a_live_lease(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "claim", "beta", "--workspace", "w", "--agent", "vera"]) == 0
    capsys.readouterr()
    assert run(lp, env, "beta", "--write", "--no-registry", "--agent", "otto") == 1
    assert "lease held by vera" in capsys.readouterr().err
    assert not (env.repo_b / "formalization.yaml").exists()


def test_write_refuses_a_kept_key_difference_without_force(lp, env, capsys):
    assert run(lp, env, "beta", "--write") == 0
    capsys.readouterr()
    target = env.repo_b / "formalization.yaml"
    doc = yaml.safe_load(target.read_text())
    doc["status"]["main_results"][0]["declaration"] = "hand_edited"   # a derived key
    target.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert run(lp, env, "beta", "--write", "--author", "Someone Else") == 1   # project.authors is kept
    err = capsys.readouterr().err
    assert "differs from the render in kept key(s) project.authors" in err and "--force" in err
    assert yaml.safe_load(target.read_text())["status"]["main_results"][0]["declaration"] == "hand_edited"
    assert run(lp, env, "beta", "--write") == 0     # #341: a derived-only difference is refreshed
    assert "refreshed derived keys: status.main_results" in capsys.readouterr().out
    assert yaml.safe_load(target.read_text())["status"]["main_results"][0]["declaration"] == "BetaAudit.Demo.foo_le_bar"
    assert run(lp, env, "beta", "--write", "--author", "Someone Else", "--force") == 0
    assert yaml.safe_load(target.read_text())["project"]["authors"] == ["Someone Else"]


def test_kept_keys_survive_regeneration_and_derived_keys_do_not(lp, env, capsys):
    assert run(lp, env, "beta", "--write") == 0
    target = env.repo_b / "formalization.yaml"
    doc = yaml.safe_load(target.read_text())
    doc["project"]["description"] = "Sharp bounds, by hand."
    doc["project"]["authors"] = ["Ada Lovelace"]
    doc["sources"] = [{"title": "A real paper", "id": "arXiv:2601.00001", "type": "article",
                       "relationship": "formalizes"}]
    doc["classification"] = {"msc2020": ["60H15"]}
    doc["review"] = {"status": "self-assessed", "reviewers": ["Ada Lovelace"]}
    doc["project"]["license"] = "MIT"              # derived: the registry (null) wins
    target.write_text(yaml.safe_dump(doc, sort_keys=False))
    assert run(lp, env, "beta", "--write") == 0    # only a derived key differs: refreshed (#341)
    doc = yaml.safe_load(target.read_text())
    assert doc["project"]["description"] == "Sharp bounds, by hand."
    assert doc["project"]["authors"] == ["Ada Lovelace"]
    assert doc["sources"][0]["id"] == "arXiv:2601.00001"
    assert doc["classification"] == {"msc2020": ["60H15"]}
    assert doc["review"]["status"] == "self-assessed"
    assert doc["project"]["license"].startswith("[PLACEHOLDER]")
    assert run(lp, env, "beta", "--write") == 0    # and now it is stable
    assert "nothing written" in capsys.readouterr().out


def test_write_honours_a_nested_registry_path_and_out_leaves_registry_alone(lp, env, tmp_path, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["formalization_yaml"] = "lean/formalization.yaml"
    write_reg(env, reg)
    assert run(lp, env, "beta", "--write") == 0
    assert (env.repo_b / "lean" / "formalization.yaml").is_file()
    assert read_events(env) == []                  # pointer already set: no update event
    outp = tmp_path / "elsewhere" / "f.yaml"
    assert run(lp, env, "alpha", "--out", str(outp)) == 0
    assert yaml.safe_load(outp.read_text())["project"]["name"] == "alpha"
    assert read_reg(env)["packages"]["alpha"]["formalization_yaml"] is None
    assert run(lp, env, "alpha", "--out", str(outp), "--write") == 2


def test_write_refuses_a_symlinked_target(lp, env, tmp_path, capsys):
    elsewhere = tmp_path / "real.yaml"
    elsewhere.write_text("project: {}\n")
    (env.repo_b / "formalization.yaml").symlink_to(elsewhere)
    assert run(lp, env, "beta", "--write") == 1
    assert "symlink" in capsys.readouterr().err


def test_write_respects_a_live_lease(lp, env, capsys):
    from .conftest import set_lease
    set_lease(env, "beta", agent="vera")
    assert run(lp, env, "beta", "--write") == 1
    assert "lease held by vera" in capsys.readouterr().err
    assert run(lp, env, "beta", "--write", "--agent", "vera") == 0


# --- schema validation paths ------------------------------------------------

def test_jsonschema_path_accepts_the_render(lp, env, fy):
    pytest.importorskip("jsonschema")
    pkg = read_reg(env)["packages"]["beta"]
    doc, _ = fy.render_doc(pkg, env.repo_b, None)
    assert fy.validate_doc(doc) == ("jsonschema", [])


def test_structural_path_runs_without_jsonschema(lp, env, fy, monkeypatch, capsys):
    monkeypatch.setattr(fy, "_import_jsonschema", lambda: None)
    pkg = read_reg(env)["packages"]["beta"]
    doc, _ = fy.render_doc(pkg, env.repo_b, None)
    assert fy.validate_doc(doc) == ("structural", [])
    assert run(lp, env, "beta", "--json") == 0
    assert json.loads(capsys.readouterr().out)["validation"] == "structural"


@pytest.mark.parametrize("structural", [False, True])
def test_invalid_kept_keys_are_refused_in_both_modes(lp, env, fy, monkeypatch, capsys, structural):
    if structural:
        monkeypatch.setattr(fy, "_import_jsonschema", lambda: None)
    else:
        pytest.importorskip("jsonschema")
    (env.repo_b / "formalization.yaml").write_text(
        "review:\n  status: ''\nautomation:\n  methods: [{method: magic}]\n"
        "sources: [{title: ok, relationship: nonsense}]\n")
    assert run(lp, env, "beta") == 1
    err = capsys.readouterr().err
    assert "review/status" in err and "automation/methods/0/method" in err
    assert "sources/0/relationship" in err
    assert ("structural" if structural else "jsonschema") in err


def test_structural_checker_agrees_with_jsonschema_on_a_bad_document(fy):
    pytest.importorskip("jsonschema")
    bad = {"project": {"name": "", "authors": [], "license": "x"}, "sources": [],
           "automation": {"methods": []}, "review": {}, "status": {"sorry_count": -1}}
    _, js_errors = fy.validate_doc(bad)
    st_errors = fy.structural_errors(bad)
    for path in ("project/name", "project/authors", "sources", "automation/methods",
                 "review", "status/sorry_count"):
        assert any(e.startswith(path) for e in js_errors), path
        assert any(e.startswith(path) for e in st_errors), path


# --- audit -------------------------------------------------------------------

def test_audit_reports_formalization_drift_and_missing(lp, env, fake_gh, capsys):
    assert run(lp, env, "beta", "--write") == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "audit"]) == 0
    assert "formalization" not in capsys.readouterr().out

    add_comparator_config(env.repo_b, "comparator/config-later.json", theorem_names=("later",))
    assert lp.main(["--registry", str(env.registry), "audit", "--json"]) == 0
    findings = json.loads(capsys.readouterr().out)["findings"]
    drift = [f for f in findings if f["check"] == "formalization-drift"]
    assert len(drift) == 1 and drift[0]["name"] == "beta"
    assert "only derived keys differ (status.main_results)" in drift[0]["detail"]
    assert "`lean-pkg formalization render beta --write`" in drift[0]["detail"] and "--force" not in drift[0]["detail"]
    assert (env.repo_b / "formalization.yaml").read_text()   # audit never rewrites the file

    assert run(lp, env, "beta", "--write") == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "audit"]) == 0
    assert "formalization-drift" not in capsys.readouterr().out

    (env.repo_b / "formalization.yaml").unlink()
    assert lp.main(["--registry", str(env.registry), "audit"]) == 0
    assert "formalization-missing" in capsys.readouterr().out


def test_audit_ignores_packages_without_a_pointer(lp, env, fake_gh, capsys):
    assert lp.main(["--registry", str(env.registry), "audit"]) == 0
    assert "formalization" not in capsys.readouterr().out


# --- render --repo DIR (field request: an isolated candidate checkout) ---------

def _candidate(env, tmp_path, science: bool) -> pathlib.Path:
    """Commit beta's receipts (evidence-only), clone the checkout as a candidate whose remote is the
    package's repo; science=True then commits kept metadata, a second configuration and a new Lean file
    there (any non-evidence commit unbinds the gate receipt)."""
    _git(env.repo_b, "add", "-f", ".lean-receipts")
    _git(env.repo_b, "commit", "-q", "-m", "evidence")
    cand = tmp_path / "candidate"
    _git(tmp_path, "clone", "-q", str(env.repo_b), str(cand))
    _git(cand, "remote", "set-url", "origin", "https://github.com/example/beta")
    if science:
        (cand / "formalization.yaml").write_text("project:\n  description: committed candidate text\n")
        _git(cand, "add", "formalization.yaml")
        _git(cand, "commit", "-q", "-m", "kept metadata")
        add_comparator_config(cand, "comparator/config-second.json", theorem_names=("second_a",))
        (cand / "Demo" / "Extra.lean").write_text("theorem extra : True := trivial\n")
        _git(cand, "add", "Demo/Extra.lean")
        _git(cand, "commit", "-q", "-m", "science")
    return cand


def test_repo_renders_from_the_candidate_head_and_leaves_the_registry_alone(lp, env, tmp_path, capsys):
    cand = _candidate(env, tmp_path, science=True)
    (cand / "formalization.yaml").write_text("project:\n  description: UNCOMMITTED\n")   # never read
    before_reg, before_ev = env.registry.read_bytes(), read_events(env)
    assert run(lp, env, "beta", "--repo", str(cand), "--json") == 0
    out, err = capsys.readouterr()
    data = json.loads(out)
    doc = yaml.safe_load(data["yaml"])
    assert [r["declaration"] for r in doc["status"]["main_results"]] == ["BetaAudit.Demo.foo_le_bar", "second_a"]
    assert doc["project"]["description"] == "committed candidate text"
    head = _git(cand, "rev-parse", "HEAD").stdout.strip()
    ov = data["repo_override"]
    assert ov["path"] == str(cand.resolve()) and ov["head"] == head and ov["source"] == "committed-head"
    assert ov["registered_path"] == str(env.repo_b) and ov["remote_match"] is True
    # the registry's gate receipt binds the accepted commit, not the candidate: its facts are omitted
    assert ov["receipt_facts"]["included"] is False and "does not validate at HEAD" in ov["receipt_facts"]["reason"]
    assert not {"sorry_count", "axioms", "evidence"} & set(doc["status"])
    assert "status.sorry_count, status.axioms and status.evidence omitted" in err
    assert "uncommitted changes to tracked files are not read" in err
    assert f"using --repo {cand.resolve()}" in err
    assert env.registry.read_bytes() == before_reg and read_events(env) == before_ev
    assert not env.registry.with_name(env.registry.name + ".lock").exists()
    assert not (env.repo_b / "formalization.yaml").exists()


def test_repo_keeps_receipt_facts_bound_to_its_head(lp, env, tmp_path, capsys):
    cand = _candidate(env, tmp_path, science=False)    # an evidence-only commit after the bound one
    assert run(lp, env, "beta", "--repo", str(cand), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    doc = yaml.safe_load(data["yaml"])
    assert data["repo_override"]["receipt_facts"] == {"receipt": ".lean-receipts/seed.json", "included": True,
                                                      "reason": None}
    assert doc["status"]["sorry_count"] == 0 and doc["status"]["evidence"]["gate_receipt"] == ".lean-receipts/seed.json"


def test_repo_reads_a_linked_worktree_at_its_own_head(lp, env, tmp_path, capsys):
    wt = tmp_path / "candidate-wt"
    _git(env.repo_b, "worktree", "add", "-q", "-b", "candidate", str(wt))
    add_comparator_config(wt, "comparator/config-second.json", theorem_names=("second_a",))
    assert run(lp, env, "beta", "--repo", str(wt), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["repo_override"]["head"] == _git(wt, "rev-parse", "HEAD").stdout.strip()
    assert yaml.safe_load(data["yaml"])["status"]["main_results"][-1]["declaration"] == "second_a"
    assert run(lp, env, "beta", "--json") == 0      # the registered checkout still renders its own HEAD
    assert json.loads(capsys.readouterr().out)["main_results"] == 1


def test_repo_out_writes_the_file_and_names_the_source(lp, env, tmp_path, capsys):
    cand = _candidate(env, tmp_path, science=True)
    target = tmp_path / "cand-formalization.yaml"
    assert run(lp, env, "beta", "--repo", str(cand), "--out", str(target)) == 0
    head = _git(cand, "rev-parse", "HEAD").stdout.strip()
    out = capsys.readouterr().out
    assert f"from --repo {cand.resolve()} at {head[:12]} (committed HEAD)" in out and "registry untouched" in out
    assert yaml.safe_load(target.read_text())["status"]["main_results"][1]["declaration"] == "second_a"
    assert not (cand / "formalization.yaml").read_text().startswith("#")   # the candidate is not written


def test_repo_is_refused_with_write_and_on_anchor(lp, env, tmp_path, capsys):
    cand = _candidate(env, tmp_path, science=False)
    before = env.registry.read_bytes()
    assert run(lp, env, "beta", "--write", "--repo", str(cand)) == 2
    assert "--repo reads another checkout" in capsys.readouterr().err
    assert run(lp, env, "anchor", "beta", "--arxiv", "2601.00001v1", "--repo", str(cand)) == 2
    assert "--repo" in capsys.readouterr().err
    assert env.registry.read_bytes() == before


def test_repo_refuses_a_checkout_of_another_repo(lp, env, tmp_path, capsys):
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(env.repo_b), str(other))
    _git(other, "remote", "set-url", "origin", "https://github.com/example/unrelated")
    assert run(lp, env, "beta", "--repo", str(other)) == 1
    assert "no git remote of it is the package's repo" in capsys.readouterr().err


def test_json_without_repo_has_a_null_repo_override(lp, env, capsys):
    assert run(lp, env, "beta", "--json") == 0
    assert json.loads(capsys.readouterr().out)["repo_override"] is None


# --- docs --------------------------------------------------------------------

def test_contract_and_docs_mention_formalization(lp):
    assert "formalization NAME [--write]" in lp.LLMS_TEXT
    assert "formalization-drift" in (ROOT / "docs" / "cli.md").read_text()
    assert (ROOT / "docs" / "formalization.md").is_file()
