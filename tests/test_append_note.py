#!/usr/bin/env python3
"""update --append-note TEXT keeps the existing notes (#32); --notes over existing notes is refused unless
--replace-notes, which says what it replaced (#53)."""

from __future__ import annotations

import datetime as dt
import json

from .conftest import read_events, read_reg, write_reg


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
    assert _update(lp, env, "alpha", "--notes", "new note", "--replace-notes") == 0
    out = capsys.readouterr().out
    assert "notes: 'prior note' -> 'new note'" in out
    assert "replaced notes were: 'prior note'" in out
    assert "use --append-note to keep the previous note" in out
    _set_notes(env, "prior note")
    assert _update(lp, env, "alpha", "--notes", "other", "--replace-notes", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"]["notes"]["from"] == "prior note"
    assert payload["hint"] == "use --append-note to keep the previous note"


def test_notes_over_existing_notes_is_refused_without_a_write(lp, env, capsys):   # #53
    """Field report (0.7.19): `update --notes` destroyed a multi-line note and only warned afterwards. Over
    non-empty notes it is now refused (exit 2, nothing written, no event) unless --replace-notes."""
    _set_notes(env, "line one\nline two")
    before = env.registry.read_text()
    n = len(read_events(env))
    assert _update(lp, env, "alpha", "--notes", "new note") == 2
    err = capsys.readouterr().err
    assert "alpha: --notes would replace the existing notes (2 line(s)); nothing written" in err
    assert "--append-note TEXT --agent A" in err and "--replace-notes" in err and "notes_from" in err
    assert env.registry.read_text() == before and len(read_events(env)) == n
    assert _update(lp, env, "alpha", "--notes", "", "--json") == 2   # clearing is a replacement too
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["usage"] is True
    assert "--replace-notes" in payload["error"] and "notes_from" in payload["error"]
    assert env.registry.read_text() == before and len(read_events(env)) == n


def test_replace_notes_replaces_and_the_event_records_it(lp, env, capsys):   # #53
    _set_notes(env, "line one\nline two")
    assert _update(lp, env, "alpha", "--notes", "new note", "--replace-notes") == 0
    assert read_reg(env)["packages"]["alpha"]["notes"] == "new note"
    ev = [e for e in read_events(env) if e["event"] == "updated"][-1]
    assert ev["fields"] == ["notes"] and ev["notes_replaced"] is True
    assert ev["notes_from"] == "line one\nline two"   # the recovery path the refusal names
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_replace_notes_needs_notes(lp, env, capsys):
    before = env.registry.read_text()
    assert _update(lp, env, "alpha", "--replace-notes") == 2
    assert "--replace-notes goes with --notes TEXT" in capsys.readouterr().err
    assert _update(lp, env, "alpha", "--replace-notes", "--append-note", "x", "--agent", "a") == 2
    assert env.registry.read_text() == before


def test_notes_on_empty_notes_and_append_note_are_unchanged(lp, env, capsys):   # #53
    _set_notes(env, "")
    assert _update(lp, env, "alpha", "--notes", "first") == 0
    assert read_reg(env)["packages"]["alpha"]["notes"] == "first"
    ev = [e for e in read_events(env) if e["event"] == "updated"][-1]
    assert "notes_replaced" not in ev
    assert _update(lp, env, "alpha", "--notes", "first") == 0   # the same text: a no-op, not a refusal
    assert "nothing written" in capsys.readouterr().out
    assert _update(lp, env, "alpha", "--append-note", "second", "--agent", "a") == 0
    assert read_reg(env)["packages"]["alpha"]["notes"] == f"first\n{_today()} (a): second"


def test_append_note_echoes_only_the_appended_line(lp, env, capsys):   # #69
    """Field report (2026-10-10): the text output printed the whole notes field before and after, burying the
    result. It now names only the appended line; the event and --json keep the full before/after."""
    long_notes = "\n".join(f"history line {i}: " + "x" * 80 for i in range(60))
    _set_notes(env, long_notes)
    assert _update(lp, env, "alpha", "--append-note", "re-gated at abc123", "--agent", "a") == 0
    out = capsys.readouterr().out
    assert out == f"alpha: updated 1 field(s)\n  appended note: {_today()} (a): re-gated at abc123\n"
    assert "history line" not in out
    ev = [e for e in read_events(env) if e["event"] == "updated"][-1]
    assert ev["notes_from"] == long_notes
    _set_notes(env, long_notes)
    assert _update(lp, env, "alpha", "--append-note", "second", "--agent", "a", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"]["notes"] == {"from": long_notes, "to": f"{long_notes}\n{_today()} (a): second"}
    assert set(payload) == {"ok", "name", "event", "changes", "reason", "forced"}
