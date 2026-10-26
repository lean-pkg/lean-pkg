# lean-pkg toolchain advisories -- Lean toolchains with a known kernel or runtime soundness bug
"""The shipped list `data/toolchain-advisories.yaml` (schema `lean-pkg-toolchain-advisories/1`) and the
match of a toolchain string against it (issue #237).

A receipt made on an affected toolchain is weaker evidence than one made on the fixed release, so `gate`
(and `gate --check`), `receipt check` and `palomar check` print one WARN line per matching advisory and add
`toolchain_advisories[]` to their JSON. They never refuse because of it: the proofs may well be fine, and
independent kernel replay (comparator with nanoda) mitigates several of these bugs.

The file, validated strictly when loaded (closed key sets; any problem raises AdvisoryError):

    schema: lean-pkg-toolchain-advisories/1
    advisories:                       # a non-empty list
      - id: TEXT                      # unique; lower case letters, digits, '.', '-'
        summary: TEXT                 # one paragraph, what the bug lets through
        affected:
          below: vX.Y.Z               # every release or rc that sorts below this one (required)
          also: [vX.Y.Z-rcN, ...]     # releases at or above `below` that lack the fix anyway (optional)
          nightlies_through: DATE     # nightly-YYYY-MM-DD dated on or before DATE (optional)
        fixed_in: vX.Y.Z              # the first stable release with the fix; it must not be affected
        links: [https://..., ...]     # primary sources: the release notes, the lean4 issue and pull request
        date_added: YYYY-MM-DD

Toolchain strings: `[leanprover/lean4:]vX.Y.Z[-rcN]` (a release; an rc sorts before its release), and
`leanprover/lean4:nightly-YYYY-MM-DD` or `leanprover/lean4-nightly:nightly-YYYY-MM-DD` (a nightly). Anything
else (a channel name such as `stable`, a pull-request toolchain, a local build) matches no advisory.
"""

from __future__ import annotations

import datetime
import functools
import pathlib
import re

SCHEMA = "lean-pkg-toolchain-advisories/1"
DATA_PATH = pathlib.Path(__file__).resolve().parent / "data" / "toolchain-advisories.yaml"
TOP_KEYS = ("schema", "advisories")
ENTRY_KEYS = ("id", "summary", "affected", "fixed_in", "links", "date_added")
AFFECTED_KEYS = ("below", "also", "nightlies_through")
AFFECTED_REQUIRED = ("below",)
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
_RELEASE_RE = re.compile(r"^(?:leanprover/lean4:)?v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
_NIGHTLY_RE = re.compile(r"^leanprover/lean4(?:-nightly)?:nightly-(\d{4}-\d{2}-\d{2})$")
_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-rc\d+)?$")


class AdvisoryError(ValueError):
    """The advisories file is malformed (raised by load())."""


def release_key(text) -> tuple[int, int, int, int, int] | None:
    """Sort key of `[leanprover/lean4:]vX.Y.Z[-rcN]`, else None: (X, Y, Z, 0, N) for an rc, (X, Y, Z, 1, 0)
    for the release, so an rc sorts before its release (the same order as audit.parse_lean_toolchain)."""
    m = _RELEASE_RE.match(str(text or "").strip())
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch)) + ((0, int(rc)) if rc is not None else (1, 0))


