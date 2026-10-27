# lean-pkg lean_scan -- a small Lean 4 source scanner for search and claims
"""Lean names and declaration headers, without Lean.

`split_name` / `last_component` split a dotted Lean name, keeping a «guillemet-quoted» component
whole (`Demo.«a.b»` is two components). `declarations(text)` lists the declarations of a Lean file
with their fully qualified names: it skips nested `/- ... -/` block comments, `--` line comments and
string literals, attaches a `/-- ... -/` docstring to the declaration after it (on the same line or
across attribute lines), reads attributes with strings and nested brackets, modifiers
(`private`, `public`, `noncomputable`, `local`, ...), `instance (priority := n)`, `class inductive` and «» names,
and tracks `namespace` / `section` / `mutual` ... `end`. It is a scanner for search, not a parser:
a declaration it cannot read is missed, never invented from a comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORDS = ("theorem", "lemma", "def", "structure", "class", "instance", "abbrev", "inductive",
            "axiom", "opaque", "irreducible_def")
# `public` and `meta` are module-system modifiers (Lean v4.35: `public theorem`, `@[expose] public section`,
# `public meta def`).
MODIFIERS = ("private", "protected", "noncomputable", "partial", "unsafe", "nonrec", "scoped", "local",
             "public", "meta")
_IDENT_PART = r"(?:«[^»\n]*»|[^\s:({\[⦃«».,@)}\]⦄]+)"
_NAME = re.compile(_IDENT_PART + r"(?:\." + _IDENT_PART + r")*")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
_PRIORITY = re.compile(r"\(\s*priority\s*:=")


def split_name(name: str) -> list[str] | None:
    """The components of a dotted Lean name, a «quoted» component kept whole with its guillemets;
    None when the name is malformed (an unclosed «, an empty component, whitespace outside «»)."""
    parts, cur, i, s = [], "", 0, str(name)
    while i < len(s):
        ch = s[i]
        if ch == "«":
            j = s.find("»", i + 1)
            if j < 0:
                return None
            cur += s[i:j + 1]
            i = j + 1
            continue
        if ch == "»" or ch.isspace():
            return None
        if ch == ".":
            if not cur:
                return None
            parts.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    if not cur:
        return None
    parts.append(cur)
    return parts


def last_component(name: str) -> str:
    """The last component of a Lean name (`Demo.«a.b»` -> `«a.b»`); the name itself when malformed."""
    parts = split_name(name)
    return parts[-1] if parts else str(name)


def occurs(component: str, text: str) -> bool:
    """Does a name component occur in the text? `«foo»` also counts as `foo` (the quotes are optional
    for a plain identifier), and `«foo bar»` only with its guillemets."""
    if component in text:
        return True
    if component.startswith("«") and component.endswith("»"):
        inner = component[1:-1]
        return bool(inner) and not any(ch.isspace() or ch in ".«»" for ch in inner) and inner in text
    return False


def is_name(name) -> bool:
    return isinstance(name, str) and split_name(name.strip()) is not None and name == name.strip()


# --------------------------------------------------------------------------
# masking: comments and strings become spaces (newlines kept), docstrings are remembered

def _mask(text: str) -> tuple[str, list[tuple[int, int, str]]]:
    """(text with comments, docstrings and string literals blanked, [(start, end, docstring text)])."""
    out = list(text)
    docs: list[tuple[int, int, str]] = []
    n, i = len(text), 0

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        ch = text[i]
        if ch == "«":                                    # a quoted name is literal text
            j = text.find("»", i + 1)
            i = n if j < 0 else j + 1
            continue
        if text.startswith("/-", i):
            is_doc = text.startswith("/--", i) and not text.startswith("/--/", i)
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/-", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("-/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if is_doc:
                docs.append((i, j, " ".join(text[i:j].split())))
            blank(i, j)
            i = j
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if ch == '"' or (ch == "r" and re.match(r'r#*"', text[i:i + 8]) and
                         (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_'."))):
            if ch == "r":
                hashes = len(re.match(r"r(#*)", text[i:]).group(1))
                close = '"' + "#" * hashes
                j = text.find(close, i + 2 + hashes)
                j = n if j < 0 else j + len(close)
            else:
                j = i + 1
                while j < n and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                j = min(n, j + 1)
            blank(i, j)
            i = j
            continue
        if ch == "'" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_'!?»")):
            m = re.match(r"'(?:\\[^\n]+?|[^\\\n])'", text[i:i + 12])
            if m:
                blank(i, i + m.end())
                i += m.end()
                continue
        i += 1
    return "".join(out), docs


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _balanced(s: str, i: int) -> int:
    """The index after the bracket group opening at s[i] (strings already blanked); len(s) if unclosed."""
    pairs = {"[": "]", "(": ")", "{": "}", "⦃": "⦄"}
    stack = [pairs[s[i]]]
    j = i + 1
    while j < len(s) and stack:
        c = s[j]
        if c in pairs:
            stack.append(pairs[c])
        elif c == stack[-1]:
            stack.pop()
        j += 1
    return j


def _scope_name(s: str, i: int) -> tuple[str | None, int]:
    """An optional name after `namespace` / `section` / `end`, on the same line."""
    j = i
    while j < len(s) and s[j] in " \t":
        j += 1
    m = _NAME.match(s, j)
    if m and m.group(0) not in KEYWORDS:
        return m.group(0), m.end()
    return None, i


def declarations(text: str) -> list[tuple[int, str, str, str | None, str, str]]:
    """(line, keyword, qualified name, docstring or None, stripped source line, name as written) for every
    declaration. `class inductive` reports keyword `class`; an anonymous instance is left out; a
    `_root_.` name is not prefixed with the namespace."""
    masked, docs = _mask(text)
    lines = text.splitlines()
    starts, off = [], 0
    for ln in masked.split("\n"):
        starts.append(off)
        off += len(ln) + 1
    doc_by_end = sorted(docs, key=lambda d: d[1])
    scopes: list[tuple[str, list[str]]] = []          # (kind, name components)
    out = []
    consumed = 0
    line_of = _line_index(starts)
    for st in starts:
        if st < consumed:
            continue
        c = st
        while c < len(masked) and masked[c] in " \t":
            c += 1
        i = c
        while masked.startswith("@[", i):                      # attributes, across lines
            i = _skip_ws(masked, _balanced(masked, i + 1))
        words_seen = []
        while True:
            m = _WORD.match(masked, i)
            if not m:
                break
            w = m.group(0)
            if w in MODIFIERS:
                words_seen.append(w)
                i = _skip_ws(masked, m.end())
                while masked.startswith("@[", i):
                    i = _skip_ws(masked, _balanced(masked, i + 1))
                continue
            break
        m = _WORD.match(masked, i)
        if not m:
            continue
        w, after = m.group(0), m.end()
        if after < len(masked) and not masked[after].isspace():
            continue
        if w == "namespace" and not words_seen:
            name, consumed = _scope_name(masked, after)
            if name:
                scopes.append(("namespace", split_name(name) or [name]))
            continue
        if w == "section":
            _, consumed = _scope_name(masked, after)
            scopes.append(("section", []))
            continue
        if w == "mutual" and not words_seen:
            scopes.append(("mutual", []))
            continue
        if w == "end" and not words_seen:
            _, consumed = _scope_name(masked, after)
            if scopes:
                scopes.pop()
            continue
        if w not in KEYWORDS:
            continue
        kw = w
        j = _skip_ws(masked, after)
        if kw == "class":
            m2 = _WORD.match(masked, j)
            if m2 and m2.group(0) == "inductive":
                j = _skip_ws(masked, m2.end())
        if kw == "instance" and _PRIORITY.match(masked, j):
            j = _skip_ws(masked, _balanced(masked, j))
        m3 = _NAME.match(masked, j)
        if not m3 or m3.group(0) in KEYWORDS or m3.group(0) in ("where", "extends", "with"):
            consumed = j
            continue
        written = m3.group(0)
        consumed = m3.end()
        parts = split_name(written) or [written]
        if parts[0] == "_root_":
            qual = ".".join(parts[1:]) or written
        else:
            ns = [p for kind, comps in scopes if kind == "namespace" for p in comps]
            qual = ".".join(ns + parts)
        line = line_of(m.start())
        doc = None
        for _a, b, d in reversed(doc_by_end):
            if b <= c and not masked[b:c].strip():
                doc = d
                break
            if b <= c:
                break
        src = lines[line - 1].strip() if line - 1 < len(lines) else ""
        out.append((line, kw, qual, doc, src, written))
    return out


def _line_index(starts: list[int]):
    import bisect

    def line_of(pos: int) -> int:
        return bisect.bisect_right(starts, pos)
    return line_of


# --------------------------------------------------------------------------
# declaration spans for the extraction ledger (proposal 0009, method text/1): comments stripped,
# declarations split at top-level commands, names mapped, text normalized. Library only; the
# functions above keep their behavior.

class ScanError(ValueError):
    """A file whose comment, string or bracket structure (or command layout) the span splitter cannot
    read. The file is refused whole, never half split."""


class MapError(ValueError):
    """A namespace or rename map that is malformed or ambiguous (a duplicate, a no-op, a chain or a
    cycle)."""


def _lex(text: str) -> list[tuple[int, int, str]]:
    """[(start, end, kind)] of the non-code regions of a Lean file, kinds `comment` (`--` and `/- -/`,
    nested), `doc` (`/-- -/`), `moddoc` (`/-! -/`), `string` and `char`. The same reading as `_mask`
    (a «quoted» name is literal text, so `«a -- b»` holds no comment), but strict: an unclosed block
    comment, string literal or «» name raises ScanError instead of running to the end of the file."""
    out: list[tuple[int, int, str]] = []
    n, i = len(text), 0
    while i < n:
        ch = text[i]
        if ch == "«":
            j = text.find("»", i + 1)
            if j < 0:
                raise ScanError(f"line {text.count(chr(10), 0, i) + 1}: an unclosed « name")
            i = j + 1
            continue
        if text.startswith("/-", i):
            if text.startswith("/--", i) and not text.startswith("/--/", i):
                kind = "doc"
            elif text.startswith("/-!", i):
                kind = "moddoc"
            else:
                kind = "comment"
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/-", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("-/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if depth:
                raise ScanError(f"line {text.count(chr(10), 0, i) + 1}: an unclosed /- comment")
            out.append((i, j, kind))
            i = j
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append((i, j, "comment"))
            i = j
            continue
        if ch == '"' or (ch == "r" and re.match(r'r#*"', text[i:i + 8]) and
                         (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_'."))):
            if ch == "r":
                hashes = len(re.match(r"r(#*)", text[i:]).group(1))
                close = '"' + "#" * hashes
                j = text.find(close, i + 2 + hashes)
                if j < 0:
                    raise ScanError(f"line {text.count(chr(10), 0, i) + 1}: an unclosed raw string")
                j += len(close)
            else:
                j = i + 1
                while j < n and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                if j >= n:
                    raise ScanError(f"line {text.count(chr(10), 0, i) + 1}: an unclosed string literal")
                j += 1
            out.append((i, j, "string"))
            i = j
            continue
        if ch == "'" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_'!?»")):
            m = re.match(r"'(?:\\[^\n]+?|[^\\\n])'", text[i:i + 12])
            if m:
                out.append((i, i + m.end(), "char"))
                i += m.end()
                continue
        i += 1
    return out


def _blank(text: str, regions: list[tuple[int, int, str]], kinds: tuple[str, ...]) -> str:
    out = list(text)
    for a, b, kind in regions:
        if kind in kinds:
            for k in range(a, b):
                if out[k] != "\n":
                    out[k] = " "
    return "".join(out)


_COMMENT_KINDS = ("comment", "doc", "moddoc")


def strip_comments(text: str) -> str:
    """The text with every comment blanked: `--` line comments, nested `/- -/` block comments, docstrings
    `/-- -/` and module docs `/-! -/`. Each comment character becomes a space and every newline stays,
    so the result has the same length, lines and columns as the input. String and character literals and
    «» names are kept as written, so a `--` or `/-` inside them is not a comment. Raises ScanError for an
    unclosed block comment, string literal or «» name."""
    return _blank(text, _lex(text), _COMMENT_KINDS)


# commands that start a span, beyond the declaration KEYWORDS and `example`
_SCOPE_WORDS = ("namespace", "section", "end", "mutual")
_HEADER_WORDS = ("import", "module", "prelude")
_CONTEXT_WORDS = ("open", "variable", "universe", "set_option", "attribute", "export", "initialize",
                  "builtin_initialize", "notation", "notation3", "infix", "infixl", "infixr", "prefix",
                  "postfix", "macro", "macro_rules", "syntax", "elab", "elab_rules", "declare_syntax_cat",
                  "omit", "include", "alias", "run_cmd", "run_elab", "run_meta", "assert_not_exists",
                  "assert_not_imported", "add_decl_doc", "library_note", "register_simp_attr",
                  "initialize_simps_projections", "suppress_compilation", "unseal", "seal", "simproc",
                  "simproc_decl", "dsimproc", "binder_predicate", "unif_hint", "deriving") + _HEADER_WORDS
# commands that may prefix another with `in` (`open Foo in`, `set_option x v in`)
_IN_WORDS = ("open", "set_option", "variable", "omit", "include", "attribute", "unseal", "#guard_msgs")
_HASH_WORD = re.compile(r"#[A-Za-z_][A-Za-z0-9_']*")
# an unknown command: a lowercase snake_case word at column 0 (`name_poly_vars`, `register_hint`)
_COMMAND_WORD = re.compile(r"[a-z][a-z0-9_]*(?=[ \t\n]|$)")
_OPEN_ENDS = ("by", "fun", "then", "else", "do", "from", "with", "at", "in", "where", "calc", "have", "show",
              "match", "if", "let", "using", "λ")
_CONTINUATIONS = _OPEN_ENDS + ("deriving", "termination_by", "decreasing_by", "extends", "nomatch",
                                "nofun", "partial_fixpoint", "rfl", "sorry", "trivial", "simp", "exact", "intro",
                                "omega", "decide", "assumption", "constructor", "rw", "apply", "refine", "cases",
                                "induction", "rcases", "obtain", "aesop", "grind", "norm_num", "linarith")


def _command_word(masked: str, i: int) -> str | None:
    """The word at masked[i] when it can be a command lean_scan does not know: lowercase snake_case, and
    not a word that continues a declaration (`where`, `deriving`) or a common term or tactic."""
    m = _COMMAND_WORD.match(masked, i)
    return m.group(0) if m and m.group(0) not in _CONTINUATIONS else None


def _complete(line: str) -> bool:
    """Does the previous line look finished, so a column-0 word after it starts a new command (rather than
    continuing a term, as `alias foo :=` followed by a name at column 0 does)?"""
    line = line.rstrip()
    if not line:
        return True
    m = re.search(r"[\w'!?]+$", line)
    if m and m.group(0) in _OPEN_ENDS:
        return False
    return line[-1].isalnum() or line[-1] in ")]}⟩'!?»_"


_IN = re.compile(r"(?<![\w'.!?«])in(?![\w'!?])")
_BRACKETS = {"(": ")", "[": "]", "{": "}", "⦃": "⦄", "⟨": "⟩"}
_CLOSERS = {v: k for k, v in _BRACKETS.items()}


def _name_end(masked: str, i: int) -> int:
    """The index after the «» name opening at masked[i]."""
    j = masked.find("»", i + 1)
    if j < 0:
        raise ScanError(f"line {masked.count(chr(10), 0, i) + 1}: an unclosed « name")
    return j + 1


def _line_depths(masked: str, starts: list[int]) -> list[int]:
    """The bracket depth at the start of each line of the masked text (strings and comments blanked);
    ScanError on a closer that does not match, or a bracket left open at the end."""
    depths: list[int] = []
    stack: list[tuple[str, int]] = []
    li, n, i = 0, len(masked), 0
    while i < n:
        while li < len(starts) and starts[li] <= i:
            depths.append(len(stack))
            li += 1
        c = masked[i]
        if c == "«":
            i = _name_end(masked, i)
            continue
        if c in _BRACKETS:
            stack.append((_BRACKETS[c], i))
        elif c in _CLOSERS:
            if not stack or stack[-1][0] != c:
                raise ScanError(f"line {masked.count(chr(10), 0, i) + 1}: an unmatched {c!r}")
            stack.pop()
        i += 1
    while li < len(starts):
        depths.append(len(stack))
        li += 1
    if stack:
        raise ScanError(f"line {masked.count(chr(10), 0, stack[-1][1]) + 1}: "
                        f"an unclosed {masked[stack[-1][1]]!r}")
    return depths


def _skip_group(masked: str, i: int) -> int:
    """The index after the bracket group opening at masked[i] (structure already checked)."""
    j = _balanced(masked, i)
    if j > len(masked) or (j == len(masked) and masked[j - 1] != _BRACKETS.get(masked[i])):
        raise ScanError(f"line {masked.count(chr(10), 0, i) + 1}: an unclosed {masked[i]!r}")
    return j


def _head(masked: str, c: int):
    """Read the command that starts at masked[c]: None when it is no command (a continuation line), else
    (class, word, word position, word end), class `decl`, `scope`, `context` or `bad` (attributes or
    modifiers before something that is not a declaration)."""
    i, prefixed = c, False
    while masked.startswith("@[", i):
        i = _skip_ws(masked, _skip_group(masked, i + 1))
        prefixed = True
    while True:
        m = _WORD.match(masked, i)
        if not m or m.group(0) not in MODIFIERS:
            break
        i, prefixed = m.end(), True
        i = _skip_ws(masked, i)
        if m.group(0) == "scoped" and masked.startswith("[", i):     # `scoped[NS] notation`
            i = _skip_ws(masked, _skip_group(masked, i))
        while masked.startswith("@[", i):
            i = _skip_ws(masked, _skip_group(masked, i + 1))
    m = _HASH_WORD.match(masked, i) if masked.startswith("#", i) else _WORD.match(masked, i)
    if not m:
        return ("bad", None, i, i) if prefixed else None
    w, after = m.group(0), m.end()
    spaced = after >= len(masked) or masked[after].isspace() or (w == "instance" and masked[after] == "(")
    if w in KEYWORDS or w == "example":
        if spaced:
            return ("decl", w, i, after)
        return ("bad", w, i, after) if prefixed else None
    if w in _SCOPE_WORDS:
        if w != "section" and prefixed:
            return ("bad", w, i, after)
        return ("scope", w, i, after)
    if w == "deriving":
        m2 = _WORD.match(masked, _skip_ws(masked, after))
        if not (m2 and m2.group(0) == "instance"):
            return ("bad", w, i, after) if prefixed else None   # a `deriving` clause stays with its declaration
    if w in _CONTEXT_WORDS or w.startswith("#"):
        if w in _IN_WORDS:
            eol = masked.find("\n", after)
            eol = len(masked) if eol < 0 else eol
            depth, k = 0, after
            while k < eol:
                ch = masked[k]
                if ch == "«":
                    k = _name_end(masked, k)
                    continue
                if ch in _BRACKETS:
                    depth += 1
                elif ch in _CLOSERS:
                    depth -= 1
                elif depth == 0 and ch == "i" and _IN.match(masked, k) and \
                        (k == 0 or not (masked[k - 1].isalnum() or masked[k - 1] in "_'.!?«»")):
                    rest = _skip_ws(masked, k + 2)
                    if rest >= len(masked):
                        raise ScanError(f"line {masked.count(chr(10), 0, k) + 1}: `{w} ... in` with nothing after")
                    inner = _head(masked, rest)
                    if inner is not None and inner[0] == "scope":
                        return ("bad", inner[1], inner[2], inner[3])
                    return inner                            # None: `open X in tactic`, a continuation
                k += 1
        return ("context", w, i, after)
    return ("bad", w, i, after) if prefixed else None


@dataclass(frozen=True)
class Span:
    """One top-level command of a Lean file, as `decl_spans` reads it.

    key: the qualified name of a declaration; `mutual(a, b)` for a mutual block (its qualified names
    sorted); `instance#k@Module` or `example#k@Module` for the k-th anonymous instance or example of
    the module (k from 1); for a context command, its command word (`open`, `variable`, `end`, ...).
    kind: the keyword (`class` for `class inductive`), `mutual`, or the context command word.
    names: the qualified names it declares (empty for an anonymous instance, an example or a context
    command). start, end: its first and last line (1-based, inclusive; comment-only lines after it are
    left out). scope: the namespace components open at its start. text: its source lines as written.
    code: the same lines with comments blanked (`strip_comments`); on the first line, a comment before
    the command (an inline docstring) is dropped and the line's own indentation kept. doc: the docstring
    just before it, whitespace collapsed, or None."""

    key: str
    kind: str
    names: tuple[str, ...]
    start: int
    end: int
    scope: tuple[str, ...]
    text: str
    code: str
    doc: str | None


def decl_spans(text: str, module: str = "") -> tuple[list[Span], list[Span]]:
    """(declarations, context): the top-level commands of a Lean file, split into spans.

    A span starts at each top-level command (bracket depth zero) whose line is indented at most as far
    as the current declaration's keyword line; after a context command, any line may start one. The
    commands are the KEYWORDS plus `example`, `namespace` / `section` / `end` / `mutual`, `open`,
    `variable`, `universe`, `set_option`, `attribute`, `export`, `initialize`, `deriving instance`, the
    notation, macro and syntax commands, `#...` commands, the module header (`module`, `prelude`,
    `import`) and a few Mathlib commands (`alias`, `omit`, `include`, ...). Attributes, modifiers and a
    preceding `open ... in` / `set_option ... in` belong to the command they prefix; an `open X in`
    followed by a tactic (inside a proof, even at column 0) is not a command. `where`, `deriving`,
    `termination_by`, `decreasing_by` and `|` lines stay with their declaration.

    Declarations are keyed as in `Span`, with the namespace tracking of `declarations()` (the two agree
    on every name). A `mutual ... end` block is one span. Every other command is a context span.

    Raises ScanError, never returning a partial split, for: an unclosed comment, string or «» name; an
    unmatched or unclosed bracket; text before the first command; attributes or modifiers before
    something that is not a declaration; a declaration whose name cannot be read; a duplicate key; an
    `end` that does not match the innermost open scope (or with none open); a command inside a `mutual`
    block other than a declaration; a `mutual` block left open; a line at column 0 after a context
    command that is not a command."""
    regions = _lex(text)
    code_all = _blank(text, regions, _COMMENT_KINDS)
    masked = _blank(text, regions, _COMMENT_KINDS + ("string", "char"))
    docs = sorted((a, b, " ".join(text[a:b].split())) for a, b, kind in regions if kind == "doc")
    raw_lines = text.split("\n")
    code_lines = code_all.split("\n")
    m_lines = masked.split("\n")
    starts, off = [], 0
    for ln in m_lines:
        starts.append(off)
        off += len(ln) + 1
    depths = _line_depths(masked, starts)
    line_of = _line_index(starts)
    suffix = f"@{module}" if module else ""

    decls: list[Span] = []
    context: list[Span] = []
    scopes: list[tuple[str, str | None]] = []   # (kind, one name component or None), as Lean keeps them
    ordinals = {"instance": 0, "example": 0}
    seen: set[str] = set()
    cur: dict | None = None
    consumed = 0
    prev_line = ""

    def ns() -> tuple[str, ...]:
        return tuple(comp for kind, comp in scopes if kind == "namespace")

    def doc_before(c: int, pos: int) -> str | None:
        inside = [d for a, b, d in docs if c <= a and b <= pos]
        if inside:                                          # `open X in` / attributes, then the docstring
            return inside[-1]
        for _a, b, d in reversed(docs):
            if b <= c:
                return d if not masked[b:c].strip() else None
        return None

    def close() -> None:
        nonlocal cur
        if cur is None:
            return
        a, b = cur["start"], cur["last"]
        code = code_lines[a:b + 1]
        col = cur["col"]
        if code and not code[0][:col].strip():
            raw = raw_lines[a]
            indent = raw[:len(raw) - len(raw.lstrip())]
            code[0] = indent[:col] + code[0][col:]
        if cur["kind"] == "mutual":
            if cur["open"]:
                raise ScanError(f"line {a + 1}: a mutual block is never closed")
            names = tuple(sorted(cur["names"]))
            key = "mutual(" + ", ".join(names) + ")"
        else:
            names = tuple(cur["names"])
            key = cur["key"]
        span = Span(key, cur["kind"], names, a + 1, b + 1, cur["scope"], "\n".join(raw_lines[a:b + 1]),
                    "\n".join(code), cur["doc"])
        (decls if cur["decl"] else context).append(span)
        cur = None

    def decl_key(kw: str, pos: int, after: int, lineno: int) -> tuple[str, list[str], int]:
        """(key, [qualified name] or [], end of the name), as declarations() reads the name."""
        j = _skip_ws(masked, after)
        if kw == "class":
            m2 = _WORD.match(masked, j)
            if m2 and m2.group(0) in ("inductive", "abbrev"):         # `class abbrev`: Mathlib's
                j = _skip_ws(masked, m2.end())
        if kw == "instance" and _PRIORITY.match(masked, j):
            j = _skip_ws(masked, _skip_group(masked, j))
        m3 = _NAME.match(masked, j) if kw != "example" else None
        if not m3 or m3.group(0) in KEYWORDS or m3.group(0) in ("where", "extends", "with"):
            if kw not in ("instance", "example"):
                raise ScanError(f"line {lineno}: the name of this {kw} cannot be read")
            ordinals[kw] += 1
            return f"{kw}#{ordinals[kw]}{suffix}", [], j
        written = m3.group(0)
        parts = split_name(written)
        if parts is None:
            raise ScanError(f"line {lineno}: the name {written!r} is malformed")
        qual = ".".join(parts[1:]) if parts[0] == "_root_" and len(parts) > 1 else ".".join(ns() + tuple(parts))
        return qual, [qual], m3.end()

    def claim(key: str, lineno: int) -> None:
        if key in seen:
            raise ScanError(f"line {lineno}: {key} is declared twice")
        seen.add(key)

    for k, mline in enumerate(m_lines):
        st = starts[k]
        if not mline.strip():
            continue
        if st < consumed or depths[k] > 0:
            if cur is not None:
                cur["last"] = k
            prev_line = mline
            continue
        raw = raw_lines[k]
        indent = len(raw) - len(raw.lstrip())
        c = st + len(mline) - len(mline.lstrip())
        thr = cur["thr"] if cur is not None else None
        head = _head(masked, c)
        if thr is not None and indent > thr and not (head and head[0] in ("decl", "scope")):
            cur["last"], prev_line = k, mline               # deeper: part of the declaration, unless it
            continue                                        # opens with a declaration or scope keyword
        in_mutual = cur is not None and cur["kind"] == "mutual" and cur["open"]
        if head is not None and head[0] == "bad" and cur is not None and indent > 0:
            head = None                                     # `unsafe Foo.init` inside a term
        if head is not None and head[0] == "bad":
            if in_mutual or not _command_word(masked, head[2]):
                raise ScanError(f"line {k + 1}: attributes or modifiers before something that is not a "
                                f"declaration")
            head = ("context", _command_word(masked, head[2]), head[2], head[2])
        elif head is None:
            m_cmd = _command_word(masked, c)
            if cur is None:
                if not m_cmd:
                    raise ScanError(f"line {k + 1}: text outside any command")
            elif in_mutual or not (m_cmd and indent == 0 and _complete(prev_line)):
                cur["last"], prev_line = k, mline
                continue
            head = ("context", m_cmd, c, c)               # a command lean_scan does not know
        cls, w, pos, after = head
        lineno = line_of(pos)
        kw_raw = raw_lines[lineno - 1]
        kw_indent = len(kw_raw) - len(kw_raw.lstrip())
        prev_line = mline
        if in_mutual:
            if cls == "context":
                cur["last"] = k                                 # `variable` inside `mutual` stays in it
                consumed = after
                continue
            if cls == "decl":
                key, names, consumed = decl_key(w, pos, after, lineno)
                claim(key, lineno)
                cur["names"].extend(names or [key])
                cur["thr"], cur["last"] = kw_indent, k
                continue
            if cls == "scope" and w == "end":
                name, consumed = _scope_name(masked, after)
                if name is not None:
                    raise ScanError(f"line {lineno}: `end {name}` closes a mutual block")
                if not cur["names"]:
                    raise ScanError(f"line {lineno}: an empty mutual block")
                scopes.pop()
                cur["open"], cur["last"] = False, k
                close()
                continue
            raise ScanError(f"line {lineno}: a {w} command inside a mutual block")
        close()
        cur = {"start": k, "last": k, "col": c - st, "scope": ns(), "doc": doc_before(c, pos), "names": [],
               "kind": w, "key": w, "decl": False, "thr": None, "open": False}
        if cls == "decl":
            key, names, consumed = decl_key(w, pos, after, lineno)
            claim(key, lineno)
            cur.update(key=key, names=names, decl=True, thr=kw_indent)
        elif cls == "scope":
            consumed = after
            if w == "mutual":
                scopes.append(("mutual", None))
                cur.update(decl=True, open=True, doc=None)
            elif w == "namespace":
                name, consumed = _scope_name(masked, after)
                if name is None or split_name(name) is None:
                    raise ScanError(f"line {lineno}: a namespace with no readable name")
                scopes.extend(("namespace", comp) for comp in split_name(name))
            elif w == "section":
                name, consumed = _scope_name(masked, after)
                if name is not None and split_name(name) is None:
                    raise ScanError(f"line {lineno}: a section with no readable name")
                scopes.extend(("section", comp) for comp in (split_name(name) if name else [None]))
            else:                                               # end: closes one scope per component
                name, consumed = _scope_name(masked, after)
                if name is not None and split_name(name) is None:
                    raise ScanError(f"line {lineno}: `end {name}` is not a name")
                comps = list(_canon(split_name(name))) if name is not None else [None]
                top = scopes[len(scopes) - len(comps):] if len(comps) <= len(scopes) else None
                if not top or [comp if comp is None else _canon([comp])[0] for _k, comp in top] != comps or \
                        any(k_ == "mutual" for k_, _c in top):
                    opened = ".".join(comp for _k, comp in scopes if comp) or "nothing"
                    raise ScanError(f"line {lineno}: `end{' ' + name if name else ''}` does not close the "
                                    f"innermost open scope (open: {opened})")
                del scopes[len(scopes) - len(comps):]
        else:
            consumed = after
    if cur is not None and cur["kind"] == "mutual" and cur["open"]:
        raise ScanError(f"line {cur['start'] + 1}: a mutual block is never closed")
    close()
    return decls, context


def normalize(text: str) -> str:
    """The text for hashing: trailing whitespace stripped from each line, blank lines dropped, and the
    indentation common to all lines removed, relative indentation kept (Lean layout is semantic). Pure
    and idempotent; no trailing newline."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return ""
    lead = [ln[:len(ln) - len(ln.lstrip())] for ln in lines]
    common = lead[0]
    for p in lead[1:]:
        while not p.startswith(common):
            common = common[:-1]
    return "\n".join(ln[len(common):] for ln in lines)


