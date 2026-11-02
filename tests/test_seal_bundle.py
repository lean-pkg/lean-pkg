#!/usr/bin/env python3
"""`lean-pkg seal-bundle` (#414): a seal job bundle rendered from receipts, and --check of one."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile

import pytest
import yaml

from lean_pkg import gate as gate_mod

from .conftest import TOOLCHAIN, _git, repo_head

MATHLIB = "1" * 40
DEP = "2" * 40
LOG = "build ...\nPASS: {n} exact theorem names; approved axioms only\n"
TEMPLATE = """#!/usr/bin/env bash
# seal of @@COMMIT_SHORT@@ (tree @@TREE_SHORT@@; @@NOTE@@; contrib @@COMMIT_SHORT:contrib@@), main @@NMAIN@@
readonly EXPECTED_COMMIT=@@COMMIT@@
readonly EXPECTED_TREE=@@TREE@@
readonly CONTRIB_COMMIT=@@COMMIT:contrib@@
readonly MATHLIB_REV=@@MATHLIB_REV@@
readonly DEP_REV=@@DEP_REV@@
readonly NMAIN=@@NMAIN@@
readonly NCONTRIB=@@NCONTRIB@@
printf '%s  %s\\n' @@SHA256:source.tgz@@ "$B/source.tgz" | sha256sum -c -
printf '%s  %s\\n' @@SHA256:main-modules.txt@@ "$B/main-modules.txt" | sha256sum -c -
printf '%s  %s\\n' @@SHA256:helper.py@@ "$B/helper.py" | sha256sum -c -
"""


def _manifest():
    return json.dumps({"version": "1.1.0", "packages": [
        {"name": "mathlib", "type": "git", "rev": MATHLIB, "url": "https://example.invalid/mathlib"},
        {"name": "dep", "type": "git", "rev": DEP, "url": "https://example.invalid/dep"}]})


def make_pkg(tmp_path):
    """A git repository whose pkg/ holds a main package (Foo) and a contrib package (contrib/, Bar)."""
    repo = tmp_path / "repo"
    files = {
        "pkg/lean-toolchain": TOOLCHAIN + "\n", "pkg/lake-manifest.json": _manifest(),
        "pkg/Foo.lean": "import Foo.A\nimport Foo.Sub.B\n", "pkg/Foo/A.lean": "theorem a : True := trivial\n",
        "pkg/Foo/Sub/B.lean": "theorem b : True := trivial\n",
        "pkg/contrib/lean-toolchain": TOOLCHAIN + "\n", "pkg/contrib/lake-manifest.json": _manifest(),
        "pkg/contrib/Bar.lean": "import Bar.X\n", "pkg/contrib/Bar/X.lean": "theorem x : True := trivial\n",
        "README.md": "outside the package\n",
    }
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "maintenance.auto", "false")
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    commit_all(repo, "init")
    return repo


def commit_all(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


def write_receipt(pkgdir, commit, n, job="slurm-41", **over):
    # over may replace any field, the commit included (the file is still named after `commit`)
    """A lower-tier receipt under pkgdir/.lean-receipts/ with its sealed log ('PASS: n exact theorem names')."""
    log = LOG.format(n=n).encode()
    over = {("commit" if k == "commit_" else k): v for k, v in over.items()}
    rec = {"schema": "lower-receipt/1", "commit": commit, "lean_toolchain": TOOLCHAIN, "mathlib_rev": MATHLIB,
           "sorry_count": 0, "axioms": ["propext"], "report_sha256": hashlib.sha256(log).hexdigest(),
           "job_id": job, "checker": "lake build + audit", "generated_at": "2026-10-30T12:00:00Z", **over}
    d = pkgdir / ".lean-receipts"
    d.mkdir(exist_ok=True)
    (d / gate_mod.report_log_name(rec)).write_bytes(log)
    p = d / f"{commit[:12]}-{job}.json"
    p.write_text(json.dumps(rec))
    return p


def make_spec(tmp_path, template=TEMPLATE, **over):
    d = tmp_path / "seal"
    d.mkdir(exist_ok=True)
    (d / "gate.sbatch.in").write_text(template)
    (d / "helper.py").write_text("print('replay check')\n")
    spec = {"schema": "seal-bundle/1", "template": "gate.sbatch.in",
            "packages": [{"key": "main", "dir": ".", "umbrella": "Foo.lean", "list": "main-modules.txt"},
                         {"key": "contrib", "dir": "contrib", "umbrella": "Bar.lean", "list": "contrib-modules.txt"}],
            "all_list": "modules-all.txt",
            "counts": [{"var": "NMAIN", "package": "main", "pattern": r"PASS: (\d+) exact theorem names"},
                       {"var": "NCONTRIB", "package": "contrib", "pattern": r"PASS: (\d+) exact theorem names"}],
            "deps": {"MATHLIB_REV": "mathlib", "DEP_REV": "dep"},
            "files": ["helper.py"], **over}
    (d / "seal-bundle.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return d / "seal-bundle.yaml"


@pytest.fixture
def env(tmp_path):
    repo = make_pkg(tmp_path)
    pkg = repo / "pkg"
    c = repo_head(repo)
    main_r = write_receipt(pkg, c, 3)
    contrib_r = write_receipt(pkg / "contrib", c, 1)
    pilot = tmp_path / "pilot.txt"
    pilot.write_text("Foo.Sub.B\n")
    return {"tmp": tmp_path, "repo": repo, "pkg": pkg, "commit": c, "main": main_r, "contrib": contrib_r,
            "pilot": pilot, "spec": make_spec(tmp_path)}


def run(lp, env, *extra, receipts=None, pilot=True):
    argv = ["seal-bundle", "--spec", str(env["spec"]), "--repo", str(env["pkg"])]
    for key, path in (receipts or {"main": env["main"], "contrib": env["contrib"]}).items():
        argv += ["--receipt", f"{key}={path}"]
    if pilot:
        argv += ["--pilot", str(env["pilot"])]
    return lp.main([*argv, *extra])


def written(lp, env, capsys, name="out", *extra):
    out = env["tmp"] / name
    assert run(lp, env, "--out", str(out), "--json", *extra) == 0
    return out, json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------
# what is written

def test_write_derives_every_value(lp, env, capsys):
    out, p = written(lp, env, capsys)
    c = env["commit"]
    tree = _git(env["pkg"], "rev-parse", f"{c}:./").stdout.strip()
    script = (out / "gate.sbatch").read_text()
    assert f"readonly EXPECTED_COMMIT={c}\n" in script and f"readonly EXPECTED_TREE={tree}\n" in script
    assert f"readonly MATHLIB_REV={MATHLIB}\n" in script and f"readonly DEP_REV={DEP}\n" in script
    assert "readonly NMAIN=3\n" in script and "readonly NCONTRIB=1\n" in script
    assert f"# seal of {c[:7]} (tree {tree[:8]}; ; contrib {c[:7]}), main 3\n" in script
    assert (out / "main-modules.txt").read_text() == "Foo.A\nFoo.Sub.B\n"
    assert (out / "contrib-modules.txt").read_text() == "Bar.X\n"
    assert (out / "modules-all.txt").read_text() == "Foo.A\nFoo.Sub.B\nBar.X\n"
    assert (out / "modules-pilot.txt").read_text() == "Foo.Sub.B\n"
    sums = (out / "INPUT-SHA256SUMS").read_text().splitlines()
    names = [ln.split("  ", 1)[1] for ln in sums]
    assert names == ["source.tgz", "main-modules.txt", "contrib-modules.txt", "modules-all.txt",
                     "modules-pilot.txt", "helper.py", "gate.sbatch"]
    for ln in sums:   # every line verifies, and the script carries the same inline hashes
        h, n = ln.split("  ", 1)
        assert hashlib.sha256((out / n).read_bytes()).hexdigest() == h
    for n in ("source.tgz", "main-modules.txt", "helper.py"):
        assert f"printf '%s  %s\\n' {hashlib.sha256((out / n).read_bytes()).hexdigest()} " in script
    assert p["ok"] and p["mode"] == "write" and p["commit"] == c and p["tree"] == tree
    assert p["counts"] == {"NMAIN": 3, "NCONTRIB": 1} and p["deps"] == {"MATHLIB_REV": MATHLIB, "DEP_REV": DEP}
    assert [q["modules"] for q in p["packages"]] == [2, 1] and p["pilot"] == ["Foo.Sub.B"]


def test_source_archive_is_the_package_tree_and_repeats(lp, env, capsys):
    a, _ = written(lp, env, capsys, "a")
    b, _ = written(lp, env, capsys, "b")
    assert (a / "source.tgz").read_bytes() == (b / "source.tgz").read_bytes()
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress((a / "source.tgz").read_bytes()))) as t:
        names = {m.name for m in t.getmembers() if m.isfile()}
        mtimes = {m.mtime for m in t.getmembers()}
    assert "Foo/Sub/B.lean" in names and "contrib/Bar/X.lean" in names and "README.md" not in names
    ct = int(_git(env["pkg"], "log", "-1", "--format=%ct").stdout)
    assert mtimes == {ct}


def test_note_is_filled(lp, env, capsys):
    out, _ = written(lp, env, capsys, "out", "--note", "two-point bound")
    assert "; two-point bound; contrib" in (out / "gate.sbatch").read_text()


@pytest.mark.parametrize("note", ["two\nlines", "x" * 201, "has @@COMMIT@@"])
def test_note_must_be_one_short_line(lp, env, note):
    assert run(lp, env, "--out", str(env["tmp"] / "o"), "--note", note) == 2
    assert not (env["tmp"] / "o").exists()


def test_out_must_not_exist(lp, env, capsys):
    out = env["tmp"] / "out"
    out.mkdir()
    assert run(lp, env, "--out", str(out)) == 1
    assert "already exists" in capsys.readouterr().err


# --------------------------------------------------------------------------
# --check

def test_check_passes_on_its_own_bundle(lp, env, capsys):
    out, _ = written(lp, env, capsys)
    assert run(lp, env, "--check", str(out), pilot=False) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0].startswith(f"seal-bundle --check {out}: PASS, 7/7 file(s) equal")
    assert "modules-pilot.txt: taken from the checked bundle" in text


def test_check_names_each_difference(lp, env, capsys):
    out, _ = written(lp, env, capsys)
    (out / "main-modules.txt").write_text("Foo.A\n")
    s = (out / "gate.sbatch").read_text()
    (out / "gate.sbatch").write_text(s.replace("readonly NMAIN=3", "readonly NMAIN=4"))
    (out / "contrib-modules.txt").unlink()
    (out / "gate.r1.sbatch").write_text("old\n")
    assert run(lp, env, "--check", str(out), "--json", pilot=False) == 1
    p = json.loads(capsys.readouterr().out)
    assert not p["ok"]
    kinds = {d["file"]: d["kind"] for d in p["differences"]}
    assert kinds == {"main-modules.txt": "differs", "contrib-modules.txt": "missing", "gate.sbatch": "differs"}
    diff = next(d for d in p["differences"] if d["file"] == "gate.sbatch")["diff"]
    assert "-readonly NMAIN=4" in diff and "+readonly NMAIN=3" in diff
    assert any("not produced by seal-bundle (not compared): gate.r1.sbatch" in n for n in p["notes"])


def test_check_text_repeats_the_verdict_last_on_failure(lp, env, capsys):
    out, _ = written(lp, env, capsys)
    (out / "main-modules.txt").write_text("Foo.A\n")
    assert run(lp, env, "--check", str(out), pilot=False) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"seal-bundle --check {out}: FAIL, 6/7") and lines[-1] == lines[0]


def test_check_takes_an_archive_hash_made_elsewhere(lp, env, capsys):
    """A hand-made source.tgz (a bare tree archive, stamped with the time it was made) never repeats: its
    recorded hash is used for the script and the sums, and the archive itself is not compared."""
    out, _ = written(lp, env, capsys)
    other = "f" * 64
    sums = (out / "INPUT-SHA256SUMS").read_text()
    real = hashlib.sha256((out / "source.tgz").read_bytes()).hexdigest()
    (out / "gate.sbatch").write_text((out / "gate.sbatch").read_text().replace(real, other))
    (out / "source.tgz").unlink()
    gate_sha = hashlib.sha256((out / "gate.sbatch").read_bytes()).hexdigest()
    lines = [ln for ln in sums.splitlines()]
    lines[0] = f"{other}  source.tgz"
    lines[-1] = f"{gate_sha}  gate.sbatch"
    (out / "INPUT-SHA256SUMS").write_text("\n".join(lines) + "\n")
    assert run(lp, env, "--check", str(out), "--json", pilot=False) == 0
    notes = json.loads(capsys.readouterr().out)["notes"]
    assert any("source.tgz: its hash is taken from the checked bundle" in n for n in notes)
    assert any("source.tgz: not in the checked bundle" in n for n in notes)


def test_check_dir_must_exist(lp, env, capsys):
    assert run(lp, env, "--check", str(env["tmp"] / "nope"), pilot=False) == 1
    assert "not a directory" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [[], ["--out", "a", "--check", "b"]])
def test_out_and_check_are_one_or_the_other(lp, env, extra):
    with pytest.raises(SystemExit) as e:
        run(lp, env, *extra)
    assert e.value.code == 2


# --------------------------------------------------------------------------
# the packages and their receipts

def test_other_package_may_differ_only_in_its_receipts(lp, env, capsys):
    """contrib's receipt names an older commit: its tree equals the bundled one apart from .lean-receipts/."""
    pkg = env["pkg"]
    old = env["commit"]
    contrib_r = write_receipt(pkg / "contrib", old, 1, job="slurm-7")
    new = commit_all(env["repo"], "contrib receipt")
    main_r = write_receipt(pkg, new, 3, job="slurm-8")
    out = env["tmp"] / "out"
    assert run(lp, env, "--out", str(out), "--json", receipts={"main": main_r, "contrib": contrib_r}) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["commit"] == new and p["packages"][1]["commit"] == old
    assert f"readonly CONTRIB_COMMIT={old}\n" in (out / "gate.sbatch").read_text()
    assert any("has the bundled tree of contrib/ (apart from .lean-receipts/)" in n for n in p["notes"])


