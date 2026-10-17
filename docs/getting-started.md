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

The repository is public, so the `https` URL needs no GitHub credentials.
The documentation site is <https://lean-pkg.github.io/lean-pkg/>; the same
pages are the Markdown files under `docs/`. `lean-pkg doctor` exits 1 until
a registry exists (see below); that is expected on a fresh install.

External tools, needed only by some commands:

| Tool | Needed for |
|---|---|
| `git` | everything that reads a package repository |
| `gh`, authenticated (`gh auth login` or `GH_TOKEN`) | `promote` and `audit`; GitHub is only the visibility mirror |
| `reuse` (optional) | the REUSE lint line of the promotion checklist |
| Linux, `elan` (`lean`, `lake`), `go`, `cargo`, `jq` | `comparator-receipt`. Linux only: the legacy mechanism sandboxes with landrun (Landlock) and the toolchain mechanism with `bwrap`; macOS is not supported. The toolchain mechanism (Lean v4.35.0-rc2 or later, what Palomar runs) uses the kernels bundled with the toolchain and builds nothing of its own: a first run on a one-theorem project took seconds once the Mathlib cache was in place. The legacy mechanism's first run builds the pinned comparator tools into `~/.cache/lean-ci-tools` (override with `TOOLS_DIR`): from about 2 minutes on a fast machine to about 25 minutes cold, seconds afterwards. `lean-pkg comparator host-check` says which mechanisms this machine can run. Budget several GB of disk for the Lean toolchain and the Mathlib cache |

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
Replace `you/my-proof` with your repository and `src/my-proof` with the
path of your checkout under `$HOME`. `my-proof` is the package name given
to `register`; every later `NAME` is that name, not a path.

### 1. The project

The finished project looks like this:

```
~/src/my-proof/
  lean-toolchain        leanprover/lean4:v4.35.0-rc2
  lakefile.toml         requires Mathlib; one library, MyProof
  lake-manifest.json    tracked; pins Mathlib
  .gitignore            ignores .lake/
  LICENSE               an OSI license GitHub can detect (needed by promote, not by the receipt)
  MyProof/Challenge.lean  module MyProof.Challenge: trusted, Mathlib-only statements, proofs replaced by `sorry`
  MyProof/Solution.lean   module MyProof.Solution: the same theorem names and statements, with proofs
  comparator.json
```

The project must depend on Mathlib (a `mathlib` entry in
`lake-manifest.json`; a bare `lake new` project has none). The Challenge is
written in Mathlib's vocabulary, and `comparator-receipt`'s toolchain
mechanism, what Palomar runs, fetches Mathlib's build cache, so the runner
refuses a project without it before judging:

```
the toolchain mechanism needs a Mathlib dependency (lake-manifest.json has none); add it and run lake update
```

