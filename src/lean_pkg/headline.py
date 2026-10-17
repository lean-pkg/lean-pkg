# headline -- the headline comparator configuration: the paper's main results, a subset of what the
# gate-bound comparator receipt compared. Feeds formalization.yaml status.main_results.
"""Why this exists.

A companion can compare far more than the paper's main results (one field package compares 83
theorems; 12 of them are the paper's main results). Palomar reads ``status.main_results`` from
formalization.yaml, which by default lists every theorem of every tracked comparator configuration.
A *headline configuration* names the main results: when it is declared and judged, ``main_results``
lists its theorems only, while the receipt and the gate still bind the full comparison.

Which file (first match wins):

1. ``lean-pkg formalization NAME --headline-config PATH`` for that call (``--write`` records it);
2. the registry field ``headline_config`` (``lean-pkg update NAME --headline-config PATH``; ``''``
   clears it);
3. the convention: ``comparator.headline.json`` tracked at the package root at HEAD.

The file holds only Palomar's six configuration keys (challenge_module, solution_module,
theorem_names, definition_names, permitted_axioms, enable_nanoda); anything else is refused. It is a
repository-relative ``.json`` tracked at HEAD. It need not be a comparator configuration path: at
``comparator*.json`` / ``comparator/config*.json`` the runner compares it like any other (and the
receipt must list it); elsewhere it is not compared on its own and needs no receipt entry.

The subset rule (judged against the comparator receipt the gate records, validated at HEAD exactly as
``gate --check`` validates it): every headline theorem name must be covered, by
``comparator_config.covers`` (the same full name, or ``<Prefix>.<name>``), by the theorem_names of a
receipted configuration with the same challenge_module and solution_module (a different module pair
compares a different statement). A headline configuration that is itself receipted covers itself.
Refused otherwise, naming every uncovered name.

Definition holes: every headline definition_names entry must carry a sign-off in the sign-off sidecar
(lean-pkg-signoff.json at the receipt's commit) of a receipted configuration with the same module pair
that lists it as a hole, the headline's own entry first when it is itself receipted (the receipt
validator already requires that entry). A main configuration's sign-off covers the same definition in
the same modules; nothing else does. Refused otherwise, naming every unsigned hole.

When no comparator receipt is recorded, or the recorded one does not validate at HEAD (not gated
yet, or a new commit awaiting a re-gate), the headline cannot be judged: it is not applied, a warning
says why, and ``main_results`` keeps the default (every tracked configuration). It is never applied
unjudged.
"""
from __future__ import annotations

import json
import pathlib

from . import cli as core
from .holes import CONFIG_RE, SIDECAR, check_definition_holes, summary as signoff_summary

CONVENTION = "comparator.headline.json"
PALOMAR_KEYS = ("challenge_module", "definition_names", "enable_nanoda", "permitted_axioms",
                "solution_module", "theorem_names")
REGISTRY_FIELD = "headline_config"


def path_error(rel) -> str | None:
    """Why `rel` cannot name a headline configuration, or None."""
    if not isinstance(rel, str) or not rel.strip():
        return "a headline configuration path must be a non-empty string"
    pp = pathlib.PurePosixPath(rel)
    if pp.is_absolute() or ".." in pp.parts or rel.startswith("~") or "\\" in rel:
        return f"headline configuration {rel!r} must be repository-relative (no leading /, ~ or ..)"
    if not rel.endswith(".json"):
        return f"headline configuration {rel!r} must be a .json file"
    if pp.name == SIDECAR:
        return f"headline configuration {rel!r} is the sign-off sidecar, not a configuration"
    return None


def declared(pkg: dict, repo: pathlib.Path, override: str | None = None,
             rev: str = "HEAD") -> tuple[str | None, str | None]:
    """(path, source): source 'flag' | 'registry' | 'convention'; (None, None) when none is declared."""
    for rel, source in ((override, "flag"), ((pkg or {}).get(REGISTRY_FIELD), "registry")):
        if rel:
            err = path_error(rel)
            if err:
                raise core.Fail(f"{err} ({'--headline-config' if source == 'flag' else REGISTRY_FIELD})")
            return rel, source
    if core.git_show_bytes(repo, CONVENTION, rev) is not None:
        return CONVENTION, "convention"
    return None, None


