#!/usr/bin/env python3
"""search (#49): packages without synced claims are searched by declaration in their checkout at
HEAD, and the output says which packages were searched that way or not at all."""

from __future__ import annotations

import json

import pytest

from .conftest import _git, base_pkg, read_reg, run_search, write_reg

HEAT_LEAN = """\
import Mathlib

namespace DemoA

/-- A measure on paths is heat-admissible when its Gaussian-damped mass is finite. -/
@[simp]
def IsHeatAdmissible (μ : Nat) : Prop := True

/-- The Gaussian-damped mass of a measure. -/
noncomputable def gaussianDampedMass (μ : Nat) : Nat := μ

theorem heatFlowMass_le (μ : Nat) : μ ≤ μ := le_refl μ

/-- Unrelated: a counting structure. -/
structure Counter where
  n : Nat

instance : Inhabited Counter := ⟨⟨0⟩⟩

end DemoA
"""


@pytest.fixture()
def unclaimed(searchable, tmp_path):
    """beta has synced claims (searchable); alpha has none and a checkout with Lean declarations;
    demo-c has no claims and no checkout."""
    s = searchable
    (s.env.repo_a / "DemoA").mkdir()
    (s.env.repo_a / "DemoA" / "Heat.lean").write_text(HEAT_LEAN)
    (s.env.repo_a / "notes.txt").write_text("gaussian damped mass in a non-Lean file\n")
    _git(s.env.repo_a, "add", "-A")
    _git(s.env.repo_a, "commit", "-m", "heat")
    reg = read_reg(s.env)
    reg["packages"]["demo-c"] = base_pkg("demo-c", tmp_path / "no-such-checkout", "gate-pending")
    write_reg(s.env, reg)
    return s


def search_json(lp, s, term, *extra):
    assert run_search(lp, s, term, "--offline", "--json", *extra) == 0


def test_unclaimed_declaration_is_found_with_its_hit_kind_and_trust(lp, srch, unclaimed, capsys):
    search_json(lp, unclaimed, "gaussian damped mass")
    p = json.loads(capsys.readouterr().out)
    reg = p["sources"][0]
    decl = [h for h in reg["hits"] if h["hit_kind"] == "declaration (unclaimed)"]
    assert [h["decl"] for h in decl] == ["DemoA.gaussianDampedMass", "DemoA.IsHeatAdmissible"]
    first = decl[0]
    assert first["package"] == "alpha" and first["trust"] == "none" and first["kind"] == "def"
    assert first["file"] == "DemoA/Heat.lean:10" and first["where"] == "name"
    assert first["summary"] == "The Gaussian-damped mass of a measure."
    assert decl[1]["where"] == "docstring" and decl[1]["file"] == "DemoA/Heat.lean:7"
    assert first["pin"] == _git(unclaimed.env.repo_a, "rev-parse", "HEAD").stdout.strip()
    for h in reg["hits"]:
        assert set(h) == set(srch.HIT_KEYS)


def test_declaration_hits_rank_after_claim_hits(lp, unclaimed, capsys):
    reg = read_reg(unclaimed.env)
    reg["packages"]["beta"]["claims"][0]["keywords"].append("heat")
    write_reg(unclaimed.env, reg)
    search_json(lp, unclaimed, "heat")
    hits = json.loads(capsys.readouterr().out)["sources"][0]["hits"]
    assert [(h["hit_kind"], h["decl"]) for h in hits] == [
        ("claim", "Demo.foo_le_bar"), ("declaration (unclaimed)", "DemoA.IsHeatAdmissible"),
        ("declaration (unclaimed)", "DemoA.heatFlowMass_le")]
    assert hits[0]["score"] == 2 and hits[1]["score"] == 1


def test_summary_names_the_blind_sources(lp, unclaimed, capsys):
    assert run_search(lp, unclaimed, "gaussian damped mass", "--offline") == 0
    out = capsys.readouterr().out.splitlines()
    assert ("registry coverage: 2 of 3 packages have no synced claims: 1 searched by declaration only, "
            "1 not searched (no checkout: demo-c)") in out
    assert out[-1].startswith("consulted: ")
    assert any(ln.startswith("  [1 name] alpha: DemoA.gaussianDampedMass  (def, declaration (unclaimed), "
                             "trust none, DemoA/Heat.lean:10)") for ln in out)


