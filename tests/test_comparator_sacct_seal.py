#!/usr/bin/env python3
"""Soundness: a comparator receipt's Slurm claim (job_id J, host_policy `Slurm job J ...`) comes from
SLURM_JOB_ID, which any shell can set. It backs a coverage.yaml compute job (the Easley sentence) only once
`comparator-receipt --seal-sacct R` corroborated it with the job's sacct record (COMPLETED, 0:0, a window
containing the run, the same account and node where known), sealed into R by its SHA-256. The forged claim
alone, or a record of another job or another time, never backs it; the receipt itself stays valid."""

from __future__ import annotations

import json

import pytest

from .conftest import _git, read_reg
from .test_page import page_json, prepare, run

JOB = "5581013"
RUN_AT = "2026-10-05T12:00:00+00:00"            # make_comparator_receipt's generated_at
HEADER = "JobID|State|ExitCode|Start|End|Elapsed|User|NodeList"


def sacct(tmp_path, *lines, header=HEADER, name="sacct.txt"):
    p = tmp_path / name
    p.write_text("\n".join(([header] if header else []) + list(lines)) + "\n")
    return p


def ok_lines(job=JOB, start="2026-10-05T06:30:00-0500", end="2026-10-05T07:30:00-0500", state="COMPLETED",
             code="0:0", node="node-1"):
    return (f"{job}|{state}|{code}|{start}|{end}|01:00:00|someone|{node}",
            f"{job}.batch|{state}|{code}|{start}|{end}|01:00:00||{node}")


def claim(**over):
    """A comparator receipt as the runner writes it inside a Slurm job (SLURM_JOB_ID set)."""
    rec = {"schema": "comparator-receipt/2", "mechanism": "toolchain", "commit": "c" * 40, "job_id": JOB,
           "host": "node-1", "host_policy": f"Slurm job {JOB} on node-1", "generated_at": RUN_AT,
           "result": "pass", "configs": []}
    rec.update(over)
    return rec


def cr_main(*argv):
    from lean_pkg import comparator_receipt as cr
    return cr.main(list(argv))


def write(tmp_path, rec, name="r-comparator.json"):
    p = tmp_path / name
    p.write_text(json.dumps(rec, indent=2))
    return p


# --- the seal --------------------------------------------------------------------------------------------

def test_seal_writes_the_normalized_record_and_binds_it(tmp_path, capsys):
    from lean_pkg import receipt
    p = write(tmp_path, claim())
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(sacct(tmp_path, *ok_lines())), "--json") == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] and res["sealed"]
    rec = json.loads(p.read_text())
    block = rec["slurm_sacct"]
    out = tmp_path / ("c" * 12 + "-comparator-sacct.txt")
    body = out.read_bytes()
    assert block["file"] == f".lean-receipts/{out.name}" and block["job"] == JOB
    assert block["start"] == "2026-10-05T11:30:00+00:00" and block["end"] == "2026-10-05T12:30:00+00:00"
    assert block["checks"] == {"user": "not checked", "node": "matched"} and block["source"] == "sacct-file"
    assert b"someone" not in body and b"node-1" not in body               # checked, never written
    assert receipt.sacct_seal_problem(rec, body) is None
    # idempotent: the same record again is a no-op; another record is refused
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(sacct(tmp_path, *ok_lines()))) == 0
    assert "already sealed" in capsys.readouterr().out
    other = sacct(tmp_path, *ok_lines(end="2026-10-05T08:00:00-0500"), name="other.txt")
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(other)) == 1
    assert "already sealed with another record" in capsys.readouterr().err


@pytest.mark.parametrize("lines,why", [
    (ok_lines(job="999"), "no main job line for job 5581013"),                     # a record of another job
    (ok_lines(start="2026-10-05T08:00:00-0500", end="2026-10-05T09:00:00-0500"),
     "does not contain the comparator run"),                                       # another time
    (ok_lines(state="FAILED", code="1:0"), "is not a clean finish"),
    (ok_lines(state="RUNNING", code="0:0"), "is not a clean finish"),              # sealed while running
    (ok_lines(start="2026-10-05T06:30:00", end="2026-10-05T07:30:00"), "no zoned Start/End"),
    (ok_lines(node="node-2"), "ran on another node"),
])
def test_seal_refuses_a_record_that_does_not_corroborate_the_run(tmp_path, capsys, lines, why):
    p = write(tmp_path, claim())
    before = p.read_bytes()
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(sacct(tmp_path, *lines))) == 1
    err = capsys.readouterr().err
    assert why in err and "not sealed" in err
    assert p.read_bytes() == before and not list(tmp_path.glob("*-comparator-sacct.txt"))


