"""`lean-pkg seal-bundle`: render a hash-bound job bundle from lower-tier receipts and a package template.

A seal is a job (an external kernel replay, a fresh rebuild, an axiom audit) that re-checks a commit some
receipt already verified. Its bundle is the job script plus every input it binds by hash: the source
archive, module lists and the package's helper scripts, with INPUT-SHA256SUMS over them. Assembled by hand,
each seal edits the same lines (commit, tree, dependency pins, theorem counts, inline hashes) and can get
one wrong (#414). Here every such value is derived from the receipts and the commit:

- the commit from the first package's receipt; the tree is `git rev-parse COMMIT:./` of --repo;
- each other package's receipt must name a commit whose tree for that package equals the bundled one,
  apart from .lean-receipts/ (its own receipt, committed after its gate);
- module lists from each package's umbrella imports, which must equal its .lean files;
- counts from a pattern over each receipt's sealed verify log (one distinct value, else a refusal);
- dependency revisions from lake-manifest.json committed at the commit;
- source.tgz: `git archive --mtime=@<commit time> COMMIT:./`, gzip without a name or time, so the
  same commit always gives the same bytes.

The template is the package's own job script with @@NAME@@ placeholders; the job body stays the
package's. lean-pkg knows no site: paths of a cluster are written in the template as they are.
`--check DIR` re-derives an existing bundle and lists every difference (calibration of a hand-made one).
Nothing here writes a receipt, the registry or the event log.
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import tarfile

import yaml

from . import core
from . import gate as gate_mod
from .core import Fail, UsageError

SPEC_SCHEMA = "seal-bundle/1"
SPEC_KEYS = {"schema", "template", "packages", "script", "source", "sums", "all_list", "pilot_list",
             "counts", "deps", "files"}
SPEC_REQUIRED = ("schema", "template", "packages")
PACKAGE_KEYS = {"key", "dir", "umbrella", "list"}
COUNT_KEYS = {"var", "package", "pattern"}
DEFAULTS = {"script": "gate.sbatch", "source": "source.tgz", "sums": "INPUT-SHA256SUMS",
            "pilot_list": "modules-pilot.txt"}
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")      # a bundle file name: no directory
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*$")
IMPORT_RE = re.compile(r"^import\s+(\S+)\s*$")
PLACEHOLDER_RE = re.compile(r"@@([A-Z][A-Z0-9_]*)(?::([A-Za-z0-9._-]+))?@@")
BUILTIN_VARS = {"COMMIT", "COMMIT_SHORT", "TREE", "TREE_SHORT", "NOTE", "SHA256"}
COMMIT_SHORT, TREE_SHORT = 7, 8
RECEIPTS_DIR = ".lean-receipts"
NOTE_MAX = 200
DIFF_LINES = 40


# --------------------------------------------------------------------------
# the spec

def _rel(value, what: str) -> str:
    """A relative path inside the spec's (or the package's) directory: no absolute path, no `..`."""
    if not isinstance(value, str) or not value.strip():
        raise Fail(f"{what} must be a nonempty relative path, got {value!r}")
    p = pathlib.PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts:
        raise Fail(f"{what} must stay inside its directory (no absolute path, no ..): {value!r}")
    return str(p)


def load_spec(path: pathlib.Path) -> dict:
    """The seal-bundle/1 spec, every problem refused with its key. Unknown keys are refused, not ignored."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise Fail(f"cannot read the spec {path}: {e.strerror or e}") from None
    except yaml.YAMLError as e:
        raise Fail(f"{path}: not YAML: {e}") from None
    if not isinstance(data, dict):
        raise Fail(f"{path}: the spec must be a mapping")
    problems = [f"unknown key {k!r}" for k in sorted(set(data) - SPEC_KEYS, key=str)]
    problems += [f"missing key {k!r}" for k in SPEC_REQUIRED if k not in data]
    if data.get("schema", SPEC_SCHEMA) != SPEC_SCHEMA:
        problems.append(f"schema must be {SPEC_SCHEMA!r}, got {data.get('schema')!r}")
    if problems:
        raise Fail(f"{path}: " + "; ".join(problems))
    spec = {k: data.get(k, DEFAULTS.get(k)) for k in SPEC_KEYS}
    spec["template"] = _rel(spec["template"], "template")

    pkgs = spec["packages"]
    if not isinstance(pkgs, list) or not pkgs:
        raise Fail(f"{path}: packages must be a nonempty list")
    seen: set[str] = set()
    out = []
    for i, p in enumerate(pkgs):
        where = f"packages[{i}]"
        if not isinstance(p, dict):
            raise Fail(f"{path}: {where} must be a mapping")
        bad = sorted(set(p) - PACKAGE_KEYS) + [f"missing {k}" for k in sorted(PACKAGE_KEYS - set(p) - {"dir"})]
        if bad:
            raise Fail(f"{path}: {where}: " + ", ".join(bad))
        key = p["key"]
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise Fail(f"{path}: {where}.key must match {KEY_RE.pattern}, got {key!r}")
        if key in seen:
            raise Fail(f"{path}: {where}.key {key!r} is used twice")
        seen.add(key)
        umbrella = _rel(p["umbrella"], f"{where}.umbrella")
        if "/" in umbrella or not umbrella.endswith(".lean"):
            raise Fail(f"{path}: {where}.umbrella must be a .lean file in the package directory, got {umbrella!r}")
        out.append({"key": key, "dir": _rel(p.get("dir", ".") or ".", f"{where}.dir"), "umbrella": umbrella,
                    "list": _name(p["list"], f"{where}.list")})
    spec["packages"] = out

    for k in ("script", "source", "sums", "pilot_list"):
        spec[k] = _name(spec[k], k)
    if spec["all_list"] is not None:
        spec["all_list"] = _name(spec["all_list"], "all_list")

    counts = spec["counts"] or []
    if not isinstance(counts, list):
        raise Fail(f"{path}: counts must be a list")
    spec["counts"] = []
    for i, c in enumerate(counts):
        where = f"counts[{i}]"
        if not isinstance(c, dict) or set(c) != COUNT_KEYS:
            raise Fail(f"{path}: {where} must have exactly the keys {', '.join(sorted(COUNT_KEYS))}")
        if c["package"] not in seen:
            raise Fail(f"{path}: {where}.package {c['package']!r} is not a package key")
        try:
            rx = re.compile(str(c["pattern"]))
        except re.error as e:
            raise Fail(f"{path}: {where}.pattern does not compile: {e}") from None
        if rx.groups != 1:
            raise Fail(f"{path}: {where}.pattern must have exactly one group (the count), it has {rx.groups}")
        spec["counts"].append({"var": _var(c["var"], f"{where}.var"), "package": c["package"], "rx": rx,
                               "pattern": str(c["pattern"])})

    deps = spec["deps"] or {}
    if not isinstance(deps, dict):
        raise Fail(f"{path}: deps must be a mapping VAR: lake-manifest package name")
    spec["deps"] = {}
    for var, name in deps.items():
        if not isinstance(name, str) or not name.strip():
            raise Fail(f"{path}: deps.{var} must name a lake-manifest.json package")
        spec["deps"][_var(var, f"deps.{var}")] = name

    files = spec["files"] or []
    if not isinstance(files, list):
        raise Fail(f"{path}: files must be a list of paths relative to the spec")
    spec["files"] = [_rel(f, "files[]") for f in files]

    names = ([spec["source"]] + [p["list"] for p in out] + ([spec["all_list"]] if spec["all_list"] else [])
             + [spec["pilot_list"]] + [pathlib.PurePosixPath(f).name for f in spec["files"]]
             + [spec["script"], spec["sums"]])
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise Fail(f"{path}: two bundle files would have the same name: {', '.join(dup)}")
    vars_ = [c["var"] for c in spec["counts"]] + list(spec["deps"])
    clash = sorted({v for v in vars_ if vars_.count(v) > 1 or v in BUILTIN_VARS})
    if clash:
        raise Fail(f"{path}: variable name(s) used twice or reserved: {', '.join(clash)}")
    spec["path"] = path
    return spec


