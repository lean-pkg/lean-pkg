#!/usr/bin/env python3
"""lean_pkg.dup_constants: a constant in the constant tables of more than one module, judged fail-closed.

The verdict tests feed synthetic probe records (the shape scripts/DupConstants.lean prints). The real-Lean
tests build a tiny project on leanprover/lean4:v4.35.0-rc2 when it is installed, in which two modules A and
B both carry
  - `g.congr_simp`, realized by `simp only` under a dependent argument in each module (the field case),
  - `Dup.equal`, added by `addDecl` with the same proof in both,
  - `Dup.unequal`, added by `addDecl` with two different proofs of `True` (Lean imports both: its
    duplicate check compares the type, not the proof),
and C, D carry `Dup.badType` with two different types (Lean refuses that import itself)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lean_pkg import cli
from lean_pkg import comparator_receipt as cr
from lean_pkg import dup_constants as dc

TC = "leanprover/lean4:v4.35.0-rc2"
TC_DIR = "leanprover--lean4---v4.35.0-rc2"


def roots(tmp_path) -> dc.Roots:
    return dc.Roots(tmp_path / "proj" / dc.PKG_LIB, {"dep": tmp_path / "proj" / ".lake/packages/dep" / dc.PKG_LIB},
                    tmp_path / "tc" / "lib" / "lean")


def olean(r: dc.Roots, origin: str, mod: str) -> str | None:
    base = {"project": r.project, "dep": r.packages["dep"], "toolchain": r.toolchain}.get(origin)
    return None if base is None else str(base / f"{mod}.olean")


def rec(r: dc.Roots, name: str, owners: list[tuple], winner: str | None = "last", recorded: str | None = "first"):
    """owners: (module, origin, differences-vs-winner)."""
    mods = [m for m, _, _ in owners]
    win = mods[-1] if winner == "last" else winner
    return {"name": name,
            "owners": [{"module": m, "olean": olean(r, o, m), "constant": {"kind": "theorem"},
                        "isWinner": m == win, "vsWinner": d} for m, o, d in owners],
            "winner": None if win is None else {"kind": "theorem"}, "winnerModule": win,
            "recordedOwner": mods[0] if recorded == "first" else recorded}


def done(n: int) -> dict:
    return {"done": True, "modules": 5, "constants": 50, "duplicates": n}


def by_name(res: dict) -> dict:
    return {e["name"]: e for e in res["duplicates"] + res["out_of_scope"]}


def test_equal_duplicate_is_accepted_with_owners_and_winner(tmp_path):
    r = roots(tmp_path)
    res = dc.judge([rec(r, "f.congr_simp", [("A", "project", []), ("B", "project", [])])], done(1), r)
    assert res["ok"] and res["verdict"] == "accepted"
    e = by_name(res)["f.congr_simp"]
    assert e["verdict"] == "accepted" and e["equal"] and e["winner"] == "B" and e["recorded_owner"] == "A"
    assert [o["module"] for o in e["owners"]] == ["A", "B"]


def test_unequal_duplicate_is_refused_with_the_differing_field(tmp_path):
    r = roots(tmp_path)
    res = dc.judge([rec(r, "f._proof_1", [("A", "project", ["value", "all"]), ("B", "project", [])])], done(1), r)
    e = by_name(res)["f._proof_1"]
    assert not res["ok"] and e["verdict"] == "refused" and not e["equal"]
    assert e["differences"] == {"A": ["value", "all"]}
    assert any("A differs from the winner in value, all" in x for x in e["reasons"])


def test_an_owner_pin_never_excuses_an_unequal_duplicate(tmp_path):
    r = roots(tmp_path)
    res = dc.judge([rec(r, "x", [("A", "project", ["type"]), ("B", "project", [])])], done(1), r,
                   allow={"x": frozenset({"A", "B"})})
    assert not res["ok"]


@pytest.mark.parametrize("winner,recorded,origin,why", [
    (None, "first", "project", "env.find? does not return"),
    ("Z", "first", "project", "winner is none of the owners"),
    ("last", "Z", "project", "records 'Z', not an owner"),
    ("last", None, "project", "records None"),
    ("last", "first", "elsewhere", "outside the project"),
])
def test_unknown_ownership_is_refused(tmp_path, winner, recorded, origin, why):
    r = roots(tmp_path)
    x = rec(r, "m.match_1", [("A", "project", []), ("B", origin, [])], winner=winner, recorded=recorded)
    if winner is None:
        for o in x["owners"]:
            o["vsWinner"] = None
    res = dc.judge([x], done(1), r)
    e = by_name(res)["m.match_1"]
    assert not res["ok"] and e["verdict"] == "refused"
    assert any(why in reason for reason in e["reasons"]), e["reasons"]


def test_a_third_owner_needs_the_exact_pin(tmp_path):
    r = roots(tmp_path)
    three = [("A", "project", []), ("B", "project", []), ("C", "project", [])]
    assert not dc.judge([rec(r, "n.eq_1", three)], done(1), r)["ok"]
    assert dc.judge([rec(r, "n.eq_1", three)], done(1), r, allow={"n.eq_1": frozenset("ABC")})["ok"]
    res = dc.judge([rec(r, "n.eq_1", three)], done(1), r, allow={"n.eq_1": frozenset("AB")})
    assert not res["ok"] and any("not pinned: C" in x for x in by_name(res)["n.eq_1"]["reasons"])
    two = [("A", "project", []), ("B", "project", [])]
    res = dc.judge([rec(r, "n.eq_1", two)], done(1), r, allow={"n.eq_1": frozenset("ABC")})
    assert not res["ok"] and any("pinned but absent: C" in x for x in by_name(res)["n.eq_1"]["reasons"])


def test_a_pin_that_matches_nothing_is_refused(tmp_path):
    r = roots(tmp_path)
    res = dc.judge([], done(0), r, allow={"gone": frozenset("AB")})
    assert not res["ok"] and "matches nothing" in res["detail"]


def test_generated_and_private_names_are_judged_like_any_other(tmp_path):
    r = roots(tmp_path)
    names = ["_private.M.0.f._proof_3", "f.match_2", "f.eq_1", "f.congr_simp"]
    res = dc.judge([rec(r, n, [("A", "project", ["value"]), ("B", "project", [])]) for n in names], done(4), r)
    assert {e["name"] for e in res["duplicates"] if e["verdict"] == "refused"} == set(names)


def test_scope_lists_but_does_not_judge_library_only_duplicates(tmp_path):
    r = roots(tmp_path)
    recs = [rec(r, "Init.x", [("Init.A", "toolchain", ["value"]), ("Init.B", "toolchain", [])]),
            rec(r, "Dep.y", [("Dep.A", "dep", ["value"]), ("Dep.B", "dep", [])]),
            rec(r, "mine", [("Dep.A", "dep", []), ("A", "project", [])])]
    res = dc.judge(recs, done(3), r)
    assert res["ok"] and [e["name"] for e in res["duplicates"]] == ["mine"]
    assert {e["name"] for e in res["out_of_scope"]} == {"Init.x", "Dep.y"}
    assert res["counts"]["out_of_scope"] == {"accepted": 0, "refused": 2}      # listed per scope, never summed
    assert not dc.judge(recs, done(3), r, scope="packages")["ok"]
    res = dc.judge(recs, done(3), r, scope="all")
    assert not res["ok"] and len(res["duplicates"]) == 3


def test_an_incomplete_or_inconsistent_probe_is_refused(tmp_path):
    r = roots(tmp_path)
    one = rec(r, "a", [("A", "project", []), ("B", "project", [])])
    assert not dc.judge([one], None, r)["ok"]
    assert not dc.judge([one], done(2), r)["ok"]
    with pytest.raises(dc.ProbeError):
        dc.parse_probe('{"done": true, "duplicates": 0}\n{"name": "a", "owners": []}\n')
    with pytest.raises(dc.ProbeError):
        dc.parse_probe("not json\n")
    with pytest.raises(dc.ProbeError):
        dc.parse_probe('{"something": 1}\n')


def test_allow_owners_syntax():
    assert dc.parse_allow(["f.congr_simp=A,B, C"]) == {"f.congr_simp": frozenset("ABC")}
    for bad in (["f"], ["f=A"], ["=A,B"], ["f=A,B", "f=A,C"]):
        with pytest.raises(ValueError):
            dc.parse_allow(bad)
    assert dc.main(["--project", ".", "M", "--allow-owners", "f=A"]) == 2


def test_a_missing_project_or_module_is_refused(tmp_path, capsys):
    assert dc.main(["--project", str(tmp_path / "nope"), "M"]) == 1
    assert "refused" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------------
# real Lean

def _toolchain_bin():
    b = cr.elan_home() / "toolchains" / TC_DIR / "bin"
    return b if (b / "lake").exists() and (b / "lean").exists() else None


def _addDecls(m: str) -> str:
    v = "mkConst ``True.intro" if m == "A" else "mkApp2 (mkConst ``id [Level.zero]) (mkConst ``True) (mkConst ``True.intro)"
    return f"""import Defs
