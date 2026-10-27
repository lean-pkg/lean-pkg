# lean-pkg -- the trust gate
"""The trust gate: `lean-pkg gate` (validate a lower-tier receipt, bind it to the package's commit, move
gate-pending -> verified-private), `gate --check`, the comparator evidence path (`comparator attach` and
`comparator check`: validate_comparator, the copy into the package), the report-log and pin checks, and the
descendant rules (which commits a receipt bound to an ancestor still covers). `--repo DIR` (repo_override),
which the read-only commands share, is here too.

Moved verbatim out of cli.py (#317, stage 3). cli.py re-exports every name defined here for at least one
release; lean-pkg's own modules import this module as `gate_mod` (`from . import gate as gate_mod`:
`gate` is a common local name for a package's gate record). It imports core.py at the top level and the
command modules it calls only inside the functions that call them; it never imports cli.py. Code here
reads this module's globals: a test patches a name here (conftest.patch_core patches a core name on every
module that imports it).
"""

from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import posixpath
import re
import shutil
import sys
import zlib

import yaml

from .core import (
    _ACTOR, ancestor_attribute_changes, bound_tree, branch_notice, checker_paths, CLAIMS_COMMIT_FIRST,
    COMPARATOR_CONFIG_FIELDS, COMPARATOR_FIELDS, COMPARATOR_LIST_FIELDS, COMPARATOR_MECHANISMS,
    comparator_pass, COMPARATOR_PASS_LINE, COMPARATOR_SCHEMAS, COMPARATOR_TOOLCHAIN_DIGESTS, COMPARATOR_TOOLS,
    DEFAULT_AXIOMS, docs_build_input, DOCS_PATHS_MAX, events_path, evidence_symlinks, Fail, formalization_rel,
    get_pkg, gh_nwo, git, git_head, git_show_bytes, _is_ancestor, lake_artifact, lease_actor, lease_live,
    linked_changes, linked_evidence_changes, load_registry, LOWER_RECEIPT_SCHEMA, _LOWER_RECEIPT_SCHEMA_RE,
    LOWER_RECEIPT_SCHEMAS, nested_exclusions, nested_label, parse_ts, print_json, RECEIPT_FIELDS, record,
    refuse_invalid_lease, registered_nested_dirs, registry_path, repo_path, require_nonempty, save_registry,
    sha256_bytes, symlink_targets, tracked_at, under_any, UsageError, warn_lease,
)
from .holes import SIDECAR as SIGNOFF_SIDECAR, check_definition_holes, parse_sidecar


# --------------------------------------------------------------------------
# the descendant rules: which commits a receipt bound to an ancestor still covers (evidence-only,
# docs-only). They stay here: formalization_derived_only reads formalization.py, which imports cli.

def formalization_derived_only(repo: pathlib.Path, bound: str, head: str, rel: str) -> str | None:
    """None when <rel> differs between bound and head only in keys the formalization renderer derives
    (status.evidence and the rest of formalization.DERIVED_PATHS), compared as YAML data; else the
    reason, naming the changed kept key(s) (#23.5)."""
    from .formalization import changed_kept_keys
    docs = []
    for rev in (bound, head):
        raw = git_show_bytes(repo, f"./{rel}", rev)
        if raw is None:
            return (f"{rel} is not tracked at {rev[:12]}; only a regeneration of a file already "
                    f"committed at the bound commit is evidence")
        try:
            doc = yaml.safe_load(raw.decode("utf-8", errors="replace"))
        except yaml.YAMLError:
            return f"{rel} at {rev[:12]} is not valid YAML"
        docs.append(doc if isinstance(doc, dict) else {})
    changed = changed_kept_keys(*docs)
    if changed:
        return (f"{rel} changed in kept (hand-authored) key(s) {', '.join(changed[:5])}; only its "
                f"derived keys (status.evidence and the rest `lean-pkg formalization` regenerates) may "
                f"change after the bound commit")
    return None


