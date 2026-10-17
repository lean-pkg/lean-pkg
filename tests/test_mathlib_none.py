#!/usr/bin/env python3
"""`mathlib_rev: none` (decision of 2026-10-17): a package without Mathlib, whose lake-manifest.json is
readable and lists no `mathlib` package, has the explicit receipt value `none`. The producers derive it from the
manifest (no log line needed; another log value is refused), the gate checks it like a rev (`none` beside a
manifest that pins Mathlib is refused) and records a null pin. Receipts written before, with another value
beside such a manifest, are still read (not checked), and an unreadable manifest keeps the old fallback."""

from __future__ import annotations

import json

import pytest

from lean_pkg import receipt as receipt_mod

from .conftest import MATHLIB_MANIFEST, MATHLIB_REV, _git, make_receipt, read_reg, repo_head, write_receipt
from .test_receipt_slurm import CHECKER, LOG, Job, fake_sacct, rdir, sha  # noqa: F401  (fake_sacct: fixture)

CORE_MANIFEST = json.dumps({"version": "1.2.0", "packagesDir": ".lake/packages", "packages": [],
                            "name": "alpha", "lakeDir": ".lake"}) + "\n"


def fields_check(fields: dict, manifest: str | None, commit: str | None = "c" * 40):
    refusals: list[str] = []
    rev, _checker = receipt_mod.check_fields(
        fields, commit=commit, toolchain=None, manifest=manifest.encode() if manifest is not None else None,
        checker_flag="x", check_log_job_id=None, refusals=refusals)
    return rev, refusals


# ------------------------------------------------------------------------------------- the manifest reader

@pytest.mark.parametrize("manifest, want", [
    (CORE_MANIFEST, "none"),                                         # readable, no mathlib: the explicit value
    (MATHLIB_MANIFEST, MATHLIB_REV),
    ('{"packages": [{"name": "Mathlib", "rev": "abc123"}]}', "abc123"),
    ('{"packages": [{"name": "mathlib"}]}', None),                   # mathlib without a rev: unreadable
    ("{}", None),                                                    # no packages list: unreadable
    ("not json", None),
    (None, None),                                                    # no manifest at all
])
def test_manifest_mathlib(lp, manifest, want):
    assert lp.manifest_mathlib(manifest.encode() if manifest is not None else None) == want
    assert receipt_mod.manifest_mathlib_rev(manifest.encode() if manifest is not None else None) == want


# ------------------------------------------------------------------------------------- the producer side

def test_no_mathlib_needs_no_log_line():
    assert fields_check({}, CORE_MANIFEST) == ("none", [])
    assert fields_check({"mathlib-rev": "none"}, CORE_MANIFEST) == ("none", [])


def test_a_rev_in_the_log_beside_a_core_manifest_is_refused():
    rev, refusals = fields_check({"mathlib-rev": "abc123"}, CORE_MANIFEST)
    assert len(refusals) == 1 and "mathlib mismatch" in refusals[0] and "has no mathlib package" in refusals[0]


def test_none_in_the_log_beside_a_mathlib_manifest_is_refused():
    _rev, refusals = fields_check({"mathlib-rev": "none"}, MATHLIB_MANIFEST)
    assert len(refusals) == 1 and "mathlib mismatch" in refusals[0] and MATHLIB_REV in refusals[0]


def test_an_unreadable_manifest_keeps_the_log_fallback():
    assert fields_check({"mathlib-rev": "none"}, None) == ("none", [])
    _rev, refusals = fields_check({}, None)
    assert refusals and "no mathlib rev" in refusals[0]       # negative control: nothing to read it from