def collapse_whitespace(text: str) -> str:
    """The text with every whitespace run collapsed to one space and the ends stripped: the second hash
    of text/1, behind `whitespace_only` (a hint, never a pass)."""
    return " ".join(text.split())


# identifiers (port_modules._TOKEN): dotted names, a «quoted» component whole
_TOKEN = re.compile(r"(?:«[^»\n]*»|[^\W\d][\w'!?]*)(?:\.(?:«[^»\n]*»|[^\W\d][\w'!?]*))*")
_PLAIN = re.compile(r"[^\W\d][\w'!?]*\Z")
_NS_POSITION = ("namespace", "section", "end", "open", "export")


def _canon(parts) -> tuple[str, ...]:
    """Name components compared as Lean does: `«foo»` is `foo` when the quotes are optional."""
    return tuple(p[1:-1] if p.startswith("«") and p.endswith("»") and _PLAIN.match(p[1:-1]) else p
                 for p in parts)


def _scope_parts(scope) -> tuple[str, ...]:
    if isinstance(scope, str):
        if not scope:
            return ()
        parts = split_name(scope)
        if parts is None:
            raise MapError(f"the scope {scope!r} is not a Lean name")
        return _canon(parts)
    return _canon(tuple(scope))


def _map_pairs(pairs, what: str) -> list[tuple[tuple[str, ...], list[str], str, str]]:
    items = list(pairs.items()) if hasattr(pairs, "items") else list(pairs)
    out = []
    for item in items:
        if not (isinstance(item, (tuple, list)) and len(item) == 2):
            raise MapError(f"{what}: {item!r} is not a (from, to) pair")
        old, new = item
        for name in (old, new):
            if not is_name(name) or (split_name(name) or [""])[0] == "_root_":
                raise MapError(f"{what}: {name!r} is not a Lean name")
        out.append((_canon(split_name(old)), split_name(new), old, new))
    return out


