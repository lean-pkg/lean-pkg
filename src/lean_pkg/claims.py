# lean-pkg claims -- named, reusable results declared next to the Lean source
"""Claims: `claims.yaml` (schema `claims/1`) at a package repository's root.

A claim is a named mathematical object or result someone might reuse. The file
travels with the commit; the registry only caches the last synced copy (with
the commit it was read at) so search works offline. `claims sync` reads the
file at HEAD, `claims list` prints the cache, `claims check` re-validates it.
Nothing derived is typed by hand; nothing hand-authored is inferred.
"""

from __future__ import annotations

import pathlib
import re

import yaml

from . import cli as core
from . import index as idx

CLAIMS_SCHEMA = "claims/1"
SUPPORTED_CLAIMS_SCHEMAS = (CLAIMS_SCHEMA,)
DEFAULT_CLAIMS_FILE = "claims.yaml"
CLAIMS_TOP_KEYS = ("schema", "package", "claims")
CLAIM_KEYS = ("id", "decl", "kind", "summary", "aliases", "keywords", "file", "status",
              "superseded_by", "citation", "depends_on")
REQUIRED_CLAIM_KEYS = ("id", "decl", "kind", "summary", "file", "status")
CLAIM_DEFAULTS = {"aliases": [], "keywords": [], "superseded_by": None, "citation": None,
                  "depends_on": []}
CLAIM_KINDS = ("definition", "lemma", "theorem", "construction", "library")
CLAIM_STATUSES = ("reusable", "internal", "experimental", "superseded")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CLAIM_ID_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)/([a-z0-9][a-z0-9-]*)$")
KEYWORD_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def slug_of_decl(decl: str) -> str:
    """Kebab-case slug of a Lean name: `Demo.foo_le_bar` -> `demo-foo-le-bar`."""
    s = re.sub(r"[^a-z0-9]+", "-", str(decl).lower()).strip("-")
    return s or "claim"


def package_trust(pkg: dict) -> str:
    """The package's trust as the index computes it from the registry's evidence pointers:
    palomar (a registered, not withdrawn id), comparator, lower-tier, else none."""
    if any(p.get("id") and p.get("version") and not p.get("withdrawn_at")
           for p in (pkg.get("palomar") or []) if isinstance(p, dict)):
        return "palomar"
    gate = pkg.get("gate") or {}
    if gate.get("comparator"):
        return "comparator"
    if gate.get("receipt"):
        return "lower-tier"
    return "none"


# --------------------------------------------------------------------------
# schema: load, validate, dump

def load_claims_text(text: str, where: str) -> dict:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise core.Fail(f"{where}: not valid YAML: {e}")
    if not isinstance(data, dict):
        raise core.Fail(f"{where}: top level is not a mapping")
    return data


def normalize_claim(c: dict) -> dict:
    """A claim with every key present, in CLAIM_KEYS order (defaults filled)."""
    return {k: (c[k] if k in c else CLAIM_DEFAULTS.get(k)) for k in CLAIM_KEYS}


def dump_claims(doc: dict) -> str:
    ordered = {"schema": doc["schema"], "package": doc["package"],
               "claims": [normalize_claim(c) for c in doc["claims"]]}
    return yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False,
                          allow_unicode=True, width=100)


def _one_line(s, limit: int = 300) -> bool:
    return isinstance(s, str) and bool(s.strip()) and "\n" not in s.strip() and len(s) <= limit