def test_a_slurm_receipt_of_a_core_package_says_none(lp, env, tmp_path, fake_sacct, capsys):
    """The whole producer: a log with no `mathlib-rev:` line, a core manifest committed; the receipt says
    `none` and `gate --check` checks it."""
    repo = env.repo_a
    (repo / "lake-manifest.json").write_text(CORE_MANIFEST)
    _git(repo, "add", "lake-manifest.json")
    _git(repo, "commit", "-qm", "core manifest")
    assert "mathlib-rev" not in LOG
    job = Job(tmp_path)
    man = sha(CORE_MANIFEST.encode())
    job.out.write_text(f"pre-manifest-sha256 {man}\npost-manifest-sha256 {man}\n"
                       f"verify-log-sha256: {sha(LOG.encode())}\n")
    argv = ["receipt", "slurm", "--job", "123", "--log", str(job.log), "--commit", repo_head(repo), "--repo",
            str(repo), "--stdout", str(job.out), "--stderr", str(job.err), "--checker", CHECKER]
    assert lp.main(argv) == 0, capsys.readouterr().err
    (rp,) = rdir(repo).glob("*.json")
    assert json.loads(rp.read_text())["mathlib_rev"] == "none"
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0
    assert "✓ mathlib_rev none: lake-manifest.json committed at" in capsys.readouterr().out


# ------------------------------------------------------------------------------------- the gate side

@pytest.fixture()
def core_repo(env):
    (env.repo_a / "lake-manifest.json").write_text(CORE_MANIFEST)
    _git(env.repo_a, "add", "lake-manifest.json")
    _git(env.repo_a, "commit", "-qm", "core manifest")
    return env.repo_a


def gate(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), "gate", "alpha", *argv])


def test_gate_checks_none_and_records_a_null_pin(lp, env, core_repo, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(core_repo, mathlib_rev="none"))
    assert gate(lp, env, "--check", "--receipt", str(p)) == 0
    out = capsys.readouterr().out
    assert "✓ mathlib_rev none: lake-manifest.json committed at" in out and "mathlib_rev not checked" not in out
    assert gate(lp, env, "--receipt", str(p), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pins"] == {"mathlib_pin": {"from": "81a5d257c8", "to": None}} and data["pins_kept"] == {}
    assert read_reg(env)["packages"]["alpha"]["mathlib_pin"] is None
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_none_with_a_null_pin_prints_no_pin_line(lp, env, core_repo, tmp_path, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["mathlib_pin"] = None
    import yaml
    env.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    p = write_receipt(tmp_path, make_receipt(core_repo, mathlib_rev="none"))
    assert gate(lp, env, "--receipt", str(p)) == 0
    out = capsys.readouterr().out
    assert "pins kept" not in out and "pins updated" not in out     # the old sentinel printed "pins kept"


def test_none_beside_a_mathlib_manifest_is_refused(lp, env, tmp_path, capsys):
    (env.repo_a / "lake-manifest.json").write_text(MATHLIB_MANIFEST)
    _git(env.repo_a, "add", "lake-manifest.json")
    _git(env.repo_a, "commit", "-qm", "manifest")
    p = write_receipt(tmp_path, make_receipt(env.repo_a, mathlib_rev="none"))
    before = env.registry.read_text()
    for extra in ((), ("--check",)):
        assert gate(lp, env, *extra, "--receipt", str(p)) == 1
        err = capsys.readouterr().err
        assert "mathlib mismatch" in err and "the value of a package without Mathlib" in err
    assert env.registry.read_text() == before


def test_an_older_receipt_beside_a_core_manifest_is_still_read(lp, env, core_repo, tmp_path, capsys):
    """Keep reading existing receipts: before `none` was checked, any nonempty value passed here."""
    p = write_receipt(tmp_path, make_receipt(core_repo, mathlib_rev="n/a"))
    assert gate(lp, env, "--check", "--receipt", str(p)) == 0
    assert "· mathlib_rev n/a not checked: lake-manifest.json committed at" in capsys.readouterr().out
    assert gate(lp, env, "--receipt", str(p), "--json") == 0
    assert read_reg(env)["packages"]["alpha"]["mathlib_pin"] is None   # never a pin of "n/a"


def test_an_empty_rev_is_still_refused(lp, env, core_repo, tmp_path, capsys):
    p = write_receipt(tmp_path, make_receipt(core_repo, mathlib_rev=" "))
    assert gate(lp, env, "--check", "--receipt", str(p)) == 1
    assert "mathlib_rev is empty" in capsys.readouterr().err
