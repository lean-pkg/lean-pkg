# lean-pkg producer skew -- which lean-pkg wrote a package's gate receipts, against the one running now
"""A package's evidence chain can mix lean-pkg versions: the lower-tier receipt written by one release, the
comparator receipt by another, and `gate`, `gate --check` or `receipt check` run by a third (a field report).
A receipt records the lean-pkg that wrote it in its additive key `producer` (`producer.lean_pkg`: `receipt
slurm`, `receipt systemd --run`, `receipt comparator`); `skew(pkg, repo)` compares that version with the
running `__version__` for the receipts the package's gate state rests on, the registry's `gate.receipt`
(lower tier) and `gate.comparator`, read from the checkout as `status` reads them.

Directions, per receipt:

- `unknown`: no readable receipt, no `producer` key (a receipt written before receipts recorded it, or by
  `receipt systemd` without `--run`) or a version that is not X.Y.Z. Info only: nothing can be compared.
- `same`: the same version string. Info only; commits between releases are not compared.
- `older` (producer < running): the breaking entries of the shipped list `data/upgrade-breaking.yaml`
  (generated from docs/upgrades/*.yaml by scripts/upgrade-breaking.py) whose version is above the producer's,
  restricted to EVIDENCE_AREAS. The list ships with the running lean-pkg, so every entry in it is part of the
  running code (its own unreleased record included): the range is (producer, running]. With such an entry:
  a WARN naming the entry ids and `lean-pkg doctor --upgrade-from vPRODUCER --source DIR`. Without one
  (only page, CLI or other non-evidence entries in between, or none): info only, no warning.
- `newer` (producer > running): always a WARN. An older checker reading a newer receipt is the dangerous
  direction: it ignores what it does not know (an older lean-pkg reads a sealed `slurm_sacct` block as an
  unknown key and checks nothing), and its shipped list cannot name the later releases' changes.

Never a refusal, never an exit status: `status NAME` prints the WARN lines and adds `producer_skew` to its
JSON; `doctor --package NAME` runs the same check on demand.

EVIDENCE_AREAS is derived from the `area` values the records use (an area is a comma-separated list of
components): the components whose change alters what a receipt holds or how it is judged, the receipt
writers and readers (receipts, receipt slurm, receipt check, comparator receipts, comparator, stage logs),
the gate, and what turns evidence into credited claims (claims, claims ledger, claims mark, coverage check,
lean-pkg-signoff.json, palomar check, verify). OTHER_AREAS are the components that do not (the page and its
renderers, the CLI surface, the registry, the index, scaffolding, diagnostics, docs and tests).
tests/test_producer_skew.py fails when a record uses a component in neither list, so a new area is
classified when it first appears.
"""

from __future__ import annotations

import functools
import json
import pathlib
import re

import yaml

SCHEMA = "lean-pkg-upgrade-breaking/1"
DATA_PATH = pathlib.Path(__file__).resolve().parent / "data" / "upgrade-breaking.yaml"
ENTRY_KEYS = ("version", "id", "pr", "kind", "area")
ROLES = (("lower-tier", "receipt"), ("comparator", "comparator"))   # (role, the registry's gate.<key>)

EVIDENCE_AREAS = frozenset({
    "receipts", "receipt slurm", "receipt check", "comparator receipts", "comparator",
    "comparator (legacy mechanism)", "stage logs", "gate", "claims", "claims ledger", "claims mark",
    "coverage check", "lean-pkg-signoff.json", "palomar check", "verify"})
OTHER_AREAS = frozenset({
    "page", "cli", "--json", "new", "doctor", "status", "registry", "public-safety", "extract",
    "lean-pkg-extract", "formalization", "formalization render", "formalization.yaml", "coverage.yaml",
    "index add", "index check", "index render", "index entries", "blueprint", "stacks", "comparator check",
    "comparator config --coverage and --from-claims", "tests", "docs (receipts recipe)"})

