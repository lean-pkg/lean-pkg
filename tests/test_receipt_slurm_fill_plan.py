#!/usr/bin/env python3
"""`receipt slurm --preflight-deps --fill-plan DONOR`: steps 1-4 of the donor-fill recipe (docs/receipts.md),
read-only. A synthetic base cache (a tarball or a directory) misses modules of the audit's closure; a donor
directory holds them, built from the same pins: real git checkouts at the manifest's revs, fake .olean headers
that match or differ. The plan lists every artifact family per module, refuses each failed check with its own
code, and writes nothing except the --plan-out files; the base and the donor are hashed before and after."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from .conftest import _git, make_repo

LIB = ".lake/build/lib/lean/"
IR = ".lake/build/ir/"
HEADER = b"olean\x02" + b"4.35.0".ljust(34, b"\0") + b"0123456789abcdef0123456789abcdef01234567"
assert len(HEADER) == 80
OTHER_HEADER = b"olean\x02" + b"4.34.0".ljust(34, b"\0") + b"fedcba9876543210fedcba9876543210fedcba98"
TOOLCHAIN = b"leanprover/lean4:v4.35.0\n"

SOURCES = {
    "mathlib": {"Mathlib.lean": "import Mathlib.Data.Nat\nimport Mathlib.Logic\n",
                "Mathlib/Data/Nat.lean": "import Init\n",
                "Mathlib/Logic.lean": "import Batteries.Logic\n"},
    "batteries": {"Batteries/Logic.lean": "prelude\nimport Init.Core\n"},
}
MODULES = {"mathlib": ["Mathlib", "Mathlib.Data.Nat", "Mathlib.Logic"], "batteries": ["Batteries.Logic"]}
# The Lean 4.35 families Lake writes for one module (13 files), relative to the package's .lake/build.
FAMILIES = [LIB + "{p}.olean", LIB + "{p}.olean.hash", LIB + "{p}.olean.private", LIB + "{p}.olean.private.hash",
            LIB + "{p}.olean.server", LIB + "{p}.olean.server.hash", LIB + "{p}.ilean", LIB + "{p}.ilean.hash",
            LIB + "{p}.ir", LIB + "{p}.ir.hash", LIB + "{p}.trace", IR + "{p}.c", IR + "{p}.c.hash"]


def body(path: str) -> bytes:
    return (HEADER + path.encode()) if path.endswith(".olean") else path.encode()


def module_files(pkg: str, module: str) -> dict[str, bytes]:
    p = module.replace(".", "/")
    return {f".lake/packages/{pkg}/{f.format(p=p)}": body(f.format(p=p)) for f in FAMILIES}


def make_dep_repos(root: Path) -> dict[str, str]:
    """The donor's dependency checkouts: real git repositories with committed sources; returns their HEADs."""
    heads = {}
    for pkg, srcs in SOURCES.items():
        d = root / ".lake" / "packages" / pkg
        d.mkdir(parents=True)
        for rel, text in srcs.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        _git(d, "init", "-q")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "src")
        heads[pkg] = _git(d, "rev-parse", "HEAD").stdout.strip()
    return heads


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def base_files(heads: dict[str, str], drop_modules=()) -> dict[str, bytes]:
    """A base cache with sources, every module's families, pins as bare .git/HEAD files, less drop_modules."""
    files: dict[str, bytes] = {}
    for pkg, srcs in SOURCES.items():
        for rel, text in srcs.items():
            files[f".lake/packages/{pkg}/{rel}"] = text.encode()
        files[f".lake/packages/{pkg}/.git/HEAD"] = (heads[pkg] + "\n").encode()
        for m in MODULES[pkg]:
            if m not in drop_modules:
                files.update(module_files(pkg, m))
    return files


