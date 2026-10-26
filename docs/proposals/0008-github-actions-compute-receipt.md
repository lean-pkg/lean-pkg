# 0008: A compute receipt from a CI runner (GitHub Actions first)

Status: draft. Author: Claude, the lean-pkg operations agent, drafting for Le Chen. Created: 2026-10-26.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

A package author with a public GitHub repository and no cluster account gets a lower-tier receipt from a
GitHub Actions run that backs `coverage.yaml` `compute` the way `slurm-<J>` does today, and that a third
party can corroborate without trusting the author. The receipt is an ordinary `lower-receipt/1` receipt
with `job_id: gha-<run_id>-<run_attempt>` and an additive `ci` block. A new scheduler `github-actions` and
a built-in site profile of the same name read it. Three corroboration levels are defined: offline
consistency (level 1, as strong as a Slurm receipt and as forgeable), the public run API (level 2,
`--corroborate`) and a Sigstore attestation (level 3, `--attested`). The page's GitHub Actions sentence
renders only at level 2 or 3, and levels 2 and 3 require lean-pkg's pinned reusable `verify.yml`. New
commands: `lean-pkg receipt ci` (the writer, in the job), `receipt ci --collect RUN` and
`receipt ci --template`. This proposal turns section 4 of
[the sites and CI receipts design note](../design/sites-and-ci-receipts.md#4-design-a-compute-receipt-from-a-ci-runner-github-actions-first)
into a decision; no code changes until it is accepted.

## Motivation

- **Verification with no cluster.** Today a receipt backs `compute` only through the `slurm` scheduler
  (`coverage.JOB_INDEX`, `src/lean_pkg/coverage.py:517`). An author outside a lab with a cluster can
  write a receipt with `receipt systemd` (alias `receipt local`), which backs no site, or none at all. The
  roadmap's wave 3, "Core apart from the lab" (design note, status line), asks that a package can be
  verified and published with no lab account. [docs/sites.md](../sites.md), "No cluster at all",
  announces the CI receipt as designed and not yet implemented. Issue
  [#318](https://github.com/lean-pkg/lean-pkg/issues/318) and row 9a of
  [the 2026-10 improvement plan](../design/improvement-plan-2026-10.md) track it.
- **A receipt a stranger can check.** A Slurm receipt is checked against `sacct` once, on the author's
  side; nobody outside the cluster can repeat that ([the trust model](../trust-model.md), "A fabricated
  cluster claim"). A GitHub Actions run of a public repository has a public record (the runs API) while it
  is retained, and can be signed into a public transparency log that outlives it.
- **A proposal is required.** The change adds a scheduler, an additive receipt block that `gate`, `receipt
  check`, `coverage check` and `page` then judge, and new refusals: a schema change and a trust-model
  change in the sense of [the proposals README](README.md#when-a-proposal-is-required).
  [Decision 0007](../decisions/0007-recommendations-accepted-2026-10-17.md) settled the design note's
  four section 6 decisions (lines 73 to 78) and records that the CI receipt "is designed there", in a
  proposal, not in the decision record (lines 127 to 130).

## Design

This proposal builds on the `lower-receipt/1` marker and the event-line `v` key of pull request
[#324](https://github.com/lean-pkg/lean-pkg/pull/324) ([on-disk formats](../formats.md)). The `ci` block
is an additive key within `lower-receipt/1`, under the additive-change rule stated there.

### Scheduler and job id

- A new scheduler in `src/lean_pkg/sites.py` (the `Scheduler` dataclass at lines 55 to 61, `SCHEDULERS`
  at line 78): name `github-actions`, label "GitHub Actions", job id `gha-<run_id>-<run_attempt>`
  matching `^gha-\d+-\d+$`, example `gha-123456789-1`. Run ids are unique on github.com; the attempt
  separates re-runs of the same run.
- The `Scheduler` dataclass gains `sentence_level` (the corroboration level at which a site of this
  scheduler renders its sentence): 1 for `slurm`, 2 for `github-actions`. The rule lives on the scheduler,
  in code, not in the profile.
- `github-actions` is built-in only: a package profile under `lean-pkg-sites/` that names
  `scheduler: github-actions` is refused, so no package can write its own wording for the evidence
  sentence (the built-in name itself is already protected, `sites.py:184`).
- A self-hosted runner is not this scheduler. Such a runner is the author's machine, and its receipt is
  worth what `receipt local` is worth; `receipt ci` refuses on one and names `receipt local`.

### The `ci` block

The receipt keeps its nine fields (`commit`, `lean_toolchain`, `mathlib_rev`, `sorry_count`, `axioms`,
`report_sha256`, `job_id`, `checker`, `generated_at`; `RECEIPT_FIELDS`, `src/lean_pkg/cli.py:255`), so
`gate` validates it unchanged, and gains one additive key `ci`, a mapping. Every value comes from a
variable the runner sets, never from the workflow author's input. Required keys and values are strict;
unknown keys inside `ci` are ignored, as for `producer` (open question 7).

| Key | Type | Required | Source | Checked |
|---|---|---|---|---|
| `provider` | string | yes | constant | level 1: equals `github-actions` |
| `server_url` | string | yes | `GITHUB_SERVER_URL` | level 1: equals `https://github.com` (open question 11) |
| `repository` | string `OWNER/REPO` | yes | `GITHUB_REPOSITORY` | level 1: well formed, and the `OWNER/REPO` prefix of `workflow_ref`; level 2: the API path it is fetched from (a renamed repository redirects; identity is the id) |
| `repository_id` | integer > 0 | yes | `GITHUB_REPOSITORY_ID` | level 2: equals the run's `repository.id` and the package's own repository id (open question 6) |
| `run_id` | integer > 0 | yes | `GITHUB_RUN_ID` | level 1: `job_id == gha-<run_id>-<run_attempt>`; level 2: the API run |
| `run_attempt` | integer >= 1 | yes | `GITHUB_RUN_ATTEMPT` | as `run_id`; level 2 reads that attempt, never the latest |
| `job` | string | yes | `GITHUB_JOB` | shown; not load-bearing (the job is identified by its annotation, below) |
| `event` | string | yes | `GITHUB_EVENT_NAME` | level 1: `push` or `workflow_dispatch` (open question 5); level 2: equals the run's `event` |
| `head_sha` | 40 lowercase hex | yes | `GITHUB_SHA` | level 1: equals `commit`; level 2: equals the run's `head_sha` |
| `workflow_ref` | string `OWNER/REPO/PATH@REF` | yes | `GITHUB_WORKFLOW_REF` | level 1: `PATH` under `.github/workflows/`; level 2: `PATH` equals the run's `path` |
| `workflow_sha` | 40 lowercase hex | yes | `GITHUB_WORKFLOW_SHA` | the commit the workflow file is read at |
| `workflow_file_sha256` | 64 lowercase hex | yes | SHA-256 of the workflow file at `workflow_sha` | level 1: rehashed when `workflow_sha` is in the clone, else a shown skip; level 2: rehashed from the API |
| `job_workflow` | string `OWNER/REPO/PATH@REF`, or `null` | yes | the OIDC token's `job_workflow_ref` claim (open question 10) | level 1: well formed when not null; level 2: not null, equals `lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex>`, pinned (below) |
| `runner` | mapping | yes | `RUNNER_ENVIRONMENT`, `RUNNER_OS`, `RUNNER_ARCH`, `ImageOS`, `ImageVersion` | level 1: `environment` equals `github-hosted` (else refused, naming `receipt local`); `os`, `arch` strings; `image`, `image_version` string or null; level 2: the job ran in the hosted runner group |
| `verify_log_sha256` | 64 lowercase hex | yes | SHA-256 of the redacted verify log | level 1: equals `report_sha256`, and the committed log hashes to it |
| `attestation` | string (a file name, no `/`) | no | written by `receipt ci` when the job attests | level 3: the Sigstore bundle committed beside the receipt under `.lean-receipts/` |

`job_workflow` is `null` when the job has no `id-token: write` permission (any workflow but lean-pkg's
own may lack it): level 1 accepts that, level 2 refuses it. The OIDC token is read in the job to take the
one claim and is never written anywhere.

Example (abridged):

```json
{
  "schema": "lower-receipt/1",
  "commit": "4f1c...e2a0",
  "job_id": "gha-11622958301-1",
  "report_sha256": "9b3e...51c4",
  "ci": {
    "provider": "github-actions",
    "server_url": "https://github.com",
    "repository": "alice/demo-lean",
    "repository_id": 812345678,
    "run_id": 11622958301,
    "run_attempt": 1,
    "job": "verify",
    "event": "push",
    "head_sha": "4f1c...e2a0",
    "workflow_ref": "alice/demo-lean/.github/workflows/lean-pkg.yml@refs/heads/main",
    "workflow_sha": "4f1c...e2a0",
    "workflow_file_sha256": "0d7a...93be",
    "job_workflow": "lean-pkg/lean-pkg/.github/workflows/verify.yml@6c2d...71f9",
    "runner": {"environment": "github-hosted", "os": "Linux", "arch": "X64",
               "image": "ubuntu24", "image_version": "20261020.1.0"},
    "verify_log_sha256": "9b3e...51c4",
    "attestation": "4f1c2d3e4a5b-gha-11622958301-1.sigstore.json"
  }
}
```

The files follow the existing naming: `.lean-receipts/<c12>-gha-<R>-<A>.json` and
`<c12>-gha-<R>-<A>-verify.redacted.log` (`cli.receipt_job_slug` keeps the id as it is).

A receipt with `job_id` of the `gha-` spelling and no `ci` block still passes `gate` (it is a lower-tier
receipt like any other) but backs no `compute` job, as a bare job number backs no Slurm job today.

### The three corroboration levels

Each level reports what it proved; the level a receipt reached is computed at check time and never
stored (no committed corroboration record, see Alternatives). Level 3 proves everything level 2 proves
about the run's identity, through the signed certificate instead of the API, so it holds after the run's
records expire; the API checks of level 2 also run under `--attested --corroborate` while the run is
retained.

| Level | How | Checks | Proves | Does not prove |
|---|---|---|---|---|
| 1, offline (the default of `gate`, `coverage check`, `page`, `receipt check`) | no network | the nine fields bind (`validate_receipt`); the committed redacted log hashes to `report_sha256`; `ci` keys and values as in the table; `verify_log_sha256 == report_sha256`; `head_sha == commit`; `job_id == gha-<run_id>-<run_attempt>`; hosted runner; `server_url`; allowed event; the workflow file at `workflow_sha` hashes to `workflow_file_sha256` when that commit is in the clone (else a shown skip) | the receipt is consistent with itself, the commit and the committed log: the same strength as `slurm-<J>` (design note, section 4.6) | that any run happened: every `GITHUB_*` value is text the author could write |
| 2, corroborated (`--corroborate`) | the public REST API, unauthenticated for a public repository, a token optional | `GET /repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}`: `status: completed`, `conclusion: success`, `head_sha == commit`, `path` equals the workflow path, `event` equals `ci.event`, `repository.id == ci.repository_id` and equals the package's repository id; the attempt's jobs: the job whose check run carries the receipt annotation (below) has conclusion success and ran in the hosted runner group; the workflow file at `workflow_sha` hashes to `workflow_file_sha256` and calls `lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex>`, whose content is in the shipped pin list and whose ref equals `job_workflow`; exactly one `lean-pkg-receipt` annotation, whose two hashes equal the committed receipt and log | a real, successful run of lean-pkg's pinned verify workflow, in that repository (by numeric id), on that commit, on a GitHub-hosted runner, which wrote exactly the committed receipt and log bytes | anything once the run is deleted or its records expire; that build code in the same job did not lie (Trust model) |
| 3, attested (`--attested`) | `gh attestation verify`, an optional outside tool, as `sacct` is | the committed bundle verifies: `gh attestation verify FILE --bundle B --repo OWNER/REPO --signer-workflow lean-pkg/lean-pkg/.github/workflows/verify.yml --deny-self-hosted-runners`, or offline with `--custom-trusted-root`; its subjects are exactly the receipt and the redacted log digests; the certificate's repository, source commit and run equal the `ci` block | the same run identity as level 2 plus a signature from GitHub's OIDC token recorded in the public transparency log, which survives log expiry | the mathematics; that build code in the same job did not lie |

Every failure at levels 2 and 3 fails closed and is named: an HTTP 404 (the run deleted or never there),
403 or 429 (rate limit, naming `GH_TOKEN`), a network error, an expired run, a redirect off
`api.github.com`. None is ever reported as a pass, and none lowers a receipt below level 1.

### The pinned reusable workflow and how "pinned" is decided

- lean-pkg publishes `.github/workflows/verify.yml`, a reusable workflow (`on: workflow_call`). It checks
  out `github.sha`, installs the toolchain (elan) and the Mathlib cache, runs the verify script with a
  self-hashing log (the line `receipt slurm` reads without `--stdout`), installs lean-pkg at the exact
  release that ships the file, and runs `lean-pkg receipt ci` last. It uploads the receipt, the redacted
  log and, from S4, the bundle as one artifact. Its permissions are `contents: read`, `id-token: write`
  and, from S4, `attestations: write`; never `contents: write`. CI never pushes to the package's
  repository.
- Every action it uses is pinned to a full 40-hex commit SHA.
- A caller workflow (the author's, printed by `receipt ci --template`) must name it as
  `uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex SHA>`. A tag or branch ref is refused at
  level 2: it can move.
- "Pinned" is decided by content, not by ref: lean-pkg ships `src/lean_pkg/data/verify-workflow-pins.yaml`,
  the SHA-256 of `verify.yml` at every release, and level 2 accepts a ref only when the file at that
  commit hashes to an entry. A commit SHA alone is not enough, because GitHub serves a fork's commits
  through the parent repository's path. A test asserts that the current `verify.yml` hashes to an entry.

### The receipt-bytes binding annotation

The API shows that a run succeeded, not that the committed files are the ones it wrote. `receipt ci`
therefore emits exactly one workflow notice,

```text
::notice title=lean-pkg-receipt::receipt_sha256=<64 hex> report_sha256=<64 hex>
```

and level 2 reads the job's check-run annotations and requires exactly one with that title, both hashes
equal to the committed receipt bytes and the committed redacted log. A second annotation of that title
(build code imitating it) refuses the receipt. `receipt ci` writes deterministic bytes and `--collect`
copies them unchanged, so an edited receipt (a key added after download) no longer corroborates.

### Allowed events and repository identity

- `receipt ci` refuses, and level 1 rejects, every event but `push` and `workflow_dispatch`.
  `pull_request` runs on a merge commit that is not the receipt's commit, and fork pull requests get no
  OIDC token; `pull_request_target` runs the base branch's workflow in a pull request's context with
  write access, a known injection route. Other events (`schedule`, `release`, `workflow_run`, ...) add
  nothing a receipt of one commit needs and are refused until a proposal says otherwise.
- The repository is identified by its numeric id, not its name: the name can be renamed, transferred or
  reused. Level 2 requires the run's `repository.id` to equal both `ci.repository_id` and the package's own
  repository id. `page` resolves the package's repository from the registry's repository URL;
  `receipt check` takes `--github OWNER/REPO` and, without it, uses `ci.repository` and prints that it
  did. A run in a fork has the fork's id and does not back.

### Commands

- **`lean-pkg receipt ci`** (in the job; no registry): `--log PATH --receipt-commit SHA [--checker TEXT]
  [--repo DIR] [--redact REGEX]... [--out PATH] [--json]`, the same writer as `receipt slurm` and
  `receipt systemd` (`receipt.produce`, `src/lean_pkg/receipt.py:1702`, with an `Evidence` whose
  `receipt_extra` carries the `ci` block, line 1521). It refuses, exit 1 and nothing written: outside
  GitHub Actions (`GITHUB_ACTIONS` not `true`); on a self-hosted runner; a missing variable from the
  table; a disallowed event; `--receipt-commit` other than `GITHUB_SHA`; a server other than github.com;
  the workflow file missing at `workflow_sha` (with a shallow-clone hint); every refusal `produce` already
  has (a sorry, a disallowed axiom, a broken log seal, a public-safety hit left after redaction). Built-in
  redactions: `GITHUB_WORKSPACE`, `RUNNER_TEMP`, `RUNNER_TOOL_CACHE` and `/home/runner`, alongside
  `receipt_systemd.redactions` (`src/lean_pkg/receipt_systemd.py:296`). It writes the job's step summary
  and the annotation above.
- **`lean-pkg receipt ci --collect RUN [--attempt N] [--repo DIR]`** (on the author's machine): downloads
  the run's artifact with `gh run download` (the author's own `gh` login), checks it as `gate --check`
  does, and copies it into `.lean-receipts/`. It refuses when `gh` is missing, the artifact is missing,
  the receipt's run id differs from RUN, the receipt is invalid, a destination file exists with other
  bytes, or `.lean-receipts/` is a symlink. It never commits or pushes; the author commits the files as an
  evidence-only descendant.
- **`lean-pkg receipt ci --template`** (no other flag): prints a caller workflow, `on: push` and
  `workflow_dispatch`, with the permissions above and `uses:` pinned to this release's 40-hex SHA.
- **`--corroborate`** on `receipt check`, `page` and the `publish --dry-run` page line: runs level 2.
  `receipt check --corroborate` exits 1 when a `gha-` receipt that backs a `compute` job does not reach
  level 2, and its `--json` gains `ci: {level, rows}`. A token is read from `GH_TOKEN` or `GITHUB_TOKEN`
  when set, sent only in the `Authorization` header to `https://api.github.com`, and never printed,
  logged, written to a file or put in `--json`; a redirect to another host is refused before the token
  could follow it. `--corroborate` downloads no logs or artifacts.
- **`--attested`** on `receipt check` and `page`: runs level 3. Without the flag a missing `gh` is a
  shown skip; with it, a failure.

### The built-in site profile and the sentence rule

- `src/lean_pkg/site_profiles/github-actions.yaml`, beside `easley.yaml`: title "GitHub Actions", the
  Actions documentation URL, and an acknowledgement that states evidence, as decision 3 of the design
  note says: "Verification ran on [GitHub Actions](https://docs.github.com/en/actions) hosted runners
  ({runs})." (the URL is settled in S1).
- `{runs}` is filled with links to the corroborated runs (`run R attempt A`). The token is allowed only in a
  built-in profile whose scheduler has `sentence_level > 1`; anywhere else, a package profile included,
  it is refused naming the key (open question 2).
- `mention_pattern` is narrow, `Verification\s+ran\s+on\s+\[?GitHub\s+Actions`: never bare "GitHub
  Actions", so a README with a CI badge or "tests run on GitHub Actions" is not read as a claim.
- The sentence renders only when the site is backed **and** the level reached is at least the scheduler's
  `sentence_level`: for GitHub Actions, only under `page --corroborate` (level 2) or `--attested`
  (level 3). At level 1 the `compute` job is backed (coverage check passes) but the page carries no
  sentence. A README that carries the sentence fails offline `page --check` and the `publish --dry-run`
  page line, with a hint to run `--corroborate` (open question 8); CI passes `--corroborate` with its
  `GITHUB_TOKEN`.
- `page --json` gains, per site under `sites`, `level` (the lowest level reached over its jobs) and
  `levels` (`{job: level}`); a Slurm site reports 1. No top-level `github-actions` key is added: the
  legacy key at `src/lean_pkg/page.py:839` is emitted for every built-in profile today and is narrowed to
  `easley` only.

### Backing `compute`

`compute: [{cluster: github-actions, jobs: [gha-<run>-<attempt>]}]`, each job the `job_id` of a receipt
of the `github-actions` scheduler committed at HEAD with a valid `ci` block (level 1). The scheduler's
reader is a second entry in `coverage.JOB_INDEX` (`coverage.py:517`); the near-miss hint for a listed job
without a receipt, Slurm-only today (`coverage.py:535` to 544), becomes per scheduler (for GitHub
Actions: a `gha-` receipt without a `ci` block, or one whose block fails, is named with the reason; a
`slurm-` id under `github-actions` and the reverse are refused).

### The comparator tier in CI

`comparator-receipt` recognizes a third context in `host_policy_ok` (`src/lean_pkg/comparator_receipt.py:452`):
`GITHUB_ACTIONS=true` and `RUNNER_ENVIRONMENT=github-hosted`. It records
`host_policy: "GitHub Actions run R attempt A"`, `job_id: gha-R-A` (instead of `SLURM_JOB_ID` or
`local-...`, line 2193) and the same `ci` block. A comparator receipt backs a `github-actions` job only
with a valid `ci` block: the CI counterpart of `slurm_job_of`'s sealed-sacct rule
(`coverage.py:450`). `GITHUB_ACTIONS` without `RUNNER_ENVIRONMENT`, or on a self-hosted runner, is not
recognized. The judge needs an unprivileged user namespace for `bwrap`, which current Ubuntu runner images
restrict through AppArmor: `verify.yml`'s comparator job runs `lean-pkg comparator host-check --routes`
first and lifts the restriction with `sudo sysctl` (hosted runners allow it), or fails with the route
named.

### Implementation plan

Stacked pull requests, each green on its own, each with its upgrade-record entry
(`docs/upgrades/<next>.yaml`) and design-note status line; the last sets this proposal `implemented`
and plan row 9a done.

| Stage | Content | Upgrade record |
|---|---|---|
| S0 | this proposal and its README row | `no_author_change` |
| S1 | the scheduler and `sentence_level`, the built-in profile, level 1 in `validate_receipt` (beside the `slurm_sacct` block, `cli.py:2859`) and `receipt check`, the `JOB_INDEX` reader and per-scheduler near-misses, `page --json` `level`/`levels`, the legacy-key narrowing, the `ci` block in `docs/formats.md` and `LLMS_TEXT` | `kind: format`, not breaking |
| S2 | `receipt ci`, `--collect`, `--template`, `verify.yml`, the built-in redactions | `kind: command` |
| S3 | `--corroborate` (`receipt check`, `page`, `publish --dry-run`), the pin list, the annotation, the sentence at level 2 and above | `kind: command` |
| S4 | the attestation step, the committed bundle, `--attested`, level 3 | `kind: command` |
| S5 | the comparator tier in CI | `kind: format`, not breaking |

## Compatibility and migration

- **Additive.** No key is removed or renamed and no format version changes: `ci` is an additive key of
  `lower-receipt/1` (and of `comparator-receipt/2` in S5). Every existing receipt validates exactly as
  before; a Slurm or systemd receipt never has a `ci` block and is not read by the new scheduler.
- **An older lean-pkg reading a receipt with a `ci` block** (checked against 0.21.0, the base of this
  draft): `validate_receipt` (`cli.py:2814`) requires the nine fields, checks `job_id` only through
  `receipt_job_slug` (`gha-R-A` is a valid slug) and ignores every other key, so `gate`, `gate --check`
  and `receipt check` accept the receipt as an ordinary lower-tier receipt and skip every `ci` check
  (`receipt_check.tier_of`, `src/lean_pkg/receipt_check.py:165`, classifies by `sorry_count` and
  `axioms`). A `coverage.yaml` naming `cluster: github-actions` is refused there, because the profile does
  not exist ("compute[0].cluster: must be one of easley", as `tests/test_sites.py:106` pins for another
  unknown name): an older lean-pkg fails closed on the claim and never renders the sentence.
- **Narrowed acceptance.** After S1, a package profile `lean-pkg-sites/github-actions.yaml` is refused
  (a built-in name), a package profile with `scheduler: github-actions` is refused, and a profile whose
  acknowledgement contains the literal text `{runs}` is refused. Whether any package commits such a
  profile today is unknown; none in the public index does.
- **`page --json`** gains `sites.<name>.level` and `levels`. The top-level legacy key stays `easley` only,
  and is dropped at the 1.0 freeze as decided.
- **No migration command**: no on-disk format changes version. Nothing to do for an author who keeps
  using Slurm or `receipt local`.
- **Goldens** (`tests/golden`): new `lean-pkg.receipt.ci.txt`; changed `lean-pkg.receipt.txt` (the
  subcommand list), `receipt check` and `page` (the new flags).
- **Older lean-pkg in CI.** `verify.yml` installs the exact lean-pkg release that ships it, so a pinned
  caller keeps writing the receipts that release writes. A newer lean-pkg judges them; a receipt written
  by a release before a breaking change shows in producer skew (`receipt ci` joins `EVIDENCE_AREAS`,
  `src/lean_pkg/producer_skew.py:51`).

## Trust model

Yes: this adds a scheduler that backs `compute`, a sentence on the page, and refusals in `gate`,
`coverage check`, `page` and `receipt check`.

- **Forgery at level 1 equals Slurm.** The `ci` block and the log are files the author writes; level 1
  checks consistency only. What a dishonest author can get accepted at level 1 (a `compute` job backed, no
  sentence) is exactly what they can get with a hand-written `slurm-<J>` receipt today; nothing more.
- **Levels 2 and 3 need a real run.** To pass level 2 the author must have run lean-pkg's pinned
  `verify.yml` successfully in that repository (by id), on that commit, on a hosted runner, and committed
  the bytes it wrote. A fabricated log beside a real run is closed by the annotation (level 2) or by the
  signed subjects (level 3).
- **Fork pull requests.** Refused by event (`pull_request`, `pull_request_target`) at level 1, and at
  level 2 by repository id: a fork's run has the fork's id.
- **Re-run attempts.** A receipt names one attempt. Level 2 reads `/attempts/{run_attempt}`, not the
  run's latest state: `gha-R-1` refuses when attempt 1 failed even though attempt 2 passed, and `gha-R-2`
  passes. Fixtures of both shapes pin it.
- **Self-hosted runners.** Refused by `receipt ci`, by level 1 (`runner.environment`), at level 2 by the
  job's runner group, and at level 3 by `--deny-self-hosted-runners`.
- **Token scope.** Writing a receipt needs no token. `verify.yml` holds `contents: read`, `id-token:
  write` and `attestations: write`, never `contents: write`. `--collect` uses the author's `gh` login.
  `--corroborate` sends `GH_TOKEN` or `GITHUB_TOKEN` only to `api.github.com` and never prints or writes
  it; a test runs every new command with a sentinel token and greps stdout, stderr, `--json` and the
  written files for it.
- **Run retention decays level 2.** Logs, artifacts and annotations expire (90 days by default) and an
  admin may delete a run. After that, level 2 fails with the reason named and the page drops the sentence
  on the next `--corroborate` render; the page never claims level 2 from memory. Level 3 is the durable
  route: the transparency-log entry and the committed bundle outlive the run.
- **Build code in the same job.** `lakefile` and build code run arbitrary code before the check, as on a
  cluster; CI does not sandbox the build from the checker. It could emit a fake annotation (refused when
  there are two) or alter files before `receipt ci` reads them (not prevented). The comparator's
  sandboxed judge and the kernel replay remain the defence for the mathematics.
- **Whose infrastructure is trusted.** GitHub (runner, API, OIDC) and, at level 3, Sigstore.

Tests that pin every refusal, by stage:

- S1 (`tests/test_ci_receipt_level1.py`; changes to `tests/test_sites.py`, whose lines 37, 38 and 51 pin
  the built-in list to `["easley"]`, and the page site tests): the built-in list becomes `easley`,
  `github-actions`; `mention_pattern` matches the acknowledgement and not a CI badge or "tests run on GitHub
  Actions"; package profiles naming the built-in or the scheduler refused; `{runs}` outside a level-2
  scheduler refused; one parametrized refusal per `ci` rule in the table (not a mapping, a key missing,
  provider, server, run id or attempt not a positive integer, job id mismatch, `head_sha` mismatch,
  self-hosted naming `receipt local`, `verify_log_sha256` mismatch, each disallowed event, malformed
  `workflow_ref` or repository mismatch, `workflow_file_sha256` malformed or mismatched with a skip row
  when the commit is absent, `repository_id` not an integer); unknown keys tolerated; `gate` and
  `gate --check` refuse each; `gha-1-1` without `ci` gates but backs nothing; coverage cases (no receipt,
  no `ci`, failing `ci` named, cross-scheduler ids, valid); `page --json` shape with no top-level key and
  no sentence at level 1; a README with the sentence fails `page --check` and `publish --dry-run`; the
  `receipt check` `ci` row; the test that no code path names a site still holds.
- S2 (`tests/test_receipt_ci.py`, environment from fixture dictionaries): each `receipt ci` refusal with
  nothing written; round trip through `gate --check` and `receipt check`; byte-identical repeat;
  `--collect` with a stubbed `gh` (each refusal above; never commits or pushes); `verify.yml` parses, every
  `uses:` pinned to a full SHA, no `contents: write`; goldens; command examples.
- S3 (`tests/test_ci_corroborate.py`, recorded and scrubbed API fixtures through an injected fetch; no
  network): pass at level 2; refusals for a run not completed, failed or cancelled, `head_sha`, `path`,
  repository id (fork), job missing or failed, self-hosted group, workflow hash, a caller without
  `verify.yml@<40-hex>` or with a tag, content not in the pin list, annotation missing, duplicated or
  mismatched, attempt 1 failed against attempt 2 green, 404, 403 and 429 naming `GH_TOKEN`, a network
  error, a redirect off `api.github.com`; the token sentinel; `page --corroborate` renders the run links;
  the pin-list test.
- S4 (`gh attestation` stubbed): pass; bundle not committed; subject digest mismatch; tool absent (skip
  without `--attested`, failure with it); wrong signer workflow; wrong repository, commit or run in the
  certificate; the verifier exits non-zero.
- S5: `host_policy_ok` for hosted, self-hosted, and `GITHUB_ACTIONS` without `RUNNER_ENVIRONMENT`; a
  `gha-` comparator receipt backs only with a valid `ci` block; `slurm_job_of` unchanged; `verify.yml`'s
  comparator job runs `host-check --routes`.

## Alternatives

- **Do nothing.** Authors without a cluster keep `receipt local`, which backs no site and which nobody
  else can corroborate.
- **Committed corroboration records** (store the API's answer beside the receipt, so the level survives
  run expiry). Rejected: an API copy committed by the author is author-written text, no stronger than the
  `ci` block itself. The level is always computed at check time. The attestation bundle is the exception
  because it is signed.
- **Sigstore as a Python dependency** (verify bundles in process). Rejected: lean-pkg's only runtime
  dependency is `pyyaml>=6` (`pyproject.toml:14`), and a cryptographic stack is a large, security-relevant
  addition. `gh attestation verify` is used as an optional outside tool, as `sacct` is.
- **Tags instead of SHAs** for the reusable workflow. Rejected: a tag can be moved. A 40-hex SHA plus the
  content pin list fixes what ran, including against a fork commit served through the parent's path.
- **Allowing `pull_request` events.** Rejected: the run is on a merge commit, not the receipt's commit,
  and fork pull requests get no OIDC token; `pull_request_target` runs the base's workflow with the
  head's code.
- **GitHub Enterprise Server in v1.** Rejected for now: different API hosts, token issuers and
  transparency setups to trust. `server_url` must be `https://github.com`; a later proposal may add
  others.

## Open questions

The maintainer agreed the recommended answer to each question in the design discussion of 2026-10-26
(issue #318); the proposal itself still needs acceptance.

1. Write this proposal before any code? Recommendation: yes; decision 0007 (lines 127 to 130) requires
   it, and proposal 0005 is the precedent. (maintainer: take the recommendation, 2026-10-26)
2. How does the verbatim acknowledgement of `site-profile/1` name the run? Recommendation: a `{runs}`
   token, allowed only where the scheduler's `sentence_level > 1` (built-in only), filled with run links.
   Alternative: a static sentence and a separate evidence line. (maintainer: take the recommendation,
   2026-10-26)
3. Bind the committed bytes at level 2? Recommendation: yes, exactly one `lean-pkg-receipt` notice
   annotation with `receipt_sha256` and `report_sha256`, required to match. (maintainer: take the
   recommendation, 2026-10-26)
4. What makes `verify.yml` "pinned"? Recommendation: its content SHA-256 is in the list shipped with
   lean-pkg, and the caller names it by a 40-hex commit SHA. (maintainer: take the recommendation,
   2026-10-26)
5. Allowed events? Recommendation: `push` and `workflow_dispatch` only. (maintainer: take the
   recommendation, 2026-10-26)
6. Repository identity? Recommendation: level 2 requires the package's own repository id (from the
   registry's repository URL in `page`; `--github OWNER/REPO` in `receipt check`, the receipt's own
   repository printed when absent); fork runs do not back. (maintainer: take the recommendation,
   2026-10-26)
7. How strict is the `ci` block? Recommendation: required keys and values strict, unknown keys ignored,
   like `producer`. (maintainer: take the recommendation, 2026-10-26)
8. Offline `page --check` on a README carrying the GitHub Actions sentence? Recommendation: fail closed
   with a `--corroborate` hint; CI passes `--corroborate` with its token. Alternative: a WARN, which would
   stay green after the run expires. (maintainer: take the recommendation, 2026-10-26)
9. Level 3 tooling? Recommendation: `gh attestation verify` as an optional outside tool; no Sigstore
   dependency. (maintainer: take the recommendation, 2026-10-26)
10. Where does `job_workflow` come from? Recommendation: the OIDC token's `job_workflow_ref` claim, read in
    the job (`id-token: write`), the token never written; S2 checks whether a context expression gives the
    same value without the token. (maintainer: take the recommendation, 2026-10-26)
11. GitHub Enterprise Server? Recommendation: refused in v1; `server_url` must be `https://github.com`.
    (maintainer: take the recommendation, 2026-10-26)

## AI assistance

Drafted by Claude (Anthropic), the lean-pkg operations agent, from section 4 of the design note, a
read-only implementation plan written by a Claude planning subagent, and the maintainer's settled summary
on issue #318; the code anchors were checked against the base commit. To be reviewed and decided by the
maintainer.
