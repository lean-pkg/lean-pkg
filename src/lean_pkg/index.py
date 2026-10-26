# lean-pkg index -- the public, forkable catalog of trust-graded packages
"""Index entries: `index/<name>/entry.yaml` (schema `index-entry/2`; `index-entry/1`
still validates) plus verbatim receipt copies under `index/<name>/receipts/`.
`index-entry/2` adds `claims[]`, one claim per headline item, derived at `index add`.

Everything is a file. `index add` writes an entry from a package clone at its
pin; `index check` re-derives every claim from bytes at the pin; `index render`
writes the Markdown table. Trust is computed from evidence, never typed.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.error
import urllib.request

import yaml

from . import cli
from . import core
from . import lean_scan


ENTRY_SCHEMA = "index-entry/2"
SUPPORTED_ENTRY_SCHEMAS = ("index-entry/1", ENTRY_SCHEMA)
INDEX_ENV = "LEAN_PKG_INDEX"
DEFAULT_INDEX_DIR = "index"
ENTRY_FILE = "entry.yaml"
RECEIPTS_DIR = "receipts"
ENTRY_KEYS_V1 = ("schema", "name", "repo", "pin", "lean_toolchain", "mathlib_rev",
                 "license_spdx", "trust", "receipts", "headline", "palomar", "doi",
                 "reservoir", "formalization_yaml", "fields", "consent", "added", "notes")
ENTRY_KEYS = ENTRY_KEYS_V1[:10] + ("claims",) + ENTRY_KEYS_V1[10:]
ENTRY_KEYS_BY_SCHEMA = {"index-entry/1": ENTRY_KEYS_V1, "index-entry/2": ENTRY_KEYS}
# Optional keys (proposal 0007): absent is valid; when present they sit at this place in the key order.
ENTRY_OPTIONAL_KEYS = {"index-entry/2": ("built_on",)}
ENTRY_KEY_ORDER = {"index-entry/1": ENTRY_KEYS_V1,
                   "index-entry/2": ENTRY_KEYS[:ENTRY_KEYS.index("formalization_yaml") + 1] + ("built_on",)
                   + ENTRY_KEYS[ENTRY_KEYS.index("formalization_yaml") + 1:]}
BUILT_ON_KEYS = ("name", "url", "authors", "license", "pin", "relation", "note")
BUILT_ON_REQUIRED = ("name", "url", "authors")
BUILT_ON_RELATIONS = ("depends-on", "adapted-from")
INDEX_CLAIM_KEYS = ("id", "decl", "kind", "summary", "judged_as", "trust")
TRUST_LEVELS = ("lower-tier", "comparator", "palomar")
TRUST_RANK = {t: i for i, t in enumerate(TRUST_LEVELS, 1)}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HTTPS_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~/%+-]+$")
PALOMAR_ID_RE = re.compile(r"^PALOMAR-\d{4}-\d{2}-\d{2}-\d{6}$")
FIELD_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PRIVATE_PATH_RE = re.compile(r"(^[/~])|(/home/)|(/Users/)|([A-Za-z]:\\)")


# --------------------------------------------------------------------------
# paths

def index_source(args) -> tuple[pathlib.Path, str]:
    """(index root, source): --index "flag", else $LEAN_PKG_INDEX "env", else <registry dir>/index when that
    directory exists "registry-sibling", else ./index "cwd"."""
    if getattr(args, "index", None):
        return pathlib.Path(args.index).expanduser(), "flag"
    env = os.environ.get(INDEX_ENV, "").strip()
    if env:
        return pathlib.Path(env).expanduser(), "env"
    beside = core.registry_home_dir(args) / DEFAULT_INDEX_DIR
    if beside.is_dir():
        return beside, "registry-sibling"
    return pathlib.Path.cwd() / DEFAULT_INDEX_DIR, "cwd"


def index_root(args) -> pathlib.Path:
    """--index, else $LEAN_PKG_INDEX, else <registry dir>/index if it exists, else ./index."""
    return index_source(args)[0]


def existing_index_root(args, *, create: bool = False) -> pathlib.Path:
    """The index root for check / render / migrate: a missing root is a refusal (exit 1), not an empty
    pass; create=True (render with an explicit --index or --out) makes it instead."""
    root = index_root(args)
    if not root.is_dir() and not create:
        raise core.Fail(f"no index at {root} (pass --index or set {INDEX_ENV})")
    return root


def entry_names(root: pathlib.Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / ENTRY_FILE).is_file())


# --------------------------------------------------------------------------
# schema: load, validate, dump

def load_entry(path: pathlib.Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise core.Fail(f"index entry not found: {path}") from None
    except UnicodeDecodeError as e:
        raise core.Fail(f"{path}: not UTF-8 text ({e.reason} at byte {e.start})") from e
    except OSError as e:
        raise core.Fail(f"{path}: unreadable: {e.strerror or e}") from e
    except yaml.YAMLError as e:
        raise core.Fail(f"{path}: not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise core.Fail(f"{path}: top level is not a mapping")
    return data


def entry_keys(entry: dict) -> tuple[str, ...]:
    """The key order of the entry's own schema (the current schema when it is unknown)."""
    return ENTRY_KEYS_BY_SCHEMA.get(entry.get("schema"), ENTRY_KEYS)


def index_claims(name: str, headline: list[dict], trust) -> list[dict]:
    """claims[] of an index-entry/2: one per headline item, derived, never typed."""
    from .claims import slug_of_decl
    return [{"id": f"{name}/{slug_of_decl(h['name'])}", "decl": h["name"], "kind": "theorem",
             "summary": h["summary"], "judged_as": h.get("judged_as"), "trust": trust}
            for h in headline]


def entry_optional_keys(entry: dict) -> tuple[str, ...]:
    return ENTRY_OPTIONAL_KEYS.get(entry.get("schema"), ())


def dump_entry(entry: dict) -> str:
    optional = entry_optional_keys(entry)
    order = ENTRY_KEY_ORDER.get(entry.get("schema"), ENTRY_KEY_ORDER[ENTRY_SCHEMA])
    ordered = {k: entry[k] for k in order if k in entry or k not in optional}
    return yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)


def _is_rel(p) -> bool:
    if not isinstance(p, str) or not p.strip():
        return False
    pp = pathlib.PurePosixPath(p)
    return not pp.is_absolute() and ".." not in pp.parts and not p.startswith("~")


def _strings(obj, where="entry"):
    if isinstance(obj, str):
        yield where, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{where}[{i}]")


def _exact_keys(obj, keys, where, errors) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where} must be a mapping with keys {list(keys)}")
        return False
    extra = sorted(set(obj) - set(keys))
    missing = [k for k in keys if k not in obj]
    if extra:
        errors.append(f"{where}: unknown key(s) {extra}")
    if missing:
        errors.append(f"{where}: missing key(s) {missing}")
    return not extra and not missing


