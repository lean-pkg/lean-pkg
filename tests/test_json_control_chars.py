#!/usr/bin/env python3
"""--json escapes control characters: registry text with C0 controls, DEL and U+2028/U+2029 in notes and
history parses with strict json.loads (Python's default, which rejects a raw control character inside a
string) and round-trips to the same strings.

Every case of the --json contract table (tests/test_cli_contract_json.py CASES and LEASED) runs on such
a registry, so a new command printing registry text through a non-JSON path fails here."""

from __future__ import annotations

import json

import pytest

from .conftest import read_reg, set_lease, write_reg
from .test_cli_contract_json import CASES, LEASED

# every C0 control (tab, newline, carriage return and NUL among them), DEL, and the two Unicode line
# separators, which ECMAScript before 2019 refused raw in a string
CONTROLS = "".join(chr(c) for c in range(0x20)) + "\x7f  "
NOTES = f"first line\nsecond\tline\r\nnul\x00here {CONTROLS} end"
REASON = f"history\treason\nwith\x0bcontrols\x1b[0m {CONTROLS}"


def _poison(env) -> None:
    reg = read_reg(env)
    for name, pkg in reg["packages"].items():
        pkg["notes"] = NOTES
        pkg["history"] = [{"ts": "2026-10-18T00:00:00+00:00", "event": "updated", "agent": "ada",
                           "fields": ["notes"], "notes_from": NOTES, "reason": REASON}]
    write_reg(env, reg)


def _strict(out: str):
    payload = json.loads(out)          # strict=True: a raw control character inside a string raises
    assert not any(ord(c) < 0x20 and c != "\n" for c in out), "raw control character on --json stdout"
    return payload


def _run(lp, env, argv, capsys):
    capsys.readouterr()
    try:
        lp.main(["--registry", str(env.registry), "--json", *argv])
    except SystemExit:
        pass
    return _strict(capsys.readouterr().out)


@pytest.fixture()
def poisoned(env):
    _poison(env)
    return env


def test_poisoned_registry_keeps_the_characters(poisoned):
    """The fixture really carries the characters through the YAML registry (else the tests prove nothing)."""
    pkg = read_reg(poisoned)["packages"]["alpha"]
    assert pkg["notes"] == NOTES and pkg["history"][0]["reason"] == REASON


def test_status_json_round_trips_notes_and_history(lp, poisoned, capsys):
    data = _run(lp, poisoned, ["status", "alpha"], capsys)
    assert data["package"]["notes"] == NOTES
    h = data["package"]["history"][0]
    assert h["notes_from"] == NOTES and h["reason"] == REASON


@pytest.mark.parametrize("cmd", ["list", "board"])
def test_registry_dump_json_round_trips(lp, poisoned, capsys, monkeypatch, cmd):
    monkeypatch.setattr(lp, "board_interactive", lambda: False)
    data = _run(lp, poisoned, [cmd], capsys)
    for name in ("alpha", "beta"):
        assert data["packages"][name]["notes"] == NOTES
        assert data["packages"][name]["history"][0]["reason"] == REASON


def test_update_notes_with_controls_round_trips(lp, env, capsys):
    """A write: update --notes with the characters, then status --json reads them back unchanged."""
    _run(lp, env, ["update", "alpha", "--notes", NOTES, "--agent", "ada"], capsys)
    assert _run(lp, env, ["status", "alpha"], capsys)["package"]["notes"] == NOTES


def test_every_json_case_is_strict_json_on_a_poisoned_registry(lp, poisoned, tmp_path, capsys, fake_gh,
                                                               monkeypatch):
    """Each command of the --json contract table prints strict-parseable JSON (no raw control character)."""
    monkeypatch.setattr(lp, "board_interactive", lambda: False)
    failed = []
    for case, argv in [*CASES.items(), *LEASED.items()]:
        reg_text = poisoned.registry.read_text()
        if case in LEASED:
            set_lease(poisoned, "alpha")
        argv = argv(poisoned, tmp_path) if callable(argv) else argv
        try:
            _run(lp, poisoned, argv, capsys)
        except (ValueError, AssertionError) as e:
            failed.append(f"{case}: {e}")
        poisoned.registry.write_text(reg_text)
        if poisoned.events.exists():
            poisoned.events.unlink()
    assert failed == []
