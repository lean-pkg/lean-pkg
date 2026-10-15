#!/usr/bin/env python3
"""#70: `receipt slurm` turns a finished Slurm job (accounting, raw verify log, stdout seal, stderr) into a
lower-tier receipt that `gate --check` accepts; every refusal writes nothing. sacct is a fake on PATH; no
registry is read."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from .conftest import MATHLIB_MANIFEST, MATHLIB_REV, TOOLCHAIN, _git, repo_head

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "slurm"
SACCT_OK = (FIXTURES / "sacct-completed.txt").read_text()
LOG = (FIXTURES / "verify.log").read_text()
STD_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]
CHECKER = "verify.sh (lake build; #print axioms; negative controls)"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture()
def repo(env) -> Path:
    """alpha's checkout with lake-manifest.json committed (a mathlib package at MATHLIB_REV)."""
    (env.repo_a / "lake-manifest.json").write_text(MATHLIB_MANIFEST)
    _git(env.repo_a, "add", "lake-manifest.json")
    _git(env.repo_a, "commit", "-m", "manifest")
    return env.repo_a


@pytest.fixture()
def fake_sacct(tmp_path, monkeypatch):
    """A `sacct` on PATH that prints the text written to the returned file (and records its argv)."""
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    out = bindir / "output.txt"
    out.write_text(SACCT_OK)
    script = bindir / "sacct"
    script.write_text(f'#!/bin/sh\necho "$@" > "{bindir}/argv.txt"\ncat "{out}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return out


def no_sacct(monkeypatch):
    """No sacct on this host (a real one, on a cluster node, is never run by the suite)."""
    from lean_pkg import receipt
    real = receipt.shutil.which
    monkeypatch.setattr(receipt.shutil, "which", lambda name, *a, **k: None if name == "sacct" else real(name, *a, **k))


T1, T2 = "2026-10-13T18:00:00+00:00", "2026-10-13T18:01:00+00:00"


def frozen_clock(monkeypatch, start: str = T1) -> list[str]:
    """#85: receipt slurm's fallback `generated_at` reads this clock; set `clock[0]` to move it."""
    from lean_pkg import receipt
    clock = [start]
    monkeypatch.setattr(receipt.core, "now_iso", lambda: clock[0])
    return clock


class Job:
    """The files a Slurm verify job leaves: the raw verify log, its stdout (.out) and stderr (.err)."""

    def __init__(self, tmp_path: Path, log: str = LOG, *, seal: str | None = None, pre: str | None = None,
                 post: str | None = None, err: str = "", stdout_extra: str = ""):
        man = sha(MATHLIB_MANIFEST.encode())
        self.log = tmp_path / "verify.log"
        self.log.write_text(log)
        self.out = tmp_path / "job-123.out"
        self.out.write_text("toolchain-staged-seconds 12\n"
                            f"pre-manifest-sha256 {pre or man}\n"
                            f"post-manifest-sha256 {post or man}\n"
                            f"verify-log-sha256: {seal or sha(log.encode())}\n" + stdout_extra)
        self.err = tmp_path / "job-123.err"
        self.err.write_text(err)


def run(lp, repo, job: Job, *extra, stdout=True, stderr=True, checker=True):
    argv = ["receipt", "slurm", "--job", "123", "--log", str(job.log), "--commit", repo_head(repo),
            "--repo", str(repo)]
    if stdout:
        argv += ["--stdout", str(job.out)]
    if stderr:
        argv += ["--stderr", str(job.err)]
    if checker:
        argv += ["--checker", CHECKER]
    return lp.main(argv + list(extra))


def rdir(repo) -> Path:
    return repo / ".lean-receipts"


def assert_nothing_written(repo):
    assert not rdir(repo).exists() or list(rdir(repo).iterdir()) == []


def test_happy_path_receipt_passes_gate_check(lp, env, repo, tmp_path, fake_sacct, capsys):
    before = env.registry.read_bytes()
    assert run(lp, repo, Job(tmp_path)) == 0
    out = capsys.readouterr().out
    c12 = repo_head(repo)[:12]
    rp, lg = rdir(repo) / f"{c12}-slurm-123.json", rdir(repo) / f"{c12}-slurm-123-verify.redacted.log"
    lines = out.splitlines()
    assert lines[0].startswith("receipt slurm: ok, job slurm-123")
    for needle in ("✓ sacct: job 123 COMPLETED", "✓ seal: job stdout verify-log-sha256", "✓ manifest: pre = post",
                   "✓ stderr: empty", "✓ public-safety"):
        assert needle in out, needle
    assert f"## `{c12[:7]}`: lower-tier receipt, Slurm job 123" in out
    assert "elapsed 00:12:05, TotalCPU 01:10:22." in out
    assert f"next: lean-pkg gate NAME --check --receipt .lean-receipts/{c12}-slurm-123.json" in out
    receipt = json.loads(rp.read_text())
    assert list(receipt) == list(lp.RECEIPT_FIELDS) + ["job_stdout_sha256", "manifest_sha256"]
    assert receipt["report_sha256"] == sha(lg.read_bytes())
    assert receipt["job_id"] == "slurm-123" and receipt["sorry_count"] == 0
    assert receipt["lean_toolchain"] == TOOLCHAIN and receipt["mathlib_rev"] == MATHLIB_REV
    assert receipt["axioms"] == STD_AXIOMS and receipt["checker"] == CHECKER
    text = lg.read_text()
    job_out = tmp_path / "job-123.out"
    assert receipt["job_stdout_sha256"] == sha(job_out.read_bytes())
    assert receipt["manifest_sha256"] == sha(MATHLIB_MANIFEST.encode())
    assert "/tmp/user-job" not in text and "/scratch/user/" not in text
    assert "/tmp/<USER>-job-123/src" in text and "/scratch/<USER>/lake-cache" in text
    argv = (fake_sacct.parent / "argv.txt").read_text().split()
    assert argv == ["-j", "123", "--format=JobID,State,ExitCode,Elapsed,TotalCPU", "-P", "-n"]
    assert not (rdir(repo) / "README.md").exists()            # printed, not appended, without the flag
    assert env.registry.read_bytes() == before                # receipt slurm never touches the registry
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


def test_json_payload(lp, repo, tmp_path, fake_sacct, capsys):
    job = Job(tmp_path)
    assert run(lp, repo, job, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["refusals"] == [] and data["job_id"] == "slurm-123"
    assert Path(data["receipt"]).is_file() and Path(data["log"]).is_file()
    assert data["report_sha256"] == sha(Path(data["log"]).read_bytes())
    assert data["raw_log_sha256"] == sha(job.log.read_bytes())
    assert data["sacct"] == {"job_id": "123", "state": "COMPLETED", "exit_code": "0:0", "elapsed": "00:12:05",
                             "total_cpu": "01:10:22"}
    assert data["redactions"] == 3 and data["lines_redacted"] == 3
    assert data["readme_appended"] is False and data["readme_block"].startswith("## `")


def test_append_readme_writes_the_block_once(lp, repo, tmp_path, fake_sacct, capsys, monkeypatch):
    clock = frozen_clock(monkeypatch)       # #85: the re-run below was flaky across a second boundary
    job = Job(tmp_path)
    rdir(repo).mkdir()
    (rdir(repo) / "README.md").write_text("# Receipts\n\nEarlier entries.\n")
    extra = ("--append-readme", "--readme-heading", "first replay", "--readme-line", "- Note: rerun of the v2 wave.",
             "--tar-sha256", "b" * 64)
    assert run(lp, repo, job, *extra) == 0
    c12 = repo_head(repo)[:12]
    text = (rdir(repo) / "README.md").read_text()
    assert text.startswith("# Receipts\n\nEarlier entries.\n\n## `")
    assert f"## `{c12[:7]}`: first replay" in text
    assert f"- Receipt: `{c12}-slurm-123.json`. Slurm job `123`: COMPLETED, exit `0:0`," in text
    assert f"tar SHA-256 `{'b' * 64}`." in text
    assert f"raw log SHA-256 `{sha(job.log.read_bytes())}` (3 line(s) differ" in text
    assert text.rstrip().endswith("- Note: rerun of the v2 wave.")
    clock[0] = T2                                             # a minute later: still the identical receipt
    assert run(lp, repo, job, *extra) == 0                    # identical re-run: no second entry
    assert json.loads((rdir(repo) / f"{c12}-slurm-123.json").read_text())["generated_at"] == T1
    assert (rdir(repo) / "README.md").read_text().count(f"`{c12}-slurm-123.json`") == 1
    assert "not appended again" in capsys.readouterr().out


def test_append_readme_creates_the_file(lp, repo, tmp_path, fake_sacct):
    assert run(lp, repo, Job(tmp_path), "--append-readme") == 0
    assert (rdir(repo) / "README.md").read_text().startswith("# Receipts\n\n## `")


def test_private_readme_text_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path), "--append-readme", "--readme-line", "see /home/someone/run") == 1
    assert "README block (home-path)" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_differing_receipt_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    log = LOG + "generated-at: 2026-10-10T12:00:00+00:00\n"
    assert run(lp, repo, Job(tmp_path, log)) == 0
    assert run(lp, repo, Job(tmp_path, log)) == 0
    next(rdir(repo).glob("*-slurm-123.json")).write_text("{}")
    capsys.readouterr()
    assert run(lp, repo, Job(tmp_path, log)) == 1
    assert "already exists with different content" in capsys.readouterr().err


