"""Which tracked files a Lake package's build reads, for the docs-only binding rule, and whether a nested
registered package is a build input of the package around it.

Both questions are answered from the files committed at a commit (`show(rel) -> bytes | None`, rel
relative to the package directory), never from the working tree, and both fail closed: when the answer
cannot be read precisely, the caller keeps the conservative rule.

Lake semantics followed (Lake 4.x, `LeanLibConfig.isLocalModule`, `Package.isBuildableModule`): a
`lean_lib` owns every module that has one of its `roots` as a prefix, or that matches one of its `globs`
(`Foo`, `Foo.*`, `Foo.+`); its sources live under the package `srcDir` joined with the library's
`srcDir` (both default "."); `roots` defaults to the library's name. A `lean_exe` owns its `root`
module (default: its name); its modules below that root are counted too (over-inclusive on purpose).
`input_file` / `input_dir` targets and `require` path dependencies inside the package make every file
under their path a build input. Only lakefile.toml is read: Lake prefers lakefile.lean when both exist,
and a lakefile.lean is code, so with one present the scope is unknown and every `*.lean` counts."""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python >= 3.11
    tomllib = None

# The array-of-tables keys of a lakefile.toml that declare targets or dependencies (Lake.Load.Toml).
# Any other array of tables is a declaration this reader does not know: the scope is then unknown.
KNOWN_TABLE_ARRAYS = ("lean_lib", "lean_exe", "input_file", "input_dir", "require")


@dataclass
class LakeScope:
    """The modules and paths a package's build reads, as read from its lakefile.toml."""
    # (source dir relative to the package, "" for the package dir; module-name prefix; target label)
    modules: list[tuple[str, tuple[str, ...], str]] = field(default_factory=list)
    # (path relative to the package; target label): every file at or under it is a build input
    paths: list[tuple[str, str]] = field(default_factory=list)


def norm_dir(value) -> str | None:
    """A package-relative directory as a normalized posix path ("" for the package directory itself), or
    None when it is not a string, is absolute, or leaves the package directory."""
    if not isinstance(value, str):
        return None
    v = value.replace("\\", "/").strip()
    if v.startswith("/"):
        return None
    n = posixpath.normpath(v) if v else "."
    if n == ".":
        return ""
    if n == ".." or n.startswith("../"):
        return None
    return n


def name_parts(value) -> list[tuple[str, ...]] | None:
    """The module-name readings of a Lake name string: split at the dots outside «», and (a name that is
    not a dotted identifier is a simple name in Lake) the whole string as one component. None when the
    value is not a nonempty string."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    parts, cur, esc = [], "", False
    for ch in s:
        if ch == "«":
            esc = True
        elif ch == "»":
            esc = False
        elif ch == "." and not esc:
            parts.append(cur)
            cur = ""
            continue
        else:
            cur += ch
    parts.append(cur)
    out = []
    if all(parts):
        out.append(tuple(parts))
    whole = (s.replace("«", "").replace("»", ""),)
    if whole not in out:
        out.append(whole)
    return out


def glob_parts(value) -> list[tuple[str, ...]] | None:
    """`Foo`, `Foo.*`, `Foo.+` as module-name prefixes (every glob kind is read as a prefix: a superset)."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith((".*", ".+")):
        s = s[:-2]
    return name_parts(s)


