#!/usr/bin/env python3
"""`receipt slurm` (with and without --check) and `receipt systemd` on a package that is a subdirectory of
its git repository (a Lean package incubated in an article repository): the commit's lean-toolchain and
lake-manifest.json are read at the package's own path, the receipt passes `gate --check`, and a root package
behaves as before. A copy of the package placed at another path of the same repository (a frozen review
packet) still refuses, and the refusal names the path that was read and where the commit holds the package.
Only a per-test registry is used; sacct and systemctl are never run."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from .conftest import MATHLIB_MANIFEST, MATHLIB_REV, TOOLCHAIN, _git, base_pkg, make_repo, read_reg, repo_head, \
    write_reg
from .test_receipt_slurm import CHECKER, Job, check, no_sacct
from .test_receipt_slurm import _fixed_identity  # noqa: F401  (autouse: pinned user and host names)
from .test_receipt_systemd import SLUG, Unit
from .test_receipt_systemd import run as run_systemd


def article_repo(tmp_path: Path, sub: str) -> tuple[Path, Path]:
    """An article repository whose Lean package is `sub` ('.' = the repository root)."""
    pkg_files = {"lean-toolchain": TOOLCHAIN + "\n", "lake-manifest.json": MATHLIB_MANIFEST}
    if sub == ".":
        top = make_repo(tmp_path / "article", extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
        return top, top
    top = make_repo(tmp_path / "article", extra_files={
        "paper.tex": "\\documentclass{article}\n", **{f"{sub}/{k}": v for k, v in pkg_files.items()}})
    (top / "lean-toolchain").unlink()                  # make_repo's root file: the package is only under sub/
    _git(top, "commit", "-q", "-am", "the package lives in a subdirectory")
    return top, top / sub


def slurm(lp, pkg: Path, job: Job, commit: str):
    (job.log.parent / "sacct.txt").write_text("123|COMPLETED|0:0|00:12:05|01:10:22\n")
    return lp.main(["receipt", "slurm", "--job", "123", "--log", str(job.log), "--stdout", str(job.out),
                    "--stderr", str(job.err), "--commit", commit, "--repo", str(pkg), "--checker", CHECKER,
                    "--sacct-file", str(job.log.parent / "sacct.txt")])


def register(env, pkg: Path) -> None:
    reg = read_reg(env)
    reg["packages"]["article-pkg"] = base_pkg("article-pkg", pkg, "gate-pending")
    write_reg(env, reg)


@pytest.mark.parametrize("sub", [".", "lean-native", "lean/contrib"])
def test_receipt_slurm_reads_the_commit_at_the_package_path_and_gate_accepts_it(lp, env, tmp_path, capsys,
                                                                                 monkeypatch, sub):
    no_sacct(monkeypatch)
    top, pkg = article_repo(tmp_path, sub)
    commit = repo_head(top)
    job = Job(tmp_path)
    assert check(lp, pkg, job) == 0
    out = capsys.readouterr().out
    assert "receipt slurm --check: ok; nothing written" in out
    assert f"✓ manifest: pre = post = lake-manifest.json at {commit[:12]}" in out
    before = env.registry.read_bytes()
    assert slurm(lp, pkg, job, commit) == 0, capsys.readouterr().out
    capsys.readouterr()
    rp = pkg / ".lean-receipts" / f"{commit[:12]}-slurm-123.json"
    receipt = json.loads(rp.read_text())
    assert receipt["lean_toolchain"] == TOOLCHAIN and receipt["mathlib_rev"] == MATHLIB_REV
    assert receipt["commit"] == commit
    if sub != ".":
        assert not (top / ".lean-receipts").exists()  # evidence goes beside the package, not the article root
    assert env.registry.read_bytes() == before
    register(env, pkg)
    assert lp.main(["--registry", str(env.registry), "gate", "article-pkg", "--check", "--receipt", str(rp)]) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


@pytest.mark.parametrize("sub", [".", "lean-native"])
def test_receipt_systemd_reads_the_commit_at_the_package_path(lp, tmp_path, capsys, sub):
    top, pkg = article_repo(tmp_path, sub)
    assert run_systemd(lp, pkg, Unit(tmp_path)) == 0, capsys.readouterr().out
    receipt = json.loads((pkg / ".lean-receipts" / f"{repo_head(top)[:12]}-{SLUG}.json").read_text())
    assert receipt["lean_toolchain"] == TOOLCHAIN and receipt["mathlib_rev"] == MATHLIB_REV


def frozen_copy(top: Path, pkg: Path) -> Path:
    """An untracked copy of the package at another path of the same repository (a review packet)."""
    dst = top / "reviews" / "packet" / pkg.name
    shutil.copytree(pkg, dst)
    return dst


def test_a_copy_at_another_path_is_refused_and_named(lp, tmp_path, capsys, monkeypatch):
    # Negative control: the copy's files exist on disk but the commit holds no package at the copy's path, so
    # nothing the job sealed can be bound to it (fail closed, exactly as before); the refusal says why.
    no_sacct(monkeypatch)
    top, pkg = article_repo(tmp_path, "lean-native")
    copy = frozen_copy(top, pkg)
    job = Job(tmp_path)
    assert check(lp, copy, job, "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    text = " ".join(payload["problems"])
    assert f"no lean-toolchain committed at {repo_head(top)[:12]}" in text
    assert "--repo is `reviews/packet/lean-native/` in its git repository" in text
    assert "a lean-toolchain is committed at: lean-native/;" in text
    assert "no lake-manifest.json committed at" in text
    assert slurm(lp, copy, job, repo_head(top)) == 1
    out = "".join(capsys.readouterr())
    assert "a lean-toolchain is committed at: lean-native/;" in out
    assert not (copy / ".lean-receipts").exists()
    assert check(lp, pkg, job) == 0                   # the checkout at the committed path is accepted


def test_the_repository_root_of_a_subdirectory_package_is_refused_and_named(lp, tmp_path, capsys):
    top, _pkg = article_repo(tmp_path, "lean/contrib")
    assert check(lp, top, Job(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "--repo is `./` in its git repository" in out
    assert "a lean-toolchain is committed at: lean/contrib/;" in out


def test_a_commit_without_any_toolchain_says_so(lp, tmp_path, capsys):
    top = make_repo(tmp_path / "bare", extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
    _git(top, "rm", "-q", "lean-toolchain")
    _git(top, "commit", "-q", "-m", "no toolchain")
    assert check(lp, top, Job(tmp_path)) == 1
    assert "a lean-toolchain is committed at: no path;" in capsys.readouterr().out
