#!/usr/bin/env python3
"""lean-pkg-extract ledger and recheck: the declaration-level extraction ledger (proposal 0009, stage S2).

Every test builds a small source repository (a private Lake package) and a target repository holding a
hand-ported library, and runs `lean_pkg.extract.main` in-process. Both sides are read from git objects
only; the workspaces are compared before and after."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from lean_pkg import extract as ex
from lean_pkg import extract_ledger as el

from .test_extract_plan import file_stats, git_state

LAKE_MANIFEST = {"version": "1.1.0", "packagesDir": ".lake/packages", "name": "paper", "lakeDir": ".lake",
                 "packages": [{"type": "git", "url": "https://github.com/leanprover-community/mathlib4",
                               "rev": "a" * 40, "name": "mathlib", "inputRev": "v4.20.0", "inherited": False,
                               "scope": "leanprover-community", "manifestFile": "lake-manifest.json",
                               "configFile": "lakefile.lean"}]}

SRC_A = """\
import Mathlib.Tactic

/-! Private notes about the bounds. -/

namespace Old.A

open Nat

/-- The main lemma. -/
theorem foo (n : Nat) : n + 0 = n := by
  -- the private reason this works
  simp

theorem uses_foo (n : Nat) : n + 0 + 0 = n := by
  rw [foo, foo]

lemma aux : True := trivial

def f (n : Nat) : Nat := n + 1

instance : Inhabited Nat := ⟨0⟩

end Old.A
"""

SRC_B = """\
import Src.A

namespace Old.B

theorem g : Old.A.f 0 = 1 := rfl

end Old.B
"""

TGT_A = """\
import Mathlib.Tactic

namespace New.A

open Nat

/-- The main lemma. -/
theorem bar (n : Nat) : n + 0 = n := by
  simp

theorem uses_foo (n : Nat) : n + 0 + 0 = n := by
  rw [bar, bar]

def f (n : Nat) : Nat := n + 1

instance : Inhabited Nat := ⟨0⟩

end New.A
"""

TGT_B = """\
import New.A

namespace New.B

theorem g : New.A.f 0 = 1 := rfl