def validate_entry(entry, dirname: str | None = None) -> list[str]:
    """Every error in an `index-entry/1` or `/2` mapping; [] when valid. Unknown keys are refused."""
    errors: list[str] = []
    if not isinstance(entry, dict):
        return ["entry is not a mapping"]
    keys = entry_keys(entry)
    if entry.get("schema") not in SUPPORTED_ENTRY_SCHEMAS:
        errors.append(f"schema must be one of {SUPPORTED_ENTRY_SCHEMAS}, got {entry.get('schema')!r}")
    optional = entry_optional_keys(entry)
    extra = sorted(set(entry) - set(keys) - set(optional))
    if extra:
        errors.append(f"unknown key(s) {extra} (schema {entry.get('schema')} allows {list(keys)}"
                      + (f", optionally {list(optional)}" if optional else "") + ")")
    missing = [k for k in keys if k not in entry]
    if missing:
        errors.append(f"missing key(s) {missing}")
        return errors
    name = entry["name"]
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        errors.append(f"name {name!r} must match [A-Za-z0-9][A-Za-z0-9._-]*")
    elif dirname is not None and name != dirname:
        errors.append(f"name {name!r} differs from its directory {dirname!r}")
    if not isinstance(entry["repo"], str) or not HTTPS_RE.match(entry["repo"]):
        errors.append(f"repo must be a public https URL without credentials, got {entry['repo']!r}")
    if _exact_keys(entry["pin"], ("commit", "tag"), "pin", errors):
        if not core._HEX40.fullmatch(str(entry["pin"]["commit"])):
            errors.append(f"pin.commit must be 40 lowercase hex, got {entry['pin']['commit']!r}")
        if entry["pin"]["tag"] is not None and not (isinstance(entry["pin"]["tag"], str)
                                                    and entry["pin"]["tag"].strip()):
            errors.append("pin.tag must be a non-empty string or null")
    for k in ("lean_toolchain", "license_spdx"):
        if not isinstance(entry[k], str) or not entry[k].strip():
            errors.append(f"{k} must be a non-empty string")
    if entry["mathlib_rev"] is not None and not (isinstance(entry["mathlib_rev"], str)
                                                 and entry["mathlib_rev"].strip()):
        errors.append("mathlib_rev must be a non-empty string or null")
    if entry["trust"] not in TRUST_LEVELS:
        errors.append(f"trust must be one of {TRUST_LEVELS}, got {entry['trust']!r}")
    if _exact_keys(entry["receipts"], ("lower", "comparator"), "receipts", errors):
        for k, v in entry["receipts"].items():
            if v is not None and not (_is_rel(v) and v.startswith(".lean-receipts/")
                                      and v.endswith(".json")):
                errors.append(f"receipts.{k} must be null or a .lean-receipts/*.json path, got {v!r}")
    hl = entry["headline"]
    if not isinstance(hl, list) or not hl:
        errors.append("headline must be a non-empty list")
    else:
        for i, h in enumerate(hl):
            if not isinstance(h, dict):
                errors.append(f"headline[{i}] must be a mapping")
                continue
            extra_h = sorted(set(h) - {"name", "file", "summary", "judged_as"})
            missing_h = [k for k in ("name", "file", "summary") if k not in h]
            if extra_h:
                errors.append(f"headline[{i}]: unknown key(s) {extra_h}")
            if missing_h:
                errors.append(f"headline[{i}]: missing key(s) {missing_h}")
            if extra_h or missing_h:
                continue
            if "judged_as" in h and h["judged_as"] is not None and not (
                    isinstance(h["judged_as"], str) and h["judged_as"].strip() and " " not in h["judged_as"]):
                errors.append(f"headline[{i}].judged_as must be null or the Challenge theorem name")
            if not isinstance(h["name"], str) or not h["name"].strip() or " " in h["name"]:
                errors.append(f"headline[{i}].name must be a fully qualified Lean name")
            if not (_is_rel(h["file"]) and h["file"].endswith(".lean")):
                errors.append(f"headline[{i}].file must be a repo-relative .lean path")
            s = h["summary"]
            if not isinstance(s, str) or not s.strip() or "\n" in s.strip() or len(s) > 300:
                errors.append(f"headline[{i}].summary must be one sentence (one line, at most 300 characters)")
    if "claims" in keys:
        errors += _index_claim_errors(entry, errors)
    pal = entry["palomar"]
    if not isinstance(pal, list):
        errors.append("palomar must be a list")
    else:
        for i, p in enumerate(pal):
            if not _exact_keys(p, ("id", "version"), f"palomar[{i}]", errors):
                continue
            if not PALOMAR_ID_RE.match(str(p["id"])):
                errors.append(f"palomar[{i}].id must look like PALOMAR-YYYY-MM-DD-NNNNNN, got {p['id']!r}")
            if isinstance(p["version"], bool) or not isinstance(p["version"], int) or p["version"] < 1:
                errors.append(f"palomar[{i}].version must be a positive integer")
    doi = entry["doi"]
    if doi is not None and _exact_keys(doi, ("concept", "versions"), "doi", errors):
        if not isinstance(doi["versions"], list):
            errors.append("doi.versions must be a list")
        else:
            for i, v in enumerate(doi["versions"]):
                if _exact_keys(v, ("doi", "tag", "commit"), f"doi.versions[{i}]", errors) \
                        and not core._HEX40.fullmatch(str(v["commit"])):
                    errors.append(f"doi.versions[{i}].commit must be 40 lowercase hex")
    if entry["reservoir"] is not None and not core.RESERVOIR_URL_RE.match(str(entry["reservoir"])):
        errors.append("reservoir must be null or https://reservoir.lean-lang.org/@<owner>/<name>")
    if entry["formalization_yaml"] is not None and not _is_rel(entry["formalization_yaml"]):
        errors.append("formalization_yaml must be null or a repo-relative path")
    if "built_on" in entry and "built_on" in optional:
        errors += built_on_errors(entry["built_on"])
    if not isinstance(entry["fields"], list) or not all(
            isinstance(f, str) and FIELD_TAG_RE.match(f) for f in entry["fields"]):
        errors.append("fields must be a list of lowercase tags ([a-z0-9-])")
    if _exact_keys(entry["consent"], ("recorded",), "consent", errors) \
            and not isinstance(entry["consent"]["recorded"], bool):
        errors.append("consent.recorded must be true or false (pointers stay private)")
    if _exact_keys(entry["added"], ("at", "by"), "added", errors):
        for k in ("at", "by"):
            if not isinstance(entry["added"][k], str) or not entry["added"][k].strip():
                errors.append(f"added.{k} must be a non-empty string")
    if not isinstance(entry["notes"], str):
        errors.append("notes must be a string")
    for where, s in _strings(entry):
        if where in ("entry.repo", "entry.reservoir") or ".doi" in where or \
                (where.startswith("entry.built_on[") and where.endswith("].url")):
            continue
        if PRIVATE_PATH_RE.search(s):
            errors.append(f"{where} looks like a private local path: {s!r}")
    return errors


def _one_line(v, limit: int = 300) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "\n" not in v.strip() and len(v) <= limit


def built_on_errors(credits) -> list[str]:
    """The optional `built_on` list of an index-entry/2 (proposal 0007): third-party Lean code the package
    depends on or adapts. Closed keys; name, url (public https) and authors (non-empty strings) required;
    license, pin (40 hex), relation (depends-on | adapted-from) and note optional, omitted rather than null."""
    if not isinstance(credits, list) or not credits:
        return ["built_on must be a non-empty list of credits (omit the key when there is none)"]
    out: list[str] = []
    names: set[str] = set()
    for i, c in enumerate(credits):
        w = f"built_on[{i}]"
        if not isinstance(c, dict):
            out.append(f"{w} must be a mapping with keys {list(BUILT_ON_REQUIRED)} and optionally "
                       f"{[k for k in BUILT_ON_KEYS if k not in BUILT_ON_REQUIRED]}")
            continue
        extra = sorted(set(c) - set(BUILT_ON_KEYS), key=str)
        missing = [k for k in BUILT_ON_REQUIRED if k not in c]
        if extra:
            out.append(f"{w}: unknown key(s) {extra} (allowed: {list(BUILT_ON_KEYS)})")
        if missing:
            out.append(f"{w}: missing key(s) {missing}")
        for k in BUILT_ON_KEYS:
            if k in c and c[k] is None:
                out.append(f"{w}.{k} must not be null (omit an optional key instead)")
        if "name" in c and c["name"] is not None:
            if not _one_line(c["name"], 100):
                out.append(f"{w}.name must be a non-empty one-line string (at most 100 characters)")
            elif c["name"].strip() in names:
                out.append(f"{w}.name {c['name']!r} is listed twice")
            else:
                names.add(c["name"].strip())
        if "url" in c and c["url"] is not None and not (isinstance(c["url"], str) and HTTPS_RE.match(c["url"])):
            out.append(f"{w}.url must be a public https URL without credentials, got {c['url']!r}")
        if "authors" in c and c["authors"] is not None and not (
                isinstance(c["authors"], list) and c["authors"]
                and all(_one_line(a, 200) for a in c["authors"])):
            out.append(f"{w}.authors must be a non-empty list of non-empty one-line strings")
        if c.get("license") is not None and not _one_line(c["license"], 100):
            out.append(f"{w}.license must be a non-empty one-line string (an SPDX expression)")
        if c.get("pin") is not None and not core._HEX40.fullmatch(str(c["pin"])):
            out.append(f"{w}.pin must be a 40-hex commit, got {c['pin']!r}")
        if c.get("relation") is not None and c["relation"] not in BUILT_ON_RELATIONS:
            out.append(f"{w}.relation must be one of {list(BUILT_ON_RELATIONS)}, got {c['relation']!r}")
        if c.get("note") is not None and not _one_line(c["note"]):
            out.append(f"{w}.note must be one line (at most 300 characters)")
    return out


def normalize_built_on(credits) -> list[dict]:
    """Credits with their keys in the documented order (unknown keys kept, so validation reports them)."""
    if not isinstance(credits, list):
        return credits
    out = []
    for c in credits:
        if isinstance(c, dict):
            c = {**{k: c[k] for k in BUILT_ON_KEYS if k in c}, **{k: v for k, v in c.items() if k not in BUILT_ON_KEYS}}
        out.append(c)
    return out


def load_built_on_file(path: str) -> list[dict]:
    """`--built-on-file`: YAML holding a list of credits, or a mapping whose only key is built_on (so a
    block copied from an entry.yaml works). An empty list means no credits."""
    p = pathlib.Path(path).expanduser()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise core.Fail(f"--built-on-file {path}: unreadable: {e.strerror or e}") from e
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise core.Fail(f"--built-on-file {path}: not valid YAML: {e}") from e
    if isinstance(data, dict) and set(data) == {"built_on"}:
        data = data["built_on"]
    if data is None:
        data = []
    if not isinstance(data, list):
        raise core.Fail(f"--built-on-file {path}: must hold a list of credits (or a mapping with the single "
                        f"key built_on)")
    errors = built_on_errors(data) if data else []
    if errors:
        raise core.Fail(f"--built-on-file {path}: " + "; ".join(errors))
    return normalize_built_on(data)


def _git_url_key(url: str) -> str:
    u = str(url).strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower()


def built_on_manifest_notes(clone: pathlib.Path, entry: dict) -> list[str]:
    """Advisory, never a failure: each credit with a pin, compared with lake-manifest.json at the pin.
    A depends-on (or unlabelled) credit whose url is not a manifest package is reported; an adapted-from
    credit is vendored source, so its absence from the manifest is expected."""
    credits = entry.get("built_on") or []
    if not any(isinstance(c, dict) and c.get("pin") for c in credits):
        return []
    mf = clone / "lake-manifest.json"
    try:
        pkgs = (json.loads(mf.read_text()).get("packages") or []) if mf.is_file() else []
    except ValueError:
        return ["warning: built_on pins not checked: lake-manifest.json at the pin is not valid JSON"]
    revs = {_git_url_key(p.get("url", "")): str(p.get("rev")) for p in pkgs if isinstance(p, dict) and p.get("url")}
    out = []
    for i, c in enumerate(credits):
        if not (isinstance(c, dict) and c.get("pin")):
            continue
        rev = revs.get(_git_url_key(c["url"]))
        if rev is None:
            if c.get("relation") != "adapted-from":
                out.append(f"warning: built_on[{i}] {c['name']}: {c['url']} is not a package in "
                           f"lake-manifest.json at the pin (pin not checked)")
        elif rev != c["pin"]:
            out.append(f"warning: built_on[{i}] {c['name']}: pin {c['pin'][:12]} differs from "
                       f"lake-manifest.json at the pin ({rev[:12]})")
    return out