def test_json_coverage_and_not_searched(lp, unclaimed, capsys):
    search_json(lp, unclaimed, "gumbel")
    p = json.loads(capsys.readouterr().out)
    assert p["coverage"] == {"claims": 1, "declaration_only": 1, "not_searched": ["demo-c"],
                             "not_searched_reasons": [{"package": "demo-c", "code": "no-checkout", "remedy":
                                 "local_path is not a directory on this host; see `lean-pkg status demo-c "
                                 "--fields local_path,repo` and clone the repository there",
                                 "detail": None}],
                             "declaration_hits_omitted": 0, "declaration_files_capped": []}
    assert p["sources"][0]["hits"] == []


def test_a_non_git_checkout_is_not_searched(lp, unclaimed, tmp_path, capsys):
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    (plain / "X.lean").write_text("def gumbelThing : Nat := 0\n")
    reg = read_reg(unclaimed.env)
    reg["packages"]["demo-c"]["local_path"] = str(plain)
    write_reg(unclaimed.env, reg)
    search_json(lp, unclaimed, "gumbel")
    p = json.loads(capsys.readouterr().out)
    assert p["coverage"]["not_searched"] == ["demo-c"] and p["sources"][0]["hits"] == []


def test_untracked_lean_files_are_not_searched(lp, unclaimed, capsys):
    (unclaimed.env.repo_a / "DemoA" / "Draft.lean").write_text("def gumbelDraft : Nat := 0\n")
    search_json(lp, unclaimed, "gumbel")
    assert json.loads(capsys.readouterr().out)["sources"][0]["hits"] == []


def test_registry_not_selected_has_no_coverage(lp, unclaimed, capsys):
    search_json(lp, unclaimed, "demo", "--sources", "index")
    assert json.loads(capsys.readouterr().out)["coverage"] is None


def test_all_synced_says_so(lp, searchable, capsys):
    reg = read_reg(searchable.env)
    del reg["packages"]["alpha"]
    write_reg(searchable.env, reg)
    assert run_search(lp, searchable, "foo bound", "--offline") == 0
    assert "registry coverage: all 1 package(s) have synced claims" in capsys.readouterr().out


def test_lean_declarations_parser(srch):
    decls = srch.lean_declarations(HEAT_LEAN)
    assert [(d[0], d[1], d[2]) for d in decls] == [
        (7, "def", "DemoA.IsHeatAdmissible"), (10, "def", "DemoA.gaussianDampedMass"),
        (12, "theorem", "DemoA.heatFlowMass_le"), (15, "structure", "DemoA.Counter")]
    assert decls[2][3] is None and "Gaussian-damped" in decls[0][3]


def test_record_lists_the_declaration_and_the_coverage(lp, unclaimed):
    s = unclaimed
    assert run_search(lp, s, "gaussian damped mass", "--offline", "--package", "alpha", "--record") == 0
    date = lp.now_iso()[:10]
    text = (s.env.repo_a / "docs" / "prior-art" / f"{date}-gaussian-damped-mass.md").read_text()
    assert ("- `DemoA.gaussianDampedMass` in package `alpha` (def, declaration (unclaimed), trust none, "
            "`DemoA/Heat.lean:10`). The Gaussian-damped mass of a measure.") in text
    assert ("2 of 3 registered packages have no synced claims: 1 searched by declaration only, "
            "1 not searched (no checkout).") in text
    assert "demo-c" not in text


def test_declaration_hits_past_the_cap_are_counted_not_listed(lp, srch, unclaimed, monkeypatch, capsys):
    monkeypatch.setattr(srch, "DECL_HITS_PER_PACKAGE", 1)
    search_json(lp, unclaimed, "heat")
    p = json.loads(capsys.readouterr().out)
    assert [h["decl"] for h in p["sources"][0]["hits"]] == ["DemoA.IsHeatAdmissible"]
    assert p["coverage"]["declaration_hits_omitted"] == 1