def test_other_package_drift_is_refused(lp, env, capsys):
    pkg = env["pkg"]
    old = env["commit"]
    (pkg / "contrib" / "Bar" / "X.lean").write_text("theorem x : 1 = 1 := rfl\n")
    new = commit_all(env["repo"], "edit contrib")
    main_r = write_receipt(pkg, new, 3, job="slurm-9")
    assert run(lp, env, "--out", str(env["tmp"] / "o"), receipts={"main": main_r, "contrib": env["contrib"]}) == 1
    err = capsys.readouterr().err
    assert f"its receipt verified {old[:12]}" in err and "differs outside .lean-receipts/: Bar/X.lean" in err
    assert not (env["tmp"] / "o").exists()


@pytest.mark.parametrize("extra,msg", [
    ({"pkg/Foo/C.lean": "theorem c : True := trivial\n"}, "a Foo/ file Foo.lean does not import: Foo.C"),
    ({"pkg/Foo.lean": "import Foo.A\nimport Foo.Sub.B\nimport Foo.Gone\n"},
     "imported by Foo.lean but no Foo/ file: Foo.Gone"),
])
def test_umbrella_must_equal_the_files(lp, env, capsys, extra, msg):
    for rel, text in extra.items():
        (env["repo"] / rel).write_text(text)
    new = commit_all(env["repo"], "modules")
    main_r = write_receipt(env["pkg"], new, 3, job="slurm-10")
    contrib_r = write_receipt(env["pkg"] / "contrib", new, 1, job="slurm-10")
    assert run(lp, env, "--out", str(env["tmp"] / "o"), receipts={"main": main_r, "contrib": contrib_r}) == 1
    assert msg in capsys.readouterr().err