def _str_list(value) -> list | None:
    """Lake's `decodeArrayOrSingleton`: a string or an array of them; None for anything else."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    return None


def scope_from_toml(text: str) -> tuple[LakeScope | None, str]:
    """(scope, "") from lakefile.toml text, or (None, why) when it cannot be read precisely."""
    if tomllib is None:
        return None, "no TOML reader"
    try:
        data = tomllib.loads(text)
    except (ValueError, TypeError) as e:
        return None, f"lakefile.toml does not parse ({str(e)[:60]})"
    if not isinstance(data, dict):
        return None, "lakefile.toml is not a table"
    for k, v in data.items():
        if k not in KNOWN_TABLE_ARRAYS and isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            return None, f"lakefile.toml declares [[{k}]], which this reader does not know"
    pkg_src = norm_dir(data.get("srcDir", "."))
    if pkg_src is None:
        return None, "lakefile.toml srcDir leaves the package directory"
    scope = LakeScope()
    for kind in KNOWN_TABLE_ARRAYS:
        entries = data.get(kind, [])
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            return None, f"lakefile.toml [[{kind}]] is not an array of tables"
        for e in entries:
            name = e.get("name")
            if kind == "require":
                src = e.get("source") if isinstance(e.get("source"), dict) else {}
                raw = e.get("path", src.get("dir") if src.get("type") == "path" else None)
                if raw is None:
                    continue
                d = norm_dir(raw)
                if d is None:
                    return None, f"require {name!r}: path {raw!r} leaves the package directory"
                scope.paths.append((d, f"require {name} path"))
                continue
            if not isinstance(name, str) or not name.strip():
                return None, f"lakefile.toml [[{kind}]] without a name"
            if kind in ("input_file", "input_dir"):
                d = norm_dir(e.get("path", name))
                if d is None:
                    return None, f"{kind} {name}: path leaves the package directory"
                scope.paths.append((d, f"{kind} {name}"))
                continue
            sd = norm_dir(e.get("srcDir", "."))
            if sd is None:
                return None, f"{kind} {name}: srcDir leaves the package directory"
            src_dir = posixpath.join(pkg_src, sd) if pkg_src and sd else (pkg_src or sd)
            if kind == "lean_lib":
                roots = _str_list(e.get("roots", name))
                globs = _str_list(e.get("globs", [])) if "globs" in e else []
                if roots is None or globs is None:
                    return None, f"lean_lib {name}: roots/globs are not strings"
                prefixes = []
                for r in roots:
                    got = name_parts(r)
                    if got is None:
                        return None, f"lean_lib {name}: unreadable root {r!r}"
                    prefixes += got
                for g in globs:
                    got = glob_parts(g)
                    if got is None:
                        return None, f"lean_lib {name}: unreadable glob {g!r}"
                    prefixes += got
            else:
                root = e.get("root", name)
                prefixes = name_parts(root)
                if prefixes is None:
                    return None, f"lean_exe {name}: unreadable root {root!r}"
            for p in prefixes:
                scope.modules.append((src_dir, p, f"{kind} {name}"))
    if not scope.modules:
        return None, "lakefile.toml declares no lean_lib or lean_exe"
    return scope, ""


def lake_scope(show) -> tuple[LakeScope | None, str]:
    """(scope, "") for the package whose committed files `show` reads, or (None, why) when the
    conservative rule applies (a lakefile.lean, no lakefile.toml, or one this reader cannot read)."""
    if show("lakefile.lean") is not None:
        return None, "lakefile.lean"
    raw = show("lakefile.toml")
    if raw is None:
        return None, "no lakefile.toml"
    return scope_from_toml(raw.decode("utf-8", errors="replace"))


def _under(path: str, d: str) -> bool:
    return d == "" or path == d or path.startswith(d + "/")


def scope_reason(scope: LakeScope, path: str) -> str | None:
    """Why a package-relative path is a build input under `scope`, or None."""
    for d, label in scope.paths:
        if _under(path, d):
            return label
    if not path.endswith(".lean"):
        return None
    for src, prefix, label in scope.modules:
        if src and not path.startswith(src + "/"):
            continue
        rel = path[len(src) + 1:] if src else path
        mod = tuple(rel[:-len(".lean")].split("/"))
        if mod[:len(prefix)] == prefix:
            return label
    return None


def overlaps(a: str, b: str) -> bool:
    """Two package-relative paths where one is (under) the other; "" is the whole package."""
    return a == b or _under(a, b) or _under(b, a)


def nested_dependency(show, rel: str) -> str | None:
    """Why the package whose committed files `show` reads depends on (or reads) the nested directory
    `rel`, or None when nothing it commits points there. Fails closed: an unreadable manifest or lakefile
    is a reason."""
    raw = show("lake-manifest.json")
    if raw is not None:
        try:
            man = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return "lake-manifest.json is not valid JSON"
        pkgs = man.get("packages") if isinstance(man, dict) else None
        if pkgs is not None and not isinstance(pkgs, list):
            return "lake-manifest.json packages is not a list"
        for p in pkgs or []:
            if isinstance(p, dict) and p.get("type") == "path":
                d = norm_dir(p.get("dir"))
                if d is None or overlaps(d, rel):
                    return f"lake-manifest.json path package {p.get('name')!r} (dir {p.get('dir')!r})"
    lean = show("lakefile.lean")
    if lean is not None:
        text = lean.decode("utf-8", errors="replace")
        for comp in {rel, rel.split("/")[0]}:
            if re.search(rf"(?<![\w.'«]){re.escape(comp)}(?![\w'»])", text):
                return f"lakefile.lean mentions {comp!r} (a lakefile.lean is code; read conservatively)"
    raw = show("lakefile.toml")
    if raw is not None:
        scope, why = scope_from_toml(raw.decode("utf-8", errors="replace"))
        if scope is None:
            if why == "lakefile.toml declares no lean_lib or lean_exe":
                scope = LakeScope()
            else:
                return why
        for d, label in scope.paths:
            if overlaps(d, rel):
                return f"lakefile.toml {label} ({d or '.'})"
        for src, prefix, label in scope.modules:
            mdir = posixpath.join(src, *prefix) if src else posixpath.join(*prefix)
            if overlaps(mdir, rel):
                return f"lakefile.toml {label} (sources under {mdir})"
    return None