def receipt_json(repo) -> dict:
    return json.loads(next(rdir(repo).glob("*-slurm-123.json")).read_text())


def test_rerun_in_a_later_second_keeps_the_stored_generated_at(lp, repo, tmp_path, fake_sacct, capsys, monkeypatch):
    """#85: without a `generated-at:` log line the receipt is clock-stamped; re-running the same job later is
    the identical receipt, not a refusal, and keeps the first time."""
    clock = frozen_clock(monkeypatch)
    assert run(lp, repo, Job(tmp_path)) == 0
    first = next(rdir(repo).glob("*-slurm-123.json")).read_bytes()
    capsys.readouterr()
    clock[0] = T2
    assert run(lp, repo, Job(tmp_path)) == 0
    assert "identical but for its clock time; kept generated_at " + T1 in capsys.readouterr().out
    assert next(rdir(repo).glob("*-slurm-123.json")).read_bytes() == first
    # a same-second re-run needs no kept time and says nothing about it
    clock[0] = T1
    assert run(lp, repo, Job(tmp_path)) == 0
    assert "kept generated_at" not in capsys.readouterr().out


def test_rerun_differing_beyond_the_clock_time_is_still_refused(lp, repo, tmp_path, fake_sacct, capsys,
                                                                monkeypatch):
    """#85: only a clock-stamped generated_at is forgiven; any other change, or a stored time that does not
    parse, still refuses with nothing written."""
    clock = frozen_clock(monkeypatch)
    assert run(lp, repo, Job(tmp_path)) == 0
    first = next(rdir(repo).glob("*-slurm-123.json")).read_bytes()
    clock[0] = T2
    capsys.readouterr()
    assert run(lp, repo, Job(tmp_path), "--checker", "a different checker") == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert next(rdir(repo).glob("*-slurm-123.json")).read_bytes() == first
    path = next(rdir(repo).glob("*-slurm-123.json"))
    path.write_text(first.decode().replace(T1, "yesterday"))
    assert run(lp, repo, Job(tmp_path)) == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert receipt_json(repo)["generated_at"] == "yesterday"


