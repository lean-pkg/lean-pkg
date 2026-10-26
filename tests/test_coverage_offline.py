#!/usr/bin/env python3
"""#328: `lean-pkg coverage check --file PATH [--claims PATH] [--comparator PATH]`, the offline form: no
registry record and no receipt. Every check that needs neither runs (coverage/1 schema and length limits, the
manuscript version, the claims file's claims/1 schema, row claim ids, reusable claims a row names, comparator
theorems mapped to rows); the rest are listed as skipped. The registered form is unchanged."""

from __future__ import annotations

import json

import yaml

from .conftest import CLAIMS_YAML, DEMO_LEAN
from .test_page import COVERAGE, prepare, run as page_run

CONFIG = {"challenge_module": "Challenge", "solution_module": "Solution",
          "theorem_names": ["BetaAudit.Demo.foo_le_bar"], "definition_names": [],
          "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"], "enable_nanoda": True}
ALWAYS_SKIPPED = ["compute receipts", "claim files at HEAD", "definition sign-off", "e-print baseline"]


def package(tmp_path, coverage=None, claims: str = CLAIMS_YAML, config=None, lean: str | None = DEMO_LEAN):
    """A package directory on disk, no git, no registry: coverage.yaml, claims.yaml, comparator.json, Lean."""
    d = tmp_path / "pkg"
    d.mkdir(exist_ok=True)
    (d / "coverage.yaml").write_text(coverage if isinstance(coverage, str)
                                     else yaml.safe_dump(coverage or COVERAGE, sort_keys=False))
    (d / "claims.yaml").write_text(claims)
    (d / "comparator.json").write_text(json.dumps(config or CONFIG))
    if lean is not None:
        (d / "Demo").mkdir(exist_ok=True)
        (d / "Demo" / "Main.lean").write_text(lean)
    return d


def offline(lp, capsys, tmp_path, d, *extra, claims=True, comparator=True, as_json=True):
    argv = ["--registry", str(tmp_path / "no-such-registry.yaml")] + (["--json"] if as_json else [])
    argv += ["coverage", "check", "--file", str(d / "coverage.yaml")]
    argv += (["--claims", str(d / "claims.yaml")] if claims else [])
    argv += (["--comparator", str(d / "comparator.json")] if comparator else [])
    capsys.readouterr()
    rc = lp.main(argv + list(extra))
    cap = capsys.readouterr()
    return rc, (json.loads(cap.out) if as_json else cap.out), cap.err


def status(data, check):
    return next(c["status"] for c in data["checks"] if c["check"] == check)


def mutated(**change) -> dict:
    doc = json.loads(json.dumps(COVERAGE))
    doc.update(change)
    return doc


def test_offline_check_passes_with_no_registry_and_says_what_it_skipped(lp, capsys, tmp_path):
    d = package(tmp_path)
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and data["ok"] and data["offline"] is True and data["name"] == "beta"
    assert data["rows"] == 2 and data["counts"] == {"same-assumptions": 1, "inputs-supplied": 0, "outside": 1}
    for check in ("schema", "manuscript", "lean_pkg_version", "claims file", "claim ids", "reusable claims",
                  "comparator theorems"):
        assert status(data, check) == "pass", check
    assert data["skipped"] == ALWAYS_SKIPPED
    assert not (tmp_path / "no-such-registry.yaml").exists()        # no registry read or written


def test_offline_text_is_verdict_first_and_last(lp, capsys, tmp_path):
    d = package(tmp_path)
    rc, out, _err = offline(lp, capsys, tmp_path, d, as_json=False)
    lines = [x for x in out.splitlines() if x.strip()]
    assert rc == 0 and lines[0].startswith(f"{d / 'coverage.yaml'}: coverage: OK; 2 row(s)")
    assert "offline, 4 check(s) skipped" in lines[0] and lines[-1] == lines[0]
    assert any(x.startswith("  skipped: compute receipts:") for x in lines)
    rc, out, _err = offline(lp, capsys, tmp_path, package(tmp_path, mutated(summary="x" * 401)), as_json=False)
    lines = [x for x in out.splitlines() if x.strip()]
    assert rc == 1 and "coverage: INVALID (1 problem(s); offline" in lines[0] and lines[-1] == lines[0]


