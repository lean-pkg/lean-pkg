#!/usr/bin/env python3
"""A comparator receipt never names its host: the hashed label `receipt systemd` uses, or the
operator's --host-label, in host, host_label, job_id and host_policy; the host policy still accepts the
raw name; public-safety flags an older receipt that names its host, whatever the host is called."""

from __future__ import annotations

import importlib
import json
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from .conftest import ROOT, _git, base_pkg, make_repo

STUB = ROOT / "tests" / "fixtures" / "comparator-check-stub.sh"
HOST = "kq7box.lab.example.org"          # distinctive: neither the user name nor a stop word


@pytest.fixture()
def cr():
    return importlib.import_module("lean_pkg.comparator_receipt")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                           "-c", "user.name=Tests", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    git(p, "init", "-q")
    (p / "lean-toolchain").write_text("leanprover/lean4:v4.32.0\n")
    (p / "comparator.json").write_text(json.dumps({
        "challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": ["foo"],
        "definition_names": [], "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
        "enable_nanoda": True}, indent=2) + "\n")
    git(p, "add", "-A")
    git(p, "commit", "-q", "-m", "init")
    return p


@pytest.fixture()
def on_host(monkeypatch, cr):
    """This process runs on HOST, outside Slurm, with no host-policy override or label."""
    monkeypatch.setattr(socket, "gethostname", lambda: HOST)
    for k in ("SLURM_JOB_ID", cr.ALLOWED_HOSTS_ENV, cr.ALLOW_HOST_ENV, cr.HOST_LABEL_ENV,
              "COMPARATOR_RECEIPT_BUILD_SOURCE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("COMPARATOR_CHECK", str(STUB))
    monkeypatch.setenv("STUB_MODE", "pass")
    monkeypatch.setenv(cr.FAKE_LAKE_ENV, "0")
    return HOST


def receipt_text(project: Path) -> str:
    head = git(project, "rev-parse", "HEAD")
    return (project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text()


def test_the_label_is_receipt_systemds(cr):
    from lean_pkg.receipt_systemd import host_label
    assert cr.hashed_host_label is host_label
    assert cr.published_host(HOST) == host_label(HOST) == host_label(HOST.upper())
    assert cr.HASHED_LABEL_RE.fullmatch(cr.published_host(HOST))


def test_receipt_on_a_distinctive_host_names_only_its_label(cr, on_host, project, monkeypatch, capsys):
    label = cr.hashed_host_label(HOST)
    monkeypatch.setenv(cr.ALLOWED_HOSTS_ENV, f"other, {HOST}")          # the raw name is still accepted
    monkeypatch.setenv("COMPARATOR_RECEIPT_BUILD_SOURCE", "prebuilt on kq7box")
    assert cr.main(["--repo", str(project)]) == 0
    text = receipt_text(project)
    out = capsys.readouterr()
    assert "kq7box" not in text.lower() and "kq7box" not in (out.out + out.err).lower()
    rec = json.loads(text)
    assert rec["host"] == rec["host_label"] == label
    assert rec["job_id"].startswith(f"local-{label}-")
    assert rec["host_policy"] == f"host {label} listed in {cr.ALLOWED_HOSTS_ENV}"
    assert cr.raw_host_problem(rec) is None


def test_host_policy_accepts_the_label_too(cr, on_host, project, monkeypatch):
    monkeypatch.setenv(cr.ALLOWED_HOSTS_ENV, cr.hashed_host_label(HOST))
    assert cr.main(["--repo", str(project)]) == 0
    assert json.loads(receipt_text(project))["host"] == cr.hashed_host_label(HOST)


def test_another_hosts_label_is_refused(cr, on_host, project, monkeypatch, capsys):
    monkeypatch.setenv(cr.ALLOWED_HOSTS_ENV, cr.hashed_host_label("otherbox.lab.example.org"))
    assert cr.main(["--repo", str(project)]) == 1
    err = capsys.readouterr().err
    assert "refusing" in err and cr.hashed_host_label(HOST) in err and "kq7box" not in err
    assert not (project / ".lean-receipts").exists()


def test_slurm_receipt_names_the_node_by_its_label(cr, on_host, project, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "4242")
    assert cr.main(["--repo", str(project)]) == 0
    rec = json.loads(receipt_text(project))
    assert rec["job_id"] == "4242" and rec["host_policy"] == f"Slurm job 4242 on {cr.hashed_host_label(HOST)}"
    assert "kq7box" not in json.dumps(rec)


def test_operator_label_wins_and_the_raw_name_still_passes_policy(cr, on_host, project, monkeypatch):
    monkeypatch.setenv(cr.ALLOWED_HOSTS_ENV, HOST.upper())                  # any case
    monkeypatch.setenv(cr.HOST_LABEL_ENV, "unset-by-teardown")   # main() sets it from --host-label
    assert cr.main(["--repo", str(project), "--host-label", "lab-node"]) == 0
    rec = json.loads(receipt_text(project))
    assert rec["host"] == rec["host_label"] == "lab-node" and "kq7box" not in json.dumps(rec)
    assert rec["host_policy"] == f"host lab-node listed in {cr.ALLOWED_HOSTS_ENV}"


def test_raw_host_problem_reads_old_receipts(cr):
    assert cr.raw_host_problem({"host": "kq7box", "job_id": "local-kq7box-1-x"})
    assert cr.raw_host_problem({"host": "kq7box", "host_label": "kq7box"}) is None   # the operator's label
    assert cr.raw_host_problem({"host": cr.hashed_host_label("kq7box")}) is None
    assert cr.raw_host_problem({}) is None


# --- public-safety: a receipt that names its host, whatever the host is called -----------------------

def _receipt(host: str, **kw) -> str:
    return json.dumps({"schema": "comparator-receipt/2", "host": host, "job_id": f"local-{host}-1-20261017T000000Z",
                       "host_policy": "allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1", **kw}, indent=2) + "\n"


def _scan(lp, tmp_path, files: dict, history=False, allow: str | None = None):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "theorem t : True := trivial\n", **files})
    if allow:
        (repo / ".lean-pkg-private-patterns").write_text(f"allow: {allow}\n")
    reg = tmp_path / "lean-packages.yaml"
    reg.write_text(yaml.safe_dump({"version": 2, "packages": {"pkg": base_pkg("pkg", repo, "gate-pending")}},
                                  sort_keys=False))
    return repo, reg


