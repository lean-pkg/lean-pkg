# lean-pkg lake hygiene -- what a Lake build log and LEAN_PATH must show before a receipt trusts them
"""Two Lake behaviours that can make a verification log say less than it seems to (checked against the Lake
and Lean sources of v4.35.0-rc2):

1. The local artifact cache. A package reads Lake's artifact cache unless it is switched off
   (`Package.isArtifactCacheReadable`: the package's own `enableArtifactCache`, else the workspace's, which is
   LAKE_ARTIFACT_CACHE and then the root package's setting, else true; a package lakefile's
   `enableArtifactCache = true` wins over LAKE_ARTIFACT_CACHE=false). The cache lives in LAKE_CACHE_DIR, else
   under the toolchain or ~/.cache/lake; LAKE_CACHE_DIR="" does not disable it (the root's .lake/cache is used).
   `--no-cache` / LAKE_NO_CACHE govern downloads only. A module restored from it is reported `Reused` and its
   `declaration uses sorry` warning is not printed, so a sorry count scraped from the log can read 0. Lake
   prints such a job (`<icon> [i/n] Reused M`, also `Replayed`, `Unpacked`) only when it has output or with
   `-v`, so the defence is a fresh, empty LAKE_CACHE_DIR with LAKE_ARTIFACT_CACHE=false and a fresh tree; a
   log that still shows a root-package module Reused, Replayed or Unpacked is refused (`cache_reuse_problems`).
   A `Replayed` line for a module the log already showed `Built` is not a restore: a later `lake lean FILE`
   re-prints the stored warnings of the modules it imports (`restored_jobs`). Only an earlier `Built`
   exonerates; a cache line before the module's `Built`, or with none, still counts.

2. LEAN_PATH order. Lake's `Workspace.leanPath` lists the root package's lib directory LAST, and Lean's
   `SearchPath.findWithExt` takes the FIRST entry holding `<R>/` or `<R>.olean` for a module whose root
   component is R. A dependency whose build directory holds `<R>/...` therefore serves its own module to
   `lake env lean FILE` (and to anything else started under `lake env`). `lake build` and `lake lean FILE` pass
   explicit module paths and are not affected. `shadow_hits` names every LEAN_PATH entry, other than the root
   package's own lib directory, that holds a root namespace of the root package.

SHADOW_CHECK_SH is the same check as a shell function; `receipt slurm --template`, comparator-check.sh and
verify-comparator.sh carry it verbatim (tests/test_lake_hygiene.py compares the copies)."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

try:
    import tomllib
except ImportError:          # pragma: no cover - Python 3.11+ is required (pyproject)
    tomllib = None

CACHE_VERBS = ("Reused", "Replayed", "Unpacked")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Lake's job line (Lake/Build/Run.lean reportJob): `<icon> [i/n][ (Optional)] <verb> <caption>[ (time)]`.
JOB_LINE_RE = re.compile(r"\[\d+/\d+\](?: \(Optional\))? (Built|Reused|Replayed|Unpacked|Fetched|Ran|Canceled|"
                         r"Building|Reusing|Replaying|Unpacking|Fetching|Running) (\S+)")
# The template's markers around the build command; the audit after it (`lake lean`) replays the modules it
# imports, which is not a restored build.
BUILD_BEGIN, BUILD_END = "lean-pkg-build: begin", "lean-pkg-build: end"
ROOT_LIB_DIR = pathlib.PurePosixPath(".lake") / "build" / "lib" / "lean"
LEAN_PATH_TIMEOUT = 600


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def build_section(text: str) -> str:
    """The part of a log between the template's build markers (the first begin, the following end), or the
    whole log when it has none."""
    lines = text.splitlines()
    try:
        i = next(n for n, x in enumerate(lines) if x.strip() == BUILD_BEGIN)
    except StopIteration:
        return text
    j = next((n for n in range(i + 1, len(lines)) if lines[n].strip() == BUILD_END), len(lines))
    return "\n".join(lines[i + 1:j]) + "\n"


def job_lines(text: str) -> list[tuple[str, str]]:
    """[(verb, target)] of Lake's job lines in a log; a target's `:facet` suffix is dropped."""
    out = []
    for m in JOB_LINE_RE.finditer(strip_ansi(text)):
        out.append((m.group(1), m.group(2).split(":", 1)[0]))
    return out


def restored_jobs(jobs) -> list[tuple[str, str]]:
    """The distinct (verb, target) cache jobs (CACHE_VERBS) of [(verb, target)], in log order, for which no
    EARLIER `Built` line names the same target. A module compiled in this log and later shown `Replayed` had
    its stored warnings re-printed (a `lake lean FILE` importing it), it was not restored; a cache line with no
    earlier `Built` (before it, or none at all) still counts."""
    built: set[str] = set()
    out: list[tuple[str, str]] = []
    for verb, target in jobs:
        if verb == "Built":
            built.add(target)
        elif verb in CACHE_VERBS and target not in built and (verb, target) not in out:
            out.append((verb, target))
    return out


def _first_component(name: str) -> str | None:
    name = name.strip().strip("`").strip()
    if not name:
        return None
    if name.startswith("«"):
        end = name.find("»")
        return name[1:end] if end > 0 else None
    first = re.split(r"[.]", name, maxsplit=1)[0]
    return first if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_'!?]*", first) else None


def lakefile_roots(toml_text: str | None = None, lean_text: str | None = None) -> set[str]:
    """The root namespaces (first name component) of every lean_lib's name, roots and globs in a
    lakefile.toml / lakefile.lean. Over-inclusive on purpose (a lib name is kept even when `roots` replaces
    it): a namespace listed here is only ever a reason to look closer."""
    names: list[str] = []
    if toml_text is not None:
        data = None
        if tomllib is not None:
            try:
                data = tomllib.loads(toml_text)
            except (ValueError, TypeError):
                data = None
        libs = data.get("lean_lib") if isinstance(data, dict) else None
        for lib in libs if isinstance(libs, list) else []:
            if not isinstance(lib, dict):
                continue
            for key in ("name", "roots", "globs"):
                v = lib.get(key)
                for x in v if isinstance(v, list) else [v]:
                    if isinstance(x, str):
                        names.append(x)
    if lean_text is not None:
        names += [m.group(1) for m in re.finditer(r"\blean_lib\s+(«[^»]+»|[A-Za-z_][A-Za-z0-9_.'!?]*)", lean_text)]
        for m in re.finditer(r"\b(?:roots|globs)\s*:=\s*#\[(.*?)\]", lean_text, re.S):
            names += re.findall(r"`(«[^»]+»|[A-Za-z_][A-Za-z0-9_.'!?«»]*)", m.group(1))
    return {c for c in map(_first_component, names) if c}


def tracked_lean_files(paths) -> list[str]:
    """The package-relative `.lean` sources among tracked paths, less the lakefile and anything under .lake/."""
    return [p for p in paths if p.endswith(".lean") and p != "lakefile.lean" and not p.startswith(".lake/")]


def fallback_roots(lean_files) -> set[str]:
    """Top-level names of the tracked Lean sources (X.lean -> X, X/Y.lean -> X): the roots when the lakefile
    names none."""
    out = set()
    for p in lean_files:
        top = p.split("/", 1)[0]
        top = top[:-len(".lean")] if top.endswith(".lean") else top
        c = _first_component(top)
        if c:
            out.add(c)
    return out


def package_roots(lakefile_toml: str | None, lakefile_lean: str | None, lean_files) -> set[str]:
    return lakefile_roots(lakefile_toml, lakefile_lean) or fallback_roots(lean_files)


def is_root_module(module: str, lean_files, roots: set[str]) -> bool:
    """A module of the root package: its source `A/B/C.lean` is tracked (at the package root or under a source
    directory), or its first component is a root namespace."""
    rel = module.replace(".", "/") + ".lean"
    if any(p == rel or p.endswith("/" + rel) for p in lean_files):
        return True
    return _first_component(module) in roots


def cache_reuse_problems(text: str, lean_files, roots: set[str]) -> tuple[list[str], list[str]]:
    """(refusals, checks) for a build log: root-package modules Lake restored instead of compiling (inside the
    template's build markers when present; a cache line after the module's own `Built` is a warning replay,
    restored_jobs), and the template's own cache declarations when present."""
    refusals, checks = [], []
    section = build_section(text)
    restored = [(verb, target) for verb, target in restored_jobs(job_lines(section))
                if is_root_module(target, lean_files, roots)]
    if restored:
        shown = ", ".join(f"{t} ({v})" for v, t in restored[:8]) + (
            f" (+{len(restored) - 8} more)" if len(restored) > 8 else "")
        refusals.append(f"verify log: Lake restored root-package module(s) instead of compiling them: {shown}; a "
                        f"restored module's `declaration uses sorry` warning need not be printed, so the sorry scan "
                        f"is unsound; rebuild from a fresh tree (no .lake/build) with LAKE_CACHE_DIR a fresh empty "
                        f"directory and LAKE_ARTIFACT_CACHE=false")
    else:
        checks.append("✓ lake: no root-package module shown Reused, Replayed or Unpacked in the build log")
    for line in strip_ansi(text).splitlines():
        m = re.match(r"^lake-artifact-cache:[ \t]*(.*?)[ \t]*$", line)
        if m and m.group(1).lower() not in ("false", "0"):
            refusals.append(f"verify log: `lake-artifact-cache: {m.group(1)}`; a fresh build needs "
                            f"LAKE_ARTIFACT_CACHE=false")
    return refusals, checks


