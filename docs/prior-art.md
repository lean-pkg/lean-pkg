---
title: Prior-art search
---

[&larr; lean-pkg documentation](index.md)

*Search before building, and write down what you found, zeros included.*

Before a new definition, lemma or library, look for it: in your own group's
packages, in the public index, in Mathlib (Loogle, LeanSearch), on Reservoir,
on Palomar, and on GitHub. `lean-pkg search` runs all of them in a fixed
order, says which ones it could not reach, and with `--record` writes a dated,
citable record into the research repository. The manual procedure, with the
traps it avoids, is the
[search-before-building skill](https://github.com/lean-pkg/lean-pkg/blob/main/skills/search-before-building/SKILL.md).

## Claims

A claim is a named result or object in a package that someone might reuse.
Claims live with the code, in `claims.yaml` (schema `claims/1`) at the
repository root, so they travel with every commit:

```yaml
schema: claims/1
package: she-rough-time-data
claims:
- id: she-rough-time-data/stochastic-fubini
  decl: SheRough.stochastic_fubini_ennreal
  kind: theorem
  summary: Stochastic Fubini for nonnegative integrands against a white-noise integral.
  aliases: [Fubini, stochastic Fubini, exchange of integrals, Walsh integral]
  keywords: [spde, white-noise, fubini]
  file: SheRough/Fubini.lean
  status: reusable
  superseded_by: null
  citation: Chen and Dalang 2015, Theorem 2.6
  depends_on: [rmt-foundations/semicircle-law]
```

| Key | Rule |
|---|---|
| `id` | `<package>/<slug>`; the slug (`[a-z0-9][a-z0-9-]*`) names the mathematics, never the declaration, so a rename changes `decl` and keeps the id |
| `decl` | the Lean declaration; its last name component must occur in `file` |
| `kind` | `definition`, `lemma`, `theorem`, `construction` or `library` |
| `summary` | one sentence |
| `aliases`, `keywords` | free phrases; lowercase tags (`[a-z0-9-]`) |
| `status` | `reusable`, `internal`, `experimental` or `superseded` |
| `superseded_by` | the id that replaces a superseded claim (a claim is never deleted; supersession must not cycle) |
| `citation`, `depends_on` | an optional paper locator; other claim ids |

Unknown keys are refused, and so is anything that looks like a local path.
Trust is never typed into a claim: it is derived from the package's receipts.

```bash
lean-pkg claims sync my-lean     # read claims.yaml at HEAD, check every decl, cache it (event claims-synced)
lean-pkg claims list             # ID, KIND, STATUS, TRUST, PIN, DECL
lean-pkg claims check            # re-validate the cached copies; exit 0 or 1
```

`claims check NAME` fails (exit 1) when the package has no coverage: no cached
claims and no `claims.yaml` at `HEAD` (`coverage: none`), or a committed
`claims.yaml` that was never synced (`coverage: unsynced`). A package whose
checkout still carries a legacy `claims.json` gets its path and the migration
command:

```bash
lean-pkg claims migrate my-lean --from data/claims.json --dry-run   # print the claims/1 skeleton
lean-pkg claims migrate my-lean --from data/claims.json             # write claims.yaml (never the registry)
```

The skeleton copies what it can map and marks the rest `TODO`
(`file: TODO.lean` makes `claims sync` refuse it until fixed). Finish it, commit it, and
run `claims sync`: only `sync` changes the cached claims.

`claims sync` reads the committed file at `HEAD` (an uncommitted edit does not
count) and refuses a file whose `package` is another name. The registry keeps
the copy and the commit it was read at (registry version 3, keys
`claims_file`, `claims`, `claims_synced_commit`; `lean-pkg migrate` adds
them), so search works offline and from any machine that shares the registry.

## Searching

```bash
lean-pkg search 'stochastic fubini' --package my-lean
lean-pkg search 'Fisher-Tippett' --offline --json
lean-pkg search 'Gumbel' --sources reservoir,github
```

Sources, always in this order:

| Source | What is searched | Revision printed |
|---|---|---|
| `registry` | the cached claims of every registered package | content hash of the registry file |
| `index` | the public index: `claims[]` of `index-entry/2` entries, else the headline | the index's git commit |
| `loogle` | Loogle's JSON API (Mathlib master, not your pin) | `live (Mathlib master)` |
| `leansearch` | LeanSearch, natural language | `live` |
| `reservoir` | descriptions, keywords and names in the `leanprover/reservoir-index` data, cached by commit; never the site's name-only box | the index commit, for example `477b5e7c` |
| `palomar` | titles, abstracts, theorem names and repositories in Palomar's results API, cached by revision | the registry revision |
| `github` | `gh search repos` and `gh search code`, Lean only | `live` |

Palomar caches are complete, revision-consistent snapshots: every page must
carry the same registry revision, and paging errors or repeated cursors are
reported as skipped sources rather than zero hits. Cached rows carry a schema,
completion marker and content hash; malformed or changed caches are refreshed
from live pages. The snapshots-v1 cache directory is separate from older
bare-list caches, so older running clients never read the new envelope. A
legacy cache alone is not used as offline search evidence. Concurrent searches
write separate temporary files and publish the cache atomically.


Local sources are scored deterministically: an exact alias scores 3, a
keyword 2, a match of every word of the term 1; matching is case-insensitive.
The registry source also scores each package itself: its name (an exact name
scores 3), notes, owner_project and repo, so a registered package is found
before it has cached claims. Package hits print as
`package: NAME (status) — repo`; in `--json` every local hit has `hit_kind`
(`package` or `claim`).
Reusable claims are listed before experimental and superseded ones. Network
sources are listed as each service ranks them and are never merged into the
local score.

Each block says `ok` or `skipped: <reason>`. An unreachable service, a missing
`gh`, or `--offline` gives `skipped`, never zero hits, and the closing line
names every source consulted. Zero hits are not an error: the exit code is 0
unless the command line is wrong (exit 2). `--package NAME` names the
registered package the search is for; every package's claims are still
searched.

## The record

`--record` writes `docs/prior-art/<YYYY-MM-DD>-<slug>.md` into the package's
checkout (`--package`) or into `--repo-dir`, and appends one
`prior-art-searched` line to the registry's event log. The record lists the
term, the date, every source with its revision, state and hit count, the top
five candidates per source, and ends with

```text
decision: pending
reason:
```

which you edit to one of `require`, `reuse`, `vendor`, `contribute` or `build`,
with the reason each candidate was rejected. Sources that ran and found nothing
are worded as the record of a bounded search, for example: Not found in
reservoir at 477b5e7c, palomar at 173 on 2026-10-06 for "Gumbel". A record is
never overwritten and is never written into the registry directory.

Privacy: registry rows, cached claims and event lines stay private. A record
lists claims of other private packages only when the package it is written for
is itself private, and then says `private-claims: yes` near the top; check such
a record before that repository goes public. Otherwise it gives only their
number.

## Reminders, not gates

`lean-pkg claim` prints a note when the package has no `prior-art-searched`
event in the last 30 days and no claims, and `lean-pkg index add` prints a
note for each headline that no record under `docs/prior-art/` at the pin names
(by the last component of its declaration). Neither refuses anything.
