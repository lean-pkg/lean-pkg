#!/usr/bin/env python3
"""lean-pkg-extract decls: write the target modules and record their ledger (proposal 0009, stage S3).

Every test builds the private source repository of tests/test_extract_ledger.py and runs
`lean_pkg.extract.main` in-process. The source is read from git objects only (its workspace is compared
before and after); the output directory is written through `extract_write.write_tree` (staged beside it,
scanned with public-safety, moved in one step) or not at all."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from lean_pkg import extract as ex
from lean_pkg import extract_ledger as el
from lean_pkg import extract_write as xw
from lean_pkg import scaffold

from .test_extract_ledger import MAP, SRC_A, SRC_FILES, commit_all, git, make_repo, write
from .test_extract_plan import file_stats, git_state

AUTHOR = ["--author", "Ada Lovelace"]


@pytest.fixture
def src(tmp_path):
    repo = tmp_path / "src"
    return repo, make_repo(repo, SRC_FILES)


def run(capsys, *argv) -> tuple[int, str]:
    rc = ex.main(list(argv))
    return rc, capsys.readouterr().out


def decls(capsys, src, tmp_path, *extra, out="newlib", name="ledger.json", mapping=MAP, json_out=True):
    repo, sha = src
    argv = ["decls", "--repo", str(repo), "--commit", sha, "--output", str(tmp_path / out), *mapping, *extra,
            "--ledger", str(tmp_path / name)]
    rc, text = run(capsys, *argv, *(["--json"] if json_out else []))
    return rc, (json.loads(text) if json_out else text)


def stage_of(tmp_path, out="newlib") -> pathlib.Path:
    return tmp_path / f".{out}{xw.STAGE_SUFFIX}"


def refused(capsys, src, tmp_path, needle, *extra, out="newlib", mapping=MAP) -> dict:
    """A refusal: exit 1, every reason listed, and nothing written (no DIR content, no staging, no ledger)."""
    before = file_stats(src[0])
    rc, data = decls(capsys, src, tmp_path, *extra, out=out, mapping=mapping)
    assert rc == 1 and not data["ok"] and not data["written"] and not data["output"]["written"], data
    assert any(needle in r for r in data["refusals"]), data["refusals"]
    assert not (tmp_path / "ledger.json").exists()
    assert not stage_of(tmp_path, out).exists()
    assert file_stats(src[0]) == before
    return data


def by_source(led: dict) -> dict:
    return {e["source"] or e["target"]: e for e in led["entries"]}


# --------------------------------------------------------------------------------------------------
# happy paths

def test_decls_writes_the_package_and_a_ledger_recheck_accepts(src, tmp_path, capsys):
    repo, sha = src
    before = (git_state(repo), file_stats(repo))
    rc, data = decls(capsys, src, tmp_path, "--scaffold", "newlib", *AUTHOR)
    assert rc == 0 and data["ok"] and data["written"], (data["refusals"], data["failures"])
    after = (file_stats(repo), git_state(repo))
    assert (after[1], after[0]) == before                                 # SRC untouched
    # all IDENTICAL by construction, the drop disclosed
    assert data["totals"] == {"IDENTICAL": 5, "DROPPED": 1, "DIFFERS": 0, "NEW": 0, "MISSING": 0, "weakened": 0}
    assert [c["verdict"] for c in data["context"]] == ["IDENTICAL", "IDENTICAL"]
    assert data["target"]["commit"] is None and data["warnings"] == []
    out = tmp_path / "newlib"
    assert data["output"] == {"files": [".gitignore", "CITATION.cff", "LICENSE", "LICENSES/Apache-2.0.txt",
                                        "New/A.lean", "New/B.lean", "README.md", "REUSE.toml", "lake-manifest.json",
                                        "lakefile.toml", "lean-toolchain"],
                              "scaffold": "newlib", "dry_run": False, "written": True, "git_init": True,
                              "committed": False, "object_format": "sha1"}
    assert "dir" not in data["output"] and "ledger_path" not in data       # no host path in the JSON
    assert data["public_safety"] == {"ok": True, "block_hits": 0, "warn_hits": 0}
    assert (out / ".git").is_dir() and git(out, "rev-parse", "HEAD", check=False).returncode != 0   # no commit
    assert not stage_of(tmp_path).exists()
    # the pins: the toolchain byte for byte, the manifest's dependency pins kept and its root name replaced
    assert (out / "lean-toolchain").read_bytes() == SRC_FILES["lean-toolchain"].encode()
    man = json.loads((out / "lake-manifest.json").read_text())
    assert man["name"] == "newlib" and man["packages"] == json.loads(SRC_FILES["lake-manifest.json"])["packages"]
    assert data["source"]["lean_toolchain"] == data["target"]["lean_toolchain"] == "leanprover/lean4:v4.20.0"
    lake = (out / "lakefile.toml").read_text()
    assert 'name = "newlib"' in lake and 'defaultTargets = ["New"]' in lake and 'rev = "v4.20.0"' in lake
    assert 'globs = ["New.+"]' in lake and 'scope = "leanprover-community"' in lake
    assert "lean-pkg-extract decls" in (out / "REUSE.toml").read_text()
    assert "- `New.A`" in (out / "README.md").read_text()
    # the ledger on disk is the one reported, and recheck accepts it once the tree is committed as written
    led = json.loads((tmp_path / "ledger.json").read_text())
    assert led == {k: data[k] for k in el.LEDGER_KEYS}
    commit_all(out)
    rc, text = run(capsys, "recheck", "--ledger", str(tmp_path / "ledger.json"), "--repo", str(repo),
                   "--target", str(out), "--target-commit", "HEAD")
    assert rc == 0 and text.startswith("recheck: OK"), text


def test_the_written_modules_carry_the_maps_and_no_comment_but_docstrings(src, tmp_path, capsys):
    rc, data = decls(capsys, src, tmp_path)
    assert rc == 0, data["refusals"]
    a = (tmp_path / "newlib" / "New" / "A.lean").read_text()
    b = (tmp_path / "newlib" / "New" / "B.lean").read_text()
    assert "namespace New.A" in a and "end New.A" in a and "theorem bar" in a and "rw [bar, bar]" in a
    assert "/-- The main lemma. -/" in a                                  # docstrings stay
    assert "Private notes" not in a and "private reason" not in a                # other comments go
    assert [ln for ln in a.splitlines() if "--" in ln] == ["/-- The main lemma. -/"]
    assert "aux" not in a                                                  # dropped
    assert "\n\n\n" not in a and a.endswith("end New.A\n")
    assert b.startswith("import New.A\n") and "New.A.f 0 = 1" in b       # imports and names mapped
    assert sorted(p.name for p in (tmp_path / "newlib").iterdir()) == ["New"]   # no --scaffold: modules only
    # without the pins beside them the ledger says so, as `ledger` would
    assert data["target"]["lean_toolchain"] is None and any("target: no lean-toolchain" in w for w in data["warnings"])
    assert not data["output"]["git_init"]


def test_without_scaffold_the_tree_rechecks_once_committed_as_written(src, tmp_path, capsys):
    rc, data = decls(capsys, src, tmp_path)
    assert rc == 0
    out = tmp_path / "newlib"
    git(out, "init", "-q", "-b", "main")
    commit_all(out)
    rc, text = run(capsys, "recheck", "--ledger", str(tmp_path / "ledger.json"), "--repo", str(src[0]),
                   "--target", str(out), "--target-commit", "HEAD")
    assert rc == 0, text


def test_dry_run_writes_nothing(src, tmp_path, capsys):
    rc, data = decls(capsys, src, tmp_path, "--dry-run", "--scaffold", "newlib", *AUTHOR)
    assert rc == 0 and data["ok"] and not data["written"] and data["output"]["dry_run"]
    assert "New/A.lean" in data["output"]["files"] and data["totals"]["IDENTICAL"] == 5
    assert not (tmp_path / "newlib").exists() and not (tmp_path / "ledger.json").exists()
    assert not stage_of(tmp_path).exists()
    rc, text = decls(capsys, src, tmp_path, "--dry-run", json_out=False)
    assert rc == 0 and text.startswith("decls: OK (text/1)") and "dry run: 2 file(s) would be written" in text
    assert text.rstrip().split("\n")[-1].startswith("decls: OK")          # verdict first and last


def test_a_repeat_run_records_a_byte_identical_ledger(src, tmp_path, capsys):
    assert decls(capsys, src, tmp_path, out="one", name="one.json")[0] == 0
    assert decls(capsys, src, tmp_path, out="two", name="two.json")[0] == 0
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()
    assert (tmp_path / "one/New/A.lean").read_bytes() == (tmp_path / "two/New/A.lean").read_bytes()


def test_an_empty_existing_directory_is_used(src, tmp_path, capsys):
    (tmp_path / "newlib").mkdir()
    rc, data = decls(capsys, src, tmp_path)
    assert rc == 0 and (tmp_path / "newlib" / "New" / "A.lean").is_file()


def test_text_output_is_verdict_first_and_lists_what_was_written(src, tmp_path, capsys):
    rc, text = decls(capsys, src, tmp_path, "--scaffold", "newlib", *AUTHOR, json_out=False)
    lines = text.rstrip().split("\n")
    assert rc == 0 and lines[0].startswith("decls: OK (text/1): 5 identical, 1 dropped") and lines[-1] == lines[0]
    assert "11 file(s) written to" in lines[0] and "git initialized, nothing committed" in lines[0]
    assert "target at not committed" in text and "  New/A.lean" in text
    assert "lean-pkg-extract recheck --ledger" in text and "--target-commit HEAD" in text


# --------------------------------------------------------------------------------------------------
# refusals: DIR

def test_a_non_empty_output_is_refused(src, tmp_path, capsys):
    write(tmp_path / "newlib", {"keep.txt": "mine\n"})
    refused(capsys, src, tmp_path, "exists and is not empty")
    assert sorted(p.name for p in (tmp_path / "newlib").iterdir()) == ["keep.txt"]


def test_an_output_that_is_a_file_is_refused(src, tmp_path, capsys):
    (tmp_path / "newlib").write_text("x")
    refused(capsys, src, tmp_path, "exists and is not a directory")


def test_an_output_inside_src_is_refused_also_through_a_link(src, tmp_path, capsys):
    refused(capsys, src, tmp_path, "inside the source repository", out="src/out")
    (tmp_path / "via").symlink_to(src[0])
    refused(capsys, src, tmp_path, "inside the source repository", out="via/out")
    assert not (src[0] / "out").exists()


def test_an_output_that_is_a_symlink_is_refused(src, tmp_path, capsys):
    (tmp_path / "real").mkdir()
    (tmp_path / "newlib").symlink_to(tmp_path / "real")
    refused(capsys, src, tmp_path, "a symbolic link")
    assert list((tmp_path / "real").iterdir()) == []


def test_an_output_whose_parent_is_missing_is_refused(src, tmp_path, capsys):
    refused(capsys, src, tmp_path, "its parent directory does not exist", out="no/such/newlib")


def test_a_leftover_staging_directory_is_refused_and_kept(src, tmp_path, capsys):
    stage = stage_of(tmp_path)
    stage.mkdir()
    (stage / "half.lean").write_text("x")
    rc, data = decls(capsys, src, tmp_path)
    assert rc == 1 and any("staging directory .newlib.lean-pkg-stage exists" in r for r in data["refusals"])
    assert (stage / "half.lean").read_text() == "x"                       # never deleted or reused
    assert not (tmp_path / "newlib").exists() and not (tmp_path / "ledger.json").exists()


def test_a_public_safety_hit_writes_nothing(src, tmp_path, capsys):
    repo, _sha = src
    write(repo, {"Src/A.lean": SRC_A.replace("/-- The main lemma. -/", "/-- From /home/alice/notes. -/")})
    sha = commit_all(repo)
    before = file_stats(repo)
    rc, data = decls(capsys, (repo, sha), tmp_path)
    assert rc == 1 and not data["written"] and data["public_safety"]["block_hits"] == 1, data["refusals"]
    assert any("public-safety: 1 blocking hit(s) in the staged tree; nothing written" in r for r in data["refusals"])
    assert any("home-path" in r and "New/A.lean" in r for r in data["refusals"])
    assert not (tmp_path / "newlib").exists() and not stage_of(tmp_path).exists()
    assert not (tmp_path / "ledger.json").exists() and file_stats(repo) == before


def test_the_source_patterns_file_applies_to_the_staged_tree(src, tmp_path, capsys):
    repo, sha = src
    (repo / ".lean-pkg-private-patterns").write_text("block: Inhabited\n")   # untracked, as it should be
    rc, data = decls(capsys, src, tmp_path)
    assert rc == 1 and any(".lean-pkg-private-patterns:1" in r for r in data["refusals"]), data["refusals"]
    assert not (tmp_path / "newlib").exists()


def test_a_failure_mid_write_leaves_nothing_half_written(src, tmp_path, capsys, monkeypatch):
    real_open, calls = open, []

    def flaky(path, mode="r", *a, **k):
        if "x" in mode and "b" in mode:
            calls.append(path)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
        return real_open(path, mode, *a, **k)
    monkeypatch.setattr(xw, "open", flaky, raising=False)
    data = refused(capsys, src, tmp_path, "writing failed (No space left on device); nothing written")
    assert len(calls) == 2 and not (tmp_path / "newlib").exists()
    assert data["output"]["files"]                                         # what it was writing is listed


def test_a_failed_move_removes_the_ledger_it_wrote(src, tmp_path, capsys, monkeypatch):
    def no_replace(a, b):
        assert (tmp_path / "ledger.json").is_file()                        # written just before the move
        raise OSError(39, "Directory not empty")
    monkeypatch.setattr(xw.os, "replace", no_replace)
    refused(capsys, src, tmp_path, "writing failed (Directory not empty)", "--scaffold", "newlib", *AUTHOR)
    assert not (tmp_path / "newlib").exists()


# --------------------------------------------------------------------------------------------------
# refusals: the input (S2's, reached through decls) and --scaffold

def test_s2_refusals_reach_decls_and_nothing_is_written(src, tmp_path, capsys):
    refused(capsys, src, tmp_path, "--rename Old.A.nope=New.A.x: stale", "--rename", "Old.A.nope=New.A.x")
    refused(capsys, src, tmp_path, "--drop Old.A.nope: stale", "--drop", "Old.A.nope:gone")
    refused(capsys, src, tmp_path, "--drop Old.A.f: still referenced by Old.B.g", "--drop", "Old.A.f:not wanted")
    refused(capsys, src, tmp_path, "--drop Old.A.f: the reason must be one non-empty line", "--drop", "Old.A.f: ")
    refused(capsys, src, tmp_path, "rename map: Old.A.uses_foo -> Old.A.foo chains into another rename",
            "--rename", "Old.A.uses_foo=Old.A.foo")
    mapping = ["--from-module", "Src/Nope.lean:New.Nope"]
    refused(capsys, src, tmp_path, "source Src/Nope.lean: missing at", mapping=mapping)


def test_a_bad_commit_is_refused(src, tmp_path, capsys):
    repo, _sha = src
    rc, text = run(capsys, "decls", "--repo", str(repo), "--commit", "nope", "--output", str(tmp_path / "newlib"),
                   *MAP, "--ledger", str(tmp_path / "ledger.json"), "--json")
    data = json.loads(text)
    assert rc == 1 and any("--commit 'nope' is not a commit" in r for r in data["refusals"])
    assert not (tmp_path / "newlib").exists()


def test_an_import_of_an_unextracted_local_module_is_refused(src, tmp_path, capsys):
    mapping = ["--from-module", "Src/B.lean:New.B", "--namespace", "Old.B=New.B"]
    refused(capsys, src, tmp_path, "source Src/B.lean imports Src.A, a module of the source", mapping=mapping)


def test_ledger_refusals_leave_no_output_behind(src, tmp_path, capsys):
    (tmp_path / "ledger.json").write_text("{}")
    rc, data = decls(capsys, src, tmp_path)
    assert rc == 1 and any("exists; a ledger is evidence" in r for r in data["refusals"])
    assert not (tmp_path / "newlib").exists() and (tmp_path / "ledger.json").read_text() == "{}"
    (tmp_path / "ledger.json").unlink()
    rc, data = decls(capsys, src, tmp_path, name="newlib/ledger.json")
    assert rc == 1 and any("--ledger" in r and "inside --output DIR" in r for r in data["refusals"])
    assert not (tmp_path / "newlib").exists()
    rc, data = decls(capsys, src, tmp_path, name="src/ledger.json")
    assert rc == 1 and any("inside the source repository" in r for r in data["refusals"])


@pytest.mark.parametrize("name", ["NewLib", "new_lib", "x--y", "a" * 65])
def test_an_invalid_scaffold_name_is_refused(src, tmp_path, capsys, name):
    refused(capsys, src, tmp_path, f"--scaffold {name!r}: lower-case letters", "--scaffold", name, *AUTHOR)


def test_a_scaffold_without_an_author_is_refused(src, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(scaffold, "_git_author", lambda: None)
    refused(capsys, src, tmp_path, "CITATION.cff needs an author", "--scaffold", "newlib")


def test_a_scaffold_into_an_existing_package_is_refused(src, tmp_path, capsys):
    write(tmp_path / "pkg", {"lakefile.toml": 'name = "pkg"\n'})
    refused(capsys, src, tmp_path, "lies inside an existing Lake package", "--scaffold", "newlib", *AUTHOR,
            out="pkg/newlib")
    assert sorted(p.name for p in (tmp_path / "pkg").iterdir()) == ["lakefile.toml"]


def test_a_scaffold_needs_the_source_toolchain(tmp_path, capsys):
    files = {k: v for k, v in SRC_FILES.items() if k != "lean-toolchain"}
    repo = tmp_path / "src"
    sha = make_repo(repo, files)
    refused(capsys, (repo, sha), tmp_path, "--scaffold: no lean-toolchain", "--scaffold", "newlib", *AUTHOR)


def test_package_flags_without_scaffold_are_a_usage_error(src, tmp_path, capsys):
    rc, data = decls(capsys, src, tmp_path, *AUTHOR)
    assert rc == 2 and data == {"ok": False, "error": "--author and --license describe the package of --scaffold "
                                                      "NAME; pass --scaffold", "usage": True}


def test_new_and_allow_differs_are_not_decls_flags(src, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        decls(capsys, src, tmp_path, "--new", "New.A.x:r")
    assert e.value.code == 2


# --------------------------------------------------------------------------------------------------
# the write helper on its own

def test_output_refusals_and_staging_name(tmp_path):
    assert xw.output_refusals(tmp_path / "a", "--output", [("SRC", None)]) == []
    assert xw.stage_path(tmp_path / "a") == tmp_path / ".a.lean-pkg-stage"
    (tmp_path / "s").mkdir()
    assert xw.output_refusals(tmp_path / "s" / "x", "--output", [("SRC", tmp_path / "s")])[0].endswith(
        "lean-pkg-extract leaves the source unchanged, so write outside it")


def test_write_tree_refuses_an_unsafe_relative_path_before_staging(tmp_path):
    with pytest.raises(xw.WriteRefused) as e:
        xw.write_tree(tmp_path / "o", {"../escape.lean": b"x"}, scan_pkg={"name": "x"}, patterns_root=None,
                      source_repo=None)
    assert e.value.reasons[0].startswith("not written: ../escape.lean") and not xw.stage_path(tmp_path / "o").exists()


def test_write_tree_serves_a_minimal_package_record(tmp_path):
    scan = xw.write_tree(tmp_path / "o", {"A.lean": b"theorem a : True := trivial\n"}, scan_pkg={"name": "x"},
                         patterns_root=None, source_repo=None)
    assert scan["ok"] and (tmp_path / "o" / "A.lean").is_file() and not xw.stage_path(tmp_path / "o").exists()


# --------------------------------------------------------------------------------------------------
# the target builds (core Lean, when the toolchain is installed)

def _toolchain():
    from .test_new import TC, _toolchain_bin
    return TC, _toolchain_bin()


CORE_A = """\
namespace Old.Core