def _hits(lp, reg, capsys, *extra):
    rc = lp.main(["--registry", str(reg), "public-safety", "pkg", "--json", *extra])
    payload = json.loads(capsys.readouterr().out)
    return rc, [h for h in payload["hits"] if h["pattern"] == "receipt-raw-host"], payload


def test_public_safety_flags_a_receipt_naming_its_host(lp, tmp_path, capsys):
    _repo, reg = _scan(lp, tmp_path, {".lean-receipts/0123456789ab-comparator.json": _receipt("kq7box")})
    rc, hits, payload = _hits(lp, reg, capsys, "--no-history")
    assert rc == 1 and len(hits) == 1 and hits[0]["severity"] == "block" and hits[0]["line"] == 3
    assert "kq7box" not in json.dumps(payload)                 # never printed


def test_public_safety_flags_it_in_history_too(lp, tmp_path, capsys):
    repo, reg = _scan(lp, tmp_path, {".lean-receipts/0123456789ab-comparator.json": _receipt("kq7box")})
    _git(repo, "rm", "-q", "-r", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "drop the receipt")
    rc, hits, _p = _hits(lp, reg, capsys)
    assert rc == 1 and [h["where"] for h in hits] == ["history"]


@pytest.mark.parametrize("rec", [
    _receipt("h-0123456789ab"),                                  # the hashed label
    _receipt("lab-node", host_label="lab-node"),                 # an operator label
])
def test_public_safety_passes_a_labelled_receipt(lp, tmp_path, capsys, rec):
    _repo, reg = _scan(lp, tmp_path, {".lean-receipts/0123456789ab-comparator.json": rec})
    rc, hits, _p = _hits(lp, reg, capsys, "--no-history")
    assert rc == 0 and hits == []


def test_public_safety_receipt_host_rule_has_no_false_positives(lp, tmp_path, capsys):
    files = {"docs/example-comparator.json": _receipt("kq7box"),           # not under .lean-receipts/
             ".lean-receipts/0123456789ab-receipt.json": _receipt("kq7box"),  # not a comparator receipt
             ".lean-receipts/0123456789ab-other-comparator.json": json.dumps({"schema": "x/1", "host": "kq7box"})}
    _repo, reg = _scan(lp, tmp_path, files)
    _rc, hits, _p = _hits(lp, reg, capsys, "--no-history")
    assert hits == []


def test_an_allow_line_exempts_a_public_host_name(lp, tmp_path, capsys):
    _repo, reg = _scan(lp, tmp_path, {".lean-receipts/0123456789ab-comparator.json": _receipt("node-1")},
                       allow="^node-1$")
    _rc, hits, _p = _hits(lp, reg, capsys, "--no-history")
    assert hits == []
