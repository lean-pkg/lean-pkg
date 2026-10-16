#!/usr/bin/env python3
"""Golden CLI contract: the compatibility corpus parses.

tests/data/compat-corpus.txt holds scrubbed `lean-pkg ...` invocations quoted by agent skills, runbooks
and wrapper scripts (scripts/extract-compat-corpus.py). Each line is parsed with the real parser, never
run. A line that does not parse today is listed in tests/data/compat-corpus-unparseable.yaml with the
reason; every other line must parse. Both directions are checked, so a refactor that breaks a quoted
spelling fails here, and so does one that starts accepting a listed line (update the list on purpose)."""

from __future__ import annotations

import shlex

import pytest
import yaml

from .cli_contract import canonical, parse_only, parser
from .conftest import ROOT
from .test_cli_contract_argv import ARGV_TABLE

CORPUS = ROOT / "tests" / "data" / "compat-corpus.txt"
UNPARSEABLE = ROOT / "tests" / "data" / "compat-corpus-unparseable.yaml"


def corpus_lines() -> list[str]:
    return [line for line in CORPUS.read_text().splitlines() if line.strip() and not line.startswith("#")]


def allowlist() -> dict[str, str]:
    data = yaml.safe_load(UNPARSEABLE.read_text()) or {}
    return {e["line"]: e["reason"] for e in data.get("unparseable", [])}


@pytest.fixture(scope="module")
def results():
    ap = parser()
    out = {}
    for line in corpus_lines():
        words = shlex.split(line)
        assert words[0] == "lean-pkg", line
        out[line] = parse_only(words[1:], ap)
    return out


def test_corpus_is_substantial():
    lines = corpus_lines()
    assert len(lines) >= 90, len(lines)
    assert len(set(lines)) == len(lines), "duplicate corpus lines"
    assert lines == sorted(lines), "keep the corpus sorted (the extractor writes it sorted)"


def test_corpus_lines_parse_except_the_listed_ones(results):
    allowed = allowlist()
    failing = {line for line, r in results.items() if not r.ok}
    new = sorted(failing - set(allowed))
    fixed = sorted(set(allowed) - failing)
    assert new == [], "corpus lines that no longer parse:\n" + "\n".join(
        f"{line}\n    {results[line].error.splitlines()[-1]}" for line in new)
    assert fixed == [], "listed as unparseable but parse now (remove them from the list):\n" + "\n".join(fixed)


def test_allowlist_entries_are_corpus_lines_with_reasons():
    lines = set(corpus_lines())
    for line, reason in allowlist().items():
        assert line in lines, f"not in the corpus: {line}"
        assert reason and len(reason) > 10, f"give a reason: {line}"


def test_every_parsed_corpus_command_is_in_the_argv_table(results):
    """The corpus exercises only commands the argv table specifies (so later phases cover it)."""
    known = {row[1] for row in ARGV_TABLE}
    ap = parser()
    missing = sorted({canonical(r.args, ap) for r in results.values() if r.args is not None} - known)
    assert missing == []


def test_corpus_carries_no_private_paths():
    """Scrubbed: no path separators or home markers survive (the boundary check covers names)."""
    text = "\n".join(corpus_lines())
    for marker in ("/", "~", "$", "@"):
        assert marker not in text, marker
