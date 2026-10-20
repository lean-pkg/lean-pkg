# holes -- the definition-hole sign-off rule shared by the comparator receipt
# runner (before a run) and the receipt validators (against the config bytes
# at the bound commit), and the sign-off sidecar that holds lean-pkg's own
# comparator metadata. Mirrors lint_config in scripts/comparator-check.sh.
"""Why this exists.

leanprover/comparator compares a name listed in a configuration's
``definition_names`` by name, universes, type and safety only
(``Comparator/Compare.lean``), never by body, and its README says such holes
must be checked by an additional, potentially human, verifier. A
configuration with non-empty ``definition_names`` is therefore refused
unless that verifier is recorded.

Where (#signoff-sidecar). Palomar's verifier refuses any configuration key
outside its six (challenge_module, solution_module, theorem_names,
definition_names, permitted_axioms, enable_nanoda), so lean-pkg keeps its own
metadata out of the configuration, in one sidecar file at the package root,
``lean-pkg-signoff.json`` (deliberately not ``comparator*.json``: the runner
and Palomar would read that as a configuration)::

    {"schema": "lean-pkg-signoff/1",
     "configs": {
       "comparator.json": {
         "config_sha256": "<sha256 of the configuration's bytes>",
         "definitions": {
           "Foo.bar": {"reviewer": "<human name>", "date": "YYYY-MM-DD",
                       "judged_as": "<what the body was judged to be, and how>"}},
         "scaffold": {"todo": [{"theorem_name": "...", ...}], ...},
         "smoke": {"reason": "<why this configuration is a sanity check>"}}}}

Strict: exact key sets, duplicate JSON keys refused, every configuration key
a comparator configuration path. ``config_sha256`` binds the sign-off to the
configuration's bytes: a changed configuration invalidates it until a human
re-judges and re-binds. ``scaffold`` holds the TODO markers `comparator config
--from-claims` writes (formerly the ``lean_pkg_scaffold`` key). ``smoke`` (optional) declares the
configuration a smoke check: a sanity theorem that tests the comparator set-up and states no result of
the package. Its theorems must still pass (they are in the receipt), but they verify no claim, need no
claim in coverage.yaml, and pages show them as smoke checks, never as results (`smoke_paths`).

Deprecated: the inline ``judged_as`` object ({reviewer, date, definitions[],
basis?}) and ``lean_pkg_scaffold`` keys inside the configuration still work
for lean-pkg's own checks, with a deprecation warning naming
``lean-pkg comparator config --migrate-signoff PATH --write``; ``palomar
check`` still refuses them. When the sidecar has an entry for a configuration,
the entry wins.

``reviewer`` must not be a model or agent token. This is a declaration, not a
verification: the tool records who signed, it cannot check the bodies.
"""
from __future__ import annotations

import hashlib
import json
import re