class _NameMap:
    def __init__(self, namespaces, renames):
        self.ns = _map_pairs(namespaces, "namespace map")
        self.renames = _map_pairs(renames, "rename map")
        for what, pairs in (("namespace map", self.ns), ("rename map", self.renames)):
            seen: dict[tuple[str, ...], str] = {}
            for key, new, old, _new_w in pairs:
                if key in seen:
                    raise MapError(f"{what}: {old} is mapped twice")
                if key == _canon(new):
                    raise MapError(f"{what}: {old} is mapped to itself")
                seen[key] = old
        tos: dict[tuple[str, ...], str] = {}
        froms = {key for key, *_ in self.renames}
        for _key, new, old, new_w in self.renames:
            cn = _canon(new)
            if cn in tos:
                raise MapError(f"rename map: {tos[cn]} and {old} are both renamed to {new_w}")
            tos[cn] = old
            if cn in froms:
                raise MapError(f"rename map: {old} -> {new_w} chains into another rename (ambiguous)")
        self.rename = {key: new for key, new, _o, _n in self.renames}
        self.ns_sorted = sorted(((key, new) for key, new, _o, _n in self.ns), key=lambda kv: -len(kv[0]))

    def ns_map(self, parts: list[str], inclusive: bool) -> list[str] | None:
        """The namespace map applied to a name as written: the longest old prefix (the whole name too
        when inclusive) replaced; None when no prefix matches."""
        cp = _canon(parts)
        for key, new in self.ns_sorted:
            if cp[:len(key)] == key and (len(cp) > len(key) or inclusive):
                return list(new) + list(parts[len(key):])
        return None

    def full(self, parts: list[str]) -> list[str]:
        """The new name of an old fully qualified name: its rename, else the namespace map, else itself."""
        new = self.rename.get(_canon(parts))
        if new is not None:
            return list(new)
        return self.ns_map(parts, False) or list(parts)


