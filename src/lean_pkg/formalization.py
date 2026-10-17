# lean-pkg formalization -- render a formalization.yaml (mathlib-initiative v0.4)
# for a registered package from the registry and the package repository at HEAD.
"""Why this exists.

Palomar reads a repo-root ``formalization.yaml`` (the mathlib-initiative
self-reporting standard, schema v0.4). The registry's ``formalization_yaml``
field only stores a path; nothing generated or validated the file. This
module renders it from facts the tool already holds and refuses to invent the
rest.

Two kinds of keys:

* **Derived** keys are regenerated on every run from the registry and the
  repository at HEAD (tracked bytes, never the working tree): ``version``,
  ``project.name``, ``project.license`` (``license_spdx``),
  ``automation.notes`` (the registry's ``automation`` disclosure),
  ``status.sorry_count`` / ``status.axioms`` / ``status.evidence`` (the
  lower-tier gate receipt), ``status.main_results`` (every tracked comparator
  configuration: one entry per ``theorem_names`` item, ``judged_as`` carried
  for definition holes), and ``alignment`` when ``claims.yaml`` is tracked.
* **Kept** keys are read from the existing file and preserved verbatim
  (``project.description``, ``project.authors``, ``sources``,
  ``related_formalizations``, ``classification``, ``automation.methods``,
  ``status.scope``, ``fidelity``, ``review``, ``acknowledgements``,
  ``repository`` and any extra key). Inside ``acknowledgements`` the lines
  starting ``Formalizes results of `` / ``AI assistance: `` are the
  renderer's own, rebuilt from the registry's ``source_paper`` and
  ``ai_assistance`` on every run (#22.6); every other line is kept. On a first render they are seeded from
  registry data where it exists (authors from ``consent[]`` entries with role
  owner or author, a Mathlib ``builds-on`` row from ``mathlib_pin``) and
  otherwise with a value starting ``[PLACEHOLDER]``, each reported on stderr.

So ``--write`` is idempotent, hand edits in kept keys survive, and
``audit`` can report ``formalization-drift`` when a derived key changed.
Nothing here is a verification: the file reports what the registry and the
receipts record.
"""
from __future__ import annotations

import importlib.resources
import json
import os
import pathlib

import yaml

from . import cli as core
from . import claims as claims_mod
from .holes import check_definition_holes, summary as signoff_summary

SCHEMA_VERSION = "v0.4"
SCHEMA_FILE = "formalization-0.4.schema.json"
SCHEMA_URL = ("https://raw.githubusercontent.com/mathlib-initiative/formalization.yaml/"
              "main/schema/formalization.schema.json")
DEFAULT_PATH = "formalization.yaml"
PLACEHOLDER = "[PLACEHOLDER]"
MATHLIB_URL = "https://github.com/leanprover-community/mathlib4"
AUTHOR_ROLES = ("owner", "author")
METHOD_ENUM = ("manual", "copilot", "agent", "autonomous", "other")
SOURCE_RELATIONSHIPS = ("", "formalizes", "adapts", "independently-proves", "background", "other")
RELATED_RELATIONSHIPS = ("", "builds-on", "adapts", "independent", "supersedes", "other")
ENDORSEMENTS = ("", "participated", "endorsed", "no-response", "not-contacted", "declined",
                "n/a", "other")

DERIVED_KEYS = ("version", "project.name", "project.license", "automation.notes",
                "status.sorry_count", "status.axioms", "status.evidence",
                "status.main_results", "alignment (when claims.yaml is tracked at HEAD)")

# The same split as data (#23.5): the key paths the renderer owns. A diff confined to these is what
# regenerating the file after a new receipt produces; the evidence-only descendant rule admits it.
DERIVED_PATHS = (("version",), ("project", "name"), ("project", "license"), ("automation", "notes"),
                 ("status", "sorry_count"), ("status", "axioms"), ("status", "evidence"),
                 ("status", "main_results"), ("alignment",))


# #22.6: the renderer's own sentences inside the kept `acknowledgements` string, one per line, each
# starting with one of these markers. A re-render replaces exactly these lines and keeps every other
# line (hand-written credits) as it is.
ACK_MARKERS = ("Formalizes results of ", "AI assistance: ")


