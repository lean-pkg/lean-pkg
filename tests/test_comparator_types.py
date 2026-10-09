#!/usr/bin/env python3
"""#25: `comparator config --from-claims NAME --challenge-from-type | --emit-script F | --from-output F`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

from .conftest import _git, commit_claims, repo_head, set_lease

LEAN = ("theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\ntheorem zap : True := trivial\n"
        "theorem wow : True := trivial\n")


def claim(cid, decl, kind="theorem", file="Demo/Main.lean"):
    return {"id": f"beta/{cid}", "decl": decl, "kind": kind, "summary": f"{cid} summary",
            "file": file, "status": "reusable", "superseded_by": None}


CLAIMS = {"schema": "claims/1", "package": "beta", "claims": [
    claim("foo-le-bar", "Demo.foo_le_bar"),          # configured by hand (comparator.json)
    claim("baz", "Demo.baz", kind="lemma"),           # ok, Init-only type
    claim("zap", "Demo.zap"),                         # Lean reports an error
    claim("wow", "Demo.wow"),                         # ok, uses a project definition, universe levels
]}

# Canned driver output per declaration, keyed by the last name component the fake lake finds.
FAKE_LAKE = r'''
import json, os, re, sys
log = os.environ["FAKE_LAKE_LOG"]
args = sys.argv[1:]
with open(log, "a") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd(), "threads": os.environ.get("LEAN_NUM_THREADS")}) + "\n")
assert args[:2] == ["env", "lean"], args
src = open(args[2], encoding="utf-8").read()
rt = re.search(r"^-- LEANPKG-ROUNDTRIP (\S+)$", src, re.M)
if rt:                                             # the round-trip check (#31)
    assert "set_option autoImplicit false" in src and "@_root_." + rt.group(1) in src, src
    if rt.group(1) in os.environ.get("FAKE_LAKE_RT_REJECT", "").split(","):
        print(args[2] + ":6:5: error: type mismatch")
        print("  " + rt.group(1))
        print("has type")
        print("  ∀ (n : ℕ), ↑n + 1 = ↑(n + 1)")
        print("but is expected to have type")
        print("  ∀ (n : ℕ), n + 1 = n + 1")
        sys.exit(1)
    sys.exit(0)
comps = re.findall(r'\.str "([^"]*)"', src)
imp = re.search(r"^import (\S+)$", src, re.M).group(1)
decl = ".".join(comps)
if os.environ.get("FAKE_LAKE_ENFILE_FIRST"):
    import fcntl
    with open(log + ".count", "a+") as c:          # calls so far, across concurrent processes
        fcntl.flock(c, fcntl.LOCK_EX)
        c.seek(0)
        n = len(c.read())
        c.write("x")
    if n < int(os.environ["FAKE_LAKE_ENFILE_FIRST"]):
        print("error: " + args[2] + ": Too many open files in system (error code: 23)")
        sys.exit(1)
if os.environ.get("FAKE_LAKE_FAIL_ALL"):
    print("lake: error: something broke")
    sys.exit(3)
if decl == "Demo.gone":
    print("LEANPKG-ERROR unknown constant " + decl)
elif decl == "Demo.baz":
    print("LEANPKG-LEVELS \nLEANPKG-MODULES Init.Prelude\nLEANPKG-TYPE-BEGIN\nTrue\nLEANPKG-TYPE-END")
elif decl == "Demo.wow":
    print(args[2] + ":3:0: warning: declaration uses 'sorry'")
    print("LEANPKG-LEVELS u_1,v")
    print("LEANPKG-MODULES Init.Prelude," + imp)
    print("LEANPKG-TYPE-BEGIN")
    print("∀ {α : Type u_1} (s : Demo.Thing α),")
    print("  s = s")
    print("LEANPKG-TYPE-END")
else:
    print(args[2] + ":1:0: error: unknown module prefix 'Demo'")
    print("No directory 'Demo' or file 'Demo.olean' in the search path")
    sys.exit(1)
'''


@pytest.fixture()
def fake_lake(tmp_path, monkeypatch):
    """PATH: a fake elan bin with git, bash, python3 (real, symlinked) and a canned `lake`."""
    bindir = tmp_path / "elan" / "bin"
    bindir.mkdir(parents=True)
    monkeypatch.setenv("ELAN_HOME", str(tmp_path / "elan"))
    for t in ("git", "bash"):
        (bindir / t).symlink_to(shutil.which(t))
    (bindir / "python3").symlink_to(sys.executable)
    lake = bindir / "lake"
    lake.write_text(f"#!{sys.executable}\n{FAKE_LAKE}")
    lake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("FAKE_LAKE_LOG", str(tmp_path / "lake.log"))
    monkeypatch.setenv("LEAN_PKG_TYPES_BACKOFF_SECONDS", "0")
    for v in ("LEAN_PKG_TYPES_JOBS", "LEAN_PKG_TYPES_TIMEOUT", "FAKE_LAKE_FAIL_ALL", "LEAN_NUM_THREADS",
              "FAKE_LAKE_ENFILE_FIRST", "FAKE_LAKE_RT_REJECT"):
        monkeypatch.delenv(v, raising=False)
    return bindir


def synced(lp, env, capsys, lakefile=True):
    if lakefile:
        (env.repo_b / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage beta\nlean_lib Demo\n")
    commit_claims(env.repo_b, yaml.safe_dump(CLAIMS, sort_keys=False), lean=LEAN)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()


def cc(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta", *extra])
    return rc, capsys.readouterr()


def dry_cfg(cap):
    return json.loads(cap.out[cap.out.index("\n{") + 1:])


def lake_calls(tmp_path, round_trip=False):
    """The logged `lake` calls: type extractions, or with round_trip=True the round-trip checks."""
    p = tmp_path / "lake.log"
    calls = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return [c for c in calls if os.path.basename(c["args"][2]).startswith("r") == round_trip]


def todo_by_claim(cfg):
    return {t["claim"]: t for t in cfg["lean_pkg_scaffold"]["todo"]}


def test_local_mode_prefills_challenge_and_solution_with_per_entry_fallback(lp, env, fake_lake, tmp_path, capsys):
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--allow-partial", "--json")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["written"] is True and (data["existing"], data["added"]) == (1, 3)
    assert data["types"]["round_trip_failed"] == 0
    assert data["types"]["extraction_failed"] == 1 and data["types"]["allow_partial"] is True
    assert data["types"]["driver_summary"]["errors"] == 1 and data["types"]["driver_summary"]["filled"] == 2
    assert data["types"]["mode"] == "local" and data["types"]["commit"] == repo_head(env.repo_b)
    assert (data["types"]["prefilled"], data["types"]["type_errors"], data["types"]["needs_restatement"]) == (2, 1, 2)
    cfg = json.loads((env.repo_b / "comparator.json").read_text())
    todo = todo_by_claim(cfg)
    baz = todo["beta/baz"]
    assert baz["type"] == "True" and baz["needs_restatement"] is False and "type_error" not in baz
    assert baz["challenge"] == ("/-- baz summary -/\ntheorem BetaAudit.Demo.baz :\n    True := by\n  sorry")
    assert baz["solution"] == "theorem BetaAudit.Demo.baz :\n    True := by\n  exact @_root_.Demo.baz"
    assert "TODO" not in baz["challenge"]
    wow = todo["beta/wow"]
    assert wow["needs_restatement"] is True and "Demo.Main" in wow["restatement_reason"]
    assert wow["challenge"].startswith("-- TODO: restate in Mathlib vocabulary: the type uses project definitions")
    assert "theorem BetaAudit.Demo.wow.{u_1, v} :\n    ∀ {α : Type u_1} (s : Demo.Thing α),\n      s = s" \
        in wow["challenge"]
    assert wow["solution"].endswith("exact @_root_.Demo.wow")
    zap = todo["beta/zap"]
    assert zap["needs_restatement"] is True and zap["type_error"] == "unknown module prefix 'Demo'"
    assert zap["restatement_reason"] == "extraction_failed"
    assert zap["challenge"].startswith("TODO: ") and zap["solution"].startswith("TODO: ")
    calls = lake_calls(tmp_path)
    assert len(calls) == 3 and all(c["args"][:2] == ["env", "lean"] for c in calls)
    assert len(lake_calls(tmp_path, round_trip=True)) == 2              # baz and wow printed a type
    assert all(c["cwd"] == str(env.repo_b) for c in calls)
    assert all(not c["args"][2].startswith(str(env.repo_b)) for c in calls)     # scratch files in a temp dir
    status = _git(env.repo_b, "status", "--porcelain").stdout.split("\n")
    assert [s for s in status if s.strip()] == [" M comparator.json"]           # nothing else in the checkout


def test_dry_run_runs_lean_but_writes_nothing_and_second_run_adds_nothing(lp, env, fake_lake, tmp_path, capsys):
    synced(lp, env, capsys)
    before = (env.repo_b / "comparator.json").read_text()
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--dry-run", "--allow-partial")
    assert rc == 0, cap.err
    assert "types: 2 pre-filled from the elaborated type, 1 type error(s)" in cap.out
    cfg = dry_cfg(cap)
    assert todo_by_claim(cfg)["beta/baz"]["type"] == "True"
    assert (env.repo_b / "comparator.json").read_text() == before
    assert cc(lp, env, capsys, "--challenge-from-type", "--allow-partial")[0] == 0
    n = len(lake_calls(tmp_path))
    after = (env.repo_b / "comparator.json").read_text()
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["added"] == 0 and data["written"] is False and data["existing"] == 4
    assert (env.repo_b / "comparator.json").read_text() == after
    assert len(lake_calls(tmp_path)) == n                     # nothing to add: Lean is not run again


def test_refuses_without_a_lakefile(lp, env, fake_lake, tmp_path, capsys):
    synced(lp, env, capsys, lakefile=False)
    before = (env.repo_b / "comparator.json").read_text()
    for flags in (["--challenge-from-type"], ["--emit-script", str(tmp_path / "d.sh")]):
        rc, cap = cc(lp, env, capsys, *flags)
        assert rc == 1 and "no lakefile.lean or lakefile.toml" in cap.err
    assert (env.repo_b / "comparator.json").read_text() == before
    assert not (tmp_path / "d.sh").exists() and lake_calls(tmp_path) == []


def test_refuses_a_lake_that_is_not_elans(lp, env, fake_lake, tmp_path, monkeypatch, capsys):
    synced(lp, env, capsys)
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("ELAN_HOME", str(other))
    rc, cap = cc(lp, env, capsys, "--challenge-from-type")
    assert rc == 1 and "is not elan's" in cap.err and lake_calls(tmp_path) == []


def test_emit_writes_the_driver_and_lists_the_declarations(lp, env, fake_lake, tmp_path, capsys):
    synced(lp, env, capsys)
    script = tmp_path / "out" / "types.sh"
    rc, cap = cc(lp, env, capsys, "--emit-script", str(script), "--jobs", "3", "--timeout", "7", "--json")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["written"] is True and data["script"] == str(script) and data["commit"] == repo_head(env.repo_b)
    assert data["decls"] == [{"decl": "Demo.baz", "claim": "beta/baz", "module": "Demo.Main"},
                             {"decl": "Demo.zap", "claim": "beta/zap", "module": "Demo.Main"},
                             {"decl": "Demo.wow", "claim": "beta/wow", "module": "Demo.Main"}]
    text = script.read_text()
    assert text.startswith("#!/usr/bin/env bash\n") and os.access(script, os.X_OK)
    assert repo_head(env.repo_b) in text and "comparator-types/1" in text
    assert '\\"jobs\\": 3' in text and '\\"timeout\\": 7' in text
    assert "LEANPKG-TYPE-BEGIN" in text and "pp.analyze true" in text
    assert lake_calls(tmp_path) == []                                      # nothing ran here
    assert _git(env.repo_b, "status", "--porcelain").stdout.strip() == ""   # checkout untouched
    rc, cap = cc(lp, env, capsys, "--emit-script", str(env.repo_b / "types.sh"))
    assert rc == 1 and "inside the checkout" in cap.err
    rc, cap = cc(lp, env, capsys, "--emit-script", str(env.repo_b / ".lake" / "types.sh"))
    assert rc == 0, cap.err


def run_driver(env, script, out):
    return subprocess.run(["bash", str(script), str(out)], cwd=env.repo_b, capture_output=True, text=True,
                          env=dict(os.environ))


def test_emit_then_ingest_equals_local_mode(lp, env, fake_lake, tmp_path, capsys):
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--dry-run", "--allow-partial")
    local = dry_cfg(cap)
    script, out = tmp_path / "d.sh", tmp_path / "types.json"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    p = run_driver(env, script, out)
    assert p.returncode == 1, p.stderr                       # one extraction failed (zap: lean exit 1)
    assert "1 extraction error(s)" in p.stderr and "--allow-partial" in p.stderr
    doc = json.loads(out.read_text())
    assert doc["summary"] == {"ok": False, "total": 3, "filled": 2, "errors": 1, "round_trip_failed": 0,
                              "declaration_errors": 0,
                              "jobs": min(2, len(os.sched_getaffinity(0))), "backoff": []}
    zap = doc["results"][1]
    assert zap["rc"] == 1 and zap["failure"] == "extraction" and "Demo.olean" in zap["output_tail"]
    assert doc["schema"] == "comparator-types/1" and doc["package"] == "beta"
    assert doc["commit"] == repo_head(env.repo_b)
    assert [(r["decl"], r["status"]) for r in doc["results"]] == [("Demo.baz", "ok"), ("Demo.zap", "error"),
                                                                  ("Demo.wow", "ok")]
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--dry-run")
    jobs = doc["summary"]["jobs"]
    assert rc == 1 and f"extraction failed for 1 of 3 declaration(s) at {jobs} job(s) (2 filled)" in cap.err
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--dry-run", "--allow-partial")
    assert rc == 0, cap.err
    assert dry_cfg(cap) == local


def write_output(tmp_path, env, **over):
    doc = {"schema": "comparator-types/1", "package": "beta", "commit": repo_head(env.repo_b),
           "toolchain": "leanprover/lean4:v4.35.0",
           "results": [{"decl": "Demo.baz", "type": "True", "status": "ok", "error": None, "levels": [],
                        "modules": ["Init.Prelude"], "round_trip": "ok"},
                       {"decl": "Demo.zap", "type": None, "status": "error",
                        "error": "timed out after 120s\nsecond line", "levels": [], "modules": []},
                       {"decl": "Demo.wow", "type": "∀ (n : Nat), n = n", "status": "ok", "error": None}]}
    doc.update(over)
    f = tmp_path / "types.json"
    f.write_text(json.dumps(doc))
    return f


def test_ingest_a_good_file_with_per_entry_fallback(lp, env, tmp_path, capsys):
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--from-output", str(write_output(tmp_path, env)), "--json", "--allow-partial")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["types"]["mode"] == "ingest" and data["types"]["toolchain"] == "leanprover/lean4:v4.35.0"
    todo = todo_by_claim(json.loads((env.repo_b / "comparator.json").read_text()))
    assert todo["beta/baz"]["needs_restatement"] is False and "exact @_root_.Demo.baz" in todo["beta/baz"]["solution"]
    assert todo["beta/zap"]["type_error"] == "timed out after 120s" and todo["beta/zap"]["needs_restatement"]
    wow = todo["beta/wow"]                     # no `modules` in the result: cannot tell, so flagged
    assert wow["needs_restatement"] is True and "modules" in wow["restatement_reason"]


def test_ingest_missing_result_falls_back(lp, env, tmp_path, capsys):
    synced(lp, env, capsys)
    f = write_output(tmp_path, env, results=[])
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--json")
    assert rc == 1 and "extraction failed for 3 of 3" in cap.err          # a missing result is a failure
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--json", "--allow-partial")
    assert rc == 0, cap.err
    todo = todo_by_claim(json.loads((env.repo_b / "comparator.json").read_text()))
    assert todo["beta/baz"]["type_error"].startswith("no result for Demo.baz in ")


@pytest.mark.parametrize("over,msg", [
    ({"commit": "0" * 40}, "but HEAD is"),
    ({"schema": "comparator-types/0"}, "expected 'comparator-types/1'"),
    ({"package": "alpha"}, "not 'beta'"),
    ({"results": {"x": 1}}, "results must be a list"),
])
def test_ingest_refusals_write_nothing(lp, env, tmp_path, capsys, over, msg):
    synced(lp, env, capsys)
    before = (env.repo_b / "comparator.json").read_text()
    rc, cap = cc(lp, env, capsys, "--from-output", str(write_output(tmp_path, env, **over)))
    assert rc == 1 and msg in cap.err and "nothing written" in cap.err
    assert (env.repo_b / "comparator.json").read_text() == before


def test_driver_refuses_another_commit(lp, env, fake_lake, tmp_path, capsys):
    synced(lp, env, capsys)
    script = tmp_path / "d.sh"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    (env.repo_b / "Demo" / "Extra.lean").write_text("-- more\n")
    _git(env.repo_b, "add", "-A")
    _git(env.repo_b, "commit", "-q", "-m", "more")
    p = run_driver(env, script, tmp_path / "o.json")
    assert p.returncode == 2 and "check out that commit" in p.stderr and not (tmp_path / "o.json").exists()


def test_type_flags_usage_errors(lp, env, tmp_path, capsys):
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--emit-script", "a.sh", "--from-output", "b.json")
    assert rc == 2 and "two halves" in cap.err
    rc, cap = cc(lp, env, capsys, "--jobs", "3")
    assert rc == 2 and "need --challenge-from-type or --emit-script" in cap.err
    rc, cap = cc(lp, env, capsys, "--from-output", "b.json", "--jobs", "3")
    assert rc == 2 and "not to --from-output" in cap.err
    rc, cap = cc(lp, env, capsys, "--emit-script", "a.sh", "--allow-partial")
    assert rc == 2 and "--allow-partial: needs --from-output" in cap.err
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--jobs", "0")
    assert rc == 2 and "--jobs 0" in cap.err
    rc = lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", "x.json",
                  "--challenge-from-type"])
    assert rc == 2 and "comparator config --from-claims" in capsys.readouterr().err


def test_local_run_with_extraction_failures_is_refused_and_writes_nothing(lp, env, fake_lake, tmp_path, capsys):
    """#30: lean exiting non-zero is an extraction failure, not a per-declaration type error."""
    synced(lp, env, capsys)
    before = (env.repo_b / "comparator.json").read_text()
    rc, cap = cc(lp, env, capsys, "--challenge-from-type", "--json")
    assert rc == 1 and "the local run: extraction failed for 1 of 3 declaration(s)" in cap.err
    assert "--allow-partial" in cap.err and "nothing written" in cap.err
    assert (env.repo_b / "comparator.json").read_text() == before


