#!/usr/bin/env python3
"""#70: `receipt slurm` turns a finished Slurm job and its sealed verify log into a lower-tier receipt
that `gate --check` accepts; every refusal writes nothing. sacct is a fake on PATH; no registry is read."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from .conftest import repo_head

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "slurm"
SACCT_OK = (FIXTURES / "sacct-completed.txt").read_text()


def seal(body: str) -> bytes:
    """The verify script's rule: hash the log, then append the verify-log-sha256 line."""
    raw = body.encode()
    return raw + f"verify-log-sha256: {hashlib.sha256(raw).hexdigest()}\n".encode()


@pytest.fixture()
def fake_sacct(tmp_path, monkeypatch):
    """A `sacct` on PATH that prints the text written to .output (and records its argv)."""
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    out = bindir / "output.txt"
    out.write_text(SACCT_OK)
    script = bindir / "sacct"
    script.write_text(f'#!/bin/sh\necho "$@" > "{bindir}/argv.txt"\ncat "{out}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return out


def write_log(tmp_path, body: str | None = None, sealed: bytes | None = None) -> Path:
    p = tmp_path / "verify.log"
    p.write_bytes(sealed if sealed is not None else seal(body if body is not None
                                                         else (FIXTURES / "verify.log").read_text()))
    return p


def run(lp, env, log, *extra, job="123"):
    return lp.main(["receipt", "slurm", "--job", job, "--log", str(log), "--commit", repo_head(env.repo_a),
                    "--repo", str(env.repo_a), *extra])


def receipts_dir(env) -> Path:
    return env.repo_a / ".lean-receipts"


def assert_nothing_written(env):
    assert not receipts_dir(env).exists() or list(receipts_dir(env).iterdir()) == []


def test_happy_path_receipt_passes_gate_check(lp, env, tmp_path, fake_sacct, capsys):
    before = env.registry.read_bytes()
    assert run(lp, env, write_log(tmp_path)) == 0
    out = capsys.readouterr().out
    c12 = repo_head(env.repo_a)[:12]
    rp, lg = receipts_dir(env) / f"{c12}-123.json", receipts_dir(env) / f"{c12}-123.verify.log"
    assert out.splitlines()[0].startswith("receipt slurm: ok, job 123")
    assert f"- Lower-tier receipt: [`.lean-receipts/{c12}-123.json`]" in out
    assert f"next: lean-pkg gate NAME --check --receipt .lean-receipts/{c12}-123.json" in out
    receipt = json.loads(rp.read_text())
    assert sorted(receipt) == sorted(lp.RECEIPT_FIELDS)
    assert receipt["report_sha256"] == hashlib.sha256(lg.read_bytes()).hexdigest()
    assert receipt["job_id"] == "123" and receipt["sorry_count"] == 0
    assert receipt["axioms"] == ["propext", "Quot.sound", "Classical.choice"]
    assert receipt["checker"] == "lake build + leanchecker (serial manifest)"
    log_text = lg.read_text()
    assert "/tmp/user-job123/" not in log_text and "/scratch/user/" not in log_text
    assert "/tmp/<job-tmp>/checkout" in log_text and "/scratch/<user>/lake-cache" in log_text
    argv = (fake_sacct.parent / "argv.txt").read_text().split()
    assert argv == ["-j", "123", "--format=JobID,State,ExitCode", "-P", "-n"]
    assert env.registry.read_bytes() == before                # receipt slurm never touches the registry
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


def test_json_payload(lp, env, tmp_path, fake_sacct, capsys):
    assert run(lp, env, write_log(tmp_path), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["refusals"] == [] and data["job_id"] == "123"
    assert Path(data["receipt"]).is_file() and Path(data["log"]).is_file()
    assert data["report_sha256"] == hashlib.sha256(Path(data["log"]).read_bytes()).hexdigest()
    assert data["sacct"] == {"job_id": "123", "state": "COMPLETED", "exit_code": "0:0"}
    assert data["redactions"] == 3


def test_rerun_is_idempotent_and_differing_file_is_refused(lp, env, tmp_path, fake_sacct, capsys):
    body = (FIXTURES / "verify.log").read_text() + "generated-at: 2026-10-10T12:00:00+00:00\n"
    log = write_log(tmp_path, body)
    assert run(lp, env, log) == 0
    assert run(lp, env, log) == 0
    rp = next(receipts_dir(env).glob("*-123.json"))
    rp.write_text("{}")
    capsys.readouterr()
    assert run(lp, env, log) == 1
    assert "already exists with different content" in capsys.readouterr().err


@pytest.mark.parametrize("sacct, needle", [
    ("123|FAILED|1:0\n123.batch|FAILED|1:0\n", "State=FAILED"),
    ("123|COMPLETED|1:0\n", "ExitCode=1:0"),
    ("123|CANCELLED by 42|0:15\n", "State=CANCELLED by 42"),
    ("124|COMPLETED|0:0\n123.batch|COMPLETED|0:0\n", "no main job line"),
])
def test_unclean_job_is_refused(lp, env, tmp_path, fake_sacct, capsys, sacct, needle):
    fake_sacct.write_text(sacct)
    assert run(lp, env, write_log(tmp_path)) == 1
    err = capsys.readouterr().err
    assert needle in err and "nothing written" in err.splitlines()[0]
    assert_nothing_written(env)


def no_sacct(monkeypatch):
    """No sacct on this host (a real one, on a cluster node, is never run by the suite)."""
    from lean_pkg import receipt
    real = receipt.shutil.which
    monkeypatch.setattr(receipt.shutil, "which", lambda name, *a, **k: None if name == "sacct" else real(name, *a, **k))


def test_no_sacct_on_path_names_the_sacct_file_route(lp, env, tmp_path, monkeypatch, capsys):
    no_sacct(monkeypatch)
    assert run(lp, env, write_log(tmp_path)) == 1
    assert "--sacct-file" in capsys.readouterr().err
    assert_nothing_written(env)


def test_sacct_file_replaces_sacct(lp, env, tmp_path, monkeypatch, capsys):
    no_sacct(monkeypatch)
    saved = tmp_path / "sacct.txt"
    saved.write_text(SACCT_OK)
    assert run(lp, env, write_log(tmp_path), "--sacct-file", str(saved)) == 0
    saved.write_text("123|TIMEOUT|0:0\n")
    for p in receipts_dir(env).iterdir():
        p.unlink()
    assert run(lp, env, write_log(tmp_path), "--sacct-file", str(saved)) == 1
    assert "State=TIMEOUT" in capsys.readouterr().err
    assert_nothing_written(env)


def test_hash_mismatch_is_refused(lp, env, tmp_path, fake_sacct, capsys):
    sealed = seal((FIXTURES / "verify.log").read_text()).replace(b"result: pass", b"result: PASS")
    assert run(lp, env, write_log(tmp_path, sealed=sealed)) == 1
    assert "changed after it was sealed" in capsys.readouterr().err
    assert_nothing_written(env)


def test_missing_or_duplicate_hash_line_is_refused(lp, env, tmp_path, fake_sacct, capsys):
    body = (FIXTURES / "verify.log").read_text()
    assert run(lp, env, write_log(tmp_path, sealed=body.encode())) == 1
    assert "no `verify-log-sha256: <64 hex>` line" in capsys.readouterr().err
    sealed = seal(body)
    assert run(lp, env, write_log(tmp_path, sealed=sealed + sealed.splitlines(keepends=True)[-1])) == 1
    assert "exactly one is allowed" in capsys.readouterr().err
    assert_nothing_written(env)


def test_hash_line_anywhere_is_excluded(lp, env, tmp_path, fake_sacct):
    body = (FIXTURES / "verify.log").read_text()
    digest = hashlib.sha256(body.encode()).hexdigest()
    lines = body.splitlines(keepends=True)
    sealed = "".join(lines[:2] + [f"verify-log-sha256: {digest}\n"] + lines[2:]).encode()
    assert run(lp, env, write_log(tmp_path, sealed=sealed)) == 0


@pytest.mark.parametrize("key", ["lean-toolchain", "mathlib-rev", "checker", "sorry-count", "axioms"])
def test_missing_field_is_refused(lp, env, tmp_path, fake_sacct, capsys, key):
    body = "".join(ln for ln in (FIXTURES / "verify.log").read_text().splitlines(keepends=True)
                   if not ln.startswith(key + ":"))
    assert run(lp, env, write_log(tmp_path, body)) == 1
    assert f"no `{key}:` line" in capsys.readouterr().err
    assert_nothing_written(env)


def test_nonzero_sorry_count_is_refused(lp, env, tmp_path, fake_sacct, capsys):
    body = (FIXTURES / "verify.log").read_text().replace("sorry-count: 0", "sorry-count: 2")
    assert run(lp, env, write_log(tmp_path, body)) == 1
    assert "sorry-count is 2; gate requires 0" in capsys.readouterr().err
    assert_nothing_written(env)


def test_toolchain_mismatch_is_refused(lp, env, tmp_path, fake_sacct, capsys):
    body = (FIXTURES / "verify.log").read_text().replace("v4.32.0", "v4.33.0")
    assert run(lp, env, write_log(tmp_path, body)) == 1
    assert "toolchain mismatch" in capsys.readouterr().err
    assert_nothing_written(env)


def test_unknown_commit_and_disagreeing_commit_line_are_refused(lp, env, tmp_path, fake_sacct, capsys):
    log = write_log(tmp_path)
    assert lp.main(["receipt", "slurm", "--job", "123", "--log", str(log), "--commit", "f" * 40,
                    "--repo", str(env.repo_a)]) == 1
    assert "not a commit" in capsys.readouterr().err
    body = (FIXTURES / "verify.log").read_text() + "commit: " + "e" * 40 + "\n"
    assert run(lp, env, write_log(tmp_path, body)) == 1
    assert "`commit:`" in capsys.readouterr().err
    body = (FIXTURES / "verify.log").read_text() + f"commit: {repo_head(env.repo_a)}\njob-id: 123\n"
    assert run(lp, env, write_log(tmp_path, body)) == 0


def test_residual_private_string_is_refused_and_redact_clears_it(lp, env, tmp_path, fake_sacct, capsys):
    body = (FIXTURES / "verify.log").read_text() + "source: /home/someone/proj/Main.lean\n"
    log = write_log(tmp_path, body)
    assert run(lp, env, log, "--json") == 1
    out, err = capsys.readouterr()
    data = json.loads(out)
    assert data["ok"] is False and data["receipt"] is None
    assert any("home-path" in r and "redacted verify log" in r for r in data["refusals"])
    assert "/home/someone" not in out + err                  # the private string is never printed
    assert_nothing_written(env)
    assert run(lp, env, log, "--redact", r"/home/[^/]+/") == 0
    lg = next(receipts_dir(env).glob("*.verify.log")).read_text()
    assert "source: <redacted>proj/Main.lean" in lg


def test_private_string_in_a_receipt_field_is_refused(lp, env, tmp_path, fake_sacct, capsys):
    body = (FIXTURES / "verify.log").read_text().replace("(serial manifest)", "(TODO-private manifest)")
    assert run(lp, env, write_log(tmp_path, body)) == 1
    err = capsys.readouterr().err
    assert "receipt JSON (todo-private)" in err
    assert_nothing_written(env)


def test_bad_redact_regex_is_a_usage_error(lp, env, tmp_path, fake_sacct, capsys):
    assert run(lp, env, write_log(tmp_path), "--redact", "(") == 2
    assert "not a valid regular expression" in capsys.readouterr().err
    assert_nothing_written(env)


def test_out_path_puts_the_log_beside_it(lp, env, tmp_path, fake_sacct, capsys):
    dest = tmp_path / "elsewhere" / "r.json"
    assert run(lp, env, write_log(tmp_path), "--out", str(dest)) == 0
    c12 = repo_head(env.repo_a)[:12]
    assert dest.is_file() and (dest.parent / f"{c12}-123.verify.log").is_file()
    out = capsys.readouterr().out
    assert "[`r.json`](r.json)" in out                        # outside the repo: the file name only
    assert_nothing_written(env)


def test_report_does_not_rerun_receipt_slurm(lp):
    from lean_pkg import report
    lp.main(["llms"])   # builds the parser, so report knows the subcommands
    plan = report.plan_rerun(["receipt", "slurm", "--job", "1", "--log", "x", "--commit", "abc"])
    assert plan["argv"] is None and "receipt slurm writes" in plan["skip"]


def test_dash_led_commit_is_a_usage_error(lp, env, tmp_path, fake_sacct, capsys):
    log = write_log(tmp_path)
    assert lp.main(["receipt", "slurm", "--job", "123", "--log", str(log), "--commit=--all",
                    "--repo", str(env.repo_a)]) == 2
    assert "--commit must name a commit" in capsys.readouterr().err
    assert_nothing_written(env)