def _name(value, what: str) -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise Fail(f"{what} must be a plain file name ({NAME_RE.pattern}), got {value!r}")
    return value


def _var(value, what: str) -> str:
    if not isinstance(value, str) or not VAR_RE.fullmatch(value):
        raise Fail(f"{what} must be an upper-case variable name ({VAR_RE.pattern}), got {value!r}")
    return value


# --------------------------------------------------------------------------
# git at the commit

def _git_out(repo: pathlib.Path, *args: str) -> str:
    p = core.git(repo, *args)
    if p.returncode != 0:
        raise Fail(f"git {' '.join(args)} in {repo} failed: {(p.stderr or '').strip()}")
    return p.stdout


def resolve_commit(repo: pathlib.Path, sha: str) -> str:
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if p.returncode != 0:
        raise Fail(f"commit {sha[:12]} does not exist in {repo}")
    return p.stdout.strip()


def tree_of(repo: pathlib.Path, commit: str) -> str:
    return _git_out(repo, "rev-parse", f"{commit}:./").strip()


def tree_entries(repo: pathlib.Path, commit: str) -> dict[str, tuple[str, str]]:
    """{path: (mode, object)} of every blob under the package directory at `commit`. --full-tree: run in a
    subdirectory, ls-tree otherwise lists only the paths under that subdirectory, even of a subtree."""
    out = {}
    for line in _git_out(repo, "ls-tree", "--full-tree", "-r", "-z", f"{commit}:./").split("\0"):
        if not line:
            continue
        meta, rel = line.split("\t", 1)
        mode, _kind, obj = meta.split()
        out[rel] = (mode, obj)
    return out


