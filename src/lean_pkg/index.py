# lean-pkg index -- the public, forkable catalog of trust-graded packages
"""Index entries: `index/<name>/entry.yaml` (schema `index-entry/2`; `index-entry/1`
still validates) plus verbatim receipt copies under `index/<name>/receipts/`.
`index-entry/2` adds `claims[]`, one claim per headline item, derived at `index add`.

Everything is a file. `index add` writes an entry from a package clone at its
pin; `index check` re-derives every claim from bytes at the pin; `index render`
writes the Markdown table. Trust is computed from evidence, never typed.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
import urllib.error
import urllib.request

import yaml

from . import cli as core


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


def entry_keys(entry: dict) -> tuple[str, ...]:
    """The key order of the entry's own schema (the current schema when it is unknown)."""
    return ENTRY_KEYS_BY_SCHEMA.get(entry.get("schema"), ENTRY_KEYS)


def index_claims(name: str, headline: list[dict], trust) -> list[dict]:
    """claims[] of an index-entry/2: one per headline item, derived, never typed."""
    from .claims import slug_of_decl
    return [{"id": f"{name}/{slug_of_decl(h['name'])}", "decl": h["name"], "kind": "theorem",
             "summary": h["summary"], "judged_as": h.get("judged_as"), "trust": trust}
            for h in headline]


def dump_entry(entry: dict) -> str:
    ordered = {k: entry[k] for k in entry_keys(entry)}
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
    extra = sorted(set(entry) - set(keys))
    if extra:
        errors.append(f"unknown key(s) {extra} (schema {entry.get('schema')} allows {list(keys)})")
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


def find_evidence(clone: pathlib.Path, rel: str, pin: str | None) -> tuple[str, bytes]:
    """(pin, bytes) of the evidence file `rel` as committed in the pin itself or in an
    evidence-only descendant of it, searched over every ref of the clone. With pin None,
    the pin is read from the receipt's own `commit` field."""
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
        ok, _ = core.evidence_only_descendant(clone, bound, c)
        if ok:
            return bound, blob
    raise core.Fail(f"{rel} is not committed at the pin or in an evidence-only descendant of it "
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


def materialize(clone: pathlib.Path, pin: str, rels: list[str]) -> dict[str, bytes]:
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
        _, blob = find_evidence(clone, rel, pin)
        _safe_write(clone, rel, blob)
        out[rel] = blob
    return out


def evidence_commit(clone: pathlib.Path, rel: str, pin: str) -> str:
    """The commit (pin or evidence-only descendant) whose version of `rel` find_evidence uses."""
    p = core.git(clone, "log", "--all", "--format=%H", "--", rel)
    for c in [x for x in p.stdout.split() if x]:
        if core.git_show_bytes(clone, rel, c) is None:
            continue
        ok, _ = core.evidence_only_descendant(clone, pin, c)
        if ok:
            return c
    raise core.Fail(f"{rel} is not committed at the pin or in an evidence-only descendant of it")


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


def private_strings_in(blob: bytes, where: str) -> list[str]:
    """String values in a JSON document that look like private local paths."""
    try:
        data = json.loads(blob)
    except ValueError:
        return []
    return [f"{where}: {w} looks like a private local path: {s!r}"
            for w, s in _strings(data, where) if PRIVATE_PATH_RE.search(s)]


def mathlib_rev_at(clone: pathlib.Path) -> str | None:
    mf = clone / "lake-manifest.json"
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text())
    except ValueError:
        raise core.Fail("lake-manifest.json at the pin is not valid JSON")
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


