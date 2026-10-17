#!/usr/bin/env python3
"""`receipt slurm --preflight-deps` (field report #5): a job restored a DEPS_TAR holding the dependency builds
of the library target's import cone; `lake build` passed, then the audit file, which imports the `Mathlib`
umbrella, failed: `Mathlib.olean` was not in the tarball. Before submission the preflight follows the import
closure of the verifier's entry files (committed at --commit) through the package and the dependencies'
sources in the cache, and refuses unless every external module has its .olean and .ilean there, with every
manifest package at its pinned rev. Fixture tarballs stand in for the cache; nothing is built or written."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest

from lean_pkg import deps_closure as dc
from lean_pkg import receipt

from .conftest import _git, make_repo, repo_head

MATHLIB = "a" * 40
BATTERIES = "b" * 40
MANIFEST = json.dumps({"version": 7, "packages": [
    {"name": "mathlib", "type": "git", "rev": MATHLIB},
    {"name": "batteries", "type": "git", "rev": BATTERIES}]})

PACKAGE = {
    "lake-manifest.json": MANIFEST,
    "lakefile.toml": 'name = "demo"\n[[lean_lib]]\nname = "Demo"\n',
    "Demo.lean": "import Demo.Main\n",
    "Demo/Main.lean": "import Mathlib.Data.Nat\n\ntheorem t : True := trivial\n",
    # The audit file of the report: the umbrella, plus the package's own module.
    "Audit/R1/Solution.lean": "/- audit -/\nimport Mathlib\nimport Demo.Main\n\nexample : True := trivial\n",
}

# The dependency sources inside the cache, each with its imports.
SOURCES = {
    "mathlib/Mathlib.lean": "import Mathlib.Data.Nat\nimport Mathlib.Logic\n",
    "mathlib/Mathlib/Data/Nat.lean": "import Init\n",
    "mathlib/Mathlib/Logic.lean": "-- logic\nimport Batteries.Logic\nimport Lean.Elab\n",
    "batteries/Batteries/Logic.lean": "prelude\nimport Init.Core\n",
}
MODULES = {"mathlib": ["Mathlib", "Mathlib.Data.Nat", "Mathlib.Logic"], "batteries": ["Batteries.Logic"]}
LIB = ".lake/build/lib/lean/"


def cache_files(*, drop=(), heads=None) -> dict[str, bytes]:
    """`.lake/packages` of a complete cache, less the paths in `drop` (relative to .lake/packages)."""
    files: dict[str, bytes] = {}
    for rel, text in SOURCES.items():
        files[rel] = text.encode()
    for pkg, mods in MODULES.items():
        for m in mods:
            for ext in (".olean", ".ilean"):
                files[f"{pkg}/{LIB}{m.replace('.', '/')}{ext}"] = b"\0olean"
    heads = heads or {"mathlib/.git/HEAD": (MATHLIB + "\n").encode(),
                      "batteries/.git/HEAD": b"ref: refs/heads/main\n",
                      "batteries/.git/packed-refs": f"# pack-refs\n{BATTERIES} refs/heads/main\n".encode()}
    files.update(heads)
    return {f".lake/packages/{k}": v for k, v in files.items() if k not in drop}


def write_tar(path: Path, files: dict[str, bytes], prefix: str = "./") -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo(prefix + rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def write_dir(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


@pytest.fixture()
def pkg(tmp_path) -> Path:
    return make_repo(tmp_path / "demo", extra_files=PACKAGE)


def preflight(lp, capsys, pkg: Path, *extra: str) -> tuple[int, dict]:
    code = lp.main(["receipt", "slurm", "--preflight-deps", "--repo", str(pkg), "--json", *extra])
    return code, json.loads(capsys.readouterr().out)


def tree_state(pkg: Path) -> str:
    return subprocess.run(["git", "-C", str(pkg), "status", "--porcelain", "--ignored", "--untracked-files=all"],
                          capture_output=True, text=True, check=True).stdout


# --------------------------------------------------------------------------
# the report's case and the accepted one

def test_missing_umbrella_is_refused_before_submission(lp, env, pkg, tmp_path, capsys):
    """The tarball of the report: the library cone built, Mathlib.olean/.ilean absent."""
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files(drop={f"mathlib/{LIB}Mathlib.olean",
                                                                f"mathlib/{LIB}Mathlib.ilean"}))
    before = tree_state(pkg)
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 1 and out["ok"] is False and out["preflight_deps"] is True
    assert out["entries"] == ["Audit/R1/Solution.lean"]        # the default entries found the audit file
    assert out["missing_count"] == 1
    assert out["missing"] == [{"module": "Mathlib", "package": "mathlib", "files": [".olean", ".ilean"],
                               "required_by": "Audit/R1/Solution.lean"}]
    assert any("not built in the staged cache: Mathlib (.olean/.ilean)" in p for p in out["problems"])
    assert tree_state(pkg) == before                           # nothing written
    assert not (pkg / ".lean-receipts").exists()


def test_complete_cache_is_accepted(lp, env, pkg, tmp_path, capsys):
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files())
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 0 and out["ok"] is True, out["problems"]
    assert out["required_external_modules"] == 4               # Mathlib, its two imports, Batteries.Logic
    assert out["missing"] == [] and out["problems"] == []
    assert any("mathlib at aaaaaaaaaaaa = the manifest's rev" in c for c in out["checks"])
    assert any("batteries at bbbbbbbbbbbb" in c for c in out["checks"])   # HEAD -> packed-refs


def test_complete_directory_is_accepted_and_text_is_verdict_first(lp, env, pkg, tmp_path, capsys):
    d = write_dir(tmp_path / "staged", cache_files())
    code = lp.main(["receipt", "slurm", "--preflight-deps", "--repo", str(pkg), "--deps-dir", str(d)])
    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert lines[0] == "receipt slurm --preflight-deps: ok; nothing written"


def test_library_cone_alone_does_not_need_the_umbrella(lp, env, pkg, tmp_path, capsys):
    """Why the report's job built: the library target's cone holds no `import Mathlib`. Only the audit entry
    brings the umbrella in, so the entries, not the build target, decide what the cache must hold."""
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files(drop={f"mathlib/{LIB}Mathlib.olean",
                                                                f"mathlib/{LIB}Mathlib.ilean"}))
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar), "--entries", "Demo/**/*.lean")
    assert code == 0, out["problems"]
    assert out["required_external_modules"] == 1               # Mathlib.Data.Nat
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar), "--entries", "Demo/**/*.lean",
                          "--entries", "Audit/**/*.lean")
    assert code == 1 and [m["module"] for m in out["missing"]] == ["Mathlib"]


def test_umbrella_present_but_a_transitive_import_missing_is_refused(lp, env, pkg, tmp_path, capsys):
    """Mathlib.olean alone is not enough: everything Mathlib.lean imports, transitively, across packages."""
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files(drop={f"batteries/{LIB}Batteries/Logic.ilean"}))
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 1
    assert out["missing"] == [{"module": "Batteries.Logic", "package": "batteries", "files": [".ilean"],
                               "required_by": "Mathlib.Logic (mathlib)"}]


def test_entries_and_package_modules_are_read_at_the_commit(lp, env, pkg, tmp_path, capsys):
    """The working tree is not what the job builds: an uncommitted import changes nothing, a committed one
    is followed through the package's own module."""
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files())
    (pkg / "Demo/Main.lean").write_text("import Mathlib.Unbuilt\n")
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 0, out["problems"]
    _git(pkg, "commit", "-qam", "import an unbuilt module")
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 1
    assert any("module Mathlib.Unbuilt (imported by Demo/Main.lean): provided by no manifest package" in p
               for p in out["problems"])
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar), "--commit", repo_head(pkg) + "~1")
    assert code == 0, out["problems"]


