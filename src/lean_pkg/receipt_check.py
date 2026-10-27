# lean-pkg receipt check -- validate a published package's receipts from git alone
"""`lean-pkg receipt check --repo DIR --commit X [--at REF] [--docs-descendants | --evidence-only] [--name NAME]
[--corroborate [--github OWNER/REPO]] [--json]`:
the registry-free receipt verifier an outside referee runs on a clone of a published package. It needs no
lean-pkg registry, no lab account and no network, and it never runs Lean: everything is read from git, at the
bound commit X and at the published ref REF (default: the checkout's HEAD), in a private clone of DIR (the
referee's checkout is never modified; its working tree and untracked files are never read).

Re-deriving a run (rebuilding at X, `#print axioms`, re-running the comparator) is a separate step: steps 4
and 5 of docs/trust-model.md#re-verify-a-page-yourself. This command checks that the published evidence is
consistent with itself and with the committed tree, nothing more.

Checks, each with a verdict pass | fail | skip (skip: the check does not apply, and says why):

- receipts: the JSON files tracked directly under .lean-receipts/ at REF; those whose `commit` is X are the
  ones checked (a comparator receipt has a comparator-receipt/N schema; a lower-tier receipt has sorry_count
  and axioms). At least one lower-tier receipt must bind X; a receipt file named for X that binds another
  commit fails. A comparator `.replaced-` copy is history and is skipped. A JSON file named for X that is not
  receipt-shaped fails, unless its `schema` is one of the comparator runner's own non-receipt records
  (NON_RECEIPT_SCHEMAS: stages.json and its kin), which is skipped and printed as skipped.
- opt-in: lean-pkg-binding.yaml in the package directory as committed at X (proposal 0002), the author's
  docs-only opt-in, read at X and nowhere else (never at REF, never in the referee's working tree). Pass
  when it opts in (docs_descendants: true) and is applied; skip, saying why, when it says false, does not
  validate (the problem is named), or is ignored with --evidence-only; skip when it is absent at X but
  committed at REF (added after X: it grants nothing). No row when no file is at X or at REF.
- binding: REF is X, or an evidence-only descendant of X (only .lean-receipts/ changed, and formalization.yaml
  at the package root, the renderer's default path, in its derived keys only: gate's own exemption, #23.5,
  applied to the fixed path because the registry pointer is not readable here; the exemption is printed when
  it was needed), or a docs-only descendant of X (no build input changed: the docs-only rule of
  docs/receipts.md) when the docs-only rule applies: by default when lean-pkg-binding.yaml at X opts in;
  with --docs-descendants, for a package without the file, as the referee's choice (printed with the
  result; a file at X that says false or does not validate overrides the flag); never with --evidence-only.
- per lower-tier receipt: gate's own validator (gate_mod.validate_receipt: the receipt keys, the binding, the
  toolchain and Mathlib pins against lean-toolchain and lake-manifest.json committed at X, no path dependency
  outside the package, sorry_count 0, axioms within propext, Quot.sound, Classical.choice); report_sha256
  against the committed redacted log (gate_mod.report_log_check; a missing log fails here); the additive evidence
  keys when present (manifest_sha256 against lake-manifest.json at X, artifacts_manifest_sha256 and
  unit_properties_sha256 against their committed files; job_stdout_sha256 is not checkable: the job's stdout
  is not published); the `container` key when present (#236: shown with its engine, digest and image ref;
  a malformed one fails; the digest itself is not re-derivable, the image is not published); the
  `slurm_sacct` block when present (`receipt slurm --seal-sacct`): the validator refuses it unless the sealed
  sacct record is committed at REF and still hashes and parses to the block (receipt.sacct_seal_problem, the
  check of a comparator receipt's seal), and a row names the job, the window and the record; the `ci` block
  when present (a GitHub Actions run, proposal 0008): the validator refuses it unless it passes level 1
  (ci_github.level1: offline consistency with the receipt, the commit and the committed log), and a row names
  the run, with a second row for the workflow-file rehash (a skip when ci.workflow_sha is not in the clone).
  A receipt with a gha- job_id and no `ci` block gets a skip row: it gates, but backs no compute job.
- per lower-tier receipt with the additive `claim_axioms` key (AX1): re-derived from the sealed log's
  claim-axioms lines against claims.yaml at X (every claim checked, none missing, no sorryAx,
  agreeing with the log's `#print axioms` output) and compared with the key.
- claims: claims.yaml at X, with the claims/1 schema and declaration check gate runs (gate_mod.claims_schema_at).
- per comparator receipt: gate's validator (gate_mod.validate_comparator: schema, binding, toolchain, the exact
  set of configurations tracked at X, each configuration's sha256 and fields, the sign-off of definition
  holes, each log's sha256 and its PASS line, the same commit as the lower-tier receipt); the olean manifest
  record when present (its lean_toolchain and lake_manifest_sha256 against X; the manifest file itself
  describes the author's build directory and is not published).
- claimed theorems: every theorem or lemma claim with status reusable in claims.yaml at X is named by a
  comparator configuration tracked at X (the rule `comparator config --coverage` applies): by its explicit
  `judged_as` when set (that exact theorem name, and nothing else), else by the naming rule (the decl, or
  <Prefix>.<decl>). Any `judged_as` that names no configured theorem, or whose claim's decl is not declared in
  its file, fails, whatever the claim's status. claims.yaml is bound to X like the sources (a change after X is
  neither evidence-only nor docs-only), so the mapping is read where the claims are.
- coverage: coverage.yaml at REF, when tracked, passes `coverage check` (schema, claim ids, backed compute
  jobs, R8 to R12: every theorem the comparator receipt compares is the declaration of a claim a row names).
- page: the README.md verification block at REF, when present, states what the receipts say: the bound
  commit, the toolchain and Mathlib pins, every Evidence row (path, commit, SHA-256 of the committed file), the
  lower-tier sorry count and axioms, the comparator's configuration and theorem counts.

--corroborate (proposal 0008, level 2) is the one networked check: every lower-tier receipt with a `ci` block
that passes level 1 is corroborated against the public GitHub REST API (ci_github.corroborate), one row per
level-2 check, and fails when it does not reach level 2 (a 404, a 403 or 429 naming GH_TOKEN, a network error
and a redirect off api.github.com each fail closed, named). The package's own repository is --github
OWNER/REPO; without it, the receipt's ci.repository, and the output says so. A token from GH_TOKEN or
GITHUB_TOKEN only raises the rate limit; it is never printed or written. The JSON gains `ci: {level, rows}`
(level: the lowest a corroborated receipt reached; null when none has a `ci` block), `github` and
`network: true`.

Exit 0 only when no check fails; 1 otherwise; 2 on a usage error (DIR is not a git checkout, X or REF does
not resolve, --github without --corroborate or not OWNER/REPO).
"""

