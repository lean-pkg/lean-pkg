"""GitHub long tail through `gh search repos` and `gh search code` (Lean only).

The term always follows `--`, so a term that starts with a dash is never read
as a flag. Unlicensed hits can be cited, not copied.
"""

from __future__ import annotations

import json

from . import hit, ok, run_cmd, skipped

NAME = "github"
LIMIT = "30"


def runner(argv: list[str], timeout: float = 60):
    return run_cmd(argv, timeout)


def revision() -> str:
    return "live"


def _json(r):
    if r is None or r[0] != 0:
        return None, (r[2].strip().splitlines()[0][:120] if r and r[2].strip() else "failed")
    try:
        data = json.loads(r[1])
    except ValueError:
        return None, "answer is not JSON"
    return (data, None) if isinstance(data, list) else (None, "unexpected answer")


def search(term: str, timeout: float = 30) -> dict:
    repos_r = runner(["gh", "search", "repos", "--language", "Lean", "--limit", LIMIT, "--json",
                      "fullName,description,stargazersCount,license,updatedAt", "--", term], timeout)
    if repos_r is None:
        return skipped("gh not installed")
    code_r = runner(["gh", "search", "code", "--language", "Lean", "--limit", LIMIT, "--json",
                     "repository,path,url", "--", term], timeout)
    repos, repos_err = _json(repos_r)
    code, code_err = _json(code_r)
    if repos is None and code is None:
        return skipped(f"gh search failed: {repos_err}")
    hits = []
    for r in repos or []:
        if isinstance(r, dict) and r.get("fullName"):
            lic = r.get("license") if isinstance(r.get("license"), dict) else {}
            hits.append(hit(r["fullName"], r.get("description"), f"https://github.com/{r['fullName']}",
                            kind="repo", stars=r.get("stargazersCount"), license=lic.get("key")))
    for c in code or []:
        repo = (c.get("repository") or {}).get("nameWithOwner") if isinstance(c, dict) else None
        if repo and c.get("path"):
            hits.append(hit(f"{repo}:{c['path']}", "", c.get("url") or f"https://github.com/{repo}",
                            kind="code"))
    note = None
    if repos is None:
        note = f"repository search failed: {repos_err}"
    elif code is None:
        note = f"code search failed: {code_err}"
    return ok(revision(), hits, note)
