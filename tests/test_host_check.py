#!/usr/bin/env python3
"""#22.5: `comparator host-check`, the doctor `host` line and the host check comparator-receipt reports."""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture()
def cr():
    return importlib.import_module("lean_pkg.comparator_receipt")


def host(cr, monkeypatch, *, abi=6, bwrap=("ok", "creates a user namespace"), missing=(), linux=True):
    monkeypatch.setattr(cr, "landlock_abi", lambda: abi)
    monkeypatch.setattr(cr, "lsm_list", lambda: ["landlock"])
    monkeypatch.setattr(cr, "bwrap_probe", lambda: bwrap)
    monkeypatch.setattr(cr.shutil, "which", lambda t: None if t in missing else f"/usr/bin/{t}")
    monkeypatch.setattr(cr.sys, "platform", "linux" if linux else "darwin")


def hc(lp, capsys, *extra, registry=None):
    argv = (["--registry", str(registry)] if registry else []) + ["comparator", "host-check", *extra]
    rc = lp.main(argv)
    return rc, capsys.readouterr()


def test_capable_host_passes_with_both_mechanisms(lp, cr, monkeypatch, capsys):
    host(cr, monkeypatch)
    rc, cap = hc(lp, capsys, "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["ok"] is True and data["can"] is True
    assert data["mechanisms"] == {"toolchain": True, "legacy": True}
    assert {c["item"] for c in data["checks"]} == {"kernel", "landlock", "bwrap", "go", "cargo", "jq", "git",
                                                   "lean", "lake"}
    assert data["verdict"].startswith("this host can produce comparator receipts")


def test_old_kernel_and_blocked_userns_cannot(lp, cr, monkeypatch, capsys):
    host(cr, monkeypatch, abi=0, bwrap=("fail", "bwrap cannot create a user namespace"))
    rc, cap = hc(lp, capsys)
    assert rc == 1
    assert "fail landlock: absent or disabled" in cap.out and "fail bwrap:" in cap.out
    assert cap.out.strip().splitlines()[-1].startswith("verdict: this host cannot produce comparator receipts")


def test_low_landlock_abi_warns_but_legacy_still_works(lp, cr, monkeypatch, capsys):
    host(cr, monkeypatch, abi=2, bwrap=("fail", "blocked"))
    rc, cap = hc(lp, capsys, "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["mechanisms"] == {"toolchain": False, "legacy": True}
    assert next(c for c in data["checks"] if c["item"] == "landlock")["level"] == "warn"


def test_missing_go_leaves_only_the_toolchain_mechanism(lp, cr, monkeypatch, capsys):
    host(cr, monkeypatch, missing=("go",))
    rc, cap = hc(lp, capsys, "--json")
    assert rc == 0 and json.loads(cap.out)["mechanisms"] == {"toolchain": True, "legacy": False}


def test_not_linux_cannot(lp, cr, monkeypatch, capsys):
    host(cr, monkeypatch, abi=None)
    monkeypatch.setattr(cr.sys, "platform", "darwin")
    rc, cap = hc(lp, capsys)
    assert rc == 1 and "fail kernel:" in cap.out and "need Linux" in cap.out


def test_host_check_is_read_only_and_needs_no_registry(lp, cr, monkeypatch, capsys, tmp_path):
    host(cr, monkeypatch)
    missing = tmp_path / "nowhere" / "lean-packages.yaml"
    rc, cap = hc(lp, capsys, "--commit", registry=missing)
    assert rc == 0 and "--commit ignored: read-only command" in cap.err
    assert not missing.parent.exists()


def test_comparator_without_receipt_is_a_usage_error(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "comparator", "beta"])
    assert rc == 2
    assert "comparator NAME needs --receipt PATH" in capsys.readouterr().err


def test_doctor_has_a_host_line(lp, env, cr, monkeypatch, capsys):
    host(cr, monkeypatch, abi=0, bwrap=("fail", "blocked"))
    lp.main(["--registry", str(env.registry), "doctor", "--json"])
    line = next(c for c in json.loads(capsys.readouterr().out)["checks"] if c["item"] == "host")
    assert line["level"] == "warn" and "cannot produce comparator receipts" in line["detail"]
    assert "lean-pkg comparator host-check" in line["detail"]


def test_comparator_receipt_reports_the_host_check_first(cr, monkeypatch, capsys, tmp_path):
    monkeypatch.delenv(cr.FAKE_LAKE_ENV, raising=False)
    monkeypatch.setenv(cr.ALLOW_HOST_ENV, "1")
    monkeypatch.setattr(cr, "host_checks", lambda: {"verdict": "this host cannot produce comparator receipts: x"})
    assert cr.main(["--project", str(tmp_path)]) == 1          # not a Lake project: refused after the report
    err = capsys.readouterr().err
    assert err.splitlines()[0] == "comparator-receipt: host-check: this host cannot produce comparator receipts: x"