def source_archive(repo: pathlib.Path, commit: str) -> bytes:
    """git archive of the package directory at `commit`, every entry stamped with the commit time, gzipped
    with no name and time 0: the same commit gives the same bytes (a bare tree archive is stamped with the
    current time and never repeats). Run from the repository's top level: in a subdirectory, git archive
    keeps only the paths under it, which a subtree of that subdirectory does not have (an empty archive)."""
    ct = _git_out(repo, "log", "-1", "--format=%ct", commit).strip()
    top = _git_out(repo, "rev-parse", "--show-toplevel").strip()
    prefix = _git_out(repo, "rev-parse", "--show-prefix").strip()
    p = subprocess.run(["git", "-C", top, "archive", "--format=tar", f"--mtime=@{ct}", f"{commit}:{prefix}"],
                       capture_output=True, env=core.scrubbed_env())
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise Fail(f"git archive {commit[:12]} failed in {repo}: {err} (seal-bundle needs git >= 2.43 for "
                   f"--mtime)" if "mtime" in err else f"git archive {commit[:12]} failed in {repo}: {err}")
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as t:   # the archive holds exactly the tree's files
        members = {m.name for m in t.getmembers() if not m.isdir()}
    expected = set(tree_entries(repo, commit))
    if members != expected:
        raise Fail(f"git archive {commit[:12]} in {repo} does not hold the package tree "
                   f"({len(members)} entries, the tree has {len(expected)}); nothing was written")
    return gzip.compress(p.stdout, compresslevel=9, mtime=0)


# --------------------------------------------------------------------------
# receipts

