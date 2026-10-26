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
  receipt keys `producer`, `claim_axioms` and `report_compressed`, and
  `kind: library` in `coverage/1`, were added this way.
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
| Site profile | `lean-pkg-sites/<name>.yaml` (and the built-in profiles) | `schema: site-profile/1` | `site-profile/1` | none needed yet |
| Source baseline | `data/source-baselines/arXiv-<id>v<N>.json` | `schema: source-baseline/1` | `source-baseline/1` | none needed yet |
| Upgrade record | `docs/upgrades/<version>.yaml` (this repository) | `schema: lean-pkg-upgrade-record/1` | `lean-pkg-upgrade-record/1` | none needed yet |

Blocks and sidecars inside the receipt formats carry their own markers and
follow the same rule: the sacct seal (`slurm-sacct-seal/1`, in
`slurm_sacct.schema`), the olean manifest (`olean-manifest/1`) and the
dependency fill provenance (`lean-pkg-fill-provenance/1`).

**Internal, not a public format.** The comparator runner's working files
under `comparator-stages/` (`comparator-stages/1`, `comparator-attempt/1`,
`comparator-partial/1`, `comparator-slot/1`), the comparator types
checkpoint, and the search cache (`lean-pkg-palomar-cache/1`) belong to
the run that wrote them and may change in any release. So do the data
files lean-pkg ships (`lean-pkg-toolchain-advisories/1`,
`lean-pkg-stack/1`, `lean-pkg-upgrade-breaking/1`), which only the same
release reads.

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
| `gate-passed` | `gate` | `commit`, `job_id`, `checker`, `receipt`, `descendant_of`, `docs_paths`, `comparator`, `comparator_cleared`, `status_unchanged`, `pins` |
| `regated` | `gate --regate` | `previous_commit`, `commit`, `job_id`, `checker`, `receipt`, `descendant_of`, `docs_paths`, `comparator`, `comparator_cleared`, `status_unchanged`, `pins`, `reason`, `non_descendant` |
| `comparator-attached` | `comparator attach` | `commit`, `comparator`, `host`, `job_id`, `descendant_of`, `docs_paths` |
| `comparator-replaced` | `comparator attach --replace` | `commit`, `comparator`, `host`, `job_id`, `replaced`, `descendant_of`, `docs_paths` |
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

The table is `EVENT_KEYS` in `src/lean_pkg/cli.py`. The test suite checks
that every event a call in the source can write is listed there and here,
and that every event line any test makes lean-pkg write has only the keys
listed for its event: a new event or key fails the suite until it is
documented.
