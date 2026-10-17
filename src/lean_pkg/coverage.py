# lean-pkg coverage -- the paper-centric coverage file (coverage/1) and its checker
"""`lean-pkg coverage check NAME [--file PATH] [--allow-private-manuscript]`.

`claims.yaml` is declaration-centric: one row per Lean result. `coverage.yaml` (schema `coverage/1`, in
the package directory) is paper-centric: one row per numbered result of a citable manuscript, saying how
the formalization relates to it. `lean-pkg page` renders the README's paper-to-Lean map, the "Not
formalized" list and the status capsule from it; this module only parses and validates.

    schema: coverage/1
    package: NAME
    manuscript:
      title: TEXT
      version: "arXiv:<id>v<N>"                 # or {repo: https://..., commit: 40hex, sha256: 64hex}
                                                # or "unposted" (needs --allow-private-manuscript)
    summary: ONE SENTENCE                        # the capsule's hand-written sentence
    rows:
      - paper: {number: "Theorem 1.2", title: "Main estimate", label: "T:main"}   # label optional
        class: same-assumptions | inputs-supplied | outside
        claims: [NAME/claim-id, ...]             # ids in claims.yaml at HEAD; none for outside
        inputs: [TEXT, ...]                      # required for inputs-supplied; none otherwise
        note: TEXT                               # optional
    nothing_excluded: {reason: TEXT}             # only when no row is outside
    compute:                                     # optional: where the gates ran
      - cluster: easley
        jobs: [slurm-12345]                      # each a Slurm receipt tracked under .lean-receipts/
    lean_pkg_version: {version: 0.7.52, doi: 10.5281/zenodo.N}   # optional: the exact version credited

Strict: unknown or missing keys, values outside the closed enums and duplicate mapping keys are
refused. Classes: `same-assumptions` (the Lean statement carries the paper's hypotheses),
`inputs-supplied` (it takes the named `inputs` as hypotheses), `outside` (no Lean counterpart).
"""

from __future__ import annotations

import json
import pathlib
import re

import yaml

from . import cli as core

COVERAGE_SCHEMA = "coverage/1"
DEFAULT_COVERAGE_FILE = "coverage.yaml"
TOP_REQUIRED = ("schema", "package", "manuscript", "summary", "rows")
TOP_OPTIONAL = ("nothing_excluded", "compute", "lean_pkg_version")
ROW_REQUIRED = ("paper", "class")
ROW_OPTIONAL = ("claims", "inputs", "note")
PAPER_REQUIRED = ("number", "title")
PAPER_OPTIONAL = ("label",)
CLASSES = ("same-assumptions", "inputs-supplied", "outside")
CLASS_GLOSS = {"same-assumptions": "Same assumptions", "inputs-supplied": "Inputs supplied",
               "outside": "Outside this formalization"}
CLUSTERS = ("easley",)
UNPOSTED = "unposted"
ARXIV_VERSION_RE = re.compile(r"^arXiv:(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HTTPS_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~/%+-]+$")
SLURM_JOB_RE = re.compile(r"^slurm-\d+(?:_\d+)?$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ZENODO_DOI_RE = re.compile(r"^10\.5281/zenodo\.\d+$")
TEXT_MAX = 400


class _StrictLoader(yaml.SafeLoader):
    """safe_load, but a mapping that repeats a key is an error (PyYAML keeps the last one silently)."""


def _no_duplicates(loader, node, deep=False):
    seen = set()
    for key_node, _value in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            dup = key in seen
        except TypeError:
            dup = False
        if dup:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark)
        try:
            seen.add(key)
        except TypeError:
            pass
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def load_text(text: str, where: str) -> dict:
    try:
        data = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        raise core.Fail(f"{where}: not valid YAML: {' '.join(str(e).split())}")
    if not isinstance(data, dict):
        raise core.Fail(f"{where}: top level is not a mapping")
    return data


def _text(v, limit: int = TEXT_MAX) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "\n" not in v.strip() and len(v) <= limit


def _keys(obj, required, optional, where: str, errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: must be a mapping")
        return False
    unknown = [str(k) for k in obj if k not in required and k not in optional]
    missing = [k for k in required if k not in obj]
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(sorted(unknown))} "
                      f"(allowed: {', '.join(tuple(required) + tuple(optional))})")
    if missing:
        errors.append(f"{where}: missing key(s) {', '.join(missing)}")
    return not unknown and not missing


def _str_list(v, where: str, errors: list[str]) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(_text(x) for x in v):
        errors.append(f"{where}: must be a list of one-line strings (at most {TEXT_MAX} characters each)")
        return []
    if len(set(v)) != len(v):
        errors.append(f"{where}: repeats an entry")
    return list(v)


