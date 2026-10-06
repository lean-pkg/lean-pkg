# lean-pkg index -- the public, forkable catalog of trust-graded packages
"""Index entries: `index/<name>/entry.yaml` (schema `index-entry/1`) plus
verbatim receipt copies under `index/<name>/receipts/`.

Everything is a file. `index add` writes an entry from a package clone at its
pin; `index check` re-derives every claim from bytes at the pin; `index render`
writes the Markdown table. Trust is computed from evidence, never typed.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import yaml

from . import cli as core


ENTRY_SCHEMA = "index-entry/1"
SUPPORTED_ENTRY_SCHEMAS = (ENTRY_SCHEMA,)
INDEX_ENV = "LEAN_PKG_INDEX"
DEFAULT_INDEX_DIR = "index"
ENTRY_FILE = "entry.yaml"
RECEIPTS_DIR = "receipts"
ENTRY_KEYS = ("schema", "name", "repo", "pin", "lean_toolchain", "mathlib_rev",
              "license_spdx", "trust", "receipts", "headline", "palomar", "doi",
              "reservoir", "formalization_yaml", "fields", "consent", "added", "notes")
TRUST_LEVELS = ("lower-tier", "comparator", "palomar")
TRUST_RANK = {t: i for i, t in enumerate(TRUST_LEVELS, 1)}
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HTTPS_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[^\s@]+$")
PALOMAR_ID_RE = re.compile(r"^PALOMAR-\d{4}-\d{2}-\d{2}-\d{6}$")
FIELD_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PRIVATE_PATH_RE = re.compile(r"(^[/~])|(/home/)|(/Users/)|([A-Za-z]:\\)")


# --------------------------------------------------------------------------
# paths

def index_root(args) -> pathlib.Path:
    """--index, else $LEAN_PKG_INDEX, else ./index."""
    if getattr(args, "index", None):
        return pathlib.Path(args.index).expanduser()
    env = os.environ.get(INDEX_ENV, "").strip()
    if env:
        return pathlib.Path(env).expanduser()
    return pathlib.Path.cwd() / DEFAULT_INDEX_DIR


def entry_names(root: pathlib.Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / ENTRY_FILE).is_file())


# --------------------------------------------------------------------------
# schema: load, validate, dump

def load_entry(path: pathlib.Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise core.Fail(f"index entry not found: {path}")
    except yaml.YAMLError as e:
        raise core.Fail(f"{path}: not valid YAML: {e}")
    if not isinstance(data, dict):
        raise core.Fail(f"{path}: top level is not a mapping")
    return data


def dump_entry(entry: dict) -> str:
    ordered = {k: entry[k] for k in ENTRY_KEYS}
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
    """Every error in an `index-entry/1` mapping; [] when valid. Unknown keys are refused."""
    errors: list[str] = []
    if not isinstance(entry, dict):
        return ["entry is not a mapping"]
    extra = sorted(set(entry) - set(ENTRY_KEYS))
    if extra:
        errors.append(f"unknown key(s) {extra} (schema {ENTRY_SCHEMA} allows {list(ENTRY_KEYS)})")
    missing = [k for k in ENTRY_KEYS if k not in entry]
    if missing:
        errors.append(f"missing key(s) {missing}")
        return errors
    if entry["schema"] not in SUPPORTED_ENTRY_SCHEMAS:
        errors.append(f"schema must be one of {SUPPORTED_ENTRY_SCHEMAS}, got {entry['schema']!r}")
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
            if not _exact_keys(h, ("name", "file", "summary"), f"headline[{i}]", errors):
                continue
            if not isinstance(h["name"], str) or not h["name"].strip() or " " in h["name"]:
                errors.append(f"headline[{i}].name must be a fully qualified Lean name")
            if not (_is_rel(h["file"]) and h["file"].endswith(".lean")):
                errors.append(f"headline[{i}].file must be a repo-relative .lean path")
            s = h["summary"]
            if not isinstance(s, str) or not s.strip() or "\n" in s.strip() or len(s) > 300:
                errors.append(f"headline[{i}].summary must be one sentence (one line, at most 300 characters)")
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
        if where in ("entry.repo", "entry.reservoir") or ".doi" in where:
            continue
        if PRIVATE_PATH_RE.search(s):
            errors.append(f"{where} looks like a private local path: {s!r}")
    return errors


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
