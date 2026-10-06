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
| `lean_toolchain` | equals the repo's `lean-toolchain`, stripped |
| `mathlib_rev` | nonempty |
| `sorry_count` | integer, must be 0 |
| `axioms` | list of strings, subset of `gate.axioms_allowed` (default `propext`, `Quot.sound`, `Classical.choice`) |
| `report_sha256` | 64 hex characters: the frozen verification report |
| `job_id`, `checker`, `generated_at` | nonempty: the executor's terminal job identity, the exact checker mode, an ISO-8601 time |

The frozen report named by `report_sha256` is expected to bind the candidate
commit or tree, the toolchain and dependency pins, complete checker
coverage, the terminal state, and executor identity. A pilot run, a partial
manifest, or a receipt for a previous candidate is not gate evidence.

## Comparator receipt schema (`comparator-receipt/1`)

| Field | Rule |
|---|---|
| `schema` | `"comparator-receipt/1"` |
| `commit` | equals the lower-tier receipt commit; HEAD, or HEAD is an evidence-only descendant |
| `lean_toolchain` | equals the repo's `lean-toolchain` |
| `tools` | `comparator`, `lean4export`, `nanoda`, `landrun`: 40-hex commits actually used |
| `host`, `job_id`, `generated_at` | nonempty |
| `host_policy` | the runner's host-policy reason (an override run is visible here) |
| `result` | `"pass"` (the runner writes a receipt only on PASS) |
| `configs[]` | exactly the `comparator*.json` / `comparator/config*.json` files tracked at the bound commit, no omissions, no extras. Each entry: `path`; `sha256` of the file's bytes at the bound commit; `challenge_module`, `solution_module`, `theorem_names` (non-empty), `definition_names`, `permitted_axioms` (each equal to the file); `log` = `.lean-receipts/<commit12>-comparator-<cfgname>.log` where `cfgname` is the path without `.json` and with `/` replaced by `_`; `log_sha256`; `result: "pass"`. The log must contain the full line `Your solution is okay!`. Symlinked evidence is refused |

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

The source closure stays byte-identical to the certified commit while the
evidence travels with the code. The rule applies to both tiers, to the
`promote` checklist, and to the `receipt-drift` / `comparator-drift`
findings of `audit`. Comparator configurations are read at X, not at H.

## Where evidence lives

Receipts and logs are copied into `<repo>/.lean-receipts/` by `gate` and
`comparator` (never committed by the tool; you commit them). The registry
stores only the relative paths; `promote` re-reads the bytes.

Next: [The comparator tier](comparator.md).
