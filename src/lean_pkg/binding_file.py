"""The committed docs-only opt-in, `lean-pkg-binding.yaml` (proposal 0002, #319).

A package opts in to the docs-only descendant rule (docs/receipts.md) with a small file committed in its
own directory:

    schema: lean-pkg-binding/1
    docs_descendants: true
    build_inputs:            # optional: files the build reads that Lake does not declare
      - data/*.csv

Every command that binds a receipt to its commit X reads the file at X and nowhere else (read_at): never the
working tree, HEAD, the published ref or a commit between X and HEAD, so a later commit cannot grant itself
the rule. The file itself is always a build input under the docs-only rule (core.DOCS_ALWAYS_REFUSED), so a
reader at HEAD sees either no file or the very bytes that bound X.

resolve_opt_in applies the registry precedence of the proposal: a registry `docs_descendants: false` vetoes,
a registry `true` with no file still opts in (the deprecated route), and once the file exists at X it
decides. The parse is strict and fails closed: a file that does not validate does not opt in, and the output
names the file and the key.
"""

from __future__ import annotations

import dataclasses
import functools
import pathlib
import re
import subprocess

import yaml

from .core import scrubbed_env

FILE = "lean-pkg-binding.yaml"
SCHEMA = "lean-pkg-binding/1"
MAX_BYTES = 64 * 1024
KEYS = ("schema", "docs_descendants", "build_inputs")
_SCHEMA_RE = re.compile(r"lean-pkg-binding/([0-9]+)")
_STR_TAG = "tag:yaml.org,2002:str"
_HEX40 = re.compile(r"[0-9a-f]{40}")


@dataclasses.dataclass(frozen=True)
class BindingRead:
    """The file at one commit. state: "absent", "valid" or "invalid"; doc: the parsed keys when valid."""
    state: str
    doc: dict | None = None
    problems: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class OptIn:
    """The opt-in that binds evidence bound to `commit`. rule: "docs-only" or "evidence-only"; source:
    "committed" (the file at X), "registry" (the record's docs_descendants: true, no file at X), "referee"
    (receipt check --docs-descendants) or None; file_state: "absent", "true", "false" or "invalid";
    build_inputs: the globs applied (the file's at X, then the registry record's); notes: what the output
    says about a file or a record that did not decide the rule."""
    rule: str
    source: str | None
    commit: str
    path: str | None = None
    file_state: str = "absent"
    build_inputs: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """Where the opt-in came from, for the admission line and its refusal."""
        if self.source == "committed":
            return f"opted in at {self.commit[:12]} by {FILE}"
        if self.source == "registry":
            return "opted in by the registry record"
        return "opted in"   # the referee's: receipt check adds "(accepted with --docs-descendants)"

    def view(self) -> dict:
        """The additive `opt_in` of the gate/regate/comparator events and --json."""
        return {"source": self.source, "commit": self.commit, "path": self.path}


# --------------------------------------------------------------------------
# parse: strict, node level (YAML 1.1 would read `yes`/`on` as true)

def _events_problem(text: str) -> str | None:
    """Anchors, aliases and explicit tags are refused: the file grants a rule, so what it says must be what
    it reads as."""
    for ev in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(ev, yaml.AliasEvent):
            return f"alias *{ev.anchor} is not allowed"
        if getattr(ev, "anchor", None):
            return f"anchor &{ev.anchor} is not allowed"
        if isinstance(ev, (yaml.ScalarEvent, yaml.CollectionStartEvent)) and ev.tag is not None:
            return f"explicit tag {ev.tag} is not allowed"
    return None


def _scalar_value(node):
    """A sequence item as build_inputs_errors expects it: the string of a str scalar, else a non-string."""
    if isinstance(node, yaml.ScalarNode):
        if node.tag == _STR_TAG:
            return node.value
        return yaml.safe_load(node.value) if node.value else None
    return [] if isinstance(node, yaml.SequenceNode) else {}


