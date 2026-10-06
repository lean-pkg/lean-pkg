---
title: Motivation
---

[&larr; lean-pkg documentation](index.md)

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

## lean-pkg, Palomar, Reservoir and Mathlib

Four things exist in this space and they do different jobs. **Palomar**
(palomar-registry.org) is the public registry of record for Lean-verified
results: an entry pins one public repository commit, checks the formal
statement against the proof with the Comparator and replays the proof in
independent kernels, runs an LLM editorial review, archives every
dependency, and assigns a stable `PALOMAR-…` id. **Reservoir** is the
automatic index of public Lean packages. **Mathlib** is the general library.
lean-pkg does not compete with any of them; it is what they leave out.

| Need | Who covers it |
|---|---|
| a general theorem in canonical form | Mathlib |
| a durable, independently replayed record of a research result | Palomar |
| discovering public packages | Reservoir |
| the months before a result is public: who holds which package, what is certified at which commit, evidence that is complete before anyone sees it | **lean-pkg** (private lifecycle) |
| finding a verified result *in a field's own language*, with its hypotheses, its paper correspondence, and who already uses it | **lean-pkg** (field index, keyed to Palomar ids and library pins) |

Palomar states that an entry "fixes a reviewable snapshot of its own
statement, not a library for later submissions", and that registration is
not a certificate that the formal statement matches the informal one. Those
two sentences mark lean-pkg's territory: the index answers the first, and
the lifecycle's fidelity records answer the second. A package that passes
lean-pkg's comparator tier uses the Palomar layout and is already in the
shape Palomar accepts.

### Mathlib in particular


Mathlib is a *library* of mathematics; `lean-pkg` is *infrastructure* around
libraries. They are different kinds of object, and every `lean-pkg` package
depends on Mathlib rather than competing with it.

| | Mathlib | lean-pkg |
|---|---|---|
| Kind | one monorepo of theorems and definitions, general-purpose, curated to a uniform standard | a registry, a trust gate, and (planned) a catalog; it contains no mathematics |
| Unit | a declaration inside one shared library | a *package*: a standalone Lean repository, typically a paper's companion, pinned to a Mathlib revision |
| Question answered | "is this standard result available, in canonical general form?" | "which packages exist, who holds each, which commit is certified, and what does the evidence prove?" |
| Acceptance | human review for generality, integration, maintainability, and style, with the kernel check as a floor; one kernel | receipts re-derived from bytes: build and axioms at a pinned commit, plus statement equality between trusted and untrusted modules replayed in an independent kernel |
| Pace | moves daily; a long review queue; results must be general enough to belong forever | a package moves at research speed on its own pin and is certified at one commit |
| Papers | no notion of a claim outside the library | first-class: the trusted statement module *is* the paper's claim, and the comparator checks that the proved theorem is that statement |

Why it lives outside Mathlib:

1. **Mathlib does not host registries or gates**, and its own contributing
   guidance recommends that fast-moving research live in standalone
   repositories that depend on Mathlib and are indexed by Reservoir.
   `lean-pkg` manages exactly those repositories.
2. **Research-frontier results do not belong in a general library yet.** They
   are too specialized or too recent, and may stay that way for years. They
   still need to be certified, found, imported, and cited now.
3. **The gate checks what a library's CI cannot.** Mathlib's CI proves the
   library compiles inside one kernel. It cannot say whether a package's
   theorem matches a paper's claim, because Mathlib has no notion of a claim
   outside itself. The comparator tier exists for that single question.
4. **Coordination is not a library's problem.** Leases, many machines,
   coding agents claiming work, one-way publication: a research group has a
   dozen companions in flight at once.
5. **Pins versus a moving target.** A receipt names a commit and a Mathlib
   revision. Upgrading the pin is a separate step with a fresh receipt.

What `lean-pkg` is not: a competing library, a fork of Mathlib, or a place
where mathematics lives. The registry's `upstream_target: mathlib` state and
the AI-disclosure requirement exist because the intended end of a general
result is a Mathlib pull request. A result moving from a package into
Mathlib is a success for both.

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
