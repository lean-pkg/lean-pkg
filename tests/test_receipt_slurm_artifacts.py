#!/usr/bin/env python3
"""The job-side artifact seal (field report: a collector interrupted after copying most of a job's native
artifacts had only its own hashes for them). The template's optional ARTIFACTS_DIR block copies every regular
file under the directory to ARTIFACTS_OUT with a sorted manifest `<path> <size> <sha256>` and prints the
manifest's sha256 on stdout beside the seal lines; `receipt slurm --artifacts DIR` (and `--check`) requires the
manifest to hash to that stdout line and rehashes every file under DIR against it. The template is run with
fake build commands; sacct is a fake; no registry is written."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import repo_head
from .test_receipt_slurm import (CHECKER, Job, _fixed_identity, fake_sacct, rdir, receipt_json,  # noqa: F401
                                 repo, template_text)
from .test_receipt_slurm_template_src import files_args, template_run

KEY = "artifacts-manifest-sha256"
# A build that leaves native artifacts in the job's package tree (the template's $SRC), a nested one included.
BUILD_ARTIFACTS = ("mkdir -p .lake/build/bin/sub && printf 'elf-a' > .lake/build/bin/a.out && "
                   "printf 'with space' > '.lake/build/bin/b c.bin' && head -c 3000000 /dev/zero > "
                   ".lake/build/bin/sub/big.bin && printf '' > .lake/build/bin/empty; ")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sealed_job(lp, capsys, repo, tmp_path, verify: str = BUILD_ARTIFACTS, extra: dict | None = None):
    """Run the template with ARTIFACTS_DIR=.lake/build/bin; (process, log, Job, artifacts copy)."""
    p, log, job = template_run(lp, capsys, repo, tmp_path, verify=verify,
                               extra={"ARTIFACTS_DIR": ".lake/build/bin", **(extra or {})})
    return p, log, job, tmp_path / "submit" / "artifacts-123"


@pytest.fixture()
def sealed(lp, capsys, repo, tmp_path):
    p, log, job, art = sealed_job(lp, capsys, repo, tmp_path)
    assert p.returncode == 0, p.stderr + log
    return job, art


def produce(lp, repo, job, art, *extra) -> int:
    return lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job), "--artifacts", str(art), *extra])


# --------------------------------------------------------------------------
# the template

def test_template_text_documents_the_optional_artifact_seal(lp, capsys):
    text = template_text(lp, capsys)
    assert "ARTIFACTS_DIR=${ARTIFACTS_DIR:-}" in text and "ARTIFACTS_BASE=${ARTIFACTS_BASE:-src}" in text
    assert "ARTIFACTS_OUT=${ARTIFACTS_OUT:-$OUT_DIR/artifacts-$JOB}" in text
    assert f'echo "{KEY} $artifacts_sha"' in text
    # after the audit, before the seal lines; printed only when the block ran
    assert text.index('bash -c "$AXIOMS_CMD"') < text.index('if [ -n "$ARTIFACTS_DIR" ]') \
        < text.index(f'echo "{KEY}') < text.index('echo "post-manifest-sha256') < text.index('echo "verify-log-sha256')
    assert "\t" not in text and "/home/" not in text


def test_without_artifacts_dir_the_job_prints_no_artifact_line(lp, repo, tmp_path, capsys):
    p, log, job = template_run(lp, capsys, repo, tmp_path)
    assert p.returncode == 0, p.stderr + log
    assert KEY not in p.stdout and "artifacts-manifest:" not in log
    assert not (tmp_path / "submit" / "artifacts-123").exists()


def test_the_job_writes_a_sorted_manifest_and_prints_its_hash(lp, repo, tmp_path, capsys, sealed):
    job, art = sealed
    manifest = (art / "artifacts.sha256").read_bytes()
    lines = manifest.decode().splitlines()
    assert lines == sorted(lines, key=lambda x: x.encode())
    big = b"\0" * 3000000
    assert lines == [f"a.out 5 {sha(b'elf-a')}", f"b c.bin 10 {sha(b'with space')}", f"empty 0 {sha(b'')}",
                     f"sub/big.bin {len(big)} {sha(big)}"]
    out = job.out.read_text()
    assert out.count(KEY) == 1 and f"{KEY} {sha(manifest)}\n" in out
    assert out.index(KEY) < out.index("post-manifest-sha256") < out.index("verify-log-sha256")
    assert (art / "sub" / "big.bin").read_bytes() == big
    log = job.log.read_text()
    assert f"artifacts-manifest: 4 file(s) from src/.lake/build/bin, sha256 {sha(manifest)}\n" in log
    assert str(tmp_path) not in log


@pytest.mark.parametrize("verify, extra, needle", [
    ("mkdir -p .lake/build/bin && ln -s /etc/hostname .lake/build/bin/link; ", {},
     "error: ARTIFACTS_DIR holds a symlink or special file: link"),
    ("mkdir -p .lake/build/bin && mkfifo .lake/build/bin/fifo; ", {},
     "error: ARTIFACTS_DIR holds a symlink or special file: fifo"),
    ("mkdir -p .lake/build/bin && touch \"$(printf '.lake/build/bin/a\\nb')\"; ", {},
     "error: ARTIFACTS_DIR holds a file name with a newline"),
    ("mkdir -p .lake/build/bin && touch .lake/build/bin/artifacts.sha256; ", {},
     "error: ARTIFACTS_DIR already holds artifacts.sha256"),
    ("", {}, "error: ARTIFACTS_DIR is not a directory under src"),
    (BUILD_ARTIFACTS, {"ARTIFACTS_DIR": "../escape"}, "error: ARTIFACTS_DIR must be a relative path without '..'"),
    (BUILD_ARTIFACTS, {"ARTIFACTS_DIR": "/etc"}, "error: ARTIFACTS_DIR must be a relative path without '..'"),
    (BUILD_ARTIFACTS, {"ARTIFACTS_BASE": "home"}, "error: ARTIFACTS_BASE is src or work, not 'home'"),
    ("mkdir -p real && ln -s real .lake/build/bin 2>/dev/null || { mkdir -p .lake/build && ln -s ../../real "
     ".lake/build/bin; }; ", {}, "error: ARTIFACTS_DIR is not a directory under src (or a path component is a "
                                 "symlink)"),
])
def test_the_job_fails_on_what_the_seal_cannot_bind(lp, repo, tmp_path, capsys, verify, extra, needle):
    p, log, job, art = sealed_job(lp, capsys, repo, tmp_path, verify=verify, extra=extra)
    assert p.returncode == 1
    assert needle in log
    assert KEY not in p.stdout and "verify-log-sha256" not in p.stdout


def test_the_job_never_overwrites_an_existing_artifacts_out(lp, repo, tmp_path, capsys):
    (tmp_path / "submit").mkdir()
    (tmp_path / "submit" / "artifacts-123").mkdir()
    path = tmp_path / "verify.sbatch"
    path.write_text(template_text(lp, capsys))
    env = {**os.environ, "SLURM_JOB_ID": "123", "SLURM_TMPDIR": str(tmp_path / "node"), "PKG_DIR": str(repo),
           "OUT_DIR": str(tmp_path / "submit"), "VERIFY_CMD": f"{BUILD_ARTIFACTS}echo 'lake build: ok'",
           "AXIOMS_CMD": "echo \"'Demo.t' does not depend on any axioms\"", "CHECKER": CHECKER,
           "LEAN_PATH_CMD": "true", "ARTIFACTS_DIR": ".lake/build/bin"}
    p = subprocess.run(["bash", str(path)], capture_output=True, text=True, env=env, cwd=tmp_path / "submit")
    assert p.returncode == 1
    assert "error: ARTIFACTS_OUT already exists" in (tmp_path / "submit" / "verify-123.log").read_text()
    assert list((tmp_path / "submit" / "artifacts-123").iterdir()) == []


def test_a_copy_that_does_not_rehash_fails_the_job(lp, repo, tmp_path, capsys):
    """Negative control: a `cp` that corrupts one copied file."""
    bindir = tmp_path / "fake-tools"
    bindir.mkdir()
    real = shutil.which("cp")
    (bindir / "cp").write_text(f'#!/usr/bin/env bash\n"{real}" "$@" || exit $?\n'
                               'for a; do d=$a; done; [ -f "$d/a.out" ] && printf X >> "$d/a.out"\nexit 0\n')
    (bindir / "cp").chmod(0o755)
    p, log, job = template_run(lp, capsys, repo, tmp_path, verify=BUILD_ARTIFACTS,
                               extra={"ARTIFACTS_DIR": ".lake/build/bin"}, path_prefix=bindir)
    assert p.returncode == 1
    assert "error: the copy in ARTIFACTS_OUT does not rehash to the manifest of ARTIFACTS_DIR" in log


def test_artifacts_base_work_reads_the_work_directory(lp, repo, tmp_path, capsys, fake_sacct):
    verify = 'mkdir -p "$SLURM_TMPDIR/out" && printf w > "$SLURM_TMPDIR/out/x"; '   # $WORK
    p, log, job, art = sealed_job(lp, capsys, repo, tmp_path, verify=verify,
                                  extra={"ARTIFACTS_DIR": "out", "ARTIFACTS_BASE": "work"})
    assert p.returncode == 0, p.stderr + log
    assert (art / "artifacts.sha256").read_text() == f"x 1 {sha(b'w')}\n"
    assert "artifacts-manifest: 1 file(s) from work/out" in log
    assert produce(lp, repo, job, art) == 0, capsys.readouterr().err


# --------------------------------------------------------------------------
# receipt slurm --artifacts

def test_artifacts_positive_receipt_passes_gate_check(lp, env, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    manifest = (art / "artifacts.sha256").read_bytes()
    assert lp.main(["receipt", "slurm", "--check", *files_args(repo, job), "--artifacts", str(art)]) == 0
    out = capsys.readouterr().out
    assert f"✓ artifacts: artifacts.sha256 sha256 = job stdout {KEY}" in out and "4 file(s)" in out
    assert not rdir(repo).exists()
    assert produce(lp, repo, job, art) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    r = receipt_json(repo)
    assert r["artifacts_manifest_sha256"] == sha(manifest)
    c12 = repo_head(repo)[:12]
    side = rdir(repo) / f"{c12}-slurm-123-artifacts.sha256"
    assert side.read_bytes() == manifest
    assert f"- Artifacts: `{side.name}`, SHA-256 `{sha(manifest)}`" in out
    assert "4 file(s), 3000015 bytes rehashed" in out
    # gate ignores the additive key
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0


def test_artifacts_json_lists_the_manifest_in_sealed(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    assert produce(lp, repo, job, art, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    roles = {s["role"]: s for s in payload["sealed"]}
    m = roles["artifacts.sha256"]
    assert m["receipt_key"] == "artifacts_manifest_sha256"
    assert m["sha256"] == sha(Path(m["path"]).read_bytes()) == sha((art / "artifacts.sha256").read_bytes())
    assert payload["artifacts"] == {"ok": True, "dir": str(art), "files": 4, "bytes": 3000015,
                                    "manifest": "artifacts.sha256",
                                    "manifest_sha256": sha((art / "artifacts.sha256").read_bytes())}


def test_without_artifacts_the_receipt_is_unchanged(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    """A sealed job's files, collected without --artifacts: the same receipt keys as ever, no artifacts JSON."""
    job, _art = sealed
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "artifacts" not in payload
    assert [s["role"] for s in payload["sealed"]] == ["receipt", "redacted-log"]
    r = receipt_json(repo)
    assert list(r) == ["schema", *lp.RECEIPT_FIELDS, "job_stdout_sha256", "manifest_sha256", "log_commit_bound",
                                            "producer"]