/-- Doubling. -/
def double (n : Nat) : Nat := n + n

-- a private remark
theorem double_eq (n : Nat) : double n = 2 * n := by
  unfold double
  omega

theorem scratch : 1 = 1 := rfl

end Old.Core
"""


@pytest.mark.skipif(_toolchain()[1] is None, reason="the pinned Lean toolchain is not installed")
@pytest.mark.timeout(900)
def test_the_scaffolded_core_lean_target_builds(tmp_path, capsys):
    tc, bin_dir = _toolchain()
    repo = tmp_path / "src"
    sha = make_repo(repo, {"lakefile.toml": 'name = "paper"\n\n[[lean_lib]]\nname = "Src"\n',
                           "lean-toolchain": tc + "\n", "Src/Core.lean": CORE_A})
    mapping = ["--from-module", "Src/Core.lean:Lib.Core", "--namespace", "Old.Core=Lib.Core",
               "--drop", "Old.Core.scratch:not needed"]
    rc, data = decls(capsys, (repo, sha), tmp_path, "--scaffold", "corelib", *AUTHOR, mapping=mapping)
    assert rc == 0, (data["refusals"], data["failures"])
    out = tmp_path / "newlib"
    env = dict(os.environ, PATH=os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]), ELAN_TOOLCHAIN=tc,
               LAKE_ARTIFACT_CACHE="false")
    p = subprocess.run([str(bin_dir / "lake"), "build"], cwd=out, env=env, capture_output=True, text=True,
                       timeout=600, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (out / ".lake" / "build" / "lib" / "lean" / "Lib" / "Core.olean").is_file(), p.stdout