# Packages a lake-manifest.json may list that are implicit for `built_on` (docs/index-catalog.md, Credits):
# Mathlib, the packages Mathlib itself pulls in, and Lean core. Matched on the manifest name and on the URL
# (case-insensitive, without a trailing .git).
MATHLIB_FAMILY_NAMES = frozenset(n.lower() for n in (
    "mathlib", "batteries", "std", "aesop", "Qq", "proofwidgets", "importGraph", "LeanSearchClient",
    "plausible", "Cli"))
MATHLIB_FAMILY_URLS = frozenset(_git_url_key(f"https://github.com/{r}") for r in (
    "leanprover-community/mathlib4", "leanprover-community/mathlib", "leanprover-community/batteries",
    "leanprover/std4", "leanprover-community/aesop", "leanprover-community/quote4",
    "leanprover-community/ProofWidgets4", "leanprover-community/import-graph",
    "leanprover-community/LeanSearchClient", "leanprover-community/plausible", "leanprover/lean4-cli"))
LEAN_CORE_NAMES = frozenset(("lean", "lean4", "init", "lake"))
LEAN_CORE_URLS = frozenset((_git_url_key("https://github.com/leanprover/lean4"),))
# An inherited package from one of these organizations is taken to come in through Mathlib, so a
# dependency Mathlib adds later is implicit before it is added to the lists above.
MATHLIB_ORG_PREFIXES = ("https://github.com/leanprover-community/", "https://github.com/leanprover/")


def implicit_lake_package(p: dict) -> bool:
    """True for a lake-manifest.json package that `built_on` never needs to credit."""
    name = str(p.get("name") or "").lower()
    url = _git_url_key(p.get("url") or "")
    if name in MATHLIB_FAMILY_NAMES or url in MATHLIB_FAMILY_URLS:
        return True
    if name in LEAN_CORE_NAMES or url in LEAN_CORE_URLS:
        return True
    return p.get("inherited") is True and url.startswith(MATHLIB_ORG_PREFIXES)


def uncredited_lake_dependencies(clone: pathlib.Path, entry: dict) -> list[dict]:
    """Proposal 0007 (open question 2, decided 2026-10-18): the lake-manifest.json packages at the pin
    that are not implicit and that no `built_on` credit names (by URL, with or without .git, or by name,
    both case-insensitive). Advisory: `index check` warns, never fails. [] without a readable manifest."""
    mf = clone / "lake-manifest.json"
    try:
        pkgs = (json.loads(mf.read_text()).get("packages") or []) if mf.is_file() else []
    except ValueError:
        return []
    credits = [c for c in entry.get("built_on") or [] if isinstance(c, dict)]
    urls = {_git_url_key(c.get("url") or "") for c in credits} - {""}
    names = {str(c.get("name") or "").lower() for c in credits} - {""}
    out = []
    for p in pkgs if isinstance(pkgs, list) else []:
        if not isinstance(p, dict) or implicit_lake_package(p):
            continue
        if _git_url_key(p.get("url") or "") in urls or str(p.get("name") or "").lower() in names:
            continue
        out.append({"name": str(p.get("name") or ""), "url": str(p.get("url") or "")})
    return out


def _index_claim_errors(entry: dict, prior: list[str]) -> list[str]:
    """claims[] of an index-entry/2 must be exactly what index_claims derives from the headline."""
    from .claims import CLAIM_ID_RE
    cs = entry["claims"]
    if not isinstance(cs, list):
        return ["claims must be a list"]
    out: list[str] = []
    for i, c in enumerate(cs):
        if _exact_keys(c, INDEX_CLAIM_KEYS, f"claims[{i}]", out) and not (
                isinstance(c["id"], str) and CLAIM_ID_RE.match(c["id"])):
            out.append(f"claims[{i}].id must be <name>/<slug>, got {c['id']!r}")
    if out or any(x.startswith("headline") for x in prior):
        return out
    if cs != index_claims(entry["name"], entry["headline"], entry["trust"]):
        out.append("claims[] must be derived from the headline (one per item: id <name>/<slug of the "
                   "name>, kind theorem, its summary and judged_as, the entry's trust); nothing is typed "
                   "by hand: re-run `lean-pkg index add`")
    return out


# --------------------------------------------------------------------------
# trust

def compute_trust(lower_ok: bool, comparator_ok: bool, palomar: list) -> str | None:
    if palomar:
        return "palomar"
    if comparator_ok:
        return "comparator"
    if lower_ok:
        return "lower-tier"
    return None


def comparator_mechanism(comp: dict | None) -> str | None:
    if not isinstance(comp, dict):
        return None
    if comp.get("schema") == "comparator-receipt/1":
        return "legacy"
    return comp.get("mechanism")


LICENSE_MARKERS = {
    "MIT": "Permission is hereby granted",
    "Apache-2.0": "Apache License",
    "BSD-3-Clause": "Redistribution and use",
    "BSD-2-Clause": "Redistribution and use",
}


# --------------------------------------------------------------------------
# the package at its pin

def clone_at(source: str, dest: pathlib.Path) -> pathlib.Path:
    p = core.run(["git", "clone", "--quiet", "--no-checkout", str(source), str(dest)])
    if p.returncode != 0:
        raise core.Fail(f"git clone {source} failed: {p.stderr.strip()[:300]}")
    return dest


def checkout_pin(clone: pathlib.Path, commit: str) -> None:
    # core.symlinks=false: a symlink in the tree becomes a plain file, never a link to follow
    p = core.git(clone, "-c", "core.symlinks=false", "checkout", "--quiet", "--detach", commit)
    if p.returncode != 0:
        raise core.Fail(f"pin {commit[:12]} is not in the repository: {p.stderr.strip()[:200]}")


def docs_record(reg: dict | None, name: str, repo: str) -> dict | None:
    """The registry record whose docs-only opt-in binds this entry, or None. Index applies the opt-in
    where gate, ledger, promote, publish and comparator read it: the package's registry record
    (docs_descendants: true), never a file in the cloned repository (a tag or a descendant could add that
    file itself). The record must be the entry's own: the same name and the same public repository.
    Without a registry (a public `index check`), or with any other record, the evidence-only rule binds."""
    pkg = ((reg or {}).get("packages") or {}).get(name) if isinstance(reg, dict) else None
    if not isinstance(pkg, dict) or pkg.get("docs_descendants") is not True:
        return None
    try:
        if _repo_key(https_repo(pkg.get("repo"))) != _repo_key(repo):
            return None
    except core.Fail:
        return None
    return pkg


REGISTRY_OPT_IN_NOTE = ("docs-only descendants accepted for the tag and the evidence: the registry record opts in "
                        "(docs_descendants: true)")


def opt_in_note(clone: pathlib.Path, pin: str, record: dict) -> str:
    """The check note for an opted-in registry record (docs_record), from the opt-in the binding rule applies
    at the pin (binding_file.resolve_opt_in, the registry precedence of proposal 0002): the record's true
    applies only when the pin has no lean-pkg-binding.yaml; once the file exists there it decides."""
    from .binding_file import FILE, resolve_opt_in
    oi = resolve_opt_in(clone, pin, record)
    if oi.source == "registry":
        return REGISTRY_OPT_IN_NOTE
    if oi.source == "committed":
        return f"docs-only descendants accepted for the tag and the evidence: {FILE} at {pin[:12]} opts in"
    why = "says docs_descendants: false" if oi.file_state == "false" else (
        "does not opt in: " + (oi.problems[0].split(": ", 1)[-1] if oi.problems else "invalid"))
    return (f"the registry record's docs_descendants: true is overridden by {FILE} at {pin[:12]} ({why}); "
            f"the evidence-only rule binds the tag and the evidence")


def receipt_checker(blob) -> str:
    """The `checker` text of a lower-tier receipt's bytes ('' when absent): the files it names are build
    inputs for the docs-only rule. Index passes it explicitly, so the rule never reads a working tree."""
    try:
        chk = json.loads(blob).get("checker")
    except (ValueError, AttributeError, TypeError):
        return ""
    return chk if isinstance(chk, str) else ""


def descends(clone: pathlib.Path, bound: str, head: str, record: dict | None = None,
             checker: str = "") -> tuple[bool, str, list[str] | None]:
    """The binding rule for the tag and the evidence commits: cli.package_descendant, the rule every
    other consumer applies, when `record` (docs_record) opts in; otherwise exactly the evidence-only rule.
    The clone has no registered nested packages, and its formalization.yaml keeps the evidence-only
    rule's stricter reading unless the record opts in. (ok, note, docs_paths)."""
    if record is None:
        ok, note = cli.evidence_only_descendant(clone, bound, head)
        return ok, note, None
    return cli.package_descendant(clone, bound, head, record, checker=checker or "", nested=())


def find_evidence(clone: pathlib.Path, rel: str, pin: str | None, record: dict | None = None,
                  checker: str = "") -> tuple[str, bytes]:
    """(pin, bytes) of the evidence file `rel` as committed in the pin itself or in an
    evidence-only descendant of it (or, when `record` opts in, a docs-only one), searched over every
    ref of the clone. With pin None, the pin is read from the receipt's own `commit` field.
    `checker`: the lower-tier receipt's, unless the blob is a receipt with its own."""
    p = core.git(clone, "log", "--all", "--format=%H", "--", rel)
    for c in [x for x in p.stdout.split() if x]:
        blob = core.git_show_bytes(clone, rel, c)
        if blob is None:
            continue
        bound = pin
        if bound is None:
            try:
                bound = str(json.loads(blob).get("commit") or "")
            except (ValueError, AttributeError):
                continue
            if not core._HEX40.fullmatch(bound):
                continue
        ok, _, _ = descends(clone, bound, c, record, receipt_checker(blob) or checker)
        if ok:
            return bound, blob
    raise core.Fail(f"{rel} is not committed at the pin or in an evidence-only "
                    f"{'or docs-only ' if record is not None else ''}descendant of it "
                    f"on any branch; commit and push the evidence first (untracked files do not count)")


