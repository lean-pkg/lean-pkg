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