def _join_names(names: list[str]) -> str:
    names = [n for n in names if n]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def own_ack_sentences(pkg: dict) -> list[str]:
    """The acknowledgements lines derived from the registry's source_paper and ai_assistance."""
    out = []
    sp = pkg.get("source_paper") if isinstance(pkg.get("source_paper"), dict) else None
    if sp and any(sp.get(k) for k in ("title", "arxiv", "doi")):
        ids = ([f"arXiv:{sp['arxiv']}"] if sp.get("arxiv") else []) + ([f"doi:{sp['doi']}"] if sp.get("doi") else [])
        subject = sp.get("title") or ids[0]
        by = _join_names([a for a in sp.get("authors") or [] if isinstance(a, str)])
        line = ACK_MARKERS[0] + subject + (f" by {by}" if by else "")
        if sp.get("title") and ids:
            line += f" ({' / '.join(ids)})"
        out.append(line + ".")
    ai = pkg.get("ai_assistance") if isinstance(pkg.get("ai_assistance"), dict) else None
    if ai and ai.get("tools"):
        roles = _join_names([r for r in ai.get("roles") or [] if isinstance(r, str)])
        out.append(ACK_MARKERS[1] + _join_names(list(ai["tools"])) + (f" for {roles}" if roles else "") + ".")
    return out


def strip_own_ack(text) -> str:
    """The hand-written part of an acknowledgements string: every line not starting with a marker."""
    lines = str(text).split("\n")
    return "\n".join(x for x in lines if not x.startswith(ACK_MARKERS)).strip("\n")


def render_acknowledgements(existing, pkg: dict):
    """The acknowledgements value: the kept hand-written text, then the renderer's sentences (each on
    its own line, refreshed on every run, never duplicated). None: leave the key as it is."""
    own = own_ack_sentences(pkg)
    if existing is not None and not isinstance(existing, str):
        return None                       # not a string: a schema problem the validator reports
    had_own = isinstance(existing, str) and any(x.startswith(ACK_MARKERS) for x in existing.split("\n"))
    if not own and not had_own:
        return None
    manual = strip_own_ack(existing) if existing else ""
    return "\n".join(([manual] if manual.strip() else []) + own)


def kept_view(doc) -> dict:
    """The document with every derived key removed: what a hand edit can change. The renderer's own
    acknowledgements lines (#22.6) count as derived."""
    import copy
    out = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    for path in DERIVED_PATHS:
        node = out
        for k in path[:-1]:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    if isinstance(out.get("acknowledgements"), str):
        manual = strip_own_ack(out["acknowledgements"])
        if manual.strip():
            out["acknowledgements"] = manual
        else:
            out.pop("acknowledgements")
    return out


def changed_kept_keys(old, new) -> list[str]:
    """Dotted paths (top level, and one level down inside a mapping) of kept keys that differ."""
    a, b = kept_view(old), kept_view(new)
    out: list[str] = []
    for k in list(a) + [k for k in b if k not in a]:
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            out += [f"{k}.{j}" for j in list(va) + [j for j in vb if j not in va] if va.get(j) != vb.get(j)]
        else:
            out.append(str(k))
    return out


TOP_ORDER = ("version", "project", "repository", "sources", "related_formalizations",
             "classification", "status", "automation", "fidelity", "review", "alignment",
             "acknowledgements")
PROJECT_ORDER = ("name", "description", "authors", "responsible_maintainers", "license")
STATUS_ORDER = ("scope", "sorry_count", "sorry_in_definitions", "axioms", "evidence",
                "main_results")
AUTOMATION_ORDER = ("methods", "spend_usd", "notes")

HEADER = f"""\
# yaml-language-server: $schema={SCHEMA_URL}
# Rendered by `lean-pkg formalization <name>` from the lean-pkg registry and the package
# repository at HEAD (formalization.yaml {SCHEMA_VERSION}, mathlib-initiative). Paths are
# repository-relative. Derived keys are regenerated on every run: version, project.name,
# project.license, automation.notes, status.sorry_count, status.axioms, status.evidence,
# status.main_results, and alignment when claims.yaml is tracked. Every other key is kept
# from this file. A value starting "{PLACEHOLDER}" is data the registry does not hold:
# fill it in by hand (it is kept) or set the registry field it names.
"""


# --------------------------------------------------------------------------
# helpers

def _ordered(d: dict, order: tuple[str, ...]) -> dict:
    """Known keys first in template order, then the rest in their own order."""
    out = {k: d[k] for k in order if k in d}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