def _symlink_components_at(clone: pathlib.Path, commit: str, rel: str) -> list[str]:
    """Path prefixes of `rel` that are symlinks (mode 120000) in the tree at `commit`."""
    parts = pathlib.PurePosixPath(rel).parts
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    p = core.git(clone, "ls-tree", "-r", commit, "--", *prefixes)
    hits = []
    for line in p.stdout.splitlines():
        meta, _, path = line.partition("\t")
        if meta.startswith("120000") and path in prefixes:
            hits.append(path)
    return hits


def _safe_write(clone: pathlib.Path, rel: str, blob: bytes) -> None:
    """Write `blob` at clone/rel, refusing any symlink on the way (never write outside the clone)."""
    if not _is_rel(rel):
        raise core.Fail(f"evidence path {rel!r} is not repository-relative")
    cur = clone
    for part in pathlib.PurePosixPath(rel).parts[:-1]:
        cur = cur / part
        if cur.is_symlink():
            raise core.Fail(f"evidence path {rel!r} crosses a symlink ({cur.relative_to(clone)}); refused")
        if cur.exists() and not cur.is_dir():
            raise core.Fail(f"evidence path {rel!r} crosses a non-directory ({cur.relative_to(clone)}); refused")
        cur.mkdir(exist_ok=True)
    dest = clone / rel
    if dest.is_symlink():
        raise core.Fail(f"evidence path {rel!r} is a symlink; refused")
    if dest.resolve().parent != (clone / rel).parent.resolve() or clone.resolve() not in dest.resolve().parents:
        raise core.Fail(f"evidence path {rel!r} resolves outside the clone; refused")
    dest.write_bytes(blob)


def materialize(clone: pathlib.Path, pin: str, rels: list[str], record: dict | None = None,
                checker: str = "") -> dict[str, bytes]:
    """Check out the pin and write each evidence file from its committed bytes into the
    working tree, so HEAD == pin and the existing validators read exactly those bytes.
    Symlinks are never followed: the tree at the pin is checked for symlink components and
    every write is guarded."""
    checkout_pin(clone, pin)
    out: dict[str, bytes] = {}
    for rel in rels:
        links = _symlink_components_at(clone, pin, rel)
        if links:
            raise core.Fail(f"evidence path {rel!r} crosses a symlink at the pin ({', '.join(links)}); refused")
        _, blob = find_evidence(clone, rel, pin, record, checker)
        _safe_write(clone, rel, blob)
        out[rel] = blob
    return out


def evidence_commit(clone: pathlib.Path, rel: str, pin: str, record: dict | None = None,
                    checker: str = "") -> str:
    """The commit (pin, or a descendant under `descends`) whose version of `rel` find_evidence uses."""
    p = core.git(clone, "log", "--all", "--format=%H", "--", rel)
    for c in [x for x in p.stdout.split() if x]:
        blob = core.git_show_bytes(clone, rel, c)
        if blob is None:
            continue
        ok, _, _ = descends(clone, pin, c, record, receipt_checker(blob) or checker)
        if ok:
            return c
    raise core.Fail(f"{rel} is not committed at the pin or in an evidence-only "
                    f"{'or docs-only ' if record is not None else ''}descendant of it")


def reachable_on(clone: pathlib.Path, public_repo: str, shas: list[str]) -> list[str]:
    """Fetch the public repository's branches into refs/remotes/public/ and return the shas
    among `shas` that no public branch contains."""
    p = core.git(clone, "fetch", "--quiet", "--no-tags", public_repo,
                 "+refs/heads/*:refs/remotes/public/*")
    if p.returncode != 0:
        raise core.Fail(f"cannot fetch {public_repo} to verify the pin: {p.stderr.strip()[:200]}")
    missing = []
    for sha in shas:
        q = core.git(clone, "for-each-ref", "--contains", sha, "refs/remotes/public")
        if q.returncode != 0 or not q.stdout.strip():
            missing.append(sha)
    return missing


def _private_walk(obj, where):
    """(label, is_key, string) for every mapping key and string value. A key that looks private is
    labelled by position (`<key#i>`), never by its text, both for itself and for what lies below it."""
    if isinstance(obj, str):
        yield where, False, obj
    elif isinstance(obj, dict):
        for i, (k, v) in enumerate(obj.items()):
            k = str(k)
            if PRIVATE_PATH_RE.search(k):
                label = f"{where}.<key#{i}>"
                yield label, True, k
            else:
                label = f"{where}.{k}"
            yield from _private_walk(v, label)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _private_walk(v, f"{where}[{i}]")


def private_string_hits(blob: bytes, where: str) -> list[tuple[str, str, str]]:
    """(field, kind, string) for every mapping key or string value in a JSON document that looks
    private. `field` never contains the private text itself (private keys are positional)."""
    try:
        data = json.loads(blob)
    except ValueError:
        return []
    return [(w, "private local path", s) for w, _key, s in _private_walk(data, where)
            if PRIVATE_PATH_RE.search(s)]


def private_strings_in(blob: bytes, where: str) -> list[str]:
    """String values in a JSON document that look like private local paths."""
    try:
        data = json.loads(blob)
    except ValueError:
        return []
    return [f"{where}: {w} looks like a private local path" + ("" if is_key else f": {s!r}")
            for w, is_key, s in _private_walk(data, where) if PRIVATE_PATH_RE.search(s)]


def mathlib_rev_at(clone: pathlib.Path) -> str | None:
    mf = clone / "lake-manifest.json"
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text())
    except ValueError:
        raise core.Fail("lake-manifest.json at the pin is not valid JSON") from None
    for p in data.get("packages") or []:
        if str(p.get("name", "")).lower() in ("mathlib", "«mathlib»") and p.get("rev"):
            return str(p["rev"])
    return None


def license_problem(clone: pathlib.Path, spdx: str) -> str | None:
    for n in core.LICENSE_NAMES:
        f = clone / n
        if f.is_file():
            marker = LICENSE_MARKERS.get(spdx)
            if marker and marker not in f.read_text(errors="replace"):
                return f"{n} at the pin does not read like {spdx} (no {marker!r})"
            return None
    return f"no license file ({', '.join(core.LICENSE_NAMES)}) at the pin"


PUBLIC_GATE_PKG = {"gate": {"axioms_allowed": list(core.DEFAULT_AXIOMS)}}


def materialize_report_log(clone: pathlib.Path, pin: str, receipt: dict, lower_rel: str,
                           record: dict | None = None, checker: str = "") -> dict[str, bytes]:
    """The lower-tier receipt's sealed verify log (.log or .log.gz beside the receipt) as committed in the
    pin or an evidence-only descendant, written into the clone like every other evidence file so that
    report_log_check hashes those bytes. None committed: refused, as `gate` refuses (a receipt whose
    report_sha256 binds nothing is not evidence; the index keeps the receipts, never the logs)."""
    name = cli.report_log_name(receipt)
    base = pathlib.PurePosixPath(lower_rel).parent
    out: dict[str, bytes] = {}
    for fname in (name, name + cli.REPORT_GZ_SUFFIX):
        rel = str(base / fname)
        try:
            out.update(materialize(clone, pin, [rel], record, checker))
        except core.Fail as e:
            if "is not committed at the pin" not in str(e):
                raise
    if not out:
        raise core.Fail(f"{cli.REPORT_LOG_MISSING}{base / name} (or its {cli.REPORT_GZ_SUFFIX}) is not committed "
                        f"at the pin or in an evidence-only descendant of it; without it report_sha256 binds "
                        f"nothing. Commit the sealed redacted log byte-exact at that path (a copy kept under "
                        f"another name may be copied there when its SHA-256 is the receipt's report_sha256) and "
                        f"push it ({cli.REPORT_LOG_RESTORE})")
    return out


def derive(clone: pathlib.Path, pin: str, lower_rel: str | None,
           comp_rel: str | None, record: dict | None = None) -> dict:
    """Validate the receipts at the pin with the registry's own validators. `record`: docs_record's
    opt-in for the evidence lookup (descends). Returns {lower, comparator, lower_ok, comparator_ok,
    mechanism, bytes, failures, checker} (checker: the lower-tier receipt's, for the docs-only rule)."""
    got = materialize(clone, pin, [lower_rel], record) if lower_rel else {}
    checker = receipt_checker(got[lower_rel]) if lower_rel else ""
    if comp_rel:
        got.update(materialize(clone, pin, [comp_rel], record, checker))
    res = {"lower": None, "comparator": None, "lower_ok": False, "comparator_ok": False,
           "mechanism": None, "bytes": got, "failures": [], "checker": checker}
    if lower_rel:
        try:
            res["lower"] = json.loads(got[lower_rel])
            cli.validate_receipt(PUBLIC_GATE_PKG, clone, res["lower"])
            if res["lower"]["commit"] != pin:
                raise core.Fail(f"lower-tier receipt binds {res['lower']['commit'][:12]}, not the pin")
            got.update(materialize_report_log(clone, pin, res["lower"], lower_rel, record, checker))
            cli.report_log_check(clone, res["lower"], clone / lower_rel)
            res["lower_ok"] = True
        except (ValueError, core.Fail) as e:
            res["failures"].append(f"lower-tier receipt {lower_rel}: {e}")
    if comp_rel:
        try:
            res["comparator"] = json.loads(got[comp_rel])
            res["mechanism"] = comparator_mechanism(res["comparator"])
            logs = [c.get("log") for c in res["comparator"].get("configs") or []
                    if isinstance(c, dict) and isinstance(c.get("log"), str)]
            got.update(materialize(clone, pin, logs, record, checker))
            lower_commit = res["lower"]["commit"] if res["lower_ok"] else None
            cli.validate_comparator(PUBLIC_GATE_PKG, clone, res["comparator"], lower_commit)
            if res["comparator"]["commit"] != pin:
                raise core.Fail(f"comparator receipt binds {res['comparator']['commit'][:12]}, not the pin")
            res["comparator_ok"] = True
        except (ValueError, core.Fail) as e:
            res["failures"].append(f"comparator receipt {comp_rel}: {e}")
    return res