def evidence_only_descendant(repo: pathlib.Path, bound: str,
                             head: str, formalization: str | None = None,
                             exclude: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Evidence-only descendant rule (see DESIGN.md): a receipt bound to commit `bound` is
    accepted at `head` when head == bound, or when head descends from bound and
    the only paths that differ live under .lean-receipts/ (the evidence itself).
    Paths are taken relative to the package directory `repo`: when the package is a
    subdirectory of its git repository (lean/, lean/contrib/), only changes under that
    directory count, and within it only <dir>/.lean-receipts/ is evidence.
    `formalization` (the package's recorded formalization.yaml path, see formalization_rel):
    that file may also differ when only its derived keys changed, since it must cite the
    receipt and so cannot be inside the bound commit (#23.5). claims.yaml never may.
    `exclude`: package-relative directories of nested registered packages this package does not depend
    on (nested_exclusions); changes under them are not this package's (#3).
    Returns (ok, note)."""
    if bound == head:
        return True, ""
    p = git(repo, "merge-base", "--is-ancestor", bound, head)
    if p.returncode != 0:
        return False, f"{bound[:12]} is not an ancestor of HEAD {head[:12]}"
    prefix = git(repo, "rev-parse", "--show-prefix").stdout.strip()
    # --relative: only paths under the package directory, relative to it.
    # --no-renames: a rename into .lean-receipts/ is a source change.
    p = git(repo, "diff", "--no-renames", "--name-only", "--relative", bound, head)
    paths = [x for x in p.stdout.splitlines() if x.strip()]
    ignored = [x for x in paths if under_any(x, exclude)]
    paths = [x for x in paths if not under_any(x, exclude)]
    # A Lake build cache or compiled artifact is never evidence, under .lean-receipts/ included (#5).
    cached = [f"{x} ({lake_artifact(x)})" for x in paths if lake_artifact(x)]
    if cached:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} in {'; '.join(cached[:5])}"
                       + (f" (+{len(cached) - 5} more)" if len(cached) > 5 else "")
                       + "; Lake reads it in place of a build, so it is never evidence")
    foreign = [x for x in paths if not x.startswith(".lean-receipts/")]
    # Outside a subdirectory package: .gitattributes/.gitmodules/.lfsconfig at the repository root or between
    # it and the package still change the bytes the build sees (filters, eol, LFS); never evidence.
    outside = ancestor_attribute_changes(repo, bound, head)
    if outside:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} in {', '.join(outside[:5])} "
                       f"(outside the package directory; can change the bytes the build sees)")
    # A committed symlink at the bound commit that resolves into .lean-receipts/ (or to the formalization file)
    # would let an "evidence" edit change what the build reads.
    linked = linked_evidence_changes(repo, bound, paths, formalization) if paths else []
    if linked:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} in evidence that a committed symlink "
                       f"points to: {', '.join(linked[:5])}; a symlink target is never evidence")
    if (prefix or ignored) and not paths:
        if ignored:
            return True, (f"descendant of {bound[:12]} with no change under {prefix or './'} outside nested "
                          f"registered package(s) {nested_label(ignored, exclude)}")
        return True, f"descendant of {bound[:12]} with no change under {prefix}"
    if formalization and formalization in foreign:
        problem = formalization_derived_only(repo, bound, head, formalization)
        if problem:
            return False, f"HEAD {head[:12]} differs from {bound[:12]}: {problem}"
        foreign.remove(formalization)
    if foreign or not paths:
        return False, (f"HEAD {head[:12]} differs from {bound[:12]} outside "
                       f".lean-receipts/: {foreign[:5]}")
    return True, (f"evidence-only descendant of {bound[:12]} (+{len(paths)} evidence file(s))"
                  + (f"; ignored {len(ignored)} change(s) under nested registered package(s) "
                     f"{nested_label(ignored, exclude)}" if ignored else ""))


def docs_only_descendant(repo: pathlib.Path, bound: str, head: str, pkg: dict | None,
                         checker=None, exclude: tuple[str, ...] = (),
                         opted: str = "opted in") -> tuple[bool, str, list[str]]:
    """The docs-only descendant rule (docs/receipts.md): head descends from bound and no build input
    under the package directory changed. Build inputs: the .lean files a lean_lib or lean_exe of the
    package owns (read from lakefile.toml at bound and at head; every *.lean when there is a
    lakefile.lean, no lakefile.toml, or one that cannot be read precisely), input_file/input_dir and
    `require` path targets, lakefile.*, lake-manifest.json,
    lean-toolchain at any depth, the record's build_inputs globs, the files the receipt's checker names;
    always refused: a Lake build cache or compiled artifact (lake_artifact: a .lake component at any depth,
    or an artifact suffix; .lean-receipts/ included), a symlink or gitlink added/changed/removed, .gitattributes, .gitmodules, .lfsconfig,
    claims.yaml (and the recorded claims_file), lean-pkg-binding.yaml (the opt-in itself, at any depth);
    for a subdirectory package also a .gitattributes,
    .gitmodules or .lfsconfig at the repository root or in any directory between it and the package.
    The formalization.yaml keeps its derived-keys-only rule. `exclude`: as for evidence_only_descendant.
    `opted`: the admission note's last clause, naming where the opt-in came from (binding_file.OptIn.label).
    Returns (ok, note, docs_paths): docs_paths are the changed non-build, non-evidence paths."""
    from .build_scope import lake_scope
    pkg = pkg or {}
    if not _is_ancestor(repo, bound, head):
        return False, f"{bound[:12]} is not an ancestor of HEAD {head[:12]}", []
    p = git(repo, "diff", "--no-renames", "--raw", "-z", "--relative", bound, head)
    fields = p.stdout.split("\0")
    globs = [g for g in (pkg.get("build_inputs") or []) if isinstance(g, str) and g.strip()]
    extra = set(checker_paths(repo, bound, checker))
    scopes, why_conservative = [], ""
    for rev in (bound, head):
        sc, why_sc = lake_scope(lambda rel, rev=rev: git_show_bytes(repo, rel, rev))
        if sc is None:
            scopes, why_conservative = None, why_sc
            break
        scopes.append(sc)
    claims_rel = str(pkg.get("claims_file") or "claims.yaml").strip()
    while claims_rel.startswith("./"):
        claims_rel = claims_rel[2:]
    fz_rel = formalization_rel(pkg) or "formalization.yaml"
    refused: list[str] = []
    claims_changed: list[str] = []   # refused only because they are the claims file
    docs: list[str] = []
    admitted: list[str] = []      # docs, evidence and formalization paths: none may be a symlink target
    ignored = 0
    i = 0
    while i + 1 < len(fields):
        meta, path = fields[i], fields[i + 1]
        i += 2
        if not meta.startswith(":"):
            continue
        if under_any(path, exclude):
            ignored += 1
            continue
        parts = meta[1:].split()
        modes = parts[:2]
        if any(m in ("120000", "160000") for m in modes):
            kind = "gitlink" if "160000" in modes else "symlink"
            refused.append(f"{path} ({kind} {parts[-1][:1]})")
            continue
        why = "claims_file" if path == claims_rel else docs_build_input(path, globs, extra, scopes)
        if why:
            refused.append(f"{path} ({why}{', deleted' if parts[-1].startswith('D') else ''})")
            if why in ("claims_file", "claims.yaml") and not parts[-1].startswith("D"):
                claims_changed.append(refused[-1])
            continue
        if path == fz_rel:
            problem = formalization_derived_only(repo, bound, head, fz_rel)
            if problem:
                refused.append(f"{path} ({problem})")
            admitted.append(path)
            continue
        admitted.append(path)
        if path.startswith(".lean-receipts/"):
            continue
        docs.append(path)
    refused += [f"{x} (outside the package directory; can change the bytes the build sees)"
                for x in ancestor_attribute_changes(repo, bound, head)]
    if admitted:   # a non-build file a committed symlink points to is read through that symlink
        try:
            refused += linked_changes(symlink_targets(bound_tree(repo, bound)), admitted)
        except Fail as e:
            refused.append(f"{len(admitted)} file(s) ({e})")
    if refused:
        if refused == claims_changed:
            names = ", ".join(x.split(" (", 1)[0] for x in claims_changed)
            return False, (f"{names} changed after the receipt commit {bound[:12]} (HEAD {head[:12]}); gate "
                           f"reads the claims at the receipt commit, so they are part of what it binds: "
                           f"{CLAIMS_COMMIT_FIRST}"), []
        lean_note = ""
        if scopes is None and any(" (*.lean" in x for x in refused):
            lean_note = (f" (every *.lean counts as a build input: {why_conservative}; only a lakefile.toml's "
                         f"lean_lib/lean_exe targets narrow it)")
        return False, (f"build input(s) changed between {bound[:12]} and HEAD {head[:12]}: "
                       f"{'; '.join(refused[:3])}" + (f" (+{len(refused) - 3} more)" if len(refused) > 3 else "")
                       + lean_note + "; the receipt must be rebuilt at HEAD"), []
    docs.sort()
    shown = ", ".join(docs[:5]) + (f", +{len(docs) - 5} more" if len(docs) > 5 else "")
    return True, (f"docs-only descendant of {bound[:12]} ({len(docs)} non-build file(s): {shown}; "
                  f"{opted}" + (f"; ignored {ignored} change(s) under nested registered package(s) "
                                 f"{', '.join(d + '/' for d in exclude)}" if ignored else "") + ")"), docs


def package_binding(repo: pathlib.Path, bound: str, head: str, pkg: dict | None,
                    checker=None, formalization: str | None = None, nested: tuple[str, ...] | None = None,
                    opt_in=None) -> tuple[bool, str, list[str] | None, object]:
    """The binding rule for a package's commit-bound evidence: the evidence-only rule, then, when the
    package opts in, the docs-only rule. The opt-in (binding_file.resolve_opt_in, unless `opt_in` is given)
    is lean-pkg-binding.yaml read at `bound` and nowhere else, with the registry record's precedence: a
    record docs_descendants: false vetoes, a record true with no file at `bound` still opts in, a file at
    `bound` decides. (ok, note, docs_paths, opt_in): docs_paths is a list only when the docs rule admitted
    head; opt_in (binding_file.OptIn) is None when the evidence-only rule decided without it. Without the
    opt-in the verdict and note are the evidence-only rule's, plus the clauses that say why the docs rule
    did not apply (a hint when it would have admitted head, the file added after `bound`, a file at
    `bound` that does not opt in, the record's veto).
    `formalization` overrides the record's pointer (gate --refresh-formalization); `checker` defaults
    to the recorded gate receipt's. `nested`: the package-relative roots of nested registered packages
    (default: from the registry this process loaded, registered_nested_dirs); changes under one are not
    this package's unless it depends on it (nested_exclusions, #3)."""
    from . import binding_file
    pkg = pkg or {}
    fz = formalization if formalization is not None else formalization_rel(pkg)
    if nested is None:
        nested = registered_nested_dirs(repo)
    exclude, kept = nested_exclusions(repo, bound, head, nested) if bound != head else ((), [])
    kept_note = (f"; nested registered package(s) still counted as build input(s): {'; '.join(kept)}"
                 if kept else "")
    ok, note = evidence_only_descendant(repo, bound, head, fz, exclude)
    if ok or not _is_ancestor(repo, bound, head):
        return ok, note + ("" if ok else kept_note), None, None
    note += kept_note
    if opt_in is None:
        opt_in = binding_file.resolve_opt_in(repo, bound, pkg)
    if checker is None:
        checker = (recorded_json(repo, (pkg.get("gate") or {}).get("receipt")) or {}).get("checker")
    dpkg = {**pkg, "build_inputs": list(opt_in.build_inputs)}
    if fz:
        dpkg["formalization_yaml"] = fz
    dok, dnote, docs = docs_only_descendant(repo, bound, head, dpkg, checker, exclude, opted=opt_in.label)
    # rule 2: a file that exists at head but not at the bound commit grants nothing
    added = (f"; {binding_file.FILE} was added after {bound[:12]}; the opt-in is read at the bound commit only"
             if opt_in.file_state == "absent" and tracked_at(repo, binding_file.FILE, head) else "")
    why_not = "".join(f"; {n}" for n in opt_in.notes)
    if opt_in.rule == "docs-only":
        if dok:
            return True, dnote, docs, opt_in
        return False, f"{note}; docs-only rule ({opt_in.label}): {dnote}{added}", None, opt_in
    if dok and opt_in.file_state == "absent":
        name = pkg.get("name") or "NAME"
        return False, (f"{note}; every changed path is a non-build file, so the package can opt in: commit "
                       f"{binding_file.FILE} (schema: {binding_file.SCHEMA}, docs_descendants: true) in the "
                       f"package directory before the next verify run (docs/receipts.md#docs-only-descendants-opt-in), "
                       f"or `lean-pkg update {name} --accept-docs-descendants --reason TEXT`"), None, opt_in
    if CLAIMS_COMMIT_FIRST in dnote:
        return False, f"{note}; {dnote}{why_not}{added}", None, opt_in
    return False, f"{note}{why_not}{added}", None, opt_in


def package_descendant(repo: pathlib.Path, bound: str, head: str, pkg: dict | None,
                       checker=None, formalization: str | None = None,
                       nested: tuple[str, ...] | None = None, opt_in=None) -> tuple[bool, str, list[str] | None]:
    """package_binding without the opt-in: (ok, note, docs_paths). Every caller that does not report where
    the opt-in came from uses this."""
    ok, note, docs, _opt_in = package_binding(repo, bound, head, pkg, checker=checker,
                                              formalization=formalization, nested=nested, opt_in=opt_in)
    return ok, note, docs


def docs_admission_line(head: str, bound: str, note: str) -> str:
    """The text line of a docs-only admission: `✓ HEAD <c12> is a docs-only descendant of ...`."""
    return f"✓ HEAD {head[:12]} is a {note}"


def docs_admission(repo: pathlib.Path, commit: str, pkg: dict, checker=None) -> tuple[dict, str | None]:
    """({descendant_of, docs_paths, opt_in} or {}, text line or None): the additive event/JSON fields when
    the docs-only rule (not the evidence-only rule) admitted HEAD for evidence bound to `commit`. opt_in is
    {source, commit, path}: source "committed" (lean-pkg-binding.yaml at `commit`, path package-relative)
    or "registry" (the record's docs_descendants: true, path null)."""
    head = git_head(repo) or ""
    if not head or head == commit:
        return {}, None
    ok, note, docs, opt_in = package_binding(repo, commit, head, pkg, checker=checker)
    if not ok or docs is None:
        return {}, None
    return ({"descendant_of": commit, "docs_paths": docs[:DOCS_PATHS_MAX], "opt_in": opt_in.view()},
            docs_admission_line(head, commit, note))


# --------------------------------------------------------------------------
# gate

# The results ledger is the project's own record of outcomes, outside lean-pkg (field report: name it).
RL_REMINDER = ("results-ledger: lean-pkg recorded this outcome for {name} (`lean-pkg status {name}`; per claim: "
               "`lean-pkg claims ledger {name}`); if your project keeps a results ledger of its own, add it "
               "there (docs/lifecycle.md#results-ledger)")

# a lower-tier receipt's sealed sacct record (`receipt slurm --seal-sacct`) is missing at HEAD or not its bytes
SACCT_SEAL_RESTORE = "sacct seal record missing or changed: "


def check_lower_receipt_schema(receipt: dict, *, newer_only: bool = False) -> None:
    """The lower-tier receipt's `schema` marker: absent is version 0 (a receipt written before 0.22.0, judged
    as before); LOWER_RECEIPT_SCHEMAS pass. A newer lower-receipt/N is refused naming the upgrade; any other
    value is refused as malformed (unless newer_only, the check that runs before the required fields, so a
    newer receipt that dropped a field is still told to upgrade)."""
    if "schema" not in receipt:
        return
    s = receipt["schema"]
    if s in LOWER_RECEIPT_SCHEMAS:
        return
    m = _LOWER_RECEIPT_SCHEMA_RE.fullmatch(s) if isinstance(s, str) else None
    if m and int(m.group(1)) > int(LOWER_RECEIPT_SCHEMA.rsplit("/", 1)[1]):
        raise Fail(f"lower-tier receipt schema {s} is not supported (supported: "
                   f"{', '.join(LOWER_RECEIPT_SCHEMAS)}, and a receipt without a schema key); this lean-pkg is "
                   f"older than the receipt: upgrade lean-pkg")
    if not newer_only:
        raise Fail(f"receipt schema must be {LOWER_RECEIPT_SCHEMA!r} (or absent, in a receipt written before "
                   f"0.22.0), got {s!r}")


def validate_receipt(pkg: dict, repo: pathlib.Path, receipt: dict) -> None:
    check_lower_receipt_schema(receipt, newer_only=True)
    for f in RECEIPT_FIELDS:
        if f not in receipt:
            raise Fail(f"receipt missing required field {f!r}")
    check_lower_receipt_schema(receipt)
    commit = str(receipt["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Fail(f"receipt commit is not a full lowercase 40-hex sha: {commit!r}")
    head = git_head(repo)
    if head is None:
        raise Fail(f"cannot resolve HEAD in {repo}")
    ok, note, _docs = package_descendant(repo, commit, head, pkg, checker=receipt.get("checker"))
    if not ok:
        raise Fail(f"receipt commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the receipt must bind to HEAD or an evidence-only descendant "
                   f"({note})")
    check_toolchain_at(repo, commit, receipt["lean_toolchain"], "toolchain mismatch")
    check_path_deps_at(repo, commit, formalization_rel(pkg))
    if not str(receipt["mathlib_rev"]).strip():
        raise Fail(f"receipt mathlib_rev is empty (a package without Mathlib says {NO_MATHLIB!r})")
    committed = committed_mathlib(repo, commit)
    rev = str(receipt["mathlib_rev"]).strip()
    if committed not in (None, NO_MATHLIB) and not _same_rev(rev, committed):
        raise Fail(f"mathlib mismatch: receipt mathlib_rev={rev!r}, the mathlib rev in lake-manifest.json "
                   f"committed at {commit[:12]}={committed!r}; the receipt must name the Mathlib the bound commit "
                   f"pins" + (f" ({NO_MATHLIB!r} is the value of a package without Mathlib)" if rev == NO_MATHLIB
                              else ""))
    # committed == NO_MATHLIB with another value: a receipt written before `none` was a checked value; it is
    # read as before (not checked, receipt_check_lines says so), never refused
    sc = receipt["sorry_count"]
    if isinstance(sc, bool) or not isinstance(sc, int):
        raise Fail(f"sorry_count must be an integer, got {sc!r}")
    if sc != 0:
        raise Fail(f"sorry_count must be 0, got {sc} — the trust gate is not clean")
    axioms = receipt["axioms"]
    if not isinstance(axioms, list) or not all(isinstance(a, str) for a in axioms):
        raise Fail("axioms must be a list of strings")
    allowed = set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS)
    extra = sorted(set(axioms) - allowed)
    if extra:
        raise Fail(f"axioms {extra} outside allowed set {sorted(allowed)}")
    if "claim_axioms" in receipt:      # additive (AX1): per-claim axioms read from the built environment
        from . import claim_axioms
        problems = claim_axioms.summary_problems(receipt["claim_axioms"], allowed)
        if problems:
            raise Fail("; ".join(problems))
    if "slurm_sacct" in receipt:       # additive (`receipt slurm --seal-sacct`): the sealed sacct record at HEAD
        from . import receipt as receipt_mod
        why = receipt_mod.committed_sacct_seal_problem(repo, receipt)
        if why:
            # the record missing at HEAD or not its bytes: restoring the byte-exact file is enough
            restore = "is not committed at HEAD" in why or "does not hash to" in why
            raise Fail(f"{SACCT_SEAL_RESTORE if restore else 'sacct seal: '}{why}")
    if "ci" in receipt:                # additive (proposal 0008): the GitHub Actions run block, level 1 (offline)
        from . import ci_github
        problems, _workflow = ci_github.level1(receipt, repo)
        if problems:
            raise Fail("ci block (a GitHub Actions run, level 1; docs/formats.md#the-ci-block): "
                       + "; ".join(problems))
    sha = str(receipt["report_sha256"])
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise Fail(f"report_sha256 must be 64 hex chars, got {sha!r}")
    for f in ("job_id", "checker", "generated_at"):
        if not str(receipt[f]).strip():
            raise Fail(f"receipt field {f!r} is empty")
    check_timestamp(receipt["generated_at"], "receipt")
    receipt_job_slug(receipt["job_id"])


def check_timestamp(value, what: str) -> None:
    """generated_at must parse as an ISO-8601 timestamp (parse_ts), not merely be non-empty."""
    try:
        parse_ts(value)
    except (ValueError, TypeError):
        raise Fail(f"{what} field 'generated_at' is not an ISO-8601 timestamp: {value!r}") from None


def receipt_job_slug(job_id) -> str:
    """The job id as it appears in the stored receipt's file name: characters outside
    [A-Za-z0-9._-] become '-', and leading dots and dashes are stripped (so '../../evil' is
    'evil', never a '..'-led name). An empty result is refused."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(job_id)).lstrip(".-")
    if not slug:
        raise Fail(f"receipt job_id {str(job_id)!r} leaves no usable file-name characters")
    return slug


def check_toolchain_at(repo: pathlib.Path, commit: str, claimed, label: str) -> None:
    """The receipt's lean_toolchain must equal the lean-toolchain file committed at the bound
    commit (relative to the package directory). The working-tree file never counts."""
    blob = git_show_bytes(repo, "./lean-toolchain", commit)
    if blob is None:
        raise Fail(f"{repo}: no lean-toolchain committed at {commit[:12]}")
    committed = blob.decode("utf-8", "replace").strip()
    if str(claimed).strip() != committed:
        wt = repo / "lean-toolchain"
        work = wt.read_text().strip() if wt.is_file() else None
        raise Fail(f"{label}: receipt={str(claimed).strip()!r} committed at {commit[:12]}="
                   f"{committed!r}"
                   + (f" (the working-tree file says {work!r}; uncommitted files do not count)"
                      if work is not None and work != committed else ""))


def outside_path_deps(manifest: bytes) -> list[str]:
    """`name (dir)` for each `path` package in lake-manifest.json bytes whose dir leaves the package
    directory (absolute, or `..` after normalising). Its sources are outside what a receipt binds, so an
    edit to them is never drift. Judged lexically on the committed text; ValueError on bad JSON."""
    data = json.loads(manifest.decode("utf-8", "replace"))
    out = []
    for p in (data.get("packages") or []) if isinstance(data, dict) else []:
        if not isinstance(p, dict) or p.get("type") != "path":
            continue
        d = str(p.get("dir") or "")
        norm = posixpath.normpath(d.replace("\\", "/")) if d else "."
        if posixpath.isabs(norm) or norm == ".." or norm.startswith("../"):
            out.append(f"{p.get('name')} ({d})")
    return out


def escaping_symlinks(tree: dict[str, str]) -> list[str]:
    """`path -> target` for each committed symlink whose target is absolute or has a `..` component. A
    target that only descends cannot leave the directory holding the symlink, so no chain of the
    remaining symlinks can leave the package directory either."""
    out = []
    for path, mode in sorted(tree.items()):
        if mode.startswith("120000:"):
            target = mode.split(":", 1)[1]
            t = target.replace("\\", "/")
            if posixpath.isabs(t) or ".." in t.split("/"):
                out.append(f"{path} -> {target}")
    return out


def path_dep_tree_problems(tree: dict[str, str], manifest: bytes) -> list[str]:
    """For each in-package `path` dependency: its dir, and every directory on the way to it, must be a
    tracked directory at the bound commit (not a symlink, a submodule gitlink, or untracked such as
    .lake/), and nothing under it may be a gitlink. Lexically escaping ones are outside_path_deps'."""
    data = json.loads(manifest.decode("utf-8", "replace"))
    out = []
    for p in (data.get("packages") or []) if isinstance(data, dict) else []:
        if not isinstance(p, dict) or p.get("type") != "path":
            continue
        d = str(p.get("dir") or "")
        norm = posixpath.normpath(d.replace("\\", "/")) if d else "."
        if posixpath.isabs(norm) or norm == ".." or norm.startswith("../") or norm == ".":
            continue
        parts = norm.split("/")
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            mode = tree.get(prefix)
            if mode != "040000":
                what = ("a symlink" if mode and mode.startswith("120000") else
                        "a submodule (gitlink)" if mode == "160000" else
                        "a file" if mode else "not tracked")
                out.append(f"{p.get('name')} ({d}): {prefix} is {what} at the bound commit")
                break
        else:
            links = [q for q, m in tree.items() if m == "160000" and q.startswith(norm + "/")]
            if links:
                out.append(f"{p.get('name')} ({d}): contains a submodule (gitlink) {links[0]}")
    return out


def check_evidence_symlinks_at(repo: pathlib.Path, commit: str, formalization: str | None = None,
                               tree: dict[str, str] | None = None) -> None:
    """Refuse a bound commit with a committed symlink that resolves into .lean-receipts/ (or to the
    package's formalization.yaml): the evidence-only rule admits later edits there, so the symlink would
    let a source change pass as evidence."""
    links = evidence_symlinks(tree if tree is not None else bound_tree(repo, commit), formalization)
    if links:
        raise Fail(f"symlinks at {commit[:12]} point into evidence the binding rule lets change later: "
                   f"{', '.join(links)}. The receipt would not bind what they point to; replace them with the "
                   f"files (outside .lean-receipts/), then a new commit and a new receipt")


def check_path_deps_at(repo: pathlib.Path, commit: str, formalization: str | None = None) -> None:
    """Refuse a receipt whose bound commit lets the build read sources outside the package directory:
    a `path` dependency in lake-manifest.json (committed at the bound commit) that leaves the directory
    lexically, or that reaches through a symlink, a submodule or an untracked directory; and any
    committed symlink whose target is absolute or climbs with `..`. Judged on the commit's tree, never
    the working tree. A manifest that is absent is left to the other checks; one that is not valid JSON
    is refused."""
    tree = bound_tree(repo, commit)
    links = escaping_symlinks(tree)
    if links:
        raise Fail(f"symlinks at {commit[:12]} can leave the package directory: {', '.join(links)}. The "
                   f"receipt does not bind what they point to; replace them with the files (or a target "
                   f"that only descends, with no `..`), then a new commit and a new receipt")
    check_evidence_symlinks_at(repo, commit, formalization, tree)
    blob = git_show_bytes(repo, "./lake-manifest.json", commit)
    if blob is None:
        return
    try:
        outside = outside_path_deps(blob)
        problems = path_dep_tree_problems(tree, blob)
    except ValueError:
        raise Fail(f"lake-manifest.json at {commit[:12]} is not valid JSON; cannot check its path "
                   f"dependencies") from None
    if outside:
        raise Fail(f"lake-manifest.json at {commit[:12]} has path dependencies outside the package directory: "
                   f"{', '.join(outside)}. The receipt does not bind their sources, so an edit to them would "
                   f"never count as drift. Require them from git pinned to a rev (lake-manifest.json then "
                   f"pins it) or move them inside the package directory; then a new commit and a new receipt")
    if problems:
        raise Fail(f"lake-manifest.json at {commit[:12]} has path dependencies the receipt does not bind: "
                   f"{'; '.join(problems)}. A path dependency must be a directory tracked in the package "
                   f"itself; require it from git pinned to a rev instead; then a new commit and a new receipt")


def dirty_tracked(repo: pathlib.Path) -> list[str]:
    """Tracked files under the package directory that differ from HEAD (staged or not)."""
    p = git(repo, "status", "--porcelain", "--untracked-files=no", "--", ".")
    return [x[3:] for x in p.stdout.splitlines() if x.strip()]


def dirty_guard(repo: pathlib.Path, name: str, refuse: bool) -> list[str]:
    """Evidence is judged at the bound commit, never the working tree; a dirty checkout is a WARN
    on stderr (the lab's workflow edits while gating) or, with --refuse-dirty, a refusal. A checkout
    off its default branch (or detached) is a second stderr WARN, never a refusal (#48)."""
    dirty = dirty_tracked(repo)
    if dirty:
        shown = ", ".join(dirty[:5]) + (", ..." if len(dirty) > 5 else "")
        msg = (f"{name}: {len(dirty)} tracked file(s) modified in the checkout ({shown}); "
               f"receipts are checked against the bound commit, not these edits")
        if refuse:
            raise Fail(f"--refuse-dirty: {msg}; commit or stash them first")
        print(f"WARN: {msg} (--refuse-dirty refuses)", file=sys.stderr)
    notice = branch_notice(repo)
    if notice:   # #48: informational, never a refusal; evidence commits are often made detached
        print(f"WARN: {notice}", file=sys.stderr)
    return dirty


COMPARATOR_CONFIG_RE = re.compile(r"^(comparator[^/]*\.json|comparator/config[^/]*\.json)$")


def cfg_name(path: str) -> str:
    """Same rule as comparator-check.sh / comparator-receipt: strip .json, '/' -> '_'."""
    base = path[:-5] if path.endswith(".json") else path
    return base.replace("/", "_")


def head_comparator_configs(repo: pathlib.Path, rev: str = "HEAD") -> list[str]:
    """Every comparator configuration tracked at <rev> (the set the runner checks)."""
    p = git(repo, "ls-tree", "-r", "--name-only", rev)
    if p.returncode != 0:
        return []
    return sorted(x for x in p.stdout.splitlines() if COMPARATOR_CONFIG_RE.match(x))


def _safe_rel(path, what: str) -> str:
    """A repo-relative path: no leading '/', no '..' component."""
    if not isinstance(path, str) or not path.strip():
        raise Fail(f"comparator {what} path is empty")
    pp = pathlib.PurePosixPath(path)
    if pp.is_absolute() or ".." in pp.parts:
        raise Fail(f"comparator {what} path must be repo-relative without '..': {path!r}")
    return path


_WARNED: set[str] = set()


def warn_once(msg: str) -> None:
    """A `lean-pkg: warning:` line, printed once per process (validators run many times per command)."""
    if msg not in _WARNED:
        _WARNED.add(msg)
        print(f"lean-pkg: warning: {msg}", file=sys.stderr)


def sidecar_at(repo: pathlib.Path, rev: str = "HEAD") -> dict:
    """lean-pkg-signoff.json tracked at <rev> (holes.py), strictly parsed; empty when not tracked there.
    Raises ValueError on a malformed file."""
    return parse_sidecar(git_show_bytes(repo, SIGNOFF_SIDECAR, rev), f"{SIGNOFF_SIDECAR} at {rev[:12]}")


def validate_comparator(pkg: dict, repo: pathlib.Path, comp: dict,
                        receipt_commit: str | None) -> None:
    """Re-derive a comparator PASS from bytes (see DESIGN.md). Fails closed."""
    if not isinstance(comp, dict):
        raise Fail("comparator receipt must be a JSON object")
    for f in COMPARATOR_FIELDS:
        if f not in comp:
            raise Fail(f"comparator receipt missing required field {f!r}")
    if comp.get("test_only"):
        raise Fail("comparator receipt is marked test_only (produced under the test-only digest "
                   "override); it is not evidence")
    if comp.get("partial"):
        raise Fail("comparator receipt is marked partial (comparator-receipt --slots judged only a subset of "
                   "the slots); it is diagnostic, not evidence: run comparator-receipt without --slots")
    schema = comp["schema"]
    if schema not in COMPARATOR_SCHEMAS:
        raise Fail(f"comparator schema must be one of {COMPARATOR_SCHEMAS}, got {schema!r}")
    if schema == "comparator-receipt/1":
        mechanism = "legacy"
    else:
        mechanism = comp.get("mechanism")
        if mechanism not in COMPARATOR_MECHANISMS:
            raise Fail(f"comparator mechanism must be one of {COMPARATOR_MECHANISMS}, "
                       f"got {mechanism!r}")
    commit = str(comp["commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Fail(f"comparator commit is not a full lowercase 40-hex sha: {commit!r}")
    head = git_head(repo)
    if head is None:
        raise Fail(f"cannot resolve HEAD in {repo}")
    ok, note, _docs = package_descendant(repo, commit, head, pkg)
    if not ok:
        raise Fail(f"comparator commit {commit[:12]}… != repo HEAD {head[:12]}… — "
                   f"the comparator receipt must bind to HEAD or an evidence-only "
                   f"descendant ({note})")
    if receipt_commit and commit != receipt_commit:
        raise Fail(f"comparator commit {commit[:12]}… != lower-tier receipt commit "
                   f"{receipt_commit[:12]}… — both tiers must bind to one commit")
    check_toolchain_at(repo, commit, comp["lean_toolchain"], "comparator toolchain mismatch")
    check_evidence_symlinks_at(repo, commit, formalization_rel(pkg))
    tools = comp["tools"]
    if not isinstance(tools, dict):
        raise Fail("comparator tools must be a mapping")
    if mechanism == "legacy":
        for tname in COMPARATOR_TOOLS:
            v = str(tools.get(tname) or "")
            if not re.fullmatch(r"[0-9a-f]{40}", v):
                raise Fail(f"comparator tools.{tname} must be a 40-hex commit, got {v!r}")
    else:
        if not str(tools.get("lean_toolchain") or "").strip():
            raise Fail("comparator tools.lean_toolchain is empty (toolchain mechanism)")
        for tname in COMPARATOR_TOOLCHAIN_DIGESTS:
            v = str(tools.get(tname) or "")
            if not re.fullmatch(r"[0-9a-f]{64}", v):
                raise Fail(f"comparator tools.{tname} must be a 64-hex sha256, got {v!r}")
    for f in ("host", "job_id", "generated_at"):
        if not str(comp[f]).strip():
            raise Fail(f"comparator field {f!r} is empty")
    check_timestamp(comp["generated_at"], "comparator receipt")
    if comp["result"] != "pass":
        raise Fail(f"comparator result must be 'pass', got {comp['result']!r}")
    configs = comp["configs"]
    if not isinstance(configs, list) or not configs:
        raise Fail("comparator configs must be a non-empty list")
    allowed = set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS)
    listed = [_safe_rel(c.get("path"), "config") for c in configs if isinstance(c, dict)]
    expected = head_comparator_configs(repo, commit)
    if sorted(listed) != expected:
        missing = sorted(set(expected) - set(listed))
        extra = sorted(set(listed) - set(expected))
        raise Fail(f"comparator receipt must list exactly the configs tracked at "
                   f"{commit[:12]} (bound commit; HEAD {head[:12]}): "
                   f"missing {missing}, extra/duplicate {extra}")
    try:
        sidecar = sidecar_at(repo, commit)
    except ValueError as e:
        raise Fail(str(e)) from None
    for i, cfg in enumerate(configs):
        if not isinstance(cfg, dict):
            raise Fail(f"comparator configs[{i}] is not an object")
        for f in COMPARATOR_CONFIG_FIELDS:
            if f not in cfg:
                raise Fail(f"comparator configs[{i}] missing field {f!r}")
        path = _safe_rel(cfg["path"], "config")
        if cfg["result"] != "pass":
            raise Fail(f"comparator config {path} result must be 'pass', "
                       f"got {cfg['result']!r}")
        blob = git_show_bytes(repo, path, commit)
        if blob is None:
            raise Fail(f"comparator config {path} is not tracked at {commit[:12]} (HEAD {head[:12]})")
        if sha256_bytes(blob) != str(cfg["sha256"]).lower():
            raise Fail(f"comparator config {path} sha256 mismatch against bytes at {commit[:12]}")
        try:
            blob_cfg = json.loads(blob)
        except ValueError:
            blob_cfg = None
        if not isinstance(blob_cfg, dict):
            raise Fail(f"comparator config {path} at {commit[:12]} is not a JSON object")
        # #61: a list field the committed config omits (or sets to null) means [], exactly as the
        # runner records it; any other difference, ["a"] vs [] included, is still a refusal.
        for f in ("challenge_module", "solution_module", "theorem_names",
                  "definition_names", "permitted_axioms"):
            want = blob_cfg.get(f)
            if want is None and f in COMPARATOR_LIST_FIELDS:
                want = []
            if cfg[f] != want:
                raise Fail(f"comparator config {path}: receipt {f} differs from the "
                           f"config at {commit[:12]} ({cfg[f]!r} vs {want!r})")
        names = cfg["theorem_names"]
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) and n for n in names):
            raise Fail(f"comparator config {path}: theorem_names must be a "
                       f"non-empty list of strings")
        ax = cfg["permitted_axioms"]
        if not isinstance(ax, list) or not all(isinstance(a, str) for a in ax):
            raise Fail(f"comparator config {path}: permitted_axioms must be a "
                       f"list of strings")
        extra = sorted(set(ax) - allowed)
        if extra:
            raise Fail(f"comparator config {path}: permitted_axioms {extra} outside "
                       f"allowed set {sorted(allowed)}")
        # Definition holes (value-blind in comparator) need a human sign-off, judged against the
        # config bytes and lean-pkg-signoff.json at the bound commit, never the receipt's copy; a
        # copy the receipt does carry (`signoff`, which the page renders) must equal it.
        try:
            so = check_definition_holes(blob_cfg, f"comparator config {path}", cfg_rel=path,
                                        cfg_bytes=blob, sidecar=sidecar, warn=warn_once)
        except ValueError as e:
            raise Fail(str(e)) from None
        if "signoff" in cfg and cfg["signoff"] != so:
            raise Fail(f"comparator config {path}: the receipt's signoff copy differs from the sign-off "
                       f"in {SIGNOFF_SIDECAR} at {commit[:12]} (the receipt must record what was signed)")
        log_rel = _safe_rel(cfg["log"], "log")
        if not log_rel.startswith(".lean-receipts/"):
            raise Fail(f"comparator log must live under .lean-receipts/: {log_rel!r}")
        bound = f".lean-receipts/{commit[:12]}-comparator-{cfg_name(path)}.log"
        if log_rel != bound:
            raise Fail(f"comparator log for {path} must be {bound}, got {log_rel!r}")
        if (repo / ".lean-receipts").is_symlink():
            raise Fail(f"{repo}/.lean-receipts is a symlink; evidence must live inside the repo")
        log_p = repo / log_rel
        if log_p.is_symlink():
            raise Fail(f"comparator log {log_rel} is a symlink; evidence must be a regular file")
        if not log_p.is_file():
            # Field report #5: a comparator log is looked up only under the package checkout, never
            # beside the receipt (unlike the redacted verify log). The hint names where it looked and
            # how to bring preserved evidence back; the refusal itself is unchanged.
            raise Fail(f"comparator log missing: {log_rel} (package checkout {repo.absolute()}; "
                       f"tried {log_p.absolute()}). Comparator logs resolve relative to the package "
                       f"checkout only, never beside the receipt; if they were preserved elsewhere, "
                       f"restore each to the path its configs[].log names, byte-exact, and check it "
                       f"against its log_sha256 (docs/receipts.md, \"Restoring preserved evidence\"); "
                       f"nothing is rerun or resealed")
        log_bytes = log_p.read_bytes()
        # #21.2: a log produced after the bound commit cannot be read at that commit; once committed
        # (at HEAD, an evidence-only descendant), the committed bytes are the evidence, so a working
        # copy that differs from them is refused here rather than first at promote.
        committed_log = git_show_bytes(repo, log_rel, "HEAD")
        if committed_log is not None and committed_log != log_bytes:
            raise Fail(f"comparator log {log_rel} differs from its copy committed at HEAD {head[:12]}; "
                       f"the committed bytes are the evidence: restore them (git checkout -- {log_rel}) "
                       f"or commit the new log with a receipt that names it")
        if sha256_bytes(log_bytes) != str(cfg["log_sha256"]).lower():
            raise Fail(f"comparator log_sha256 mismatch for {log_rel}")
        lines = log_bytes.decode("utf-8", "replace").splitlines()
        if not comparator_pass("\n".join(lines), mechanism):
            raise Fail(f"comparator log {log_rel} has no PASS line for the {mechanism} mechanism "
                       f"({COMPARATOR_PASS_LINE!r} as a full line)")


def binding_lines(pkg: dict, repo: pathlib.Path, commit: str, toolchain, checker=None) -> list[str]:
    """The ✓ lines of a validated binding (#26.2): commit vs HEAD, the formalization.yaml rule, the
    toolchain at the bound commit. Called only after the validator passed, so every line is a pass."""
    head = git_head(repo) or ""
    _ok, note, docs = package_descendant(repo, commit, head, pkg, checker=checker)
    out = [f"✓ binding: commit {commit[:12]} is HEAD" if commit == head
           else docs_admission_line(head, commit, note) if docs is not None
           else f"✓ binding: HEAD {head[:12]}: {note}"]
    rel = formalization_rel(pkg)
    if rel and commit != head:
        changed = git(repo, "diff", "--no-renames", "--name-only", "--relative", commit, head, "--", rel)
        out.append(f"✓ {rel}: only derived keys changed since {commit[:12]} (evidence-only rule)"
                   if changed.stdout.strip() else f"✓ {rel}: unchanged since {commit[:12]}")
    elif not rel:
        out.append("✓ formalization.yaml: no registry pointer, so no exception was applied")
    out.append(f"✓ lean_toolchain {str(toolchain).strip()} = lean-toolchain committed at {commit[:12]}")
    if git_show_bytes(repo, "./lake-manifest.json", commit) is not None:
        out.append(f"✓ lake-manifest.json at {commit[:12]}: no path dependency outside the package directory")
    out.append(f"✓ no symlink at {commit[:12]} leaves the package directory or points into .lean-receipts/")
    return out


def receipt_check_lines(pkg: dict, repo: pathlib.Path, receipt: dict) -> list[str]:
    allowed = sorted(set((pkg.get("gate") or {}).get("axioms_allowed") or DEFAULT_AXIOMS))
    committed = committed_mathlib(repo, receipt["commit"])
    rev, c12 = str(receipt["mathlib_rev"]).strip(), receipt["commit"][:12]
    if committed == NO_MATHLIB:
        mathlib = (f"✓ mathlib_rev {NO_MATHLIB}: lake-manifest.json committed at {c12} has no mathlib package"
                   if rev == NO_MATHLIB else
                   f"· mathlib_rev {rev} not checked: lake-manifest.json committed at {c12} has no mathlib "
                   f"package (a receipt written now says {NO_MATHLIB!r})")
    elif committed:
        mathlib = f"✓ mathlib_rev {rev} = the mathlib rev in lake-manifest.json committed at {c12}"
    else:
        mathlib = f"· mathlib_rev not checked: no readable mathlib entry in lake-manifest.json at {c12}"
    return binding_lines(pkg, repo, receipt["commit"], receipt["lean_toolchain"], receipt.get("checker")) + [
        mathlib,
        "✓ sorry_count 0",
        f"✓ axioms {sorted(receipt['axioms'])} within {allowed}",
        *claim_axioms_lines(receipt),
        *sacct_seal_lines(receipt),
        *ci_check_lines(receipt, repo),
        f"✓ mathlib_rev, report_sha256, job_id {receipt['job_id']}, checker, generated_at well-formed"]


def ci_check_lines(receipt: dict, repo: pathlib.Path) -> list[str]:
    """The receipt's additive `ci` block (a GitHub Actions run, proposal 0008) at level 1, when present and
    valid (validate_receipt refuses an invalid one), or the note that a gha- receipt without it backs nothing."""
    from . import ci_github
    return ci_github.check_lines(receipt, repo)


def sacct_seal_lines(receipt: dict) -> list[str]:
    """The receipt's additive `slurm_sacct` block (`receipt slurm --seal-sacct`), when present and valid
    (validate_receipt refuses an invalid one)."""
    b = receipt.get("slurm_sacct")
    if not isinstance(b, dict):
        return []
    return [f"✓ slurm_sacct: job {b.get('job')} COMPLETED, exit 0:0, {b.get('start')} .. {b.get('end')} contains "
            f"generated_at; the sealed record {b.get('file')} is committed at HEAD and hashes to "
            f"{str(b.get('sha256'))[:12]}"]


def claim_axioms_lines(receipt: dict) -> list[str]:
    """The receipt's additive `claim_axioms` key (AX1), when present and valid (validate_receipt refuses an
    invalid one): every claim's axioms read from the built environment, not only the printed theorems."""
    ca = receipt.get("claim_axioms")
    if not isinstance(ca, dict):
        return []
    extra = sorted({a for e in ca.get("nonstandard") or [] for a in e.get("axioms") or []})
    return [f"✓ claim_axioms: {ca.get('checked')} claim(s) read from the built environment (every claim in "
            f"claims.yaml), none missing" + (f"; beyond the standard three, allowed: {extra}" if extra else "")]


def build_provenance_line(bp: dict) -> str:
    """One line for a comparator receipt's additive build_provenance (#56); never validated."""
    src = bp.get("source")
    tail = f" (source: {src})" if src else ""
    # Lake's artifact cache writes oleans without compiling them: never counted as compiled (lake_hygiene)
    cached = [f"{bp[k]} {what}" for k, what in (("restored", "olean(s) restored, not compiled"),
                                                 ("cache_reused", "module(s) Lake reports reused from a cache"))
              if isinstance(bp.get(k), int) and bp[k]]
    cached_tail = f"; {', '.join(cached)}" if cached else ""
    if bp.get("compiled") is True:
        n = bp.get("modules_built")
        return (f"build: compiled {n} module(s)" if isinstance(n, int) else "build: compiled") + tail + cached_tail
    if bp.get("prebuilt") is True:
        return f"build: prebuilt (source: {src or 'not declared'})" + cached_tail
    if bp.get("compiled") is False:
        return "build: no build stage ran (skipped)" + tail
    return "build: provenance not recorded (the setup was prepared before build_provenance)" + tail


def comparator_check_lines(pkg: dict, repo: pathlib.Path, comp: dict, lower_commit: str | None,
                           with_binding: bool = True) -> list[str]:
    mech = "legacy" if comp.get("schema") == "comparator-receipt/1" else comp.get("mechanism")
    out = binding_lines(pkg, repo, comp["commit"], comp["lean_toolchain"]) if with_binding else []
    out.append(f"✓ comparator: {len(comp['configs'])} config(s) re-derived from bytes at {comp['commit'][:12]}, "
               f"each log PASS ({mech} mechanism)")
    out.append(f"✓ comparator commit = lower-tier receipt commit {lower_commit[:12]}" if lower_commit
               else "· no lower-tier receipt to pair with (commit equality not checked)")
    if isinstance(comp.get("build_provenance"), dict):   # #56: printed, never validated
        out.append(f"· {build_provenance_line(comp['build_provenance'])}")
    om = comp.get("olean_manifest")
    if isinstance(om, dict):                             # printed, never validated (no oleans here to rehash)
        out.append(f"· olean manifest: {om.get('files')} compiled module file(s), sha256 "
                   f"{str(om.get('sha256'))[:12]}… (captured at {om.get('captured')}; checked by the runner "
                   f"before and after the comparison)")
    return out


def claims_schema_at(name: str, pkg: dict, repo: pathlib.Path, commit: str) -> dict:
    """#58: the claims/1 schema check (claims.schema_check_text, the validator `claims sync` uses) on
    the package's claims file as tracked at the gated commit, never the working tree, then
    `claims sync`'s declaration check (claims.decl_problems) with each claim's file read at that same
    commit. {checked, commit, file, ok, claims, errors, declarations: {checked, ok, problems}}; `ok` is
    False on either kind of problem; checked False (ok null) when no file is tracked there."""
    from . import claims as claims_mod
    from . import index as idx
    rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
    blob = git_show_bytes(repo, rel, commit)
    unchecked = {"checked": False, "ok": None, "problems": []}
    if blob is None:
        return {"checked": False, "commit": commit, "file": rel, "ok": None, "claims": None, "errors": [],
                "declarations": unchecked}
    text = blob.decode("utf-8", errors="replace")
    n, errors = claims_mod.schema_check_text(text, rel, name)
    decls = unchecked
    try:
        listed = claims_mod.load_claims_text(text, rel).get("claims")
    except Fail:
        listed = None
    # decl_problems needs a claim's id, decl and file; with schema errors present, only the claims
    # whose three fields are well formed are looked up (no other rule is added).
    usable = [c for c in (listed if isinstance(listed, list) else [])
              if isinstance(c, dict) and isinstance(c.get("id"), str) and isinstance(c.get("decl"), str)
              and idx._is_rel(c.get("file")) and str(c["file"]).endswith(".lean")]
    if usable:
        read = claims_mod._reader(repo, commit)
        problems = claims_mod.decl_problems(read, usable) + claims_mod.evidence_problems(read, usable)
        decls = {"checked": True, "ok": not problems, "problems": problems}
    return {"checked": True, "commit": commit, "file": rel, "ok": not errors and decls["ok"] is not False,
            "claims": n, "errors": errors, "declarations": decls}


def claims_problems(cs: dict) -> list[str]:
    """Schema errors first, then declaration problems in `claims sync`'s wording."""
    return list(cs["errors"]) + list(cs.get("declarations", {}).get("problems", []))


def claims_schema_line(cs: dict) -> str:
    at = f"{cs['file']} at {str(cs['commit'])[:12]}"
    if not cs["checked"]:
        return f"· no {cs['file']} tracked at {str(cs['commit'])[:12]} (claims schema not checked)"
    if cs["ok"]:
        found = ", declarations found" if cs["declarations"]["checked"] else ""
        return f"✓ {at}: schema ok ({cs['claims']} claims){found}"
    problems = claims_problems(cs)
    more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
    return f"✗ {at}: {problems[0]}{more}"


def load_json_object(path_arg, what: str) -> tuple[pathlib.Path, dict]:
    p = pathlib.Path(str(path_arg)).expanduser()
    if not p.is_file():
        raise Fail(f"{what} not found: {p}")
    try:
        obj = json.loads(p.read_text())
    except ValueError as e:
        raise Fail(f"{what} is not valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise Fail(f"{what} must be a JSON object")
    return p, obj


def locate_evidence_arg(path_arg, what: str, name: str, repo: pathlib.Path,
                        resolved: list | None = None) -> pathlib.Path:
    """The file a --receipt / --comparator argument names (field report #5). A path that exists as given
    (relative to the caller's cwd, or absolute) is used as given. A relative path that does not exist
    from the cwd but exists under the package's registered checkout (or --repo DIR) is resolved there,
    and the resolution is announced on stderr and appended to `resolved`: the same relative path anchored
    at the checkout, never another matching receipt. Otherwise the refusal names the caller's cwd, the
    checkout, every path tried and how to pass the file."""
    p = pathlib.Path(str(path_arg)).expanduser()
    if p.is_file() or p.is_absolute():
        return p
    under = repo / p
    if under.is_file():
        note = (f"{name}: {what} {path_arg} does not exist relative to the cwd {pathlib.Path.cwd()}; "
                f"resolved against the package checkout: {under}")
        print(f"lean-pkg: note: {note}", file=sys.stderr)
        if resolved is not None:
            resolved.append({"what": what, "arg": str(path_arg), "path": str(under), "base": str(repo)})
        return under
    raise Fail(f"{what} not found: {path_arg} (caller cwd {pathlib.Path.cwd()}; package checkout {repo}; "
               f"tried {pathlib.Path.cwd() / p} and {under}). Pass an absolute path, run from the checkout, "
               f"or give a path relative to the checkout; for `gate --check` / `comparator --check` on "
               f"another checkout pass --repo DIR")


def replaced_comparator_rel(repo: pathlib.Path, commit: str) -> str:
    """Where `comparator --replace` keeps the older receipt for `commit` (#21.1): beside it, never
    overwritten: .lean-receipts/<c12>-comparator.replaced-<UTC stamp>[-N].json."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f".lean-receipts/{commit[:12]}-comparator.replaced-{stamp}"
    rel, n = f"{base}.json", 1
    while (repo / rel).exists() or (repo / rel).is_symlink():
        n += 1
        rel = f"{base}-{n}.json"
    return rel


def evidence_dest_problem(repo: pathlib.Path, dest_rel: str) -> str | None:
    """Why gate/comparator must not write `dest_rel` (under .lean-receipts/): .lean-receipts itself or the
    destination is a symlink (a planted link would carry the write outside the repository), or the
    directory is not a directory. None when the write may go ahead."""
    d = repo / ".lean-receipts"
    if d.is_symlink():
        return f"{d} is a symlink; refusing to write evidence through it (nothing copied or recorded)"
    if d.exists() and not d.is_dir():
        return f"{d} is not a directory (nothing copied or recorded)"
    dest = repo / dest_rel
    if dest.is_symlink():
        return f"{dest} is a symlink; refusing to write through it (remove it; nothing copied or recorded)"
    if dest.exists() and not dest.is_file():
        return f"{dest} is not a regular file (nothing copied or recorded)"
    return None


def plan_comparator_copy(repo: pathlib.Path, commit: str, src: pathlib.Path,
                         replace: bool = False) -> dict:
    """Every check copy_comparator makes, before anything is written (gate runs it before it copies the
    lower-tier receipt, so a refusal writes nothing): {dest_rel, action: present | copy | replace, moved}."""
    dest_rel = f".lean-receipts/{commit[:12]}-comparator.json"
    dest = repo / dest_rel
    why = evidence_dest_problem(repo, dest_rel)
    if why:
        raise Fail(why)
    if not dest.exists():
        return {"dest_rel": dest_rel, "action": "copy", "moved": None}
    if recorded_json(repo, dest_rel) == json.loads(src.read_text()):
        return {"dest_rel": dest_rel, "action": "present", "moved": None}
    if not replace:
        raise Fail(f"{dest} already holds a different comparator receipt for commit {commit[:12]}; "
                   f"to keep it, pass that file (--receipt {dest}); to record the newer one, re-run "
                   f"with --replace (the older file is kept beside it as "
                   f".lean-receipts/{commit[:12]}-comparator.replaced-<UTC stamp>.json and the event "
                   f"is comparator-replaced)")
    moved = replaced_comparator_rel(repo, commit)
    return {"dest_rel": dest_rel, "action": "replace", "moved": moved}


def apply_comparator_copy(repo: pathlib.Path, plan: dict, src: pathlib.Path) -> tuple[str, str, str | None]:
    """(path of the receipt in the repo, note, where the replaced older receipt now lives or None)."""
    dest_rel, moved = plan["dest_rel"], plan["moved"]
    dest = repo / dest_rel
    if plan["action"] == "present":
        return dest_rel, f"comparator receipt already present at {dest_rel} (not re-copied)", None
    if moved:
        os.replace(dest, repo / moved)
    dest.parent.mkdir(exist_ok=True)
    shutil.copy2(src, dest)
    note = (f"comparator receipt copied to {dest} — NOT committed there; "
            f"commit it with its logs in the verified repo yourself")
    if moved:
        note += f"; the older receipt for this commit is kept at {moved} (commit that too)"
    return dest_rel, note, moved


def copy_comparator(repo: pathlib.Path, commit: str, src: pathlib.Path,
                    replace: bool = False) -> tuple[str, str, str | None]:
    """(path of the receipt in the repo, note, where the replaced older receipt now lives or None)."""
    return apply_comparator_copy(repo, plan_comparator_copy(repo, commit, src, replace), src)


REPORT_LOG_SUFFIX = "-verify.redacted.log"
# A compressed sealed log (`receipt slurm|systemd --compress-log`) is `<log name>.gz`: deterministic gzip
# (level 9, mtime 0, no file name in the header). report_sha256 stays the SHA-256 of the UNCOMPRESSED log;
# the receipt's additive `report_compressed` {format: gzip, sha256: <of the .gz>, bytes: <decoded (uncompressed)
# length>} binds the .gz itself and bounds its decompression.
REPORT_GZ_SUFFIX = ".gz"
REPORT_GZ_LEVEL = 9
# The decompression bound for a .gz whose receipt records no `report_compressed.bytes` (a log compressed by
# hand for an older receipt): output past it is refused, never hashed further (gzip-bomb guard).
REPORT_GZ_DEFAULT_CAP = 4 << 30
_CHUNK = 1 << 20


def gzip_deterministic(data: bytes) -> bytes:
    """`data` as gzip with a fixed header (no file name, mtime 0, OS byte 255) at level 9: the same bytes
    for the same input and zlib, on every host and at every time."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", compresslevel=REPORT_GZ_LEVEL, fileobj=buf, mtime=0) as g:
        g.write(data)
    return buf.getvalue()


def gunzip_digest(src, limit: int, what: str) -> tuple[str, int]:
    """(sha256, length) of the decompressed stream of `src` (a path or bytes), read in chunks and never
    past `limit` bytes of output: more refuses (gzip-bomb guard), and so does a stream that is not valid
    gzip (bad header, CRC or length check, truncated)."""
    h, n = hashlib.sha256(), 0
    try:
        with (open(src, "rb") if isinstance(src, (str, os.PathLike)) else io.BytesIO(src)) as raw, \
                gzip.GzipFile(fileobj=raw, mode="rb") as g:
            while True:
                chunk = g.read(min(_CHUNK, limit - n + 1))
                if not chunk:
                    break
                n += len(chunk)
                if n > limit:
                    raise Fail(f"{what} decompresses to more than the {limit} bytes the receipt allows "
                               f"(report_compressed.bytes); refused without reading further")
                h.update(chunk)
    except (OSError, EOFError, zlib.error) as e:
        raise Fail(f"{what} is not a valid gzip stream ({e.__class__.__name__}: {e}); the compressed log "
                   f"cannot be checked against report_sha256") from None
    return h.hexdigest(), n


def report_compressed_bound(receipt: dict) -> tuple[int, str | None]:
    """(decompression limit, the .gz SHA-256 or None) from the receipt's optional `report_compressed`;
    a malformed value refuses. It never replaces report_sha256, which is always the uncompressed hash."""
    rc = receipt.get("report_compressed")
    if rc is None:
        return REPORT_GZ_DEFAULT_CAP, None
    if not isinstance(rc, dict) or rc.get("format") != "gzip":
        raise Fail("report_compressed must be {format: \"gzip\", sha256, bytes}, got "
                   f"{json.dumps(rc)[:80]}")
    gz_sha, size = rc.get("sha256"), rc.get("bytes")
    if not (isinstance(gz_sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", gz_sha)):
        raise Fail("report_compressed.sha256 must be 64 hex chars (the SHA-256 of the .gz file)")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise Fail("report_compressed.bytes must be a non-negative integer (the uncompressed log's length)")
    if gz_sha.lower() == str(receipt.get("report_sha256", "")).lower():
        raise Fail("report_compressed.sha256 equals report_sha256: report_sha256 must be the SHA-256 of the "
                   "uncompressed log, never of the .gz")
    return size, gz_sha.lower()


def _file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def report_log_name(receipt: dict) -> str:
    return f"{str(receipt['commit'])[:12]}-{receipt_job_slug(receipt['job_id'])}{REPORT_LOG_SUFFIX}"


def report_log_check(repo: pathlib.Path, receipt: dict, receipt_path: pathlib.Path) -> tuple[str, dict]:
    """report_sha256 against the redacted verify log `receipt slurm|systemd` writes beside the receipt
    (<c12>-<job slug>-verify.redacted.log, or that name + .gz with --compress-log): looked for beside the
    --receipt file, in the package's .lean-receipts/ and tracked there at HEAD. Every copy found must hash
    to report_sha256 (a mismatch refuses); a .gz copy is stream-decompressed (bounded by
    report_compressed.bytes) and its UNCOMPRESSED bytes must hash to report_sha256, with that length, and
    the .gz itself to report_compressed.sha256 when recorded. None found, or a copy that cannot be read,
    is a refusal too (report_log_missing): a receipt whose report_sha256 binds nothing is not evidence.
    (line, {checked, log, where})."""
    name = report_log_name(receipt)
    gz = name + REPORT_GZ_SUFFIX
    want = str(receipt["report_sha256"]).lower()
    limit, gz_want = report_compressed_bound(receipt)
    found: list[tuple[str, str, object]] = []      # (where, file name, path or bytes)
    seen: set[str] = set()
    for where, base in (("beside the receipt", receipt_path.parent), (".lean-receipts/", repo / ".lean-receipts")):
        for fname in (name, gz):
            path = base / fname
            key = os.path.realpath(path)
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            found.append((where, fname, path))
    for fname in (name, gz):
        blob = git_show_bytes(repo, f"./.lean-receipts/{fname}", "HEAD")
        if blob is not None:
            found.append(("tracked at HEAD", fname, blob))
    if not found:
        raise Fail(report_log_missing(repo, receipt, receipt_path))
    try:
        wheres = _report_log_verify(found, name, gz, want, limit, gz_want)
    except OSError as e:
        raise Fail(report_log_missing(repo, receipt, receipt_path,
                                      f"a copy cannot be read ({e.__class__.__name__}: {e.strerror or e})")) from None
    return (f"✓ report_sha256 = sha256 of {name} ({', '.join(wheres)})",
            {"checked": True, "log": name, "where": wheres})


REPORT_LOG_RESTORE = 'docs/receipts.md, "Restoring preserved evidence"'
REPORT_LOG_MISSING = "report log missing: "


def report_log_rels(repo: pathlib.Path, receipt: dict) -> list[str]:
    """The sealed log copies (.log, .log.gz) of a lower-tier receipt present under the package's
    .lean-receipts/ (package-relative): the files `promote` needs committed beside the receipt."""
    try:
        name = report_log_name(receipt)
    except (Fail, KeyError):
        return []
    return [f".lean-receipts/{f}" for f in (name, name + REPORT_GZ_SUFFIX) if (repo / ".lean-receipts" / f).is_file()]


def recorded_report_log_problem(repo: pathlib.Path, receipt: dict, rel: str) -> str | None:
    """report_log_check for a recorded lower-tier receipt (`rel`, package-relative): None when its sealed
    log is there and hashes to report_sha256, else the refusal text (missing, unreadable or mismatched).
    Every consumer that judges a recorded receipt (ledger, next, comparator, palomar, promote) uses this, so
    none accepts a receipt `gate` would refuse."""
    try:
        report_log_check(repo, receipt, repo / rel)
    except Fail as e:
        return str(e)
    return None


SEALED_LOG_TEXT_CAP = 256 << 20     # sealed_log_text reads no more than this (uncompressed) into memory


def sealed_log_text(repo: pathlib.Path, receipt: dict, rel: str) -> str | None:
    """The text of a recorded lower-tier receipt's sealed verify log: the first copy report_log_check looks at
    (beside the receipt `rel`, in .lean-receipts/, tracked there at HEAD; the .log, else its .gz decompressed)
    whose uncompressed bytes hash to report_sha256. None when there is none, it cannot be read, or it is larger
    than SEALED_LOG_TEXT_CAP: a reader of the log's facts (the claims ledger, proposal 0003) then has none."""
    try:
        name = report_log_name(receipt)
        want = str(receipt["report_sha256"]).lower()
    except (Fail, KeyError, TypeError):
        return None
    cands: list = []
    for base in ((repo / rel).parent, repo / ".lean-receipts"):
        cands += [(base / name, False), (base / (name + REPORT_GZ_SUFFIX), True)]
    for fname, unpack in ((name, False), (name + REPORT_GZ_SUFFIX, True)):
        cands.append((lambda f=fname: git_show_bytes(repo, f"./.lean-receipts/{f}", "HEAD"), unpack))
    for src, unpack in cands:
        try:
            if isinstance(src, pathlib.Path):
                if not src.is_file() or src.stat().st_size > SEALED_LOG_TEXT_CAP:
                    continue
                data = src.read_bytes()
            else:
                data = src()
                if data is None:
                    continue
            if unpack:
                with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as g:
                    data = g.read(SEALED_LOG_TEXT_CAP + 1)
                if len(data) > SEALED_LOG_TEXT_CAP:
                    continue
        except (OSError, EOFError, zlib.error):
            continue
        if sha256_bytes(data) == want:
            return data.decode("utf-8", "replace")
    return None


def report_log_missing(repo: pathlib.Path, receipt: dict, receipt_path: pathlib.Path,
                       why: str = "none found") -> str:
    """The refusal for a lower-tier receipt whose redacted verify log is not there to hash: where it was
    looked for, the path it must be restored to, and how (byte-exact, never rerun or resealed)."""
    name = report_log_name(receipt)
    want = str(receipt.get("report_sha256") or "").lower()
    return (f"{REPORT_LOG_MISSING}{name} (or {name}{REPORT_GZ_SUFFIX}): {why}; looked beside the receipt "
            f"({receipt_path.parent.absolute()}), in {(repo / '.lean-receipts').absolute()} and tracked there at "
            f"HEAD. Without it report_sha256 binds nothing, so the receipt is not evidence. Restore the sealed "
            f"redacted log byte-exact to .lean-receipts/{name} (a copy kept under another name may be copied "
            f"there when its SHA-256 is the receipt's report_sha256 {want[:16]}...) and commit it ({REPORT_LOG_RESTORE}); "
            f"never rerun or reseal it. A receipt whose log is lost needs a new run")


def _report_log_verify(found: list, name: str, gz: str, want: str, limit: int, gz_want: str | None) -> list[str]:
    """Hash every copy report_log_check found; a mismatch refuses. [where]; OSError propagates."""
    wheres = []
    for where, fname, src in found:
        if fname == name:
            got = _file_sha256(src) if isinstance(src, pathlib.Path) else sha256_bytes(src)
            if got != want:
                raise Fail(f"report_sha256 mismatch: {name} ({where}) hashes to {got}, the receipt says {want}; "
                           f"the redacted log is not the one the receipt binds")
            wheres.append(where)
            continue
        if gz_want is not None:
            got = _file_sha256(src) if isinstance(src, pathlib.Path) else sha256_bytes(src)
            if got != gz_want:
                raise Fail(f"report_compressed.sha256 mismatch: {gz} ({where}) hashes to {got}, the receipt says "
                           f"{gz_want}; the compressed log is not the one the receipt binds")
        got, n = gunzip_digest(src, limit, f"{gz} ({where})")
        if got != want:
            raise Fail(f"report_sha256 mismatch: {gz} ({where}) decompresses to bytes hashing to {got}, the receipt "
                       f"says {want}; the redacted log is not the one the receipt binds (report_sha256 is the "
                       f"SHA-256 of the uncompressed log, never of the .gz)")
        if gz_want is not None and n != limit:
            raise Fail(f"report_compressed.bytes mismatch: {gz} ({where}) decompresses to {n} bytes, the receipt "
                       f"says {limit}")
        wheres.append(f"{where}, decompressed from .gz")
    return wheres


def plan_report_log_copy(repo: pathlib.Path, receipt: dict, receipt_path: pathlib.Path) -> list[tuple[pathlib.Path, str]]:
    """The sealed log copies `gate` adds beside the canonical receipt .lean-receipts/<c12>-<job>.json, so
    that copy still verifies (field report: a receipt passed from a nested or outside path left its log
    behind): each form (.log, .log.gz) found beside the --receipt file, when .lean-receipts/ holds neither.
    Run after report_log_check (which verified every copy). [(source, dest_rel)]; refusals as for the
    receipt copy (a symlinked .lean-receipts or destination)."""
    name = report_log_name(receipt)
    canon = repo / ".lean-receipts"
    src_dir = receipt_path.parent
    if canon.is_dir() and os.path.realpath(src_dir) == os.path.realpath(canon):
        return []
    forms = (name, name + REPORT_GZ_SUFFIX)
    plan = [(src_dir / f, f".lean-receipts/{f}") for f in forms if (src_dir / f).is_file()]
    if not plan:
        return []
    for f in forms:
        why = evidence_dest_problem(repo, f".lean-receipts/{f}")
        if why:
            raise Fail(why)
    if any((canon / f).exists() for f in forms):
        return []
    return plan


def receipt_private_strings(blobs: list[tuple[str, bytes]], refuse: bool,
                            sink: list | None = None) -> list[dict]:
    """Scan receipt bytes (keys and values) for private strings before they are copied into the
    repository. Each hit is {"field", "kind"} (the private text itself is never echoed). With
    `refuse`, any hit is a refusal; otherwise the hits are returned (and appended to `sink`) and
    the caller prints them with warn_private once it knows what happened to the receipt."""
    from .index import private_string_hits
    hits = [{"field": f, "kind": k} for where, blob in blobs
            for f, k, _ in private_string_hits(blob, where)]
    if hits and refuse:
        raise Fail("--refuse-private: receipt holds private strings, nothing copied: "
                   + "; ".join(f"{h['field']} ({h['kind']})" for h in hits))
    if sink is not None:
        sink.extend(hits)
    return hits


# What happened to the receipt, by outcome event, for the private-string WARN line. A receipt that was
# accepted says "kept"; only a refusal for another reason says nothing was copied (field report: the old
# default read "refused" on a receipt `gate --check` had accepted).
PRIVATE_WARN_OUTCOME = {
    "gate-passed": "kept: the receipt is now in .lean-receipts/ as is",
    "regated": "kept: the receipt is now in .lean-receipts/ as is",
    "comparator-attached": "kept: the receipt is now in .lean-receipts/ as is",
    "comparator-replaced": "kept: the receipt is now in .lean-receipts/ as is",
    "already-gated": "kept: already recorded; nothing copied now",
    "already-attached": "kept: already recorded; nothing copied now",
    "gate-checked": "not refused for it; nothing copied (--check)",
    "comparator-checked": "not refused for it; nothing copied (--check)",
    None: "nothing copied: refused for another reason, below",
}


def warn_private(hits: list[dict], event: str | None) -> None:
    outcome = PRIVATE_WARN_OUTCOME.get(event, PRIVATE_WARN_OUTCOME[None])
    for h in hits:
        print(f"WARN: receipt field {h['field']} contains a {h['kind']} "
              f"({outcome}; --refuse-private refuses such a receipt)", file=sys.stderr)


def _with_private_warnings(fn, *args, **kwargs) -> tuple[list[str], dict]:
    """Run do_gate/do_comparator's body and print the private-string WARN lines worded for
    the outcome: copied, already recorded, --check, or refused for another reason."""
    sink: list[dict] = []
    try:
        out, payload = fn(*args, private_sink=sink, **kwargs)
    except Fail:
        warn_private(sink, None)
        raise
    warn_private(sink, payload.get("event"))
    return out, payload


def recorded_json(repo: pathlib.Path, rel) -> dict | None:
    if not rel:
        return None
    try:
        return json.loads((repo / rel).read_text())
    except Exception:
        return None


REGATE_STATUSES = ("verified-private", "public")


def to_gate_pending_cmd(name: str, commit: str) -> str:
    """The exact move an `active` package needs before its first gate (field report: the receipt's
    `next:` hint led to a gate refusal that did not say how to get to gate-pending)."""
    what = f"receipt for {commit[:12]} ready" if commit else "receipt ready"
    return f'`lean-pkg update {name} --status gate-pending --reason "{what}"`'


def regate_status_problem(name: str, pkg: dict) -> str | None:
    """Why `gate --regate` cannot apply to this package (#67), or None: it re-gates a gated package, so the
    status is verified-private (or public, which `gate` already accepts) and a gate receipt is recorded."""
    status = pkg.get("status")
    if status not in REGATE_STATUSES:
        return (f"{name}: --regate re-gates a gated package (verified-private or public), got {status!r}; "
                f"use `lean-pkg gate {name} --receipt R` without --regate")
    if not (pkg.get("gate") or {}).get("receipt"):
        return (f"{name}: --regate: no gate receipt is recorded, so there is nothing to re-gate; "
                f"use `lean-pkg gate {name} --receipt R` without --regate")
    return None


def regate_lineage(name: str, pkg: dict, repo: pathlib.Path, commit: str,
                   reason: str | None) -> tuple[str | None, bool, str | None]:
    """(previous_commit, non_descendant, problem) for `gate --regate` (#67): the new receipt's commit must
    descend from the commit of the recorded gate receipt (`git merge-base --is-ancestor`); a --reason
    accepts any other commit, recorded as non_descendant. An unreadable recorded receipt has no commit to
    compare, which needs a --reason too."""
    rec = recorded_json(repo, (pkg.get("gate") or {}).get("receipt"))
    prev = str(rec["commit"]) if isinstance(rec, dict) and rec.get("commit") else None
    if prev and _is_ancestor(repo, prev, commit):
        return prev, False, None
    if reason:
        return prev, True, None
    if prev is None:
        return None, False, (f"{name}: --regate: the recorded gate receipt "
                             f"{(pkg.get('gate') or {}).get('receipt')!r} is missing or has no commit, so the "
                             f"new commit cannot be checked as its descendant; pass --reason TEXT to re-gate "
                             f"anyway (recorded as non_descendant)")
    return prev, False, (f"{name}: --regate: receipt commit {commit[:12]} is not a descendant of the gated "
                         f"commit {prev[:12]}; pass --reason TEXT to re-gate it anyway (recorded as "
                         f"non_descendant: true)")


def _same_rev(a: str, b: str) -> bool:
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


# The explicit mathlib_rev of a package without Mathlib: its lake-manifest.json is readable and lists no
# `mathlib` package. A value, not a sentinel: it is compared with the manifest like any rev.
NO_MATHLIB = "none"


def manifest_mathlib(blob: bytes | None) -> str | None:
    """The mathlib rev a lake-manifest.json pins; NO_MATHLIB when it is readable and lists no `mathlib`
    package; None when it is absent or unreadable (or lists mathlib without a rev)."""
    try:
        data = json.loads(blob.decode("utf-8")) if blob is not None else None
    except (ValueError, UnicodeDecodeError):
        return None
    pkgs = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(pkgs, list):
        return None
    for p in pkgs:
        if isinstance(p, dict) and str(p.get("name", "")).casefold() in ("mathlib", "«mathlib»"):
            return str(p["rev"]).strip() if p.get("rev") else None
    return NO_MATHLIB


def committed_mathlib(repo: pathlib.Path, commit: str) -> str | None:
    """manifest_mathlib of lake-manifest.json committed at `commit`."""
    return manifest_mathlib(git_show_bytes(repo, "./lake-manifest.json", commit))


def committed_mathlib_rev(repo: pathlib.Path, commit: str) -> str | None:
    """The mathlib package's rev in lake-manifest.json committed at `commit`; None when unreadable or when
    the package has no Mathlib (committed_mathlib tells the two apart)."""
    rev = committed_mathlib(repo, commit)
    return None if rev == NO_MATHLIB else rev


def receipt_pin_changes(pkg: dict, repo: pathlib.Path, receipt: dict) -> tuple[dict, dict]:
    """(changes, kept) for the package's lean_toolchain and mathlib_pin against the receipt's lean_toolchain and
    mathlib_rev (field report: a deliberate pin bump, gated, left `list` on the old pin). A receipt value is
    adopted only when it agrees with the file committed at the receipt's commit (`lean-toolchain`, the mathlib
    rev in `lake-manifest.json`); otherwise the recorded value is kept and `kept` says why, {field: {receipt,
    manifest}}; never a refusal. validate_receipt already refuses a mathlib_rev that disagrees with a readable
    manifest, so `kept` for mathlib_pin is only the case of no readable mathlib entry (manifest None). mathlib_pin keeps register's 10-character form; revs that are prefixes of one
    another are equal."""
    changes, kept = {}, {}
    commit = str(receipt.get("commit") or "")
    tc = str(receipt.get("lean_toolchain") or "").strip()
    if tc and pkg.get("lean_toolchain") != tc:
        blob = git_show_bytes(repo, "./lean-toolchain", commit) if commit else None
        committed = blob.decode("utf-8", "replace").strip() if blob is not None else None
        if committed == tc:
            changes["lean_toolchain"] = {"from": pkg.get("lean_toolchain"), "to": tc}
        else:
            kept["lean_toolchain"] = {"receipt": tc, "manifest": committed}
    rev = str(receipt.get("mathlib_rev") or "").strip()
    old = str(pkg.get("mathlib_pin") or "")
    committed = committed_mathlib(repo, commit) if commit and rev else None
    if committed == NO_MATHLIB:
        # no Mathlib at the receipt's commit: the pin is null (what register records), whatever the receipt
        # says; a legacy value other than `none` is not checked, and never becomes a pin
        if pkg.get("mathlib_pin"):
            changes["mathlib_pin"] = {"from": pkg.get("mathlib_pin"), "to": None}
    elif rev and not _same_rev(rev, old):
        if committed is None:
            kept["mathlib_pin"] = {"receipt": rev, "manifest": None}
        elif _same_rev(rev, committed):
            changes["mathlib_pin"] = {"from": pkg.get("mathlib_pin"), "to": rev[:10]}
        # a disagreeing rev never reaches here: validate_receipt refuses it
    return changes, kept


def pins_kept_lines(name: str, kept: dict, commit: str) -> list[str]:
    files = {"lean_toolchain": ("lean_toolchain", "lean-toolchain"), "mathlib_pin": ("mathlib_rev", "lake-manifest.json")}
    out = []
    for field, v in kept.items():
        key, f = files[field]
        found = v["manifest"] if v["manifest"] is not None else "unreadable"
        out.append(f"{name}: pins kept: receipt {key} {v['receipt']} does not match {f} at {commit[:12]} ({found})")
    return out


def pin_changes_text(changes: dict) -> str:
    return ", ".join(f"{f} {v['from'] or '-'} -> {v['to'] or '-'}" for f, v in changes.items())


def do_gate(reg_path, reg, events_p, name, receipt_arg,
            comparator_arg=None, refuse_private: bool = False,
            refuse_dirty: bool = False, formalization_path: str | None = None,
            regate: bool = False, reason: str | None = None) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_gate, reg_path, reg, events_p, name, receipt_arg,
                                  comparator_arg, refuse_private, refuse_dirty=refuse_dirty,
                                  formalization_path=formalization_path, regate=regate, reason=reason)


def toolchain_advisories_of(receipt) -> list[dict]:
    """The toolchain advisories (#237) the receipt's lean_toolchain is affected by: a warning, never a
    refusal (cmd_gate prints one WARN line per match). An unreadable advisories file is one WARN line."""
    from . import toolchain_advisories as ta
    found, problem = ta.lookup(receipt.get("lean_toolchain") if isinstance(receipt, dict) else None)
    if problem:
        print(problem, file=sys.stderr)
    return found


def do_gate_check(reg, name, receipt_arg, comparator_arg=None, refuse_private: bool = False,
                  refuse_dirty: bool = False, regate: bool = False,
                  reason: str | None = None) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_gate_check, reg, name, receipt_arg, comparator_arg, refuse_private,
                                  refuse_dirty=refuse_dirty, regate=regate, reason=reason)


def _do_gate_check(reg, name, receipt_arg, comparator_arg=None, refuse_private: bool = False,
                   private_sink=None, refuse_dirty: bool = False, regate: bool = False,
                   reason: str | None = None) -> tuple[list[str], dict]:
    """`gate NAME --receipt R --check` (#26.2): every validation `gate` makes, against HEAD; writes
    nothing, records no event, works at any status. The mirror of `comparator --check`."""
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    dirty = dirty_guard(repo, name, refuse_dirty)
    resolved: list[dict] = []
    rp, receipt = load_json_object(locate_evidence_arg(receipt_arg, "receipt", name, repo, resolved), "receipt")
    cp = comp = None
    if comparator_arg is not None:
        cp, comp = load_json_object(locate_evidence_arg(comparator_arg, "comparator receipt", name, repo,
                                                        resolved), "comparator receipt")
    private = receipt_private_strings([("receipt", rp.read_bytes())]
                                      + ([("comparator", cp.read_bytes())] if cp else []),
                                      refuse_private, private_sink)
    validate_receipt(pkg, repo, receipt)
    commit = receipt["commit"]
    report_line, report_view = report_log_check(repo, receipt, rp)
    log_copy = plan_report_log_copy(repo, receipt, rp)
    report_view["copy"] = [d for _s, d in log_copy]
    checks = [f"· {r['what']} {r['arg']} resolved against the package checkout: {r['path']}" for r in resolved]
    checks += receipt_check_lines(pkg, repo, receipt) + [report_line]
    if log_copy:
        checks.append(f"· gate would also copy {', '.join(report_view['copy'])} beside the canonical receipt "
                      f"(the log beside --receipt; .lean-receipts/ holds no copy)")
    if comp is not None:
        validate_comparator(pkg, repo, comp, commit)
        checks += comparator_check_lines(pkg, repo, comp, commit, with_binding=False)
    cs = claims_schema_at(name, pkg, repo, commit)
    checks.append(claims_schema_line(cs))
    docs_view, _docs_line = docs_admission(repo, commit, pkg, receipt.get("checker"))
    status = pkg.get("status")
    lineage_ok = True
    regate_view = None
    if regate:   # #67: the checks `gate --regate` adds, without writing
        problem = regate_status_problem(name, pkg)
        if problem is None:
            prev, non_desc, problem = regate_lineage(name, pkg, repo, commit, reason)
            regate_view = {"previous_commit": prev, "non_descendant": non_desc}
            if problem is None:
                checks.append(f"✓ regate: {commit[:12]} " + (
                    f"is not a descendant of the gated commit {str(prev)[:12]} (accepted with --reason)"
                    if non_desc and prev else "accepted with --reason (no readable gated commit)"
                    if non_desc else f"descends from the gated commit {str(prev)[:12]}"))
        if problem is not None:
            lineage_ok = False
            checks.append(f"✗ regate: {problem.split(': ', 1)[-1]}")
    elif status not in ("gate-pending", "public"):
        checks.append(f"· status {status}: `gate` itself needs gate-pending (or public)"
                      + (f"; re-gate with `lean-pkg gate {name} --receipt R --regate`"
                         if status == "verified-private" else "")
                      + (f"; first: {to_gate_pending_cmd(name, commit)}" if status == "active" else ""))
    pins, pins_kept = receipt_pin_changes(pkg, repo, receipt)
    if pins:
        checks.append(f"· pins: gate would record {pin_changes_text(pins)} (from the receipt)")
    checks += [f"· {line.split(': ', 1)[1]}" for line in pins_kept_lines(name, pins_kept, commit)]
    ok = cs["ok"] is not False and lineage_ok
    msg = (f"{name}: receipt valid at commit {commit[:12]}"
           + (f" with comparator ({len(comp['configs'])} config(s))" if comp else "")
           + ("" if cs["ok"] is not False else f"; {cs['file']} FAILS the claims "
                  + ("schema" if cs["errors"] else "declaration check") + " (`gate` would refuse)")
           + ("" if lineage_ok else "; `gate --regate` would refuse (see the regate line)")
           + " — not recorded (--check)")
    payload = {"ok": ok, "name": name, "event": "gate-checked", "recorded": False, "commit": commit,
               "head": git_head(repo), "status": status, "checks": checks,
               "comparator": ({"commit": comp["commit"], "configs": len(comp["configs"])} if comp else None),
               "private_strings": private, "dirty": dirty, "claims_schema": cs, "pins": pins,
               "pins_kept": pins_kept, "report_log": report_view, "resolved_paths": resolved, **docs_view,
               "toolchain_advisories": toolchain_advisories_of(receipt)}
    if regate:
        payload["regate"] = regate_view
    return [msg] + [f"  {c}" for c in checks], payload


def _do_gate(reg_path, reg, events_p, name, receipt_arg, comparator_arg=None,
             refuse_private: bool = False, private_sink=None,
             refuse_dirty: bool = False, formalization_path: str | None = None,
             regate: bool = False, reason: str | None = None) -> tuple[list[str], dict]:
    """formalization_path (#26.4): the formalization.yaml `--refresh-formalization` is about to own and
    point the registry at; when no pointer is recorded yet, the evidence rule's derived-only exception
    is judged for that path (the refresh records it as the pointer right after the gate)."""
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    vpkg = pkg if (formalization_rel(pkg) or not formalization_path) \
        else {**pkg, "formalization_yaml": formalization_path}
    dirty = dirty_guard(repo, name, refuse_dirty)
    resolved: list[dict] = []
    rp, receipt = load_json_object(locate_evidence_arg(receipt_arg, "receipt", name, repo, resolved), "receipt")
    cp = comp = None
    if comparator_arg is not None:
        cp, comp = load_json_object(locate_evidence_arg(comparator_arg, "comparator receipt", name, repo,
                                                        resolved), "comparator receipt")
    private = receipt_private_strings([("receipt", rp.read_bytes())]
                                      + ([("comparator", cp.read_bytes())] if cp else []),
                                      refuse_private, private_sink)
    gate = pkg.get("gate") or {}
    # Idempotent retry: an identical receipt (pair) already recorded is a
    # no-op success, so agent retries are safe.
    recorded = recorded_json(repo, gate.get("receipt"))
    if recorded is not None and recorded == receipt:
        rec_comp = recorded_json(repo, gate.get("comparator"))
        if comp is None or (rec_comp is not None and rec_comp == comp):
            payload = {"ok": True, "name": name, "event": "already-gated",
                       "status": pkg.get("status"), "gate": pkg.get("gate"),
                       "receipt": gate.get("receipt"), "private_strings": private,
                       "dirty": dirty, "toolchain_advisories": toolchain_advisories_of(receipt)}
            return ([f"{name}: already gated (receipt {gate.get('receipt')}); "
                     f"nothing to do"], payload)
        raise Fail(f"{name}: already gated with this receipt; to add or replace "
                   f"comparator evidence use `lean-pkg comparator {name} --receipt ...`")
    # gate-pending is the designed entry. A package that was public before it was
    # ever gated (for example a collaborator-owned repository registered as public)
    # may also receive evidence: the status stays public, the receipt and the
    # gate-passed event are recorded. verified-private re-certifies through the
    # manual move back to gate-pending, or in one step with --regate (#67).
    if regate:
        problem = regate_status_problem(name, pkg)
        if problem:
            raise Fail(problem)
    elif pkg.get("status") not in ("gate-pending", "public"):
        raise Fail(f"{name}: gate requires status gate-pending (or public, for "
                   f"evidence on an already-public package), got {pkg.get('status')!r}"
                   + (f"; to re-gate a new commit in one step: `lean-pkg gate {name} --receipt R --regate` "
                      f"(the receipt's commit must descend from the gated one, else add --reason TEXT)"
                      if pkg.get("status") == "verified-private" else "")
                   + (f"; first: {to_gate_pending_cmd(name, str(receipt.get('commit') or ''))}, then gate again"
                      if pkg.get("status") == "active" and isinstance(receipt, dict) else ""))
    was_public = pkg.get("status") == "public"
    validate_receipt(vpkg, repo, receipt)
    commit = receipt["commit"]
    report_line, report_view = report_log_check(repo, receipt, rp)
    if comp is not None:
        validate_comparator(vpkg, repo, comp, commit)
    docs_view, docs_line = docs_admission(repo, commit, vpkg, receipt.get("checker"))
    prev_commit, non_descendant = None, False
    if regate:
        prev_commit, non_descendant, problem = regate_lineage(name, pkg, repo, commit, reason)
        if problem:
            raise Fail(problem + "; nothing copied or recorded")
    cs = claims_schema_at(name, pkg, repo, commit)
    if cs["ok"] is False:
        problems = claims_problems(cs)
        if cs["errors"]:
            what = "fails the claims/1 schema"
            fix = (f"fix the file, check it with `lean-pkg claims check --file {cs['file']}`"
                   + (" (schema only; the declarations are checked by `claims sync` at HEAD)"
                      if cs["declarations"]["problems"] else ""))
        else:
            what = "names declarations missing from their files at that commit"
            fix = (f"fix the declarations or the claims' decl/file entries (`lean-pkg claims sync {name}` "
                   f"runs the same check at HEAD)")
        raise Fail(f"{name}: {cs['file']} at {commit[:12]} {what}, so `claims sync` "
                   f"would refuse what this gate binds: " + "; ".join(problems[:3])
                   + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else "")
                   + f". Nothing copied or recorded. Fixing it is a new commit, which needs a new receipt "
                   f"and a re-gate: {fix}, "
                   f"commit, re-run the receipt at that commit, then `lean-pkg gate {name} --receipt R`")

    job = receipt_job_slug(receipt["job_id"])
    dest_rel = f".lean-receipts/{commit[:12]}-{job}.json"
    dest = repo / dest_rel
    out: list[str] = [f"{r['what']} {r['arg']} resolved against the package checkout: {r['path']}"
                      for r in resolved] + [claims_schema_line(cs), report_line]
    # Every precondition of both copies runs before either is written, so a refusal writes nothing.
    why = evidence_dest_problem(repo, dest_rel)
    if why:
        raise Fail(why)
    log_copy = plan_report_log_copy(repo, receipt, rp)
    report_view["copy"] = [d for _s, d in log_copy]
    present = dest.exists()
    if present and recorded_json(repo, dest_rel) != receipt:
        raise Fail(f"{dest} already holds a different receipt for commit {commit[:12]} and job "
                   f"{receipt['job_id']}; pass that file (--receipt {dest}), or give the new run its own "
                   f"job_id")
    comp_plan = plan_comparator_copy(repo, commit, cp) if comp is not None else None
    if present:
        out.append(f"receipt already present at {dest_rel} (not re-copied)")
    else:
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(rp, dest)
        out.append(f"receipt copied to {dest} — NOT committed there; "
                   f"commit it in the verified repo yourself")
    for src, log_rel in log_copy:   # the sealed log follows the receipt, so the canonical copy verifies
        shutil.copy2(src, repo / log_rel)
        out.append(f"sealed log copied to {repo / log_rel} beside the receipt — NOT committed there; "
                   f"commit it with the receipt")
    pkg.setdefault("gate", {})["receipt"] = dest_rel
    fields = dict(commit=commit, job_id=str(receipt["job_id"]),
                  checker=str(receipt["checker"]), receipt=dest_rel, **docs_view)
    if docs_line:
        out.append(docs_line)
    comp_rel = None
    if comp is not None:
        comp_rel, note, _moved = apply_comparator_copy(repo, comp_plan, cp)
        out.append(note)
        pkg["gate"]["comparator"] = comp_rel
        fields["comparator"] = comp_rel
    else:
        prev_comp = pkg["gate"].get("comparator")
        prev = recorded_json(repo, prev_comp) if prev_comp else None
        if prev_comp and (prev is None or str(prev.get("commit")) != commit):
            pkg["gate"]["comparator"] = None
            fields["comparator_cleared"] = prev_comp
            out.append(f"comparator evidence {prev_comp} cleared: it was bound to another commit; "
                       f"attach a fresh one with `lean-pkg comparator {name} --receipt ...`")
        else:
            pkg["gate"].setdefault("comparator", None)
    if was_public:
        fields["status_unchanged"] = True
        new_status = "public"
    else:
        new_status = "verified-private"
    pkg["status"] = new_status
    pins, pins_kept = receipt_pin_changes(pkg, repo, receipt)
    for field, change in pins.items():
        pkg[field] = change["to"]
    if pins:
        fields["pins"] = pins
    event = "gate-passed"
    if regate:   # #67: one transition, one event naming both commits
        event = "regated"
        fields = {"previous_commit": prev_commit, **fields, "reason": reason or ""}
        if non_descendant:
            fields["non_descendant"] = True
    record(reg, events_p, pkg, event, **fields)
    save_registry(reg_path, reg)
    if regate:
        out.append(f"{name}: {new_status}, re-gated {str(prev_commit or 'unknown')[:12]} -> {commit[:12]}"
                   + (" (not a descendant; --reason given)" if non_descendant else "")
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    elif was_public:
        out.append(f"{name}: public, evidence recorded, status unchanged (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    else:
        out.append(f"{name}: gate-pending -> verified-private (commit {commit[:12]})"
                   + (f" with comparator evidence {comp_rel}" if comp_rel else ""))
    if pins:
        out.append(f"{name}: pins updated from the receipt: {pin_changes_text(pins)}")
    out += pins_kept_lines(name, pins_kept, commit)
    out.append(RL_REMINDER.format(name=name))
    ignored = attached_ignored_evidence(pkg, repo)
    payload = {"ok": True, "name": name, "event": event,
               "status": new_status, "gate": pkg["gate"],
               "receipt": dest_rel, "commit": commit, "rl_reminder": True,
               "rl_hint": RL_REMINDER.format(name=name),
               "status_unchanged": was_public or regate, "private_strings": private, "dirty": dirty,
               "ignored_evidence": ignored, "claims_schema": cs, "pins": pins,
               "pins_kept": pins_kept, "report_log": report_view, "resolved_paths": resolved, **docs_view,
               "toolchain_advisories": toolchain_advisories_of(receipt)}
    if regate:
        payload.update(previous_commit=prev_commit, non_descendant=non_descendant, reason=reason or "")
    if comp_rel:
        payload["comparator"] = comp_rel
    return out, payload


def attached_ignored_evidence(pkg: dict, repo: pathlib.Path) -> list[dict]:
    """After `gate`/`comparator` attach evidence: [{path, rule}] for the evidence files git ignores,
    each also a stderr WARN, so the gap shows now and not at promote time (#40)."""
    from . import publish as publish_mod
    ignored = publish_mod.ignored_evidence(repo, publish_mod.evidence_paths(pkg, repo))
    publish_mod.warn_ignored_evidence(ignored)
    return ignored


def do_comparator(reg_path, reg, events_p, name, receipt_arg,
                  check_only: bool = False, refuse_private: bool = False,
                  refuse_dirty: bool = False, replace: bool = False) -> tuple[list[str], dict]:
    return _with_private_warnings(_do_comparator, reg_path, reg, events_p, name, receipt_arg,
                                  check_only, refuse_private, refuse_dirty=refuse_dirty, replace=replace)


def _do_comparator(reg_path, reg, events_p, name, receipt_arg, check_only: bool = False,
                   refuse_private: bool = False, private_sink=None,
                   refuse_dirty: bool = False, replace: bool = False) -> tuple[list[str], dict]:
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir():
        raise Fail(f"{name}: local_path does not exist: {repo}")
    dirty = dirty_guard(repo, name, refuse_dirty)
    cp, comp = load_json_object(locate_evidence_arg(receipt_arg, "comparator receipt", name, repo),
                                "comparator receipt")
    private = receipt_private_strings([("comparator", cp.read_bytes())], refuse_private, private_sink)
    gate = pkg.get("gate") or {}
    if check_only:
        lower = recorded_json(repo, gate.get("receipt"))
        lower_commit = str(lower.get("commit") or "") if lower else None
        validate_comparator(pkg, repo, comp, lower_commit or None)
        why = recorded_report_log_problem(repo, lower, gate["receipt"]) if isinstance(lower, dict) else None
        if why:              # the tier this receipt pairs with must still be evidence
            raise Fail(f"{name}: the recorded gate receipt {gate['receipt']}: {why}")
        docs_view, _docs_line = docs_admission(repo, comp["commit"], pkg)
        n = len(comp["configs"])
        msg = (f"{name}: comparator receipt valid at commit {comp['commit'][:12]} "
               f"({n} config(s)) — not recorded (--check)"
               + ("" if lower_commit else "; no lower-tier receipt recorded yet"))
        checks = comparator_check_lines(pkg, repo, comp, lower_commit or None)
        payload = {"ok": True, "name": name, "event": "comparator-checked",
                   "recorded": False, "commit": comp["commit"], "configs": n,
                   "lower_receipt_commit": lower_commit, "head": git_head(repo), "checks": checks,
                   "private_strings": private, "dirty": dirty, **docs_view}
        return [msg] + [f"  {c}" for c in checks], payload
    rec_comp = recorded_json(repo, gate.get("comparator"))
    if rec_comp is not None and rec_comp == comp:
        payload = {"ok": True, "name": name, "event": "already-attached",
                   "status": pkg.get("status"), "gate": gate,
                   "comparator": gate.get("comparator"), "private_strings": private,
                   "dirty": dirty}
        return ([f"{name}: comparator receipt already attached "
                 f"({gate.get('comparator')}); nothing to do"], payload)
    if pkg.get("status") not in ("verified-private", "public"):
        raise Fail(f"{name}: comparator requires status verified-private or public "
                   f"(gate first), got {pkg.get('status')!r}")
    lower = recorded_json(repo, gate.get("receipt"))
    if lower is None:
        raise Fail(f"{name}: no readable lower-tier receipt at {gate.get('receipt')!r}; "
                   f"run `lean-pkg gate` first")
    validate_comparator(pkg, repo, comp, str(lower.get("commit") or ""))
    why = recorded_report_log_problem(repo, lower, gate["receipt"])
    if why:                  # the tier this receipt pairs with must still be evidence
        raise Fail(f"{name}: the recorded gate receipt {gate['receipt']}: {why}; nothing recorded")
    commit = comp["commit"]
    docs_view, docs_line = docs_admission(repo, commit, pkg)
    comp_rel, note, moved = copy_comparator(repo, commit, cp, replace=replace)
    pkg.setdefault("gate", {})["comparator"] = comp_rel
    event = "comparator-replaced" if moved else "comparator-attached"
    extra = {"replaced": moved} if moved else {}
    record(reg, events_p, pkg, event, commit=commit,
           comparator=comp_rel, host=str(comp["host"]), job_id=str(comp["job_id"]), **extra, **docs_view)
    save_registry(reg_path, reg)
    out = [note, *([docs_line] if docs_line else []),
           f"{name}: {event} ({comp_rel}, commit {commit[:12]}, "
           f"{len(comp['configs'])} config(s))" + (f"; older receipt kept at {moved}" if moved else "")]
    payload = {"ok": True, "name": name, "event": event, **extra,
               "status": pkg.get("status"), "gate": pkg["gate"],
               "comparator": comp_rel, "commit": commit, "private_strings": private,
               "dirty": dirty, "ignored_evidence": attached_ignored_evidence(pkg, repo), **docs_view}
    return out, payload


# --------------------------------------------------------------------------
# --repo DIR: a read-only command run against another checkout of the package

def _remote_abs_path(url: str) -> str | None:
    """The absolute repository path a non-GitHub remote names, whatever host it is reached through:
    `/p`, `file:///p`, scp-style `host:/p` and `ssh://[user@]host[:port]/p` all give `/p` (no
    `.git`, no trailing slash). None for a relative or home-relative path (`host:p`, `host:~/p`)."""
    u = str(url).strip()
    m = (re.fullmatch(r"file://(/.*)", u) or re.fullmatch(r"ssh://[^/]+(/.*)", u)
         or re.fullmatch(r"[^/:\s]+:(/.*)", u) or re.fullmatch(r"(/.*)", u))
    if not m:
        return None
    return re.sub(r"(\.git)?/*$", "", m.group(1)) or None


def _same_remote(a: str, b: str) -> bool:
    """Do two remote URLs name the same repository? GitHub URLs by owner/repo (any scheme, any case);
    otherwise exact match up to `.git`/trailing slash, or (field report) the same absolute path when
    one side is a plain local path: a registry `repo` that is the bare repo's path on the registry host
    matches `Host:<that path>` from a checkout elsewhere. Two remotes that both name a host keep the
    exact comparison."""
    na, nb = gh_nwo(a), gh_nwo(b)
    if na and nb:
        return na.lower() == nb.lower()
    norm = lambda u: re.sub(r"(\.git)?/*$", "", str(u).strip())
    if bool(norm(a)) and norm(a) == norm(b):
        return True
    local = lambda u: str(u).strip().startswith(("/", "file://"))
    if local(a) or local(b):
        pa, pb = _remote_abs_path(a), _remote_abs_path(b)
        return pa is not None and pa == pb
    return False


def repo_override(reg: dict, name: str, repo_arg: str) -> tuple[dict, dict]:
    """`--repo DIR` on a read-only command (field report: one local_path per package, but checkouts
    on different hosts hold different branches): (a copy of the registry whose NAME record points at
    DIR for this one call, {path, registered_path, remote_match}). Never saved. DIR must be a git
    work tree; when it has remotes and the record names a repo, one remote must be that repo
    (remote_match True; `Host:/p` matches a local-path repo `/p`, see _same_remote), unless DIR is the
    registered local_path itself. Otherwise remote_match is None and the caller vouches for DIR."""
    pkg = get_pkg(reg, name)
    d = pathlib.Path(os.path.expanduser(repo_arg)).resolve()
    if not d.is_dir():
        raise Fail(f"--repo {repo_arg}: not a directory")
    p = git(d, "rev-parse", "--is-inside-work-tree")
    if p.returncode != 0 or p.stdout.strip() != "true":
        raise Fail(f"--repo {repo_arg}: not inside a git work tree")
    want = str(pkg.get("repo") or "").strip()
    p = git(d, "config", "--get-regexp", r"^remote\..*\.url$")
    urls = [line.split(None, 1)[1] for line in p.stdout.splitlines() if len(line.split(None, 1)) == 2]
    registered = pkg.get("local_path")
    try:
        registered = str(repo_path(pkg))
    except (KeyError, TypeError, ValueError):
        pass
    is_registered = False
    try:
        is_registered = registered is not None and pathlib.Path(registered).resolve() == d
    except OSError:
        pass
    match = None
    if want and urls and any(_same_remote(u, want) for u in urls):
        match = True
    elif want and urls and not is_registered:      # the registered checkout itself needs no remote proof
        raise Fail(f"--repo {repo_arg}: no git remote of it is the package's repo {want} "
                   f"(remotes: {', '.join(urls)}); pass a checkout of {name}")
    copied = dict(reg)
    copied["packages"] = dict(reg.get("packages") or {})
    copied["packages"][name] = {**copy.deepcopy(pkg), "local_path": str(d)}
    return copied, {"path": str(d), "registered_path": registered, "remote_match": match}


def repo_override_note(view: dict) -> str | None:
    """The stderr note for `--repo DIR`; None when DIR is the registered local_path itself (field
    report: the note then named the same path twice and said nothing)."""
    try:
        if view.get("registered_path") and \
                pathlib.Path(view["registered_path"]).resolve() == pathlib.Path(view["path"]).resolve():
            return None
    except OSError:
        pass
    return (f"lean-pkg: note: using --repo {view['path']} instead of the registered local_path "
            f"{view['registered_path']} (this call only; the registry is unchanged)"
            + ("" if view["remote_match"] else "; no remote to compare with the record's repo: "
               "the caller vouches that it is this package's checkout"))


# --------------------------------------------------------------------------
# the gate command

def refresh_preflight(reg: dict, name: str, agent: str | None) -> None:
    """--refresh-formalization, before the gate writes anything: the package's checkout exists, no other
    agent holds a live lease (the file write needs it), and the current render passes the schema (a kept
    key that fails it would otherwise refuse only after the gate has been recorded)."""
    from . import formalization as fz
    pkg = get_pkg(reg, name)
    repo = repo_path(pkg)
    if not repo.is_dir() or git_head(repo) is None:
        raise Fail(f"{name}: --refresh-formalization needs the git checkout at {repo}")
    refuse_invalid_lease(name, pkg.get("lease"))
    lease = pkg.get("lease")
    if lease_live(lease, dt.datetime.now(dt.timezone.utc)) and agent != lease.get("agent"):
        raise Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                   f"nothing written")
    _text, doc, _w, _existing = fz.render_for(pkg, repo)
    mode, errors = fz.validate_doc(doc)
    if errors:
        raise Fail(f"{name}: formalization.yaml would fail the {fz.SCHEMA_VERSION} schema ({mode}); fix "
                   f"its kept keys first; nothing written:\n  " + "\n  ".join(errors))


def do_gate_refresh(reg_path, reg, events_p, args) -> tuple[list[str], dict, list[str]]:
    """`gate NAME --receipt R --refresh-formalization` (#22.4): gate, then regenerate formalization.yaml
    (derived keys, including status.evidence, from the new receipt; kept keys survive) in one step.
    Refuses, writing nothing, when the gate is an identical re-gate and the file is already current."""
    from . import formalization as fz
    refresh_preflight(reg, args.name, args.agent)
    out, payload = do_gate(reg_path, reg, events_p, args.name, args.receipt, args.comparator,
                           refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                           formalization_path=fz.target_rel(get_pkg(reg, args.name)),
                           regate=getattr(args, "regate", False), reason=getattr(args, "reason", None))
    gated = payload.get("event") in ("gate-passed", "regated")
    pkg = get_pkg(reg, args.name)
    if not gated:
        text, _doc, _w, existing = fz.render_for(pkg, repo_path(pkg))
        if existing == text and pkg.get("formalization_yaml") == fz.target_rel(pkg):
            raise Fail(f"{args.name}: nothing to refresh: already gated with this receipt and "
                       f"{fz.target_rel(pkg)} is current; nothing written")
    f_lines, f_payload, warnings = fz.do_formalization(
        reg_path, reg, events_p, args.name, write=True, out=None, force=True, authors=None,
        agent=args.agent, reason="lean-pkg gate --refresh-formalization")
    payload["formalization"] = {k: f_payload[k] for k in ("path", "written", "unchanged", "registry_updated",
                                                         "validation", "placeholders")}
    return out + f_lines, payload, warnings


def cmd_gate(args) -> int:
    reg_path, events_p = registry_path(args), events_path(args)
    regate, reason = getattr(args, "regate", False), getattr(args, "reason", None)
    if reason is not None:
        if not regate:
            raise UsageError("gate --reason goes with --regate (it accepts a non-descendant re-gate)")
        require_nonempty("--reason", reason)
        reason = reason.strip()
    repo_arg = getattr(args, "repo", None)
    if repo_arg is not None and not getattr(args, "check", False):
        # field report: a writing gate binds the registered checkout; move it with `update --path`
        raise UsageError("gate --repo goes with --check: a writing gate binds the registered local_path "
                         f"(validate here with `gate {args.name} --check --repo DIR`, or move the record with "
                         f"`lean-pkg update {args.name} --path DIR --reason TEXT`)")
    if getattr(args, "check", False):
        if getattr(args, "refresh_formalization", False) or getattr(args, "agent", None) is not None:
            raise UsageError("gate --check writes nothing; --refresh-formalization and --agent do not apply: "
                             "--check takes no lease and checks none (anyone may validate a receipt, at any "
                             "status), so drop --agent here and pass it to the writing `gate` "
                             f"(`lean-pkg gate {args.name} --receipt R --agent A`)")
        reg, override = load_registry(reg_path), None
        if repo_arg is not None:
            reg, override = repo_override(reg, args.name, repo_arg)
            if note := repo_override_note(override):
                print(note, file=sys.stderr)
        out, payload = do_gate_check(reg, args.name, args.receipt, args.comparator,
                                     refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                                     regate=regate, reason=reason)
        payload["repo_override"] = override
        from . import palomar_check   # advisory (feature request #6): never changes the verdict
        payload["palomar_check"] = view = palomar_check.hook(reg, args.name, payload.get("commit"))
        if view and (warning := palomar_check.summary_warning(view)):
            print(f"WARN: {warning}", file=sys.stderr)
    elif getattr(args, "refresh_formalization", False):
        _ACTOR["agent"] = getattr(args, "agent", None) or None    # its own lease check runs in do_gate_refresh
        out, payload, warnings = do_gate_refresh(reg_path, load_registry(reg_path), events_p, args)
        for w in warnings:
            print(f"lean-pkg: warning: {w}", file=sys.stderr)
    else:
        reg = load_registry(reg_path)
        warn_lease(lease_actor(reg, args.name, getattr(args, "agent", None), "gate"))
        out, payload = do_gate(reg_path, reg, events_p,
                               args.name, args.receipt, args.comparator,
                               refuse_private=args.refuse_private, refuse_dirty=args.refuse_dirty,
                               regate=regate, reason=reason)
    from .toolchain_advisories import warn_line   # #237: a warning, never a refusal
    for view in payload.get("toolchain_advisories") or []:
        print(warn_line(view), file=sys.stderr)
    if args.json_out:
        print_json(payload)
    else:
        print("\n".join(out))
    return 0 if payload.get("ok", True) else 1