def manuscript_kind(version) -> str:
    """'arxiv', 'commit' or 'unposted' (assumes a validated version)."""
    if isinstance(version, dict):
        return "commit"
    return "unposted" if version == UNPOSTED else "arxiv"


def _manuscript_errors(m, errors: list[str]) -> None:
    if not _keys(m, ("title", "version"), (), "manuscript", errors):
        return
    if not _text(m["title"]):
        errors.append("manuscript.title: must be a one-line string")
    v = m["version"]
    if isinstance(v, str):
        if v != UNPOSTED and not ARXIV_VERSION_RE.match(v):
            errors.append(f"manuscript.version: must be an arXiv id with its version (arXiv:<id>v<N>), "
                          f"a mapping {{repo, commit, sha256}}, or {UNPOSTED!r}; got {v!r}")
    elif isinstance(v, dict):
        if _keys(v, ("repo", "commit", "sha256"), (), "manuscript.version", errors):
            if not (isinstance(v["repo"], str) and HTTPS_URL_RE.match(v["repo"])):
                errors.append("manuscript.version.repo: must be a public https URL")
            if not (isinstance(v["commit"], str) and HEX40.match(v["commit"])):
                errors.append("manuscript.version.commit: must be a full 40-hex commit")
            if not (isinstance(v["sha256"], str) and HEX64.match(v["sha256"])):
                errors.append("manuscript.version.sha256: must be the 64-hex SHA-256 of the manuscript file")
    else:
        errors.append("manuscript.version: must be a string or a mapping {repo, commit, sha256}")


def validate(doc, package: str | None = None) -> list[str]:
    """Schema errors of a coverage/1 document (claim ids and receipts are checked by check_against)."""
    errors: list[str] = []
    if not _keys(doc, TOP_REQUIRED, TOP_OPTIONAL, "<root>", errors) and not isinstance(doc, dict):
        return errors
    if doc.get("schema") != COVERAGE_SCHEMA:
        errors.append(f"schema: must be {COVERAGE_SCHEMA!r}, got {doc.get('schema')!r}")
    if "package" in doc and not (isinstance(doc["package"], str) and doc["package"].strip()):
        errors.append("package: must be the package name")
    elif package and "package" in doc and doc["package"] != package:
        errors.append(f"package: {doc['package']!r} is not {package!r}")
    if "manuscript" in doc:
        _manuscript_errors(doc["manuscript"], errors)
    if "summary" in doc and not _text(doc["summary"]):
        errors.append(f"summary: must be one line of at most {TEXT_MAX} characters")
    rows = doc.get("rows")
    numbers: dict[str, int] = {}
    n_outside = 0
    if "rows" in doc and (not isinstance(rows, list) or not rows):
        errors.append("rows: must be a non-empty list")
        rows = []
    for i, r in enumerate(rows or []):
        w = f"rows[{i}]"
        if not _keys(r, ROW_REQUIRED, ROW_OPTIONAL, w, errors):
            continue
        p = r["paper"]
        if _keys(p, PAPER_REQUIRED, PAPER_OPTIONAL, f"{w}.paper", errors):
            for k in ("number", "title") + (("label",) if "label" in p else ()):
                if not _text(p[k], 200):
                    errors.append(f"{w}.paper.{k}: must be a one-line string")
            num = p.get("number")
            if isinstance(num, str):
                if num in numbers:
                    errors.append(f"{w}.paper.number: {num!r} repeats rows[{numbers[num]}]")
                numbers.setdefault(num, i)
        cls = r["class"]
        if cls not in CLASSES:
            errors.append(f"{w}.class: must be one of {', '.join(CLASSES)}, got {cls!r}")
            continue
        claims = _str_list(r.get("claims"), f"{w}.claims", errors)
        inputs = _str_list(r.get("inputs"), f"{w}.inputs", errors)
        if "note" in r and not _text(r["note"]):
            errors.append(f"{w}.note: must be one line of at most {TEXT_MAX} characters")
        if cls == "outside":
            n_outside += 1
            if claims:
                errors.append(f"{w}: an outside row names no claims (a row with a Lean counterpart is "
                              f"same-assumptions or inputs-supplied)")
            if inputs:
                errors.append(f"{w}: an outside row has no inputs")
        else:
            if not claims:
                errors.append(f"{w}: a {cls} row names at least one claim id (claims.yaml)")
            if cls == "inputs-supplied" and not inputs:
                errors.append(f"{w}: an inputs-supplied row names its inputs (the hypotheses the Lean "
                              f"statement takes that the paper proves or assumes elsewhere)")
            if cls == "same-assumptions" and inputs:
                errors.append(f"{w}: a same-assumptions row has no inputs (use inputs-supplied)")
    ne = doc.get("nothing_excluded")
    if "nothing_excluded" in doc:
        if _keys(ne, ("reason",), (), "nothing_excluded", errors) and not _text(ne["reason"]):
            errors.append("nothing_excluded.reason: must be one line saying why nothing is excluded")
        if n_outside:
            errors.append("nothing_excluded: contradicts the outside row(s); drop one or the other")
    elif rows and not n_outside:
        errors.append("rows: no outside row: list what is not formalized (class outside), or state "
                      "nothing_excluded: {reason: ...}")
    comp = doc.get("compute")
    if "compute" in doc:
        if not isinstance(comp, list) or not comp:
            errors.append("compute: must be a non-empty list of {cluster, jobs}")
        else:
            seen = set()
            for i, c in enumerate(comp):
                w = f"compute[{i}]"
                if not _keys(c, ("cluster", "jobs"), (), w, errors):
                    continue
                if c["cluster"] not in CLUSTERS:
                    errors.append(f"{w}.cluster: must be one of {', '.join(CLUSTERS)}, got {c['cluster']!r}")
                elif c["cluster"] in seen:
                    errors.append(f"{w}.cluster: {c['cluster']!r} is listed twice")
                seen.add(c["cluster"])
                jobs = c["jobs"]
                if not isinstance(jobs, list) or not jobs or not all(
                        isinstance(j, str) and SLURM_JOB_RE.match(j) for j in jobs):
                    errors.append(f"{w}.jobs: must be a non-empty list of Slurm job ids (slurm-12345)")
                elif len(set(jobs)) != len(jobs):
                    errors.append(f"{w}.jobs: repeats a job id")
    lv = doc.get("lean_pkg_version")
    if "lean_pkg_version" in doc and _keys(lv, ("version", "doi"), (), "lean_pkg_version", errors):
        if not (isinstance(lv["version"], str) and VERSION_RE.match(lv["version"])):
            errors.append("lean_pkg_version.version: must be a release version such as 0.7.52")
        if not (isinstance(lv["doi"], str) and ZENODO_DOI_RE.match(lv["doi"])):
            errors.append("lean_pkg_version.doi: must be a Zenodo version DOI (10.5281/zenodo.N)")
    return errors


