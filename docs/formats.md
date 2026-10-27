---
title: On-disk formats
---

[&larr; lean-pkg documentation](index.md)

# On-disk formats

Every file lean-pkg writes or reads, who owns its format, the version it
is at, the versions a reader accepts, and how an older file is brought
forward. This is the format freeze of the
[1.0 command tree](design/1.0-command-tree.md#6-on-disk-formats)
(section 6, items 1 to 3); `tests/test_formats.py` checks the versions
below against the code.

## The rule for changes within a version

A format version names a set of keys and what each one means.

- **A new optional key keeps the version.** Readers that do not know the
  key ignore it, and a file without it means what it meant before. The
  receipt keys `producer`, `claim_axioms` and `report_compressed`,
  `kind: library` in `coverage/1`, and the conditional-claim keys
  (`hypotheses`, `discharges` and `witness` in `claims/1`, an `inputs`
  entry `{hypothesis, text}` in `coverage/1`; proposal 0003) were added
  this way. `claims/1` and `coverage/1` are strict, so an older lean-pkg
  refuses a file that uses a key added after it: a package that uses one
  needs a release that knows it in its CI pin.
- **A new required key, a removed key, or a changed meaning bumps the
  version** (`claims/1` to `claims/2`, `v: 1` to `v: 2`). A lean-pkg that
  meets a version newer than it knows refuses the file, naming the upgrade,
  rather than reading part of it; a reader of the event log skips such a
  line and says so.
- **Evidence is never rewritten.** Receipts bind bytes, so readers accept
  every earlier receipt version forever, and there is no migration for
  them. State files (registry, claims, index entries) have a migration
  command, a dry run by default where the command has one, and readers
  accept the older versions until the next major release
  ([section 5](design/1.0-command-tree.md#5-migration-and-deprecation-policy)).

**One exception: `lean-pkg-binding.yaml` refuses unknown keys.** The file
grants a rule (the [docs-only descendant rule](receipts.md#docs-only-descendants-opt-in)),
so a key this lean-pkg does not know is not ignored: the file does not opt
in, and the output names the key. Repeated keys, anchors, aliases and
explicit tags are refused the same way, and `docs_descendants` must be the
literal `true` or `false`. A new optional key there still keeps
`lean-pkg-binding/1`, but an older lean-pkg reading a file that uses it
falls back to the evidence-only rule, which is the safe direction.

A new version of any lean-pkg format is a schema change: it starts as a
[design proposal](proposals/README.md).

## Formats lean-pkg owns

| Format | File | Version marker (current) | Readers accept | Migration |
|---|---|---|---|---|
| Registry | `lean-packages.yaml` | `version: 3` | 1, 2 and 3; a newer version is refused ("upgrade lean-pkg") | `lean-pkg registry migrate` (in place, additive, idempotent; `--dry-run`) |
| Event log | `lean-package-events.jsonl` | `v: 1` on each line | 0 (a line without `v`) and 1; a line with a newer or unreadable `v` is not interpreted, and the reader says so | none: the log is append-only, old lines stay as written |
| Claims | `claims.yaml` | `schema: claims/1` | `claims/1` | `lean-pkg claims migrate` (a legacy `claims.json` to a `claims/1` skeleton) |
| Coverage | `coverage.yaml` | `schema: coverage/1` | `coverage/1` | none needed yet |
| Lower-tier receipt | `.lean-receipts/<c12>-<job>.json` | `schema: lower-receipt/1` | 0 (no `schema` key: every receipt written before 0.22.0) and `lower-receipt/1`; a newer `lower-receipt/N` is refused ("upgrade lean-pkg"), any other value is refused | none: evidence is never rewritten |
| Comparator receipt | `.lean-receipts/<c12>-comparator.json` | `schema: comparator-receipt/2` | `comparator-receipt/1` (legacy mechanism implied) and `/2` | none: evidence is never rewritten |
| Comparator estimate | printed JSON (`receipt comparator --estimate`) | `schema: comparator-estimate/2` | not read back as evidence (`receipt check` skips it) | none needed |
| Sign-off sidecar | `lean-pkg-signoff.json` | `schema: lean-pkg-signoff/1` | `lean-pkg-signoff/1` | from inline `judged_as` keys: `lean-pkg comparator migrate-signoff PATH` |
| Index entry | `index/<name>/entry.yaml` | `schema: index-entry/2` | `index-entry/1` and `/2` | `lean-pkg index migrate` (`--check` to list without writing) |
| Binding opt-in | `lean-pkg-binding.yaml` (the package directory) | `schema: lean-pkg-binding/1` | `lean-pkg-binding/1`; a newer `lean-pkg-binding/N`, an unknown key or any other invalid file does not opt in (fail closed: the evidence-only rule binds, and the output names the file and the key) | none needed yet (written by hand; [docs-only descendants](receipts.md#docs-only-descendants-opt-in)) |
| Site profile | `lean-pkg-sites/<name>.yaml` (and the built-in profiles) | `schema: site-profile/1` | `site-profile/1` | none needed yet |
| Source baseline | `data/source-baselines/arXiv-<id>v<N>.json` | `schema: source-baseline/1` | `source-baseline/1` | none needed yet |
| Upgrade record | `docs/upgrades/<version>.yaml` (this repository) | `schema: lean-pkg-upgrade-record/1` | `lean-pkg-upgrade-record/1` | none needed yet |

Blocks and sidecars inside the receipt formats carry their own markers and
follow the same rule: the sacct seal (`slurm-sacct-seal/1`, in
`slurm_sacct.schema`), the olean manifest (`olean-manifest/1`) and the
dependency fill provenance (`lean-pkg-fill-provenance/1`). So does the
hypotheses section of the sealed verify log (`hyp-probe/1`, in its header
line; [The hypotheses section](#the-hypotheses-section-of-the-verify-log-hyp-probe1)). The `ci` block
of a receipt from a GitHub Actions run has no marker of its own: it is an
additive key of `lower-receipt/1` ([The `ci` block](#the-ci-block)).

**Internal, not a public format.** The comparator runner's working files
under `comparator-stages/` (`comparator-stages/1`, `comparator-attempt/1`,
`comparator-partial/1`, `comparator-slot/1`), the comparator types
checkpoint, and the search cache (`lean-pkg-palomar-cache/1`) belong to
the run that wrote them and may change in any release. So do the data
files lean-pkg ships (`lean-pkg-toolchain-advisories/1`,
`lean-pkg-stack/1`, `lean-pkg-upgrade-breaking/1`,
`lean-pkg-verify-workflow-pins/1`), which only the same release reads.

**Reports.** Output that is printed or written on request and never read
back is versioned too, so a script can tell what it has:
`claims-ledger/1`, `claims-rebase/1`, `host-routes/1`, `exercise-check/1`,
`exercise-batch/1`, `lean-pkg-extract-plan/1`, `lean-pkg-upgrade/1`,
`formalization-kept/1`, `comparator-types/1`, `lean-pkg-lean-diag/1`,
`lean-pkg-dup-constants/1` and `lean-pkg-comparator-contracts/1`. The
`lean-pkg-extract` manifest a user writes is `lean-pkg-extract/1`.

## Formats owned by outside projects

lean-pkg reads and writes these but does not define them: it follows the
version its release targets, and says which one.

| Format | Owner | Version lean-pkg targets | How lean-pkg handles another version |
|---|---|---|---|
| `formalization.yaml` | mathlib-initiative ([formalization.yaml](formalization.md)) | `version: v0.4`, validated against the vendored JSON schema `formalization-0.4.schema.json` | `lean-pkg formalization render NAME --write` writes v0.4; a later version is adopted by a lean-pkg release that vendors its schema (an upgrade-record entry of kind `format`) |
| Comparator configuration | Palomar (`comparator.json`, `comparator/config*.json`) | Palomar's keys; the deprecated inline `judged_as` is warned | `lean-pkg comparator migrate-signoff PATH` moves inline sign-offs to the sidecar |
| Headline configuration | Palomar's six keys (`comparator.headline.json`, or the registry's `headline_config`) | as a comparator configuration | as a comparator configuration |
| Palomar submission rules | PalomarRegistry/PalomarSubmission | `browser-preflight-policy.json` schema 1 at commit `d4e41c1` (committed 2026-10-05, read 2026-10-16) | `lean-pkg palomar check --rules FILE` reads a newer policy file downloaded by hand; lean-pkg never fetches it |

## Lower-tier receipt versions

`receipt slurm` and `receipt systemd` (alias `receipt local`) write
`"schema": "lower-receipt/1"` as the first key of every receipt since
0.22.0. Version 1 is version 0 with the marker: every key, rule and
optional key is the same ([lower-tier receipt schema](receipts.md#lower-tier-receipt-schema)).

| Version | Marker | Read by `gate`, `gate --check`, `receipt check`, the ledger, `next`, `page`, `palomar`, `index check`, `formalization` |
|---|---|---|
| 0 | no `schema` key | judged exactly as before, forever |
| 1 | `"schema": "lower-receipt/1"` | the same rules |
| newer | `"schema": "lower-receipt/2"` and later | refused: `lower-tier receipt schema lower-receipt/2 is not supported (...); this lean-pkg is older than the receipt: upgrade lean-pkg` (exit 1) |
| other | any other `schema` value | refused: `receipt schema must be 'lower-receipt/1' (or absent, ...)` (exit 1) |

A receipt that lacks a required key is refused for that key first, as
before, unless its marker names a newer version: then the upgrade message
comes first, since a newer version may have changed the key set.

## The `ci` block

`ci` is an optional, additive key of `lower-receipt/1`
([proposal 0008](proposals/0008-github-actions-compute-receipt.md)): the
GitHub Actions run a receipt came from, every value from a variable the
runner sets. The marker stays `lower-receipt/1`: a receipt without `ci`
means what it meant before, and a lean-pkg that predates the key ignores it
(it then reads the receipt as an ordinary lower-tier receipt and checks no
`ci` rule). A receipt whose `job_id` is `gha-<run_id>-<run_attempt>` and
whose `ci` block passes level 1 is the job of the built-in `github-actions`
scheduler and can back `coverage.yaml` `compute`
([Compute sites](sites.md#github-actions)). `lean-pkg receipt ci` writes
the block inside the job, keys in the order of the table below
([A lower-tier receipt from GitHub Actions](receipts.md#a-lower-tier-receipt-from-github-actions)).

Level 1 is offline and is what `gate`, `gate --check`, `coverage check`,
`page` and `receipt check` apply. A block that fails any rule below refuses
the receipt (`ci block (a GitHub Actions run, level 1; ...): ...`, every
failing rule named). Unknown keys inside `ci` (and inside `runner`) are
ignored, as for `producer`.

| Key | Type | Level 1 check |
|---|---|---|
| `provider` | string | equals `github-actions` |
| `server_url` | string | equals `https://github.com` (GitHub Enterprise Server is not accepted) |
| `repository` | `OWNER/REPO` | well formed, and the `OWNER/REPO` prefix of `workflow_ref` |
| `repository_id` | integer > 0 | the type (level 2 compares it with the run) |
| `run_id` | integer > 0 | `job_id` is `gha-<run_id>-<run_attempt>` |
| `run_attempt` | integer >= 1 | as `run_id` |
| `job` | one line of text | shown; not load-bearing |
| `event` | string | `push` or `workflow_dispatch`; every other event is refused |
| `head_sha` | 40 lowercase hex | equals the receipt's `commit` |
| `workflow_ref` | `OWNER/REPO/.github/workflows/FILE.yml@REF` | well formed (the file directly under `.github/workflows/`, `.yml` or `.yaml`) |
| `workflow_sha` | 40 lowercase hex | the commit the workflow file is read at |
| `workflow_file_sha256` | 64 lowercase hex | the workflow file at `workflow_sha` hashes to it when that commit is in the clone; when it is not, a shown skip (`· ci: workflow file ... not rehashed`) |
| `job_workflow` | `OWNER/REPO/.github/workflows/FILE.yml@REF` or `null` | well formed when not null |
| `runner` | mapping `{environment, os, arch, image, image_version}` | `environment` is `github-hosted` (a self-hosted runner is refused, naming `receipt local`); `os`, `arch` one line of text; `image`, `image_version` one line of text or null |
| `verify_log_sha256` | 64 lowercase hex | equals `report_sha256` (which the committed redacted log must hash to, as for every receipt) |
| `attestation` | file name (no `/`), optional | the form only (the bundle is checked at level 3) |

Level 1 is consistency with the receipt, the commit and the committed log:
the strength of a `slurm-<J>` receipt, and as forgeable, since every value
is text the author could write ([Trust model](trust-model.md#not-covered-lean-pkg-relies-on-honesty-here)).
It backs a `compute` job but never puts the GitHub Actions sentence on the
page: that needs a corroborated run (level 2, `--corroborate`; [Corroborating
the run](receipts.md#corroborating-the-run-level-2---corroborate)). A receipt with a `gha-` job id and no `ci` block still passes
`gate` and backs no `compute` job.

### In a comparator receipt

`ci` is also an optional, additive key of `comparator-receipt/2` (stage S5
of the proposal): `lean-pkg receipt comparator` writes it when it runs on a
GitHub-hosted runner, with `job_id` `gha-<run_id>-<run_attempt>` and
`host_policy` `GitHub Actions run <run_id> attempt <run_attempt>`
([On a GitHub-hosted runner](comparator.md#on-a-github-hosted-runner)). The
schema stays `comparator-receipt/2`, and a lean-pkg that predates the key
ignores it, as `validate_comparator` ignores every key it does not read.

The block has the keys of the table above except `verify_log_sha256`: a
comparator receipt has no verify log, and each of its comparison logs is
bound by `configs[].log_sha256`. Level 1 applies the same rules, with
`commit` the comparator receipt's own, plus two of its own: `host_policy`
must name the run (`GitHub Actions run <run_id> attempt <run_attempt>`), and
a `verify_log_sha256` key is refused. `gate`, `gate --check` and
`receipt check` refuse a comparator receipt whose block fails
(`comparator ci block (a GitHub Actions run, level 1; ...): ...`). Only a
comparator receipt whose block passes backs a `github-actions` `compute`
job, as only a sealed one backs a Slurm job; one with a `gha-` job id and no
block is named in the refusal.

## The hypotheses section of the verify log (`hyp-probe/1`)

**Written since stage H1 of [proposal 0003](proposals/0003-conditional-claims-and-discharge.md)
(conditional claims, [#320](https://github.com/lean-pkg/lean-pkg/issues/320)); read by the claims
ledger since stage H3** ([Conditional claims](lifecycle.md#conditional-claims)), which judges the
facts from the sealed log the gate receipt binds, at the gate-bound commit, on every call.

The lower-tier verify scripts (`receipt slurm --template` and the `verify.sh` that `lean-pkg new`
writes) append this section after the
[per-claim axioms](receipts.md#per-claim-axioms-claim_axioms) lines, before the seal, so
`report_sha256` covers it. The receipt schema does not change: there is no new receipt key and
`lower-receipt/1` stays. `hyp-probe/1` is the marker in the section's header line. A small
core-Lean program, `HypProbe.lean`, prints facts and Python judges them: the probe never prints a
verdict. Every line after the header is a tag, a colon, a space and one JSON object with exactly
the keys below. An excerpt of the section the test fixture (`tests/fixtures/hypotheses`) gives:

```text
== lean-pkg hypotheses (hyp-probe/1) ==
hyp-decl: {"v":1,"theorem":"Paper.main","binders":[{"i":0,"head":"Hyp.A","kind":"def","module":"Fx.Statements","explicit":true},{"i":1,"head":"Hyp.B","kind":"def","module":"Fx.Statements","explicit":true}],"data_binders":0}
hyp-decl: {"v":1,"theorem":"Paper.useAx","binders":[{"i":0,"head":"Heads.Ax","kind":"axiom","module":"Fx.Heads","explicit":true}],"data_binders":0}
hyp-missing: {"v":1,"theorem":"Paper.gone"}
hyp-challenge-build: {"module":"Fx.Challenge","ok":true}
hyp-challenge: {"v":1,"theorem":"Audit.main","binders":[{"i":0,"head":"Hyp.A","kind":"def","module":"Fx.Statements","explicit":true},{"i":1,"head":"Hyp.B","kind":"def","module":"Fx.Statements","explicit":true}],"data_binders":0,"config":"comparator.json"}
hyp-challenge: {"v":1,"theorem":"Audit.main_of_B","binders":[{"i":0,"head":"Hyp.B","kind":"def","module":"Fx.Statements","explicit":true}],"data_binders":0,"config":"comparator.json"}
hyp-challenge: {"v":1,"theorem":"Audit.param_of","binders":[],"data_binders":1,"config":"comparator.json"}
hyp-challenge: {"v":1,"theorem":"Audit.proofA","binders":[],"data_binders":0,"config":"comparator.json"}
hyp-corollary: {"corollary":"Audit.main_of_B","of":"Audit.main","drops":["Hyp.A"],"binders":[0]}
hyp-corollary: {"corollary":"Audit.param_of","of":"Audit.param","drops":["Hyp.PA"],"binders":[1]}
hyp-skip: {"theorem":"Audit.many","reason":"13 distinct hypothesis constants, more than 12: the corollary search is not run"}
hyp-proof: {"proof":"Audit.proofA","proves":"Hyp.A"}
hyp-witness: {"witness":"Audit.witness_ok","of":"Audit.three2"}
hyp-done: {"v":1,"decls":7,"challenge":23,"configs":["comparator.json"]}
```

The probe has two parts. The **decl part** runs whenever the package has a `claims.yaml`, under
the job's LEAN_PATH, on the modules the claims' `file`s name. The **Challenge part** runs only
when `claims.yaml` uses `hypotheses`, `discharges` or `witness` (a line matching
`^[[:blank:]-]+(hypotheses|discharges|witness):`), so other packages pay nothing. It reads the
comparator configurations (`comparator*.json` and `comparator/config*.json`), builds each
Challenge module they name with `lake build`, its output in a side log
(`hyp-challenge-build.log`: in the job's work directory for the template, beside the log for
`verify.sh`), **outside the sealed log**, so the Challenge's `sorry` warnings never reach
`sorry_count`; only the `hyp-challenge-build` status line enters the sealed log. It then imports
every Challenge module in a separate run from the decl part. The job, not Lean, writes the header
and the final `hyp-done` line, counting the lines Lean printed; a part whose run fails is replaced
by one `hyp-error` line, and the job goes on.

| Line | Printed by | When | JSON keys |
|---|---|---|---|
| `hyp-decl:` | decl part | once per claim `decl` found in the built environment | `v` (1); `theorem` (the claim's `decl`); `binders`: every binder of `Prop` type whose head is a constant, explicit or not, each `{i, head, kind, module, explicit}` (its position in the telescope, counting every binder from 0; the head constant; the head's declaration kind: `def`, `inductive`, `axiom`, `opaque`, `theorem`, `quot`, `constructor` or `recursor`; the module that declares the head, `""` when none; and whether the binder is explicit); `data_binders` (the number of binders that are not `Prop`s) |
| `hyp-missing:` | decl part | once per claim `decl` not in the built environment | `v`, `theorem` |
| `hyp-challenge-build:` | Challenge part | once per Challenge module the configurations name | `module`, `ok` (whether `lake build` succeeded; its output is in the side log) |
| `hyp-challenge:` | Challenge part | once per name in each configuration's `theorem_names` that is in the Challenge environment | the keys of `hyp-decl`, plus `config` (the configuration's path) |
| `hyp-corollary:` | Challenge part | for each ordered pair of distinct Challenge theorems where the corollary's type is the other theorem's type with every binder headed by some non-empty set of its hypothesis constants removed | `corollary`, `of`, `drops` (the hypothesis constants whose binders were removed, in the order they first occur), `binders` (the removed positions in `of`'s telescope) |
| `hyp-proof:` | Challenge part | when a Challenge theorem's type is exactly a hypothesis constant (the head of a `Prop` binder of some Challenge theorem) | `proof`, `proves` |
| `hyp-witness:` | Challenge part | when a Challenge theorem's type is the expected witness type of a Challenge theorem with at least one hypothesis: `∃` over its non-`Prop` binders in order, of the right-nested `∧` of all its `Prop` binders in order | `witness`, `of` |
| `hyp-skip:` | Challenge part | the corollary search was not run for a theorem (more than 12 distinct hypothesis constants) | `theorem`, `reason` |
| `hyp-error:` | either part | the part could not run (for example, the program does not compile on the package's toolchain, a module is not built, or a Challenge module did not build), or a configured Challenge name is not in the Challenge environment | `phase` (`decl` or `challenge`), `message` (`exit N`, then the first error line with every path prefix removed, at most 200 characters, so a redaction never alters it) |
| `hyp-done:` | the job, last line | always, once | `v` (1); `decls` (the number of `hyp-decl` and `hyp-missing` lines); `challenge` (the number of `hyp-challenge` lines); `configs` (the configuration paths read; `[]` when the Challenge part did not run) |

Only positive facts are printed: no line says that a corollary or a proof does not match. Types
are compared with `Expr.eqv`, which ignores binder names, plus equal binder annotations on the
outer binders of the two types, and the other theorem's universe parameters renamed positionally
to the theorem's; there is no unfolding, `whnf` or `isDefEq`. Only non-dependent binders are
dropped (a binder a later binder or the conclusion mentions cannot be), a hypothesis constant
names all of its binders, and the search covers the non-empty subsets of at most 12 distinct
hypothesis constants. A `theorem` head cannot occur in practice: the kernel refuses a theorem
whose type is not a proposition, so no theorem can be a binder's type.

Refusals, at write time (`receipt slurm`, `receipt systemd`, `--check`; the receipt is not
written): a section without `hyp-done`, counts that do not match it (and a `hyp-challenge` line
naming a configuration `hyp-done` does not list), a line printed twice (or a second line for the
same `decl`, Challenge theorem and configuration, or Challenge module), a malformed line (not one
JSON object, or not exactly its tag's keys and types, or `v` not 1), a line after `hyp-done`, a
`hyp-*:` line before the header or with an unknown tag, a second header, and a redaction that
alters a section line. A log without the section stays a valid receipt, as before. `hyp-error`
and `hyp-skip` are never refusals. The claims ledger reads a section the write-time check would refuse,
no section, a `hyp-error` of the part it needs, a failed Challenge build or a `hyp-skip` of the theorem
it needs as missing facts: the claims that use `hypotheses`, `discharges` or `witness` are then
`hypotheses-unchecked`, never discharged. Only the decl part is needed at the gate stage.

## Event log

`lean-package-events.jsonl` holds one JSON object per line, keys sorted,
appended and never rewritten. Every line has `ts` (UTC ISO-8601 with an
offset, second precision), `event`, `name` (the package, or null) and,
since 0.22.0, `v: 1`. A line without `v` (written before) is version 0 and
is read as before. A reader that interprets events (the prior-art note of
`lease claim`) skips a line whose `v` is newer than 1, or is not a
non-negative integer, and prints one warning naming the upgrade; a reader
that only shows lines (the board's last event, the pending-change summary
of a committed-mode refusal) shows them as they are.

Package events (every event below except the four log-only ones) are also
appended to the package's `history[]` in the registry, without `v`, and may
carry `actor`: the `--agent` of the `gate`, `comparator`, `record`,
`publish`, `claims sync` or `verify` run that wrote an event with no
`agent` key. The log-only events are `registry-created`,
`registry-migrated`, `index-added` and `prior-art-searched`.

The keys of each event, beyond `ts`, `event`, `name` and `v`. Not every key
is on every line: a key that does not apply is left out. `<field>_from`
stands for one key per field `update` changed, holding its old value.

| Event | Written by | Keys |
|---|---|---|
| `registry-created` | `registry init` | `registry`, `version`, `forced` |
| `registry-migrated` | `registry migrate` | `from_version`, `to_version`, `changes` |
| `registered` | `register` | `repo`, `local_path`, `status`, `visibility`, `lean_toolchain`, `mathlib_pin`, `workspace`, `agent`, `reason`, `like`, `license_spdx` |
| `updated` | `update` | `fields`, `reason`, `forced`, `notes_replaced`, `<field>_from` |
| `claimed` | `lease claim` | `agent`, `workspace`, `ttl_hours`, `reason`, `contact` |
| `claim-forced` | `lease claim --force` | `agent`, `workspace`, `ttl_hours`, `reason`, `contact` |
| `claim-refreshed` | `lease claim` by the holder, `lease renew` | `agent`, `workspace`, `ttl_hours`, `reason`, `contact`, `contact_cleared` |
| `released` | `lease release` | `agent`, `workspace`, `released_by`, `reason` |
| `release-forced` | `lease release --force` | `agent`, `workspace`, `released_by`, `reason` |
| `lease-expired` | `registry audit --write` | `agent`, `workspace`, `claimed_at`, `ttl_hours` |
| `gate-passed` | `gate` | `commit`, `job_id`, `checker`, `receipt`, `descendant_of`, `docs_paths`, `opt_in`, `comparator`, `comparator_cleared`, `status_unchanged`, `pins` |
| `regated` | `gate --regate` | `previous_commit`, `commit`, `job_id`, `checker`, `receipt`, `descendant_of`, `docs_paths`, `opt_in`, `comparator`, `comparator_cleared`, `status_unchanged`, `pins`, `reason`, `non_descendant` |
| `comparator-attached` | `comparator attach` | `commit`, `comparator`, `host`, `job_id`, `descendant_of`, `docs_paths`, `opt_in` |
| `comparator-replaced` | `comparator attach --replace` | `commit`, `comparator`, `host`, `job_id`, `replaced`, `descendant_of`, `docs_paths`, `opt_in` |
| `promoted` | `publish --yes` | `commit`, `visibility` |
| `publication-withdrawn` | `update --withdraw-publication` | `status_from`, `status_to`, `visibility_from`, `visibility_to`, `reason`, `by`, `github_visibility`, `promoted_commit`, `forced` |
| `publication-hold-released` | `update --release-publication-hold` | `reason`, `hold`, `by`, `forced` |
| `audit-fix` | `registry audit --write` | `field`, `old`, `new`, `source` |
| `relocated` | `repo move` | `old_path`, `new_path`, `reason`, `cleared`, `agent`, `redetected` |
| `status-changed` | `repo move` (when the status moves) | `status_from`, `status_to`, `reason` |
| `palomar-prepared` | `palomar prepare` | `commit`, `bundle`, `bundle_sha256` |
| `claims-synced` | `claims sync` | `commit`, `file`, `claims` |
| `index-added` | `index add` | `repo`, `commit`, `trust`, `index` |
| `prior-art-searched` | `search --record` | `term`, `sources`, `hits`, `record`, `decision`, `actor` |
| `evidence-retracted` | `record NAME retract` | `retracted_event`, `retracted_ts`, `retracted_entry`, `reason` |
| `consent-recorded` | `record` | `who`, `role`, `pointer`, `reason` |
| `palomar-preflight-pass` | `record` | `commit`, `run`, `reason` |
| `palomar-submitted` | `record` | `commit`, `submission_id`, `comparator_config`, `reason` |
| `palomar-review` | `record` | `commit`, `outcome`, `reason` |
| `palomar-registered` | `record` | `commit`, `id`, `version`, `comparator_config`, `reason` |
| `palomar-withdrawn` | `record` | `id`, `version`, `reason` |
| `doi-minted` | `record` | `doi`, `tag`, `commit`, `reason`, `concept`, `track` |
| `reservoir-listed` | `record` | `url`, `reason` |
| `mathlib-pr-opened` | `record` | `url`, `reason` |
| `mathlib-pr-merged` | `record` | `url`, `reason` |

The table is `EVENT_KEYS` in `src/lean_pkg/core.py`. The test suite checks
that every event a call in the source can write is listed there and here,
and that every event line any test makes lean-pkg write has only the keys
listed for its event: a new event or key fails the suite until it is
documented.
