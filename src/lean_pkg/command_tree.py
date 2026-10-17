# lean-pkg command tree: canonical spellings, permanent aliases, the newcomer page
"""The 1.0 command tree's spellings (docs/design/1.0-command-tree.md, accepted 2026-10-17).

Every renamed command or flag keeps its old spelling as a PERMANENT alias: the old argv parses to the
same namespace as the canonical one, JSON output and exit codes are unchanged, and text mode prints one
stderr note (`note: 'lean-pkg claim' is now 'lean-pkg lease claim' (the old spelling keeps working)`),
which LEAN_PKG_NO_ALIAS_NOTE=1 silences. Deprecation applies to behavior only, never to a spelling
(RELEASING.md).

This module is pure data plus argv rewriting: nothing here reads the registry or the network. Public API
(the CLI, the contract tests and the command-reference generator read it; keep it stable):

- COMMAND_ALIASES: tuple[CommandAlias, ...]. `old` words -> `new` (canonical) words, rewritten as an argv
  prefix before argparse runs (`claim NAME ...` -> `lease claim NAME ...`).
- IMPLIED_VERBS: {noun: ImpliedVerb}. `noun ARG ...` where ARG is not one of the noun's verbs means
  `noun verb ARG ...` (`lease NAME` -> `lease claim NAME`, `comparator NAME --receipt R` ->
  `comparator attach NAME --receipt R`, `formalization NAME` -> `formalization render NAME`).
- MODE_ALIASES: tuple[ModeAlias, ...]. On command `command`, the flag `flag` selected another job before 1.0;
  the argv is rewritten to the command `new`, the flag's value (if it takes one) becoming that command's
  last positional (`lease release NAME --fresh-repo DIR` -> `repo export NAME DIR`).
- FLAG_ALIASES: tuple[FlagAlias, ...]. On the canonical command `command`, the option `old` is another
  option string of `new` (same dest, same namespace); `apply_flag_aliases(root)` wires them.
- TIERS: {top-level command: tier}; `apply_tiers(root)` stores each tier on its parser (`parser.tier`), where
  the tiered root --help and the command reference read it. TIER_TITLES names the tiers.
- aliases_of(path) / flag_aliases_of(path): the alias spellings of one canonical command, for its page.
- canonical_command(args): the canonical command path ("lease claim") a parsed namespace selects.
- rewrite_argv(argv) / rewrite_words(words): the rewriting itself; each returns (new argv, hits).
- NEWCOMER_VERBS, TRACKS, newcomer_text(), aliases_text(): the newcomer page and `lean-pkg help aliases`.

Internally a moved command keeps its pre-1.0 `cmd` value in the namespace (`lease claim` parses with
cmd == "claim"), so every predicate keyed on it (is_write_invocation, the lease warning, committed mode)
is unchanged; LEGACY_CMD maps it to the canonical path.
"""

from __future__ import annotations

import argparse
import os
import textwrap
from dataclasses import dataclass

NOTE_ENV = "LEAN_PKG_NO_ALIAS_NOTE"   # 1/true/yes/on: no alias note on stderr


@dataclass(frozen=True)
class CommandAlias:
    old: tuple[str, ...]   # the old spelling, as argv words after the global flags
    new: tuple[str, ...]   # the canonical words it is rewritten to
    unless: tuple[str, tuple[str, ...]] | None = None   # (flag, words): without the flag, these words follow
    #                                                   the new command (`promote NAME` -> `publish --yes --dry-run NAME`)


@dataclass(frozen=True)
class ModeAlias:
    command: tuple[str, ...]   # canonical command path the old argv names (after COMMAND_ALIASES)
    flag: str                  # the flag that selected another job
    new: tuple[str, ...]       # the command that job is now
    value: bool                # the flag takes a value, which becomes the new command's last positional


@dataclass(frozen=True)
class ImpliedVerb:
    verb: str                    # inserted after the noun
    verbs: tuple[str, ...]       # the noun's verbs: a first word among them is never rewritten
    unless_flag: str | None = None   # ...unless this flag is present: then a verb other than `verb` names a
    #                                  package, as before 1.0 (`comparator config --receipt R`)
    named: tuple[str, ...] = ()      # verbs that take NAME first and also take the flag: such a verb names a
    #                                  package only when no NAME follows it (`comparator check --receipt R`)