@pytest.mark.parametrize("over,msg", [
    ({"sorry_count": 1}, "sorry_count must be 0"),
    ({"lean_toolchain": "leanprover/lean4:v4.0.0"}, "toolchain mismatch"),
    ({"mathlib_rev": "3" * 40}, "is not the mathlib committed at"),
    ({"axioms": ["propext", "sorryAx"]}, "axioms beyond"),
    ({"commit_": "abc"}, "not a full lowercase 40-hex sha"),
    ({"commit_": "9" * 40}, "does not exist in"),
    ({"schema": "lower-receipt/9"}, "upgrade lean-pkg"),
])
def test_receipt_checks_at_its_own_commit(lp, env, capsys, over, msg):
    bad = write_receipt(env["pkg"], env["commit"], 3, job="slurm-11", **over)
    assert run(lp, env, "--out", str(env["tmp"] / "o"), receipts={"main": bad, "contrib": env["contrib"]}) == 1
    err = capsys.readouterr().err
    assert f"--receipt main={bad}" in err and msg in err


def test_receipt_need_not_bind_head(lp, env, capsys):
    """A seal re-checks a verified commit after the fact: HEAD may have moved on."""
    (env["repo"] / "README.md").write_text("later\n")
    (env["pkg"] / "Foo" / "A.lean").write_text("theorem a : 2 = 2 := rfl\n")
    commit_all(env["repo"], "later work")
    out, p = written(lp, env, capsys)
    assert p["commit"] == env["commit"]


