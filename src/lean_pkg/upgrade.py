# lean-pkg doctor --upgrade-from -- what changed for a package author between two lean-pkg versions
"""`lean-pkg doctor --upgrade-from OLD [--to NEW] [--source DIR] [--json]`.

A package author about to move from one lean-pkg to another (or between two commits that carry the same
version string) wants one machine-readable answer to "what changed for me": formats, derived keys,
commands and flags, the Python floor, and the behaviour rules a receipt is now judged by. The design is in
docs/design/upgrade-preflight.md. Two halves:

* **Mechanical** (the surface): read from the two source trees' git objects, never by running their code.
  pyproject.toml (version, requires-python, dependencies, console scripts); the golden CLI contract
  (tests/golden/help/*.txt: the command paths and their options; tests/golden/json-keys.json: each
  command's --json key set); module-level constants found by parsing the source with `ast` (format
  markers such as `"comparator-receipt/2"`, the registry version, formalization's SCHEMA_VERSION and
  DERIVED_PATHS, the on-disk key lists PACKAGE_FIELDS / RECEIPT_FIELDS / COMPARATOR_FIELDS / ...,
  command_tree's alias tables); and the blob ids of the packaged data files (schemas, templates,
  scripts, site profiles).
* **Curated** (behaviour): docs/upgrades/<version>.yaml (schema `lean-pkg-upgrade-record/1`) at the
  NEW tree, one entry per change a surface diff cannot show (a receipt that used to pass and is now
  refused). An entry applies when its PR's squash commit (subject ending `(#N)`) lies in OLD..NEW, so a
  commit-to-commit comparison inside one version gets exactly the entries merged in between.
  Merged PRs in the range that no record names are listed under `uncurated` (the record may be
  incomplete; read their CHANGELOG entries).

Read-only: never the registry, no network, writes nothing.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import tomllib

import yaml

from . import cli
from . import core

SCHEMA = "lean-pkg-upgrade/1"
RECORD_SCHEMA = "lean-pkg-upgrade-record/1"
RECORD_DIR = "docs/upgrades"
SRC = "src/lean_pkg"
HELP_DIR = "tests/golden/help"
JSON_KEYS = "tests/golden/json-keys.json"
DATA_DIRS = ("schemas", "templates", "scripts", "site_profiles")
FORMAT_RE = re.compile(r"^([a-z][a-z0-9-]*)/(\d+)$")
# Named constants whose value is a version or a key list of an on-disk format (module, name). A `core`
# constant is read from cli.py in a tree that has no core.py yet (before the core split, #317): _const.
NAMED = {
    "registry_version": ("core", "REGISTRY_VERSION"),
    "registry_versions_read": ("core", "SUPPORTED_REGISTRY_VERSIONS"),
    "event_line_version": ("core", "EVENT_LINE_VERSION"),
    "formalization_yaml_version": ("formalization", "SCHEMA_VERSION"),
}
KEY_LISTS = {   # format label -> (module, constant in lower case: an upper-case COMPARATOR_* literal reads as an
    # environment variable to the suite's env scan); a tuple of tuples counts by its first items
    "registry package": ("core", "package_fields"),
    "registry lease": ("core", "lease_fields"),
    "lower-tier receipt": ("core", "receipt_fields"),
    "comparator receipt": ("core", "comparator_fields"),
    "comparator receipt config": ("core", "comparator_config_fields"),
    "comparator receipt digests": ("core", "comparator_toolchain_digests"),
    "formalization.yaml derived": ("formalization", "derived_paths"),
}
RECORD_KINDS = ("behavior", "format", "command", "requirement")
RECORD_KEYS = {"pr", "id", "kind", "area", "summary", "action", "breaking", "changelog", "split"}


class Unknown:
    """A value the restricted evaluator does not compute (a call result, an f-string, arithmetic)."""

    def __repr__(self) -> str:
        return "<unknown>"


UNKNOWN = Unknown()


# --------------------------------------------------------------------------
# reading a tree from git objects


class Tree:
    def __init__(self, repo: pathlib.Path, rev: str):
        self.repo, self.rev = repo, rev
        self.commit = resolve(repo, rev)
        self._ls: list[str] | None = None

    def read(self, rel: str) -> str | None:
        p = core.git(self.repo, "show", f"{self.commit}:{rel}")
        return p.stdout if p.returncode == 0 else None

    def files(self) -> list[str]:
        if self._ls is None:
            p = core.git(self.repo, "ls-tree", "-r", "--name-only", self.commit)
            self._ls = p.stdout.splitlines() if p.returncode == 0 else []
        return self._ls

    def blobs(self, prefix: str) -> dict[str, str]:
        p = core.git(self.repo, "ls-tree", "-r", self.commit, "--", prefix)
        out = {}
        for line in p.stdout.splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob" and "__pycache__" not in path:
                out[path[len(SRC) + 1:]] = parts[2]
        return out


def resolve(repo: pathlib.Path, rev: str) -> str:
    """A lean-pkg version (0.9.0 / v0.9.0: its tag, else the `release: 0.9.0` commit) or any git revision."""
    candidates = [rev]
    m = re.fullmatch(r"v?(\d+\.\d+\.\d+(?:[-.+][0-9A-Za-z.]+)?)", rev)
    if m:
        candidates = [f"v{m.group(1)}", rev]
    for c in candidates:
        p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{c}^{{commit}}")
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    if m:
        p = core.git(repo, "log", "--all", "--format=%H", "--fixed-strings", f"--grep=release: {m.group(1)}")
        for h in p.stdout.split():
            subj = core.git(repo, "log", "-1", "--format=%s", h).stdout.strip()
            if subj == f"release: {m.group(1)}":
                return h
    raise core.Fail(f"{rev!r} is neither a lean-pkg version (tag v{rev.lstrip('v')} or a `release:` commit) nor a "
                    f"commit in {repo}")


# --------------------------------------------------------------------------
# the restricted evaluator (never executes the source)


def literal(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return tuple(literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return {literal(k): literal(v) for k, v in zip(node.keys, node.values, strict=False) if k is not None}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return {"__call__": node.func.id, "args": tuple(literal(a) for a in node.args),
                **{k.arg: literal(k.value) for k in node.keywords if k.arg}}
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return UNKNOWN
    return UNKNOWN


def module_constants(source: str | None) -> dict:
    """Module-level NAME = <literal> assignments (the last one wins), values evaluated by `literal`."""
    if not source:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = literal(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            out[node.target.id] = literal(node.value)
    return out


def _jsonable(v):
    if isinstance(v, Unknown):
        return None
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return v


def _key_names(v) -> list[str] | None:
    """Key names of a key-list constant: strings as they are, key paths (DERIVED_PATHS) dotted, and
    (name, tier, meaning) rows (PACKAGE_FIELDS) by their first item."""
    if not isinstance(v, tuple):
        return None
    if v and _is_path_list(v):
        return [".".join(i) for i in v]
    return [i if isinstance(i, str) else i[0] for i in v
            if isinstance(i, str) or (isinstance(i, tuple) and i and isinstance(i[0], str))]


def _is_path_list(v) -> bool:
    """DERIVED_PATHS-like: a tuple of tuples of identifiers (key paths), not (name, tier, meaning) rows."""
    return all(isinstance(i, tuple) and all(isinstance(x, str) and re.fullmatch(r"[a-z_]+", x) for x in i)
               for i in v)


# --------------------------------------------------------------------------
# the surface of one tree


def help_surface(tree: Tree) -> dict[str, list[str]] | None:
    """{command path: sorted options} from the help goldens ("" is the bare `lean-pkg`)."""
    names = [f for f in tree.files() if f.startswith(HELP_DIR + "/") and f.endswith(".txt")]
    if not names:
        return None
    out = {}
    for f in names:
        stem = f[len(HELP_DIR) + 1:-4]
        if stem == "lean-pkg":
            path = ""
        elif stem.startswith("lean-pkg."):
            path = stem[len("lean-pkg."):].replace(".", " ")
        else:
            path = stem   # another executable (comparator-receipt, lean-exercise, ...)
        text = tree.read(f) or ""
        opts = set()
        for line in text.splitlines():
            m = re.match(r"^\s{2,}(?:-\w, )?(--[a-z0-9][a-z0-9-]*)", line)
            if m:
                opts.add(m.group(1))
        out[path] = sorted(opts)
    return out


def alias_surface(consts: dict) -> dict:
    cmds, flags, implied = [], [], []
    for a in consts.get("COMMAND_ALIASES") or ():
        if isinstance(a, dict) and len(a.get("args", ())) == 2:
            old, new = a["args"]
            cmds.append(f"{' '.join(old)} -> {' '.join(new)}")
    for a in (consts.get("FLAG_ALIASES") or ()) + (consts.get("PROGRAM_FLAG_ALIASES") or ()):
        if isinstance(a, dict) and len(a.get("args", ())) == 3:
            cmd, old, new = a["args"]
            flags.append(f"{' '.join(cmd)} {old} -> {new}")
    iv = consts.get("IMPLIED_VERBS")
    if isinstance(iv, dict):
        for noun, v in iv.items():
            if isinstance(v, dict) and v.get("args"):
                verbs = v["args"][1] if len(v["args"]) > 1 else ()
                implied.append(f"{noun} NAME -> {noun} {v['args'][0]} NAME (verbs: {', '.join(verbs)})")
    return {"commands": sorted(cmds), "flags": sorted(flags), "implied_verbs": sorted(implied)}


def alias_map(consts: dict) -> dict:
    """The alias tables as lookups: {old words: new words}, {noun: implied verb}, {command: {old: new flag}}."""
    cmds, implied, flags = {}, {}, {}
    for a in consts.get("COMMAND_ALIASES") or ():
        if isinstance(a, dict) and len(a.get("args", ())) == 2:
            cmds[" ".join(a["args"][0])] = " ".join(a["args"][1])
    iv = consts.get("IMPLIED_VERBS")
    for noun, v in (iv.items() if isinstance(iv, dict) else ()):
        if isinstance(v, dict) and v.get("args") and isinstance(v["args"][0], str):
            implied[noun] = v["args"][0]
    for a in consts.get("FLAG_ALIASES") or ():
        if isinstance(a, dict) and len(a.get("args", ())) == 3:
            cmd, old, new = a["args"]
            flags.setdefault(" ".join(cmd), {})[old] = new
    modes: dict[str, dict[str, str]] = {}
    for a in consts.get("MODE_ALIASES") or ():   # ModeAlias(command, flag, new command, ...): a split mode
        if isinstance(a, dict) and len(a.get("args", ())) >= 3:
            cmd, flag, new = a["args"][:3]
            if isinstance(cmd, tuple) and isinstance(new, tuple) and isinstance(flag, str):
                modes.setdefault(" ".join(cmd), {})[flag] = " ".join(new)
    hidden = consts.get("HELP_HIDDEN_FLAGS")
    hidden = sorted(h for h in hidden if isinstance(h, str)) if isinstance(hidden, (tuple, list)) else []
    return {"commands": cmds, "implied": implied, "flags": flags, "modes": modes, "hidden": hidden}


def command_delta(old: dict, new: dict, amap: dict) -> dict:
    """Command paths and their options, old -> new, read through the NEW tree's alias tables: a path the
    new tree spells differently is `renamed` (its old spelling keeps working), not removed; a path that
    became a noun with verbs keeps every option one of its verbs has; a flag spelled differently is
    `renamed` too. An option of the new tree's HELP_HIDDEN_FLAGS (accepted, help shown only where it acts)
    is never removed."""
    def target(path: str) -> str | None:
        if path in new:
            return path
        for o, n in sorted(amap["commands"].items(), key=lambda kv: -len(kv[0])):
            if path == o or path.startswith(o + " "):
                cand = n + path[len(o):]
                if cand in new:
                    return cand
        if path in amap["implied"] and f"{path} {amap['implied'][path]}" in new:
            return f"{path} {amap['implied'][path]}"
        return None

    renamed, removed, options = {}, [], {}
    targets = set()
    for path in sorted(old):
        t = target(path)
        if t is None:
            removed.append(path)
            continue
        targets.add(t)
        if t != path:
            renamed[path] = t
        noun = t.split(" ")[0] if t != path and path in amap["implied"] else None
        have = set(new[t])
        base = noun if noun is not None else (path if path and any(p.startswith(path + " ") for p in new) else None)
        if base is not None:   # a noun with verbs: an option of any verb still exists
            have |= {o for p in new if p == base or p.startswith(base + " ") for o in new[p]}
        fl = {o: n for p in new if p == t or (base and p.startswith(base + " "))
              for o, n in amap["flags"].get(p, {}).items()}
        gone = sorted(set(old[path]) - have)
        ren = {o: fl[o] for o in gone if o in fl}
        modes = amap.get("modes", {}).get(t, {})   # a mode split off as its own command (MODE_ALIASES)
        moved = {o: modes[o] for o in gone if o in modes}
        for o in gone:
            if o not in ren and o not in moved:
                hit = next((m for m in sorted(set(modes.values())) if o in new.get(m, ())), None)
                if hit:
                    moved[o] = hit
        lost = [o for o in gone if o not in fl and o not in moved and o not in amap.get("hidden", ())]
        added = sorted(set(new[t]) - set(old[path]) - set(ren.values()))
        if lost or ren or added or moved:
            options[path if t == path else f"{path} -> {t}"] = {"added": added, "removed": lost, "renamed": ren,
                                                                "moved": moved}
    added_paths = sorted(p for p in new if p not in old and p not in targets)
    return {"added": added_paths, "removed": removed, "renamed": renamed, "options": options}


def _const(consts: dict, mod: str, name: str):
    """The parsed value of constant `name` (any case) of module `mod`, None when absent. Module `core` falls
    back to `cli`: the shared constants lived in cli.py until the core split (#317), so an older tree
    reads the same as a newer one."""
    found = consts.get(mod, {})
    if mod == "core" and name.upper() not in found:
        found = consts.get("cli", {})
    return found.get(name.upper())


def surface(tree: Tree) -> dict:
    """Everything mechanically derivable about one tree, as JSON-shaped data."""
    py = tree.read("pyproject.toml")
    proj = tomllib.loads(py).get("project", {}) if py else {}
    modules = sorted(f for f in tree.files() if f.startswith(SRC + "/") and f.endswith(".py")
                     and "/" not in f[len(SRC) + 1:])
    consts = {f[len(SRC) + 1:-3]: module_constants(tree.read(f)) for f in modules}
    formats: dict[str, int] = {}
    for mod in sorted(consts):
        for _name, v in consts[mod].items():
            if isinstance(v, str) and (m := FORMAT_RE.match(v)):
                formats[m.group(1)] = max(formats.get(m.group(1), 0), int(m.group(2)))
            elif isinstance(v, tuple) and v and all(isinstance(x, str) and FORMAT_RE.match(x) for x in v):
                for x in v:
                    m = FORMAT_RE.match(x)
                    formats[m.group(1)] = max(formats.get(m.group(1), 0), int(m.group(2)))
    named = {label: _jsonable(_const(consts, *where)) for label, where in NAMED.items()}
    keys = {label: _key_names(_const(consts, *where)) for label, where in KEY_LISTS.items()}
    jk = tree.read(JSON_KEYS)
    try:
        json_keys = {k: sorted(v.get("keys") or []) for k, v in json.loads(jk).items()} if jk else None
    except (ValueError, AttributeError):
        json_keys = None
    data = {}
    for d in DATA_DIRS:
        data.update(tree.blobs(f"{SRC}/{d}"))
    return {
        "version": proj.get("version"),
        "requires_python": proj.get("requires-python"),
        "dependencies": sorted(proj.get("dependencies") or []),
        "optional_dependencies": {k: sorted(v) for k, v in (proj.get("optional-dependencies") or {}).items()},
        "scripts": sorted((proj.get("scripts") or {}).keys()),
        "commands": help_surface(tree),
        "json_keys": json_keys,
        "formats": formats,
        "named": named,
        "keys": keys,
        "aliases": alias_surface(consts.get("command_tree", {})) if "command_tree" in consts else None,
        "alias_map": alias_map(consts.get("command_tree", {})),
        "data_files": data,
    }


# --------------------------------------------------------------------------
# the delta


def _set_delta(a, b) -> dict:
    a, b = set(a or ()), set(b or ())
    return {"added": sorted(b - a), "removed": sorted(a - b)}


def _map_delta(a: dict | None, b: dict | None) -> dict:
    """Keyed lists: entries added/removed, and per common key the items added/removed."""
    a, b = a or {}, b or {}
    out = {**_set_delta(a.keys(), b.keys()), "changed": {}}
    for k in sorted(set(a) & set(b)):
        d = _set_delta(a[k], b[k])
        if d["added"] or d["removed"]:
            out["changed"][k] = d
    return out


def _scalar(a, b) -> dict:
    return {"from": a, "to": b, "changed": a != b}


def delta(old: dict, new: dict) -> dict:
    formats = {"added": {}, "removed": {}, "changed": {}}
    for fam in sorted(set(old["formats"]) | set(new["formats"])):
        a, b = old["formats"].get(fam), new["formats"].get(fam)
        if a is None:
            formats["added"][fam] = f"{fam}/{b}"
        elif b is None:
            formats["removed"][fam] = f"{fam}/{a}"
        elif a != b:
            formats["changed"][fam] = {"from": f"{fam}/{a}", "to": f"{fam}/{b}"}
    out = {
        "version": _scalar(old["version"], new["version"]),
        "requires_python": _scalar(old["requires_python"], new["requires_python"]),
        "dependencies": _set_delta(old["dependencies"], new["dependencies"]),
        "scripts": _set_delta(old["scripts"], new["scripts"]),
        "commands": None if old["commands"] is None or new["commands"] is None else
        command_delta(old["commands"], new["commands"], new["alias_map"]),
        "json_keys": None if old["json_keys"] is None or new["json_keys"] is None else
        _map_delta(old["json_keys"], new["json_keys"]),
        "formats": formats,
        "named": {k: _scalar(old["named"].get(k), new["named"].get(k)) for k in NAMED},
        "keys": {k: _set_delta(old["keys"].get(k), new["keys"].get(k)) for k in KEY_LISTS},
        "aliases": {k: _set_delta((old["aliases"] or {}).get(k), (new["aliases"] or {}).get(k))
                    for k in ("commands", "flags", "implied_verbs")},
        "data_files": {**_set_delta(old["data_files"], new["data_files"]),
                       "changed": sorted(k for k in set(old["data_files"]) & set(new["data_files"])
                                         if old["data_files"][k] != new["data_files"][k])},
    }
    return out


# --------------------------------------------------------------------------
# curated records


def load_records(tree: Tree) -> tuple[list[dict], list[str]]:
    """Every entry of docs/upgrades/*.yaml at the tree (with its record's version), and problems."""
    entries, problems = [], []
    for f in sorted(x for x in tree.files() if x.startswith(RECORD_DIR + "/") and x.endswith(".yaml")):
        try:
            doc = yaml.safe_load(tree.read(f) or "")
        except yaml.YAMLError as e:
            problems.append(f"{f}: not valid YAML: {e}")
            continue
        problems += [f"{f}: {p}" for p in validate_record(doc)]
        if not isinstance(doc, dict):
            continue
        if isinstance(doc.get("version"), str) and f != f"{RECORD_DIR}/{doc['version']}.yaml":
            problems.append(f"{f}: the file name must be the record's version ({doc['version']}.yaml)")
        for e in doc.get("changes") or []:
            if isinstance(e, dict):
                entries.append({**e, "record": f, "release": doc.get("version")})
        for n in doc.get("no_author_change") or []:
            if isinstance(n, int):
                entries.append({"pr": n, "id": None, "no_author_change": True, "record": f,
                                "release": doc.get("version")})
    return entries, problems


def validate_record(doc) -> list[str]:
    """Problems of one curated record (schema lean-pkg-upgrade-record/1)."""
    if not isinstance(doc, dict):
        return ["not a mapping"]
    out = []
    if doc.get("schema") != RECORD_SCHEMA:
        out.append(f"schema must be {RECORD_SCHEMA}")
    if not isinstance(doc.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", doc.get("version") or ""):
        out.append("version must be X.Y.Z")
    changes = doc.get("changes")
    # Empty only while the release so far has no author-facing change: then no_author_change lists the PRs read.
    if not isinstance(changes, list) or (not changes and not doc.get("no_author_change")):
        return out + ["changes must be a non-empty list (or empty with a non-empty no_author_change)"]
    extra = set(doc) - {"schema", "version", "changes", "no_author_change"}
    if extra:
        out.append(f"unknown key(s) {', '.join(sorted(extra))}")
    nac = doc.get("no_author_change", [])
    if not isinstance(nac, list) or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in nac):
        out.append("no_author_change must be a list of PR numbers")
    seen = set()
    by_pr: dict[int, list[dict]] = {}
    for i, e in enumerate(changes):
        if not isinstance(e, dict):
            out.append(f"changes[{i}] is not a mapping")
            continue
        extra = set(e) - RECORD_KEYS
        missing = {"pr", "id", "kind", "summary", "action", "breaking"} - set(e)
        if extra:
            out.append(f"changes[{i}]: unknown key(s) {', '.join(sorted(extra))}")
        if missing:
            out.append(f"changes[{i}]: missing {', '.join(sorted(missing))}")
        if "pr" in e and not (isinstance(e["pr"], int) and not isinstance(e["pr"], bool) and e["pr"] > 0):
            out.append(f"changes[{i}].pr must be a PR number")
        if e.get("kind") not in RECORD_KINDS:
            out.append(f"changes[{i}].kind must be one of {', '.join(RECORD_KINDS)}")
        if "breaking" in e and not isinstance(e["breaking"], bool):
            out.append(f"changes[{i}].breaking must be true or false")
        if "split" in e and not isinstance(e["split"], bool):
            out.append(f"changes[{i}].split must be true or false")
        if e.get("id") in seen:
            out.append(f"changes[{i}].id {e.get('id')!r} repeats")
        seen.add(e.get("id"))
        if isinstance(e.get("pr"), int) and not isinstance(e["pr"], bool):
            by_pr.setdefault(e["pr"], []).append(e)
    # One entry per pull request, unless the PR made several separate changes and each of its
    # entries says so (split: true). An unmarked repeat is one change recorded twice, as #285
    # was in 0.19.0 (one entry breaking, the other not).
    for pr, es in by_pr.items():
        ids = ", ".join(str(e.get("id")) for e in es)
        if len(es) > 1 and not all(e.get("split") is True for e in es):
            out.append(f"pr {pr} has {len(es)} entries ({ids}): merge them, or mark each split: true "
                       f"when the pull request made separate changes")
        if len(es) == 1 and es[0].get("split") is True:
            out.append(f"pr {pr} has one entry ({ids}) marked split: true")
    if isinstance(nac, list):
        for pr in sorted(set(by_pr) & {n for n in nac if isinstance(n, int)}):
            out.append(f"pr {pr} is both a change and in no_author_change")
    return out


def _released(repo: pathlib.Path, version: str) -> str | None:
    """The commit of release X.Y.Z (tag or `release:` commit), None when it was never cut."""
    try:
        return resolve(repo, version)
    except core.Fail:
        return None


def provisional_problems(repo: pathlib.Path, tree: Tree) -> list[str]:
    """Only the newest record may be unreleased: its name is provisional until the release is cut, when
    scripts/cut-release.py renames it to the version actually cut (RELEASING.md "When to tag",
    docs/design/upgrade-preflight.md). Reported: an older record that was never released, and an
    unreleased record that lists a pull request already shipped in the tree's current version (the
    record of that release kept its provisional name). A clone without any release commit or tag
    compares nothing."""
    recs: dict[str, str] = {}
    for f in tree.files():
        if f.startswith(RECORD_DIR + "/") and f.endswith(".yaml"):
            try:
                doc = yaml.safe_load(tree.read(f) or "")
            except yaml.YAMLError:
                continue
            v = doc.get("version") if isinstance(doc, dict) else None
            if isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+", v):
                recs[v] = f
    if not recs:
        return []
    key = lambda v: tuple(int(x) for x in v.split("."))  # noqa: E731
    order = sorted(recs, key=key)
    released = {v: _released(repo, v) for v in order}
    try:
        current = tomllib.loads(tree.read("pyproject.toml") or "")["project"]["version"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        current = None
    cut = _released(repo, current) if isinstance(current, str) else None
    if not any(released.values()) and cut is None:
        return []
    out = [f"{recs[v]}: {v} was never released (no tag v{v} or `release: {v}` commit); only the newest record "
           f"may carry a provisional name" for v in order[:-1] if released[v] is None]
    newest = order[-1]
    if released[newest] is None:
        if cut and current != newest:
            doc = yaml.safe_load(tree.read(recs[newest]) or "") or {}
            listed = {e.get("pr") for e in doc.get("changes") or [] if isinstance(e, dict)}
            listed |= {n for n in doc.get("no_author_change") or [] if isinstance(n, int)}
            shipped = sorted(n for n in merged_prs(repo, None, cut) if n in listed)
            if shipped:
                fix = (f"move them to {recs[current]}" if current in recs else
                       f"the record is {current}'s: rename it (scripts/release-version.py --rename {current})")
                out.append(f"{recs[newest]}: unreleased (provisional name {newest}), but "
                           + ", ".join(f"#{n}" for n in shipped) + f" shipped in {current}; {fix}")
    return out


def merged_prs(repo: pathlib.Path, old: str | None, new: str) -> dict[int, dict]:
    """{PR number: {commit, subject}} for the commits in old..new (old None: all of new's history) whose
    subject ends `(#N)`."""
    p = core.git(repo, "log", "--format=%H%x09%s", f"{old}..{new}" if old else new)
    out = {}
    for line in p.stdout.splitlines():
        h, _, subj = line.partition("\t")
        m = re.search(r"\(#(\d+)\)\s*$", subj)
        if m:
            out.setdefault(int(m.group(1)), {"commit": h, "subject": subj})
    return out


# --------------------------------------------------------------------------
# the command


def default_source() -> pathlib.Path | None:
    """The git checkout the running lean-pkg is imported from (an editable install), else None."""
    d = cli.package_dir()
    p = core.git(d, "rev-parse", "--show-toplevel")
    return pathlib.Path(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def preflight(repo: pathlib.Path, old_rev: str, new_rev: str) -> dict:
    old, new = Tree(repo, old_rev), Tree(repo, new_rev)
    if core.git(repo, "merge-base", "--is-ancestor", old.commit, new.commit).returncode != 0:
        raise core.Fail(f"{old_rev} ({old.commit[:12]}) is not an ancestor of {new_rev} ({new.commit[:12]}); "
                        f"compare an older revision with a newer one")
    s_old, s_new = surface(old), surface(new)
    # Records are read at the checkout's HEAD when it descends from NEW (a record written after a release
    # still describes it), else at NEW.
    head = Tree(repo, "HEAD")
    rec_tree = head if core.git(repo, "merge-base", "--is-ancestor", new.commit, head.commit).returncode == 0 else new
    entries, problems = load_records(rec_tree)
    problems += provisional_problems(repo, rec_tree)
    prs = merged_prs(repo, old.commit, new.commit)
    applies = [{**e, "commit": prs[e["pr"]]["commit"]} for e in entries if isinstance(e.get("pr"), int)
               and e["pr"] in prs and not e.get("no_author_change")]
    named_prs = {e.get("pr") for e in entries}
    uncurated = [{"pr": n, **prs[n]} for n in sorted(prs) if n not in named_prs]
    d = delta(s_old, s_new)
    return {
        "ok": True, "schema": SCHEMA,
        "from": {"rev": old_rev, "commit": old.commit, "version": s_old["version"]},
        "to": {"rev": new_rev, "commit": new.commit, "version": s_new["version"]},
        "same_version": s_old["version"] == s_new["version"],
        "mechanical": d,
        "behavior": sorted(applies, key=lambda e: e["pr"]),
        "breaking": [e["id"] for e in sorted(applies, key=lambda e: e["pr"]) if e.get("breaking")],
        "uncurated": uncurated,
        "record_problems": problems,
        "records_at": rec_tree.commit,
        "not_derivable": ["what a refusal rule accepts or refuses (curated: behavior[])",
                          "the meaning of a key whose name is unchanged",
                          "reader compatibility (which old format versions are still accepted)"],
    }


def _lines(payload: dict) -> list[str]:
    d, out = payload["mechanical"], []
    f, t = payload["from"], payload["to"]
    out.append(f"lean-pkg upgrade preflight: {f['rev']} ({f['commit'][:12]}, {f['version']}) -> "
               f"{t['rev']} ({t['commit'][:12]}, {t['version']})")

    def sec(title: str, items: list[str]) -> None:
        if items:
            out.append(f"{title}:")
            out.extend(f"  {x}" for x in items)

    sec("python", [f"requires-python {d['requires_python']['from']} -> {d['requires_python']['to']}"]
        if d["requires_python"]["changed"] else [])
    sec("dependencies", [f"+ {x}" for x in d["dependencies"]["added"]] + [f"- {x}" for x in d["dependencies"]["removed"]])
    sec("executables", [f"+ {x}" for x in d["scripts"]["added"]] + [f"- {x}" for x in d["scripts"]["removed"]])
    fm = d["formats"]
    sec("formats", [f"+ {v}" for v in fm["added"].values()] + [f"- {v}" for v in fm["removed"].values()]
        + [f"{c['from']} -> {c['to']}" for c in fm["changed"].values()]
        + [f"{k}: {v['from']} -> {v['to']}" for k, v in d["named"].items() if v["changed"]])
    sec("on-disk keys", [f"{k}: " + ", ".join([f"+ {x}" for x in v["added"]] + [f"- {x}" for x in v["removed"]])
                         for k, v in d["keys"].items() if v["added"] or v["removed"]])
    if d["commands"] is not None:
        c = d["commands"]
        sec("commands", [f"new: {x or 'lean-pkg'}" for x in c["added"]]
            + [f"removed: {x or 'lean-pkg'}" for x in c["removed"]]
            + [f"renamed: {o} -> {n} (the old spelling keeps working)" for o, n in c["renamed"].items()]
            + [f"{k or 'lean-pkg'}: " + "; ".join(([f"new {', '.join(v['added'])}"] if v["added"] else [])
                                                  + ([f"removed {', '.join(v['removed'])}"] if v["removed"] else [])
                                                  + [f"renamed {o} -> {n}" for o, n in v["renamed"].items()]
                                                  + [f"{o} moved to `{n}` (the old form keeps working)"
                                                     for o, n in v.get("moved", {}).items()])
               for k, v in c["options"].items()])
    if d["json_keys"] is not None:
        j = d["json_keys"]
        sec("--json keys", [f"+ {x}" for x in j["added"]] + [f"- {x}" for x in j["removed"]]
            + [f"{k}: " + ", ".join([f"+ {x}" for x in v["added"]] + [f"- {x}" for x in v["removed"]])
               for k, v in j["changed"].items()])
    a = d["aliases"]
    sec("aliases", [f"+ {x}" for k in a for x in a[k]["added"]] + [f"- {x}" for k in a for x in a[k]["removed"]])
    df = d["data_files"]
    sec("packaged files", [f"+ {x}" for x in df["added"]] + [f"- {x}" for x in df["removed"]]
        + [f"~ {x}" for x in df["changed"]])
    sec("behavior (curated)", [f"#{e['pr']} {e['id']}{' [breaking]' if e.get('breaking') else ''}: {e['summary']}"
                               + (f"\n    do: {e['action']}" if e.get("action") else "")
                               for e in payload["behavior"]])
    sec("uncurated PRs in the range (read their CHANGELOG entries)",
        [f"#{u['pr']} {u['subject']}" for u in payload["uncurated"]])
    sec("record problems", payload["record_problems"])
    out.append(f"breaking (curated): {', '.join(payload['breaking']) or 'none'}")
    return out


def run(args) -> int:
    src = pathlib.Path(args.upgrade_source).expanduser() if args.upgrade_source else default_source()
    if src is None:
        raise core.Fail("--upgrade-from needs a lean-pkg git checkout: this lean-pkg is not imported from one; "
                        "pass --source DIR (a clone of the lean-pkg repository)")
    if core.git(src, "rev-parse", "--git-dir").returncode != 0:
        raise core.Fail(f"--source {src}: not a git repository")
    payload = preflight(src, args.upgrade_from, args.upgrade_to or "HEAD")
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(_lines(payload)))
    return 0


def add_arguments(s) -> None:
    s.add_argument("--upgrade-from", dest="upgrade_from", default=None, metavar="OLD",
                   help="instead of the diagnostics: what changed for a package author since OLD (a version, "
                        "0.9.0 / v0.9.0, or any commit of the lean-pkg repository): formats, on-disk keys, "
                        "commands, flags, --json keys, aliases, Python floor, packaged files, and the curated "
                        "behaviour changes of docs/upgrades/; read-only (docs/design/upgrade-preflight.md)")
    s.add_argument("--to", dest="upgrade_to", default=None, metavar="NEW",
                   help="with --upgrade-from: the newer version or commit (default HEAD of the source)")
    s.add_argument("--source", dest="upgrade_source", default=None, metavar="DIR",
                   help="with --upgrade-from: the lean-pkg git checkout to read both trees from (default: the "
                        "checkout this lean-pkg runs from)")
