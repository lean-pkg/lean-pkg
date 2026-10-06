#!/usr/bin/env python3
"""llms: the agent-oriented usage contract."""

from __future__ import annotations


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
    assert len(out.splitlines()) <= 160


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
    for needle in ("--comparator", "comparator <name>", "comparator-receipt/1",
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