def load_receipt(pkgdir: pathlib.Path, path: pathlib.Path, key: str) -> dict:
    """A lower-tier receipt and the checks that hold at its own commit: the schema and fields, a full commit
    that exists, the toolchain and Mathlib committed there, sorry_count 0, the default axioms. Not the gate:
    a seal re-checks a commit after the fact, so the receipt need not bind HEAD."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise Fail(f"--receipt {key}={path}: cannot read it: {e.strerror or e}") from None
    except ValueError as e:
        raise Fail(f"--receipt {key}={path}: not JSON: {e}") from None
    if not isinstance(data, dict):
        raise Fail(f"--receipt {key}={path}: not a receipt object")
    label = f"--receipt {key}={path}"
    try:
        gate_mod.check_lower_receipt_schema(data, newer_only=True)
        missing = [f for f in core.RECEIPT_FIELDS if f not in data]
        if missing:
            raise Fail(f"missing field(s) {', '.join(missing)}")
        gate_mod.check_lower_receipt_schema(data)
        commit = str(data["commit"])
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise Fail(f"commit is not a full lowercase 40-hex sha: {commit!r}")
        resolve_commit(pkgdir, commit)
        gate_mod.check_toolchain_at(pkgdir, commit, data["lean_toolchain"], "toolchain mismatch")
        committed = gate_mod.committed_mathlib(pkgdir, commit)
        rev = str(data["mathlib_rev"]).strip()
        if committed is not None and not gate_mod._same_rev(rev, committed):
            raise Fail(f"mathlib_rev {rev!r} is not the mathlib committed at {commit[:12]} ({committed!r})")
        sc = data["sorry_count"]
        if isinstance(sc, bool) or sc != 0:
            raise Fail(f"sorry_count must be 0, got {sc!r}")
        axioms = data["axioms"]
        if not isinstance(axioms, list) or not all(isinstance(a, str) for a in axioms):
            raise Fail("axioms must be a list of strings")
        extra = sorted(set(axioms) - set(core.DEFAULT_AXIOMS))
        if extra:
            raise Fail(f"axioms beyond {', '.join(core.DEFAULT_AXIOMS)}: {', '.join(extra)}")
    except Fail as e:
        raise Fail(f"{label}: {e}") from None
    return data


def parse_receipt_args(values: list[str] | None, keys: list[str]) -> dict[str, pathlib.Path]:
    out: dict[str, pathlib.Path] = {}
    for v in values or []:
        key, sep, path = v.partition("=")
        if not sep or not path:
            raise UsageError(f"--receipt takes KEY=PATH, got {v!r}")
        if key not in keys:
            raise UsageError(f"--receipt {key}: not a package key of the spec ({', '.join(keys)})")
        if key in out:
            raise UsageError(f"--receipt {key} given twice")
        out[key] = pathlib.Path(path)
    missing = [k for k in keys if k not in out]
    if missing:
        raise UsageError("--receipt KEY=PATH is required for every package of the spec; missing: "
                         + ", ".join(missing))
    return out


# --------------------------------------------------------------------------
# derived facts

def modules_of(pkgdir: pathlib.Path, commit: str, umbrella: str) -> list[str]:
    """The umbrella's imports at `commit`, which must equal the modules of the .lean files under the
    umbrella's directory (both sorted); one problem per difference."""
    blob = core.git_show_bytes(pkgdir, umbrella, commit)
    if blob is None:
        raise Fail(f"{pkgdir}: {umbrella} is not committed at {commit[:12]}")
    imports = [m.group(1) for ln in blob.decode("utf-8", "replace").splitlines() if (m := IMPORT_RE.match(ln))]
    stem = umbrella[:-len(".lean")]
    p = core.git(pkgdir, "ls-tree", "--full-tree", "-r", "-z", "--name-only", f"{commit}:./{stem}")
    files = sorted(f"{stem}." + f[:-len(".lean")].replace("/", ".")
                   for f in (p.stdout.split("\0") if p.returncode == 0 else []) if f.endswith(".lean"))
    problems = []
    bad = [m for m in imports if not MODULE_RE.fullmatch(m)]
    if bad:
        problems.append("unexpected import name(s): " + ", ".join(bad))
    dup = sorted({m for m in imports if imports.count(m) > 1})
    if dup:
        problems.append("imported twice: " + ", ".join(dup))
    only_imp = sorted(set(imports) - set(files))
    only_files = sorted(set(files) - set(imports))
    if only_imp:
        problems.append(f"imported by {umbrella} but no {stem}/ file: " + ", ".join(only_imp))
    if only_files:
        problems.append(f"a {stem}/ file {umbrella} does not import: " + ", ".join(only_files))
    if not files and not problems:
        problems.append(f"no .lean file under {stem}/")
    if problems:
        raise Fail(f"{pkgdir} at {commit[:12]}: the umbrella and the files differ: " + "; ".join(problems))
    return files