def _relative(parts: list[str], scope: tuple[str, ...]) -> list[str]:
    """A fully qualified name written relative to a scope: the longest leading run of scope components
    it shares is dropped, keeping at least its last component."""
    cp, j = _canon(parts), 0
    while j < len(scope) and j < len(cp) - 1 and cp[j] == scope[j]:
        j += 1
    return list(parts[j:])


def apply_map(text: str, namespaces=(), renames=(), *, src_scope=(), tgt_scope=None, declared=()) -> str:
    """The text with the namespace and rename maps applied to its identifiers, in one simultaneous pass.

    `namespaces` and `renames` are mappings or (old, new) pairs of Lean names; rename keys are old fully
    qualified names, and a rename takes precedence over the namespace map for its name. `src_scope` is the
    namespace open where the text stands in the source (a dotted name or components, `Span.scope`);
    `tgt_scope` the target's, by default the namespace map applied to `src_scope`. `declared` optionally
    lists the old fully qualified names that exist, so a relative name resolves as Lean resolves it.

    Only whole identifiers outside strings, character literals and comments are rewritten, never part of
    a longer identifier, and never a name after `.` (a field or `.ctor` notation). For each identifier t:
    - after `namespace`, `section`, `end`, `open` or `export` (and the further names of an `open`), the
      namespace map applies to t as written, t itself included;
    - else, for each namespace S' enclosing the text, innermost first, if S'.t is a renamed old name (or
      is in `declared`), t becomes that name's new name written relative to the target scope;
    - else, t written fully qualified (or `_root_.t`): a renamed old name becomes its new name, and a name
      that starts with an old namespace and `.` gets the namespace map;
    - else t is kept.
    A dotted identifier is matched whole: `foo.mp` is not rewritten by a rename of `foo`.

    Raises MapError for a malformed map, a name mapped twice or to itself, two names renamed to one, or a
    rename chain or cycle (`A=B`, `B=C`), and ScanError for an unclosed comment, string or «» name."""
    nm = _NameMap(namespaces, renames)
    src = _scope_parts(src_scope)
    tgt = _scope_parts(tgt_scope) if tgt_scope is not None else _canon(nm.ns_map(list(src), True) or src)
    known = {_canon(split_name(d)) for d in declared if is_name(d)}
    masked = _blank(text, _lex(text), _COMMENT_KINDS + ("string", "char"))
    out: list[str] = []
    last, ns_run, prev_end = 0, False, -1
    for m in _TOKEN.finditer(masked):
        a, b, tok = m.start(), m.end(), m.group(0)
        gap = masked[prev_end:a] if prev_end >= 0 else "\n"
        in_run = ns_run and gap.strip() == "" and "\n" not in gap
        prev_end = b
        prev = masked[a - 1] if a else ""
        if prev and (prev.isalnum() or prev in "_'!?.»«"):
            ns_run = False                                  # inside a longer identifier, or after `.`
            continue
        parts = split_name(tok)
        if parts is None or tok in ("in", "hiding", "renaming"):
            ns_run = False
            continue
        if in_run:
            ns_run = True                                   # `open A B`, `open scoped A`
            new = None if tok == "scoped" else nm.ns_map(parts, True)
        elif tok in _NS_POSITION:
            ns_run = True
            continue
        else:
            ns_run = False
            new = _resolve(nm, parts, src, tgt, known)
        if new is not None:
            rendered = ".".join(new)
            if rendered != tok:
                out.append(text[last:a])
                out.append(rendered)
                last = b
    out.append(text[last:])
    return "".join(out)


