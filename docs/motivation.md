---
title: Motivation
---

# Motivation

## The gap between "it compiles" and "it is verified"

A Lean file that builds with no `sorry` is a strong signal, but it is not the
same as "the paper's theorem is verified". Three failure modes survive a
green build, and all three have bitten real formalization projects:

1. **Statement drift.** The compiled theorem differs from the claimed one.
   A hypothesis quietly moved into a definition; a quantifier order changed;
   a normalization constant was absorbed; an `L^p` norm became a moment. In
   analysis and probability these defects are the common case, not the
   exception, because the objects are built from many layered definitions.
2. **Same-kernel trust.** Every check (`lake build`, `#print axioms`) runs
   inside the kernel that elaborated the proof. A single trusted component
   checks itself.
3. **Status words instead of evidence.** "CI was green", "verified last
   month", "the receipt is in the repo somewhere". None of these can be
   re-derived by a third party from bytes.

`lean-pkg` addresses each one with a mechanism rather than a policy:

| Failure mode | Mechanism |
|---|---|
| statement drift | the comparator tier: trusted `Challenge.lean` statements, untrusted `Solution.lean` proofs, statement equality checked by `leanprover/comparator` ([Palomar](https://github.com/PalomarRegistry/PalomarSubmission) layout) |
| same-kernel trust | every proof replayed in the independent `nanoda` kernel inside a `landrun` sandbox |
| status words | receipts bound to a commit, a toolchain, tool pins, file hashes, and a PASS line; `promote` re-derives the PASS from bytes |

## Coordination is the other half

Research formalization is rarely one repository. It is a dozen companions
for a dozen papers, on several machines, worked on by people and by coding
agents. Without a registry, nobody can answer "which packages exist, in what
state, held by whom, verified against which commit" without reading a dozen
`README`s. `lean-pkg` keeps one append-only YAML registry with:

- a status enum with explicit transitions and the evidence each requires;
- compare-and-swap leases so one package lives in one workspace at a time;
- an event log in which every transition is a line that is never rewritten;
- a one-way promotion that is dry-run by default and refuses on any
  checklist failure.

## Where this is going

The tool is the first layer. The intended second layer is a **catalog of
verified results** for areas where the formalization frontier is thin, such
as stochastic analysis and random matrix theory: a place a person or an
agent checks first to ask "has this already been proved in Lean?", with
entries that state the result, its hypotheses, its paper correspondence,
and the receipts behind it, importable under a stable, citable identifier.
The acceptance bar for that catalog is exactly the comparator receipt that
this tool already enforces. The catalog does not compete with Mathlib; it
is a curated layer above it, and results that generalize are meant to be
upstreamed.

## Design principles

- **Validator, not executor.** `lean-pkg` never runs Lean. It validates
  evidence that an executor (a Slurm job, a CI run, a workstation) produced,
  so the gate is the same on every host.
- **Fail closed, in bytes.** Every refusal names the field and the value.
  Hashes are compared against the repository at the certified commit, not
  the working tree.
- **Append-only history.** Repairs are new events, never edits.
- **Non-interactive.** No code path blocks on a prompt off a terminal;
  `--json` exists for every subcommand. Agents are first-class users.
- **One-way steps are dry-run by default.** `promote` prints the checklist;
  `--yes` is the only way to flip.

Next: [Getting started](getting-started.md).
