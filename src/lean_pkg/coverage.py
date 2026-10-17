# lean-pkg coverage -- the coverage file (coverage/1: a paper's results, or a library's) and its checker
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
      eprint_sha256: 64hex                      # optional, with an arXiv version: the e-print's SHA-256
                                                # (`formalization anchor` writes it and its source baseline)
    summary: ONE SENTENCE                        # the capsule's hand-written sentence
    rows:
      - paper: {number: "Theorem 1.2", title: "Main estimate", label: "T:main"}   # label optional
          # paper.statement (optional): the result as the paper states it, in TeX, at most 2000 characters
        class: same-assumptions | inputs-supplied | outside
        claims: [NAME/claim-id, ...]             # ids in claims.yaml at HEAD; none for outside
        inputs: [TEXT, ...]                      # required for inputs-supplied; none otherwise
        note: TEXT                               # optional
    nothing_excluded: {reason: TEXT}             # only when no row is outside
    compute:                                     # optional: where the gates ran
      - cluster: easley
        jobs: [slurm-12345]                      # each a Slurm receipt tracked under .lean-receipts/
    lean_pkg_version: {version: 0.7.52, doi: 10.5281/zenodo.N}   # optional: the exact version credited
    kind: paper                                  # optional; paper is the default

A reusable library with no manuscript of its own is `kind: library`: no `manuscript` and no
`nothing_excluded` (refused, so a library page never claims a paper), and each row is one of the library's
main results, named by its Lean declarations:

    schema: coverage/1
    package: NAME
    kind: library
    summary: ONE SENTENCE
    rows:
      - title: "Main estimate"                   # one line, unique in the file
        claims: [NAME/claim-id, ...]             # at least one id in claims.yaml at HEAD
        note: TEXT                               # optional
        statement: TEX                           # optional: the result in mathematical words
    compute: ...                                 # as above (the Easley rule is the same)
    lean_pkg_version: ...                        # as above

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
from . import sites

COVERAGE_SCHEMA = "coverage/1"
DEFAULT_COVERAGE_FILE = "coverage.yaml"
TOP_REQUIRED = ("schema", "package", "manuscript", "summary", "rows")
TOP_OPTIONAL = ("nothing_excluded", "compute", "lean_pkg_version", "kind")
KINDS = ("paper", "library")
LIBRARY_TOP_REQUIRED = ("schema", "package", "kind", "summary", "rows")
LIBRARY_TOP_OPTIONAL = ("compute", "lean_pkg_version")
LIBRARY_ROW_REQUIRED = ("title", "claims")
LIBRARY_ROW_OPTIONAL = ("note", "statement")
# keys of a paper coverage that a library refuses by name: each would state a manuscript, or a relation to one
LIBRARY_NO_PAPER = {
    "manuscript": "manuscript: a library coverage names no manuscript (kind: library); a package that formalizes "
                  "results of a paper is kind paper (the default) and pins the paper's posted version",
    "nothing_excluded": "nothing_excluded: states that nothing of a manuscript is excluded, and a library "
                        "coverage (kind: library) has no manuscript; drop it",
}
LIBRARY_ROW_NO_PAPER = ("paper", "class", "inputs")
ROW_REQUIRED = ("paper", "class")
ROW_OPTIONAL = ("claims", "inputs", "note")
PAPER_REQUIRED = ("number", "title")
PAPER_OPTIONAL = ("label", "statement")
CLASSES = ("same-assumptions", "inputs-supplied", "outside")
CLASS_GLOSS = {"same-assumptions": "Same assumptions", "inputs-supplied": "Inputs supplied",
               "outside": "Outside this formalization"}
