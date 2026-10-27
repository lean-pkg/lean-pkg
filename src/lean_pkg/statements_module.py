# lean-pkg statements module -- the one trusted module of `Prop` definitions a Challenge may import
"""The statements module of a package with conditional claims (proposal 0003, #320, #369).

A conditional claim names its hypotheses (`hypotheses: [Hyp.A]` in claims.yaml), and the discharge rule
compares Challenge types without unfolding, so the Challenge must name `Hyp.A` itself. That needs one module
of `Prop` definitions that the Challenge and the Solution both import. The maintainer's decision on #320:
a Challenge may import one declared, trusted statements module; the page links it; `palomar check` warns.

The declaration is the optional top-level key of claims.yaml (claims/1, additive)::

    schema: claims/1
    package: demo
    statements_module: Demo.Statements     # exactly one Lean module name
    claims: [...]

claims.yaml holds it because it is the file that names the hypotheses, it is read at the commit every
check binds, and it is lean-pkg's own file (a comparator configuration takes only Palomar's six keys).

Two checks:

- `key_errors(value)` (the schema, in `claims.validate_claims`): one module name, not a list, not a path,
  not a module of Mathlib or Lean core, and one that Lake can map to a file in the package.
- `problems(read, module, commit)` (at a commit: `claims sync`, `claims check NAME`, the gate's claims
  check): the module's file is committed in the package at that commit, imports only Mathlib (and the
  modules Mathlib brings in), and holds only `Prop` definitions: each declaration a `def`, `abbrev`,
  `inductive` or `structure` whose written type ends in `Prop`, with no theorem, lemma, axiom, opaque, instance, example,
  `sorry` or other command beyond namespace, section, open, universe, variable and set_option. This is a
  text check by lean-pkg's scanner (`lean_scan.decl_spans`), not Lean's elaborator: it reads what is
  written, so a reader must still read the module, which is part of the trusted statement.
"""

from __future__ import annotations

import re

from . import core
from . import lean_scan

KEY = "statements_module"
# Module roots a Challenge may import without a declaration: Mathlib, and what Mathlib brings in (the same
# list comparator_types.VOCAB_ROOTS uses to tell Mathlib vocabulary from project definitions).
VOCAB_ROOTS = ("Init", "Std", "Lean", "Mathlib", "Batteries", "Aesop", "Qq", "ProofWidgets", "Plausible",
               "ImportGraph", "LeanSearchClient")
DEF_KINDS = ("def", "abbrev", "inductive", "structure")
CONTEXT_KINDS = ("module", "prelude", "import", "namespace", "section", "end", "open", "universe", "variable",
                 "set_option")
TEXT_CHECK = "a text check by lean-pkg's scanner, not Lean's elaborator"
_SORRY = re.compile(r"(?<![\w'.!?«])sorry(?![\w'!?])")
_WHERE = re.compile(r"where(?![\w'!?])")
_INSTANCE_ATTR = re.compile(r"@\[[^\]]*\binstance\b")
_OPEN = {"(": ")", "[": "]", "{": "}", "⦃": "⦄", "⟨": "⟩"}
_CLOSE = set(_OPEN.values())


def root(module: str) -> str:
    parts = lean_scan.split_name(module) or [module]
    p = parts[0]
    return p[1:-1] if p.startswith("«") and p.endswith("»") else p


def is_vocabulary(module: str) -> bool:
    """A module of Mathlib, Lean core or a library Mathlib brings in: never a module of the package."""
    return root(module) in VOCAB_ROOTS


def key_errors(v) -> list[str]:
    """The schema errors of a `statements_module` value ([] when it is one well-formed module name)."""
    where = KEY
    if isinstance(v, list):
        return [f"{where} names exactly one module, got a list of {len(v)}: a package has one statements "
                f"module (put every hypothesis in it)"]
    if not isinstance(v, str) or not v.strip():
        return [f"{where} must be one Lean module name such as Demo.Statements, got {v!r} (omit the key when "
                f"the package has no statements module)"]
    if v.endswith(".lean") or "/" in v or "\\" in v:
        return [f"{where} must be a Lean module name (Demo.Statements), not a path, got {v!r}"]
    if v == "TODO" or not lean_scan.is_name(v):
        return [f"{where} must be a Lean module name such as Demo.Statements, got {v!r}"]
    if lean_scan.module_file(v) is None:
        return [f"{where} {v!r} does not name a file inside the package (an empty, `.` or `..` component)"]
    if is_vocabulary(v):
        return [f"{where} {v!r} is a module of {root(v)}, not of the package: a Challenge imports Mathlib "
                f"without a declaration; the statements module is the package's own"]
    return []


def declared(doc) -> str | None:
    """The declared module of a loaded claims document when the key is well formed, else None."""
    v = doc.get(KEY) if isinstance(doc, dict) else None
    return v if isinstance(v, str) and not key_errors(v) else None