def _section(existing: dict | None, key: str) -> dict:
    v = (existing or {}).get(key)
    return dict(v) if isinstance(v, dict) else {}


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and v.startswith(PLACEHOLDER)


def module_path(module) -> str | None:
    """Lean module name -> conventional source path: `A.B.C` -> `A/B/C.lean`."""
    if not isinstance(module, str) or not module.strip():
        return None
    return module.strip().replace(".", "/") + ".lean"


def consent_authors(pkg: dict) -> list[str]:
    """`consent[]` entries whose role is owner or author, in record order, deduplicated."""
    out: list[str] = []
    for c in pkg.get("consent") or []:
        if isinstance(c, dict) and c.get("role") in AUTHOR_ROLES:
            who = str(c.get("who") or "").strip()
            if who and who not in out:
                out.append(who)
    return out


def find_placeholders(obj, path: str = "") -> list[str]:
    """Dotted paths of every placeholder value in the document."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found += find_placeholders(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += find_placeholders(v, f"{path}[{i}]")
    elif _is_placeholder(obj):
        found.append(path)
    return found


# --------------------------------------------------------------------------
# derived parts

def main_results(repo: pathlib.Path, warnings: list[str]) -> list[dict]:
    """One entry per theorem of every comparator configuration tracked at HEAD."""
    out: list[dict] = []
    for rel in core.head_comparator_configs(repo):
        raw = core.git_show_bytes(repo, rel)
        try:
            cfg = json.loads(raw or b"")
        except ValueError as e:
            raise core.Fail(f"{rel} at HEAD is not valid JSON: {e}")
        if not isinstance(cfg, dict):
            raise core.Fail(f"{rel} at HEAD is not a JSON object")
        solution = module_path(cfg.get("solution_module"))
        challenge = module_path(cfg.get("challenge_module"))
        if solution is None:
            raise core.Fail(f"{rel}: solution_module is missing or empty")
        if core.git_show_bytes(repo, solution) is None:
            warnings.append(f"{rel}: solution module file {solution} is not tracked at HEAD "
                            f"(main_results.file uses the conventional module path)")
        names = cfg.get("theorem_names") or []
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise core.Fail(f"{rel}: theorem_names must be a list of strings")
        if not names:
            warnings.append(f"{rel}: theorem_names is empty; no main_results entry")
        holes = cfg.get("definition_names") or []
        judged = None
        try:                                 # lean-pkg-signoff.json at HEAD (holes.py)
            sidecar = core.sidecar_at(repo)
        except ValueError as e:
            raise core.Fail(str(e))
        if holes:
            try:
                so = check_definition_holes(cfg, rel, cfg_rel=rel, cfg_bytes=raw, sidecar=sidecar,
                                            warn=warnings.append)
                judged = cfg["judged_as"] if so["source"] == "inline" else signoff_summary(so)
            except ValueError as e:
                warnings.append(f"{e}; judged_as is null in main_results")
        from .comparator_config import scaffold_todo_names
        todo = scaffold_todo_names(cfg, rel, sidecar)  # #23.6: scaffolded, not yet written: not a main result
        if todo & set(names):
            warnings.append(f"{rel}: {len(todo & set(names))} theorem name(s) still marked TODO in the "
                            f"scaffold; left out of status.main_results")
        for name in names:
            if name in todo:
                continue
            entry = {"comparator_config": rel, "declaration": name, "file": solution}
            if challenge:
                entry["challenge_file"] = challenge
            if holes:
                entry["definition_holes"] = list(holes)
                entry["judged_as"] = judged
            out.append(entry)
    if not out:
        warnings.append("no comparator configuration with theorem_names is tracked at HEAD: "
                        "status.main_results is empty")
    return out


def receipt_facts(pkg: dict, repo: pathlib.Path) -> dict | None:
    """sorry_count, axioms and evidence pointers from the lower-tier gate receipt, if readable."""
    gate = pkg.get("gate") or {}
    rel = gate.get("receipt")
    rec = core.recorded_json(repo, rel) if rel else None
    if not isinstance(rec, dict):
        return None
    facts: dict = {}
    if isinstance(rec.get("sorry_count"), int):
        facts["sorry_count"] = rec["sorry_count"]
    if isinstance(rec.get("axioms"), list):
        facts["axioms"] = [str(a) for a in rec["axioms"]]
    facts["evidence"] = {"commit": rec.get("commit"), "gate_receipt": rel,
                         "comparator_receipt": gate.get("comparator")}
    return facts


def alignment_from_claims(pkg: dict, repo: pathlib.Path) -> dict | None:
    """`alignment` rows from claims.yaml tracked at HEAD; None when there is no such file."""
    rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
    raw = core.git_show_bytes(repo, rel)
    if raw is None:
        return None
    doc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), rel)
    errors = claims_mod.validate_claims(doc, pkg.get("name"))
    if errors:
        raise core.Fail(f"{rel} at HEAD is not a valid claims/1 file (fix it, then "
                        f"`lean-pkg claims sync`):\n  " + "\n  ".join(errors))
    rows = []
    for c in doc.get("claims") or []:
        c = claims_mod.normalize_claim(c)
        rows.append({"source": c.get("citation"), "lean": c["decl"], "module": c["file"],
                     "kind": c["kind"], "claim": c["id"], "status": c["status"],
                     "note": c["summary"]})
    return {"generated_from": rel, "statements": rows}


# --------------------------------------------------------------------------
# render

def render_doc(pkg: dict, repo: pathlib.Path, existing: dict | None,
               authors: list[str] | None = None) -> tuple[dict, list[str]]:
    """The document and its warnings (placeholders are reported by the caller)."""
    warnings: list[str] = []
    name = pkg["name"]
    doc: dict = dict(existing or {})
    doc["version"] = SCHEMA_VERSION

    project = _section(existing, "project")
    project["name"] = name
    if "description" not in project:
        project["description"] = (f"{PLACEHOLDER} project.description: a concise account of the "
                                  f"mathematical content and principal results")
    kept_authors = [a for a in (project.get("authors") or []) if isinstance(a, str)
                    and a.strip() and not _is_placeholder(a)]
    if authors:
        project["authors"] = list(authors)
    elif kept_authors:
        project["authors"] = kept_authors
    elif consent_authors(pkg):
        project["authors"] = consent_authors(pkg)
    else:
        project["authors"] = [f"{PLACEHOLDER} project.authors: no consent[] entry with role "
                              f"owner/author in the registry; pass --author NAME or record "
                              f"consent (lean-pkg record {name} consent-recorded ...)"]
    project["license"] = pkg.get("license_spdx") or (
        f"{PLACEHOLDER} project.license: license_spdx is unset in the registry "
        f"(lean-pkg update {name} --license-spdx SPDX)")
    doc["project"] = _ordered(project, PROJECT_ORDER)

    if not isinstance(doc.get("sources"), list) or not doc["sources"]:
        src = {"title": f"{PLACEHOLDER} sources[0].title: the registry carries no paper "
                        f"metadata; name the formalized source by hand (title, authors, "
                        f"id = DOI/arXiv/URL, type, location)",
               "relationship": "formalizes"}
        if pkg.get("owner_project"):
            src["note"] = f"registry owner_project: {pkg['owner_project']}"
        doc["sources"] = [src]

    if "related_formalizations" not in doc:
        doc["related_formalizations"] = ([{"id": MATHLIB_URL, "relationship": "builds-on",
                                           "note": "pinned in lake-manifest.json"}]
                                         if pkg.get("mathlib_pin") else [])

    status = _section(existing, "status")
    if "scope" not in status:
        status["scope"] = (f"{PLACEHOLDER} status.scope: what is formalized and what is "
                           f"excluded, in the project's own words")
    for k in ("sorry_count", "axioms", "evidence"):
        status.pop(k, None)
    facts = receipt_facts(pkg, repo)
    if facts:
        status.update(facts)
    status["main_results"] = main_results(repo, warnings)
    doc["status"] = _ordered(status, STATUS_ORDER)

    automation = _section(existing, "automation")
    if not isinstance(automation.get("methods"), list) or not automation["methods"]:
        automation["methods"] = [{"method": "other"}]
    note = str(pkg.get("automation") or "").strip()
    automation["notes"] = note or (
        f"{PLACEHOLDER} automation.notes: the registry's automation disclosure is empty "
        f"(lean-pkg update {name} --automation TEXT)")
    doc["automation"] = _ordered(automation, AUTOMATION_ORDER)

    if not isinstance(doc.get("review"), dict):
        doc["review"] = {"status": "unchecked",
                         "notes": "seeded by lean-pkg formalization: no human review status is "
                                  "recorded in the registry; mechanical evidence (gate and "
                                  "comparator receipts) is listed under status.evidence"}

    alignment = alignment_from_claims(pkg, repo)
    if alignment is not None:
        doc["alignment"] = alignment

    ack = render_acknowledgements(doc.get("acknowledgements"), pkg)
    if ack is not None:
        if ack:
            doc["acknowledgements"] = ack
        else:
            doc.pop("acknowledgements", None)

    return _ordered(doc, TOP_ORDER), warnings


def dump_doc(doc: dict) -> str:
    return HEADER + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100,
                                   default_flow_style=False)


def load_existing(path: pathlib.Path) -> tuple[str | None, dict | None]:
    """(text, mapping) of an existing formalization.yaml; (None, None) when absent."""
    if path.is_symlink():
        raise core.Fail(f"{path} is a symlink; refusing to read or write through it")
    if not path.is_file():
        return None, None
    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise core.Fail(f"{path}: not valid YAML: {e}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise core.Fail(f"{path}: top level is not a mapping")
    return text, data


# --------------------------------------------------------------------------
# validation

def load_schema() -> dict:
    text = importlib.resources.files("lean_pkg").joinpath("schemas", SCHEMA_FILE).read_text()
    return json.loads(text)


def _import_jsonschema():
    try:
        import jsonschema  # optional dependency
    except ImportError:
        return None
    return jsonschema


def structural_errors(doc) -> list[str]:
    """A hand-written subset of the v0.4 schema for hosts without jsonschema."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["<root>: not a mapping"]

    def nonempty_str(v) -> bool:
        return isinstance(v, str) and len(v) > 0

    for k in ("project", "sources", "automation", "review"):
        if k not in doc:
            errors.append(f"<root>: '{k}' is a required property")
    p = doc.get("project")
    if p is not None:
        if not isinstance(p, dict):
            errors.append("project: not a mapping")
        else:
            for k in ("name", "authors", "license"):
                if k not in p:
                    errors.append(f"project: '{k}' is a required property")
            if "name" in p and not nonempty_str(p["name"]):
                errors.append("project/name: must be a non-empty string")
            if "license" in p and not nonempty_str(p["license"]):
                errors.append("project/license: must be a non-empty string")
            a = p.get("authors")
            if "authors" in p and (not isinstance(a, list) or not a
                                   or not all(nonempty_str(x) for x in a)):
                errors.append("project/authors: must be a non-empty list of non-empty strings")
    s = doc.get("sources")
    if s is not None:
        if not isinstance(s, list) or not s:
            errors.append("sources: must be a non-empty list")
        else:
            for i, e in enumerate(s):
                if not isinstance(e, dict) or not nonempty_str(e.get("title")):
                    errors.append(f"sources/{i}: 'title' is required and must be non-empty")
                    continue
                if e.get("relationship", "") not in SOURCE_RELATIONSHIPS:
                    errors.append(f"sources/{i}/relationship: not one of {list(SOURCE_RELATIONSHIPS)}")
                if e.get("author_endorsement", "") not in ENDORSEMENTS:
                    errors.append(f"sources/{i}/author_endorsement: not one of {list(ENDORSEMENTS)}")
    r = doc.get("related_formalizations")
    if r is not None:
        if not isinstance(r, list):
            errors.append("related_formalizations: must be a list")
        else:
            for i, e in enumerate(r):
                if not isinstance(e, dict) or not nonempty_str(e.get("id")):
                    errors.append(f"related_formalizations/{i}: 'id' is required and must be non-empty")
                elif e.get("relationship", "") not in RELATED_RELATIONSHIPS:
                    errors.append(f"related_formalizations/{i}/relationship: not one of "
                                  f"{list(RELATED_RELATIONSHIPS)}")
    a = doc.get("automation")
    if a is not None:
        m = a.get("methods") if isinstance(a, dict) else None
        if not isinstance(a, dict) or "methods" not in a:
            errors.append("automation: 'methods' is a required property")
        elif not isinstance(m, list) or not m:
            errors.append("automation/methods: must be a non-empty list")
        else:
            for i, e in enumerate(m):
                if not isinstance(e, dict) or e.get("method") not in METHOD_ENUM:
                    errors.append(f"automation/methods/{i}/method: not one of {list(METHOD_ENUM)}")
    rv = doc.get("review")
    if rv is not None and (not isinstance(rv, dict) or not nonempty_str(rv.get("status"))):
        errors.append("review/status: required, non-empty string")
    st = doc.get("status")
    if st is not None:
        if not isinstance(st, dict):
            errors.append("status: not a mapping")
        else:
            for k in ("sorry_count", "sorry_in_definitions"):
                v = st.get(k)
                if k in st and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                    errors.append(f"status/{k}: must be a non-negative integer")
            mr = st.get("main_results")
            if "main_results" in st and (not isinstance(mr, list)
                                         or not all(isinstance(e, dict) for e in mr)):
                errors.append("status/main_results: must be a list of mappings")
    return errors