def count_from_log(pkgdir: pathlib.Path, receipt: dict, receipt_path: pathlib.Path, c: dict) -> int:
    rel = os.path.relpath(receipt_path.resolve(), pkgdir.resolve())
    text = gate_mod.sealed_log_text(pkgdir, receipt, rel)
    if text is None:
        raise Fail(f"counts {c['var']}: no verify log of the {c['package']} receipt hashes to its report_sha256 "
                   f"({gate_mod.report_log_name(receipt)} beside it or in {RECEIPTS_DIR}/)")
    values = sorted({m.group(1) for m in c["rx"].finditer(text)})
    if len(values) != 1:
        found = ", ".join(values) if values else "no match"
        raise Fail(f"counts {c['var']}: the pattern {c['pattern']!r} must give one value in the {c['package']} "
                   f"receipt's log; it gives {found}")
    if not values[0].isdigit():
        raise Fail(f"counts {c['var']}: the pattern matched {values[0]!r}, not a number")
    return int(values[0])


def manifest_revs(root: pathlib.Path, commit: str, deps: dict[str, str]) -> dict[str, str]:
    if not deps:
        return {}
    blob = core.git_show_bytes(root, "lake-manifest.json", commit)
    if blob is None:
        raise Fail(f"deps: no lake-manifest.json committed at {commit[:12]} in {root}")
    try:
        pkgs = {p.get("name"): p.get("rev") for p in json.loads(blob).get("packages") or [] if isinstance(p, dict)}
    except (ValueError, AttributeError):
        raise Fail(f"deps: lake-manifest.json at {commit[:12]} is not a manifest") from None
    out, missing = {}, []
    for var, name in deps.items():
        rev = pkgs.get(name)
        if not isinstance(rev, str) or not re.fullmatch(r"[0-9a-f]{40}", rev):
            missing.append(f"{var} ({name})")
        else:
            out[var] = rev
    if missing:
        raise Fail(f"deps: lake-manifest.json at {commit[:12]} has no 40-hex rev for " + ", ".join(missing))
    return out


def read_pilot(path: pathlib.Path, all_modules: list[str]) -> list[str]:
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    except OSError as e:
        raise Fail(f"--pilot {path}: cannot read it: {e.strerror or e}") from None
    pilot = [ln for ln in lines if ln]
    if not pilot:
        raise Fail(f"--pilot {path}: no module listed")
    dup = sorted({m for m in pilot if pilot.count(m) > 1})
    unknown = [m for m in pilot if m not in all_modules]
    if dup or unknown:
        raise Fail(f"--pilot {path}: " + "; ".join(
            ([f"listed twice: {', '.join(dup)}"] if dup else [])
            + ([f"not a module of the bundle: {', '.join(unknown)}"] if unknown else [])))
    return pilot


def render(template: str, values: dict[str, str], hashes: dict[str, str], commits: dict[str, str]) -> str:
    """Fill every @@NAME@@ / @@NAME:ARG@@; an unknown placeholder, or a template without @@COMMIT@@ and
    @@TREE@@ (the job would bind nothing), is refused, all problems at once."""
    problems = []
    for need in ("COMMIT", "TREE"):
        if f"@@{need}@@" not in template:
            problems.append(f"@@{need}@@ is missing (the job must bind the commit and its tree)")

    def fill(m: re.Match) -> str:
        name, arg = m.group(1), m.group(2)
        if arg is None and name in values:
            return values[name]
        if name == "SHA256" and arg in hashes:
            return hashes[arg]
        if name in ("COMMIT", "COMMIT_SHORT") and arg in commits:
            return commits[arg] if name == "COMMIT" else commits[arg][:COMMIT_SHORT]
        problems.append(m.group(0))
        return m.group(0)

    out = PLACEHOLDER_RE.sub(fill, template)
    if problems:
        raise Fail("template: unknown or unfilled placeholder(s): " + ", ".join(dict.fromkeys(problems))
                   + " (known: @@COMMIT@@ @@COMMIT_SHORT@@ @@TREE@@ @@TREE_SHORT@@ @@NOTE@@, @@COMMIT:KEY@@, "
                   "@@COMMIT_SHORT:KEY@@, @@SHA256:FILE@@ and the spec's counts and deps variables)")
    return out