def test_the_summary_length_limit_is_caught_before_registration(lp, capsys, tmp_path):
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, mutated(summary="x" * 796)))
    assert rc == 1 and data["ok"] is False and status(data, "schema") == "fail"
    assert any("summary: must be one line of at most 400 characters" in e for e in data["errors"])
    assert status(data, "claim ids") == "skipped"                    # needs a schema-valid file


def test_schema_refusals_match_the_registered_form(lp, capsys, tmp_path):
    rows = COVERAGE["rows"]
    cases = [(mutated(rows=[rows[0]]), "no outside row"),
             (mutated(rows=[dict(rows[0], **{"class": "verified"}), rows[1]]), "class: must be one of"),
             (mutated(compute=[{"cluster": "elsewhere", "jobs": ["slurm-1"]}]), "compute[0].cluster"),
             (mutated(manuscript={"title": "T", "version": "draft 12"}), "manuscript.version"),
             (mutated(summary="FILL-ME: later"), "carries the placeholder"),
             ("schema: coverage/1\nschema: coverage/1\n", "duplicate key 'schema'")]
    for doc, needle in cases:
        rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, doc))
        assert rc == 1 and any(needle in e for e in data["errors"]), needle


def test_row_claim_ids_must_be_claims_of_the_given_file(lp, capsys, tmp_path):
    rows = COVERAGE["rows"]
    rc, data, _err = offline(lp, capsys, tmp_path,
                             package(tmp_path, mutated(rows=[dict(rows[0], claims=["beta/nope"]), rows[1]])))
    assert rc == 1 and status(data, "claim ids") == "fail"
    assert any("'beta/nope' is not a claim id in" in e and "claims.yaml" in e for e in data["errors"])
    superseded = CLAIMS_YAML.replace("status: reusable", "status: superseded").replace(
        "superseded_by: null", "superseded_by: beta/other")
    superseded += CLAIMS_YAML.split("claims:\n", 1)[1].replace("beta/foo-le-bar", "beta/other")
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, claims=superseded))
    assert rc == 1 and any("is superseded" in e for e in data["errors"])


def test_the_coverage_package_must_be_the_claims_package(lp, capsys, tmp_path):
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, mutated(package="gamma")))
    assert rc == 1 and any("package: 'gamma' is not 'beta'" in e for e in data["errors"])


def test_an_invalid_claims_file_is_refused_with_the_claims_message(lp, capsys, tmp_path):
    bad = CLAIMS_YAML.replace("kind: theorem", "kind: bogus")
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, claims=bad))
    assert rc == 1 and status(data, "claims file") == "fail" and status(data, "claim ids") == "skipped"
    assert any("claims.yaml: " in e and "kind" in e for e in data["errors"])


def test_a_reusable_claim_no_row_names_is_a_warning(lp, capsys, tmp_path):
    extra = CLAIMS_YAML + CLAIMS_YAML.split("claims:\n", 1)[1].replace("beta/foo-le-bar", "beta/extra")
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, claims=extra), comparator=False)
    assert rc == 0 and data["ok"] and status(data, "reusable claims") == "warn"
    assert any("beta/extra" in w and "named by no row" in w for w in data["warnings"])