def validate_doc(doc) -> tuple[str, list[str]]:
    """('jsonschema' | 'structural', errors)."""
    js = _import_jsonschema()
    if js is None:
        return "structural", structural_errors(doc)
    validator = js.Draft7Validator(load_schema())
    errs = sorted(validator.iter_errors(doc), key=lambda e: [str(x) for x in e.path])
    return "jsonschema", [f"{'/'.join(str(x) for x in e.path) or '<root>'}: {e.message}"
                          for e in errs]


# --------------------------------------------------------------------------
# the subcommand and the audit hook

def _atomic_write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def target_rel(pkg: dict) -> str:
    return pkg.get("formalization_yaml") or DEFAULT_PATH


def render_for(pkg: dict, repo: pathlib.Path, authors: list[str] | None = None
               ) -> tuple[str, dict, list[str], str | None]:
    """(rendered text, doc, warnings, existing text) for the package's target path."""
    existing_text, existing = load_existing(repo / target_rel(pkg))
    doc, warnings = render_doc(pkg, repo, existing, authors)
    return dump_doc(doc), doc, warnings, existing_text


def do_formalization(reg_path, reg, events_p, name: str, *, write: bool, out: str | None,
                     force: bool, authors: list[str] | None, agent: str | None,
                     reason: str = "", no_registry: bool = False) -> tuple[list[str], dict, list[str]]:
    """(stdout lines, payload, stderr warnings). The YAML text is in payload['yaml'].

    --write checks before it writes: the lease (a live lease held by another agent refuses unless
    --force --reason) and a dry run of the registry update, so a refusal leaves formalization.yaml
    untouched. --force alone overwrites a differing file; with --reason it also overrides the lease.
    no_registry (--write --no-registry, #22.8): write the file only; the registry is read, never written
    (no pointer update, no event), so the invocation is not a registry write."""
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    if core.git_head(repo) is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    if write and out:
        raise core.UsageError("--write and --out are mutually exclusive")
    if no_registry and not write:
        raise core.UsageError("--no-registry needs --write (a render without --write never touches the registry)")

    rel = target_rel(pkg)
    text, doc, warnings, existing_text = render_for(pkg, repo, authors)
    mode, errors = validate_doc(doc)
    if errors:
        raise core.Fail(f"{name}: the rendered formalization.yaml fails the {SCHEMA_VERSION} "
                        f"schema ({mode}); fix the kept keys in {rel}:\n  " + "\n  ".join(errors))
    placeholders = find_placeholders(doc)
    warnings = warnings + [f"placeholder at {p} (the registry holds no value for it)"
                           for p in placeholders]
    n_results = len(doc["status"]["main_results"])
    payload = {"ok": True, "name": name, "path": rel, "validation": mode,
               "main_results": n_results, "placeholders": placeholders,
               "warnings": warnings, "written": False, "unchanged": False,
               "registry_updated": False, "yaml": text}
    summary = f"{n_results} main result(s); validation: {mode} OK; {len(placeholders)} placeholder(s)"

    if out:
        outp = pathlib.Path(out)
        if outp.is_symlink():
            raise core.Fail(f"{outp} is a symlink; refusing to write through it")
        _atomic_write(outp, text)
        payload.update(path=str(outp), written=True)
        return [f"{name}: wrote {outp} ({summary}); registry untouched"], payload, warnings

    if not write:
        return [text.rstrip("\n")], payload, warnings

    target = repo / rel
    lease_force = bool(force and reason)
    needs_update = pkg.get("formalization_yaml") != rel
    if needs_update or existing_text != text:   # something will be written: the lease decides first
        core.refuse_invalid_lease(name, pkg.get("lease"))
        lease = pkg.get("lease")
        if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) \
                and agent != lease.get("agent") and not lease_force:
            holder = lease.get("agent")
            raise core.Fail(f"{name}: lease held by {holder} — pass --agent {holder}, or --force --reason "
                            f"TEXT to override it; nothing written")
    update_kw = dict(sets={"formalization_yaml": rel}, status=None,
                     reason=reason or "lean-pkg formalization --write", redetect=False,
                     agent=agent, force=lease_force)
    if no_registry:
        payload["registry_skipped"] = True
    if needs_update and not no_registry:   # every registry check first, on a copy: a refusal writes nothing
        core.do_update(reg_path, reg, events_p, name, dry_run=True, **update_kw)
    if existing_text is not None and existing_text == text:
        payload["unchanged"] = True
        lines = [f"{name}: {rel} is already what the generator emits; nothing written ({summary})"]
    else:
        if existing_text is not None and not force:
            raise core.Fail(f"{name}: {rel} exists and differs from the render; inspect "
                            f"`lean-pkg formalization {name}` and pass --force to overwrite "
                            f"(kept keys survive; derived keys are regenerated)")
        _atomic_write(target, text)
        payload["written"] = True
        lines = [f"{name}: wrote {target} ({summary}) — not committed; commit it yourself"]
    if needs_update and no_registry:
        lines.append(f"{name}: registry untouched (--no-registry); the formalization_yaml pointer is not "
                     f"set: run `lean-pkg update {name} --formalization-yaml {rel}` when the registry is open")
    elif needs_update:
        up_lines, _ = core.do_update(reg_path, reg, events_p, name, **update_kw)
        lines += up_lines
        payload["registry_updated"] = True
    return lines, payload, warnings


