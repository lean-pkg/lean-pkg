#!/usr/bin/env python3
"""migrate: registry v1 -> v3 (additive), idempotent, and v1 stays readable."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from .conftest import ROOT, read_events

V1 = ROOT / "tests" / "fixtures" / "schemas" / "registry-v1.yaml"
V2_KEYS = ["license_spdx", "formalization_yaml", "palomar", "doi", "reservoir", "consent",
           "claims_file", "claims", "claims_synced_commit"]


def copy_v1(tmp_path: Path) -> Path:
    reg = tmp_path / "lean-packages.yaml"
    shutil.copy(V1, reg)
    return reg


def test_migrate_v1_to_v3_adds_keys_and_keeps_everything_else(lp, tmp_path):
    reg = copy_v1(tmp_path)
    before = yaml.safe_load(reg.read_text())
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    after = yaml.safe_load(reg.read_text())
    assert after["version"] == 3
    for name, pkg in after["packages"].items():
        for k in V2_KEYS:
            assert k in pkg, (name, k)
        for k, v in before["packages"][name].items():
            assert pkg[k] == v, (name, k)          # nothing old changed
    ev = read_events(type("E", (), {"events": reg.parent / "lean-package-events.jsonl"})())
    assert ev[-1]["event"] == "registry-migrated"
    assert ev[-1]["from_version"] == 1 and ev[-1]["to_version"] == 3


def test_migrate_is_idempotent(lp, tmp_path):
    reg = copy_v1(tmp_path)
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    first = reg.read_bytes()
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    assert reg.read_bytes() == first


def test_migrate_dry_run_changes_nothing(lp, tmp_path, capsys):
    reg = copy_v1(tmp_path)
    before = reg.read_bytes()
    assert lp.main(["--registry", str(reg), "migrate", "--dry-run"]) == 0
    assert reg.read_bytes() == before
    assert "DRY RUN" in capsys.readouterr().out


def test_v1_registry_readable_without_migrate(lp, tmp_path, capsys):
    reg = copy_v1(tmp_path)
    assert lp.main(["--registry", str(reg), "list"]) == 0
    assert lp.main(["--registry", str(reg), "status", "alpha"]) == 0
    assert lp.main(["--registry", str(reg), "validate"]) == 0   # v1 is still valid


def test_write_commands_tolerate_missing_v2_keys(lp, tmp_path, capsys):
    reg = copy_v1(tmp_path)
    rc = lp.main(["--registry", str(reg), "claim", "alpha", "--workspace", "w", "--agent", "a"])
    assert rc == 0                                                 # no KeyError on v1
    assert lp.main(["--registry", str(reg), "validate"]) == 0


def test_validate_rejects_unknown_version(lp, tmp_path, capsys):
    reg = copy_v1(tmp_path)
    data = yaml.safe_load(reg.read_text()); data["version"] = 4
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    assert lp.main(["--registry", str(reg), "validate"]) == 1
    assert "version" in capsys.readouterr().out


def test_register_writes_v2_keys(lp, env, tmp_path, capsys):
    from .conftest import make_repo
    repo = make_repo(tmp_path / "gamma-repo")
    assert lp.main(["--registry", str(env.registry), "register", "gamma",
                    "--repo", "git@github.com:example/gamma.git", "--path", str(repo),
                    "--dry-run"]) == 0
    out = capsys.readouterr().out
    for k in V2_KEYS:
        assert f"{k}:" in out, k


def test_validate_checks_doi_binding_on_v1_registry(lp, tmp_path, capsys):
    """The D5 binding rule applies whenever the keys are present, migrated or not."""
    reg = copy_v1(tmp_path)
    c = "0123456789abcdef0123456789abcdef01234567"
    before = reg.read_text()
    assert lp.main(["--registry", str(reg), "record", "beta", "doi-minted", "--field", "doi=10.5281/zenodo.9",
                    "--field", "tag=v1.0.0", "--field", f"commit={c}"]) == 1   # record refuses (#16) ...
    assert "rule doi-in-palomar" in capsys.readouterr().err and reg.read_text() == before
    data = yaml.safe_load(reg.read_text())                                     # ... a legacy entry is flagged
    data["packages"]["beta"]["doi"] = {"concept": None, "versions": [
        {"doi": "10.5281/zenodo.9", "tag": "v1.0.0", "commit": c, "minted_at": "2026-10-01T00:00:00+00:00"}]}
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    assert lp.main(["--registry", str(reg), "validate"]) == 1
    out = capsys.readouterr().out
    assert c[:12] in out and "palomar" in out


def test_migrate_rewrites_legacy_tilde_local_paths(lp, tmp_path, monkeypatch, capsys):
    """#21.4: rows stored before #17 as a literal `~/x` resolved to $HOME/~/x; migrate rewrites them
    through the normaliser ($HOME-relative), leaves other paths alone, and is idempotent."""
    reg = copy_v1(tmp_path)
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    data = yaml.safe_load(reg.read_text())
    names = list(data["packages"])
    data["packages"][names[0]]["local_path"] = "~/src/legacy-pkg/"
    if len(names) > 1:
        data["packages"][names[1]]["local_path"] = "~other/pkg"
    reg.write_text(yaml.safe_dump(data, sort_keys=False))
    keep = data["packages"][names[1]]["local_path"] if len(names) > 1 else None
    capsys.readouterr()
    assert lp.main(["--registry", str(reg), "migrate", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any("legacy literal ~/" in c for c in payload["changes"])
    assert yaml.safe_load(reg.read_text())["packages"][names[0]]["local_path"] == "~/src/legacy-pkg/"
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    after = yaml.safe_load(reg.read_text())["packages"]
    assert after[names[0]]["local_path"] == "src/legacy-pkg"
    assert lp.repo_path(after[names[0]]) == lp.HOME / "src" / "legacy-pkg"
    if keep is not None:
        assert after[names[1]]["local_path"] == keep                # ~user is never guessed
    capsys.readouterr()
    assert lp.main(["--registry", str(reg), "migrate"]) == 0
    assert "nothing to do" in capsys.readouterr().out