@dataclass(frozen=True)
class FlagAlias:
    command: tuple[str, ...]   # canonical command path
    old: str                   # the old option string
    new: str                   # the canonical option string (same dest)


COMMAND_ALIASES: tuple[CommandAlias, ...] = (
    # coordination: leases (multi-agent registries)
    CommandAlias(("claim",), ("lease", "claim")),
    CommandAlias(("renew",), ("lease", "renew")),
    CommandAlias(("release",), ("lease", "release")),
    CommandAlias(("leases",), ("lease", "list")),
    # maintenance: the registry file itself
    CommandAlias(("init",), ("registry", "init")),
    CommandAlias(("migrate",), ("registry", "migrate")),
    CommandAlias(("validate",), ("registry", "validate")),
    CommandAlias(("audit",), ("registry", "audit")),
    CommandAlias(("board",), ("registry", "board")),
    CommandAlias(("wiki",), ("registry", "wiki")),
    # evidence
    CommandAlias(("receipt", "local"), ("receipt", "systemd")),
    CommandAlias(("verify-receipts",), ("receipt", "check")),
    # publication: `promote NAME` was the dry run of `promote NAME --yes`
    CommandAlias(("promote",), ("publish",), unless=("--yes", ("--yes", "--dry-run"))),
)

# (the old form's tail after the command, the new form's tail after the new command), for notes and docs
MODE_USAGE: dict[str, tuple[str, str]] = {
    "--fresh-repo": ("NAME --fresh-repo DIR", "NAME DIR"),
    "--adopt-repo": ("NAME --adopt-repo DIR", "NAME DIR"),
    "--check": ("NAME --receipt R --check", "NAME --receipt R"),
    "--coverage": ("--coverage NAME", "NAME"),
    "--migrate-signoff": ("--migrate-signoff PATH", "PATH"),
}

MODE_ALIASES: tuple[ModeAlias, ...] = (
    # `release` released a lease, or exported/adopted a repository
    # (adopt first: release checked --adopt-repo first, so an argv naming both is refused as before)
    ModeAlias(("lease", "release"), "--adopt-repo", ("repo", "adopt"), True),
    ModeAlias(("lease", "release"), "--fresh-repo", ("repo", "export"), True),
    # `comparator NAME --receipt R --check` validated without attaching
    ModeAlias(("comparator", "attach"), "--check", ("comparator", "check"), False),
    # `comparator config` had three modes; the two that are not "write a configuration" are verbs now
    ModeAlias(("comparator", "config"), "--coverage", ("comparator", "coverage"), True),
    ModeAlias(("comparator", "config"), "--migrate-signoff", ("comparator", "migrate-signoff"), True),
)

IMPLIED_VERBS: dict[str, ImpliedVerb] = {
    # `lease NAME` was the alias of `claim NAME` (0.7)
    "lease": ImpliedVerb("claim", ("claim", "renew", "release", "list")),
    # `comparator NAME --receipt R [--check]`; `comparator config|host-check --receipt R` named a package
    # (`comparator check NAME --receipt R` is the check verb; `comparator check --receipt R` the package)
    "comparator": ImpliedVerb("attach", ("attach", "check", "config", "coverage", "migrate-signoff", "host-check"),
                              unless_flag="--receipt", named=("check",)),
    # `formalization NAME [--write]` renders formalization.yaml
    "formalization": ImpliedVerb("render", ("render", "anchor")),
}