# --------------------------------------------------------------------------
# the bundle

def build(spec: dict, root: pathlib.Path, receipts: dict[str, pathlib.Path], pilot_path: pathlib.Path | None,
          note: str, check_dir: pathlib.Path | None = None) -> dict:
    """Every file of the bundle in order ({name: bytes}), with the facts behind it. With check_dir, a pilot
    list and the source archive's recorded hash may come from that bundle (reported as taken from it)."""
    notes: list[str] = []
    spec_dir = spec["path"].parent
    pkgs = []
    for p in spec["packages"]:
        pkgdir = root / p["dir"]
        if not pkgdir.is_dir():
            raise Fail(f"packages {p['key']}: {pkgdir} is not a directory")
        rec = load_receipt(pkgdir, receipts[p["key"]], p["key"])
        pkgs.append({**p, "pkgdir": pkgdir, "receipt": rec, "receipt_path": receipts[p["key"]],
                     "commit": resolve_commit(pkgdir, rec["commit"])})
    primary = pkgs[0]
    commit = primary["commit"]
    tree = tree_of(root, commit)

    for p in pkgs[1:]:   # each other package's receipt must have verified the tree bundled here
        if p["commit"] == commit:
            continue
        a = {k: v for k, v in tree_entries(p["pkgdir"], commit).items() if RECEIPTS_DIR not in k.split("/")}
        b = {k: v for k, v in tree_entries(p["pkgdir"], p["commit"]).items() if RECEIPTS_DIR not in k.split("/")}
        drift = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if drift:
            shown = ", ".join(drift[:10]) + (f" (+{len(drift) - 10} more)" if len(drift) > 10 else "")
            raise Fail(f"packages {p['key']}: its receipt verified {p['commit'][:12]}, but {p['dir']}/ at "
                       f"{commit[:12]} differs outside {RECEIPTS_DIR}/: {shown}")
        notes.append(f"{p['key']}: the receipt's commit {p['commit'][:12]} has the bundled tree of {p['dir']}/ "
                     f"(apart from {RECEIPTS_DIR}/)")

    for p in pkgs:
        p["modules"] = modules_of(p["pkgdir"], commit, p["umbrella"])
    all_modules = [m for p in pkgs for m in p["modules"]]

    if pilot_path is not None:
        pilot = read_pilot(pilot_path, all_modules)
    elif check_dir is not None and (check_dir / spec["pilot_list"]).is_file():
        pilot = read_pilot(check_dir / spec["pilot_list"], all_modules)
        notes.append(f"{spec['pilot_list']}: taken from the checked bundle (a pilot list is a choice)")
    else:
        raise UsageError("--pilot FILE is required: the modules the pilot run replays, one per line")

    counts = {}
    for c in spec["counts"]:
        p = next(q for q in pkgs if q["key"] == c["package"])
        counts[c["var"]] = count_from_log(p["pkgdir"], p["receipt"], p["receipt_path"], c)
    deps = manifest_revs(root, commit, spec["deps"])

    files: dict[str, bytes] = {}
    files[spec["source"]] = source_archive(root, commit)
    for p in pkgs:
        files[p["list"]] = "".join(m + "\n" for m in p["modules"]).encode()
    if spec["all_list"]:
        files[spec["all_list"]] = "".join(m + "\n" for m in all_modules).encode()
    files[spec["pilot_list"]] = "".join(m + "\n" for m in pilot).encode()
    for f in spec["files"]:
        src = spec_dir / f
        try:
            files[pathlib.PurePosixPath(f).name] = src.read_bytes()
        except OSError as e:
            raise Fail(f"files: cannot read {src}: {e.strerror or e}") from None

    hashes = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    source_taken = False
    if check_dir is not None:
        recorded = _recorded_sums(check_dir / spec["sums"]).get(spec["source"])
        if recorded is not None and recorded != hashes[spec["source"]]:
            hashes[spec["source"]] = recorded
            source_taken = True
            notes.append(f"{spec['source']}: its hash is taken from the checked bundle's {spec['sums']} (an "
                         f"archive made elsewhere is not rebuilt byte for byte; the script binds its tree)")

    values = {"COMMIT": commit, "COMMIT_SHORT": commit[:COMMIT_SHORT], "TREE": tree, "TREE_SHORT": tree[:TREE_SHORT],
              "NOTE": note, **{k: str(v) for k, v in counts.items()}, **deps}
    try:
        template = (spec_dir / spec["template"]).read_text(encoding="utf-8")
    except OSError as e:
        raise Fail(f"template: cannot read {spec_dir / spec['template']}: {e.strerror or e}") from None
    script = render(template, values, hashes, {p["key"]: p["commit"] for p in pkgs})
    files[spec["script"]] = script.encode()
    hashes[spec["script"]] = hashlib.sha256(files[spec["script"]]).hexdigest()
    files[spec["sums"]] = "".join(f"{hashes[n]}  {n}\n" for n in files).encode()

    return {"files": files, "hashes": hashes, "commit": commit, "tree": tree, "counts": counts, "deps": deps,
            "pilot": pilot, "notes": notes, "source_taken": source_taken,
            "packages": [{"key": p["key"], "dir": p["dir"], "commit": p["commit"], "receipt": str(p["receipt_path"]),
                          "modules": len(p["modules"])} for p in pkgs]}


