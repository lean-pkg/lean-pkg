# lean-pkg export_paths -- path and link safety rules shared by every export of a tree
"""The path rules `release --fresh-repo` and `lean-pkg-extract` share.

- `excluded_from_export(rel)`: why a path never leaves the source (the private patterns file and the
  internal paths `public-safety` knows: approvals/, notes/, drafts/, scratch/, *.priv, *.private).
- `fs_link_leaves_tree(path, root)`: a symlink on disk that is absolute or resolves outside the
  tree (followed through the filesystem; `release --fresh-repo --from-tree`).
- `link_target(link_rel, target)`: the same question asked of a symlink stored in a git tree,
  answered lexically (nothing on disk is followed): the target as a repository-relative path, or
  why it cannot be one.
- `bad_rel_path(p)`: why a manifest path is not a plain repository-relative POSIX path.
"""

from __future__ import annotations

import os
import pathlib
import posixpath

from . import public_safety as ps

RECEIPTS_DIR = ".lean-receipts"


def excluded_from_export(rel: str) -> str | None:
    """Why a path is dropped from a fresh-history export, else None."""
    if rel == ps.PATTERNS_FILE or rel.endswith("/" + ps.PATTERNS_FILE):
        return "patterns file"
    return ps.internal_path(rel)


def is_receipts_path(rel: str) -> bool:
    return RECEIPTS_DIR in pathlib.PurePosixPath(rel).parts


def fs_link_leaves_tree(path: pathlib.Path, root: pathlib.Path) -> bool:
    """True when the symlink at `path` is absolute or its real path lies outside `root`'s."""
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    return os.path.isabs(os.readlink(path)) or not (real == real_root or real.startswith(real_root + os.sep))


def bad_rel_path(p, allow_dot: bool = False) -> str | None:
    """Why `p` is not a plain repository-relative POSIX path (no `..`, no `.` segment unless the whole
    path is `.` and allow_dot, no empty segment, no trailing `/`, no backslash, not absolute), else None."""
    if not isinstance(p, str) or not p:
        return "must be a non-empty string"
    if p == ".":
        return None if allow_dot else "must name a path, not `.`"
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
        return "must be relative, not absolute"
    if "\\" in p or "\0" in p:
        return "must use `/` separators (no backslash or NUL)"
    parts = p.split("/")
    if ".." in parts:
        return "must not contain `..` (path traversal)"
    if any(s in ("", ".") for s in parts):
        return "must be normalized (no empty or `.` segment, no trailing `/`)"
    return None


def link_target(link_rel: str, target: str) -> tuple[str | None, str | None]:
    """(repository-relative target, None) for a stored symlink at `link_rel` pointing at `target`, or
    (None, why) when the target is absolute or climbs out of the repository."""
    if not target:
        return None, "empty symlink target"
    if target.startswith("/"):
        return None, f"absolute symlink target {target!r}"
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(link_rel), target))
    if joined == ".." or joined.startswith("../"):
        return None, f"symlink target {target!r} leaves the repository"
    return joined, None


def under(path: str, root: str) -> bool:
    """`path` is `root` or lies below it (`.` contains everything)."""
    return root == "." or path == root or path.startswith(root + "/")