def _claim_errors(c, i: int, package: str | None) -> list[str]:
    where = f"claims[{i}]"
    if not isinstance(c, dict):
        return [f"{where} must be a mapping"]
    errors = []
    extra = sorted(set(c) - set(CLAIM_KEYS))
    missing = [k for k in REQUIRED_CLAIM_KEYS if k not in c]
    if extra:
        errors.append(f"{where}: unknown key(s) {extra} (schema {CLAIMS_SCHEMA} allows {list(CLAIM_KEYS)})")
    if missing:
        errors.append(f"{where}: missing key(s) {missing}")
    if extra or missing:
        return errors
    c = normalize_claim(c)
    m = CLAIM_ID_RE.match(str(c["id"])) if isinstance(c["id"], str) else None
    if not m:
        errors.append(f"{where}.id must be <package>/<slug> with slug [a-z0-9][a-z0-9-]*, got {c['id']!r}")
    elif package is not None and m.group(1) != package:
        errors.append(f"{where}.id {c['id']!r} must start with the package name {package!r}/")
    if not isinstance(c["decl"], str) or not c["decl"].strip() or any(ch.isspace() for ch in c["decl"]):
        errors.append(f"{where}.decl must be a Lean declaration name (no spaces)")
    if c["kind"] not in CLAIM_KINDS:
        errors.append(f"{where}.kind must be one of {CLAIM_KINDS}, got {c['kind']!r}")
    if c["status"] not in CLAIM_STATUSES:
        errors.append(f"{where}.status must be one of {CLAIM_STATUSES}, got {c['status']!r}")
    if not _one_line(c["summary"]):
        errors.append(f"{where}.summary must be one sentence (one line, at most 300 characters)")
    if not (idx._is_rel(c["file"]) and str(c["file"]).endswith(".lean")):
        errors.append(f"{where}.file must be a repo-relative .lean path, got {c['file']!r}")
    if not isinstance(c["aliases"], list) or not all(_one_line(a, 120) for a in c["aliases"]):
        errors.append(f"{where}.aliases must be a list of one-line phrases")
    if not isinstance(c["keywords"], list) or not all(
            isinstance(k, str) and KEYWORD_RE.match(k) for k in c["keywords"]):
        errors.append(f"{where}.keywords must be a list of lowercase tags ([a-z0-9-])")
    if c["citation"] is not None and not _one_line(c["citation"]):
        errors.append(f"{where}.citation must be null or one line")
    sb = c["superseded_by"]
    if sb is not None and not (isinstance(sb, str) and CLAIM_ID_RE.match(sb)):
        errors.append(f"{where}.superseded_by must be null or a claim id, got {sb!r}")
    if c["status"] == "superseded" and sb is None:
        errors.append(f"{where}: status superseded needs superseded_by (claims are never deleted)")
    if c["status"] != "superseded" and sb is not None:
        errors.append(f"{where}: superseded_by is set but status is {c['status']!r}, not superseded")
    if sb is not None and sb == c["id"]:
        errors.append(f"{where}: a claim cannot supersede itself")
    deps = c["depends_on"]
    if not isinstance(deps, list) or not all(isinstance(d, str) and CLAIM_ID_RE.match(d) for d in deps):
        errors.append(f"{where}.depends_on must be a list of claim ids <package>/<slug>")
    elif c["id"] in deps:
        errors.append(f"{where}: a claim cannot depend on itself")
    return errors


def validate_claims(doc, package: str | None = None) -> list[str]:
    """Every error in a `claims/1` document; [] when valid. Unknown keys are refused."""
    if not isinstance(doc, dict):
        return ["claims file is not a mapping"]
    errors: list[str] = []
    extra = sorted(set(doc) - set(CLAIMS_TOP_KEYS))
    missing = [k for k in CLAIMS_TOP_KEYS if k not in doc]
    if extra:
        errors.append(f"unknown key(s) {extra} (schema {CLAIMS_SCHEMA} allows {list(CLAIMS_TOP_KEYS)})")
    if missing:
        errors.append(f"missing key(s) {missing}")
        return errors
    if doc["schema"] not in SUPPORTED_CLAIMS_SCHEMAS:
        errors.append(f"schema must be one of {SUPPORTED_CLAIMS_SCHEMAS}, got {doc['schema']!r}")
    pkg_name = doc["package"]
    if not isinstance(pkg_name, str) or not idx.NAME_RE.fullmatch(pkg_name):
        errors.append(f"package {pkg_name!r} must be a registry name")
        pkg_name = None
    elif package is not None and pkg_name != package:
        errors.append(f"package {pkg_name!r} differs from {package!r}")
    claims = doc["claims"]
    if not isinstance(claims, list):
        return errors + ["claims must be a list"]
    for i, c in enumerate(claims):
        errors += _claim_errors(c, i, pkg_name)
    ids = [c.get("id") for c in claims if isinstance(c, dict)]
    dups = sorted({i for i in ids if isinstance(i, str) and ids.count(i) > 1})
    if dups:
        errors.append(f"duplicate claim id(s) {dups}")
    by_id = {c["id"]: c for c in claims if isinstance(c, dict) and isinstance(c.get("id"), str)}
    for cid, c in by_id.items():
        sb = c.get("superseded_by")
        if isinstance(sb, str) and pkg_name and sb.startswith(pkg_name + "/") and sb not in by_id:
            errors.append(f"{cid}: superseded_by {sb!r} is not a claim in this file")
        seen = [cid]
        while isinstance(sb, str) and sb in by_id:
            if sb in seen:
                errors.append(f"supersession cycle: {' -> '.join(seen + [sb])}")
                break
            seen.append(sb)
            sb = by_id[sb].get("superseded_by")
    for where, s in idx._strings(claims, "claims"):
        if idx.PRIVATE_PATH_RE.search(s):
            errors.append(f"{where} looks like a private local path: {s!r}")
    return sorted(set(errors), key=errors.index)


