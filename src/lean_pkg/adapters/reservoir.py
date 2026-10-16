"""Reservoir, searched through its index data, never the site's name-only box.

The `leanprover/reservoir-index` tarball is cached under
$XDG_CACHE_HOME/lean-pkg/reservoir-index/<commit>/ (fetched with `gh api`), and
descriptions, keywords and names in every metadata.json are searched. Tests and
offline users can point LEAN_PKG_RESERVOIR_INDEX at an unpacked index directory.
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import re
import shutil
import tarfile

from . import cache_dir, hit, matches, note_cache_write, ok, run_cmd, skipped

NAME = "reservoir"
ENV = "LEAN_PKG_RESERVOIR_INDEX"
REPO = "leanprover/reservoir-index"
LIMIT = 30
_SHA = re.compile(r"^[0-9a-f]{40}$")


def runner(argv: list[str], timeout: float = 120):
    return run_cmd(argv, timeout)


def head_commit() -> str | None:
    r = runner(["gh", "api", f"repos/{REPO}/commits/HEAD", "--jq", ".sha"], 30)
    if r is None or r[0] != 0:
        return None
    sha = r[1].decode("utf-8", errors="replace").strip()
    return sha if _SHA.match(sha) else None


def cached_dirs() -> list[pathlib.Path]:
    base = cache_dir("reservoir-index")
    if not base.is_dir():
        return []
    dirs = [p for p in base.iterdir() if p.is_dir() and _SHA.match(p.name)]
    return sorted(dirs, key=lambda p: p.stat().st_mtime, reverse=True)


def extract(blob: bytes, dest: pathlib.Path) -> None:
    """Unpack a GitHub tarball (one top directory) into dest. Only regular files and
    directories are written, by hand, below dest; any other member refuses the tarball."""
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for m in tf.getmembers():
            parts = pathlib.PurePosixPath(m.name).parts[1:]
            if not parts:
                continue
            if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()):
                shutil.rmtree(tmp, ignore_errors=True)
                raise ValueError(f"unsafe tarball member {m.name!r}")
            target = tmp.joinpath(*parts)
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tf.extractfile(m).read())
    tmp.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)


def index_dir() -> tuple[pathlib.Path | None, str | None, str | None]:
    """(directory, revision, reason when unavailable)."""
    env = os.environ.get(ENV, "").strip()
    if env:
        p = pathlib.Path(env).expanduser()
        return (p, p.name, None) if p.is_dir() else (None, None, f"{ENV} is not a directory")
    sha = head_commit()
    if sha is None:
        cached = cached_dirs()
        if cached:
            return cached[0], cached[0].name[:8] + " (cached)", None
        return None, None, "gh unavailable or offline, and no cached index"
    dest = cache_dir("reservoir-index", sha)
    if not dest.is_dir():
        r = runner(["gh", "api", f"repos/{REPO}/tarball/{sha}"])
        if r is None or r[0] != 0:
            return None, None, "index tarball download failed"
        try:
            extract(r[1], dest)
        except (ValueError, tarfile.TarError, OSError) as e:
            return None, None, f"index tarball refused: {type(e).__name__}"
        note_cache_write("reservoir-index", sha)
    return dest, sha[:8], None


def revision() -> str | None:
    env = os.environ.get(ENV, "").strip()
    if env:
        return pathlib.Path(env).name
    cached = cached_dirs()
    return cached[0].name[:8] if cached else None


def search(term: str, timeout: float = 10) -> dict:
    d, rev, reason = index_dir()
    if d is None:
        return skipped(reason)
    found, n = [], 0
    for f in sorted(d.rglob("metadata.json")):
        try:
            m = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(m, dict):
            continue
        n += 1
        if not matches(term, m.get("fullName"), m.get("name"), m.get("description"),
                       " ".join(str(k) for k in m.get("keywords") or [])):
            continue
        srcs = [s for s in m.get("sources") or [] if isinstance(s, dict)]
        repo = (srcs[0].get("repoUrl") if srcs else None) or f"https://github.com/{m.get('fullName')}"
        found.append(hit(m.get("fullName") or m.get("name"), m.get("description"), repo,
                         stars=m.get("stars") or 0, license=m.get("license"),
                         keywords=list(m.get("keywords") or []),
                         reservoir=f"https://reservoir.lean-lang.org/@{m.get('owner')}/{m.get('name')}"))
    found.sort(key=lambda h: (-int(h["extra"]["stars"] or 0), h["name"].lower()))
    return ok(rev, found[:LIMIT], f"{n} packages searched", total=len(found))
