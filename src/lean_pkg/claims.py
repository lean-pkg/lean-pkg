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


def schema_check_text(text: str, where: str, package: str | None = None) -> tuple[int | None, list[str]]:
    """(claim count, errors) for a claims file's text: YAML load plus `validate_claims`, the same
    schema check `claims sync` and `claims check` apply. The count is None when `claims` is not a list."""
    try:
        doc = load_claims_text(text, where)
    except core.Fail as e:
        return None, [str(e)]
    claims = doc.get("claims")
    return (len(claims) if isinstance(claims, list) else None), validate_claims(doc, package)


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
# coverage: is anything synced, and is there a legacy claims.json to migrate?

LEGACY_SUFFIX = "claims.json"
LEGACY_PRUNE = {".git", ".lake", "lake-packages", "node_modules", ".venv", "venv", "__pycache__", "build"}
LEGACY_LIMIT = 10


def find_legacy_claims(repo: pathlib.Path) -> list[str]:
    """Repo-relative paths of legacy `*claims.json` files in the working tree (build and VCS
    directories pruned; at most LEGACY_LIMIT, sorted). lean-pkg never reads them as claims."""
    import os
    found = []
    for d, dirs, files in os.walk(repo):
        dirs[:] = sorted(x for x in dirs if x not in LEGACY_PRUNE and not x.startswith("."))
        for f in files:
            if f.lower().endswith(LEGACY_SUFFIX):
                found.append(pathlib.Path(d, f).relative_to(repo).as_posix())
    return sorted(found)[:LEGACY_LIMIT]


def coverage(name: str, pkg: dict) -> dict:
    """What claims coverage a package has: `synced` (cached claims), `empty` (an empty list was
    synced), `unsynced` (claims.yaml committed at HEAD, nothing cached) or `none` (no cached claims
    and no claims.yaml at HEAD). claims_yaml is present|absent|unknown (no checkout)."""
    claims, commit = pkg.get("claims") or [], pkg.get("claims_synced_commit")
    rel = pkg.get("claims_file") or DEFAULT_CLAIMS_FILE
    repo = core.repo_path(pkg) if pkg.get("local_path") else None
    checkout = repo is not None and repo.is_dir() and core.git_head(repo) is not None
    if not checkout:
        yaml_state = "unknown"
    else:
        yaml_state = "present" if idx._is_rel(rel) and core.git_show_bytes(repo, rel, "HEAD") is not None \
            else "absent"
    legacy = find_legacy_claims(repo) if checkout and not claims else []
    if claims:
        cov = "synced"
    elif commit is not None:
        cov = "empty"
    elif yaml_state == "present":
        cov = "unsynced"
    else:
        cov = "none"
    hint = None
    if cov == "unsynced":
        hint = f"{rel} is committed at HEAD but not synced: lean-pkg claims sync {name}"
    elif cov == "none" and legacy:
        hint = (f"legacy {legacy[0]} is not read as claims: lean-pkg claims migrate {name} --from "
                f"{legacy[0]} --dry-run, then without --dry-run, finish the TODOs, commit {rel} and run "
                f"lean-pkg claims sync {name}")
    elif cov == "none" and checkout:
        hint = (f"write {rel} (schema {CLAIMS_SCHEMA}) at the repo root, commit it, then "
                f"lean-pkg claims sync {name}")
    elif cov == "none":
        hint = f"no checkout at local_path; sync from one: lean-pkg claims sync {name}"
    return {"coverage": cov, "cached_claims": len(claims), "claims_yaml": yaml_state,
            "claims_file": rel, "legacy_claims": legacy, "hint": hint}


def coverage_lines(name: str, c: dict) -> list[str]:
    out = [f"{name}: coverage: {c['coverage']} (cached_claims: {c['cached_claims']}, "
           f"{c['claims_file']}: {c['claims_yaml']})"]
    out += [f"  legacy: {x} (not read by lean-pkg; migrate it)" for x in c["legacy_claims"]]
    if c["hint"]:
        out.append(f"  next: {c['hint']}")
    return out


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
    cov = coverage(name, reg["packages"][name]) if name is not None else None
    if not rows:
        if cov is not None:
            return coverage_lines(name, cov), {"ok": True, "claims": [], **cov}
        return ([f"no cached claims for any package; run `lean-pkg claims sync NAME`"],
                {"ok": True, "claims": []})
    head = ("ID", "KIND", "STATUS", "TRUST", "PIN", "DECL")
    table = [head] + [(r["id"], r["kind"], r["status"], r["trust"], str(r["pin"] or "")[:12],
                       r["decl"]) for r in rows]
    widths = [max(len(str(t[i])) for t in table) for i in range(len(head) - 1)]
    lines = ["  ".join(str(t[i]).ljust(widths[i]) for i in range(len(head) - 1)) + "  " + str(t[-1])
             for t in table]
    return [x.rstrip() for x in lines], {"ok": True, "claims": rows, **(cov or {})}