def test_each_comparator_theorem_must_be_the_declaration_of_a_claim_a_row_names(lp, capsys, tmp_path):
    cfg = dict(CONFIG, theorem_names=["BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.other"])
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, config=cfg))
    assert rc == 1 and status(data, "comparator theorems") == "fail"
    assert any("comparator theorem 'BetaAudit.Demo.other' (comparator.json, comparator configuration)" in e
               for e in data["errors"])
    d = package(tmp_path, config=cfg)                                 # a smoke configuration is exempt
    (d / "lean-pkg-signoff.json").write_text(json.dumps({"schema": "lean-pkg-signoff/1", "configs": {
        "comparator.json": {"smoke": {"reason": "a smoke check of the toolchain"}}}}))
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert any("marks as a smoke check" in e for e in data["errors"])   # but its theorem names a row's claim
    assert not any("'BetaAudit.Demo.other'" in e for e in data["errors"])


def test_comparator_mapping_is_skipped_when_the_lean_files_are_not_here(lp, capsys, tmp_path):
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, lean=None))
    assert rc == 0 and status(data, "comparator theorems") == "skipped" and "comparator theorems" in data["skipped"]


def test_a_malformed_comparator_configuration_is_a_problem(lp, capsys, tmp_path):
    d = package(tmp_path)
    (d / "comparator.json").write_text("{not json")
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and any("comparator.json: not valid JSON" in e for e in data["errors"])


def test_without_claims_or_comparator_those_checks_are_skipped(lp, capsys, tmp_path):
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path), claims=False, comparator=False)
    assert rc == 0 and data["claims_file"] is None and data["comparator"] == []
    assert data["skipped"] == ["claims file", "claim ids", "reusable claims", "comparator theorems"] + ALWAYS_SKIPPED


def test_an_unposted_manuscript_needs_the_flag(lp, capsys, tmp_path):
    doc = mutated(manuscript={"title": "A demonstration bound", "version": "unposted"})
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, doc))
    assert rc == 1 and any("--allow-private-manuscript" in e for e in data["errors"])
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, doc), "--allow-private-manuscript")
    assert rc == 0 and data["private_manuscript"] is True


def test_a_library_coverage_checks_offline(lp, capsys, tmp_path):
    doc = {"schema": "coverage/1", "package": "beta", "kind": "library", "summary": "One bound, formalized.",
           "rows": [{"title": "Main bound", "claims": ["beta/foo-le-bar"]}]}
    rc, data, _err = offline(lp, capsys, tmp_path, package(tmp_path, doc))
    assert rc == 0 and data["kind"] == "library" and data["manuscript"] is None and data["counts"] is None


def test_missing_files_and_usage_errors(lp, capsys, tmp_path):
    d = package(tmp_path)
    (d / "coverage.yaml").unlink()
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and data["ok"] is False and "no such file" in data["errors"][0]
    d = package(tmp_path)
    (d / "claims.yaml").unlink()
    rc, _out, err = offline(lp, capsys, tmp_path, d, as_json=False)
    assert rc == 1 and "claims file not found" in err
    rc = lp.main(["coverage", "check"])
    assert rc == 2 and "--file PATH without NAME" in capsys.readouterr().err
    rc = lp.main(["coverage", "check", "beta", "--claims", str(d / "claims.yaml")])
    assert rc == 2 and "offline form" in capsys.readouterr().err


def test_the_registered_form_is_unchanged(lp, env, capsys, tmp_path):
    prepare(env)
    rc, out, _err = page_run(lp, env, capsys, "--json", "coverage", "check", "beta")
    data = json.loads(out)
    assert rc == 0 and sorted(data) == ["counts", "errors", "file", "kind", "manuscript", "name", "ok",
                                        "private_manuscript", "rows", "warnings"]
    f = tmp_path / "cov.yaml"
    rows = COVERAGE["rows"]
    f.write_text(yaml.safe_dump(mutated(rows=[dict(rows[0], claims=["beta/nope"]), rows[1]]), sort_keys=False))
    rc, out, _err = page_run(lp, env, capsys, "--json", "coverage", "check", "beta", "--file", str(f))
    assert rc == 1 and any("'beta/nope' is not a claim id in claims.yaml at HEAD" in e
                           for e in json.loads(out)["errors"])
