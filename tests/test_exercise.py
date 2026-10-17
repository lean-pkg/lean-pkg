#!/usr/bin/env python3
"""lean-exercise check: theorem discovery, refusals, verdicts from `lake comparator` output, trust labels.

Unit tests run a stub `lake` in a fake ELAN_HOME (no Lean). The integration test at the end runs the real
`lake comparator` and is opt-in: LEAN_EXERCISE_INTEGRATION=1 on a host where `comparator host-check`
passes and leanprover/lean4:v4.35.0-rc2 is installed."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import sys
import textwrap
import tomllib

import pytest

from lean_pkg import cli
from lean_pkg import comparator_receipt as cr
from lean_pkg import exercise as ex

ROOT = pathlib.Path(__file__).resolve().parents[1]
TC_DIR = "leanprover--lean4---v4.35.0-rc2"

# The stub prints the shapes real `lake comparator` runs printed (captured on a Linux host, v4.35.0-rc2).
# Each Solution line `-- FAKE <theorem>: <shape>` picks the shape for that theorem; default ok.
STUB = r'''#!PYTHON
import json, os, pathlib, sys, time
args = sys.argv[1:]
log = os.environ.get("FAKE_LAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": args, "cwd": os.getcwd(),
                            "env": {k: os.environ.get(k) for k in ("LAKE_ARTIFACT_CACHE", "LAKE_CACHE_DIR",
                                                                   "ELAN_TOOLCHAIN")}}) + "\n")
cfg = json.loads(pathlib.Path(args[args.index("--config") + 1]).read_text())
name = cfg["theorem_names"][0]
shape = "ok"
for line in pathlib.Path("Solution.lean").read_text().splitlines():
    if line.startswith("-- FAKE " + name + ":"):
        shape = line.split(":", 1)[1].strip()
print("Building Challenge")
print("Build completed successfully (3 jobs).")
if shape == "challenge_build":
    print("error: Challenge.lean:1:8: unknown identifier 'x'"); print("error: build failed"); sys.exit(1)
if shape == "challenge_private":   # a private theorem of a `module` Challenge: the export fails before the Solution
    print(f"PANIC at LeanExport.dumpConstant LeanExport.Basic:254:48: Constant {name} not found in environment.")
    print("error: Child exited with 134"); sys.exit(134)
print("Building Solution")
if shape == "ok":
    for k in ("con-ron", "nanoda", "Lean default"):
        print(f"Running {k} kernel on solution"); print(f"{k} kernel accepts the solution")
    print("Your solution is okay!"); sys.exit(0)
if shape == "build":
    print("error: Solution.lean:2:8: Unknown identifier `foo`")
    print("Some required targets logged failures:"); print("- Solution")
    print("error: build failed"); print("error: Child exited with 1"); sys.exit(1)
msgs = {"mismatch": f"error: Challenge and solution theorem statement do not match: '{name}'",
        "sorry": "error: Illegal axiom detected: 'sorryAx'",
        "axiom": "error: Illegal axiom detected: 'cheat'",
        "native": f"error: Illegal axiom detected: '{name}._native.native_decide.ax_1_1'",
        "missing": f"PANIC at LeanExport.dumpConstant LeanExport.Basic:254:48: Constant {name} not found in environment.",
        "kernel": "nanoda kernel rejected the solution",
        "garbage": "something unexpected"}
if shape == "sleep":
    time.sleep(30)
print(msgs[shape]); sys.exit(134 if shape == "missing" else 1)
'''.replace("#!PYTHON", "#!" + sys.executable)

CHALLENGE = "theorem t (n : Nat) : n + 0 = n := by sorry\n"


def _exe(p: pathlib.Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    """A fake elan with v4.35.0-rc2 whose lake is STUB; a capable host; TMPDIR under tmp_path; a
    registry path that must never be touched."""
    home = tmp_path / "elan"
    _exe(home / "bin" / "elan", "#!/bin/sh\nexit 0\n")
    _exe(home / "bin" / "lake", STUB)
    for tool in cr.TOOLCHAIN_TOOLS:
        _exe(home / "toolchains" / TC_DIR / "bin" / tool, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("ELAN_HOME", str(home))
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    log = tmp_path / "lake.log"
    monkeypatch.setenv("FAKE_LAKE_LOG", str(log))
    registry = tmp_path / "reg" / "lean-packages.yaml"
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(registry))

    def boom(*a, **k):
        raise AssertionError("lean-exercise touched the registry")
    for fn in ("registry_path", "events_path", "load_registry", "registry_lock"):
        monkeypatch.setattr(cli, fn, boom)
    host(monkeypatch)
    ns = type("Fake", (), {})()
    ns.root, ns.tmp, ns.log, ns.registry, ns.home = tmp_path, tmp, log, registry, home
    return ns


def host(monkeypatch, bwrap="ok", kernel="ok"):
    monkeypatch.setattr(ex, "host_report", lambda: {"checks": [
        {"item": "kernel", "level": kernel, "detail": "Linux 6.x" if kernel == "ok" else "Darwin 24 (need Linux)"},
        {"item": "bwrap", "level": bwrap, "detail": "creates a user namespace" if bwrap == "ok" else
         "bwrap cannot create a user namespace (AppArmor)"}]})


def files(root: pathlib.Path, challenge=CHALLENGE, solution="theorem t (n : Nat) : n + 0 = n := by simp\n",
          defs=None) -> list[str]:
    (root / "C.lean").write_text(challenge)
    (root / "S.lean").write_text(solution)
    argv = ["check", "--challenge", str(root / "C.lean"), "--solution", str(root / "S.lean")]
    if defs is not None:
        (root / "Defs.lean").write_text(defs)
        argv += ["--defs", str(root / "Defs.lean")]
    return argv


def run(capsys, argv):
    rc = ex.main(argv + ["--json"])
    cap = capsys.readouterr()
    return rc, json.loads(cap.out), cap.err


def calls(fake) -> list[dict]:
    return [json.loads(x) for x in fake.log.read_text().splitlines()] if fake.log.exists() else []


# ------------------------------------------------------------------------------------------- verdicts

@pytest.mark.parametrize("shape,verdict,axiom", [
    ("mismatch", "statement_mismatch", None), ("sorry", "illegal_axiom", "sorry"),
    ("axiom", "illegal_axiom", "cheat"), ("native", "illegal_axiom", "t._native.native_decide.ax_1_1"),
    ("missing", "missing", None), ("kernel", "kernel_rejected", None), ("build", "build_error", None),
    ("garbage", "tool_error", None)])
def test_each_log_shape_maps_to_its_verdict(fake, capsys, shape, verdict, axiom):
    argv = files(fake.root, solution=f"-- FAKE t: {shape}\ntheorem t (n : Nat) : n + 0 = n := by simp\n")
    rc, data, _ = run(capsys, argv)
    row = data["theorems"][0]
    assert rc == 1 and data["ok"] is False and row["verdict"] == verdict
    assert row.get("axiom") == axiom
    assert data["summary"] == {"accepted": 0, "total": 1}


def test_accepted_payload_follows_the_schema(fake, capsys):
    rc, data, err = run(capsys, files(fake.root))
    assert rc == 0 and data["ok"] is True
    assert set(data) == {"schema", "ok", "trust", "toolchain", "mechanism", "challenge_sha256",
                         "solution_sha256", "permitted_axioms", "theorems", "summary", "kept"}
    assert data["schema"] == "exercise-check/1" and data["trust"] == "sandboxed"
    assert data["toolchain"] == "leanprover/lean4:v4.35.0-rc2" and data["mechanism"] == "toolchain"
    assert data["permitted_axioms"] == ["propext", "Quot.sound", "Classical.choice"]
    row = data["theorems"][0]
    assert row["name"] == "t" and row["verdict"] == "accepted" and row["kernels"] == ["con-ron", "nanoda", "lean"]
    assert "WARN" not in err


def test_one_comparator_run_per_theorem_with_external_kernels_and_fresh_cache(fake, capsys):
    challenge = "theorem a : 1 = 1 := by sorry\ntheorem b : 2 = 2 := by\n  sorry\n"
    solution = "-- FAKE b: sorry\ntheorem a : 1 = 1 := rfl\ntheorem b : 2 = 2 := sorry\n"
    rc, data, _ = run(capsys, files(fake.root, challenge, solution))
    assert rc == 1 and [r["verdict"] for r in data["theorems"]] == ["accepted", "illegal_axiom"]
    cs = calls(fake)
    assert len(cs) == 2 and all("--inadvisably-no-sandbox" not in c["argv"] for c in cs)
    for c in cs:
        assert c["argv"][:2] == ["comparator", "--config"]
        assert c["env"]["LAKE_ARTIFACT_CACHE"] == "false"
        assert c["env"]["LAKE_CACHE_DIR"].startswith(c["cwd"])
        assert c["env"]["ELAN_TOOLCHAIN"] == "leanprover/lean4:v4.35.0-rc2"


def test_build_error_short_circuits_the_rest(fake, capsys):
    challenge = "theorem a : 1 = 1 := by sorry\ntheorem b : 2 = 2 := by sorry\n"
    rc, data, _ = run(capsys, files(fake.root, challenge, "-- FAKE a: build\n"))
    assert rc == 1 and [r["verdict"] for r in data["theorems"]] == ["build_error", "build_error"]
    assert "Unknown identifier" in data["theorems"][0]["detail"]
    assert data["theorems"][1]["detail"].startswith("not judged") and len(calls(fake)) == 1


def test_missing_does_not_short_circuit(fake, capsys):
    challenge = "theorem a : 1 = 1 := by sorry\ntheorem b : 2 = 2 := by sorry\n"
    rc, data, _ = run(capsys, files(fake.root, challenge, "-- FAKE a: missing\n"))
    assert [r["verdict"] for r in data["theorems"]] == ["missing", "accepted"] and len(calls(fake)) == 2


def test_a_challenge_that_does_not_build_is_a_tool_error(fake, capsys):
    rc, data, _ = run(capsys, files(fake.root, solution="-- FAKE t: challenge_build\n"))
    assert rc == 1 and data["theorems"][0]["verdict"] == "tool_error"
    assert "Challenge" in data["theorems"][0]["detail"]


# ------------------------------------------------------------------------------------ module system

MOD_CHALLENGE = "module\n\npublic section\n\ntheorem t (n : Nat) : n + 0 = n := by sorry\n\nend\n"
MOD_SOLUTION = "module\n\npublic section\n\ntheorem t (n : Nat) : n + 0 = n := by simp\n\nend\n"


def test_a_private_theorem_of_a_module_challenge_is_a_tool_error_not_missing(fake, capsys):
    # Real run (v4.35.0-rc2): a `module` Challenge whose theorem is outside `public section` panics in the
    # Challenge export, before `Building Solution`; it was reported as the learner's `missing`.
    rc, data, _ = run(capsys, files(fake.root, MOD_CHALLENGE, "-- FAKE t: challenge_private\n"))
    row = data["theorems"][0]
    assert rc == 1 and row["verdict"] == "tool_error"
    assert row["detail"].startswith("the Challenge does not export this theorem") and "public section" in row["detail"]


def test_missing_in_a_module_solution_names_the_private_default(fake, capsys):
    rc, data, _ = run(capsys, files(fake.root, MOD_CHALLENGE, "module\n-- FAKE t: missing\n"))
    row = data["theorems"][0]
    assert row["verdict"] == "missing" and "public section" in row["detail"]
    rc, data, _ = run(capsys, files(fake.root, solution="-- FAKE t: missing\n"))
    assert data["theorems"][0]["detail"] == "the Solution does not declare this theorem"


def test_module_headed_files_are_judged(fake, capsys):
    rc, data, _ = run(capsys, files(fake.root, MOD_CHALLENGE, MOD_SOLUTION))
    assert rc == 0 and [r["name"] for r in data["theorems"]] == ["t"]


def test_public_theorems_are_discovered(fake):
    challenge = ("module\n\npublic theorem a : 1 = 1 := by sorry\n@[expose] public section\nnamespace N\n"
                 "theorem b : 2 = 2 := by sorry\nend N\nend\n")
    assert ex.plan_theorems(challenge, None) == ["a", "N.b"]


@pytest.mark.parametrize("decl", ["public def f : Nat := 1", "@[expose] public def f : Nat := 1",
                                  "public meta def f : Nat := 1", "public instance : Inhabited Nat := ⟨0⟩",
                                  "public structure S where x : Nat"])
def test_a_module_challenge_that_defines_something_is_refused(fake, capsys, decl):
    # Before the fix `public def` passed the definition refusal and the exercise was judged (accepted).
    rc, _, err = run(capsys, files(fake.root, challenge="module\n" + decl + "\npublic section\n" + CHALLENGE + "end\n"))
    assert rc == 2 and "--defs" in err and not calls(fake)


@pytest.mark.parametrize("line,needle", [("public import Mathlib", "needs --project"),
                                         ("meta import Mathlib.Tactic", "needs --project"),
                                         ("import all Mathlib", "needs --project"),
                                         ("public meta import Challenge", "only Init, Std, Lean"),
                                         ("public import Defs", "--defs")])
def test_module_system_imports_are_checked(fake, capsys, line, needle):
    # Before the fix only a line starting with `import` was read: `public import Mathlib` passed.
    rc, _, err = run(capsys, files(fake.root, challenge="module\n\n" + line + "\n\npublic section\n" + CHALLENGE
                                   + "end\n"))
    assert rc == 2 and needle in err and not calls(fake)


def test_module_system_imports_of_the_core_library_are_allowed(fake, capsys):
    rc, _, _ = run(capsys, files(fake.root, MOD_CHALLENGE, "module\n\npublic import Std.Data.HashMap\n"
                                 "meta import Lean\nimport all Init.Data.Nat\n" + MOD_SOLUTION[len("module\n"):]))
    assert rc == 0


def test_timeout(fake, capsys):
    rc, data, _ = run(capsys, files(fake.root, solution="-- FAKE t: sleep\n") + ["--timeout", "1"])
    assert rc == 1 and data["theorems"][0]["verdict"] == "timeout"


def test_human_output_is_verdict_first_and_last(fake, capsys):
    rc = ex.main(files(fake.root, solution="-- FAKE t: sorry\n"))
    out = capsys.readouterr().out.strip().splitlines()
    assert rc == 1 and out[0] == out[-1] and out[0].startswith("FAIL: 0/1 theorem(s) accepted (sandboxed")
    assert out[1].startswith("illegal_axiom") and "uses sorry" in out[1]


# ------------------------------------------------------------------------------------ discovery

def test_discovery_qualifies_namespaces_and_reads_lemma_and_skips_proved(fake, capsys):
    challenge = textwrap.dedent("""\
        namespace Foo
        /-- doc -/
        theorem one : 1 = 1 := by sorry
        namespace Bar
        lemma two : 2 = 2 := sorry
        end Bar
        theorem proved : 3 = 3 := rfl
        -- theorem commented : 4 = 4 := by sorry
        end Foo
        theorem _root_.top : 5 = 5 := by
          simp
          sorry
        """)
    assert ex.plan_theorems(challenge, None) == ["Foo.one", "Foo.Bar.two", "top"]
    assert ex.plan_theorems(challenge, "Foo.proved,Foo.one") == ["Foo.proved", "Foo.one"]


def test_no_sorry_theorem_is_a_usage_error(fake, capsys):
    rc, data, err = run(capsys, files(fake.root, challenge="theorem t : 1 = 1 := rfl\n"))
    assert rc == 2 and data["usage"] is True and "no theorem or lemma whose proof is `sorry`" in err
    assert not calls(fake) and list(fake.tmp.iterdir()) == []


def test_unknown_theorems_flag_is_refused(fake, capsys):
    rc, _, err = run(capsys, files(fake.root) + ["--theorems", "t,nope"])
    assert rc == 2 and "nope" in err and not calls(fake)


def test_sorry_inside_a_word_is_not_a_sorry(fake):
    assert ex.plan_theorems("theorem t : 1 = 1 := by sorry\ntheorem u : 1 = 1 := not_sorry\n", None) == ["t"]


def test_private_theorem_is_refused(fake, capsys):
    rc, _, err = run(capsys, files(fake.root, challenge="private theorem t : 1 = 1 := by sorry\n"))
    assert rc == 2 and "private" in err


# ------------------------------------------------------------------------------------ refusals

def test_axioms_outside_the_standard_three_are_refused(fake, capsys):
    rc, _, err = run(capsys, files(fake.root) + ["--axioms", "propext,cheat"])
    assert rc == 2 and "cheat" in err and not calls(fake)


def test_a_subset_of_the_standard_axioms_is_passed_through(fake, capsys):
    rc, data, _ = run(capsys, files(fake.root) + ["--axioms", "propext"])
    assert rc == 0 and data["permitted_axioms"] == ["propext"]


@pytest.mark.parametrize("decl", ["def f : Nat := 1", "structure S where x : Nat", "axiom ax : False",
                                  "instance : Inhabited Nat := ⟨0⟩", "abbrev N := Nat"])
def test_a_challenge_that_defines_something_is_refused(fake, capsys, decl):
    rc, _, err = run(capsys, files(fake.root, challenge=decl + "\n" + CHALLENGE))
    assert rc == 2 and "--defs" in err and not calls(fake)


def test_shared_definitions_via_defs_are_accepted(fake, capsys):
    defs = "def double (n : Nat) : Nat := n + n\n"
    challenge = "import Defs\ntheorem t (n : Nat) : double n = 2 * n := by sorry\n"
    rc, data, _ = run(capsys, files(fake.root, challenge, "import Defs\n", defs=defs) + ["--keep",
                                                                                     str(fake.root / "kept")])
    assert rc == 0 and data["kept"]
    kept = pathlib.Path(data["kept"])
    assert (kept / "Defs.lean").read_text() == defs
    assert 'name = "Defs"' in (kept / "lakefile.toml").read_text()


def test_import_defs_without_defs_and_mathlib_are_refused(fake, capsys):
    rc, _, err = run(capsys, files(fake.root, challenge="import Defs\n" + CHALLENGE))
    assert rc == 2 and "--defs" in err
    rc, _, err = run(capsys, files(fake.root, solution="import Mathlib\n"))
    assert rc == 2 and "needs --project DIR" in err
    rc, _, err = run(capsys, files(fake.root, solution="import Challenge\n"))
    assert rc == 2 and "only Init, Std, Lean" in err


def test_imports_of_the_core_library_are_allowed(fake, capsys):
    rc, _, _ = run(capsys, files(fake.root, solution="import Std.Data.HashMap\nimport Lean\n"))
    assert rc == 0


def test_mathlib_flag_is_refused(fake, capsys):
    rc, _, err = run(capsys, files(fake.root) + ["--mathlib"])
    assert rc == 2 and "--project DIR" in err


def test_host_refusal_writes_nothing_and_names_the_failing_line(fake, capsys, monkeypatch):
    host(monkeypatch, bwrap="fail")
    rc, data, err = run(capsys, files(fake.root))
    assert rc == 2 and data["ok"] is False and "bwrap: bwrap cannot create a user namespace" in err
    assert "--no-sandbox" in err and list(fake.tmp.iterdir()) == [] and not calls(fake)


def test_host_refusal_on_macos_points_to_wsl2(fake, capsys, monkeypatch):
    host(monkeypatch, bwrap="fail", kernel="fail")
    monkeypatch.setattr(ex.sys, "platform", "darwin")
    rc, _, err = run(capsys, files(fake.root))
    assert rc == 2 and "WSL2" in err


def test_toolchain_below_the_floor_or_absent_is_refused(fake, capsys):
    rc, _, err = run(capsys, files(fake.root) + ["--toolchain", "v4.34.0"])
    assert rc == 2 and "elan toolchain install leanprover/lean4:v4.35.0-rc2" in err
    rc, _, err = run(capsys, files(fake.root) + ["--toolchain", "v4.36.0"])
    assert rc == 2 and "not installed" in err


def test_newest_installed_toolchain_is_chosen(fake, capsys):
    for d, tools in (("leanprover--lean4---v4.40.0", cr.TOOLCHAIN_TOOLS), ("leanprover--lean4---v4.34.1", ())):
        (fake.home / "toolchains" / d / "bin").mkdir(parents=True)
        for tool in tools:
            _exe(fake.home / "toolchains" / d / "bin" / tool, "#!/bin/sh\n")
    rc, data, _ = run(capsys, files(fake.root))
    assert rc == 0 and data["toolchain"] == "leanprover/lean4:v4.40.0"


def test_no_elan_is_refused(fake, capsys, monkeypatch):
    monkeypatch.setenv("ELAN_HOME", str(fake.root / "nowhere"))
    rc, _, err = run(capsys, files(fake.root))
    assert rc == 2 and "elan not found" in err


def test_keep_dir_must_be_empty(fake, capsys):
    (fake.root / "busy").mkdir()
    (fake.root / "busy" / "x").write_text("x")
    rc, _, err = run(capsys, files(fake.root) + ["--keep", str(fake.root / "busy")])
    assert rc == 2 and "empty" in err


# ------------------------------------------------------------------------------------ trust, hygiene

def test_no_sandbox_skips_the_host_check_passes_the_flag_and_labels_the_trust(fake, capsys, monkeypatch):
    host(monkeypatch, bwrap="fail", kernel="fail")
    rc, data, err = run(capsys, files(fake.root) + ["--no-sandbox"])
    assert rc == 0 and data["trust"] == "self-check-only"
    assert "UNSANDBOXED" in err and "self-check only" in err
    assert all("--inadvisably-no-sandbox" in c["argv"] for c in calls(fake))
    ex.main(files(fake.root) + ["--no-sandbox"])
    assert "self-check only" in capsys.readouterr().out


def test_temp_dir_is_removed_and_the_inputs_are_copied_byte_for_byte(fake, capsys):
    solution = "theorem t (n : Nat) : n + 0 = n := by simp -- ü\r\n"
    rc, data, _ = run(capsys, files(fake.root, solution=solution))
    assert rc == 0 and list(fake.tmp.iterdir()) == [] and data["kept"] is None
    import hashlib
    assert data["solution_sha256"] == hashlib.sha256(solution.encode()).hexdigest()
    work = pathlib.Path(calls(fake)[0]["cwd"])
    assert work.parent == fake.tmp and not work.exists()


def test_never_touches_a_registry_git_or_events(fake, capsys, monkeypatch):
    import subprocess
    real = subprocess.Popen

    def guarded(cmd, *a, **k):
        assert pathlib.Path(cmd[0]).name not in ("git", "gh"), cmd
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", guarded)
    rc, _, _ = run(capsys, files(fake.root))
    assert rc == 0 and not fake.registry.parent.exists()


def test_console_script_is_declared():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["lean-exercise"] == "lean_pkg.exercise:main"


def test_help_has_examples(capsys):
    with pytest.raises(SystemExit) as e:
        ex.main(["check", "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0 and "examples:" in out and "lean-exercise check --challenge" in out


def test_missing_required_flag_is_usage_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        ex.main(["check", "--challenge", "C.lean", "--json"])
    assert e.value.code == 2 and json.loads(capsys.readouterr().out)["usage"] is True


# ------------------------------------------------------------------------------------ integration

def _real_host_ready() -> str | None:
    if os.environ.get("LEAN_EXERCISE_INTEGRATION") != "1":
        return "set LEAN_EXERCISE_INTEGRATION=1 to run the real `lake comparator` cases (~1 min)"
    if not (cr.elan_home() / "toolchains" / TC_DIR / "bin" / "nanoda_bin").exists():
        return "leanprover/lean4:v4.35.0-rc2 is not installed"
    res = cr.host_checks()
    if not res["mechanism_detail"]["toolchain"]["ok"]:
        return "comparator host-check: the toolchain mechanism fails here"
    return None


REAL = [  # (case, solution, verdict, axiom)
    ("a_honest", "theorem t (n : Nat) : n + 0 = n := by simp\n", "accepted", None),
    ("b_weakened", "theorem t (n : Nat) : n + 0 = n + 0 := rfl\n", "statement_mismatch", None),
    ("c_sorry", "theorem t (n : Nat) : n + 0 = n := by sorry\n", "illegal_axiom", "sorry"),
    ("d_axiom", "axiom cheat : False\ntheorem t (n : Nat) : n + 0 = n := cheat.elim\n", "illegal_axiom", "cheat"),
    ("e_broken", "theorem t (n : Nat) : n + 0 = n := by\n  exact foo\n", "build_error", None),
    # native_decide in an extra theorem the Challenge does not ask for: only `t` is judged.
    ("f_native_unjudged", "theorem t (n : Nat) : n + 0 = n := by simp\ntheorem u : 2 + 2 = 4 := by native_decide\n",
     "accepted", None),
    ("g_helper", "theorem helper (n : Nat) : n + 0 = n := Nat.add_zero n\ntheorem t (n : Nat) : n + 0 = n := helper n\n",
     "accepted", None),
]

REAL_MODULE = [  # (case, challenge, solution, verdict, detail fragment): Lean's module system, v4.35.0-rc2
    ("m_honest", MOD_CHALLENGE, MOD_SOLUTION, "accepted", "okay"),
    ("m_private_solution", MOD_CHALLENGE, "module\n\ntheorem t (n : Nat) : n + 0 = n := by simp\n", "missing",
     "public section"),
    ("m_private_challenge", "module\n\ntheorem t (n : Nat) : n + 0 = n := by sorry\n", MOD_SOLUTION, "tool_error",
     "the Challenge does not export"),
]


@pytest.mark.skipif(_real_host_ready() is not None, reason=str(_real_host_ready()))
@pytest.mark.parametrize("case,solution,verdict,axiom", REAL, ids=[r[0] for r in REAL])
def test_real_lake_comparator(tmp_path, monkeypatch, capsys, case, solution, verdict, axiom):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    argv = files(tmp_path, solution=solution) + ["--toolchain", "v4.35.0-rc2", "--json"]
    rc = ex.main(argv)
    data = json.loads(capsys.readouterr().out)
    row = data["theorems"][0]
    print(f"{case}: {row['verdict']} ({row['detail']}) {row['seconds']}s kernels={row['kernels']}",
          file=sys.__stderr__)
    assert row["verdict"] == verdict and row.get("axiom") == axiom and data["trust"] == "sandboxed"
    assert rc == (0 if verdict == "accepted" else 1)
    if verdict == "accepted":
        assert row["kernels"] == ["con-ron", "nanoda", "lean"]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("lean-exercise-")] == []


@pytest.mark.skipif(_real_host_ready() is not None, reason=str(_real_host_ready()))
@pytest.mark.parametrize("case,challenge,solution,verdict,fragment", REAL_MODULE, ids=[r[0] for r in REAL_MODULE])
def test_real_module_headed_exercise(tmp_path, monkeypatch, capsys, case, challenge, solution, verdict, fragment):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    rc = ex.main(files(tmp_path, challenge, solution) + ["--toolchain", "v4.35.0-rc2", "--json"])
    row = json.loads(capsys.readouterr().out)["theorems"][0]
    print(f"{case}: {row['verdict']} ({row['detail']})", file=sys.__stderr__)
    assert row["verdict"] == verdict and fragment in row["detail"] and rc == (0 if verdict == "accepted" else 1)


@pytest.mark.skipif(_real_host_ready() is not None, reason=str(_real_host_ready()))
def test_real_native_decide_in_the_judged_theorem(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    argv = files(tmp_path, challenge="theorem t : 2 + 2 = 4 := by sorry\n",
                 solution="theorem t : 2 + 2 = 4 := by native_decide\n") + ["--toolchain", "v4.35.0-rc2", "--json"]
    rc = ex.main(argv)
    row = json.loads(capsys.readouterr().out)["theorems"][0]
    print(f"h_native_judged: {row['verdict']} ({row['detail']}) axiom={row.get('axiom')}", file=sys.__stderr__)
    assert rc == 1 and row["verdict"] == "illegal_axiom" and "native_decide" in row["axiom"]