def parse(raw: bytes, where: str = FILE) -> tuple[dict | None, list[str]]:
    """(doc, problems) of the bytes of a lean-pkg-binding.yaml. doc is {schema, docs_descendants (bool),
    build_inputs (list)} when there are no problems, else None. Each problem starts with `where`."""
    from .audit import build_inputs_errors   # audit imports gate, which imports this module
    if len(raw) > MAX_BYTES:
        return None, [f"{where}: {len(raw)} bytes, larger than the {MAX_BYTES}-byte limit"]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        return None, [f"{where}: not valid UTF-8 (byte {e.start})"]
    try:
        problem = _events_problem(text)
        if problem:
            return None, [f"{where}: {problem}"]
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as e:
        return None, [f"{where}: not valid YAML: {' '.join(str(e).split())}"]
    if root is None:
        return None, [f"{where}: empty; it needs schema: {SCHEMA} and docs_descendants"]
    if not isinstance(root, yaml.MappingNode):
        return None, [f"{where}: top level is not a mapping"]
    problems: list[str] = []
    nodes: dict[str, yaml.Node] = {}
    for k, v in root.value:
        if not isinstance(k, yaml.ScalarNode) or k.tag != _STR_TAG:
            problems.append(f"{where}: a key must be a plain string, got {getattr(k, 'value', k)!r}")
            continue
        if k.value in nodes:
            problems.append(f"{where}: {k.value}: repeated key")
            continue
        if k.value not in KEYS:
            problems.append(f"{where}: unknown key {k.value!r} (keys: {', '.join(KEYS)})")
            continue
        nodes[k.value] = v
    sch = nodes.get("schema")
    if sch is None:
        problems.append(f"{where}: schema: missing; it must be {SCHEMA}")
    elif not isinstance(sch, yaml.ScalarNode) or sch.tag != _STR_TAG or sch.value != SCHEMA:
        val = getattr(sch, "value", None)
        m = _SCHEMA_RE.fullmatch(val) if isinstance(val, str) else None
        if m and int(m.group(1)) > 1:
            problems.append(f"{where}: schema: {val} is newer than this lean-pkg reads ({SCHEMA}); "
                            f"upgrade lean-pkg")
        else:
            problems.append(f"{where}: schema: must be {SCHEMA}, got {val!r}")
    dd = nodes.get("docs_descendants")
    value = None
    if dd is None:
        problems.append(f"{where}: docs_descendants: missing; it must be true or false")
    elif not (isinstance(dd, yaml.ScalarNode) and dd.style is None and dd.value in ("true", "false")):
        shown = dd.value if isinstance(dd, yaml.ScalarNode) else type(dd).__name__
        quoted = isinstance(dd, yaml.ScalarNode) and dd.style is not None
        problems.append(f"{where}: docs_descendants: must be the literal true or false"
                        + (" (unquoted)" if quoted else "") + f", got {shown!r}")
    else:
        value = dd.value == "true"
    globs: list = []
    bi = nodes.get("build_inputs")
    if bi is not None:
        if not isinstance(bi, yaml.SequenceNode):
            problems.append(f"{where}: build_inputs: must be a list of globs")
        else:
            globs = [_scalar_value(n) for n in bi.value]
            problems += [f"{where}: {e.lstrip('.')}" for e in build_inputs_errors(globs, "")]
    if problems:
        return None, problems
    return {"schema": SCHEMA, "docs_descendants": value, "build_inputs": list(globs)}, []


# --------------------------------------------------------------------------
# read_at: the file at the bound commit, from that commit's tree only

def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=scrubbed_env())


@functools.lru_cache(maxsize=256)
def _location(repo: str) -> tuple[str, str] | None:
    p = _git(pathlib.Path(repo), "rev-parse", "--show-toplevel", "--show-prefix")
    if p.returncode != 0:
        return None
    lines = p.stdout.decode("utf-8", "replace").splitlines()
    return (lines[0], lines[1] if len(lines) > 1 else "") if lines else None


@functools.lru_cache(maxsize=1024)
def _read(top: str, prefix: str, commit: str) -> BindingRead:
    where = f"{FILE} at {commit[:12]}"
    root = pathlib.Path(top)
    path = f"{prefix}{FILE}"
    # --full-tree from the toplevel: the path is exactly <prefix>/lean-pkg-binding.yaml, nothing else (rule 5)
    p = _git(root, "ls-tree", "-l", "-z", "--full-tree", commit, "--", path)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        return BindingRead("invalid", problems=(f"{where}: cannot read the tree"
                                                + (f" ({err[0]})" if err else ""),))
    recs = [r for r in p.stdout.decode("utf-8", "replace").split("\0") if r]
    rec = next((r for r in recs if r.partition("\t")[2] == path), None)
    if rec is None:
        return BindingRead("absent")
    mode, kind, sha, size = rec.partition("\t")[0].split()
    if mode not in ("100644", "100755"):
        what = {"120000": "a symlink", "160000": "a gitlink", "040000": "a directory"}.get(mode, f"mode {mode}")
        return BindingRead("invalid", problems=(f"{where}: {what}, not a regular file",))
    if size.isdigit() and int(size) > MAX_BYTES:
        return BindingRead("invalid", problems=(f"{where}: {size} bytes, larger than the {MAX_BYTES}-byte limit",))
    b = _git(root, "cat-file", "blob", sha)   # the bytes of the very entry whose mode was checked
    if b.returncode != 0:
        return BindingRead("invalid", problems=(f"{where}: cannot read the blob {sha[:12]}",))
    doc, problems = parse(b.stdout, where)
    if problems:
        return BindingRead("invalid", problems=tuple(problems))
    return BindingRead("valid", doc=doc)


