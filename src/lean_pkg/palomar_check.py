# lean-pkg palomar check -- read-only Palomar readiness of one package at one commit (no network)
"""`lean-pkg palomar check NAME [--rev REV] [--config PATH] [--rules FILE] [--repo DIR] [--json]`.

Lists every gap between the package, at one commit, and the mechanical rules Palomar's verifier
applies before it builds anything. Each gap is {code, severity, paths, detail, fix}. Read-only and
offline: it reads git objects at the commit (never the package's working tree), writes nothing,
records no event and makes no network request. The one exception is the dependency scan (#404): a
dependency's checkout under .lake/packages, a local build artifact, is read when its HEAD is the
revision lake-manifest.json pins at the commit.

The commit: `--rev REV`; else the commit the recorded gate receipt binds; else HEAD.

Severity: `error` needs a change to the repository content, so a new commit (and a re-gate); `warn`
is a submission-time step (make the repository public, push the commit) or advice. Exit 0 when there
is no error (ready), 1 when there is one, 2 on a usage error.

The rules and where they come from (Palomar's floor moves, so they are dated):

- `PalomarRegistry/PalomarSubmission` at commit RULES_SOURCE["commit"], read on
  RULES_SOURCE["retrieved"]: `browser-preflight-policy.json` (the machine-readable subset of the
  preparation rules: toolchain pattern and minimum, the `.lean` source scope and line cap, the
  comparator configuration keys and standard axioms, source relationships, classification
  cardinality), `toolchains.json` (the minimum), and README.md ("Required source layout", "Lean
  source requirements": the licence file names, the formalization.yaml minimum, the Challenge limits).
- `--rules FILE` replaces the policy part with a newer `browser-preflight-policy.json` (schema 1)
  downloaded by hand; lean-pkg never fetches it.

This is a predictive, non-authoritative check, like Palomar's own browser preflight: the verifier
repeats every check, confirms headers with Lean's parser, and checks more than this (see
NOT_CHECKED).
"""

from __future__ import annotations

import json
import pathlib
import posixpath
import re
import subprocess

from . import build_scope
from . import gate as gate_mod
from . import core
from . import lean_scan
from . import statements_module as stm
from .holes import INLINE_KEYS, SIDECAR, check_definition_holes, migrate_command, parse_sidecar
from .holes import summary as signoff_summary

RULES_SOURCE = {
    "repository": "https://github.com/PalomarRegistry/PalomarSubmission",
    "commit": "d4e41c1d5b0d114c4859e6e5831dc6d3ad1d0d44",
    "committed": "2026-10-05",
    "retrieved": "2026-10-16",
    "files": ["browser-preflight-policy.json", "toolchains.json", "README.md"],
}

# The parts of browser-preflight-policy.json (schema 1) at RULES_SOURCE["commit"] this check reads.
DEFAULT_POLICY = {
    "schema_version": 1,
    "limits": {"lean_source_lines": 10000},
    "lean_sources": {"module_required": True, "excluded_directories": [".git", ".lake"],
                     "module_exempt_filenames": ["lakefile.lean"], "symlinks_allowed": False},
    "toolchain": {"pattern": r"^leanprover/lean4:v([0-9]+)\.([0-9]+)\.([0-9]+)(?:-rc([0-9]+))?$",
                  "minimum": "v4.35.0-rc2"},
    "formalization": {"source_relationship_categories": ["adapts", "background", "formalizes",
                                                         "independently-proves", "other"],
                      "classification_cardinality": {"arxiv": [1, 8], "msc2020": [0, 8]}},
    "comparator": {"required_keys": ["challenge_module", "permitted_axioms", "solution_module",
                                     "theorem_names"],
                   "allowed_keys": ["challenge_module", "definition_names", "enable_nanoda",
                                    "permitted_axioms", "solution_module", "theorem_names"],
                   "standard_axioms": ["Classical.choice", "Quot.sound", "propext"]},
}

# From README.md at RULES_SOURCE["commit"] (not in the policy file).
CHALLENGE_MAX_LINES = 1000
CHALLENGE_MAX_BYTES = 100 * 1024
PROJECT_NAME_MAX = 300
LICENSE_FILE_RE = re.compile(r"^(?:LICENSE|LICENCE|COPYING|UNLICENSE|OFL)(?:\.md|\.markdown|\.txt)?$",
                             re.IGNORECASE)
SOURCE_BASED = ("formalizes", "adapts", "independently-proves")

NOT_CHECKED = [
    "the header with Lean's parser (this check reads the first token after comments, as Palomar's "
    "own pre-installation scan does)",
    "the licence text against SPDX with licensee (only a marker for MIT, Apache-2.0, BSD)",
    "arXiv and MSC2020 codes against the taxonomies; ORCID iDs against the ORCID Registry",
    "Challenge imports against the trusted dependencies; a Challenge module root shadowed by a "
    "dependency; declaration names as canonical Lean identifiers",
    "Git LFS pointers; dependency pins and hosts in lake-manifest.json",
    "the build, the export and the comparator run itself",
]
# #404: the shadowing clause of NOT_CHECKED[3], dropped when the dependency scan ran on every dependency
_SHADOW_CLAUSE = "a Challenge module root shadowed by a dependency"
# a bare root this generic is likely to be a dependency's top-level module too (#404)
GENERIC_ROOTS = ("Challenge", "Solution", "Main")

USAGE = "usage: lean-pkg palomar check NAME [--rev REV] [--config PATH] [--rules FILE] [--repo DIR] [--json]"


# --------------------------------------------------------------------------
# rules

def load_rules(path: str | None) -> tuple[dict, dict]:
    """(policy, source view). A --rules FILE replaces the policy; its keys must be present."""
    if path is None:
        return DEFAULT_POLICY, {**RULES_SOURCE, "kind": "pinned"}
    p = pathlib.Path(path).expanduser()
    try:
        data = json.loads(p.read_text())
    except OSError as e:
        raise core.UsageError(f"--rules {path}: cannot read: {e.strerror or e}") from e
    except ValueError as e:
        raise core.UsageError(f"--rules {path}: not JSON: {e}") from e
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise core.UsageError(f"--rules {path}: not a browser-preflight-policy.json with schema_version 1")
    try:
        policy = {
            "schema_version": 1,
            "limits": {"lean_source_lines": int(data["limits"]["lean_source_lines"])},
            "lean_sources": {k: data["lean_sources"][k] for k in DEFAULT_POLICY["lean_sources"]},
            "toolchain": {k: str(data["toolchain"][k]) for k in ("pattern", "minimum")},
            "formalization": {k: data["formalization"][k] for k in DEFAULT_POLICY["formalization"]},
            "comparator": {k: list(data["comparator"][k]) for k in DEFAULT_POLICY["comparator"]},
        }
        re.compile(policy["toolchain"]["pattern"])
    except (KeyError, TypeError, ValueError, re.error) as e:
        raise core.UsageError(f"--rules {path}: missing or malformed key ({e})") from e
    return policy, {"kind": "file", "path": str(p), "retrieved": None, "commit": None}