def _resolve(nm: _NameMap, parts: list[str], src: tuple[str, ...], tgt: tuple[str, ...], known) -> list[str] | None:
    """The new spelling of one identifier (see apply_map), or None to keep it."""
    cp = _canon(parts)
    if cp[0] == "_root_":
        if len(parts) < 2:
            return None
        rest = parts[1:]
        new = nm.rename.get(_canon(rest))
        if new is None:
            new = nm.ns_map(rest, False)
        return None if new is None else ["_root_"] + list(new)
    for i in range(len(src), 0, -1):
        cand = list(src[:i]) + list(parts)
        cc = _canon(cand)
        if cc in nm.rename or cc in known:
            return _relative(nm.full(cand), tgt)
    new = nm.rename.get(cp)
    if new is not None:
        return list(new)
    return nm.ns_map(parts, False)


# --------------------------------------------------------------------------
# the statement of one theorem, as written (the verification page shows it beside the paper's)

STATEMENT_MAX_CHARS = 3000
STATEMENT_MAX_LINES = 40
_TOP_LINE = re.compile(r"\n(?:\||where\b|@\[|#[a-z]|(?:" + "|".join(KEYWORDS + MODIFIERS) +
                       r"|end|namespace|section|open|variable|universe)\b)")


# Statement-level words the proof boundary depends on. Each `let` / `letI` / `have` / `haveI` (and `let_fun`)
# opener at bracket depth zero owns exactly one later `:=`, its value; only a `:=` no opener is waiting for ends
# the statement. `match` and `fun` (or `λ`) allow `| pattern =>` alternatives on their own lines. A word that can hide a
# statement-level `:=` the scanner does not track (a tactic block, `calc` steps, `do` reassignments) makes the
# statement unreadable: None, never a prefix.
_VALUE_OPENERS = ("let", "letI", "have", "haveI", "let_fun")
_ALT_OPENERS = ("match", "fun")
_UNSURE_WORDS = ("by", "calc", "do")
_KEYWORD_AT = re.compile(r"(?<![\w'.!?«@])(" + "|".join(_VALUE_OPENERS + _ALT_OPENERS + _UNSURE_WORDS) +
                         r")(?![\w'!?])")