def test_explicit_generated_at_that_disagrees_is_still_refused(lp, repo, tmp_path, fake_sacct, capsys):
    """#85: a log that states its own `generated-at:` is never overridden by the stored receipt."""
    assert run(lp, repo, Job(tmp_path, LOG + f"generated-at: {T1}\n")) == 0
    capsys.readouterr()
    assert run(lp, repo, Job(tmp_path, LOG + f"generated-at: {T2}\n")) == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert receipt_json(repo)["generated_at"] == T1


@pytest.mark.parametrize("sacct, needle", [
    ("123|FAILED|1:0|00:01:00|00:00:10\n123.batch|FAILED|1:0||\n", "State=FAILED"),
    ("123|COMPLETED|1:0|00:01:00|00:00:10\n", "ExitCode=1:0"),
    ("123|CANCELLED by 42|0:15\n", "State=CANCELLED by 42"),
    ("124|COMPLETED|0:0\n123.batch|COMPLETED|0:0\n", "no main job line"),
])
def test_unclean_job_is_refused(lp, repo, tmp_path, fake_sacct, capsys, sacct, needle):
    fake_sacct.write_text(sacct)
    assert run(lp, repo, Job(tmp_path)) == 1
    err = capsys.readouterr().err
    assert needle in err and "nothing written" in err.splitlines()[0]
    assert_nothing_written(repo)