def audit_findings(pkg: dict, repo: pathlib.Path) -> list[tuple[str, str]]:
    """(check, detail) pairs for `audit`: formalization-missing | formalization-drift | warn."""
    rel = pkg.get("formalization_yaml")
    if not rel:
        return []
    target = repo / rel
    if not target.is_file():
        return [("formalization-missing", rel)]
    try:
        text, _doc, _warnings, existing_text = render_for(pkg, repo)
    except (core.Fail, core.UsageError) as e:
        return [("warn", f"formalization.yaml cannot be re-rendered: {e}")]
    if existing_text != text:
        return [("formalization-drift", f"{rel} differs from `lean-pkg formalization "
                                        f"{pkg['name']}` (a derived key changed); re-run with "
                                        f"--write --force")]
    return []


ANCHOR_CMD = "anchor"
ANCHOR_ONLY = (("arxiv", "--arxiv"), ("from_file", "--from-file"), ("fetch", "--fetch"),
               ("compare_tex", "--compare-tex"), ("accept_diff", "--accept-diff"), ("main_tex", "--main-tex"))
RENDER_ONLY = (("out", "--out"), ("force", "--force"), ("reason", "--reason"), ("author", "--author"),
               ("no_registry", "--no-registry"))


def is_anchor(args) -> bool:
    """`lean-pkg formalization anchor NAME ...`: the keyword form (never a registry write)."""
    return getattr(args, "name", None) == ANCHOR_CMD


