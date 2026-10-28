# lean-pkg-extract recheck --public-summary -- the public summary of an extraction ledger (proposal 0009, S5)
"""The public summary of an extraction ledger: `lean-pkg-extract recheck --public-summary PATH`.

The extraction ledger is private: it names source paths, the source commit, source module and
declaration names and the reasons of drops and allowed differences. The summary
(`lean-pkg-extract-ledger-summary/1`) is what may be published: a report, written on request and never
read back as evidence. It holds the method, the SHA-256 of the ledger file it summarizes, the target
commit and the target module names; per declaration the target name (null for a DROPPED one: the name it
would have had is the mapped source name), the target module, the verdict, `target_sha256` and
`source_sha256` (a hash commitment: a holder of the source can verify it, and it tells anyone else
nothing); the same for each module's context row; and the totals. It leaves out source paths, source
module and declaration names (a rename reveals the old name), every reason, the warnings (they quote
both sides' pins) and the source commit, unless `--include-source-commit`.

Rules: written only when the recheck passes (exit 0), once (an existing path or a symbolic link is
refused, and the file is opened exclusively), never inside SRC (its work tree or git directory; the
extraction tools leave SRC unchanged), and allowed inside the target. Before it is written it is
staged beside PATH and scanned with public-safety as `decls` scans its tree (the source repository's
patterns file applies); a blocking hit refuses and nothing is written. Deterministic: keys sorted, rows
sorted by target-side values only (so their order reveals nothing about source names), no timestamp,
no host path.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tempfile

from . import public_safety as ps

SUMMARY_SCHEMA = "lean-pkg-extract-ledger-summary/1"
SUMMARY_KEYS = ("schema", "method", "ok", "ledger_sha256", "at_ledger_commits", "source_commit", "target",
                "entries", "context", "totals")
SUMMARY_ENTRY_KEYS = ("target", "target_module", "verdict", "source_sha256", "target_sha256")
SUMMARY_CONTEXT_KEYS = ("target_module", "verdict", "source_sha256", "target_sha256")
SCAN_NAME = "lean-pkg-extract-recheck"


def _inside(p: pathlib.Path, roots) -> bool:
    return any(r is not None and (p == r or r in p.parents) for r in roots)


def path_refusals(arg: str, src_roots) -> list[str]:
    """Why --public-summary PATH cannot be written: inside SRC (its work tree or git directory), a
    symbolic link, existing, or in a missing directory. Inside the target is allowed."""
    refusals = []
    p = pathlib.Path(arg).expanduser()
    real = p.parent.resolve() / p.name
    if _inside(real, [r.resolve() if r is not None else None for r in src_roots]):
        refusals.append(f"--public-summary {arg}: inside the source repository, which lean-pkg-extract leaves "
                        f"unchanged; write the summary outside SRC (inside the target is allowed)")
    if p.is_symlink():
        refusals.append(f"--public-summary {arg}: a symlink; name a new regular file")
    elif p.exists():
        refusals.append(f"--public-summary {arg}: exists; a summary is written once, so name a new path")
    elif not real.parent.is_dir():
        refusals.append(f"--public-summary {arg}: its directory does not exist")
    return refusals


def summary(led: dict, ledger_bytes: bytes, at_ledger_commits: bool, include_source_commit: bool) -> dict:
    """The summary of a passing recheck: `led` the recomputed ledger, `ledger_bytes` the ledger file read. A
    row with no target declaration (DROPPED) has `target: null`: the name it would have had is the source
    name under the map, which would reveal the source name."""
    entries = [{"target": e["target"] if e["target_sha256"] is not None else None, "target_module": e["target_module"], "verdict": e["verdict"],
                "source_sha256": e["source_sha256"], "target_sha256": e["target_sha256"]} for e in led["entries"]]
    context = [{"target_module": c["target_module"], "verdict": c["verdict"], "source_sha256": c["source_sha256"],
                "target_sha256": c["target_sha256"]} for c in led["context"]]

    def order(row: dict) -> tuple:
        return tuple("" if row[k] is None else row[k] for k in ("target_module", "target", "verdict",
                                                                 "target_sha256", "source_sha256") if k in row)

    return {
        "schema": SUMMARY_SCHEMA,
        "method": led["method"],
        "ok": True,
        "ledger_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "at_ledger_commits": at_ledger_commits,
        "source_commit": led["source"]["commit"] if include_source_commit else None,
        "target": {"commit": led["target"]["commit"], "modules": sorted(r["module"] for r in led["target"]["modules"])},
        "entries": sorted(entries, key=order),
        "context": sorted(context, key=order),
        "totals": dict(led["totals"]),
    }


def summary_json(s: dict) -> str:
    return json.dumps(s, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def scan_summary(scan: dict) -> dict:
    return {"ok": scan["ok"], "block_hits": scan["block_hits"], "warn_hits": scan["warn_hits"]}


def write(arg: str, text: str, patterns_root: pathlib.Path | None, source_repo: pathlib.Path | None
          ) -> tuple[dict | None, list[str]]:
    """Stage the summary beside PATH, scan it with public-safety, and write PATH exclusively when no hit
    blocks. Returns (scan summary or None, refusals); on a refusal nothing is written and the staging
    directory is removed."""
    p = pathlib.Path(arg).expanduser()
    data = text.encode("utf-8")
    try:
        stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{p.name}.", suffix=".lean-pkg-stage", dir=p.parent))
    except OSError as e:
        return None, [f"--public-summary {arg}: cannot be staged ({e.strerror or e}); nothing written"]
    try:
        (stage / p.name).write_bytes(data)
        scan = ps.scan({"name": SCAN_NAME}, stage, export=True, patterns_root=patterns_root,
                       source_repo=source_repo, public_allow_root=stage)
        blocking = ps.blocking(scan["hits"])
        if blocking:
            lines = [f"public-safety: {len(blocking)} blocking hit(s) in the public summary; nothing written"]
            lines += [f"public-safety:{ps.hit_line(h)}" for h in blocking]
            return scan_summary(scan), lines
        try:
            f = open(p, "xb")
        except OSError as e:
            return scan_summary(scan), [f"--public-summary {arg}: cannot be written ({e.strerror or e})"]
        try:
            with f:
                f.write(data)
        except OSError as e:
            p.unlink(missing_ok=True)
            return scan_summary(scan), [f"--public-summary {arg}: writing failed ({e.strerror or e}); nothing "
                                        f"written"]
        return scan_summary(scan), []
    finally:
        shutil.rmtree(stage, ignore_errors=True)
