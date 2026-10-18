# lean-pkg stacks -- the declared LTS stacks (proposal 0004) and the check of a package against one
"""The LTS stacks lean-pkg declares (schema `lean-pkg-stack/1`, proposal 0004, docs/stacks/).

Source of truth: `docs/stacks/lts-YYYY.MM.yaml` in the lean-pkg repository, the reviewed files beside their
pages. The wheel ships a byte-identical copy of each under `lean_pkg/data/stacks/` (written by `make docs`;
tests/test_stacks.py fails when a copy differs or is missing), and this module reads only that packaged copy,
so an installed lean-pkg behaves the same inside and outside a lean-pkg checkout.

    declared     a stack whose `published` is set (declared after its matrix passed)
    window       published <= day <= supported_until
    current      the newest declared stack whose window covers today (none: no stack is current)

A package opts in with `stack: NAME` in its coverage.yaml. `check()` compares the package's committed pins
with the stack: `lean-toolchain`, the Mathlib commit in `lake-manifest.json` (against the stack's commit,
never only the tag name: a tag can be moved) and, when a comparator receipt is given, its toolchain digests
against `comparator_tools.binaries_sha256` (recorded for Linux x86_64). Every finding is a warning:
the stack key never makes a check pass or fail (proposal 0004, "How a package opts in" and "Trust model").
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import pathlib
import re

import yaml

SCHEMA = "lean-pkg-stack/1"
DATA_DIR = pathlib.Path(__file__).resolve().parent / "data" / "stacks"
DOCS_URL = "https://github.com/lean-pkg/lean-pkg/blob/main/docs/stacks/{name}.md"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TOOLCHAIN = re.compile(r"^leanprover/lean4:v(\d+)\.(\d+)\.(\d+)$")      # a release, never an rc or nightly
VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
NAME = re.compile(r"^lts-(\d{4})\.(04|10)$")                              # decided: April and October
RANGE = re.compile(r"^>=(\d+)\.(\d+)\.(\d+),<(\d+)\.(\d+)$")
CHOICES = ("current", "none")                                             # `new --stack` besides a name

TOP_KEYS = {"schema", "name", "defined", "published", "lean_toolchain", "lean_commit", "mathlib", "lean_pkg",
            "palomar_floor", "comparator_tools", "supported_until", "successor", "tested", "migration_cases",
            "advisories"}
MATHLIB_KEYS = {"rev", "commit"}
FLOOR_KEYS = {"minimum", "satisfied"}
TOOLS_KEYS = {"mechanism", "sources", "release_asset", "binaries_sha256"}
SOURCE_KEYS = {"lake_comparator", "leanexport", "leanchecker", "nanoda_lib", "con_ron"}
ASSET_KEYS = {"name", "sha256"}
TESTED_KEYS = {"package", "commit", "moved", "build", "result"}
MIGRATION_KEYS = {"package", "commit", "reason", "to_move"}
RESULTS = {"verified", "built", "migration", "pending", "blocked", "failed"}


class StackError(ValueError):
    """A shipped stack file is malformed (a packaging bug: tests/test_stacks.py refuses it first)."""


def version_key(v: str) -> tuple[int, int, int, int]:
    m = VERSION.match(v)
    if not m:
        raise ValueError(v)
    a, b, c, rc = m.groups()
    return int(a), int(b), int(c), int(rc) if rc else 1 << 30      # a release sorts after its candidates


def digest_keys() -> set[str]:
    from . import cli as core
    return {k.removesuffix("_sha256") for k in core.COMPARATOR_TOOLCHAIN_DIGESTS}


def validate(doc) -> list[str]:
    """Problems of one stack document; [] when it is a valid lean-pkg-stack/1 file (closed keys)."""
    p: list[str] = []

    def closed(where: str, d, keys: set[str]) -> bool:
        if not isinstance(d, dict):
            p.append(f"{where}: not a mapping")
            return False
        if set(d) != keys:
            p.append(f"{where}: keys {sorted(set(d) ^ keys)} differ from the schema")
            return False
        return True

    if not closed("stack", doc, TOP_KEYS):
        return p
    if doc["schema"] != SCHEMA:
        p.append(f"schema: {doc['schema']!r}")
    if not isinstance(doc["name"], str) or not NAME.match(doc["name"]):
        p.append(f"name: {doc['name']!r} is not lts-YYYY.04 or lts-YYYY.10")
    for k in ("defined", "supported_until"):
        if not isinstance(doc[k], dt.date):
            p.append(f"{k}: not a date")
    if doc["published"] is not None and not isinstance(doc["published"], dt.date):
        p.append("published: neither null nor a date")
    m = TOOLCHAIN.match(str(doc["lean_toolchain"]))
    if not m:
        p.append(f"lean_toolchain: {doc['lean_toolchain']!r} is not a Lean release")
    if not HEX40.match(str(doc["lean_commit"])):
        p.append("lean_commit: not a 40-hex commit")
    if closed("mathlib", doc["mathlib"], MATHLIB_KEYS):
        rev = str(doc["mathlib"]["rev"])
        if not VERSION.match(rev) or "-rc" in rev:
            p.append(f"mathlib.rev: {rev!r} is not a release tag")
        elif m and rev != "v" + ".".join(m.groups()):
            p.append(f"mathlib.rev: {rev} is not the tag of the toolchain")
        if not HEX40.match(str(doc["mathlib"]["commit"])):
            p.append("mathlib.commit: not a 40-hex commit")
    if not RANGE.match(str(doc["lean_pkg"])):
        p.append(f"lean_pkg: {doc['lean_pkg']!r} is not a >=X.Y.Z,<A.B range")
    if closed("palomar_floor", doc["palomar_floor"], FLOOR_KEYS) and m:
        floor = str(doc["palomar_floor"]["minimum"])
        ok = VERSION.match(floor) is not None and version_key(floor) <= version_key("v" + ".".join(m.groups()))
        if doc["palomar_floor"]["satisfied"] is not ok:
            p.append(f"palomar_floor.satisfied: {doc['palomar_floor']['satisfied']} but {floor} vs the toolchain "
                     f"says {ok}")
    t = doc["comparator_tools"]
    if closed("comparator_tools", t, TOOLS_KEYS):
        if t["mechanism"] not in ("toolchain", "legacy"):
            p.append(f"comparator_tools.mechanism: {t['mechanism']!r}")
        if closed("comparator_tools.sources", t["sources"], SOURCE_KEYS):
            for k, v in t["sources"].items():
                if v != "in-tree" and not HEX40.match(str(v)):
                    p.append(f"comparator_tools.sources.{k}: neither in-tree nor a 40-hex commit")
        if closed("comparator_tools.release_asset", t["release_asset"], ASSET_KEYS):
            if not HEX64.match(str(t["release_asset"]["sha256"])):
                p.append("comparator_tools.release_asset.sha256: not a SHA-256")
        if closed("comparator_tools.binaries_sha256", t["binaries_sha256"], digest_keys()):
            for k, v in t["binaries_sha256"].items():
                if not HEX64.match(str(v)):
                    p.append(f"comparator_tools.binaries_sha256.{k}: not a SHA-256")
    if doc["successor"] is not None and not NAME.match(str(doc["successor"])):
        p.append(f"successor: {doc['successor']!r}")
    if not isinstance(doc["tested"], list) or not doc["tested"]:
        p.append("tested: not a non-empty list")
    else:
        for i, e in enumerate(doc["tested"]):
            if closed(f"tested[{i}]", e, TESTED_KEYS):
                if e["result"] not in RESULTS:
                    p.append(f"tested[{i}].result: {e['result']!r}")
                if e["commit"] is not None and not HEX40.match(str(e["commit"])):
                    p.append(f"tested[{i}].commit: not a 40-hex commit")
        if doc["published"] is not None and any(e.get("result") in ("pending", "blocked", "failed")
                                                for e in doc["tested"] if isinstance(e, dict)):
            p.append("published: set while a matrix entry is pending, blocked or failed (declared after the "
                     "matrix passes)")
    if not isinstance(doc["migration_cases"], list):
        p.append("migration_cases: not a list")
    else:
        for i, e in enumerate(doc["migration_cases"]):
            if closed(f"migration_cases[{i}]", e, MIGRATION_KEYS):
                if not HEX40.match(str(e["commit"])):
                    p.append(f"migration_cases[{i}].commit: not a 40-hex commit")
                for k in ("reason", "to_move"):
                    if not str(e[k] or "").strip():
                        p.append(f"migration_cases[{i}].{k}: empty")
    if not isinstance(doc["advisories"], list):
        p.append("advisories: not a list")
    return p


def load_dir(d: pathlib.Path) -> dict[str, dict]:
    """Every lts-*.yaml of d, validated, by name (file order = name order = chronological)."""
    out: dict[str, dict] = {}
    for f in sorted(d.glob("lts-*.yaml")):
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            raise StackError(f"stack file {f.name}: unreadable ({e})") from None
        problems = validate(doc)
        if problems:
            raise StackError(f"stack file {f.name}: {'; '.join(problems)}")
        if f.stem != doc["name"]:
            raise StackError(f"stack file {f.name}: names {doc['name']}")
        out[doc["name"]] = doc
    return out


@functools.lru_cache(maxsize=None)
def _cached(d: str) -> tuple[tuple[str, dict], ...]:
    return tuple(load_dir(pathlib.Path(d)).items())


def load() -> dict[str, dict]:
    """The stacks this lean-pkg ships (all of them, declared or not), by name."""
    return dict(_cached(str(DATA_DIR)))


def today() -> dt.date:
    return dt.date.today()


def declared(stacks: dict[str, dict] | None = None) -> dict[str, dict]:
    """The declared stacks (published set), by name."""
    return {n: s for n, s in (load() if stacks is None else stacks).items() if s["published"] is not None}


def window(stack: dict, day: dt.date | None = None) -> str:
    """'before' (not yet published, or published after day), 'in', or 'past' (after supported_until)."""
    day = day or today()
    if stack["published"] is None or day < stack["published"]:
        return "before"
    return "in" if day <= stack["supported_until"] else "past"


def current(day: dt.date | None = None, stacks: dict[str, dict] | None = None) -> dict | None:
    """The newest declared stack whose window covers day (default today); None when there is none."""
    inside = [s for s in declared(stacks).values() if window(s, day) == "in"]
    return max(inside, key=lambda s: (s["published"], s["name"])) if inside else None


def declared_list(stacks: dict[str, dict] | None = None) -> str:
    d = declared(stacks)
    return ", ".join(f"{n} ({s['published']} to {s['supported_until']})" for n, s in d.items()) or "(none)"


def resolve(value: str, day: dt.date | None = None) -> dict | None:
    """`new --stack VALUE`: a declared stack's name, `current` or `none`. UsageError otherwise."""
    from . import cli as core
    if value == "none":
        return None
    if value == "current":
        cur = current(day)
        if cur is None:
            raise core.UsageError(f"--stack current: no declared stack covers {day or today()}; declared: "
                                  f"{declared_list()} (name one, or --stack none)")
        return cur
    d = declared()
    if value not in d:
        undeclared = " (defined but not declared yet)" if value in load() else ""
        raise core.UsageError(f"--stack {value!r}{undeclared}: not a declared stack; declared: {declared_list()}; "
                              f"or current, none")
    return d[value]


