# lean-pkg blueprint -- cross-check a leanblueprint's declaration list with checkdecls
"""`lean-pkg blueprint check NAME [--decls PATH]`: the blueprint cross-check (two-tracks gap #17).

leanblueprint writes every name in the blueprint's `\\lean{...}` tags to `blueprint/lean_decls`, one per
line. checkdecls (PatrickMassot/checkdecls, Apache-2.0) is a Lake executable that imports the package's
root modules and prints `NAME is missing.` for every listed name the environment does not contain. This
command runs it; it does not reimplement it, and it never vendors its code:

1. the checkout must be clean (tracked files as committed at HEAD), so the answer is about HEAD;
2. checkdecls must be a dependency in `lake-manifest.json` at HEAD: the package adds the `require`
   (REQUIRE_LEAN / REQUIRE_TOML, pinned to CHECKDECLS_REV); lean-pkg never edits a lakefile;
3. `lake build`, then `lake exe checkdecls PATH`; the `is missing.` lines are the missing names;
4. claims.yaml at HEAD: every claim whose `decl` the blueprint names must be a name checkdecls accepts.
   Claims outside the blueprint and blueprint names no claim carries are listed, never refused.

A package without a blueprint (no `blueprint/` directory and no declaration list) is skipped, exit 0.
Exit 0 when nothing is missing, 1 otherwise (a refusal is 1 as well). Nothing is written.
"""

from __future__ import annotations

import json
import pathlib
import re

from . import core

DEFAULT_DECLS = "blueprint/lean_decls"
CHECKDECLS_URL = "https://github.com/PatrickMassot/checkdecls.git"
# The latest tag (lean4.18.0) and the default branch's head when this was written; lake builds it with the
# package's own toolchain (run on v4.29.0 for this check's design).
CHECKDECLS_REV = "3d425859e73fcfbef85b9638c2a91708ef4a22d4"
REQUIRE_LEAN = f'require checkdecls from git "{CHECKDECLS_URL}" @ "{CHECKDECLS_REV}"'
REQUIRE_TOML = f'[[require]]\nname = "checkdecls"\ngit = "{CHECKDECLS_URL}"\nrev = "{CHECKDECLS_REV}"'
MISSING_RE = re.compile(r"^(?P<name>\S.*?) is missing\.$")
TAIL = 12


def run_lake(repo: pathlib.Path, *args: str):
    """`lake ARGS` in the checkout, without the repository-selecting Git variables (#27)."""
    return core.run(["lake", *args], cwd=str(repo), scrub=True)


def read_names(text: str) -> list[str]:
    """The declaration list as leanblueprint writes it: one name per line (blank lines ignored)."""
    out: list[str] = []
    for line in text.splitlines():
        name = line.strip()
        if name and name not in out:
            out.append(name)
    return out


def parse_missing(output: str) -> list[str]:
    """The names a checkdecls run reported (`NAME is missing.`), in output order."""
    out: list[str] = []
    for line in output.splitlines():
        m = MISSING_RE.match(line.strip())
        if m and m.group("name") not in out:
            out.append(m.group("name"))
    return out


def manifest_entry(repo: pathlib.Path) -> dict | None:
    """checkdecls's entry in lake-manifest.json at HEAD, or None."""
    raw = core.git_show_bytes(repo, "lake-manifest.json")
    if raw is None:
        return None
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    packages = doc.get("packages") if isinstance(doc, dict) else None
    for p in packages if isinstance(packages, list) else []:
        if isinstance(p, dict) and p.get("name") == "checkdecls":
            return p
    return None


def how_to_add(name: str) -> str:
    return (f"{name}: checkdecls is not a dependency in lake-manifest.json at HEAD. Add it to the lakefile "
            f"yourself (lean-pkg never edits it):\n"
            f"  lakefile.lean: {REQUIRE_LEAN}\n"
            f"  lakefile.toml: " + REQUIRE_TOML.replace("\n", "\n                 ") + "\n"
            "then run `lake update checkdecls`, commit the lakefile and lake-manifest.json, and rerun")


def tail(text: str) -> str:
    lines = [x for x in (text or "").strip().splitlines() if x.strip()]
    return "\n    ".join(lines[-TAIL:]) or "(no output)"


def safe_rel(path: str) -> str:
    pp = pathlib.PurePosixPath(path)
    if not path.strip() or pp.is_absolute() or ".." in pp.parts:
        raise core.Fail(f"--decls must be a path relative to the checkout without '..': {path!r}")
    return path


def claims_cross_check(pkg: dict, repo: pathlib.Path, names: list[str], missing: list[str]) -> dict:
    from . import claims as claims_mod
    rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
    raw = core.git_show_bytes(repo, rel)
    if raw is None:
        return {"file": rel, "present": False, "checked": 0, "problems": [], "outside_blueprint": [],
                "blueprint_without_claim": names}
    doc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), f"{rel} at HEAD")
    errors = claims_mod.validate_claims(doc, pkg["name"])
    if errors:
        raise core.Fail(f"{rel} at HEAD is not a valid claims/1 file:\n  " + "\n  ".join(errors[:10]))
    claims = [c for c in doc.get("claims") or [] if isinstance(c, dict)]
    in_bp, gone = set(names), set(missing)
    problems = [f"claim {c['id']} names blueprint declaration {c['decl']}, which checkdecls reports missing"
                for c in claims if c["decl"] in in_bp and c["decl"] in gone]
    decls = {c["decl"] for c in claims}
    return {"file": rel, "present": True, "checked": sum(c["decl"] in in_bp for c in claims),
            "problems": problems,
            "outside_blueprint": [c["id"] for c in claims if c["decl"] not in in_bp],
            "blueprint_without_claim": [n for n in names if n not in decls]}