UNKNOWN_NOTE = ("the receipt records no `producer` (written before receipts recorded it, or by `receipt systemd` "
                "without --run)")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def version_key(text) -> tuple[int, int, int] | None:
    m = _VERSION.fullmatch(str(text or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def area_components(area) -> list[str]:
    return [a.strip() for a in str(area or "").split(",") if a.strip()]


def evidence_entry(entry: dict) -> bool:
    return any(a in EVIDENCE_AREAS for a in area_components(entry.get("area")))


class DataError(ValueError):
    """The shipped breaking-entries file is malformed."""


def parse(text: str) -> list[dict]:
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or set(doc) != {"schema", "entries"}:
        raise DataError(f"not a {SCHEMA} document with exactly the keys schema and entries")
    out = []
    for i, e in enumerate(doc.get("entries") or []):
        if not isinstance(e, dict) or tuple(e) != ENTRY_KEYS or version_key(e["version"]) is None:
            raise DataError(f"entries[{i}]: not {{{', '.join(ENTRY_KEYS)}}} with an X.Y.Z version")
        out.append(dict(e))
    return out


@functools.lru_cache(maxsize=1)
def load() -> tuple[dict, ...]:
    return tuple(parse(DATA_PATH.read_text(encoding="utf-8")))


def doctor_command(frm: str, to: str | None = None) -> str:
    return (f"lean-pkg doctor --upgrade-from v{frm}" + (f" --to v{to}" if to else "")
            + " --source DIR")


def _read(repo: pathlib.Path | None, rel) -> tuple[dict | None, str | None]:
    if not rel:
        return None, None
    if repo is None or not repo.is_dir():
        return None, "the checkout is missing"
    try:
        data = json.loads((repo / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"unreadable ({e.__class__.__name__})"
    return (data, None) if isinstance(data, dict) else (None, "not a JSON object")


def judge(role: str, rel: str, rec: dict | None, why: str | None, running: str, entries) -> dict:
    """One receipt's row: {role, receipt, producer (the lean_pkg string or "unknown"), producer_record (the
    receipt's `producer` object or null), direction, breaking[] (entry views), other_breaking[] (ids outside
    the evidence areas), level (warn|info), message, command}."""
    prod = rec.get("producer") if isinstance(rec, dict) else None
    ver = prod.get("lean_pkg") if isinstance(prod, dict) else None
    row = {"role": role, "receipt": rel, "readable": rec is not None,
           "producer": str(ver) if isinstance(ver, str) and ver.strip() else "unknown",
           "producer_record": prod if isinstance(prod, dict) else None, "direction": "unknown",
           "breaking": [], "other_breaking": [], "level": "info", "message": "", "command": None}
    what = f"{role} receipt {rel}"
    pk, rk = version_key(ver), version_key(running)
    if rec is None:
        row["message"] = f"{what}: producer unknown ({why or 'unreadable'})"
        return row
    if pk is None or rk is None:
        row["message"] = (f"{what}: producer unknown: {UNKNOWN_NOTE}" if row["producer"] == "unknown"
                          else f"{what}: producer unknown (version {row['producer']!r} is not X.Y.Z)")
        return row
    if pk == rk:
        row.update(direction="same", message=f"{what}: written by lean-pkg {ver}, the running version")
        return row
    if pk > rk:
        row.update(direction="newer", level="warn", command=doctor_command(running, ver),
                   message=(f"{what} was written by lean-pkg {ver}, newer than the running {running}: an older "
                            f"lean-pkg ignores evidence it does not know (for example a sealed slurm_sacct record "
                            f"that 0.19.0 and older never check) and cannot list what later releases changed; "
                            f"upgrade lean-pkg to {ver} or later before trusting gate, gate --check or receipt "
                            f"check on it, or read `{doctor_command(running, ver)}` in a lean-pkg clone"))
        return row
    later = [e for e in entries if version_key(e["version"]) > pk]
    hits = [e for e in later if evidence_entry(e)]
    row["breaking"] = [{"id": e["id"], "version": e["version"], "pr": e["pr"], "kind": e["kind"],
                        "area": e["area"]} for e in hits]
    row["other_breaking"] = [e["id"] for e in later if not evidence_entry(e)]
    row["direction"] = "older"
    row["command"] = doctor_command(ver)
    if hits:
        ids = ", ".join(f"{e['id']} ({e['version']}, #{e['pr']})" for e in hits)
        row.update(level="warn",
                   message=(f"{what} was written by lean-pkg {ver}; the running {running} carries "
                            f"{len(hits)} breaking change(s) to receipts, gate or claims since then: {ids}; "
                            f"read `{doctor_command(ver)}` (DIR: a lean-pkg clone; omit --source when this "
                            f"lean-pkg runs from its own checkout)"))
    else:
        other = (f" ({len(row['other_breaking'])} breaking change(s) elsewhere: "
                 f"{', '.join(row['other_breaking'])})" if row["other_breaking"] else "")
        row["message"] = (f"{what}: written by lean-pkg {ver}, older than the running {running}; no breaking "
                          f"change to receipts, gate or claims in between{other}")
    return row


def skew(pkg: dict, repo: pathlib.Path | None, running: str | None = None, entries=None) -> dict:
    """{running, receipts[] (judge rows), warnings[] (the WARN texts), skew (any warning), data_problem}.
    Never raises on a package or receipt; a malformed shipped list yields data_problem and no entries."""
    from . import __version__
    running = running or __version__
    problem = None
    if entries is None:
        try:
            entries = load()
        except (OSError, ValueError, yaml.YAMLError) as e:
            entries, problem = (), f"{DATA_PATH.name} could not be read ({e})"
    gate = (pkg or {}).get("gate") if isinstance((pkg or {}).get("gate"), dict) else {}
    rows = []
    for role, key in ROLES:
        rel = gate.get(key)
        if rel:
            rec, why = _read(repo, rel)
            rows.append(judge(role, str(rel), rec, why, running, entries))
    warnings = [r["message"] for r in rows if r["level"] == "warn"]
    return {"running": running, "receipts": rows, "warnings": warnings, "skew": bool(warnings),
            "data_problem": problem}


def warn_lines(result: dict) -> list[str]:
    out = [f"WARN: producer skew: {w}" for w in result["warnings"]]
    if result.get("data_problem"):
        out.append(f"WARN: producer skew: {result['data_problem']}")
    return out


def info_lines(result: dict) -> list[str]:
    """The text block `status` prints: one line per gate receipt (nothing when none is recorded)."""
    if not result["receipts"]:
        return []
    return ([f"producer (the lean-pkg that wrote each gate receipt; running {result['running']}):"]
            + [f"  {'WARN ' if r['level'] == 'warn' else ''}{r['message']}" for r in result["receipts"]])