def decl_problems(read_file, claims: list[dict]) -> list[str]:
    """Each claim's declaration (its last name component) must occur in its file.
    `read_file(rel) -> str | None` reads the file at the commit being checked."""
    out = []
    for c in claims:
        text = read_file(c["file"])
        last = str(c["decl"]).rsplit(".", 1)[-1]
        if text is None:
            out.append(f"{c['id']}: file {c['file']} is not tracked at that commit")
        elif last not in text:
            out.append(f"{c['id']}: {last!r} does not occur in {c['file']}")
    return out


def _reader(repo: pathlib.Path, rev: str):
    def read(rel: str) -> str | None:
        b = core.git_show_bytes(repo, rel, rev)
        return None if b is None else b.decode("utf-8", errors="replace")
    return read


# --------------------------------------------------------------------------
# claims sync | list | check

def _tolerate_v2(pkg: dict) -> None:
    for k, d in core.V3_PACKAGE_DEFAULTS.items():
        pkg.setdefault(k, list(d) if isinstance(d, list) else d)


def do_claims_sync(reg_path, reg, events_p, name: str) -> tuple[list[str], dict]:
    pkg = core.get_pkg(reg, name)
    _tolerate_v2(pkg)
    repo = core.repo_path(pkg)
    head = core.git_head(repo) if repo.is_dir() else None
    if head is None:
        raise core.Fail(f"{name}: no git checkout at local_path ({repo})")
    rel = pkg.get("claims_file") or DEFAULT_CLAIMS_FILE
    if not idx._is_rel(rel):
        raise core.Fail(f"{name}: claims_file must be repo-relative, got {rel!r}")
    blob = core.git_show_bytes(repo, rel, "HEAD")
    if blob is None:
        if not (repo / rel).exists():
            raise core.Fail(f"{name}: {rel} is missing in {repo} (write it at the repo root, schema "
                            f"claims/1, and commit it)")
        raise core.Fail(f"{name}: {rel} is not committed at HEAD {head[:12]} "
                        f"(untracked files do not count; commit it first)")
    doc = load_claims_text(blob.decode("utf-8", errors="replace"), rel)
    if isinstance(doc.get("package"), str) and doc["package"] != name:
        raise core.Fail(f"{name}: {rel} declares package {doc['package']!r}, not {name!r}")
    errors = validate_claims(doc, name)
    if not errors:
        errors = decl_problems(_reader(repo, head), doc["claims"])
    if errors:
        raise core.Fail(f"{name}: {rel} refused: " + "; ".join(errors))
    claims = [normalize_claim(c) for c in doc["claims"]]
    if pkg.get("claims") == claims and pkg.get("claims_synced_commit") == head:
        return ([f"{name}: claims already synced at {head[:12]}; nothing written"],
                {"ok": True, "name": name, "event": "claims-noop", "commit": head,
                 "claims": len(claims)})
    pkg["claims_file"] = rel
    pkg["claims"] = claims
    pkg["claims_synced_commit"] = head
    core.record(reg, events_p, pkg, "claims-synced", commit=head, file=rel, claims=len(claims))
    core.save_registry(reg_path, reg)
    return ([f"{name}: {len(claims)} claim(s) synced from {rel} at {head[:12]}"],
            {"ok": True, "name": name, "event": "claims-synced", "commit": head,
             "claims": len(claims)})


def _targets(reg: dict, name: str | None) -> list[str]:
    if name is not None:
        core.get_pkg(reg, name)
        return [name]
    return sorted(reg.get("packages") or {})