def cmd_formalization(args) -> int:
    if is_anchor(args):
        given = [flag for dest, flag in RENDER_ONLY if getattr(args, dest, None)]
        if given:
            raise core.UsageError(f"formalization anchor: {', '.join(given)} belong(s) to the render form "
                                  f"(`lean-pkg formalization NAME`)")
        if not getattr(args, "package", None):
            raise core.UsageError("formalization anchor: name the package: "
                                  "`lean-pkg formalization anchor NAME --arxiv <id>v<N> --from-file E-PRINT`")
        if not getattr(args, "arxiv", None):
            raise core.UsageError("formalization anchor: --arxiv <id>v<N> is required")
        from .anchor import cmd_anchor
        return cmd_anchor(args)
    given = [flag for dest, flag in ANCHOR_ONLY if getattr(args, dest, None)]
    if getattr(args, "package", None) or given:
        raise core.UsageError(f"formalization {args.name}: "
                              + (f"{', '.join(given)} go(es) with" if given else "a second name needs")
                              + " the anchor form: `lean-pkg formalization anchor NAME --arxiv <id>v<N> ...`")
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    lines, payload, warnings = do_formalization(
        reg_path, core.load_registry(reg_path), events_p, args.name, write=args.write,
        out=args.out, force=args.force, authors=args.author, agent=args.agent,
        reason=getattr(args, "reason", "") or "", no_registry=getattr(args, "no_registry", False))
    for w in warnings:
        print(f"lean-pkg: warning: {w}", file=core.sys.stderr)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return 0


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("formalization",
                       help=f"render formalization.yaml ({SCHEMA_VERSION}, what Palomar reads) "
                            f"from the registry and the repo at HEAD; stdout unless --write/--out")
    s.add_argument("name", help="the package; or `anchor` followed by the package (the anchor form)")
    s.add_argument("package", nargs="?", default=None,
                   help="anchor form only: the package to anchor")
    s.add_argument("--write", action="store_true",
                   help="write <repo>/<formalization_yaml or formalization.yaml> and set the "
                        "registry pointer (event updated); refuses a differing file without --force. "
                        "Anchor form: write the source-baseline record, coverage.yaml manuscript.version and "
                        "the formalization.yaml source id (never the registry; not committed)")
    s.add_argument("--no-registry", action="store_true",
                   help="with --write: write the file only; the registry is not updated (no pointer, no "
                        "event), so this is not a registry write (no lock, no --commit)")
    s.add_argument("--out", default=None, metavar="PATH",
                   help="write the render here instead; the registry is untouched")
    s.add_argument("--force", action="store_true",
                   help="with --write: overwrite a file that differs from the render; with --reason "
                        "also override a live lease held by another agent")
    s.add_argument("--reason", default="",
                   help="with --write --force: why another agent's lease is overridden (recorded in "
                        "the updated event)")
    s.add_argument("--author", action="append", default=None, metavar="NAME",
                   help="project.authors entry (repeatable); otherwise kept from the file, else "
                        "consent[] owners/authors, else a placeholder")
    s.add_argument("--agent", default=None, help="lease holder, if one is live (for the registry update)")
    a = s.add_argument_group(
        "anchor form", "`lean-pkg formalization anchor NAME --arxiv <id>v<N> (--from-file E-PRINT | --fetch) "
        "[--compare-tex LOCAL.tex [--accept-diff REASON]] [--main-tex PATH] [--write]`: bind the companion to one "
        "posted arXiv version. Reads the e-print (gzip'd tar, gzip'd .tex or tar; PDF-only is refused) in memory, "
        "hashes it and its .tex/.bib/.bbl/.sty files, finds the main .tex (\\documentclass), compares it with "
        "LOCAL.tex and records it all in data/source-baselines/arXiv-<id>v<N>.json. Without --write: a dry run. "
        "--write refuses unless the e-print is tied to the manuscript coverage.yaml is bound to (same version, a "
        "matching bound sha256, or --compare-tex identical / --accept-diff); a move to another version prints the "
        "`claims rebase` command. Never writes the registry; never commits")
    a.add_argument("--arxiv", default=None, metavar="ID",
                   help="the arXiv id WITH its version (<id>v<N> or arXiv:<id>v<N>); unversioned ids are refused")
    a.add_argument("--from-file", dest="from_file", default=None, metavar="E-PRINT",
                   help="the e-print as downloaded from arxiv.org/e-print/<id>v<N> (offline; no network)")
    a.add_argument("--fetch", action="store_true",
                   help="download the e-print over HTTPS (60 s timeout, 64 MiB cap) and the arXiv API entry of "
                        "that version; the only network access this command makes")
    a.add_argument("--compare-tex", dest="compare_tex", default=None, metavar="LOCAL.tex",
                   help="compare this local .tex with the e-print's main .tex (line endings and trailing "
                        "whitespace normalised); the differing lines are shown")
    a.add_argument("--accept-diff", dest="accept_diff", default=None, metavar="REASON",
                   help="with --compare-tex: anchor although the files differ; the reason is recorded")
    a.add_argument("--main-tex", dest="main_tex", default=None, metavar="PATH",
                   help="the e-print's main .tex when more than one file carries \\documentclass")
    add_json(s)
    s.set_defaults(fn=cmd_formalization)
