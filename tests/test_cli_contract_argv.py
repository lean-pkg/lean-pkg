#!/usr/bin/env python3
"""Golden CLI contract: argv -> (canonical command, writes?, locks the registry?).

ARGV_TABLE is the specification later refactors are checked against: one row per command spelling,
aliases and the flags that turn a write into a dry run (or a read into a write) included. Each row is
parsed with the real parser (parse only, nothing runs) and checked against today's predicates
`is_write_invocation` and `locks_registry`. The coverage tests fail when a command or alias is added
without a row here."""

from __future__ import annotations

import pytest

from .cli_contract import canonical, command_tree, comparator_keywords, parse_only, parser

R = "receipt.json"

# (argv, canonical command, writes, locks the registry for the whole command, board on a tty)
ARGV_TABLE: list[tuple[list[str], str, bool, bool, bool]] = [
    ([], "board", False, False, False),
    ([], "board", True, False, True),                      # the TUI writes, locking around each key
    (["board"], "board", False, False, False),
    (["board"], "board", True, False, True),
    (["board", "--json"], "board", False, False, True),    # a static view never writes
    (["board", "--only", "alpha"], "board", False, False, True),
    (["board", "--fields", "name"], "board", False, False, True),
    (["board", "--no-claims"], "board", False, False, True),
    (["list"], "list", False, False, False),
    (["list"], "list", False, False, True),
    (["help"], "help", False, False, False),               # help never reads the registry
    (["help", "exit-codes"], "help", False, False, False),
    (["help", "gate"], "help", False, False, False),
    (["status", "demo"], "status", False, False, False),
    (["new", "demo"], "new", False, False, False),                       # files in a new directory, never the registry
    (["new", "demo", "--no-mathlib", "--dir", "d"], "new", False, False, False),
    (["init"], "init", True, True, False),
    (["init", "--force"], "init", True, True, False),
    (["migrate"], "migrate", True, True, False),
    (["migrate", "--dry-run"], "migrate", False, False, False),
    (["record", "demo", "retract"], "record", True, True, False),
    (["register", "demo", "--repo", "r", "--path", "p"], "register", True, True, False),
    (["register", "demo", "--repo", "r", "--path", "p", "--dry-run"], "register", False, False, False),
    (["claim", "demo", "--workspace", "w", "--agent", "a"], "claim", True, True, False),
    (["lease", "demo", "--workspace", "w", "--agent", "a"], "claim", True, True, False),       # alias
    (["release", "demo"], "release", True, True, False),
    (["release", "demo", "--dry-run"], "release", False, False, False),
    (["release", "demo", "--fresh-repo", "d"], "release", True, True, False),
    (["release", "demo", "--fresh-repo", "d", "--dry-run"], "release", False, False, False),
    (["release", "demo", "--adopt-repo", "d"], "release", True, True, False),
    (["renew", "demo", "--agent", "a"], "renew", True, True, False),
    (["leases"], "leases", False, False, False),
    (["update", "demo", "--notes", "n"], "update", True, True, False),
    (["update", "demo", "--notes", "n", "--dry-run"], "update", False, False, False),
    (["update", "demo", "--headline-config", "h.json"], "update", True, True, False),
    (["update", "demo", "--headline-config", ""], "update", True, True, False),
    (["update", "demo", "--withdraw-publication", "--reason", "r"], "update", True, True, False),
    (["update", "demo", "--withdraw-publication", "--reason", "r", "--dry-run"], "update", False, False, False),
    (["update", "demo", "--release-publication-hold", "--reason", "r"], "update", True, True, False),
    (["gate", "demo", "--receipt", R], "gate", True, True, False),
    (["gate", "demo", "--receipt", R, "--check"], "gate", False, False, False),
    (["gate", "demo", "--receipt", R, "--regate"], "gate", True, True, False),
    (["gate", "demo", "--receipt", R, "--comparator", R], "gate", True, True, False),
    (["comparator", "demo", "--receipt", R], "comparator", True, True, False),
    (["comparator", "demo", "--receipt", R, "--check"], "comparator", False, False, False),
    (["comparator", "demo", "--receipt", R, "--check", "--repo", "d"], "comparator", False, False, False),
    (["comparator", "demo", "--receipt", R, "--replace"], "comparator", True, True, False),
    (["comparator", "host-check"], "comparator host-check", False, False, False),
    (["comparator", "host-check", "--routes"], "comparator host-check", False, False, False),
    (["comparator", "host-check", "--routes", "--json"], "comparator host-check", False, False, False),
    (["comparator", "host-check", "--receipt", R], "comparator", True, True, False),   # a package named so
    (["comparator", "config", "--from-claims", "demo"], "comparator config", False, False, False),
    (["comparator", "config", "--from-claims", "demo", "--claims-file", "c.yaml"], "comparator config",
     False, False, False),
    (["comparator", "config", "--from-claims", "demo", "--challenge-from-type", "--claims-file", "c.yaml"],
     "comparator config", False, False, False),
    (["comparator", "config", "--coverage", "demo"], "comparator config", False, False, False),
    (["comparator", "config", "--migrate-signoff", "comparator.json"], "comparator config", False, False, False),
    (["comparator", "config", "--migrate-signoff", "comparator.json", "--write"], "comparator config",
     False, False, False),                                  # writes the checkout's files only, never the registry
    (["comparator", "config", "--receipt", R], "comparator", True, True, False),
    (["promote", "demo"], "promote", False, False, False),
    (["promote", "demo", "--yes"], "promote", True, True, False),
    (["audit"], "audit", False, False, False),
    (["audit", "--fix"], "audit", True, True, False),
    (["validate"], "validate", False, False, False),
    (["doctor"], "doctor", False, False, False),
    (["llms"], "llms", False, False, False),
    (["wiki"], "wiki", False, False, False),
    (["index", "add", "demo", "--by", "m"], "index add", True, False, False),   # locks only around its append
    (["index", "check"], "index check", False, False, False),
    (["index", "render"], "index render", False, False, False),
    (["index", "migrate"], "index migrate", False, False, False),
    (["claims", "sync", "demo"], "claims sync", True, True, False),
    (["claims", "list"], "claims list", False, False, False),
    (["claims", "check"], "claims check", False, False, False),
    (["claims", "migrate", "demo", "--from", "f"], "claims migrate", False, False, False),   # writes a file, not the registry
    (["claims", "ledger", "demo"], "claims ledger", False, False, False),
    (["claims", "mark", "demo", "--from-comparator", "c.json", "--status", "reusable"], "claims mark", False, False,
     False),
    (["claims", "mark", "demo", "--from-comparator", "c.json", "--status", "reusable", "--write"], "claims mark",
     False, False, False),                                                                  # writes claims.yaml, not the registry
    (["claims", "rebase", "demo", "--from-tex", "a", "--to-tex", "b", "--to-aux", "c"], "claims rebase",
     False, False, False),                                  # dry run: prints the report only
    (["claims", "rebase", "demo", "--from-tex", "a", "--to-tex", "b", "--to-aux", "c", "--out-dir", "d"],
     "claims rebase", False, False, False),                 # writes --out-dir, never the registry
    (["search", "term"], "search", False, False, False),
    (["search", "term", "--record"], "search", True, False, False),   # locks only around its append
    (["formalization", "demo"], "formalization", False, False, False),
    (["formalization", "demo", "--write"], "formalization", True, True, False),
    (["formalization", "demo", "--write", "--no-registry"], "formalization", False, False, False),
    (["formalization", "demo", "--headline-config", "h.json"], "formalization", False, False, False),
    (["formalization", "demo", "--write", "--headline-config", "h.json"], "formalization", True, True, False),
    (["formalization", "demo", "--write", "--no-registry", "--headline-config", "h.json"], "formalization",
     False, False, False),
    (["formalization", "anchor", "demo", "--arxiv", "X", "--from-file", "f"], "formalization anchor",
     False, False, False),
    (["formalization", "anchor", "demo", "--arxiv", "X", "--from-file", "f", "--write"], "formalization anchor",
     False, False, False),                                  # writes package files only, never the registry
    (["formalization", "anchor", "demo", "--arxiv", "X", "--fetch", "--compare-tex", "t", "--accept-diff", "r"],
     "formalization anchor", False, False, False),
    (["publish", "demo", "--dry-run"], "publish", False, False, False),
    (["coverage", "check", "demo"], "coverage check", False, False, False),
    (["coverage", "check", "demo", "--file", "f", "--allow-private-manuscript"], "coverage check", False, False, False),
    (["page", "demo"], "page", False, False, False),
    (["page", "demo", "--check"], "page", False, False, False),
    (["page", "demo", "--out", "o"], "page", False, False, False),
    (["page", "demo", "--write"], "page", False, False, False),   # writes README.md only, never the registry
    (["page", "demo", "--write", "--agent", "a"], "page", False, False, False),
    (["page", "demo", "--site", "d"], "page", False, False, False),   # site files only, never the registry
    (["page", "demo", "--html", "h"], "page", False, False, False),
    (["page", "demo", "--check", "--site", "d", "--html", "h"], "page", False, False, False),
    (["page", "demo", "--check", "--repo", "r"], "page", False, False, False),
    (["reservoir", "check", "demo"], "reservoir check", False, False, False),
    (["public-safety", "demo"], "public-safety", False, False, False),
    (["public-safety", "demo", "--gzip-limit", "64M"], "public-safety", False, False, False),
    (["public-safety", "demo", "--gzip-limit", "0"], "public-safety", False, False, False),
    (["palomar", "prepare", "demo"], "palomar prepare", True, True, False),
    (["palomar", "prepare", "demo", "--dry-run"], "palomar prepare", False, False, False),
    (["palomar", "check", "demo"], "palomar check", False, False, False),            # read-only, offline
    (["palomar", "check", "demo", "--rev", "HEAD", "--config", "c.json", "--rules", "p.json", "--repo", "d"],
     "palomar check", False, False, False),
    (["palomar", "port-modules", "demo"], "palomar port-modules", False, False, False),   # dry run by default
    (["palomar", "port-modules", "demo", "--dry-run"], "palomar port-modules", False, False, False),
    (["palomar", "port-modules", "demo", "--write"], "palomar port-modules", False, False, False),  # .lean files only
    (["palomar", "port-modules", "demo", "--write", "--agent", "a"], "palomar port-modules", False, False, False),
    (["next", "demo"], "next", False, False, False),
    (["report", "--command", "x"], "report", False, False, False),
    (["report", "--command", "x", "--yes"], "report", False, False, False),   # files an issue, not a registry write
    (["receipt", "slurm"], "receipt slurm", False, False, False),
    (["receipt", "slurm", "--job", "1", "--log", "l", "--commit", "c", "--stdout", "o", "--artifacts", "d",
      "--artifacts-manifest", "m"], "receipt slurm", False, False, False),   # writes .lean-receipts, no registry
    (["receipt", "slurm", "--check", "--log", "l", "--stdout", "o", "--artifacts", "d"], "receipt slurm", False,
     False, False),
    (["receipt", "slurm", "--preflight-deps", "--deps-tar", "t", "--entries", "Audit/**/*.lean"], "receipt slurm",
     False, False, False),                                                  # reads only
    (["receipt", "slurm", "--preflight-deps", "--deps-dir", "d"], "receipt slurm", False, False, False),
    (["receipt", "systemd", "--unit", "u", "--log", "l", "--commit", "c"], "receipt systemd", False, False, False),
    (["receipt", "local", "--unit", "u", "--log", "l", "--commit", "c"], "receipt systemd", False, False, False),   # alias
    (["receipt", "check", "--repo", "d", "--commit", "c"], "receipt check", False, False, False),   # no registry
    (["receipt", "check", "--repo", "d", "--commit", "c", "--at", "v1", "--docs-descendants", "--name", "n",
      "--json"], "receipt check", False, False, False),
    # global flags never change the predicate (a snapshot is refused later, by run_snapshot)
    (["--json", "list"], "list", False, False, False),
    (["--registry", "r.yaml", "--commit", "claim", "demo", "--workspace", "w", "--agent", "a"], "claim", True, True, False),
    (["--registry-snapshot", "HEAD", "gate", "demo", "--receipt", R], "gate", True, True, False),
    (["--no-commit", "audit", "--fix"], "audit", True, True, False),
]


