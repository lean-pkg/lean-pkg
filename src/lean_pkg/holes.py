# holes -- the definition-hole sign-off rule shared by the comparator receipt
# runner (before a run) and the receipt validators (against the config bytes
# at the bound commit). Mirrors lint_config in scripts/comparator-check.sh.
"""Why this exists.

leanprover/comparator compares a name listed in a configuration's
``definition_names`` by name, universes, type and safety only
(``Comparator/Compare.lean``), never by body, and its README says such holes
must be checked by an additional, potentially human, verifier. A
configuration with non-empty ``definition_names`` is therefore refused
unless it records that verifier::

    "judged_as": {"reviewer": "<human name>", "date": "YYYY-MM-DD",
                  "definitions": [<every definition_names entry>],
                  "basis": "<how the bodies were compared, optional>"}

``reviewer`` must not be a model or agent token. This is a declaration, not a
verification: the tool records who signed, it cannot check the bodies.
comparator's ``Config`` derives ``Lean.FromJson``, which ignores unknown
fields, so ``judged_as`` may stay in the file.
"""
from __future__ import annotations

import re

REVIEWER_DENY = frozenset({
    "claude", "codex", "gpt", "opus", "sonnet", "fable", "agent", "bot",
    "opencode", "assistant", "llm", "ai",
})
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TAIL = (" (comparator checks definition holes by name and type only, never by "
         "body; a human must sign off in judged_as)")


def check_definition_holes(cfg: dict, label: str) -> None:
    """Raise ValueError unless cfg's definition holes carry a human sign-off."""
    holes = cfg.get("definition_names") or []
    if not isinstance(holes, list) or not all(isinstance(h, str) for h in holes):
        raise ValueError(f"{label}: definition_names must be a list of strings")
    if not holes:
        return
    judged = cfg.get("judged_as")
    if not isinstance(judged, dict):
        raise ValueError(f"{label}: definition_names is non-empty ({len(holes)} hole(s)) "
                         f"but there is no judged_as sign-off object{_TAIL}")
    reviewer = judged.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError(f"{label}: judged_as.reviewer must be a non-empty human name{_TAIL}")
    tokens = [t for t in re.split(r"[^a-z0-9]+", reviewer.lower()) if t]
    if any(t in REVIEWER_DENY for t in tokens):
        raise ValueError(f"{label}: judged_as.reviewer {reviewer!r} names an agent/model "
                         f"token, not a human reviewer{_TAIL}")
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
