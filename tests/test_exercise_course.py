#!/usr/bin/env python3
"""lean-exercise for course use: --project (Mathlib), --forbid, batch.

Unit tests need no Lean: a synthetic NDJSON export for the dependency walk, the stub `lake` of
test_exercise for the command. The integration tests at the end run the real `lake comparator` on a
Mathlib project and are opt-in: LEAN_EXERCISE_INTEGRATION=1 and LEAN_EXERCISE_MATHLIB_PROJECT=DIR, a
project pinning Mathlib on leanprover/lean4:v4.35.0-rc2 (or later) whose Mathlib is built."""

from __future__ import annotations

import csv
import json
import os
import pathlib
import subprocess
import sys

import pytest

from lean_pkg import comparator_receipt as cr
from lean_pkg import exercise as ex
from lean_pkg import exercise_forbid as fb

from .test_exercise import CHALLENGE, TC_DIR, _exe, calls, fake, files, run  # noqa: F401  (fake: fixture)


# ------------------------------------------------------------------------------- a synthetic export

def export(path: pathlib.Path, decls: dict[str, tuple[list[str], list[str]]], kind: str = "thm") -> pathlib.Path:
    """NDJSON in lean4export's format: each declaration's type and value are app spines of constants."""
    lines, names, n_expr = [], {}, [0]

    def name(s: str) -> int:
        pre = 0
        comps = s.split(".")
        for i in range(len(comps)):
            key = ".".join(comps[:i + 1])
            if key not in names:
                names[key] = len(names) + 1
                comp = comps[i]
                body = {"num": {"pre": pre, "i": int(comp)}} if comp.isdigit() else {"str": {"pre": pre, "str": comp}}
                lines.append({**body, "in": names[key]})
            pre = names[key]
        return pre

    def expr(consts: list[str]) -> int:
        def new(obj):
            n_expr[0] += 1
            lines.append({**obj, "ie": n_expr[0]})
            return n_expr[0]
        e = new({"sort": 0})
        for c in consts:
            e = new({"app": {"fn": e, "arg": new({"const": {"name": name(c), "us": []}})}})
        return e

    lines.insert(0, {"meta": {"format": {"version": "3.1.0"}}})
    for d, (ty, val) in decls.items():
        lines.append({kind: {"name": name(d), "levelParams": [], "type": expr(ty), "value": expr(val), "all": []}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return path


LIB = {"Mathlib.bad", "Mathlib.ok", "Mathlib.wraps_bad", "Stmt.forbidden_def"}
DECLS = {
    "Mathlib.bad": ([], []), "Mathlib.ok": ([], []), "Stmt.forbidden_def": ([], []),
    "Mathlib.wraps_bad": ([], ["Mathlib.bad"]),          # a library lemma proved with the forbidden one
    "direct": (["Stmt.forbidden_def"], ["Mathlib.bad", "Stmt.forbidden_def"]),
    "h2": ([], ["Mathlib.bad"]), "h1": ([], ["h2"]),
    "laundered": ([], ["h1"]),
    "_private.Solution.0.p": ([], ["Mathlib.bad"]), "privately": ([], ["_private.Solution.0.p"]),
    "clean": (["Stmt.forbidden_def"], ["Mathlib.ok", "Stmt.forbidden_def"]),
    "wrapped": ([], ["Mathlib.wraps_bad"]),
}


def lib_classify(seen: list):
    def classify(names):
        seen.append(sorted(".".join(map(str, n)) for n in names))
        return {n for n in names if ".".join(map(str, n)) in LIB}
    return classify


@pytest.mark.parametrize("thm,hits,via", [
    ("direct", ["Mathlib.bad"], []), ("laundered", ["Mathlib.bad"], ["h1", "h2"]),
    ("privately", ["Mathlib.bad"], ["_private.Solution.0.p"]), ("clean", [], None), ("wrapped", [], None)])
def test_walk_follows_the_solutions_own_declarations_only(tmp_path, thm, hits, via):
    e = fb.read_export(export(tmp_path / "s.ndjson", DECLS))
    seen: list = []
    found = fb.find_forbidden(e, thm, ["Mathlib.bad", "Stmt."], [], "solution", lib_classify(seen))
    assert [h["constant"] for h in found] == hits
    if hits:
        assert found[0]["via"] == via
    assert not any("Mathlib.bad" in batch and thm == "wrapped" for batch in seen[1:])


def test_scope_all_enters_library_constants(tmp_path):
    e = fb.read_export(export(tmp_path / "s.ndjson", DECLS))
    found = fb.find_forbidden(e, "wrapped", [], ["Mathlib.bad"], "all", None)
    assert [h["constant"] for h in found] == ["Mathlib.bad"] and found[0]["via"] == ["Mathlib.wraps_bad"]


def test_a_constant_of_the_statement_is_never_reported(tmp_path):
    e = fb.read_export(export(tmp_path / "s.ndjson", DECLS))
    assert fb.find_forbidden(e, "clean", [], ["Stmt.forbidden_def"], "solution", lib_classify([])) == []


def test_unknown_theorem_and_garbage_fail_closed(tmp_path):
    e = fb.read_export(export(tmp_path / "s.ndjson", DECLS))
    with pytest.raises(fb.ExportError):
        fb.find_forbidden(e, "nope", ["x"], [], "solution", lib_classify([]))
    (tmp_path / "g.ndjson").write_text('{"what": 1}\n')
    with pytest.raises(fb.ExportError):
        fb.read_export(tmp_path / "g.ndjson")


def test_names_literals_and_private_modules():
    assert fb.literal(("Nat", "add_zero")) == "«Nat».«add_zero»"
    assert fb.literal(("_private", "Solution", 0, "p")) is None
    assert fb.private_module(("_private", "Mathlib", "A", 0, "p")) == "Mathlib.A"
    assert fb.private_module(("Mathlib", "A")) is None
    assert fb.match("MeasurableSpace.DynkinSystem.ext", ["MeasurableSpace.DynkinSystem."], [])
    assert not fb.match("MeasurableSpace.induction_on_inter'", [], ["MeasurableSpace.induction_on_inter"])


def test_the_export_sandbox_has_no_network_and_nothing_writable(tmp_path):
    args = fb.bwrap_args([tmp_path], {"LEAN_PATH": "x"}, tmp_path)
    assert "--share-net" not in args and "--bind" not in args and "--unshare-all" in args
    assert args[args.index("--ro-bind", 3) + 1] == str(tmp_path) and args[-1] == "--"


# ------------------------------------------------------------------------------- --project

def project(root: pathlib.Path, toolchain="leanprover/lean4:v4.35.0-rc2", mathlib=True, built=True) -> pathlib.Path:
    p = root / "course"
    pk = p / ".lake" / "packages"
    pkgs = [{"name": "mathlib", "type": "git", "url": "https://github.com/leanprover-community/mathlib4.git",
             "rev": "a" * 40, "inputRev": "a" * 40, "inherited": False, "scope": ""},
            {"name": "batteries", "type": "git", "url": "https://github.com/leanprover-community/batteries",
             "rev": "b" * 40, "inputRev": "main", "inherited": True, "scope": "leanprover-community"}]
    if not mathlib:
        pkgs = pkgs[1:]
    for x in pkgs:
        (pk / x["name"] / ".lake" / "build" / "lib" / "lean").mkdir(parents=True)
    if built and mathlib:
        (pk / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean").write_bytes(b"")
    (p / "lean-toolchain").write_text(toolchain + "\n")
    (p / "lake-manifest.json").write_text(json.dumps({"version": "1.2.0", "packagesDir": ".lake/packages",
                                                      "packages": pkgs, "name": "course"}))
    return p


MATHLIB_CHALLENGE = "import Mathlib.Data.Real.Basic\ntheorem t (x : ℝ) : x + 0 = x := by sorry\n"
MATHLIB_SOLUTION = "import Mathlib.Data.Real.Basic\ntheorem t (x : ℝ) : x + 0 = x := add_zero x\n"


def test_mathlib_import_without_project_is_still_refused(fake, capsys):
    rc, out, err = run(capsys, files(fake.root, MATHLIB_CHALLENGE, MATHLIB_SOLUTION))
    assert rc == 2 and out["usage"] and "needs --project DIR" in err and calls(fake) == []


def test_project_writes_requires_a_read_only_link_and_the_wrapper(fake, capsys, monkeypatch):
    monkeypatch.setattr(ex, "real_bwrap", lambda: "/usr/bin/bwrap")
    p = project(fake.root)
    kept = fake.root / "kept"
    rc, out, _ = run(capsys, files(fake.root, MATHLIB_CHALLENGE, MATHLIB_SOLUTION)
                     + ["--project", str(p), "--keep", str(kept)])
    assert rc == 0 and out["mathlib"] is True and out["toolchain"] == "leanprover/lean4:v4.35.0-rc2"
    lakefile = (kept / "lakefile.toml").read_text()
    assert 'name = "mathlib"' in lakefile and "mathlib4.git" in lakefile and "batteries" not in lakefile
    assert (kept / ".lake" / "packages").resolve() == (p / ".lake" / "packages").resolve()
    man = json.loads((kept / "lake-manifest.json").read_text())
    assert [x["name"] for x in man["packages"]] == ["mathlib", "batteries"] and man["name"] == "exercise"
    wrapper = (kept / "bwrap-wrapper.sh").read_text()
    assert str((p / ".lake" / "packages").resolve()) in wrapper and "/usr/bin/bwrap" in wrapper
    assert calls(fake)[0]["argv"][:1] == ["comparator"]


def test_the_wrapper_binds_read_only_drops_the_network_and_fails_closed(tmp_path, monkeypatch):
    echo = tmp_path / "bwrap"
    _exe(echo, "#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done\n")
    monkeypatch.setenv("COMPARATOR_BWRAP", str(echo))
    w = ex.write_wrapper(tmp_path, [tmp_path / "pk s"])
    line = ["--ro-bind", "/", "/", "--share-net", "--chdir", "/w", "--", "lake", "env", "--share-net"]
    got = subprocess.run([str(w)] + line, capture_output=True, text=True, env={}).stdout.splitlines()
    assert got == ["--ro-bind", "/", "/", "--chdir", "/w", "--ro-bind", str(tmp_path / "pk s"),
                   str(tmp_path / "pk s"), "--", "lake", "env", "--share-net"]
    bad = subprocess.run([str(w), "--ro-bind", "/", "/", "--", "x"], capture_output=True, text=True, env={})
    assert bad.returncode == 125 and bad.stdout == ""


@pytest.mark.parametrize("kw,needle", [({"mathlib": False}, "no mathlib package"),
                                       ({"built": False}, "Mathlib is not built"),
                                       ({"toolchain": "leanprover/lean4:v4.34.0"}, "v4.35.0-rc2 or later")])
def test_a_project_that_cannot_be_judged_offline_is_refused(fake, capsys, kw, needle):
    p = project(fake.root, **kw)
    rc, out, err = run(capsys, files(fake.root, MATHLIB_CHALLENGE, MATHLIB_SOLUTION) + ["--project", str(p)])
    assert rc == 2 and needle in err and calls(fake) == []


def test_a_toolchain_other_than_the_projects_is_refused(fake, capsys):
    p = project(fake.root)
    rc, _, err = run(capsys, files(fake.root, MATHLIB_CHALLENGE, MATHLIB_SOLUTION)
                     + ["--project", str(p), "--toolchain", "v4.36.0"])
    assert rc == 2 and "pins leanprover/lean4:v4.35.0-rc2" in err


# ------------------------------------------------------------------------------- --forbid (stubbed walk)

def test_forbidden_hit_overrides_accepted_and_names_the_constant(fake, capsys, monkeypatch):
    seen = {}

    def walk(plan, work, solution, names):
        seen.update(plan["forbid"])
        return {n: [{"constant": "M.induction_on_inter", "via": ["helper"]}] for n in names}
    monkeypatch.setattr(ex, "forbidden", walk)
    rc, out, _ = run(capsys, files(fake.root) + ["--forbid", "M.D.", "--forbid-exact", "M.induction_on_inter"])
    row = out["theorems"][0]
    assert rc == 1 and row["verdict"] == "forbidden_constant" and row["constant"] == "M.induction_on_inter"
    assert "through helper" in row["detail"] and out["forbid"]["scope"] == "solution"
    assert seen == {"prefixes": ["M.D."], "exact": ["M.induction_on_inter"], "scope": "solution"}


def test_forbid_check_failure_is_a_tool_error_never_a_pass(fake, capsys, monkeypatch):
    def walk(*a):
        raise fb.ExportError("leanexport failed: boom")
    monkeypatch.setattr(ex, "forbidden", walk)
    rc, out, _ = run(capsys, files(fake.root) + ["--forbid", "X."])
    assert rc == 1 and out["theorems"][0]["verdict"] == "tool_error" and "boom" in out["theorems"][0]["detail"]


def test_forbid_is_not_run_on_a_rejected_theorem_or_without_patterns(fake, capsys, monkeypatch):
    monkeypatch.setattr(ex, "forbidden", lambda *a: pytest.fail("walked"))
    rc, out, _ = run(capsys, files(fake.root, solution="-- FAKE t: sorry\n") + ["--forbid", "X."])
    assert out["theorems"][0]["verdict"] == "illegal_axiom"
    rc, out, _ = run(capsys, files(fake.root))
    assert rc == 0 and "forbid" not in out
    rc, _, err = run(capsys, files(fake.root) + ["--forbid", "a b"])
    assert rc == 2


# ------------------------------------------------------------------------------- batch

def batch_argv(root: pathlib.Path, sols: dict[str, str]) -> list[str]:
    (root / "C.lean").write_text(CHALLENGE)
    d = root / "subs"
    d.mkdir()
    for n, s in sols.items():
        (d / n).write_text(s)
    return ["batch", "--challenge", str(root / "C.lean"), "--solutions", str(d), "--csv", str(root / "g.csv"),
            "--logs", str(root / "logs")]


def test_batch_writes_one_row_and_one_log_per_file(fake, capsys):
    ok = "theorem t (n : Nat) : n + 0 = n := by simp\n"
    argv = batch_argv(fake.root, {"b.lean": ok, "a.lean": ok + "-- FAKE t: sorry\n",
                                  "c.lean": "import Mathlib\n" + ok, "notes.txt": "x"})
    rc, out, _ = run(capsys, argv)
    rows = list(csv.reader((fake.root / "g.csv").open()))
    assert rows[0] == ["file", "sha256", "result", "accepted", "total", "t", "detail"]
    assert [(r[0], r[2], r[5]) for r in rows[1:]] == [("a.lean", "fail", "illegal_axiom"), ("b.lean", "pass", "accepted"),
                                                      ("c.lean", "refused", "refused")]
    assert "needs --project" in rows[3][6] and str(fake.root) not in (fake.root / "g.csv").read_text()
    assert sorted(p.name for p in (fake.root / "logs").iterdir()) == ["a.log", "b.log", "c.log"]
    assert "Illegal axiom" in (fake.root / "logs" / "a.log").read_text()
    assert rc == 1 and out["schema"] == "exercise-batch/1" and out["summary"] == {"passed": 1, "total": 3}
    assert len(calls(fake)) == 2


@pytest.mark.parametrize("extra,needle", [(["--no-sandbox"], "--no-sandbox is refused"), ([], "already exists")])
def test_batch_refusals(fake, capsys, extra, needle):
    argv = batch_argv(fake.root, {"a.lean": "theorem t (n : Nat) : n + 0 = n := by simp\n"})
    if not extra:
        (fake.root / "g.csv").write_text("keep me")
    rc, _, err = run(capsys, argv + extra)
    assert rc == 2 and needle in err and calls(fake) == []


# ------------------------------------------------------------------------------- integration (Mathlib)

def _mathlib_ready() -> str | None:
    if os.environ.get("LEAN_EXERCISE_INTEGRATION") != "1":
        return "set LEAN_EXERCISE_INTEGRATION=1 and LEAN_EXERCISE_MATHLIB_PROJECT=DIR to run the Mathlib cases"
    p = os.environ.get("LEAN_EXERCISE_MATHLIB_PROJECT")
    if not p:
        return "set LEAN_EXERCISE_MATHLIB_PROJECT=DIR (a built Mathlib project on v4.35.0-rc2 or later)"
    if not (cr.elan_home() / "toolchains" / TC_DIR / "bin" / "nanoda_bin").exists():
        return "leanprover/lean4:v4.35.0-rc2 is not installed"
    if not cr.host_checks()["mechanism_detail"]["toolchain"]["ok"]:
        return "comparator host-check: the toolchain mechanism fails here"
    return None


HEAD = "import Mathlib.MeasureTheory.PiSystem\nopen MeasurableSpace\n\n"
PL = """{kw} {name} {{α : Type*}} [m : MeasurableSpace α] {{C : Set (Set α)}} (h_eq : m = generateFrom C)
    (h_inter : IsPiSystem C) {{p : ∀ s : Set α, MeasurableSet s → Prop}}
    (h_empty : p ∅ MeasurableSet.empty)
    (h_basic : ∀ t (ht : t ∈ C), p t (h_eq ▸ measurableSet_generateFrom ht))
    (h_compl : ∀ t (htm : MeasurableSet t), p t htm → p tᶜ htm.compl)
    (h_union : ∀ (f : ℕ → Set α), Pairwise (fun i j => Disjoint (f i) (f j)) →
      ∀ (hf : ∀ i, MeasurableSet (f i)), (∀ i, p (f i) (hf i)) → p (⋃ i, f i) (MeasurableSet.iUnion hf)) :
    ∀ t (ht : MeasurableSet t), p t ht := {proof}
"""
U = "theorem u (s t : Set ℕ) : s ∩ t ⊆ s := {}\n\n"
DIRECT = "induction_on_inter h_eq h_inter h_empty h_basic h_compl h_union"
VIA = "helper h_eq h_inter h_empty h_basic h_compl h_union"
M_CHALLENGE = HEAD + U.format("by sorry") + PL.format(kw="theorem", name="pl", proof="by sorry")
M_CASES = [  # (case, solution, {theorem: verdict})
    ("reference", HEAD + U.format("Set.inter_subset_left") + PL.format(kw="theorem", name="pl", proof=DIRECT),
     None),
    ("direct", HEAD + U.format("Set.inter_subset_left") + PL.format(kw="theorem", name="pl", proof=DIRECT),
     {"u": "accepted", "pl": "forbidden_constant"}),
    ("helper", HEAD + U.format("Set.inter_subset_left") + PL.format(kw="theorem", name="helper", proof=DIRECT)
     + "\n" + PL.format(kw="theorem", name="pl", proof=VIA), {"u": "accepted", "pl": "forbidden_constant"}),
    ("private_helper", HEAD + U.format("Set.inter_subset_left")
     + PL.format(kw="private theorem", name="helper", proof=DIRECT) + "\n"
     + PL.format(kw="theorem", name="pl", proof=VIA), {"u": "accepted", "pl": "forbidden_constant"}),
    ("sorry", HEAD + U.format("by sorry") + PL.format(kw="theorem", name="pl", proof=DIRECT),
     {"u": "illegal_axiom", "pl": "forbidden_constant"}),
    ("weakened", HEAD + "theorem u (s t : Set ℕ) : s ∩ t ⊆ s ∪ t := fun _ h => Or.inl h.1\n\n"
     + PL.format(kw="theorem", name="pl", proof=DIRECT), {"u": "statement_mismatch", "pl": "forbidden_constant"}),
]
FORBID = ["--forbid", "MeasurableSpace.DynkinSystem.", "--forbid-exact", "MeasurableSpace.induction_on_inter"]


@pytest.mark.skipif(_mathlib_ready() is not None, reason=str(_mathlib_ready()))
@pytest.mark.timeout(0)     # a batch of Mathlib solutions: each one is bounded by the judge's own --timeout
def test_real_mathlib_batch_with_forbidden_constants(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    proj = os.environ["LEAN_EXERCISE_MATHLIB_PROJECT"]
    (tmp_path / "C.lean").write_text(M_CHALLENGE)
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "S.lean").write_text(M_CASES[0][1])
    rc = ex.main(["check", "--challenge", str(tmp_path / "C.lean"), "--solution", str(ref / "S.lean"),
                  "--project", proj, "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and [r["verdict"] for r in data["theorems"]] == ["accepted", "accepted"], data
    subs = tmp_path / "subs"
    subs.mkdir()
    for case, sol, _ in M_CASES[1:]:
        (subs / f"{case}.lean").write_text(sol)
    rc = ex.main(["batch", "--challenge", str(tmp_path / "C.lean"), "--solutions", str(subs), "--csv",
                  str(tmp_path / "g.csv"), "--logs", str(tmp_path / "logs"), "--project", proj, "--json"] + FORBID)
    data = json.loads(capsys.readouterr().out)
    got = {f["file"][:-5]: f["verdicts"] for f in data["files"]}
    print(json.dumps(got, indent=1), file=sys.__stderr__)
    assert got == {case: want for case, _, want in M_CASES[1:]}
    assert rc == 1 and [p.name for p in tmp_path.iterdir() if p.name.startswith("lean-exercise-")] == []