# --------------------------------------------------------------------------
# joins with the package: claims.yaml, receipts, lean-pkg's own CITATION.cff

def claims_at_head(pkg: dict, repo: pathlib.Path) -> tuple[dict[str, dict], str]:
    """{id: claim} from the claims file committed at HEAD, and its path; refuses when absent or invalid."""
    from . import claims as claims_mod
    rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
    raw = core.git_show_bytes(repo, rel)
    if raw is None:
        raise core.Fail(f"{pkg['name']}: {rel} is not committed at HEAD; coverage rows name claim ids from it")
    doc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), f"{rel} at HEAD")
    errors = claims_mod.validate_claims(doc, pkg["name"])
    if errors:
        raise core.Fail(f"{rel} at HEAD is not a valid claims/1 file:\n  " + "\n  ".join(errors[:10]))
    return {str(c.get("id")): c for c in doc.get("claims") or [] if isinstance(c, dict)}, rel


def tracked_receipts(repo: pathlib.Path) -> list[str]:
    """Receipt JSON files tracked at HEAD directly under .lean-receipts/ (package-relative, sorted)."""
    p = core.git(repo, "ls-tree", "-z", "--name-only", "HEAD", "--", ".lean-receipts/")
    if p.returncode != 0:
        return []
    out = [x for x in p.stdout.split("\0") if x.endswith(".json") and x.count("/") == 1]
    return sorted(out)


def receipt_at_head(repo: pathlib.Path, rel: str) -> tuple[dict | None, bytes | None]:
    raw = core.git_show_bytes(repo, rel)
    if raw is None:
        return None, None
    try:
        data = json.loads(raw)
    except ValueError:
        return None, raw
    return (data if isinstance(data, dict) else None), raw


def slurm_job_of(rec: dict) -> str | None:
    """The receipt's Slurm job as `slurm-<J>`, or None when the receipt does not show a Slurm run: a
    lower-tier receipt whose job_id is `slurm-<J>` (what `receipt slurm` writes), or a comparator receipt
    whose host_policy says it ran inside a Slurm job and whose job_id is that job's number. Other job ids
    (systemd units, local runs) carry host labels and are never copied."""
    job = rec.get("job_id")
    if isinstance(job, str) and SLURM_JOB_RE.match(job):
        return job
    if rec.get("schema") == "comparator-receipt/1" and isinstance(job, str) and re.fullmatch(r"\d+(?:_\d+)?", job) \
            and str(rec.get("host_policy") or "").startswith(f"Slurm job {job} "):
        return f"slurm-{job}"
    return None


