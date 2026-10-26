# lean-pkg sites -- compute site profiles: where gates ran, how a receipt shows it, how the page credits it
"""Site profiles (docs/sites.md; design: docs/design/sites-and-ci-receipts.md).

A *site* is a place verification jobs run: a cluster, later a CI service. Its profile is data, a
`site-profile/1` YAML file, never a code path:

    schema: site-profile/1
    name: NAME                  # the coverage.yaml compute[].cluster value; [a-z][a-z0-9-]*, at most 40
    title: TEXT                 # the linked name in the acknowledgement
    url: https://...            # where the title links
    acknowledgement: TEXT       # the sentence the page renders, verbatim; must contain [title](url)
    mention: TEXT               # how a refusal names the site ("the Easley Cluster")
    mention_pattern: REGEX      # README text that claims the site (case-insensitive); must match the
                                # acknowledgement, so a backed page passes its own check
    scheduler: slurm            # the job system whose receipts back the site (SCHEDULERS)
    x-NAME: ANY                 # optional extension keys, tolerated and never read

Where profiles come from, in this order (the order of the Acknowledgments sentences):

1. built-in: `site_profiles/*.yaml` shipped with lean-pkg (Easley is the first);
2. the package's own: `lean-pkg-sites/<name>.yaml` committed at HEAD in the package directory, so the
   page renders the same on every machine and in CI. A package profile may not reuse a built-in name.

A *scheduler* is code, not data: it is how a committed receipt shows a job ran (`job_re`, the receipt
`job_id` spelling; coverage.JOB_INDEX, its reader), and from which corroboration level a site of it renders
its sentence (`sentence_level`). Slurm: `slurm-<J>`, which `receipt slurm` writes only after sacct shows the
job COMPLETED, or a comparator receipt whose Slurm claim (job_id J, host_policy `Slurm job J ...`) carries a
valid sacct seal (`comparator-receipt --seal-sacct`); sentence level 1. GitHub Actions (proposal 0008):
`gha-<run_id>-<run_attempt>`, a lower-tier receipt whose additive `ci` block passes level 1 (ci_github.py);
sentence level 2, so the page carries its sentence only once the run is corroborated, never offline. A
built-in-only scheduler (`builtin_only`) may be named by a built-in profile alone: no package writes its own
wording for its evidence sentence. The acknowledgement token `{runs}` (filled with the corroborated runs'
links) is allowed only in a built-in profile whose scheduler has sentence_level > 1.

The rule every profile carries (unchanged from the Easley-only code it replaced): a site's sentence
appears only when coverage.yaml lists the site with job ids and every listed id is the job of a receipt of
the site's scheduler committed at HEAD; a README that mentions the site without that backing fails `page
--check`. Receipts carry no site identity (a Slurm job number alone does not name a cluster), so the
declaration is what names the site and the receipt is what shows a job of that scheduler ran.
"""

from __future__ import annotations

import functools
import pathlib
import re
from dataclasses import dataclass

import yaml

SCHEMA = "site-profile/1"
KEYS = ("schema", "name", "title", "url", "acknowledgement", "mention", "mention_pattern", "scheduler")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
EXTENSION_RE = re.compile(r"^x-[a-z][a-z0-9-]*$")
HTTPS_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9._~/%+-]*$")
BUILTIN_DIR = pathlib.Path(__file__).resolve().parent / "site_profiles"
PACKAGE_DIR = "lean-pkg-sites"          # package-relative; profiles committed at HEAD
LIMITS = {"title": 120, "acknowledgement": 400, "mention": 80, "mention_pattern": 200}


@dataclass(frozen=True)
class Scheduler:
    name: str
    label: str
    job_re: re.Pattern
    example: str
    placeholder: str
    sentence_level: int = 1             # the corroboration level from which a site of it renders its sentence
    builtin_only: bool = False          # only a built-in profile may name it


@dataclass(frozen=True)
class Site:
    name: str
    title: str
    url: str
    acknowledgement: str
    mention: str
    mention_re: re.Pattern
    scheduler: str
    source: str                         # "built-in" or the package-relative path of its file


SLURM = Scheduler(name="slurm", label="Slurm", job_re=re.compile(r"^slurm-\d+(?:_\d+)?$"),
                  example="slurm-12345", placeholder="slurm-J")
GITHUB_ACTIONS = Scheduler(name="github-actions", label="GitHub Actions", job_re=re.compile(r"^gha-\d+-\d+$"),
                           example="gha-123456789-1", placeholder="gha-R-A", sentence_level=2, builtin_only=True)
SCHEDULERS: dict[str, Scheduler] = {SLURM.name: SLURM, GITHUB_ACTIONS.name: GITHUB_ACTIONS}
RUNS_TOKEN = "{runs}"                   # in an acknowledgement: the corroborated runs' links (sentence_level > 1)


class _StrictLoader(yaml.SafeLoader):
    """safe_load, but a mapping that repeats a key is an error."""


def _no_duplicates(loader, node, deep=False):
    keys = [loader.construct_object(k, deep=deep) for k, _v in node.value]
    dup = sorted({str(k) for k in keys if keys.count(k) > 1})
    if dup:
        raise yaml.constructor.ConstructorError(None, None, f"duplicate key {dup[0]!r}", node.start_mark)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