CLUSTERS = sites.names()                        # the built-in site profiles; a package may add its own (sites.py)
UNPOSTED = "unposted"
ARXIV_VERSION_RE = re.compile(r"^arXiv:(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HTTPS_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~/%+-]+$")
SLURM_JOB_RE = sites.SLURM.job_re
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ZENODO_DOI_RE = re.compile(r"^10\.5281/zenodo\.\d+$")
TEXT_MAX = 400
STATEMENT_MAX = 2000                            # paper.statement / a library row's statement: TeX, may span lines
# lean-scope-page R4: a placeholder never reaches a public page (FILL-ME is the lab template's marker)
PLACEHOLDER_RE = re.compile(r"(?<![A-Za-z0-9])(?:FILL-ME|TODO|TBD|FIXME|XXX)(?![A-Za-z0-9])")


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


def _strings(obj, where: str):
    """(path, string) for every string value of a document, in document order."""
    if isinstance(obj, str):
        yield where, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{where}.{k}" if where else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{where}[{i}]")


def _statement_errors(v, where: str, errors: list[str]) -> None:
    """An optional statement: TeX text a reader compares with the Lean statement; several lines allowed.
    A line that opens a code fence is refused: the page shows the text inside one."""
    if not (isinstance(v, str) and v.strip() and len(v) <= STATEMENT_MAX):
        errors.append(f"{where}: must be a non-empty string of at most {STATEMENT_MAX} characters (TeX)")
    elif any(x.lstrip().startswith(("```", "~~~")) for x in v.splitlines()):
        errors.append(f"{where}: a line may not start with ``` or ~~~ (the page shows the statement in a "
                      f"code block)")


def doc_kind(doc) -> str:
    """'paper' or 'library' (kind is optional; paper is the default)."""
    return "library" if isinstance(doc, dict) and doc.get("kind") == "library" else "paper"


def manuscript_kind(version) -> str:
    """'arxiv', 'commit' or 'unposted' (assumes a validated version)."""
    if isinstance(version, dict):
        return "commit"
    return "unposted" if version == UNPOSTED else "arxiv"


def _manuscript_errors(m, errors: list[str]) -> None:
    if not _keys(m, ("title", "version"), ("eprint_sha256",), "manuscript", errors):
        return
    if "eprint_sha256" in m:   # additive (formalization anchor): only beside an arXiv version
        if not (isinstance(m["eprint_sha256"], str) and HEX64.match(m["eprint_sha256"])):
            errors.append("manuscript.eprint_sha256: must be the 64-hex SHA-256 of the arXiv e-print")
        if not (isinstance(m["version"], str) and ARXIV_VERSION_RE.match(m["version"])):
            errors.append("manuscript.eprint_sha256: goes with an arXiv version (arXiv:<id>v<N>)")
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