FLAG_ALIASES: tuple[FlagAlias, ...] = (
    # the DOI track, not the product track (`new --kind paper|library`)
    FlagAlias(("record",), "--track", "--doi-track"),
    FlagAlias(("update",), "--track", "--doi-track"),
    # the receipt's commit, not the global --commit (commit the registry files)
    FlagAlias(("receipt", "slurm"), "--commit", "--receipt-commit"),
    FlagAlias(("receipt", "systemd"), "--commit", "--receipt-commit"),
    # --repo is a checkout directory; a remote URL is --url, a GitHub slug --target-repo
    FlagAlias(("register",), "--repo", "--url"),
    FlagAlias(("update",), "--repo", "--url"),
    FlagAlias(("index", "add"), "--repo", "--url"),
    FlagAlias(("index", "check"), "--repo-dir", "--repo"),
    FlagAlias(("search",), "--repo-dir", "--repo"),
    FlagAlias(("comparator", "config"), "--checkout", "--repo"),
    FlagAlias(("report",), "--repo", "--target-repo"),
    # a command that changes the registry and previews without the flag says --write
    FlagAlias(("registry", "audit"), "--fix", "--write"),
)

# The pre-1.0 `cmd` a moved command keeps in its namespace -> its canonical path.
LEGACY_CMD: dict[str, str] = {
    "claim": "lease claim", "renew": "lease renew", "release": "lease release", "leases": "lease list",
    "init": "registry init", "migrate": "registry migrate", "validate": "registry validate",
    "audit": "registry audit", "board": "registry board", "wiki": "registry wiki",
}

# Top-level commands by tier (the root --help, the command reference index). Every top-level parser has one
# (apply_tiers fails loudly otherwise); an alias has none (it is not a parser).
TIER_TITLES: dict[str, str] = {
    "newcomer": "START HERE: one package on your own machine",
    "author": "PACKAGE AUTHOR: evidence, publication, the catalog",
    "coordination": "COORDINATION AND MAINTENANCE: shared registries, leases, the registry file",
}
TIERS: dict[str, str] = {
    "new": "newcomer", "verify": "newcomer", "status": "newcomer", "page": "newcomer", "publish": "newcomer",
    "doctor": "newcomer", "help": "newcomer",
    "register": "author", "update": "author", "record": "author", "gate": "author", "next": "author",
    "list": "author", "search": "author", "receipt": "author", "comparator": "author", "claims": "author",
    "coverage": "author", "formalization": "author", "palomar": "author", "reservoir": "author",
    "public-safety": "author", "report": "author",
    "lease": "coordination", "registry": "coordination", "repo": "coordination", "index": "coordination",
    "llms": "coordination",
}

# argparse dests of the nouns' verb (and of the older nested commands), for canonical_command.
NOUN_DESTS = {"lease": "lease_cmd", "registry": "registry_cmd", "repo": "repo_cmd",
              "comparator": "comparator_cmd", "formalization": "formalization_cmd"}
NESTED_DESTS = ("index_cmd", "claims_cmd", "reservoir_cmd", "palomar_cmd", "receipt_cmd", "coverage_cmd")

# Root options that take a value (skipped when looking for the command word).
GLOBAL_VALUE_FLAGS = ("--registry", "--events", "--registry-snapshot")


# --------------------------------------------------------------------------
# rewriting


@dataclass(frozen=True)
class Hit:
    old: str   # the old spelling as typed, e.g. "claim" or "lease NAME" or "record --track"
    new: str   # the canonical spelling


def _command_index(argv: list[str], value_flags=GLOBAL_VALUE_FLAGS) -> int | None:
    """Index of the command word in argv (after the global flags), else None."""
    i = 0
    while i < len(argv):
        t = argv[i]
        if t == "--":
            return None
        if t.startswith("-") and t != "-":
            takes_value = "=" not in t and (t in value_flags or (
                t.startswith("--") and len(t) > 2 and sum(f.startswith(t) for f in value_flags) == 1
                and not any(f.startswith(t) for f in ("--json", "--commit", "--push", "--no-commit", "--version",
                                                      "--help"))))
            i += 2 if takes_value else 1
            continue
        return i
    return None


def _flag_at(words: list[str], flag: str, stop: int) -> int | None:
    """Index of `flag` (or `flag=VALUE`) among words[:stop], else None."""
    return next((i for i, w in enumerate(words[:stop]) if w == flag or w.startswith(flag + "=")), None)