REVIEWER_DENY = frozenset({
    "claude", "codex", "gpt", "opus", "sonnet", "fable", "agent", "bot",
    "opencode", "assistant", "llm", "ai",
})
SIDECAR = "lean-pkg-signoff.json"
SIDECAR_SCHEMA = "lean-pkg-signoff/1"
INLINE_KEYS = ("judged_as", "lean_pkg_scaffold")      # deprecated in a configuration (Palomar refuses them)
ENTRY_KEYS = frozenset({"config_sha256", "definitions", "scaffold", "smoke"})
SIGNOFF_KEYS = frozenset({"reviewer", "date", "judged_as"})
SCAFFOLD_KEYS = frozenset({"generated_by", "note", "types_note", "todo"})
SMOKE_KEYS = frozenset({"reason"})
# Same rule as cli.COMPARATOR_CONFIG_RE (a test pins the two equal); holes.py imports nothing of lean-pkg.
CONFIG_RE = re.compile(r"^(comparator[^/]*\.json|comparator/config[^/]*\.json)$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TAIL = (" (comparator checks definition holes by name and type only, never by "
         f"body; a human must sign off in {SIDECAR})")


def migrate_command(cfg_rel: str) -> str:
    return f"lean-pkg comparator config --migrate-signoff {cfg_rel} --write"


def deprecation(cfg_rel: str, keys) -> str:
    keys = [k for k in INLINE_KEYS if k in set(keys)]
    return (f"{cfg_rel}: inline {' and '.join(keys)} in the comparator configuration is deprecated "
            f"(Palomar refuses keys outside its six); move {'them' if len(keys) > 1 else 'it'} to {SIDECAR}: "
            f"{migrate_command(cfg_rel)} (the config's bytes change: a gated package then needs a new "
            f"comparator receipt)")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _strict_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def check_reviewer(reviewer, where: str) -> None:
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError(f"{where}.reviewer must be a non-empty human name{_TAIL}")
    tokens = [t for t in re.split(r"[^a-z0-9]+", reviewer.lower()) if t]
    if any(t in REVIEWER_DENY for t in tokens):
        raise ValueError(f"{where}.reviewer {reviewer!r} names an agent/model token, not a human reviewer{_TAIL}")


def parse_sidecar(raw: bytes | None, label: str = SIDECAR) -> dict:
    """The sidecar document, validated strictly; {"schema": ..., "configs": {}} when `raw` is None (no file).
    Raises ValueError naming the first problem."""
    if raw is None:
        return {"schema": SIDECAR_SCHEMA, "configs": {}}
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"{label}: not valid strict JSON ({e})")
    if not isinstance(doc, dict) or set(doc) != {"schema", "configs"}:
        raise ValueError(f"{label}: must be an object with exactly the keys schema and configs")
    if doc["schema"] != SIDECAR_SCHEMA:
        raise ValueError(f"{label}: schema must be {SIDECAR_SCHEMA!r}, got {doc['schema']!r}")
    if not isinstance(doc["configs"], dict):
        raise ValueError(f"{label}: configs must be an object keyed by comparator configuration path")
    for rel, ent in doc["configs"].items():
        where = f"{label}: configs[{rel!r}]"
        if not CONFIG_RE.match(rel):
            raise ValueError(f"{where}: not a comparator configuration path (comparator*.json at the package "
                             f"root or comparator/config*.json)")
        if not isinstance(ent, dict) or not ent or set(ent) - ENTRY_KEYS:
            raise ValueError(f"{where}: must be a non-empty object with keys among "
                             f"{', '.join(sorted(ENTRY_KEYS))}")
        defs = ent.get("definitions")
        if defs is not None:
            if not isinstance(defs, dict) or not defs:
                raise ValueError(f"{where}.definitions: must be a non-empty object keyed by definition name")
            if not isinstance(ent.get("config_sha256"), str) or not _HEX64.match(ent["config_sha256"]):
                raise ValueError(f"{where}.config_sha256: must be the 64-hex sha256 of the configuration's "
                                 f"bytes (it binds the sign-off to them)")
            for dname, s in defs.items():
                w = f"{where}.definitions[{dname!r}]"
                if not dname.strip():
                    raise ValueError(f"{w}: empty definition name")
                if not isinstance(s, dict) or set(s) != SIGNOFF_KEYS:
                    raise ValueError(f"{w}: must have exactly the keys {', '.join(sorted(SIGNOFF_KEYS))}")
                check_reviewer(s["reviewer"], w)
                if not isinstance(s["date"], str) or not _DATE_RE.match(s["date"]):
                    raise ValueError(f"{w}.date must be an ISO date YYYY-MM-DD")
                if not isinstance(s["judged_as"], str) or not s["judged_as"].strip():
                    raise ValueError(f"{w}.judged_as must be a non-empty text: what the body was judged to "
                                     f"be, and how")
        elif "config_sha256" in ent:
            raise ValueError(f"{where}: config_sha256 binds a sign-off; it needs definitions")
        sc = ent.get("scaffold")
        if sc is not None:
            if not isinstance(sc, dict) or set(sc) - SCAFFOLD_KEYS or not isinstance(sc.get("todo"), list):
                raise ValueError(f"{where}.scaffold: must be an object with a todo list (keys among "
                                 f"{', '.join(sorted(SCAFFOLD_KEYS))})")
            for i, t in enumerate(sc["todo"]):
                if not isinstance(t, dict) or not isinstance(t.get("theorem_name"), str) or not t["theorem_name"]:
                    raise ValueError(f"{where}.scaffold.todo[{i}]: must be an object with a theorem_name")
            for k in ("generated_by", "note", "types_note"):
                if k in sc and not isinstance(sc[k], str):
                    raise ValueError(f"{where}.scaffold.{k}: must be a string")
        sm = ent.get("smoke")
        if sm is not None and (not isinstance(sm, dict) or set(sm) != SMOKE_KEYS
                               or not isinstance(sm["reason"], str) or not sm["reason"].strip()):
            raise ValueError(f"{where}.smoke: must be an object with exactly the key reason, a non-empty text "
                             f"saying why the configuration is a sanity check and states no result")
    return doc


