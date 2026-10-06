#!/usr/bin/env python3
"""migrate: registry v2 -> v3 adds claims_file, claims[], claims_synced_commit (additive)."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from .conftest import ROOT

V2 = ROOT / "tests" / "fixtures" / "schemas" / "registry-v2.yaml"
V3_KEYS = {"claims_file": "claims.yaml", "claims": [], "claims_synced_commit": None}


def copy_v2(tmp_path: Path) -> Path:
    reg = tmp_path / "lean-packages.yaml"
    shutil.copy(V2, reg)
    return reg


def test_migrate_v2_to_v3_adds_claim_keys_and_keeps_everything_else(lp, tmp_path):
    reg = copy_v2(tmp_path)
    before = yaml.safe_load(reg.read_text())
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    after = yaml.safe_load(reg.read_text())
    assert after["version"] == 3
    for name, pkg in after["packages"].items():
        for k, v in V3_KEYS.items():
            assert pkg[k] == v, (name, k)
        for k, v in before["packages"][name].items():
            assert pkg[k] == v, (name, k)
    assert lp.main(["--registry", str(reg), "validate"]) == 0


def test_migrate_v2_is_idempotent(lp, tmp_path):
    reg = copy_v2(tmp_path)
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    first = reg.read_bytes()
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    assert reg.read_bytes() == first


def test_v2_registry_readable_without_migrate(lp, tmp_path):
    reg = copy_v2(tmp_path)
    assert lp.main(["--registry", str(reg), "list"]) == 0
    assert lp.main(["--registry", str(reg), "claims", "list"]) == 0
    assert lp.main(["--registry", str(reg), "validate"]) == 0


def test_validate_v3_requires_and_types_the_claim_keys(lp, tmp_path, capsys):
    reg = copy_v2(tmp_path)
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    data = yaml.safe_load(reg.read_text())
    data["packages"]["alpha"]["claims"] = "not a list"
    data["packages"]["beta"]["claims_synced_commit"] = "abc"
    del data["packages"]["beta"]["claims_file"]
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    assert lp.main(["--registry", str(reg), "validate"]) == 1
    out = capsys.readouterr().out
    assert "alpha.claims: must be a list" in out
    assert "claims_synced_commit" in out and "missing v3 field 'claims_file'" in out


def test_init_writes_the_current_version(lp, tmp_path):
    reg = tmp_path / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "init"]) == 0
    assert yaml.safe_load(reg.read_text())["version"] == lp.REGISTRY_VERSION == 3
