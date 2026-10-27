# lean-pkg-extract -- the write step shared by every lean-pkg-extract verb that writes a tree
"""The one way lean-pkg-extract writes an output tree (proposal 0009, stage S3; two-tracks gap 17).

`lean-pkg-extract decls --output DIR` uses it today; the file-level `write --output DIR` of gap 17 is
meant to use it too. The contract:

- `output_refusals(out, flag, protected)`: why DIR cannot be written, as a list (empty: it can). DIR
  must be absent or an empty directory, must not be a symbolic link, its parent directory must exist,
  and its resolved path must lie outside every protected tree (SRC's work tree and git directory: the
  extraction tools leave SRC unchanged). Nothing is created by asking.
- `stage_path(out)`: the staging directory, beside DIR on the same filesystem (so the final move is
  one `os.replace`): `.<DIR name>.lean-pkg-stage`. Its name is fixed, so a staging directory left by
  an interrupted run is found and refused (`StagingCollision`), never silently reused or deleted.
- `write_tree(out, files, ...)`: creates the staging directory (exclusively), writes every file into
  it, scans it with public-safety as `repo export` scans its staged tree (`ps.scan(..., export=True)`:
  every file, the source repository's patterns file and declared addresses), and refuses on a blocking
  hit (`WriteRefused`). Then `prepare(stage)` runs (for example `git init`), then `before_move(stage)`
  (for example writing the ledger, which may return an undo callable), then one `os.replace(stage,
  out)` puts the tree in place. Any failure on the way, a refusal or an exception, removes the
  staging directory and undoes `before_move`: DIR is then exactly as it was (absent or empty), and
  nothing half-written is left anywhere.
"""

from __future__ import annotations

import os
import pathlib
import shutil
from typing import Callable

from . import export_paths as xp
from . import public_safety as ps

STAGE_SUFFIX = ".lean-pkg-stage"


class WriteRefused(Exception):
    """The tree was not written; `reasons` says why (one line each). Nothing is left behind."""

    def __init__(self, reasons: list[str], scan: dict | None = None):
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.scan = scan


class StagingCollision(WriteRefused):
    """The staging directory beside DIR already exists (an interrupted run, or a concurrent one)."""


def _resolved(p: pathlib.Path) -> pathlib.Path:
    """p with its nearest existing ancestor resolved (symbolic links followed), the rest appended."""
    p = p.absolute()
    tail: list[str] = []
    while not p.exists() and not p.is_symlink() and p != p.parent:
        tail.append(p.name)
        p = p.parent
    return p.resolve().joinpath(*reversed(tail))


def output_refusals(out: pathlib.Path, flag: str, protected) -> list[str]:
    """Why `out` cannot receive a written tree (see the module docstring); [] when it can. `protected`
    is [(what, path or None)]."""
    shown = f"{flag} {out}"
    if out.is_symlink():
        return [f"{shown}: a symbolic link; name a new directory (the tree is never written through a link)"]
    if out.exists() and not out.is_dir():
        return [f"{shown}: exists and is not a directory; nothing written"]
    refusals = []
    if out.is_dir() and any(out.iterdir()):
        refusals.append(f"{shown}: exists and is not empty; nothing written (name a new or empty directory)")
    if not out.absolute().parent.is_dir():
        refusals.append(f"{shown}: its parent directory does not exist")
    real = _resolved(out)
    for what, root in protected:
        if root is None:
            continue
        r = root.resolve()
        if real == r or r in real.parents:
            refusals.append(f"{shown}: inside {what} (resolved: it lies under that tree); lean-pkg-extract "
                            f"leaves the source unchanged, so write outside it")
    return refusals


def stage_path(out: pathlib.Path) -> pathlib.Path:
    out = out.absolute()
    return out.parent / f".{out.name}{STAGE_SUFFIX}"


def blob_rel_refusal(rel: str) -> str | None:
    why = xp.bad_rel_path(rel)
    return f"{rel}: {why}" if why else None


def write_tree(out: pathlib.Path, files: dict[str, bytes], *, scan_pkg: dict, patterns_root: pathlib.Path | None,
               source_repo: pathlib.Path | None, prepare: Callable[[pathlib.Path], None] | None = None,
               before_move: Callable[[pathlib.Path], Callable[[], None] | None] | None = None) -> dict:
    """Write `files` (relative POSIX path -> bytes) as the tree `out`, through a staging directory beside it
    (see the module docstring). Returns the public-safety scan of the staged tree. Raises WriteRefused
    (StagingCollision for a leftover staging directory) or the OSError that stopped it; in every failing
    case the staging directory is gone and `out` is as it was."""
    bad = [r for r in (blob_rel_refusal(rel) for rel in sorted(files)) if r]
    if bad:
        raise WriteRefused([f"not written: {b}" for b in bad])
    stage = stage_path(out)
    collision = StagingCollision([f"the staging directory {stage.name} exists beside {out.name} (left by an "
                                  f"interrupted run, or another run is writing): remove it after checking "
                                  f"nothing is using it; nothing written"])
    if stage.exists() or stage.is_symlink():
        raise collision
    try:
        stage.mkdir()
    except FileExistsError:
        raise collision from None
    undo: Callable[[], None] | None = None
    try:
        for rel in sorted(files):
            f = stage / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            with open(f, "xb") as fh:
                fh.write(files[rel])
        scan = ps.scan(scan_pkg, stage, export=True, patterns_root=patterns_root, source_repo=source_repo,
                       public_allow_root=stage)
        blocking = ps.blocking(scan["hits"])
        if blocking:
            lines = [f"public-safety: {len(blocking)} blocking hit(s) in the staged tree; nothing written"]
            lines += [f"public-safety:{ps.hit_line(h)}" for h in blocking]
            raise WriteRefused(lines, scan)
        if prepare is not None:
            prepare(stage)
        if before_move is not None:
            undo = before_move(stage)
        os.replace(stage, out)
        return scan
    except BaseException:
        if undo is not None:
            undo()
        shutil.rmtree(stage, ignore_errors=True)
        raise