def smoke_paths(sidecar: dict | None) -> dict[str, str]:
    """{configuration path: reason} of the configurations the sidecar marks as smoke checks."""
    out = {}
    for rel, ent in ((sidecar or {}).get("configs") or {}).items():
        sm = ent.get("smoke") if isinstance(ent, dict) else None
        if isinstance(sm, dict) and isinstance(sm.get("reason"), str) and sm["reason"].strip():
            out[rel] = sm["reason"].strip()
    return out


def dump_sidecar(doc: dict) -> str:
    configs = {k: doc["configs"][k] for k in sorted(doc["configs"]) if doc["configs"][k]}
    return json.dumps({"schema": SIDECAR_SCHEMA, "configs": configs}, indent=2, ensure_ascii=False) + "\n"


def _inline_check(cfg: dict, holes: list[str], label: str) -> dict:
    judged = cfg.get("judged_as")
    if not isinstance(judged, dict):
        raise ValueError(f"{label}: definition_names is non-empty ({len(holes)} hole(s)) but there is no "
                         f"sign-off: no entry in {SIDECAR} and no (deprecated) inline judged_as object{_TAIL}")
    check_reviewer(judged.get("reviewer"), f"{label}: judged_as")
    date = judged.get("date")
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise ValueError(f"{label}: judged_as.date must be an ISO date YYYY-MM-DD{_TAIL}")
    defs = judged.get("definitions")
    if not isinstance(defs, list):
        raise ValueError(f"{label}: judged_as.definitions must be an array covering "
                         f"every definition_names entry{_TAIL}")
    missing = [h for h in holes if h not in defs]
    if missing:
        raise ValueError(f"{label}: judged_as.definitions does not cover definition "
                         f"hole(s): {', '.join(missing)}{_TAIL}")
    basis = judged.get("basis")
    text = basis.strip() if isinstance(basis, str) and basis.strip() else \
        "signed off in the inline judged_as object (no basis recorded)"
    return {h: {"reviewer": judged["reviewer"], "date": date, "judged_as": text} for h in holes}


