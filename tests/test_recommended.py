#!/usr/bin/env python3
"""The registry's recommended lean-pkg version (#388): `registry recommend`, the offline stderr notice on
writing commands, `lean_pkg_skew` in their --json, doctor's `recommended` line and `registry validate`."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import read_events, read_reg, write_reg

NOTICE = "lean-pkg: notice: this is lean-pkg"


@pytest.fixture()
def running(monkeypatch):
    """Set the running lean-pkg version; the install is a wheel (the pipx route)."""
    import lean_pkg

    def set_version(v: str) -> None:
        monkeypatch.setattr(lean_pkg, "__version__", v)
    from lean_pkg import cli
    monkeypatch.setattr(cli, "install_detect", lambda d: {"install": "installed", "path": str(d),
                                                         "commit": None, "dirty": None})
    set_version("0.22.0")
    return set_version


def recommend(env, version: str) -> None:
    reg = read_reg(env)
    reg["lean_pkg"] = {"recommended": version}
    write_reg(env, reg)


def claim(lp, env, *extra) -> int:
    return lp.main(["--registry", str(env.registry), *extra, "lease", "claim", "alpha", "--workspace", "w",
                    "--agent", "a"])


# ---- the comparison --------------------------------------------------------------------------------------

@pytest.mark.parametrize("running_v, rec, older", [
    ("0.22.0", "0.23.0", True), ("0.22.0", "0.22.0", False), ("0.22.0", "0.21.9", False),
    ("0.9.0", "0.10.0", True),                          # numeric, not lexical
    ("0.23.0.dev1", "0.23.0", False), ("0.22.0+dirty", "0.23.0", True), ("0.23.0.dev1", "0.23.1", True),
    ("unknown", "0.23.0", False),                       # no leading X.Y.Z: never older
])
def test_skew_compares_on_x_y_z(running_v, rec, older):
    from lean_pkg import recommended
    sk = recommended.skew({"lean_pkg": {"recommended": rec}}, running_v)
    assert (sk == {"running": running_v, "recommended": rec}) if older else sk is None


@pytest.mark.parametrize("data", [{}, {"lean_pkg": {}}, {"lean_pkg": {"recommended": "0.23"}},
                                  {"lean_pkg": "0.23.0"}, {"lean_pkg": {"recommended": 23}}])
def test_skew_is_none_when_absent_or_malformed(data):
    from lean_pkg import recommended
    assert recommended.skew(data, "0.1.0") is None


# ---- registry validate -----------------------------------------------------------------------------------

@pytest.mark.parametrize("block, ok", [
    ({"recommended": "0.23.0"}, True), ({}, True), ({"recommended": "0.23.0", "later_key": 1}, True),
    ({"recommended": "0.23"}, False), ({"recommended": "v0.23.0"}, False), ({"recommended": "0.023.0"}, False),
    ({"recommended": None}, False), ({"recommended": 1.2}, False), ("0.23.0", False), (None, False),
])
def test_validate_checks_the_shape(lp, env, capsys, block, ok):
    reg = read_reg(env)
    reg["lean_pkg"] = block
    write_reg(env, reg)
    rc = lp.main(["--registry", str(env.registry), "registry", "validate"])
    out = capsys.readouterr().out
    assert (rc == 0) == ok, out
    if not ok:
        assert "lean_pkg" in out


def test_validate_without_the_key_is_unchanged(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "registry", "validate"]) == 0
    assert "lean_pkg" not in capsys.readouterr().out


# ---- the setter ------------------------------------------------------------------------------------------

def test_recommend_writes_the_key_and_a_log_only_event(lp, env, running, capsys):
    before = read_reg(env)
    assert lp.main(["--registry", str(env.registry), "registry", "recommend", "0.22.0",
                    "--reason", "lab upgrade"]) == 0
    out = capsys.readouterr()
    assert "the registry recommends lean-pkg 0.22.0" in out.out and NOTICE not in out.err
    reg = read_reg(env)
    assert reg["lean_pkg"] == {"recommended": "0.22.0"}
    assert reg["packages"] == before["packages"] and reg["version"] == before["version"]   # nothing else
    ev = read_events(env)
    assert ev[-1]["event"] == "registry-recommended" and ev[-1]["name"] is None
    assert ev[-1]["recommended"] == "0.22.0" and ev[-1]["previous"] is None and ev[-1]["reason"] == "lab upgrade"
    assert all(h["event"] != "registry-recommended" for p in reg["packages"].values() for h in p["history"])
    # raising it records the previous value; the same value again writes nothing
    assert lp.main(["--json", "--registry", str(env.registry), "registry", "recommend", "0.23.0"]) == 0
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["recommended"] == "0.23.0" and payload["previous"] == "0.22.0"
    assert payload["event"] == "registry-recommended"
    assert payload["lean_pkg_skew"] == {"running": "0.22.0", "recommended": "0.23.0"}   # the setter writes too
    assert out.err.count(NOTICE) == 1
    n = len(read_events(env))
    assert lp.main(["--registry", str(env.registry), "registry", "recommend", "0.23.0"]) == 0
    assert "nothing written" in capsys.readouterr().out
    assert len(read_events(env)) == n


@pytest.mark.parametrize("bad", ["0.23", "v0.23.0", "0.23.0.dev1", "latest", ""])
def test_recommend_refuses_a_non_version(lp, env, capsys, bad):
    before = env.registry.read_text()
    assert lp.main(["--registry", str(env.registry), "registry", "recommend", bad]) == 2
    assert "VERSION must be X.Y.Z" in capsys.readouterr().err
    assert env.registry.read_text() == before


def test_recommend_is_a_write(lp, env):
    from lean_pkg import cli
    args = cli.build_parser().parse_args(["registry", "recommend", "0.23.0"])
    assert cli.is_write_invocation(args) and cli.locks_registry(args)
    args = cli.build_parser().parse_args(["registry", "validate"])
    assert not cli.is_write_invocation(args)


def test_an_older_writer_keeps_the_key(lp, env, running):
    # every save writes back the mapping it loaded: a lean-pkg that does not know the key keeps it
    recommend(env, "0.22.0")
    assert claim(lp, env) == 0
    assert read_reg(env)["lean_pkg"] == {"recommended": "0.22.0"}


# ---- the notice ------------------------------------------------------------------------------------------

def test_notice_on_a_writing_command_when_older(lp, env, running, capsys):
    recommend(env, "0.23.0")
    assert claim(lp, env) == 0
    err = capsys.readouterr().err
    assert err.count(NOTICE) == 1
    assert ("lean-pkg: notice: this is lean-pkg 0.22.0; the registry recommends 0.23.0 (upgrade: pipx install "
            "--force 'git+https://github.com/lean-pkg/lean-pkg.git@v0.23.0')") in err


def test_notice_names_the_checkout_for_an_editable_install(lp, env, running, monkeypatch, capsys, tmp_path):
    from lean_pkg import cli
    monkeypatch.setattr(cli, "install_detect", lambda d: {"install": "editable", "path": str(tmp_path / "co"),
                                                         "commit": "abc", "dirty": False})
    recommend(env, "0.23.0")
    assert claim(lp, env) == 0
    assert f"(upgrade: git -C {tmp_path / 'co'} pull --ff-only)" in capsys.readouterr().err


@pytest.mark.parametrize("rec", ["0.22.0", "0.21.0", None, "0.23"])
def test_no_notice_when_equal_newer_absent_or_malformed(lp, env, running, capsys, rec):
    if rec is not None:
        recommend(env, rec)
    assert claim(lp, env) == 0
    assert NOTICE not in capsys.readouterr().err


def test_notice_never_refuses_and_keeps_the_exit_code(lp, env, running, capsys):
    recommend(env, "9.0.0")
    assert claim(lp, env) == 0
    capsys.readouterr()
    # a refused write keeps its exit code, and still says it
    assert lp.main(["--registry", str(env.registry), "lease", "claim", "alpha", "--workspace", "w",
                    "--agent", "other"]) == 1
    assert capsys.readouterr().err.count(NOTICE) == 1


def test_no_notice_on_read_only_commands(lp, env, running, capsys):
    recommend(env, "0.23.0")
    for argv in (["list"], ["status", "alpha"], ["registry", "validate"], ["lease", "list"],
                 ["register", "gamma", "--url", "https://example.com/g.git", "--path", "g", "--dry-run"],
                 ["gate", "beta", "--check", "--receipt", "nope.json"]):
        lp.main(["--registry", str(env.registry), *argv])
        assert NOTICE not in capsys.readouterr().err, argv


def test_receipt_producers_get_the_notice_their_read_only_forms_do_not(lp, env, running, capsys, monkeypatch):
    from lean_pkg import cli, recommended
    recommend(env, "0.23.0")
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(env.registry))

    def parse(*argv):
        return cli.build_parser().parse_args(list(argv))
    assert recommended.notices_for(parse("receipt", "slurm", "--job", "1", "--log", "l", "--receipt-commit", "c"))
    assert recommended.notices_for(parse("receipt", "comparator", "--check", "/x/comparator-check.sh"))
    assert not recommended.notices_for(parse("receipt", "slurm", "--template"))
    assert not recommended.notices_for(parse("receipt", "slurm", "--check", "--log", "l"))
    assert not recommended.notices_for(parse("receipt", "comparator", "--estimate"))
    assert not recommended.notices_for(parse("receipt", "check", "--repo", ".", "--commit", "c"))
    # a receipt producer reads no registry itself: the notice reads the resolved one
    lp.main(["receipt", "slurm", "--job", "1", "--log", "missing.log", "--receipt-commit", "c"])
    assert capsys.readouterr().err.count(NOTICE) == 1


def test_receipt_producer_without_a_registry_is_silent(lp, tmp_path, running, capsys, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_REGISTRY", str(tmp_path / "none" / "lean-packages.yaml"))
    lp.main(["receipt", "slurm", "--job", "1", "--log", "missing.log", "--receipt-commit", "c"])
    assert NOTICE not in capsys.readouterr().err


# ---- --json ----------------------------------------------------------------------------------------------

def test_json_carries_lean_pkg_skew_only_when_older(lp, env, running, capsys):
    recommend(env, "0.23.0")
    assert claim(lp, env, "--json") == 0
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["lean_pkg_skew"] == {"running": "0.22.0", "recommended": "0.23.0"}
    assert out.err.count(NOTICE) == 1
    assert lp.main(["--json", "--registry", str(env.registry), "lease", "release", "alpha", "--agent", "a"]) == 0
    assert "lean_pkg_skew" in json.loads(capsys.readouterr().out)
    recommend(env, "0.22.0")
    assert claim(lp, env, "--json") == 0
    assert "lean_pkg_skew" not in json.loads(capsys.readouterr().out)


def test_json_refusal_carries_lean_pkg_skew(lp, env, running, capsys):
    recommend(env, "0.23.0")
    assert claim(lp, env) == 0
    capsys.readouterr()
    assert lp.main(["--json", "--registry", str(env.registry), "lease", "claim", "alpha", "--workspace", "w",
                    "--agent", "other"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["lean_pkg_skew"]["recommended"] == "0.23.0"


# ---- doctor ----------------------------------------------------------------------------------------------

def _doctor_line(lp, env, capsys) -> dict:
    lp.main(["--json", "--registry", str(env.registry), "doctor"])
    checks = json.loads(capsys.readouterr().out)["checks"]
    return next(c for c in checks if c["item"] == "recommended")


@pytest.mark.parametrize("rec, level, text", [
    ("0.23.0", "warn", "this is lean-pkg 0.22.0, older than the 0.23.0 the registry recommends (upgrade: "),
    ("0.22.0", "ok", "lean-pkg 0.22.0; the registry recommends 0.22.0"),
    ("0.21.0", "ok", "the registry recommends 0.21.0"),
    (None, "n/a", "the registry recommends no lean-pkg version"),
    ("0.23", "warn", "lean_pkg.recommended: must be a version string X.Y.Z"),
])
def test_doctor_recommended_line(lp, env, running, capsys, rec, level, text):
    if rec is not None:
        recommend(env, rec)
    line = _doctor_line(lp, env, capsys)
    assert line["level"] == level and text in line["detail"], line
    # never an exit 1 of its own
    assert lp.main(["--registry", str(env.registry), "doctor"]) == 0
    assert f"{level} recommended: " in capsys.readouterr().out


def test_doctor_without_a_registry_has_no_recommended_line(lp, tmp_path, capsys):
    lp.main(["--json", "--registry", str(tmp_path / "nope.yaml"), "doctor"])
    checks = json.loads(capsys.readouterr().out)["checks"]
    assert not [c for c in checks if c["item"] == "recommended"]


def test_schema_version_stays_3(lp, tmp_path, running):
    reg = tmp_path / "lean-packages.yaml"
    assert lp.main(["--registry", str(reg), "registry", "init"]) == 0
    assert lp.main(["--registry", str(reg), "registry", "recommend", "0.22.0"]) == 0
    data = yaml.safe_load(reg.read_text())
    assert data["version"] == 3 and data["lean_pkg"] == {"recommended": "0.22.0"}


def test_nested_invocations_print_one_notice(lp, env, running, monkeypatch, capsys):
    # `verify` runs its steps (receipts, the status move, the gate) as nested invocations of main: one notice
    from lean_pkg import cli
    recommend(env, "0.23.0")
    reg = ["--registry", str(env.registry)]

    def outer(args):
        assert cli.main([*reg, "lease", "claim", "alpha", "--workspace", "w", "--agent", "a"]) == 0
        assert cli.main([*reg, "lease", "release", "alpha", "--agent", "a"]) == 0
        return 0
    monkeypatch.setattr(cli, "cmd_status", outer)
    assert lp.main([*reg, "status", "alpha"]) == 0
    assert capsys.readouterr().err.count(NOTICE) == 1
    assert claim(lp, env) == 0                          # the next top-level invocation says it again
    assert capsys.readouterr().err.count(NOTICE) == 1