def test_no_sacct_on_path_names_the_sacct_file_route(lp, repo, tmp_path, monkeypatch, capsys):
    no_sacct(monkeypatch)
    assert run(lp, repo, Job(tmp_path)) == 1
    assert "--sacct-file" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_sacct_file_replaces_sacct_and_three_columns_suffice(lp, repo, tmp_path, monkeypatch, capsys):
    no_sacct(monkeypatch)
    saved = tmp_path / "sacct.txt"
    saved.write_text("123|COMPLETED|0:0\n")
    assert run(lp, repo, Job(tmp_path), "--sacct-file", str(saved)) == 0
    assert "elapsed unknown, TotalCPU unknown." in capsys.readouterr().out
    saved.write_text("123|TIMEOUT|0:0\n")
    for p in rdir(repo).iterdir():
        p.unlink()
    assert run(lp, repo, Job(tmp_path), "--sacct-file", str(saved)) == 1
    assert "State=TIMEOUT" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_stdout_seal_mismatch_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path, seal="c" * 64)) == 1
    assert "is not the raw verify log's sha256" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_edited_log_breaks_the_stdout_seal(lp, repo, tmp_path, fake_sacct, capsys):
    job = Job(tmp_path)
    job.log.write_text(LOG.replace("exit 0", "exit 0 (edited)"))
    assert run(lp, repo, job) == 1
    assert "the log is not the one the job sealed" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_missing_or_duplicate_stdout_lines_are_refused(lp, repo, tmp_path, fake_sacct, capsys):
    job = Job(tmp_path)
    job.out.write_text("\n".join(x for x in job.out.read_text().splitlines() if not x.startswith("post-")) + "\n")
    assert run(lp, repo, job) == 1
    assert "0 `post-manifest-sha256` line(s)" in capsys.readouterr().err
    job = Job(tmp_path, stdout_extra=f"verify-log-sha256 {'d' * 64}\n")
    assert run(lp, repo, job) == 1
    assert "2 `verify-log-sha256` line(s)" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_manifest_changed_during_the_job_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path, post="e" * 64)) == 1
    assert "the manifest changed during the job" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_manifest_not_the_committed_one_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path, pre="f" * 64, post="f" * 64)) == 1
    assert "is not lake-manifest.json committed at" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_manifest_check_needs_a_committed_manifest(lp, env, tmp_path, fake_sacct, capsys):
    assert run(lp, env.repo_a, Job(tmp_path)) == 1             # repo_a has no lake-manifest.json
    err = capsys.readouterr().err
    assert "no lake-manifest.json committed" in err and "no mathlib rev" in err
    assert_nothing_written(env.repo_a)


def test_nonempty_stderr_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path, err="warning: something\n")) == 1
    assert "job stderr is not empty (19 bytes)" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_in_log_seal_is_the_fallback_without_stdout(lp, repo, tmp_path, fake_sacct, capsys):
    raw = LOG.encode()
    sealed = (raw + f"verify-log-sha256: {sha(raw)}\n".encode()).decode()
    assert run(lp, repo, Job(tmp_path, sealed), stdout=False, stderr=False) == 0
    out = capsys.readouterr().out
    assert "✓ seal: in-log verify-log-sha256 (fallback; no --stdout)" in out
    assert "· manifest: not checked (no --stdout)" in out and "· stderr: not checked" in out
    for p in rdir(repo).iterdir():
        p.unlink()
    assert run(lp, repo, Job(tmp_path, sealed.replace("exit 0", "exit 1")), stdout=False) == 1
    assert "changed after it was sealed" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path), stdout=False) == 1
    assert "--stdout" in capsys.readouterr().err               # no seal anywhere: names the stdout route
    assert_nothing_written(repo)


