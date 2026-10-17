#!/usr/bin/env python3
"""llms: the agent-oriented usage contract."""

from __future__ import annotations

import json

import pytest


def test_llms_exits_zero_and_covers_contract(lp, capsys):
    rc = lp.main(["llms"])
    assert rc == 0
    out = capsys.readouterr().out
    for cmd in ("board", "list", "status", "init", "register", "claim", "release",
                "gate", "promote", "audit", "validate", "llms", "wiki"):
        assert cmd in out, f"subcommand {cmd!r} missing from llms text"
    for field in ("commit", "lean_toolchain", "mathlib_rev", "sorry_count",
                  "axioms", "report_sha256", "job_id", "checker",
                  "generated_at"):
        assert field in out, f"receipt field {field!r} missing from llms text"
    for rule in ("NEVER hand-edit", "TTL", "dry-run", "EXIT CODES"):
        assert rule in out
    assert "terminal executor job identity" in out
    assert "The gate is executor-neutral" in out
    assert "A pilot," in out
    assert "partial manifest" in out
    assert len(out.splitlines()) <= 180    # raised from 170 when renew/leases landed (#10), 176 for next (#42), 177 for new (#195),
    #                                        180 for the 1.0 tree's two standing rules and verify (stage 2)


def test_llms_mentions_event_names_and_status_enum(lp, capsys):
    lp.main(["llms"])
    out = capsys.readouterr().out
    for event in ("registered", "claimed", "gate-passed", "promoted",
                  "lease-expired", "audit-fix"):
        assert event in out
    for status in ("scratch", "active", "gate-pending", "verified-private",
                   "public", "upstreamed", "archived"):
        assert status in out


def test_llms_documents_comparator_tier(lp, capsys):
    assert lp.main(["llms"]) == 0
    out = capsys.readouterr().out
    for needle in ("--comparator", "comparator attach NAME", "comparator-receipt/1",
                   "Your solution is okay!", "required by promote",
                   "comparator-attached"):
        assert needle in out, needle


def test_llms_documents_index(lp, capsys):
    assert lp.main(["llms"]) == 0
    out = capsys.readouterr().out
    for needle in ("index     public catalog", "LEAN_PKG_INDEX", "index-entry/1", "index-added",
                   "computed, never typed", '"skipped"', "check [NAME ...]", "render [--out PATH]"):
        assert needle in out, needle


def test_llms_documents_claims_and_search(lp, capsys):
    assert lp.main(["llms"]) == 0
    out = capsys.readouterr().out
    for needle in ("claims    claims.yaml", "claims/1", "sync NAME", "search    TERM", "--offline", "--record",
                   "registry, index, loogle, leansearch, reservoir, palomar, github", "skipped: <reason>",
                   "decision: pending", "claims-synced", "prior-art-searched", "index-entry/2",
                   "v3 adds claims_file"):
        assert needle in out, needle


def test_llms_documents_audit_reference_branch(lp, capsys):
    assert lp.main(["llms"]) == 0
    out = capsys.readouterr().out
    for needle in ("reference branch", "origin/HEAD", "master", "checkout-branch", "--version",
                   "below-toolchain-floor", "no-github-mirror", "toolchain-unparsed"):
        assert needle in out, needle


def _full_table_commands(text: str) -> list[str]:
    """The command names the full SUBCOMMANDS table lists: the first word of each column-0 line."""
    lines = text.splitlines()
    start = next(k for k, line in enumerate(lines) if line.startswith("SUBCOMMANDS "))
    end = next(k for k in range(start + 1, len(lines)) if not lines[k].strip())
    return [line.split()[0] for line in lines[start + 1:end] if line[:1].strip()]


def test_llms_short_is_a_compact_subset_of_the_full_contract(lp, capsys):
    assert lp.main(["llms", "--short"]) == 0
    short = capsys.readouterr().out
    full = lp.LLMS_TEXT.splitlines()
    lines = short.splitlines()
    assert short == lp.llms_short()
    assert [line for line in lines if line not in full] == []          # line by line, nothing hand-written
    assert len(lines) < 60
    for header in ("PURPOSE", "STANDING RULES"):
        assert header in lines
    assert any(line.startswith("SUBCOMMANDS ") for line in lines)
    for absent in ("RECEIPT SCHEMA", "COMPARATOR RECEIPT SCHEMA", "EVENT LINE SCHEMA", "EXIT CODES"):
        assert not any(line.startswith(absent) for line in lines), absent
    assert not any(line.lstrip().startswith("ex: ") for line in lines)  # no example-only lines
    table = _full_table_commands(lp.LLMS_TEXT)
    assert "next" in table and "gate" in table
    for cmd in table:
        assert any(line.split()[:1] == [cmd] for line in lines), cmd


def test_llms_short_names_every_subcommand(lp, capsys):
    """Every subcommand argparse knows is at least named in the short text (a pointer to the full one)."""
    import re
    with pytest.raises(SystemExit):
        lp.main(["--help"])
    names = re.findall(r"^  ([a-z][a-z-]+)  ", capsys.readouterr().out, re.M)   # the tiered root help
    assert len(names) >= 25
    short = lp.llms_short()
    for name in names:
        assert re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", short), name


def test_llms_short_json(lp, capsys):
    assert lp.main(["llms", "--short", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "text": lp.llms_short(), "short": True}
    assert lp.main(["llms", "--json"]) == 0                              # the full form keeps its shape
    assert json.loads(capsys.readouterr().out) == {"ok": True, "text": lp.LLMS_TEXT}


def test_llms_short_fails_loud_without_its_section_markers(lp):
    with pytest.raises(ValueError):
        lp.llms_short("title\n\nPURPOSE\na\n")
