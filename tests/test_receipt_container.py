#!/usr/bin/env python3
"""#236: the container image a job ran in, as the additive receipt key `container`. The sbatch template takes
the SIF file's sha256 before the build and writes it to the log and the stdout; `receipt slurm` seals it (log =
stdout), `receipt check` and the page show it, `comparator-receipt` measures it inside Apptainer. A receipt
without the key is unchanged. No registry is read but the throwaway test one."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from lean_pkg import container as ctr

from . import test_receipt_check as trc
from .conftest import repo_head
from .test_comparator_receipt import git, project, run_runner  # noqa: F401
from .test_receipt_slurm import (CHECKER, LOG, Job, _fixed_identity, fake_sacct, rdir, receipt_json, repo,  # noqa: F401
                                 run, template_text)

SIF = b"SIF image bytes\n" * 64
SIF_SHA = hashlib.sha256(SIF).hexdigest()
OCI = "sha256:" + "b" * 64


def container_lines(digest: str = SIF_SHA, *, engine: str = "apptainer", ref: str = "images/lean.sif",
                    key: str = "container-image-sha256") -> str:
    return f"container-engine: {engine}\ncontainer-image: {ref}\n{key}: {digest}\n"


def job_with_container(tmp_path, *, log_digest=SIF_SHA, stdout_digest=SIF_SHA, ref="images/lean.sif",
                       engine="apptainer", key="container-image-sha256") -> Job:
    log = container_lines(log_digest, engine=engine, ref=ref, key=key) + LOG
    extra = f"{key} {stdout_digest}\n" if stdout_digest is not None else ""
    return Job(tmp_path, log, stdout_extra=extra)


# --------------------------------------------------------------------------
# receipt slurm

def test_a_container_digest_is_sealed_into_the_receipt(lp, env, repo, tmp_path, fake_sacct, capsys):
    job = job_with_container(tmp_path)
    assert run(lp, repo, job) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert f"✓ container: Apptainer container, SIF SHA-256 {SIF_SHA}" in out and "verify log = job stdout" in out
    r = receipt_json(repo)
    assert r["container"] == {"engine": "apptainer", "image_sha256": SIF_SHA, "ref": "images/lean.sif"}
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0


def test_an_oci_digest_is_sealed_too(lp, repo, tmp_path, fake_sacct, capsys):
    job = job_with_container(tmp_path, log_digest=OCI, stdout_digest=OCI, ref="ghcr.io/example/lean:4.29",
                             engine="podman", key="container-oci-digest")
    assert run(lp, repo, job) == 0, capsys.readouterr().err
    assert receipt_json(repo)["container"] == {"engine": "podman", "oci_digest": OCI,
                                                "ref": "ghcr.io/example/lean:4.29"}


def test_a_receipt_without_container_lines_has_no_key(lp, env, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path)) == 0
    r = receipt_json(repo)
    assert "container" not in r and "container" not in capsys.readouterr().out
    rp = next(rdir(repo).glob("*-slurm-123.json"))
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0


@pytest.mark.parametrize("kwargs, needle", [
    ({"stdout_digest": "c" * 64}, "the image digest was changed after the job"),
    ({"stdout_digest": None}, "0 `container-image-sha256` line(s) beside the verify log's"),
    ({"engine": "podman"}, "`container-engine:` must be one of apptainer, singularity"),
    ({"log_digest": "xyz", "stdout_digest": "xyz"}, "`container-image-sha256:` must match"),
])
def test_a_tampered_or_incomplete_container_line_is_refused(lp, repo, tmp_path, fake_sacct, capsys, kwargs,
                                                             needle):
    assert run(lp, repo, job_with_container(tmp_path, **kwargs)) == 1
    assert needle in capsys.readouterr().err
    assert not rdir(repo).exists() or not list(rdir(repo).glob("*.json"))


def test_an_edited_log_digest_breaks_the_log_seal(lp, repo, tmp_path, fake_sacct, capsys):
    job = job_with_container(tmp_path)
    job.log.write_text(job.log.read_text().replace(SIF_SHA, "d" * 64))
    job.out.write_text(job.out.read_text().replace(SIF_SHA, "d" * 64))
    assert run(lp, repo, job) == 1
    assert "is not the raw verify log's sha256" in capsys.readouterr().err


def test_a_stdout_digest_without_a_log_line_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    job = Job(tmp_path, stdout_extra=f"container-image-sha256 {SIF_SHA}\n")
    assert run(lp, repo, job) == 1
    assert "the job stdout has `container-image-sha256`" in capsys.readouterr().err


def test_without_stdout_the_in_log_seal_carries_the_digest(lp, repo, tmp_path, fake_sacct, capsys):
    body = f"commit: {repo_head(repo)}\n" + container_lines() + LOG
    seal = hashlib.sha256(body.encode()).hexdigest()
    job = Job(tmp_path, body + f"verify-log-sha256: {seal}\n")
    assert run(lp, repo, job, stdout=False) == 0, capsys.readouterr().err
    assert "in-log seal only (no --stdout)" in capsys.readouterr().out
    assert receipt_json(repo)["container"]["image_sha256"] == SIF_SHA


def test_check_preflight_names_a_container_mismatch(lp, repo, tmp_path, capsys):
    job = job_with_container(tmp_path, stdout_digest="e" * 64)
    assert lp.main(["receipt", "slurm", "--check", "--log", str(job.log), "--stdout", str(job.out),
                    "--checker", CHECKER, "--repo", str(repo)]) == 1
    assert "the image digest was changed after the job" in capsys.readouterr().out


def test_the_ref_is_published_redacted(lp, repo, tmp_path, fake_sacct, capsys):
    job = job_with_container(tmp_path, ref="/scratch/someone/images/lean.sif")
    assert run(lp, repo, job) == 0, capsys.readouterr().err
    assert receipt_json(repo)["container"]["ref"] == "/scratch/<USER>/images/lean.sif"


def test_a_redaction_may_not_alter_the_digest_line(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, job_with_container(tmp_path), "--redact", SIF_SHA[:20]) == 1
    assert "container-image-sha256 differ" in capsys.readouterr().err


def test_redact_ref_shows_a_home_directory_as_tilde(monkeypatch):
    monkeypatch.setenv("HOME", "/srv/u/me")
    assert ctr.redact_ref("/srv/u/me/img/lean.sif") == "~/img/lean.sif"
    # built at run time: the public-safety scan blocks a literal home path in the tree
    assert ctr.redact_ref("/".join(("", "home", "alice", "lean.sif"))) == "~/lean.sif"
    assert ctr.redact_ref("/".join(("", "Users", "bob", "x", "lean.sif"))) == "~/x/lean.sif"
    assert ctr.redact_ref("/opt/images/lean.sif") == "/opt/images/lean.sif"
    assert ctr.redact_ref("docker.io/library/lean:4") == "docker.io/library/lean:4"


# --------------------------------------------------------------------------
# the template

def run_container_template(lp, capsys, repo, tmp_path, monkeypatch) -> Job:
    """The printed template run as the job would, under a fake `apptainer` that runs its command on the host."""
    path = tmp_path / "verify.sbatch"
    path.write_text(template_text(lp, capsys))
    home = tmp_path / "home"
    (home / "images").mkdir(parents=True)
    sif = home / "images" / "lean.sif"
    sif.write_bytes(SIF)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "apptainer"
    fake.write_text('#!/bin/bash\n[ "$1" = exec ] || exit 9\nshift 2\nexec "$@"\n')
    fake.chmod(0o755)
    (tmp_path / "build.txt").write_text("Build completed successfully\n")
    (tmp_path / "axioms.txt").write_text("'Demo.x' does not depend on any axioms\n")
    out_dir = tmp_path / "submit"
    out_dir.mkdir()
    env = {**os.environ, "HOME": str(home), "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
           "SLURM_JOB_ID": "123", "SLURM_TMPDIR": str(tmp_path / "node"), "PKG_DIR": str(repo),
           "OUT_DIR": str(out_dir), "VERIFY_CMD": f"cat {tmp_path / 'build.txt'}",
           "AXIOMS_CMD": f"cat {tmp_path / 'axioms.txt'}", "CHECKER": CHECKER, "LEAN_PATH_CMD": "true",
           "RUN": f"apptainer exec {sif}"}
    p = subprocess.run(["bash", str(path)], capture_output=True, text=True, env=env, cwd=out_dir)
    assert p.returncode == 0, p.stderr
    job = Job.__new__(Job)
    job.log = out_dir / "verify-123.log"
    job.out, job.err = out_dir / "verify-123.out", out_dir / "verify-123.err"
    job.out.write_text(p.stdout)
    job.err.write_text(p.stderr)
    return job


def test_the_template_hashes_the_sif_and_the_receipt_seals_it(lp, repo, tmp_path, fake_sacct, capsys,
                                                               monkeypatch):
    text = template_text(lp, capsys)
    assert 'echo "$container_key $container_digest"' in text and "CONTAINER_IMAGE" in text
    job = run_container_template(lp, capsys, repo, tmp_path, monkeypatch)
    assert job.err.read_text() == ""
    assert job.out.read_text().count(f"container-image-sha256 {SIF_SHA}\n") == 1
    log = job.log.read_text()
    assert "container-engine: apptainer\n" in log and "container-image: ~/images/lean.sif\n" in log
    assert f"container-image-sha256: {SIF_SHA}\n" in log and str(tmp_path) + "/home" not in log
    assert lp.main(["receipt", "slurm", "--job", "123", "--log", str(job.log), "--stdout", str(job.out),
                    "--stderr", str(job.err), "--commit", repo_head(repo), "--repo", str(repo)]) == 0
    assert receipt_json(repo)["container"] == {"engine": "apptainer", "image_sha256": SIF_SHA,
                                                "ref": "~/images/lean.sif"}


def test_the_template_without_a_container_prints_no_container_line(lp, repo, tmp_path, capsys):
    from .test_receipt_slurm import run_template_job
    job = run_template_job(lp, capsys, repo, tmp_path, "Build completed successfully\n",
                           "'Demo.x' does not depend on any axioms\n")
    assert "container" not in job.out.read_text() and "container-" not in job.log.read_text()


# --------------------------------------------------------------------------
# receipt check and the page

GOOD = {"engine": "apptainer", "image_sha256": SIF_SHA, "ref": "~/images/lean.sif"}


def publish_with(monkeypatch, lp, env, capsys, value) -> str:
    orig = trc.make_receipt
    monkeypatch.setattr(trc, "make_receipt", lambda repo, **kw: {**orig(repo, **kw), "container": value})
    return trc.publish_fixture(lp, env, capsys)


def test_receipt_check_and_the_page_show_the_container(lp, env, capsys, tmp_path, monkeypatch):
    x = publish_with(monkeypatch, lp, env, capsys, GOOD)
    readme = (env.repo_b / "README.md").read_text()
    assert f"run in a container (Apptainer), SIF SHA-256 `{SIF_SHA}`" in readme
    assert "~/images" not in readme                        # the ref (a path) is never copied to the page
    rc, payload = trc.check(lp, capsys, trc.referee_clone(env, tmp_path), x, "--docs-descendants")
    cid = f"lower .lean-receipts/{x[:12]}-{trc.JOB}.json: container"
    item = next(i for i in payload["checks"] if i["id"] == cid)
    assert item["verdict"] == "pass"
    assert rc == 0, [i for i in payload["checks"] if i["verdict"] == "fail"]
    assert f"Apptainer container, SIF SHA-256 {SIF_SHA} (image `~/images/lean.sif`)" in item["detail"]


def test_receipt_check_shows_a_comparator_receipts_container(lp, env, capsys, tmp_path, monkeypatch):
    orig = trc.make_comparator_receipt
    monkeypatch.setattr(trc, "make_comparator_receipt", lambda repo, **kw: {**orig(repo, **kw), "container": GOOD})
    x = trc.publish_fixture(lp, env, capsys)
    rc, payload = trc.check(lp, capsys, trc.referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 0 and trc.verdicts(payload)[f"comparator .lean-receipts/{x[:12]}-comparator.json: container"] == "pass"


def test_receipt_check_fails_a_malformed_container(lp, env, capsys, tmp_path, monkeypatch):
    x = publish_with(monkeypatch, lp, env, capsys, {"engine": "apptainer", "image_sha256": "short"})
    assert "run in a container" not in (env.repo_b / "README.md").read_text()
    rc, payload = trc.check(lp, capsys, trc.referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 1 and trc.verdicts(payload)[f"lower .lean-receipts/{x[:12]}-{trc.JOB}.json: container"] == "fail"


def test_receipt_check_has_no_container_line_without_the_key(lp, env, capsys, tmp_path):
    x = trc.publish_fixture(lp, env, capsys)
    _rc, payload = trc.check(lp, capsys, trc.referee_clone(env, tmp_path), x, "--docs-descendants")
    assert not any(i["id"].endswith(": container") for i in payload["checks"])


@pytest.mark.parametrize("value, bad", [
    (GOOD, []),
    ({"engine": "podman", "oci_digest": OCI}, []),
    ({"engine": "podman", "image_sha256": SIF_SHA}, ["only for engine apptainer or singularity, not podman"]),
    ({"engine": "apptainer", "image_sha256": SIF_SHA, "oci_digest": OCI}, ["exactly one of"]),
    ({"engine": "lxc", "image_sha256": SIF_SHA}, ["container.engine"]),
    ({**GOOD, "host": "x"}, ["unknown key"]),
    ("apptainer", ["must be a mapping"]),
])
def test_problems(value, bad):
    got = ctr.problems(value)
    assert bool(got) == bool(bad) and all(any(b in g for g in got) for b in bad)


# --------------------------------------------------------------------------
# comparator-receipt

def test_from_environment_hashes_a_visible_sif(tmp_path, monkeypatch):
    sif = tmp_path / "lean.sif"
    sif.write_bytes(SIF)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ctr.from_environment({"APPTAINER_CONTAINER": str(sif), "SINGULARITY_CONTAINER": str(sif)}) == \
        {"engine": "apptainer", "image_sha256": SIF_SHA, "ref": "~/lean.sif"}
    assert ctr.from_environment({"SINGULARITY_CONTAINER": str(sif)})["engine"] == "singularity"
    assert ctr.from_environment({"APPTAINER_CONTAINER": str(tmp_path / "absent.sif")}) is None
    assert ctr.from_environment({"APPTAINER_CONTAINER": str(tmp_path)}) is None        # a sandbox directory
    assert ctr.from_environment({}) is None


def test_comparator_receipt_records_the_container_inside_apptainer(project, tmp_path):
    """A real runner run (the stub comparator): APPTAINER_CONTAINER naming a readable SIF adds the key, its
    path with the home directory shown as ~ (without the variable the receipt has none: from_environment)."""
    import json
    sif = tmp_path / "lean.sif"
    sif.write_bytes(SIF)
    r = run_runner(project, extra_env={"APPTAINER_CONTAINER": str(sif), "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    rec = json.loads((project / ".lean-receipts" / f"{git(project, 'rev-parse', 'HEAD')[:12]}-comparator.json")
                     .read_text())
    assert rec["container"] == {"engine": "apptainer", "image_sha256": SIF_SHA, "ref": "~/lean.sif"}