def test_driver_exits_2_when_nothing_was_filled(lp, env, fake_lake, tmp_path, monkeypatch, capsys):
    synced(lp, env, capsys)
    script, out = tmp_path / "d.sh", tmp_path / "types.json"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    monkeypatch.setenv("FAKE_LAKE_FAIL_ALL", "1")
    p = run_driver(env, script, out)
    assert p.returncode == 2, p.stderr
    doc = json.loads(out.read_text())
    assert (doc["summary"]["ok"], doc["summary"]["filled"], doc["summary"]["errors"]) == (False, 0, 3)
    assert all(r["rc"] == 3 and r["error"] == "lake: error: something broke" for r in doc["results"])
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--allow-partial", "--json")
    assert rc == 0, cap.err
    todo = todo_by_claim(json.loads((env.repo_b / "comparator.json").read_text()))
    assert all(todo[c]["restatement_reason"] == "extraction_failed" for c in ("beta/baz", "beta/zap", "beta/wow"))


def test_declaration_errors_are_not_extraction_failures(lp, env, fake_lake, tmp_path, capsys):
    """An unknown constant (Lean ran, exit 0) keeps its TODO without failing the run."""
    claims = dict(CLAIMS, claims=[claim("baz", "Demo.baz", kind="lemma"), claim("gone", "Demo.gone")])
    (env.repo_b / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage beta\nlean_lib Demo\n")
    commit_claims(env.repo_b, yaml.safe_dump(claims, sort_keys=False), lean=LEAN + "theorem gone : True := trivial\n")
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    script, out = tmp_path / "d.sh", tmp_path / "types.json"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    p = run_driver(env, script, out)
    assert p.returncode == 0, p.stderr
    doc = json.loads(out.read_text())
    assert doc["summary"]["ok"] is True and doc["summary"]["declaration_errors"] == 1
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--json")
    assert rc == 0, cap.err
    gone = todo_by_claim(json.loads((env.repo_b / "comparator.json").read_text()))["beta/gone"]
    assert gone["type_error"] == "unknown constant Demo.gone" and "restatement_reason" not in gone


def test_driver_backs_off_on_too_many_open_files(lp, env, fake_lake, tmp_path, monkeypatch, capsys):
    """#30: ENFILE/EMFILE halves the workers and retries only the failed declarations."""
    synced(lp, env, capsys)
    script, out = tmp_path / "d.sh", tmp_path / "types.json"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    text = script.read_text()
    assert "def run_all(" in text and "too many open files" in text and "LEAN_PKG_TYPES_BACKOFF_SECONDS" in text
    assert '\\"jobs\\": null' in text                # no --jobs: the driver picks min(2, CPUs) where it runs
    monkeypatch.setenv("LEAN_PKG_TYPES_JOBS", "4")
    monkeypatch.setenv("FAKE_LAKE_ENFILE_FIRST", "3")      # the first round fails for every declaration
    p = run_driver(env, script, out)
    assert p.returncode == 1, p.stderr                     # only zap's genuine failure is left
    assert "retry 1 with 2 worker(s)" in p.stderr
    doc = json.loads(out.read_text())
    assert doc["summary"]["jobs"] == 4 and (doc["summary"]["filled"], doc["summary"]["errors"]) == (2, 1)
    assert doc["summary"]["backoff"] == [{"round": 1, "jobs": 2, "retried": 3, "recovered": 3}]
    assert [r["status"] for r in doc["results"]] == ["ok", "error", "ok"]
    assert doc["results"][1]["failure"] == "extraction"


def test_driver_backoff_gives_up_after_one_worker_rounds(lp, env, fake_lake, tmp_path, monkeypatch, capsys):
    synced(lp, env, capsys)
    script, out = tmp_path / "d.sh", tmp_path / "types.json"
    assert cc(lp, env, capsys, "--emit-script", str(script), "--jobs", "4")[0] == 0
    monkeypatch.setenv("FAKE_LAKE_ENFILE_FIRST", "1000")
    p = run_driver(env, script, out)
    assert p.returncode == 2, p.stderr                     # nothing filled
    doc = json.loads(out.read_text())
    assert [b["jobs"] for b in doc["summary"]["backoff"]] == [2, 1]
    assert all(r["failure"] == "resource" and r["rc"] == 1 and "Too many open files" in r["error"]
               for r in doc["results"])
    assert doc["summary"]["errors"] == 3
    monkeypatch.setenv("LEAN_PKG_TYPES_JOBS", "1")
    p = run_driver(env, script, out)
    assert [b["jobs"] for b in json.loads(out.read_text())["summary"]["backoff"]] == [1, 1]
    rc, cap = cc(lp, env, capsys, "--from-output", str(out))
    assert rc == 1 and "extraction failed for 3 of 3 declaration(s) at 1 job(s) (0 filled)" in cap.err


def test_round_trip_failure_keeps_the_todo(lp, env, fake_lake, tmp_path, monkeypatch, capsys):
    """#31: a printed type that does not elaborate back to the declaration's is never ready."""
    synced(lp, env, capsys)
    script, out = tmp_path / "d.sh", tmp_path / "types.json"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    assert "LEANPKG-ROUNDTRIP" in script.read_text() and "autoImplicit false" in script.read_text()
    monkeypatch.setenv("FAKE_LAKE_RT_REJECT", "Demo.baz")
    p = run_driver(env, script, out)
    assert p.returncode == 1, p.stderr                       # zap's extraction failure, not the round trip
    assert "1 round-trip failure(s)" in p.stderr
    doc = json.loads(out.read_text())
    assert (doc["summary"]["filled"], doc["summary"]["round_trip_failed"], doc["summary"]["errors"]) == (1, 1, 1)
    baz = doc["results"][0]
    assert (baz["status"], baz["failure"], baz["round_trip"], baz["round_trip_rc"]) == ("error", "round_trip",
                                                                                         "failed", 1)
    assert baz["type"] == "True" and "type mismatch" in baz["error"]
    assert doc["results"][2]["round_trip"] == "ok" and doc["results"][2]["status"] == "ok"
    rc, cap = cc(lp, env, capsys, "--from-output", str(out), "--allow-partial", "--json")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["types"]["round_trip_failed"] == 1 and data["types"]["prefilled"] == 1
    todo = todo_by_claim(json.loads((env.repo_b / "comparator.json").read_text()))
    baz = todo["beta/baz"]
    assert baz["needs_restatement"] is True and baz["restatement_reason"] == "round_trip_failed"
    assert baz["type_error"].startswith("round-trip check failed (exit 1):") and "but is expected" in baz["type_error"]
    assert baz["challenge"].startswith("TODO: ") and baz["solution"].startswith("TODO: ")
    assert baz["printed_type"] == "True" and "type" not in baz


def test_output_without_round_trip_is_never_ready(lp, env, tmp_path, capsys):
    """Output from a driver before #31 has no round_trip: pre-filled, but needs_restatement."""
    synced(lp, env, capsys)
    f = write_output(tmp_path, env, results=[
        {"decl": "Demo.baz", "type": "True", "status": "ok", "error": None, "levels": [], "modules": ["Init.Prelude"]}])
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--allow-partial", "--json")
    assert rc == 0, cap.err
    baz = todo_by_claim(json.loads((env.repo_b / "comparator.json").read_text()))["beta/baz"]
    assert baz["needs_restatement"] is True and "no round-trip check" in baz["restatement_reason"]


def good_output(tmp_path, env):
    return write_output(tmp_path, env, results=[
        {"decl": d, "type": "True", "status": "ok", "error": None, "levels": [], "modules": ["Init.Prelude"],
         "round_trip": "ok"} for d in ("Demo.baz", "Demo.zap", "Demo.wow")])


def test_no_registry_writes_a_scratch_config_under_a_foreign_lease(lp, env, tmp_path, capsys):
    """#31: ingestion for a package someone else holds, without touching their checkout."""
    synced(lp, env, capsys)
    set_lease(env, "beta", agent="vera")
    before_cfg = (env.repo_b / "comparator.json").read_text()
    before_reg = env.registry.read_text()
    f = good_output(tmp_path, env)
    rc, cap = cc(lp, env, capsys, "--from-output", str(f))
    assert rc == 1 and "lease held by vera" in cap.err                   # the normal path still refuses
    scratch = tmp_path / "scratch" / "comparator.json"
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--out", str(scratch), "--no-registry")
    assert rc == 0, cap.err
    assert f"wrote {scratch}" in cap.out and "not done: the package lease was not consulted" in cap.out
    cfg = json.loads(scratch.read_text())
    base = json.loads(before_cfg)
    assert cfg["theorem_names"][:len(base["theorem_names"])] == base["theorem_names"]   # merged, not replaced
    assert set(todo_by_claim(cfg)) == {"beta/baz", "beta/zap", "beta/wow"}
    assert (env.repo_b / "comparator.json").read_text() == before_cfg
    assert env.registry.read_text() == before_reg
    assert _git(env.repo_b, "status", "--porcelain").stdout.strip() == ""
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--out", str(scratch), "--no-registry", "--json",
                 "--dry-run")
    data = json.loads(cap.out)
    assert rc == 0 and data["no_registry"] is True and data["written"] is False and data["out"] == str(scratch)
    assert len(data["not_done"]) == 3