def _recorded_sums(path: pathlib.Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for ln in text.splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", ln.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def compare(bundle: dict, spec: dict, check_dir: pathlib.Path) -> tuple[list[dict], list[str]]:
    """(differences, notes) of check_dir against the derived bundle. The source archive is compared by its
    recorded hash only when check_dir has none to take; files check_dir has beyond the bundle are notes."""
    diffs, notes = [], []
    source = spec["source"]
    for name, data in bundle["files"].items():
        f = check_dir / name
        if name == source:
            if not f.is_file():
                notes.append(f"{source}: not in the checked bundle (not compared; the script binds its tree)")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != bundle["hashes"][source] and not bundle["source_taken"]:
                notes.append(f"{source}: differs byte for byte (not a difference: the script binds its tree)")
            continue
        if not f.is_file():
            diffs.append({"file": name, "kind": "missing", "diff": []})
            continue
        have = f.read_bytes()
        if have != data:
            d = list(difflib.unified_diff(have.decode("utf-8", "replace").splitlines(),
                                          data.decode("utf-8", "replace").splitlines(),
                                          f"checked/{name}", f"derived/{name}", n=0, lineterm=""))
            diffs.append({"file": name, "kind": "differs", "diff": d[:DIFF_LINES]})
    extra = sorted(p.name for p in check_dir.iterdir() if p.is_file() and p.name not in bundle["files"])
    if extra:
        notes.append("not produced by seal-bundle (not compared): " + ", ".join(extra))
    return diffs, notes


def write_bundle(bundle: dict, out: pathlib.Path) -> None:
    if out.exists():
        raise Fail(f"--out {out} already exists; seal-bundle writes a new directory and never overwrites one")
    out.mkdir(parents=True)
    for name, data in bundle["files"].items():
        (out / name).write_bytes(data)


# --------------------------------------------------------------------------
# the command

def cmd_seal_bundle(args) -> int:
    note = args.note or ""
    if len(note) > NOTE_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in note) or "@@" in note:
        raise UsageError(f"--note must be one line of at most {NOTE_MAX} characters, without control characters "
                         f"or @@")
    spec = load_spec(pathlib.Path(args.spec))
    root = pathlib.Path(args.repo)
    if core.git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise Fail(f"--repo {root}: not inside a git work tree")
    receipts = parse_receipt_args(args.receipt, [p["key"] for p in spec["packages"]])
    check_dir = pathlib.Path(args.check) if args.check else None
    if check_dir is not None and not check_dir.is_dir():
        raise Fail(f"--check {check_dir}: not a directory")
    pilot = pathlib.Path(args.pilot) if args.pilot else None
    bundle = build(spec, root, receipts, pilot, note, check_dir)

    payload = {"ok": True, "mode": "check" if check_dir else "write", "commit": bundle["commit"],
               "tree": bundle["tree"], "packages": bundle["packages"], "counts": bundle["counts"],
               "deps": bundle["deps"], "pilot": bundle["pilot"],
               "files": [{"name": n, "sha256": h} for n, h in bundle["hashes"].items() if n != spec["sums"]],
               "notes": list(bundle["notes"]), "differences": []}
    head = (f"commit {bundle['commit'][:12]}, tree {bundle['tree'][:12]}, "
            + ", ".join(f"{p['key']} {p['modules']} module(s)" for p in bundle["packages"])
            + "".join(f", {k}={v}" for k, v in bundle["counts"].items()))
    if check_dir is not None:
        diffs, more = compare(bundle, spec, check_dir)
        payload["notes"] += more
        payload["differences"] = diffs
        payload["ok"] = not diffs
        compared = len(bundle["files"]) - 1
        verdict = (f"seal-bundle --check {check_dir}: " + ("PASS" if not diffs else "FAIL")
                   + f", {compared - len(diffs)}/{compared} file(s) equal ({head})")
        lines = [verdict]
        for d in diffs:
            lines.append(f"  {d['kind']}: {d['file']}")
            lines += [f"    {ln}" for ln in d["diff"]]
        lines += [f"  note: {n}" for n in payload["notes"]]
        if diffs:
            lines.append(verdict)
    else:
        out = pathlib.Path(args.out)
        write_bundle(bundle, out)
        payload["out"] = str(out)
        lines = [f"seal-bundle: wrote {out} ({len(bundle['files'])} files; {head})"]
        lines += [f"  {h}  {n}" for n, h in bundle["hashes"].items() if n != spec["sums"]]
        lines += [f"  note: {n}" for n in payload["notes"]]
        lines.append(f"  submit from a copy of {out} on the cluster; nothing was written to the registry")
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return 0 if payload["ok"] else 1


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser(
        "seal-bundle",
        help="render a hash-bound seal job bundle (job script, module lists, source archive, INPUT-SHA256SUMS) "
             "from lower-tier receipts and the package's template; --check DIR re-derives an existing bundle",
        description="Every value a hand-assembled seal edits is derived: the commit from the first package's "
                    "receipt and its tree, each other package's receipt checked against the bundled tree (apart "
                    "from .lean-receipts/), module lists from the umbrella imports (which must equal the .lean "
                    "files), counts from each receipt's sealed verify log, dependency revisions from "
                    "lake-manifest.json, and a reproducible source archive. The template's @@NAME@@ placeholders "
                    "are filled; an unknown one is refused. Writes only --out; never a receipt or the registry.")
    s.add_argument("--spec", required=True, metavar="PATH",
                   help="the package's seal-bundle/1 spec (YAML); its template and files are relative to it")
    s.add_argument("--receipt", action="append", metavar="KEY=PATH",
                   help="the lower-tier receipt of each package in the spec (repeat per package); the first "
                        "package's commit is the one bundled")
    s.add_argument("--repo", default=".", metavar="DIR",
                   help="the package directory archived as the source (default: .); package dirs are relative to it")
    s.add_argument("--pilot", default=None, metavar="FILE",
                   help="the modules the pilot run replays, one per line (required with --out; with --check the "
                        "bundle's own list is used when omitted)")
    s.add_argument("--note", default="", metavar="TEXT", help="one line filled into @@NOTE@@ (what this seal adds)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--out", metavar="DIR", help="write the bundle into DIR, which must not exist")
    g.add_argument("--check", metavar="DIR",
                   help="re-derive the bundle and compare DIR with it, file by file (exit 1 on a difference)")
    add_json(s)
    s.set_defaults(fn=cmd_seal_bundle)
