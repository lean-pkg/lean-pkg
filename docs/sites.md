---
title: Compute sites
---

[&larr; lean-pkg documentation](index.md)

*Credit the place a job ran only when a committed receipt shows the job ran.*

A verification page may thank the cluster that ran its gates. lean-pkg
renders that sentence from a *site profile*: a small YAML file naming the
site, its official acknowledgment and the job system whose receipts back it.
The [Auburn University Easley Cluster](https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html)
is the first built-in profile. Any other site is one more file, with no
change to lean-pkg. The format contract and its rationale are in
[the design note](design/sites-and-ci-receipts.md).

## Declare where the gates ran

In `coverage.yaml` (see [The verification page](page.md#coverageyaml)):

```yaml
compute:
  - cluster: easley              # a site profile name
    jobs: [slurm-123456]         # each the job_id of a receipt committed under .lean-receipts/
```

Each listed job must be the `job_id` of a receipt of the site's scheduler,
committed at `HEAD` under `.lean-receipts/`. For Slurm that is `slurm-<J>`,
which `lean-pkg receipt slurm` writes only after sacct shows the job
COMPLETED with exit 0:0, or a comparator receipt whose `host_policy` says it
ran in Slurm job `<J>` and whose claim is sealed with that job's sacct record
([`lean-pkg receipt comparator --seal-sacct`](comparator.md#seal-the-slurm-claim-with-sacct)).
With the declaration backed, `lean-pkg page` puts the
site's sentence, verbatim, under Acknowledgments, in profile order (built-in
first, then the package's own, each by name).

## The rule

- No declaration, or a listed job without a committed receipt: no sentence.
  A listed job without a receipt refuses the page and names the job.
- A README that mentions a site (its `mention_pattern`, anywhere, inside the
  page markers or not) without that backing fails `lean-pkg page NAME --check`
  and the page line of `lean-pkg publish NAME --dry-run`.
- A receipt does not record where its job ran (a Slurm job number does not
  name a cluster), so the declaration names the site and the receipt shows a
  job of that scheduler ran. When your jobs ran on a site, declare them: that
  is what puts the site's required acknowledgment on the page.
- A scheduler also says from which corroboration level its sentence is
  rendered. Slurm's is level 1, what an offline check reaches: a backed
  Easley job puts the Easley sentence on the page. GitHub Actions' is level
  2 (below).

| Scheduler | Job id | Backed by | Sentence from |
|---|---|---|---|
| `slurm` | `slurm-<J>` | a lower-tier receipt with that `job_id` (`receipt slurm`), or a comparator receipt with a sacct seal | level 1 (offline) |
| `github-actions` | `gha-<run_id>-<run_attempt>` | a lower-tier receipt with that `job_id` and a `ci` block that passes level 1 ([The `ci` block](formats.md#the-ci-block)), or a comparator receipt written on a GitHub-hosted runner with one ([comparator tier in CI](comparator.md#on-a-github-hosted-runner)) | level 2 (a corroborated run) |

## Add a site of your own

Commit a profile in the package directory as `lean-pkg-sites/<name>.yaml`:

```yaml
schema: site-profile/1
name: mycluster
title: Example University Research Cluster
url: https://example.edu/research-computing
acknowledgement: "Computations used the [Example University Research Cluster](https://example.edu/research-computing)."
mention: the Example University Research Cluster
mention_pattern: 'Research\s+Cluster'
scheduler: slurm
```

| Key | Rule |
|---|---|
| `schema` | `site-profile/1` |
| `name` | `[a-z][a-z0-9-]*`, at most 40 characters, equal to the file name; the `cluster` value in `compute` |
| `title`, `url` | the linked name (one line, at most 120 characters) and an `https://` URL |
| `acknowledgement` | the sentence, verbatim, as your site requires it: one line, at most 400 characters, containing the Markdown link from `title` to `url`; no `<`, `>`, backtick or pipe character |
| `mention` | how a refusal names the site |
| `mention_pattern` | a case-insensitive regular expression for README text that claims the site; it must match the acknowledgement |
| `scheduler` | `slurm` (`github-actions` is built-in only: a package profile naming it is refused) |
| `x-NAME` | optional: your own data (any value) under an `x-` key; lean-pkg ignores it |

A site that wants to pin the container image its jobs run in can keep the
expected digests under an `x-` key today (for example
`x-container-image-sha256: [<hex>, ...]`); lean-pkg does not read it yet.
Receipts record the image themselves (the `container` key, see
[Receipts and trust](receipts.md#a-lower-tier-receipt-from-a-slurm-job)); a
`gate` / `receipt check` WARN when a receipt's digest is not one its site
names is a follow-up: a receipt carries no site identity, so the check has to
go through the `coverage.yaml` `compute` job ids.

The file must be committed: lean-pkg reads it at `HEAD`, so the page renders
the same on your machine, in CI and for a referee. A profile may not reuse a
built-in name, and a malformed one refuses `coverage check` and `page`,
naming the file and the key. `lean-pkg page NAME --json` reports every
profile the package can name under `sites: {NAME: {backed, jobs}}` (the
older top-level `easley` key is kept, in that shape, for Easley only). Each
site there also has `level`, the lowest corroboration level its listed jobs
reached (null when none is listed), `levels` (`{job: level}`) and
`sentence` (whether the page carries its acknowledgement).

## GitHub Actions

`github-actions` is a built-in profile and a built-in-only scheduler
([proposal 0008](proposals/0008-github-actions-compute-receipt.md)). A
GitHub Actions run of the package's public repository can back `compute`:

```yaml
compute:
  - cluster: github-actions
    jobs: [gha-11622958301-1]     # gha-<run_id>-<run_attempt>
```

The job must be the `job_id` of a lower-tier receipt committed at `HEAD`
whose additive [`ci` block](formats.md#the-ci-block) names the run and
passes level 1: consistent with itself, the commit and the committed log,
a GitHub-hosted runner, a `push` or `workflow_dispatch` event. A `gha-`
receipt without the block, or with a failing one, backs nothing, and the
refusal names it with the reason. A `slurm-` id under `github-actions`, and
a `gha-` id under a Slurm site, are refused.

**Level 1 backs the job, but puts no sentence on the page.** Level 1 is as
strong as a `slurm-<J>` receipt and as forgeable: every value in the block
is text the author could write. The GitHub Actions acknowledgement ("Verification
ran on [GitHub Actions](https://docs.github.com/en/actions) hosted runners
(...)") states that the runs happened, so it renders only from a run
corroborated against GitHub's public record (level 2, below). Without
`--corroborate` a README that carries that sentence fails `page --check`
and the page line of `publish --dry-run`, with a hint to add the flag. The
profile's `mention_pattern` is narrow on purpose
(`Verification ran on [GitHub Actions`): a CI badge or "tests run on GitHub
Actions" is not a claim and is never refused.

**Level 2 renders the sentence.** `lean-pkg page NAME --corroborate` (and
`page --check --corroborate`, `publish NAME --dry-run --corroborate`) asks
the public GitHub REST API about every listed `gha-` job: the attempt the
receipt names completed with success on the receipt's commit, in the
package's repository (by numeric id, from the registry's repository URL),
on a GitHub-hosted runner, running lean-pkg's pinned `verify.yml`, and it
wrote exactly the committed receipt and log bytes
([every check](receipts.md#corroborating-the-run-level-2---corroborate)).
When every listed job reaches level 2 the Acknowledgments carry

> Verification ran on [GitHub Actions](https://docs.github.com/en/actions) hosted runners ([run 11622958301 attempt 1](https://github.com/OWNER/REPO/actions/runs/11622958301/attempts/1)).

with `{runs}` filled by the runs' links; `page --json` reports `level: 2`
and, per job, the `corroboration` rows and the `runs`. A failure (a 404
once the run's records expire, a rate limit, a network error) is a warning
naming why, and the sentence is left out: the page never claims level 2 from
memory, so render it in CI with `--corroborate` and `GITHUB_TOKEN` set
([The verification page in CI](page.md#in-ci)). Written with
`--corroborate`, the README block is stale to an offline `page --check`;
check it with the flag too.

### Writing the receipt in CI

`lean-pkg receipt ci` writes the receipt inside the job, and lean-pkg's
reusable workflow `.github/workflows/verify.yml` runs it for you. Print the
caller workflow, commit it and push:

```bash
lean-pkg receipt ci --template > .github/workflows/lean-pkg.yml
```

The caller names `lean-pkg/lean-pkg/.github/workflows/verify.yml@<40-hex>`,
a full commit: the template fills in the commit of the installed lean-pkg
when pip recorded one (a `pipx install git+...@vX.Y.Z`); otherwise it
prints a placeholder to replace. It runs on `push` and `workflow_dispatch`
only, with `contents: read` and `id-token: write`, never
`contents: write`: CI never pushes to your repository. The job checks out the pushed
commit, installs elan (a pinned release), the Mathlib cache and lean-pkg at
the commit of `verify.yml` itself, runs your verify command (input
`verify-command`, default `bash verify.sh`, the script `lean-pkg new`
writes) with the log path as its last argument, seals the log in-log, runs
`lean-pkg receipt ci` and uploads the receipt and its redacted log as the
artifact `lean-pkg-receipt-<run_id>-<run_attempt>`. After a green run, on
your machine:

```bash
lean-pkg receipt ci --collect 11622958301   # gh run download, the gate's checks, copy into .lean-receipts/
```

`--collect` uses your own `gh` login, checks the receipt as `gate --check`
does against your checkout, and never commits or pushes: commit the two
files under `.lean-receipts/` yourself (an evidence-only descendant), then
name the job under `compute` as above. A re-run writes `gha-<run>-2`; pass
`--attempt N` when a run has more than one receipt artifact. Every refusal
and the built-in redactions are in [Receipts and
trust](receipts.md#a-lower-tier-receipt-from-github-actions).

## No cluster at all

Nothing above is required. A page without `compute` carries only the
lean-pkg credit. The lower-tier receipt can come from any executor
(`lean-pkg receipt systemd --run` runs the verify script in a capped local unit; see
[Receipts and trust](receipts.md)). A receipt from a GitHub Actions run is
judged at level 1, written in the job by `lean-pkg receipt ci`
([Writing the receipt in CI](#writing-the-receipt-in-ci)) and corroborated
against the run with `--corroborate` ([GitHub Actions](#github-actions)).
