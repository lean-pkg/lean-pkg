# 0014: Declared controls in the sealed verify log, checked by the gate

Status: draft. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-10.
Revised: 2026-11-10, after a field review. Decided: (date and maintainer, once accepted or declined).
Implemented in: (release).

## Summary

A package may commit a declaration of its **controls**, `lean-pkg-controls.yaml` (`controls/1`), in three
kinds: Lean files that must fail to compile for a mathematical reason (`must-fail`: a wrong statement, a
removed hypothesis), files that must compile and prove a wrong variant false (`refutes`), and files that
must simply compile cleanly (`compiles`: audit printouts, a consumer example). The lower-tier verify script
runs every declared control after the build and appends a `controls/1` section to the sealed verify log,
the way the hypotheses section (`hyp-probe/1`) is appended today. The section is covered by the receipt's
`report_sha256`; the receipt schema does not change. `gate`, `gate --check` and `receipt check` re-judge
the section at the gated commit and refuse a receipt whose package declares controls when the section is
missing, does not match the declaration, or records a failure. `page`, `status` and the catalog show the
counts, never the files. A package without the declaration is unaffected.

## Motivation

A receipt certifies the build, the `sorry` count, the axioms and, per claim, the axioms of each claim's
declaration. It says nothing about the controls some companions keep beside their proofs. A field report
(2026-11-09) describes a paper companion with 68 must-fail controls, 30 refutations and 26 audit files,
run by the package's own script: a reader of the receipt cannot see that the controls exist, let alone
that they ran and passed at the certified commit. The results were recorded by hand in commit messages
and review notes.

Controls are evidence of a different kind than the build. A must-fail control shows that a statement is
not vacuously provable in the package's setting (a theorem with a contradictory hypothesis would let a
wrong variant compile too); a refutation shows that a plausible misreading of the paper is false. Both say
something about the statements the comparator tier does not: the comparator checks that the proof proves
the Challenge statement, not that the Challenge statement excludes the wrong variants.

