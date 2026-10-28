#!/usr/bin/env python3
"""lean-pkg-extract recheck --public-summary: the public summary of an extraction ledger (proposal 0009,
stage S5). The fixtures are those of test_extract_ledger.py: a private source repository and a target
repository holding the hand-ported library."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from lean_pkg import extract_summary as xs

from .test_extract_ledger import (SRC_FILES, TGT_A, by_source, commit_all, ledger, retarget, run,  # noqa: F401
                                  repos)
from .test_extract_plan import file_stats, git_state

# Every string of the source side the fixture's ledger holds: source paths, module names, declaration
# names (a rename's old name included), the drop reason, the namespaces.
SOURCE_STRINGS = ("Src/A.lean", "Src/B.lean", "Src.A", "Src.B", "Old.A", "Old.B", "Old", "aux",
                  "exploration only", "paper", "Src", "lakefile", "Mathlib")


@pytest.fixture
def recorded(repos, tmp_path, capsys):
    assert ledger(capsys, repos, tmp_path)[0] == 0
    return tmp_path / "ledger.json"


def recheck(capsys, repos, ledger_path, summary, *extra, json_out=True):
    src, _sha, tgt, _t = repos
    argv = ["recheck", "--ledger", str(ledger_path), "--repo", str(src), "--target", str(tgt)]
    if summary is not None:
        argv += ["--public-summary", str(summary)]
    rc, out = run(capsys, *argv, *extra, *(["--json"] if json_out else []))
    return rc, (json.loads(out) if json_out else out)


def refused(capsys, repos, recorded, summary, needle, *extra) -> dict:
    rc, out = recheck(capsys, repos, recorded, summary, *extra)
    assert rc == 1 and not out["ok"], out
    assert any(needle in r for r in out["refusals"]), out["refusals"]
    assert out["public_summary"]["written"] is False and out["public_summary"]["sha256"] is None
    return out


# --------------------------------------------------------------------------------------------------
# the summary

def test_the_summary_is_written_with_exactly_its_keys(repos, recorded, tmp_path, capsys):
    _s, _sha, _tgt, t = repos
    path = tmp_path / "summary.json"
    rc, out = recheck(capsys, repos, recorded, path)
    assert rc == 0 and out["ok"], out["refusals"]
    data = path.read_bytes()
    assert out["public_summary"] == {"written": True, "sha256": hashlib.sha256(data).hexdigest(),
                                     "include_source_commit": False,
                                     "public_safety": {"ok": True, "block_hits": 0, "warn_hits": 0}}
    s = json.loads(data)
    assert list(s) == sorted(xs.SUMMARY_KEYS)
    assert s["schema"] == "lean-pkg-extract-ledger-summary/1" and s["method"] == "text/1" and s["ok"] is True
    assert s["ledger_sha256"] == hashlib.sha256(recorded.read_bytes()).hexdigest()
    assert s["at_ledger_commits"] is True and s["source_commit"] is None
    assert s["target"] == {"commit": t, "modules": ["New.A", "New.B"]}
    assert s["totals"] == {"IDENTICAL": 5, "DROPPED": 1, "DIFFERS": 0, "NEW": 0, "MISSING": 0, "weakened": 0}
    rows = by_source(out)
    assert all(list(e) == sorted(xs.SUMMARY_ENTRY_KEYS) for e in s["entries"])
    assert all(list(c) == sorted(xs.SUMMARY_CONTEXT_KEYS) for c in s["context"])
    assert [(e["target"], e["verdict"]) for e in s["entries"]] == [
        (None, "DROPPED"), ("New.A.bar", "IDENTICAL"), ("New.A.f", "IDENTICAL"), ("New.A.uses_foo", "IDENTICAL"),
        ("instance#1@New.A", "IDENTICAL"), ("New.B.g", "IDENTICAL")]
    bar = next(e for e in s["entries"] if e["target"] == "New.A.bar")
    assert bar == {"target": "New.A.bar", "target_module": "New.A", "verdict": "IDENTICAL",
                   "source_sha256": rows["Old.A.foo"]["source_sha256"],
                   "target_sha256": rows["Old.A.foo"]["target_sha256"]}
    dropped = s["entries"][0]
    assert dropped == {"target": None, "target_module": "New.A", "verdict": "DROPPED",
                       "source_sha256": rows["Old.A.aux"]["source_sha256"], "target_sha256": None}
    assert [(c["target_module"], c["verdict"]) for c in s["context"]] == [("New.A", "IDENTICAL"),
                                                                           ("New.B", "IDENTICAL")]
    assert data.decode() == json.dumps(s, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_no_source_string_appears_in_the_summary(repos, recorded, tmp_path, capsys):
    src, sha, _tgt, _t = repos
    path = tmp_path / "summary.json"
    assert recheck(capsys, repos, recorded, path)[0] == 0
    text = path.read_text()
    led = json.loads(recorded.read_text())
    secrets = set(SOURCE_STRINGS) | {sha, str(src), str(tmp_path)}
    secrets |= {r["path"] for r in led["source"]["modules"]} | {r["module"] for r in led["source"]["modules"]}
    secrets |= {r["blob"] for r in led["source"]["modules"]} | {r["sha256"] for r in led["source"]["modules"]}
    secrets |= {e["source"] for e in led["entries"]}
    # the hash before the map is private unless it equals the mapped one (nothing to map in that declaration)
    secrets |= {e["source_raw_sha256"] for e in led["entries"] if e["source_raw_sha256"] != e["source_sha256"]}
    secrets |= {d["reason"] for d in led["map"]["drops"]} | {led["source"]["lake_manifest_sha256"]}
    secrets |= {m["from"] for m in led["map"]["modules"]} | {n["from"] for n in led["map"]["namespaces"]}
    secrets |= {n["from"] for n in led["map"]["renames"]}
    leaked = sorted(x for x in secrets if x and x in text)
    assert leaked == [], leaked
    assert "warning" not in text and "reason" not in text and "path" not in text


def test_the_source_commit_only_on_request(repos, recorded, tmp_path, capsys):
    _src, sha, _tgt, _t = repos
    path = tmp_path / "summary.json"
    rc, out = recheck(capsys, repos, recorded, path, "--include-source-commit")
    assert rc == 0 and out["public_summary"]["include_source_commit"] is True
    assert json.loads(path.read_text())["source_commit"] == sha


def test_the_summary_is_deterministic(repos, recorded, tmp_path, capsys):
    assert recheck(capsys, repos, recorded, tmp_path / "one.json")[0] == 0
    assert recheck(capsys, repos, recorded, tmp_path / "two.json", json_out=False)[0] == 0
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


def test_the_summary_may_live_inside_the_target_and_the_workspaces_are_unchanged(repos, recorded, capsys):
    src, _sha, tgt, _t = repos
    before = (git_state(src), file_stats(src))
    rc, out = recheck(capsys, repos, recorded, tgt / "EXTRACTION.json")
    assert rc == 0 and out["public_summary"]["written"]
    assert json.loads((tgt / "EXTRACTION.json").read_text())["schema"] == xs.SUMMARY_SCHEMA
    assert file_stats(src) == before[1] and git_state(src) == before[0]
    assert sorted(p.name for p in tgt.parent.iterdir() if "lean-pkg-stage" in p.name) == []
    assert sorted(p.name for p in tgt.iterdir() if "lean-pkg-stage" in p.name) == []


def test_at_newer_commits_the_summary_describes_the_recheck(repos, recorded, tmp_path, capsys):
    t2 = retarget(repos, {"README.md": "a library\n"})
    path = tmp_path / "summary.json"
    rc, out = recheck(capsys, repos, recorded, path, "--target-commit", "HEAD")
    assert rc == 0 and out["ok"], out["refusals"]
    s = json.loads(path.read_text())
    assert s["at_ledger_commits"] is False and s["target"]["commit"] == t2


def test_text_output_names_the_summary_in_the_verdict_lines(repos, recorded, tmp_path, capsys):
    path = tmp_path / "summary.json"
    rc, text = recheck(capsys, repos, recorded, path, json_out=False)
    lines = text.splitlines()
    assert rc == 0 and lines[0] == lines[-1] and lines[0].startswith("recheck: OK (text/1)")
    assert lines[0].endswith(f"; public summary written to {path}")
    assert "public-safety of the public summary: 0 blocking, 0 warn-only" in lines


# --------------------------------------------------------------------------------------------------
# without the flag: nothing changes

def test_without_the_flag_the_output_has_no_summary_keys(repos, recorded, tmp_path, capsys):
    rc, out = recheck(capsys, repos, recorded, None)
    assert rc == 0 and "public_summary" not in out and "public_summary_path" not in out
    rc, text = recheck(capsys, repos, recorded, None, json_out=False)
    assert rc == 0 and "public summary" not in text and "public-safety" not in text
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.json", "newlib", "src"]


# --------------------------------------------------------------------------------------------------
# refusals: nothing written

def test_an_existing_summary_is_refused_and_kept(repos, recorded, tmp_path, capsys):
    path = tmp_path / "summary.json"
    path.write_text("mine\n")
    refused(capsys, repos, recorded, path, "exists; a summary is written once")
    assert path.read_text() == "mine\n"


def test_a_symlink_is_refused(repos, recorded, tmp_path, capsys):
    path, dest = tmp_path / "summary.json", tmp_path / "elsewhere.json"
    os.symlink(dest, path)
    refused(capsys, repos, recorded, path, "a symlink; name a new regular file")
    assert not dest.exists() and path.is_symlink()


def test_a_missing_directory_is_refused(repos, recorded, tmp_path, capsys):
    refused(capsys, repos, recorded, tmp_path / "no" / "summary.json", "its directory does not exist")


@pytest.mark.parametrize("where", ["work tree", "git directory", "through a link"])
def test_inside_src_is_refused(repos, recorded, tmp_path, capsys, where):
    src, _sha, _tgt, _t = repos
    before = file_stats(src)
    if where == "work tree":
        path = src / "summary.json"
    elif where == "git directory":
        path = src / ".git" / "summary.json"
    else:
        os.symlink(src, tmp_path / "link")
        path = tmp_path / "link" / "summary.json"
    refused(capsys, repos, recorded, path, "inside the source repository")
    assert not (src / "summary.json").exists() and not (src / ".git" / "summary.json").exists()
    assert file_stats(src) == before


def test_a_public_safety_hit_writes_nothing(repos, recorded, tmp_path, capsys):
    src, _sha, _tgt, _t = repos
    (src / ".lean-pkg-private-patterns").write_text("block: uses_foo\n")     # untracked, as it should be
    path = tmp_path / "summary.json"
    out = refused(capsys, repos, recorded, path, "public-safety: 1 blocking hit(s) in the public summary; nothing written")
    assert any(".lean-pkg-private-patterns:1" in r and "summary.json" in r for r in out["refusals"])
    assert out["public_summary"]["public_safety"]["block_hits"] == 1
    assert not path.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["ledger.json", "newlib", "src"]


def test_a_failing_recheck_writes_no_summary(repos, recorded, tmp_path, capsys):
    retarget(repos, {"New/A.lean": TGT_A.replace("def f (n : Nat) : Nat := n + 1", "def f (n : Nat) : Nat := 1 + n")})
    path = tmp_path / "summary.json"
    rc, out = recheck(capsys, repos, recorded, path, "--target-commit", "HEAD")
    assert rc == 1 and out["failures"] and out["public_summary"] == {
        "written": False, "sha256": None, "include_source_commit": False, "public_safety": None}
    assert not path.exists()
    rc, text = recheck(capsys, repos, recorded, path, "--target-commit", "HEAD", json_out=False)
    assert rc == 1 and text.splitlines()[0].endswith("; public summary not written")


def test_a_refused_recheck_writes_no_summary(repos, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}\n")
    path = tmp_path / "summary.json"
    rc, out = recheck(capsys, repos, bad, path)
    assert rc == 1 and out["refusals"] and not out["public_summary"]["written"] and not path.exists()


def test_the_ledger_itself_is_refused_as_the_summary_path(repos, recorded, capsys):
    before = recorded.read_bytes()
    refused(capsys, repos, recorded, recorded, "exists")
    assert recorded.read_bytes() == before


@pytest.mark.parametrize("argv,needle", [
    (["--include-source-commit"], "--include-source-commit adds the source commit"),
    (["--public-summary", "S", "--emit-script", "D"], "--public-summary summarizes a recheck"),
])
def test_usage_errors_exit_2_also_under_json(repos, recorded, tmp_path, capsys, argv, needle):
    src, _sha, tgt, _t = repos
    argv = [a.replace("S", str(tmp_path / "s.json")).replace("D", str(tmp_path / "d")) if a in ("S", "D") else a
            for a in argv]
    rc, out = run(capsys, "recheck", "--ledger", str(recorded), "--repo", str(src), "--target", str(tgt), *argv,
                  "--json")
    data = json.loads(out)
    assert rc == 2 and data["usage"] is True and needle in data["error"]
    assert not (tmp_path / "s.json").exists() and not (tmp_path / "d").exists()


def test_the_flag_belongs_to_recheck_only(repos, tmp_path, capsys):
    src, sha, tgt, _t = repos
    with pytest.raises(SystemExit) as e:
        run(capsys, "ledger", "--repo", str(src), "--commit", sha, "--target", str(tgt), "--from-module",
            "Src/A.lean:New.A", "--ledger", str(tmp_path / "l.json"), "--public-summary", str(tmp_path / "s.json"))
    assert e.value.code == 2 and not (tmp_path / "s.json").exists()


def test_a_types_ledger_is_summarized_with_its_method(repos, tmp_path, capsys):
    from . import test_extract_types as tt
    assert tt.typed(capsys, repos, tmp_path)[0] == 0
    outs = [str(tmp_path / "source.out"), str(tmp_path / "target.out")]
    path = tmp_path / "summary.json"
    rc, out = recheck(capsys, repos, tmp_path / "ledger.json", path, "--from-output", *outs)
    assert rc == 0 and out["public_summary"]["written"], out["refusals"]
    s = json.loads(path.read_text())
    assert s["method"] == "types/1" and s["at_ledger_commits"] is True
    assert all(list(e) == sorted(xs.SUMMARY_ENTRY_KEYS) for e in s["entries"])
    assert "Old." not in path.read_text() and "Src" not in path.read_text()