Add `mathlib` to the lakefile's requirements, run `lake update` and commit
the manifest. A project that knowingly has no Mathlib sets
`COMPARATOR_RECEIPT_NO_MATHLIB=1` (see
[The comparator tier](comparator.md#mechanisms)).

#### Start from scratch

No project yet? These commands make the one above, with one theorem. Paste
them as they are; `my-proof`, `MyProof` and `two_mul_le` are names you may
change later, consistently.
`lean-pkg new my-proof` writes the same files, plus the claims, coverage,
citation, license and verify script the [Quickstart](quickstart.md) uses.

```bash
mkdir -p ~/src/my-proof && cd ~/src/my-proof
git init
echo 'leanprover/lean4:v4.35.0-rc2' > lean-toolchain
printf '/.lake\n' > .gitignore

cat > lakefile.toml <<'EOF'
name = "my-proof"

[[require]]
name = "mathlib"
scope = "leanprover-community"
rev = "v4.35.0-rc2"

[[lean_lib]]
name = "MyProof"
globs = ["MyProof.+"]
EOF

mkdir -p MyProof
cat > MyProof/Challenge.lean <<'EOF'
module

public import Mathlib

public section

theorem MyProof.two_mul_le (n : ℕ) : n ≤ 2 * n := by
  sorry

end
EOF

cat > MyProof/Solution.lean <<'EOF'
module

public import Mathlib

public section

theorem MyProof.two_mul_le (n : ℕ) : n ≤ 2 * n := by
  omega

end
EOF

cat > comparator.json <<'EOF'
{
  "challenge_module": "MyProof.Challenge",
  "solution_module": "MyProof.Solution",
  "theorem_names": ["MyProof.two_mul_le"],
  "definition_names": [],
  "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
  "enable_nanoda": true
}
EOF

lake update
lake exe cache get
lake build MyProof.Challenge MyProof.Solution
git add -A && git commit -m "minimal Challenge/Solution project"
```

`lake update` writes `lake-manifest.json` and downloads Mathlib (several
GB with its cache); the build ends with `Build completed successfully` and
one ``declaration uses `sorry` `` warning, from the Challenge. Add a `LICENSE`
before you promote. `lake new MyProof math` is another start: it writes a
Mathlib `lakefile.toml` with the library `MyProof`; put the two files in its
`MyProof/` directory and write `comparator.json` as above. Its own
`MyProof.lean` and `MyProof/Basic.lean` have no `module` header (the
template of Lean v4.35.0-rc2): give them one, as below, or delete them, before
`palomar check`.

#### The module header

Both files use Lean's module system, which Palomar requires of every tracked
`.lean` file (`lean-pkg palomar check NAME` reports a file without it as
`source.module_required`). Three lines carry it:

- `module` is the file's first command; only ordinary comments may come
  before it.
- `public import Mathlib`: the theorem statements mention Mathlib, so the
  import must be public. A plain `import` is private to the module's
  implementation, and a statement that uses a name it brings in fails with
  `Unknown identifier`.
- `public section` ... `end`: in a module a declaration is private unless it
  is public, and `lake comparator` cannot export a private theorem (it stops
  with `Constant ... not found in environment`). Writing `public theorem` on
  each theorem works too.

A definition that a Challenge and its Solution share, and whose body a proof
unfolds, also needs `@[expose]` (`@[expose] public section` in the file that
defines it); without it the body is hidden from importing modules and
`unfold` fails. These forms were checked with `lake build` and
`lake comparator` on Lean v4.35.0-rc2. On a toolchain below v4.35.0-rc2,
which Palomar does not accept, leave out `module`, `public section` and
`end`, and write `import Mathlib`.

An existing project written without the module system can be ported with
`lean-pkg palomar port-modules NAME`: a dry run prints the diff and the
definitions likely to need `@[expose]`; `--write` applies it to a clean
working tree, never committing ([CLI reference](cli.md)).

#### Module names are file paths

A module's name is its path from the project root: `MyProof.Challenge` is the
file `MyProof/Challenge.lean`. A `Challenge.lean` at the root is the module
`Challenge`, so a configuration naming `MyProof.Challenge` while the file sits
at the root fails at the build stage:

```text
error: no such file or directory (error code: 2)
  file: .../my-proof/MyProof/Challenge.lean
```

Every module must also belong to a library in `lakefile.toml`, or
`lake build MyProof.Challenge` fails with `unknown target`
(`comparator-receipt` checks this with `lake --no-build build` at the end of
its `checkout` stage, before the cache download and the build). One `[[lean_lib]]`
whose `globs` cover the directory, as above, is enough. For several
challenges, keep them under one directory, for example
`Audit/<Name>/Challenge.lean` with `[[lean_lib]] name = "Audit"` and
`globs = ["Audit.+"]`; the configuration then names the modules
`Audit.<Name>.Challenge` and `Audit.<Name>.Solution`.
[The comparator tier](comparator.md#writing-the-challenge-and-the-solution)
explains what goes in each file.

#### comparator.json

`comparator.json` uses the Palomar layout. Write every field:

| Field | Meaning |
|---|---|
| `challenge_module`, `solution_module` | the two module names, as above |
| `theorem_names` | the theorems to compare, fully qualified; at least one |
| `definition_names` | Challenge definitions whose body the Solution may supply (compared by name and type only); `[]` when there are none, as here. A non-empty list needs a human sign-off in `lean-pkg-signoff.json` beside the configuration, never inside it ([the comparator tier](comparator.md#definition-holes-need-a-human-sign-off)) |
| `permitted_axioms` | a subset of `["propext", "Quot.sound", "Classical.choice"]` |
| `enable_nanoda` | `true`: also replay in the nanoda kernel |

Write `"definition_names": []` even when it is empty: the receipt records
the list, and `lean-pkg comparator --check` compares it with the file.

Several configurations may be declared, as `comparator*.json` at the project
root or as `comparator/config*.json`. Every one of them must pass. See the
[comparator tier](comparator.md) for the details.

### 2. A registry and a package entry

Assume the shared registry from the setup above:

```bash
# ~/.lean-pkg/registry is the default registry location: no LEAN_PKG_REGISTRY needed

lean-pkg register my-proof --repo git@github.com:you/my-proof.git \
    --path src/my-proof --upstream-target palomar
lean-pkg claim my-proof --workspace laptop --agent alice --reason "comparator tier"
lean-pkg update my-proof --agent alice --status gate-pending --reason "lower-tier check submitted"
```

`register` prints:

```text
my-proof: registered (status=active, visibility=private, toolchain=leanprover/lean4:v4.35.0-rc2, pin=065356127b)
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
  "lean_toolchain": "leanprover/lean4:v4.35.0-rc2",
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

From your checkout (here `~/src/my-proof`), with a clean working tree and
after `lake exe cache get`:

```bash
COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)" comparator-receipt
```

Expected output:

```text
mechanism: toolchain
comparator-receipt: stage 01-checkout: ok (dependency checkout per lake-manifest.json)
comparator-receipt: stage 02-cache-get: ok (Mathlib build cache)
comparator-receipt: stage 03-build: ok (build of MyProof.Challenge, MyProof.Solution)
comparator-receipt: 1 slot(s) in 1 config(s), 1 judge invocation(s), 1 at a time, mechanism toolchain
...
con-ron kernel accepts the solution
nanoda kernel accepts the solution
Lean default kernel accepts the solution
Your solution is okay!
slot 1/1 MyProof.two_mul_le done in 9.4s (1/1 done)
comparator-receipt: stage 04-comparator: ok (1 config(s) accepted (toolchain; 1 slot(s)))
comparator-receipt: stage 05-receipt: ok (.lean-receipts/cb3d68a21454-comparator.json)
comparator receipt written: .../my-proof/.lean-receipts/cb3d68a21454-comparator.json (1 config(s), commit cb3d68a21454, host policy: host <host> listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS)
commit .lean-receipts/ in this repo, then record it:
  lean-pkg comparator <name> --receipt .../my-proof/.lean-receipts/cb3d68a21454-comparator.json
  (or at gate time: lean-pkg gate <name> --receipt R.json --comparator .../my-proof/.lean-receipts/cb3d68a21454-comparator.json)
```

This is the `toolchain` mechanism's output, the default on Lean v4.35.0-rc2
or later (shape of a real run on 2026-10-10, shortened). Both mechanisms
print `Your solution is okay!` for each accepted configuration
([mechanisms](comparator.md#mechanisms)); the legacy check prints
`PASS comparator.json` and `All 1 comparator configuration(s) accepted.`
instead of the stage lines. The line to look for is
`comparator receipt written:`, which appears only when every configuration
is accepted. `COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)"` allows this
host; without it, outside Slurm, the run refuses by design. A
`WARN: soft stack limit is 8 MiB` line near the start is advice for large
modules; a small project can ignore it.

Inside a Slurm job the host policy is satisfied automatically. The runner
writes `.lean-receipts/<commit12>-comparator.json` and one
`.lean-receipts/<commit12>-comparator-<cfgname>.log` per configuration. If
any configuration fails, it writes nothing.

### 5. Gate with comparator evidence

```bash
lean-pkg gate my-proof --receipt /path/to/receipt.json \
    --comparator .lean-receipts/cb3d68a21454-comparator.json
```

Expected output:

```text
receipt copied to .../my-proof/.lean-receipts/cb3d68a21454-slurm-123456.json — NOT committed there; commit it in the verified repo yourself
comparator receipt already present at .lean-receipts/cb3d68a21454-comparator.json (not re-copied)
my-proof: gate-pending -> verified-private (commit cb3d68a21454) with comparator evidence .lean-receipts/cb3d68a21454-comparator.json
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
```

Expected output:

```text
my-proof: comparator receipt valid at commit cb3d68a21454 (1 config(s)) — not recorded (--check)
```

`--check` re-derives the PASS from bytes and records nothing. It works at any
status, so you can validate a receipt the moment the runner wrote it.

### 7. Promotion dry run

```bash
lean-pkg promote my-proof
```

Expected output:

```text
promotion checklist for my-proof (~/src/my-proof):
  ✓  reuse lint passed
  ✓  LICENSE/COPYING present at repo root
  ✓  lake-manifest.json tracked at HEAD
  ✓  gate receipt bound to HEAD 148818d4c635 (evidence-only descendant of cb3d68a21454 (+3 evidence file(s)))
  ✓  gate receipt .lean-receipts/cb3d68a21454-<job>.json tracked at HEAD (1 file(s))
  ✓  comparator PASS bound to HEAD 148818d4c635 (1 config(s): config and log bytes match the receipt, PASS line present; not re-run)
  ✓  comparator receipt .lean-receipts/cb3d68a21454-comparator.json and its 1 log(s) tracked at HEAD (2 file(s))
  ✓  HEAD 148818d4c635 contained in origin/main (local refs; nothing fetched)
  ✓  gh visibility matches registry (private)
  ✓  automation disclosure not required (upstream_target=palomar)
WARNING: a GitHub private→public flip is effectively ONE-WAY — ...
DRY RUN — nothing changed. Re-run with --yes to execute.
```

`lean-pkg promote my-proof --yes` performs the flip
(`gh repo edit --visibility public`). Forks, clones, and caches stay public
afterwards, so treat it as irreversible.

Next: [How to](how-to.md) for task recipes, or the [CLI reference](cli.md).
