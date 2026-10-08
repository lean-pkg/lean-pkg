---
name: search-before-building
description: "Run before writing any new non-trivial Lean definition, lemma or library: Loogle, Mathlib at the pin, the Reservoir index, Palomar, known authors, the GitHub long tail; record every query including zero hits; decide require / vendor / contribute / build. Trigger phrases: search before building, prior art in Lean, does this already exist in Lean, Reservoir search, is it on Reservoir, Palomar search, Loogle, reuse a Lean library, should I depend on, vendor or build, new lean_lib, don't build wheels."
license: Apache-2.0
---

# Search before building

## Problem

A formalization starts writing a definition or a small library that already
exists elsewhere, sometimes with a better API, sometimes with the exact gap
the new work could close upstream. The duplicate costs days, splits users
between two versions, and is usually discovered only after release. The
opposite failure is quieter: an agent searches one place, finds nothing, and
writes "nothing exists" as if that were a fact.

## Why it happens

Lean prior art lives in several indexes that barely overlap. Mathlib holds the
general library; [Reservoir](https://reservoir.lean-lang.org/) lists public
Lake packages automatically and checks only that they build; Palomar
registers kernel-checked research results; much else sits in repositories
that neither index lists (fewer than 2 stars, no detected license, a fork, no
root manifest). In one survey only 22 of 363 Palomar source repositories were
also on Reservoir. Two traps make a single search look conclusive:

- **The Reservoir site search matches package names only.** Its `?q=` box
  never reads descriptions, so `kkytola/ExtremeValueProject`, whose
  description says "Fisher-Tippett-Gnedenko", is invisible to a site search
  for "Fisher-Tippett". Search the index data instead.
- **Descriptions use the author's words, not yours.** The same description
  never says "Gumbel", although the project formalizes the Gumbel
  distribution; only a synonym or a code search finds it.
- **A Reservoir package name is not always the repository name.** The
  package `eric-wieser/matrix_cookbook` lives in the repository
  `eric-wieser/lean-matrix-cookbook`; cloning the package name returns 404
  and looks like a deleted project. Read the repository URL from the index.

## Rule

- Run the checklist below before writing any new non-trivial definition,
  lemma or `lean_lib`, and before claiming that something "does not exist in
  Lean".
- Search every source; one index is never enough.
- Record every query, including zero-hit ones, as term, source, date, hits,
  decision.
- **Zero hits do not prove absence.** Say "not found in <sources> on <date>
  for <terms>", never "does not exist".
- End with one decision per need: **require** with an exact pin, **vendor**
  with a NOTICE and the upstream license, **contribute** upstream, or
  **build**, stating why every hit fails. Do not start a competing library
  without that written justification.

## How to apply

Work in a scratch directory outside the repository. Try at least two phrasings
per need: the paper's term, the standard name, and a classical theorem name
(for example "Gumbel", "extreme value", "Fisher-Tippett").

With lean-pkg 0.4 or later, one command per phrasing covers your registry's
claims, the public index and steps 1, 3, 4 and 6, and writes the record of
step 7 into the package's repository with `decision: pending`:

```bash
lean-pkg search 'Gumbel' --package my-lean --record
```

Read its closing line: a source marked `skipped` was not searched, so run that
step by hand or say so in the record. Steps 2 and 5 always stay manual. See
the [Prior-art search](https://github.com/lean-pkg/lean-pkg/blob/main/docs/prior-art.md) page.

1. **Loogle** (Mathlib and its dependencies, by name or type pattern):

   ```bash
   q='Real.exp _ ≤ _'
   curl -s "https://loogle.lean-lang.org/json?q=$(jq -rn --arg q "$q" '$q|@uri')" \
     | jq '.error // .count, [.hits[]?.name][:20]'
   ```

   For natural language use <https://leansearch.net/>. Confirm every hit with
   `#check` in your own project: the web services may run a newer Mathlib.

2. **Mathlib at the exact pin**, not a newer checkout:

   ```bash
   test "$(git -C .lake/packages/mathlib rev-parse HEAD)" = \
        "$(jq -r '.packages[] | select(.name=="mathlib") | .rev' lake-manifest.json)"
   rg -n -i 'gumbel|extreme value' .lake/packages/mathlib/Mathlib
   gh pr list -R leanprover-community/mathlib4 --state all --search 'Gumbel' --limit 30
   ```

   An open or closed pull request is prior art too: review it, revive it, or
   cite why it was dropped.

3. **Reservoir, through its index repository** (not the site search). The
   tarball of `leanprover/reservoir-index` is a few MB; its top directory name
   carries the index commit, which goes in the record.

   ```bash
   mkdir -p ri && gh api repos/leanprover/reservoir-index/tarball | tar xz -C ri
   ls ri                                    # leanprover-reservoir-index-<commit>
   grep -ril -e 'gumbel' -e 'extreme value' ri/*/ --include=metadata.json
   jq '{description, keywords, stars, license, repo: [.sources[].repoUrl]}' ri/*/<owner>/<name>/metadata.json
   # build history per toolchain (built, tested, requiredUpdate)
   curl -s https://reservoir.lean-lang.org/api/v1/packages/<owner>/<name>/builds \
     | jq '.data[] | {toolchain, built, tested, requiredUpdate, runAt}'
   ```

   Index paths are lower case; the HTTP API takes the package name in any
   case. Clone from the `repo` URL above, not from the package name.
   Browsing by keyword (`https://reservoir.lean-lang.org/packages?keyword=probability`)
   complements, never replaces, the grep. A Reservoir build only means
   `lake build` succeeded at that toolchain: it says nothing about `sorry`,
   axioms or fidelity.

4. **Palomar** (registered, kernel-checked results; about 450 in October
   2026). Page the public API and grep titles and abstracts:

   ```bash
   url='https://data.palomar-registry.org/api/v1/results?order=registered'; cursor=''
   while :; do
     page=$(curl -s "$url${cursor:+&cursor=$cursor}")
     printf '%s\n' "$page" | jq -r '.entries[] | [.id, .path, .source.repository, .title, .abstract] | @tsv'
     cursor=$(printf '%s\n' "$page" | jq -r '.next // empty | @uri')
     [ -n "$cursor" ] || break
   done > palomar.tsv
   grep -i -e 'gumbel' -e 'extreme value' palomar.tsv | cut -f1-4
   ```

   Read a hit's full entry at `https://data.palomar-registry.org/<path>`
   (the second column). Use `printf`, not `echo`: some shells expand
   backslashes inside the JSON.

5. **Known authors** in your area. List their repositories, including
   unlisted ones:

   ```bash
   gh repo list <author> --limit 200 --json name,description,stargazerCount,updatedAt
   ```

   Keep your own list of authors who publish Lean in your field, and the
   ledger of candidates you have already assessed.

6. **The GitHub long tail** (repositories no index lists):

   ```bash
   gh search repos 'extreme value' --language Lean --limit 30 \
     --json fullName,description,stargazersCount,license,updatedAt
   gh search code 'Gumbel' --language Lean --limit 30 --json repository,path
   ```

   A hit without a license cannot be reused; ask the author or treat it as
   prior art to cite, not code to copy.

7. **Decide and record.** One block per need, kept in the project (for
   example `docs/prior-art.md`) or in your candidates ledger:

   ```text
   need:     Gumbel CDF and its max-stability
   date:     2026-10-05
   searches:
     - term: "gumbel" | source: Loogle            | hits: 0
     - term: "gumbel" | source: Mathlib @ <rev>    | hits: <n>
     - term: "extreme value" | source: reservoir-index@<commit> | hits: 1 (kkytola/ExtremeValueProject, 2 stars, Apache-2.0; 4 sorries in the CDF)
     - term: "gumbel" | source: Palomar rev <n>    | hits: 0
     - term: "Gumbel" | source: gh search code     | hits: <n> repositories (each read; why none fits)
   decision: build locally sorry-free, then contribute upstream to close the sorries
   why not require: the upstream CDF still has sorries at its latest build
   ```

If the result will be depended on by others, run the same search again at
publication time: the field moves in weeks.

## Anti-patterns

- Searching the Reservoir website box and reporting "not on Reservoir".
- One phrasing, one source, then "nothing exists in Lean".
- Not writing down the zero-hit searches, so the next person repeats them.
- Copying code from an unlicensed repository because it "is public".
- Treating a Reservoir build badge or star count as evidence that the
  mathematics is right; read the code and run the axiom audit
  (`lean-axiom-audit`) on anything you depend on.
- Building next to a package you found without recording why you did not
  use it.

## Verification

For each new definition or library there is a dated record naming every
source above, the exact terms, the hit counts (zeros included), the Reservoir
index commit and the Palomar revision used, and one decision with its reason.
Every "not found" statement in the project's documents names its sources and
date.

Learned from: a reuse audit of a research group's Lean packages, where a
Gumbel CDF had been re-derived beside a public package nobody had searched
for, because the Reservoir site search matches package names only and the
package's description used a different name for the same object, October 2026.