def derive(clone: pathlib.Path, pin: str, lower_rel: str | None,
           comp_rel: str | None) -> dict:
    """Validate the receipts at the pin with the registry's own validators.
    Returns {lower, comparator, lower_ok, comparator_ok, mechanism, bytes, failures}."""
    rels = [r for r in (lower_rel, comp_rel) if r]
    got = materialize(clone, pin, rels)
    res = {"lower": None, "comparator": None, "lower_ok": False, "comparator_ok": False,
           "mechanism": None, "bytes": got, "failures": []}
    if lower_rel:
        try:
            res["lower"] = json.loads(got[lower_rel])
            core.validate_receipt(PUBLIC_GATE_PKG, clone, res["lower"])
            if res["lower"]["commit"] != pin:
                raise core.Fail(f"lower-tier receipt binds {res['lower']['commit'][:12]}, not the pin")
            res["lower_ok"] = True
        except (ValueError, core.Fail) as e:
            res["failures"].append(f"lower-tier receipt {lower_rel}: {e}")
    if comp_rel:
        try:
            res["comparator"] = json.loads(got[comp_rel])
            res["mechanism"] = comparator_mechanism(res["comparator"])
            logs = [c.get("log") for c in res["comparator"].get("configs") or []
                    if isinstance(c, dict) and isinstance(c.get("log"), str)]
            got.update(materialize(clone, pin, logs))
            lower_commit = res["lower"]["commit"] if res["lower_ok"] else None
            core.validate_comparator(PUBLIC_GATE_PKG, clone, res["comparator"], lower_commit)
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


def judged_names(comp) -> set[str]:
    """Theorem and definition names the comparator receipt's configurations judged."""
    names: set[str] = set()
    for c in (comp or {}).get("configs") or []:
        if isinstance(c, dict):
            names.update(str(x) for x in (c.get("theorem_names") or []))
            names.update(str(x) for x in (c.get("definition_names") or []))
    return names


def unjudged_headlines(headline: list[dict], comp) -> list[str]:
    """Headline names whose `judged_as` is missing or not among the judged names."""
    judged = judged_names(comp)
    return [h["name"] for h in headline if not (h.get("judged_as") and h["judged_as"] in judged)]


def headline_problems(clone: pathlib.Path, headline: list[dict]) -> list[str]:
    out = []
    for h in headline:
        f = clone / h["file"]
        if not f.is_file():
            out.append(f"headline file {h['file']} is not tracked at the pin")
        elif h["name"].rsplit(".", 1)[-1] not in f.read_text(errors="replace"):
            out.append(f"headline {h['name']}: {h['name'].rsplit('.', 1)[-1]!r} does not occur in {h['file']}")
    return out


def at_pin_problems(clone: pathlib.Path, entry: dict, receipts: dict) -> list[str]:
    """Checks shared by add and check: toolchain, manifest, license, tag, headline."""
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
            ok, _ = core.evidence_only_descendant(clone, entry["pin"]["commit"], p.stdout.strip())
            if not ok:
                out.append(f"tag {tag!r} is neither the pin nor an evidence-only descendant of it")
    out += headline_problems(clone, entry["headline"])
    return out


def prior_art_notes(clone: pathlib.Path, headline: list[dict]) -> list[str]:
    """Advisory: headline items that no prior-art record at the pin names. A record under
    docs/prior-art/*.md counts for a headline when it contains the last component of its name."""
    d = clone / "docs" / "prior-art"
    texts = [p.read_text(errors="replace") for p in sorted(d.glob("*.md"))] if d.is_dir() else []
    missing = [h["name"] for h in headline
               if not any(h["name"].rsplit(".", 1)[-1] in t for t in texts)]
    if not missing:
        return []
    return [f"note: no prior-art record under docs/prior-art/ at the pin names {', '.join(missing)} "
            f"(advisory; `lean-pkg search TERM --record` writes one)"]


