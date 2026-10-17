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
ran in Slurm job `<J>`. With the declaration backed, `lean-pkg page` puts the
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
| `scheduler` | `slurm` (the only one today) |
| `x-NAME` | optional: your own data (any value) under an `x-` key; lean-pkg ignores it |

The file must be committed: lean-pkg reads it at `HEAD`, so the page renders
the same on your machine, in CI and for a referee. A profile may not reuse a
built-in name, and a malformed one refuses `coverage check` and `page`,
naming the file and the key. `lean-pkg page NAME --json` reports every
profile the package can name under `sites: {NAME: {backed, jobs}}` (the
older top-level `easley` key is kept).

## No cluster at all

Nothing above is required. A page without `compute` carries only the
lean-pkg credit. The lower-tier receipt can come from any executor
(`lean-pkg receipt local` runs the verify script in a capped local unit; see
[Receipts and trust](receipts.md)). A receipt from a CI runner is designed
in [the design note](design/sites-and-ci-receipts.md#4-design-a-compute-receipt-from-a-ci-runner-github-actions-first)
and not yet implemented.