from __future__ import annotations

import json
import pathlib
import re
import tempfile

from . import claim_axioms as ca
from .binding_file import FILE as BINDING_FILE, referee_opt_in
from . import gate as gate_mod
from . import core
from . import container as ctr
from . import toolchain_advisories

RECEIPTS_DIR = ".lean-receipts"
DEFAULT_AXIOMS = tuple(core.DEFAULT_AXIOMS)
RERUN_NOTE = ("this command never runs Lean and never uses the network: rebuilding at the commit and re-running "
              "the comparator are steps 4 and 5 of docs/trust-model.md#re-verify-a-page-yourself")
# Additive lower-tier keys that bind a file `receipt slurm|systemd` writes beside the receipt as
# <c12>-<job slug>-<suffix>: the key's value is the file's sha256.
EVIDENCE_FILE_KEYS = {"artifacts_manifest_sha256": "artifacts.sha256",
                      "unit_properties_sha256": "unit.properties"}
PASS, FAIL, SKIP = "pass", "fail", "skip"
CORROBORATE_NOTE = ("this command never runs Lean; with --corroborate it asks the public GitHub REST API (level 2) "
                    "and nothing else on the network: rebuilding at the commit and re-running the comparator are "
                    "steps 4 and 5 of docs/trust-model.md#re-verify-a-page-yourself")
REPO_DEFAULT = ("no --github: the package repository is taken to be each receipt's ci.repository (pass --github "
                "OWNER/REPO to name it; a run in another repository, a fork included, then does not back)")
REPO_NOTE = "the package repository is {where}; a run in another repository (by numeric id) does not back"
# The one formalization.yaml path the evidence-only rule exempts without a registry: the renderer's default
# (formalization.DEFAULT_PATH), package-relative. A package whose registry records another path keeps the
# strict reading here (or the referee's --docs-descendants).
FORMALIZATION_REL = "formalization.yaml"
# JSON records the comparator runner writes beside its receipt (comparator_receipt, comparator_stage). One of
# these under .lean-receipts/ named for X is not a receipt: it is skipped (and printed), never failed. Only an
# exact `schema` value skips a file, and only a file that is not receipt-shaped (tier_of None): a truncated or
# hand-edited receipt has no such schema and still fails as unreadable.
NON_RECEIPT_SCHEMAS = ("comparator-stages/1", "olean-manifest/1", "comparator-attempt/1",
                       "comparator-partial/1", "comparator-slot/1", "comparator-estimate/2")


# --------------------------------------------------------------------------
# the source: a private clone of the referee's checkout, at REF

def _resolve(repo: pathlib.Path, rev: str, what: str) -> str:
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if p.returncode != 0 or not core._HEX40.fullmatch(p.stdout.strip()):
        raise core.UsageError(f"{what} {rev!r} is not a commit in {repo}")
    return p.stdout.strip()