def test_log_key_lines_are_used_and_must_agree_with_the_commit(lp, repo, tmp_path, fake_sacct, capsys):
    keyed = (LOG + f"lean-toolchain: {TOOLCHAIN}\nmathlib-rev: {MATHLIB_REV}\nchecker: serial leanchecker\n"
             "sorry-count: 0\naxioms: propext\n")
    assert run(lp, repo, Job(tmp_path, keyed), checker=False) == 0
    r = json.loads(next(rdir(repo).glob("*.json")).read_text())
    assert r["checker"] == "serial leanchecker" and r["axioms"] == ["propext"]
    for p in rdir(repo).iterdir():
        p.unlink()
    capsys.readouterr()
    assert run(lp, repo, Job(tmp_path, keyed.replace("v4.32.0", "v4.33.0"))) == 1
    assert "toolchain mismatch" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path, keyed.replace(MATHLIB_REV, "0" * 40))) == 1
    assert "mathlib mismatch" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path, keyed + "axioms: propext\n")) == 1
    assert "`axioms:` appears 2 times" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_missing_checker_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path), checker=False) == 1
    assert "no checker: pass --checker TEXT" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_missing_axiom_evidence_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    log = "".join(x for x in LOG.splitlines(keepends=True) if "axioms" not in x)
    assert run(lp, repo, Job(tmp_path, log)) == 1
    assert "no `axioms:` line and no `#print axioms` output" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_sorry_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    log = LOG + "warning: Demo.lean:3:8: declaration uses 'sorry'\n"
    assert run(lp, repo, Job(tmp_path, log)) == 1
    assert "sorry count is 1; gate requires 0" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path, LOG + "sorry-count: 2\n")) == 1
    assert "sorry count is 2" in capsys.readouterr().err
    assert_nothing_written(repo)


# #82: the fixture's `lake build: ok` line is the only completion evidence it has
UNFINISHED = "".join(x for x in LOG.splitlines(keepends=True) if not x.startswith("lake build: ok"))


def test_unknown_sorry_count_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    """#82: no `sorry-count:` line and no sign the build finished (a cut-short or wrong log) is not a 0."""
    assert "lake build" not in UNFINISHED and "sorry" not in UNFINISHED
    assert run(lp, repo, Job(tmp_path, UNFINISHED)) == 1
    err = capsys.readouterr().err
    assert "no `sorry-count:` line and no build-completion line" in err and "the sorry count is unknown" in err
    assert_nothing_written(repo)
    # the gap is the missing count, so a `lake build: ok` buried mid-line is not completion evidence either
    assert run(lp, repo, Job(tmp_path, UNFINISHED + "note: lake build: ok was never printed\n")) == 1
    assert "the sorry count is unknown" in capsys.readouterr().err
    assert_nothing_written(repo)


@pytest.mark.parametrize("evidence", ["sorry-count: 0\n", "Build completed successfully (3427 jobs).\n",
                                      "lake build: ok\n"])
def test_count_or_completion_line_settles_the_sorry_count(lp, repo, tmp_path, fake_sacct, capsys, evidence):
    """#82: an explicit count, Lake's summary line or the verify script's `lake build: ok` each make 0 a
    measured value, and the receipt is written."""
    assert run(lp, repo, Job(tmp_path, UNFINISHED + evidence)) == 0
    c12 = repo_head(repo)[:12]
    assert json.loads((rdir(repo) / f"{c12}-slurm-123.json").read_text())["sorry_count"] == 0


def test_sorry_warning_without_completion_line_still_reports_the_count(lp, repo, tmp_path, fake_sacct, capsys):
    """A sorry warning in an unfinished log is refused by its count, the more useful message."""
    assert run(lp, repo, Job(tmp_path, UNFINISHED + "warning: Demo.lean:3:8: declaration uses 'sorry'\n")) == 1
    err = capsys.readouterr().err
    assert "sorry count is 1; gate requires 0" in err and "the sorry count is unknown" not in err
    assert_nothing_written(repo)


def test_unknown_commit_and_disagreeing_log_lines_are_refused(lp, repo, tmp_path, fake_sacct, capsys):
    job = Job(tmp_path)
    assert lp.main(["receipt", "slurm", "--job", "123", "--log", str(job.log), "--commit", "f" * 40,
                    "--repo", str(repo), "--checker", CHECKER]) == 1
    assert "not a commit" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path, LOG + "commit: " + "e" * 40 + "\n")) == 1
    assert "`commit:`" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path, LOG + "job-id: 999\n")) == 1
    assert "`job-id:`" in capsys.readouterr().err
    assert run(lp, repo, Job(tmp_path, LOG + f"commit: {repo_head(repo)}\njob-id: slurm-123\n")) == 0


