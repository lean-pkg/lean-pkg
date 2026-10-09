---
title: Getting started
---

[&larr; lean-pkg documentation](index.md)

## Install

Python 3.11 or newer. The only Python dependency is PyYAML. Git is a core
requirement for package provenance: receipts bind to Git commits and several
checks inspect Git ancestry and working-tree state.

```bash
pipx install git+https://github.com/lean-pkg/lean-pkg.git
# or
uv tool install git+https://github.com/lean-pkg/lean-pkg.git
# or, from a checkout, for development
python3 -m pip install -e '.[test]'
```

While the repository is private, the `https` URL above needs GitHub
credentials. Either run `gh auth setup-git` once, or install over SSH:

```bash
pipx install git+ssh://git@github.com/lean-pkg/lean-pkg.git
```

The documentation site is offline while the repository is private; the same
pages are the Markdown files under `docs/` on GitHub. `lean-pkg doctor`
exits 1 until a registry exists (see below); that is expected on a fresh
install.

External tools, needed only by some commands:

| Tool | Needed for |
|---|---|
| `git` | everything that reads a package repository |
| `gh`, authenticated (`gh auth login` or `GH_TOKEN`) | `promote` and `audit`; GitHub is only the visibility mirror |
| `reuse` (optional) | the REUSE lint line of the promotion checklist |
| Linux, `elan` (`lean`, `lake`), `go`, `cargo`, `jq` | `comparator-receipt`. Linux only: the legacy mechanism sandboxes with landrun (Landlock) and the toolchain mechanism with `bwrap`; macOS is not supported. The first run builds the pinned comparator tools into `~/.cache/lean-ci-tools` (override with `TOOLS_DIR`): from about 2 minutes on a fast machine to about 25 minutes cold, seconds afterwards. Budget several GB of disk for the Lean toolchain and the Mathlib cache |

## Configuration

| Setting | Meaning |
|---|---|
| `--registry PATH`, then env `LEAN_PKG_REGISTRY`, then the user-level default `~/.lean-pkg/registry`, then `./lean-packages.yaml` | registry file, in that order of precedence; `lean-pkg doctor` names the one in use (`[source: flag\|env\|user-default\|cwd]`) |
| `~/.lean-pkg/registry` (base directory: env `LEAN_PKG_HOME`) | user-level default: a directory, or a symlink to one, holding `lean-packages.yaml`, or the registry file itself. Point it at an existing registry repository with `mkdir -p ~/.lean-pkg && ln -s /path/to/registry-repo ~/.lean-pkg/registry` |
| `--events PATH`, else `lean-package-events.jsonl` next to the registry | append-only event log |
| `LEAN_PKG_NOTIFY_CMD` | optional command run once after a successful `promote --yes`, message on stdin |
| `COMPARATOR_RECEIPT_ALLOWED_HOSTS` | comma-separated host names on which `comparator-receipt` may run |
| `COMPARATOR_RECEIPT_ALLOW_HOST=1` | explicit override of the host policy (recorded in the receipt) |
| `SLURM_JOB_ID` | inside a Slurm job the run is always allowed and the job id becomes the receipt `job_id` |
| `COMPARATOR_CHECK` | an alternative `comparator-check.sh` (default: the copy shipped in the package) |

Each package's `local_path` is resolved relative to `$HOME`, so machines that
keep checkouts at the same home-relative place can share one registry.
Absolute paths also work.

## Recommended layout: one registry, many projects

Install `lean-pkg` once and use one shared registry to coordinate many
independent Lean/Git repositories:

<img src="assets/global-registry.svg" alt="Architecture diagram: one private global registry coordinates many independent Lean project repositories; a chosen public snapshot can later flow through index review into the lean-pkg public index" style="max-width:100%;height:auto">

```text
~/.lean-pkg/registry/
  .git/
  lean-packages.yaml
  lean-package-events.jsonl

~/src/
  paper-a/        # independent Git repo
  paper-b/        # independent Git repo
  paper-c/        # independent Git repo
```

The research repositories hold the mathematics, Lean source, comparator files,
and their own committed `.lean-receipts/`. The registry repository holds the
cross-project coordination state and append-only event history.

