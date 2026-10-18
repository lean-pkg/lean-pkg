# 0005: Per-claim axioms read from the built environment

Status: accepted. Author: Claude, the lean-pkg operations agent, drafting for Le Chen. Created: 2026-10-17.
Decided: accepted 2026-10-18 by Le Chen, with the answers recorded under Open questions. Implemented in:
(release).

## Summary

The lower-tier receipt gains an optional, additive key `claim_axioms`
`{checked, nonstandard: [{decl, axioms}], missing}`: the axioms of **every** claim in
`claims.yaml`, read by core Lean's `collectAxioms` from the built environment after the build, instead
of only the `#print axioms` text for the theorems an author chose to print. When the key is present,
`receipt slurm|systemd`, `gate`, `receipt check` and the page read it and refuse a missing claim, a claim
on `sorryAx`, an unchecked claim, a disagreement with `#print axioms`, or an axiom outside
`gate.axioms_allowed`. A receipt without it is valid exactly as before. The implementation pull request
is the one that adds this file.

## Motivation

The receipt's `axioms` field is the union of the `#print axioms` output in the verify log. That output
covers the declarations the package's `Axioms.lean` lists, which the author writes by hand. A claim the
author did not list was never checked: a reusable theorem resting on `sorryAx` from a dependency, or on a
custom `axiom`, could sit in `claims.yaml` while the receipt said `axioms: [propext]`. The Equational
Theories Project closes the same gap inside Lean: its `@[equational_result]` attribute runs
`collectAxioms` on every counted result and refuses anything outside the standard three (a study of that project,
2026-10-17). lean-pkg cannot ask every package to depend on a lean-pkg attribute (decision 0001,
files and git only), but it can run the same check from outside, after the build.

## Design

- `src/lean_pkg/scripts/ClaimAxioms.lean` (core Lean, no Mathlib; checked on v4.35.0 and v4.35.0-rc2):
  `LEAN_PATH=... lean --run ClaimAxioms.lean MOD... -- NAME...` (the LEAN_PATH the script printed with `lake env printenv LEAN_PATH`, which the template checks for shadowing; never under `lake env`) imports the modules (data only,
  `loadExts := false`: no axiom list cached in an `.olean` is read, every body is walked) and prints
  `claim-axioms: NAME AX1,AX2` (sorted, `none` for no axiom), `claim-missing: NAME`, and a final
  `claim-axioms-done: N`.
- The verify scripts (`receipt slurm --template`, `lean-pkg new`'s `verify.sh`) embed the program and a
  block-style `claims.yaml` reader (`claim_axioms.job_sh()`), and run it after the build, before the
  key lines and the seal. The lines are in the sealed log.
- `receipt slurm|systemd` (and `--check`) judge the lines against `claims.yaml` committed at the
  commit (`claim_axioms.judge`): every claim of any kind and status must have a line; a missing claim,
  `sorryAx`, an incomplete probe, a malformed or duplicated line, or a `#print axioms` result for the same
  name that names other axioms refuses; a redaction that alters a line refuses. Otherwise the receipt
  gains `claim_axioms`. Example:

  ```json
  "claim_axioms": {"checked": 3, "nonstandard": [], "missing": []}
  ```

- `gate` (`cli.validate_receipt`) refuses a malformed key, a non-empty `missing`, `sorryAx`, or an axiom
  under `nonstandard` outside `gate.axioms_allowed`. `gate --check` prints a `✓ claim_axioms: ...` line.
- `receipt check` re-derives the key from the committed sealed log and `claims.yaml` at X and fails when
  they differ. The page adds one bullet under *How this was checked*.
- `python3 -m lean_pkg.claim_axioms --project DIR` runs the probe locally (bwrap sandbox, as
  `dup_constants`), for an author before a job.

No command, flag or `--json` key is removed or renamed. No schema version changes: the key is additive,
and `RECEIPT_FIELDS` is unchanged.

## Compatibility and migration

- A receipt without the key (every receipt written before this, and any job whose script lacks the
  probe, or a package without `claims.yaml`) validates exactly as before. An older lean-pkg ignores the
  key (keys beyond `RECEIPT_FIELDS` are ignored).
- No migration. To gain the key, regenerate `verify.sbatch` with `lean-pkg receipt slurm --template`, or
  copy the new `verify.sh` block from `lean-pkg new`; the next receipt carries it.
- A package whose claims use a bare or partial `decl` (`foo` for `Demo.foo`) gets `claim-missing` for
  it, so a new receipt from the new script is refused until the decl is fully qualified. That is the
  intended fail-closed behaviour; the upgrade record says so.
- Goldens: none (no help text or command changes).

## Trust model

Yes, this changes what a receipt with the key proves and what `gate` refuses. It only adds refusals:
a receipt `gate` accepted before is still accepted (it has no key), and a receipt with the key is accepted
only if, in addition to everything before, every claim's axioms are standard or allowed. What a dishonest
submitter could get accepted that they could not before: nothing new; the key rests on the same executor
and the same sealed log as `axioms`. What it does not stop: an executor who writes the lines by hand (as
for every log line), or who drops the probe and submits a receipt without the key (the page then shows no
per-claim line; `receipt check` shows no claim-axioms check). Pinning tests: `tests/test_claim_axioms.py`
(a real core-Lean fixture with a clean, a sorried, a custom-axiom and a missing claim, in the bwrap
sandbox; an edited line breaking the seal; a tampered key refused by `gate` and `receipt check`; an old
receipt still valid).

## Alternatives

- Do nothing: the axioms of unprinted claims stay unchecked.
- A Lean attribute in a lean-pkg library, as ET does: every package would depend on lean-pkg's Lean
  code (decision 0001 argues against it).
- Generate `Axioms.lean` from `claims.yaml`: still parses pretty-printed text, and an author can edit the
  generated file.
- Wait for `lake check` (v4.35.0) to emit a machine-readable report: it refuses non-standard axioms for
  the whole build, not per claim, and has no report to bind yet (trust model, *Positioning*). When it
  has one, this key may become redundant.
- Require the key in every new receipt: rejected for now, so receipts from existing job scripts keep
  working; a later proposal may require it.

## Open questions

1. Should `gate` eventually require `claim_axioms` for a package with `claims.yaml` (a schema change)?
   **Decided (2026-10-18): no, not yet.** The key stays optional until 1.0, when it is revisited.
2. Should superseded claims be exempt? Today every claim is checked; `claims check` already requires a
   superseded claim's decl to stay in its file. **Decided (2026-10-18): no.** Every claim is checked,
   superseded ones included.
   Also decided: a bare or partial claim name refuses the receipt (fail closed); the 0.14.0 upgrade record
   tells authors to qualify the names in `claims.yaml` before adopting the new verify script.
3. Should the comparator tier record the same per-claim list from its export?

## AI assistance

Drafted and implemented by Claude (Anthropic), the lean-pkg operations agent, from item 1 of a study of
the Equational Theories Project that Le Chen approved; to be reviewed and decided by the maintainer.