def _strict(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def parse(raw: bytes | None, rel: str) -> dict:
    """The headline configuration, strictly: a JSON object of Palomar's six keys only, theorem_names a
    non-empty list of names, both modules named. Raises ValueError naming the first problem."""
    if raw is None:
        raise ValueError(f"headline configuration {rel} is not tracked")
    try:
        cfg = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict)
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"headline configuration {rel}: not valid strict JSON ({e})")
    if not isinstance(cfg, dict):
        raise ValueError(f"headline configuration {rel}: not a JSON object")
    extra = sorted(set(cfg) - set(PALOMAR_KEYS))
    if extra:
        raise ValueError(f"headline configuration {rel}: key(s) outside Palomar's six: {', '.join(extra)} "
                         f"(allowed: {', '.join(PALOMAR_KEYS)}; lean-pkg's own metadata lives in {SIDECAR})")
    for k in ("challenge_module", "solution_module"):
        if not isinstance(cfg.get(k), str) or not cfg[k].strip():
            raise ValueError(f"headline configuration {rel}: {k} must be a non-empty module name")
    names = cfg.get("theorem_names")
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n.strip() for n in names):
        raise ValueError(f"headline configuration {rel}: theorem_names must be a non-empty list of names")
    holes = cfg.get("definition_names")
    if holes is not None and (not isinstance(holes, list) or not all(isinstance(h, str) and h for h in holes)):
        raise ValueError(f"headline configuration {rel}: definition_names must be a list of names")
    return cfg


def load(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> dict:
    try:
        return parse(core.git_show_bytes(repo, rel, rev), rel)
    except ValueError as e:
        raise core.Fail(f"{e} at {rev}" if "is not tracked" in str(e) else str(e)) from None


def judge(pkg: dict, repo: pathlib.Path, rel: str, cfg: dict) -> dict:
    """The subset and sign-off rules against the gate-bound comparator receipt.

    {judged, reason, receipt, receipt_commit, uncovered[], covered_by{name: [config paths]},
    unsigned[], judged_as}; judged False (with a reason) when no recorded comparator receipt validates
    at HEAD."""
    out = {"judged": False, "reason": None, "receipt": None, "receipt_commit": None, "uncovered": [],
           "covered_by": {}, "unsigned": [], "judged_as": None}
    gate = (pkg or {}).get("gate") or {}
    comp_rel = gate.get("comparator")
    if not comp_rel:
        out["reason"] = "no comparator receipt is recorded (lean-pkg comparator NAME --receipt R)"
        return out
    out["receipt"] = comp_rel
    comp = core.recorded_json(repo, comp_rel)
    if not isinstance(comp, dict):
        out["reason"] = f"the recorded comparator receipt {comp_rel} is missing or not JSON"
        return out
    lower = core.recorded_json(repo, gate.get("receipt")) if gate.get("receipt") else None
    try:
        core.validate_comparator(pkg, repo, comp, str((lower or {}).get("commit") or "") or None)
    except core.Fail as e:
        out["reason"] = f"the recorded comparator receipt {comp_rel} does not validate at HEAD ({e})"
        return out
    commit = str(comp["commit"])
    out.update(judged=True, receipt_commit=commit)
    from .comparator_config import covers
    same = [c for c in comp["configs"] if c.get("challenge_module") == cfg["challenge_module"]
            and c.get("solution_module") == cfg["solution_module"]]
    same.sort(key=lambda c: (c.get("path") != rel, str(c.get("path"))))   # the headline's own entry first
    for name in cfg["theorem_names"]:
        by = [str(c["path"]) for c in same if covers(set(c.get("theorem_names") or []), name, True)]
        if by:
            out["covered_by"][name] = by
        else:
            out["uncovered"].append(name)
    holes = list(cfg.get("definition_names") or [])
    if holes:
        try:
            sidecar = core.sidecar_at(repo, commit)
        except ValueError as e:
            raise core.Fail(str(e)) from None
        signed: dict = {}
        for c in same:
            path = str(c["path"])
            blob = core.git_show_bytes(repo, path, commit)
            try:
                bc = json.loads(blob or b"")
                so = check_definition_holes(bc, f"comparator config {path}", cfg_rel=path, cfg_bytes=blob,
                                            sidecar=sidecar)
            except ValueError:
                continue                    # the receipt validated, so this does not happen; never borrow it
            for h, s in ((so or {}).get("definitions") or {}).items():
                signed.setdefault(h, s)
        out["unsigned"] = [h for h in holes if h not in signed]
        if not out["unsigned"]:
            out["judged_as"] = signoff_summary({"definitions": {h: signed[h] for h in holes}})
    return out


def refusal(rel: str, res: dict) -> str | None:
    """The refusal text for a judged headline that breaks a rule, or None."""
    parts = []
    if res["uncovered"]:
        parts.append(f"{len(res['uncovered'])} theorem name(s) no receipted configuration with the same "
                     f"challenge_module and solution_module covers: {', '.join(res['uncovered'])}")
    if res["unsigned"]:
        parts.append(f"{len(res['unsigned'])} definition hole(s) without a sign-off in {SIDECAR} by a "
                     f"receipted configuration with the same modules: {', '.join(res['unsigned'])}")
    if not parts:
        return None
    return (f"headline configuration {rel} is not a subset of the comparison the gate binds (comparator "
            f"receipt {res['receipt']} at {str(res['receipt_commit'])[:12]}): " + "; ".join(parts)
            + ". Compare them (add them to a compared configuration, run comparator-receipt, re-gate) or "
              "drop them from the headline configuration")


def is_config_path(rel: str) -> bool:
    return bool(CONFIG_RE.match(rel))
