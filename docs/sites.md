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
| `github-actions` | `gha-<run_id>-<run_attempt>` | a lower-tier receipt with that `job_id` and a `ci` block that passes level 1 ([The `ci` block](formats.md#the-ci-block)) | level 2 (a corroborated run) |

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
corroborated against GitHub's public record (level 2, `--corroborate`, a
later stage of the proposal, not in this release). Until then a README
that carries that sentence fails `page --check` and the page line of
`publish --dry-run`. The profile's `mention_pattern` is narrow on purpose
(`Verification ran on [GitHub Actions`): a CI badge or "tests run on GitHub
Actions" is not a claim and is never refused.

Writing the receipt in the job (`lean-pkg receipt ci`) and lean-pkg's
reusable `verify.yml` are the next stages of the proposal; this release
judges a `ci` block, it does not write one.

## No cluster at all

Nothing above is required. A page without `compute` carries only the
lean-pkg credit. The lower-tier receipt can come from any executor
(`lean-pkg receipt systemd --run` runs the verify script in a capped local unit; see
[Receipts and trust](receipts.md)). A receipt from a GitHub Actions run is
judged at level 1 ([GitHub Actions](#github-actions)); writing one in the
job, and corroborating it against the run, are the next stages of
[proposal 0008](proposals/0008-github-actions-compute-receipt.md).
