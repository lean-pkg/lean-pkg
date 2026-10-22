#!/usr/bin/env python3
"""claims mark (#6 field report): set the status of exactly the claims a comparator config's theorem_names
cover (comparator_config.covers: full names, fully qualified decls only), in the working copy, only with
--write, never committed; unmatched names reported. Plus the zero-reusable WARN of claims sync/check and the
named fix in the ledger's `unknown` reason."""

from __future__ import annotations

import json

from .conftest import CLAIMS_YAML, _git, commit_claims, make_receipt, publish_evidence

LEAN = ("namespace Demo\n\ntheorem foo_le_bar : True := trivial\n\ntheorem mass_le : True := trivial\n\n"
        "theorem other : True := trivial\n\nend Demo\n")
EXTRA = """\
# hand-written comment kept by an in-place edit
- id: beta/mass-le
  decl: Demo.mass_le
  kind: theorem
  summary: Mass bound.
  file: Demo/Main.lean
  status: experimental   # trailing comment
- id: beta/other
  decl: Demo.other
  kind: theorem
  summary: Another one.
  file: Demo/Main.lean
  status: experimental
- id: beta/bare
  decl: mass_le
  kind: theorem
  summary: A bare decl is credited to nothing.
  file: Demo/Main.lean
  status: experimental
"""
TEXT = CLAIMS_YAML.replace("status: reusable", "status: experimental") + EXTRA


def claims_mark_setup(env, cfg_names=("BetaAudit.Demo.mass_le", "Demo.foo_le_bar", "Demo.nowhere")) -> str:
    commit_claims(env.repo_b, TEXT, lean=LEAN)
    cfg = env.repo_b / "comparator" / "config-mass.json"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(json.dumps({"theorem_names": list(cfg_names)}))
    return "comparator/config-mass.json"


def mark(lp, env, capsys, *extra) -> tuple[int, dict]:
    rc = lp.main(["--registry", str(env.registry), "claims", "mark", "beta", "--from-comparator",
                  "comparator/config-mass.json", "--status", "reusable", "--json", *extra])
    return rc, json.loads(capsys.readouterr().out)


def test_dry_run_by_default_reports_matches_and_unmatched(lp, env, capsys):
    claims_mark_setup(env)
    before = (env.repo_b / "claims.yaml").read_text()
    rc, data = mark(lp, env, capsys)
    assert rc == 0 and data["written"] is False
    assert {m["id"] for m in data["matched"]} == {"beta/foo-le-bar", "beta/mass-le"}
    assert data["changed"] == 2 and data["unmatched"] == ["Demo.nowhere"]
    assert (env.repo_b / "claims.yaml").read_text() == before


def test_write_edits_only_status_values_and_never_commits(lp, env, capsys):
    claims_mark_setup(env)
    head = _git(env.repo_b, "rev-parse", "HEAD").stdout
    rc, data = mark(lp, env, capsys, "--write")
    assert rc == 0 and data["written"] is True and data["reformatted"] is False
    after = (env.repo_b / "claims.yaml").read_text()
    assert "# hand-written comment kept by an in-place edit" in after and "status: reusable   # trailing comment" in after
    expect = TEXT.replace("status: experimental\n  superseded_by", "status: reusable\n  superseded_by", 1)
    expect = expect.replace("status: experimental   # trailing", "status: reusable   # trailing")
    assert after == expect                                      # the bare decl and Demo.other untouched
    assert _git(env.repo_b, "rev-parse", "HEAD").stdout == head
    assert "claims.yaml" in _git(env.repo_b, "status", "--porcelain").stdout
    rc, data = mark(lp, env, capsys, "--write")
    assert data["changed"] == 0 and data["written"] is False    # idempotent


def test_negative_control_a_shared_last_component_never_matches(lp, env, capsys):
    claims_mark_setup(env, cfg_names=("Other.mass_le",))
    rc, data = mark(lp, env, capsys)
    assert data["matched"] == [] and data["unmatched"] == ["Other.mass_le"]


def test_write_and_dry_run_conflict_and_bad_config(lp, env, capsys):
    claims_mark_setup(env)
    assert lp.main(["--registry", str(env.registry), "claims", "mark", "beta", "--from-comparator",
                    "comparator/config-mass.json", "--status", "reusable", "--write", "--dry-run"]) == 2
    (env.repo_b / "comparator" / "config-mass.json").write_text(json.dumps({"theorem_names": []}))
    assert lp.main(["--registry", str(env.registry), "claims", "mark", "beta", "--from-comparator",
                    "comparator/config-mass.json", "--status", "reusable"]) == 1
    assert "non-empty list" in capsys.readouterr().err


def rebind(env) -> None:
    """The gate receipt now binds the commit whose claims are all experimental (what the ledger reads)."""
    (env.repo_b / ".lean-receipts" / "seed.json").write_text(json.dumps(make_receipt(env.repo_b), indent=2))


def test_sync_and_check_warn_on_a_gated_package_with_no_reusable_claim(lp, env, capsys):
    claims_mark_setup(env)
    rebind(env)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["warnings"]) == 1 and "none with status reusable" in data["warnings"][0]
    assert "claims mark beta --from-comparator" in data["warnings"][0]
    rc = lp.main(["--registry", str(env.registry), "claims", "check", "beta"])
    out = capsys.readouterr().out.splitlines()
    assert rc == 0 and out[-1].startswith("1 package(s) checked") and any(x.startswith("WARN beta:") for x in out)
    rc = lp.main(["--registry", str(env.registry), "claims", "ledger", "beta", "--json"])
    led = json.loads(capsys.readouterr().out)
    assert any("fix: mark the results others should rely on as status: reusable" in r for r in led["reasons"])


def test_no_warning_with_a_reusable_claim(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["warnings"] == []


def test_publish_names_the_fix(lp, env, fake_gh, capsys):
    claims_mark_setup(env)
    rebind(env)
    lp.main(["--registry", str(env.registry), "claims", "sync", "beta"])
    publish_evidence(env.repo_b)
    capsys.readouterr()
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run"])
    assert "mark the results others should rely on as status: reusable" in capsys.readouterr().out
