#!/usr/bin/env python3
"""`receipt slurm --seal-sacct` (LO-20261019-06): a lower-tier receipt read from `--sacct-file` rests on a file
any shell can write. With the flag the job's accounting record is sealed into the receipt exactly as
`receipt comparator --seal-sacct` seals a comparator receipt (the same checks, the same normalized record,
the same `slurm_sacct` block, schema slurm-sacct-seal/1), and every validator of a lower-tier receipt
(core.validate_receipt: gate, gate --check, receipt check, ...) refuses a present block whose record is
missing, uncommitted, re-hashed or edited, through receipt.sacct_seal_problem. A receipt without the block
passes as before."""

from __future__ import annotations

import hashlib
import json

import pytest

from .conftest import _git, repo_head
from .test_receipt_slurm import Job, LOG, assert_nothing_written, fake_sacct, rdir, repo, run  # noqa: F401
from .test_receipt_slurm import _fixed_identity  # noqa: F401  (autouse: pinned user and host names)

JOB = "123"
RUN_AT = "2026-10-05T12:00:00+00:00"
SEALED_LOG = LOG + f"generated-at: {RUN_AT}\n"
HEADER = "JobID|State|ExitCode|Start|End|Elapsed|TotalCPU|User|NodeList"


def sacct_lines(job=JOB, start="2026-10-05T06:30:00-0500", end="2026-10-05T07:30:00-0500", state="COMPLETED",
                code="0:0", user="someone", header=HEADER):
    rows = [f"{job}|{state}|{code}|{start}|{end}|01:00:00|01:10:22|{user}|node-1",
            f"{job}.batch|{state}|{code}|{start}|{end}|01:00:00|01:10:20||node-1"]
    return "\n".join(([header] if header else []) + rows) + "\n"


def sacct_file(tmp_path, text=None, name="job.sacct"):
    p = tmp_path / name
    p.write_text(sacct_lines() if text is None else text)
    return p


def seal_run(lp, repo, tmp_path, *extra, log=SEALED_LOG, sacct=None):
    return run(lp, repo, Job(tmp_path, log), "--seal-sacct", "--sacct-file", str(sacct or sacct_file(tmp_path)),
               *extra)


def receipt_path(repo):
    return rdir(repo) / f"{repo_head(repo)[:12]}-slurm-{JOB}.json"


def gate_check(lp, env, rp):
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)])


def commit_evidence(repo, msg="evidence"):
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", msg)


# --- writing the seal -------------------------------------------------------------------------------------