# --------------------------------------------------------------------------
# Lean sources

_ID_CONT = re.compile(r"[\w'!?₀-₉ₐ-ₜᵢ-ᵪⱼ]|\.[^\W\d]|\.«")


def has_module_header(text: str) -> bool:
    """Does the file start with the `module` keyword, after blanks and ordinary comments only?

    The rule (README, "Lean source requirements"): ordinary comments may precede the header;
    module documentation (`/-! -/`) and doc comments (`/-- -/`) are commands, so they must follow
    it. Only space, CR and LF count as blanks (no BOM, no other Unicode space), as in Lean's header
    parser. Block comments nest. `module` must be a whole token (`moduleFoo` is not a header)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \r\n":
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i + 2)
            i = n if j < 0 else j + 1
        elif text.startswith("/-", i) and not text.startswith(("/--", "/-!"), i):
            i += 3                 # Lean reads one character past a plain opener before scanning
            depth = 1
            while depth and i < n:
                if text.startswith("/-", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("-/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                return False       # an unterminated comment: no header
        else:
            return text.startswith("module", i) and _ID_CONT.match(text, i + 6) is None
    return False


def physical_lines(text: str) -> int:
    """LF-delimited lines; an unterminated last line counts, a final newline adds none."""
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


class Tree:
    """The git tree of one commit, read from the object store (the working tree is never read)."""

    def __init__(self, repo: pathlib.Path, commit: str, claims_file: str = "claims.yaml"):
        self.repo, self.commit, self.claims_file = repo, commit, claims_file
        self._statements: tuple[str | None, list[str]] | None = None
        self._deps: dict | None = None
        self.module_hint = "MOD"          # the package's root module, for a suggested module name (#404)
        p = core.git(repo, "rev-parse", "--show-prefix")
        self.prefix = p.stdout.strip() if p.returncode == 0 else ""
        p = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", commit],
                           capture_output=True, env=core.scrubbed_env())
        if p.returncode != 0:
            raise core.Fail(f"cannot list the tree of {commit[:12]} in {repo}")
        self.entries: dict[str, tuple[str, str, str]] = {}
        for rec in p.stdout.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            self.entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, sha)

    def project(self, rel: str) -> str:
        """A package-relative path as a repository path."""
        return self.prefix + rel

    def blob(self, path: str) -> bytes | None:
        e = self.entries.get(path)
        if e is None or e[1] != "blob":
            return None
        p = subprocess.run(["git", "-C", str(self.repo), "cat-file", "blob", e[2]],
                           capture_output=True, env=core.scrubbed_env())
        return p.stdout if p.returncode == 0 else None

    def statements(self) -> tuple[str | None, list[str]]:
        """(the statements module the claims file at this commit declares, or None; its refusals), read once
        (#369)."""
        if self._statements is None:
            self._statements = stm.at_commit(self.repo, self.claims_file, self.commit)
        return self._statements

    def dependencies(self) -> dict:
        """The dependency scan (#404), read once: dependency_scan(self)."""
        if self._deps is None:
            self._deps = dependency_scan(self)
        return self._deps

    def blobs(self, paths: list[str]) -> dict[str, bytes]:
        """Many blobs in one `git cat-file --batch` (a package can have thousands of files)."""
        want = [(p, self.entries[p][2]) for p in paths if p in self.entries and self.entries[p][1] == "blob"]
        if not want:
            return {}
        p = subprocess.run(["git", "-C", str(self.repo), "cat-file", "--batch"],
                           input="".join(f"{sha}\n" for _p, sha in want).encode(),
                           capture_output=True, env=core.scrubbed_env())
        out, data, pos = {}, p.stdout, 0
        for path, _sha in want:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].split()
            size = int(header[2]) if len(header) == 3 else 0
            out[path] = data[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
        return out


def _gap(code: str, severity: str, detail: str, fix: str, paths: list[str] | None = None) -> dict:
    return {"code": code, "severity": severity, "paths": sorted(paths or []), "detail": detail, "fix": fix}


def source_gaps(tree: Tree, policy: dict, name: str = "NAME") -> tuple[list[dict], dict]:
    """Every tracked .lean file in the repository (Palomar scans the whole submitted repository,
    contained projects and unused files included), outside the excluded directories."""
    ls = policy["lean_sources"]
    excluded = set(ls["excluded_directories"])
    exempt = set(ls["module_exempt_filenames"])
    cap = policy["limits"]["lean_source_lines"]
    lean = sorted(p for p in tree.entries
                  if p.endswith(".lean") and not excluded.intersection(p.split("/")[:-1]))
    links = [p for p in lean if tree.entries[p][0] == "120000"]
    regular = [p for p in lean if tree.entries[p][0] != "120000" and tree.entries[p][1] == "blob"]
    texts = tree.blobs(regular)
    no_module, too_long, bad_utf8 = [], [], []
    for p in regular:
        try:
            text = texts.get(p, b"").decode("utf-8")
        except UnicodeDecodeError:
            bad_utf8.append(p)
            continue
        if physical_lines(text) > cap:
            too_long.append(p)
        if ls["module_required"] and p.rsplit("/", 1)[-1] not in exempt and not has_module_header(text):
            no_module.append(p)
    gaps = []
    if no_module:
        gaps.append(_gap("source.module_required", "error",
                         f"{len(no_module)} of {len(regular)} tracked .lean file(s) do not begin with a `module` "
                         f"header (ordinary comments may precede it; lakefile.lean is exempt)",
                         f"port the files to the module system (`lean-pkg palomar port-modules {name}` plans the "
                         "mechanical part as a diff, `--write` applies it): `module` first, `public import` where "
                         "the public interface needs the import, `public`/`@[expose]` for what other modules use; "
                         "rebuild, commit, re-gate", no_module))
    if too_long:
        gaps.append(_gap("source.file_too_long", "error",
                         f"{len(too_long)} .lean file(s) longer than {cap:,} lines",
                         "split them into smaller modules (or reduce generated certificates); commit, re-gate",
                         too_long))
    if links and not ls["symlinks_allowed"]:
        gaps.append(_gap("source.symlink_not_allowed", "error", f"{len(links)} .lean symbolic link(s)",
                         "commit each as a regular file; commit, re-gate", links))
    if bad_utf8:
        gaps.append(_gap("source.invalid_utf8", "error", f"{len(bad_utf8)} .lean file(s) not valid UTF-8",
                         "re-encode them as UTF-8; commit, re-gate", bad_utf8))
    return gaps, {"lean_files": len(regular) + len(links), "missing_module": len(no_module)}


def toolchain_key(text: str, pattern: str) -> tuple | None:
    m = re.match(pattern, text.strip())
    if not m:
        return None
    major, minor, patch, rc = m.groups()
    return (int(major), int(minor), int(patch)) + ((0, int(rc)) if rc is not None else (1, 0))


def toolchain_gaps(tree: Tree, policy: dict) -> tuple[list[dict], str | None]:
    """lean-toolchain in the project, else at the repository root, against the minimum."""
    path = next((p for p in (tree.project("lean-toolchain"), "lean-toolchain") if p in tree.entries), None)
    raw = tree.blob(path) if path else None
    if raw is None:
        return [_gap("toolchain.missing", "error", "no lean-toolchain in the project or at the repository root",
                     "commit a lean-toolchain")], None
    tc = raw.decode("utf-8", "replace").strip()
    tcp = policy["toolchain"]
    minimum = tcp["minimum"]
    key = toolchain_key(tc, tcp["pattern"])
    floor = toolchain_key(f"leanprover/lean4:{minimum}", tcp["pattern"])
    if key is None:
        return [_gap("toolchain.unsupported", "error",
                     f"lean-toolchain {tc!r} is not a released or rc Lean toolchain (leanprover/lean4:vX.Y.Z[-rcN])",
                     f"pin a release or rc at or above {minimum}; rebuild, commit, re-gate", [path])], tc
    if floor is not None and key < floor:
        return [_gap("toolchain.unsupported", "error",
                     f"lean-toolchain {tc} is below Palomar's minimum {minimum}",
                     f"upgrade to {minimum} or later; rebuild, commit, re-gate", [path])], tc
    return [], tc


def layout_gaps(tree: Tree) -> list[dict]:
    gaps = []
    toml, lean = tree.project("lakefile.toml"), tree.project("lakefile.lean")
    if toml not in tree.entries and lean not in tree.entries:
        gaps.append(_gap("layout.lakefile_missing", "error", "no lakefile.toml or lakefile.lean in the project",
                         "commit the Lake configuration"))
    elif toml not in tree.entries and tree.project("lake-manifest.json") not in tree.entries:
        gaps.append(_gap("layout.manifest_missing", "error", "lakefile.lean without lake-manifest.json",
                         "commit lake-manifest.json", [lean]))
    subs = [p for p, e in tree.entries.items() if e[1] == "commit"]
    if subs or ".gitmodules" in tree.entries:
        gaps.append(_gap("layout.submodule", "error", "Git submodules (Palomar rejects them in the submitted "
                         "repository)", "vendor the content or make it a pinned Lake dependency; commit, re-gate",
                         subs or [".gitmodules"]))
    return gaps


def comparator_gaps(tree: Tree, policy: dict, only: str | None) -> tuple[list[dict], list[str]]:
    configs = gate_mod.head_comparator_configs(tree.repo, tree.commit)
    if only is not None:
        if only not in configs:
            return [_gap("comparator.path_missing", "error",
                         f"--config {only} is not a comparator configuration tracked at {tree.commit[:12]} "
                         f"(tracked: {', '.join(configs) or 'none'})",
                         "commit it, or name a tracked one", [only])], configs
        configs = [only]
    if not configs:
        return [_gap("comparator.path_missing", "error", "no comparator configuration tracked "
                     "(comparator*.json or comparator/config*.json)",
                     "write one (`lean-pkg comparator config --from-claims NAME`), commit, run comparator-receipt")], []
    gaps: list[dict] = []
    for rel in configs:
        gaps += config_gaps(tree, policy, rel)
    return gaps, configs


def config_gaps(tree: Tree, policy: dict, rel: str) -> list[dict]:
    """The gaps of one comparator configuration (package-relative `rel`) at the tree's commit."""
    cp = policy["comparator"]
    required, allowed, standard = set(cp["required_keys"]), set(cp["allowed_keys"]), set(cp["standard_axioms"])
    gaps: list[dict] = []
    path = tree.project(rel)
    try:
        cfg = json.loads(tree.blob(path) or b"")
    except ValueError:
        cfg = None
    if not isinstance(cfg, dict):
        gaps.append(_gap("comparator.invalid", "error", f"{rel} is not a JSON object", "fix the file", [path]))
        return gaps
    missing = sorted(required - cfg.keys())
    if missing:
        gaps.append(_gap("comparator.missing_key", "error", f"{rel} lacks {', '.join(missing)}",
                         "add the keys; commit, re-run comparator-receipt, re-gate", [path]))
    unknown = sorted(cfg.keys() - allowed)
    if unknown:
        ours = [k for k in INLINE_KEYS if k in unknown]
        note = (f" ({' and '.join(ours)}: lean-pkg's own metadata, deprecated inside a configuration; it "
                f"belongs in {SIDECAR})" if ours else "")
        fix = (f"`{migrate_command(rel)}` moves {' and '.join(ours)} to {SIDECAR} (the configuration's "
               f"Palomar keys unchanged)" + ("; remove the other key(s)" if set(unknown) - set(ours) else "")
               + "; commit, re-run comparator-receipt (the config's bytes change), re-gate") if ours else \
            "remove them from the configuration you submit; commit, re-run comparator-receipt, re-gate"
        gaps.append(_gap("comparator.unknown_key", "error",
                         f"{rel} has key(s) Palomar refuses: {', '.join(unknown)} (allowed: "
                         f"{', '.join(sorted(allowed))}){note}", fix, [path]))
    names = cfg.get("theorem_names")
    if not isinstance(names, list) or not names or not all(isinstance(x, str) and x for x in names):
        gaps.append(_gap("comparator.invalid", "error", f"{rel}: theorem_names must be a non-empty list of names",
                         "list the theorems the configuration certifies", [path]))
    axioms = cfg.get("permitted_axioms")
    if isinstance(axioms, list) and not set(map(str, axioms)) <= standard:
        gaps.append(_gap("comparator.invalid_axioms", "error",
                         f"{rel}: permitted_axioms {sorted(set(map(str, axioms)) - standard)} outside "
                         f"{', '.join(sorted(standard))}", "permit only the standard axioms", [path]))
    ch, so = cfg.get("challenge_module"), cfg.get("solution_module")
    if ch and ch == so:
        gaps.append(_gap("comparator.invalid", "error", f"{rel}: challenge_module and solution_module are both "
                         f"{ch!r}", "use two distinct modules", [path]))
    for key, mod in (("challenge_module", ch), ("solution_module", so)):
        src = _module_source(mod)
        if src is not None and tree.project(src) not in tree.entries:
            gaps.append(_gap("comparator.path_missing", "error", f"{rel}: {key} {mod} has no source {src} "
                             f"tracked in the project", "commit it", [tree.project(src)]))
    gaps += shadow_gaps(tree, rel, path, ch, so)
    csrc = _module_source(ch)
    blob = tree.blob(tree.project(csrc)) if csrc else None
    if blob is not None and (len(blob) > CHALLENGE_MAX_BYTES
                             or physical_lines(blob.decode("utf-8", "replace")) > CHALLENGE_MAX_LINES):
        gaps.append(_gap("challenge.too_large", "error",
                         f"{csrc} exceeds the Challenge limits ({CHALLENGE_MAX_LINES:,} lines, 100 KiB)",
                         "state fewer or shorter declarations in the Challenge", [tree.project(csrc)]))
    if blob is not None:
        gaps += challenge_import_gaps(tree, rel, ch, csrc, blob)
    holes = cfg.get("definition_names") or []
    if isinstance(holes, list) and holes:
        try:
            so = check_definition_holes(cfg, rel, cfg_rel=rel, cfg_bytes=tree.blob(path) or b"",
                                        sidecar=parse_sidecar(tree.blob(tree.project(SIDECAR))))
            signed = f"; lean-pkg sign-off: {signoff_summary(so)['reviewer']} ({so['source']})"
        except ValueError as e:
            signed = f"; lean-pkg sign-off: none valid ({str(e).split(': ', 1)[-1][:160]})"
        gaps.append(_gap("comparator.definition_holes", "warn",
                         f"{rel}: {len(holes)} definition_names entr(y/ies); comparator compares them by name "
                         f"and type, never by body, so reviewers must judge the bodies{signed}",
                         "keep definition_names empty where you can; otherwise justify each in the submission",
                         [path]))
    return gaps


def challenge_import_gaps(tree: Tree, rel: str, ch: str, csrc: str, blob: bytes) -> list[dict]:
    """A warning for each module the Challenge imports beyond Mathlib's vocabulary (#369): the declared
    statements module (trusted by declaration: a reader must read it), and, more strongly, any other module.
    Never an error: Palomar's verifier is what checks Challenge imports against the trusted dependencies."""
    imports = [m for m in dict.fromkeys(lean_scan.header_imports(blob.decode("utf-8", "replace")))
               if not stm.is_vocabulary(m)]
    if not imports:
        return []
    declared, problems = tree.statements()
    path = tree.project(csrc)
    gaps = []
    for m in imports:
        mrel = lean_scan.module_file(m) or m
        if m == declared:
            refused = (f"; lean-pkg refuses the declaration at {tree.commit[:12]}: {problems[0]}"
                       + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")) if problems else ""
            gaps.append(_gap("challenge.statements_module", "warn",
                             f"{rel}: challenge_module {ch} imports {m}, the declared statements module "
                             f"({tree.claims_file} {stm.KEY}): trusted by declaration, read it{refused}",
                             f"read {mrel} with the Challenge: each hypothesis there is part of what the "
                             f"theorems state" + ("; fix the module, commit, re-gate" if problems else ""),
                             [path, tree.project(mrel)]))
        else:
            other = (f"the declared statements module is {declared}" if declared
                     else f"no statements module is declared ({tree.claims_file} {stm.KEY})")
            gaps.append(_gap("challenge.import", "warn",
                             f"{rel}: challenge_module {ch} imports {m}, which is neither Mathlib's vocabulary nor "
                             f"the declared statements module ({other}): the statement then rests on {m}, which "
                             f"no check here reads",
                             "state the Challenge in Mathlib's vocabulary and drop the import; the hypotheses of "
                             f"conditional claims go in the one declared statements module ({stm.KEY} in "
                             f"{tree.claims_file}); commit, re-gate", [path]))
    return gaps


# --------------------------------------------------------------------------
# module roots a dependency shadows (#404)

_SRC_DIR_TOML = re.compile(r'^\s*srcDir\s*=\s*"([^"\n]*)"', re.MULTILINE)
_SRC_DIR_LEAN = re.compile(r'\bsrcDir\s*:=\s*"([^"\n]*)"')


def _src_dirs(toml: str | None, lean: str | None) -> list[str]:
    """The package root and every `srcDir` a lakefile states literally (package or lean_lib, each taken
    relative to the package root; nesting a lean_lib srcDir under the package's is not modelled)."""
    found = (_SRC_DIR_TOML.findall(toml) if toml is not None else _SRC_DIR_LEAN.findall(lean or ""))
    out = ["."]
    for d in found:
        d = posixpath.normpath(d.strip() or ".")
        if not d.startswith(("/", "..")) and d not in out:
            out.append(d)
    return out


def _read(p: pathlib.Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    except OSError:
        return None


def dependency_scan(tree: Tree) -> dict:
    """The source trees of the dependencies lake-manifest.json pins at the tree's commit, where the scan can
    read them offline: {complete, reason, scanned: [(name, where, has_root)], pdir}. A git dependency is read
    from its checkout `<project>/<packagesDir>/<name>/` (the working tree's build artifact, by default
    .lake/packages) only when that checkout's HEAD is the pinned rev; a path dependency inside the repository
    from the commit's tree. `complete` is True when every dependency was read; `reason` says why not."""
    mpath = tree.project("lake-manifest.json")
    out: dict = {"complete": False, "reason": None, "scanned": [], "pdir": ".lake/packages"}
    raw = tree.blob(mpath)
    if raw is None:
        out["reason"] = f"no lake-manifest.json at {tree.commit[:12]}"
        return out
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        doc = None
    pkgs = doc.get("packages", []) if isinstance(doc, dict) else None
    if not isinstance(pkgs, list):
        out["reason"] = f"lake-manifest.json at {tree.commit[:12]} is not a manifest with a packages list"
        return out
    pdir = doc.get("packagesDir") if isinstance(doc.get("packagesDir"), str) else ".lake/packages"
    pdir = posixpath.normpath(pdir)
    if pdir.startswith(("/", "..")):
        pdir = ".lake/packages"
    out["pdir"] = pdir
    notes: list[str] = []
    for p in pkgs:
        name = p.get("name") if isinstance(p, dict) else None
        if not isinstance(name, str) or not name:
            notes.append("a package entry without a name")
            continue
        # Lake's directory name for the escaped manifest name (`«dep-x»` is checked out as dep-x)
        dname = build_scope.lake_dir_name(name)
        if dname is None or "/" in dname or "\\" in dname or dname in (".", ".."):
            notes.append(f"{name}: not a Lake package name")
            continue
        name = dname
        if p.get("type") == "path":
            d = posixpath.normpath(posixpath.join(tree.prefix or ".", str(p.get("dir") or "")))
            if d.startswith(("/", "..")) or d == ".":
                notes.append(f"{name}: path dependency {p.get('dir')!r} outside the repository")
                continue
            ent = tree.entries
            srcs = _src_dirs(*((tree.blob(f"{d}/lakefile.toml") or b"").decode("utf-8", "replace")
                                if f"{d}/lakefile.toml" in ent else None,
                                (tree.blob(f"{d}/lakefile.lean") or b"").decode("utf-8", "replace")
                                if f"{d}/lakefile.lean" in ent else None))
            bases = [posixpath.normpath(f"{d}/{s}") for s in srcs]

            def has(r, bases=bases, ent=ent):
                return any(f"{b}/{r}.lean" in ent or any(k.startswith(f"{b}/{r}/") for k in ent) for b in bases)
            out["scanned"].append((name, d, has))
            continue
        rev = p.get("rev")
        where = f"{pdir}/{name}"
        d = tree.repo / pdir / name
        if not d.is_dir():
            notes.append(f"{name}: not checked out at {where}")
            continue
        top = core.git(d, "rev-parse", "--show-toplevel")
        try:
            own = top.returncode == 0 and pathlib.Path(top.stdout.strip()).resolve() == d.resolve()
        except OSError:
            own = False
        if not own:
            notes.append(f"{name}: {where} is not a git checkout")
            continue
        head = core.git(d, "rev-parse", "HEAD").stdout.strip()
        if not isinstance(rev, str) or head != rev:
            notes.append(f"{name}: {where} is at {head[:12] or 'no commit'}, lake-manifest.json pins "
                         f"{str(rev)[:12]}")
            continue
        srcs = _src_dirs(_read(d / "lakefile.toml"), _read(d / "lakefile.lean"))
        bases = [d / s for s in srcs]

        def has(r, bases=bases):
            return any((b / f"{r}.lean").is_file() or (b / r).is_dir() for b in bases)
        out["scanned"].append((name, where, has))
    out["complete"] = not notes
    if notes:
        what = ("no dependency read" if not out["scanned"]
                else f"{len(notes)} of {len(notes) + len(out['scanned'])} dependencies not read")
        out["reason"] = (f"{what}: " + "; ".join(notes[:5]) + (f"; and {len(notes) - 5} more" if len(notes) > 5
                                                                  else "")
                         + f" (`lake build` checks them out at the pinned revisions under {pdir}/)")
    return out


def shadow_gaps(tree: Tree, rel: str, path: str, ch, so) -> list[dict]:
    """#404: an error gap for each dependency whose source tree has a top-level `R.lean` or `R/`, R the top-level
    root of challenge_module or solution_module (the resolver may take the dependency's file instead); without
    every dependency's tree, a warn gap for a bare generic root (Challenge, Solution, Main)."""
    gaps: list[dict] = []
    deps = None
    for key, mod in (("challenge_module", ch), ("solution_module", so)):
        if not isinstance(mod, str) or not mod.strip():
            continue
        mod = mod.strip()
        root, _, rest = mod.partition(".")
        kind = key.split("_")[0]
        qual = f"{tree.module_hint}.{rest or root}"
        fix = (f"move the module under a package-qualified root, such as {qual} (the layout `lean-pkg new` "
               f"scaffolds: MOD/Challenge.lean as MOD.Challenge, MOD/Solution.lean as MOD.Solution), and set "
               f"{key} in {rel}; rebuild, commit, re-run comparator-receipt, re-gate")
        deps = deps or tree.dependencies()
        hits = [(name, where) for name, where, has in deps["scanned"] if has(root)]
        for name, where in hits:
            gaps.append(_gap(f"{kind}.module_root_shadowed", "error",
                             f"{rel}: {key} {mod}: its top-level name {root!r} is also a top-level entry "
                             f"({root}.lean or {root}/) of the dependency {name!r} ({where}), where the resolver "
                             f"would look for its source", fix, [path]))
        if not hits and not deps["complete"] and root in GENERIC_ROOTS:
            gaps.append(_gap(f"{kind}.module_root_generic", "warn",
                             f"{rel}: {key} {mod} has the bare top-level root {root!r}, a name dependencies also "
                             f"use; the dependency scan could not rule out shadowing ({deps['reason']})",
                             fix, [path]))
    return gaps


def not_checked(tree: Tree) -> list[str]:
    """NOT_CHECKED, with the shadowing clause dropped when the dependency scan read every dependency and
    carrying the reason when it did not (#404). Unchanged when no configuration asked for the scan."""
    deps = tree._deps
    if deps is None:
        return list(NOT_CHECKED)
    out = list(NOT_CHECKED)
    clause = (f"{_SHADOW_CLAUSE} beyond the dependencies read" if deps["scanned"] else _SHADOW_CLAUSE) \
        + f" (dependency scan incomplete: {deps['reason']})"
    out[3] = out[3].replace(f"; {_SHADOW_CLAUSE};", ";" if deps["complete"] else f"; {clause};")
    return out


def headline_gaps(pkg: dict, tree: Tree, policy: dict, checked: list[str]) -> tuple[list[dict], dict | None]:
    """The headline configuration (headline.py): its own configuration gaps when the loop above did not
    check it, and the subset rule against the comparator receipt the gate records, validated at HEAD as
    `gate --check` validates it (error: uncovered names or unsigned holes; warn: no receipt to judge by)."""
    from . import headline as hl
    try:
        rel, source = hl.declared(pkg, tree.repo, rev=tree.commit)
    except core.Fail as e:
        return [_gap("comparator.headline_invalid", "error", str(e),
                     f"lean-pkg update {pkg['name']} --headline-config REL (or '' to clear it)")], None
    if rel is None:
        return [], None
    view = {"config": rel, "source": source, "judged": False, "covered": None}
    path = tree.project(rel)
    if path not in tree.entries:
        return [_gap("comparator.headline_missing", "error",
                     f"the headline configuration {rel} ({source}) is not tracked at {tree.commit[:12]}",
                     "commit it, or clear the registry field (lean-pkg update NAME --headline-config '')",
                     [path])], view
    gaps = [] if rel in checked else config_gaps(tree, policy, rel)
    try:
        cfg = hl.parse(tree.blob(path), rel)
    except ValueError as e:
        if not gaps and rel not in checked:   # the loop above already reported a checked one
            gaps.append(_gap("comparator.headline_invalid", "error", str(e),
                             "keep only Palomar's six keys and name both modules and the theorems", [path]))
        return gaps, view
    try:
        res = hl.judge(pkg, tree.repo, rel, cfg)
    except core.Fail as e:
        res = {"judged": False, "reason": str(e)}
    if not res["judged"]:
        gaps.append(_gap("comparator.headline_unjudged", "warn",
                         f"headline configuration {rel} ({source}) cannot be judged: {res['reason']}; "
                         f"formalization.yaml status.main_results falls back to every tracked configuration",
                         "run comparator-receipt at the commit, record it (lean-pkg comparator NAME --receipt R) "
                         "and re-gate", [path]))
        return gaps, view
    view.update(judged=True, covered=not (res["uncovered"] or res["unsigned"]))
    why = hl.refusal(rel, res)
    if why:
        gaps.append(_gap("comparator.headline_uncovered", "error", why,
                         "compare the missing names (a compared configuration, comparator-receipt, re-gate), "
                         "or drop them from the headline configuration; commit", [path]))
    return gaps, view


def _module_source(mod) -> str | None:
    if not isinstance(mod, str) or not re.fullmatch(r"[^\s/]+(\.[^\s/]+)*", mod.strip()):
        return None
    return mod.strip().replace(".", "/") + ".lean"


# --------------------------------------------------------------------------
# formalization.yaml and the licence

def _nonempty(v) -> bool:
    return (isinstance(v, str) and v.strip() != "") or (isinstance(v, (list, dict)) and len(v) > 0)


def palomar_minimum(doc: dict, policy: dict) -> list[str]:
    """The mechanical minimum Palomar's README documents (beyond the v0.4 schema): one problem per field."""
    out = []
    proj = doc.get("project") if isinstance(doc.get("project"), dict) else {}
    name = proj.get("name")
    if not _nonempty(name) or (isinstance(name, str) and len(name) > PROJECT_NAME_MAX):
        out.append(f"project.name: non-empty, at most {PROJECT_NAME_MAX} characters")
    for k in ("description", "authors", "license"):
        if not _nonempty(proj.get(k)):
            out.append(f"project.{k}: required, non-empty")
    if not _nonempty(proj.get("responsible_maintainers")) and not _nonempty(proj.get("responsible_maintainer")):
        out.append("project.responsible_maintainers: required, non-empty")
    card = policy["formalization"]["classification_cardinality"]
    cls = doc.get("classification") if isinstance(doc.get("classification"), dict) else {}
    for k in ("arxiv", "msc2020"):
        lo, hi = card[k]
        v = cls.get(k) or []
        if not isinstance(v, list) or not (lo <= len(v) <= hi):
            out.append(f"classification.{k}: {lo} to {hi} codes")
    auto = doc.get("automation") if isinstance(doc.get("automation"), dict) else {}
    if not _nonempty(auto.get("methods")):
        out.append("automation.methods: required, non-empty")
    rv = doc.get("review") if isinstance(doc.get("review"), dict) else {}
    if not _nonempty(rv.get("status")):
        out.append("review.status: required, non-empty")
    sources = doc.get("sources")
    if not isinstance(sources, list) or not sources:
        out.append("sources: required, non-empty")
        return out
    cats = set(policy["formalization"]["source_relationship_categories"])
    rels, original = [], []
    for i, s in enumerate(sources):
        s = s if isinstance(s, dict) else {}
        r = s.get("relationship")
        if r not in cats:
            out.append(f"sources[{i}].relationship: one of {', '.join(sorted(cats))}")
        rels.append(r)
        if s.get("type") == "original-proof":
            original.append((i, r))
    if original:
        if any(r != "other" for _i, r in original) or any(r in SOURCE_BASED for r in rels):
            out.append("sources: an original-proof entry needs relationship other, and the other entries "
                       "background or other")
    elif not any(r in SOURCE_BASED for r in rels):
        out.append("sources: no original-proof entry, so at least one relationship must be formalizes, adapts "
                   "or independently-proves")
    return out


def formalization_gaps(pkg: dict, tree: Tree, policy: dict) -> tuple[list[dict], dict | None]:
    from . import formalization as fz
    import yaml
    rel = fz.target_rel(pkg)
    path = tree.project(rel)
    raw = tree.blob(path)
    name = pkg["name"]
    if raw is None:
        return [_gap("formalization.missing", "error", f"{rel} is not tracked in the project",
                     f"render it (`lean-pkg formalization {name} --write`), fill the placeholders, commit", [path])], None
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        return [_gap("formalization.invalid_yaml", "error", f"{rel}: not valid UTF-8 YAML ({e})",
                     "fix the file; commit", [path])], None
    if not isinstance(doc, dict):
        return [_gap("formalization.wrong_root_type", "error", f"{rel}: the top level is not a mapping",
                     "fix the file; commit", [path])], None
    gaps = []
    version = doc.get("version")
    if version not in (None, fz.SCHEMA_VERSION):
        gaps.append(_gap("formalization.version", "error", f"{rel}: version {version!r}, Palomar reads {fz.SCHEMA_VERSION}",
                         f"re-render with `lean-pkg formalization {name} --write`; commit", [path]))
    mode, errors = fz.validate_doc(doc)
    if errors:
        gaps.append(_gap("formalization.schema", "error",
                         f"{rel} fails the {fz.SCHEMA_VERSION} schema ({mode}): " + "; ".join(errors[:10])
                         + (f"; and {len(errors) - 10} more" if len(errors) > 10 else ""),
                         "fix the fields; commit", [path]))
    holes = fz.find_placeholders(doc)
    if holes:
        gaps.append(_gap("formalization.placeholder", "error",
                         f"{rel}: {len(holes)} placeholder value(s): {', '.join(holes)}",
                         "replace every [PLACEHOLDER] value; commit", [path]))
    minimum = palomar_minimum(doc, policy)
    if minimum:
        gaps.append(_gap("formalization.palomar_minimum", "error",
                         f"{rel} misses Palomar's metadata minimum: " + "; ".join(minimum),
                         "fill the fields (PalomarPolicy CONTRIBUTING.md, section 3); commit", [path]))
    return gaps, doc


def license_gaps(pkg: dict, tree: Tree, doc: dict | None) -> list[dict]:
    from . import index as index_mod
    name = pkg["name"]
    root = sorted(p for p in tree.entries if "/" not in p and LICENSE_FILE_RE.match(p))
    gaps = []
    spdx = pkg.get("license_spdx")
    fl = ((doc or {}).get("project") or {}).get("license") if isinstance((doc or {}).get("project"), dict) else None
    if not spdx:
        gaps.append(_gap("license.spdx_unset", "error", "license_spdx is not recorded in the registry",
                         f"lean-pkg update {name} --license-spdx ID"))
    if not root:
        gaps.append(_gap("license.missing", "error", "no licence file at the repository root (LICENSE, LICENCE, "
                         "COPYING, UNLICENSE or OFL, optionally .md/.markdown/.txt)", "commit one; re-gate"))
        return gaps
    if len(root) > 1:
        gaps.append(_gap("license.ambiguous", "error", f"{len(root)} licence files at the repository root; "
                         f"Palomar needs exactly one", "keep one; commit, re-gate", root))
    for p in root:
        if tree.entries[p][0] == "120000":
            gaps.append(_gap("license.not_regular", "error", f"{p} is a symbolic link", "commit a regular file", [p]))
    want = spdx or fl
    text = (tree.blob(root[0]) or b"").decode("utf-8", "replace")
    if not text.strip():
        gaps.append(_gap("license.empty", "error", f"{root[0]} is empty", "commit the licence text", [root[0]]))
    marker = index_mod.LICENSE_MARKERS.get(str(want)) if want else None
    if marker and text.strip() and marker not in text:
        gaps.append(_gap("license.mismatch", "error", f"{root[0]} does not read like {want} (no {marker!r})",
                         "commit the licence text the SPDX identifier names", [root[0]]))
    if spdx and fl and str(fl) != str(spdx):
        gaps.append(_gap("license.mismatch", "error",
                         f"formalization.yaml project.license {fl} differs from license_spdx {spdx}",
                         f"re-render (`lean-pkg formalization {name} --write`); commit"))
    return gaps


def repository_gaps(pkg: dict, tree: Tree) -> list[dict]:
    name = pkg["name"]
    gaps = []
    if not core.gh_nwo(str(pkg.get("repo") or "")):
        gaps.append(_gap("repository.not_github", "error", f"repo {pkg.get('repo')!r} is not a GitHub repository; "
                         "Palomar accepts public GitHub repositories only",
                         f"lean-pkg update {name} --url git@github.com:OWNER/REPO.git (after creating it)"))
    if pkg.get("visibility") != "public":
        gaps.append(_gap("repository.not_public", "warn",
                         f"the registry records visibility {pkg.get('visibility')!r} (not queried)",
                         f"lean-pkg publish {name} --dry-run, then lean-pkg promote {name}"))
    p = core.git(tree.repo, "for-each-ref", "--format=%(refname)", "--contains", tree.commit, "refs/remotes/")
    if p.returncode != 0 or not p.stdout.strip():
        gaps.append(_gap("commit.not_pushed", "warn",
                         f"commit {tree.commit[:12]} is in no remote-tracking branch (local refs; nothing fetched)",
                         "push it; Palomar pins a commit of the public repository"))
    return gaps


# --------------------------------------------------------------------------
# the check

def resolve_commit(pkg: dict, repo: pathlib.Path, rev: str | None) -> tuple[str, str]:
    name = pkg["name"]
    if rev is not None:
        sha = core._rev(repo, rev)
        if not sha:
            raise core.Fail(f"{name}: --rev {rev!r} is not a commit of {repo}")
        return sha, "rev"
    from .fresh_repo import gate_bound_commit
    sha = gate_bound_commit(pkg, repo)
    if sha and core._rev(repo, sha):
        return sha, "gate"
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    return head, "head"


def check(pkg: dict, repo: pathlib.Path, commit: str, policy: dict, source: dict,
          config: str | None = None) -> dict:
    tree = Tree(repo, commit, pkg.get("claims_file") or "claims.yaml")
    # the root module `lean-pkg new` gives the name (scaffold.module_of: my-proof -> MyProof)
    tree.module_hint = "".join(part[:1].upper() + part[1:] for part in str(pkg["name"]).split("-"))
    gaps, facts = source_gaps(tree, policy, pkg["name"])
    tc_gaps, tc = toolchain_gaps(tree, policy)
    gaps += tc_gaps + layout_gaps(tree)
    cg, configs = comparator_gaps(tree, policy, config)
    hg, headline = headline_gaps(pkg, tree, policy, configs)
    fg, doc = formalization_gaps(pkg, tree, policy)
    gaps += cg + hg + fg + license_gaps(pkg, tree, doc) + repository_gaps(pkg, tree)
    errors = sum(g["severity"] == "error" for g in gaps)
    # #237: a warning, never a gap (Palomar's rules have no such list); an unreadable list matches nothing
    from . import toolchain_advisories
    advisories, _problem = toolchain_advisories.lookup(tc)
    return {"ready": errors == 0, "commit": commit, "project_path": tree.prefix.rstrip("/") or ".",
            "lean_toolchain": tc, "toolchain_advisories": advisories, "configs": configs, "headline": headline,
            **facts,
            "counts": {"error": errors, "warn": len(gaps) - errors}, "gaps": gaps,
            "rules": {**source, "toolchain_minimum": policy["toolchain"]["minimum"]},
            "not_checked": not_checked(tree)}


def verdict(name: str, rep: dict) -> str:
    c = rep["counts"]
    head = f"{name}: Palomar check at {rep['commit'][:12]}"
    if rep["ready"]:
        return head + (": READY" + (f" ({c['warn']} warning(s): submission steps or advice)" if c["warn"] else ""))
    return head + f": NOT READY — {c['error'] + c['warn']} gap(s): {c['error']} error, {c['warn']} warn"


def report_lines(name: str, rep: dict, commit_source: str) -> list[str]:
    src = {"gate": "the gate-bound commit", "head": "HEAD", "rev": "--rev"}[commit_source]
    lines = [verdict(name, rep) + f" [{src}]"]
    from .toolchain_advisories import warn_line
    lines += [f"  {warn_line(v)}" for v in rep.get("toolchain_advisories") or []]
    for g in rep["gaps"]:
        mark = "✗" if g["severity"] == "error" else "WARN"
        lines.append(f"  {mark}  {g['code']}: {g['detail']}")
        lines += [f"        {p}" for p in g["paths"]]
        lines.append(f"        fix: {g['fix']}")
    r = rep["rules"]
    lines.append(f"rules: {'PalomarSubmission@' + r['commit'][:12] + ' (committed ' + r['committed'] + ', read ' + r['retrieved'] + ')' if r.get('kind') == 'pinned' else '--rules ' + str(r.get('path'))}; "
                 f"toolchain minimum {r['toolchain_minimum']}; {rep['lean_files']} .lean file(s), "
                 f"{rep['missing_module']} without a module header")
    lines.append("not checked (Palomar's verifier does): " + "; ".join(rep["not_checked"]))
    if rep["gaps"]:
        lines.append(verdict(name, rep))
    return lines


def summary(pkg: dict, repo: pathlib.Path, commit: str) -> dict:
    """The hook for `gate --check` and `publish --dry-run`: {ready, commit, errors, warns, codes, command}
    with the pinned rules; {error: TEXT} when the check itself could not run (never a refusal)."""
    name = pkg["name"]
    cmd = f"lean-pkg palomar check {name} --rev {commit[:12]}"
    try:
        policy, source = load_rules(None)
        rep = check(pkg, repo, commit, policy, source)
    except (core.Fail, OSError, ValueError) as e:
        return {"ready": None, "commit": commit, "error": str(e), "command": cmd}
    return {"ready": rep["ready"], "commit": commit, "errors": rep["counts"]["error"],
            "warns": rep["counts"]["warn"], "codes": sorted({g["code"] for g in rep["gaps"]}), "command": cmd}


# why the gate's advisory stays quiet for a package that does not intend Palomar (#387)
SKIPPED_NOT_AIMING = "not aiming at Palomar"


def hook(reg: dict, name: str, commit: str | None) -> dict | None:
    """summary() for the package NAME at `commit` (None: HEAD), plus `skipped`: None for a package that
    intends Palomar, else SKIPPED_NOT_AIMING (the fields are still filled; the gate prints no WARN, #387).
    None when there is no checkout to read."""
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        return None
    commit = commit or core.git_head(repo)
    if not commit or not core._rev(repo, commit):
        return None
    return {**summary(pkg, repo, commit), "skipped": None if intends_palomar(pkg) else SKIPPED_NOT_AIMING}


def summary_warning(view: dict) -> str | None:
    if view.get("skipped"):
        return None
    if view.get("ready") is None:
        return f"palomar: the readiness check could not run ({view.get('error')}); run {view['command']}"
    if view["ready"]:
        return None
    return (f"palomar: not submittable at {view['commit'][:12]}: {view['errors']} gap(s) need a new commit "
            f"({', '.join(view['codes'])}); see {view['command']}")


# packages aiming at Palomar: the record says so (upstream_target palomar) or Palomar evidence exists;
# a scratch, archived or upstreamed package is not judged (audit stays quiet for it)
AUDIT_EXEMPT_STATUSES = ("scratch", "archived", "upstreamed")


def intends_palomar(pkg: dict) -> bool:
    if pkg.get("status") in AUDIT_EXEMPT_STATUSES:
        return False
    return pkg.get("upstream_target") == "palomar" or any(isinstance(e, dict) for e in pkg.get("palomar") or [])


def error_gaps(rep: dict) -> list[dict]:
    return [g for g in rep["gaps"] if g["severity"] == "error"]


def gap_lines(gaps: list[dict]) -> list[str]:
    return [f"  ✗  {g['code']}: {g['detail']}" + "".join(f"\n        {p}" for p in g["paths"])
            + f"\n        fix: {g['fix']}" for g in gaps]


def audit_finding(pkg: dict, repo: pathlib.Path) -> dict | None:
    """The `palomar-not-ready` audit finding for a package that intends Palomar, judged at the commit
    `palomar check NAME` reads (gate-bound, else HEAD), with the pinned rules; None when it is ready
    (warn gaps are submission steps, never a finding) or does not intend Palomar."""
    if not intends_palomar(pkg):
        return None
    name = pkg["name"]
    cmd = f"lean-pkg palomar check {name}"
    try:
        commit, how = resolve_commit(pkg, repo, None)
        policy, source = load_rules(None)
        rep = check(pkg, repo, commit, policy, source)
    except (core.Fail, OSError, ValueError) as e:
        return {"detail": f"Palomar readiness could not be checked ({e}); run `{cmd}`",
                "errors": None, "codes": [], "commit": None, "command": cmd}
    errs = error_gaps(rep)
    if not errs:
        return None
    codes = sorted({g["code"] for g in errs})
    src = {"gate": "the gate-bound commit", "head": "HEAD"}[how]
    return {"detail": f"{len(errs)} Palomar readiness gap(s) at {src} {commit[:12]} ({', '.join(codes)}); "
                      f"run `{cmd}`",
            "errors": len(errs), "codes": codes, "commit": commit, "command": cmd}


def cmd_check(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    override = None
    if getattr(args, "repo", None) is not None:
        reg, override = gate_mod.repo_override(reg, args.name, args.repo)
        if note := gate_mod.repo_override_note(override):
            print(note, file=core.sys.stderr)
    policy, source = load_rules(args.rules)
    pkg = core.get_pkg(reg, args.name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{args.name}: local_path does not exist: {repo}")
    commit, how = resolve_commit(pkg, repo, args.rev)
    rep = check(pkg, repo, commit, policy, source, args.config)
    if getattr(args, "json_out", False):
        core.print_json({"ok": rep["ready"], "name": args.name, "commit_source": how, **rep,
                         "repo_override": override})
    else:
        print("\n".join(report_lines(args.name, rep, how)))
    return 0 if rep["ready"] else 1


def add_parser(psub, add_json) -> None:
    p = psub.add_parser("check", help="read-only, offline: every gap to Palomar's mechanical rules at one commit "
                                      "(module headers, toolchain floor, comparator configs, formalization.yaml, "
                                      "licence, repository); exit 0 ready / 1 gaps")
    p.add_argument("name")
    p.add_argument("--rev", default=None, metavar="REV",
                   help="the commit to judge (default: the gate-bound commit, else HEAD)")
    p.add_argument("--config", default=None, metavar="PATH",
                   help="judge only this comparator configuration (Palomar verifies the one a submission names); "
                        "default: every configuration tracked at the commit")
    p.add_argument("--rules", default=None, metavar="FILE",
                   help="a newer PalomarSubmission browser-preflight-policy.json (schema 1) downloaded by hand; "
                        f"default: the rules pinned at PalomarSubmission {RULES_SOURCE['commit'][:12]} "
                        f"(read {RULES_SOURCE['retrieved']})")
    p.add_argument("--repo", default=None, metavar="DIR",
                   help="judge the checkout DIR instead of the registered local_path (never saved)")
    add_json(p)
    p.set_defaults(fn=cmd_check, palomar_fn="check")