def test_no_registry_usage_and_refusals(lp, env, tmp_path, capsys):
    synced(lp, env, capsys)
    f = good_output(tmp_path, env)
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--no-registry")
    assert rc == 2 and "--no-registry: needs --out FILE" in cap.err
    rc, cap = cc(lp, env, capsys, "--no-registry", "--out", str(tmp_path / "x.json"))
    assert rc == 2 and "--no-registry: needs --from-output or --challenge-from-type" in cap.err
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--no-registry", "--out", str(tmp_path / "x.json"),
                 "--agent", "vera")
    assert rc == 2 and "takes no --agent" in cap.err
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--no-registry", "--out", str(env.repo_b / "c2.json"))
    assert rc == 1 and "--out" in cap.err and "inside the checkout" in cap.err


def test_checkout_overrides_the_registry_path(lp, env, tmp_path, capsys):
    """#31: --checkout DIR, validated against the claims-synced commit and the lakefile."""
    synced(lp, env, capsys)
    clone = tmp_path / "elsewhere" / "beta"
    subprocess.run(["git", "clone", "-q", str(env.repo_b), str(clone)], check=True, capture_output=True)
    f = good_output(tmp_path, env)
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--checkout", str(clone), "--json")
    assert rc == 0, cap.err
    data = json.loads(cap.out)
    assert data["checkout"] == str(clone) and data["written"] is True
    assert set(todo_by_claim(json.loads((clone / "comparator.json").read_text()))) == {"beta/baz", "beta/zap",
                                                                                      "beta/wow"}
    assert "lean_pkg_scaffold" not in (env.repo_b / "comparator.json").read_text()   # registry path untouched
    # a checkout whose HEAD moved past the synced commit with a source change: refused, with the paths
    (clone / "Demo" / "Main.lean").write_text(LEAN + "theorem more : True := trivial\n")
    _git(clone, "commit", "-qam", "more")
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--checkout", str(clone))
    assert rc == 1 and f"--checkout {clone}: HEAD" in cap.err and "Demo/Main.lean" in cap.err
    rc, cap = cc(lp, env, capsys, "--from-output", str(f), "--checkout", str(tmp_path / "nope"))
    assert rc == 1 and "not a directory" in cap.err