# --------------------------------------------------------------------------
# pins and the cache's shape

def test_cache_at_another_rev_is_refused(lp, env, pkg, tmp_path, capsys):
    files = cache_files()
    files[".lake/packages/mathlib/.git/HEAD"] = ("c" * 40 + "\n").encode()
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(write_tar(tmp_path / "d.tgz", files)))
    assert code == 1
    assert any("manifest package mathlib: the cache is at cccccccccccc, the manifest pins aaaaaaaaaaaa" in p
               for p in out["problems"])


def test_stripped_checkout_and_missing_package_are_refused(lp, env, pkg, tmp_path, capsys):
    files = {k: v for k, v in cache_files().items() if not k.startswith(".lake/packages/batteries/")}
    del files[".lake/packages/mathlib/.git/HEAD"]
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(write_tar(tmp_path / "d.tgz", files)))
    assert code == 1
    probs = "\n".join(out["problems"])
    assert "manifest package mathlib: no .git/HEAD in the cache" in probs
    assert "manifest package batteries: not in the staged cache" in probs
    assert "module Batteries.Logic (imported by Mathlib.Logic (mathlib)): provided by no manifest package" in probs


def test_olean_without_source_cannot_be_followed(lp, env, pkg, tmp_path, capsys):
    files = cache_files()
    del files[".lake/packages/mathlib/Mathlib.lean"]
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(write_tar(tmp_path / "d.tgz", files)))
    assert code == 1
    assert any("module Mathlib (mathlib): no source in the staged cache" in p for p in out["problems"])


def test_symlinked_olean_does_not_count(lp, env, pkg, tmp_path, capsys):
    files = cache_files(drop={f"mathlib/{LIB}Mathlib.olean"})
    tar = tmp_path / "d.tgz"
    with tarfile.open(tar, "w:gz") as tf:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f".lake/packages/mathlib/{LIB}Mathlib.olean")
        link.type, link.linkname = tarfile.SYMTYPE, "/elsewhere/Mathlib.olean"
        tf.addfile(link)
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 1 and out["missing"][0]["files"] == [".olean"]