def test_job_given_as_slurm_prefixed(lp, repo, tmp_path, fake_sacct):
    job = Job(tmp_path)
    assert lp.main(["receipt", "slurm", "--job", "slurm-123", "--log", str(job.log), "--commit", repo_head(repo),
                    "--repo", str(repo), "--checker", CHECKER, "--stdout", str(job.out)]) == 0
    assert (rdir(repo) / f"{repo_head(repo)[:12]}-slurm-123.json").is_file()


def test_residual_private_string_is_refused_and_redact_clears_it(lp, repo, tmp_path, fake_sacct, capsys):
    log = LOG + "source: /home/someone/proj/Main.lean\n"
    assert run(lp, repo, Job(tmp_path, log), "--json") == 1
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["ok"] is False and data["receipt"] is None
    assert any("home-path" in r and "redacted verify log" in r for r in data["refusals"])
    assert "/home/someone" not in out + err                  # the private string is never printed
    assert_nothing_written(repo)
    assert run(lp, repo, Job(tmp_path, log), "--redact", r"/home/[^/]+/") == 0
    assert "source: <redacted>proj/Main.lean" in next(rdir(repo).glob("*.log")).read_text()


def test_private_string_in_a_receipt_field_is_refused(lp, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path), "--checker", "verify.sh TODO-private") == 1
    assert "receipt JSON (todo-private)" in capsys.readouterr().err
    assert_nothing_written(repo)


@pytest.mark.parametrize("extra, needle", [
    (("--redact", "("), "not a valid regular expression"),
    (("--tar-sha256", "xyz"), "--tar-sha256 must be 64 hex"),
    (("--stderr", "/nonexistent/job.err"), "no such file"),
])
def test_usage_errors(lp, repo, tmp_path, fake_sacct, capsys, extra, needle):
    assert run(lp, repo, Job(tmp_path), *extra, stderr=False) == 2
    assert needle in capsys.readouterr().err
    assert_nothing_written(repo)


def test_dash_led_commit_is_a_usage_error(lp, repo, tmp_path, fake_sacct, capsys):
    job = Job(tmp_path)
    assert lp.main(["receipt", "slurm", "--job", "123", "--log", str(job.log), "--commit=--all",
                    "--repo", str(repo)]) == 2
    assert "--commit must name a commit" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_out_path_puts_the_log_beside_it(lp, repo, tmp_path, fake_sacct, capsys):
    dest = tmp_path / "elsewhere" / "r.json"
    assert run(lp, repo, Job(tmp_path), "--out", str(dest)) == 0
    c12 = repo_head(repo)[:12]
    assert dest.is_file() and (dest.parent / f"{c12}-slurm-123-verify.redacted.log").is_file()
    assert "next: lean-pkg gate NAME --check --receipt r.json" in capsys.readouterr().out
    assert_nothing_written(repo)


def test_report_does_not_rerun_receipt_slurm(lp):
    from lean_pkg import report
    lp.main(["llms"])   # builds the parser, so report knows the subcommands
    plan = report.plan_rerun(["receipt", "slurm", "--job", "1", "--log", "x", "--commit", "abc"])
    assert plan["argv"] is None and "receipt slurm writes" in plan["skip"]