def write_tar(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for rel, data in sorted(files.items()):
            info = tarfile.TarInfo("./" + rel)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def tree_hash(root: Path) -> str:
    """Every path under root, its type, mode, mtime and content, hashed; .git internals included."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            p = Path(dirpath) / name
            st = p.lstat()
            h.update(f"{p.relative_to(root)}\0{st.st_mode}\0{st.st_mtime_ns}\0".encode())
            if p.is_file() and not p.is_symlink():
                h.update(p.read_bytes())
    return h.hexdigest()


class World:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.donor = tmp_path / "donor"
        self.heads = make_dep_repos(self.donor)
        (self.donor / "lean-toolchain").write_bytes(TOOLCHAIN)
        for pkg, mods in MODULES.items():
            for m in mods:
                write_files(self.donor, module_files(pkg, m))
        manifest = json.dumps({"version": 7, "packages": [{"name": n, "type": "git", "rev": r}
                                                           for n, r in self.heads.items()]})
        self.pkg = make_repo(tmp_path / "demo", extra_files={
            "lake-manifest.json": manifest, "lean-toolchain": TOOLCHAIN.decode(),
            "lakefile.toml": 'name = "demo"\n[[lean_lib]]\nname = "Demo"\n',
            "Demo.lean": "import Demo.Main\n",
            "Demo/Main.lean": "import Mathlib.Data.Nat\n",
            "Audit/R1/Solution.lean": "import Mathlib\nimport Demo.Main\n"})
        # The base misses Mathlib (the umbrella) and Mathlib.Logic, as in the field report.
        self.base_files = base_files(self.heads, drop_modules=("Mathlib", "Mathlib.Logic"))
        self.tar = write_tar(tmp_path / "base.tar.gz", self.base_files)

    def run(self, lp, capsys, *extra: str, json_out: bool = True, deps=None) -> tuple[int, object]:
        deps = deps or ["--deps-tar", str(self.tar)]
        argv = ["receipt", "slurm", "--preflight-deps", "--repo", str(self.pkg), *deps,
                "--fill-plan", str(self.donor), *extra] + (["--json"] if json_out else [])
        code = lp.main(argv)
        out = capsys.readouterr().out
        return code, (json.loads(out) if json_out else out)


@pytest.fixture()
def world(tmp_path) -> World:
    return World(tmp_path)


def codes(out: dict) -> list[str]:
    return sorted({r["code"] for r in out["refusals"]})


# --------------------------------------------------------------------------
# the plan

def test_successful_plan_lists_every_family_and_writes_nothing(lp, env, world, capsys):
    before = (tree_hash(world.donor), world.tar.read_bytes(), tree_hash(world.pkg))
    code, out = world.run(lp, capsys)
    assert code == 0 and out["ok"] is True, out["refusals"]
    assert out["fill_plan"] is True and out["preflight_deps"] is True
    assert out["missing_count"] == 2 and out["totals"]["missing_modules"] == 2
    mods = {e["module"]: e for e in out["plan"]}
    assert sorted(mods) == ["Mathlib", "Mathlib.Logic"]
    logic = mods["Mathlib.Logic"]
    assert logic["fillable"] is True and logic["package"] == "mathlib"
    assert logic["lacking"] == [".olean", ".ilean"]
    assert len(logic["files"]) == 13                            # all families, not a fixed pair
    paths = {f["path"] for f in logic["files"]}
    assert ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib/Logic.olean.private" in paths
    assert ".lake/packages/mathlib/.lake/build/ir/Mathlib/Logic.c.hash" in paths
    assert ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib/Logic.trace" in paths
    # Mathlib.Logic's families are not listed under the umbrella Mathlib (only <path>.<ext>)
    assert all("/Mathlib/Logic" not in f["path"] for f in mods["Mathlib"]["files"])
    one = next(f for f in logic["files"] if f["path"].endswith("Logic.olean"))
    data = (world.donor / one["path"]).read_bytes()
    assert one["size"] == len(data) and one["sha256"] == hashlib.sha256(data).hexdigest()
    assert out["totals"]["files"] == 26 and out["totals"]["fillable_modules"] == 2
    assert out["totals"]["bytes"] == sum(e["bytes"] for e in out["plan"])
    assert out["refusals"] == [] and out["problems"] == [] and out["plan_out"] is None
    assert {c["check"] for c in out["donor_checks"]} == {"lean-toolchain", "manifest revs", "source edits"}
    assert all(c["ok"] for c in out["donor_checks"])
    assert set(out["donor"]["package_heads"]) == {"mathlib", "batteries"}
    assert (tree_hash(world.donor), world.tar.read_bytes(), tree_hash(world.pkg)) == before


def test_json_shape(lp, env, world, capsys):
    code, out = world.run(lp, capsys)
    assert set(out) == {"ok", "preflight_deps", "fill_plan", "commit", "entries", "required_external_modules",
                        "missing_count", "missing", "base", "donor", "donor_checks", "plan", "not_fillable",
                        "refusals", "totals", "plan_out", "checks", "problems"}
    assert set(out["plan"][0]) == {"module", "package", "lacking", "required_by", "fillable", "files", "bytes"}
    assert set(out["plan"][0]["files"][0]) == {"path", "size", "sha256"}
    assert set(out["totals"]) == {"missing_modules", "fillable_modules", "files", "bytes"}
    assert set(out["base"]) == {"kind", "name", "files"} and out["base"]["kind"] == "tar"
    assert set(out["donor"]) == {"name", "package_heads"}
    assert set(out["donor_checks"][0]) == {"check", "ok", "detail"}


def test_text_summary(lp, env, world, capsys):
    code, out = world.run(lp, capsys, json_out=False)
    lines = out.splitlines()
    assert code == 0
    assert lines[0].startswith("receipt slurm --preflight-deps --fill-plan: ok; 2 missing module(s), 26 file(s)")
    assert lines[0].endswith("nothing written")
    assert any("+ mathlib Mathlib.Logic: 13 file(s)" in ln for ln in lines)
    assert lines[-1].startswith("next: write the new archive yourself")


def test_directory_base_is_planned_the_same_way(lp, env, world, capsys, tmp_path):
    base = tmp_path / "basedir"
    write_files(base, world.base_files)
    before = tree_hash(base)
    code, out = world.run(lp, capsys, deps=["--deps-dir", str(base)])
    assert code == 0, out["refusals"]
    assert out["base"]["kind"] == "dir" and out["totals"]["files"] == 26
    assert tree_hash(base) == before


def test_plan_out_contents(lp, env, world, capsys, tmp_path):
    out_dir = tmp_path / "plan"
    out_dir.mkdir()
    plan = out_dir / "add.list"
    before = (tree_hash(world.donor), world.tar.read_bytes())
    code, out = world.run(lp, capsys, "--plan-out", str(plan))
    assert code == 0, out["refusals"]
    prov_path = out_dir / "add.list.provenance.json"
    assert out["plan_out"] == {"additions": str(plan), "provenance": str(prov_path)}
    lines = plan.read_text().splitlines()
    assert len(lines) == 26 and lines == sorted(lines)
    assert all(ln.startswith(".lake/packages/mathlib/.lake/build/") for ln in lines)
    assert sorted(f["path"] for e in out["plan"] for f in e["files"]) == lines
    prov = json.loads(prov_path.read_text())
    assert prov["schema"] == "lean-pkg-fill-provenance/1"
    assert "never a job seal" in prov["kind"]
    assert prov["commit"] == out["commit"]
    assert prov["lean_toolchain"] == "leanprover/lean4:v4.35.0"
    assert prov["manifest_revs"] == world.heads and prov["donor"]["package_heads"] == world.heads
    assert prov["base"] == {"kind": "tar", "name": "base.tar.gz",
                            "sha256": hashlib.sha256(world.tar.read_bytes()).hexdigest()}
    assert prov["donor"]["name"] == "donor"
    assert prov["donor"]["olean_header_sha256"] == hashlib.sha256(HEADER).hexdigest()
    assert prov["created_utc"].endswith("Z") and prov["filled_archive"] is None
    assert {a["path"]: a["sha256"] for a in prov["additions"]} == \
        {f["path"]: f["sha256"] for e in out["plan"] for f in e["files"]}
    assert (tree_hash(world.donor), world.tar.read_bytes()) == before
    # never overwritten
    code, out = world.run(lp, capsys, "--plan-out", str(plan))
    assert code == 1 and codes(out) == ["plan-out"]


def test_plan_out_is_not_written_when_refused(lp, env, world, capsys, tmp_path):
    (world.donor / "lean-toolchain").write_bytes(b"leanprover/lean4:v4.34.0\n")
    plan = tmp_path / "add.list"
    code, out = world.run(lp, capsys, "--plan-out", str(plan))
    assert code == 1 and out["plan_out"] is None
    assert not plan.exists() and not (tmp_path / "add.list.provenance.json").exists()


def test_plan_out_inside_the_donor_is_refused(lp, env, world, capsys):
    code, out = world.run(lp, capsys, "--plan-out", str(world.donor / "add.list"))
    assert code == 1 and codes(out) == ["plan-out"]
    assert not (world.donor / "add.list").exists()


def test_complete_base_needs_no_fill(lp, env, world, capsys, tmp_path):
    tar = write_tar(tmp_path / "full.tar.gz", base_files(world.heads))
    code, out = world.run(lp, capsys, deps=["--deps-tar", str(tar)])
    assert code == 0 and out["plan"] == [] and out["totals"]["files"] == 0


# --------------------------------------------------------------------------
# the donor checks, each with its own code

def test_toolchain_mismatch_is_refused(lp, env, world, capsys):
    (world.donor / "lean-toolchain").write_bytes(TOOLCHAIN.rstrip(b"\n"))    # one byte short
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-toolchain"]
    assert any("byte for byte" in r["reason"] for r in out["refusals"])
    (world.donor / "lean-toolchain").unlink()
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-toolchain"]


def test_manifest_rev_mismatch_is_refused(lp, env, world, capsys):
    d = world.donor / ".lake/packages/batteries"
    (d / "NEW.txt").write_text("x")
    _git(d, "add", "NEW.txt")
    _git(d, "commit", "-qm", "another rev")
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-manifest"]
    assert any("manifest package batteries: the cache is at" in r["reason"] for r in out["refusals"])


def test_source_edit_in_the_donor_is_refused_and_the_index_is_untouched(lp, env, world, capsys):
    d = world.donor / ".lake/packages/mathlib"
    (d / "Mathlib/Logic.lean").write_text("import Batteries.Logic\n-- edited\n")
    index_before = (d / ".git/index").read_bytes()
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-source-edits"]
    assert any("mathlib: 1 tracked file(s) differ" in r["reason"] and "Mathlib/Logic.lean" in r["reason"]
               for r in out["refusals"])
    assert (d / ".git/index").read_bytes() == index_before


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_edit_hidden_by_an_index_flag_is_refused(lp, env, world, capsys, flag):
    """git status skips an assume-unchanged or skip-worktree entry, so its edit would pass unseen (#284)."""
    d = world.donor / ".lake/packages/mathlib"
    _git(d, "update-index", flag, "Mathlib/Logic.lean")
    (d / "Mathlib/Logic.lean").write_text("import Batteries.Logic\n-- edited\n")
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-source-edits"]
    assert any("mathlib: 1 tracked file(s) hidden from git status" in r["reason"]
               and "Mathlib/Logic.lean" in r["reason"] for r in out["refusals"])


@pytest.mark.parametrize("ignored", [False, True])
def test_untracked_or_ignored_lean_source_is_refused(lp, env, world, capsys, ignored):
    """--untracked-files=no never shows a Lean source outside the commit, ignored or not (#284)."""
    d = world.donor / ".lake/packages/batteries"
    if ignored:
        (d / ".git/info/exclude").write_text("Extra.lean\n")
    (d / "Extra.lean").write_text("def extra := 0\n")
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-source-edits"]
    assert any("batteries: 1 untracked or ignored Lean source" in r["reason"] and "Extra.lean" in r["reason"]
               for r in out["refusals"])


def test_untracked_files_under_dot_lake_are_not_sources(lp, env, world, capsys):
    """.lake/ is the generated area: a nested build or package there is not a source edit."""
    d = world.donor / ".lake/packages/batteries"
    (d / ".lake/build/ir").mkdir(parents=True, exist_ok=True)
    (d / ".lake/build/ir/Gen.lean").write_text("-- generated\n")
    code, out = world.run(lp, capsys)
    assert code == 0 and out["ok"]


def test_donor_without_git_cannot_rule_out_edits(lp, env, world, capsys, tmp_path):
    """A stripped donor (only .git/HEAD) passes the pin check but not the source-edit check."""
    d = world.donor / ".lake/packages/batteries"
    subprocess.run(["rm", "-rf", str(d / ".git")], check=True)
    (d / ".git").mkdir()
    (d / ".git/HEAD").write_text(world.heads["batteries"] + "\n")
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-source-edits"]
    assert any("batteries: git status failed" in r["reason"] for r in out["refusals"])


def test_olean_header_mismatch_is_refused(lp, env, world, capsys):
    p = world.donor / ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib/Logic.olean"
    p.write_bytes(OTHER_HEADER + b"x")
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["olean-header"]
    assert any("1 candidate .olean file(s) differ" in r["reason"] and "Mathlib/Logic.olean" in r["reason"]
               for r in out["refusals"])


def test_module_missing_in_the_donor_is_unfillable(lp, env, world, capsys):
    for f in module_files("mathlib", "Mathlib.Logic"):
        (world.donor / f).unlink()
    code, out = world.run(lp, capsys)
    assert code == 1 and codes(out) == ["donor-lacks-module"]
    mods = {e["module"]: e for e in out["plan"]}
    assert mods["Mathlib.Logic"]["fillable"] is False and mods["Mathlib.Logic"]["files"] == []
    assert mods["Mathlib"]["fillable"] is True
    assert out["totals"]["fillable_modules"] == 1


# --------------------------------------------------------------------------
# additions only

def test_overlap_with_a_base_file_is_refused(lp, env, world, capsys, tmp_path):
    """The base holds one family file of a missing module (its .trace): the donor's would replace it."""
    files = dict(world.base_files)
    files[".lake/packages/mathlib/.lake/build/lib/lean/Mathlib/Logic.trace"] = b"base trace"
    tar = write_tar(tmp_path / "b2.tar.gz", files)
    code, out = world.run(lp, capsys, deps=["--deps-tar", str(tar)])
    assert code == 1 and codes(out) == ["base-overlap"]
    assert any("1 addition(s) already in the base" in r["reason"] and "Logic.trace" in r["reason"]
               for r in out["refusals"])
    base = tmp_path / "b2dir"
    write_files(base, files)
    code, out = world.run(lp, capsys, deps=["--deps-dir", str(base)])
    assert code == 1 and codes(out) == ["base-overlap"]


def test_base_list_names_count_as_base_members(lp, env, world, capsys, tmp_path):
    listing = tmp_path / "base.list"
    listing.write_text("./.lake/packages/mathlib/.lake/build/ir/Mathlib/Logic.c\n")
    code, out = world.run(lp, capsys, "--base-list", str(listing))
    assert code == 1 and codes(out) == ["base-overlap"]


def test_addition_holding_a_root_namespace_is_refused(lp, env, world, capsys, tmp_path):
    """A dependency module in the package's own root namespace would shadow the package (deps-restore-shadow)."""
    d = world.donor / ".lake/packages/mathlib"
    (d / "Demo").mkdir()
    (d / "Demo/Shadow.lean").write_text("import Init\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "shadow")
    rev = _git(d, "rev-parse", "HEAD").stdout.strip()
    heads = {**world.heads, "mathlib": rev}
    manifest = json.dumps({"version": 7, "packages": [{"name": n, "type": "git", "rev": r}
                                                       for n, r in heads.items()]})
    (world.pkg / "lake-manifest.json").write_text(manifest)
    (world.pkg / "Audit/R1/Solution.lean").write_text("import Mathlib\nimport Demo.Main\nimport Demo.Shadow\n")
    _git(world.pkg, "commit", "-qam", "shadow import")
    files = {**base_files(heads, drop_modules=("Mathlib", "Mathlib.Logic")),
             ".lake/packages/mathlib/Demo/Shadow.lean": b"import Init\n"}
    write_files(world.donor, module_files("mathlib", "Demo.Shadow"))
    tar = write_tar(tmp_path / "b3.tar.gz", files)
    code, out = world.run(lp, capsys, deps=["--deps-tar", str(tar)])
    assert code == 1 and "package-build" in codes(out)
    assert any("root namespaces" in r["reason"] and "/lib/lean/Demo/Shadow.ilean" in r["reason"] for r in out["refusals"])


def test_other_preflight_problems_are_not_fillable(lp, env, world, capsys, tmp_path):
    files = dict(world.base_files)
    files[".lake/packages/batteries/.git/HEAD"] = ("c" * 40 + "\n").encode()
    tar = write_tar(tmp_path / "b4.tar.gz", files)
    code, out = world.run(lp, capsys, deps=["--deps-tar", str(tar)])
    assert code == 1 and codes(out) == ["base-problem"]
    assert any("manifest package batteries: the cache is at cccccccccccc" in p for p in out["not_fillable"])


def test_more_than_the_display_cap_is_planned(lp, env, world, capsys, tmp_path, monkeypatch):
    from lean_pkg import deps_closure as dc
    monkeypatch.setattr(dc, "JSON_MISSING", 1)
    code, out = world.run(lp, capsys)
    assert code == 0 and out["missing_count"] == 2
    assert len(out["missing"]) == 1 and len(out["plan"]) == 2           # the cap is for display only


# --------------------------------------------------------------------------
# usage

@pytest.mark.parametrize("argv", [
    ["--fill-plan", "d"],
    ["--plan-out", "p"],
    ["--preflight-deps", "--deps-tar", "a", "--plan-out", "p"],
    ["--preflight-deps", "--deps-tar", "a", "--base-list", "l"],
    ["--preflight-deps", "--fill-plan", "/nonexistent-donor", "--deps-tar", "a"],
])
def test_usage_errors(lp, env, world, argv, capsys):
    assert lp.main(["receipt", "slurm", "--repo", str(world.pkg), *argv]) == 2


def test_header_key_reads_both_formats():
    from lean_pkg import deps_fill as df
    v1 = b"olean\x01" + b"c" * 40 + b"\x10\x00\x00\x00\x00\x00\x00\x00" + b"payload..."
    v1_other_module = b"olean\x01" + b"c" * 40 + b"\x20\x00\x00\x00\x00\x00\x00\x00" + b"other....."
    assert df.header_key(v1) == df.header_key(v1_other_module) == v1[:46]
    assert df.header_key(HEADER + b"\x00" * 8) == HEADER
    assert df.header_key(HEADER) != df.header_key(OTHER_HEADER)