def toolchain_version(stack: dict) -> str:
    return stack["lean_toolchain"].split(":", 1)[1]


def page_url(name: str) -> str:
    return DOCS_URL.format(name=name)


def _range_ok(rng: str, version: str) -> bool | None:
    m = RANGE.match(rng)
    v = re.match(r"^(\d+)\.(\d+)\.(\d+)", version or "")
    if not m or not v:
        return None
    lo = tuple(int(x) for x in m.groups()[:3])
    hi = tuple(int(x) for x in m.groups()[3:])
    have = tuple(int(x) for x in v.groups())
    return lo <= have and have[:2] < hi


def _manifest_mathlib(blob: bytes | None) -> dict | None:
    """{rev, input_rev} of the mathlib package in a lake-manifest.json; {} when it lists none; None when the
    manifest is absent or unreadable."""
    try:
        data = json.loads(blob.decode("utf-8")) if blob is not None else None
    except (ValueError, UnicodeDecodeError):
        return None
    pkgs = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(pkgs, list):
        return None
    for p in pkgs:
        if isinstance(p, dict) and str(p.get("name", "")).casefold() in ("mathlib", "«mathlib»"):
            return {"rev": str(p.get("rev") or "") or None, "input_rev": str(p.get("inputRev") or "") or None}
    return {}