def skipped(name: str, commit: str, decls_rel: str, why: str) -> tuple[list[str], dict, int]:
    payload = {"ok": True, "name": name, "commit": commit, "skipped": True, "reason": why,
               "decls_file": decls_rel, "declarations": 0, "missing": [], "checkdecls": None, "claims": None}
    return [f"{name}: blueprint check: skipped ({why})"], payload, 0


def do_blueprint_check(reg: dict, name: str, decls_rel: str = DEFAULT_DECLS) -> tuple[list[str], dict, int]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    commit = core.git_head(repo) if repo.is_dir() else None
    if commit is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    decls_rel = safe_rel(decls_rel)
    decls = repo / decls_rel
    if not decls.is_file():
        if decls_rel == DEFAULT_DECLS and not (repo / "blueprint").is_dir():
            return skipped(name, commit, decls_rel, "no blueprint: neither blueprint/ nor a declaration list")
        raise core.Fail(f"{name}: {decls_rel} does not exist; leanblueprint writes it when it builds the "
                        f"blueprint (`leanblueprint web`), or pass --decls PATH")
    names = read_names(decls.read_text(encoding="utf-8", errors="replace"))
    if not names:
        return skipped(name, commit, decls_rel, f"{decls_rel} names no Lean declaration")
    dirty = core.git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise core.Fail(f"{name}: the checkout has uncommitted changes to tracked files; commit or stash them, "
                        f"so the check is about HEAD ({commit[:12]})")
    entry = manifest_entry(repo)
    if entry is None:
        raise core.Fail(how_to_add(name))
    build = run_lake(repo, "build")
    if build.returncode != 0:
        raise core.Fail(f"{name}: `lake build` failed at {commit[:12]} (exit {build.returncode}); checkdecls "
                        f"imports the built package:\n    {tail(build.stdout + build.stderr)}")
    run = run_lake(repo, "exe", "checkdecls", decls_rel)
    missing = parse_missing(run.stdout)
    if run.returncode != 0 and not missing:
        raise core.Fail(f"{name}: `lake exe checkdecls {decls_rel}` failed (exit {run.returncode}) without "
                        f"naming a missing declaration:\n    {tail(run.stdout + run.stderr)}")
    if run.returncode == 0 and missing:
        raise core.Fail(f"{name}: checkdecls exited 0 but reported missing names; refusing the ambiguous run")
    claims = claims_cross_check(pkg, repo, names, missing)
    ok = not missing and not claims["problems"]
    rev = str(entry.get("rev") or "")
    payload = {"ok": ok, "name": name, "commit": commit, "skipped": False, "reason": None,
               "decls_file": decls_rel, "declarations": len(names), "missing": missing,
               "checkdecls": {"rev": rev, "url": entry.get("url"), "pinned": rev == CHECKDECLS_REV},
               "claims": claims}
    head = (f"{name}: blueprint check at {commit[:12]}: " +
            ("OK" if ok else "FAILED") + f"; {len(names)} declaration(s) in {decls_rel}, {len(missing)} missing"
            f"; checkdecls {rev[:12] or '?'}")
    lines = [head] + [f"  missing: {n}" for n in missing]
    if claims["present"]:
        lines.append(f"  claims: {claims['checked']} claim(s) in {claims['file']} name a blueprint declaration")
        lines += [f"  problem: {p}" for p in claims["problems"]]
        if claims["outside_blueprint"]:
            lines.append(f"  note: {len(claims['outside_blueprint'])} claim(s) outside the blueprint: "
                         + ", ".join(claims["outside_blueprint"]))
        if claims["blueprint_without_claim"]:
            lines.append(f"  note: {len(claims['blueprint_without_claim'])} blueprint declaration(s) no claim "
                         f"carries: " + ", ".join(claims["blueprint_without_claim"]))
    else:
        lines.append(f"  claims: no {claims['file']} at HEAD; claim cross-check skipped")
    if rev != CHECKDECLS_REV:
        lines.append(f"  note: checkdecls at {rev[:12] or 'an unknown rev'}, not the documented pin "
                     f"{CHECKDECLS_REV[:12]}")
    return lines, payload, 0 if ok else 1


def cmd_blueprint(args) -> int:
    if getattr(args, "blueprint_cmd", None) != "check":
        raise core.UsageError("usage: lean-pkg blueprint check NAME [--decls PATH]")
    lines, payload, rc = do_blueprint_check(core.load_registry(core.registry_path(args)), args.name, args.decls)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("blueprint", help="blueprint: check (\\lean{} names with checkdecls)")
    add_json(s)
    s.set_defaults(fn=cmd_blueprint, blueprint_cmd=None)
    bsub = s.add_subparsers(dest="blueprint_cmd")
    c = bsub.add_parser("check", help="run checkdecls on the blueprint's declaration list at HEAD and check "
                                      "claims.yaml against it; exit 0 nothing missing / 1; writes nothing")
    c.add_argument("name")
    c.add_argument("--decls", default=DEFAULT_DECLS, metavar="PATH",
                   help=f"the declaration list, relative to the checkout (default {DEFAULT_DECLS}, written by "
                        f"leanblueprint)")
    add_json(c)
    c.set_defaults(fn=cmd_blueprint)