def rewrite_words(words: list[str], implied: bool = True) -> tuple[list[str], list[Hit]]:
    """Rewrite command words (no global flags in front) to their canonical spelling."""
    words, hits = list(words), []
    typed = ""   # the old command words as typed, for a mode alias's note
    for a in sorted(COMMAND_ALIASES, key=lambda a: -len(a.old)):
        if tuple(words[:len(a.old)]) == a.old:
            rest = words[len(a.old):]
            stop = rest.index("--") if "--" in rest else len(rest)
            new, old_text, new_text = list(a.new), " ".join(a.old), " ".join(a.new)
            if a.unless is not None:
                flag, extra = a.unless
                if _flag_at(rest, flag, stop) is None:
                    new += list(extra)
                    new_text = " ".join(list(a.new) + list(extra))
                else:
                    old_text, new_text = f"{old_text} {flag}", f"{new_text} {flag}"
            words = new + rest
            hits.append(Hit(old_text, new_text))
            typed = " ".join(a.old)
            break
    if implied and len(words) > 1 and words[0] in IMPLIED_VERBS:
        noun, iv, nxt = words[0], IMPLIED_VERBS[words[0]], words[1]
        guarded = iv.unless_flag is not None and nxt != iv.verb and any(
            w == iv.unless_flag or w.startswith(iv.unless_flag + "=") for w in words[2:]) and (
            nxt not in iv.named or len(words) < 3 or words[2].startswith("-"))
        if nxt not in ("-h", "--help") and (nxt not in iv.verbs or guarded):
            words = [noun, iv.verb] + words[1:]
            old = " ".join(h.old for h in hits) or noun
            hits = [Hit(f"{old} NAME", f"{noun} {iv.verb} NAME")]
            typed = f"{old} NAME"
    for m in MODE_ALIASES:
        n = len(m.command)
        if tuple(words[:n]) != m.command:
            continue
        rest = words[n:]
        stop = rest.index("--") if "--" in rest else len(rest)
        i = _flag_at(rest, m.flag, stop)
        if i is None:
            continue
        tok = rest[i]
        if not m.value:
            if "=" in tok:   # `--check=x`: not this flag's spelling; argparse refuses it as before
                continue
            del rest[i]
            stop -= 1
        elif "=" in tok:
            value = tok.split("=", 1)[1]
            del rest[i]
            stop -= 1
            rest.insert(stop, value)
        elif i + 1 < stop:
            value = rest[i + 1]
            del rest[i:i + 2]
            stop -= 2
            rest.insert(stop, value)
        else:   # the flag without its value: leave it, argparse names the problem on the new command
            continue
        old_cmd = typed or " ".join(m.command)
        if old_cmd.endswith(" NAME"):
            old_cmd = old_cmd[:-len(" NAME")]
        words = list(m.new) + rest
        hits = [Hit(f"{old_cmd} {MODE_USAGE[m.flag][0]}", f"{' '.join(m.new)} {MODE_USAGE[m.flag][1]}")]
        break
    return words, hits


def rewrite_argv(argv: list[str], value_flags=GLOBAL_VALUE_FLAGS) -> tuple[list[str], list[Hit]]:
    """The whole argv (global flags included) with its command words canonical."""
    argv = list(argv)
    i = _command_index(argv, value_flags)
    if i is None:
        return argv, []
    words, hits = rewrite_words(argv[i:])
    return argv[:i] + words, hits


def flag_hits(argv: list[str], path: str) -> list[Hit]:
    """The old flag spellings used on canonical command `path` (as returned by canonical_command)."""
    i = _command_index(argv)
    tokens = argv[i:] if i is not None else []
    if "--" in tokens:
        tokens = tokens[:tokens.index("--")]
    out = []
    for fa in FLAG_ALIASES:
        if " ".join(fa.command) == path and any(t == fa.old or t.startswith(fa.old + "=") for t in tokens):
            out.append(Hit(f"{path} {fa.old}", f"{path} {fa.new}"))
    return out


def note_lines(hits: list[Hit]) -> list[str]:
    return [f"note: 'lean-pkg {h.old}' is now 'lean-pkg {h.new}' (the old spelling keeps working)" for h in hits]