def _row_id(row) -> str:
    argv, _, _, _, tty = row
    return " ".join(argv or ["<bare>"]) + (" [tty]" if tty else "")


@pytest.fixture(scope="module")
def ap():
    return parser()


@pytest.mark.parametrize("row", ARGV_TABLE, ids=_row_id)
def test_argv_table_matches_predicates(row, ap, lp, monkeypatch):
    argv, want_cmd, want_writes, want_locks, tty = row
    monkeypatch.setattr(lp, "board_interactive", lambda: tty)
    res = parse_only(argv, ap)
    assert res.ok, res.error
    got = (canonical(res.args, ap), lp.is_write_invocation(res.args), lp.locks_registry(res.args))
    assert got == (want_cmd, want_writes, want_locks)


def test_argv_table_covers_every_command_path(ap):
    """A new command (nested ones and the comparator keyword forms included) needs a row."""
    paths = {" ".join(n.path) for n in command_tree(ap) if n.path}
    paths -= {"index", "claims", "reservoir", "palomar", "receipt", "coverage"}   # groups: a verb is required
    paths |= {f"comparator {k}" for k in comparator_keywords()} | {"formalization anchor"}
    covered = {row[1] for row in ARGV_TABLE}
    assert sorted(paths - covered) == [], "add ARGV_TABLE rows for these commands"
    assert sorted(covered - paths) == [], "ARGV_TABLE names commands the parser no longer has"


def test_argv_table_covers_every_alias(ap):
    spellings = {tuple(row[0][:2]) for row in ARGV_TABLE} | {tuple(row[0][:1]) for row in ARGV_TABLE}
    missing = []
    for node in command_tree(ap):
        for alias in node.aliases:
            spelled = node.path[:-1] + (alias,)
            if spelled not in spellings:
                missing.append(" ".join(spelled))
    assert missing == [], "add ARGV_TABLE rows for these aliases"


def test_every_write_has_a_dry_or_read_counterpart_row():
    """Not a rule of the tool, a rule of this table: every canonical command that writes in some row is
    listed with all its write-switching flags; keep the table honest when a flag is added."""
    writes = {row[1] for row in ARGV_TABLE if row[2]}
    assert writes == {"board", "init", "migrate", "record", "register", "claim", "release", "renew",
                      "update", "gate", "comparator", "promote", "audit", "index add", "claims sync",
                      "search", "formalization", "palomar prepare"}
