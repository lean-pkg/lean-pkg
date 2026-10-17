#!/usr/bin/env python3
"""lean-pkg-extract plan: the dry run over a mixed TeX+Lean repository.

Every test builds a small git repository (a paper beside a Lake package and a verification folder) and
runs `lean_pkg.extract.main` in-process. The plan reads git objects only: the source workspace, its
index and HEAD are compared before and after, and the registry loaders are patched to fail."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import tomllib

import pytest

from lean_pkg import cli
from lean_pkg import extract as ex

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHA_A = "a" * 40

LAKE_MANIFEST = {
    "version": "1.1.0", "packagesDir": ".lake/packages", "name": "demo", "lakeDir": ".lake",
    "packages": [{"type": "git", "url": "https://github.com/leanprover-community/mathlib4", "rev": SHA_A,
                  "name": "mathlib", "inputRev": "v4.20.0", "inherited": False, "scope": "leanprover-community",
                  "manifestFile": "lake-manifest.json", "configFile": "lakefile.lean"}],
}

CLAIMS = """\
schema: claims/1
package: demo
claims:
  - id: demo/main-bound
    decl: Demo.main_bound
    kind: theorem
    summary: The main bound holds.
    file: Demo/Main.lean
    status: reusable
  - id: demo/basic-lemma
    decl: Demo.basic_lemma
    kind: lemma
    summary: A basic lemma.
    file: Demo/Basic.lean
    status: internal