def refused(lp, repo, job, art, capsys, *extra) -> str:
    assert produce(lp, repo, job, art, *extra) == 1
    err = capsys.readouterr().err
    assert not rdir(repo).exists() or list(rdir(repo).iterdir()) == []
    return err


def test_a_missing_file_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    (art / "sub" / "big.bin").unlink()
    assert "--artifacts: 1 missing file(s): sub/big.bin" in refused(lp, repo, job, art, capsys)


def test_an_extra_file_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    (art / "sub" / "stray.o").write_bytes(b"x")
    assert "--artifacts: 1 extra file(s): sub/stray.o" in refused(lp, repo, job, art, capsys)


@pytest.mark.parametrize("edit", [b"elf-b", b"elf-a!"])          # same size, other size
def test_a_changed_file_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed, edit):
    job, art = sealed
    (art / "a.out").write_bytes(edit)
    assert "--artifacts: 1 changed file(s): a.out" in refused(lp, repo, job, art, capsys)


def test_a_symlink_and_a_special_file_are_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    (art / "a.out").unlink()
    (art / "a.out").symlink_to(art / "empty")
    os.mkfifo(art / "sub" / "pipe")
    err = refused(lp, repo, job, art, capsys)
    assert "--artifacts: 1 symlink file(s): a.out" in err and "--artifacts: 1 special file(s): sub/pipe" in err


