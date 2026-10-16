# lean-pkg lean_scan -- a small Lean 4 source scanner for search and claims
"""Lean names and declaration headers, without Lean.

`split_name` / `last_component` split a dotted Lean name, keeping a «guillemet-quoted» component
whole (`Demo.«a.b»` is two components). `declarations(text)` lists the declarations of a Lean file
with their fully qualified names: it skips nested `/- ... -/` block comments, `--` line comments and
string literals, attaches a `/-- ... -/` docstring to the declaration after it (on the same line or
across attribute lines), reads attributes with strings and nested brackets, modifiers
(`private`, `noncomputable`, `local`, ...), `instance (priority := n)`, `class inductive` and «» names,
and tracks `namespace` / `section` / `mutual` ... `end`. It is a scanner for search, not a parser:
a declaration it cannot read is missed, never invented from a comment.
"""

from __future__ import annotations

import re

KEYWORDS = ("theorem", "lemma", "def", "structure", "class", "instance", "abbrev", "inductive",
            "axiom", "opaque", "irreducible_def")
MODIFIERS = ("private", "protected", "noncomputable", "partial", "unsafe", "nonrec", "scoped", "local")
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
        for a, b, d in reversed(doc_by_end):
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
