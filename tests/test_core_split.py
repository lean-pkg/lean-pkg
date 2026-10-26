"""The core split (#317): the shared core moved from cli.py to core.py (stage 1), cli.py re-exports it,
and lean-pkg's own modules import core directly (stage 2).

`import lean_pkg` is not the supported interface (AGENTS.md), but the tests and outside scripts that
read the core through lean_pkg.cli must keep working for at least one release, so:

- every attribute the cli module had before the split is still an attribute of lean_pkg.cli
  (tests/data/cli_module_names.txt, recorded from origin/main at 72e4f24);
- every name core.py defines is re-exported by cli.py as the SAME object, never a copy that could drift;
- core.py never imports cli (no cycle), and importing it imports no other lean_pkg module;
- cli.py never rebinds a core name: module state (caches, the lease actor, pending events) has exactly
  one home, core.py, and a rebinding in cli.py would not be seen by the code there.

Inside lean-pkg (src/ and scripts/), no module imports cli under the name `core` any more: `core` is the
core module, and a name still defined in cli.py is reached as `cli.X`, so the spelling says which module
a name is read from (and which one a test must patch).
"""
from __future__ import annotations

import ast
import subprocess
import sys
import types

from lean_pkg import cli, core

from .conftest import ROOT

SRC = ROOT / "src" / "lean_pkg"