def do_index_add(root: pathlib.Path, events_p: pathlib.Path | None, reg: dict | None,
                 name: str, opts: dict) -> tuple[list[str], dict]:
    if not NAME_RE.fullmatch(name):
        raise core.Fail(f"bad entry name {name!r}")
    d = root / name
    if (d / ENTRY_FILE).exists():
        raise core.Fail(f"index entry {name!r} already exists at {d / ENTRY_FILE}; "
                        f"change it by pull request (there is no in-place update in {ENTRY_SCHEMA})")
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
    with tempfile.TemporaryDirectory(prefix="lean-pkg-index-") as tmp:
        clone = clone_at(source or repo, pathlib.Path(tmp) / "pkg")
        pin, _ = find_evidence(clone, lower_rel or comp_rel, None)
        palomar = [{"id": p["id"], "version": int(p["version"])} for p in palomar_rows
                   if p.get("commit") == pin]          # a registration at another commit does not grade this pin
        for other in entry_names(root):
            try:
                o = load_entry(root / other / ENTRY_FILE)
            except core.Fail:
                continue                                # a broken sibling is check's business, not add's
            if _repo_key(str(o.get("repo"))) == _repo_key(repo) and \
                    (o.get("pin") or {}).get("commit") == pin:
                raise core.Fail(f"duplicate: entry {other!r} already lists {repo} at {pin[:12]}")
        if source and not opts.get("allow_unpushed"):
            shas = sorted({pin, *[evidence_commit(clone, r, pin) for r in (lower_rel, comp_rel) if r]})
            missing = reachable_on(clone, repo, shas)
            if missing:
                raise core.Fail(f"commit(s) {', '.join(s[:12] for s in missing)} not reachable from any branch "
                                f"of {repo}; push the pin and the evidence first (or pass --allow-unpushed "
                                f"for a provisional entry that `index check --clone` will refuse until pushed)")
        res = derive(clone, pin, lower_rel, comp_rel)
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
            unjudged = unjudged_headlines(headline, res["comparator"])
            if unjudged:
                msg = (f"headline theorem(s) {', '.join(unjudged)} not judged by the comparator receipt "
                       f"(judged: {', '.join(sorted(judged_names(res['comparator']))) or 'none'}); "
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
            "fields": list(opts.get("fields") or []),
            "consent": {"recorded": consent_recorded},
            "added": {"at": core.now_iso(), "by": opts["by"]},
            "notes": opts.get("notes") or "",
        }
        errors = validate_entry(entry, name)
        errors += at_pin_problems(clone, entry, res)
        if errors:
            raise core.Fail("index entry refused: " + "; ".join(errors))
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
    if coverage_note:
        out.append(coverage_note)
    out += notes
    if source and opts.get("allow_unpushed"):
        out.append("provisional: --allow-unpushed skipped the public-repository check; "
                   "`index check --clone` will fail until the pin and evidence are pushed")
    if events_p is not None:
        core.append_event(events_p, core.now_iso(), name, "index-added", repo=repo,
                          commit=pin, trust=trust, index=str(root))
    else:
        out.append("no event log (no registry here and no --events): index-added not recorded")
    return out, {"ok": True, "name": name, "event": "index-added", "trust": trust,
                 "commit": pin, "mechanism": res["mechanism"], "entry": str(d / ENTRY_FILE),
                 "receipts": copied, "notes": notes}


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
        raise core.UsageError("index needs an action: add | check | render")
    return fn(args)


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
            "allow_unpushed": args.allow_unpushed}
    out, payload = do_index_add(root, events_p, reg, args.name, opts)
    return _emit(args, out, payload)


