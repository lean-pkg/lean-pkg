# lean-pkg reservoir -- would Reservoir list this package?
"""`lean-pkg reservoir check NAME [--offline]` (#22.2): read-only static checks of the
documented Reservoir listing criteria (docs/faq.md#reservoir): public on GitHub, not a
fork, at least 2 stars, `lake-manifest.json` at the root, an OSI license GitHub can
detect, a Lake package that has not opted out (`reservoir := false`), and the lakefile
metadata Reservoir displays (`description`, `keywords`, `versionTags`). Then, unless
--offline, whether Reservoir's package API already lists it, with the `record` command
to run when it does. Nothing is written; lean-pkg never records the listing by itself.
"""

from __future__ import annotations

import json
import re

from . import cli as core
from . import publish as publish_mod

MIN_STARS = 2
DISPLAY_KEYS = ("description", "keywords", "versionTags")
OPT_OUT_RE = re.compile(r"^\s*reservoir\s*(?::=|=)\s*false\b", re.MULTILINE)
PENDING = "pending promote: "     # #23.3: a GitHub-dependent item that `promote --yes` will satisfy


def lakefile_text(repo) -> tuple[str | None, str]:
    for name in ("lakefile.lean", "lakefile.toml"):
        try:
            return name, (repo / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None, ""


def pre_promote(pkg: dict) -> bool:
    """Not public yet: GitHub-dependent items are reported as pending promote (#23.3)."""
    return pkg.get("status") not in publish_mod.ALREADY_PUBLIC


def github_items(pkg: dict, nwo: str | None) -> list[tuple[str, str]]:
    name, pending = pkg["name"], pre_promote(pkg)
    if not nwo:
        if pending:
            return [("✗", f"{PENDING}repo {pkg.get('repo')!r} is not a GitHub URL yet (Reservoir indexes "
                          f"GitHub repositories); create the GitHub repository, point the registry at it "
                          f"(`lean-pkg update {name} --repo git@github.com:OWNER/REPO.git`), then "
                          f"`lean-pkg promote {name} --yes` makes it public")]
        return [("✗", f"repo {pkg.get('repo')!r} is not on GitHub (Reservoir indexes GitHub repositories)")]
    p = core.gh("repo", "view", nwo, "--json", "visibility,isFork,stargazerCount")
    try:
        info = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        info = None
    if not isinstance(info, dict):
        err = ((p.stderr or p.stdout or "").strip().splitlines() or ["no output"])[0]
        if pending:
            return [("✗", f"{PENDING}gh query for {nwo} failed ({err}); the GitHub repository must exist "
                          f"before `lean-pkg promote {name} --yes` makes it public")]
        return [("WARN", f"gh query for {nwo} failed ({err}); visibility, fork and stars not checked")]
    items = []
    vis = str(info.get("visibility", "")).lower()
    if vis == "public":
        items.append(("✓", f"{nwo} is public"))
    elif pending:
        items.append(("✗", f"{PENDING}{nwo} is {vis or 'of unknown visibility'}; "
                           f"`lean-pkg promote {name} --yes` makes it public (Reservoir lists public "
                           f"repositories)"))
    else:
        items.append(("✗", f"{nwo} is {vis or 'of unknown visibility'} (Reservoir lists public repositories; "
                           f"`lean-pkg promote {name}`)"))
    if "isFork" in info:
        items.append(("✗", f"{nwo} is a fork (a 'Use this template' repository counts as one); Reservoir "
                           f"skips forks") if info["isFork"] else ("✓", "not a fork"))
    if "stargazerCount" in info:
        stars = int(info.get("stargazerCount") or 0)
        items.append(("✓", f"{stars} star(s)") if stars >= MIN_STARS else
                     ("✗", f"{stars} star(s); Reservoir needs at least {MIN_STARS}"))
    return items


def lakefile_items(pkg: dict, repo) -> list[tuple[str, str]]:
    name, text = lakefile_text(repo)
    if name is None:
        return [("✗", "no lakefile.lean or lakefile.toml at the repo root")]
    items = []
    lake_name = publish_mod.lakefile_package_name(repo)
    items.append(("✓", f"{name}: package {lake_name}") if lake_name else
                 ("✗", f"{name}: no package name"))
    if OPT_OUT_RE.search(text):
        items.append(("✗", f"{name} sets reservoir false: the package opted out of Reservoir"))
    missing = [k for k in DISPLAY_KEYS if not re.search(rf"^\s*{k}\s*(?::=|=)", text, re.MULTILINE)]
    if missing:
        items.append(("WARN", f"{name} has no {', '.join(missing)} (what Reservoir displays and searches)"))
    else:
        items.append(("✓", f"{name} sets {', '.join(DISPLAY_KEYS)}"))
    return items


def repo_items(pkg: dict, repo) -> list[tuple[str, str]]:
    items = []
    for rel, why in (("lake-manifest.json", "Reservoir requires a root manifest"),
                     ("lean-toolchain", "Reservoir builds on the declared toolchain")):
        items.append(("✓", f"{rel} tracked at HEAD") if core.tracked_at(repo, rel) else
                     ("✗", f"{rel} not tracked at HEAD ({why})"))
    if any((repo / f).is_file() for f in core.LICENSE_NAMES):
        items += publish_mod.license_items(pkg, repo)
    else:
        items.append(("✗", "no LICENSE/COPYING at the repo root (Reservoir needs a GitHub-detected OSI license)"))
    items.append(toolchain_item(repo))
    tags = publish_mod.tags_at_head(repo)
    items.append(("✓", f"tag at HEAD: {', '.join(tags)}") if tags else
                 ("WARN", "no tag at HEAD (versionTags select the versions Reservoir shows)"))
    return items


def toolchain_item(repo) -> tuple[str, str]:
    """#23.3: Reservoir builds and shows the declared toolchain; a release candidate or a nightly may be
    displayed poorly, so it is a WARN (never a refusal)."""
    try:
        tc = (repo / "lean-toolchain").read_text(encoding="utf-8").strip()
    except OSError:
        return ("WARN", "lean-toolchain unreadable; release check skipped")
    low = tc.lower()
    if "nightly" in low or re.search(r"-rc\d*\b", low):
        kind = "a nightly" if "nightly" in low else "a release candidate"
        return ("WARN", f"lean-toolchain {tc} is {kind}; Reservoir may display it poorly "
                        f"(prefer a release toolchain for the listed tag)")
    return ("✓", f"lean-toolchain {tc} is a release")


def listing_item(pkg: dict, nwo: str | None, offline: bool,
                 pending: bool = False) -> tuple[tuple[str, str], str | None]:
    """(item, the record command to run when listed and not yet recorded). pending: the repository is
    not public yet and promote will make it so (#23.3)."""
    if offline or not nwo:
        return ("WARN", "Reservoir listing not queried" + (" (--offline)" if offline else "")), None
    owner, name = nwo.split("/", 1)
    code = core.reservoir_listing_status(owner, name)
    url = f"https://reservoir.lean-lang.org/@{owner}/{name}"
    if code == 200:
        cmd = None if pkg.get("reservoir") else \
            f"lean-pkg record {pkg['name']} reservoir-listed --field url={url}"
        return ("✓", f"listed on Reservoir: {url}" + ("" if cmd else " (recorded)")), cmd
    if code == 404 and pending:
        return ("WARN", f"{PENDING}not listed on Reservoir ({url} API: 404); Reservoir lists public "
                        f"repositories and {pkg['name']} is {pkg.get('status')}"), None
    if code == 404:
        return ("WARN", f"not listed on Reservoir yet ({url} API: 404); indexing runs periodically"), None
    return ("WARN", f"Reservoir unreachable (HTTP {code}); listing skipped" if code else
            "Reservoir unreachable; listing skipped"), None


def do_reservoir_check(reg: dict, name: str, offline: bool) -> tuple[list[str], int, dict]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir() or core.git_head(repo) is None:
        raise core.Fail(f"{name}: the git checkout {repo} does not exist")
    nwo = core.gh_nwo(pkg.get("repo") or "")
    gh_items = github_items(pkg, nwo)
    items = gh_items + repo_items(pkg, repo) + lakefile_items(pkg, repo)
    listed, cmd = listing_item(pkg, nwo, offline, pending=any(i[1].startswith(PENDING) for i in gh_items))
    items.append(listed)
    eligible = not any(i[0] == "✗" for i in items)
    pending = [label for _m, label in items if label.startswith(PENDING)]
    lines = [f"reservoir check for {name}:"] + [f"  {m}  {label}" for m, label in items]
    lines.append("eligible: Reservoir's listing criteria are met" if eligible else
                 "not eligible: fix every ✗ above")
    if pending:
        others = sum(1 for m, label in items if m == "✗" and not label.startswith(PENDING))
        lines.append(f"pre-promote: {name} is {pkg.get('status')}; {len(pending)} GitHub-dependent item(s) "
                     f"pending promote (`lean-pkg promote {name} --yes`, one-way, makes the repository "
                     f"public, which they need)" + (f"; {others} other ✗ to fix first" if others else ""))
    if cmd:
        lines.append(f"next: {cmd}")
    payload = {"ok": eligible, "name": name, "eligible": eligible,
               "checklist": [{"status": core._MARK_TO_STATUS[m], "label": label} for m, label in items],
               "record": cmd, "pre_promote": pre_promote(pkg), "pending_promote": pending}
    return lines, 0 if eligible else 1, payload


def cmd_reservoir(args) -> int:
    if getattr(args, "reservoir_cmd", None) != "check":
        raise core.UsageError("usage: lean-pkg reservoir check NAME [--offline]")
    lines, rc, payload = do_reservoir_check(core.load_registry(core.registry_path(args)), args.name,
                                            args.offline)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("reservoir", help="Reservoir: check (read-only listing criteria)")
    add_json(s)
    s.set_defaults(fn=cmd_reservoir, reservoir_cmd=None)
    rsub = s.add_subparsers(dest="reservoir_cmd")
    c = rsub.add_parser("check", help="would Reservoir list this package? read-only; exit 0 eligible / 1 not")
    c.add_argument("name")
    c.add_argument("--offline", action="store_true", help="do not query Reservoir's package API")
    add_json(c)
    c.set_defaults(fn=cmd_reservoir)