def parse(text: str, where: str, source: str) -> tuple[Site | None, list[str]]:
    """(profile, []) for a valid site-profile/1 document, else (None, errors), each prefixed by where."""
    try:
        doc = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        return None, [f"{where}: not YAML ({str(e).splitlines()[0]})"]
    if not isinstance(doc, dict):
        return None, [f"{where}: must be a mapping with keys {', '.join(KEYS)}"]
    errors = [f"{where}: missing key {k}" for k in KEYS if k not in doc]
    # `x-NAME` keys are an extension namespace: tolerated, never read (an operator's own data, e.g. a lab's
    # scheduler options); every other unknown key is refused
    errors += [f"{where}: unknown key {k!r}" for k in doc
               if k not in KEYS and not (isinstance(k, str) and EXTENSION_RE.match(k))]
    if errors:
        return None, errors
    bad = [k for k in KEYS if not isinstance(doc[k], str) or not doc[k].strip() or "\n" in doc[k]]
    if bad:
        return None, [f"{where}: {k}: must be one non-empty line of text" for k in bad]
    if doc["schema"] != SCHEMA:
        errors.append(f"{where}: schema: must be {SCHEMA}, got {doc['schema']!r}")
    if not NAME_RE.match(doc["name"]):
        errors.append(f"{where}: name: must match {NAME_RE.pattern}, got {doc['name']!r}")
    for k, n in LIMITS.items():
        if len(doc[k]) > n:
            errors.append(f"{where}: {k}: at most {n} characters")
    if not HTTPS_URL_RE.match(doc["url"]):
        errors.append(f"{where}: url: must be an https URL")
    if any(c in doc["acknowledgement"] for c in "<>`|"):
        errors.append(f"{where}: acknowledgement: may not contain < > ` or | (it is rendered as Markdown)")
    if f"[{doc['title']}]({doc['url']})" not in doc["acknowledgement"]:
        errors.append(f"{where}: acknowledgement: must contain the link [{doc['title']}]({doc['url']})")
    sched = SCHEDULERS.get(doc["scheduler"])
    if sched is None:
        errors.append(f"{where}: scheduler: must be one of {', '.join(SCHEDULERS)}, got {doc['scheduler']!r}")
    elif sched.builtin_only and source != "built-in":
        errors.append(f"{where}: scheduler: {sched.name} is built-in only (the built-in {sched.name} profile "
                      f"words its evidence sentence; a package profile may not)")
    if RUNS_TOKEN in doc["acknowledgement"] and not (
            source == "built-in" and sched is not None and sched.sentence_level > 1):
        errors.append(f"{where}: acknowledgement: the token {RUNS_TOKEN} is allowed only in a built-in profile "
                      f"whose scheduler renders its sentence from a corroborated run (sentence level > 1)")
    try:
        mention_re = re.compile(doc["mention_pattern"], re.I)
    except re.error as e:
        mention_re = None
        errors.append(f"{where}: mention_pattern: not a regular expression ({e})")
    if mention_re is not None and not mention_re.search(doc["acknowledgement"]):
        errors.append(f"{where}: mention_pattern: must match the acknowledgement (a backed page must pass "
                      f"its own check)")
    if errors:
        return None, errors
    return Site(name=doc["name"], title=doc["title"], url=doc["url"], acknowledgement=doc["acknowledgement"],
                mention=doc["mention"], mention_re=mention_re, scheduler=doc["scheduler"], source=source), []


@functools.lru_cache(maxsize=1)
def _builtin() -> tuple[Site, ...]:
    out = []
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        site, errors = parse(path.read_text(encoding="utf-8"), f"built-in site profile {path.name}", "built-in")
        if errors:
            raise RuntimeError("; ".join(errors))           # a packaging defect, caught by the tests
        if site.name != path.stem:
            raise RuntimeError(f"built-in site profile {path.name}: name {site.name!r} is not the file name")
        out.append(site)
    return tuple(out)


def builtin() -> dict[str, Site]:
    """The built-in profiles by name, in file-name order."""
    return {s.name: s for s in _builtin()}


def names() -> tuple[str, ...]:
    """The built-in profile names (the accepted compute[].cluster values of a package with none of its own)."""
    return tuple(builtin())


def package_profiles(repo: pathlib.Path) -> tuple[dict[str, Site], list[str]]:
    """({name: profile}, errors) for the package's own profiles, `lean-pkg-sites/*.yaml` committed at HEAD."""
    from . import cli as core
    p = core.git(repo, "ls-tree", "-z", "--name-only", "HEAD", "--", f"{PACKAGE_DIR}/")
    rels = sorted(x for x in (p.stdout.split("\0") if p.returncode == 0 else []) if x)
    out: dict[str, Site] = {}
    errors: list[str] = []
    taken = builtin()
    for rel in rels:
        if not rel.endswith(".yaml") or rel.count("/") != 1:
            errors.append(f"{rel}: {PACKAGE_DIR}/ holds only <name>.yaml site profiles")
            continue
        raw = core.git_show_bytes(repo, rel)
        site, errs = parse((raw or b"").decode("utf-8", errors="replace"), f"{rel} at HEAD", rel)
        if errs:
            errors += errs
        elif site.name != pathlib.PurePosixPath(rel).stem:
            errors.append(f"{rel} at HEAD: name {site.name!r} is not the file name")
        elif site.name in taken:
            errors.append(f"{rel} at HEAD: {site.name!r} is a built-in site profile; a package may not redefine "
                          f"it (its acknowledgement is the site's own wording)")
        else:
            out[site.name] = site
    return out, errors


def for_repo(repo: pathlib.Path | None) -> dict[str, Site]:
    """Every profile a package can name: built-in, then its own; a malformed own profile refuses."""
    profiles = builtin()
    if repo is None:
        return profiles
    own, errors = package_profiles(repo)
    if errors:
        from . import cli as core
        raise core.Fail(f"{len(errors)} site profile problem(s) (docs/sites.md):\n  " + "\n  ".join(errors))
    return {**profiles, **own}


def scheduler_of(site: Site) -> Scheduler:
    return SCHEDULERS[site.scheduler]