Keep the registry repository private by default. It may contain pre-publication
project names, local paths, agent/workspace identifiers, notes, and lifecycle
status.

### Bootstrap the shared registry today

The current CLI can create the registry files, while Git initialization is done
explicitly:

```bash
mkdir -p ~/.lean-pkg/registry
git -C ~/.lean-pkg/registry init

lean-pkg --registry ~/.lean-pkg/registry/lean-packages.yaml init

git -C ~/.lean-pkg/registry add lean-packages.yaml lean-package-events.jsonl
git -C ~/.lean-pkg/registry commit -m "registry: initialize lean-pkg"

# ~/.lean-pkg/registry is the default registry location: no LEAN_PKG_REGISTRY needed
```

After that initial commit, modify the registry through `lean-pkg`, not by
hand, and commit meaningful registry changes as part of your normal coordination
workflow.

A collaborator setting up for one package can run
`scripts/bootstrap-collaborator.sh OWNER/PACKAGE` instead, which also checks
access, clones and registers the package
(step 0 of [the comparator guide](existing-package-comparator.md)).

A proposed future `lean-pkg init --global` will automate this Git-backed
bootstrap and allow a custom registry-repository location. It is described in
the [roadmap](../ROADMAP.md) and is not implemented yet.

## Worked example, end to end

This takes a small Lake project from registration to a promotion dry run.
Replace `you/my-proof` with your repository.

### 1. The project

```
~/src/my-proof/
  lean-toolchain        leanprover/lean4:v4.32.0
  lakefile.toml
  lake-manifest.json    tracked; pins Mathlib
  LICENSE               an OSI license GitHub can detect
  Challenge.lean        trusted, Mathlib-only statements, proofs replaced by `sorry`
  Solution.lean         the same theorem names and statements, with proofs
  comparator.json
```

`lakefile.toml` must declare a library for each of the two modules, or
`lake build Challenge` fails with `unknown target`:

```toml
[[lean_lib]]
name = "Challenge"

[[lean_lib]]
name = "Solution"
```