# --------------------------------------------------------------------------
# LEAN_PATH shadowing

def _same_dir(a: str, b: pathlib.Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def shadow_hits(lean_path, project: pathlib.Path, roots: set[str]) -> list[str]:
    """The `<entry>/<R>` (directory) or `<entry>/<R>.olean` / `.ilean` paths, for every LEAN_PATH entry other
    than the root package's own .lake/build/lib/lean, that hold a root namespace R of the root package."""
    own = pathlib.Path(project) / ROOT_LIB_DIR
    entries = lean_path.split(os.pathsep) if isinstance(lean_path, str) else list(lean_path)
    hits = []
    for entry in entries:
        if not entry or _same_dir(entry, own):
            continue
        base = pathlib.Path(entry)
        if not base.is_absolute():
            base = pathlib.Path(project) / base
        for r in sorted(roots):
            for cand in (base / r, base / f"{r}.olean", base / f"{r}.ilean"):
                if (cand.is_dir() if cand.name == r else cand.exists()) and str(cand) not in hits:
                    hits.append(str(cand))
    return hits


def lean_path(project: pathlib.Path, lake: str, env: dict | None = None,
              preexec=None) -> tuple[str | None, str | None]:
    """(LEAN_PATH as `lake env` sets it, error)."""
    try:
        p = subprocess.run([lake, "env", "printenv", "LEAN_PATH"], cwd=project, env=env, capture_output=True,
                           text=True, timeout=LEAN_PATH_TIMEOUT, stdin=subprocess.DEVNULL, preexec_fn=preexec)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"`lake env printenv LEAN_PATH` could not run: {e}"
    if p.returncode != 0:
        return None, (f"`lake env printenv LEAN_PATH` exited {p.returncode}: "
                      f"{(p.stderr or p.stdout).strip().splitlines()[-1:] or 'no output'}")
    return p.stdout.strip(), None


def module_roots(modules) -> set[str]:
    """The root namespaces of module names (A.B.C -> A)."""
    return {c for c in map(_first_component, modules) if c}


def project_roots(project: pathlib.Path, tracked) -> set[str]:
    def read(name: str) -> str | None:
        try:
            return (pathlib.Path(project) / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return package_roots(read("lakefile.toml"), read("lakefile.lean"), tracked_lean_files(tracked))


SHADOW_HINT = ("a dependency's build directory comes before the root package's on LEAN_PATH (Lake lists the root "
               "last) and Lean loads <R>/ from the first entry holding it, so `lake env lean` would read that "
               "copy; remove it (or rename the root namespace) and rebuild")


# The shell copy (begin/end lines included). Needs bash; reads lakefile.toml / lakefile.lean in the current
# directory. Usage: `lake_shadow_check "$(lake env printenv LEAN_PATH)"`: prints `lean-path-shadow: <path>` per
# hit and returns 1, else prints `lean-path-shadow: none (<n> entries, roots <R,...>)`.
SHADOW_CHECK_SH = r"""# >>> lean-pkg lake_shadow_check (keep identical in every copy; see lean_pkg/lake_hygiene.py)
lake_root_namespaces() {
  local found
  found=$(
    {
      if [ -f lakefile.toml ]; then
        awk '/^[[:space:]]*\[/ { lib = ($0 ~ /^[[:space:]]*\[\[[[:space:]]*lean_lib[[:space:]]*\]\]/) }
             lib && /^[[:space:]]*(name|roots|globs)[[:space:]]*=/' lakefile.toml | grep -oE '"[^"]+"' | tr -d '"'
      fi
      if [ -f lakefile.lean ]; then
        grep -oE 'lean_lib[[:space:]]+(«[^»]+»|[A-Za-z_][A-Za-z0-9_.]*)' lakefile.lean | awk '{print $2}'
        tr '\n' ' ' < lakefile.lean | grep -oE '(roots|globs)[[:space:]]*:=[[:space:]]*#\[[^]]*\]' \
          | grep -oE '`[^]`[:space:],)]+' | tr -d '`'
      fi
    } 2>/dev/null | sed -e 's/^«\([^»]*\)».*$/\1/' -e 's/[.].*$//' | grep -E '^[A-Za-z_]' | sort -u || true
  )
  if [ -z "$found" ]; then
    found=$(find . -path ./.lake -prune -o -name '*.lean' ! -path ./lakefile.lean -print 2>/dev/null \
      | sed -e 's|^\./||' -e 's|/.*$||' -e 's|\.lean$||' | grep -E '^[A-Za-z_]' | sort -u || true)
  fi
  printf '%s\n' "$found"
}
lake_shadow_check() {
  local own entry r n=0 hits=0 roots
  local -a entries
  own="$(pwd -P)/.lake/build/lib/lean"
  roots=$(lake_root_namespaces)
  IFS=: read -r -a entries <<<"${1:-}"
  for entry in "${entries[@]}"; do
    [ -n "$entry" ] || continue
    n=$((n + 1))
    if [ "$(cd "$entry" 2>/dev/null && pwd -P)" = "$own" ]; then continue; fi
    for r in $roots; do
      if [ -d "$entry/$r" ] || [ -e "$entry/$r.olean" ] || [ -e "$entry/$r.ilean" ]; then
        echo "lean-path-shadow: $entry/$r"
        hits=$((hits + 1))
      fi
    done
  done
  if [ "$hits" -gt 0 ]; then return 1; fi
  echo "lean-path-shadow: none ($n entries, roots $(printf '%s' "$roots" | paste -sd, -))"
}
# <<< lean-pkg lake_shadow_check
"""