def notes_enabled() -> bool:
    return os.environ.get(NOTE_ENV, "").strip().lower() not in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# the parser side


def _sub_action(parser: argparse.ArgumentParser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def find_parser(root: argparse.ArgumentParser, path: tuple[str, ...]) -> argparse.ArgumentParser | None:
    p = root
    for word in path:
        action = _sub_action(p)
        if action is None or word not in action.choices:
            return None
        p = action.choices[word]
    return p


def apply_flag_aliases(root: argparse.ArgumentParser) -> None:
    """Make each FLAG_ALIASES old spelling parse as its canonical option (same action, same dest)."""
    for fa in FLAG_ALIASES:
        p = find_parser(root, fa.command)
        if p is None:
            raise AssertionError(f"FLAG_ALIASES: no command {' '.join(fa.command)!r}")
        action = p._option_string_actions.get(fa.new)
        if action is None:
            raise AssertionError(f"FLAG_ALIASES: {' '.join(fa.command)} has no {fa.new}")
        if fa.old in p._option_string_actions:
            raise AssertionError(f"FLAG_ALIASES: {' '.join(fa.command)} already has {fa.old}")
        # Parsed exactly like the canonical option, but named in its help text rather than added to
        # option_strings: argparse lays out a multi-spelling option differently across Python versions,
        # and the help goldens are compared word by word.
        p._option_string_actions[fa.old] = action
        if action.help and action.help is not argparse.SUPPRESS:
            action.help = f"{action.help} (old spelling {fa.old}, kept working)"


def apply_tiers(root: argparse.ArgumentParser) -> None:
    """Store each top-level command's tier on its parser (`parser.tier`); the root --help and the command
    reference group by it. A top-level command without a tier, or a tier for no command, fails loudly."""
    action = _sub_action(root)
    names = set(action.choices)
    missing, unknown = sorted(names - set(TIERS)), sorted(set(TIERS) - names)
    if missing or unknown:
        raise AssertionError(f"command_tree.TIERS: no tier for {missing}; no command {unknown}")
    for name, p in action.choices.items():
        p.tier = TIERS[name]


def tier_rows(root: argparse.ArgumentParser) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(tier, [(command, one-line help)])] in TIER_TITLES order, commands in TIERS order, read from the
    parser (`parser.tier`, set by apply_tiers; the help from each command's add_parser)."""
    action = _sub_action(root)
    helps = {a.dest: a.help or "" for a in action._choices_actions}
    out = []
    for tier in TIER_TITLES:
        names = [n for n in TIERS if getattr(action.choices.get(n), "tier", None) == tier]
        out.append((tier, [(n, helps.get(n, "")) for n in names]))
    return out


def root_help(root: argparse.ArgumentParser) -> str:
    """The root --help in tiers: usage, the commands grouped newcomer / package author / coordination (from
    the parser's tier data), then the global options and the epilog."""
    fmt = root._get_formatter()
    width = max(fmt._width, 60)
    out = [root.format_usage().rstrip(), ""]
    if root.description:
        out += [textwrap.fill(root.description, width), ""]
    rows = tier_rows(root)
    col = max(len(n) for _t, names in rows for n, _h in names) + 4
    for tier, names in rows:
        out.append(TIER_TITLES[tier])
        for name, text in names:
            body = textwrap.wrap(text, max(width - col, 30)) or [""]
            out.append(f"  {name.ljust(col - 2)}{body[0]}")
            out += [" " * col + more for more in body[1:]]
        out.append("")
    fmt = root._get_formatter()
    fmt.start_section("global options")
    fmt.add_arguments([a for a in root._actions if a.option_strings])
    fmt.end_section()
    out.append(fmt.format_help().strip("\n"))
    if root.epilog:
        out += ["", textwrap.fill(root.epilog, width)]
    return "\n".join(out) + "\n"


def tier_of(root: argparse.ArgumentParser, name: str) -> str | None:
    action = _sub_action(root)
    p = action.choices.get(name) if action else None
    return getattr(p, "tier", None)


def canonical_command(args) -> str:
    """The canonical command path a parsed namespace selects ("" for a bare `lean-pkg`)."""
    cmd = getattr(args, "cmd", None)
    if cmd is None:
        return ""
    if getattr(args, "repo_cmd", None):   # `repo move|export|adopt` keep update's / release's cmd
        return f"repo {args.repo_cmd}"
    if cmd in LEGACY_CMD:
        return LEGACY_CMD[cmd]
    if cmd in NOUN_DESTS and getattr(args, NOUN_DESTS[cmd], None):
        return f"{cmd} {getattr(args, NOUN_DESTS[cmd])}"
    for dest in NESTED_DESTS:
        sub = getattr(args, dest, None)
        if sub:
            return f"{cmd} {sub}"
    return cmd


def aliases_of(path: tuple[str, ...] | str) -> list[str]:
    """The old spellings of a canonical command, e.g. aliases_of("lease claim") == ["claim", "lease NAME"]."""
    path = tuple(path.split()) if isinstance(path, str) else tuple(path)
    out = [" ".join(a.old) for a in COMMAND_ALIASES if a.new == path]
    if len(path) == 2 and path[0] in IMPLIED_VERBS and IMPLIED_VERBS[path[0]].verb == path[1]:
        out.append(f"{path[0]} NAME")
    for m in MODE_ALIASES:
        if m.new == path:
            tail = MODE_USAGE[m.flag][0]
            out.append(f"{' '.join(m.command)} {tail}")
            out += [f"{' '.join(a.old)} {tail}" for a in COMMAND_ALIASES if a.new == m.command]
            if len(m.command) == 2 and m.command[0] in IMPLIED_VERBS and IMPLIED_VERBS[m.command[0]].verb == m.command[1]:
                out.append(f"{m.command[0]} {tail}")
    return out


def flag_aliases_of(path: tuple[str, ...] | str) -> dict[str, list[str]]:
    """{canonical flag: [old spellings]} on one canonical command."""
    path = tuple(path.split()) if isinstance(path, str) else tuple(path)
    out: dict[str, list[str]] = {}
    for fa in FLAG_ALIASES:
        if fa.command == path:
            out.setdefault(fa.new, []).append(fa.old)
    return out


# --------------------------------------------------------------------------
# the newcomer page and `help aliases`

# (verb, usage, one line). Availability is read from the parser, never assumed.
NEWCOMER_VERBS: tuple[tuple[str, str, str], ...] = (
    ("new", "new NAME", "scaffold a Lean package that the receipts, the gate and the page accept"),
    ("verify", "verify NAME", "run the lower-tier receipt, the comparator receipt and the gate in one step"),
    ("status", "status NAME", "the package's record, history and scope"),
    ("page", "page NAME", "the README verification section: what is verified, at which commit"),
    ("publish", "publish NAME", "the checklist from verified-private to public"),
)

# What to run until a newcomer verb arrives.
UNTIL_THEN = {
    "new": "registry init, then register NAME --url URL --path DIR",
}

# (step, track P: the paper is the product, track L: the Lean package is the product)
TRACKS: tuple[tuple[str, str, str], ...] = (
    ("start", "new NAME --kind paper", "new NAME --kind library"),
    ("bind the source", "formalization anchor NAME --arxiv IDvN", "(none: the rows are the main results)"),
    ("verify", "verify NAME", "verify NAME"),
    ("page", "page NAME --write", "page NAME --write"),
    ("publish", "publish NAME --yes", "publish NAME --yes"),
    ("after", "palomar prepare NAME", "index add NAME"),
)

_ROOT: list[argparse.ArgumentParser] = []   # the parser main built, for availability


def register_root(root: argparse.ArgumentParser) -> None:
    _ROOT[:] = [root]


def missing(command: str, root: argparse.ArgumentParser | None = None) -> str | None:
    """What of `command` (words, placeholders in capitals, flags) the parser lacks: "verify" (a command),
    "new --kind" (a flag of it), else None. None too when no parser is known (nothing to check)."""
    root = root or (_ROOT[0] if _ROOT else None)
    if root is None or command.startswith("("):
        return None
    p, path = root, []
    for word in command.split():
        if word.startswith("-") or not word.islower():
            break
        action = _sub_action(p)
        if action is None:
            break
        if word not in action.choices:
            return " ".join(path + [word])
        p = action.choices[word]
        path.append(word)
    flag = next((f for f in command.split() if f.startswith("--") and f not in p._option_string_actions), None)
    return f"{' '.join(path)} {flag}" if flag else None


def available(command: str, root: argparse.ArgumentParser | None = None) -> bool:
    return missing(command, root) is None


def newcomer_text(root: argparse.ArgumentParser | None = None) -> str:
    """The newcomer page: five verbs, one line each, and the two product tracks as two columns. A command
    the installed lean-pkg does not have yet is marked `*`, with what to run until then."""
    pending: list[str] = []

    def mark(c: str) -> str:
        m = missing(c, root)
        if m is None:
            return c
        if m not in pending:
            pending.append(m)
        return c + " *"

    out = ["lean-pkg: a registry and trust gate for Lean 4 formalization packages.", "",
           "START HERE: five verbs"]
    for _verb, usage, line in NEWCOMER_VERBS:
        out.append(f"  {mark(usage).ljust(16)}{line}")
    out += ["", "TWO TRACKS: what you publish"]
    rows = [("", "paper (the paper is the product)", "library (the Lean package is)")]
    rows += [(step, mark(p), mark(lib)) for step, p, lib in TRACKS]
    w0 = max(len(r[0]) for r in rows) + 2
    w1 = max(len(r[1]) for r in rows) + 2
    out += [f"  {step.ljust(w0)}{p.ljust(w1)}{lib}".rstrip() for step, p, lib in rows]
    if pending:
        out += ["", "* coming in the next release; until then:"]
        out += [f"  {m}: {UNTIL_THEN.get(m, 'see lean-pkg help')}" for m in pending]
    out += ["", "MORE",
            "  lean-pkg help            every command and the help topics",
            "  lean-pkg help aliases    old spellings that keep working",
            "  lean-pkg <command> -h    one command's flags"]
    return "\n".join(out) + "\n"


def aliases_text() -> str:
    """`lean-pkg help aliases`: every permanent alias, command and flag."""
    out = ["Old spellings are permanent aliases: they parse exactly like the canonical spelling, with the",
           "same JSON output and exit codes. In text mode each use prints one stderr note; set",
           f"{NOTE_ENV}=1 to silence it. Deprecation applies to behavior, never to a spelling.", "",
           "COMMANDS"]
    rows = [(" ".join(a.old), " ".join(a.new) + (f" (without {a.unless[0]}: {' '.join(a.new + a.unless[1])})"
                                                   if a.unless else "")) for a in COMMAND_ALIASES]
    rows += [(f"{noun} NAME ...", f"{noun} {iv.verb} NAME ...") for noun, iv in IMPLIED_VERBS.items()]
    rows += [(f"{' '.join(m.command)} {MODE_USAGE[m.flag][0]}", f"{' '.join(m.new)} {MODE_USAGE[m.flag][1]}")
             for m in MODE_ALIASES]
    width = max(len(o) for o, _ in rows) + 4
    out += [f"  lean-pkg {o.ljust(width)}lean-pkg {n}" for o, n in rows]
    out += ["", "FLAGS"]
    rows = [(f"{' '.join(fa.command)} {fa.old}", fa.new) for fa in FLAG_ALIASES]
    width = max(len(o) for o, _ in rows) + 4
    out += [f"  {o.ljust(width)}{n}" for o, n in rows]
    out += ["", "PROGRAMS",
            "  comparator-receipt ...    lean-pkg receipt comparator ...   (the same code and flags; both stay)"]
    out += ["", textwrap.fill("`comparator config|host-check|check|coverage --receipt R` (no NAME after the verb) "
                              "still attaches a receipt to a package named so, as before.", 100)]
    return "\n".join(out) + "\n"
