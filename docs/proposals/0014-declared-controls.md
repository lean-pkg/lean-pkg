# 0014: Declared controls in the sealed verify log, checked by the gate

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-10.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

A package may commit a declaration of its **controls**, `lean-pkg-controls.yaml` (`controls/1`): Lean
files that must fail to compile (negative controls: a wrong statement, a removed hypothesis) and Lean files
that must compile and prove a wrong variant false (refutations). The lower-tier verify script runs every
declared control after the build, in the job, and appends a `controls/1` section to the sealed verify log,
the way the hypotheses section (`hyp-probe/1`) is appended today. The section is covered by the receipt's
`report_sha256`; the receipt schema does not change. `gate`, `gate --check` and `receipt check` read the
section at the gated commit and refuse a receipt whose package declares controls when the section is
missing, does not match the declaration, or records a failure. `page` says how many controls passed. A
package without the file is unaffected.

## Motivation

A receipt certifies the build, the `sorry` count, the axioms and, per claim, the axioms of each claim's
declaration. It says nothing about the controls some companions keep beside their proofs. A field report
(2026-11-09) describes a paper companion with 68 must-fail controls and 17 refutations, run by the
package's own script: a reader of the receipt cannot see that the controls exist, let alone that they ran
and passed at the certified commit. The results were recorded by hand in commit messages and review notes.

Controls are evidence of a different kind than the build. A must-fail control shows that a statement is
not vacuously provable in the package's setting (a theorem with a contradictory hypothesis would let a
wrong variant compile too); a refutation shows that a plausible misreading of the paper is false. Both say
something about the statements the comparator tier does not: the comparator checks that the proof proves
the Challenge statement, not that the Challenge statement excludes the wrong variants.

## Design

### The declaration: `lean-pkg-controls.yaml`

Committed in the package directory, beside `claims.yaml`:

```yaml
schema: controls/1
controls:
  - id: neg-drop-homog            # unique, [a-z0-9][a-z0-9-]*
    kind: must-fail               # the file must not compile
    file: controls/DropHomog.lean
    expect: "failed to synthesize"   # optional: a substring of the first error (see below)
    note: the bound without the homogeneity hypothesis   # optional, one line
  - id: ref-kappa-sign
    kind: refutes                 # the file must compile, with no sorry, and prove `theorem`
    file: refutations/KappaSign.lean
    theorem: Refutations.kappa_sign_wrong
    note: kappa with the opposite sign is false
```

- `kind: must-fail`: the control passes when `lean FILE` exits non-zero. With `expect`, the first
  error message must also contain that substring, so that a control does not pass for the wrong reason (a
  typo, a missing import, a renamed lemma). Without `expect` the control is recorded as `any-error`, and
  `page` says so.
- `kind: refutes`: the control passes when the file compiles with exit 0, reports no `sorry`, declares
  `theorem`, and `#print axioms theorem` lists only the standard axioms (`propext`, `Quot.sound`,
  `Classical.choice`): the rule `claim_axioms` applies to a claim's declaration.
- The file is a build input under the docs-only rule (as `claims.yaml` is): changing it, or a control file
  it names, between the gated commit and HEAD unbinds the receipt.
- Paths are relative to the package directory and must be tracked; a control file is never a module of the
  package's library (it is run alone with `lean`, after the build), so it cannot change what the library
  proves.

### The section: `controls/1` in the sealed verify log

The verify scripts (`receipt slurm --template` and the `verify.sh` that `lean-pkg new` writes) run the
controls after the per-claim axioms and the hypotheses section, before the seal, when the declaration is
tracked. Each control runs as `lean FILE` under the LEAN_PATH the script printed and checked for
shadowing, never under `lake env` (the rule the per-claim axioms program follows), with a time limit
(open question 3). The job, not Lean, writes the lines:

```text
== lean-pkg controls (controls/1) ==
ctl-decl: {"v":1,"file":"lean-pkg-controls.yaml","sha256":"<64 hex>","controls":85}
ctl: {"v":1,"id":"neg-drop-homog","kind":"must-fail","file":"controls/DropHomog.lean","sha256":"<64 hex>","exit":1,"matched":true,"ok":true}
ctl: {"v":1,"id":"ref-kappa-sign","kind":"refutes","file":"refutations/KappaSign.lean","sha256":"<64 hex>","exit":0,"sorry":false,"axioms":["propext"],"ok":true}
ctl-done: {"v":1,"ran":85,"passed":85,"failed":0}
```