def _decoys_and_target(repo, n_decoys):
    """`n_decoys` files that mention the query only in a comment, then ZTarget.lean with the one matching
    theorem (sorted last), as in the field report's synthetic fixture."""
    d = repo / "Decoy"
    d.mkdir()
    for i in range(n_decoys):
        (d / f"D{i:04d}.lean").write_text(f"-- talks about chaos moments in passing ({i})\ndef d{i} : Nat := {i}\n")
    (repo / "ZTarget.lean").write_text("/-- The chaos moments bound. -/\ntheorem chaosMoments_bound : True := trivial\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "decoys and target")


def test_a_match_past_200_candidate_files_is_found(lp, srch, unclaimed, capsys):
    """Field report: 200 files with the words in comments sorted before the target; the old 200-file slice
    returned ([], 0) and said nothing. The target in the 201st candidate file must be found."""
    _decoys_and_target(unclaimed.env.repo_a, 200)
    search_json(lp, unclaimed, "chaos moments")
    p = json.loads(capsys.readouterr().out)
    decls = [h["decl"] for h in p["sources"][0]["hits"] if h.get("hit_kind") == srch.DECL_HIT_KIND]
    assert decls == ["chaosMoments_bound"]
    # no file cap was hit: the registry's only incomplete reason is demo-c, registered without a checkout (#376)
    assert p["coverage"]["declaration_files_capped"] == []
    assert p["sources"][0]["incomplete"] == ("1 registered package(s) not searched (no checkout) "
                                             "(see the coverage line)")


def test_a_file_cap_that_is_reached_is_reported_and_makes_the_search_incomplete(lp, srch, unclaimed,
                                                                                 monkeypatch, capsys):
    monkeypatch.setattr(srch, "DECL_FILES_MAX", 3)
    _decoys_and_target(unclaimed.env.repo_a, 4)
    search_json(lp, unclaimed, "chaos moments")
    p = json.loads(capsys.readouterr().out)
    assert p["coverage"]["declaration_files_capped"] == [{"package": "alpha", "candidates": 5, "inspected": 3,
                                                         "unreadable": 0}]
    assert p["truncated_sources"] == ["registry"] and p["complete"] is False
    assert run_search(lp, unclaimed, "chaos moments", "--offline") == 0
    out = capsys.readouterr().out
    assert "alpha: only 3 of 5 candidate file(s) inspected (file cap), so a zero there is not an absence" in out
    assert "registry: declaration search hit a file cap" in out


def test_the_display_cap_is_not_a_file_cap(lp, srch, unclaimed, capsys):
    """More matching declarations than are listed: counted as not listed, never reported as a file cap."""
    body = "".join(f"theorem chaosMoments_{i} : True := trivial\n" for i in range(12))
    (unclaimed.env.repo_a / "Many.lean").write_text(body)
    _git(unclaimed.env.repo_a, "add", "-A")
    _git(unclaimed.env.repo_a, "commit", "-qm", "many")
    search_json(lp, unclaimed, "chaos moments")
    p = json.loads(capsys.readouterr().out)
    assert p["coverage"]["declaration_hits_omitted"] == 2 and p["coverage"]["declaration_files_capped"] == []


def test_each_not_searched_package_carries_a_redacted_reason_and_remedy(lp, srch, unclaimed, tmp_path,
                                                                         monkeypatch, capsys):
    """Field report (R2): `not_searched` listed names only, so a missing checkout, a missing path, a checkout
    without a commit and a failed scan all looked alike. Each now has a code and a read-only remedy keyed by
    the package name; no host path appears, and the name list itself is unchanged."""
    from .conftest import base_pkg
    plain = tmp_path / "plain-dir"                               # a directory that is not a git checkout
    plain.mkdir()
    (plain / "X.lean").write_text("def gumbelThing : Nat := 0\n")
    broken = tmp_path / "broken-repo"                             # a git checkout where the scan will fail
    broken.mkdir()
    _git(broken, "init", "-q")
    (broken / "Y.lean").write_text("def gumbelY : Nat := 0\n")
    _git(broken, "add", "-A")
    _git(broken, "commit", "-qm", "y")
    reg = read_reg(unclaimed.env)
    reg["packages"]["demo-d"] = base_pkg("demo-d", plain, "gate-pending")
    reg["packages"]["demo-e"] = base_pkg("demo-e", tmp_path, "gate-pending", local_path="")
    reg["packages"]["demo-f"] = base_pkg("demo-f", broken, "gate-pending")
    write_reg(unclaimed.env, reg)
    real_git = srch.git_bytes

    def git(repo, *args, **kw):
        if args and args[0] == "grep" and str(repo).endswith("broken-repo"):
            import subprocess
            return subprocess.CompletedProcess(["git", *args], 128, b"", b"fatal: something broke")
        return real_git(repo, *args, **kw)
    monkeypatch.setattr(srch, "git_bytes", git)
    search_json(lp, unclaimed, "gumbel")
    out = capsys.readouterr().out
    p = json.loads(out)
    assert p["coverage"]["not_searched"] == ["demo-c", "demo-d", "demo-e", "demo-f"]   # unchanged shape
    codes = {r["package"]: r["code"] for r in p["coverage"]["not_searched_reasons"]}
    assert codes == {"demo-c": "no-checkout", "demo-d": "no-git-head", "demo-e": "no-path", "demo-f": "scan-error"}
    remedies = {r["package"]: r["remedy"] for r in p["coverage"]["not_searched_reasons"]}
    assert "`lean-pkg status demo-e --fields local_path,repo`" in remedies["demo-e"]
    assert str(tmp_path) not in json.dumps(p["coverage"]["not_searched_reasons"])
    assert "fatal: something broke" not in out
    assert run_search(lp, unclaimed, "gumbel", "--offline") == 0
    text = capsys.readouterr().out
    assert ("4 not searched (no path: demo-e; no checkout: demo-c; no git HEAD: demo-d; scan error: demo-f "
            "(git grep exit 128: cause not recognised))" in text)


def test_the_record_groups_reasons_without_names(lp, unclaimed, tmp_path):
    from .conftest import base_pkg
    plain = tmp_path / "plain-dir"
    plain.mkdir()
    reg = read_reg(unclaimed.env)
    reg["packages"]["demo-d"] = base_pkg("demo-d", plain, "gate-pending")
    write_reg(unclaimed.env, reg)
    assert run_search(lp, unclaimed, "gaussian damped mass", "--offline", "--package", "alpha", "--record") == 0
    date = lp.now_iso()[:10]
    text = (unclaimed.env.repo_a / "docs" / "prior-art" / f"{date}-gaussian-damped-mass.md").read_text()
    assert "2 not searched (no checkout: 1, no git HEAD: 1)." in text and "demo-d" not in text


NESTED_LEAN = """\
namespace Contrib

/-- Lives only in the nested package. -/
theorem nestedOnlyThing : True := trivial

end Contrib
"""


def test_a_nested_registered_package_is_not_scanned_as_part_of_its_parent(lp, srch, unclaimed, capsys):
    """Field report: alpha's checkout contains another registered package (contrib/); the declaration scan of
    alpha attributed contrib's declarations to alpha. Each file now belongs to the innermost package."""
    s = unclaimed
    (s.env.repo_a / "contrib").mkdir()
    (s.env.repo_a / "contrib" / "Contrib.lean").write_text(NESTED_LEAN)
    _git(s.env.repo_a, "add", "-A")
    _git(s.env.repo_a, "commit", "-m", "contrib")
    reg = read_reg(s.env)
    reg["packages"]["alpha-contrib"] = base_pkg("alpha-contrib", s.env.repo_a / "contrib", "gate-pending")
    write_reg(s.env, reg)
    search_json(lp, s, "nested only thing")
    p = json.loads(capsys.readouterr().out)
    hits = [h for h in p["sources"][0]["hits"] if h.get("decl") == "Contrib.nestedOnlyThing"]
    assert [h["package"] for h in hits] == ["alpha-contrib"]
    assert hits[0]["file"].startswith("Contrib.lean:")
    search_json(lp, s, "gaussian damped mass")        # the parent's own declarations are still found
    p = json.loads(capsys.readouterr().out)
    assert any(h["package"] == "alpha" for h in p["sources"][0]["hits"])