def test_a_symlinked_artifacts_dir_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    link = tmp_path / "linked"
    link.symlink_to(art)
    assert "--artifacts linked: not a directory (a symlink is not one)" in refused(lp, repo, job, link, capsys)


def reseal(job: Job, art: Path, manifest: bytes) -> None:
    """Rewrite the manifest AND the stdout line (the stdout seal then still binds the log): only the
    manifest's own content is under test."""
    (art / "artifacts.sha256").write_bytes(manifest)
    out = job.out.read_text().splitlines(keepends=True)
    job.out.write_text("".join(f"{KEY} {sha(manifest)}\n" if x.startswith(KEY) else x for x in out))


@pytest.mark.parametrize("line, needle", [
    ("../escape 1 {h}", "1 path-traversal or invalid file(s): ../escape"),
    ("/etc/passwd 1 {h}", "1 path-traversal or invalid file(s): /etc/passwd"),
    ("sub/../a.out 5 {h}", "1 path-traversal or invalid file(s): sub/../a.out"),
    ("./a.out 5 {h}", "1 path-traversal or invalid file(s): ./a.out"),
    ("artifacts.sha256 1 {h}", "1 path-traversal or invalid file(s): artifacts.sha256"),
    ("a.out 5", "not `<relative path> <size> <sha256>`"),
])
def test_a_bad_manifest_entry_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed, line, needle):
    job, art = sealed
    manifest = (art / "artifacts.sha256").read_bytes() + (line.format(h=sha(b"x")) + "\n").encode()
    reseal(job, art, manifest)
    assert needle in refused(lp, repo, job, art, capsys)


