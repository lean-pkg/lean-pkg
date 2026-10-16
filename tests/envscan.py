"""Find the environment variables a source tree reads (helper for test_help_topics; no registry, no I/O
beyond reading the files it is given)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Helpers that read os.environ by the name they are given (cli._env_on/_env_int/_env_float, the release
# scripts' env_int): a call with a literal or a named constant is a read of that variable.
ENV_HELPERS = {"_env_on", "_env_int", "_env_float", "env_int"}
# The prefixes of lean-pkg's own variables: a string constant spelled exactly like one is a read, even when
# the code reaches it indirectly (a tuple, a dict, a generated script).
OWN_PREFIXES = ("LEAN_PKG_", "COMPARATOR_", "LEAN_EXERCISE_", "VERIFY_COMPARATOR_")
_ENVLIKE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# ${VAR:-default}, ${VAR-x}, ${VAR:=x}, ${VAR:+x}, ${VAR:?x}: a shell script reading VAR from its environment.
_SHELL_READ = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?[-=+?]")
# A read spelled out inside a string, such as the driver script comparator_types writes (DRIVER_PY), which
# the AST sees only as text: os.environ.get("X"), os.getenv("X"), os.environ["X"].
_TEXT_READ = re.compile(r"""(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]*)["']|environ\[\s*["']([A-Z][A-Z0-9_]*)["']""")


def _is_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or \
        (isinstance(node, ast.Name) and node.id == "environ")


def _constants(trees) -> dict[str, str]:
    """Module-level NAME = "STRING" across every scanned module (REGISTRY_ENV = "LEAN_PKG_REGISTRY", ...)."""
    out = {}
    for tree in trees:
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = node.value.value
    return out


class _Reads(ast.NodeVisitor):
    def __init__(self, consts: dict[str, str], where: str):
        self.consts, self.where = consts, where
        self.found: set[str] = set()
        self.unresolved: list[str] = []
        self.params: list[set[str]] = [set()]

    def visit_FunctionDef(self, node):
        a = node.args
        self.params.append({x.arg for x in a.posonlyargs + a.args + a.kwonlyargs})
        self.generic_visit(node)
        self.params.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _name(self, node) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            self.found.add(node.value)
        elif isinstance(node, ast.Name) and node.id in self.consts:
            self.found.add(self.consts[node.id])
        elif isinstance(node, ast.Attribute) and node.attr in self.consts:   # core.REGISTRY_ENV
            self.found.add(self.consts[node.attr])
        elif isinstance(node, ast.Name) and node.id in self.params[-1]:
            pass   # a helper's own parameter (_env_on(name)): its callers are scanned instead
        else:
            self.unresolved.append(f"{self.where}:{getattr(node, 'lineno', '?')}")

    def visit_Call(self, node):
        f = node.func
        if node.args and ((isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value))
                          or (isinstance(f, ast.Attribute) and f.attr == "getenv")
                          or (isinstance(f, ast.Name) and f.id in ENV_HELPERS)
                          or (isinstance(f, ast.Attribute) and f.attr in ENV_HELPERS)):
            self._name(node.args[0])
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if _is_environ(node.value) and isinstance(node.ctx, ast.Load):
            self._name(node.slice)
        self.generic_visit(node)

    def visit_Compare(self, node):
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) and _is_environ(node.comparators[0]):
            self._name(node.left)
        self.generic_visit(node)

    def visit_Constant(self, node):
        v = node.value
        if isinstance(v, str) and _ENVLIKE.fullmatch(v) and v.startswith(OWN_PREFIXES):
            self.found.add(v)


def python_files(roots) -> list[Path]:
    out = []
    for r in roots:
        r = Path(r)
        out += [r] if r.is_file() else sorted(r.rglob("*.py"))
    return [p for p in out if p.suffix == ".py"]


def shell_files(roots) -> list[Path]:
    out = []
    for r in roots:
        r = Path(r)
        out += [r] if r.is_file() else sorted(r.rglob("*.sh"))
    return [p for p in out if p.suffix == ".sh"]


def env_reads(roots) -> tuple[set[str], list[str]]:
    """(variables read, unresolved read sites) over the .py and .sh files under ROOTS."""
    files = python_files(roots)
    trees = [(p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p))) for p in files]
    consts = _constants(t for _, t in trees)
    found: set[str] = set()
    unresolved: list[str] = []
    for p, tree in trees:
        v = _Reads(consts, p.name)
        v.visit(tree)
        found |= v.found
        unresolved += v.unresolved
        found |= {a or b for a, b in _TEXT_READ.findall(p.read_text(encoding="utf-8"))}
    for p in shell_files(roots):
        found |= set(_SHELL_READ.findall(p.read_text(encoding="utf-8")))
    return found, unresolved
