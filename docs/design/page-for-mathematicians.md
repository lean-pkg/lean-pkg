---
title: The verification page, for mathematicians
---

# The verification page, for mathematicians

Status: design, 2026-10-17. Implemented here: the layout, the plain wording
and the Lean statement on every result (`page.py`, `page_html.py`,
`lean_scan.statement`); then the `paper.statement` field in `coverage.yaml`
([below](#the-paper-statement-a-new-field)), a schema change, after
[proposal 0001](../proposals/0001-paper-statement.md) was accepted.

The verification page used to read like a lean-pkg report: a status capsule,
a table keyed by correspondence class, a "Trust boundary" section about
"lower-tier gate receipts", an evidence table. A mathematician opening a
companion README wants to know three things, in this order: which results are
proved, what exactly the Lean statement says next to the paper's, and how far
to trust the machine. The redesign puts the mathematics first and the
receipts last.

## What Palomar does

[Palomar](https://palomar-registry.org/) is the public registry of record for
Lean-verified results; its entry pages are the model. Studied on 2026-10-17:
the entries
[PALOMAR-2026-10-17-000009 v1](https://palomar-registry.org/entry?id=PALOMAR-2026-10-17-000009&version=1)
and
[PALOMAR-2026-09-01-000005 v2](https://palomar-registry.org/entry?id=PALOMAR-2026-09-01-000005&version=2),
three more entry records as JSON (`https://data.palomar-registry.org/entries/<ID>-vN.json`),
the [about](https://palomar-registry.org/about) and
[how to submit](https://palomar-registry.org/how-to-submit) pages, the
[record schema](https://data.palomar-registry.org/schema-v3.json) and the
author's template
[`formalization.yaml`](https://github.com/PalomarRegistry/PalomarTemplate/blob/main/formalization.yaml).

There is no separate sign-off document. Each entry has one page, and the
author's own document is the repository's `formalization.yaml` (scope,
fidelity, review status, alignment of source statements to Lean names), most
of which the page does not display. The only sign-off act is the submitter's:
after reading the private review, they choose to register or withdraw ("Submit
only if you are a responsible author or maintainer of the substantive
formalization, or have approval from one").

An entry page, top to bottom:

1. Identity: the entry id and version, a badge ("Statement dependencies:
   Mathlib only"), the title, the abstract (the informal account, in prose),
   the authors, the references.
2. **Named compared declarations**: the formal statement, an embedded,
   hyperlinked rendering of `Challenge.lean` (types on hover), with the note
   "The formatted view shows only the declarations named in this entry's
   comparator configuration. Comparator also checks the declarations used by
   their types." Then the libraries the statement imports and the module
   docstring of the statement file.
3. **What was checked**: two sentences, then a definition list of details.
   "Mechanical assurance: Comparator checked that the recorded Solution.lean
   proves the recorded formal Challenge.lean under the listed axiom and
   dependency rules. The exported proof was checked successfully by Lean's
   kernel and by the independent kernels nanoda, con-ron." and "Automated
   review: an AI-mediated review checked whether that formal Challenge.lean
   matches the informal mathematical claim under the recorded policy. This is
   not human peer review or a novelty certificate." Theorems checked and
   permitted axioms are rows of the list.
4. **Depends only on Mathlib** (the statement's imports), **Verified proof**
   (a link to `Solution.lean` and its SHA-256, not the proof text),
   **Mathematical origin**, **Subject classification**, **Cite this**
   (BibTeX), **Automated review** (an outcome badge, no scores),
   **Versions** ("Every version is an immutable registered snapshot").

Hashes, commits, the toolchain and the CI run all sit inside the "What was
checked" details list, after the statement. Definitions are not listed (the
records studied have none). Hypotheses appear only inside the Lean statement.
Mathematics is plain Unicode; there is no MathJax or KaTeX. The typography is
plain: a serif body (Georgia) at 16px on a 64rem measure, small uppercase
sans labels above each heading, monospace code, a dark mode. An entry runs
from about 450 to 1,500 words. The trust boundary is said once, in plain
words, and the about page repeats it: "Palomar asserts nothing beyond these
three checks", "Palomar does not verify informal proofs".

## The layout

Both kinds of page (a paper's companion and a library) have the same
sections; a library has *Main results* where a paper has its results and *Not
formalized*.

| Section | Content |
|---|---|
| Verification at a glance | **How to read this page**, one fixed line: "Lean has checked every step of the proofs listed here. It cannot check that a Lean statement says what the paper says: that comparison is yours. For each result below, read the paper's statement beside the Lean statement, and check that the hypotheses match." Then the authors' summary sentence, the paper, and the counts in words |
| The paper's results in Lean / Main results | one block per formalized result, headed by number and title: **Verified** or **Not yet verified** (tied to the claims ledger, as before); **In the paper**; **In Lean**, the declaration linked at the checked commit and its statement in a `lean` code block; **Hypotheses**, the paper's own or the paper's plus the named inputs; **Definitions checked by a person**, each definition hole with who signed it off and when; **Axioms**; **Note** |
| Not formalized | as before |
| What the checks establish | three plain bullets: every proof is checked by Lean (no `sorry`, these axioms); the statements shown are the ones proved (the comparator, and independent kernels); what no tool checks |
| How this was checked | the claims ledger and its digest; the build check and the statement comparison with their commits; *Receipts* (every receipt, its check, commit, date, job and SHA-256); *Reproduce* (toolchain, Mathlib, commands, the expected `#print axioms`) |
| Citation, License, Acknowledgments | as before |

The Lean statement is the statement the reader must compare. When the
comparator compared the claim, it is read from the configuration's Challenge
module (what the proof was checked against); otherwise from the claim's own
file. It is copied from the source at the gate-bound commit, from the keyword
to the `:=` that starts the proof, by `lean_scan.statement`: the first `:=`
outside brackets that no `let`, `letI`, `have` or `haveI` of the statement owns
as its value. That function is a scanner, not a parser, so it refuses whenever
it is unsure (the name not declared exactly once, no such `:=`, a `by`, `calc`
or `do` outside brackets before the proof, more than 40 lines), and the page
then links the file and says so. It never shows a guess or a prefix.

The internal vocabulary is gone from every output: no "lower-tier", "evidence-only
descendant", rule numbers ("R11"), stage names ("promote stage"), class
tokens (`same-assumptions`) or "Tier". A test holds the four outputs (README
block, `verification.md`, `coverage.md`, the HTML file) to that list. The
receipt table's column is *Check*, with the values *build* and *statement
comparison*.

The HTML file keeps its contract (no script, no external asset) and takes
Palomar's typography: a serif text face, a 48rem measure, italic result
headings, the Lean and TeX statements in marked code blocks.

## Where lean-pkg differs from Palomar, on purpose

- **Per result, not per entry.** A Palomar entry is one result, so its page
  shows one statement. A companion carries a paper's numbered results, some
  formal, some with supplied inputs, some not formalized, so the page has one
  block per result and an explicit *Not formalized* list. Palomar has no
  counterpart of *Not formalized*; for a paper it is the honest boundary.
- **Hypotheses named in words.** Palomar leaves hypotheses inside the Lean
  statement. lean-pkg says, per result, whether the Lean statement uses the
  paper's own hypotheses or adds named inputs: the most common way a
  formalization can say less than the paper.
- **Definitions shown.** Comparator compares a definition hole by name and
  type only. Palomar does not list them; lean-pkg names each one with the
  person who signed it off, because the reader must trust that person.
- **Plain text, not a rendering.** Palomar embeds a hyperlinked Verso
  rendering of `Challenge.lean`. lean-pkg copies the source text into a code
  block: it must render the same on GitHub, in MkDocs and in a script-free
  HTML file, and it must pass the public-safety scan. A link to the file at
  the checked commit stands in for the hyperlinks.
- **No review section.** Palomar records an AI-mediated statement review.
  lean-pkg runs none and says no tool checks fidelity; the reader is the
  reviewer.
- **Receipts in the open.** Palomar keeps its evidence on its own servers
  and shows a details list. A companion is self-hosted, so the receipts, their
  hashes and the commands to reproduce stay on the page, last.
- **Not a registration.** The page certifies nothing by itself and carries
  no registry status; a Palomar entry is an immutable registered snapshot. A
  companion may also become a Palomar entry (`palomar prepare`).

## The paper statement: a new field

The **In the paper** line today points at the result's number. Le's
direction, and Palomar's abstract, call for the paper's own words beside the
Lean statement. `coverage.yaml` does not hold them, so this is a schema change
to `coverage/1`, made after [proposal 0001](../proposals/0001-paper-statement.md)
was accepted (2026-10-17). The design:

- `rows[].paper.statement` (paper) and `rows[].statement` (library, shown as
  **In words**): optional, TeX, at most 2000 characters, several lines
  allowed; no line may start a code fence, and the README block's markers
  (`lean-pkg:page:`) may not appear; the placeholder rule applies. An
  older lean-pkg refuses the key, as with `eprint_sha256`.
- Rendered as source in a `latex` code block in every output: inert (no data
  can make Markdown or HTML), and exactly the authors' transcription.
- Not checked against the manuscript. A later step could compare it with the
  labelled environment in the anchored e-print (`formalization anchor` keeps
  its SHA-256 and `paper.label` names the environment).

## Typeset mathematics

Rendering the TeX (MathJax or KaTeX) needs a script. The HTML file promises
none, and GitHub's own `$...$` support in a README would have to receive the
TeX unescaped, which reopens the injection the page closes by escaping all
data. So statements are shown as TeX source: decided 2026-10-17, for now
(proposal 0001). Options for later: an opt-in `--html-math` that adds a pinned KaTeX
from a CDN with a subresource-integrity hash; or GitHub math in the README
for statements that pass a strict character allow-list.

## Not done here

- The statement of a claim compared under a different name than its file
  declares (the scanner needs the exact qualified name).
- A per-result axiom list: receipts record the package's union of axioms, so
  every block shows that union.
- The Palomar-style "statement imports" line (which libraries the Challenge
  imports).