# --------------------------------------------------------------------------
# add

def https_repo(url: str) -> str:
    url = str(url or "").strip()
    nwo = core.gh_nwo(url)
    if nwo:
        return f"https://github.com/{nwo}"
    if HTTPS_RE.match(url):
        return url[:-4] if url.endswith(".git") else url.rstrip("/")
    raise core.Fail(f"repo {url!r} is not a public https or GitHub URL; pass --repo https://...")


def _repo_key(url: str) -> str:
    u = url.lower().rstrip("/")
    return u[:-4] if u.endswith(".git") else u


def parse_headline(item: str) -> dict:
    parts = [x.strip() for x in str(item).split(" :: ", 3)]
    if len(parts) not in (3, 4) or not all(parts):
        raise core.Fail(f"--headline expects 'Name :: path/File.lean :: One sentence.[ :: ChallengeTheorem]', got {item!r}")
    out = {"name": parts[0], "file": parts[1], "summary": parts[2]}
    if len(parts) == 4:
        out["judged_as"] = parts[3]
    return out


def judged_names(comp, smoke=None) -> set[str]:
    """Theorem and definition names the comparator receipt's configurations judged, leaving out the
    configurations in `smoke` (paths lean-pkg-signoff.json marks as smoke checks: they credit nothing)."""
    names: set[str] = set()
    for c in (comp or {}).get("configs") or []:
        if isinstance(c, dict) and c.get("path") not in set(smoke or ()):
            names.update(str(x) for x in (c.get("theorem_names") or []))
            names.update(str(x) for x in (c.get("definition_names") or []))
    return names


def unjudged_headlines(headline: list[dict], comp, smoke=None) -> list[str]:
    """Headline names whose `judged_as` is missing or not among the judged names (smoke checks excluded)."""
    judged = judged_names(comp, smoke)
    return [h["name"] for h in headline if not (h.get("judged_as") and h["judged_as"] in judged)]


def smoke_at(clone: pathlib.Path, pin: str, comp) -> dict[str, str]:
    """{configuration path: reason} of the smoke checks lean-pkg-signoff.json declares at the pin, at the
    comparator receipt's commit, or at the tip of the clone's default branch (refs/remotes/origin/HEAD): a
    mark at any of them only takes credit away, as for coverage check (comparator_config.smoke_configs).
    Fail when a sidecar there is malformed. A copy-only check (no clone) cannot read the mark."""
    from . import comparator_config as cc
    commit = str((comp or {}).get("commit") or "")
    return cc.smoke_configs(clone, pin, commit if core._HEX40.fullmatch(commit) else None,
                            "refs/remotes/origin/HEAD")


def headline_problems(clone: pathlib.Path, headline: list[dict]) -> list[str]:
    out = []
    for h in headline:
        f = clone / h["file"]
        if not f.is_file():
            out.append(f"headline file {h['file']} is not tracked at the pin")
        elif not lean_scan.occurs(lean_scan.last_component(h["name"]), f.read_text(errors="replace")):
            out.append(f"headline {h['name']}: {lean_scan.last_component(h['name'])!r} does not occur in {h['file']}")
    return out


