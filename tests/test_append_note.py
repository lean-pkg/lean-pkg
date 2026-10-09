#!/usr/bin/env python3
"""update --append-note TEXT keeps the existing notes; --notes says what it replaced (#32)."""

from __future__ import annotations

import datetime as dt
import json

from .conftest import read_reg, write_reg


def _update(lp, env, *argv) -> int:
    return lp.main(["--registry", str(env.registry), "update", *argv])


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def _set_notes(env, text):
    reg = read_reg(env)
    reg["packages"]["alpha"]["notes"] = text
    write_reg(env, reg)


def test_append_note_adds_a_dated_signed_line(lp, env, capsys):
    _set_notes(env, "prior note")
    assert _update(lp, env, "alpha", "--append-note", "upstream removed; checkout is the copy",
                   "--agent", "agent-7") == 0
    notes = read_reg(env)["packages"]["alpha"]["notes"]
    assert notes == f"prior note\n{_today()} (agent-7): upstream removed; checkout is the copy"
    assert "use --append-note" not in capsys.readouterr().out
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_append_note_creates_notes_when_absent(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"].pop("notes")
    write_reg(env, reg)
    assert _update(lp, env, "alpha", "--append-note", "first", "--agent", "a") == 0
    assert read_reg(env)["packages"]["alpha"]["notes"] == f"{_today()} (a): first"


def test_append_note_refusals(lp, env, capsys):
    before = env.registry.read_text()
    assert _update(lp, env, "alpha", "--append-note", "x", "--notes", "y", "--agent", "a") == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert _update(lp, env, "alpha", "--append-note", "x") == 2
    assert "--agent" in capsys.readouterr().err
    assert env.registry.read_text() == before


def test_notes_replacement_shows_the_previous_value_and_the_hint(lp, env, capsys):
    _set_notes(env, "prior note")
    assert _update(lp, env, "alpha", "--notes", "new note") == 0
    out = capsys.readouterr().out
    assert "notes: 'prior note' -> 'new note'" in out
    assert "replaced notes were: 'prior note'" in out
    assert "use --append-note to keep the previous note" in out
    _set_notes(env, "prior note")
    assert _update(lp, env, "alpha", "--notes", "other", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"]["notes"]["from"] == "prior note"
    assert payload["hint"] == "use --append-note to keep the previous note"
