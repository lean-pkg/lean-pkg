---
title: "Design: compute sites and CI-runner receipts"
---

[&larr; lean-pkg documentation](../index.md)

*Status: section 1 (the site profile format) is implemented and stable; section 4 (CI-runner receipts) is
being implemented in stages under [proposal 0008](../proposals/0008-github-actions-compute-receipt.md): stage
S1 (the `github-actions` scheduler and profile, the `ci` block judged at level 1, `page --json` levels) and
S2 (`lean-pkg receipt ci`, its `--collect` and `--template`, the reusable `verify.yml`) and S3 (`--corroborate`,
level 2 against the public run API, the `verify.yml` pin list, the sentence at level 2) are in; attestation (S4) and
the comparator tier (S5) are not yet. Roadmap wave 3, "Core apart from the lab": a package can be verified and
published with no lab account. The four decisions of section 6 were taken on 2026-10-17, as recommended
([decision 0007](../decisions/0007-recommendations-accepted-2026-10-17.md)).*

The user guide is [Compute sites](../sites.md). This page records the format contract, why it has the
shape it has, the audit of lab couplings it came from, and the next step: a compute receipt from a CI
runner (GitHub Actions first).

## 1. The site profile format (`site-profile/1`, stable)

A *site* is a place verification jobs run: an HPC cluster, later a CI service. Everything lean-pkg says
about one site is a small YAML file. No code path is special to one site.

```yaml
schema: site-profile/1
name: easley                 # the coverage.yaml compute[].cluster value
title: Auburn University Easley Cluster
url: https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html
acknowledgement: "This work was completed in part with resources provided by the [Auburn University Easley Cluster](https://hpc.auburn.edu/hpc/docs/hpcdocs/build/html/easley/intro.html)."
mention: the Easley Cluster
mention_pattern: 'Easley\s+Cluster'
scheduler: slurm
```

| Key | Rule |
|---|---|
| `schema` | `site-profile/1` |
| `name` | `[a-z][a-z0-9-]*`, at most 40 characters; equals the file name without `.yaml`; the value of `cluster` in `coverage.yaml` `compute[]`; stable once a page uses it |
| `title` | one line, at most 120 characters: the linked name |
| `url` | an `https://` URL |
| `acknowledgement` | one line, at most 400 characters, the sentence the page renders verbatim under Acknowledgments; must contain the Markdown link from `title` to `url`; may not contain `<`, `>`, a backtick or a pipe character |
| `mention` | one line, at most 80 characters: how a refusal names the site |
| `mention_pattern` | a regular expression (Python syntax, matched case-insensitively, at most 200 characters) for README text that claims the site; it must match `acknowledgement`, so a page carrying the sentence passes its own check |
| `scheduler` | the job system whose receipts back the site; a closed set in code: `slurm` today |
| `x-NAME` | optional extension keys (`x-` then `[a-z][a-z0-9-]*`), any value; tolerated and never read |

