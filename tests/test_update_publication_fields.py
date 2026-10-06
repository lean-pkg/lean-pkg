#!/usr/bin/env python3
"""update can set the v2 publication facts license_spdx and formalization_yaml."""

from __future__ import annotations

from .conftest import read_events, read_reg


def test_update_sets_license_spdx_and_formalization_yaml(lp, env):
    assert lp.main(["--registry", str(env.registry), "update", "beta", "--license-spdx", "Apache-2.0",
                    "--formalization-yaml", "formalization.yaml", "--reason", "relicensed"]) == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["license_spdx"] == "Apache-2.0" and pkg["formalization_yaml"] == "formalization.yaml"
    assert read_events(env)[-1]["event"] == "updated"


def test_update_refuses_a_malformed_spdx_id_or_absolute_path(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "update", "beta", "--license-spdx", "MIT License"]) == 1
    assert "license_spdx" in capsys.readouterr().err
    assert lp.main(["--registry", str(env.registry), "update", "beta",
                    "--formalization-yaml", "/abs/formalization.yaml"]) == 1
    assert "formalization_yaml" in capsys.readouterr().err
    assert read_reg(env)["packages"]["beta"]["license_spdx"] is None