For several challenges, keep them under one directory and declare it once,
for example `Audit/<Name>/Challenge.lean` with
`[[lean_lib]] name = "Audit"` and `globs = ["Audit.+"]`; the configuration
then names the modules `Audit.<Name>.Challenge` and `Audit.<Name>.Solution`.
[The comparator tier](comparator.md#writing-the-challenge-and-the-solution)
explains what goes in each file.

`comparator.json` uses the Palomar layout:

```json
{
  "challenge_module": "Challenge",
  "solution_module": "Solution",
  "theorem_names": ["MyProof.two_mul_le"],
  "definition_names": [],
  "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
  "enable_nanoda": true
}
```

Several configurations may be declared, as `comparator*.json` at the project
root or as `comparator/config*.json`. Every one of them must pass. See the
[comparator tier](comparator.md) for the details.

### 2. A registry and a package entry

Assume the shared registry from the setup above:

```bash
# ~/.lean-pkg/registry is the default registry location: no LEAN_PKG_REGISTRY needed

lean-pkg register my-proof --repo git@github.com:you/my-proof.git \
    --path src/my-proof --upstream-target palomar
# my-proof: registered (status=active, visibility=private, toolchain=leanprover/lean4:v4.32.0, pin=81a5d257c8)

lean-pkg claim my-proof --workspace laptop --agent alice --reason "comparator tier"
lean-pkg update my-proof --agent alice --status gate-pending --reason "lower-tier check submitted"
```

After this bootstrap, change the registry only through `lean-pkg`. Never
edit the YAML by hand.

### 3. The lower-tier receipt

Run your usual checks at the commit you want to certify (`lake build`, a
sorry scan, `#print axioms` over the exported theorems), freeze the report,
and write a receipt JSON. `lean-pkg` validates the receipt but does not
produce it:

```json
{
  "commit": "<40-hex HEAD of ~/src/my-proof>",
  "lean_toolchain": "leanprover/lean4:v4.32.0",
  "mathlib_rev": "<full Mathlib rev from lake-manifest.json>",
  "sorry_count": 0,
  "axioms": ["propext", "Quot.sound", "Classical.choice"],
  "report_sha256": "<sha256 of the frozen report>",
  "job_id": "slurm-123456",
  "checker": "lake build + sorry scan + #print axioms",
  "generated_at": "2026-10-05T12:00:00+00:00"
}
```

### 4. The comparator receipt

From the project directory, with a clean working tree and after
`lake exe cache get`:

```bash
cd ~/src/my-proof
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt
# == comparator @ 575674928e23...   (tool pins as checked out)
# ...
# nanoda kernel accepts the solution
# Running Lean default kernel on solution.
# Lean default kernel accepts the solution
# Your solution is okay!
# PASS comparator.json
# All 1 comparator configuration(s) accepted.
# comparator receipt written: .../my-proof/.lean-receipts/cb3d68a21454-comparator.json (1 config(s), commit cb3d68a21454, host policy: host <host> listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS)
# commit .lean-receipts/ in this repo, then record it:
#   lean-pkg comparator <name> --receipt .../my-proof/.lean-receipts/cb3d68a21454-comparator.json
#   (or at gate time: lean-pkg gate <name> --receipt R.json --comparator .../my-proof/.lean-receipts/cb3d68a21454-comparator.json)
```

This is the `legacy` mechanism's output. Both mechanisms print
`Your solution is okay!` for each accepted configuration
([mechanisms](comparator.md#mechanisms)); the `PASS` and `All ... accepted.`
lines come from the legacy check. The line to look for is
`comparator receipt written:`, which appears only when every configuration
is accepted.

Inside a Slurm job the host policy is satisfied automatically. The runner
writes `.lean-receipts/<commit12>-comparator.json` and one
`.lean-receipts/<commit12>-comparator-<cfgname>.log` per configuration. If
any configuration fails, it writes nothing.

### 5. Gate with comparator evidence

```bash
lean-pkg gate my-proof --receipt /path/to/receipt.json \
    --comparator .lean-receipts/cb3d68a21454-comparator.json
# receipt copied to .../my-proof/.lean-receipts/cb3d68a21454-slurm-123456.json — NOT committed there; commit it in the verified repo yourself
# comparator receipt already present at .lean-receipts/cb3d68a21454-comparator.json (not re-copied)
# my-proof: gate-pending -> verified-private (commit cb3d68a21454) with comparator evidence .lean-receipts/cb3d68a21454-comparator.json
```

Now commit the evidence in the package repository:

```bash
git add .lean-receipts && git commit -m "evidence: receipts for cb3d68a21454"
```

This commit changes only `.lean-receipts/`, so it is an *evidence-only
descendant* of the certified commit, and both receipts stay valid. See
[Receipts and trust](receipts.md).

### 6. Re-check the comparator evidence

```bash
lean-pkg comparator my-proof --check --receipt .lean-receipts/cb3d68a21454-comparator.json
# my-proof: comparator receipt valid at commit cb3d68a21454 (1 config(s)) — not recorded (--check)
```

`--check` re-derives the PASS from bytes and records nothing. It works at any
status, so you can validate a receipt the moment the runner wrote it.

### 7. Promotion dry run

```bash
lean-pkg promote my-proof
# promotion checklist for my-proof (~/src/my-proof):
#   ✓  reuse lint passed
#   ✓  LICENSE/COPYING present at repo root
#   ✓  lake-manifest.json tracked at HEAD
#   ✓  gate receipt bound to HEAD 148818d4c635 (evidence-only descendant of cb3d68a21454 (+3 evidence file(s)))
#   ✓  comparator PASS bound to HEAD 148818d4c635 (1 config(s), nanoda replay)
#   ✓  gh visibility matches registry (private)
#   ✓  automation disclosure not required (upstream_target=palomar)
# WARNING: a GitHub private→public flip is effectively ONE-WAY — ...
# DRY RUN — nothing changed. Re-run with --yes to execute.
```

`lean-pkg promote my-proof --yes` performs the flip
(`gh repo edit --visibility public`). Forks, clones, and caches stay public
afterwards, so treat it as irreversible.

Next: [How to](how-to.md) for task recipes, or the [CLI reference](cli.md).