- `ctl-decl` binds the section to the declaration's bytes; each `ctl` line to its file's bytes.
- `ok` is the job's verdict by the rule above; `matched` is whether `expect` matched (null without
  `expect`). On failure a `ctl` line adds `message`: the first error line with every path prefix removed,
  at most 200 characters (the `hyp-error` rule), so a redaction never alters it.
- Refused at write time (`receipt slurm`, `receipt systemd`; the receipt is not written): a section without
  `ctl-done`, counts that do not match it, an id twice, a line for a control the declaration does not list.

### The gate

When `lean-pkg-controls.yaml` is tracked at the gated commit, `gate` and `gate --check` (and `receipt
check`, which re-derives the gate's checks) require, in the sealed log the receipt binds:

1. a `controls/1` section whose `ctl-decl` `sha256` is the declaration's at the gated commit;
2. one `ctl` line per declared control, each `sha256` the control file's at that commit;
3. `ok: true` on every line, re-judged from the line's facts by the rule above (the gate does not trust
   the job's `ok` alone: `exit`, `matched`, `sorry` and `axioms` must support it).

Otherwise the gate refuses with the control ids and the reason (`controls: section missing`, `controls:
neg-drop-homog failed: exit 0 (the file compiled)`, `controls: ref-kappa-sign: axiom sorryAx`). A package
without the declaration is gated as before, and a receipt with a section but no declaration is gated as
before too (the section is ignored and `gate --check` says so).

`gate --check` prints one line, `✓ controls: 85 declared, 85 passed (68 must-fail, 17 refutes)`, and JSON
`controls {declared, ran, passed, failed[], any_error}`.

### What readers see

- `page`: one line under the verification summary, `Controls: 68 must-fail controls and 17 refutations
  passed at commit X` (with `of which N any-error`), from the gate receipt's section; nothing without it.
- `status NAME`: `controls 85/85` beside the gate line.
- The section is evidence like the axioms lines: it lives in the sealed log, never in the registry.

## Compatibility and migration

- Additive: no receipt key, no registry field. `lower-receipt/1` stays; `controls/1` is the section's
  marker (docs/formats.md).
- A package that adds the declaration needs a new lower-tier receipt from a lean-pkg that runs the
  controls, then a re-gate (`gate --regate`): an older receipt has no section, so the gate refuses it once
  the declaration is tracked. The declaration can be committed in the same commit as the new receipt run.
- An older lean-pkg ignores the declaration and the section: it gates as before. A package that declares
  controls should raise its CI pin to the release that implements this.
- The comparator tier is unchanged.

## Trust model

This **adds a refusal**: a package that declares controls cannot be gated with a receipt whose controls
did not all pass. Nothing that was refused before is accepted now.

The controls are lower-tier evidence: the verify script runs them and writes the lines, as it writes the
axioms lines, and the gate re-judges the facts but cannot re-run Lean. A dishonest script could fake the
section as it could fake the axioms; the seal, the receipt's binding and an independent re-run (`receipt
check` on another host) are the defence, as for every lower-tier fact. The gate checks that the declared
controls ran on the committed bytes, not that the controls are good ones: a must-fail control that fails
for an irrelevant reason passes unless it carries `expect`. `page` shows `any-error` controls as such.

## Alternatives

- **Run the package's own script and record its exit status.** Simplest, but the receipt would say only
  that some script exited 0; the counts, the per-control verdicts and the bytes they ran on would be the
  script's word.
- **Make controls claims in `claims.yaml`.** A claim is a result offered or used; a control is a check
  about the statements. Mixing them would put must-fail files into the claims ledger and the comparator
  coverage.
- **Check controls in the comparator tier.** The comparator judges proofs against Challenge statements; a
  must-fail control has no proof, and a refutation is not a Challenge theorem of the paper.
- **A registry field.** The controls belong to the commit, not to the record; a field would drift.

## Open questions

1. Whether `expect` should be required for `must-fail` controls (stronger, but a burden on packages with
   many controls), or optional with `any-error` shown, as proposed.
2. Whether a refutation should also be compared by the comparator tier when its negated statement is a
   Challenge theorem (likely a later proposal).
3. The time limit per control (proposed: 300 s, configurable per control with `timeout`), and whether a
   timeout is a failure (proposed: yes, `message: timeout`).
4. Whether `index check` and the catalog show the controls count for a published package.

## AI assistance

Drafted with Claude Code (Claude Opus 5.5) at Le Chen's request, from a field report by an AI agent
working on a paper companion (point 11 of its report). Le Chen reviews and decides.
