---
title: The verification page
---

[&larr; lean-pkg documentation](index.md)

*Every number and status word a reader sees is rendered from data. None is typed by hand.*

A public Lean verification repository needs one section a reader can trust:
what is formalized and what is not, under which assumptions, what the
receipts prove, how to reproduce the result, how to cite it, and whom to
credit. `lean-pkg page NAME` renders that section into the package's
`README.md` from data the tool already checks. `lean-pkg page NAME --check`
fails when the section no longer matches the data. Counts copied by hand into
several files drift apart; a rendered block that is checked in CI cannot.

```bash
lean-pkg coverage check my-companion        # validate coverage.yaml (below)
lean-pkg page my-companion                  # print the block
lean-pkg page my-companion --write          # replace the marked block in README.md
lean-pkg page my-companion --check          # exit 1 when it is missing or stale
lean-pkg page my-companion --site docs --html docs/verification.html   # docs pages + one HTML file
```

## What it reads

| Input | Read how | Feeds |
|---|---|---|
| `coverage.yaml` | committed at `HEAD`, schema `coverage/1` ([below](#coverageyaml)) | the capsule sentence, the paper-to-Lean map, Not formalized, the manuscript reference, the compute acknowledgment |
| `claims.yaml` | committed at `HEAD` | the Lean declarations of each row |
| the claims ledger | `lean-pkg claims ledger NAME`, promote stage | the word *verified*, per row and in the capsule |
| receipts | `.lean-receipts/*.json` committed at `HEAD`; the registry's `gate.receipt` and `gate.comparator` mark the recorded ones | the trust boundary, the expected axioms, the evidence table |
| `lean-toolchain`, `lake-manifest.json` | committed at `HEAD` | the reproduce section |
| `CITATION.cff` | committed at `HEAD`; it needs a title and at least one author | the citation |
| registry | `repo` (as an https URL), `status`, `license_spdx` (checked against the committed license file), `doi` | the clone command, the capsule, the license, the citation DOI when `CITATION.cff` has none |

Only these receipt fields are copied: the commit, the toolchain, the Mathlib
revision, the `sorry` count, the axioms, the theorem names, the permitted
axioms and the definition names of each comparator configuration, the date
part of `generated_at`, the SHA-256 of the committed file, and a job id of
the form `slurm-<J>`. Host names, paths, user names, checker commands and
every other job id (a systemd unit's or a local run's, which name a host) are
never copied. Every output (the block, the site pages, the HTML file) then
passes through the
[public-safety scanner](how-to.md#scan-for-private-strings-before-publishing)
with the package's `.lean-pkg-private-patterns`. A blocking hit in any of them
refuses the render and nothing is written.

Text that comes from data (titles, notes, inputs, the summary, authors) is
written inert: `&`, `<` and `>` become entities, and the Markdown markup
characters are backslash-escaped. A note cannot open a tag, a link or a code
span on GitHub, in MkDocs or in the HTML file.

## The sections

| Section | What it says |
|---|---|
| Verification status | the `summary` sentence from `coverage.yaml`; the manuscript version; the number of paper rows by class and how many formalized rows the ledger closes; the ledger's package status; the registry status |
| Paper ↔ Lean map | one row per paper result: number and title, correspondence class, declarations (linked at the gate-bound commit when the repository is on GitHub), the ledger word, inputs and notes |
| Not formalized | every `outside` row, or the `nothing_excluded` reason |
| Trust boundary | what the gate receipt and the comparator receipt record, and a fixed paragraph on what neither checks |
| Reproduce | toolchain, Mathlib revision, a command template (clone, checkout of the gate-bound commit, `lake exe cache get` when Mathlib is a dependency, `lake build`), the expected `#print axioms` result |
| Evidence | one row per receipt: path, tier, commit, date, Slurm job, SHA-256 |
| Citation | authors, title, version and DOI from `CITATION.cff`, and the manuscript |
| License | `license_spdx` and the license file |
| Acknowledgments | the lean-pkg credit, always; the Easley sentence only [with evidence](#the-easley-acknowledgment) |

The status words are tied to the ledger. A row says *verified* only when
`lean-pkg claims ledger NAME` (promote stage, so the comparator tier as well
as the gate) closes every claim the row names. Otherwise the row shows the
ledger's own status (`open`, `blocked`, `unknown`, `out-of-scope`). Nothing in
the block calls the paper verified.

Commit the README and `coverage.yaml` after the gate, and the gate-bound
commit is no longer `HEAD`. Opt the package into the docs-only rule
(`lean-pkg update NAME --accept-docs-descendants --reason TEXT`, see
[receipts](receipts.md)) so the receipts keep binding. Without it, the ledger
reports the receipts as stale and the page says so.

## coverage.yaml

`claims.yaml` lists Lean results. `coverage.yaml` lists paper results: each
numbered result of a citable manuscript and how the formalization relates to
it. It lives in the package directory and is read at `HEAD`.

```yaml
schema: coverage/1
package: telescoping-companion
manuscript:
  title: "Telescoping sums and a sharp estimate"
  version: "arXiv:<id>v<N>"          # the posted id and version; or {repo: https://..., commit: <40 hex>, sha256: <64 hex>}
summary: "Lemma 2.1 is formalized with the paper's hypotheses; Proposition 2.3 uses it with one supplied input."
rows:
  - paper: {number: "Lemma 2.1", title: "Partial sums", label: "L:partial"}   # label optional
    class: same-assumptions
    claims: [telescoping-companion/sum-inv-mul-succ]
  - paper: {number: "Proposition 2.3", title: "The estimate"}
    class: inputs-supplied
    claims: [telescoping-companion/estimate]
    inputs: ["the tail bound of Section 3"]
    note: "the Lean statement takes the tail bound as a hypothesis"
  - paper: {number: "Theorem 4.1", title: "Asymptotics"}
    class: outside
    note: "not formalized"
compute:                              # optional; see the Easley acknowledgment
  - cluster: easley
    jobs: [slurm-123456]
```

| Key | Rule |
|---|---|
| `schema`, `package` | `coverage/1`; the package name |
| `manuscript.version` | a posted version: `arXiv:<id>v<N>`, or `{repo, commit, sha256}` (a public https repository, a full commit, the SHA-256 of the manuscript file). `unposted` (a private draft) is refused unless `--allow-private-manuscript` is passed; the page then says the numbers cannot be checked yet |
| `manuscript.eprint_sha256` | optional, only beside an `arXiv:<id>v<N>` version: the SHA-256 of that version's e-print. `lean-pkg formalization anchor` writes it with the record `data/source-baselines/arXiv-<id>v<N>.json`; `coverage check` refuses a value that differs from the record committed at `HEAD` and warns when no record is committed. An older lean-pkg refuses the key (coverage/1 is strict) |
| `summary` | one line: the capsule's only hand-written sentence |
| `rows[].class` | `same-assumptions` (the Lean statement carries the paper's hypotheses), `inputs-supplied` (it also takes the named `inputs` as hypotheses), `outside` (no Lean counterpart) |
| `rows[].claims` | ids in `claims.yaml` at `HEAD`; at least one for the first two classes, none for `outside`; a superseded claim is refused |
| `rows[].inputs` | required for `inputs-supplied`, refused for `same-assumptions` |
| `nothing_excluded` | `{reason: TEXT}`, only when no row is `outside`. One of the two is required: a page always says where the formalization stops |
| `compute` | `[{cluster: easley, jobs: [slurm-<J>, ...]}]`; each job must be a Slurm receipt committed under `.lean-receipts/` |
| `lean_pkg_version` | optional `{version, doi}`: the exact lean-pkg version to credit. When lean-pkg's own `CITATION.cff` is readable it must list that version with exactly that DOI |

The file is strict: unknown and missing keys, values outside the enums and
repeated mapping keys are refused. `lean-pkg coverage check NAME` validates the
committed file; `--file PATH` checks a working copy before you commit it (claim
ids and receipts are still read at `HEAD`).

Migrating an existing map is mechanical. A hand-written guide table with
one row per theorem becomes one row each: its correspondence class, the
declarations' claim ids, and the premises as `inputs`. A ledger JSON whose
rows carry `reference {label, number, page}` and a `status` becomes rows of
`paper {number, title, label}`. There `verified` maps to `same-assumptions`,
`verified-conditional` to `inputs-supplied` with its conditions as inputs,
and `outside-agreed-scope` to `outside`. A row that is not yet verified keeps its class: the ledger, not
the class, decides the word *verified*. The per-theorem prose notes stay
hand-written, outside the markers.

## The README block

The block sits between two markers:

```markdown
<!-- lean-pkg:page:begin -->
...generated...
<!-- lean-pkg:page:end -->
```

`--write` replaces exactly the marked block. When the README has no markers
it appends the block, and when there is no README it creates one. Prose before
and after the markers is never touched, so keep the title, the long reader
guide and the per-theorem notes outside them. Unbalanced or repeated markers
are refused. `--write` never commits and never writes the registry (no lock,
no `--commit`). A live lease held by another agent refuses it unless you pass
`--agent HOLDER`. `--out PATH` writes the block to another file instead.

`--check` writes nothing and exits 1 when:

- `README.md` is missing, or has no block, or its block differs from a fresh render;
- the README lacks the lean-pkg credit (the repository link and the concept DOI);
- the README mentions the Easley Cluster anywhere, inside the markers or not, without a backing receipt;
- with `--site DIR` or `--html PATH`, a file there is missing or differs from a fresh render.

Run it in the companion's CI. `lean-pkg publish NAME --dry-run` carries the
same check as one checklist line (JSON `page {status, adopted, problems}`). For
a package with no `coverage.yaml` and no block, the line is a WARN, except for
an unbacked Easley sentence, which is a failure.

## The Easley acknowledgment

A receipt does not record where its job ran: a Slurm receipt says
`slurm-<J>`, and the job number alone does not name the cluster. The sentence
therefore needs two things: an explicit declaration in `coverage.yaml`
(`compute: [{cluster: easley, jobs: [...]}]`), and, for every listed job, a
committed receipt whose job is that Slurm job. That is a lower-tier receipt
with `job_id: slurm-<J>` (what `lean-pkg receipt slurm` writes) or a comparator
receipt whose `host_policy` says it ran in Slurm job `<J>`. With both, the
Acknowledgments carry the official sentence with the cluster linked. Without
them there is no sentence, and `--check` fails on one written by hand.

## Citation

`CITATION.cff` must be committed, with a `title` and at least one author
with `family-names` or `name`: a citation without its human authors is not
usable. The DOI is the file's `doi`, else the registry's concept DOI.

## Docs pages and a static HTML page

```bash
lean-pkg page my-companion --site docs                       # docs/verification.md + docs/coverage.md
lean-pkg page my-companion --html docs/verification.html     # one HTML file
lean-pkg page my-companion --check --site docs --html docs/verification.html
```

`--site DIR` writes two Markdown pages for MkDocs (or any static site
generator) from the same data as the README block:

| File | What it holds |
|---|---|
| `verification.md` | the full page under one `# Verification` title: every section of the README block, in the same order, plus a link to `coverage.md` |
| `coverage.md` | every row of `coverage.yaml`, one table line each: number, title, class, every claim id, its declaration, each claim's own ledger status, the inputs and the note |

`--html PATH` writes one HTML file holding both pages: no script, no
external stylesheet, font or image, a few lines of inline CSS (light and dark).
It is rendered from the same Markdown, so it says exactly what the pages say.
Links are kept only to https targets and in-page anchors.

The files do not depend on DIR: the same data gives the same bytes wherever
they are written. Links to the package's own files (`LICENSE`,
`CITATION.cff`) point at the repository on GitHub (`blob/HEAD`), since the
docs directory is your choice; on another host they are plain names. Without
`--check` both options write (atomically; a symlink as a target is refused)
and leave `README.md` alone unless `--write` is given too. With `--check` they
are compared and nothing is written. Neither writes the registry.

Add `verification.md` and `coverage.md` to the `nav` of `mkdocs.yml`
yourself; lean-pkg never edits it.

## In CI

`page --check` reads the lean-pkg registry record of the package (its
status, license, recorded receipts). A hosted runner has no registry, so the
job writes one from a repository secret and points `--repo` at the checkout.
`--repo DIR` reads that checkout instead of the registered `local_path`, for
this call only; one of its remotes must be the package's repository.

1. Put a registry file holding the package's record into a repository secret
   named `LEAN_PKG_REGISTRY_YAML` (the record names local paths, so it does
   not belong in the public tree). Refresh it when the record changes (a new
   gate, a new status).
2. Add the job:

```yaml
# .github/workflows/verification-page.yml
name: verification page
on: [push, pull_request]
jobs:
  page:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0            # the ledger checks that HEAD descends from the gate-bound commit
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pipx install git+https://github.com/lean-pkg/lean-pkg.git@v0.7.54
      - name: lean-pkg page --check
        env:
          LEAN_PKG_REGISTRY_YAML: ${{ secrets.LEAN_PKG_REGISTRY_YAML }}
        run: |
          printf '%s\n' "$LEAN_PKG_REGISTRY_YAML" > "$RUNNER_TEMP/lean-packages.yaml"
          lean-pkg --registry "$RUNNER_TEMP/lean-packages.yaml" page my-companion --check --repo "$GITHUB_WORKSPACE" --site docs
```

Drop `--site docs` when the package has no docs pages; add `--html PATH`
when it ships the HTML file. Pin the lean-pkg version you render with: a newer
version may word a section differently, and the check then reports the
committed files as stale until you render them again.

Where the registry is at hand (the lab machine, a self-hosted runner, a
pre-push hook), a Makefile target is enough:

```make
.RECIPEPREFIX := >
.PHONY: verification-page
# exit 1 when README.md, docs/verification.md, docs/coverage.md or the HTML page is stale
verification-page:
> lean-pkg page my-companion --check --site docs --html docs/verification.html
```

`release --fresh-repo` exports the package tree as it is and adds no
generated files, so it does not install the workflow; copy it in yourself.

## From lean-scope-page

The lab script `lean-scope-page` (`init` and `lint` of hand-written
`docs/scope/<slug>.md` pages) is superseded by `coverage.yaml` and
`lean-pkg page`: the page is now rendered, so most of its rules hold by
construction, and the rest are checks of the data. `coverage check` runs
them, and so does every `page` render (`page --check` included).

| lean-scope-page rule | In lean-pkg |
|---|---|
| R1 a scope page exists | `page --check` fails when `README.md` has no page block; with `--site`/`--html`, when a file is missing |
| R2 exactly one H1 title | by construction: `verification.md` and `coverage.md` each have one `#` title |
| R3 the sections, in order | by construction: the fixed section order above; Not formalized always sits after the map |
| R4 no `FILL-ME` placeholder | `coverage check`: any text of `coverage.yaml` carrying `FILL-ME`, `TODO`, `TBD`, `FIXME` or `XXX` is refused |
| R5 Papers has a bullet | `coverage check`: `manuscript` {title, version} is required, the version posted (or `--allow-private-manuscript`) |
| R6 Scope has prose | `coverage check`: `summary` is required, one line |
| R7 Not formalized is non-empty, or "nothing excluded" with a justification | `coverage check`: at least one `outside` row, or `nothing_excluded: {reason}` (never both) |
| R8 the comparator table's links resolve | `coverage check`: every claim a row names has its `file` committed at `HEAD`; the declaration links point at the gate-bound commit |
| R9 every relative link resolves | by construction: the only relative links are `LICENSE` and `CITATION.cff` from the README (both required at `HEAD`) and `coverage.md`/`verification.md` beside each other |
| R10 every comparator config is linked | `coverage check` (with R11): every configuration of the recorded comparator receipt is accounted for through its theorems |
| R11 every `theorem_names` entry has a row | `coverage check`: every theorem the recorded comparator receipt compares is the declaration of a claim some row names (`<Prefix>.<decl>`, the Challenge form, counts) |
| R12 definition holes name their reviewer | `coverage check`: a configuration of the recorded comparator receipt with `definition_names` must carry a sign-off reviewer (its `signoff` copy of `lean-pkg-signoff.json`, or the deprecated `judged_as.reviewer`); the trust boundary then names each hole and who judged its body, and when |

Two differences. lean-pkg reads the comparator configurations from the
recorded comparator receipt, not from the files in the tree: the page states
what was compared, not what is configured. And there is one page per package,
not one per family; a package with several families lists them as rows of one
`coverage.yaml`.

Migrating a scope page: each table row of `## Comparator links` becomes a
`coverage.yaml` row whose `claims` name the theorem's claim; each bullet of
`## Not formalized` becomes an `outside` row; the "nothing excluded"
justification becomes `nothing_excluded.reason`; the `## Scope` prose becomes
`summary` (one sentence) and, for the rest, hand prose outside the README
markers. Then delete `docs/scope/` and run `lean-pkg page NAME --write`.

## Not yet

- A blueprint cross-check (`\lean{}` names against `claims.yaml`).
- Measured wall time and peak memory in the reproduce section, once receipts
  record them.
- A registry-free CI check (today the job needs the record from a secret).