Strict: every key above but `x-NAME` is required, other unknown and repeated keys are refused, every
required value is one line of text. An operator keeps its own data in the same file under an extension
key (a lab's partitions, container and thread defaults, say: section 6), and lean-pkg ignores it.

**Where profiles come from**, in this order (which is also the order of the Acknowledgments sentences):

1. *Built in*: `src/lean_pkg/site_profiles/<name>.yaml`, shipped in the wheel. Easley is the first.
2. *The package's own*: `lean-pkg-sites/<name>.yaml` in the package directory, **committed at HEAD**
   (read with `git show`, like `coverage.yaml`). A package profile may not reuse a built-in name, so no
   package can alter a site's official wording. A malformed package profile refuses `coverage check`,
   `page` and the `publish --dry-run` page line, naming the file and the key.

There is deliberately no user-level or environment-variable profile. A page is rendered by the author, by
CI (`page --check --repo`) and by a referee re-checking it; a profile known only to one machine would make
the same commit render differently in each place.

**Schedulers are code, not data.** A scheduler is how a committed receipt shows that a job ran: its
`job_id` spelling and the rule that corroborates it. Slurm: `job_id: slurm-<J>` (written by `receipt
slurm` only after sacct shows the job COMPLETED, exit 0:0), or a comparator receipt whose `host_policy`
says it ran in Slurm job `J` and whose claim `comparator-receipt --seal-sacct` sealed with sacct. Adding a scheduler (section 4) is a code change with its own tests; adding a
site that uses an existing scheduler is a data change only.

## 2. The rule a profile carries (the credit rule)

Unchanged from the Easley-only code it replaced, now applied to every profile alike:

- A site's sentence appears on the page only when `coverage.yaml` declares the site
  (`compute: [{cluster: NAME, jobs: [...]}]`) **and** every listed job is the job of a receipt of the
  site's scheduler committed at HEAD under `.lean-receipts/`. A listed job without such a receipt refuses
  the page.
- A README that matches the site's `mention_pattern` anywhere (inside the page markers or not) without
  that backing fails `page --check`, and fails the `publish --dry-run` page line even for a package that
  has not adopted the page.
- Receipts carry no site identity (a Slurm job number does not name a cluster), so the declaration names
  the site and the receipt shows that a job of that scheduler ran.

The obligation "when receipts show the site ran, its acknowledgement is required" is met the same way it
was for Easley: declaring the jobs is what puts the sentence on the page, and the lab's own conventions
(outside this repository) require the declaration for jobs that ran there. lean-pkg cannot infer the site
from a receipt, so it cannot force a declaration; it can and does refuse a claim that is not backed.

`--json` of `page` gains `sites: {NAME: {backed, jobs}}` for every profile the package can name. The
top-level `easley: {backed, jobs}` key is kept unchanged for existing callers (one legacy key per built-in
profile; built-in names are chosen not to collide with other payload keys).

## 3. Audit of lab couplings (2026-10-17, at 0.9.0)

Classes: **(a)** generic already; **(b)** a configurable default that belongs in a site profile or a
scheduler; **(c)** hard-coded, must change.

| Coupling | Where | Class | Action |
|---|---|---|---|
| Easley sentence, link and README pattern as constants | `page.py` (`EASLEY`, `EASLEY_RE`) | (c) | **Done here**: data of the built-in `easley` profile; the constants remain as aliases of the first built-in profile |
| `if backed.get("easley")`, `"Easley" in problem` branches | `page.py` render, `readme_problems`, `publish_item` | (c) | **Done here**: a loop over profiles |
| `CLUSTERS = ("easley",)` closed enum | `coverage.py` | (b) | **Done here**: the built-in profiles plus the package's own |
| Slurm job id spelling and the sacct-checked receipt | `coverage.py` `SLURM_JOB_RE`, `slurm_job_of`; `receipt.py` | (b) | **Done here**: the `slurm` scheduler; its reader stays code (`coverage.JOB_INDEX`) |
| JSON key `easley` in `page --json` | `page.py` facts | (c), kept | `sites` added; `easley` is dropped at the 1.0 freeze (decided, section 6) |
| Comparator host policy: a Slurm job, or a host in `COMPARATOR_RECEIPT_ALLOWED_HOSTS`, or `COMPARATOR_RECEIPT_ALLOW_HOST=1` | `comparator_receipt.py` `host_policy_ok` | (b) | Generic enough to run anywhere today; a CI scheduler should add a recognized context (section 4.5) instead of the override; a lab's allowed hosts can later come from site files (section 5) |
| Comparator `job_id` = `SLURM_JOB_ID`, else `local-<host>-<pid>-<stamp>` | `comparator_receipt.py` | (b) | Ask the scheduler for the job identity (section 4.5) |
| Slurm template (`receipt slurm --template`) | `receipt.py` `SBATCH_TEMPLATE` | (a) | No partition, account or site names; "add the lines your site requires" |
| Local executor (`receipt systemd`, alias `receipt local`) | `receipt_systemd.py` | (a) | Already a non-cluster route for the lower tier |
| Registry location | `core.py` `registry_path`: `--registry`, `LEAN_PKG_REGISTRY`, `~/.lean-pkg/registry` (`LEAN_PKG_HOME`), `./lean-packages.yaml` | (a) | Nothing lab-specific; the lab sets the variables in its own wrapper |
| Committed-mode and push (`LEAN_PKG_COMMIT`, `LEAN_PKG_PUSH`) | `cli.py` | (a) | Off by default |
| Notification | `core.py` `LEAN_PKG_NOTIFY_CMD` | (a) | An optional hook; no messaging system is built in |
| Lease holder help text gives a lab-style session address as its example | `cli.py` `--holder` help, `docs/cli.md` | (a) | Example text only; a neutral example is a wave-2 docs item |
| Private-string boundary | `scripts/private-boundary-check.sh`, `.lean-pkg-private-patterns` | (a) | Lab names are forbidden in the tree, never built in |
| Easley paragraphs in the guides | `docs/page.md` (the Easley acknowledgment), `docs/paper-companion.md` (Acknowledge the compute), `docs/receipts.md`, `docs/comparator.md` | (b) | Point to [Compute sites](../sites.md) (done for `page.md` and `paper-companion.md`); the "one story" rewrite belongs to wave 2 |
| `formalization.yaml` `acknowledgements` carries the site sentence by hand | `docs/comparator.md` | (b) | Later: render it from the same backed profiles |
| lean-pkg's own README credits the cluster it was developed on | `README.md` | (a) | Correct as is: a fact about lean-pkg, not a rule for packages |
| A course-specific newcomer page | `docs/newcomers.md` | (b) | Owned by the wave-1 quickstart stream; not touched here |

Nothing in `src/lean_pkg/*.py` names a site any more (a test enforces that no module carries the site's
institution or host name).

## 4. Design: a compute receipt from a CI runner (GitHub Actions first)

Designed in [proposal 0008](../proposals/0008-github-actions-compute-receipt.md), accepted 2026-10-26; stages S1 (level 1), S2 (writing the receipt in CI) and S3 (level 2, `--corroborate`) are implemented, stages S4 and S5 are not yet. The goal: a mathematician with a public GitHub repository and no cluster account gets a
compute receipt that backs `coverage.yaml` `compute` the way `slurm-<J>` does, and that a third party can
corroborate without trusting the author.

### 4.1 Scheduler and site

- A new scheduler `github-actions`. Job id spelling: `gha-<run_id>-<run_attempt>`
  (`^gha-\d+-\d+$`). Run ids are unique on github.com; the attempt separates re-runs.
- A built-in site profile `github-actions` (title "GitHub Actions", the Actions docs URL). Its sentence
  states evidence and is rendered only at corroboration level 2 or 3 (decided, section 6); the profile is
  needed in any case so that `compute` can name it.
- Runs on self-hosted runners are **not** this scheduler: such a runner is the author's machine, and its
  receipt is worth what `receipt local` is worth.

### 4.2 What the receipt records

The lower-tier receipt keeps its nine fields (`commit`, `lean_toolchain`, `mathlib_rev`, `sorry_count`,
`axioms`, `report_sha256`, `job_id`, `checker`, `generated_at`), so `gate` validates it unchanged, with
`job_id: gha-<run>-<attempt>`. An additive `ci` block, read by the scheduler only:

| Field | Source (set by the runner, never by the workflow author) |
|---|---|
| `provider` | `github-actions` |
| `server_url`, `repository`, `repository_id` | `GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_REPOSITORY_ID` |
| `run_id`, `run_attempt`, `job` | `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, `GITHUB_JOB` |
| `event`, `head_sha` | `GITHUB_EVENT_NAME`, `GITHUB_SHA` (must equal `commit`) |
| `workflow_ref`, `workflow_sha` | `GITHUB_WORKFLOW_REF`, `GITHUB_WORKFLOW_SHA` |
| `workflow_file_sha256` | SHA-256 of the workflow file at `workflow_sha` |
| `job_workflow` | the reusable verify workflow the job called, `OWNER/REPO/.github/workflows/F@SHA` (from the OIDC token's `job_workflow_ref`) |
| `runner` | `{environment, os, arch, image, image_version}` from `RUNNER_ENVIRONMENT`, `RUNNER_OS`, `RUNNER_ARCH`, `ImageOS`, `ImageVersion`; `environment` must be `github-hosted` |
| `verify_log_sha256` | equal to `report_sha256`: the redacted verify log, committed beside the receipt as for Slurm |
| `attestation` | optional: the file name of a Sigstore bundle committed beside the receipt (4.3) |

The job runs a pinned reusable workflow published by lean-pkg (`verify.yml@<sha>`), whose last step runs
`lean-pkg receipt ci` (stage S2), which writes the receipt from these variables and the verify log, then
uploads receipt, redacted log and bundle as one artifact. The author commits them under `.lean-receipts/`
as an evidence-only descendant, by hand or with `lean-pkg receipt ci --collect RUN`, which downloads the
artifact and checks it first. CI never pushes to the package's repository.

### 4.3 How a verifier corroborates it

Three levels, each a strict superset of the one before; the page states which one held.

1. **Offline, like Slurm today.** The fields bind to the commit (`gate`), the committed redacted log
   hashes to `report_sha256`, `ci.head_sha == commit`, the job id equals `gha-<run_id>-<run_attempt>`,
   `runner.environment == github-hosted`. This is all `page` checks by default: no network.
2. **Online, the public run API** (`--corroborate`, unauthenticated for a public repository):
   `GET /repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}` must show `status: completed`,
   `conclusion: success`, `head_sha == commit`, `path` equal to the workflow path, and the same repository
   id; the run's jobs must include `job` with conclusion success on a GitHub-hosted runner; the workflow
   file at `workflow_sha` must hash to `workflow_file_sha256` and call the pinned lean-pkg verify workflow.
3. **Attested** (`actions/attest-build-provenance`): the workflow signs SLSA provenance whose subjects are
   the receipt and the redacted log. The Sigstore certificate binds the signature to the repository, the
   workflow (`job_workflow_ref`), the commit and the run, through GitHub's OIDC token. A verifier checks it
   with `gh attestation verify FILE --repo OWNER/REPO --signer-workflow lean-pkg/lean-pkg/.github/workflows/verify.yml`,
   or offline from the committed bundle with a Sigstore client. The public transparency log entry outlives
   the run's logs.

### 4.4 How it backs `compute`

Exactly like Slurm: `compute: [{cluster: github-actions, jobs: [gha-<run>-<attempt>]}]`, each job the
`job_id` of a receipt of the `github-actions` scheduler committed at HEAD (offline level 1). The scheduler
reader is a second entry in `coverage.JOB_INDEX`; the page code does not change. The level reached (offline,
corroborated, attested) is reported in `page --json` and may be shown in the evidence table.

### 4.5 The comparator tier in CI

`comparator-receipt` today runs inside a Slurm job, on a listed host, or with an explicit override. The
`github-actions` scheduler adds a recognized context (`GITHUB_ACTIONS=true` and
`RUNNER_ENVIRONMENT=github-hosted`), records `host_policy: "GitHub Actions run R attempt A"` and
`job_id: gha-R-A`, and `slurm_job_of`'s comparator rule gets its CI counterpart. The judge needs an
unprivileged user namespace for `bwrap`; current Ubuntu runner images restrict it through AppArmor, so the
reusable workflow must run `lean-pkg comparator host-check --routes` first and lift the restriction (the
runner has passwordless sudo) or fail with the route named. Hosted runners have 4 CPUs, 16 GB and a 6 h
job limit on public repositories: a Mathlib-heavy package with the Mathlib cache fits; a large one keeps
the Slurm route.

### 4.6 Threat model: a CI receipt against an sacct-checked Slurm receipt

The general trust model, what every receipt certifies and where lean-pkg relies on honesty, is
[The trust model](../trust-model.md); this section only compares the two compute receipts.

| Question | Slurm receipt (`slurm-<J>`) | GitHub Actions receipt (`gha-<run>-<attempt>`) |
|---|---|---|
| Who checked the job finished cleanly? | `receipt slurm`, run by the author, against sacct on the cluster | The runner itself; anyone can re-read the conclusion from the public API |
| Can a third party re-check that the job existed? | No: sacct needs a cluster account, and the job number does not name the cluster | Yes, for a public repository, while the run is retained; indefinitely with an attestation |
| Can the author forge one? | Yes: the committed JSON and log are author-written files; the page checks spelling and hashes, not sacct | Level 1: yes, equally. Levels 2 and 3: only by actually running a workflow in that repository on that commit |
| Is what ran known? | Only through the author's verify script and log | Through the workflow file hash and, when pinned, lean-pkg's own verify workflow |
| Can the build lie to the checker? | Yes: `lakefile` and build code run arbitrary code before the check | Same: running in CI does not sandbox the package's build from the checker; the comparator's sandboxed judge and kernel replay remain the defence |
| Whose infrastructure is trusted? | The cluster operator and the author | GitHub (runner, API, OIDC) and Sigstore |
| Does the evidence expire? | The committed files do not; the cluster's accounting is never public | Logs and artifacts expire (90 days by default) and an admin may delete a run; the committed files and an attestation do not |
| Resource ceiling | The cluster's | 4 CPUs, 16 GB, 6 h per job on hosted runners |

In short: a CI receipt at level 1 is no weaker and no stronger than a Slurm receipt. At level 2 it is
stronger on *existence* (anyone can check the run happened on that commit and succeeded) while it lasts; at
level 3 it is stronger on *provenance* (a signature no author can produce without the run). Neither proves
more about the mathematics than the receipt's fields; that remains the comparator's and the kernel's job.

## 5. Operational keys: what a lab's site file needs beyond the page

`site-profile/1` today carries only what the *page* reads: the credit and the scheduler that backs it. A
lab also needs operational data to *run* jobs (partitions, resources, container, thread defaults, the
comparator route). The answers below are the stable contract for a lab-side site file that is also a
valid profile.

| Need | Answer |
|---|---|
| Schema id, discovery | `site-profile/1`. The page reads built-in profiles and the package's committed `lean-pkg-sites/*.yaml` only, never a machine-local directory, so a page renders the same everywhere. A future operational reader (job-script rendering, `host-check`) may add a directory variable, `LEAN_PKG_SITES`, read by those commands only, never by `page` or `coverage` |
| `kind` | It is `scheduler`: `slurm` now; `github-actions` (section 4) and `systemd` (the local executor, for operational files; it backs no acknowledgement) are the planned values |
| Scheduler options, resources, container, `PATH` line, required tools, Lean thread default, open-file thresholds, comparator route | Not read by lean-pkg yet. Keep them under an extension key now (`x-lab:` with the provisional shape of the lab's plan). When lean-pkg renders `receipt slurm --template` from a profile, it adopts them as optional top-level keys of `site-profile/1` named `resources`, `runtime`, `lean` and `comparator` (reserved now; a file valid today stays valid), taking over `SBATCH_TEMPLATE`'s `HOST_TOOLS`, `host_routes` `MATHLIB_THREADS` / `NOFILE_LOW` / `FILE_TABLE_FREE_LOW`, and the comparator's allowed hosts. Per-job values on the job's own command line always win |
| Acknowledgement text and evidence rule | `acknowledgement` (with `title`, `url`, `mention`, `mention_pattern`); the evidence rule is fixed by the scheduler (section 2); `compute[].cluster` validates against profile names |
| Does a receipt record its site? | No, and the design keeps it so: a receipt field the author writes would not prove the site. The declaration names the site; a CI receipt's `ci` block is checkable against the provider's API, which is what makes it stronger (section 4) |
| Lab-only keys | Under `x-NAME` in the same file (tolerated), or in a separate file. Private values (partitions, accounts, aliases, image paths) never go into a built-in profile or into a public package's `lean-pkg-sites/` |
| Job script from a profile (`receipt slurm --template --site NAME`) | Intended, not in this change; it is the step that lets the lab retire its own sbatch wrapper. The built-in `easley` profile then stays public data only, and the lab's private values come from its own file through `LEAN_PKG_SITES` |

## 6. Decisions (decided 2026-10-17)

The maintainer accepted all four recommendations on 2026-10-17
([decision 0007](../decisions/0007-recommendations-accepted-2026-10-17.md)).

1. **Package-defined sites** (implemented): a package may commit its own profile under `lean-pkg-sites/`.
   Recommendation: keep. The alternative, built-in only, makes every new cluster a lean-pkg release.
   **Decided: kept.**
2. **The `easley` JSON key**: keep until the 1.0 freeze, then drop in favour of `sites` (recorded in the
   1.0 deprecation list). Recommendation: drop at 1.0. **Decided: dropped at the 1.0 freeze.**
3. **Does GitHub Actions get an acknowledgement sentence?** Recommendation: a built-in profile, but its
   sentence describes evidence ("Verification ran on GitHub Actions hosted runners (run R).") rather than
   thanking a resource, and it is rendered only at corroboration level 2 or 3. **Decided: as
   recommended.**
4. **Require the pinned lean-pkg verify workflow for a CI receipt to back `compute`?** Recommendation: yes
   for levels 2 and 3; level 1 accepts any workflow, as Slurm accepts any verify script. **Decided: as
   recommended.**