def test_checkout_without_a_lakefile_is_refused(lp, env, tmp_path, capsys):
    synced(lp, env, capsys, lakefile=False)
    rc, cap = cc(lp, env, capsys, "--from-output", str(good_output(tmp_path, env)), "--checkout", str(env.repo_b))
    assert rc == 1 and "no lakefile.lean or lakefile.toml" in cap.err


def test_emitted_driver_defaults_lean_threads_to_one_and_keeps_an_explicit_value(lp, env, fake_lake, tmp_path,
                                                                                  monkeypatch, capsys):
    """#34: shared cluster nodes. The --emit-script driver exports LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"
    near the top; the one-shot local run leaves the environment as it is."""
    synced(lp, env, capsys)
    script = tmp_path / "d.sh"
    assert cc(lp, env, capsys, "--emit-script", str(script))[0] == 0
    lines = script.read_text().splitlines()
    export = 'export LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"'
    assert export in lines and lines.index(export) < lines.index("exec python3 - \"$1\" <<'LEAN_PKG_DRIVER'")
    assert lines.index(export) < 20 and "LEAN_PKG_TYPES_JOBS <= 2" in script.read_text()
    run_driver(env, script, tmp_path / "a.json")
    assert {c["threads"] for c in lake_calls(tmp_path)} == {"1"}
    (tmp_path / "lake.log").unlink()
    monkeypatch.setenv("LEAN_NUM_THREADS", "3")
    run_driver(env, script, tmp_path / "b.json")
    assert {c["threads"] for c in lake_calls(tmp_path)} == {"3"}
    (tmp_path / "lake.log").unlink()
    monkeypatch.delenv("LEAN_NUM_THREADS")
    cc(lp, env, capsys, "--challenge-from-type", "--dry-run", "--allow-partial")
    assert {c["threads"] for c in lake_calls(tmp_path)} == {None}
