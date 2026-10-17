#!/usr/bin/env python3
"""#163 follow-up: `audit` reports Palomar readiness gaps as a warning (`palomar-not-ready`, the error-gap
count and `lean-pkg palomar check NAME`), only for packages that intend Palomar: upstream_target palomar
or a recorded palomar entry, status not scratch, archived or upstreamed. Quiet for every other package,
and quiet when only warn gaps (submission steps) remain.

Every registry here is a throwaway file under tmp_path; nothing touches the network (--no-gh)."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import read_reg, repo_head, write_reg
from .test_palomar_check import files, make_pkg

CHECK = "palomar-not-ready"
UNHEADED = {"Gamma/Main.lean": "import Mathlib\n\ntheorem Gamma.foo : True := trivial\n"}


def audit(lp, reg, capsys, *extra) -> dict:
    assert lp.main(["--registry", str(reg), "audit", "--json", "--no-gh", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def found(data: dict) -> list[dict]:
    return [f for f in data["findings"] if f["check"] == CHECK]


def set_fields(reg, **fields) -> None:
    data = yaml.safe_load(reg.read_text())
    data["packages"]["gamma"].update(fields)
    reg.write_text(yaml.safe_dump(data, sort_keys=False))


def test_palomar_target_with_error_gaps_is_a_warning(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, files(**UNHEADED), upstream_target="palomar",
                         lean_toolchain="leanprover/lean4:v4.35.0")
    before = reg.read_bytes()
    (f,) = found(audit(lp, reg, capsys))
    assert f["name"] == "gamma" and f["level"] == "warn" and f["fixed"] is False
    pal = f["palomar"]
    assert pal["errors"] == 1 and pal["codes"] == ["source.module_required"]
    assert pal["commit"] == repo_head(repo) and pal["command"] == "lean-pkg palomar check gamma"
    assert f["detail"] == (f"1 Palomar readiness gap(s) at HEAD {repo_head(repo)[:12]} "
                           f"(source.module_required); run `lean-pkg palomar check gamma`")
    assert reg.read_bytes() == before                    # read-only
    assert lp.main(["--registry", str(reg), "audit", "--no-gh"]) == 0
    out = capsys.readouterr().out.splitlines()
    (line,) = [x for x in out if CHECK in x]
    assert line.startswith("warn  ") and "gamma: 1 Palomar readiness gap(s)" in line


def test_a_recorded_palomar_entry_also_signals_intent(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(**UNHEADED), lean_toolchain="leanprover/lean4:v4.35.0",
                          palomar=[{"commit": "a" * 40, "preflight_run": "run-1"}])
    assert [f["name"] for f in found(audit(lp, reg, capsys))] == ["gamma"]


def test_ready_package_is_quiet_even_with_warn_gaps(lp, tmp_path, capsys):
    _repo, reg = make_pkg(tmp_path, files(), upstream_target="palomar", visibility="private",
                          lean_toolchain="leanprover/lean4:v4.35.0")
    assert lp.main(["--registry", str(reg), "palomar", "check", "gamma", "--json"]) == 0
    check = json.loads(capsys.readouterr().out)
    assert check["counts"]["error"] == 0 and check["counts"]["warn"] >= 1
    assert found(audit(lp, reg, capsys)) == []


@pytest.mark.parametrize("fields", [
    {"upstream_target": "none"},
    {"upstream_target": "mathlib"},
    {"upstream_target": "palomar", "status": "scratch"},
    {"upstream_target": "palomar", "status": "archived"},
    {"upstream_target": "palomar", "status": "upstreamed"},
])
def test_quiet_for_packages_not_aiming_at_palomar(lp, tmp_path, capsys, fields):
    _repo, reg = make_pkg(tmp_path, files(**UNHEADED), lean_toolchain="leanprover/lean4:v4.35.0")
    set_fields(reg, **fields)
    assert found(audit(lp, reg, capsys)) == []


def test_conftest_packages_are_quiet_until_they_target_palomar(lp, env, capsys):
    """beta (conftest) has error gaps (Lean v4.32.0, no module headers) but upstream_target none."""
    assert found(audit(lp, env.registry, capsys)) == []
    reg = read_reg(env)
    reg["packages"]["beta"]["upstream_target"] = "palomar"
    write_reg(env, reg)
    (f,) = found(audit(lp, env.registry, capsys))
    assert f["name"] == "beta" and "the gate-bound commit" in f["detail"]
    assert {"source.module_required", "toolchain.unsupported"} <= set(f["palomar"]["codes"])
    assert f["palomar"]["command"] == "lean-pkg palomar check beta"


def test_missing_checkout_is_left_to_missing_path(lp, tmp_path, capsys):
    repo, reg = make_pkg(tmp_path, files(**UNHEADED), upstream_target="palomar")
    set_fields(reg, local_path=str(tmp_path / "gone"))
    data = audit(lp, reg, capsys)
    assert found(data) == [] and [f["check"] for f in data["findings"] if f["name"] == "gamma"].count(
        "missing-path") == 1