@pytest.mark.parametrize("args", [[], ["--receipt", "main=x"], ["--receipt", "nope=x", "--receipt", "main=x"],
                                  ["--receipt", "main"], ["--receipt", "main=a", "--receipt", "main=b"]])
def test_receipt_arguments(lp, env, args):
    argv = ["seal-bundle", "--spec", str(env["spec"]), "--repo", str(env["pkg"]), "--pilot", str(env["pilot"]),
            "--out", str(env["tmp"] / "o"), *args]
    assert lp.main(argv) == 2


# --------------------------------------------------------------------------
# counts, deps, pilot

def test_count_needs_exactly_one_value(lp, env, capsys):
    two = write_receipt(env["pkg"], env["commit"], 3, job="slurm-12")
    rec = json.loads(two.read_text())
    log = (LOG.format(n=3) + LOG.format(n=4)).encode()
    (two.parent / gate_mod.report_log_name(rec)).write_bytes(log)
    rec["report_sha256"] = hashlib.sha256(log).hexdigest()
    two.write_text(json.dumps(rec))
    assert run(lp, env, "--out", str(env["tmp"] / "o"), receipts={"main": two, "contrib": env["contrib"]}) == 1
    assert "it gives 3, 4" in capsys.readouterr().err


def test_count_log_must_hash_to_the_receipt(lp, env, capsys):
    rec = json.loads(env["main"].read_text())
    (env["main"].parent / gate_mod.report_log_name(rec)).write_text(LOG.format(n=99))
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert "no verify log of the main receipt hashes to its report_sha256" in capsys.readouterr().err


