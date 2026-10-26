#!/usr/bin/env python3
"""Command-line consistency (#316): the commit flags in the help of write commands only, and --repo for the
package checkout on every command and program, --project kept as a permanent alias."""

from __future__ import annotations

import argparse

import pytest

from lean_pkg import claim_axioms as ca
from lean_pkg import command_tree as ct
from lean_pkg import comparator_receipt as cr
from lean_pkg import dup_constants as dc

from .cli_contract import command_tree, parse_only, parser
from .test_cli_contract_argv import ARGV_TABLE

NOTE = "(the old spelling keeps working)"


@pytest.fixture(scope="module")
def ap():
    return parser()


def _commit_actions(p) -> list:
    return [a for a in p._actions if getattr(a, "commit_flag", False)]


def _shown(p) -> bool:
    return any(a.help is not argparse.SUPPRESS for a in _commit_actions(p))


# --------------------------------------------------------------------------
# the commit flags


def test_commit_flags_shown_exactly_where_some_invocation_writes(ap):
    """The help split (derived from is_write_invocation by may_write) agrees with the hand-written ARGV_TABLE:
    a command shows --commit/--push/--no-commit iff one of its rows writes (verify: passes them on); a group
    shows them iff one of its subcommands does."""
    lp_writes: dict[str, bool] = {}
    for argv, cmd, writes, _locks, _tty in ARGV_TABLE:
        lp_writes[cmd] = lp_writes.get(cmd, False) or writes
    nodes = [n for n in command_tree(ap) if n.path and _commit_actions(n.parser)]
    assert nodes
    wrong = []
    for n in nodes:
        path = " ".join(n.path)
        kids = [k for k in command_tree(ap) if len(k.path) == len(n.path) + 1 and k.path[:len(n.path)] == n.path]
        if kids:
            want = any(_shown(k.parser) for k in kids)
        else:
            want = lp_writes[path] or path == "verify"
        if _shown(n.parser) != want:
            wrong.append(path)
        assert len({a.help is argparse.SUPPRESS for a in _commit_actions(n.parser)}) == 1   # all three, or none
    assert wrong == []


def test_read_only_help_hides_commit_flags(lp, capsys):
    for words in (["list"], ["next"], ["public-safety"], ["claims", "check"]):
        with pytest.raises(SystemExit):
            lp.main(words + ["--help"])
        out = capsys.readouterr().out
        assert "--json" in out
        assert "--commit" not in out and "--push" not in out and "--no-commit" not in out, words


def test_write_help_shows_commit_flags(lp, capsys):
    for words in (["gate"], ["register"], ["lease", "claim"], ["claims", "sync"], ["verify"]):
        with pytest.raises(SystemExit):
            lp.main(words + ["--help"])
        out = capsys.readouterr().out
        assert "--commit" in out and "--push" in out and "--no-commit" in out, words


@pytest.mark.parametrize("argv", [
    ["list", "--commit"], ["list", "--push"], ["list", "--no-commit"], ["--commit", "list"],
    ["next", "demo", "--commit", "--json"], ["claims", "check", "--push"], ["public-safety", "demo", "--no-commit"],
])
def test_read_only_commands_still_accept_commit_flags(argv, ap):
    res = parse_only(argv, ap)
    assert res.ok, res.error


def test_commit_flag_on_read_only_command_is_noted_not_refused(lp, capsys, tmp_path):
    reg = str(tmp_path / "r.yaml")
    assert lp.main(["--registry", reg, "registry", "init"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", reg, "list", "--commit"]) == 0
    assert "--commit ignored: read-only command" in capsys.readouterr().err
    assert lp.main(["--registry", reg, "--commit", "list"]) == 0           # the global spelling, as before
    assert "--commit ignored: read-only command" in capsys.readouterr().err


# --------------------------------------------------------------------------
# --repo, --project an alias


def test_receipt_comparator_project_is_repo(ap):
    a = parse_only(["receipt", "comparator", "--project", "d", "--prepare"], ap)
    b = parse_only(["receipt", "comparator", "--repo", "d", "--prepare"], ap)
    assert a.ok and b.ok and vars(a.args) == vars(b.args) and b.args.project == "d"


def test_receipt_comparator_alias_note(lp, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv(ct.NOTE_ENV, raising=False)
    d = str(tmp_path)
    assert lp.main(["receipt", "comparator", "--project", d, "--attempts", d]) == 0
    assert (f"note: 'lean-pkg receipt comparator --project' is now 'lean-pkg receipt comparator --repo' {NOTE}"
            in capsys.readouterr().err)
    assert lp.main(["receipt", "comparator", "--repo", d, "--attempts", d]) == 0
    assert "note:" not in capsys.readouterr().err                          # the canonical spelling: silent
    assert lp.main(["receipt", "comparator", "--project", d, "--attempts", d, "--json"]) == 0
    assert "note:" not in capsys.readouterr().err                          # its own --json: never a note
    monkeypatch.setenv(ct.NOTE_ENV, "1")
    assert lp.main(["receipt", "comparator", "--project", d, "--attempts", d]) == 0
    assert "note:" not in capsys.readouterr().err


@pytest.mark.parametrize("mod,program", [(cr, "comparator-receipt"), (dc, "python3 -m lean_pkg.dup_constants"),
                                         (ca, "python3 -m lean_pkg.claim_axioms")])
def test_programs_spell_the_checkout_repo(mod, program, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv(ct.NOTE_ENV, raising=False)
    nope = str(tmp_path / "nope")
    tail = {cr: ["--attempts", str(tmp_path)], dc: ["M"], ca: ["X.y", "--module", "X"]}[mod]
    rc_old = mod.main(["--project", nope] + tail)
    err = capsys.readouterr().err
    assert f"note: '{program} --project' is now '{program} --repo' {NOTE}" in err
    rc_new = mod.main(["--repo", nope] + tail)
    assert rc_new == rc_old and "note:" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        mod.main(["--help"])
    out = capsys.readouterr().out
    out = " ".join(out.split())   # help wraps at the terminal width
    assert "--repo DIR" in out and "old spelling --project, kept working" in out


def test_program_aliases_listed_in_help_aliases():
    text = ct.aliases_text()
    for fa in ct.PROGRAM_FLAG_ALIASES:
        assert f"{fa.command[0]} {fa.old}" in text
