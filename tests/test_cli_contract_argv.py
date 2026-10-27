#!/usr/bin/env python3
"""Golden CLI contract: argv -> (canonical command, writes?, locks the registry?).

ARGV_TABLE is the specification later refactors are checked against: one row per command spelling,
the permanent aliases of lean_pkg.command_tree (old command and flag spellings) and the flags that turn a write into a dry run (or a read into a write) included. Each row is
parsed with the real parser (parse only, nothing runs) and checked against today's predicates
`is_write_invocation` and `locks_registry`. The coverage tests fail when a command or alias is added
without a row here."""

from __future__ import annotations

import pytest

from lean_pkg import command_tree as ct

from .cli_contract import canonical, command_tree, parse_only, parser

R = "receipt.json"

# (argv, canonical command, writes, locks the registry for the whole command, board on a tty)
ARGV_TABLE: list[tuple[list[str], str, bool, bool, bool]] = [
    ([], "registry board", False, False, False),
    ([], "registry board", True, False, True),                      # the TUI writes, locking around each key
    (["board"], "registry board", False, False, False),
    (["board"], "registry board", True, False, True),
    (["board", "--json"], "registry board", False, False, True),    # a static view never writes
    (["board", "--only", "alpha"], "registry board", False, False, True),
    (["board", "--fields", "name"], "registry board", False, False, True),
    (["board", "--no-claims"], "registry board", False, False, True),
    (["list"], "list", False, False, False),
    (["list"], "list", False, False, True),
    (["help"], "help", False, False, False),               # help never reads the registry
    (["help", "exit-codes"], "help", False, False, False),
    (["help", "gate"], "help", False, False, False),
    (["status", "demo"], "status", False, False, False),
    (["new", "demo"], "new", False, False, False),                       # files in a new directory, never the registry
    (["new", "demo", "--no-mathlib", "--dir", "d"], "new", False, False, False),
    (["new", "demo", "--kind", "library"], "new", False, False, False),
    (["new", "demo", "--stack", "lts-2026.10"], "new", False, False, False),
    (["new", "demo", "--stack", "none", "--kind", "paper"], "new", False, False, False),
    (["init"], "registry init", True, True, False),
    (["init", "--force"], "registry init", True, True, False),
    (["migrate"], "registry migrate", True, True, False),
    (["migrate", "--dry-run"], "registry migrate", False, False, False),
    (["record", "demo", "retract"], "record", True, True, False),
    (["register", "demo", "--repo", "r", "--path", "p"], "register", True, True, False),
    (["register", "demo", "--repo", "r", "--path", "p", "--dry-run"], "register", False, False, False),
    (["claim", "demo", "--workspace", "w", "--agent", "a"], "lease claim", True, True, False),
    (["lease", "demo", "--workspace", "w", "--agent", "a"], "lease claim", True, True, False),       # alias
    (["release", "demo"], "lease release", True, True, False),
    (["release", "demo", "--dry-run"], "lease release", False, False, False),
    (["release", "demo", "--fresh-repo", "d"], "repo export", True, True, False),          # mode alias
    (["release", "demo", "--fresh-repo", "d", "--dry-run"], "repo export", False, False, False),
    (["release", "demo", "--adopt-repo", "d"], "repo adopt", True, True, False),            # mode alias
    (["release", "demo", "--adopt-repo=d", "--dry-run"], "repo adopt", False, False, False),
    (["renew", "demo", "--agent", "a"], "lease renew", True, True, False),
    (["leases"], "lease list", False, False, False),
    (["update", "demo", "--notes", "n"], "update", True, True, False),
    (["update", "demo", "--notes", "n", "--dry-run"], "update", False, False, False),
    (["update", "demo", "--write-binding-file"], "update", False, False, False),   # the package's file only
    (["update", "demo", "--write-binding-file", "--dry-run"], "update", False, False, False),
    (["update", "demo", "--headline-config", "h.json"], "update", True, True, False),
    (["update", "demo", "--headline-config", ""], "update", True, True, False),
    (["update", "demo", "--withdraw-publication", "--reason", "r"], "update", True, True, False),
    (["update", "demo", "--withdraw-publication", "--reason", "r", "--dry-run"], "update", False, False, False),
    (["update", "demo", "--release-publication-hold", "--reason", "r"], "update", True, True, False),
    (["gate", "demo", "--receipt", R], "gate", True, True, False),
    (["gate", "demo", "--receipt", R, "--check"], "gate", False, False, False),
    (["gate", "demo", "--receipt", R, "--regate"], "gate", True, True, False),
    (["gate", "demo", "--receipt", R, "--comparator", R], "gate", True, True, False),
    (["comparator", "demo", "--receipt", R], "comparator attach", True, True, False),
    (["comparator", "demo", "--receipt", R, "--check"], "comparator check", False, False, False),
    (["comparator", "demo", "--receipt", R, "--check", "--repo", "d"], "comparator check", False, False, False),
    (["comparator", "check", "demo", "--contracts-only"], "comparator check", False, False, False),
    (["comparator", "check", "demo", "--contracts-only", "--config", "c.json", "--no-sandbox"], "comparator check",
     False, False, False),
    (["comparator", "demo", "--receipt", R, "--replace"], "comparator attach", True, True, False),
    (["comparator", "host-check"], "comparator host-check", False, False, False),
    (["comparator", "host-check", "--routes"], "comparator host-check", False, False, False),
    (["comparator", "host-check", "--routes", "--json"], "comparator host-check", False, False, False),
    (["comparator", "host-check", "--receipt", R], "comparator attach", True, True, False),   # a package named so
    (["comparator", "config", "--from-claims", "demo"], "comparator config", False, False, False),
    (["comparator", "config", "--from-claims", "demo", "--claims-file", "c.yaml"], "comparator config",
     False, False, False),
    (["comparator", "config", "--from-claims", "demo", "--challenge-from-type", "--claims-file", "c.yaml"],
     "comparator config", False, False, False),
    (["comparator", "config", "--coverage", "demo"], "comparator coverage", False, False, False),     # mode alias
    (["comparator", "config", "--migrate-signoff", "comparator.json"], "comparator migrate-signoff", False, False,
     False),                                                                                         # mode alias
    (["comparator", "config", "--migrate-signoff", "comparator.json", "--write"], "comparator migrate-signoff",
     False, False, False),                                  # writes the checkout's files only, never the registry
    (["comparator", "config", "--receipt", R], "comparator attach", True, True, False),
    (["promote", "demo"], "publish", False, False, False),               # alias: publish --yes --dry-run
    (["promote", "demo", "--yes"], "publish", True, True, False),        # alias: publish --yes
    (["promote", "demo", "--skip-public-safety"], "publish", False, False, False),
    (["audit"], "registry audit", False, False, False),
    (["audit", "--fix"], "registry audit", True, True, False),
    (["validate"], "registry validate", False, False, False),
    (["doctor"], "doctor", False, False, False),
    (["doctor", "demo"], "doctor", False, False, False),                   # the stack check reads the registry
    (["doctor", "--upgrade-from", "0.10.0"], "doctor", False, False, False),      # read-only, no registry
    (["doctor", "--upgrade-from", "v1", "--to", "v2", "--source", "d"], "doctor", False, False, False),
    (["llms"], "llms", False, False, False),
    (["wiki"], "registry wiki", False, False, False),
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
    (["formalization", "demo"], "formalization render", False, False, False),
    (["formalization", "demo", "--write"], "formalization render", True, True, False),
    (["formalization", "demo", "--write", "--no-registry"], "formalization render", False, False, False),
    (["formalization", "demo", "--headline-config", "h.json"], "formalization render", False, False, False),
    (["formalization", "demo", "--repo", "r"], "formalization render", False, False, False),
    (["formalization", "demo", "--repo", "r", "--out", "o"], "formalization render", False, False, False),
    (["formalization", "demo", "--write", "--headline-config", "h.json"], "formalization render", True, True, False),
    (["formalization", "demo", "--write", "--no-registry", "--headline-config", "h.json"], "formalization render",
     False, False, False),
    (["formalization", "anchor", "demo", "--arxiv", "X", "--from-file", "f"], "formalization anchor",
     False, False, False),
    (["formalization", "anchor", "demo", "--arxiv", "X", "--from-file", "f", "--write"], "formalization anchor",
     False, False, False),                                  # writes package files only, never the registry
    (["formalization", "anchor", "demo", "--arxiv", "X", "--fetch", "--compare-tex", "t", "--accept-diff", "r"],
     "formalization anchor", False, False, False),
    (["publish", "demo", "--dry-run"], "publish", False, False, False),
    (["publish", "demo"], "publish", False, False, False),              # the checklist, read-only by default
    (["publish", "demo", "--public-safety"], "publish", False, False, False),
    (["publish", "demo", "--yes"], "publish", True, True, False),       # the GitHub repository goes public
    (["publish", "demo", "--yes", "--dry-run"], "publish", False, False, False),   # the promotion checklist only
    (["publish", "demo", "--yes", "--agent", "a", "--skip-public-safety"], "publish", True, True, False),
    (["coverage", "check", "demo"], "coverage check", False, False, False),
    (["coverage", "check", "demo", "--file", "f", "--allow-private-manuscript"], "coverage check", False, False, False),
    # the offline form (#328): no NAME, no registry
    (["coverage", "check", "--file", "f", "--claims", "c", "--comparator", "x", "--comparator", "y"],
     "coverage check", False, False, False),
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
    (["blueprint", "check", "demo"], "blueprint check", False, False, False),
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
    (["receipt", "slurm", "--preflight-deps", "--deps-tar", "t", "--fill-plan", "d", "--plan-out", "p",
      "--base-list", "l"], "receipt slurm", False, False, False),            # reads; writes only --plan-out
    (["receipt", "systemd", "--unit", "u", "--log", "l", "--commit", "c"], "receipt systemd", False, False, False),
    (["receipt", "local", "--unit", "u", "--log", "l", "--commit", "c"], "receipt systemd", False, False, False),   # alias
    (["receipt", "ci", "--log", "l", "--commit", "c"], "receipt ci", False, False, False),   # in the job, no registry
    (["receipt", "ci", "--collect", "5", "--attempt", "1"], "receipt ci", False, False, False),   # copies, never commits
    (["receipt", "ci", "--template"], "receipt ci", False, False, False),
    (["receipt", "check", "--repo", "d", "--commit", "c"], "receipt check", False, False, False),   # no registry
    (["verify-receipts", "--repo", "d", "--commit", "c"], "receipt check", False, False, False),     # alias
    (["receipt", "comparator"], "receipt comparator", False, False, False),        # writes .lean-receipts only
    (["receipt", "comparator", "--repo", "d", "--judges", "2", "--json"], "receipt comparator", False, False,
     False),
    (["receipt", "comparator", "--project", "d", "--judges", "2", "--json"], "receipt comparator", False, False,
     False),                                                                       # flag alias
    (["receipt", "comparator", "--estimate", "--sample", "3"], "receipt comparator", False, False, False),
    # verify: its registry steps (the status move, the gate) are invocations of their own (own lock, own commit)
    (["verify", "demo"], "verify", False, False, False),
    (["verify", "demo", "--dry-run"], "verify", False, False, False),
    (["verify", "demo", "--agent", "a", "--work-dir", "w", "--unit", "u", "--judges", "2"], "verify", False, False,
     False),
    (["register", "demo", "--url", "r", "--path", "p", "--license-spdx", "Apache-2.0"], "register", True, True,
     False),
    (["receipt", "check", "--repo", "d", "--commit", "c", "--at", "v1", "--docs-descendants", "--name", "n",
      "--json"], "receipt check", False, False, False),
    # the 1.0 canonical spellings (docs/design/1.0-command-tree.md); the rows above are their permanent aliases
    (["registry", "board"], "registry board", False, False, False),
    (["registry", "board"], "registry board", True, False, True),
    (["registry", "board", "--json"], "registry board", False, False, True),
    (["registry", "init"], "registry init", True, True, False),
    (["registry", "migrate"], "registry migrate", True, True, False),
    (["registry", "migrate", "--dry-run"], "registry migrate", False, False, False),
    (["registry", "validate"], "registry validate", False, False, False),
    (["registry", "audit"], "registry audit", False, False, False),
    (["registry", "audit", "--write"], "registry audit", True, True, False),
    (["registry", "audit", "--fix"], "registry audit", True, True, False),                      # flag alias
    (["registry", "wiki"], "registry wiki", False, False, False),
    (["lease", "claim", "demo", "--workspace", "w", "--agent", "a"], "lease claim", True, True, False),
    (["lease", "renew", "demo", "--agent", "a"], "lease renew", True, True, False),
    (["lease", "release", "demo"], "lease release", True, True, False),
    (["lease", "release", "demo", "--dry-run"], "lease release", False, False, False),
    (["lease", "release", "demo", "--fresh-repo", "d"], "repo export", True, True, False),        # mode alias
    (["lease", "release", "demo", "--fresh-repo", "d", "--dry-run"], "repo export", False, False, False),
    (["lease", "release", "demo", "--adopt-repo", "d"], "repo adopt", True, True, False),          # mode alias
    (["repo", "export", "demo", "d"], "repo export", True, True, False),
    (["repo", "export", "demo", "d", "--remote", "u", "--keep-receipts"], "repo export", True, True, False),
    (["repo", "export", "demo", "d", "--from-tree", "s", "--dry-run"], "repo export", False, False, False),
    (["repo", "adopt", "demo", "d"], "repo adopt", True, True, False),
    (["repo", "adopt", "demo", "d", "--allow-history", "--dry-run"], "repo adopt", False, False, False),
    (["lease", "list"], "lease list", False, False, False),
    (["repo", "move", "demo", "p", "--reason", "r"], "repo move", True, True, False),
    (["repo", "move", "demo", "p", "--reason", "r", "--dry-run"], "repo move", False, False, False),
    (["update", "demo", "--path", "p", "--reason", "r"], "update", True, True, False),
    (["comparator", "attach", "demo", "--receipt", R], "comparator attach", True, True, False),
    (["comparator", "attach", "demo", "--receipt", R, "--check"], "comparator check", False, False, False),  # mode
    (["comparator", "check", "demo", "--receipt", R], "comparator check", False, False, False),
    (["comparator", "check", "demo", "--receipt", R, "--repo", "d"], "comparator check", False, False, False),
    (["comparator", "check", "--receipt", R], "comparator attach", True, True, False),     # a package named so
    (["comparator", "coverage", "demo"], "comparator coverage", False, False, False),
    (["comparator", "coverage", "--receipt", R], "comparator attach", True, True, False),  # a package named so
    (["comparator", "migrate-signoff", "comparator.json"], "comparator migrate-signoff", False, False, False),
    (["comparator", "migrate-signoff", "comparator.json", "--write"], "comparator migrate-signoff", False, False,
     False),                                                # the checkout's files only, never the registry
    (["comparator", "attach", "host-check", "--receipt", R], "comparator attach", True, True, False),
    (["comparator", "config", "--from-claims", "demo", "--repo", "d"], "comparator config", False, False, False),
    (["comparator", "config", "--from-claims", "demo", "--checkout", "d"], "comparator config", False, False,
     False),                                                # flag alias
    (["formalization", "render", "demo"], "formalization render", False, False, False),
    (["formalization", "render", "demo", "--write"], "formalization render", True, True, False),
    (["formalization", "render", "demo", "--write", "--no-registry"], "formalization render", False, False, False),
    (["formalization", "kept"], "formalization kept", False, False, False),            # read-only, no registry
    (["formalization", "kept", "--repo", "d", "--at", "v1", "--against", "v0"], "formalization kept", False, False,
     False),
    (["record", "demo", "doi-minted", "--doi-track", "doi-only"], "record", True, True, False),
    (["record", "demo", "doi-minted", "--track", "doi-only"], "record", True, True, False),     # flag alias
    (["update", "demo", "--doi-track", "doi-only", "--reason", "r"], "update", True, True, False),
    (["update", "demo", "--track", "doi-only", "--reason", "r"], "update", True, True, False),  # flag alias
    (["update", "demo", "--url", "u"], "update", True, True, False),
    (["update", "demo", "--repo", "u"], "update", True, True, False),                           # flag alias
    (["register", "demo", "--url", "r", "--path", "p"], "register", True, True, False),
    (["index", "add", "demo", "--by", "m", "--url", "u"], "index add", True, False, False),
    (["index", "add", "demo", "--by", "m", "--repo", "u"], "index add", True, False, False),     # flag alias
    (["index", "add", "demo", "--by", "m", "--replace", "--built-on-file", "c.yaml"], "index add", True, False, False),
    (["index", "check", "demo", "--repo", "d"], "index check", False, False, False),
    (["index", "check", "demo", "--repo-dir", "d"], "index check", False, False, False),         # flag alias
    (["search", "term", "--record", "--repo", "d"], "search", True, False, False),
    (["search", "term", "--record", "--repo-dir", "d"], "search", True, False, False),           # flag alias
    (["report", "--command", "x", "--target-repo", "o/n"], "report", False, False, False),
    (["report", "--command", "x", "--repo", "o/n"], "report", False, False, False),               # flag alias
    (["receipt", "slurm", "--job", "1", "--log", "l", "--receipt-commit", "c"], "receipt slurm", False, False, False),
    (["receipt", "systemd", "--unit", "u", "--log", "l", "--receipt-commit", "c"], "receipt systemd", False, False,
     False),
    (["receipt", "ci", "--log", "l", "--receipt-commit", "c"], "receipt ci", False, False, False),
    # global flags never change the predicate (a snapshot is refused later, by run_snapshot)
    (["--json", "list"], "list", False, False, False),
    (["--registry", "r.yaml", "--commit", "claim", "demo", "--workspace", "w", "--agent", "a"], "lease claim", True, True, False),
    (["--registry-snapshot", "HEAD", "gate", "demo", "--receipt", R], "gate", True, True, False),
    (["--no-commit", "audit", "--fix"], "registry audit", True, True, False),
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
    """A new command (nested ones included) needs a row."""
    paths = {" ".join(n.path) for n in command_tree(ap) if n.path}
    paths -= {"index", "claims", "reservoir", "blueprint", "palomar", "receipt", "coverage",   # groups: a verb is required
              "lease", "registry", "repo", "comparator", "formalization"}
    covered = {row[1] for row in ARGV_TABLE}
    assert sorted(paths - covered) == [], "add ARGV_TABLE rows for these commands"
    assert sorted(covered - paths) == [], "ARGV_TABLE names commands the parser no longer has"


def test_argv_table_covers_every_alias(ap):
    """Every permanent alias (lean_pkg.command_tree) is spelled by at least one row, on its canonical command."""
    missing = []
    for a in ct.COMMAND_ALIASES:
        if not any(tuple(row[0][:len(a.old)]) == a.old and row[1] == " ".join(a.new) for row in ARGV_TABLE):
            missing.append(" ".join(a.old))
    for noun, iv in ct.IMPLIED_VERBS.items():
        if not any(row[0][:1] == [noun] and len(row[0]) > 1 and row[0][1] not in iv.verbs for row in ARGV_TABLE):
            missing.append(f"{noun} NAME")
    for m in ct.MODE_ALIASES:
        if not any(m.flag in " ".join(row[0]) and row[1] == " ".join(m.new) for row in ARGV_TABLE):
            missing.append(f"{' '.join(m.command)} {m.flag}")
    for fa in ct.FLAG_ALIASES:
        if not any(fa.old in row[0] and row[1] == " ".join(fa.command) for row in ARGV_TABLE):
            missing.append(f"{' '.join(fa.command)} {fa.old}")
    for node in command_tree(ap):   # argparse-level aliases: none today, all aliases live in command_tree
        missing += [" ".join(node.path[:-1] + (alias,)) for alias in node.aliases]
    assert missing == [], "add ARGV_TABLE rows for these aliases"


def test_every_write_has_a_dry_or_read_counterpart_row():
    """Not a rule of the tool, a rule of this table: every canonical command that writes in some row is
    listed with all its write-switching flags; keep the table honest when a flag is added."""
    writes = {row[1] for row in ARGV_TABLE if row[2]}
    assert writes == {"registry board", "registry init", "registry migrate", "record", "register", "lease claim",
                      "lease release", "lease renew", "update", "repo move", "repo export", "repo adopt", "gate",
                      "comparator attach", "publish",
                      "registry audit", "index add", "claims sync", "search", "formalization render",
                      "palomar prepare"}


# --- permanent aliases parse exactly like the canonical spelling ----------------------------------

# (old argv, canonical argv): the two must give the same namespace. One pair per alias at least.
ALIAS_PAIRS: list[tuple[list[str], list[str]]] = [
    (["claim", "demo", "--workspace", "w", "--agent", "a"], ["lease", "claim", "demo", "--workspace", "w", "--agent", "a"]),
    (["lease", "demo", "--workspace", "w", "--agent", "a"], ["lease", "claim", "demo", "--workspace", "w", "--agent", "a"]),
    (["renew", "demo", "--agent", "a", "--ttl", "3"], ["lease", "renew", "demo", "--agent", "a", "--ttl", "3"]),
    (["release", "demo", "--fresh-repo", "d"], ["lease", "release", "demo", "--fresh-repo", "d"]),
    (["leases", "--agent", "a"], ["lease", "list", "--agent", "a"]),
    (["init", "--force"], ["registry", "init", "--force"]),
    (["migrate", "--dry-run"], ["registry", "migrate", "--dry-run"]),
    (["validate"], ["registry", "validate"]),
    (["audit", "--fix"], ["registry", "audit", "--write"]),
    (["board", "--json"], ["registry", "board", "--json"]),
    (["wiki", "--out", "w.md"], ["registry", "wiki", "--out", "w.md"]),
    (["receipt", "local", "--unit", "u", "--log", "l", "--commit", "c"],
     ["receipt", "systemd", "--unit", "u", "--log", "l", "--receipt-commit", "c"]),
    (["comparator", "demo", "--receipt", R, "--check"], ["comparator", "attach", "demo", "--receipt", R, "--check"]),
    (["comparator", "host-check", "--receipt", R], ["comparator", "attach", "host-check", "--receipt", R]),
    (["formalization", "demo", "--write"], ["formalization", "render", "demo", "--write"]),
    (["--registry", "r.yaml", "--json", "claim", "demo", "--workspace", "w", "--agent", "a"],
     ["--registry", "r.yaml", "--json", "lease", "claim", "demo", "--workspace", "w", "--agent", "a"]),
    (["record", "demo", "doi-minted", "--track", "doi-only"], ["record", "demo", "doi-minted", "--doi-track", "doi-only"]),
    (["update", "demo", "--track", "palomar", "--reason", "r"], ["update", "demo", "--doi-track", "palomar", "--reason", "r"]),
    (["receipt", "slurm", "--job", "1", "--commit", "c"], ["receipt", "slurm", "--job", "1", "--receipt-commit", "c"]),
    (["receipt", "systemd", "--unit", "u", "--log", "l", "--commit", "c"],
     ["receipt", "systemd", "--unit", "u", "--log", "l", "--receipt-commit", "c"]),
    (["receipt", "ci", "--log", "l", "--commit", "c"], ["receipt", "ci", "--log", "l", "--receipt-commit", "c"]),
    (["register", "demo", "--repo", "u", "--path", "p"], ["register", "demo", "--url", "u", "--path", "p"]),
    (["update", "demo", "--repo", "u"], ["update", "demo", "--url", "u"]),
    (["index", "add", "demo", "--by", "m", "--repo", "u"], ["index", "add", "demo", "--by", "m", "--url", "u"]),
    (["index", "check", "demo", "--repo-dir", "d"], ["index", "check", "demo", "--repo", "d"]),
    (["search", "t", "--record", "--repo-dir", "d"], ["search", "t", "--record", "--repo", "d"]),
    (["comparator", "config", "--from-claims", "demo", "--checkout", "d"],
     ["comparator", "config", "--from-claims", "demo", "--repo", "d"]),
    (["report", "--command", "x", "--repo", "o/n"], ["report", "--command", "x", "--target-repo", "o/n"]),
    (["receipt", "comparator", "--project", "d", "--judges", "2"],
     ["receipt", "comparator", "--repo", "d", "--judges", "2"]),
    (["registry", "audit", "--fix"], ["registry", "audit", "--write"]),
    # the 1.0 command tree, stage 2: split modes and folded commands
    (["release", "demo", "--fresh-repo", "d", "--remote", "u", "--dry-run"],
     ["repo", "export", "demo", "d", "--remote", "u", "--dry-run"]),
    (["release", "demo", "--fresh-repo=d", "--keep-receipts"], ["repo", "export", "demo", "d", "--keep-receipts"]),
    (["lease", "release", "demo", "--fresh-repo", "d"], ["repo", "export", "demo", "d"]),
    (["release", "demo", "--adopt-repo", "d", "--allow-history"], ["repo", "adopt", "demo", "d", "--allow-history"]),
    (["lease", "release", "demo", "--adopt-repo", "d"], ["repo", "adopt", "demo", "d"]),
    (["comparator", "attach", "demo", "--receipt", R, "--check", "--repo", "d"],
     ["comparator", "check", "demo", "--receipt", R, "--repo", "d"]),
    (["comparator", "config", "--coverage", "demo", "--json"], ["comparator", "coverage", "demo", "--json"]),
    (["comparator", "config", "--migrate-signoff", "c.json", "--write"],
     ["comparator", "migrate-signoff", "c.json", "--write"]),
    (["promote", "demo"], ["publish", "demo", "--yes", "--dry-run"]),
    (["promote", "demo", "--yes", "--agent", "a"], ["publish", "demo", "--yes", "--agent", "a"]),
    (["promote", "demo", "--skip-public-safety", "--json"],
     ["publish", "demo", "--yes", "--dry-run", "--skip-public-safety", "--json"]),
    (["verify-receipts", "--repo", "d", "--commit", "c"], ["receipt", "check", "--repo", "d", "--commit", "c"]),
]


@pytest.mark.parametrize("old,new", ALIAS_PAIRS, ids=lambda v: " ".join(v))
def test_alias_parses_to_the_canonical_namespace(old, new, ap):
    a, b = parse_only(old, ap), parse_only(new, ap)
    assert a.ok and b.ok, (a.error, b.error)
    assert vars(a.args) == vars(b.args)


def test_alias_pairs_cover_every_alias():
    olds = [old for old, _ in ALIAS_PAIRS]
    missing = [" ".join(a.old) for a in ct.COMMAND_ALIASES if not any(tuple(o[:len(a.old)]) == a.old for o in olds)]
    missing += [f"{n} NAME" for n, iv in ct.IMPLIED_VERBS.items()
                if not any(o[:1] == [n] and o[1] not in iv.verbs for o in olds if len(o) > 1)]
    missing += [f"{' '.join(fa.command)} {fa.old}" for fa in ct.FLAG_ALIASES
                if not any(fa.old in o and tuple(o[:len(fa.command)]) == fa.command for o in olds)]
    missing += [f"{' '.join(m.command)} {m.flag}" for m in ct.MODE_ALIASES
                if not any(any(w == m.flag or w.startswith(m.flag + "=") for w in o) for o in olds)]
    assert missing == []


@pytest.mark.parametrize("alias", ct.COMMAND_ALIASES, ids=lambda a: " ".join(a.old))
def test_alias_help_is_the_canonical_help(alias, lp, capsys):
    def help_of(words):
        with pytest.raises(SystemExit) as e:
            lp.main(list(words) + ["--help"])
        assert e.value.code == 0
        return capsys.readouterr().out
    assert help_of(alias.old) == help_of(alias.new)


def test_repo_move_is_update_path(ap):
    """`repo move NAME PATH` parses to the namespace of `update NAME --path PATH` (plus the noun's verb)."""
    a = vars(parse_only(["update", "demo", "--path", "p", "--reason", "r", "--dry-run"], ap).args)
    b = vars(parse_only(["repo", "move", "demo", "p", "--reason", "r", "--dry-run"], ap).args)
    assert b.pop("repo_cmd") == "move" and a == b


def test_old_spelling_note_text_mode_only(lp, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("LEAN_PKG_NO_ALIAS_NOTE", raising=False)
    reg = str(tmp_path / "r.yaml")
    assert lp.main(["--registry", reg, "init"]) == 0
    err = capsys.readouterr().err
    assert "note: 'lean-pkg init' is now 'lean-pkg registry init' (the old spelling keeps working)" in err
    assert lp.main(["--registry", reg, "leases", "--json"]) == 0
    assert "note:" not in capsys.readouterr().err                       # --json: never a note
    assert lp.main(["--registry", reg, "registry", "validate"]) == 0
    assert "note:" not in capsys.readouterr().err                       # the canonical spelling: none
    assert lp.main(["--registry", reg, "audit", "--fix", "--no-gh", "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "'lean-pkg audit' is now 'lean-pkg registry audit'" in err
    assert "'lean-pkg registry audit --fix' is now 'lean-pkg registry audit --write'" in err
    monkeypatch.setenv("LEAN_PKG_NO_ALIAS_NOTE", "1")
    assert lp.main(["--registry", reg, "validate"]) == 0
    assert "note:" not in capsys.readouterr().err