def test_seal_evidence_is_persisted_with_stdout(lp, env, repo, tmp_path, fake_sacct, capsys):
    """#72: with --stdout the README block names the job stdout and manifest SHA-256 and the receipt carries
    them as additive keys, which gate ignores (check and a real gate on the scratch registry both pass)."""
    job = Job(tmp_path)
    assert run(lp, repo, job, "--append-readme", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    out_sha, man_sha, raw_sha = sha(job.out.read_bytes()), sha(MATHLIB_MANIFEST.encode()), sha(job.log.read_bytes())
    assert data["seal"] == {"job_stdout_sha256": out_sha, "manifest_sha256": man_sha}
    line = (f"- Seal: job stdout SHA-256 `{out_sha}` carries verify-log-sha256 = raw log SHA-256 `{raw_sha}`; "
            f"lake-manifest.json SHA-256 `{man_sha}` (pre = post).")
    assert line in data["readme_block"].splitlines()
    assert line in (rdir(repo) / "README.md").read_text().splitlines()
    receipt = json.loads(Path(data["receipt"]).read_text())
    assert receipt["job_stdout_sha256"] == out_sha and receipt["manifest_sha256"] == man_sha
    reg = ["--registry", str(env.registry)]
    assert lp.main(reg + ["gate", "alpha", "--check", "--receipt", data["receipt"]]) == 0
    assert "receipt valid at commit" in capsys.readouterr().out
    assert lp.main(reg + ["gate", "alpha", "--receipt", data["receipt"]]) == 0


def test_no_seal_evidence_without_stdout(lp, repo, tmp_path, fake_sacct, capsys):
    raw = LOG.encode()
    sealed = (raw + f"verify-log-sha256: {sha(raw)}\n".encode()).decode()
    job = Job(tmp_path, sealed)
    assert run(lp, repo, job, "--json", stdout=False) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["seal"] is None
    assert "- Seal:" not in data["readme_block"]
    assert sha(job.out.read_bytes()) not in data["readme_block"]
    assert sha(MATHLIB_MANIFEST.encode()) not in data["readme_block"]
    receipt = json.loads(Path(data["receipt"]).read_text())
    assert list(receipt) == list(lp.RECEIPT_FIELDS)


def test_help_names_checker_requirement_and_redaction_tokens(lp, capsys):
    with pytest.raises(SystemExit):
        lp.main(["receipt", "slurm", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "when it has no `checker:` line, --checker TEXT is required" in out
    assert "replace matches with <redacted>" in out
    assert "redacted to <USER> instead (/tmp/<USER>-job, /scratch/<USER>/)" in out


@pytest.mark.parametrize("sacct", [
    # field report: saved with --format=JobID,JobName,Partition,... -P -n, so State/ExitCode were misread
    "123|replay-v3-gate|std_queue|COMPLETED|0:0\n123.batch|batch|std_queue|COMPLETED|0:0\n",
    "123|COMPLETED|00:01:00|0:0\n",                               # columns swapped: ExitCode not N:N
])
def test_other_sacct_columns_without_a_header_are_an_unexpected_format(lp, repo, tmp_path, fake_sacct, capsys,
                                                                       sacct):
    fake_sacct.write_text(sacct)
    assert run(lp, repo, Job(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "unexpected sacct format" in err and "not a clean finish" not in err
    assert "header line naming JobID, State and ExitCode" in err
    assert_nothing_written(repo)


def test_a_header_line_locates_the_columns_in_any_order(lp, repo, tmp_path, fake_sacct, capsys):
    fake_sacct.write_text("JobID|JobName|Partition|ExitCode|State|Elapsed\n"
                          "123|replay-v3-gate|std_queue|0:0|COMPLETED|00:02:00\n"
                          "123.batch|batch|std_queue|0:0|COMPLETED|00:02:00\n")
    assert run(lp, repo, Job(tmp_path), "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sacct"]["state"] == "COMPLETED" and payload["sacct"]["exit_code"] == "0:0"
    assert payload["sacct"]["elapsed"] == "00:02:00" and payload["sacct"]["total_cpu"] is None


def test_a_header_line_still_refuses_an_unclean_finish(lp, repo, tmp_path, fake_sacct, capsys):
    fake_sacct.write_text("JobID|JobName|State|ExitCode\n123|replay|FAILED|1:0\n")
    assert run(lp, repo, Job(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "not a clean finish (State=FAILED, ExitCode=1:0)" in err
    assert_nothing_written(repo)


def test_json_sealed_lists_the_receipt_and_the_redacted_log(lp, repo, tmp_path, fake_sacct, capsys):
    import hashlib
    assert run(lp, repo, Job(tmp_path), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert [e["role"] for e in data["sealed"]] == ["receipt", "redacted-log"]
    receipt = json.loads(Path(data["sealed"][0]["path"]).read_text())
    for e in data["sealed"]:
        assert hashlib.sha256(Path(e["path"]).read_bytes()).hexdigest() == e["sha256"]
    assert data["sealed"][1]["receipt_key"] == "report_sha256" and receipt["report_sha256"] == data["sealed"][1]["sha256"]
    assert data["sealed"][1]["path"] == data["log"] and data["sealed"][0]["path"] == data["receipt"]