def test_no_entry_file_and_no_packages_dir_are_refused(lp, env, pkg, tmp_path, capsys):
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files())
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar), "--entries", "Nowhere/*.lean")
    assert code == 1 and any("matches the entries (Nowhere/*.lean)" in p for p in out["problems"])
    other = write_tar(tmp_path / "other.tgz", {"build/Mathlib.olean": b"x"})
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(other))
    assert code == 1 and any("no regular file under .lake/packages/" in p for p in out["problems"])
    bad = tmp_path / "bad.tgz"
    bad.write_bytes(b"not a tarball")
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(bad))
    assert code == 1 and any("cannot read the tarball" in p for p in out["problems"])


def test_path_package_is_refused(lp, env, tmp_path, capsys):
    manifest = json.dumps({"packages": [{"name": "mathlib", "type": "git", "rev": MATHLIB},
                                        {"name": "local", "type": "path", "dir": "../local"}]})
    pkg = make_repo(tmp_path / "p", extra_files={**PACKAGE, "lake-manifest.json": manifest})
    tar = write_tar(tmp_path / "deps.tar.gz", cache_files())
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 1 and any("manifest package local: type 'path'" in p for p in out["problems"])


# --------------------------------------------------------------------------
# usage, and the job's guards are untouched

@pytest.mark.parametrize("argv", [
    ["--preflight-deps"],
    ["--preflight-deps", "--deps-tar", "a", "--deps-dir", "b"],
    ["--preflight-deps", "--check", "--deps-tar", "a"],
    ["--preflight-deps", "--template"],
    ["--check", "--log", "l", "--deps-tar", "a"],
    ["--template", "--entries", "X/*.lean"],
])
def test_usage_errors(lp, env, pkg, argv, capsys):
    assert lp.main(["receipt", "slurm", "--repo", str(pkg), *argv]) == 2


def test_the_template_guards_are_unchanged():
    """The preflight adds a check before submission; it relaxes nothing the job checks itself."""
    t = receipt.SBATCH_TEMPLATE
    for guard in ('echo "error: DEPS_TAR: the committed tree already holds .lake/packages',
                  'echo "error: DEPS_TAR: the restore changed the tree outside .lake/packages"',
                  "echo \"error: DEPS_TAR: a dependency's build directory holds the package's root namespace(s)",
                  'echo "error: not a fresh build: the committed tree holds .lake/build, or the Lake cache is not empty"',
                  'echo "error: Lake restored root-package module(s) instead of compiling them:$restored"',
                  'export LAKE_ARTIFACT_CACHE=false'):
        assert guard in t
    assert "lean-pkg receipt slurm --preflight-deps" in t


def test_header_imports():
    text = ("/- a /- nested -/ comment -/\nmodule\n-- line\nprelude\npublic import A.B\nimport all C\n"
            "public meta import «D».E\nimport F G\n\nimport H -- after a command is not a header\n")
    # `import F G` is F, then G ends the header (it is not a keyword)
    assert dc.header_imports(text) == ["A.B", "C", "D.E", "F"]
    assert dc.header_imports("import Mathlib\nimport Demo.Main\ntheorem x : True := trivial\nimport Z\n") == \
        ["Mathlib", "Demo.Main"]


def test_glob():
    r = dc.glob_regex("**/AxiomCheck*.lean")
    assert r.match("AxiomCheck.lean") and r.match("a/b/AxiomCheck2.lean") and not r.match("a/AxiomCheckx/y.lean")
    assert dc.glob_regex("Audit/**/*.lean").match("Audit/R1/Solution.lean")
    assert dc.glob_regex("Audit/**/*.lean").match("Audit/S.lean")
    assert not dc.glob_regex("Verify/*.lean").match("Verify/a/b.lean")


def test_hard_links_read_as_their_target(lp, env, pkg, tmp_path, capsys):
    """A hard-linked source keeps its target's imports (never an empty header); a dangling one is a problem."""
    files = cache_files()
    umbrella = files.pop(".lake/packages/mathlib/Mathlib.lean")
    files.pop(f".lake/packages/batteries/{LIB}Batteries/Logic.olean")
    tar = tmp_path / "d.tgz"
    with tarfile.open(tar, "w:gz") as tf:
        info = tarfile.TarInfo(".lake/packages/mathlib/Copy.lean")
        info.size = len(umbrella)
        tf.addfile(info, io.BytesIO(umbrella))
        link = tarfile.TarInfo(".lake/packages/mathlib/Mathlib.lean")
        link.type, link.linkname = tarfile.LNKTYPE, ".lake/packages/mathlib/Copy.lean"
        tf.addfile(link)
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        bad = tarfile.TarInfo(".lake/packages/mathlib/Dangling.lean")
        bad.type, bad.linkname = tarfile.LNKTYPE, "nowhere/X.lean"
        tf.addfile(bad)
    code, out = preflight(lp, capsys, pkg, "--deps-tar", str(tar))
    assert code == 1
    assert [m["module"] for m in out["missing"]] == ["Batteries.Logic"]     # followed through the link
    assert any("Dangling.lean: a hard link to a member that is not a regular file" in p for p in out["problems"])