def test_seal_checks_the_account_when_named_and_a_receipt_without_a_slurm_claim(tmp_path, capsys):
    p = write(tmp_path, claim())
    f = sacct(tmp_path, *ok_lines())
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(f), "--user", "another") == 1
    assert "ran under another account" in capsys.readouterr().err
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(f), "--user", "someone") == 0
    capsys.readouterr()
    q = write(tmp_path, claim(job_id="local-x-1", host_policy="allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"),
              name="local.json")
    assert cr_main("--seal-sacct", str(q), "--sacct-file", str(f)) == 1
    assert "does not claim a Slurm job" in capsys.readouterr().err
    assert cr_main("--sacct-file", str(f)) == 2                           # usage: needs --seal-sacct


def test_a_host_label_is_not_compared_with_the_node(tmp_path, capsys):
    p = write(tmp_path, claim(host="lab-node", host_label="lab-node", host_policy=f"Slurm job {JOB} on lab-node"))
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(sacct(tmp_path, *ok_lines()))) == 0
    assert json.loads(p.read_text())["slurm_sacct"]["checks"]["node"] == "not checked (host label)"


def test_the_validator_rejects_a_tampered_or_mismatched_seal(tmp_path):
    from lean_pkg import receipt
    p = write(tmp_path, claim())
    assert cr_main("--seal-sacct", str(p), "--sacct-file", str(sacct(tmp_path, *ok_lines()))) == 0
    rec = json.loads(p.read_text())
    body = (tmp_path / ("c" * 12 + "-comparator-sacct.txt")).read_bytes()
    assert receipt.sacct_seal_problem(rec, body) is None
    assert "does not hash" in receipt.sacct_seal_problem(rec, body.replace(b"12:30", b"13:30"))
    assert "not committed at HEAD" in receipt.sacct_seal_problem(rec, None)
    assert "no sacct seal" in receipt.sacct_seal_problem({k: v for k, v in rec.items() if k != "slurm_sacct"},
                                                         body)
    moved = {**rec, "generated_at": "2026-10-06T12:00:00+00:00"}           # a seal copied onto another run
    assert "does not contain the comparator run" in receipt.sacct_seal_problem(moved, body)
    other = {**rec, "job_id": "999", "host_policy": "Slurm job 999 on node-1"}
    assert "seals job '5581013', the receipt claims job 999" in receipt.sacct_seal_problem(other, body)


# --- coverage compute / the Easley sentence -----------------------------------------------------------

def _comparator_backed(env, tmp_path, capsys, seal: bool, lines=None):
    """beta page-ready with coverage `compute: easley [slurm-JOB]`, whose only Slurm evidence is the
    comparator receipt claiming JOB (the lower-tier receipt ran in a systemd unit)."""
    prepare(env, easley=True, job=f"slurm-{JOB}", receipt_job="systemd-user-unit-1234-h-abcdef012345",
            comparator={"schema": "comparator-receipt/2", "mechanism": "legacy", "job_id": JOB,
                        "host": "node-1", "host_policy": f"Slurm job {JOB} on node-1"})
    comp = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
    if seal:
        assert cr_main("--seal-sacct", str(env.repo_b / comp), "--sacct-file",
                       str(sacct(tmp_path, *(lines or ok_lines())))) == 0
        capsys.readouterr()
        _git(env.repo_b, "add", "-f", ".lean-receipts")
        _git(env.repo_b, "commit", "-q", "-m", "sacct seal")
    return comp


def test_a_forged_slurm_job_id_without_sacct_does_not_back_easley(lp, env, tmp_path, capsys):
    comp = _comparator_backed(env, tmp_path, capsys, seal=False)
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and f"no Slurm receipt tracked at HEAD under .lean-receipts/ has job_id slurm-{JOB}" in err
    assert f"{comp} claims Slurm job {JOB} but does not back it: no sacct seal" in err
    assert "a bare number" not in err                                     # not the lower-tier hint
    assert lp.main(["--registry", str(env.registry), "comparator", "beta", "--check",
                    "--receipt", str(env.repo_b / comp)]) == 0           # still a valid comparator receipt


def test_a_sealed_sacct_record_backs_easley(lp, env, tmp_path, capsys):
    _comparator_backed(env, tmp_path, capsys, seal=True)
    rc, data = page_json(lp, env, capsys)
    assert rc == 0, data
    assert "Easley Cluster" in data["markdown"]
    row = next(x for x in data["markdown"].splitlines() if "-comparator.json`" in x and x.startswith("| `"))
    assert f"slurm-{JOB}" in row


def test_a_seal_edited_after_commit_does_not_back_easley(lp, env, tmp_path, capsys):
    _comparator_backed(env, tmp_path, capsys, seal=True)
    seal = next((env.repo_b / ".lean-receipts").glob("*-comparator-sacct.txt"))
    seal.write_text(seal.read_text().replace("COMPLETED", "COMPLETED "))
    _git(env.repo_b, "commit", "-q", "-am", "edit the seal")
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "does not hash to slurm_sacct.sha256" in err