def at_pin_problems(clone: pathlib.Path, entry: dict, receipts: dict,
                    record: dict | None = None) -> list[str]:
    """Checks shared by add and check: toolchain, manifest, license, tag, headline. The tag is bound by
    `descends` (the docs-only rule too when `record`, docs_record, opts in)."""
    out = []
    tc = (clone / "lean-toolchain").read_text().strip() if (clone / "lean-toolchain").is_file() else None
    if tc != entry["lean_toolchain"]:
        out.append(f"lean_toolchain {entry['lean_toolchain']!r} != lean-toolchain at the pin {tc!r}")
    try:
        rev = mathlib_rev_at(clone)
    except core.Fail as e:
        rev = None
        out.append(str(e))
    if rev != entry["mathlib_rev"]:
        out.append(f"mathlib_rev {entry['mathlib_rev']!r} != lake-manifest.json at the pin {rev!r}")
    lower = receipts.get("lower")
    if lower and rev and str(lower.get("mathlib_rev")) != rev:
        out.append(f"lower-tier receipt mathlib_rev {lower.get('mathlib_rev')!r} != lake-manifest.json {rev!r}")
    lp = license_problem(clone, entry["license_spdx"])
    if lp:
        out.append(lp)
    tag = entry["pin"]["tag"]
    if tag:
        p = core.git(clone, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
        if p.returncode != 0:
            out.append(f"tag {tag!r} does not exist in the repository")
        else:
            pin, head, checker = entry["pin"]["commit"], p.stdout.strip(), receipts.get("checker") or ""
            ok, note, _ = descends(clone, pin, head, record, checker)
            if not ok:
                out.append(f"tag {tag!r} is neither the pin nor an evidence-only descendant of it"
                           + (f" ({note})" if record is not None else tag_docs_hint(clone, pin, head, checker, entry["name"])))
    out += headline_problems(clone, entry["headline"])
    return out


def tag_docs_hint(clone: pathlib.Path, pin: str, head: str, checker: str, name: str) -> str:
    """Without the opt-in: a clause naming the docs-only route when that rule would admit the tag."""
    if not core._is_ancestor(clone, pin, head):
        return ""
    ok, note, _ = cli.docs_only_descendant(clone, pin, head, {}, checker)
    if not ok:
        return ""
    return (f"; it is a {note.replace('; opted in', '')}, which binds only when the package's registry record "
            f"opts in (docs_descendants: true, `lean-pkg update {name} --accept-docs-descendants --reason R`) "
            f"and index runs with that registry")


def prior_art_notes(clone: pathlib.Path, headline: list[dict]) -> list[str]:
    """Advisory: headline items that no prior-art record at the pin names. A record under
    docs/prior-art/*.md counts for a headline when it contains the last component of its name."""
    d = clone / "docs" / "prior-art"
    texts = [p.read_text(errors="replace") for p in sorted(d.glob("*.md"))] if d.is_dir() else []
    missing = [h["name"] for h in headline
               if not any(lean_scan.occurs(lean_scan.last_component(h["name"]), t) for t in texts)]
    if not missing:
        return []
    return [f"note: no prior-art record under docs/prior-art/ at the pin names {', '.join(missing)} "
            f"(advisory; `lean-pkg search TERM --record` writes one)"]


def do_index_add(root: pathlib.Path, events_p: pathlib.Path | None, reg: dict | None,
                 name: str, opts: dict, opt_in_reg: dict | None = None) -> tuple[list[str], dict]:
    """`opt_in_reg`: the registry docs_record reads the docs-only opt-in from (default: `reg`)."""
    if not NAME_RE.fullmatch(name):
        raise core.Fail(f"bad entry name {name!r}")
    d = root / name
    replaced = None
    if (d / ENTRY_FILE).exists():
        if not opts.get("replace"):
            raise core.Fail(f"index entry {name!r} already exists at {d / ENTRY_FILE}; to re-pin it, pass "
                            f"--replace (its built_on credits are carried over) and change it by pull request")
        replaced = load_entry(d / ENTRY_FILE)
    elif opts.get("replace"):
        raise core.Fail(f"--replace: there is no index entry {name!r} at {d / ENTRY_FILE} to replace")
    built_on, built_on_from = None, None
    if opts.get("built_on_file"):
        built_on = load_built_on_file(opts["built_on_file"]) or None
        built_on_from = f"from {opts['built_on_file']}" if built_on else None
    elif replaced is not None and replaced.get("built_on"):
        built_on = normalize_built_on(replaced["built_on"])
        built_on_from = "carried over from the replaced entry"
    pkg = core.get_pkg(reg, name) if reg is not None else {}
    gate = pkg.get("gate") or {}
    repo = https_repo(opts.get("repo") or pkg.get("repo"))
    lower_rel = opts.get("lower") or gate.get("receipt")
    comp_rel = opts.get("comparator") or gate.get("comparator")
    if not lower_rel and not comp_rel:
        raise core.Fail("no receipt: pass --lower and/or --comparator (repo-relative .lean-receipts/ paths)")
    license_spdx = opts.get("license") or pkg.get("license_spdx")
    if not license_spdx:
        raise core.Fail("license_spdx unknown: pass --license SPDX-ID")
    if not opts.get("headline"):
        raise core.Fail("at least one --headline 'Name :: path/File.lean :: One sentence.' is required")
    headline = [parse_headline(h) for h in opts["headline"]]
    consent_recorded = bool(opts.get("consent_recorded") or pkg.get("consent"))
    if not consent_recorded:
        raise core.Fail("consent not recorded: a public listing needs every coauthor's recorded "
                        "consent (ADR-0070 D4); pass --consent-recorded, or record it first with "
                        "`lean-pkg record NAME consent-recorded ...` and use --from-registry")
    palomar_rows = [p for p in pkg.get("palomar") or []
                    if p.get("id") and p.get("version") and not p.get("withdrawn_at")]
    doi = None
    if pkg.get("doi"):
        doi = {"concept": pkg["doi"].get("concept"),
               "versions": [{"doi": v["doi"], "tag": v["tag"], "commit": v["commit"]}
                            for v in pkg["doi"].get("versions") or []]}
    reservoir = (pkg.get("reservoir") or {}).get("url") if isinstance(pkg.get("reservoir"), dict) else None
    source = opts.get("repo_dir")
    if not source and pkg.get("local_path") and core.repo_path(pkg).is_dir():
        source = str(core.repo_path(pkg))
    record = docs_record(opt_in_reg if opt_in_reg is not None else reg, name, repo)
    with tempfile.TemporaryDirectory(prefix="lean-pkg-index-") as tmp:
        clone = clone_at(source or repo, pathlib.Path(tmp) / "pkg")
        pin, first = find_evidence(clone, lower_rel or comp_rel, None, record)
        checker = receipt_checker(first) if lower_rel else ""
        palomar = [{"id": p["id"], "version": int(p["version"])} for p in palomar_rows
                   if p.get("commit") == pin]          # a registration at another commit does not grade this pin
        for other in entry_names(root):
            if replaced is not None and other == name:
                continue                                # the entry being replaced is not a duplicate
            try:
                o = load_entry(root / other / ENTRY_FILE)
            except core.Fail:
                continue                                # a broken sibling is check's business, not add's
            if _repo_key(str(o.get("repo"))) == _repo_key(repo) and \
                    (o.get("pin") or {}).get("commit") == pin:
                raise core.Fail(f"duplicate: entry {other!r} already lists {repo} at {pin[:12]}")
        if source and not opts.get("allow_unpushed"):
            shas = sorted({pin, *[evidence_commit(clone, r, pin, record, checker)
                                  for r in (lower_rel, comp_rel) if r]})
            missing = reachable_on(clone, repo, shas)
            if missing:
                raise core.Fail(f"commit(s) {', '.join(s[:12] for s in missing)} not reachable from any branch "
                                f"of {repo}; push the pin and the evidence first (or pass --allow-unpushed "
                                f"for a provisional entry that `index check --clone` will refuse until pushed)")
        res = derive(clone, pin, lower_rel, comp_rel, record)
        if res["failures"]:
            raise core.Fail("; ".join(res["failures"]))
        notes = prior_art_notes(clone, headline)
        leaks = [x for r in (lower_rel, comp_rel) if r for x in private_strings_in(res["bytes"][r], r)]
        if leaks:
            raise core.Fail("receipt copies are public; refused: " + "; ".join(leaks)
                            + " (set COMPARATOR_RECEIPT_HOST_LABEL / use public-safe checker strings)")
        coverage_note = None
        comparator_covers = res["comparator_ok"]
        if res["comparator_ok"]:
            smoke = smoke_at(clone, pin, res["comparator"])
            unjudged = unjudged_headlines(headline, res["comparator"], smoke)
            if unjudged:
                msg = (f"headline theorem(s) {', '.join(unjudged)} not judged by the comparator receipt "
                       f"(judged: {', '.join(sorted(judged_names(res['comparator'], smoke))) or 'none'}"
                       f"{'; smoke checks credit nothing: ' + ', '.join(sorted(smoke)) if smoke else ''}); "
                       f"give each headline its Challenge name as a fourth '::' part")
                if not res["lower_ok"]:
                    raise core.Fail(msg + "; a comparator-only entry needs every headline judged")
                comparator_covers = False
                coverage_note = msg + "; trust is lower-tier"
        trust = compute_trust(res["lower_ok"], comparator_covers, palomar)
        entry = {
            "schema": ENTRY_SCHEMA, "name": name, "repo": repo,
            "pin": {"commit": pin, "tag": opts.get("tag")},
            "lean_toolchain": (clone / "lean-toolchain").read_text().strip(),
            "mathlib_rev": mathlib_rev_at(clone),
            "license_spdx": license_spdx, "trust": trust,
            "receipts": {"lower": lower_rel or None, "comparator": comp_rel or None},
            "headline": headline, "claims": index_claims(name, headline, trust),
            "palomar": palomar, "doi": doi, "reservoir": reservoir,
            "formalization_yaml": opts.get("formalization_yaml") or pkg.get("formalization_yaml"),
            **({"built_on": built_on} if built_on else {}),
            "fields": list(opts.get("fields") or []),
            "consent": {"recorded": consent_recorded},
            "added": {"at": core.now_iso(), "by": opts["by"]},
            "notes": opts.get("notes") or "",
        }
        errors = validate_entry(entry, name)
        errors += at_pin_problems(clone, entry, res, record)
        if errors:
            raise core.Fail("index entry refused: " + "; ".join(errors))
        if replaced is not None and (d / RECEIPTS_DIR).is_dir():
            for old in (d / RECEIPTS_DIR).iterdir():   # the replaced entry's copies; the new ones follow
                if old.is_file():
                    old.unlink()
        (d / RECEIPTS_DIR).mkdir(parents=True, exist_ok=True)
        copied = []
        for rel in (lower_rel, comp_rel):
            if rel:
                (d / RECEIPTS_DIR / pathlib.PurePosixPath(rel).name).write_bytes(res["bytes"][rel])
                copied.append(f"{name}/{RECEIPTS_DIR}/{pathlib.PurePosixPath(rel).name}")
        (d / ENTRY_FILE).write_text(dump_entry(entry))
    out = [f"{name}: index entry written at {d / ENTRY_FILE} (trust {trust}, pin {pin[:12]}"
           + (f", {res['mechanism']} comparator" if res["mechanism"] else "") + ")",
           f"receipts copied: {', '.join(copied)}"]
    if replaced is not None:
        out.append(f"replaced the previous entry (pin {str((replaced.get('pin') or {}).get('commit'))[:12]})")
    if built_on:
        out.append(f"built_on: {len(built_on)} credit(s) {built_on_from}: "
                   + ", ".join(c["name"] for c in built_on))
    elif replaced is not None and opts.get("built_on_file"):
        out.append("built_on: none (the --built-on-file list is empty)")
    if coverage_note:
        out.append(coverage_note)
    if source and opts.get("allow_unpushed"):
        out.append("provisional: --allow-unpushed skipped the public-repository check; "
                   "`index check --clone` will fail until the pin and evidence are pushed")
    if events_p is not None:
        lock_reg = opts.get("registry_lock")   # the clone above runs unlocked; the append under the lock
        with core.registry_lock(lock_reg) if lock_reg else contextlib.nullcontext():
            core.append_event(events_p, core.now_iso(), name, "index-added", repo=repo,
                              commit=pin, trust=trust, index=str(root))
    else:
        out.append("no event log (no registry here and no --events): index-added not recorded")
    return out, {"ok": True, "name": name, "event": "index-added", "trust": trust,
                 "commit": pin, "mechanism": res["mechanism"], "entry": str(d / ENTRY_FILE),
                 "receipts": copied, "notes": notes, "replaced": replaced is not None,
                 "built_on": [c["name"] for c in built_on or []]}


# --------------------------------------------------------------------------
# CLI glue: lean_pkg.cli.main calls add_parsers(sub, add_json)

def _emit(args, out: list[str], payload: dict, rc: int = 0) -> int:
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    return rc


def cmd_index(args) -> int:
    fn = getattr(args, "index_fn", None)
    if fn is None:
        raise core.UsageError("index needs an action: add | check | render | migrate")
    return fn(args)


def opt_in_registry(args) -> dict | None:
    """The registry docs_record reads the docs-only opt-in from: the one --registry / $LEAN_PKG_REGISTRY /
    the user default resolves to, when it exists and loads; else None (the evidence-only rule)."""
    reg_path = core.registry_path(args)
    if not reg_path.is_file():
        return None
    try:
        return core.load_registry(reg_path)
    except core.Fail:
        return None


def run_add(args) -> int:
    root = index_root(args)
    reg_path = core.registry_path(args)
    reg = core.load_registry(reg_path) if args.from_registry else None
    events_p = core.events_path(args) if (args.events or reg_path.is_file()) else None
    opts = {"repo": args.repo, "tag": args.tag, "lower": args.lower,
            "comparator": args.comparator, "license": args.license,
            "headline": args.headline, "fields": args.field_tag,
            "formalization_yaml": args.formalization_yaml,
            "consent_recorded": args.consent_recorded, "by": args.by,
            "notes": args.notes, "repo_dir": args.repo_dir,
            "allow_unpushed": args.allow_unpushed,
            "replace": args.replace, "built_on_file": args.built_on_file,
            "registry_lock": reg_path if reg_path.is_file() else None}
    out, payload = do_index_add(root, events_p, reg, args.name, opts,
                                opt_in_reg=reg if reg is not None else opt_in_registry(args))
    rc = _emit(args, out, payload)
    if not args.json_out:
        for note in payload["notes"]:        # advisory: stderr, so stdout's last line is the result
            print(note, file=sys.stderr)
    return rc


def _parser_add(isub, common) -> None:
    a = isub.add_parser("add", help="write <index>/<name>/entry.yaml from a clone at the pin")
    a.add_argument("name")
    a.add_argument("--from-registry", action="store_true",
                   help="start from the registry row (repo, receipts, license, palomar, doi, reservoir)")
    a.add_argument("--repo-dir", default=None, help="local clone to read from (never modified)")
    a.add_argument("--url", dest="repo", metavar="URL", default=None,
                   help="public https URL (a GitHub ssh URL is converted)")
    a.add_argument("--tag", default=None, help="release tag at the pin or an evidence-only descendant")
    a.add_argument("--lower", default=None, metavar="REL", help="lower-tier receipt, .lean-receipts/...json")
    a.add_argument("--comparator", default=None, metavar="REL", help="comparator receipt, .lean-receipts/...json")
    a.add_argument("--license", default=None, metavar="SPDX")
    a.add_argument("--headline", action="append", metavar="'NAME :: FILE :: SUMMARY'")
    a.add_argument("--field-tag", action="append", metavar="TAG", help="free field tag (repeatable)")
    a.add_argument("--formalization-yaml", default=None, metavar="REL")
    a.add_argument("--consent-recorded", action="store_true",
                   help="every coauthor agreed to the listing (the pointer stays private)")
    a.add_argument("--by", required=True, help="public handle of the person adding the entry")
    a.add_argument("--allow-unpushed", action="store_true",
                   help="do not verify that the pin and evidence are on the public repository (provisional entry)")
    a.add_argument("--notes", default="")
    a.add_argument("--built-on-file", default=None, metavar="FILE",
                   help="YAML list of credits for third-party Lean code the package builds on "
                        "(name, url, authors; optional license, pin, relation, note); [] for none")
    a.add_argument("--replace", action="store_true",
                   help="replace an existing entry of this name (a re-pin); its built_on is carried over "
                        "unless --built-on-file is given")
    common(a, run_add)


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("index", help="the public catalog: add | check | render | migrate")
    add_json(s)
    s.set_defaults(fn=cmd_index, index_fn=None)
    isub = s.add_subparsers(dest="index_cmd")

    def common(p, fn) -> None:
        p.add_argument("--index", default=None,
                       help=f"index root (default: ${INDEX_ENV}, else {DEFAULT_INDEX_DIR}/ beside the "
                            f"registry when present, else ./{DEFAULT_INDEX_DIR})")
        add_json(p)
        p.set_defaults(fn=cmd_index, index_fn=fn)

    _parser_add(isub, common)
    _parser_check(isub, common)
    _parser_render(isub, common)
    _parser_migrate(isub, common)


PALOMAR_ENTRY_URL = "https://data.palomar-registry.org/entries/{id}-v{version}.json"


# --------------------------------------------------------------------------
# network (tests monkeypatch these; None means unreachable)

def palomar_entry_fetch(entry_id: str, version: int):
    """The Palomar entry JSON (dict) on 200, the HTTP status (int) otherwise, None when unreachable.
    Tests monkeypatch this."""
    req = urllib.request.Request(PALOMAR_ENTRY_URL.format(id=entry_id, version=version),
                                 headers={"User-Agent": "lean-pkg"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            try:
                return json.loads(r.read().decode("utf-8"))
            except ValueError:
                return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def network_checks(entry: dict) -> tuple[dict, list[str]]:
    """({"palomar": state, "reservoir": state}, failures). state: verified | skipped | failed | n/a.
    Unreachable is `skipped`: never a failure and never a pass."""
    states = {"palomar": "n/a", "reservoir": "n/a"}
    failures: list[str] = []
    if entry.get("palomar"):
        seen = []
        for p in entry["palomar"]:
            got = palomar_entry_fetch(p["id"], p["version"])
            seen.append(got)
            if got is None:
                continue
            if not isinstance(got, dict):
                failures.append(f"palomar {p['id']} v{p['version']}: data.palomar-registry.org answered {got}")
                continue
            src = got.get("source") or {}
            commit = str(src.get("commit") or "")
            url = str(src.get("repository_url") or "")
            if commit != entry["pin"]["commit"] or (url and _repo_key(url) != _repo_key(entry["repo"])):
                failures.append(f"palomar {p['id']} v{p['version']} is registered for "
                                f"{url or '?'} at {commit[:12] or '?'}, not {entry['repo']} at "
                                f"{entry['pin']['commit'][:12]}")
        states["palomar"] = ("skipped" if any(s is None for s in seen) and not failures
                             else "failed" if failures else "verified")
    if entry.get("reservoir"):
        m = core.RESERVOIR_URL_RE.match(entry["reservoir"])
        st = core.reservoir_listing_status(m.group(1), m.group(2)) if m else 404
        if st is None:
            states["reservoir"] = "skipped"
        elif st == 200:
            states["reservoir"] = "verified"
        else:
            states["reservoir"] = "failed"
            failures.append(f"reservoir {entry['reservoir']}: API answered {st}")
    return states, failures


# --------------------------------------------------------------------------
# check

def static_problems(root: pathlib.Path, name: str, entry: dict) -> list[str]:
    errors = validate_entry(entry, name)
    if errors:
        return errors
    rec = entry["receipts"]
    copies = {}
    for k in ("lower", "comparator"):
        if rec[k]:
            f = root / name / RECEIPTS_DIR / pathlib.PurePosixPath(rec[k]).name
            if not f.is_file():
                errors.append(f"receipts.{k}: copy missing at {f.relative_to(root)}")
                continue
            try:
                copies[k] = json.loads(f.read_text())
            except ValueError:
                errors.append(f"receipts.{k}: copy is not valid JSON")
                continue
            if copies[k].get("commit") != entry["pin"]["commit"]:
                errors.append(f"receipts.{k}: copy binds {str(copies[k].get('commit'))[:12]}, "
                              f"not the pin {entry['pin']['commit'][:12]}")
    comparator_covers = "comparator" in copies
    if comparator_covers:
        unjudged = unjudged_headlines(entry["headline"], copies["comparator"])
        if unjudged:
            comparator_covers = False
            if entry["trust"] == "comparator":
                errors.append(f"headline theorem(s) {', '.join(unjudged)} not judged by the comparator "
                              f"receipt copy (judged: {', '.join(sorted(judged_names(copies['comparator']))) or 'none'})")
    supported = compute_trust("lower" in copies, comparator_covers, entry["palomar"])
    if supported is None or TRUST_RANK[entry["trust"]] > TRUST_RANK[supported]:
        errors.append(f"trust {entry['trust']!r} exceeds its evidence ({supported or 'none'})")
    return errors


def check_entry(root: pathlib.Path, name: str, source: str | None, clone_it: bool,
                seen: dict, reg: dict | None = None) -> dict:
    rep = {"name": name, "ok": False, "trust_stated": None, "trust_computed": None,
           "mechanism": None, "failures": [], "notes": [], "uncredited_dependencies": [],
           "network": {"palomar": "n/a", "reservoir": "n/a"}}
    try:
        entry = load_entry(root / name / ENTRY_FILE)
    except core.Fail as e:
        rep["failures"].append(str(e))
        return rep
    rep["trust_stated"] = entry.get("trust")
    rep["failures"] += static_problems(root, name, entry)
    if rep["failures"]:
        return rep
    key = (_repo_key(entry["repo"]), entry["pin"]["commit"])
    if key in seen:
        rep["failures"].append(f"duplicate of entry {seen[key]!r} (same repo and commit)")
    seen.setdefault(key, name)
    if not entry["consent"]["recorded"]:
        rep["notes"].append("consent not recorded: every coauthor must agree before this entry is published")
    if source is None and not clone_it:
        rep["notes"].append("static check only: pass --clone (or --repo) to re-derive from bytes")
        rep["ok"] = not rep["failures"]
        return rep
    record = docs_record(reg, name, entry["repo"])
    at = len(rep["notes"])
    if record is not None:
        rep["notes"].append(REGISTRY_OPT_IN_NOTE)
    with tempfile.TemporaryDirectory(prefix="lean-pkg-index-") as tmp:
        try:
            clone = clone_at(source or entry["repo"], pathlib.Path(tmp) / "pkg")
            if record is not None:   # what decides at the pin: the record, or lean-pkg-binding.yaml there
                rep["notes"][at] = opt_in_note(clone, entry["pin"]["commit"], record)
            res = derive(clone, entry["pin"]["commit"], entry["receipts"]["lower"],
                         entry["receipts"]["comparator"], record)
        except core.Fail as e:
            rep["failures"].append(str(e))
            return rep
        rep["mechanism"] = res["mechanism"]
        rep["failures"] += res["failures"]
        for k in ("lower", "comparator"):
            rel = entry["receipts"][k]
            if rel and rel in res["bytes"]:
                copy = (root / name / RECEIPTS_DIR / pathlib.PurePosixPath(rel).name).read_bytes()
                if copy != res["bytes"][rel]:
                    rep["failures"].append(f"receipts.{k}: index copy differs from the bytes in the repository")
        rep["failures"] += at_pin_problems(clone, entry, res, record)
        try:
            smoke = smoke_at(clone, entry["pin"]["commit"], res["comparator"]) if res["comparator_ok"] else {}
        except core.Fail as e:
            rep["failures"].append(str(e))
            smoke = {}
        rep["notes"] += built_on_manifest_notes(clone, entry)
        rep["uncredited_dependencies"] = uncredited_lake_dependencies(clone, entry)
        rep["notes"] += [f"warning: Lake dependency {d['name']} ({d['url'] or 'no url'}) in lake-manifest.json "
                         f"at the pin has no built_on credit" for d in rep["uncredited_dependencies"]]
    comparator_covers = res["comparator_ok"]
    if res["comparator_ok"]:
        unjudged = unjudged_headlines(entry["headline"], res["comparator"], smoke)
        if unjudged:
            comparator_covers = False
            rep["notes"].append(f"headline theorem(s) {', '.join(unjudged)} not judged by the comparator receipt"
                                + (f" (smoke checks credit nothing: {', '.join(sorted(smoke))})" if smoke else ""))
    computed = compute_trust(res["lower_ok"], comparator_covers, entry["palomar"])
    rep["trust_computed"] = computed
    if computed != entry["trust"]:
        rep["failures"].append(f"stated trust {entry['trust']!r} != computed {computed!r}"
                               + (" (stated trust exceeds its evidence)"
                                  if computed is None or TRUST_RANK[entry["trust"]] > TRUST_RANK[computed]
                                  else ""))
    rep["network"], nf = network_checks(entry)
    rep["failures"] += nf
    for k, v in rep["network"].items():
        if v == "skipped":
            rep["notes"].append(f"{k} verification skipped: network unreachable")
    rep["ok"] = not rep["failures"]
    return rep


def do_index_check(root: pathlib.Path, names: list[str], repo_dir: str | None,
                   clone_it: bool, reg: dict | None = None) -> tuple[list[str], dict, int]:
    """`reg`: the registry whose records may opt an entry in to the docs-only rule (docs_record)."""
    if repo_dir and len(names) != 1:
        raise core.UsageError("--repo needs exactly one NAME")
    all_names = entry_names(root)
    targets = names or all_names
    seen: dict = {}
    reports = []
    for n in sorted(set(all_names) - set(targets)):   # duplicates are judged against the whole index
        try:
            e = load_entry(root / n / ENTRY_FILE)
        except core.Fail:
            continue                                   # reported when that entry itself is checked
        if isinstance(e.get("pin"), dict):
            seen.setdefault((_repo_key(str(e.get("repo"))), e["pin"].get("commit")), n)
    for n in targets:
        if n not in all_names:
            reports.append({"name": n, "ok": False, "failures": [f"no entry {n!r} under {root}"],
                            "notes": [], "uncredited_dependencies": [], "network": {}, "trust_stated": None,
                            "trust_computed": None, "mechanism": None})
            continue
        reports.append(check_entry(root, n, repo_dir, clone_it, seen, reg))
    ok = all(r["ok"] for r in reports)
    summary = (f"{len(reports)} entr{'y' if len(reports) == 1 else 'ies'} checked: "
               + ("all pass" if ok else "FAILED"))
    lines = [summary]           # #44: the verdict before the per-entry lines, and again last
    for r in reports:
        tag = "PASS" if r["ok"] else "FAIL"
        what = r.get("trust_computed") or r.get("trust_stated") or "?"
        mech = f", {r['mechanism']}" if r.get("mechanism") else ""
        lines.append(f"{tag} {r['name']} ({what}{mech})")
        lines += [f"  fail: {x}" for x in r["failures"]] + [f"  note: {x}" for x in r["notes"]]
    lines.append(summary)
    mode = "bytes" if (repo_dir or clone_it) else "static"
    return lines, {"ok": ok, "mode": mode, "index": str(root), "entries": reports}, (0 if ok else 1)


def run_check(args) -> int:
    out, payload, rc = do_index_check(existing_index_root(args), args.names, args.repo_dir, args.clone,
                                      opt_in_registry(args) if (args.repo_dir or args.clone) else None)
    return _emit(args, out, payload, rc)


def _parser_check(isub, common) -> None:
    c = isub.add_parser("check", help="static check, or re-derive entries from bytes at the pin; exit 0/1/2")
    c.add_argument("names", nargs="*", metavar="NAME")
    c.add_argument("--clone", action="store_true", help="clone each entry's repo at the pin into a temp dir")
    c.add_argument("--repo", dest="repo_dir", metavar="DIR", default=None,
                   help="local clone for a single NAME (never modified)")
    common(c, run_check)


RENDER_BANNER = ("<!-- machine-generated by `lean-pkg index render` from index/*/entry.yaml; "
                 "do not hand-edit -->")


# --------------------------------------------------------------------------
# render

def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _authors(names: list[str]) -> str:
    names = [_cell(a) for a in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def render_index(root: pathlib.Path) -> str:
    rows, claim_rows, credit_rows = [], [], []
    legacy = False
    for n in entry_names(root):
        e = load_entry(root / n / ENTRY_FILE)
        errors = validate_entry(e, n)
        if errors:
            raise core.Fail(f"cannot render: entry {n!r} is invalid: " + "; ".join(errors))
        trust = e["trust"]
        if trust == "comparator" and e["receipts"]["comparator"]:
            f = root / n / RECEIPTS_DIR / pathlib.PurePosixPath(e["receipts"]["comparator"]).name
            try:
                mech = comparator_mechanism(json.loads(f.read_text())) if f.is_file() else None
            except ValueError:
                raise core.Fail(f"cannot render: receipt copy for {n!r} is not valid JSON") from None
            if mech in ("legacy", "toolchain"):
                trust = f"comparator ({mech})"
                legacy = legacy or mech == "legacy"
        tc = e["lean_toolchain"].split(":", 1)[-1]
        head = "<br>".join(f"`{_cell(h['name'])}`: {_cell(h['summary'])}" for h in e["headline"])
        commit = e["pin"]["commit"]
        pin = f"`{commit[:12]}`" + (f" ({_cell(e['pin']['tag'])})" if e["pin"]["tag"] else "")
        tree = f"{e['repo']}/tree/{commit}" if e["repo"].startswith("https://github.com/") else e["repo"]
        links = [f"[source at pin]({tree})"]
        links += [f"[{p['id']} v{p['version']}](https://palomar-registry.org/entries/{p['id']})"
                  for p in e["palomar"]]
        if e["doi"] and e["doi"]["versions"]:
            links.append(f"[DOI](https://doi.org/{e['doi']['versions'][-1]['doi']})")
        if e["reservoir"]:
            links.append(f"[Reservoir]({e['reservoir']})")
        credits = e.get("built_on") or []
        built = "<br>".join(f"[{_cell(c['name'])}]({c['url']}) by {_authors(c['authors'])}" for c in credits)
        for c in credits:
            credit_rows.append(
                f"| {_cell(n)} | [{_cell(c['name'])}]({c['url']}) | {_authors(c['authors'])} | "
                f"{c.get('relation') or ''} | {_cell(c.get('license') or '')} | "
                + (f"`{c['pin'][:12]}`" if c.get("pin") else "") + f" | {_cell(c.get('note') or '')} |")
        rows.append(f"| [{_cell(n)}]({e['repo']}) | {trust} | {_cell(tc)} | {head} | {built} | {pin} | "
                    f"{' · '.join(links)} |")
        claim_rows += [f"| `{_cell(c['id'])}` | `{_cell(c['decl'])}` | {c['kind']} | {c['trust']} |"
                       for c in e.get("claims") or []]
    out = [RENDER_BANNER, "", "# lean-pkg index", "",
           "Generated by `lean-pkg index render` from `index/<name>/entry.yaml`. Do not edit this "
           "file by hand: change the entry and re-render. Every row can be re-derived with "
           "`lean-pkg index check --clone NAME`.", ""]
    if not rows:
        out.append("No entries yet.")
    else:
        out += ["| Package | Trust | Toolchain | Headline theorem | Built on | Pin | Links |",
                "|---|---|---|---|---|---|---|", *rows]
        if legacy:
            out += ["", "`comparator (legacy)`: checked with the pinned comparator stack; it is not "
                        "predictive of Palomar acceptance, which runs the toolchain mechanism."]
        if credit_rows:
            out += ["", "## Built on", "",
                    "Third-party Lean code each package depends on (`depends-on`, a Lake dependency) or "
                    "adapts (`adapted-from`, source copied into the package), from the entry's `built_on`. "
                    "Mathlib and its own dependencies are implicit and not listed.", "",
                    "| Package | Library | Authors | Relation | License | Pin | Note |",
                    "|---|---|---|---|---|---|---|", *credit_rows]
        if claim_rows:
            out += ["", "## Claims", "",
                    "Derived from each `index-entry/2` headline; `lean-pkg search TERM` finds them.", "",
                    "| Claim | Declaration | Kind | Trust |", "|---|---|---|---|", *claim_rows]
    out.append("")
    return "\n".join(out)


def run_render(args) -> int:
    root = existing_index_root(args, create=bool(getattr(args, "index", None) or args.out))
    text = render_index(root)
    dest = pathlib.Path(args.out).expanduser() if args.out else root / "README.md"
    n = len(entry_names(root))
    if args.check:
        current = dest.is_file() and dest.read_text() == text
        out = [f"{dest}: " + ("current" if current else "STALE; run `lean-pkg index render`")]
        return _emit(args, out, {"ok": current, "out": str(dest), "current": current}, 0 if current else 1)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    return _emit(args, [f"index rendered: {dest} ({n} entries)"],
                 {"ok": True, "out": str(dest), "entries": n})


def _parser_render(isub, common) -> None:
    r = isub.add_parser("render", help="write the Markdown table (default <index>/README.md)")
    r.add_argument("--out", default=None)
    r.add_argument("--check", action="store_true", help="exit 1 when the file is stale; write nothing")
    common(r, run_render)


# --------------------------------------------------------------------------
# migrate: rewrite index-entry/1 files as index-entry/2 (claims[] derived from the headline)

def migrate_entry(name: str, entry: dict) -> dict | None:
    """The index-entry/2 form of an index-entry/1 entry; None when it is already current."""
    if entry.get("schema") == ENTRY_SCHEMA:
        return None
    if entry.get("schema") != "index-entry/1":
        raise core.Fail(f"{name}: unknown schema {entry.get('schema')!r}; nothing migrated")
    new = dict(entry, schema=ENTRY_SCHEMA,
               claims=index_claims(name, entry.get("headline") or [], entry.get("trust")))
    errors = validate_entry(new, name)
    if errors:
        raise core.Fail(f"{name}: migrated entry would not validate: " + "; ".join(errors))
    return new


def run_migrate(args) -> int:
    root = existing_index_root(args)
    out, stale, done = [], [], []
    for name in entry_names(root):
        path = root / name / ENTRY_FILE
        entry = load_entry(path)
        new = migrate_entry(name, entry)
        if new is None:
            out.append(f"{name}: already {ENTRY_SCHEMA}")
            continue
        if args.check:
            out.append(f"{name}: {entry['schema']} (run `lean-pkg index migrate`)")
            stale.append(name)
            continue
        path.write_text(dump_entry(new))
        out.append(f"{name}: {entry['schema']} -> {ENTRY_SCHEMA} ({len(new['claims'])} claim(s) derived)")
        done.append(name)
    if done:
        out.append("render the table again: `lean-pkg index render`")
    if not out:
        out.append(f"no entries under {root}")
    rc = 1 if stale else 0
    return _emit(args, out, {"ok": not stale, "migrated": done, "stale": stale}, rc)


def _parser_migrate(isub, common) -> None:
    m = isub.add_parser("migrate", help="rewrite index-entry/1 entries as index-entry/2 (claims derived)")
    m.add_argument("--check", action="store_true", help="exit 1 when an entry is not current; write nothing")
    common(m, run_migrate)