def open_source(repo_dir: str, commit: str, at: str | None, tmp: pathlib.Path) -> dict:
    """{clone (package directory inside the clone), commit, at, prefix}: DIR cloned (local transport only, no
    other protocol allowed) and checked out at REF, detached, symlinks as plain files."""
    src = pathlib.Path(repo_dir).expanduser()
    if not src.is_dir():
        raise core.UsageError(f"--repo {repo_dir}: not a directory (a local clone of the package; this command "
                              f"never fetches a URL)")
    top = core.git(src, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise core.UsageError(f"--repo {repo_dir}: not a git checkout")
    prefix = core.git(src, "rev-parse", "--show-prefix").stdout.strip().strip("/")
    x = _resolve(src, commit, "--commit")
    ref = _resolve(src, at or "HEAD", "--at")
    dest = tmp / "pkg"
    p = core.run(["git", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always", "clone", "--quiet",
                  "--no-checkout", "--no-hardlinks", top.stdout.strip(), str(dest)], scrub=True)
    if p.returncode != 0:
        raise core.Fail(f"cannot clone {repo_dir}: {p.stderr.strip()[:300]}")
    for sha in (x, ref):   # a detached HEAD or a commit on no branch is not cloned: fetch it locally
        if core.git(dest, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            f = core.run(["git", "-C", str(dest), "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
                          "fetch", "--quiet", "--no-tags", top.stdout.strip(), sha], scrub=True)
            if f.returncode != 0:
                raise core.Fail(f"cannot copy commit {sha[:12]} from {repo_dir}: {f.stderr.strip()[:200]}")
    p = core.git(dest, "-c", "core.symlinks=false", "checkout", "--quiet", "--detach", ref)
    if p.returncode != 0:
        raise core.Fail(f"cannot check out {ref[:12]} in the private clone: {p.stderr.strip()[:200]}")
    clone = dest / prefix if prefix else dest
    if not clone.is_dir():
        raise core.Fail(f"the package directory {prefix}/ does not exist at {ref[:12]}")
    return {"clone": clone, "root": dest, "commit": x, "at": ref, "prefix": prefix}


def referee_pkg(name: str, docs_descendants: bool, comparator_rel: str | None = None,
                evidence_only: bool = False) -> dict:
    """The package record the validators read, built from nothing but the referee's choices: the default
    axioms, the default claims file, the default formalization.yaml path (FORMALIZATION_REL; gate's
    derived-keys rule decides from the file at X and at REF, and a file not tracked at X is never exempt).
    The docs-only opt-in is lean-pkg-binding.yaml at X, which every validator reads through the binding rule
    (binding_file.resolve_opt_in); --docs-descendants stands in for the record's docs_descendants: true (it
    applies only without a file at X), --evidence-only for its false (the veto: the file is not applied)."""
    pkg = {"name": name, "gate": {"axioms_allowed": list(DEFAULT_AXIOMS), "comparator": comparator_rel},
           "formalization_yaml": FORMALIZATION_REL}
    if docs_descendants:
        pkg["docs_descendants"] = True
    elif evidence_only:
        pkg["docs_descendants"] = False
    return pkg


# --------------------------------------------------------------------------
# reading at REF and at X

def _json_at(repo: pathlib.Path, rel: str, rev: str = "HEAD") -> tuple[dict | None, bytes | None]:
    raw = core.git_show_bytes(repo, rel, rev)
    if raw is None:
        return None, None
    try:
        data = json.loads(raw)
    except ValueError:
        return None, raw
    return (data if isinstance(data, dict) else None), raw


def tier_of(rec: dict | None) -> str | None:
    if not isinstance(rec, dict):
        return None
    if rec.get("schema") in core.COMPARATOR_SCHEMAS:
        return "comparator"
    if "sorry_count" in rec and "axioms" in rec:
        return "lower"
    return None


def discover(repo: pathlib.Path, x: str) -> dict:
    """{lower: [view], comparator: [view], other: [view], misnamed: [view], unreadable: [rel], sidecar:
    [{path, schema}]} for the receipt JSON files tracked directly under .lean-receipts/ at HEAD (REF). view:
    {path, sha256, commit, tier, data}. sidecar: files named for X that are a NON_RECEIPT_SCHEMAS record."""
    from . import coverage as cov
    out: dict = {"lower": [], "comparator": [], "other": [], "misnamed": [], "unreadable": [], "sidecar": []}
    for rel in cov.tracked_receipts(repo):
        name = rel.rsplit("/", 1)[-1]
        if ".replaced-" in name:          # `comparator --replace` keeps the older receipt beside it: history
            continue
        data, raw = _json_at(repo, rel)
        tier = tier_of(data)
        if raw is None or tier is None:
            if data is not None and tier is None and data.get("schema") in NON_RECEIPT_SCHEMAS:
                if name.startswith(x[:12]):
                    out["sidecar"].append({"path": rel, "schema": data["schema"]})
                continue
            if name.startswith(x[:12]):
                out["unreadable"].append(rel)
            continue
        view = {"path": rel, "sha256": core.sha256_bytes(raw), "commit": str(data.get("commit") or ""),
                "tier": tier, "data": data}
        if view["commit"] == x:
            out[tier].append(view)
        elif name.startswith(x[:12]):
            out["misnamed"].append(view)
        else:
            out["other"].append(view)
    return out


def _sha_at(repo: pathlib.Path, rel: str, rev: str) -> str | None:
    raw = core.git_show_bytes(repo, rel, rev)
    return core.sha256_bytes(raw) if raw is not None else None


def toolchain_at(repo: pathlib.Path, rev: str) -> str | None:
    raw = core.git_show_bytes(repo, "./lean-toolchain", rev)
    return raw.decode("utf-8", "replace").strip() if raw is not None else None


# --------------------------------------------------------------------------
# the checks

class Checks:
    def __init__(self, clone: pathlib.Path | None = None, root: pathlib.Path | None = None) -> None:
        self.items: list[dict] = []
        self.exemptions: list[str] = []   # the binding exemptions the verdict needed (formalization.yaml)
        # The private clone lives in a temporary directory that is gone when the command ends: a validator's
        # message that names a path inside it (report_log_missing names where it looked) is rewritten to the
        # package-relative path, so the output shows no temporary path (#213).
        self._paths: list[tuple[str, str]] = []
        for base, label in ((clone, "."), (root, "<repository root>")):
            if base is None:
                continue
            for form in dict.fromkeys((str(base.absolute()), str(base.resolve()))):
                self._paths.append((form, label))

    def relative(self, text: str) -> str:
        for form, label in self._paths:
            text = text.replace(form + "/", "" if label == "." else label + "/").replace(form, label)
        return text

    def add(self, cid: str, verdict: str, detail: str) -> None:
        self.items.append({"id": cid, "verdict": verdict, "detail": self.relative(detail)})

    def run(self, cid: str, fn, ok_detail: str) -> bool:
        """Run fn(); pass with ok_detail, or fail with the refusal's text."""
        try:
            fn()
        except (core.Fail, ValueError, KeyError, TypeError) as e:
            self.add(cid, FAIL, str(e))
            return False
        self.add(cid, PASS, ok_detail)
        return True

    def failed(self) -> int:
        return sum(i["verdict"] == FAIL for i in self.items)


def opt_in_check(c: Checks, repo: pathlib.Path, x: str, ref: str, oi) -> None:
    """The `opt-in` row: lean-pkg-binding.yaml as committed at X, the only copy that can opt in (proposal
    0002). No row when the file is neither at X nor at REF, so a package without it reads as before."""
    x12 = x[:12]
    if oi.file_state == "absent":
        if x != ref and core.tracked_at(repo, BINDING_FILE, ref):
            c.add("opt-in", SKIP, f"no {BINDING_FILE} at {x12}; the one at {ref[:12]} was added after {x12}, and "
                                  f"the opt-in is read at the bound commit only, so it grants nothing")
        return
    if oi.file_state == "true":
        what = (f"{BINDING_FILE} at {x12} opts in to docs-only descendants (build_inputs: "
                f"{len(oi.build_inputs)} glob(s))")
        if oi.rule == "docs-only":
            c.add("opt-in", PASS, what)
        else:
            c.add("opt-in", SKIP, f"{what}; ignored: --evidence-only")
        return
    why = (f"{BINDING_FILE} at {x12} says docs_descendants: false" if oi.file_state == "false" else
           f"{BINDING_FILE} at {x12} does not opt in: " + "; ".join(pr.split(": ", 1)[-1] for pr in oi.problems))
    over = [n for n in oi.notes if n.startswith("--docs-descendants")]
    c.add("opt-in", SKIP, f"{why}; the evidence-only rule binds" + "".join(f"; {n}" for n in over))


def check_binding(c: Checks, repo: pathlib.Path, x: str, ref: str, pkg: dict, checker, oi) -> None:
    ok, note, docs = gate_mod.package_descendant(repo, x, ref, pkg, checker=checker, nested=(), opt_in=oi)
    if ok:
        what = ("the published ref is the bound commit" if x == ref
                else f"{note} (accepted with --docs-descendants)" if docs is not None and oi.source == "referee"
                else note)
        exempt = formalization_exemption(repo, x, ref, pkg)
        if exempt:
            what += f"; exemption applied: {exempt}"
            c.exemptions.append(exempt)
        c.add("binding", PASS, what)
    else:
        c.add("binding", FAIL, f"{ref[:12]} is not {x[:12]} or an evidence-only"
              + (" or docs-only" if oi.rule == "docs-only" else "") + f" descendant of it: {note}; the "
              f"receipts certify {x[:12]}, not this tree (check the tree they bind with --at {x[:12]}, or an "
              f"evidence commit after it)")


def formalization_exemption(repo: pathlib.Path, x: str, ref: str, pkg: dict) -> str | None:
    """The exemption text when the binding passed although formalization.yaml differs between X and REF (gate's
    derived-keys rule admitted it); None when that file did not change."""
    rel = core.formalization_rel(pkg)
    if not rel or x == ref:
        return None
    p = core.git(repo, "diff", "--no-renames", "--name-only", "--relative", x, ref, "--", rel)
    if rel not in p.stdout.splitlines():
        return None
    return (f"{rel} differs from {x[:12]} only in derived keys ({', '.join(fz_derived_keys())}), the keys "
            f"`lean-pkg formalization` regenerates after a receipt; they cannot change what was verified "
            f"(gate's evidence-only rule, #23.5)")


def fz_derived_keys() -> tuple[str, ...]:
    from .formalization import DERIVED_KEYS
    return DERIVED_KEYS


def evidence_file_checks(c: Checks, repo: pathlib.Path, view: dict, x: str) -> None:
    """The additive keys of a lower-tier receipt that bind another file by its sha256."""
    rec, rel = view["data"], view["path"]
    cid = f"lower {rel}: evidence files"
    problems, passed, skipped = [], [], []
    if rec.get("manifest_sha256"):
        got = _sha_at(repo, "./lake-manifest.json", x)
        if got != str(rec["manifest_sha256"]).lower():
            problems.append(f"manifest_sha256 {str(rec['manifest_sha256'])[:12]} != sha256 of lake-manifest.json "
                            f"at {x[:12]} ({(got or 'not tracked')[:12]})")
        else:
            passed.append("manifest_sha256 = lake-manifest.json at X")
    try:
        slug = gate_mod.receipt_job_slug(rec.get("job_id"))
    except core.Fail:
        slug = None
    for key, suffix in EVIDENCE_FILE_KEYS.items():
        if not rec.get(key):
            continue
        frel = f"{RECEIPTS_DIR}/{x[:12]}-{slug}-{suffix}"
        got = _sha_at(repo, frel, "HEAD") if slug else None
        if got is None:
            skipped.append(f"{key}: {frel} is not committed")
        elif got != str(rec[key]).lower():
            problems.append(f"{key} {str(rec[key])[:12]} != sha256 of {frel} ({got[:12]})")
        else:
            passed.append(f"{key} = {frel}")
    if rec.get("job_stdout_sha256"):
        skipped.append("job_stdout_sha256: the job's stdout is not published")
    if problems:
        c.add(cid, FAIL, "; ".join(problems))
    elif passed:
        c.add(cid, PASS, "; ".join(passed + skipped))
    else:
        c.add(cid, SKIP, "; ".join(skipped) or "no additive evidence key (manifest_sha256, "
              "artifacts_manifest_sha256, unit_properties_sha256)")


def sacct_seal_check(c: Checks, view: dict) -> None:
    """The additive `slurm_sacct` block (`receipt slurm --seal-sacct`), when present: shown. The validator
    (gate_mod.validate_receipt, run first) already refused a block whose record is missing, uncommitted at the
    published ref, rehashed or edited (receipt.sacct_seal_problem); this row names what it corroborated."""
    rec, rel = view["data"], view["path"]
    if "slurm_sacct" not in rec:
        return
    c.add(f"lower {rel}: sacct seal", PASS, gate_mod.sacct_seal_lines(rec)[0][2:].replace("at HEAD", "at the ref"))


def ci_check(c: Checks, repo: pathlib.Path, view: dict, tier: str = "lower") -> None:
    """The additive `ci` block (a GitHub Actions run, proposal 0008), at level 1: shown. The validator
    (gate_mod.validate_receipt, run first) already refused a block that fails level 1; this row names the run, and
    the workflow-file row says whether the file was rehashed or why not. `tier`: "lower", or "comparator" for a
    comparator receipt written on a GitHub-hosted runner (proposal 0008, S5; validate_comparator refused a failing
    block). A gha- receipt without the block: a skip row (it backs no GitHub Actions compute job)."""
    from . import ci_github
    rec, rel = view["data"], view["path"]
    if ci_github.KEY not in rec:
        job = rec.get("job_id")
        if isinstance(job, str) and ci_github.JOB_RE.match(job):
            c.add(f"{tier} {rel}: ci", SKIP, f"job_id {job} has no ci block: the receipt is a "
                  f"{'lower-tier' if tier == 'lower' else tier} receipt "
                  f"like any other, but backs no GitHub Actions compute job")
        return
    problems, row = ci_github.level1(rec, repo)
    if problems:        # unreachable after the validator; kept so a row never claims a pass it did not check
        c.add(f"{tier} {rel}: ci", FAIL, "; ".join(problems))
        return
    c.add(f"{tier} {rel}: ci", PASS, ci_github.summary(rec))
    if row is not None:
        c.add(f"{tier} {rel}: ci workflow file", PASS if row[0] == "pass" else SKIP, row[1])


def corroborate_check(c: Checks, repo: pathlib.Path, view: dict, fetch, github: str | None) -> dict | None:
    """--corroborate: level 2 of a receipt whose `ci` block passed level 1 (ci_github.corroborate), one row per
    check, failed when one fails. {path, level, rows}, or None for a receipt without a valid block."""
    from . import ci_github
    rec, rel = view["data"], view["path"]
    if ci_github.KEY not in rec or ci_github.level1(rec, repo)[0]:
        return None
    level, rows = ci_github.corroborate(rec, repo, fetch=fetch, receipt_sha256=view["sha256"], expect_repo=github)
    for r in rows:
        c.add(f"lower {rel}: ci level 2 {r['check']}", PASS if r["verdict"] == "pass" else FAIL, r["detail"])
    c.add(f"lower {rel}: ci level", PASS if level >= 2 else FAIL,
          ci_github.LEVEL2_NOTE if level >= 2 else
          f"level {level}: not corroborated, so the GitHub Actions sentence is not earned (the receipt stays a "
          f"valid level-1 receipt and still backs its compute job)")
    return {"path": rel, "level": level, "rows": rows}


def container_check(c: Checks, view: dict, tier: str = "lower") -> None:
    """The additive `container` key (#236), when present: shown, and failed when malformed. The digest is what
    the job measured; the image is not published, so it is not re-derived here."""
    rec, rel = view["data"], view["path"]
    if "container" not in rec:
        return
    bad = ctr.problems(rec["container"])
    if bad:
        c.add(f"{tier} {rel}: container", FAIL, "; ".join(bad))
    else:
        c.add(f"{tier} {rel}: container", PASS, f"{ctr.describe(rec['container'])}; recorded by the job, not "
              f"re-derivable from git (the image is not published)")


def _sealed_log_text(repo: pathlib.Path, rec: dict) -> str | None:
    """The receipt's redacted verify log as tracked under .lean-receipts/ (its .gz decompressed), or None."""
    import gzip
    try:
        name = gate_mod.report_log_name(rec)
    except (core.Fail, KeyError):
        return None
    for fname, unpack in ((name, False), (name + gate_mod.REPORT_GZ_SUFFIX, True)):
        path = repo / RECEIPTS_DIR / fname
        if path.is_file() and not path.is_symlink():
            try:
                data = path.read_bytes()
                return (gzip.decompress(data) if unpack else data).decode("utf-8", "replace")
            except (OSError, EOFError, gzip.BadGzipFile):
                return None
    return None


def claim_axioms_check(c: Checks, repo: pathlib.Path, view: dict, x: str) -> None:
    """AX1: a receipt's `claim_axioms` key re-derived from its sealed log (whose sha256 the report-log check
    binds) against claims.yaml at X: every claim checked, none missing, no sorryAx, agreeing with
    the log's `#print axioms` output, and equal to the key. A receipt without the key is not checked here."""
    rec, rel = view["data"], view["path"]
    if "claim_axioms" not in rec:
        return
    cid = f"lower {rel}: claim axioms"
    text = _sealed_log_text(repo, rec)
    if text is None:
        c.add(cid, FAIL, "the sealed verify log is not readable here, so claim_axioms cannot be re-derived")
        return
    required, problems = ca.required_claims(core.git_show_bytes(repo, "./claims.yaml", x))
    summary, refusals, _lines = ca.judge(text, required)
    if summary is None:
        c.add(cid, FAIL, "the receipt has claim_axioms but its sealed log has no claim-axioms line")
    elif problems or refusals:
        c.add(cid, FAIL, "; ".join(problems + refusals))
    elif summary != rec["claim_axioms"]:
        c.add(cid, FAIL, f"claim_axioms {rec['claim_axioms']!r} is not what the sealed log gives: {summary!r}")
    else:
        c.add(cid, PASS, f"{summary['checked']} claim(s) read from the built environment, every claim of "
                         f"claims.yaml at {x[:12]} included; re-derived from the sealed log")


def olean_check(c: Checks, repo: pathlib.Path, view: dict, x: str) -> None:
    cid = f"comparator {view['path']}: olean manifest"
    om = view["data"].get("olean_manifest")
    if not isinstance(om, dict):
        c.add(cid, SKIP, "the receipt records no olean_manifest")
        return
    problems = []
    tc = toolchain_at(repo, x)
    if str(om.get("lean_toolchain") or "").strip() != (tc or ""):
        problems.append(f"olean_manifest.lean_toolchain {om.get('lean_toolchain')!r} != lean-toolchain at "
                        f"{x[:12]} {tc!r}")
    if om.get("lake_manifest_sha256") is not None:
        got = _sha_at(repo, "./lake-manifest.json", x)
        if got != str(om["lake_manifest_sha256"]).lower():
            problems.append(f"olean_manifest.lake_manifest_sha256 != sha256 of lake-manifest.json at {x[:12]}")
    if problems:
        c.add(cid, FAIL, "; ".join(problems))
    else:
        c.add(cid, PASS, f"toolchain and lake-manifest.json digests agree with {x[:12]} ({om.get('files')} "
              f"module file(s); the manifest file describes the author's build directory and is not published)")


def claimed_theorems_check(c: Checks, repo: pathlib.Path, pkg: dict, x: str, have_comparator: bool) -> None:
    from . import comparator_config as cc
    from .claims import _reader
    cid = "claimed theorems"
    if not have_comparator:
        c.add(cid, SKIP, "no comparator receipt binds the commit: the claims rest on the lower tier only")
        return
    try:
        claims, source = cc._claims_at({**pkg, "claims": None}, repo, x)
    except core.Fail:
        c.add(cid, SKIP, f"no claims.yaml at {x[:12]}: no claimed theorem to match")
        return
    cls = cc._classify(claims, cc._configs_at(repo, x), cc.qualified_decls(_reader(repo, x), claims),
                       cc._sidecar_at(repo, x), cc.smoke_configs(repo, x, "HEAD"))
    required = [cl for cl in cls["not_covered"] + cls["todo"] if cl.get("status") == "reusable"]
    problems = []
    if cls["judged_as_problems"]:
        problems.append(f"judged_as mapping(s) not backed by the comparator configurations at {x[:12]}: "
                        + "; ".join(cls["judged_as_problems"][:10]))
    if required:
        problems.append(f"reusable theorem/lemma claim(s) named by no comparator configuration at {x[:12]}: "
                        + ", ".join(f"{cl.get('id')} ({cl.get('decl')})" for cl in required[:10]))
    if problems:
        c.add(cid, FAIL, "; ".join(problems))
        return
    rest = len(cls["not_covered"]) + len(cls["todo"])
    mapped = sum(1 for cl in cls["covered"] if cc.judged_name(cl))
    c.add(cid, PASS, f"every reusable theorem/lemma claim of {source} is named by a comparator configuration "
          f"({len(cls['covered'])} covered" + (f", {mapped} by an explicit judged_as" if mapped else "")
          + (f"; {rest} not covered, none of them reusable" if rest else "") + ")")


def coverage_check(c: Checks, repo: pathlib.Path, pkg: dict) -> None:
    from . import coverage as cov
    cid = "coverage"
    if not core.tracked_at(repo, cov.DEFAULT_COVERAGE_FILE):
        c.add(cid, SKIP, f"no {cov.DEFAULT_COVERAGE_FILE} at the published ref")
        return
    try:
        doc, _claims, warnings = cov.load_checked(pkg, repo, None, False)
    except core.Fail as e:
        c.add(cid, FAIL, " ".join(str(e).split()))
        return
    c.add(cid, PASS, f"{cov.DEFAULT_COVERAGE_FILE}: {len(doc['rows'])} row(s); every compared theorem is the "
          f"declaration of a claim a row names (R11)" + (f"; warning: {warnings[0]}" if warnings else ""))


# --------------------------------------------------------------------------
# the page block (README.md at REF)

# Both wordings are read: the page of lean-pkg 0.11 and later (docs/design/page-for-mathematicians.md) and
# the one before it, so a page published with an earlier version is still checked.
PAGE_CHECKOUT_RE = re.compile(r"^git checkout ([0-9a-f]{40})\s+# the commit the (?:gate receipt|build check) binds$",
                              re.M)
PAGE_TOOLCHAIN_RE = re.compile(r"^- Lean toolchain: `([^`]*)`\.$", re.M)
PAGE_MATHLIB_RE = re.compile(r"^- Mathlib: (?:`([0-9a-f]{7,40})` \(lake-manifest\.json\)|not a dependency)", re.M)
PAGE_ROW_RE = re.compile(r"^\| `([^`]+)`( \((?:recorded|used here)\))? \| (lower tier|comparator|build|statement "
                         r"comparison) \| `([0-9a-f]{12})` \|"
                         r"[^|]*\|[^|]*\| `([0-9a-f]{64})` \|$", re.M)
PAGE_LOWER_RES = (
    re.compile(r"^- Build check \(`lean-pkg gate`\) at commit `([0-9a-f]{12})`: `sorry` count ([^;\s]+); axioms "
               r"used: (.*?)(?:; run in a container \(\w+\), (?:SIF SHA-256|OCI digest) `([^`]+)`)?\.$", re.M),
    re.compile(r"^- Lower-tier gate receipt at `([0-9a-f]{12})` records: `sorry` count (\S+) after "
               r"the build; axioms used: (.*?); allowed:", re.M))
PAGE_COMP_RES = (
    re.compile(r"^- Statement comparison \(comparator\) at commit `([0-9a-f]{12})`: (\d+) configuration\(s\), "
               r"(\d+) theorem\(s\) compared", re.M),
    re.compile(r"^- Comparator receipt at `([0-9a-f]{12})`: (\d+) configuration\(s\), (\d+) "
               r"theorem\(s\) compared", re.M))


def _search(patterns, text: str):
    return next((m for m in (p.search(text) for p in patterns) if m), None)


def _names_in(text: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", text))


def page_checks(c: Checks, repo: pathlib.Path, x: str, found: dict) -> None:
    from . import page as page_mod
    raw = core.git_show_bytes(repo, page_mod.README)
    split = page_mod.split_block(raw.decode("utf-8", "replace")) if raw is not None else None
    if not split or split[1] is None:
        c.add("page", SKIP, f"no lean-pkg page block in {page_mod.README} at the published ref")
        return
    block = split[1]
    m = PAGE_CHECKOUT_RE.search(block)
    if not m:
        c.add("page: commit", FAIL, "the page names no bound commit (no `git checkout <commit>` line)")
    elif m.group(1) != x:
        c.add("page: commit", FAIL, f"the page binds {m.group(1)[:12]}, not {x[:12]}")
    else:
        c.add("page: commit", PASS, f"the page's Reproduce section checks out {x[:12]}")
    problems = []
    tc = toolchain_at(repo, x)
    m = PAGE_TOOLCHAIN_RE.search(block)
    if not m or m.group(1) != tc:
        problems.append(f"toolchain {m.group(1) if m else '(none)'!r} != lean-toolchain at {x[:12]} {tc!r}")
    rev = gate_mod.committed_mathlib_rev(repo, x)
    m = PAGE_MATHLIB_RE.search(block)
    if not m or (m.group(1) or None) != (rev or None):
        problems.append(f"Mathlib {(m.group(1) or 'not a dependency') if m else '(none)'} != lake-manifest.json "
                        f"at {x[:12]} ({rev or 'no mathlib'})")
    c.add("page: pins", FAIL if problems else PASS,
          "; ".join(problems) or f"toolchain {tc} and Mathlib {rev or 'none'} as committed at {x[:12]}")
    rows = PAGE_ROW_RE.findall(block)
    problems = []
    listed = set()
    for path, _rec, _tier, c12, sha in rows:
        listed.add(path)
        got = _sha_at(repo, path, "HEAD")
        data, _raw = _json_at(repo, path)
        if got is None:
            problems.append(f"{path} is not committed")
        elif got != sha:
            problems.append(f"{path}: the page says SHA-256 {sha[:12]}, the committed file hashes to {got[:12]}")
        elif not data or str(data.get("commit") or "")[:12] != c12:
            problems.append(f"{path}: the page says commit {c12}, the receipt binds "
                            f"{str((data or {}).get('commit') or '?')[:12]}")
    bound = found["lower"] + found["comparator"]
    problems += [f"{v['path']} binds {x[:12]} but is not in the page's Evidence table"
                 for v in bound if v["path"] not in listed]
    c.add("page: evidence", FAIL if problems else PASS,
          "; ".join(problems) or f"{len(rows)} Evidence row(s): every path, commit and SHA-256 as committed")
    recorded = {path for path, rec, *_ in rows if rec}
    problems = []
    m = _search(PAGE_LOWER_RES, block)
    lowers = [v for v in found["lower"] if v["path"] in recorded] or found["lower"]
    if m:
        axioms = _names_in(m.group(3))
        if m.group(1) != x[:12]:
            problems.append(f"the trust boundary names the lower-tier receipt at {m.group(1)}, not {x[:12]}")
        elif not any(str(v["data"].get("sorry_count")) == m.group(2) and set(map(str, v["data"].get("axioms") or []))
                     == axioms for v in lowers):
            problems.append(f"the trust boundary says sorry count {m.group(2)} and axioms "
                            f"{sorted(axioms)}; no lower-tier receipt bound to {x[:12]} says so")
        elif m.re is PAGE_LOWER_RES[0] and m.group(4) and not any(
                isinstance(v["data"].get("container"), dict) and not ctr.problems(v["data"]["container"])
                and ctr.digest_of(v["data"]["container"])[1] == m.group(4) for v in lowers):
            problems.append(f"the trust boundary names the container image {m.group(4)[:19]}…; no lower-tier "
                            f"receipt bound to {x[:12]} records it")
    elif found["lower"]:
        problems.append("the page has no lower-tier trust-boundary line, yet a lower-tier receipt binds the commit")
    m = _search(PAGE_COMP_RES, block)
    comps = [v for v in found["comparator"] if v["path"] in recorded] or found["comparator"]
    if m:
        if m.group(1) != x[:12]:
            problems.append(f"the trust boundary names the comparator receipt at {m.group(1)}, not {x[:12]}")
        elif not any(len(v["data"].get("configs") or []) == int(m.group(2)) and sum(
                len(cf.get("theorem_names") or []) for cf in v["data"].get("configs") or [] if isinstance(cf, dict))
                == int(m.group(3)) for v in comps):
            problems.append(f"the trust boundary says {m.group(2)} configuration(s) and {m.group(3)} theorem(s) "
                            f"compared; no comparator receipt bound to {x[:12]} says so")
    elif found["comparator"]:
        problems.append("the page says no comparator receipt is committed, yet one binds the commit")
    c.add("page: trust boundary", FAIL if problems else PASS,
          "; ".join(problems) or "sorry count, axioms and comparator counts as the receipts record them")


# --------------------------------------------------------------------------
# the command

def package_name(repo: pathlib.Path, x: str, given: str | None) -> str:
    """--name, else `package` of coverage.yaml at REF, else of claims.yaml at X, else the directory name."""
    import yaml
    if given:
        return given
    for rel, rev in (("coverage.yaml", "HEAD"), ("claims.yaml", x)):
        raw = core.git_show_bytes(repo, rel, rev)
        try:
            doc = yaml.safe_load(raw) if raw is not None else None
        except yaml.YAMLError:
            doc = None
        if isinstance(doc, dict) and isinstance(doc.get("package"), str) and doc["package"].strip():
            return doc["package"].strip()
    return repo.name


def rule_text(oi, x: str, evidence_only: bool = False) -> str:
    """The `rule:` line: which descendants bind, and who chose the docs-only rule. Without a file at X and
    without a flag it reads as before the committed opt-in existed."""
    x12 = x[:12]
    if evidence_only and oi.file_state != "true":
        return "evidence-only descendants (docs-only not accepted: --evidence-only)"
    if oi.source == "committed":
        return (f"evidence-only or docs-only descendants (docs-only: the author's opt-in, {BINDING_FILE} "
                f"committed at {x12}; --evidence-only to refuse them)")
    if oi.source == "referee":
        return "evidence-only or docs-only descendants (docs-only accepted by the referee: --docs-descendants)"
    if oi.file_state == "true":   # applied only when --evidence-only did not ignore it
        return f"evidence-only descendants (docs-only not accepted: --evidence-only ignores {BINDING_FILE} at {x12})"
    if oi.file_state in ("false", "invalid"):
        over = "; --docs-descendants overridden" if any(n.startswith("--docs-descendants") for n in oi.notes) else ""
        return f"evidence-only descendants ({BINDING_FILE} at {x12} does not opt in{over})"
    return "evidence-only descendants (docs-only not accepted; --docs-descendants to accept them)"


def opt_in_view(oi) -> dict | None:
    """JSON `opt_in`: {source (committed | referee | null), commit, path, build_inputs, file (absent | true |
    false | invalid), problems}; null when no file is at X and the evidence-only rule applies."""
    if oi.file_state == "absent" and oi.source is None:
        return None
    return {"source": oi.source, "commit": oi.commit, "path": oi.path, "build_inputs": list(oi.build_inputs),
            "file": oi.file_state, "problems": list(oi.problems)}


def do_check(repo_dir: str, commit: str, at: str | None = None, docs_descendants: bool = False,
             name: str | None = None, evidence_only: bool = False, corroborate: bool = False,
             github: str | None = None, fetch=None) -> tuple[list[str], dict, int]:
    from . import index as index_mod
    if docs_descendants and evidence_only:
        raise core.UsageError("--docs-descendants and --evidence-only are mutually exclusive: --docs-descendants "
                              f"accepts docs-only descendants for a package without {BINDING_FILE}, "
                              "--evidence-only refuses them whatever the package committed")
    from . import ci_github
    if github is not None and not corroborate:
        raise core.UsageError("--github names the package's repository for --corroborate; pass both")
    if github is not None and not ci_github.REPOSITORY_RE.match(github):
        raise core.UsageError(f"--github {github!r}: must be OWNER/REPO")
    corroborated: list[dict] = []
    if corroborate and fetch is None:
        fetch = ci_github.api_fetch()
    with tempfile.TemporaryDirectory(prefix="lean-pkg-receipt-check-") as tmp:
        src = open_source(repo_dir, commit, at, pathlib.Path(tmp))
        repo, x, ref = src["clone"], src["commit"], src["at"]
        pkg_name = package_name(repo, x, name)
        found = discover(repo, x)
        comp_rel = found["comparator"][0]["path"] if found["comparator"] else None
        pkg = referee_pkg(pkg_name, docs_descendants, comp_rel, evidence_only)
        oi = referee_opt_in(repo, x, docs_descendants, evidence_only)
        c = Checks(repo, src["root"])
        bound = len(found["lower"]) + len(found["comparator"])
        if not found["lower"]:
            c.add("receipts", FAIL, f"no lower-tier receipt tracked under {RECEIPTS_DIR}/ at {ref[:12]} binds "
                  f"{x[:12]}" + (f" ({len(found['other'])} receipt(s) bind other commits: "
                                 f"{', '.join(sorted({v['commit'][:12] for v in found['other']}))})"
                                 if found["other"] else ""))
        else:
            c.add("receipts", PASS, f"{len(found['lower'])} lower-tier and {len(found['comparator'])} comparator "
                  f"receipt(s) bind {x[:12]}")
        for v in found["misnamed"]:
            c.add(f"receipt {v['path']}", FAIL, f"named for {x[:12]} but binds {v['commit'][:12] or '?'}")
        for v in found["sidecar"]:
            c.add(f"receipt {v['path']}", SKIP, f"not a receipt: a {v['schema']} record the comparator runner "
                  f"writes (only an exact non-receipt schema is skipped; any other non-receipt JSON named for "
                  f"the commit fails)")
        for rel in found["unreadable"]:
            c.add(f"receipt {rel}", FAIL, "named for the commit but not a readable receipt (not JSON, or neither "
                  "a lower-tier nor a comparator receipt, nor a comparator runner record whose schema is one of "
                  f"{', '.join(NON_RECEIPT_SCHEMAS)}); rename a non-receipt file so it does not start with "
                  f"{x[:12]}, or keep it outside {RECEIPTS_DIR}/")
        checker = found["lower"][0]["data"].get("checker") if found["lower"] else None
        opt_in_check(c, repo, x, ref, oi)
        check_binding(c, repo, x, ref, pkg, checker, oi)
        for v in found["lower"]:
            rec, rel = v["data"], v["path"]
            if c.run(f"lower {rel}", lambda: gate_mod.validate_receipt(pkg, repo, rec),  # noqa: B023  called synchronously, within this iteration
                     f"schema, binding, pins (lean-toolchain, lake-manifest.json at {x[:12]}), sorry_count 0, "
                     f"axioms {sorted(rec.get('axioms') or [])} within {list(DEFAULT_AXIOMS)}"):
                try:
                    line, _view = gate_mod.report_log_check(repo, rec, repo / rel)   # a missing log refuses
                    c.add(f"lower {rel}: report log", PASS, line[2:])
                except core.Fail as e:
                    c.add(f"lower {rel}: report log", FAIL, str(e))
                evidence_file_checks(c, repo, v, x)
                sacct_seal_check(c, v)
                ci_check(c, repo, v)
                if corroborate and (got := corroborate_check(c, repo, v, fetch, github)) is not None:
                    corroborated.append(got)
                container_check(c, v)
                claim_axioms_check(c, repo, v, x)
        cs = gate_mod.claims_schema_at(pkg_name, pkg, repo, x)
        if not cs["checked"]:
            c.add("claims", SKIP, f"no {cs['file']} at {x[:12]}")
        else:
            c.add("claims", PASS if cs["ok"] else FAIL, gate_mod.claims_schema_line(cs)[2:])
        lower_commit = x if found["lower"] else None
        for v in found["comparator"]:
            rel = v["path"]
            n_cfg = len(v["data"].get("configs") or [])
            c.run(f"comparator {rel}", lambda: gate_mod.validate_comparator(pkg, repo, v["data"], lower_commit),  # noqa: B023  called synchronously, within this iteration
                  f"{n_cfg} configuration(s) tracked at {x[:12]} re-derived from bytes (sha256, theorem names, "
                  f"sign-off), every log's sha256 and PASS line, same commit as the lower tier")
            olean_check(c, repo, v, x)
            container_check(c, v, "comparator")
            ci_check(c, repo, v, "comparator")
        claimed_theorems_check(c, repo, pkg, x, bool(found["comparator"]))
        coverage_check(c, repo, pkg)
        page_checks(c, repo, x, found)
        # #237: the toolchain the receipts bind (else lean-toolchain at X) against the shipped advisories
        toolchains = [v["data"].get("lean_toolchain") for k in ("lower", "comparator") for v in found[k]]
        advisories, advisory_lines = toolchain_advisories.warn_lines(
            [t for t in toolchains if isinstance(t, str) and t.strip()] or [toolchain_at(repo, x)])
    failed = c.failed()
    lower_ok = bool(found["lower"]) and not any(
        i["verdict"] == FAIL for i in c.items if i["id"].startswith(("lower ", "receipts", "binding")))
    comp_ok = lower_ok and bool(found["comparator"]) and not any(
        i["verdict"] == FAIL for i in c.items if i["id"].startswith(("comparator ", "claimed theorems")))
    tier = index_mod.compute_trust(lower_ok, comp_ok, [])
    rule = rule_text(oi, x, evidence_only)
    ok = failed == 0
    counts = {v: sum(i["verdict"] == v for i in c.items) for v in (PASS, FAIL, SKIP)}
    summary = (f"receipt check: {'PASS' if ok else 'FAIL'} {pkg_name} at {x[:12]} "
               + (f"(tier {tier}; {counts[PASS]} passed, {counts[SKIP]} skipped)" if ok
                  else f"({counts[FAIL]} check(s) failed; tier {tier or 'none'})"))
    lines = [summary,
             f"  commit {x} (published ref {ref[:12]}{', the same commit' if ref == x else ''})",
             f"  rule: {rule}",
             *[f"  exemption: {e}" for e in c.exemptions],
             f"  note: {CORROBORATE_NOTE if corroborate else RERUN_NOTE}",
             *([f"  repository: {REPO_NOTE.format(where='--github ' + github) if github else REPO_DEFAULT}"]
               if corroborate else []),
             *[f"  {w}" for w in advisory_lines]]
    lines += [f"  {i['verdict'].upper():4} {i['id']}: {i['detail']}" for i in c.items]
    lines.append(summary)
    receipts = {k: [{"path": v["path"], "sha256": v["sha256"], "commit": v["commit"]} for v in found[k]]
                for k in ("lower", "comparator")}
    receipts["other"] = [{"path": v["path"], "sha256": v["sha256"], "commit": v["commit"]} for v in found["other"]]
    payload = {"ok": ok, "name": pkg_name, "commit": x, "at": ref, "prefix": src["prefix"] or None,
               "docs_descendants": oi.rule == "docs-only", "evidence_only": bool(evidence_only),
               "opt_in": opt_in_view(oi), "rule": rule, "tier": tier, "runs_lean": False,
               "network": bool(corroborate),
               "note": CORROBORATE_NOTE if corroborate else RERUN_NOTE, "receipts": receipts, "bound_receipts": bound,
               "counts": counts, "exemptions": c.exemptions,
               "skipped_records": [v["path"] for v in found["sidecar"]], "checks": c.items,
               "toolchain_advisories": advisories}
    if corroborate:
        payload["github"] = github
        payload["ci"] = {"level": min((g["level"] for g in corroborated), default=None),
                         "rows": [{"receipt": g["path"], **r} for g in corroborated for r in g["rows"]]}
    return lines, payload, 0 if ok else 1


def cmd_receipt_check(args) -> int:
    lines, payload, rc = do_check(args.repo, args.commit_sha, args.at, args.docs_descendants, args.name,
                                  getattr(args, "evidence_only", False),
                                  corroborate=args.corroborate, github=args.github)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def add_parser(rsub) -> None:
    import argparse
    s = rsub.add_parser("check", help="validate a published package's receipts from git alone: no registry, no "
                                      "network, never runs Lean (an outside referee's check; exit 0 only when "
                                      "every check passes)")
    s.add_argument("--repo", required=True, metavar="DIR",
                   help="a local clone of the package (the package directory; cloned privately, never modified)")
    s.add_argument("--commit", dest="commit_sha", required=True, metavar="SHA",
                   help="the commit the receipts bind (the page's `git checkout <commit>` line)")
    s.add_argument("--at", default=None, metavar="REF",
                   help="the published ref whose .lean-receipts/, coverage.yaml and README.md are read (default "
                        "HEAD of --repo); it must be the commit or an evidence-only descendant of it (or a docs-only "
                        "one, when the docs-only rule applies)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--docs-descendants", action="store_true",
                   help="for a package without lean-pkg-binding.yaml at the commit: also accept a docs-only "
                        "descendant (no build input changed) as the referee's choice, printed in the result; a "
                        "committed file that says false or does not validate overrides it")
    g.add_argument("--evidence-only", action="store_true",
                   help="accept only evidence-only descendants: ignore the author's committed opt-in "
                        "(lean-pkg-binding.yaml at the commit, applied by default)")
    s.add_argument("--name", default=None,
                   help="the package name the claims must carry (default: `package` in coverage.yaml, else "
                        "claims.yaml, else the directory name)")
    s.add_argument("--corroborate", action="store_true",
                   help="also corroborate every receipt with a ci block (a GitHub Actions run) against the public "
                        "GitHub REST API, level 2, and fail when one does not reach it (404, rate limit, network "
                        "error and a redirect off api.github.com fail closed). GH_TOKEN or GITHUB_TOKEN, when set, "
                        "only raises the rate limit and is never printed. The one networked check")
    s.add_argument("--github", default=None, metavar="OWNER/REPO",
                   help="with --corroborate: the package's own GitHub repository, whose numeric id the run's must "
                        "equal (default: the receipt's ci.repository, and the output says so)")
    s.add_argument("--json", dest="json_out", action="store_true", default=argparse.SUPPRESS,
                   help="machine-readable JSON on stdout: every check with its verdict")
    s.set_defaults(fn=cmd_receipt_check)

