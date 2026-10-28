# lean-pkg -- the argument parser
"""The `lean-pkg` argparse tree: build_parser() builds it exactly as main() parses it, and _Parser (the
UsageError JSON contract, the old-spelling rewrite of lean_pkg.command_tree) with the helpers that copy
options between forms of a command (_hidden_copies, _defaults_except).

Moved verbatim out of cli.py's main() (#317, stage 3); main() calls build_parser(), and cli.py re-exports
the names defined here for at least one release. The tree binds the command handlers, most of which are
cli.py's, so build_parser imports them from cli.py when called (cli.py imports this module); gate.py and
audit.py are imported at the top level.
"""

from __future__ import annotations

import argparse
import sys

from .core import (
    BULK_FIELDS, DEFAULT_REGISTRY_NAME, DEFAULT_TTL_HOURS, EVENTS_NAME, EVIDENCE_EVENTS, LEASE_CONTACT_MAX,
    MAX_TTL_HOURS, print_json, REGISTER_STATUSES, REGISTRY_ENV, RETRACT_EVENT, SNAPSHOT_ENV, STATUSES, TRACKS,
    UPSTREAM_TARGETS, VISIBILITIES,
)
from .audit import cmd_audit, cmd_validate, DEFAULT_TOOLCHAIN_FLOOR, TOOLCHAIN_FLOOR_ENV
from .gate import cmd_gate


class _Parser(argparse.ArgumentParser):
    """argparse with the UsageError JSON contract (#21.7): when --json is on the command line, an
    argparse-level error (unknown flag, bad choice, missing argument) also prints
    {ok: false, error, usage: true} on stdout; the stderr usage text and exit 2 are unchanged.
    Subparsers inherit the class."""
    json_requested = False
    alias_root = False   # the root parser: rewrite old spellings to canonical ones first (command_tree)
    alias_hits: list = []

    def parse_known_args(self, args=None, namespace=None):
        if self.alias_root:
            from . import command_tree
            args, self.alias_hits = command_tree.rewrite_argv(list(sys.argv[1:] if args is None else args))
            self.command_argv = list(args)   # the canonical argv (receipt comparator's resume command repeats it)
        return super().parse_known_args(args, namespace)

    def format_help(self):
        if self.alias_root:   # the root --help: commands in tiers (command_tree.root_help)
            from . import command_tree
            return command_tree.root_help(self)
        return super().format_help()

    def error(self, message):
        if _Parser.json_requested:
            print_json({"ok": False, "error": f"{self.prog}: {message}", "usage": True})
        super().error(message)


def _hidden_copies(target, *sources) -> None:
    """Give target every option of sources it lacks, hidden from its help (help=SUPPRESS). A form split out
    of one parser (`comparator config`, `formalization anchor`) then parses any old argv exactly as before:
    a flag of another form still reaches the command, which refuses it with the message naming the right
    form, as it always did."""
    import copy
    for src in sources:
        for a in src._actions:
            if not a.option_strings or isinstance(a, argparse._HelpAction):
                continue
            if any(o in target._option_string_actions for o in a.option_strings) \
                    or any(b.dest == a.dest for b in target._actions):
                continue
            b = copy.copy(a)
            b.option_strings = list(a.option_strings)
            b.help = argparse.SUPPRESS
            target._add_action(b)
        target.set_defaults(**{k: v for k, v in _defaults_except(src, target).items()})


def _defaults_except(source, target) -> dict:
    """source's argument defaults for every dest target does not define: a form split out of a command parses
    to the namespace that command produced (`repo move` = `update --path`, `comparator config` = the old
    keyword form)."""
    own = {a.dest for a in target._actions}
    out = {}
    for a in source._actions:
        if a.dest in own or a.dest in out or a.default is argparse.SUPPRESS or isinstance(a, argparse._HelpAction):
            continue
        out[a.dest] = a.default
    return out