def _parser_add(isub, common) -> None:
    a = isub.add_parser("add", help="write <index>/<name>/entry.yaml from a clone at the pin")
    a.add_argument("name")
    a.add_argument("--from-registry", action="store_true",
                   help="start from the registry row (repo, receipts, license, palomar, doi, reservoir)")
    a.add_argument("--repo-dir", default=None, help="local clone to read from (never modified)")
    a.add_argument("--repo", default=None, help="public https URL (a GitHub ssh URL is converted)")
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
    common(a, run_add)


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("index", help="the public catalog: add | check | render")
    add_json(s)
    s.set_defaults(fn=cmd_index, index_fn=None)
    isub = s.add_subparsers(dest="index_cmd")

    def common(p, fn) -> None:
        p.add_argument("--index", default=None,
                       help=f"index root (default: ${INDEX_ENV}, else ./{DEFAULT_INDEX_DIR})")
        add_json(p)
        p.set_defaults(fn=cmd_index, index_fn=fn)

    _parser_add(isub, common)
    _parser_check(isub, common)
    _parser_render(isub, common)


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
                seen: dict) -> dict:
    rep = {"name": name, "ok": False, "trust_stated": None, "trust_computed": None,
           "mechanism": None, "failures": [], "notes": [],
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
        rep["notes"].append("static check only: pass --clone (or --repo-dir) to re-derive from bytes")
        rep["ok"] = not rep["failures"]
        return rep
    with tempfile.TemporaryDirectory(prefix="lean-pkg-index-") as tmp:
        try:
            clone = clone_at(source or entry["repo"], pathlib.Path(tmp) / "pkg")
            res = derive(clone, entry["pin"]["commit"], entry["receipts"]["lower"],
                         entry["receipts"]["comparator"])
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
        rep["failures"] += at_pin_problems(clone, entry, res)
    comparator_covers = res["comparator_ok"]
    if res["comparator_ok"]:
        unjudged = unjudged_headlines(entry["headline"], res["comparator"])
        if unjudged:
            comparator_covers = False
            rep["notes"].append(f"headline theorem(s) {', '.join(unjudged)} not judged by the comparator receipt")
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
                   clone_it: bool) -> tuple[list[str], dict, int]:
    if repo_dir and len(names) != 1:
        raise core.UsageError("--repo-dir needs exactly one NAME")
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
                            "notes": [], "network": {}, "trust_stated": None,
                            "trust_computed": None, "mechanism": None})
            continue
        reports.append(check_entry(root, n, repo_dir, clone_it, seen))
    ok = all(r["ok"] for r in reports)
    lines = []
    for r in reports:
        tag = "PASS" if r["ok"] else "FAIL"
        what = r.get("trust_computed") or r.get("trust_stated") or "?"
        mech = f", {r['mechanism']}" if r.get("mechanism") else ""
        lines.append(f"{tag} {r['name']} ({what}{mech})")
        lines += [f"  fail: {x}" for x in r["failures"]] + [f"  note: {x}" for x in r["notes"]]
    lines.append(f"{len(reports)} entr{'y' if len(reports) == 1 else 'ies'} checked: "
                 + ("all pass" if ok else "FAILED"))
    mode = "bytes" if (repo_dir or clone_it) else "static"
    return lines, {"ok": ok, "mode": mode, "index": str(root), "entries": reports}, (0 if ok else 1)


def run_check(args) -> int:
    out, payload, rc = do_index_check(index_root(args), args.names, args.repo_dir, args.clone)
    return _emit(args, out, payload, rc)


def _parser_check(isub, common) -> None:
    c = isub.add_parser("check", help="static check, or re-derive entries from bytes at the pin; exit 0/1/2")
    c.add_argument("names", nargs="*", metavar="NAME")
    c.add_argument("--clone", action="store_true", help="clone each entry's repo at the pin into a temp dir")
    c.add_argument("--repo-dir", default=None, help="local clone for a single NAME (never modified)")
    common(c, run_check)


RENDER_BANNER = ("<!-- machine-generated by `lean-pkg index render` from index/*/entry.yaml; "
                 "do not hand-edit -->")


# --------------------------------------------------------------------------
# render

def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_index(root: pathlib.Path) -> str:
    rows, claim_rows = [], []
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
                raise core.Fail(f"cannot render: receipt copy for {n!r} is not valid JSON")
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
        rows.append(f"| [{_cell(n)}]({e['repo']}) | {trust} | {_cell(tc)} | {head} | {pin} | "
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
        out += ["| Package | Trust | Toolchain | Headline theorem | Pin | Links |",
                "|---|---|---|---|---|---|", *rows]
        if legacy:
            out += ["", "`comparator (legacy)`: checked with the pinned comparator stack; it is not "
                        "predictive of Palomar acceptance, which runs the toolchain mechanism."]
        if claim_rows:
            out += ["", "## Claims", "",
                    "Derived from each `index-entry/2` headline; `lean-pkg search TERM` finds them.", "",
                    "| Claim | Declaration | Kind | Trust |", "|---|---|---|---|", *claim_rows]
    out.append("")
    return "\n".join(out)


def run_render(args) -> int:
    root = index_root(args)
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