def validate(doc, package: str | None = None, profiles: dict | None = None) -> list[str]:
    """Schema errors of a coverage/1 document (claim ids and receipts are checked by check_against).
    profiles: the site profiles compute may name (sites.for_repo); None: the built-in ones."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        _keys(doc, TOP_REQUIRED, TOP_OPTIONAL, "<root>", errors)
        return errors
    if "kind" in doc and doc["kind"] not in KINDS:
        errors.append(f"kind: must be one of {', '.join(KINDS)}, got {doc['kind']!r}")
    library = doc_kind(doc) == "library"
    if library:
        _library_keys(doc, errors)
    else:
        _keys(doc, TOP_REQUIRED, TOP_OPTIONAL, "<root>", errors)
    _identity_errors(doc, package, errors)
    if not library and "manuscript" in doc:
        _manuscript_errors(doc["manuscript"], errors)
    if "summary" in doc and not _text(doc["summary"]):
        errors.append(f"summary: must be one line of at most {TEXT_MAX} characters")
    if library:
        _library_rows_errors(doc, errors)
    else:
        _paper_rows_errors(doc, errors)
    _tail_errors(doc, errors, sites.builtin() if profiles is None else profiles)
    return errors


def _identity_errors(doc: dict, package: str | None, errors: list[str]) -> None:
    if doc.get("schema") != COVERAGE_SCHEMA:
        errors.append(f"schema: must be {COVERAGE_SCHEMA!r}, got {doc.get('schema')!r}")
    if "package" in doc and not (isinstance(doc["package"], str) and doc["package"].strip()):
        errors.append("package: must be the package name")
    elif package and "package" in doc and doc["package"] != package:
        errors.append(f"package: {doc['package']!r} is not {package!r}")


def _tail_errors(doc: dict, errors: list[str], profiles: dict) -> None:
    """compute, placeholders and lean_pkg_version: the same for both kinds."""
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
                if c["cluster"] not in profiles:
                    errors.append(f"{w}.cluster: must be one of {', '.join(profiles)}, got {c['cluster']!r}")
                elif c["cluster"] in seen:
                    errors.append(f"{w}.cluster: {c['cluster']!r} is listed twice")
                seen.add(c["cluster"])
                # an unknown cluster is refused above; its jobs are still read as Slurm ids, as before
                sched = sites.scheduler_of(profiles[c["cluster"]]) if c["cluster"] in profiles else sites.SLURM
                jobs = c["jobs"]
                if not isinstance(jobs, list) or not jobs or not all(
                        isinstance(j, str) and sched.job_re.match(j) for j in jobs):
                    errors.append(f"{w}.jobs: must be a non-empty list of {sched.label} job ids ({sched.example})")
                elif len(set(jobs)) != len(jobs):
                    errors.append(f"{w}.jobs: repeats a job id")
    for where, s in _strings(doc, ""):
        if PLACEHOLDER_RE.search(s):
            errors.append(f"{where or '<root>'}: carries the placeholder {PLACEHOLDER_RE.search(s).group(0)!r}; "
                          f"write the text (a placeholder never reaches the page)")
    lv = doc.get("lean_pkg_version")
    if "lean_pkg_version" in doc and _keys(lv, ("version", "doi"), (), "lean_pkg_version", errors):
        if not (isinstance(lv["version"], str) and VERSION_RE.match(lv["version"])):
            errors.append("lean_pkg_version.version: must be a release version such as 0.7.52")
        if not (isinstance(lv["doi"], str) and ZENODO_DOI_RE.match(lv["doi"])):
            errors.append("lean_pkg_version.doi: must be a Zenodo version DOI (10.5281/zenodo.N)")


def _paper_rows_errors(doc: dict, errors: list[str]) -> None:
    """The rows and nothing_excluded of a kind: paper document."""
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
            if "statement" in p:
                _statement_errors(p["statement"], f"{w}.paper.statement", errors)
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


def _library_keys(doc: dict, errors: list[str]) -> None:
    """The top-level keys of a kind: library document; a paper key is refused by name."""
    for key, why in LIBRARY_NO_PAPER.items():
        if key in doc:
            errors.append(why)
    _keys({k: v for k, v in doc.items() if k not in LIBRARY_NO_PAPER}, LIBRARY_TOP_REQUIRED,
          LIBRARY_TOP_OPTIONAL, "<root>", errors)


def _library_rows_errors(doc: dict, errors: list[str]) -> None:
    """The rows of a kind: library document: one main result each, named by its claims."""
    rows = doc.get("rows")
    if "rows" in doc and (not isinstance(rows, list) or not rows):
        errors.append("rows: must be a non-empty list")
        return
    titles: dict[str, int] = {}
    for i, r in enumerate(rows or []):
        w = f"rows[{i}]"
        if isinstance(r, dict) and any(k in r for k in LIBRARY_ROW_NO_PAPER):
            errors.append(f"{w}: a library row is a main result of the library (title, claims, note), not a "
                          f"paper result: drop {', '.join(k for k in LIBRARY_ROW_NO_PAPER if k in r)} (the Lean "
                          f"statement is the result; a library coverage has no manuscript to correspond to)")
            r = {k: v for k, v in r.items() if k not in LIBRARY_ROW_NO_PAPER}
        if not _keys(r, LIBRARY_ROW_REQUIRED, LIBRARY_ROW_OPTIONAL, w, errors):
            continue
        if not _text(r["title"], 200):
            errors.append(f"{w}.title: must be a one-line string")
        elif r["title"] in titles:
            errors.append(f"{w}.title: {r['title']!r} repeats rows[{titles[r['title']]}]")
        else:
            titles[r["title"]] = i
        if r["claims"] is None or r["claims"] == []:
            errors.append(f"{w}: a library row names at least one claim id (claims.yaml)")
        else:
            _str_list(r["claims"], f"{w}.claims", errors)
        if "note" in r and not _text(r["note"]):
            errors.append(f"{w}.note: must be one line of at most {TEXT_MAX} characters")
        if "statement" in r:
            _statement_errors(r["statement"], f"{w}.statement", errors)


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


def slurm_job_of(rec: dict, repo: pathlib.Path | None = None) -> str | None:
    """The receipt's Slurm job as `slurm-<J>`, or None when the receipt does not show a Slurm run: a
    lower-tier receipt whose job_id is `slurm-<J>` (what `receipt slurm` writes after sacct), or a comparator
    receipt whose claim (job_id J, host_policy `Slurm job J ...`, from SLURM_JOB_ID, which any shell can set)
    is corroborated by its sacct seal committed at HEAD of `repo` (receipt.sacct_seal_problem). Without
    `repo`, or without a valid seal, a comparator receipt shows no Slurm run. Other job ids (systemd units,
    local runs) carry host labels and are never copied."""
    job = rec.get("job_id")
    if isinstance(job, str) and SLURM_JOB_RE.match(job):
        return job
    if rec.get("schema") in core.COMPARATOR_SCHEMAS and repo is not None and unsealed_comparator_job(rec, repo) is None \
            and receipt_mod().comparator_slurm_job(rec):
        return f"slurm-{job}"
    return None


def receipt_mod():
    from . import receipt
    return receipt


def unsealed_comparator_job(rec: dict, repo: pathlib.Path) -> str | None:
    """Why a comparator receipt's Slurm claim is not corroborated (receipt.sacct_seal_problem against the
    seal file committed at HEAD), or None when it is; also a reason when it claims no Slurm job."""
    r = receipt_mod()
    block = rec.get("slurm_sacct")
    rel = block.get("file") if isinstance(block, dict) and isinstance(block.get("file"), str) else None
    sealed = core.git_show_bytes(repo, f"./{rel}") if rel and rel.startswith(".lean-receipts/") else None
    return r.sacct_seal_problem(rec, sealed)


def slurm_jobs_at_head(repo: pathlib.Path) -> dict[str, str]:
    """{slurm-<J>: receipt path} for every receipt tracked at HEAD that shows a Slurm run."""
    out: dict[str, str] = {}
    for rel in tracked_receipts(repo):
        rec, _raw = receipt_at_head(repo, rel)
        job = slurm_job_of(rec, repo) if rec else None
        if job:
            out.setdefault(job, rel)
    return out


def unsealed_comparator_receipts(repo: pathlib.Path) -> dict[str, tuple[str, str]]:
    """{slurm-<J>: (receipt path, why)} for each comparator receipt tracked at HEAD that claims Slurm job J
    but whose claim no sacct seal corroborates: a near miss to name in a refusal, never backing."""
    out: dict[str, tuple[str, str]] = {}
    for rel in tracked_receipts(repo):
        rec, _raw = receipt_at_head(repo, rel)
        if rec and rec.get("schema") in core.COMPARATOR_SCHEMAS and receipt_mod().comparator_slurm_job(rec):
            why = unsealed_comparator_job(rec, repo)
            if why:
                out.setdefault(f"slurm-{rec['job_id']}", (rel, why))
    return out


def bare_job_receipts(repo: pathlib.Path) -> dict[str, str]:
    """{slurm-<J>: receipt path} for each receipt tracked at HEAD whose job_id is the bare number J and that
    slurm_job_of does not accept: a near miss to name in a refusal, never backing. A bare number says
    nothing about where it ran (a process id is a number too); `receipt slurm` writes `slurm-<J>` only
    after sacct shows the job COMPLETED."""
    out: dict[str, str] = {}
    for rel in tracked_receipts(repo):
        rec, _raw = receipt_at_head(repo, rel)
        job = rec.get("job_id") if rec else None
        if isinstance(job, str) and re.fullmatch(r"\d+(?:_\d+)?", job) and not slurm_job_of(rec, repo) \
                and rec.get("schema") not in core.COMPARATOR_SCHEMAS:     # unsealed comparator: its own hint
            out.setdefault(f"slurm-{job}", rel)
    return out


# scheduler name -> {job id: receipt path} for the receipts tracked at HEAD that show a run under it
JOB_INDEX = {sites.SLURM.name: slurm_jobs_at_head}


def compute_backing(doc: dict, repo: pathlib.Path, profiles: dict | None = None
                    ) -> tuple[dict[str, list[str]], list[str]]:
    """({cluster: [backed job ids]}, problems): every listed job must be the job of a receipt tracked at HEAD,
    of the scheduler the cluster's site profile names (sites.py)."""
    profiles = sites.for_repo(repo) if profiles is None else profiles
    index: dict[str, dict[str, str]] = {}
    backed: dict[str, list[str]] = {}
    problems: list[str] = []
    for c in doc.get("compute") or []:
        sched = sites.scheduler_of(profiles[c["cluster"]])
        if sched.name not in index:
            index[sched.name] = JOB_INDEX[sched.name](repo)
        jobs = index[sched.name]
        missing = [j for j in c["jobs"] if j not in jobs]
        if missing:
            # the near-miss hint is Slurm's (`receipt slurm`); a new scheduler brings its own with its reader
            near = bare_job_receipts(repo) if sched is sites.SLURM else {}
            hint = "".join(
                f"; {near[j]} has job_id {j.removeprefix('slurm-')!r}, a bare number, which is not accepted as a "
                f"Slurm job: write the receipt with `lean-pkg receipt slurm --job {j.removeprefix('slurm-')}` "
                f"(it checks sacct and writes job_id {j})" for j in missing if j in near)
            if sched is sites.SLURM:          # a comparator receipt's unsealed claim: its own hint
                unsealed = unsealed_comparator_receipts(repo)
                hint += "".join(f"; {unsealed[j][0]} claims Slurm job {j.removeprefix('slurm-')} but does not back "
                                f"it: {unsealed[j][1]}" for j in missing if j in unsealed)
            problems.append(f"compute ({c['cluster']}): no {sched.label} receipt tracked at HEAD under .lean-receipts/ "
                            f"has job_id {', '.join(missing)} (commit the receipt, or drop the job){hint}")
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
        from .anchor import baseline_problems
        e, w = baseline_problems(doc, repo)
        errors, warnings = errors + e, warnings + w
    p, w = version_doi_problems(doc)
    return errors + p, warnings + w