def build_parser():
    """The whole `lean-pkg` argparse tree, as main() parses it: every command and alias, the commit-flag
    split of the help (show_commit_flags_on_writes), then the command tree's flag aliases, tiers and root
    help (lean_pkg.command_tree), in that order. Built fresh on each call: help texts read the environment
    when the parser is built."""
    from .cli import (   # the command handlers bound below are cli.py's, and cli.py imports this module
        add_commit_flags, cmd_board, cmd_claim, cmd_comparator, cmd_doctor, cmd_help, cmd_init, cmd_leases,
        cmd_list, cmd_llms, cmd_migrate, cmd_record, cmd_register, cmd_release, cmd_renew, cmd_status,
        cmd_update, cmd_wiki, COMMIT_ENV, CONFIG_CMD, HOST_CHECK, nonneg_hours, package_fields_help,
        PAPER_STATUSES, PUSH_ENV, show_commit_flags_on_writes,
    )
    ap = _Parser(
        prog="lean-pkg",
        description="lean-pkg: a registry and trust gate for Lean 4 formalization packages: receipts, the gate, "
                    "the verification page, publication.",
        epilog="New here? `lean-pkg help start`: the five verbs (new, verify, status, page, publish) and the two "
               "product tracks. `lean-pkg <command> -h`: one command's flags. Old spellings (claim, init, promote, "
               "comparator-receipt, ...) keep working: `lean-pkg help aliases`.")
    ap.add_argument("--registry", default=None,
                    help=f"registry yaml (default: ${REGISTRY_ENV}, else "
                         f"~/.lean-pkg/registry, else ./{DEFAULT_REGISTRY_NAME})")
    ap.add_argument("--events", default=None,
                    help=f"event log jsonl (default: {EVENTS_NAME} next to the "
                         f"registry)")
    ap.add_argument("--json", dest="json_out", action="store_true",
                    help="machine-readable JSON on stdout (exit codes unchanged)")
    ap.add_argument("--commit", action="store_true",
                    help=f"write commands: pull --ff-only first, then commit the registry and event log "
                         f"together (default: ${COMMIT_ENV}=1)")
    ap.add_argument("--push", action="store_true",
                    help=f"--commit, then push to origin (default: ${PUSH_ENV}=1)")
    ap.add_argument("--no-commit", action="store_true",
                    help=f"never commit or push, overriding ${COMMIT_ENV} and ${PUSH_ENV}")
    ap.add_argument("--registry-snapshot", metavar="COMMIT", default=None,
                    help=f"read-only: read the registry file (and its event log) as of COMMIT of the git "
                         f"repository holding it (env ${SNAPSHOT_ENV}); every write exits 2; --json adds "
                         f"registry_snapshot")
    from . import __version__
    ap.add_argument("--version", dest="show_version", action="store_true",
                    help=f"print lean-pkg {__version__} and the build that runs: short commit (+dirty) and "
                         f"checkout of an editable install, else the installed package directory; local "
                         f"only (no fetch); with --json: {{ok, version, commit, dirty, install, path}}")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    def add_json(s):
        # Repeat --json on every subparser so BOTH `lean-pkg --json list` and
        # `lean-pkg list --json` work. SUPPRESS keeps the global value when
        # the flag is absent after the subcommand.
        s.add_argument("--json", dest="json_out", action="store_true",
                       default=argparse.SUPPRESS,
                       help="machine-readable JSON on stdout")
        add_commit_flags(s)

    only_help = ("filters PACKAGES, never fields: only these packages (comma-separated names), each with its "
                 "full record (cached claims included: --no-claims or --fields shrink it); the same table or --json shape, restricted; an unknown "
                 "name exits 1")
    list_fields_help = ("trims FIELDS, never packages: each package record cut to these keys, comma-separated; "
                        "the same validation, dotted paths and aliases as `status --fields` (gate.receipt, "
                        "lease.agent, lease.expires_at; the table columns as aliases: pin, vis, reaudit, expires; an "
                        "unknown field exits 2 listing the valid keys); --json keeps the dump's shape plus a "
                        "`fields` list, text prints the selected keys of each package as YAML")
    no_claims_help = ("drop the bulk cached arrays (" + ", ".join(BULK_FIELDS) + "; the [bulk] keys below) from "
                      "each package record; every other key unchanged; --json adds an `omitted` list")
    # 1.0 command tree (docs/design/1.0-command-tree.md): coordination and maintenance commands live under
    # nouns; each keeps its pre-1.0 `cmd` (set_defaults) so every predicate below is unchanged, and its old
    # top-level spelling is a permanent alias (command_tree.COMMAND_ALIASES).
    s = sub.add_parser("registry", help="the registry file itself: init, migrate, validate, audit, board, recommend, "
                                        "wiki "
                                        "(the old top-level spellings keep working)")
    registry_sub = s.add_subparsers(dest="registry_cmd", required=True,
                                    metavar="{board,init,migrate,validate,audit,recommend,wiki}")
    s = registry_sub.add_parser("board", help="curses TUI on a tty; static table otherwise",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=package_fields_help())
    s.add_argument("--only", metavar="NAME[,NAME]", default=None,
                   help=only_help + "; never opens the TUI")
    s.add_argument("--fields", metavar="a,b,c", default=None, help=list_fields_help + "; never opens the TUI")
    s.add_argument("--no-claims", dest="no_claims", action="store_true",
                   help=no_claims_help + "; never opens the TUI")
    add_json(s)
    s.set_defaults(fn=cmd_board, cmd="board")
    s = sub.add_parser("list", help="static table, pipe-friendly",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=package_fields_help())
    s.add_argument("--only", metavar="NAME[,NAME]", default=None, help=only_help)
    s.add_argument("--fields", metavar="a,b,c", default=None, help=list_fields_help)
    s.add_argument("--no-claims", dest="no_claims", action="store_true", help=no_claims_help)
    add_json(s)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("status", help="full record + last 5 history entries + scope (gated commit, privacy, "
                                         "the exact comparator-covered declarations)",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog=package_fields_help())
    s.add_argument("name")
    s.add_argument("--fields", metavar="a,b,c", default=None,
                   help="trims FIELDS: only these keys of the package record, comma-separated; dotted paths "
                        "reach into mappings (gate.receipt, lease.agent, the derived lease.expires_at); aliases "
                        "pin (=mathlib_pin), vis (=visibility), reaudit (=reaudit_after), expires and lease.expires "
                        "(=lease.expires_at), lease.claimed (=lease.claimed_at), output keeps the record key; "
                        "--json keeps the nesting, text prints YAML; an unknown field exits 2 listing the valid keys")
    s.add_argument("--no-claims", dest="no_claims", action="store_true", help=no_claims_help)
    add_json(s)
    s.set_defaults(fn=cmd_status)

    from . import scaffold as scaffold_mod   # `new`: placed beside `init`, the other way to start
    scaffold_mod.add_parsers(sub, add_json)

    s = registry_sub.add_parser("init", help="bootstrap an empty registry and its event log")
    s.add_argument("--force", action="store_true",
                   help="re-create only if the existing registry is empty")
    add_json(s)
    s.set_defaults(fn=cmd_init, cmd="init")

    s = registry_sub.add_parser("migrate", help="upgrade the registry schema in place (additive; idempotent)")
    s.add_argument("--dry-run", action="store_true", help="print the changes; write nothing")
    add_json(s)
    s.set_defaults(fn=cmd_migrate, cmd="migrate")

    s = sub.add_parser("record", help="record a publication evidence event (consent, Palomar, DOI, Reservoir, Mathlib PR)")
    s.add_argument("name")
    s.add_argument("event", help="one of: " + ", ".join(EVIDENCE_EVENTS) + f", or {RETRACT_EVENT} "
                   "(--field event=E --field ts=T --reason TEXT: mark an evidence entry retracted)")
    s.add_argument("--field", action="append", metavar="KEY=VALUE",
                   help="event field (repeatable); required keys per event are listed by `lean-pkg llms`")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    s.add_argument("--doi-track", dest="track", choices=TRACKS, default=None,
                   help="doi-minted only: the DOI track for this event (default: the record's track, else palomar); "
                        "doi-only skips rule doi-in-palomar for a public package")
    s.add_argument("--agent", default=None,
                   help="the agent acting (recorded as the event's actor); another agent's live lease then refuses")
    add_json(s)
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("register", help="the ONLY write path for new packages: "
                                        "validated, deduping, auto-detecting")
    s.add_argument("name")
    s.add_argument("--url", dest="repo", metavar="URL", default=None,
                   help="git URL (or local bare path); required unless --like gives it")
    s.add_argument("--path", required=True,
                   help="local checkout path, $HOME-relative")
    s.add_argument("--like", default=None, metavar="OTHER",
                   help="a sibling of registered package OTHER: copy its repo, owner-project, upstream-target "
                        "and automation (flags given here win; visibility is never copied, default private), "
                        "and report whether the toolchain and Mathlib pin match OTHER's")
    s.add_argument("--owner-project", default=None)
    s.add_argument("--license-spdx", dest="license_spdx", metavar="SPDX", default=None,
                   help="the package's license, an SPDX identifier or expression (Apache-2.0, MIT OR Apache-2.0); "
                        "the page, the publish checklist and formalization.yaml read it (later: update "
                        "--license-spdx)")
    s.add_argument("--visibility", choices=VISIBILITIES, default="private")
    s.add_argument("--status", choices=REGISTER_STATUSES, default="active",
                   help="public only with --visibility public and --reason (an already-public repo)")
    s.add_argument("--upstream-target", choices=UPSTREAM_TARGETS, default=None, help="default none")
    s.add_argument("--automation", default=None)
    s.add_argument("--notes", default="")
    s.add_argument("--reason", default="", help="required with --status public")
    s.add_argument("--workspace", default=None,
                   help="metadata only; does NOT claim")
    s.add_argument("--agent", default=None, help="metadata only; does NOT claim")
    s.add_argument("--ttl", type=int, default=None,
                   help="accepted and ignored (register never claims; claim separately with "
                        "`lean-pkg lease claim NAME --ttl H`)")
    s.add_argument("--dry-run", action="store_true",
                   help="print the would-be entry as YAML without writing")
    add_json(s)
    s.set_defaults(fn=cmd_register)

    s = sub.add_parser("lease", help="leases for agents sharing a registry: claim, renew, release, list (the old "
                                     "spellings claim, renew, release, leases keep working)")
    lease_sub = s.add_subparsers(dest="lease_cmd", required=True, metavar="{claim,renew,release,list}")
    s = lease_sub.add_parser("claim", help="CAS lease on a package (24h default TTL)")
    s.add_argument("name")
    s.add_argument("--workspace", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=None, metavar="HOURS",
                   help=f"lease TTL, 1..{MAX_TTL_HOURS} (default: {DEFAULT_TTL_HOURS} for a new claim; a holder's "
                        f"re-claim keeps the lease's current ttl_hours; above {MAX_TTL_HOURS}: exit 2)")
    s.add_argument("--reason", default="")
    s.add_argument("--contact", default=None, metavar="TEXT",
                   help=f"address of the holding session (e.g. an amsg address such as lab-desk/n14-paper or "
                        f"@lean-ops), stored in the lease and shown by leases/status; one line, at most "
                        f"{LEASE_CONTACT_MAX} characters; a holder's re-claim without it keeps the lease's")
    s.add_argument("--force", action="store_true",
                   help="override a live lease; requires --reason")
    add_json(s)
    s.set_defaults(fn=cmd_claim, cmd="claim")

    s = lease_sub.add_parser("release", help="release a lease (holder-only unless --force); exporting or adopting a "
                                             "repository is `repo export` / `repo adopt`")
    s.add_argument("name")
    s.add_argument("--agent", default=None,
                   help="the holder; with --force, the acting agent recorded as released_by")
    s.add_argument("--force", action="store_true", help="release another agent's lease; requires --reason")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    add_json(s)
    s.set_defaults(fn=cmd_release, cmd="release")
    lease_release = s

    s = lease_sub.add_parser("renew", help="restart your own live lease from now (holder-only; refuses once it "
                                     "has lapsed: claim again)")
    s.add_argument("name")
    s.add_argument("--agent", required=True)
    s.add_argument("--ttl", type=int, default=None, metavar="HOURS",
                   help=f"new TTL, 1..{MAX_TTL_HOURS} (default: the lease's current ttl_hours; above "
                        f"{MAX_TTL_HOURS}: exit 2)")
    s.add_argument("--workspace", default=None, help="move the lease to this workspace")
    s.add_argument("--reason", default="")
    s.add_argument("--contact", default=None, metavar="TEXT",
                   help=f"set or replace the lease's contact (address of the holding session; one line, at most "
                        f"{LEASE_CONTACT_MAX} characters); without it the lease keeps its contact")
    s.add_argument("--no-contact", action="store_true",
                   help="clear the lease's contact while renewing (not with --contact)")
    add_json(s)
    s.set_defaults(fn=cmd_renew, cmd="renew")

    s = lease_sub.add_parser("list", help="held leases, soonest expiry first; read-only")
    s.add_argument("--agent", default=None, help="only leases held by this agent")
    s.add_argument("--expiring", type=nonneg_hours, default=None, metavar="HOURS",
                   help="only leases that lapse within HOURS (or have already lapsed)")
    add_json(s)
    s.set_defaults(fn=cmd_leases, cmd="leases")

    s = sub.add_parser("update", help="set fields of an already-registered "
                                      "package (register is for NEW ones)")
    s.add_argument("name")
    s.add_argument("--automation", help="AI-disclosure note; required by mathlib "
                                        "when upstream_target is mathlib")
    s.add_argument("--notes", metavar="TEXT",
                   help="set the notes; refused when notes exist unless --replace-notes (use --append-note)")
    s.add_argument("--replace-notes", dest="replace_notes", action="store_true",
                   help="with --notes: replace non-empty notes (the old text stays in history as notes_from)")
    s.add_argument("--upstream-target", dest="upstream_target",
                   choices=UPSTREAM_TARGETS)
    s.add_argument("--doi-track", dest="track", choices=TRACKS, default=None,
                   help="the DOI track (needs --reason): doi-only lets a public package record doi-minted "
                        "without a palomar-* event; palomar (the default when absent) keeps rule doi-in-palomar")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--accept-docs-descendants", dest="docs_descendants", action="store_const", const=True,
                   default=None,
                   help="deprecated (WARN; removed no earlier than 1.0.0): the registry route of the docs-only "
                        "opt-in, replaced by a committed lean-pkg-binding.yaml (--write-binding-file). "
                        "Opt in (needs --reason): a receipt bound to commit C is also accepted at a descendant "
                        "of C where no build input under the package directory changed (*.lean, lakefile.*, "
                        "lake-manifest.json, lean-toolchain, --build-inputs globs, the checker's files; never a "
                        "symlink/gitlink change, .gitattributes, .gitmodules, .lfsconfig, claims.yaml, nor those "
                        "attribute files in a directory above a subdirectory package); the gated commit "
                        "stays C (docs/receipts.md); --dry-run supported")
    g.add_argument("--no-accept-docs-descendants", dest="docs_descendants", action="store_const", const=False,
                   help="opt out again (needs --reason): only the evidence-only rule applies")
    s.add_argument("--build-inputs", dest="build_inputs", metavar="GLOB,GLOB",
                   help="extra build inputs for the docs-only rule (needs --reason): comma-separated "
                        "package-relative fnmatch globs (`*` crosses /; a glob without / also matches a "
                        "basename), replacing the recorded list; '' clears it")
    s.add_argument("--write-binding-file", dest="write_binding_file", action="store_true",
                   help="write lean-pkg-binding.yaml (schema lean-pkg-binding/1) into the package directory from "
                        "the record's docs_descendants: true and build_inputs, the migration off the deprecated "
                        "registry route; writes nothing (exit 0) when the record does not opt in; an identical "
                        "file is a no-op, a different file or a symlink is refused (exit 1); never staged or "
                        "committed, and the registry is not written. On its own (no field, --status or --path); "
                        "--dry-run prints the file")
    s.add_argument("--reaudit-after", dest="reaudit_after", metavar="ISO8601")
    s.add_argument("--owner-project", dest="owner_project")
    s.add_argument("--url", dest="repo", metavar="URL", help="remote URL (e.g. after publishing a private dep)")
    s.add_argument("--license-spdx", dest="license_spdx", metavar="SPDX",
                   help="the package's license as an SPDX id or expression, e.g. 'MIT OR Apache-2.0' (what the index and Reservoir expect)")
    s.add_argument("--formalization-yaml", dest="formalization_yaml", metavar="REL",
                   help="repository-relative path of the package's formalization.yaml")
    s.add_argument("--headline-config", dest="headline_config", metavar="REL",
                   help="repository-relative path of the headline comparator configuration (the paper's main "
                        "results, fed to formalization.yaml status.main_results); '' clears it (then "
                        "comparator.headline.json applies when tracked)")
    s.add_argument("--paper-arxiv", dest="paper_arxiv", metavar="ID",
                   help="arXiv id of the paper this package verifies (sets paper.arxiv; status defaults to "
                        "preprint)")
    s.add_argument("--paper-status", dest="paper_status", choices=PAPER_STATUSES,
                   help="the verified paper's status; promote refuses while unpublished without an arXiv id")
    s.add_argument("--source-paper-title", dest="source_paper_title", metavar="TITLE",
                   help="title of the formalized paper (source_paper.title; rendered into formalization.yaml "
                        "acknowledgements, #22.6)")
    s.add_argument("--source-paper-author", dest="source_paper_author", action="append", metavar="NAME",
                   help="an author of the formalized paper (repeatable; the list replaces the recorded one). "
                        "Credited in acknowledgements, never in project.authors; no consent needed")
    s.add_argument("--source-paper-arxiv", dest="source_paper_arxiv", metavar="ID",
                   help="arXiv id of the formalized paper (source_paper.arxiv)")
    s.add_argument("--source-paper-doi", dest="source_paper_doi", metavar="DOI",
                   help="DOI of the formalized paper (source_paper.doi)")
    s.add_argument("--ai-tool", dest="ai_tool", action="append", metavar="TOOL",
                   help="an AI tool used (repeatable; the list replaces the recorded one; ai_assistance.tools)")
    s.add_argument("--ai-role", dest="ai_role", action="append", metavar="ROLE",
                   help="what the AI tools did (repeatable; replaces; ai_assistance.roles)")
    s.add_argument("--append-note", dest="append_note", metavar="TEXT",
                   help="append a line `YYYY-MM-DD (AGENT): TEXT` (UTC date, --agent) to notes, keeping what is "
                        "there; --notes replaces instead")
    s.add_argument("--namespace-note", dest="namespace_note", metavar="TEXT",
                   help="record why this package shares a Lean root namespace with another (namespace_notes); "
                        "audit's duplicate-namespace is info once both packages carry a note for it")
    s.add_argument("--namespace", action="append", metavar="NS",
                   help="with --namespace-note: the namespace (repeatable); default every root namespace this "
                        "checkout shares with another registered package")
    s.add_argument("--status", choices=STATUSES,
                   help="manual transitions only; verified-private is gate's "
                        "and public is promote's")
    s.add_argument("--reason", default="", help="required for --status and --force")
    s.add_argument("--redetect", action="store_true",
                   help="re-read lean_toolchain and mathlib_pin from the repo")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--force", action="store_true",
                   help="override a live lease held by another agent; needs --reason")
    s.add_argument("--path", default=None, metavar="HOME-RELATIVE",
                   help="relocate: move local_path to another checkout, keeping name, history "
                        "and claims (needs --reason; event relocated; commit-bound evidence "
                        "not bound to the new HEAD is cleared)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--withdraw-publication", dest="withdraw_publication", action="store_true",
                   help="the owner made a promoted (public) repository private again: record it (needs --reason; "
                        "refused while gh still reports it public). status public -> verified-private (active when "
                        "never gated), visibility private, publication_hold {reason, at, by}; promotion history "
                        "kept; promote refuses while held; gate/regate keep the hold; --dry-run supported")
    g.add_argument("--release-publication-hold", dest="release_hold", action="store_true",
                   help="lift a publication hold (needs --reason); status and visibility unchanged; a later "
                        "public flip still goes through promote")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="with --path, --withdraw-publication or --release-publication-hold: print the would-be "
                        "changes, write nothing; with --write-binding-file: print the file")
    add_json(s)
    s.set_defaults(fn=cmd_update)
    update_parser = s

    s = sub.add_parser("repo", help="the package's checkout: move (relocate the record), export (a new single-commit "
                                    "public repository), adopt (an existing one)")
    repo_sub = s.add_subparsers(dest="repo_cmd", required=True, metavar="{move,export,adopt}")
    s = repo_sub.add_parser("move", help="relocate: point the record at another checkout, keeping name, history "
                                         "and claims (the same as `update NAME --path PATH`)")
    s.add_argument("name")
    s.add_argument("path", metavar="HOME-RELATIVE",
                   help="the new checkout (event relocated; commit-bound evidence not bound to the new HEAD is "
                        "cleared)")
    s.add_argument("--reason", default="", help="required")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--force", action="store_true",
                   help="override a live lease held by another agent; needs --reason")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="print the would-be changes, write nothing")
    add_json(s)
    s.set_defaults(**_defaults_except(update_parser, s), fn=cmd_update, cmd="update")

    # `repo export|adopt` were `release NAME --fresh-repo|--adopt-repo DIR` before 1.0 (command_tree.MODE_ALIASES
    # rewrites the old argv). Each keeps release's `cmd` and the other forms' flags hidden, so cmd_release sees the
    # namespace it always saw and refuses a flag of another form as it always did.
    s = repo_sub.add_parser("export", help="export the gate-bound commit's tree (or --from-tree SRCDIR) as a NEW "
                                           "single-commit repository at DIR, then move the package there "
                                           "(`release NAME --fresh-repo DIR` is the same)")
    s.add_argument("name")
    s.add_argument("fresh_repo", metavar="DIR",
                   help="the new repository (must not exist, or be empty): internal files and the patterns file "
                        "dropped, public-safety scanned; then the package is relocated there (repo move): "
                        "evidence cleared, provenance note added")
    s.add_argument("--remote", metavar="URL", default=None,
                   help="git remote add origin URL, and set the record's repo")
    s.add_argument("--keep-receipts", action="store_true",
                   help="copy .lean-receipts/ from the source HEAD (they do not bind to the new commit)")
    s.add_argument("--source-commit", metavar="SHA", default=None,
                   help="export this commit instead of the gate-bound one")
    s.add_argument("--from-tree", metavar="SRCDIR", default=None,
                   help="export the working tree at SRCDIR (a hand-cleaned tree, not a commit; .gitignore "
                        "honoured in a checkout) instead of the gate-bound commit")
    s.add_argument("--author", metavar="IDENTITY", default=None,
                   help="'NAME <EMAIL>': author and committer of the export commit (default: user.name and "
                        "user.email of the source repository's local git config; the global identity is never "
                        "used)")
    s.add_argument("--allow-author-email", action="store_true",
                   help="accept an identity email that is not a GitHub noreply address (it enters public history)")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    s.add_argument("--dry-run", action="store_true",
                   help="stage and scan, print the plan; nothing written")
    add_json(s)
    export = s
    s = repo_sub.add_parser("adopt", help="adopt the existing repository at DIR as the public history, then move "
                                          "the package there (`release NAME --adopt-repo DIR` is the same)")
    s.add_argument("name")
    s.add_argument("adopt_repo", metavar="DIR",
                   help="the repository: exactly one commit unless --allow-history; tree and history "
                        "public-safety scanned; no new commit")
    s.add_argument("--remote", metavar="URL", default=None,
                   help="git remote add origin URL, and set the record's repo")
    s.add_argument("--allow-history", action="store_true",
                   help="accept a repository with more than one commit")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--reason", default="", help="free-text reason stored with the event")
    s.add_argument("--dry-run", action="store_true",
                   help="scan, print the plan; nothing written")
    add_json(s)
    adopt = s
    # an old argv naming both modes reaches cmd_release, which refuses it as before (adopt is checked first)
    export.add_argument("--adopt-repo", dest="adopt_repo", default=None, help=argparse.SUPPRESS)
    adopt.add_argument("--fresh-repo", dest="fresh_repo", default=None, help=argparse.SUPPRESS)
    for p in (lease_release, export, adopt):
        _hidden_copies(p, *(q for q in (lease_release, export, adopt) if q is not p))
        p.set_defaults(fn=cmd_release, cmd="release")

    s = sub.add_parser("gate", help="validate a lower-tier receipt; "
                                    "gate-pending -> verified-private; records the receipt's "
                                    "lean_toolchain and mathlib_rev as the package's pins")
    s.add_argument("name")
    s.add_argument("--receipt", required=True, help="path to receipt JSON")
    s.add_argument("--comparator", default=None, metavar="PATH",
                   help="optional comparator receipt JSON; "
                        "required later by promote")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when a receipt holds private strings; "
                        "default is a WARN line and the receipt is kept")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN "
                        "line (receipts are always checked against the bound commit)")
    s.add_argument("--refresh-formalization", action="store_true",
                   help="after the gate, regenerate the package's formalization.yaml (derived keys, "
                        "including status.evidence; kept keys survive) and set its registry pointer; "
                        "refuses when nothing would change")
    s.add_argument("--agent", default=None,
                   help="the agent acting (recorded as the event's actor); another agent's live lease then "
                        "refuses; with --refresh-formalization: the live lease holder (the file write needs it); "
                        "with --check: not used (a stderr note), carried into the printed next: command")
    s.add_argument("--check", action="store_true",
                   help="validate only, against HEAD (with --comparator: both tiers); write nothing, record "
                        "nothing (works at any status), like `comparator --check`; when it passes, prints the "
                        "writing gate to run next (next:, JSON next_command)")
    s.add_argument("--regate", action="store_true",
                   help="re-gate a verified-private (or public) package on a new receipt in one step: event "
                        "regated {previous_commit, commit, job_id, reason}, status unchanged; the receipt's "
                        "commit must descend from the gated commit unless --reason is given")
    s.add_argument("--reason", default=None, metavar="TEXT",
                   help="with --regate: accept a receipt commit that does not descend from the gated commit "
                        "(recorded with non_descendant: true)")
    s.add_argument("--repo", default=None, metavar="DIR",
                   help="with --check only: validate against the checkout DIR instead of the registered "
                        "local_path, for this call (never saved; JSON repo_override). DIR must be a git work "
                        "tree, and when it has remotes one must be the package's repo. A writing gate refuses "
                        "it: it binds the registered checkout (drop --repo, or move that with `update --path`)")
    add_json(s)
    s.set_defaults(fn=cmd_gate)

    # `comparator attach|check|config|coverage|migrate-signoff|host-check`: real subparsers, each with only its
    # flags (config and host-check were values of a positional before 1.0; check, coverage and migrate-signoff were
    # flags: command_tree.MODE_ALIASES rewrites the old argv). Each sets `name` as the positional did and the other
    # forms' flags to their defaults, hidden, so cmd_comparator sees the namespace it always saw and refuses a flag
    # of another form as it always did; `comparator NAME ...` is `comparator attach NAME ...`
    # (command_tree.IMPLIED_VERBS).
    s = sub.add_parser("comparator", help="comparator receipts: attach one to a gated package, or check it; "
                                          "config (write a configuration from claims), coverage, migrate-signoff; "
                                          "host-check")
    comparator_sub = s.add_subparsers(dest="comparator_cmd", required=True,
                                      metavar="{attach,check,config,coverage,migrate-signoff,host-check}")
    s = comparator_sub.add_parser("attach", help="attach a comparator receipt to a gated package; no status change "
                                                 "(`comparator NAME --receipt R` is the same)")
    s.add_argument("name")
    s.add_argument("--receipt", default=None, help="path to comparator receipt JSON (required)")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1, nothing copied) when the receipt holds private strings; "
                        "default is a WARN line and the receipt is kept")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN line")
    s.add_argument("--replace", action="store_true",
                   help="when .lean-receipts/ already holds a different comparator receipt for the same commit: "
                        "keep it as <c12>-comparator.replaced-<UTC stamp>.json, attach the new one, record "
                        "comparator-replaced")
    s.add_argument("--agent", default=None, help="the agent acting (recorded as the event's actor; another "
                                                  "agent's live lease then refuses)")
    add_json(s)
    attach = s
    s = comparator_sub.add_parser("check", help="validate a comparator receipt against the package; read-only, "
                                                "records nothing, works at any status (`comparator NAME --receipt R "
                                                "--check` is the same)")
    s.add_argument("name")
    s.add_argument("--receipt", default=None, help="path to comparator receipt JSON (required)")
    s.add_argument("--refuse-private", action="store_true",
                   help="refuse (exit 1) when the receipt holds private strings; default is a WARN line")
    s.add_argument("--refuse-dirty", action="store_true",
                   help="refuse when tracked files are modified in the checkout; default is a WARN line")
    s.add_argument("--repo", default=None, metavar="DIR",
                   help="validate against the checkout DIR instead of the registered local_path, for this call "
                        "(never saved; JSON repo_override). DIR must be a git work tree, and when it has remotes "
                        "one must be the package's repo")
    s.add_argument("--contracts-only", action="store_true",
                   help="no receipt: a read-only diagnostic of the compare step of every comparator configuration "
                        "in the checkout (Lake's own compareAt and checkAxioms on one shared export per module); "
                        "per failing configuration the first differing ConstantInfo field and the breadcrumb "
                        "from the target; no kernel runs, nothing is written, never acceptance")
    s.add_argument("--config", dest="contracts_configs", action="append", default=[], metavar="PATH",
                   help="with --contracts-only: only this configuration (relative to the checkout; repeatable; "
                        "default: comparator*.json and comparator/config*.json)")
    s.add_argument("--export", dest="contracts_exports", action="append", default=[], metavar="MODULE=FILE",
                   help="with --contracts-only: read this existing leanexport file for MODULE instead of "
                        "exporting it (repeatable; you answer for its freshness)")
    s.add_argument("--no-sandbox", dest="contracts_no_sandbox", action="store_true",
                   help="with --contracts-only: run leanexport and the compare helper without bwrap")
    add_json(s)
    check = s
    s = comparator_sub.add_parser("config", help="write a comparator configuration from claims (--from-claims); "
                                                 "coverage and migrate-signoff are verbs of their own")
    s.add_argument("--from-claims", default=None, metavar="NAME",
                   help="add a TODO theorem_names entry per synced claim (theorem/lemma) no comparator "
                        "configuration covers yet; never overwrites")
    s.add_argument("--out", default=None, help="the configuration to extend or create (default comparator.json)")
    s.add_argument("--dry-run", action="store_true", help="print the would-be JSON; write nothing")
    s.add_argument("--challenge-namespace", default=None,
                   help="namespace of the new Challenge names (default <Package>Audit)")
    s.add_argument("--agent", default=None, help="lease holder, if one is live")
    s.add_argument("--challenge-from-type", action="store_true",
                   help="pre-fill each new Challenge/Solution from the declaration's elaborated "
                        "type (runs `lake env lean` in the checkout)")
    s.add_argument("--emit-script", default=None, metavar="F",
                   help="write the type driver to F (run it elsewhere: cd CHECKOUT && bash F OUT.json); "
                        "no Lean runs here")
    s.add_argument("--from-output", default=None, metavar="F",
                   help="fill the scaffold from the driver's comparator-types/1 JSON F")
    s.add_argument("--repo", dest="checkout", default=None, metavar="DIR",
                   help="with --from-claims: use this package checkout instead of the registry's "
                        "local_path (HEAD must be the claims-synced commit or an evidence-only descendant)")
    s.add_argument("--claims-file", dest="claims_file", default=None, metavar="PATH",
                   help="with --from-claims: read the claims from this working-copy claims.yaml instead of "
                        "the registry's synced cache (no `claims sync` needed); schema-checked exactly like "
                        "`claims check --file`; never written to the registry; the output names the file and "
                        "its sha256")
    s.add_argument("--no-registry", action="store_true",
                   help="with --from-output/--challenge-from-type and --out FILE: scratch mode; write "
                        "the merged config to FILE outside the checkout, lease not consulted")
    s.add_argument("--allow-partial", action="store_true",
                   help="with --from-output/--challenge-from-type: ingest a run with extraction failures "
                        "(the failed entries keep TODO, restatement_reason extraction_failed)")
    s.add_argument("--lake-env", default=None, metavar="DIR",
                   help="with the type flags: Lake directory inside the checkout (default: the checkout)")
    s.add_argument("--jobs", type=int, default=None, help="with the type flags: parallel Lean processes (default 2, at most the CPUs; halved and retried on too many open files)")
    s.add_argument("--timeout", type=int, default=None,
                   help="with the type flags: seconds per declaration (default 120)")
    # the old mode flags, hidden: spread to the other forms below, so an old argv mixing modes reaches
    # cmd_comparator and is refused as before
    s.add_argument("--coverage", default=None, help=argparse.SUPPRESS)
    s.add_argument("--migrate-signoff", dest="migrate_signoff", default=None, help=argparse.SUPPRESS)
    add_json(s)
    config = s
    s = comparator_sub.add_parser("coverage", help="read-only: the claims covered by the comparator configuration "
                                                   "tracked at the gate-bound commit, and why a changed one needs a "
                                                   "new gate (`comparator config --coverage NAME` is the same)")
    s.add_argument("coverage", metavar="NAME")
    add_json(s)
    coverage = s
    s = comparator_sub.add_parser("migrate-signoff", help="move the deprecated inline judged_as / lean_pkg_scaffold "
                                                          "keys of a configuration into lean-pkg-signoff.json; dry "
                                                          "run unless --write (`comparator config --migrate-signoff "
                                                          "PATH` is the same)")
    s.add_argument("migrate_signoff", metavar="PATH",
                   help="the configuration; its Palomar keys are unchanged, its bytes (and so its receipt hash) "
                        "change")
    s.add_argument("--write", action="store_true", help="write both files")
    add_json(s)
    signoff = s
    s = comparator_sub.add_parser("host-check", help="can this host run the comparator; read-only")
    s.add_argument("--routes", action="store_true",
                   help="per route (judge = `lake comparator` in bwrap, prepare = setup "
                        "stages, transfer = build here, judge elsewhere) supported yes/no with the reason, "
                        "executables (type -P), locale, open-file limits, CPUs, cgroup memory and a command "
                        "line per supported route; read-only; exit 0 when any route is supported")
    add_json(s)
    host = s
    forms = ((attach, None), (check, None), (config, CONFIG_CMD), (coverage, CONFIG_CMD), (signoff, CONFIG_CMD),
             (host, HOST_CHECK))
    for p, _name in forms:   # `--check` (hidden): the check form's mark, on every form as the old flag was
        p.add_argument("--check", action="store_true", default=p is check, help=argparse.SUPPRESS)
    for p, name in forms:
        _hidden_copies(p, *(q for q, _ in forms if q is not p))
        p.set_defaults(fn=cmd_comparator, **({"name": name} if name else {}))

    s = registry_sub.add_parser("audit", help="report expired leases, gh visibility, drift; read-only without --write")
    s.add_argument("--write", dest="fix", action="store_true",
                   help="clear expired leases and update visibility/archive state from gh")
    s.add_argument("--toolchain-floor", default=None, metavar="VERSION",
                   help=f"oldest acceptable lean_toolchain (default: ${TOOLCHAIN_FLOOR_ENV}, "
                        f"else {DEFAULT_TOOLCHAIN_FLOOR})")
    s.add_argument("--promotion", action="store_true",
                   help="judge every package against the toolchain floor as for promote (warn, not info)")
    s.add_argument("--no-gh", action="store_true",
                   help="skip the GitHub (gh) checks: visibility and archived state are not compared")
    s.add_argument("--quiet", action="store_true",
                   help="only the count line (\"N finding(s): W warn, I info\", or \"no findings\"); no "
                        "per-finding line (--json is unchanged)")
    add_json(s)
    s.set_defaults(fn=cmd_audit, cmd="audit")

    s = registry_sub.add_parser("validate", help="schema + enum + history sanity; exit 0/1")
    add_json(s)
    s.set_defaults(fn=cmd_validate, cmd="validate")

    from . import recommended as recommended_mod
    recommended_mod.add_parser(registry_sub, add_json)

    s = sub.add_parser("doctor", help="installation, registry, index and tool diagnostics; "
                                      "exit 1 when the registry is unreadable or its schema "
                                      "version is unsupported")
    s.add_argument("name", nargs="?", default=None, metavar="NAME",
                   help="also check this package's committed lean-toolchain, Mathlib commit (lake-manifest.json) and "
                        "recorded comparator receipt's tool digests against the LTS stack its coverage.yaml declares "
                        "(`stack: NAME`; docs/stacks/): a warn line on a mismatch or a stack past its window, never "
                        "an exit 1")
    s.add_argument("--fetch", action="store_true",
                   help="git fetch origin in the tool checkout and the registry repo before comparing "
                        "(default: local refs)")
    s.add_argument("--package", dest="skew_package", default=None, metavar="NAME",
                   help="also compare the lean-pkg that wrote each gate receipt of package NAME (the receipt's "
                        "producer.lean_pkg) with the running one: a `producer <role>` warn line on a skew across "
                        "a breaking change to receipts, gate or claims, naming the upgrade-record entries and "
                        "the `doctor --upgrade-from` command, or on a newer producer; never an exit 1. --json "
                        "adds producer_skew")
    from . import upgrade as upgrade_mod
    upgrade_mod.add_arguments(s)
    add_json(s)
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("llms", help="print the agent-oriented usage contract")
    s.add_argument("--short", action="store_true",
                   help="a compact contract (~40 lines) cut from the full one: PURPOSE, STANDING RULES and the first "
                        "line of each subcommand; no flag details, schemas or examples")
    add_json(s)
    s.set_defaults(fn=cmd_llms)

    s = sub.add_parser("help", help="help topics (exit-codes, environment, formatting) or a command's --help",
                       description="Without an argument: the commands and help topics. With a topic "
                                   "(exit-codes, environment, formatting): that topic. With a command "
                                   "(`help status`, `help index add`): exactly its --help. Reads no registry.")
    s.add_argument("words", nargs="*", metavar="TOPIC|COMMAND")
    add_json(s)
    s.set_defaults(fn=cmd_help, help_root=ap)

    s = registry_sub.add_parser("wiki", help="render a Markdown status page")
    s.add_argument("--out", default=None,
                   help="write the Markdown here instead of stdout")
    s.add_argument("--candidates", default=None,
                   help="adoption ledger path (default: lean-adoption-candidates.yaml "
                        "next to the registry)")
    add_json(s)
    s.set_defaults(fn=cmd_wiki, cmd="wiki")

    from . import index as index_mod
    index_mod.add_parsers(sub, add_json)
    from . import claims as claims_mod
    claims_mod.add_parsers(sub, add_json)
    from . import search as search_mod
    search_mod.add_parsers(sub, add_json)
    from . import formalization as formalization_mod
    formalization_mod.add_parsers(sub, add_json)
    from . import publish as publish_mod
    publish_mod.add_parsers(sub, add_json)
    from . import coverage as coverage_mod
    coverage_mod.add_parsers(sub, add_json)
    from . import page as page_mod
    page_mod.add_parsers(sub, add_json)
    from . import reservoir as reservoir_mod
    reservoir_mod.add_parsers(sub, add_json)
    from . import blueprint as blueprint_mod
    blueprint_mod.add_parsers(sub, add_json)
    from . import public_safety as public_safety_mod
    public_safety_mod.add_parsers(sub, add_json)
    from . import palomar as palomar_mod
    palomar_mod.add_parsers(sub, add_json)
    from . import next_step as next_mod
    next_mod.add_parsers(sub, add_json)
    from . import report as report_mod
    report_mod.add_parsers(sub, add_json)
    from . import receipt as receipt_mod
    receipt_mod.add_parsers(sub, add_json)
    from . import verify as verify_mod
    verify_mod.add_parsers(sub, add_json)

    from . import command_tree
    show_commit_flags_on_writes(ap)   # before the flag aliases: they annotate the help texts left shown
    command_tree.apply_flag_aliases(ap)
    command_tree.apply_tiers(ap)
    command_tree.register_root(ap)
    ap.alias_root = True   # old spellings are rewritten to canonical ones before parsing
    ap.set_defaults(fn=cmd_board)
    return ap