def core_defined_names() -> set[str]:
    """Names core.py binds at module level (functions, classes, assignments), not its imports."""
    out = set()
    for node in ast.parse((SRC / "core.py").read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                out |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    return out


def test_every_name_the_cli_module_had_is_still_there():
    lines = (ROOT / "tests" / "data" / "cli_module_names.txt").read_text().splitlines()
    names = [ln for ln in lines if ln and not ln.startswith("#")]
    assert len(names) > 500 and "Fail" in names and "main" in names
    missing = [n for n in names if not hasattr(cli, n)]
    assert not missing, f"lean_pkg.cli lost {len(missing)} name(s): {missing}"


def test_every_core_name_is_re_exported_as_the_same_object():
    names = core_defined_names()
    assert {"Fail", "UsageError", "load_registry", "save_registry", "record", "now_iso", "_event_line",
            "EVENT_KEYS", "_HEX40", "_ACTOR", "_LOADED_PACKAGES"} <= names
    missing = sorted(n for n in names if not hasattr(cli, n))
    assert not missing, f"cli.py does not re-export: {missing}"
    copies = sorted(n for n in names if getattr(cli, n) is not getattr(core, n))
    assert not copies, f"cli.X is not core.X for: {copies}"
    # (not `== "lean_pkg.core"`: the autouse fixture in conftest.py wraps _event_line for every test)
    in_cli = sorted(n for n in names if isinstance(getattr(core, n), (types.FunctionType, type))
                    and getattr(core, n).__module__ in ("lean_pkg.cli", "__main__"))
    assert not in_cli, f"core names whose object is defined in cli.py: {in_cli}"


def lean_pkg_imports(module: str) -> list[str]:
    """The lean_pkg modules a module imports anywhere (top level or inside a function), by name."""
    out = []
    for node in ast.walk(ast.parse((SRC / f"{module}.py").read_text())):
        if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").startswith("lean_pkg")):
            out.append((node.module or "").removeprefix("lean_pkg.") or ",".join(a.name for a in node.names))
        elif isinstance(node, ast.Import):
            out += [a.name.removeprefix("lean_pkg.") for a in node.names if a.name.startswith("lean_pkg")]
    return out


def test_core_never_imports_cli():
    # core.py imports only build_scope, a leaf module that imports no lean_pkg module, and only inside
    # the functions that use it: no import of core can reach cli, so there is no cycle
    assert set(lean_pkg_imports("core")) <= {"build_scope"}, lean_pkg_imports("core")
    assert lean_pkg_imports("build_scope") == []
    top = [n for n in ast.parse((SRC / "core.py").read_text()).body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert all(isinstance(n, ast.Import) or n.module == "__future__" or not n.level for n in top)
    r = subprocess.run([sys.executable, "-c", "import sys, lean_pkg.core; "
                        "print(sorted(m for m in sys.modules if m.startswith('lean_pkg')))"],
                       capture_output=True, text=True, check=True, cwd=ROOT,
                       env={"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
    assert r.stdout.strip() == "['lean_pkg', 'lean_pkg.core']", r.stdout


def test_cli_never_rebinds_a_core_name():
    names = core_defined_names()
    tree = ast.parse((SRC / "cli.py").read_text())
    rebound = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            rebound |= set(node.names) & names
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            rebound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                rebound |= {n.id for n in ast.walk(target) if isinstance(n, ast.Name)} & names
    assert not rebound, f"cli.py rebinds core names (core.py would not see it): {sorted(rebound)}"


def test_a_name_core_rebinds_is_read_through_from_cli(env):
    # every `global` statement in core.py names a module-level name it rebinds at run time; cli.py must
    # read each one from core.py (its __getattr__), or lean_pkg.cli would keep the first object
    rebinds = {name for node in ast.walk(ast.parse((SRC / "core.py").read_text()))
               if isinstance(node, ast.Global) for name in node.names}
    assert rebinds == set(cli._CORE_REBOUND) == {"_LOADED_PACKAGES"}
    assert "_LOADED_PACKAGES" not in vars(cli)        # not a re-export: read through
    reg = cli.load_registry(env.registry)              # rebinds core._LOADED_PACKAGES to reg["packages"]
    assert core._LOADED_PACKAGES is reg["packages"]
    assert cli._LOADED_PACKAGES is core._LOADED_PACKAGES
    assert not hasattr(cli, "no_such_name_in_cli")


def own_python_files() -> list:
    """Every Python file of lean-pkg itself: the package (src/) and the maintenance scripts (scripts/)."""
    files = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
    assert SRC / "cli.py" in files and SRC / "core.py" in files and len(files) > 50
    return files


def test_no_module_imports_cli_under_the_name_core():
    # stage 2: `from . import cli as core` (and every other spelling: `from lean_pkg import cli as core`,
    # `import lean_pkg.cli as core`) is gone from src/ and scripts/; read by the AST, so every form counts,
    # at the top level or inside a function, and a comment or docstring quoting it does not
    found = []
    for path in own_python_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                mod = "." * node.level + (node.module or "")
                names = [(f"{mod}.{a.name}" if mod.strip(".") else mod + a.name, a.asname) for a in node.names]
            elif isinstance(node, ast.Import):
                names = [(a.name, a.asname) for a in node.names]
            else:
                continue
            found += [f"{path.relative_to(ROOT)}:{node.lineno}" for full, asname in names
                      if asname == "core" and full.rsplit(".", 1)[-1] == "cli"]
    assert not found, f"cli imported as `core` (import lean_pkg.core instead): {found}"
    # the plain-text form too, so a grep for it in src/ and scripts/ stays empty
    quoted = [str(p.relative_to(ROOT)) for p in own_python_files() if "cli as core" in p.read_text()]
    assert not quoted, quoted


def test_every_core_attribute_a_module_reads_is_on_core():
    # a module that imports core reaches through it only names core.py has; a name still defined in
    # cli.py is spelled cli.X (a `core` alias that silently fell back to cli would hide which module a
    # test has to patch)
    missing = []
    for path in sorted(SRC.glob("*.py")):
        tree = ast.parse(path.read_text())
        imports_core = any(isinstance(n, ast.ImportFrom) and n.level == 1 and n.module is None
                           and any(a.name == "core" and a.asname is None for a in n.names) for n in ast.walk(tree))
        if not imports_core:
            continue
        missing += [f"{path.name}:{n.lineno}: core.{n.attr}" for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "core"
                    and not hasattr(core, n.attr)]
    assert not missing, missing
