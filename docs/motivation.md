---
title: Motivation
---

[&larr; lean-pkg documentation](index.md)

*Mathlib holds the theorems. Palomar keeps the record. lean-pkg keeps the trust.*

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

## Formalization as a second referee

Formalization can improve a paper before it is finished. In practice, a Lean
development may reveal a hidden hypothesis, an unjustified reduction, a mismatch
between the informal theorem and the formal statement, or a dependency that the
manuscript treated as "standard" without naming it.

The useful research loop is therefore:

`manuscript → computation/conjecture → Lean formalization → discovered gap → manuscript revision → re-verification`.

The loop may stay private. `lean-pkg` already provides the commit-bound
lifecycle, append-only history, receipts, and re-certification rules needed to
track changing evidence without publishing intermediate work. Conditional
claims make the mathematical boundary itself explicit across revisions: a
claim names the assumptions it is proved under, and each is shown as
outstanding until a compared corollary discharges it
([Conditional claims](lifecycle.md#conditional-claims)).

### Discovery is provenance, not proof

Symbolic or numerical computation is often the fastest way to discover the
right formula. That is a strength, not a defect, provided the trust boundary is
clear.

A Python, SymPy, Sage, Mathematica, Julia, Maple, or notebook artifact may
suggest a candidate theorem. If Lean then proves the theorem independently, the
final verification does not rely on trusting the exploratory computation. The
computation remains valuable provenance: it explains how the claim was found,
which script and environment produced it, and which formal theorem eventually
certified it.

A future provenance record should be able to bind, for example:

- discovery tool and version;
- script/notebook path and commit or content hash;
- environment lockfile or package versions;
- candidate formula or claim produced;
- the Lean declaration that formalizes the claim;
- the certificate version that verifies it.

The rule is simple: **computation may propose; only the declared verification
boundary determines what is certified.**

### Three records, three questions

A reproducible research record should keep three channels separate:

1. **Verification evidence** answers: *why should I trust this mathematical
   claim?* It contains the Lean declaration, exact source pin, assumptions,
   receipts, comparator evidence, and verification boundary.
2. **Discovery provenance** answers: *how was this claim found?* It may record
   symbolic or numerical tools, scripts, notebooks, environments, content
   hashes, and intermediate candidate formulas.
3. **AI-assistance disclosure** answers: *where did AI participate?* It should
   identify the model/tool and its role in conjecture generation, code,
   formalization, documentation, or review.

These records may cross-reference one another, but they must not collapse into
one trust score. An AI-assisted Mathematica notebook can be excellent discovery
provenance without contributing any proof evidence; conversely, a Lean proof
can remain valid even if the exploratory notebook is unavailable.

For publication, a journal's required AI-disclosure paragraph remains the
authoritative disclosure for that venue. A `lean-pkg` provenance record can
support it with more reproducible detail; it does not replace journal policy.

### Conditional verification is still verification

End-to-end formalization is not required for a result to be useful. In fields
where library coverage is thin, a theorem may be proved in Lean conditional on
explicit mathematical inputs that have not yet been formalized.

Those inputs should normally appear as theorem hypotheses, structures, or
abstract interfaces—not as new trusted Lean axioms. That distinction keeps
`#print axioms` meaningful: a conditional theorem can still use only the
standard permitted axioms while making its mathematical assumptions visible in
the theorem statement.

A conditional certificate should distinguish:

- **theorem hypotheses**: part of the mathematical claim and not expected to
  disappear;
- **formal dependencies**: already kernel-checked Lean results;
- **external dependencies**: cited mathematical results not yet formalized;
- **project assumptions**: temporary interfaces introduced because supporting
  infrastructure is missing;
- **unverified scope**: portions of the paper for which the certificate makes no
  claim.

The honest interpretation is then: *if the declared external inputs are
mathematically valid, Lean certifies the stated consequence inside this
boundary.*

lean-pkg records this boundary for the first kind of input that matters most:
an assumption the theorem takes as an explicit argument, a named `Prop`
definition, listed as the claim's `hypotheses`. The claim is then
*conditional*, never plainly verified, on the page, in `status`, in
`receipt check` and in the index, and each outstanding assumption is named
there with the text the authors give for it
([Conditional results](page.md#conditional-results)). The other kinds above
stay prose in the coverage file's `inputs` and notes.

As the ecosystem grows, external dependencies can be replaced by formal ones.
A new certificate should supersede the old one without erasing it, so the
verification history becomes progressively stronger rather than merely
"pass/fail". For a listed assumption this is **discharge**: a corollary that
drops the assumption, compared in the same build as the theorem, moves it from
outstanding to discharged, and the page names the proof and the corollary.
This matters especially in areas such as stochastic analysis, where today's
conditional result may become tomorrow's end-to-end formalization.

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

Reservoir lists a package automatically once it is public, licensed, not a
fork, has a root manifest and 2 stars, and then rebuilds it on new
toolchains; it checks that a package builds, never what its theorems say, so
it is the place to *search before building* and the place a released package
appears, while the trust questions stay with Palomar and lean-pkg (see the
[FAQ](faq.md#reservoir)).

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
