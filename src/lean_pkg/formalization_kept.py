# lean-pkg formalization kept -- the canonical kept view of formalization.yaml and its SHA-256
"""`lean-pkg formalization kept [--repo DIR] [--file REL] [--at REV] [--against REV2] [--json]`.

A package's own release-input audit wants to pin what a person wrote in ``formalization.yaml`` without
tripping over the keys ``lean-pkg formalization`` regenerates after every receipt (``status.evidence``
and the rest of ``formalization.DERIVED_PATHS``). This command prints the **kept view** (the document with
every derived key removed, ``formalization.kept_view``) in one canonical byte form and its SHA-256, so an
audit can pin that hash; ``--against REV2`` reports which kept keys differ from another revision
(``formalization.changed_kept_keys``, the same rule the evidence-only descendant check applies) and exits 1
when any does. Read-only: never the registry, never a write, no network.

The recipe (docs/formalization.md, "The kept view"; pinned by tests/test_formalization_kept.py):

1. Parse the YAML file (UTF-8) as YAML 1.1 with the safe schema (PyYAML ``SafeLoader``), except that
   timestamps are not resolved: an unquoted ``2026-10-17`` stays the string ``"2026-10-17"``.
   A non-mapping document reads as ``{}``.
2. Remove each derived key path in DERIVED_PATHS where it exists; a parent mapping left empty stays (as
   ``{}``). In ``acknowledgements`` (a string) drop every line starting ``Formalizes results of `` or
   ``AI assistance: ``, strip leading and trailing newlines, and drop the key when what is left is
   blank.
3. Serialise as JSON: object keys sorted by Unicode code point, separators ``,`` and ``:`` (no spaces),
   non-ASCII characters written as themselves (not ``\\uXXXX``), no NaN/Infinity, no trailing newline;
   encode as UTF-8.
4. The hash is the lowercase hex SHA-256 of those bytes.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib

import yaml

from . import core
from .formalization import DEFAULT_PATH, DERIVED_PATHS, changed_kept_keys, kept_view

SCHEMA = "formalization-kept/1"
RECIPE = ("kept_view (formalization.DERIVED_PATHS removed; renderer acknowledgements lines dropped), "
          "YAML 1.1 safe schema without timestamp resolution, JSON sort_keys, separators (',', ':'), "
          "ensure_ascii false, UTF-8, no trailing newline; sha256 hex")


class KeptLoader(yaml.SafeLoader):
    """SafeLoader without the timestamp resolver: dates stay strings, so every value maps onto JSON."""


KeptLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()}


def load_text(text: str) -> dict:
    """The document as JSON-shaped data (step 1); a non-mapping document is {}."""
    doc = yaml.load(text, Loader=KeptLoader)   # noqa: S506 -- a SafeLoader subclass
    return doc if isinstance(doc, dict) else {}


def _check_json(value, where: str = "") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise core.Fail(f"formalization.yaml: key {k!r}{' under ' + where if where else ''} is not a "
                                f"string; the kept view needs JSON object keys")
            _check_json(v, f"{where}.{k}" if where else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_json(v, f"{where}[{i}]")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise core.Fail(f"formalization.yaml: {where or 'the document'} is {value!r}; JSON has no NaN/Infinity")
    elif not (value is None or isinstance(value, (str, bool, int))):
        raise core.Fail(f"formalization.yaml: {where or 'the document'} is a {type(value).__name__}, not a "
                        f"JSON value (binary or set tags are not accepted)")


def canonical_bytes(view: dict) -> bytes:
    """Step 3: the canonical JSON bytes of a kept view."""
    _check_json(view)
    return json.dumps(view, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def kept_digest(doc: dict) -> tuple[dict, bytes, str]:
    """(kept view, canonical bytes, sha256 hex) of a loaded document."""
    view = kept_view(doc)
    raw = canonical_bytes(view)
    return view, raw, hashlib.sha256(raw).hexdigest()


def _resolve(repo: pathlib.Path, rev: str) -> str:
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if p.returncode != 0 or not p.stdout.strip():
        raise core.Fail(f"{rev!r} is not a commit in {repo}")
    return p.stdout.strip()


def read_doc(repo: pathlib.Path, rel: str, rev: str | None) -> tuple[dict, str | None]:
    """(document, resolved commit or None for the working tree)."""
    if rev is None:
        path = repo / rel
        if not path.is_file():
            raise core.Fail(f"{rel} not found in {repo}")
        raw, commit = path.read_bytes(), None
    else:
        commit = _resolve(repo, rev)
        raw = core.git_show_bytes(repo, rel, commit)
        if raw is None:
            raise core.Fail(f"{rel} is not tracked at {rev} ({commit[:12]})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise core.Fail(f"{rel} at {rev or 'the working tree'} is not UTF-8: {e}") from None
    try:
        return load_text(text), commit
    except yaml.YAMLError as e:
        raise core.Fail(f"{rel} at {rev or 'the working tree'} is not valid YAML: {e}") from None


def run_kept(args) -> int:
    repo = pathlib.Path(args.repo or ".").expanduser()
    if not repo.is_dir():
        raise core.Fail(f"--repo {repo}: not a directory")
    rel = (args.file or DEFAULT_PATH).strip()
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or pathlib.PurePosixPath(rel).is_absolute() or ".." in pathlib.PurePosixPath(rel).parts:
        raise core.UsageError(f"--file {args.file!r}: a path inside the repository (no '..', not absolute)")
    if args.against is not None and core.git(repo, "rev-parse", "--git-dir").returncode != 0:
        raise core.Fail(f"--against needs a git repository; {repo} is not one")
    doc, commit = read_doc(repo, rel, args.at)
    view, raw, sha = kept_digest(doc)
    payload = {"ok": True, "schema": SCHEMA, "file": rel, "at": args.at, "commit": commit,
               "kept_view": view, "canonical": raw.decode("utf-8"), "sha256": sha,
               "derived_paths": [".".join(p) for p in DERIVED_PATHS], "recipe": RECIPE,
               "against": None, "changed_kept_keys": None, "same": None}
    rc = 0
    if args.against is not None:
        old, old_commit = read_doc(repo, rel, args.against)
        _v, _r, old_sha = kept_digest(old)
        changed = changed_kept_keys(old, doc)
        same = old_sha == sha
        if not changed and not same:   # a difference changed_kept_keys cannot name (deeper than one level)
            changed = ["(kept view)"]
        payload.update(against={"rev": args.against, "commit": old_commit, "sha256": old_sha},
                       changed_kept_keys=changed, same=not changed)
        rc = 1 if changed else 0
    if args.json_out:
        core.print_json(payload)
        return rc
    print(json.dumps(view, sort_keys=True, indent=2, ensure_ascii=False))
    where = f"{args.at} ({commit[:12]})" if commit else "working tree"
    print(f"kept-view sha256 {sha}  {rel} at {where}")
    if args.against is not None:
        a = payload["against"]
        if changed:
            print(f"changed kept key(s) since {args.against} ({a['commit'][:12]}): {', '.join(changed)}")
        else:
            print(f"kept view unchanged since {args.against} ({a['commit'][:12]}); only derived keys may differ")
    return rc


def add_parser(fsub, add_json) -> None:
    k = fsub.add_parser(
        "kept", help="the canonical kept view of formalization.yaml (derived keys removed) and its SHA-256, for "
                     "a release-input audit; --against REV: exit 1 when a kept key changed (read-only)",
        description="`lean-pkg formalization kept [--repo DIR] [--file REL] [--at REV] [--against REV2] [--json]`: "
        "print formalization.yaml without the keys `formalization render` derives (formalization.DERIVED_PATHS "
        "and the renderer's acknowledgements lines), as canonical JSON (sort_keys, separators ',' ':', UTF-8, "
        "no trailing newline) and the SHA-256 of those bytes; the recipe is in docs/formalization.md, \"The kept "
        "view\". --against REV2 lists the kept keys that differ from REV2 and exits 1 when any does. Reads no "
        "registry; writes nothing")
    k.add_argument("--repo", default=None, metavar="DIR",
                   help="the package checkout (default: the current directory)")
    k.add_argument("--file", default=None, metavar="REL",
                   help=f"the file's path inside the checkout (default {DEFAULT_PATH})")
    k.add_argument("--at", default=None, metavar="REV",
                   help="read the file committed at REV (git objects) instead of the working tree")
    k.add_argument("--against", default=None, metavar="REV2",
                   help="compare with the file committed at REV2: changed_kept_keys, exit 1 when any")
    add_json(k)
    k.set_defaults(fn=run_kept, name=None)