The same field report shows how controls pass for the wrong reason, which this design guards against: a
must-fail file that fails on a typo or a renamed lemma; a refutation that restates the package's definition
locally and so refutes a copy (0 of that companion's 30 refutations imported the package); a control that
states bare numerals where the package's declaration should appear (9 of its 68).

## Design

### The declaration: `lean-pkg-controls.yaml`

Committed in the package directory, beside `claims.yaml`:

```yaml
schema: controls/1
controls:
  - id: neg-drop-homog            # unique, [a-z0-9][a-z0-9-]*
    kind: must-fail               # the file must not compile, for a non-setup reason
    file: controls/DropHomog.lean
    expect: "⊢ False"             # optional: a substring of the first error message (see below)
    targets: [Paper.second_moment_bound]   # optional: package declarations the file must use
    note: the bound without the homogeneity hypothesis   # optional, one line
  - id: ref-kappa-sign
    kind: refutes                 # must compile and prove `theorem`, a negation
    file: refutations/KappaSign.lean
    theorem: Refutations.kappa_sign_wrong
    targets: [Paper.kappa]
    note: kappa with the opposite sign is false
  - id: audit-main
    kind: compiles                # must compile: no sorry, standard axioms
    file: Audit/Main.lean
```

- **`must-fail`**: the control passes when `lean FILE` exits non-zero and its **first error message** is not
  a setup error. The first error message is the text from the first `error:` to the next `error:` or
  `warning:` or the end of the output, over all its lines: Lean often puts the generic line first
  (`unsolved goals`, ``Tactic `decide` proved that the proposition``) and the mathematical evidence (`⊢
  False`, `is false`) on the following lines of the same message.
  - **Setup errors** are a fixed list lean-pkg ships, matched anywhere in that first message: `unknown
    identifier`, `unknown tactic`, `unknown constant`, `unknown namespace`, `unknown module`, `could not
    resolve`, `object file ... does not exist`, `maximum recursion`, `deterministic timeout`. A control
    that fails on one of them is a failed control (the file is broken, not refuted), with the matched
    entry as its reason. The list grows only through the upgrade record.
  - With `expect`, the first error message must also contain that substring.
  - Without `expect`, the control is recorded as `any-error`: it failed for a non-setup reason that the
    declaration does not name. `page` shows how many controls are `any-error`.
- **`refutes`**: the control passes when the file compiles with exit 0, reports no `sorry`, declares
  `theorem` (required: an `example` cannot be checked), the type of `theorem` is a negation (`¬ P`, or a
  Π-type whose conclusion is `False`), and `#print axioms theorem` lists only the standard axioms
  (`propext`, `Quot.sound`, `Classical.choice`): the rule `claim_axioms` applies to a claim's declaration.
- **`compiles`**: the control passes when the file compiles with exit 0, reports no `sorry`, and every
  declaration it adds uses only the standard axioms. For audit printouts (`#print`, `#check`) and consumer
  examples.
- **`targets`** (optional, every kind): package declarations the file must use. Each must resolve in the
  file's environment to a declaration of the package's own library (the module that declares it is one of
  the package's modules), and for `refutes` and `compiles` must occur in a declaration the file adds (for
  `must-fail`, in the elaborated statement the error is about, when Lean got that far; otherwise the
  imports must bring it in). A control without `targets` passes on its own facts, and `page` counts it as
  **naming no package declaration**, as it counts `any-error`. A refutation of a local copy of a
  definition is then visible as such, not presented as a refutation of the package.
- **`timeout`** (optional, seconds; default 300): the per-control time limit. A timeout is a failure
  (`message: timeout`).
- A refutation that is false only through a junk value of a library (Mathlib's `Γ 0 = 0`, `x / 0 = 0`) must
  say so in its `note`; docs/formats.md states this rule. No key records it.
- The declaration and every file it names are build inputs under the docs-only rule (as `claims.yaml` is):
  changing them between the gated commit and HEAD unbinds the receipt.
- Paths are relative to the package directory and must be tracked. A control file is never a module of the
  package's library (it is run alone with `lean`, after the build), so it cannot change what the library
  proves.

### The section: `controls/1` in the sealed verify log

The verify scripts (`receipt slurm --template` and the `verify.sh` that `lean-pkg new` writes) run the
controls after the per-claim axioms and the hypotheses section, before the seal, when the declaration is
tracked. Each control runs as `lean FILE` under the LEAN_PATH the script printed and checked for
shadowing, never under `lake env` (the rule the per-claim axioms program follows), **one at a time** (a
field measurement: about 3 s and up to about 3 GB per control with Mathlib imported, so sequential runs fit
the default 8 GiB unit cap and parallel ones would not). A file that begins with `module` must run there
as it runs under `lake env lean`; the implementation tests this before the format is fixed (open question
4). The job, not Lean, writes the lines:

```text
== lean-pkg controls (controls/1) ==
ctl-decl: {"v":1,"file":"lean-pkg-controls.yaml","sha256":"<64 hex>","controls":124}
ctl: {"v":1,"id":"neg-drop-homog","kind":"must-fail","file":"controls/DropHomog.lean","sha256":"<64 hex>","exit":1,"setup_error":null,"expect":"matched","targets":{"Paper.second_moment_bound":true},"seconds":3.1,"ok":true}
ctl: {"v":1,"id":"ref-kappa-sign","kind":"refutes","file":"refutations/KappaSign.lean","sha256":"<64 hex>","exit":0,"sorry":false,"theorem":"Refutations.kappa_sign_wrong","negation":true,"axioms":["propext"],"targets":{"Paper.kappa":true},"seconds":2.8,"ok":true}
ctl: {"v":1,"id":"audit-main","kind":"compiles","file":"Audit/Main.lean","sha256":"<64 hex>","exit":0,"sorry":false,"axioms":["propext","Quot.sound"],"targets":null,"seconds":3.4,"ok":true}
ctl-done: {"v":1,"ran":124,"passed":124,"failed":0,"seconds":346.0}
```

- `ctl-decl` binds the section to the declaration's bytes; each `ctl` line to its file's bytes.
- `setup_error` is the matched setup-error entry or null; `expect` is `matched`, `not-matched` or `absent`;
  `targets` maps each declared target to whether it resolved and occurs as required (null without
  `targets`); `negation` is whether the theorem's type is a negation.
- On failure a `ctl` line adds `message`: the first error message with every path prefix removed, at most
  400 characters (the `hyp-error` rule, longer because the evidence is often on its later lines), so a
  redaction never alters it.
- `seconds` per control and in total; `ctl-done.seconds` is the section's whole time.
- Refused at write time (`receipt slurm`, `receipt systemd`; the receipt is not written): a section without
  `ctl-done`, counts that do not match it, an id twice, a line for a control the declaration does not list.

### The gate

When `lean-pkg-controls.yaml` is tracked at the gated commit, `gate` and `gate --check` (and `receipt
check`, which re-derives the gate's checks) require, in the sealed log the receipt binds:

1. a `controls/1` section whose `ctl-decl` `sha256` is the declaration's at the gated commit;
2. one `ctl` line per declared control, each `sha256` the control file's at that commit;
3. every control passed, **re-judged from the line's facts** by the rules above (the gate does not trust
   the job's `ok`: `exit`, `setup_error`, `expect`, `negation`, `sorry`, `axioms` and `targets` must
   support it).

Otherwise the gate refuses with the control ids and the reason (`controls: section missing`, `controls:
neg-drop-homog failed: exit 0 (the file compiled)`, `controls: neg-x failed: setup error 'unknown
identifier'`, `controls: ref-kappa-sign: theorem type is not a negation`, `controls: ref-y: target
Paper.kappa not used`). A package without the declaration is gated as before; a receipt with a section but
no declaration is gated as before too (the section is ignored and `gate --check` says so).

`gate --check` prints one line with the counts and the section's time, so a growing section is seen before
it reaches the unit's runtime cap:

```text
✓ controls: 124 declared, 124 passed (68 must-fail, 30 refutes, 26 compiles); 0 any-error, 0 naming no package declaration; 5m46s
```

JSON `controls {declared, passed, failed[], by_kind {must-fail, refutes, compiles}, any_error,
no_target, seconds}`.

### What readers see

- `page`: one line under the verification summary, from the gate receipt's section: `Controls: 68
  must-fail, 30 refutations and 26 compile checks passed at commit X`, then, when non-zero, `of which N
  failed with an error the control does not name` and `N name no package declaration`.
- `status NAME`: `controls 124/124` beside the gate line.
- `index check` and the catalog: the same counts for a published package, never the files.
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

This **adds refusals**: a package that declares controls cannot be gated with a receipt whose controls did
not all pass, and a control cannot pass on a setup error, on a refutation whose theorem is not a
negation, or without the targets it declares. Nothing that was refused before is accepted now.

The controls are lower-tier evidence: the verify script runs them and writes the lines, as it writes the
axioms lines, and the gate re-judges the facts but cannot re-run Lean. A dishonest script could fake the
section as it could fake the axioms; the seal, the receipt's binding and an independent re-run (`receipt
check` on another host) are the defence, as for every lower-tier fact. The gate checks that the declared
controls ran on the committed bytes and passed for the declared reasons, not that they are good controls:
an `any-error` control, or one naming no package declaration, passes, and `page` and the catalog show how
many there are.

## Alternatives

- **Run the package's own script and record its exit status.** Simplest, but the receipt would say only
  that some script exited 0; the counts, the per-control verdicts and the bytes they ran on would be the
  script's word.
- **Make controls claims in `claims.yaml`.** A claim is a result offered or used; a control is a check
  about the statements. Mixing them would put must-fail files into the claims ledger and the comparator
  coverage.
- **Check controls in the comparator tier.** The comparator judges proofs against Challenge statements; a
  must-fail control has no proof, and a refutation is not a Challenge theorem of the paper (open question 1).
- **Require `expect` on every must-fail control.** Stronger, but a burden on packages with many controls;
  the setup-error list catches the common wrong reasons without it, and `page` shows the `any-error` count.
- **Match `expect` against the first error line.** Simpler, but it would miss the evidence in most real
  controls, which Lean prints on the later lines of the message.
- **A registry field.** The controls belong to the commit, not to the record; a field would drift.

## Open questions

Answered by the field review and folded in above: `expect` stays optional, with the setup-error list
(was 1); comparing refutations in the comparator tier is a later proposal, `targets` taking most of its
value now (was 2); 300 s per control by default, a timeout is a failure, and `gate --check` shows the
section's total time (was 3); the catalog shows counts, never files (was 4). Still open:

1. Whether a refutation whose negated statement is a Challenge theorem should also be compared by the
   comparator tier (a later proposal).
2. Whether the setup-error list should be extendable per package (proposed: no; it grows only through
   lean-pkg's upgrade record, so every package means the same by `any-error`).
3. How `targets` is checked for a `must-fail` control whose error comes before the target is elaborated
   (proposed: the target must then be in the file's environment through its imports).
4. Whether a `module`-headed control runs identically under `lean` with the checked LEAN_PATH and under
   `lake env lean` (to be tested in the implementation; if not, such files run under `lake env` with the
   same shadowing check).

## AI assistance

Drafted and revised with Claude Code (Claude Opus 5.5) at Le Chen's request, from a field report and a
field review by an AI agent working on a paper companion (points 11 and the review of this draft, measured
on its 68 must-fail controls, 30 refutations and 26 audit files). Le Chen reviews and decides.
