# lean-pkg palomar -- build a Palomar submission bundle (no network)
"""`lean-pkg palomar prepare NAME [--out DIR] [--dry-run] [--json]` (#22.3): collect what a manual
Palomar submission needs into one directory, check it, and record that it was built. Bundle only:
lean-pkg makes no HTTP request and submits nothing; the README lists the manual steps.

The bundle (default `<checkout>/.lean-receipts/palomar-<HEAD12>/`):

- `formalization.yaml`: rendered by the formalization renderer, schema-valid (placeholders warn);
- `configs/<path>`: every comparator configuration tracked at HEAD, byte-identical;
- `receipts/<file>`: the recorded gate and comparator receipts, validated against HEAD (bound to it
  or to an ancestor whose difference is evidence only);
- `README.txt`: the manual submission steps and the Palomar URLs;
- `MANIFEST.sha256`: `sha256  path` for every other file (`sha256sum -c` checks it).

Refusals (exit 1, nothing written): a missing receipt, a receipt not bound to HEAD, no consent
recorded, a render that fails the schema, blocking public-safety hits in the bundle, an existing
target. The event `palomar-prepared` {commit, bundle, bundle_sha256} (the sha256 of
MANIFEST.sha256) goes to history and the event log. `--dry-run` runs every check and prints the
plan; it writes nothing.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil
import tempfile

from . import cli as core

PALOMAR_URL = "https://palomar-registry.org/"
PALOMAR_SUBMISSION_URL = "https://github.com/PalomarRegistry/PalomarSubmission"
EVENT = "palomar-prepared"


def _write(root: pathlib.Path, rel: str, data: bytes) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def readme_text(pkg: dict, head: str, configs: list[str], files: list[str], placeholders: list[str]) -> str:
    from . import __version__
    name = pkg["name"]
    cfg = configs[0] if configs else "comparator.json"
    lines = [
        f"Palomar submission bundle for {name} at commit {head}",
        f"Built by lean-pkg {__version__} on {core.now_iso()}. Nothing was submitted: lean-pkg makes no",
        "network request. Submit by hand:",
        "",
        f"1. Make sure commit {head} is pushed to the public repository {pkg.get('repo')}.",
        f"2. Open {PALOMAR_URL} and follow its submission instructions; the submission",
        f"   repository layout is described at {PALOMAR_SUBMISSION_URL}.",
        "3. Give the repository URL, the commit above and the comparator configuration(s):",
    ]
    lines += [f"     {c}" for c in configs]
    lines += [
        "4. Check this bundle first: sha256sum -c MANIFEST.sha256 (run inside this directory).",
        "5. Once Palomar returns a submission id, record it:",
        f"     lean-pkg record {name} palomar-submitted --field commit={head} \\",
        f"       --field submission_id=ID --field comparator_config={cfg}",
        "",
        "Files:",
    ]
    lines += [f"  {f}" for f in files]
    if placeholders:
        lines += ["", "formalization.yaml still has placeholders (fill them in the repository, then rebuild):"]
        lines += [f"  {p}" for p in placeholders]
    return "\n".join(lines) + "\n"


def build(pkg: dict, repo: pathlib.Path, stage: pathlib.Path) -> tuple[list[str], dict]:
    """Fill `stage` with the bundle; (warnings, facts). Raises Fail on every refusal."""
    from . import formalization as fz
    from . import publish as publish_mod
    name = pkg["name"]
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    gate = pkg.get("gate") or {}
    if not gate.get("receipt"):
        raise core.Fail(f"{name}: no gate receipt recorded (lean-pkg gate {name} --receipt R)")
    if not gate.get("comparator"):
        raise core.Fail(f"{name}: no comparator receipt recorded (lean-pkg comparator {name} --receipt R)")
    receipt = core.recorded_json(repo, gate["receipt"])
    if receipt is None:
        raise core.Fail(f"{name}: gate receipt {gate['receipt']} is missing or not JSON in {repo}")
    comp = core.recorded_json(repo, gate["comparator"])
    if comp is None:
        raise core.Fail(f"{name}: comparator receipt {gate['comparator']} is missing or not JSON in {repo}")
    try:
        core.validate_receipt(pkg, repo, receipt)
        core.validate_comparator(pkg, repo, comp, str(receipt.get("commit") or ""))
    except core.Fail as e:
        raise core.Fail(f"{name}: receipts not bound to HEAD {head[:12]}: {e}") from None
    mark, label = publish_mod.consent_item(pkg)
    if mark != "✓":
        raise core.Fail(f"{name}: {label}")
    text, doc, warnings, _existing = fz.render_for(pkg, repo)
    mode, errors = fz.validate_doc(doc)
    if errors:
        raise core.Fail(f"{name}: the rendered formalization.yaml fails the {fz.SCHEMA_VERSION} schema "
                        f"({mode}):\n  " + "\n  ".join(errors))
    placeholders = fz.find_placeholders(doc)
    warnings = list(warnings) + [f"placeholder at {p} (formalization.yaml is not Palomar-ready)"
                                 for p in placeholders]
    _write(stage, "formalization.yaml", text.encode())
    configs = core.head_comparator_configs(repo, "HEAD")
    for c in configs:
        blob = core.git_show_bytes(repo, c, "HEAD")
        _write(stage, f"configs/{c}", blob or b"")
    for rel in (gate["receipt"], gate["comparator"]):
        _write(stage, f"receipts/{pathlib.PurePosixPath(rel).name}", (repo / rel).read_bytes())
    files = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    _write(stage, "README.txt", readme_text(pkg, head, configs, files + ["README.txt", "MANIFEST.sha256"],
                                            placeholders).encode())
    files = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    manifest = "".join(f"{hashlib.sha256((stage / f).read_bytes()).hexdigest()}  {f}\n" for f in files)
    _write(stage, "MANIFEST.sha256", manifest.encode())
    from . import public_safety as ps
    scan = ps.scan(pkg, stage, export=True, patterns_root=repo, source_repo=repo)
    nblock = scan["block_hits"]
    if nblock:
        hits = "\n".join(ps.hit_line(h) for h in ps.blocking(scan["hits"]))
        raise core.Fail(f"{name}: public-safety: {nblock} blocking hit(s) in the bundle; nothing written:\n"
                        + hits)
    if scan["warn_hits"]:
        warnings.append(f"public-safety: {scan['warn_hits']} warning(s) in the bundle "
                        f"({ps.warn_families(scan['hits'])}); review them")
    from . import ledger as ledger_mod
    facts = {"commit": head, "configs": configs, "files": files + ["MANIFEST.sha256"],
             "manuscript_findings": ledger_mod.package_findings(pkg, repo),
             "bundle_sha256": hashlib.sha256(manifest.encode()).hexdigest(), "placeholders": placeholders,
             "public_safety": {"block_hits": nblock, "warn_hits": scan["warn_hits"]}}
    return warnings, facts


def do_prepare(reg_path, reg, events_p, name: str, out: str | None, dry_run: bool) -> tuple[list[str], dict, list[str]]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    target = pathlib.Path(out).expanduser() if out else repo / ".lean-receipts" / f"palomar-{head[:12]}"
    target = pathlib.Path(os.path.abspath(target))
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise core.Fail(f"{name}: {target} already exists; a bundle is never overwritten (remove it, or "
                        f"pass --out another directory)")
    try:
        shown = target.relative_to(repo).as_posix()
    except ValueError:
        shown = str(target)
    with tempfile.TemporaryDirectory(prefix="lean-pkg-palomar-") as tmp:
        stage = pathlib.Path(tmp) / "bundle"
        stage.mkdir()
        warnings, facts = build(pkg, repo, stage)
        payload = {"ok": True, "name": name, "dry_run": dry_run, "bundle": shown, **facts,
                   "warnings": warnings, "event": None}
        lines = [f"{name}: Palomar bundle for commit {head[:12]} -> {shown}"
                 + (" (dry run: nothing written)" if dry_run else "")]
        lines += [f"  {f}" for f in facts["files"]]
        lines.append(f"  bundle sha256 (of MANIFEST.sha256): {facts['bundle_sha256']}")
        from . import ledger as ledger_mod
        fl = ledger_mod.findings_line(facts["manuscript_findings"])
        if fl:
            lines.append(f"  {fl}")
        if dry_run:
            lines.append("next: run again without --dry-run, then follow README.txt in the bundle")
            return lines, payload, warnings
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()                 # empty (checked above)
        shutil.copytree(stage, target)
    core.record(reg, events_p, pkg, EVENT, commit=head, bundle=shown, bundle_sha256=facts["bundle_sha256"])
    core.save_registry(reg_path, reg)
    payload["event"] = EVENT
    lines.append(f"{name}: {EVENT} — bundle written (not committed, never submitted); follow "
                 f"{shown}/README.txt to submit by hand")
    return lines, payload, warnings


def cmd_palomar(args) -> int:
    if getattr(args, "palomar_fn", None) is None:
        raise core.UsageError("palomar needs an action: prepare")
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    lines, payload, warnings = do_prepare(reg_path, core.load_registry(reg_path), events_p, args.name,
                                          args.out, args.dry_run)
    for w in warnings:
        print(f"lean-pkg: warning: {w}", file=core.sys.stderr)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return 0


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("palomar", help="Palomar: prepare (build a submission bundle; no network)")
    add_json(s)
    s.set_defaults(fn=cmd_palomar, palomar_fn=None)
    psub = s.add_subparsers(dest="palomar_cmd")
    p = psub.add_parser("prepare", help="build the submission bundle (formalization.yaml, comparator configs, "
                                        "receipts, README.txt, MANIFEST.sha256); event palomar-prepared")
    p.add_argument("name")
    p.add_argument("--out", default=None, metavar="DIR",
                   help="bundle directory (default <checkout>/.lean-receipts/palomar-<HEAD12>/); must not exist "
                        "or be empty")
    p.add_argument("--dry-run", action="store_true", help="run every check and print the plan; write nothing")
    add_json(p)
    p.set_defaults(fn=cmd_palomar, palomar_fn="prepare")