def scope_problems(pkg: dict, doc: dict, claims: dict[str, dict], repo: pathlib.Path) -> list[str]:
    """The lab's lean-scope-page rules that apply to a rendered page (docs/page.md#from-lean-scope-page):
    R8/R9, every claim a row names has its `file` committed at HEAD (the page links the declaration
    there); R10/R11, every theorem the recorded comparator receipt compares is the declaration of a claim
    some row names (the page says how many theorems were compared, so each must be accounted for);
    R12, every comparator configuration with definition holes carries a sign-off reviewer (the page names
    who judged the bodies): the receipt's `signoff` copy of lean-pkg-signoff.json, else its deprecated
    `judged_as` copy (holes.receipt_judged)."""
    from .holes import receipt_judged
    from . import claims as claims_mod
    from . import comparator_config as cc
    errors: list[str] = []
    named = [(i, cid) for i, r in enumerate(doc["rows"]) for cid in r.get("claims") or [] if cid in claims]
    for i, cid in named:
        rel = claims[cid].get("file")
        if rel and not core.tracked_at(repo, str(rel)):
            errors.append(f"rows[{i}].claims: {cid!r} names file {rel!r}, which is not committed at HEAD "
                          f"(the page links the declaration there)")
    rel = (pkg.get("gate") or {}).get("comparator")
    comp, _raw = receipt_at_head(repo, rel) if rel else (None, None)
    configs = [c for c in (comp or {}).get("configs") or [] if isinstance(c, dict)]
    if not configs:
        return errors
    row_claims = [claims[cid] for _i, cid in named]
    qualified = cc.qualified_decls(claims_mod._reader(repo, "HEAD"), row_claims)
    decls = [(str(c.get("decl") or ""), str(c.get("decl") or "") in qualified) for c in row_claims]
    for c in configs:
        where = c.get("path") or "?"
        for t in c.get("theorem_names") or []:
            if isinstance(t, str) and not any(cc.covers({t}, d, q) for d, q in decls):
                near = cc.name_hints([t], decls)            # #6: a root-namespace near miss, named, never credited
                errors.append(f"comparator theorem {t!r} ({where}, recorded comparator receipt) is the declaration "
                              f"of no claim a row names: map it to its paper result (a row's claims), or drop it "
                              f"from the configuration" + (f"; hint: {near[0]['hint']}" if near else ""))
        judged = receipt_judged(c)
        if c.get("definition_names") and not str(judged.get("reviewer") or "").strip():
            errors.append(f"comparator configuration {where} (recorded comparator receipt) has definition holes "
                          f"{', '.join(map(str, c['definition_names']))} without a sign-off reviewer (signoff, from "
                          f"lean-pkg-signoff.json; or the deprecated judged_as.reviewer): comparator never compares "
                          f"a hole's body, so the page must name the human who did")
    return errors


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
    errors = validate(doc, pkg["name"], sites.for_repo(repo))
    if errors:
        raise core.Fail(f"{where} is not a valid {COVERAGE_SCHEMA} file:\n  " + "\n  ".join(errors))
    claims, claims_rel = claims_at_head(pkg, repo)
    errors, warnings = check_against(doc, claims, claims_rel, repo)
    errors += scope_problems(pkg, doc, claims, repo)
    if doc_kind(doc) == "paper" and manuscript_kind(doc["manuscript"]["version"]) == "unposted" \
            and not allow_private:
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
        payload = {"ok": False, "name": name, "file": file or DEFAULT_COVERAGE_FILE, "kind": None, "rows": None,
                   "counts": None, "manuscript": None, "private_manuscript": None,
                   "errors": problems, "warnings": []}
        return [f"{name}: coverage: INVALID ({len(problems)} problem(s))", f"  {lines[0]}"] + \
            [f"  {x}" for x in problems if x != lines[0]], payload, 1
    if doc_kind(doc) == "library":
        payload = {"ok": True, "name": name, "file": file or DEFAULT_COVERAGE_FILE, "kind": "library",
                   "rows": len(doc["rows"]), "counts": None, "manuscript": None, "private_manuscript": False,
                   "errors": [], "warnings": warnings}
        line = f"{name}: coverage: OK; library; {len(doc['rows'])} main result(s); no manuscript"
        return [line] + [f"  warning: {w}" for w in warnings], payload, 0
    counts = {c: sum(r["class"] == c for r in doc["rows"]) for c in CLASSES}
    kind = manuscript_kind(doc["manuscript"]["version"])
    payload = {"ok": True, "name": name, "file": file or DEFAULT_COVERAGE_FILE, "kind": "paper",
               "rows": len(doc["rows"]), "counts": counts, "manuscript": kind, "private_manuscript": kind == "unposted",
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
    s = sub.add_parser("coverage", help="coverage.yaml (coverage/1): the paper-to-Lean map (or a library's main "
                                        "results) `page` renders")
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