def nightly_date(text) -> datetime.date | None:
    m = _NIGHTLY_RE.match(str(text or "").strip())
    if not m:
        return None
    try:
        return datetime.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _date(value, where: str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        raise AdvisoryError(f"{where}: {value!r} is not a date (YYYY-MM-DD)")
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    raise AdvisoryError(f"{where}: {value!r} is not a date (YYYY-MM-DD)")


def _version(value, where: str, stable: bool = False) -> str:
    if not isinstance(value, str) or not _VERSION_RE.match(value):
        raise AdvisoryError(f"{where}: {value!r} is not a Lean version vX.Y.Z[-rcN]")
    if stable and "-rc" in value:
        raise AdvisoryError(f"{where}: {value!r} is a release candidate; name the first stable release")
    return value


def _closed(d, keys: tuple[str, ...], required: tuple[str, ...], where: str) -> dict:
    if not isinstance(d, dict):
        raise AdvisoryError(f"{where}: expected a mapping, got {type(d).__name__}")
    unknown = sorted(str(k) for k in d if k not in keys)
    if unknown:
        raise AdvisoryError(f"{where}: unknown key(s) {', '.join(unknown)} (allowed: {', '.join(keys)})")
    missing = [k for k in required if k not in d]
    if missing:
        raise AdvisoryError(f"{where}: missing key(s) {', '.join(missing)}")
    return d


def validate(doc) -> list[dict]:
    """The advisories of a parsed file, normalized (dates as YYYY-MM-DD strings, `also` always a list,
    `nightlies_through` None when absent); AdvisoryError on the first problem."""
    _closed(doc, TOP_KEYS, TOP_KEYS, "toolchain advisories")
    if doc["schema"] != SCHEMA:
        raise AdvisoryError(f"toolchain advisories: schema {doc['schema']!r} is not {SCHEMA!r}")
    entries = doc["advisories"]
    if not isinstance(entries, list) or not entries:
        raise AdvisoryError("toolchain advisories: `advisories` must be a non-empty list")
    out, seen = [], set()
    for i, e in enumerate(entries):
        where = f"advisories[{i}]"
        _closed(e, ENTRY_KEYS, ENTRY_KEYS, where)
        aid = e["id"]
        if not isinstance(aid, str) or not _ID_RE.match(aid):
            raise AdvisoryError(f"{where}.id: {aid!r} is not lower case letters, digits, '.' and '-'")
        if aid in seen:
            raise AdvisoryError(f"{where}.id: {aid!r} is not unique")
        seen.add(aid)
        where = f"advisory {aid}"
        summary = e["summary"]
        if not isinstance(summary, str) or not summary.strip() or "\n" in summary.strip():
            raise AdvisoryError(f"{where}.summary: a non-empty, one-paragraph string is required")
        links = e["links"]
        if not isinstance(links, list) or not links:
            raise AdvisoryError(f"{where}.links: a non-empty list of https URLs is required")
        for u in links:
            if not isinstance(u, str) or not re.match(r"^https://[^\s<>()\[\]`|]+$", u):
                raise AdvisoryError(f"{where}.links: {u!r} is not an https URL")
        if len(set(links)) != len(links):
            raise AdvisoryError(f"{where}.links: a link is listed twice")
        aff = _closed(e["affected"], AFFECTED_KEYS, AFFECTED_REQUIRED, f"{where}.affected")
        below = _version(aff["below"], f"{where}.affected.below")
        also = aff.get("also", [])
        if not isinstance(also, list):
            raise AdvisoryError(f"{where}.affected.also: expected a list of Lean versions")
        also = [_version(v, f"{where}.affected.also") for v in also]
        if len(set(also)) != len(also):
            raise AdvisoryError(f"{where}.affected.also: a version is listed twice")
        for v in also:
            if release_key(v) < release_key(below):
                raise AdvisoryError(f"{where}.affected.also: {v} sorts below {below} and is affected already")
        nightly = aff.get("nightlies_through")
        nightly = _date(nightly, f"{where}.affected.nightlies_through").isoformat() if nightly is not None else None
        fixed = _version(e["fixed_in"], f"{where}.fixed_in", stable=True)
        entry = {"id": aid, "summary": " ".join(summary.split()), "fixed_in": fixed, "links": list(links),
                 "date_added": _date(e["date_added"], f"{where}.date_added").isoformat(),
                 "affected": {"below": below, "also": also, "nightlies_through": nightly}}
        if affected(entry, fixed):
            raise AdvisoryError(f"{where}.fixed_in: {fixed} is itself affected by the `affected` rule")
        out.append(entry)
    return out


def load_file(path: pathlib.Path) -> list[dict]:
    import yaml
    try:
        doc = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise AdvisoryError(f"toolchain advisories {path}: unreadable ({e})") from None
    return validate(doc)


@functools.lru_cache(maxsize=None)
def _load_cached(path: str) -> tuple[dict, ...]:
    return tuple(load_file(pathlib.Path(path)))


def load() -> list[dict]:
    """The shipped advisories (validated; cached per path)."""
    return list(_load_cached(str(DATA_PATH)))


def affected(entry: dict, toolchain) -> bool:
    """True when the toolchain string falls under the entry's `affected` rule."""
    aff = entry["affected"]
    key = release_key(toolchain)
    if key is not None:
        return key < release_key(aff["below"]) or any(key == release_key(v) for v in aff["also"])
    day = nightly_date(toolchain)
    if day is not None and aff["nightlies_through"]:
        return day <= datetime.date.fromisoformat(aff["nightlies_through"])
    return False


def matches(toolchain, advisories: list[dict] | None = None) -> list[dict]:
    """The JSON view of every advisory the toolchain string is affected by, in file order:
    {id, toolchain, summary, fixed_in, links, date_added}. An empty or unparsed toolchain matches none."""
    tc = str(toolchain or "").strip()
    if not tc:
        return []
    return [{"id": e["id"], "toolchain": tc, "summary": e["summary"], "fixed_in": e["fixed_in"],
             "links": list(e["links"]), "date_added": e["date_added"]}
            for e in (load() if advisories is None else advisories) if affected(e, tc)]


def warn_line(view: dict) -> str:
    """One WARN line for a matching advisory: the advisory, the toolchain, the fixed release, the link."""
    return (f"WARN: toolchain advisory {view['id']}: {view['toolchain']} has a known Lean soundness bug, "
            f"fixed in {view['fixed_in']}; {view['links'][0]} (a warning, not a refusal; a receipt made on "
            f"{view['fixed_in']} or later is stronger evidence)")


def lookup(toolchains) -> tuple[list[dict], str | None]:
    """(matches, problem) for one or more toolchain strings (duplicates collapse). A malformed shipped file
    never breaks a command: it yields no matches and a WARN line naming the problem."""
    if isinstance(toolchains, str) or toolchains is None:
        toolchains = [toolchains]
    try:
        out: list[dict] = []
        for tc in dict.fromkeys(str(t or "").strip() for t in toolchains):
            out += matches(tc)
    except AdvisoryError as e:
        return [], f"WARN: toolchain advisories could not be read ({e})"
    return out, None


def warn_lines(toolchains) -> tuple[list[dict], list[str]]:
    """(matches, the WARN lines to print): one line per match, or the one line naming a read problem."""
    found, problem = lookup(toolchains)
    return found, ([problem] if problem else [warn_line(v) for v in found])