def test_count_pattern_without_a_match(lp, env, capsys):
    env["spec"] = make_spec(env["tmp"], counts=[{"var": "NMAIN", "package": "main", "pattern": r"proved (\d+)"}])
    env["spec"].parent.joinpath("gate.sbatch.in").write_text(TEMPLATE.replace("@@NCONTRIB@@", "0"))
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert "it gives no match" in capsys.readouterr().err


def test_dep_missing_from_the_manifest(lp, env, capsys):
    env["spec"] = make_spec(env["tmp"], deps={"MATHLIB_REV": "mathlib", "DEP_REV": "absent"})
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert "has no 40-hex rev for DEP_REV (absent)" in capsys.readouterr().err


def test_pilot_is_required_to_write(lp, env, capsys):
    assert run(lp, env, "--out", str(env["tmp"] / "o"), pilot=False) == 2
    assert "--pilot FILE is required" in capsys.readouterr().err


@pytest.mark.parametrize("text,msg", [("Foo.Nope\n", "not a module of the bundle: Foo.Nope"),
                                      ("Foo.A\nFoo.A\n", "listed twice: Foo.A"), ("\n\n", "no module listed")])
def test_pilot_list_is_checked(lp, env, capsys, text, msg):
    env["pilot"].write_text(text)
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert msg in capsys.readouterr().err