_EQUATION_ARM = re.compile(r"[ \t]*\|")


def _arm_line(masked: str, i: int) -> bool:
    """Is the line starting after the newline at masked[i] a `| pattern => ...` alternative: it opens with
    `|` and holds `=>` outside brackets (an absolute value line such as `|x| ≤ 1` has no `=>`)?"""
    if not _EQUATION_ARM.match(masked, i + 1):
        return False
    eol = masked.find("\n", i + 1)
    line = masked[i + 1:len(masked) if eol < 0 else eol]
    depth = 0
    for k, c in enumerate(line):
        if c in "([{⦃⟨":
            depth += 1
        elif c in ")]}⦄⟩":
            depth -= 1
        elif depth == 0 and line.startswith("=>", k):
            return True
    return False


def statement(text: str, qual: str, decls: list | None = None) -> str | None:
    """The source text of the theorem or lemma whose fully qualified name is `qual`, from its keyword up
    to (not including) the `:=` that starts its proof; None when the file has no such declaration, has it
    more than once, its statement has no such `:=` (an equation-compiler or `where` form), or the statement
    is longer than STATEMENT_MAX_LINES lines / STATEMENT_MAX_CHARS characters.

    The proof's `:=` is the first one outside every bracket that no `let`, `letI`, `have`, `haveI` or
    `let_fun` of the statement is waiting for: each such opener outside brackets owns exactly one later
    bracket-free `:=` (its value), so `theorem t :\n  let n : Nat := 1\n  n = 1 := by rfl` keeps its let
    value and its conclusion. Binder defaults (`{k : Nat := 3}`) and named arguments sit inside brackets.
    Where the scanner cannot be sure the statement is whole it returns None, never a prefix: a `by`, `calc`
    or `do` outside brackets before the proof's `:=`, a `| pattern =>` alternative line with no `match` or
    `fun` before it (an equation-compiler form), or a new top-level line.

    Comments inside the statement (`/- ... -/`, nested as in Lean, and `--` to the end of the line) are kept
    as written: the brackets and the `:=` are found in the masked text, where comments and string literals
    are blanks, so a `:=`, a bracket or a `-/` inside a comment or a string never ends or unbalances the
    statement; a «quoted» name is skipped whole. A scanner, not a parser: when in doubt it returns None and
    the page links the file instead of showing a guess. `decls` is `declarations(text)` when the caller has
    it."""
    hits = [d for d in (declarations(text) if decls is None else decls)
            if d[1] in ("theorem", "lemma") and d[2] == qual]
    if len(hits) != 1:
        return None
    masked, _docs = _mask(text)
    starts, off = [], 0
    for ln in masked.split("\n"):
        starts.append(off)
        off += len(ln) + 1
    line = hits[0][0]
    m = re.compile(r"\b" + hits[0][1] + r"\b").search(masked, starts[line - 1])
    if not m:
        return None
    pairs = {"[": "]", "(": ")", "{": "}", "⦃": "⦄", "⟨": "⟩"}
    stack: list[str] = []
    pending = 0                                          # openers still waiting for their `:=`
    alternatives = False                                 # a `match` / `fun` seen: `| p =>` lines belong to it
    i, end = m.end(), None
    limit = min(len(masked), m.start() + STATEMENT_MAX_CHARS)
    while i < limit:
        c = masked[i]
        if c == "«":                                     # a quoted name is one token
            j = masked.find("»", i + 1)
            if j < 0:
                return None
            i = j + 1
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
        elif not stack and masked.startswith(":=", i):
            if pending:
                pending -= 1
                i += 2
                continue
            end = i
            break
        elif not stack and c == "\n":
            if _TOP_LINE.match(masked, i):
                return None                              # a new top-level line before any `:=`
            if not alternatives and _arm_line(masked, i):
                return None                              # an equation-compiler alternative: no statement `:=`
        elif not stack and c == "λ":
            alternatives = True
        elif not stack:
            w = _KEYWORD_AT.match(masked, i)
            if w and (i == 0 or not (masked[i - 1].isalnum() or masked[i - 1] in "_'.!?«@")):
                word = w.group(1)
                if word in _UNSURE_WORDS:
                    return None
                if word in _VALUE_OPENERS:
                    pending += 1
                else:
                    alternatives = True
                i = w.end()
                continue
        i += 1
    if end is None:
        return None
    out = text[m.start():end].rstrip()
    lines = out.split("\n")
    if len(lines) > STATEMENT_MAX_LINES:
        return None
    first = text[starts[line - 1]:m.start()]                # the keyword line's indentation: strip it
    base = len(first) - len(first.lstrip())
    return "\n".join([lines[0]] + [x[base:] if x[:base].strip() == "" else x.lstrip() for x in lines[1:]])


