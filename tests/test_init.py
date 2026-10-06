#!/usr/bin/env python3
"""init: bootstrap a registry and its event log."""

from __future__ import annotations

import json
import yaml


def test_init_creates_registry_and_events(lp, tmp_path, capsys):
    reg = tmp_path / "r" / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "init"]) == 0
    data = yaml.safe_load(reg.read_text())
    assert data["version"] in (1, 2)
    assert data["packages"] == {}
    events = (reg.parent / "lean-package-events.jsonl").read_text().splitlines()
    assert len(events) == 1 and json.loads(events[0])["event"] == "registry-created"
    assert "created" in capsys.readouterr().out


def test_init_refuses_existing_registry(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "init"])
    assert rc == 1
    assert "exists" in capsys.readouterr().err
    assert "alpha" in env.registry.read_text()          # untouched


def test_init_force_only_replaces_an_empty_registry(lp, tmp_path, env, capsys):
    reg = tmp_path / "e" / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "init"]) == 0
    assert lp.main(["--registry", str(reg), "init", "--force"]) == 0   # empty: allowed
    rc = lp.main(["--registry", str(env.registry), "init", "--force"])  # has packages: refused
    assert rc == 1
    assert "packages" in capsys.readouterr().err


def test_init_json_payload(lp, tmp_path, capsys):
    reg = tmp_path / "j" / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "--json", "init"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["event"] == "registry-created"
    assert payload["registry"] == str(reg)


def test_init_force_refuses_non_mapping_yaml(lp, tmp_path, capsys):
    reg = tmp_path / "bad" / "lean-packages.yaml"
    reg.parent.mkdir()
    reg.write_text("- not\n- a\n- registry\n")
    assert lp.main(["--registry", str(reg), "init", "--force"]) == 1
    assert "YAML" in capsys.readouterr().err