def do_claims_list(reg: dict, name: str | None) -> tuple[list[str], dict]:
    rows = []
    for n in _targets(reg, name):
        pkg = reg["packages"][n]
        trust = package_trust(pkg)
        for c in pkg.get("claims") or []:
            rows.append({"package": n, "id": c.get("id"), "decl": c.get("decl"),
                         "kind": c.get("kind"), "status": c.get("status"), "trust": trust,
                         "summary": c.get("summary"),
                         "pin": pkg.get("claims_synced_commit")})
    if not rows:
        what = name or "any package"
        return ([f"no cached claims for {what}; run `lean-pkg claims sync NAME`"],
                {"ok": True, "claims": []})
    head = ("ID", "KIND", "STATUS", "TRUST", "PIN", "DECL")
    table = [head] + [(r["id"], r["kind"], r["status"], r["trust"], str(r["pin"] or "")[:12],
                       r["decl"]) for r in rows]
    widths = [max(len(str(t[i])) for t in table) for i in range(len(head) - 1)]
    lines = ["  ".join(str(t[i]).ljust(widths[i]) for i in range(len(head) - 1)) + "  " + str(t[-1])
             for t in table]
    return [x.rstrip() for x in lines], {"ok": True, "claims": rows}


def do_claims_check(reg: dict, name: str | None) -> tuple[list[str], dict, int]:
    reports = []
    for n in _targets(reg, name):
        pkg = reg["packages"][n]
        claims = pkg.get("claims") or []
        commit = pkg.get("claims_synced_commit")
        rep = {"name": n, "ok": True, "claims": len(claims), "failures": [], "notes": []}
        if not claims and commit is None:
            if name is None:
                continue
            rep["notes"].append("no claims synced")
        rep["failures"] += validate_claims({"schema": CLAIMS_SCHEMA, "package": n, "claims": claims}, n)
        if claims and not core._HEX40.fullmatch(str(commit or "")):
            rep["failures"].append(f"claims_synced_commit must be 40 lowercase hex, got {commit!r}")
        repo = core.repo_path(pkg) if pkg.get("local_path") else None
        if not rep["failures"] and claims:
            if repo is None or not repo.is_dir() or core.git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode:
                rep["notes"].append(f"decl check skipped: no checkout holding {str(commit)[:12]}")
            else:
                rep["failures"] += decl_problems(_reader(repo, commit), claims)
                rel = pkg.get("claims_file") or DEFAULT_CLAIMS_FILE
                if core.git_show_bytes(repo, rel, "HEAD") != core.git_show_bytes(repo, rel, commit):
                    rep["notes"].append(f"{rel} changed since {commit[:12]}; run `lean-pkg claims sync {n}`")
        rep["ok"] = not rep["failures"]
        reports.append(rep)
    ok = all(r["ok"] for r in reports)
    lines = []
    for r in reports:
        lines.append(f"{'PASS' if r['ok'] else 'FAIL'} {r['name']} ({r['claims']} claim(s))")
        lines += [f"  fail: {x}" for x in r["failures"]] + [f"  note: {x}" for x in r["notes"]]
    lines.append(f"{len(reports)} package(s) checked: " + ("all pass" if ok else "FAILED"))
    return lines, {"ok": ok, "packages": reports}, (0 if ok else 1)


# --------------------------------------------------------------------------
# CLI glue: lean_pkg.cli.main calls add_parsers(sub, add_json)

def _emit(args, out: list[str], payload: dict, rc: int = 0) -> int:
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    return rc


def cmd_claims(args) -> int:
    fn = getattr(args, "claims_fn", None)
    if fn is None:
        raise core.UsageError("claims needs an action: sync | list | check")
    return fn(args)


def run_sync(args) -> int:
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    out, payload = do_claims_sync(reg_path, core.load_registry(reg_path), events_p, args.name)
    return _emit(args, out, payload)


def run_list(args) -> int:
    out, payload = do_claims_list(core.load_registry(core.registry_path(args)), args.name)
    return _emit(args, out, payload)


def run_check(args) -> int:
    out, payload, rc = do_claims_check(core.load_registry(core.registry_path(args)), args.name)
    return _emit(args, out, payload, rc)


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("claims", help="claims.yaml: sync | list | check (named reusable results)")
    add_json(s)
    s.set_defaults(fn=cmd_claims, claims_fn=None)
    csub = s.add_subparsers(dest="claims_cmd")
    a = csub.add_parser("sync", help="read claims.yaml at HEAD of the checkout; validate; cache; event")
    a.add_argument("name")
    add_json(a)
    a.set_defaults(fn=cmd_claims, claims_fn=run_sync)
    b = csub.add_parser("list", help="table of cached claims with status and trust")
    b.add_argument("name", nargs="?", default=None)
    add_json(b)
    b.set_defaults(fn=cmd_claims, claims_fn=run_list)
    c = csub.add_parser("check", help="re-validate cached claims; exit 0/1")
    c.add_argument("name", nargs="?", default=None)
    add_json(c)
    c.set_defaults(fn=cmd_claims, claims_fn=run_check)