# --------------------------------------------------------------------------
# a statement declared in a module the Challenge imports (an umbrella Challenge.lean holds only imports)

IMPORT_MAX_DEPTH = 32          # import edges followed from the root module
IMPORT_MAX_FILES = 1000        # package files read in one closure
_HEADER_MODIFIERS = ("public", "private", "meta")


def header_imports(text: str) -> list[str]:
    """The modules a Lean 4 file imports, as written («» kept): its header is an optional `module` and
    `prelude`, then `[public|private] [meta] import [all] Name` commands, comments anywhere; the first
    other token ends it."""
    masked, _docs = _mask(text)
    out: list[str] = []
    i, after_import, started = _skip_ws(masked, 0), False, False
    while i < len(masked):
        m = _NAME.match(masked, i)
        if not m:
            break
        tok = m.group(0)
        i = _skip_ws(masked, m.end())
        if after_import:
            if tok == "all":
                continue
            out.append(tok)
            after_import = False
        elif tok in ("module", "prelude") and not started:
            continue
        elif tok in _HEADER_MODIFIERS or tok == "import":
            started, after_import = True, tok == "import"
        else:
            break
    return out


def module_file(module: str) -> str | None:
    """Lake's source path of a module of the package, `A.B.C` -> `A/B/C.lean` (a «quoted» component is one
    path component); None when the name is malformed or a component could leave the package directory
    (empty, `.`, `..`, a path separator, a NUL)."""
    parts = split_name(module)
    if not parts:
        return None
    comps = []
    for p in parts:
        c = p[1:-1] if p.startswith("«") and p.endswith("»") else p
        if not c or c in (".", "..") or any(ch in c for ch in "/\\\0") or c != c.strip():
            return None
        comps.append(c)
    return "/".join(comps) + ".lean"


