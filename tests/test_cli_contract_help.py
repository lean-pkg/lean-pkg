#!/usr/bin/env python3
"""Golden CLI contract: the `--help` text of every command and subcommand.

tests/golden/help/<command path>.txt holds each text, rendered at a fixed width with colour off and the
version number replaced by <VERSION>. The comparison is word by word (runs of whitespace are one
separator): Python versions lay out the same help differently (3.11 indents an aliased subcommand's
summary on a line of its own, 3.11/3.12 wrap a long `{a,b,...} ...` usage line elsewhere), and the
contract is the words and their order, not the column layout. A change to any word fails here until
the goldens are re-recorded on purpose: `make goldens` (LEAN_PKG_RECORD_GOLDENS=1), then review the
diff."""

from __future__ import annotations

import re

import pytest

from .cli_contract import cli, command_tree, parser, recording
from .conftest import ROOT

HELP_DIR = ROOT / "tests" / "golden" / "help"
WIDTH = "100"


def golden_name(path: tuple[str, ...]) -> str:
    return ".".join(("lean-pkg",) + path) + ".txt"


def normalise(text: str) -> str:
    """The text as recorded: the release number is not part of the help contract."""
    from lean_pkg import __version__
    text = text.replace(__version__, "<VERSION>")
    text = re.sub(r"[ ]+$", "", text, flags=re.M)
    return text.rstrip("\n") + "\n"


def words(text: str) -> list[str]:
    return text.split()


@pytest.fixture()
def helps(monkeypatch) -> dict[str, str]:
    """{golden file name: normalised help text} for every command path, root included."""
    monkeypatch.setenv("COLUMNS", WIDTH)
    monkeypatch.setenv("LINES", "50")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("PYTHON_COLORS", "0")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    ap = parser()
    return {golden_name(n.path): normalise(n.parser.format_help()) for n in command_tree(ap)}


def test_help_texts_match_goldens(helps):
    if recording():
        HELP_DIR.mkdir(parents=True, exist_ok=True)
        for old in HELP_DIR.glob("*.txt"):
            if old.name not in helps:
                old.unlink()
        for name, text in helps.items():
            (HELP_DIR / name).write_text(text)
    changed = [name for name, text in helps.items()
               if not (HELP_DIR / name).is_file() or words((HELP_DIR / name).read_text()) != words(text)]
    assert changed == [], ("help text changed (or a command has no golden yet); if intended, run "
                           "`make goldens` and review the diff: " + ", ".join(sorted(changed)))


def test_no_stale_help_goldens(helps):
    stale = sorted(p.name for p in HELP_DIR.glob("*.txt") if p.name not in helps)
    assert stale == [], "goldens for commands the parser no longer has: " + ", ".join(stale)


def test_help_goldens_cover_the_whole_tree(helps):
    """The walk itself works: the root, a top-level command, a nested one and an alias target."""
    for name in ("lean-pkg.txt", "lean-pkg.gate.txt", "lean-pkg.claims.sync.txt",
                 "lean-pkg.receipt.systemd.txt"):
        assert name in helps, name
    assert len(helps) >= 40


def test_version_is_normalised(helps):
    from lean_pkg import __version__
    assert all(__version__ not in t for t in helps.values())
    assert "<VERSION>" in helps["lean-pkg.txt"]


def test_help_does_not_depend_on_import_state():
    """Two parsers built in a row render the same root help (no state leaks between builds)."""
    assert cli()  # the module imports
    a = parser().format_help()
    b = parser().format_help()
    assert a == b
