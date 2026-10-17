#!/usr/bin/env python3
"""Extract a scrubbed compatibility corpus of `lean-pkg ...` invocations from documents and scripts.

    PYTHONPATH=src python3 scripts/extract-compat-corpus.py --git-repo DIR [--git-repo DIR ...] \\
        [--file FILE ...] [--scrub-names-from REGISTRY.yaml] --out tests/data/compat-corpus.txt

Sources are read only. A git repository contributes its tracked text files (`git ls-files`); --file adds
single files (a wrapper script). Invocations are taken from code only: fenced blocks and inline backtick
spans of any text file, plus the non-comment lines of shell scripts. A span counts when the word after
`lean-pkg` (past the global options) is a top-level command or the span is global options alone.

Every word is scrubbed before it is written: flags, command names and choice values of the real parser
are kept, numbers are kept, a value of an int option becomes 1, a verb right after a command group
(`index build`) is kept, and every other word (package names, paths, URLs, placeholders such as NAME,
<name> or $VAR, free text) becomes `demo`. The corpus therefore carries no host, user, path or package
name of the sources. tests/test_cli_contract_corpus.py parses each line with the real parser."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.cli_contract import command_tree, parser  # noqa: E402

PLACEHOLDER = "demo"
SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}
TEXT_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".jsonl", ".toml", ".py", ".org", ".rst",
                 ".tex", ".cfg", ".ini", ".html", ".tsv", ".csv", ""}
GLOBAL_VALUE_OPTS = {"--registry", "--events", "--registry-snapshot"}
INVOKE = re.compile(r"(?:^|(?<=[\s\"'(=/]))lean-pkg(?=\s|$)")
FENCE = re.compile(r"^\s*(```|~~~)")
# What may precede `lean-pkg` in command position: a prompt sign, env assignments, a directory, or a
# shell operator, opening paren, command substitution or llms-style `ex:` before them. Anything else is
# prose.
COMMAND_POSITION = re.compile(r"(?:^|(?:&&|\|\||;|\||\$\(|\(|\bexec|\bthen|\bdo|\bex:)\s*)\s*(?:\$\s+)?"
                              r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*[\"']?(?:\S*/)?$")


def _accepts_number(conv) -> bool:
    try:
        conv("1")
    except Exception:   # noqa: BLE001 -- any refusal means "not a numeric option"
        return False
    return True


def vocabulary():
    ap = parser()
    tree = command_tree(ap)
    top, groups, words, int_opts = set(), set(), set(), set()
    for node in tree:
        for a in node.parser._actions:
            words.update(a.option_strings)
            if isinstance(a, argparse._SubParsersAction):
                words.update(a.choices)
                if not node.path:
                    top.update(a.choices)
                else:
                    groups.add(node.path[-1])
            elif a.choices:
                words.update(str(c) for c in a.choices)
            if a.type not in (None, str) and _accepts_number(a.type):
                int_opts.update(a.option_strings)   # int, float, or a converter such as hours
    from lean_pkg import command_tree as ct   # old spellings (claim, init, receipt local, ...) are commands too
    for a in ct.COMMAND_ALIASES:
        top.add(a.old[0])
        words.update(a.old)
    words.update(fa.old for fa in ct.FLAG_ALIASES)
    return top, groups, words, int_opts


def strip_comment(line: str) -> str:
    """A shell line up to its comment (`#` at the start or after a blank, outside quotes)."""
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def code_spans(path: Path, text: str):
    """(code text) pieces of a file: fenced blocks and backtick spans; shell scripts line by line."""
    is_shell = path.suffix in SHELL_SUFFIXES or text.startswith("#!") and "sh" in text.split("\n", 1)[0]
    lines = text.splitlines()
    joined: list[str] = []
    buf = ""
    for line in lines:   # shell-style continuations
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        joined.append(buf + line)
        buf = ""
    if buf:
        joined.append(buf)
    in_fence = False
    for line in joined:
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or is_shell:
            yield strip_comment(line)
        yield from re.findall(r"`([^`\n]+)`", line)


def cut(snippet: str) -> str:
    """The invocation text up to the first shell operator outside quotes; placeholders flattened."""
    s = snippet
    s = re.sub(r"\$\([^()]*\)", PLACEHOLDER, s)
    s = re.sub(r"\$\{[^{}]*\}", PLACEHOLDER, s)
    s = re.sub(r"\$[A-Za-z_@*#?][A-Za-z0-9_]*", PLACEHOLDER, s)
    s = re.sub(r"<[^<>]*>", PLACEHOLDER, s)
    s = s.replace("…", " ").replace("...", " ")
    for _ in range(4):   # optional groups: [a|b] -> a
        s = re.sub(r"\[([^\[\]]*)\]", lambda m: m.group(1).split("|")[0], s)
    s = re.sub(r"\{([^{}]*\|[^{}]*)\}", lambda m: m.group(1).split("|")[0], s)
    out, quote = [], None
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "|;&>)" or (c == "<" and s[i:i + 2] != "<<") or (c == "#" and (i == 0 or s[i - 1].isspace())):
            if c in ">&" and len(out) >= 2 and out[-1].isdigit() and out[-2].isspace():
                out.pop()   # the fd of `2>&1`
            break
        out.append(c)
        i += 1
    return "".join(out).strip()


def words_of(text: str) -> list[str]:
    for tail in ("", '"', "'"):
        try:
            return shlex.split(text + tail)
        except ValueError:
            continue
    return text.split()


def scrub(tokens: list[str], top, groups, vocab, int_opts, names) -> list[str]:
    out: list[str] = []
    for i, tok in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        if tok.startswith("-") and re.fullmatch(r"--?[a-z][a-z0-9-]*(=.*)?", tok, re.S):
            flag, eq, value = tok.partition("=")
            out.append(flag + (eq + ("1" if flag in int_opts else PLACEHOLDER) if eq else ""))
        elif tok == "--":
            out.append(tok)
        elif prev in int_opts:
            out.append("1")
        elif re.fullmatch(r"\d+", tok):
            out.append(tok)
        elif tok in names:
            out.append(PLACEHOLDER)
        elif tok in vocab and not (prev and prev.startswith("--") and prev in GLOBAL_VALUE_OPTS):
            out.append(tok)
        elif prev in groups and out and out[-1] == prev and re.fullmatch(r"[a-z][a-z-]*", tok):
            out.append(tok)   # `index build`: an unknown verb is the point of the line
        else:
            out.append(PLACEHOLDER)
    return out


def is_invocation(words: list[str], top) -> bool:
    i = 0
    seen_global = False
    while i < len(words) and words[i].startswith("-"):
        seen_global = True
        flag = words[i].split("=", 1)[0]
        i += 2 if flag in GLOBAL_VALUE_OPTS and "=" not in words[i] else 1
    if i >= len(words):
        return seen_global
    return words[i] in top


def files_of(repo: Path):
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True).stdout
    for rel in out.decode().split("\0"):
        if rel:
            yield repo / rel


def extract(paths, names: set[str]) -> tuple[list[str], int]:
    top, groups, vocab, int_opts = vocabulary()
    lines: set[str] = set()
    hit_files = 0
    for path in paths:
        if path.suffix not in TEXT_SUFFIXES and path.suffix not in SHELL_SUFFIXES:
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "lean-pkg" not in text:
            continue
        found = False
        for span in code_spans(path, text):
            for m in INVOKE.finditer(span):
                before = span[:m.start()]
                if not COMMAND_POSITION.search(before):
                    continue
                rest = span[m.end():]
                if before.endswith(("\"", "'")):   # "lean-pkg ..." quoted as a string: up to its end
                    rest = rest.split(before[-1], 1)[0]
                words = words_of(cut(rest))
                if not words or not is_invocation(words, top):
                    continue
                lines.add(" ".join(["lean-pkg", *(shlex.quote(w) for w in
                                                 scrub(words, top, groups, vocab, int_opts, names))]))
                found = True
        hit_files += found
    return sorted(lines), hit_files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--git-repo", action="append", default=[], type=Path)
    ap.add_argument("--file", action="append", default=[], type=Path)
    ap.add_argument("--scrub-names-from", type=Path, help="registry YAML whose package names are scrubbed")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    names: set[str] = set()
    if a.scrub_names_from:
        import yaml
        names = set((yaml.safe_load(a.scrub_names_from.read_text()) or {}).get("packages") or {})
    paths = [p for repo in a.git_repo for p in files_of(repo)] + list(a.file)
    lines, hit_files = extract(paths, names)
    header = ["# lean-pkg compatibility corpus: scrubbed invocations quoted by agent skills, runbooks and",
              "# wrapper scripts (scripts/extract-compat-corpus.py). One invocation per line; every word that",
              "# is not a flag, command, choice or number is `demo`. Parsed (never run) by",
              "# tests/test_cli_contract_corpus.py; lines that do not parse are listed with a reason in",
              "# tests/data/compat-corpus-unparseable.yaml.",
              f"# {len(lines)} lines from {hit_files} source files."]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(header + lines) + "\n")
    print(f"{len(lines)} lines from {hit_files} files -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