import Lean
theorem cg{m} (n : Nat) (h : 0 < n + 0) : g (n + 0) h = g n (by simpa using h) := by simp only [Nat.add_zero]
open Lean in
run_meta addDecl (.thmDecl {{ name := `Dup.equal, levelParams := [], type := mkConst ``True, value := mkConst ``True.intro, all := [`Dup.equal] }})
open Lean in
run_meta addDecl (.thmDecl {{ name := `Dup.unequal, levelParams := [], type := mkConst ``True, value := {v}, all := [`Dup.unequal] }})
"""


def _badType(m: str) -> str:
    t = "mkConst ``True" if m == "C" else "mkApp3 (mkConst ``Eq [Level.one]) (mkConst ``Nat) (mkNatLit 0) (mkNatLit 0)"
    v = "mkConst ``True.intro" if m == "C" else "mkApp2 (mkConst ``Eq.refl [Level.one]) (mkConst ``Nat) (mkNatLit 0)"
    return f"""import Lean
open Lean in
run_meta addDecl (.thmDecl {{ name := `Dup.badType, levelParams := [], type := {t}, value := {v}, all := [`Dup.badType] }})
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tc_bin = _toolchain_bin()
    if tc_bin is None:
        pytest.skip(f"{TC} is not installed")
    d = tmp_path_factory.mktemp("dupproj")
    (d / "lean-toolchain").write_text(TC + "\n")
    (d / "lakefile.toml").write_text('name = "dup"\ndefaultTargets = ["Top"]\n\n[[lean_lib]]\nname = "Top"\n'
                                     'roots = ["Defs", "A", "B", "C", "D", "Top"]\n')
    (d / "Defs.lean").write_text("def g (n : Nat) (_h : 0 < n) : Nat := n\n")
    for m in "AB":
        (d / f"{m}.lean").write_text(_addDecls(m))
    for m in "CD":
        (d / f"{m}.lean").write_text(_badType(m))
    (d / "Top.lean").write_text("import A\nimport B\n")
    env = cli.scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(tc_bin), env.get("PATH", "")]), "ELAN_TOOLCHAIN": TC,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(d / "lake-cache")})
    p = subprocess.run([str(tc_bin / "lake"), "build", "Top", "C", "D"], cwd=d, env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    return d


def test_real_equal_duplicates_are_accepted_and_the_unequal_one_refused(built):
    res = dc.duplicate_constants(built, ["Top"], sandboxed=False)
    got = by_name(res)
    for n in ("g.congr_simp", "Dup.equal"):
        e = got[n]
        assert e["verdict"] == "accepted" and e["equal"], e
        assert [o["module"] for o in e["owners"]] == ["A", "B"] and e["winner"] in ("A", "B")
        assert e["origins"] == ["project"]
    e = got["Dup.unequal"]
    assert e["verdict"] == "refused" and list(e["differences"].values()) == [["value"]], e
    assert not res["ok"] and [x["name"] for x in res["duplicates"] if x["verdict"] == "refused"] == ["Dup.unequal"]
    assert res["out_of_scope"] and all(set(x["origins"]) == {"toolchain"} for x in res["out_of_scope"])


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap is not installed")
def test_real_probe_runs_in_the_sandbox(built):
    res = dc.duplicate_constants(built, ["A"], sandboxed=True)
    assert res["ok"] and res["duplicates"] == [], res["detail"]


def test_real_unbuilt_module_and_lean_refused_import(built):
    res = dc.duplicate_constants(built, ["Nope"], sandboxed=False)
    assert not res["ok"] and "ownership cannot be determined" in res["detail"]
    res = dc.duplicate_constants(built, ["C", "D"], sandboxed=False)
    assert not res["ok"] and "Lean refused the import itself" in res["detail"], res["detail"]


def test_real_command_line_exit_codes(built, capsys):
    assert dc.main(["--project", str(built), "A", "--no-sandbox"]) == 0
    assert dc.main(["--project", str(built), "Top", "--no-sandbox"]) == 1
    out = capsys.readouterr().out
    assert "refused: Dup.unequal [A, B" in out and "accepted: g.congr_simp [A, B" in out