# --------------------------------------------------------------------------
# the template and the spec

@pytest.mark.parametrize("template,msg", [
    (TEMPLATE + "@@UNKNOWN@@\n", "@@UNKNOWN@@"),
    (TEMPLATE + "@@SHA256:gate.sbatch@@\n", "@@SHA256:gate.sbatch@@"),
    (TEMPLATE + "@@COMMIT:nope@@\n", "@@COMMIT:nope@@"),
    (TEMPLATE.replace("@@TREE@@", "x"), "@@TREE@@ is missing"),
    (TEMPLATE.replace("@@COMMIT@@", "x"), "@@COMMIT@@ is missing"),
])
def test_template_placeholders_are_checked(lp, env, capsys, template, msg):
    env["spec"] = make_spec(env["tmp"], template=template)
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert msg in capsys.readouterr().err
    assert not (env["tmp"] / "o").exists()


@pytest.mark.parametrize("over,msg", [
    ({"extra": 1}, "unknown key 'extra'"),
    ({"schema": "seal-bundle/2"}, "schema must be 'seal-bundle/1'"),
    ({"packages": []}, "packages must be a nonempty list"),
    ({"all_list": "main-modules.txt"}, "two bundle files would have the same name: main-modules.txt"),
    ({"script": "../x"}, "script must be a plain file name"),
    ({"files": ["/etc/passwd"]}, "must stay inside its directory"),
    ({"counts": [{"var": "NMAIN", "package": "main", "pattern": "no group"}]}, "exactly one group"),
    ({"counts": [{"var": "NMAIN", "package": "other", "pattern": "(x)"}]}, "is not a package key"),
    ({"deps": {"COMMIT": "mathlib"}}, "used twice or reserved: COMMIT"),
    ({"deps": {"lower": "mathlib"}}, "upper-case variable name"),
])
def test_spec_problems_are_refused(lp, env, capsys, over, msg):
    env["spec"] = make_spec(env["tmp"], **over)
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert msg in capsys.readouterr().err


def test_spec_package_keys(lp, env, capsys):
    spec = yaml.safe_load(env["spec"].read_text())
    spec["packages"][1]["umbrella"] = "sub/Bar.lean"
    env["spec"].write_text(yaml.safe_dump(spec))
    assert run(lp, env, "--out", str(env["tmp"] / "o")) == 1
    assert "umbrella must be a .lean file in the package directory" in capsys.readouterr().err


def test_writes_nothing_but_out(lp, env, capsys):
    before = _git(env["repo"], "status", "--porcelain", "--untracked-files=all").stdout
    written(lp, env, capsys)
    assert _git(env["repo"], "status", "--porcelain", "--untracked-files=all").stdout == before
