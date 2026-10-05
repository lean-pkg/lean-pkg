# lean-pkg

`lean-pkg` is a command-line registry and trust gate for Lean 4 formalization
packages. It keeps one YAML file as the source of truth for which packages
exist, who is working on each one (time-limited leases), and what evidence
binds each package to a verified commit. A package becomes public only through
a one-way promotion that requires two receipts:

- a **lower-tier receipt**: `lake build`, a sorry scan and `#print axioms`,
  bound to one commit;
- a **comparator receipt**: the [Palomar](https://github.com/PalomarRegistry/PalomarSubmission)
  acceptance bar. Trusted `Challenge.lean` statements are checked against
  untrusted `Solution.lean` proofs by `leanprover/comparator`, which replays
  every proof in the independent `nanoda` kernel inside a `landrun` sandbox.

The package ships two console scripts:

| Script | Purpose |
|---|---|
| `lean-pkg` | registry, leases, gate, comparator evidence, promotion, audit, agent contract (`lean-pkg llms`) |
| `comparator-receipt` | runs the bundled `comparator-check.sh` on a Lake project and writes a comparator receipt, only on PASS |

The lifecycle and the rules behind it are described in [DESIGN.md](DESIGN.md).
Agents should start with [AGENTS.md](AGENTS.md) or [llms.txt](llms.txt).

## Install

Python 3.11 or newer. The only runtime dependency is PyYAML.

```bash
pipx install git+https://github.com/chenle02/lean-pkg.git
# or
uv tool install git+https://github.com/chenle02/lean-pkg.git
# or, from a checkout, for development
python3 -m pip install -e '.[test]'
```

External tools, needed only for some commands:

- `git` for everything that reads a package repository;
- `gh`, **authenticated** (`gh auth login` or `GH_TOKEN`), for `promote` and
  `audit`. GitHub is used only as a visibility mirror: `promote` queries the
  repository's visibility and, with `--yes`, flips it to public. `audit`
  reconciles visibility and the archived flag;
- `reuse` (optional) for the REUSE lint item in the promotion checklist;
- for `comparator-receipt`: Linux, `elan` (`lean`, `lake`), `git`, `go`,
  `cargo` and `jq`. The first run builds the pinned comparator toolchain
  into `~/.cache/lean-ci-tools`, which takes about 25 minutes cold.

## Configuration

| Setting | Meaning |
|---|---|
| `--registry PATH`, then env `LEAN_PKG_REGISTRY`, then `./lean-packages.yaml` | registry file, in that order of precedence |
| `--events PATH`, else `lean-package-events.jsonl` next to the registry | append-only event log |
| `LEAN_PKG_NOTIFY_CMD` | optional command run once after a successful `promote --yes`, with the message on stdin. If it is unset, the tool prints "notify command not configured" and moves on |
| `COMPARATOR_RECEIPT_ALLOWED_HOSTS` | comma-separated host names on which `comparator-receipt` may run |
| `COMPARATOR_RECEIPT_ALLOW_HOST=1` | explicit override of the host policy (recorded in the receipt) |
| `SLURM_JOB_ID` | inside a Slurm job, the receipt run is always allowed and the job id becomes the receipt `job_id` |
| `COMPARATOR_CHECK` | path to an alternative `comparator-check.sh` (by default, the copy shipped in the package) |

Each package's `local_path` is resolved relative to `$HOME`, so several
machines that keep checkouts at the same home-relative place can share one
registry. Absolute paths also work.

## Worked example, end to end

This walk-through takes a small Lake project from registration to a promotion
dry run. Replace `you/my-proof` with your repository.

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
[Palomar submission layout](https://github.com/PalomarRegistry/PalomarSubmission)
for the full template, including `formalization.yaml`.

### 2. A registry and a package entry

```bash
mkdir -p ~/lean-registry && cd ~/lean-registry
printf 'version: 1\npackages: {}\n' > lean-packages.yaml   # one-time bootstrap
export LEAN_PKG_REGISTRY=~/lean-registry/lean-packages.yaml

lean-pkg register my-proof --repo git@github.com:you/my-proof.git \
    --path src/my-proof --upstream-target palomar
# my-proof: registered (status=active, visibility=private, toolchain=leanprover/lean4:v4.32.0, pin=81a5d257c8)

lean-pkg claim my-proof --workspace laptop --agent alice --reason "comparator tier"
lean-pkg update my-proof --agent alice --status gate-pending --reason "lower-tier check submitted"
```

After this bootstrap, change the registry only through `lean-pkg`. Do not
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
# All configurations accepted.
# comparator receipt written: .../my-proof/.lean-receipts/cb3d68a21454-comparator.json (1 config(s), commit cb3d68a21454, host policy: host <host> listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS)
```

Inside a Slurm job (`sbatch`/`srun`), the host policy is satisfied
automatically. The runner writes `.lean-receipts/<commit12>-comparator.json`
and one `.lean-receipts/<commit12>-comparator-<cfgname>.log` per
configuration. If any configuration fails, it writes nothing.

### 5. Gate with comparator evidence

```bash
lean-pkg gate my-proof --receipt /path/to/receipt.json \
    --comparator .lean-receipts/cb3d68a21454-comparator.json
# receipt copied to .../my-proof/.lean-receipts/cb3d68a21454-slurm-123456.json — NOT committed there; commit it in the verified repo yourself
# comparator receipt already present at .lean-receipts/cb3d68a21454-comparator.json (not re-copied)
# my-proof: gate-pending -> verified-private (commit cb3d68a21454) with comparator evidence .lean-receipts/cb3d68a21454-comparator.json
# results-ledger: record the outcome for my-proof in your results ledger
```

Now commit the evidence in the package repository:

```bash
git add .lean-receipts && git commit -m "evidence: receipts for cb3d68a21454"
```

This commit changes only `.lean-receipts/`, so it is an *evidence-only
descendant* of the certified commit, and both receipts stay valid
(DESIGN.md, "Evidence binding").

### 6. Re-check the comparator evidence

```bash
lean-pkg comparator my-proof --check --receipt .lean-receipts/cb3d68a21454-comparator.json
# my-proof: comparator receipt valid at commit cb3d68a21454 (1 config(s)) — not recorded (--check)
```

`--check` re-derives the PASS from bytes and records nothing. It works at any
status.

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
(`gh repo edit --visibility public`). The flip cannot be undone in practice:
forks, clones and caches stay public.

## Receipt schemas

**Lower-tier receipt** (validated by `gate`):

| Field | Rule |
|---|---|
| `commit` | 40 lowercase hex; HEAD, or HEAD is an evidence-only descendant of it |
| `lean_toolchain` | equals the repo's `lean-toolchain`, stripped |
| `mathlib_rev` | nonempty |
| `sorry_count` | integer, must be 0 |
| `axioms` | list of strings, subset of `gate.axioms_allowed` (default `propext`, `Quot.sound`, `Classical.choice`) |
| `report_sha256` | 64 hex characters |
| `job_id`, `checker`, `generated_at` | nonempty |

**Comparator receipt** (`"schema": "comparator-receipt/1"`, written by
`comparator-receipt`, re-derived from bytes by `gate --comparator`,
`comparator` and `promote`):

| Field | Rule |
|---|---|
| `commit` | equals the lower-tier receipt commit; HEAD, or HEAD is an evidence-only descendant |
| `lean_toolchain` | equals the repo's `lean-toolchain` |
| `tools` | `comparator`, `lean4export`, `nanoda`, `landrun`: 40-hex commits actually used |
| `host`, `job_id`, `generated_at` | nonempty |
| `host_policy` | the runner's host-policy reason (an override is visible here) |
| `result` | `"pass"` |
| `configs[]` | exactly the `comparator*.json` / `comparator/config*.json` files tracked at the bound commit. Each entry has `path`, `sha256` of its bytes, `challenge_module`, `solution_module`, `theorem_names` (non-empty), `definition_names`, `permitted_axioms` (each equal to the file), `log` = `.lean-receipts/<commit12>-comparator-<cfgname>.log`, `log_sha256`, `result: "pass"`. The log must contain the full line `Your solution is okay!` |

## Status enum

```
scratch -> active -> gate-pending -> verified-private -> public -> upstreamed
                                          any of the first four -> archived
```

`verified-private` is reached only through `gate`, and `public` only through
`promote --yes`. The other moves go through `lean-pkg update --status ...
--reason ...`. Run `lean-pkg llms` for the full contract, including event
names and exit codes.

## Differences from the lab-internal tool

`lean-pkg` 0.1.0 was extracted from a tool used inside one research group.
The behavior and JSON shapes are the same except for these deliberate changes:

- **Registry location.** There is no built-in path. The registry comes from
  `--registry`, then `LEAN_PKG_REGISTRY`, then `./lean-packages.yaml`. The
  event log defaults to a sibling `lean-package-events.jsonl`.
- **Notifications.** The built-in chat notifier is gone. Set
  `LEAN_PKG_NOTIFY_CMD` to opt in. **Breaking:** the `promote --yes --json`
  field for the notification result is now `notified` (boolean), replacing
  the old chat-specific `*_sent` field.
- **Host policy.** No host or user names are built in. `comparator-receipt`
  runs only inside a Slurm job, on a host listed in
  `COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or with `COMPARATOR_RECEIPT_ALLOW_HOST=1`.
- **Messages.** Internal decision-record numbers are no longer printed. The
  results-ledger reminder is a neutral one-liner, and `lean-pkg wiki`
  prints a generic banner and references.

## AI assistance

This tool was built with AI assistance. The original lab-internal
implementation of `lean-pkg`, `comparator-receipt` and their test suites was
written with Anthropic's Claude (Claude Code), directed and reviewed by the
maintainer. The 0.1.0 extraction into this repository was also done with
Claude: the packaging, the removal of lab-private defaults (registry path,
notification hook, host policy), the ported and new tests, the CI workflow
and this documentation. `comparator-check.sh` is vendored unchanged from the
Palomar verifier workflow, with its tool pins. The maintainer is responsible
for every merged change. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
disclosure rules that apply to contributions.

## Citation

A Zenodo DOI will be minted for the first public release. Until then:

```bibtex
@software{lean_pkg,
  author  = {Chen, Le},
  title   = {lean-pkg: a registry and trust gate for Lean 4 formalization packages},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/chenle02/lean-pkg},
  doi     = {10.5281/zenodo.TBD}
}
```

## License

Apache-2.0. See [LICENSE](LICENSE).