def check_definition_holes(cfg: dict, label: str, *, cfg_rel: str | None = None,
                           cfg_bytes: bytes | None = None, sidecar: dict | None = None,
                           warn=None) -> dict | None:
    """Raise ValueError unless cfg's definition holes carry a human sign-off.

    Returns None when the configuration has no holes, else {"source": "sidecar"|"inline", "definitions":
    {hole: {reviewer, date, judged_as}}}. With `sidecar` (a parse_sidecar document) and `cfg_rel`, an entry
    for the configuration is used first; its config_sha256 must equal sha256(`cfg_bytes`). Without an
    entry, the deprecated inline judged_as is read, and `warn(msg)` (when given) is told to migrate."""
    holes = cfg.get("definition_names") or []
    if not isinstance(holes, list) or not all(isinstance(h, str) for h in holes):
        raise ValueError(f"{label}: definition_names must be a list of strings")
    inline = [k for k in INLINE_KEYS if k in cfg]
    ent = ((sidecar or {}).get("configs") or {}).get(cfg_rel) if cfg_rel else None
    defs = (ent or {}).get("definitions")
    if not holes:
        if inline and warn:
            warn(deprecation(cfg_rel or label, inline))
        return None
    if defs:
        if cfg_bytes is None:
            raise ValueError(f"{label}: the sign-off in {SIDECAR} is bound to the configuration's bytes, "
                             f"which were not read")
        have = sha256_bytes(cfg_bytes)
        if have != ent["config_sha256"]:
            raise ValueError(f"{label}: the sign-off in {SIDECAR} is bound to config_sha256 "
                             f"{ent['config_sha256'][:12]}, but the configuration's bytes hash to {have[:12]}: a "
                             f"changed configuration invalidates its sign-off; a human re-judges the definition "
                             f"bodies, then sets config_sha256 to {have}{_TAIL}")
        missing = [h for h in holes if h not in defs]
        if missing:
            raise ValueError(f"{label}: {SIDECAR} definitions do not cover definition hole(s): "
                             f"{', '.join(missing)}{_TAIL}")
        extra = sorted(set(defs) - set(holes))
        if extra:
            raise ValueError(f"{label}: {SIDECAR} signs off {', '.join(extra)}, not a definition_names entry of "
                             f"the configuration (remove the stale sign-off)")
        if inline and warn:
            warn(deprecation(cfg_rel or label, inline) + f"; the {SIDECAR} entry is the one read")
        return {"source": "sidecar", "definitions": {h: dict(defs[h]) for h in holes}}
    out = _inline_check(cfg, holes, label)
    if warn:
        warn(deprecation(cfg_rel or label, inline))
    return {"source": "inline", "definitions": out}


def scaffold_block(cfg: dict, cfg_rel: str | None = None, sidecar: dict | None = None) -> dict | None:
    """The scaffold TODO block of a configuration: the sidecar's entry, else the deprecated inline
    lean_pkg_scaffold key."""
    ent = ((sidecar or {}).get("configs") or {}).get(cfg_rel) if cfg_rel else None
    if isinstance(ent, dict) and isinstance(ent.get("scaffold"), dict):
        return ent["scaffold"]
    block = cfg.get("lean_pkg_scaffold") if isinstance(cfg, dict) else None
    return block if isinstance(block, dict) else None


def summary(signoff: dict | None) -> dict | None:
    """One {reviewer, date, definitions, basis} object for a sign-off (the shape the deprecated inline
    judged_as had, which formalization.yaml main_results and the page render): reviewers joined, the
    latest date, the judgements joined."""
    if not signoff:
        return None
    defs = signoff["definitions"]
    uniq = lambda xs: list(dict.fromkeys(xs))   # noqa: E731
    return {"reviewer": "; ".join(uniq(d["reviewer"] for d in defs.values())),
            "date": max(d["date"] for d in defs.values()),
            "definitions": list(defs),
            "basis": "; ".join(uniq(d["judged_as"] for d in defs.values()))}


def receipt_judged(entry: dict) -> dict:
    """{reviewer, date} a comparator receipt's config entry records for its holes: the `signoff` copy
    (from the sidecar) first, else the deprecated `judged_as` copy; {} when neither."""
    so = entry.get("signoff")
    if isinstance(so, dict) and isinstance(so.get("definitions"), dict) and so["definitions"]:
        try:
            return summary({"definitions": so["definitions"]}) or {}
        except (KeyError, TypeError, ValueError):
            return {}
    j = entry.get("judged_as")
    return j if isinstance(j, dict) else {}