def do_claims_check(reg: dict, name: str | None) -> tuple[list[str], dict, int]:
    reports = []
    for n in _targets(reg, name):
        pkg = reg["packages"][n]
        claims = pkg.get("claims") or []
        commit = pkg.get("claims_synced_commit")
        rep = {"name": n, "ok": True, "claims": len(claims), "failures": [], "notes": []}
        if not claims and commit is None and name is None:
            continue
        if name is not None:
            rep.update(coverage(n, pkg))
            if rep["coverage"] in ("none", "unsynced"):
                rep["failures"].append(f"no claims coverage: coverage {rep['coverage']}, cached_claims 0, "
                                       f"{rep['claims_file']} {rep['claims_yaml']} at HEAD")
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
        if r.get("coverage") in ("none", "unsynced"):
            lines += coverage_lines(r["name"], r)
    lines.append(f"{len(reports)} package(s) checked: " + ("all pass" if ok else "FAILED"))
    return lines, {"ok": ok, "packages": reports}, (0 if ok else 1)


def do_claims_check_file(path: str, name: str | None = None) -> tuple[list[str], dict, int]:
    """`claims check --file PATH`: the schema check on a working-copy file, before it is committed.
    Reads no registry; NAME, when given, must be the file's `package`."""
    p = pathlib.Path(str(path)).expanduser()
    if not p.is_file():
        raise core.Fail(f"claims file not found: {p}")
    n, errors = schema_check_text(p.read_text(encoding="utf-8", errors="replace"), str(p), name)
    count = "?" if n is None else n
    if errors:
        lines = [f"FAIL {p}: {len(errors)} schema error(s) ({count} claim(s))"]
        lines += [f"  fail: {e}" for e in errors]
        lines.append(f"FAIL {p}: fix the errors above before you commit the file you will gate")
    else:
        lines = [f"PASS {p}: schema ok ({count} claim(s))"]
    return lines, {"ok": not errors, "path": str(p), "claims": n, "errors": errors}, (1 if errors else 0)


# --------------------------------------------------------------------------
# claims migrate: a legacy claims.json -> a claims/1 skeleton in the checkout (never the registry)

LEGACY_FIELDS = {
    "id": ("id", "claim_id", "key", "slug", "label", "name"),
    "decl": ("decl", "lean_decl", "declaration", "lean_name", "lean_theorem", "lean", "theorem"),
    "kind": ("kind", "claim_kind", "type"),
    "summary": ("summary", "statement", "description", "title", "text", "claim"),
    "file": ("file", "lean_file", "lean_path", "path", "source"),
    "status": ("status",),
    "citation": ("citation", "paper_ref", "manuscript_ref", "reference", "ref", "locator", "paper"),
    "keywords": ("keywords", "tags"),
    "aliases": ("aliases",),
    "depends_on": ("depends_on", "dependencies", "deps"),
}
KIND_ALIASES = {"def": "definition", "proposition": "theorem", "prop": "theorem", "corollary": "theorem",
                "lem": "lemma", "thm": "theorem", "structure": "construction"}
TODO = "TODO"