def slurm_jobs_at_head(repo: pathlib.Path) -> dict[str, str]:
    """{slurm-<J>: receipt path} for every receipt tracked at HEAD that shows a Slurm run."""
    out: dict[str, str] = {}
    for rel in tracked_receipts(repo):
        rec, _raw = receipt_at_head(repo, rel)
        job = slurm_job_of(rec) if rec else None
        if job:
            out.setdefault(job, rel)
    return out


def compute_backing(doc: dict, repo: pathlib.Path) -> tuple[dict[str, list[str]], list[str]]:
    """({cluster: [backed job ids]}, problems): every listed job must be a Slurm receipt tracked at HEAD."""
    jobs = slurm_jobs_at_head(repo)
    backed: dict[str, list[str]] = {}
    problems: list[str] = []
    for c in doc.get("compute") or []:
        missing = [j for j in c["jobs"] if j not in jobs]
        if missing:
            problems.append(f"compute ({c['cluster']}): no Slurm receipt tracked at HEAD under .lean-receipts/ "
                            f"has job_id {', '.join(missing)} (commit the receipt, or drop the job)")
        else:
            backed[c["cluster"]] = list(c["jobs"])
    return backed, problems


def own_citation() -> dict | None:
    """lean-pkg's own CITATION.cff (a source checkout only; an installed wheel has none), parsed."""
    p = pathlib.Path(__file__).resolve().parents[2] / "CITATION.cff"
    try:
        data = yaml.safe_load(p.read_text())
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def own_version_dois(cff: dict | None) -> dict[str, str]:
    """{version: version DOI} from lean-pkg's CITATION.cff identifiers ("Version DOI for X.Y.Z")."""
    out: dict[str, str] = {}
    for ident in (cff or {}).get("identifiers") or []:
        if isinstance(ident, dict):
            m = re.fullmatch(r"Version DOI for (\d+\.\d+\.\d+)", str(ident.get("description") or ""))
            if m and ident.get("value"):
                out[m.group(1)] = str(ident["value"])
    return out


def version_doi_problems(doc: dict, cff: dict | None = None) -> tuple[list[str], list[str]]:
    """(problems, warnings) for lean_pkg_version: when lean-pkg's own CITATION.cff is readable it must
    list that version with exactly that DOI (a version DOI is never guessed)."""
    lv = doc.get("lean_pkg_version")
    if not lv:
        return [], []
    cff = own_citation() if cff is None else cff
    if cff is None:
        return [], ["lean_pkg_version: lean-pkg's own CITATION.cff is not readable here (an installed copy); "
                    "the version DOI is used as written"]
    known = own_version_dois(cff)
    if lv["version"] not in known:
        return [f"lean_pkg_version: lean-pkg's CITATION.cff lists no version DOI for {lv['version']} "
                f"(omit the key, or name a version that has one)"], []
    if known[lv["version"]] != lv["doi"]:
        return [f"lean_pkg_version: lean-pkg's CITATION.cff gives {known[lv['version']]} for "
                f"{lv['version']}, not {lv['doi']}"], []
    return [], []


def check_against(doc: dict, claims: dict[str, dict], claims_rel: str, repo: pathlib.Path | None
                  ) -> tuple[list[str], list[str]]:
    """(errors, warnings) of a schema-valid document against the package: claim ids, receipts, versions."""
    errors: list[str] = []
    warnings: list[str] = []
    for i, r in enumerate(doc["rows"]):
        for cid in r.get("claims") or []:
            c = claims.get(cid)
            if c is None:
                errors.append(f"rows[{i}].claims: {cid!r} is not a claim id in {claims_rel} at HEAD")
            elif c.get("status") == "superseded":
                errors.append(f"rows[{i}].claims: {cid!r} is superseded (by {c.get('superseded_by')!r}); "
                              f"name its successor")
    if repo is not None:
        _backed, problems = compute_backing(doc, repo)
        errors += problems
    p, w = version_doi_problems(doc)
    return errors + p, warnings + w