end New.B
"""

SRC_FILES = {
    "lakefile.toml": 'name = "paper"\n\n[[lean_lib]]\nname = "Src"\n',
    "lean-toolchain": "leanprover/lean4:v4.20.0\n",
    "lake-manifest.json": json.dumps(LAKE_MANIFEST, indent=2) + "\n",
    "Src/A.lean": SRC_A,
    "Src/B.lean": SRC_B,
    "paper/main.tex": "\\documentclass{amsart}\n",
}

TGT_FILES = {
    "lakefile.toml": 'name = "newlib"\n\n[[lean_lib]]\nname = "New"\n',
    "lean-toolchain": "leanprover/lean4:v4.20.0\n",
    "lake-manifest.json": json.dumps(dict(LAKE_MANIFEST, name="newlib"), indent=2) + "\n",
    "New/A.lean": TGT_A,
    "New/B.lean": TGT_B,
}

MAP = ["--from-module", "Src/A.lean:New.A", "--from-module", "Src/B.lean:New.B",
       "--namespace", "Old.A=New.A", "--namespace", "Old.B=New.B",
       "--rename", "Old.A.foo=New.A.bar", "--drop", "Old.A.aux:exploration only, never used"]


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def commit_all(repo, msg="c") -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "--allow-empty", "-m", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def make_repo(path: pathlib.Path, files: dict) -> str:
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    write(path, files)
    return commit_all(path)


def write(repo: pathlib.Path, files: dict) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


@pytest.fixture
def repos(tmp_path):
    src, tgt = tmp_path / "src", tmp_path / "newlib"
    return src, make_repo(src, SRC_FILES), tgt, make_repo(tgt, TGT_FILES)


def run(capsys, *argv) -> tuple[int, str]:
    rc = ex.main(list(argv))
    return rc, capsys.readouterr().out


def ledger(capsys, repos, tmp_path, *extra, name="ledger.json", commit=None, json_out=True) -> tuple[int, dict | str]:
    src, sha, tgt, _t = repos
    argv = ["ledger", "--repo", str(src), "--commit", commit or sha, "--target", str(tgt), *MAP, *extra,
            "--ledger", str(tmp_path / name)]
    rc, out = run(capsys, *argv, *(["--json"] if json_out else []))
    return rc, (json.loads(out) if json_out else out)


def refused(capsys, repos, tmp_path, needle, *extra) -> dict:
    rc, out = ledger(capsys, repos, tmp_path, *extra)
    assert rc == 1 and not out["ok"] and not out["written"], out
    assert any(needle in r for r in out["refusals"]), out["refusals"]
    assert not (tmp_path / "ledger.json").exists()
    return out


def failed(capsys, repos, tmp_path, needle, *extra) -> dict:
    rc, out = ledger(capsys, repos, tmp_path, *extra)
    assert rc == 1 and not out["ok"] and not out["written"] and out["refusals"] == [], out["refusals"]
    assert any(needle in f for f in out["failures"]), out["failures"]
    assert not (tmp_path / "ledger.json").exists()
    return out


def retarget(repos, files: dict) -> str:
    """Commit changed target files; the new target HEAD."""
    _s, _sha, tgt, _t = repos
    write(tgt, files)
    return commit_all(tgt)


def by_source(led: dict) -> dict:
    return {e["source"] or e["target"]: e for e in led["entries"]}


# --------------------------------------------------------------------------------------------------
# happy paths

def test_ledger_records_identical_and_dropped_and_writes_once(repos, tmp_path, capsys):
    rc, out = ledger(capsys, repos, tmp_path)
    assert rc == 0 and out["ok"] and out["written"], (out["refusals"], out["failures"])
    rows = by_source(out)
    assert rows["Old.A.foo"]["verdict"] == "IDENTICAL" and rows["Old.A.foo"]["target"] == "New.A.bar"
    assert rows["Old.A.uses_foo"]["verdict"] == "IDENTICAL"                 # `foo` -> `bar` inside the proof
    assert rows["Old.A.aux"]["verdict"] == "DROPPED" and rows["Old.A.aux"]["reason"] == "exploration only, never used"
    assert rows["instance#1@Src.A"]["target"] == "instance#1@New.A"
    assert rows["Old.B.g"]["verdict"] == "IDENTICAL"
    assert out["totals"] == {"IDENTICAL": 5, "DROPPED": 1, "DIFFERS": 0, "NEW": 0, "MISSING": 0, "weakened": 0}
    assert [c["verdict"] for c in out["context"]] == ["IDENTICAL", "IDENTICAL"]
    assert out["source"]["modules"][0]["module"] == "Src.A" and out["target"]["modules"][1]["path"] == "New/B.lean"
    assert out["source"]["lean_toolchain"] == "leanprover/lean4:v4.20.0" and out["warnings"] == []
    written = json.loads((tmp_path / "ledger.json").read_text())
    assert list(written) == sorted(el.LEDGER_KEYS) and written["schema"] == "lean-pkg-extract-ledger/1"
    assert written["method"] == "text/1" and written["ok"] is True
    assert {k: v for k, v in out.items() if k in el.LEDGER_KEYS} == written
    assert written["map"]["renames"] == [{"from": "Old.A.foo", "to": "New.A.bar"}]
    # the source-side hash before mapping differs from the mapped one for the renamed declaration
    assert rows["Old.A.foo"]["source_raw_sha256"] != rows["Old.A.foo"]["source_sha256"]
    assert not rows["Old.A.foo"]["doc_changed"]


def test_text_output_is_verdict_first_and_last_and_marks_private_lists(repos, tmp_path, capsys):
    rc, out = ledger(capsys, repos, tmp_path, json_out=False)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert rc == 0 and lines[0] == lines[-1]
    assert lines[0].startswith("ledger: OK (text/1): 5 identical, 1 dropped, 0 differs, 0 new, 0 missing, 0 weakened")
    assert lines[0].endswith("written to " + str(tmp_path / "ledger.json"))
    assert any("PRIVATE" in ln and ln.startswith("entries") for ln in lines)
    assert any("DROPPED" in ln and "Old.A.aux" in ln and "exploration only" in ln for ln in lines)


def test_repeat_run_is_byte_identical(repos, tmp_path, capsys):
    assert ledger(capsys, repos, tmp_path, name="one.json")[0] == 0
    assert ledger(capsys, repos, tmp_path, name="two.json")[0] == 0
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


def test_declared_new_and_allowed_differs_pass_and_are_counted(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("def f (n : Nat) : Nat := n + 1", "def f (n : Nat) : Nat := 1 + n")
                     .replace("end New.A", "theorem extra : True := trivial\n\nend New.A")})
    rc, out = ledger(capsys, repos, tmp_path, "--new", "New.A.extra:a convenience lemma for users",
                     "--allow-differs", "Old.A.f:commuted for definitional unfolding")
    assert rc == 0 and out["ok"], (out["refusals"], out["failures"])
    rows = by_source(out)
    assert rows["Old.A.f"]["verdict"] == "DIFFERS" and rows["Old.A.f"]["reason"].startswith("commuted")
    assert rows["New.A.extra"]["verdict"] == "NEW" and rows["New.A.extra"]["source"] is None
    assert out["totals"]["weakened"] == 1 and out["totals"]["NEW"] == 1 and out["totals"]["DIFFERS"] == 1


def test_a_docstring_change_is_reported_not_failed(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("/-- The main lemma. -/", "/-- The main lemma, ported. -/")})
    rc, out = ledger(capsys, repos, tmp_path)
    assert rc == 0 and by_source(out)["Old.A.foo"]["doc_changed"] is True


def test_workspaces_are_unchanged(repos, tmp_path, capsys):
    src, _sha, tgt, _t = repos
    (src / "untracked.txt").write_text("u\n")
    before = [(git_state(r), file_stats(r)) for r in (src, tgt)]
    assert ledger(capsys, repos, tmp_path)[0] == 0
    assert ledger(capsys, repos, tmp_path, name="x.json", json_out=False)[0] == 0
    assert run(capsys, "recheck", "--ledger", str(tmp_path / "ledger.json"), "--repo", str(src),
               "--target", str(tgt))[0] == 0
    after = [(file_stats(r), git_state(r)) for r in (src, tgt)]
    assert [(b[1], b[0]) for b in before] == after


def test_the_working_tree_is_never_read(repos, tmp_path, capsys):
    src, _sha, tgt, _t = repos
    (src / "Src/A.lean").write_text("garbage (\n")
    (tgt / "New/A.lean").write_text("garbage (\n")
    assert ledger(capsys, repos, tmp_path)[0] == 0


def test_argsfile_holds_the_map_with_reasons_with_spaces(repos, tmp_path, capsys):
    src, sha, tgt, _t = repos
    args = tmp_path / "args.txt"
    args.write_text("# the map for newlib\n--repo " + str(src) + "\n--commit " + sha + "\n--target " + str(tgt) +
                    "\n--from-module Src/A.lean:New.A\n--from-module=Src/B.lean:New.B\n\n"
                    "--namespace Old.A=New.A\n--namespace Old.B=New.B\n--rename Old.A.foo=New.A.bar\n"
                    "--drop Old.A.aux:exploration only, never used\n--ledger " + str(tmp_path / "l.json") +
                    "\n--json\n")
    rc, out = run(capsys, "ledger", "@" + str(args))
    out = json.loads(out)
    assert rc == 0 and out["written"] and by_source(out)["Old.A.aux"]["reason"] == "exploration only, never used"


def test_missing_argsfile_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        ex.main(["ledger", "@" + str(tmp_path / "nope.txt")])
    assert e.value.code == 2


# --------------------------------------------------------------------------------------------------
# failures: exit 1, nothing written, JSON on stdout

def test_a_changed_declaration_differs(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("n + 1", "n + 2")})
    out = failed(capsys, repos, tmp_path, "DIFFERS Old.A.f -> New.A.f")
    assert by_source(out)["Old.A.f"]["whitespace_only"] is False


def test_a_reformatted_declaration_differs_with_the_whitespace_hint(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("def f (n : Nat) : Nat := n + 1",
                                                  "def f (n : Nat) : Nat :=\n  n + 1")})
    out = failed(capsys, repos, tmp_path, "(whitespace only)")
    assert by_source(out)["Old.A.f"]["whitespace_only"] is True


def test_module_system_modifiers_are_not_ignored(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("def f", "public def f")})
    failed(capsys, repos, tmp_path, "DIFFERS Old.A.f")


def test_an_undeclared_new_declaration_fails(repos, tmp_path, capsys):
    retarget(repos, {"New/B.lean": TGT_B.replace("end New.B", "theorem h : True := trivial\nend New.B")})
    out = failed(capsys, repos, tmp_path, "NEW New.B.h")
    assert by_source(out)["New.B.h"]["reason"] is None


def test_a_missing_declaration_fails(repos, tmp_path, capsys):
    retarget(repos, {"New/B.lean": "import New.A\n\nnamespace New.B\n\nend New.B\n"})
    failed(capsys, repos, tmp_path, "MISSING Old.B.g")


def test_a_changed_context_command_fails_unless_allowed(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("open Nat", "open Nat Function")})
    failed(capsys, repos, tmp_path, "context DIFFERS Src.A -> New.A")
    rc, out = ledger(capsys, repos, tmp_path, "--allow-differs", "context@Src.A:Function is opened for users")
    assert rc == 0 and out["context"][0]["reason"] == "Function is opened for users" and out["totals"]["weakened"] == 1


def test_failure_text_output_lists_failures_and_writes_nothing(repos, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("n + 1", "n + 2")})
    rc, out = ledger(capsys, repos, tmp_path, json_out=False)
    lines = out.splitlines()
    assert rc == 1 and lines[0].startswith("ledger: FAILED (1 failure(s)) (text/1)") and lines[-1] == lines[0]
    assert lines[0].endswith("nothing written") and "failures:" in lines


# --------------------------------------------------------------------------------------------------
# refusals: exit 1, every reason listed, nothing written

def test_commit_not_a_commit(repos, tmp_path, capsys):
    rc, out = ledger(capsys, repos, tmp_path, commit="no-such-rev")
    assert rc == 1 and any("--commit 'no-such-rev' is not a commit of the source repository" in r
                           for r in out["refusals"])
    assert out["source"]["commit"] is None and not (tmp_path / "ledger.json").exists()


def test_target_commit_not_a_commit(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "is not a commit of the target repository", "--target-commit", "0" * 40)


@pytest.mark.parametrize("which", ["--repo", "--target"])
def test_repo_or_target_not_git_or_a_subdirectory_is_usage(repos, tmp_path, capsys, which):
    src, sha, tgt, _t = repos
    for bad, why in ((tmp_path, "not a git repository"), ((src if which == "--repo" else tgt) / "Src", "subdirectory")):
        if not bad.exists():
            bad = tgt / "New"
        argv = {"--repo": str(src), "--target": str(tgt)}
        argv[which] = str(bad)
        rc = ex.main(["ledger", "--repo", argv["--repo"], "--commit", sha, "--target", argv["--target"], *MAP,
                      "--ledger", str(tmp_path / "l.json"), "--json"])
        out = json.loads(capsys.readouterr().out)
        assert rc == 2 and out["usage"] and which in out["error"] and why in out["error"]


def test_usage_errors_answer_in_json(capsys):
    with pytest.raises(SystemExit) as e:
        ex.main(["ledger", "--json", "--repo", "x"])
    assert e.value.code == 2
    assert json.loads(capsys.readouterr().out)["usage"] is True


def test_a_missing_blob_is_refused(repos, tmp_path, capsys):
    src, sha, _tgt, _t = repos
    oid = git(src, "rev-parse", f"{sha}:Src/B.lean").stdout.strip()
    (src / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    refused(capsys, repos, tmp_path, "is not in the object store (partial clone?)")


@pytest.mark.parametrize("value, needle", [
    ("Src/C.lean:New.C", "source Src/C.lean: missing at"),
    ("paper/main.tex:New.T", "not a .lean file"),
    ("Src/A.lean:New.B", "is also the target of"),
    ("Src/A.lean:not a name", "is not a Lean module name"),
    ("Src/A.lean:Mathlib.Extra", "root namespace of Lean or a Mathlib dependency"),
    ("Src/../Src/A.lean:New.Z", "path traversal"),
    ("Src/A.lean", "expected SRC/Path.lean:New.Module"),
])
def test_from_module_refusals(repos, tmp_path, capsys, value, needle):
    refused(capsys, repos, tmp_path, needle, "--from-module", value)


def test_from_module_duplicated_source(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "the source module is mapped twice", "--from-module", "Src/A.lean:New.C")


def test_target_module_missing_in_the_target(repos, tmp_path, capsys):
    retarget(repos, {})
    _s, _sha, tgt, _t = repos
    git(tgt, "rm", "-q", "New/B.lean")
    commit_all(tgt)
    refused(capsys, repos, tmp_path, "target module New.B (from Src/B.lean): no file New/B.lean")


def test_namespace_stale(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "--namespace Old.C=New.C: stale", "--namespace", "Old.C=New.C")


def test_namespace_ambiguous_overlap(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "mapped twice", "--namespace", "Old.A=Other.A")


@pytest.mark.parametrize("extra, needle", [
    (["--rename", "Old.A.nope=New.A.nope"], "--rename Old.A.nope=New.A.nope: stale"),
    (["--rename", "Old.A.foo=New.A.other"], "mapped twice"),
    (["--rename", "Old.A.uses_foo=New.A.bar"], "both renamed to New.A.bar"),
    (["--rename", "Old.A.f=New.A.uses_foo"], "both map to New.A.uses_foo"),
    (["--rename", "New.A.bar=New.A.baz"], "chains into another rename"),
    (["--rename", "Old.A.f"], "expected OLD=NEW"),
])
def test_rename_refusals(repos, tmp_path, capsys, extra, needle):
    refused(capsys, repos, tmp_path, needle, *extra)


def test_rename_cycle(repos, tmp_path, capsys):
    refused(capsys, repos, tmp_path, "chains into another rename", "--rename", "New.A.bar=Old.A.foo")


@pytest.mark.parametrize("extra, needle", [
    (["--drop", "Old.A.nope:gone"], "--drop Old.A.nope: stale"),
    (["--drop", "Old.A.f:"], "the reason must be one non-empty line"),
    (["--drop", "Old.A.f:two\nlines"], "the reason must be one non-empty line"),
    (["--drop", "Old.A.f"], "expected NAME:REASON"),
    (["--drop", "Old.A.f:dup", "--drop", "Old.A.f:dup again"], "named twice"),
    (["--drop", "Old.A.f:not needed"], "dropped, yet present in the target as New.A.f"),
    (["--drop", "Old.A.foo:not needed"], "still referenced by Old.A.uses_foo"),
    (["--new", "New.A.nope:x"], "--new New.A.nope: stale, not a declaration"),
    (["--new", "New.A.f:x"], "--new New.A.f: stale, it has a source counterpart"),
    (["--allow-differs", "Old.A.nope:x"], "--allow-differs Old.A.nope: stale"),
    (["--allow-differs", "Old.A.f:x"], "--allow-differs Old.A.f: stale, the declaration is IDENTICAL"),
    (["--allow-differs", "context@Src.A:x"], "stale, the context rows are IDENTICAL"),
    (["--allow-differs", "Old.A.f:x", "--drop", "Old.A.f:y"], "both dropped (--drop) and allowed to differ"),
])
def test_declared_list_refusals(repos, tmp_path, capsys, extra, needle):
    refused(capsys, repos, tmp_path, needle, *extra)


def test_a_duplicate_qualified_name_across_source_modules(repos, tmp_path, capsys):
    src, _sha, _tgt, _t = repos
    write(src, {"Src/B.lean": SRC_B + "\nnamespace Old.A\ntheorem foo : True := trivial\nend Old.A\n"})
    sha = commit_all(src)
    rc, out = ledger(capsys, repos, tmp_path, commit=sha)
    assert rc == 1 and any("Old.A.foo is declared in both Src.A and Src.B" in r for r in out["refusals"])


def test_an_unsplittable_file_is_refused_whole(repos, tmp_path, capsys):
    src, _sha, _tgt, _t = repos
    write(src, {"Src/B.lean": SRC_B + "/- never closed\n"})
    sha = commit_all(src)
    rc, out = ledger(capsys, repos, tmp_path, commit=sha)
    assert rc == 1 and any("source Src/B.lean: cannot be split into declarations" in r for r in out["refusals"])
    assert out["entries"] == []


def test_ledger_inside_the_target_or_the_source(repos, tmp_path, capsys):
    src, sha, tgt, _t = repos
    for where, needle in ((tgt / "ledger.json", "inside the target repository"),
                          (tgt / ".git" / "ledger.json", "inside the target repository"),
                          (src / "notes" / "ledger.json", "inside the source repository")):
        where.parent.mkdir(exist_ok=True)
        rc, out = run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), *MAP,
                      "--ledger", str(where), "--json")
        out = json.loads(out)
        assert rc == 1 and any(needle in r for r in out["refusals"]) and not where.exists()


def test_ledger_existing_or_a_symlink_or_no_directory(repos, tmp_path, capsys):
    (tmp_path / "ledger.json").write_text("{}\n")
    rc, out = ledger(capsys, repos, tmp_path)
    assert rc == 1 and any("exists; a ledger is evidence and is never overwritten" in r for r in out["refusals"])
    assert (tmp_path / "ledger.json").read_text() == "{}\n"
    (tmp_path / "ledger.json").unlink()
    os.symlink(tmp_path / "elsewhere.json", tmp_path / "ledger.json")
    rc, out = ledger(capsys, repos, tmp_path)
    assert rc == 1 and any("a symlink" in r for r in out["refusals"])
    assert not (tmp_path / "elsewhere.json").exists()
    rc, out = ledger(capsys, repos, tmp_path, name="no/such/dir.json")
    assert rc == 1 and any("its directory does not exist" in r for r in out["refusals"])


def test_a_pin_mismatch_warns_and_leaves_the_exit_code(repos, tmp_path, capsys):
    man = dict(LAKE_MANIFEST, name="newlib")
    man["packages"] = [dict(man["packages"][0], rev="b" * 40)]
    retarget(repos, {"lean-toolchain": "leanprover/lean4:v4.21.0\n",
                     "lake-manifest.json": json.dumps(man, indent=2) + "\n"})
    rc, out = ledger(capsys, repos, tmp_path)
    assert rc == 0 and out["ok"] and out["written"]
    assert any("lean-toolchain differs" in w for w in out["warnings"])
    assert any("dependency mathlib is at aaaaaaaaaaaa in the source and bbbbbbbbbbbb" in w for w in out["warnings"])


# --------------------------------------------------------------------------------------------------
# recheck

def _recheck(capsys, repos, path, *extra, json_out=True):
    src, _sha, tgt, _t = repos
    rc, out = run(capsys, "recheck", "--ledger", str(path), "--repo", str(src), "--target", str(tgt), *extra,
                  *(["--json"] if json_out else []))
    return rc, (json.loads(out) if json_out else out)


@pytest.fixture
def recorded(repos, tmp_path, capsys):
    assert ledger(capsys, repos, tmp_path)[0] == 0
    return tmp_path / "ledger.json"


def test_recheck_matches_at_the_recorded_commits(repos, recorded, capsys):
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 0 and out["ok"] and out["drift"] == [] and not out["written"]
    rc, text = _recheck(capsys, repos, recorded, json_out=False)
    lines = text.splitlines()
    assert rc == 0 and lines[0].startswith("recheck: OK (text/1): 5 identical, 1 dropped") and lines[-1] == lines[0]


def test_recheck_reports_drift_at_newer_commits_that_still_agree(repos, recorded, capsys):
    src, _sha, tgt, _t = repos
    write(src, {"Src/A.lean": SRC_A.replace("n + 1", "n + 3")})
    new_src = commit_all(src)
    retarget(repos, {"New/A.lean": TGT_A.replace("n + 1", "n + 3")})
    rc, out = _recheck(capsys, repos, recorded, "--commit", new_src, "--target-commit", "HEAD")
    assert rc == 0 and out["ok"], (out["refusals"], out["failures"])
    assert any(d.startswith("Old.A.f: source_raw_sha256, target_sha256") for d in out["drift"])
    assert any(d.startswith("source module Src/A.lean: blob") for d in out["drift"])


def test_recheck_fails_when_the_source_moved_on(repos, recorded, capsys):
    src, _sha, _tgt, _t = repos
    write(src, {"Src/A.lean": SRC_A.replace("n + 1", "n + 3")})
    new_src = commit_all(src)
    rc, out = _recheck(capsys, repos, recorded, "--commit", new_src)
    assert rc == 1 and any("DIFFERS Old.A.f" in f for f in out["failures"])
    assert any("verdict IDENTICAL -> DIFFERS" in d for d in out["drift"])


def test_recheck_defaults_to_the_recorded_target_commit_not_head(repos, recorded, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("n + 1", "n + 2")})
    assert _recheck(capsys, repos, recorded)[0] == 0
    assert _recheck(capsys, repos, recorded, "--target-commit", "HEAD")[0] == 1


def _edit(path: pathlib.Path, fn) -> None:
    led = json.loads(path.read_text())
    fn(led)
    path.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n")


def test_recheck_refuses_a_tampered_hash(repos, recorded, capsys):
    _edit(recorded, lambda d: d["entries"][0].update(target_sha256="0" * 64))
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("does not match a recomputation at its own commits" in r for r in out["refusals"])
    assert any("ledger.entries[0].target_sha256" in d for d in out["drift"])


def test_recheck_refuses_a_tampered_verdict_with_consistent_totals(repos, recorded, capsys):
    def fn(d):
        d["entries"][0]["verdict"] = "DROPPED"
        d["totals"]["IDENTICAL"] -= 1
        d["totals"]["DROPPED"] += 1
    _edit(recorded, fn)
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("ledger.entries[0].verdict" in d for d in out["drift"])


@pytest.mark.parametrize("edit, needle", [
    (lambda d: d.update(schema="lean-pkg-extract-ledger/2"), "newer than this lean-pkg reads"),
    (lambda d: d.update(schema="claims-ledger/1"), "unknown schema"),
    (lambda d: d.update(method="types/2"), "method types/2 is not known to this lean-pkg"),
    (lambda d: d.update(method="types/1"), "missing key(s) ['types']"),
    (lambda d: d.update(method="tokens"), "unknown method"),
    (lambda d: d.update(extra=1), "unknown key(s) ['extra']"),
    (lambda d: d.pop("context"), "missing key(s) ['context']"),
    (lambda d: d["entries"][0].update(verdict="SAME"), "is not one of"),
    (lambda d: d["entries"][0].update(kind="lemmata"), "is not a declaration kind"),
    (lambda d: d["entries"][0].update(whitespace_only=0), "not a boolean"),
    (lambda d: d["totals"].update(IDENTICAL=4), "does not count the entries"),
    (lambda d: d.update(ok=False), "ok is not true"),
    (lambda d: d["source"].update(commit="abc"), "not a full 40-hex commit"),
    (lambda d: d["map"]["drops"][0].update(why="x"), "unknown key(s) ['why']"),
])
def test_recheck_refuses_an_invalid_ledger(repos, recorded, capsys, edit, needle):
    _edit(recorded, edit)
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any(needle in r for r in out["refusals"]), out["refusals"]


def test_recheck_refuses_an_invalid_map(repos, recorded, capsys):
    _edit(recorded, lambda d: d["map"]["renames"].append({"from": "New.A.bar", "to": "X.y"}))
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("invalid map" in r and "chains" in r for r in out["refusals"])


def test_recheck_refuses_non_strict_json_and_duplicate_keys(repos, recorded, capsys):
    text = recorded.read_text()
    recorded.write_text(text.replace('"ok": true', '"ok": true, "ok": true', 1))
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("duplicate key 'ok'" in r for r in out["refusals"])
    recorded.write_text(text.replace('"ok": true', '"ok": NaN', 1))
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("not strict JSON" in r for r in out["refusals"])
    rc, out = _recheck(capsys, repos, recorded.parent / "absent.json")
    assert rc == 1 and any("not a readable regular file" in r for r in out["refusals"])


def test_recheck_refuses_a_commit_missing_from_the_repository(repos, recorded, capsys):
    _edit(recorded, lambda d: d["source"].update(commit="1" * 40))
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("(the ledger) is not a commit of the source repository" in r for r in out["refusals"])


def test_recheck_of_a_null_target_commit_needs_target_commit(repos, recorded, capsys):
    _edit(recorded, lambda d: d["target"].update(commit=None))
    rc, out = _recheck(capsys, repos, recorded)
    assert rc == 1 and any("pass --target-commit" in r for r in out["refusals"])
    rc, out = _recheck(capsys, repos, recorded, "--target-commit", "HEAD")
    assert rc == 0 and out["ok"], out["refusals"]


def test_recheck_usage_errors_answer_in_json(capsys):
    with pytest.raises(SystemExit) as e:
        ex.main(["recheck", "--json", "--ledger", "x"])
    assert e.value.code == 2 and json.loads(capsys.readouterr().out)["usage"] is True


# --------------------------------------------------------------------------------------------------
# contract

def test_no_gate_page_or_index_reads_the_ledger():
    """The ledger is evidence for a person, read by no gate, page, index or claims command (decls writes one)."""
    src = pathlib.Path(el.__file__).parent
    readers = sorted(p.name for p in src.glob("*.py")
                     if p.name not in ("extract_ledger.py", "extract.py", "extract_decls.py") and
                     ("extract_ledger" in p.read_text() or el.EXTRACT_LEDGER_SCHEMA in p.read_text()))
    assert readers == []