def _depth0(code: str, stop) -> int:
    """The index of the first position at bracket depth 0 where `stop(code, i)` holds, else -1."""
    depth = 0
    i = 0
    while i < len(code):
        ch = code[i]
        if ch == "«":
            j = code.find("»", i + 1)
            i = len(code) if j < 0 else j + 1
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and stop(code, i):
            return i
        i += 1
    return -1


def written_type(code: str) -> str | None:
    """The type a declaration's header writes after its last top-level `:` (comments blanked), or None."""
    def ends(c: str, i: int) -> bool:
        if c.startswith(":=", i) or c[i] == "|":          # a value, a constructor or an equation arm
            return True
        return _WHERE.match(c, i) is not None and (i == 0 or not (c[i - 1].isalnum() or c[i - 1] in "_'.!?»"))
    end = _depth0(code, ends)
    head = code if end < 0 else code[:end]
    colons = []
    depth = 0
    for i, ch in enumerate(head):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and ch == ":" and not head.startswith(":=", i):
            colons.append(i)
    return " ".join(head[colons[-1] + 1:].split()) if colons else None


def is_prop_type(t: str | None) -> bool:
    """`Prop`, or a function type whose last top-level arrow target is `Prop` (`ℕ → Prop`)."""
    if not t:
        return False
    last = t
    for arrow in ("→", "->"):
        k = _rfind_depth0(last, arrow)
        if k >= 0:
            last = last[k + len(arrow):]
    return last.strip() == "Prop"


def _rfind_depth0(s: str, sub: str) -> int:
    found, depth = -1, 0
    for i, ch in enumerate(s):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif depth == 0 and s.startswith(sub, i):
            found = i
    return found


def content_problems(text: str, module: str, rel: str) -> list[str]:
    """What the scanner finds in the module's text beyond `Prop` definitions; [] when it finds nothing."""
    try:
        decls, context = lean_scan.decl_spans(text, module)
    except lean_scan.ScanError as e:
        return [f"{rel}: lean-pkg's text scanner cannot read it ({e}); a statements module must be one it can "
                f"read"]
    out: list[tuple[int, str]] = []
    for m in lean_scan.header_imports(text):
        if not is_vocabulary(m):
            out.append((0, f"{rel} imports {m}: a statements module imports Mathlib (and Lean core) only"))
    for s in context:
        if s.kind not in CONTEXT_KINDS:
            out.append((s.start, f"{rel} line {s.start}: `{s.kind}` command: a statements module holds only Prop "
                                 f"definitions (and namespace, section, open, universe, variable, set_option)"))
    for s in decls:
        what = f"{s.kind} {', '.join(s.names)}" if s.names else f"an anonymous {s.kind}"
        if s.kind not in DEF_KINDS:
            out.append((s.start, f"{rel} line {s.start}: {what}: a statements module holds only Prop definitions "
                                 f"(no theorem, lemma, axiom, opaque, instance or example)"))
            continue
        if _INSTANCE_ATTR.search(s.code):
            out.append((s.start, f"{rel} line {s.start}: {what} has an instance attribute: a statements module "
                                 f"holds no instance"))
        if _SORRY.search(s.code):
            out.append((s.start, f"{rel} line {s.start}: {what} uses sorry: a hypothesis is a definition, never "
                                 f"a sorry"))
        t = written_type(s.code)
        if not is_prop_type(t):
            out.append((s.start, f"{rel} line {s.start}: {what} is not a Prop definition (its written type is "
                                 f"{('`' + t + '`') if t else 'missing'}, not `Prop` or `... → Prop`)"))
    return [msg for _line, msg in sorted(out, key=lambda x: x[0])]


def problems(read, module: str, commit: str) -> list[str]:
    """The refusals of a declared, well-formed module at `commit`: `read(rel)` returns the text of a file
    committed in the package there, or None."""
    rel = lean_scan.module_file(module) or module
    text = read(rel)
    if text is None:
        return [f"{KEY} {module}: {rel} is not committed in the package at {commit[:12]} (untracked files do "
                f"not count)"]
    found = content_problems(text, module, rel)
    return [f"{KEY} {module}: {p} ({TEXT_CHECK})" for p in found]


def at_commit(repo, claims_rel: str, commit: str) -> tuple[str | None, list[str]]:
    """(declared module or None, its problems) from the claims file committed at `commit`; (None, []) when
    the file or the key is absent, or the file cannot be read (the schema check reports that)."""
    from . import claims as claims_mod
    raw = core.git_show_bytes(repo, claims_rel, commit)
    if raw is None:
        return None, []
    try:
        doc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), claims_rel)
    except core.Fail:
        return None, []
    mod = declared(doc)
    if mod is None:
        return None, []
    return mod, problems(claims_mod._reader(repo, commit), mod, commit)
