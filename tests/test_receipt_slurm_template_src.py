#!/usr/bin/env python3
"""`receipt slurm --template`: SRC_MODE=clone and DEPS_TAR (field reports). (1) The template extracts
`git archive COMMIT`, so the job's tree has no .git and a verify script binding its evidence to
`git rev-parse HEAD` failed; SRC_MODE=clone makes the work directory a new repository holding only COMMIT
(file transport, its own object store, no untracked or ignored file), refuses unless HEAD = COMMIT and
`git status` is empty, and logs `src-mode: clone` / `src-head: <sha>`, which the receipt checks against
--commit. (2) Every job fetched and built Mathlib again; DEPS_TAR restores .lake/packages from a tarball,
refusing a restore that changed anything else in the tree or that brings the package's root namespace, and
logs the tarball's sha256. The package itself still compiles fresh. The template is run with fake tools."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from .conftest import repo_head
from .test_lake_hygiene import demo_repo
from .test_receipt_slurm import (CHECKER, LOG, Job, _fixed_identity, check, fake_sacct,  # noqa: F401
                                 rdir, receipt_json, repo, template_text)


def template_run(lp, capsys, repo: Path, tmp_path: Path, *, verify: str = "", extra: dict | None = None,
                 path_prefix: Path | None = None):
    """Run the printed template with fake build and audit commands; (process, log text, Job)."""
    path = tmp_path / "verify.sbatch"
    path.write_text(template_text(lp, capsys))
    (tmp_path / "build.txt").write_text("✔ [1/1] Built Demo.Main\nBuild completed successfully\n")
    (tmp_path / "axioms.txt").write_text("'Demo.t' does not depend on any axioms\n")
    out_dir = tmp_path / "submit"
    out_dir.mkdir()
    env = {**os.environ, "SLURM_JOB_ID": "123", "SLURM_TMPDIR": str(tmp_path / "node"), "PKG_DIR": str(repo),
           "OUT_DIR": str(out_dir), "VERIFY_CMD": f"{verify}cat {tmp_path / 'build.txt'}",
           "AXIOMS_CMD": f"cat {tmp_path / 'axioms.txt'}", "CHECKER": CHECKER, "LEAN_PATH_CMD": "true",
           **(extra or {})}
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    p = subprocess.run(["bash", str(path)], capture_output=True, text=True, env=env, cwd=out_dir)
    job = Job.__new__(Job)
    job.log = out_dir / "verify-123.log"
    job.out, job.err = out_dir / "verify-123.out", out_dir / "verify-123.err"
    job.out.write_text(p.stdout)
    job.err.write_text(p.stderr)
    return p, (job.log.read_text() if job.log.exists() else ""), job


def files_args(repo: Path, job: Job) -> list[str]:
    return ["--log", str(job.log), "--stdout", str(job.out), "--stderr", str(job.err), "--repo", str(repo),
            "--commit", repo_head(repo)]


def wrapper(tmp_path: Path, name: str, real: str, after: str) -> Path:
    """A `name` on PATH that runs the real tool, then `after` (bash, with the arguments in "$@")."""
    bindir = tmp_path / "fake-tools"
    bindir.mkdir(exist_ok=True)
    w = bindir / name
    w.write_text(f'#!/usr/bin/env bash\n"{real}" "$@" || exit $?\n{after}\n')
    w.chmod(0o755)
    return bindir


def deps_tar(tmp_path: Path, members: dict[str, bytes | None], symlinks: dict[str, str] | None = None) -> Path:
    """A gzipped tarball: path -> file content (None: a directory); symlinks: path -> target."""
    out = tmp_path / "warm.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(info)
            else:
                info.size, info.mode = len(data), 0o644
                tf.addfile(info, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    return out


MATHLIB_OLEAN = ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean"


# --------------------------------------------------------------------------
# SRC_MODE

def test_archive_stays_the_default_and_has_no_git(lp, repo, tmp_path, capsys):
    p, log, _ = template_run(lp, capsys, repo, tmp_path, verify="test ! -e .git; ")
    assert p.returncode == 0, p.stderr + log
    assert "\nsrc-mode: archive\ndeps-restore: none\n" in log and "src-head:" not in log


def test_clone_mode_gives_the_verify_script_a_clean_repository_at_commit(lp, env, repo, tmp_path, capsys,
                                                                          fake_sacct):
    head = repo_head(repo)
    (repo / "untracked-note.txt").write_text("not committed\n")      # must not reach the job's tree
    (repo / ".git" / "info" / "exclude").write_text("ignored.txt\n")
    (repo / "ignored.txt").write_text("ignored\n")
    verify = ('echo "verify-head: $(git rev-parse HEAD)"; test -z "$(git status --porcelain --ignored)"; '
              'test ! -e untracked-note.txt; test ! -e ignored.txt; '
              'test ! -e "$(git rev-parse --git-common-dir)/objects/info/alternates"; ')
    p, log, job = template_run(lp, capsys, repo, tmp_path, verify=verify, extra={"SRC_MODE": "clone"})
    assert p.returncode == 0, p.stderr + log
    assert f"\nsrc-mode: clone\nsrc-head: {head}\ndeps-restore: none\n" in log
    assert f"verify-head: {head}\n" in log and f"commit: {head}\n" in log
    assert str(tmp_path) not in log and job.err.read_text() == ""
    assert repo_head(repo) == head and (repo / "untracked-note.txt").exists()   # the checkout is untouched
    assert not (tmp_path / "node" / "src").exists()                          # the clone is removed with the job
    assert lp.main(["receipt", "slurm", "--check", *files_args(repo, job)]) == 0
    assert "✗" not in capsys.readouterr().out
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job)]) == 0
    assert receipt_json(repo)["sorry_count"] == 0
    published = next(rdir(repo).glob("*.log")).read_text()
    assert f"src-head: {head}\n" in published


def test_clone_mode_takes_a_short_commit_and_logs_the_full_one(lp, repo, tmp_path, capsys):
    head = repo_head(repo)
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"SRC_MODE": "clone", "COMMIT": head[:10]})
    assert p.returncode == 0, p.stderr + log
    assert f"commit: {head}\n" in log and f"src-head: {head}\n" in log


def test_clone_mode_refuses_a_head_that_is_not_the_commit(lp, repo, tmp_path, capsys):
    """Negative control: a git that checks out the parent after the requested checkout."""
    real = shutil.which("git")
    bindir = wrapper(tmp_path, "git", real,
                     f'case " $* " in *" checkout "*) "{real}" -C "$2" -c core.hooksPath=/dev/null '
                     f'checkout -q --detach HEAD~1 ;; esac')
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"SRC_MODE": "clone"}, path_prefix=bindir)
    assert p.returncode == 1
    assert f"error: the clone's HEAD is not COMMIT {repo_head(repo)}" in log
    assert "lean-pkg-build: begin" not in log and "sorry-count:" not in log


def test_clone_mode_refuses_a_dirty_clone(lp, repo, tmp_path, capsys):
    real = shutil.which("git")
    bindir = wrapper(tmp_path, "git", real, 'case " $* " in *" checkout "*) echo x > "$2/stray.lean" ;; esac')
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"SRC_MODE": "clone"}, path_prefix=bindir)
    assert p.returncode == 1
    assert "error: the clone at COMMIT is not clean" in log and "lean-pkg-build: begin" not in log


def test_an_unknown_src_mode_fails_before_anything_runs(lp, repo, tmp_path, capsys):
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"SRC_MODE": "worktree"})
    assert p.returncode == 1 and "SRC_MODE is archive or clone" in p.stderr and log == ""


def test_the_receipt_refuses_a_src_head_that_is_not_the_commit(lp, repo, tmp_path, capsys):
    head = repo_head(repo)
    other = ("0" if head[0] != "0" else "1") + head[1:]
    job = Job(tmp_path, LOG + f"commit: {head}\nsrc-head: {other}\n")
    assert check(lp, repo, job) == 1
    assert f"`src-head:` '{other}' is not --commit {head[:12]}" in capsys.readouterr().out
    job = Job(tmp_path, LOG + f"commit: {head}\nsrc-head: {head}\n")
    assert check(lp, repo, job) == 0, capsys.readouterr().out


# --------------------------------------------------------------------------
# DEPS_TAR

def test_deps_tar_restores_only_lake_packages_and_the_package_still_builds_fresh(lp, repo, tmp_path, capsys,
                                                                                fake_sacct):
    demo_repo(repo)
    tar = deps_tar(tmp_path, {".lake/packages/mathlib/lakefile.lean": b"-- dep\n", MATHLIB_OLEAN: b"olean",
                              ".lake/build/lib/lean/Demo/Main.olean": b"stale root build",   # ignored
                              "Demo/Main.lean": b"-- not the committed source\n"})          # ignored
    digest = hashlib.sha256(tar.read_bytes()).hexdigest()
    verify = (f"test -f {MATHLIB_OLEAN}; test ! -e .lake/build; "
              "git diff --quiet HEAD -- Demo/Main.lean; ")
    p, log, job = template_run(lp, capsys, repo, tmp_path, verify=verify,
                               extra={"SRC_MODE": "clone", "DEPS_TAR": str(tar)})
    assert p.returncode == 0, p.stderr + log
    assert f"\ndeps-restore: tar {digest}\n" in log
    assert "deps-restore-check: ok (only .lake/packages changed; no root namespace in it)\n" in log
    assert str(tar) not in log and str(tmp_path) not in log
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job)]) == 0, capsys.readouterr().err
    assert f"deps-restore: tar {digest}" in next(rdir(repo).glob("*.log")).read_text()


def test_deps_tar_with_the_root_namespace_is_refused(lp, repo, tmp_path, capsys):
    demo_repo(repo)
    tar = deps_tar(tmp_path, {MATHLIB_OLEAN: b"olean",
                              ".lake/packages/evil/.lake/build/lib/lean/Demo/Main.olean": b"planted"})
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"DEPS_TAR": str(tar)})
    assert p.returncode == 1
    assert "deps-restore-shadow: .lake/packages/evil/.lake/build/lib/lean/Demo\n" in log
    assert "a dependency's build directory holds the package's root namespace(s): Demo" in log
    assert "lean-pkg-build: begin" not in log


def test_a_restore_that_touches_the_tree_outside_lake_packages_is_refused(lp, repo, tmp_path, capsys):
    """Negative control: a tar that also edits a committed file in the package directory."""
    demo_repo(repo)
    tar = deps_tar(tmp_path, {MATHLIB_OLEAN: b"olean"})
    bindir = wrapper(tmp_path, "tar", shutil.which("tar"),
                     'case " $* " in *" --no-same-owner "*) echo "theorem planted : True := trivial" >> '
                     'Demo/Main.lean ;; esac')
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"DEPS_TAR": str(tar)}, path_prefix=bindir)
    assert p.returncode == 1
    assert "error: DEPS_TAR: the restore changed the tree outside .lake/packages" in log
    assert "lean-pkg-build: begin" not in log


def test_a_restore_that_creates_a_root_build_is_refused(lp, repo, tmp_path, capsys):
    """A tar that writes .lake/build next to .lake/packages: outside .lake/packages, so the restore check refuses
    (the fresh-build check after it would too)."""
    demo_repo(repo)
    tar = deps_tar(tmp_path, {MATHLIB_OLEAN: b"olean"})
    bindir = wrapper(tmp_path, "tar", shutil.which("tar"),
                     'case " $* " in *" --no-same-owner "*) mkdir -p .lake/build/lib/lean/Demo ;; esac')
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"DEPS_TAR": str(tar)}, path_prefix=bindir)
    assert p.returncode == 1
    assert "error: DEPS_TAR: the restore changed the tree outside .lake/packages" in log
    assert "lean-pkg-build: begin" not in log


def test_a_deps_tar_without_lake_packages_or_with_a_symlink_is_refused(lp, repo, tmp_path, capsys):
    tar = deps_tar(tmp_path, {"packages/mathlib/x": b"x"})
    p, log, _ = template_run(lp, capsys, repo, tmp_path, extra={"DEPS_TAR": str(tar)})
    assert p.returncode == 1 and "error: DEPS_TAR holds no .lake/packages directory" in log
    (tmp_path / "again").mkdir()
    tar = deps_tar(tmp_path / "again", {".lake/": None}, symlinks={".lake/packages": "/"})
    p, log, _ = template_run(lp, capsys, repo, tmp_path / "again", extra={"DEPS_TAR": str(tar)})
    assert p.returncode == 1 and "error: DEPS_TAR holds no .lake/packages directory" in log


def test_template_text_documents_both_options(lp, capsys):
    text = template_text(lp, capsys)
    assert "SRC_MODE=${SRC_MODE:-archive}" in text and "DEPS_TAR=${DEPS_TAR:-}" in text
    assert "GIT_ALLOW_PROTOCOL=file" in text and "git init -q --template=" in text
    assert 'echo "src-mode: $SRC_MODE"' in text and 'echo "deps-restore: $deps_restore"' in text
    # the restore runs before the fresh-build check and the build markers
    assert text.index('tar -xf "$DEPS_TAR"') < text.index("error: not a fresh build") \
        < text.index('echo "lean-pkg-build: begin"')