def read_doc(pkg: dict, repo: pathlib.Path, file: str | None = None) -> tuple[dict, str]:
    """(document, where): --file PATH from disk, else coverage.yaml committed at HEAD."""
    if file is not None:
        path = pathlib.Path(file)
        if not path.is_file():
            raise core.Fail(f"{file}: no such file")
        return load_text(path.read_text(encoding="utf-8", errors="replace"), file), file
    raw = core.git_show_bytes(repo, DEFAULT_COVERAGE_FILE)
    if raw is None:
        raise core.Fail(f"{pkg['name']}: {DEFAULT_COVERAGE_FILE} is not committed at HEAD (write one: "
                        f"docs/page.md#coverageyaml, then commit it)")
    where = f"{DEFAULT_COVERAGE_FILE} at HEAD"
    return load_text(raw.decode("utf-8", errors="replace"), where), where


def load_checked(pkg: dict, repo: pathlib.Path, file: str | None = None, allow_private: bool = False
                 ) -> tuple[dict, dict[str, dict], list[str]]:
    """(document, claims by id, warnings) of a fully valid coverage file; any problem refuses."""
    doc, where = read_doc(pkg, repo, file)
    errors = validate(doc, pkg["name"])
    if errors:
        raise core.Fail(f"{where} is not a valid {COVERAGE_SCHEMA} file:\n  " + "\n  ".join(errors))
    claims, claims_rel = claims_at_head(pkg, repo)
    errors, warnings = check_against(doc, claims, claims_rel, repo)
    if manuscript_kind(doc["manuscript"]["version"]) == "unposted" and not allow_private:
        errors.append("manuscript.version: unposted: theorem numbers of a private draft are not checkable by a "
                      "reader; pin a posted version (arXiv:IDvN or {repo, commit, sha256}), or pass "
                      "--allow-private-manuscript (the output is then marked)")
    if errors:
        raise core.Fail(f"{where}: {len(errors)} problem(s):\n  " + "\n  ".join(errors))
    return doc, claims, warnings


def package_checkout(reg: dict, name: str) -> tuple[dict, pathlib.Path]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    if core.git_head(repo) is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    return pkg, repo


def do_coverage_check(reg: dict, name: str, file: str | None, allow_private: bool) -> tuple[list[str], dict, int]:
    pkg, repo = package_checkout(reg, name)
    try:
        doc, _claims, warnings = load_checked(pkg, repo, file, allow_private)
    except core.Fail as e:
        lines = str(e).split("\n")
        problems = [x.strip() for x in lines[1:]] or [lines[0]]
        payload = {"ok": False, "name": name, "file": file or DEFAULT_COVERAGE_FILE, "rows": None,
                   "counts": None, "manuscript": None, "private_manuscript": None,
                   "errors": problems, "warnings": []}
        return [f"{name}: coverage: INVALID ({len(problems)} problem(s))", f"  {lines[0]}"] + \
            [f"  {x}" for x in problems if x != lines[0]], payload, 1
    counts = {c: sum(r["class"] == c for r in doc["rows"]) for c in CLASSES}
    kind = manuscript_kind(doc["manuscript"]["version"])
    payload = {"ok": True, "name": name, "file": file or DEFAULT_COVERAGE_FILE, "rows": len(doc["rows"]),
               "counts": counts, "manuscript": kind, "private_manuscript": kind == "unposted",
               "errors": [], "warnings": warnings}
    line = (f"{name}: coverage: OK; {len(doc['rows'])} row(s): " + ", ".join(f"{n} {c}" for c, n in counts.items())
            + f"; manuscript {kind}" + (" (PRIVATE DRAFT, --allow-private-manuscript)" if kind == "unposted" else ""))
    return [line] + [f"  warning: {w}" for w in warnings], payload, 0


def run_check(args) -> int:
    out, payload, rc = do_coverage_check(core.load_registry(core.registry_path(args)), args.name,
                                         args.file, args.allow_private_manuscript)
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    return rc


def cmd_coverage(args) -> int:
    fn = getattr(args, "coverage_fn", None)
    if fn is None:
        raise core.UsageError("coverage needs an action: check")
    return fn(args)


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("coverage", help="coverage.yaml (coverage/1): the paper-to-Lean map `page` renders")
    add_json(s)
    s.set_defaults(fn=cmd_coverage, coverage_fn=None)
    csub = s.add_subparsers(dest="coverage_cmd")
    c = csub.add_parser("check", help="validate coverage.yaml against claims.yaml and the receipts at HEAD; "
                                      "exit 0/1; read-only")
    c.add_argument("name")
    c.add_argument("--file", default=None, metavar="PATH",
                   help="check this working-copy file instead of coverage.yaml committed at HEAD (claim ids and "
                        "receipts are still read at HEAD)")
    c.add_argument("--allow-private-manuscript", action="store_true",
                   help="accept manuscript.version 'unposted' (a private draft); the output says so")
    add_json(c)
    c.set_defaults(fn=cmd_coverage, coverage_fn=run_check)