def declared_key(repo: pathlib.Path, rev: str = "HEAD") -> str | None:
    """The `stack:` value of coverage.yaml committed at rev (None: no file, no key, or unreadable YAML). Read
    loosely: the stack check never depends on the rest of the coverage file being valid."""
    from . import cli as core
    blob = core.git_show_bytes(repo, "coverage.yaml", rev)
    if blob is None:
        return None
    try:
        doc = yaml.safe_load(blob.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    v = doc.get("stack") if isinstance(doc, dict) else None
    return str(v) if v is not None else None


def check(repo: pathlib.Path, name: str, rev: str = "HEAD", comparator: dict | None = None,
          comparator_path: str | None = None, day: dt.date | None = None,
          stacks: dict[str, dict] | None = None) -> dict:
    """The package at repo (committed at rev) against the stack it declares. The JSON view:
    {name, declared, matches, supported_until, window, toolchain, mathlib, comparator_tools, lean_pkg,
    mismatches[], warnings[]}. matches: the toolchain and the Mathlib commit both match (None when the stack
    is not declared here); comparator digests and the window only warn."""
    from . import __version__
    from . import cli as core
    d = declared(stacks)
    out: dict = {"name": name, "declared": name in d, "matches": None, "supported_until": None, "window": None,
                 "toolchain": None, "mathlib": None, "comparator_tools": None, "lean_pkg": None,
                 "mismatches": [], "warnings": []}
    if name not in d:
        out["warnings"].append(f"coverage.yaml declares stack {name}, which lean-pkg {__version__} does not "
                               f"declare (declared: {declared_list(stacks)}); the pins are not checked")
        return out
    s = d[name]
    out["supported_until"] = s["supported_until"].isoformat()
    out["window"] = window(s, day)
    if out["window"] == "past":
        out["warnings"].append(f"stack {name} is past its window (supported until {s['supported_until']}); "
                               f"move to {s['successor'] or 'a newer stack'} (docs/stacks/README.md)")
    elif out["window"] == "before":
        out["warnings"].append(f"stack {name}'s window starts {s['published']}")
    in_range = _range_ok(s["lean_pkg"], __version__)
    out["lean_pkg"] = {"range": s["lean_pkg"], "version": __version__, "in_range": in_range}
    if in_range is False:
        out["warnings"].append(f"lean-pkg {__version__} is outside stack {name}'s supported range {s['lean_pkg']}")
    tc_blob = core.git_show_bytes(repo, "lean-toolchain", rev)
    tc = tc_blob.decode("utf-8", "replace").strip() if tc_blob is not None else None
    tc_ok = tc == s["lean_toolchain"]
    out["toolchain"] = {"stack": s["lean_toolchain"], "package": tc, "matches": tc_ok}
    if not tc_ok:
        out["mismatches"].append(f"lean-toolchain is {tc or '(not committed)'}, the stack pins {s['lean_toolchain']}")
    ml = _manifest_mathlib(core.git_show_bytes(repo, "lake-manifest.json", rev))
    want_rev, want_commit = s["mathlib"]["rev"], s["mathlib"]["commit"]
    if ml == {}:   # a package without Mathlib: the toolchain is the whole pin
        out["mathlib"] = {"stack_rev": want_rev, "stack_commit": want_commit, "package": None, "input_rev": None,
                          "matches": None}
        ml_ok = True
    else:
        commit = (ml or {}).get("rev")
        ml_ok = commit == want_commit
        out["mathlib"] = {"stack_rev": want_rev, "stack_commit": want_commit, "package": commit,
                          "input_rev": (ml or {}).get("input_rev"), "matches": ml_ok}
        if ml is None:
            out["mismatches"].append("lake-manifest.json is not committed (or unreadable): the Mathlib pin is "
                                     "unknown")
        elif not ml_ok:
            shown = f"{commit[:12]}" if commit else "(no rev)"
            out["mismatches"].append(f"Mathlib is at {shown} (input rev {(ml or {}).get('input_rev')}), the stack "
                                     f"pins {want_rev} = {want_commit[:12]}")
    out["matches"] = tc_ok and ml_ok
    if comparator is not None:
        out["comparator_tools"] = comparator_check(s, comparator, comparator_path)
        if out["comparator_tools"]["differ"]:
            which = f"the comparator receipt {comparator_path}" if comparator_path else "the comparator receipt"
            out["warnings"].append(f"{which} records tool digests that differ from stack {name}'s "
                                   f"({', '.join(out['comparator_tools']['differ'])}); the stack's digests are "
                                   f"those of Linux x86_64")
    for m in out["mismatches"]:
        out["warnings"].append(f"stack {name}: {m}")
    return out


def comparator_check(stack: dict, comp: dict, path: str | None = None) -> dict:
    """A comparator receipt's toolchain digests against the stack's binaries_sha256: {receipt, mechanism,
    matches, differ[]} (differ: digest keys that differ or are missing; a legacy receipt differs as a whole)."""
    want = stack["comparator_tools"]["binaries_sha256"]
    mech = comp.get("mechanism") or "legacy"
    tools = comp.get("tools") if isinstance(comp.get("tools"), dict) else {}
    if mech != stack["comparator_tools"]["mechanism"]:
        differ = ["mechanism"]
    else:
        differ = sorted(f"{k}_sha256" for k, v in want.items() if tools.get(f"{k}_sha256") != v)
    return {"receipt": path, "mechanism": mech, "matches": not differ, "differ": differ}


def summary_line(res: dict) -> tuple[str, str]:
    """(level, detail) for a doctor line or a verify note."""
    if not res["declared"]:
        return "warn", res["warnings"][0]
    head = f"on stack {res['name']} (supported until {res['supported_until']})"
    if res["matches"] and not res["warnings"]:
        return "ok", head
    if res["matches"]:
        return "warn", head + "; " + "; ".join(res["warnings"])
    return "warn", f"declares stack {res['name']} but its pins differ: " + "; ".join(res["mismatches"]) + (
        "" if len(res["warnings"]) == len(res["mismatches"]) else
        "; " + "; ".join(w for w in res["warnings"] if not w.startswith(f"stack {res['name']}: ")))