def read_at(repo: pathlib.Path, commit: str) -> BindingRead:
    """The package's lean-pkg-binding.yaml as committed at `commit`: the entry <package dir>/FILE of that
    commit's tree and nothing else (no working tree, no index, no other commit; a copy at the repository
    root, under .lean-receipts/ or in a nested package's directory is another path). Only a regular file
    (mode 100644 or 100755) of at most 64 KiB is read. Memoized by (toplevel, prefix, commit) for a full
    commit id, which addresses the content."""
    loc = _location(str(pathlib.Path(repo).resolve()))
    if loc is None:
        return BindingRead("invalid", problems=(f"{FILE} at {commit[:12]}: {repo} is not a git checkout",))
    if _HEX40.fullmatch(commit or ""):
        return _read(loc[0], loc[1], commit)
    return _read.__wrapped__(loc[0], loc[1], commit)


# --------------------------------------------------------------------------
# resolve_opt_in: the registry precedence (proposal 0002, "Registry precedence")

def _globs(value) -> list[str]:
    return [g for g in (value or []) if isinstance(g, str) and g.strip()] if isinstance(value, list) else []


def resolve_opt_in(repo: pathlib.Path, bound: str, pkg: dict | None) -> OptIn:
    """The opt-in for evidence bound to `bound`, from the file at `bound` and the registry record `pkg`:

    | file at X | record docs_descendants | rule          | source    |
    |-----------|-------------------------|---------------|-----------|
    | absent    | absent or false         | evidence-only | None      |
    | absent    | true                    | docs-only     | registry  |
    | true      | absent or true          | docs-only     | committed |
    | true      | false                   | evidence-only | None (the record's veto is named) |
    | false/invalid | any                 | evidence-only | None (the file is named; a record true is ignored) |

    build_inputs is the file's globs (when it opts in) followed by the record's, without repeats: the
    record can only add inputs."""
    pkg = pkg or {}
    reg = pkg.get("docs_descendants")
    reg_globs = _globs(pkg.get("build_inputs"))
    b = read_at(repo, bound)
    x12 = bound[:12]
    if b.state == "absent":
        rule, source = ("docs-only", "registry") if reg is True else ("evidence-only", None)
        return OptIn(rule, source, bound, None, "absent", tuple(reg_globs))
    if b.state == "invalid":
        notes = [f"{FILE} at {x12} does not opt in: {b.problems[0].split(': ', 1)[-1]}"]
        if reg is True:
            notes.append(f"the registry record's docs_descendants: true is ignored once {FILE} exists at {x12}")
        return OptIn("evidence-only", None, bound, FILE, "invalid", tuple(reg_globs), b.problems, tuple(notes))
    doc = b.doc or {}
    if doc["docs_descendants"] is not True:
        notes = [f"{FILE} at {x12} says docs_descendants: false"]
        if reg is True:
            notes.append(f"the registry record's docs_descendants: true is ignored once {FILE} exists at {x12}")
        return OptIn("evidence-only", None, bound, FILE, "false", tuple(reg_globs), (), tuple(notes))
    union = list(dict.fromkeys(_globs(doc.get("build_inputs")) + reg_globs))
    if reg is False:
        return OptIn("evidence-only", None, bound, FILE, "true", tuple(union), (), (
            f"the registry record refuses the docs-only rule (docs_descendants: false), over {FILE} at {x12}",))
    return OptIn("docs-only", "committed", bound, FILE, "true", tuple(union))


def referee_opt_in(repo: pathlib.Path, bound: str, accepted: bool = False, evidence_only: bool = False) -> OptIn:
    """The opt-in of `receipt check`, which reads no registry (proposal 0002, stage G2; open questions 4 and 5):

    | file at X      | flags               | rule          | source    |
    |----------------|---------------------|---------------|-----------|
    | true           | none                | docs-only     | committed |
    | any            | --evidence-only     | evidence-only | None (a file at X is named as ignored) |
    | absent         | --docs-descendants  | docs-only     | referee   |
    | absent         | none                | evidence-only | None      |
    | false/invalid  | --docs-descendants  | evidence-only | None (the flag is named as overridden) |

    `accepted` is --docs-descendants, `evidence_only` --evidence-only; both at once is a usage error. The
    file is read at `bound` only (read_at), so the referee's working tree is never consulted. build_inputs
    keeps the file's globs even where the file is ignored, so the output can say what it declared."""
    if accepted and evidence_only:
        raise ValueError("--docs-descendants and --evidence-only are mutually exclusive")
    oi = resolve_opt_in(repo, bound, None)
    x12 = bound[:12]
    if evidence_only:
        notes = (f"{FILE} at {x12} opts in but is ignored: --evidence-only",) if oi.file_state == "true" else oi.notes
        return dataclasses.replace(oi, rule="evidence-only", source=None, notes=notes)
    if oi.file_state == "absent":
        return OptIn("docs-only", "referee", bound) if accepted else oi
    if oi.file_state == "true" or not accepted:
        return oi
    return dataclasses.replace(oi, notes=oi.notes + (
        f"--docs-descendants is overridden: {FILE} at {x12} is the author's committed choice",))