"""

FILES = {
    "paper/main.tex": "\\documentclass{amsart}\n",
    "paper/refs.bib": "@article{x}\n",
    "lean/lakefile.toml": 'name = "demo"\ndefaultTargets = ["Demo"]\n\n[[require]]\nname = "mathlib"\n'
                          'scope = "leanprover-community"\nrev = "v4.20.0"\n\n[[lean_lib]]\nname = "Demo"\n',
    "lean/lean-toolchain": "leanprover/lean4:v4.20.0\n",
    "lean/lake-manifest.json": json.dumps(LAKE_MANIFEST, indent=2) + "\n",
    "lean/claims.yaml": CLAIMS,
    "lean/README.md": "# demo\n",
    "lean/Demo.lean": "import Demo.Basic\nimport Demo.Main\n",
    "lean/Demo/Basic.lean": "import Mathlib.Tactic\n-- import Not.A.Module\n\nnamespace Demo\n"
                            "theorem basic_lemma : True := trivial\nend Demo\n",
    "lean/Demo/Main.lean": "import Demo.Basic\n\nnamespace Demo\ntheorem main_bound : True := trivial\nend Demo\n",
    "lean/notes/todo.md": "private plan\n",
    "lean/reviews/round1.md": "reviewer packet\n",
    "lean/.lean-receipts/0123-job.json": "{}\n",
    "lean/session.transcript.txt": "model said\n",
    "lean/docs/overview.tex": "overview\n",
    "verification/check.sh": "#!/bin/sh\nlake build\n",
    "verification/Controls/Bad.lean": "import Demo.Main\n",
    "mail/thread.eml": "From: someone\n",
}


def manifest(**over) -> dict:
    m = {
        "schema": "lean-pkg-extract/1",
        "roots": [{"from": "lean", "to": ".", "kind": "lean-package"},
                  {"from": "verification", "to": "verification", "kind": "verification"}],
        "pins": {"lean_toolchain": "lean/lean-toolchain", "lake_manifest": "lean/lake-manifest.json",
                 "claims": "lean/claims.yaml", "verification": ["verification/check.sh"]},
        "allow_private": [{"path": "lean/docs/overview.tex",
                           "reason": "the statement overview the README links"}],
    }
    m.update(over)
    return m


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def commit_all(repo, msg="c") -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def src(tmp_path):
    repo = tmp_path / "src"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for rel, text in FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    (repo / "verification/check.sh").chmod(0o755)
    (repo / ".gitignore").write_text("build/\n")
    sha = commit_all(repo)
    (repo / "build").mkdir()
    (repo / "build/out.olean").write_text("ignored\n")          # ignored, for --porcelain --ignored
    (repo / "untracked.txt").write_text("untracked\n")          # untracked
    return repo, sha


def write_manifest(tmp_path, m, name="extract.json") -> pathlib.Path:
    p = tmp_path / name
    p.write_text(json.dumps(m, indent=2))
    return p


def run(capsys, repo, commit, man, *extra) -> tuple[int, str]:
    rc = ex.main(["plan", "--repo", str(repo), "--commit", commit, "--manifest", str(man), *extra])
    return rc, capsys.readouterr().out


def plan_json(capsys, repo, commit, man, *extra) -> tuple[int, dict]:
    rc, out = run(capsys, repo, commit, man, "--json", *extra)
    return rc, json.loads(out)


def refused(capsys, repo, commit, man, needle: str) -> dict:
    rc, plan = plan_json(capsys, repo, commit, man)
    assert rc == 1 and not plan["ok"], plan["refusals"]
    assert any(needle in r for r in plan["refusals"]), plan["refusals"]
    return plan


def blob_sha256(repo, commit, path) -> str:
    data = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", f"{commit}:{path}"],
                          capture_output=True, check=True).stdout
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------------------------------
# the clean plan

def test_plan_succeeds_with_exact_inventory_module_and_claim_maps(src, tmp_path, capsys):
    repo, sha = src
    (repo / "lean/Demo/Basic.lean").write_text("-- edited in the working tree, never read\n")
    rc, plan = plan_json(capsys, repo, sha[:10], write_manifest(tmp_path, manifest()))
    assert rc == 0 and plan["ok"], plan["refusals"]
    assert plan["schema"] == "lean-pkg-extract-plan/1"
    assert plan["source"]["commit"] == sha
    assert [(e["to"], e["from"], e["class"]) for e in plan["included"]] == [
        ("Demo.lean", "lean/Demo.lean", "lean-source"),
        ("Demo/Basic.lean", "lean/Demo/Basic.lean", "lean-source"),
        ("Demo/Main.lean", "lean/Demo/Main.lean", "lean-source"),
        ("README.md", "lean/README.md", "legal-readme"),
        ("claims.yaml", "lean/claims.yaml", "pin:claims"),
        ("docs/overview.tex", "lean/docs/overview.tex", "allowed-private"),
        ("lake-manifest.json", "lean/lake-manifest.json", "pin:lake_manifest"),
        ("lakefile.toml", "lean/lakefile.toml", "lake-config"),
        ("lean-toolchain", "lean/lean-toolchain", "pin:lean_toolchain"),
        ("verification/Controls/Bad.lean", "verification/Controls/Bad.lean", "lean-source"),
        ("verification/check.sh", "verification/check.sh", "pin:verification"),
    ]
    by_to = {e["to"]: e for e in plan["included"]}
    basic = by_to["Demo/Basic.lean"]
    # the committed blob, not the edited working-tree file
    assert basic["sha256"] == blob_sha256(repo, sha, "lean/Demo/Basic.lean")
    assert basic["blob"] == git(repo, "rev-parse", f"{sha}:lean/Demo/Basic.lean").stdout.strip()
    assert basic["size"] == len(FILES["lean/Demo/Basic.lean"].encode()) and basic["mode"] == "100644"
    assert by_to["verification/check.sh"]["mode"] == "100755"
    assert plan["modules"] == [
        {"module": "Demo", "file": "Demo.lean", "from": "lean/Demo.lean", "lib": "Demo"},
        {"module": "Demo.Basic", "file": "Demo/Basic.lean", "from": "lean/Demo/Basic.lean", "lib": "Demo"},
        {"module": "Demo.Main", "file": "Demo/Main.lean", "from": "lean/Demo/Main.lean", "lib": "Demo"},
    ]
    assert plan["unowned_lean_files"] == ["verification/Controls/Bad.lean"]
    assert plan["claims"] == [
        {"id": "demo/basic-lemma", "decl": "Demo.basic_lemma", "file": "Demo/Basic.lean", "module": "Demo.Basic"},
        {"id": "demo/main-bound", "decl": "Demo.main_bound", "file": "Demo/Main.lean", "module": "Demo.Main"},
    ]
    pins = plan["pins"]
    assert pins["lean_toolchain"]["value"] == "leanprover/lean4:v4.20.0"
    assert pins["lean_toolchain"]["sha256"] == blob_sha256(repo, sha, "lean/lean-toolchain")
    assert pins["lake_manifest"]["packages"] == [
        {"name": "mathlib", "type": "git", "url": "https://github.com/leanprover-community/mathlib4",
         "rev": SHA_A, "inputRev": "v4.20.0"}]
    assert [p["to"] for p in pins["verification"]] == ["verification/check.sh"]
    assert plan["totals"]["included_files"] == 11 and plan["totals"]["refusals"] == 0
    # paths outside every root (the paper, mail/) are not part of the plan at all
    assert not any(x["from"].startswith(("paper/", "mail/")) for x in plan["excluded"])


def test_private_files_are_excluded_and_disclosed(src, tmp_path, capsys):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())
    rc, plan = plan_json(capsys, repo, sha, man)
    assert rc == 0
    assert [(x["from"], x["class"]) for x in plan["excluded"]] == [
        ("lean/.lean-receipts/0123-job.json", "receipts"),
        ("lean/notes/todo.md", "private-marker"),
        ("lean/reviews/round1.md", "review-material"),
        ("lean/session.transcript.txt", "review-material"),
    ]
    rc, text = run(capsys, repo, sha, man)
    assert "excluded (PRIVATE: keep this list out of anything published):" in text
    assert "  lean/notes/todo.md  private-marker: internal-dir notes/" in text


def test_allowlisted_file_is_included_with_its_reason_shown(src, tmp_path, capsys):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())
    rc, plan = plan_json(capsys, repo, sha, man)
    assert plan["allowed_private"] == [{
        "to": "docs/overview.tex", "from": "lean/docs/overview.tex", "denied_class": "manuscript",
        "glob": "lean/docs/overview.tex", "reason": "the statement overview the README links"}]
    rc, text = run(capsys, repo, sha, man)
    assert ("  docs/overview.tex <- lean/docs/overview.tex  manuscript  allow_private 'lean/docs/overview.tex': "
            "the statement overview the README links") in text
    # without the allowance the .tex file is excluded as manuscript material
    m = manifest()
    del m["allow_private"]
    rc, plan = plan_json(capsys, repo, sha, write_manifest(tmp_path, m, "m2.json"))
    assert rc == 0
    assert ("lean/docs/overview.tex", "manuscript") in [(x["from"], x["class"]) for x in plan["excluded"]]


def test_repeat_plan_is_byte_identical(src, tmp_path, capsys):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())
    outs = [run(capsys, repo, sha, man, *flags)[1] for flags in ((), (), ("--json",), ("--json",))]
    assert outs[0] == outs[1] and outs[2] == outs[3]
    assert str(tmp_path) not in outs[0] + outs[2]       # no host paths
    assert outs[0].splitlines()[0] == outs[0].splitlines()[-1]   # verdict first and last
    assert outs[0].startswith("plan: OK: 11 file(s) included")


def test_report_is_the_json_plan_and_lands_outside_src(src, tmp_path, capsys):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())
    report = tmp_path / "out" / "plan.json"
    report.parent.mkdir()
    rc, out = run(capsys, repo, sha, man, "--json", "--report", str(report))
    assert rc == 0 and report.read_text() == out


def test_report_inside_src_is_refused_and_not_written(src, tmp_path, capsys):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())
    for target in (repo / "lean" / "plan.json", repo / "plan.json", repo / ".git" / "plan.json"):
        rc, plan = plan_json(capsys, repo, sha, man, "--report", str(target))
        assert rc == 1 and any("--report" in r and "inside the source repository" in r for r in plan["refusals"])
        assert not target.exists()


# --------------------------------------------------------------------------------------------------
# the source is read, never written; no registry

def file_stats(repo: pathlib.Path) -> dict:
    """(mtime_ns, size) of every path under repo, .git included; no git command runs."""
    out = {}
    for d, dirs, names in os.walk(repo):
        for n in names + dirs:
            p = pathlib.Path(d) / n
            st = p.lstat()
            out[str(p.relative_to(repo))] = (st.st_mtime_ns, st.st_size)
    return out


def git_state(repo: pathlib.Path) -> dict:
    return {"status": git(repo, "status", "--porcelain", "--ignored").stdout,
            "head": git(repo, "rev-parse", "HEAD").stdout,
            "index": hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()}


def test_source_workspace_is_unchanged_and_no_registry_is_opened(src, tmp_path, capsys, monkeypatch):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())

    def boom(*a, **k):
        raise AssertionError("the registry must not be opened")
    for name in ("load_registry", "registry_path", "save_registry"):
        if hasattr(cli, name):
            monkeypatch.setattr(cli, name, boom)
    reg = tmp_path / "never" / "lean-packages.yaml"
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(reg))
    # `git status` itself may refresh the index stat cache, so the git state is read before the file
    # stats and after them, never between: only lean-pkg-extract runs between the two stat walks
    git_before = git_state(repo)
    files_before = file_stats(repo)
    for flags in ((), ("--json",), ("--report", str(tmp_path / "r.json"))):
        assert run(capsys, repo, sha, man, *flags)[0] == 0
    refused(capsys, repo, sha, write_manifest(tmp_path, manifest(roots=[{"from": "../x", "to": ".",
                                                                          "kind": "data"}]), "bad.json"),
            "path traversal")
    files_after = file_stats(repo)
    assert git_state(repo) == git_before
    assert files_after == files_before
    assert "build/" in git_before["status"] and "untracked.txt" in git_before["status"]
    assert not reg.parent.exists()


# --------------------------------------------------------------------------------------------------
# refusals

def test_refuses_path_traversal_and_absolute_paths(src, tmp_path, capsys):
    repo, sha = src
    roots = manifest()["roots"]
    for bad, needle in ((dict(roots[0], **{"from": "lean/../paper"}), "path traversal"),
                        (dict(roots[0], to="../out"), "path traversal"),
                        (dict(roots[0], to="/abs"), "absolute"),
                        (dict(roots[0], **{"from": "lean/"}), "normalized")):
        m = manifest(roots=[bad, roots[1]])
        refused(capsys, repo, sha, write_manifest(tmp_path, m), needle)


def test_refuses_symlinks_that_escape_or_point_at_excluded_files(src, tmp_path, capsys):
    repo, sha = src
    man = write_manifest(tmp_path, manifest())
    cases = (("../paper/main.tex", "leaves its root"), ("/etc/passwd", "absolute symlink"),
             ("../../../x", "leaves the repository"), ("notes/todo.md", "excluded file"),
             ("Nope.lean", "does not exist"))
    for target, needle in cases:
        link = repo / "lean" / "link.lean"
        if link.is_symlink():
            link.unlink()
        os.symlink(target, link)
        refused(capsys, repo, commit_all(repo, target), man, needle)
    link.unlink()
    os.symlink("Demo/Main.lean", link)               # a relative link to an included file is fine
    rc, plan = plan_json(capsys, repo, commit_all(repo, "ok link"), man)
    assert rc == 0, plan["refusals"]
    assert {e["to"]: e["mode"] for e in plan["included"]}["link.lean"] == "120000"


def test_refuses_a_gitlink(src, tmp_path, capsys):
    repo, sha = src
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},lean/vendor")
    git(repo, "-c", "user.name=T", "-c", "user.email=t@example.org", "commit", "-q", "-m", "submodule")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    refused(capsys, repo, head, write_manifest(tmp_path, manifest()), "lean/vendor: a gitlink (submodule)")


def test_refuses_a_missing_pin(src, tmp_path, capsys):
    repo, sha = src
    m = manifest()
    m["pins"]["verification"] = ["verification/check.sh", "verification/controls.sh"]
    refused(capsys, repo, sha, write_manifest(tmp_path, m), "pins.verification verification/controls.sh: missing")
    m = manifest()
    m["pins"]["claims"] = "lean/notes/todo.md"
    refused(capsys, repo, sha, write_manifest(tmp_path, m), "not included (private-marker")


def test_refuses_an_unpinned_or_outside_dependency(src, tmp_path, capsys):
    repo, sha = src
    lm = json.loads(json.dumps(LAKE_MANIFEST))
    lm["packages"][0]["rev"] = "master"
    lm["packages"].append({"type": "path", "name": "local", "dir": "../../elsewhere", "scope": "",
                           "inherited": False, "configFile": "lakefile.toml"})
    lm["packages"].append({"type": "path", "name": "paperlib", "dir": "../paper"})
    (repo / "lean/lake-manifest.json").write_text(json.dumps(lm))
    plan = refused(capsys, repo, commit_all(repo), write_manifest(tmp_path, manifest()),
                   "git rev 'master' is not a full 40-hex SHA")
    assert any("'../../elsewhere' leaves the repository" in r for r in plan["refusals"])
    assert any("'../paper' is not inside the included roots" in r for r in plan["refusals"])


def test_refuses_an_unclassified_file(src, tmp_path, capsys):
    repo, sha = src
    (repo / "lean/blob.bin").write_bytes(b"\0\1")
    (repo / "verification/plot.png").write_bytes(b"\x89PNG")
    plan = refused(capsys, repo, commit_all(repo), write_manifest(tmp_path, manifest()),
                   "lean/blob.bin: unclassified in a lean-package root")
    assert any(r.startswith("verification/plot.png: unclassified in a verification root") for r in plan["refusals"])


def test_refuses_a_claim_whose_file_is_not_included(src, tmp_path, capsys):
    repo, sha = src
    m = manifest(exclude=[{"path": "lean/Demo/Main.lean", "reason": "not ready"}])
    plan = refused(capsys, repo, sha, write_manifest(tmp_path, m),
                   "claim demo/main-bound: file Demo/Main.lean (output Demo/Main.lean) is not included")
    assert ("lean/Demo/Main.lean", "manifest-exclude", "not ready") in [
        (x["from"], x["class"], x["reason"]) for x in plan["excluded"]]
    # and the module that imported it is refused too
    assert any("imports Demo.Main, a module of library Demo whose file is not included" in r
               for r in plan["refusals"])


def test_refuses_an_invalid_claims_file(src, tmp_path, capsys):
    repo, sha = src
    (repo / "lean/claims.yaml").write_text(CLAIMS.replace("kind: lemma", "kind: lemmata"))
    refused(capsys, repo, commit_all(repo), write_manifest(tmp_path, manifest()), "kind must be one of")


def test_refuses_an_unresolved_import_unless_explicitly_weakened(src, tmp_path, capsys):
    repo, sha = src
    (repo / "lean/Demo/Main.lean").write_text("import Demo.Basic\nimport Paper.Notation\n\nnamespace Demo\n"
                                              "theorem main_bound : True := trivial\nend Demo\n")
    head = commit_all(repo)
    refused(capsys, repo, head, write_manifest(tmp_path, manifest()),
            "lean/Demo/Main.lean: imports Paper.Notation, which resolves to no included module")
    m = manifest(allow_unresolved_imports=[{"import": "Paper.Notation", "reason": "generated at build time"}])
    rc, plan = plan_json(capsys, repo, head, write_manifest(tmp_path, m))
    assert rc == 0, plan["refusals"]
    assert plan["weakened"] == [{"rule": "allow_unresolved_imports", "import": "Paper.Notation",
                                 "reason": "generated at build time"}]
    m = manifest(allow_unresolved_imports=[{"import": "Never.Imported", "reason": "x"}])
    refused(capsys, repo, head, write_manifest(tmp_path, m), "never imported unresolved")


def test_refuses_overlapping_targets(src, tmp_path, capsys):
    repo, sha = src
    roots = manifest()["roots"]
    refused(capsys, repo, sha, write_manifest(tmp_path, manifest(roots=[roots[0], dict(roots[1], to=".")])),
            "overlaps roots[0].to (same target)")
    # an outer root writing into an inner root's target directory
    (repo / "lean/verification").mkdir()
    (repo / "lean/verification/extra.md").write_text("x\n")
    refused(capsys, repo, commit_all(repo), write_manifest(tmp_path, manifest()),
            "lean/verification/extra.md maps to 'verification/extra.md', inside roots[1].to 'verification'")


def test_refuses_a_stale_allowance_a_bad_manifest_and_a_wrong_commit(src, tmp_path, capsys):
    repo, sha = src
    m = manifest(allow_private=[{"path": "lean/nothing.tex", "reason": "x"}] + manifest()["allow_private"])
    refused(capsys, repo, sha, write_manifest(tmp_path, m), "matches no default-denied file")
    refused(capsys, repo, sha, write_manifest(tmp_path, manifest(extra=1)), "unknown key(s) ['extra']")
    (tmp_path / "dup.json").write_text('{"schema": "lean-pkg-extract/1", "schema": "x"}')
    refused(capsys, repo, sha, tmp_path / "dup.json", "duplicate key 'schema'")
    refused(capsys, repo, sha, write_manifest(tmp_path, manifest(source_commit="b" * 40)), "differs from --commit")
    refused(capsys, repo, "no-such-ref", write_manifest(tmp_path, manifest()), "is not a commit of the source")


def test_lakefile_lean_needs_declared_libraries(src, tmp_path, capsys):
    repo, sha = src
    (repo / "lean/lakefile.toml").unlink()
    (repo / "lean/lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage demo\nlean_lib Demo\n")
    head = commit_all(repo)
    refused(capsys, repo, head, write_manifest(tmp_path, manifest()), "must declare its libraries")
    rc, plan = plan_json(capsys, repo, head, write_manifest(tmp_path, manifest(lean_libs=[{"name": "Demo"}])))
    assert rc == 0, plan["refusals"]
    assert [m["module"] for m in plan["modules"]] == ["Demo", "Demo.Basic", "Demo.Main"]


def test_floating_toolchain_is_refused(src, tmp_path, capsys):
    repo, sha = src
    (repo / "lean/lean-toolchain").write_text("leanprover/lean4:stable\n")
    refused(capsys, repo, commit_all(repo), write_manifest(tmp_path, manifest()), "is not a pinned toolchain")


# --------------------------------------------------------------------------------------------------
# usage and packaging

def test_usage_errors_exit_2(tmp_path, capsys):
    man = write_manifest(tmp_path, manifest())
    assert ex.main(["plan", "--repo", str(tmp_path), "--commit", "HEAD", "--manifest", str(man)]) == 2
    assert "not a git repository" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        ex.main(["plan", "--repo", str(tmp_path)])
    assert e.value.code == 2


def test_subdirectory_repo_is_a_usage_error(src, tmp_path, capsys):
    repo, sha = src
    assert ex.main(["plan", "--repo", str(repo / "lean"), "--commit", sha, "--manifest",
                    str(write_manifest(tmp_path, manifest()))]) == 2
    assert "pass its top directory" in capsys.readouterr().err


def test_help_is_short_with_examples(capsys):
    with pytest.raises(SystemExit):
        ex.main(["plan", "--help"])
    out = capsys.readouterr().out
    assert "examples:" in out and "lean-pkg-extract plan --repo . --commit HEAD --manifest extract.json" in out
    assert "exit codes: 0 plan clean, 1 refused" in out


def test_console_script_is_declared():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["lean-pkg-extract"] == "lean_pkg.extract:main"