def test_seal_writes_the_normalized_record_and_binds_it(lp, repo, tmp_path, capsys):
    from lean_pkg import receipt
    c12 = repo_head(repo)[:12]
    assert seal_run(lp, repo, tmp_path, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    rp = receipt_path(repo)
    rec = json.loads(rp.read_text())
    block = rec["slurm_sacct"]
    rec_file = rdir(repo) / f"{c12}-slurm-{JOB}-sacct.txt"
    body = rec_file.read_bytes()
    assert block["schema"] == "slurm-sacct-seal/1" and block["job"] == JOB
    assert block["file"] == f".lean-receipts/{rec_file.name}"
    assert block["sha256"] == hashlib.sha256(body).hexdigest()
    assert block["start"] == "2026-10-05T11:30:00+00:00" and block["end"] == "2026-10-05T12:30:00+00:00"
    assert block["state"] == "COMPLETED" and block["exit_code"] == "0:0" and block["elapsed"] == "01:00:00"
    assert block["checks"] == {"user": "not checked", "node": "not checked"} and block["source"] == "sacct-file"
    assert body.decode() == ("JobID|State|ExitCode|Start|End|Elapsed\n"
                             f"{JOB}|COMPLETED|0:0|2026-10-05T11:30:00+00:00|2026-10-05T12:30:00+00:00|01:00:00\n")
    assert b"someone" not in body and b"node-1" not in body and "someone" not in rp.read_text()
    assert receipt.sacct_seal_problem(rec, body) is None          # the comparator's validator, reused
    # JSON: additive keys only; the record is listed in sealed[] with the key that binds it
    assert data["ok"] and data["slurm_sacct"] == block
    assert "user" not in data["sacct"] and "nodelist" not in data["sacct"]   # checked, never printed
    assert data["sacct"]["total_cpu"] == "01:10:22"
    roles = {s["role"]: s for s in data["sealed"]}
    assert roles["sacct.txt"] == {"role": "sacct.txt", "path": str(rec_file), "sha256": block["sha256"],
                                  "receipt_key": "slurm_sacct.sha256"}
    assert {"receipt", "redacted-log", "sacct.txt"} == set(roles)
    assert any(c.startswith("✓ sacct seal: job 123 COMPLETED") for c in data["checks"])
    assert f"- Seal: sacct record `{rec_file.name}`, SHA-256 `{block['sha256']}`" in data["readme_block"]
    assert any("gate checks the sealed sacct record committed at HEAD" in c for c in data["checks"])


def test_identical_rerun_is_a_no_op_and_another_record_is_refused(lp, repo, tmp_path, capsys):
    assert seal_run(lp, repo, tmp_path) == 0
    rp = receipt_path(repo)
    first = rp.read_bytes()
    assert seal_run(lp, repo, tmp_path) == 0
    assert rp.read_bytes() == first
    capsys.readouterr()
    other = sacct_file(tmp_path, sacct_lines(end="2026-10-05T08:00:00-0500"), name="other.sacct")
    assert seal_run(lp, repo, tmp_path, sacct=other) == 1
    assert "already exists with different content" in capsys.readouterr().err
    assert rp.read_bytes() == first


def test_text_output_names_the_seal_and_the_readme_line(lp, repo, tmp_path, capsys):
    assert seal_run(lp, repo, tmp_path, "--append-readme") == 0
    out = capsys.readouterr().out
    assert "✓ sacct seal: job 123 COMPLETED, exit 0:0, 2026-10-05T11:30:00+00:00 .. 2026-10-05T12:30:00+00:00" in out
    assert f"evidence    .lean-receipts/{repo_head(repo)[:12]}-slurm-{JOB}-sacct.txt" in out
    readme = (rdir(repo) / "README.md").read_text()
    assert "- Seal: sacct record `" in readme and "(= `slurm_sacct.sha256`, slurm-sacct-seal/1)" in readme


@pytest.mark.parametrize("text,why", [
    (sacct_lines(job="999"), "no main job line for job 123"),                        # a record of another job
    (sacct_lines(start="2026-10-05T08:00:00-0500", end="2026-10-05T09:00:00-0500"),
     "does not contain the verify run"),                                              # another time
    (sacct_lines(state="FAILED", code="1:0"), "is not a clean finish"),
    (sacct_lines(state="RUNNING"), "is not a clean finish"),                         # sealed while running
    (sacct_lines(start="2026-10-05T06:30:00", end="2026-10-05T07:30:00"), "no zoned Start/End"),
    ("123|COMPLETED|0:0|00:12:05|01:10:22\n", "no zoned Start/End"),                 # the plain 5-column file
])
def test_a_record_that_does_not_corroborate_the_run_is_refused(lp, repo, tmp_path, capsys, text, why):
    assert seal_run(lp, repo, tmp_path, sacct=sacct_file(tmp_path, text)) == 1
    err = capsys.readouterr().err
    assert why in err
    assert_nothing_written(repo)


def test_a_log_without_generated_at_cannot_be_sealed(lp, repo, tmp_path, capsys):
    assert seal_run(lp, repo, tmp_path, log=LOG) == 1
    assert "--seal-sacct: the verify log has no `generated-at:` line" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_the_account_is_checked_when_named(lp, repo, tmp_path, capsys):
    assert seal_run(lp, repo, tmp_path, "--user", "another") == 1
    assert "ran under another account than --user" in capsys.readouterr().err
    assert_nothing_written(repo)
    assert seal_run(lp, repo, tmp_path, "--user", "someone") == 0
    assert json.loads(receipt_path(repo).read_text())["slurm_sacct"]["checks"]["user"] == "matched"


def test_live_sacct_runs_the_seal_query(lp, repo, tmp_path, fake_sacct, capsys):
    """Without --sacct-file the seal asks sacct itself, with a header and zoned times, and checks this account."""
    fake_sacct.write_text(sacct_lines(user="idtester"))
    assert run(lp, repo, Job(tmp_path, SEALED_LOG), "--seal-sacct") == 0
    argv = (fake_sacct.parent / "argv.txt").read_text().split()
    assert argv == ["-j", JOB, "--format=JobID,State,ExitCode,Start,End,Elapsed,TotalCPU,User,NodeList", "-P"]
    block = json.loads(receipt_path(repo).read_text())["slurm_sacct"]
    assert block["source"] == "sacct" and block["checks"]["user"] == "matched"
    assert "TotalCPU 01:10:22." in capsys.readouterr().out
    fake_sacct.write_text(sacct_lines(user="someone-else"))
    (tmp_path / "again").mkdir()
    assert run(lp, repo, Job(tmp_path / "again", SEALED_LOG), "--seal-sacct", "--out",
               str(rdir(repo) / "other.json")) == 1
    assert "ran under another account than this one" in capsys.readouterr().err


@pytest.mark.parametrize("extra,needle", [
    (["--user", "someone"], "--user goes with --seal-sacct"),
    (["--seal-sacct", "--check"], "--seal-sacct seals the receipt this command writes; not with --check"),
    (["--seal-sacct", "--template"], "not with --template"),
    (["--seal-sacct", "--out", "/elsewhere/r.json"], "--out must name a file in that directory"),
])
def test_flag_misuse_is_a_usage_error(lp, repo, tmp_path, capsys, extra, needle):
    assert run(lp, repo, Job(tmp_path, SEALED_LOG), "--sacct-file", str(sacct_file(tmp_path)), *extra) == 2
    assert needle in capsys.readouterr().err
    assert_nothing_written(repo)


def test_a_job_that_is_not_a_number_cannot_be_sealed(lp, repo, tmp_path, capsys):
    argv = ["receipt", "slurm", "--job", "abc", "--log", str(Job(tmp_path, SEALED_LOG).log), "--commit",
            repo_head(repo), "--repo", str(repo), "--seal-sacct", "--sacct-file", str(sacct_file(tmp_path))]
    assert lp.main(argv) == 2
    assert "--seal-sacct needs a Slurm job number" in capsys.readouterr().err


# --- the gate check ---------------------------------------------------------------------------------------

def test_gate_check_needs_the_sealed_record_committed(lp, env, repo, tmp_path, capsys):
    assert seal_run(lp, repo, tmp_path) == 0
    rp = receipt_path(repo)
    capsys.readouterr()
    assert gate_check(lp, env, rp) == 1                           # written, not yet committed
    assert "sacct seal record missing or changed: slurm_sacct.file .lean-receipts/" in capsys.readouterr().err
    commit_evidence(repo)
    assert gate_check(lp, env, rp) == 0
    out = capsys.readouterr().out
    assert "✓ slurm_sacct: job 123 COMPLETED, exit 0:0" in out and "is committed at HEAD" in out


def _sealed_and_committed(lp, env, repo, tmp_path, capsys):
    assert seal_run(lp, repo, tmp_path) == 0
    rp = receipt_path(repo)                                       # named for the verified commit
    commit_evidence(repo)
    assert gate_check(lp, env, rp) == 0
    capsys.readouterr()
    return rp, next(rdir(repo).glob("*-sacct.txt"))


def test_gate_refuses_an_edited_record(lp, env, repo, tmp_path, capsys):
    rp, rec_file = _sealed_and_committed(lp, env, repo, tmp_path, capsys)
    rec_file.write_text(rec_file.read_text().replace("12:30:00", "12:45:00"))
    commit_evidence(repo, "edit the record")
    assert gate_check(lp, env, rp) == 1
    assert "does not hash to slurm_sacct.sha256" in capsys.readouterr().err


def test_gate_refuses_a_removed_record(lp, env, repo, tmp_path, capsys):
    rp, rec_file = _sealed_and_committed(lp, env, repo, tmp_path, capsys)
    _git(repo, "rm", "-q", str(rec_file))
    _git(repo, "commit", "-q", "-m", "drop the record")
    assert gate_check(lp, env, rp) == 1
    assert "is not committed at HEAD" in capsys.readouterr().err


def test_gate_checks_the_committed_bytes_not_the_working_tree(lp, env, repo, tmp_path, capsys):
    rp, rec_file = _sealed_and_committed(lp, env, repo, tmp_path, capsys)
    rec_file.write_text("JobID|State|ExitCode\n")                 # an uncommitted edit changes nothing
    assert gate_check(lp, env, rp) == 0


def test_gate_refuses_a_rehashed_block(lp, env, repo, tmp_path, capsys):
    """Editing the record and re-hashing the block to match still fails: the block's fields must be what the
    record says, and the record must contain the run."""
    rp, rec_file = _sealed_and_committed(lp, env, repo, tmp_path, capsys)
    rec = json.loads(rp.read_text())
    edited = rec_file.read_text().replace("12:30:00+00:00", "12:45:00+00:00")
    rec_file.write_text(edited)
    rec["slurm_sacct"]["sha256"] = hashlib.sha256(edited.encode()).hexdigest()
    rp.write_text(json.dumps(rec, indent=2) + "\n")
    commit_evidence(repo, "rehash")
    assert gate_check(lp, env, rp) == 1
    assert "slurm_sacct's fields differ from the sealed record" in capsys.readouterr().err
    # a block re-pointed at a window that no longer contains the run (record and block edited together)
    moved = rec_file.read_text().replace("2026-10-05T11:30:00", "2026-10-05T12:10:00").replace(
        "12:45:00+00:00", "13:00:00+00:00")
    rec_file.write_text(moved)
    rec["slurm_sacct"].update(sha256=hashlib.sha256(moved.encode()).hexdigest(),
                              start="2026-10-05T12:10:00+00:00", end="2026-10-05T13:00:00+00:00")
    rp.write_text(json.dumps(rec, indent=2) + "\n")
    commit_evidence(repo, "move")
    assert gate_check(lp, env, rp) == 1
    assert "does not contain the verify run" in capsys.readouterr().err


@pytest.mark.parametrize("edit,why", [
    (lambda b: b.update(job="999"), "slurm_sacct seals job '999', the receipt claims job 123"),
    (lambda b: b.update(file="../x-sacct.txt"), "must be under .lean-receipts/"),
    (lambda b: b.pop("sha256"), "is not a slurm-sacct-seal/1 block"),
    (lambda b: b.update(start="2026-10-05T11:00:00+00:00"), "fields differ from the sealed record"),
])
def test_gate_refuses_an_edited_block(lp, env, repo, tmp_path, capsys, edit, why):
    rp, _f = _sealed_and_committed(lp, env, repo, tmp_path, capsys)
    rec = json.loads(rp.read_text())
    edit(rec["slurm_sacct"])
    rp.write_text(json.dumps(rec, indent=2) + "\n")
    assert gate_check(lp, env, rp) == 1
    assert why in capsys.readouterr().err


def test_a_null_or_malformed_block_is_refused_not_ignored(lp, env, repo, tmp_path, capsys):
    rp, _f = _sealed_and_committed(lp, env, repo, tmp_path, capsys)
    rec = json.loads(rp.read_text())
    rec["slurm_sacct"] = None
    rp.write_text(json.dumps(rec, indent=2) + "\n")
    assert gate_check(lp, env, rp) == 1
    assert "no sacct seal" in capsys.readouterr().err


def test_a_receipt_without_the_block_still_passes(lp, env, repo, tmp_path, fake_sacct, capsys):
    assert run(lp, repo, Job(tmp_path, SEALED_LOG), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert "slurm_sacct" not in data and [s["role"] for s in data["sealed"]] == ["receipt", "redacted-log"]
    rec = json.loads(receipt_path(repo).read_text())
    assert "slurm_sacct" not in rec and not list(rdir(repo).glob("*-sacct.txt"))
    assert gate_check(lp, env, receipt_path(repo)) == 0
    assert "slurm_sacct" not in capsys.readouterr().out


def test_the_validator_reads_a_lower_tier_seal(tmp_path):
    """receipt.sacct_seal_problem knows a lower-tier receipt by its job_id slurm-J; a comparator receipt keeps
    its own claim rule (a comparator receipt with job_id slurm-J claims nothing)."""
    from lean_pkg import receipt
    rec = {"job_id": f"slurm-{JOB}", "generated_at": RUN_AT}
    seen, why = receipt.check_job(JOB, sacct_lines())
    assert why is None
    block, body = receipt.sacct_seal_record(JOB, rec, seen, live=False, user=None, rel=".lean-receipts/x-sacct.txt",
                                            kind="lower-tier")
    rec["slurm_sacct"] = block
    assert receipt.sacct_seal_problem(rec, body) is None
    assert "not committed at HEAD" in receipt.sacct_seal_problem(rec, None)
    assert receipt.lower_slurm_job({"job_id": "slurm-12_3"}) == "12_3"
    assert receipt.lower_slurm_job({"job_id": "systemd-user:u:i@h-0"}) is None
    comp = {"schema": "comparator-receipt/2", "job_id": f"slurm-{JOB}", "slurm_sacct": block,
            "generated_at": RUN_AT}
    assert "does not claim a Slurm job" in receipt.sacct_seal_problem(comp, body)


# --- receipt check (the outside referee, from git alone) --------------------------------------------------

def _seal_into(rec: dict, repo) -> dict:
    """make_receipt's lower-tier receipt with a slurm_sacct seal whose record is written under .lean-receipts/
    (committed by publish_fixture with the receipt)."""
    from lean_pkg import receipt
    job = rec["job_id"].removeprefix("slurm-")
    seen, _why = receipt.check_job(job, sacct_lines(job=job, start="2026-09-18T23:30:00+00:00",
                                                    end="2026-09-19T00:30:00+00:00"))
    name = f"{rec['commit'][:12]}-{rec['job_id']}-sacct.txt"
    block, body = receipt.sacct_seal_record(job, rec, seen, live=False, user=None, rel=f".lean-receipts/{name}",
                                            kind="lower-tier")
    (repo / ".lean-receipts" / name).write_bytes(body)
    return {**rec, "slurm_sacct": block}


@pytest.fixture()
def sealed_publish(monkeypatch):
    from . import test_receipt_check as trc
    real = trc.make_receipt
    monkeypatch.setattr(trc, "make_receipt", lambda repo, **kw: _seal_into(real(repo, **kw), repo))
    return trc


def test_receipt_check_verifies_the_seal_from_git_alone(lp, env, capsys, tmp_path, sealed_publish):
    trc = sealed_publish
    x = trc.publish_fixture(lp, env, capsys)
    rc, payload = trc.check(lp, capsys, trc.referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 0, trc.failed(payload)
    rows = {c["id"]: c for c in payload["checks"]}
    row = next(v for k, v in rows.items() if k.endswith(": sacct seal"))
    assert row["verdict"] == "pass" and "job 12345 COMPLETED" in row["detail"] and "at the ref" in row["detail"]


def test_receipt_check_fails_an_edited_seal(lp, env, capsys, tmp_path, sealed_publish):
    trc = sealed_publish
    x = trc.publish_fixture(lp, env, capsys)
    rec_file = next((env.repo_b / ".lean-receipts").glob("*-sacct.txt"))
    rec_file.write_text(rec_file.read_text().replace("00:30:00+00:00", "00:45:00+00:00"))
    commit_evidence(env.repo_b, "edit the record")
    rc, payload = trc.check(lp, capsys, trc.referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 1
    assert any("sacct seal record missing or changed: slurm_sacct.file" in f and "does not hash" in f
               for f in trc.failed(payload))
