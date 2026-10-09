---
title: Receipts and trust
---

[&larr; lean-pkg documentation](index.md)

*Evidence you can re-derive, or it isn't evidence.*

## Two tiers

| Tier | Produced by | Validated by | Proves |
|---|---|---|---|
| **Lower** | any executor: `lake build`, sorry scan, `#print axioms`, frozen report | `gate --receipt` | the package builds at the pinned toolchain with `sorry_count = 0` and only the permitted axioms, at one commit |
| **Comparator** | `comparator-receipt` running `comparator-check.sh` | `gate --comparator`, `comparator`, `promote` | each `Solution` theorem has exactly the statement of its trusted `Challenge` namesake, uses only the permitted axioms, and replays in the independent `nanoda` kernel |

The comparator tier is optional at `gate`, so exploratory packages do not pay
its cost. It is required at `promote`, so nothing becomes public without it.
Both tiers must bind to the same commit.

Either tier may run as a Slurm job (see [Host policy](comparator.md#host-policy)).
The lab's Slurm receipts were produced on the Auburn University Easley
Cluster; packages verified there carry the acknowledgment "This work was completed in part with resources provided by the Auburn University Easley Cluster."

## What a passing pair does and does not prove

| Proves | Does not prove |
|---|---|
| the listed theorems compile, sorry-free, with `propext`, `Quot.sound`, `Classical.choice` only | that the Lean statement matches the paper's intent (human fidelity review) |
| statement equality between `Challenge` and `Solution` for every listed name | anything about theorems not listed in the comparator configuration |
| replay in a second, independent kernel | soundness of dependencies beyond the pinned Mathlib |
| evidence bytes on disk hash to what the receipt recorded | public availability, license, or documentation (separate checklist lines) |

## Lower-tier receipt schema

| Field | Rule |
|---|---|
| `commit` | 40 lowercase hex; HEAD, or HEAD is an evidence-only descendant of it |
| `lean_toolchain` | equals the `lean-toolchain` file committed at `commit`, stripped (an uncommitted edit never counts) |
| `mathlib_rev` | nonempty |
| `sorry_count` | integer, must be 0 |
| `axioms` | list of strings, subset of `gate.axioms_allowed` (default `propext`, `Quot.sound`, `Classical.choice`) |
| `report_sha256` | 64 hex characters: the frozen verification report |
| `job_id`, `checker`, `generated_at` | nonempty: the executor's terminal job identity, the exact checker mode, and an ISO-8601 time that must parse. The stored copy is named `<commit12>-<job_id>.json`, with characters outside `[A-Za-z0-9._-]` replaced by `-` and leading dots and dashes dropped |

The frozen report named by `report_sha256` is expected to bind the candidate
commit or tree, the toolchain and dependency pins, complete checker
coverage, the terminal state, and executor identity. A pilot run, a partial
manifest, or a receipt for a previous candidate is not gate evidence.

## Comparator receipt schema (`comparator-receipt/2`; `/1` still validates)

| Field | Rule |
|---|---|
| `schema` | `"comparator-receipt/2"` (a `"comparator-receipt/1"` receipt is read as the legacy mechanism) |
| `mechanism` | `"toolchain"` (`lake comparator` with the toolchain's bundled kernels, Lean v4.35.0-rc2+) or `"legacy"` (pinned stack); see [Comparator](comparator.md#mechanisms) |
| `commit` | equals the lower-tier receipt commit; HEAD, or HEAD is an evidence-only descendant |
| `lean_toolchain` | equals the `lean-toolchain` file committed at `commit` |
| `tools` | legacy: `comparator`, `lean4export`, `nanoda`, `landrun` as 40-hex commits; toolchain: `lean_toolchain` and the 64-hex sha256 of `lake`, `leanexport`, `leanchecker`, `nanoda_bin`, `con-ron` |
| `host`, `job_id`, `generated_at` | nonempty; `generated_at` must parse as ISO-8601 |
| `host_policy` | the runner's host-policy reason (an override run is visible here) |
| `stages` | optional, additive, not validated: the runner's stage summary (`stage`, `status`, `exit`, `wall_s` for `checkout`, `cache-get`, `build`, `comparator`); the full logs stay in the runner's evidence directory, see [The comparator tier](comparator.md) |
| `test_only` | present and true only on receipts produced under the runner's test-only digest override; such a receipt is refused by every validator |
| `result` | `"pass"` (the runner writes a receipt only on PASS) |
| `configs[]` | exactly the `comparator*.json` / `comparator/config*.json` files tracked at the bound commit, no omissions, no extras. Each entry: `path`; `sha256` of the file's bytes at the bound commit; `challenge_module`, `solution_module`, `theorem_names` (non-empty), `definition_names`, `permitted_axioms` (each equal to the file); `judged_as` (optional; copied from the file when present); `log` = `.lean-receipts/<commit12>-comparator-<cfgname>.log` where `cfgname` is the path without `.json` and with `/` replaced by `_`; `log_sha256`; `result: "pass"`. The log must contain the full line `Your solution is okay!` for both mechanisms (`lake comparator` prints it last, after the per-kernel acceptance lines; the runner and the validator share one predicate). Symlinked evidence is refused |

A config whose file at the bound commit has a non-empty `definition_names` must carry a human `judged_as` sign-off there (see [Comparator](comparator.md#definition-holes-need-a-human-sign-off)); the rule is judged against the tracked bytes, never the receipt's copy.

Validation fails closed on the first miss and names the field. A file already
at the canonical `.lean-receipts/` path that differs from the receipt being
recorded is refused, so a registry pointer can never silently refer to
different bytes.

## Evidence binding: the evidence-only descendant rule

A receipt names commit X, but the receipt file is committed afterwards, so it
can never be tracked at the commit it names. The rule:

> A receipt bound to commit X is accepted at HEAD H when H = X, or when H
> descends from X and `git diff --name-only X H` is non-empty and contains
> only paths under `.lean-receipts/`. Any other difference is drift.

One more path may differ (#23.5): the package's recorded `formalization_yaml`
(usually `formalization.yaml`). Its `status.evidence` cites the receipt, so,
like the receipt, it cannot be inside X. It is accepted when it was already
tracked at X and, compared as YAML data, only the keys the
formalization renderer derives changed (`version`, `project.name`,
`project.license`, `automation.notes`, `status.sorry_count`,
`status.axioms`, `status.evidence`, `status.main_results`, `alignment`). A
changed kept key (a hand-authored one such as `project.description` or
`project.authors`) is drift, and the refusal names it. `claims.yaml` is
hand-authored and never exempt. A `formalization.yaml` first added after X
is drift too: render it (`lean-pkg formalization NAME --write`) and commit
it with the source before gating, then regenerate it after the gate.

With no `formalization_yaml` pointer recorded, the exception does not apply,
except under `gate NAME --receipt R --refresh-formalization` (#26.4). That
command owns the file at the pointer path, or `formalization.yaml` when no
pointer is set, and records the pointer right after the gate, so it judges
the exception for that path. The tested sequence is: on a clean checkout at
X, `gate --receipt R --refresh-formalization`; commit the regenerated file
with `.lean-receipts/` as H; then `gate --check` and `comparator --check`
both pass at H. Every kept key's text is byte-equal before and after the
refresh, as long as the file was in the renderer's own layout (the renderer
rewrites the whole file, so comments and hand formatting do not survive;
the values do).

The source closure stays byte-identical to the certified commit while the
evidence travels with the code. The rule applies to both tiers, to the
`promote` checklist, and to the `receipt-drift` / `comparator-drift`
findings of `audit`. Comparator configurations and `lean-toolchain` are read
at X, not at H, and never from the working tree. Modified tracked files in
the checkout print a WARN at `gate` and `comparator`; `--refuse-dirty` turns
the WARN into a refusal.

## Where evidence lives

Receipts and logs are copied into `<repo>/.lean-receipts/` by `gate` and
`comparator` (never committed by the tool; you commit them). The registry
stores only the relative paths; `promote` re-reads the bytes, and refuses
unless the receipts and every comparator log are committed at HEAD unmodified
and HEAD is contained in `origin/<branch>` (judged on local refs; nothing is
fetched). An
[index entry](index-catalog.md) keeps verbatim copies of the receipts (not
the logs) and `index check --clone` re-derives them at the pin.

`lean-pkg formalization` reports the receipts under `status.evidence` of the
package's `formalization.yaml` (commit, gate receipt, comparator receipt) and
lists every tracked comparator configuration under `status.main_results`; see
[formalization.yaml](formalization.md).

## Publishing a package incubated beside private work

`promote` makes the whole repository public, history included. A package
that grew next to private material (unpublished drafts, approval notes,
absolute paths of a work machine) can carry it in old commits even when the
current tree is clean. The sequence:

1. `lean-pkg public-safety NAME` scans the tree and every blob and commit
   message reachable from any ref. Private strings specific to the package
   go in an untracked `.lean-pkg-private-patterns` (one regular expression
   per line; `allow: REGEX` exempts a public string). Hits are printed
   redacted. `promote` runs the same scan and refuses while hits remain.
2. Fix the hits. A hit only in the tree is fixed with a commit, followed by
   a new gate, since the gate-bound commit changes. A hit in the history
   needs a history rewrite, or, simpler, a fresh-history export:
   `lean-pkg release NAME --fresh-repo DIR [--remote URL] --dry-run`, then
   the same without `--dry-run`. It exports the gate-bound tree (internal
   files and the patterns file dropped) into a new repository with one
   commit, scans it, and moves the registry entry there. Gate and comparator
   evidence is cleared, with a dated note keeping the old receipt paths and
   the source commit.
3. Run a new gate on the new commit: the lower-tier check, then
   `lean-pkg gate NAME --receipt R`. Receipts copied with `--keep-receipts`
   are provenance only, because a receipt binds one exact commit and the
   export commit is not a descendant of the old one.
4. Run the comparator on the new commit:
   `comparator-receipt --project DIR --prepare`, then the run, then
   `lean-pkg comparator NAME --receipt ...`.
5. `lean-pkg publish NAME --dry-run --public-safety`, push the new
   repository, and `lean-pkg promote NAME` (dry run first, then `--yes`).

Next: [The comparator tier](comparator.md).
