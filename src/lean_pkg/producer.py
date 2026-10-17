# lean-pkg producer -- a fingerprint of the running lean-pkg, so a long run notices its own code changing
"""The producer of a receipt is the lean-pkg that writes it. A `receipt systemd --run` (alias `receipt local
--run`) lives for the whole verification run, hours for a large package, and only collects the evidence at the
end. When lean-pkg runs from a shared editable checkout, a `git pull` or `git checkout` there during the run
replaces its own sources underneath it: the receipt would be written by a mix of the code that started the run
(already imported) and the code now on disk (imported lazily, and the shipped scripts and templates). A version
string or the entry-point script alone does not show it; the package's internal sources do.

`fingerprint()` records, at run start, the SHA-256 of

- every file of the imported `lean_pkg` package directory (`core.package_dir()`: Python sources, shipped
  scripts, schemas, templates; `__pycache__/` and `*.pyc` are skipped, they are caches of the sources),
- the installed distribution's metadata (`*.dist-info`: RECORD, METADATA, direct_url.json, entry_points.txt)
  when it is the one this package was imported from,
- the Python interpreter (`sys.executable`, resolved) and the entry-point script (`sys.argv[0]`) when it is a
  file outside the package.

`changes(before, after)` names every entry added, removed or changed; `receipt systemd --run` checks it while
it waits (a WARN, once) and again at final collection, before anything is written (a refusal naming the files).

`sources_sha256` is reproducible by anyone from the same release: it is the SHA-256 of the `sha256sum` listing
of the package files, paths relative to the package directory, sorted bytewise:

    cd "$(python3 -c 'import lean_pkg, os; print(os.path.dirname(lean_pkg.__file__))')" &&
      find . -type f ! -path '*/__pycache__/*' ! -name '*.pyc' | sed 's|^\\./||' | LC_ALL=C sort |
      xargs -d '\\n' sha256sum | sha256sum

`receipt_producer(fp)` is the part sealed into the receipt as the additive key `producer` (gate and
`receipt check` ignore it): the version, the install kind (editable / installed / unknown, as `--version`
detects it), the editable checkout's commit and dirtiness, `sources_sha256`, the number of source files and the
Python version. No path, host or user name is written."""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
import pathlib
import platform
import sys
import urllib.parse
import urllib.request

from . import cli as core

SKIP_DIRS = ("__pycache__",)
SKIP_SUFFIXES = (".pyc", ".pyo")
DIST_FILES = ("RECORD", "METADATA", "direct_url.json", "entry_points.txt")
SHOWN = 9


def _sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return f"unreadable ({e.__class__.__name__})"
    return h.hexdigest()


def source_files(pkg: pathlib.Path) -> dict[str, str]:
    """{path relative to the package directory: SHA-256} for every file of the package, caches skipped."""
    out = {}
    for root, dirs, files in os.walk(pkg):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in files:
            if name.endswith(SKIP_SUFFIXES):
                continue
            p = pathlib.Path(root, name)
            out[p.relative_to(pkg).as_posix()] = _sha(p)
    return out


def sources_sha256(files: dict[str, str]) -> str:
    """SHA-256 of the `sha256sum` listing of `files`, sorted bytewise by path (reproducible with coreutils)."""
    listing = "".join(f"{files[rel]}  {rel}\n" for rel in sorted(files, key=lambda r: r.encode()))
    return hashlib.sha256(listing.encode()).hexdigest()


def _dist_info(pkg: pathlib.Path) -> pathlib.Path | None:
    """The `*.dist-info` directory of the installed lean-pkg distribution this package was imported from: a
    regular install whose files hold this package, or an editable install whose direct_url.json names a
    directory containing it. None otherwise (e.g. run from PYTHONPATH=src beside an unrelated install)."""
    try:
        dist = md.distribution("lean-pkg")
    except md.PackageNotFoundError:
        return None
    base = getattr(dist, "_path", None)
    if base is None:
        return None
    base = pathlib.Path(base)
    try:
        if pathlib.Path(dist.locate_file("lean_pkg/__init__.py")).resolve() == (pkg / "__init__.py").resolve():
            return base
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
    except (OSError, ValueError):
        return None
    url = direct.get("url", "")
    if direct.get("dir_info", {}).get("editable") and url.startswith("file:"):
        root = pathlib.Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path)).resolve()
        if root == pkg.resolve() or root in pkg.resolve().parents:
            return base
    return None


def fingerprint(pkg: pathlib.Path | None = None) -> dict:
    """{"version", "sources": {rel: sha}, "environment": {label: sha}} of the running lean-pkg."""
    from . import __version__
    pkg = (pkg or core.package_dir()).resolve()
    env = {}
    exe = os.path.realpath(sys.executable) if sys.executable else ""
    if exe:
        env["python interpreter"] = _sha(pathlib.Path(exe))
    entry = pathlib.Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else None
    if entry is not None and entry.is_file() and pkg not in entry.parents:
        env[f"entry point {entry.name}"] = _sha(entry)
    info = _dist_info(pkg)
    if info is not None:
        for name in DIST_FILES:
            if (info / name).is_file():
                env[f"dist-info {name}"] = _sha(info / name)
    return {"version": __version__, "pkg": str(pkg), "sources": source_files(pkg), "environment": env}


def changes(before: dict, after: dict) -> list[str]:
    """Every source file (package-relative, `lean_pkg/...`) and environment entry added, removed or changed."""
    out = []
    for kind, prefix in (("sources", "lean_pkg/"), ("environment", "")):
        a, b = before.get(kind, {}), after.get(kind, {})
        for key in sorted(set(a) | set(b)):
            if key not in b:
                out.append(f"{prefix}{key} (removed)")
            elif key not in a:
                out.append(f"{prefix}{key} (added)")
            elif a[key] != b[key]:
                out.append(f"{prefix}{key}")
    if before.get("version") != after.get("version") and not out:
        out.append(f"version {before.get('version')} -> {after.get('version')}")
    return out


def shown(names: list[str]) -> str:
    return ", ".join(names[:SHOWN]) + (f" (+{len(names) - SHOWN} more)" if len(names) > SHOWN else "")


def changed_message(names: list[str], when: str) -> str:
    """The refusal (or WARN) text for a producer that changed."""
    return (f"lean-pkg itself changed {when}: {len(names)} file(s) of the running lean-pkg differ from the run "
            f"start: {shown(names)}; the receipt would be written by other code than the one that started the "
            f"run. Run long jobs from a frozen install (pipx/venv from a release tag), never a shared editable "
            f"checkout: docs/receipts.md, \"Long runs: freeze the producer\"")


def receipt_producer(fp: dict) -> dict:
    """The additive receipt key `producer`: what wrote the receipt, without any path."""
    info = core.install_detect(pathlib.Path(fp["pkg"]))
    commit = None
    if info["install"] == "editable":
        p = core.registry_git(pathlib.Path(info["path"]), "--no-optional-locks", "rev-parse", "HEAD")
        commit = p.stdout.strip() if p.returncode == 0 else None
    return {"lean_pkg": fp["version"], "install": info["install"], "commit": commit, "dirty": info["dirty"],
            "sources_sha256": sources_sha256(fp["sources"]), "source_files": len(fp["sources"]),
            "python": platform.python_version()}