def test_a_duplicate_manifest_entry_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    lines = (art / "artifacts.sha256").read_bytes().splitlines(keepends=True)
    reseal(job, art, b"".join([lines[0], *lines]))
    assert "--artifacts: artifacts.sha256: 1 duplicate file(s): a.out" in refused(lp, repo, job, art, capsys)


def test_an_unsorted_or_truncated_manifest_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    lines = (art / "artifacts.sha256").read_bytes().splitlines(keepends=True)
    reseal(job, art, b"".join(reversed(lines)))
    assert "the lines are not sorted by path" in refused(lp, repo, job, art, capsys)
    reseal(job, art, b"".join(lines).rstrip(b"\n"))
    assert "no final newline" in refused(lp, repo, job, art, capsys)


def test_a_manifest_that_is_not_the_stdout_line_is_refused(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    """The collector's own manifest (another attempt's hashes) is never trusted: a manifest rewritten to match
    the copied files fails against the job's stdout line."""
    job, art = sealed
    (art / "a.out").write_bytes(b"other")
    path = art / "artifacts.sha256"
    path.write_bytes(path.read_bytes().replace(f"a.out 5 {sha(b'elf-a')}".encode(),
                                               f"a.out 5 {sha(b'other')}".encode()))
    err = refused(lp, repo, job, art, capsys)
    assert "is not the manifest the job wrote" in err and f"not the job stdout's {KEY}" in err


def test_a_stdout_without_the_line_is_refused_and_stdout_is_required(lp, repo, tmp_path, capsys, fake_sacct,
                                                                     sealed):
    job, art = sealed
    job.out.write_text("".join(x for x in job.out.read_text().splitlines(keepends=True) if not x.startswith(KEY)))
    assert f"job stdout: 0 `{KEY}` line(s); exactly one is required" in refused(lp, repo, job, art, capsys)
    argv = ["receipt", "slurm", "--job", "123", "--log", str(job.log), "--commit", repo_head(repo), "--repo",
            str(repo), "--artifacts", str(art)]
    assert lp.main(argv) == 2
    assert "--artifacts needs --stdout" in capsys.readouterr().err


@pytest.mark.parametrize("extra, needle", [
    (["--artifacts-manifest", "m.sha256"], "--artifacts-manifest is only for --artifacts DIR"),
    (["--artifacts", "nowhere"], "--artifacts nowhere: no such directory"),
    (["--artifacts", ".", "--artifacts-manifest", "../m"], "a file name inside the --artifacts directory"),
])
def test_artifacts_usage_errors(lp, repo, tmp_path, capsys, fake_sacct, sealed, extra, needle):
    job, _art = sealed
    assert lp.main(["receipt", "slurm", "--job", "123", *files_args(repo, job), *extra]) == 2
    assert needle in capsys.readouterr().err


def test_a_renamed_manifest_is_found_by_name(lp, repo, tmp_path, capsys, fake_sacct, sealed):
    job, art = sealed
    (art / "artifacts.sha256").rename(art / "job.manifest")
    assert produce(lp, repo, job, art) == 1
    assert "no regular file artifacts.sha256 in the directory" in capsys.readouterr().err
    assert produce(lp, repo, job, art, "--artifacts-manifest", "job.manifest") == 0, capsys.readouterr().err


def test_check_names_artifact_problems_in_json(lp, repo, tmp_path, capsys, sealed):
    job, art = sealed
    (art / "empty").unlink()
    assert lp.main(["receipt", "slurm", "--check", *files_args(repo, job), "--artifacts", str(art), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any("1 missing file(s): empty" in x for x in payload["problems"])
    assert payload["artifacts"] == {"ok": False, "dir": str(art), "manifest": "artifacts.sha256"}
    assert payload["written"] == []