def import_closure(read, root: str) -> tuple[list[tuple[str, str]], bool]:
    """([(path, text)] of the package's own files in the import closure of module `root`, the root first),
    complete. `read(path)` returns the text of a file committed in the package, or None (not committed, or
    outside the package, such as Mathlib's modules: their imports are not followed). Breadth first, each
    file once (cycle-safe); complete is False when a module name cannot be a package path, or the walk
    stopped at IMPORT_MAX_DEPTH or IMPORT_MAX_FILES with imports left to read."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    complete = True
    frontier = [root]
    depth = 0
    while frontier:
        nxt: list[str] = []
        for module in frontier:
            rel = module_file(module)
            if rel is None:
                complete = False
                continue
            if rel in seen:
                continue
            seen.add(rel)
            if len(out) >= IMPORT_MAX_FILES:
                return out, False
            text = read(rel)
            if text is None:
                continue
            out.append((rel, text))
            nxt += header_imports(text)
        if nxt and depth >= IMPORT_MAX_DEPTH:
            return out, False
        frontier, depth = nxt, depth + 1
    return out, complete


class DeclarationIndex:
    """The declarations of a module's import closure within the package, read once; `statement(qual)`
    finds the one file that declares `qual` and copies its statement from there."""

    def __init__(self, read, root: str):
        self.files, self.complete = import_closure(read, root)
        self._decls: dict[str, list] = {}
        self.by_name: dict[str, list[tuple[str, int, str]]] = {}
        for rel, text in self.files:
            ds = declarations(text)
            self._decls[rel] = ds
            for d in ds:
                self.by_name.setdefault(d[2], []).append((rel, d[0], d[1]))
        self._text = dict(self.files)

    def statement(self, qual: str) -> dict:
        """{file, line, text, reason}: the declaring file and the keyword's line, with the copied statement;
        text None and a reason when the closure is incomplete, the name is declared in no file or in more
        than one, it is not a theorem or lemma, or `statement` refuses it. When the name is not declared
        anywhere, file is the root module's file and line None."""
        root = self.files[0][0] if self.files else None
        hits = self.by_name.get(qual) or []
        if not self.complete:
            return {"file": root, "line": None, "text": None, "reason": "the import closure was not read in full"}
        if not hits:
            return {"file": root, "line": None, "text": None, "reason": "declared in no file of the closure"}
        if len(hits) > 1:
            return {"file": root, "line": None, "text": None, "reason": "declared more than once"}
        rel, line, kw = hits[0]
        text = statement(self._text[rel], qual, self._decls[rel]) if kw in ("theorem", "lemma") else None
        return {"file": rel, "line": line, "text": text,
                "reason": None if text is not None else "the statement could not be copied"}