def legacy_rows(data) -> list:
    """The rows of a legacy claims JSON: a list, a list under claims|rows|items|entries, or a
    mapping id -> row (the id is kept as `id`)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("claims", "rows", "items", "entries"):
            if isinstance(data.get(k), list):
                return data[k]
        if data and all(isinstance(v, dict) for v in data.values()):
            return [{"id": k, **v} for k, v in data.items()]
    raise core.Fail("legacy claims JSON: expected a list of rows, or rows under claims|rows|items|entries")


def _first(row: dict, keys) -> tuple[str | None, object]:
    for k in keys:
        if row.get(k) not in (None, "", []):
            return k, row[k]
    return None, None


def _clean(v, limit: int = 300) -> str | None:
    """A one-line string, or None when it is missing, multi-line-only or names a local path."""
    if not isinstance(v, str) or not v.strip():
        return None
    line = " ".join(v.split())
    if idx.PRIVATE_PATH_RE.search(line):
        return None
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def migrate_rows(name: str, rows: list) -> tuple[dict, list[dict], list[str], int]:
    """(claims/1 document, per-claim TODO list, unmapped legacy keys, rows skipped). Mapped fields
    are copied; anything that cannot be mapped is a TODO marker sync will refuse (file TODO.lean)."""
    claims, todos, used, unmapped, skipped, ids = [], [], set(), set(), 0, {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            skipped += 1
            continue
        got = {}
        for field, keys in LEGACY_FIELDS.items():
            k, v = _first(row, keys)
            if k is not None:
                got[field] = v
                used.add(k)
        unmapped.update(k for k in row if k not in used and k not in
                        {x for keys in LEGACY_FIELDS.values() for x in keys})
        todo = []
        decl = got.get("decl") if isinstance(got.get("decl"), str) and got["decl"].strip() and \
            not any(ch.isspace() for ch in got["decl"].strip()) else None
        legacy_id = str(got.get("id")) if got.get("id") not in (None, "") else None
        base = slug_of_decl(legacy_id.rsplit("/", 1)[-1]) if legacy_id else \
            (slug_of_decl(decl) if decl else f"claim-{i + 1}")
        slug, n = base, 2
        while slug in ids.values():
            slug, n = f"{base}-{n}", n + 1
        if legacy_id:
            ids[legacy_id] = slug
        cid = f"{name}/{slug}"
        kind = str(got.get("kind") or "").strip().lower()
        kind = KIND_ALIASES.get(kind, kind)
        if kind not in CLAIM_KINDS:
            todo.append("kind (defaulted to theorem)")
            kind = "theorem"
        f = got.get("file")
        f = f if isinstance(f, str) and f.endswith(".lean") and idx._is_rel(f) else None
        summary = _clean(got.get("summary"))
        status = got.get("status") if got.get("status") in CLAIM_STATUSES and got.get("status") != "superseded" \
            else None
        if decl is None:
            todo.append("decl")
        if f is None:
            todo.append("file")
        if summary is None:
            todo.append("summary")
        if status is None:
            todo.append("status (defaulted to experimental)")
        kw = got.get("keywords") if isinstance(got.get("keywords"), list) else []
        kw = sorted({slug_of_decl(k) for k in kw if isinstance(k, str) and slug_of_decl(k) != "claim"})
        al = [a for a in (_clean(x, 120) for x in got.get("aliases") or [] if isinstance(x, str)) if a]
        claims.append({"id": cid, "decl": decl or TODO, "kind": kind,
                       "summary": summary or f"{TODO}: one sentence.", "aliases": al, "keywords": kw,
                       "file": f or f"{TODO}.lean", "status": status or "experimental",
                       "superseded_by": None, "citation": _clean(got.get("citation")),
                       "depends_on": [d for d in got.get("depends_on") or [] if isinstance(d, str)]})
        if todo:
            todos.append({"id": cid, "todo": todo})
    for c in claims:      # depends_on: keep only references to rows migrated here
        c["depends_on"] = sorted({f"{name}/{ids[d]}" for d in c["depends_on"] if d in ids} - {c["id"]})
    return ({"schema": CLAIMS_SCHEMA, "package": name, "claims": claims}, todos, sorted(unmapped), skipped)


def do_claims_migrate(reg: dict, name: str, src: str, out: str | None, dry_run: bool,
                      agent: str | None) -> tuple[list[str], dict, str]:
    import json
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg) if pkg.get("local_path") else None
    if repo is None or not repo.is_dir():
        raise core.Fail(f"{name}: no checkout at local_path; nothing to migrate into")
    rel = out or pkg.get("claims_file") or DEFAULT_CLAIMS_FILE
    if not idx._is_rel(rel):
        raise core.UsageError(f"--out must be repo-relative, got {rel!r}")
    path = pathlib.Path(src).expanduser()
    path = path if path.is_absolute() else repo / path
    if not path.is_file():
        raise core.Fail(f"{name}: --from {src} is not a file (relative paths are read from the checkout)")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise core.Fail(f"{name}: --from {src} is not JSON: {e}")
    doc, todos, unmapped, skipped = migrate_rows(name, legacy_rows(data))
    header = [f"# Migrated by `lean-pkg claims migrate {name}` from a legacy claims JSON. A skeleton:",
              f"# fix every TODO (sync refuses {TODO}.lean), delete these comments, commit, then run",
              f"# `lean-pkg claims sync {name}`. Nothing here is verified; trust comes from receipts."]
    header += [f"# TODO {t['id']}: {', '.join(t['todo'])}" for t in todos]
    if unmapped:
        header.append("# legacy keys not mapped: " + ", ".join(unmapped))
    text = "\n".join(header) + "\n" + dump_claims(doc)
    n, k = len(doc["claims"]), len(todos)
    payload = {"ok": True, "name": name, "from": src, "out": rel, "dry_run": dry_run, "written": False,
               "claims": n, "todo": k, "todos": todos, "unmapped_keys": unmapped, "rows_skipped": skipped,
               "next": [f"edit {rel}: resolve the TODO markers", f"git add {rel} && git commit",
                        f"lean-pkg claims sync {name}"]}
    summary = (f"{name}: {n} claim(s) from {src}, {k} with TODO markers"
               + (f", {skipped} non-mapping row(s) skipped" if skipped else ""))
    exists = (repo / rel).exists()
    if dry_run:
        note = f" ({rel} already exists: the real run will refuse; pass --out)" if exists else ""
        return [summary, f"{rel} (dry run, nothing written){note}:"], payload, text
    if exists:
        raise core.Fail(f"{name}: {rel} already exists in the checkout; refusing to overwrite "
                        f"(pass --out another path, or --dry-run to print)")
    lease = pkg.get("lease")
    core.refuse_invalid_lease(name, lease)
    if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) and agent != lease.get("agent"):
        raise core.Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                        f"nothing written")
    dest = repo / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(dest)
    payload["written"] = True
    return [summary, f"wrote {rel} (not committed, not synced; the registry is unchanged)",
            "next: " + "; ".join(payload["next"])], payload, text


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
        raise core.UsageError("claims needs an action: sync | list | check | migrate")
    return fn(args)


def run_sync(args) -> int:
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    reg = core.load_registry(reg_path)
    core.warn_lease(core.lease_actor(reg, args.name, args.agent, "claims sync"))
    out, payload = do_claims_sync(reg_path, reg, events_p, args.name)
    return _emit(args, out, payload)


def run_list(args) -> int:
    out, payload = do_claims_list(core.load_registry(core.registry_path(args)), args.name)
    return _emit(args, out, payload)


def run_migrate(args) -> int:
    out, payload, text = do_claims_migrate(core.load_registry(core.registry_path(args)), args.name,
                                           args.from_path, args.out, args.dry_run, args.agent)
    if args.json_out:
        core.print_json({**payload, "yaml": text if args.dry_run else None})
        return 0
    print("\n".join(out + ([text.rstrip("\n")] if args.dry_run else [])))
    return 0


def run_check(args) -> int:
    if getattr(args, "file", None) is not None:
        out, payload, rc = do_claims_check_file(args.file, args.name)
        return _emit(args, out, payload, rc)
    out, payload, rc = do_claims_check(core.load_registry(core.registry_path(args)), args.name)
    return _emit(args, out, payload, rc)


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("claims", help="claims.yaml: sync | list | check | migrate (named reusable results)")
    add_json(s)
    s.set_defaults(fn=cmd_claims, claims_fn=None)
    csub = s.add_subparsers(dest="claims_cmd")
    a = csub.add_parser("sync", help="read claims.yaml at HEAD of the checkout; validate; cache; event")
    a.add_argument("name")
    a.add_argument("--agent", default=None,
                   help="the agent acting (recorded as the event's actor); another agent's live lease then refuses")
    add_json(a)
    a.set_defaults(fn=cmd_claims, claims_fn=run_sync)
    b = csub.add_parser("list", help="table of cached claims with status and trust")
    b.add_argument("name", nargs="?", default=None)
    add_json(b)
    b.set_defaults(fn=cmd_claims, claims_fn=run_list)
    c = csub.add_parser("check", help="re-validate cached claims; exit 0/1 (--file: a working-copy file)")
    c.add_argument("name", nargs="?", default=None)
    c.add_argument("--file", default=None, metavar="PATH",
                   help="schema-check this claims file (e.g. claims.yaml before you commit it) instead of the "
                        "registry cache; schema only (no checkout, so declarations are not looked up: "
                        "`claims sync` and `gate` do that); reads no registry; NAME, if given, must be "
                        "its package")
    add_json(c)
    c.set_defaults(fn=cmd_claims, claims_fn=run_check)
    m = csub.add_parser("migrate", help="legacy claims.json -> a claims/1 skeleton in the checkout "
                                        "(never the registry)")
    m.add_argument("name")
    m.add_argument("--from", dest="from_path", required=True, metavar="PATH",
                   help="the legacy JSON (relative paths are read from the checkout)")
    m.add_argument("--out", default=None, help="repo-relative output (default: claims_file, else claims.yaml)")
    m.add_argument("--dry-run", action="store_true", help="print the skeleton; write nothing")
    m.add_argument("--agent", default=None, help="your lease agent, when the package is leased")
    add_json(m)
    m.set_defaults(fn=cmd_claims, claims_fn=run_migrate)
