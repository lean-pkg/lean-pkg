---
title: FAQ
---

[← lean-pkg documentation](index.md)

## About the project

**Is lean-pkg a registry like Palomar?**
No. [Palomar](https://palomar-registry.org/) is the public registry of
record for Lean-verified results: it pins a public commit, checks the
statement against the proof in independent kernels, reviews it, archives
every dependency, and assigns a stable id. lean-pkg does not duplicate any
of that. It covers the months *before* a result is public (who holds which
package, what is certified at which commit, evidence complete before anyone
sees it) and a field-curated index *after* (hypotheses, paper
correspondence, who uses the result), keyed to Palomar ids. A package that
passes lean-pkg's comparator tier is already in the shape Palomar accepts.

**Is it a library? Does it compete with Mathlib?**
No. It contains no mathematics. Every package depends on Mathlib, and
general results are meant to be upstreamed there. See
[Motivation](motivation.md#lean-pkg-palomar-reservoir-and-mathlib).

**Is it only for probability and stochastic analysis?**
The tool is field-neutral. The index starts with the maintainer's field
because that is where the formalization frontier is thin and where the
first entries come from; entries from any field are welcome.

**Who maintains it, and is it free?**
Apache-2.0, free. Maintainers are listed in the repository's
`MAINTAINERS.md`.

## About verification

**My project builds with no `sorry`. Isn't that verified?**
It proves that *some* statement compiles. It does not prove that the
statement is the one the paper claims, and it checks itself inside one
kernel. The comparator tier adds both: trusted statements in
`Challenge.lean` compared against untrusted proofs in `Solution.lean`, with
replay in the independent `nanoda` kernel. [Receipts and trust](receipts.md).

**What exactly does a passing receipt pair prove?**
See the table in [Receipts and trust](receipts.md#what-a-passing-pair-does-and-does-not-prove).
Short version: the listed theorems compile sorry-free with the standard
axioms, each `Solution` theorem has exactly its `Challenge` statement, the
proofs replay in a second kernel, and the evidence bytes match. It does not
prove the Lean statement matches the paper's intent; a human reads that.

**Does lean-pkg require end-to-end formalization?**
No. The current receipt machinery certifies the formal theorem and evidence at
a pinned commit; it does not require that every mathematical fact used by the
paper originate inside the same repository. A conditional theorem should make
its mathematical inputs explicit in its statement or formal interfaces.
Structured "conditional certificate" and certificate-lineage metadata are
planned rather than current schema fields.

**What does a conditional verification mean?**
It means Lean has checked the stated consequence from explicitly declared
inputs, while some mathematical dependencies may remain outside the current
formalization boundary. The important distinction is between theorem
hypotheses, kernel-checked Lean dependencies, external results from the
literature, temporary project assumptions, and material that is simply outside
scope. A conditional certificate must never be described as verification of
more than that boundary.

**Should an external theorem be added as a Lean axiom?**
Usually no. Prefer a theorem hypothesis, structure field, or abstract interface
that exposes the dependency in the formal statement. Introducing a new trusted
axiom would enlarge Lean's logical trust base and would conflict with the
receipt discipline that audits permitted axioms.

**Can a certificate become stronger later?**
Yes in principle, and the roadmap proposes explicit certificate lineage for
this. If an external result is later formalized, a new pinned certificate can
replace that dependency with a formal Lean theorem. The earlier certificate
should remain immutable historical evidence rather than being rewritten.

**Can Lean verification help improve a manuscript before publication?**
Yes. This is a central research use case: formalization can expose missing
hypotheses, statement drift, or hidden dependencies while the paper is still
being written. The manuscript and Lean development can then be revised
together, with each new receipt tied to the exact source commit it certifies.

**Can symbolic or numerical computation be part of the workflow?**
Yes. Python, SymPy, Sage, Mathematica, numerical experiments, and similar tools
are excellent discovery engines. Their output is provenance, not proof. The
strongest workflow is: use computation to propose a formula, state that claim
precisely in Lean, and let the Lean proof—not the exploratory calculation—carry
the verification claim. The roadmap proposes structured discovery-artifact
provenance so a future certificate can record how a theorem was found without
confusing that history with evidence.

**How is AI-assistance disclosure different from discovery provenance?**
AI disclosure records *where AI participated*: for example, generating a
SymPy script, proposing Mathematica transformations, drafting Lean code, or
editing documentation. Discovery provenance records *how a candidate
mathematical claim was found*: the concrete notebook, script, software
environment, content hash, and output that led to the conjecture.

Neither is proof evidence. The verification record is separate and answers a
different question: which exact Lean theorem, at which commit, was checked
under which assumptions and receipts? A future certificate should keep all
three channels distinct and link them when useful.

Journal disclosure requirements remain journal-specific. A detailed
`lean-pkg` provenance record can support an author's disclosure, but it does
not replace the wording or policy required by a venue.

**Do I have to make a work-in-progress repository public to use lean-pkg?**
No. The private lifecycle exists specifically so development, receipts, and
re-certification can remain private. A public index entry, however, must point
to source and evidence that independent reviewers can retrieve and re-derive at
the pinned commit. Publishing a certificate does not require publishing every
failed experiment, agent conversation, or abandoned branch.

**Why does `promote` refuse even though the registry says the receipt exists?**
Because `promote` re-derives everything from bytes at that moment: config
hashes at the certified commit, log hashes on disk, the PASS line, the
axiom subset, and the commit binding. A registry pointer is never evidence
on its own. The refusal names the exact field; fix that and rerun.

**I committed my receipts and now the gate says drift. Why?**
It should not, if the commit touched only `.lean-receipts/`. That is the
evidence-only descendant rule. If anything else changed in the same commit,
the certified source closure changed and you must re-certify. Keep evidence
commits pure.

**Can I run the comparator on my laptop?**
Yes, if you list it: `COMPARATOR_RECEIPT_ALLOWED_HOSTS="$(hostname)"`. The
policy exists because a first legacy-mechanism run builds tools for about 25
minutes and a full comparator run can be heavy; inside a Slurm job it is always allowed.

**Why did `comparator-receipt` write nothing?**
Because at least one configuration failed, the tree was dirty, or a log
lacked the PASS line. It writes evidence only when everything passed, so a
failed run can never be mistaken for a receipt. Read its stderr.

**Can I register a package that pins an older Lean, say v4.29, in Palomar?**
Not today. Palomar's `toolchains.json` sets a minimum (v4.35.0-rc2 as of
2026-10-05), its verifier installs the toolchain you pin and rejects one
below the floor, and every file must carry a `module` header. Of the 447
entries registered by 2026-10-05, none is below v4.30.0, and every entry
since 2026-09-24 is at v4.35.0-rc2 or rc3. A v4.29 package has to be
upgraded first; lean-pkg's lifecycle is unaffected by the pin.
`lean-pkg palomar check NAME` reports both gaps (see
[Palomar readiness](#palomar-readiness)); the header each file needs is in
[Getting started](getting-started.md#the-module-header).

**Which Lean version do I need?**
Whatever your package pins. For a package you intend to register in
Palomar, follow Palomar's current floor (Lean v4.35.0-rc2 or later, with
`module` headers in every file, at the time of writing).

## About the workflow

**Do I have to use leases?**
Only if more than one person or agent touches a package. A lease is cheap
insurance: `claim` refuses if someone else holds it and tells you who.

**What produces the lower-tier receipt?**
Your executor: a CI job, a Slurm job, or a shell script that runs
`lake build`, a sorry scan, and `#print axioms`, then freezes and hashes
the report. lean-pkg validates receipts; it never runs Lean.

**Can an agent run this for me?**
Yes; the tool was designed for it. Point the agent at [llms.txt](llms.txt).
Two rules it must keep: claim before working, and never pass `--yes` to
`promote` without a human reading the dry run.

**Several machines share one registry. How?**
Keep checkouts at the same `$HOME`-relative path on every machine and
version the registry file plus its event log. See
[How to](how-to.md#share-one-registry-across-machines).

**How do I cite a verified result?**
By the package's Zenodo version DOI, cut at the commit Palomar registered,
plus the `PALOMAR-…` id with its version. Both name the same bytes.

## Palomar readiness

**How do I know a package is Palomar-submittable before I release it?**
Run `lean-pkg palomar check NAME`. It is read-only and offline: it reads the
git objects of one commit (`--rev REV`, else the commit the gate receipt
binds, else HEAD), never the working tree, and lists every gap as
`{code, severity, paths, detail, fix}`. `error` gaps need a new commit, and
so a re-gate; `warn` gaps are submission steps (make the repository public,
push the commit) or advice. Exit 0 when there is no `error`, 1 when there
is, 2 on a usage error. Run it before the gated waves: a package can pass
every lean-pkg gate and still be refused by Palomar, and porting to the
module system changes build inputs. `gate --check` and `publish --dry-run`
print a `palomar:` warning with the gap count and the command (JSON
`palomar_check`); it never changes their verdict.

**Where else does the check run?**
`lean-pkg audit` runs it for every package that aims at Palomar: one whose
record says `upstream_target: palomar`, or that already has a `palomar`
entry, and whose status is not `scratch`, `archived` or `upstreamed`. A
package with `error` gaps gets a `palomar-not-ready` warning naming the gap
count, the codes and `lean-pkg palomar check NAME` (JSON `palomar`
`{errors, codes, commit, command}`); `warn` gaps alone are no finding, and
every other package stays quiet. Set the signal with
`lean-pkg update NAME --upstream-target palomar`.
`lean-pkg palomar prepare NAME` refuses (exit 1, nothing written, also
under `--dry-run`) while `palomar check` finds any `error` gap at HEAD, and
lists each one with its fix. `warn` gaps never block it: they print as
`lean-pkg: warning: palomar check: ...` lines, and no flag is needed to
proceed past them.

**Which rules does it check, and where do they come from?**
Palomar's own repository,
[PalomarSubmission](https://github.com/PalomarRegistry/PalomarSubmission),
at commit `d4e41c1d5b0d` (committed 2026-10-05, read 2026-10-16):
`browser-preflight-policy.json`, `toolchains.json` and the README sections
"Required source layout" and "Lean source requirements". Palomar's floor
moves; when it does, download the newer `browser-preflight-policy.json` and
pass it with `--rules FILE` (lean-pkg never fetches it). The checks:

- every tracked `.lean` file of the repository (contained projects and
  unused files included; `.git` and `.lake` excluded) begins with `module`;
  ordinary comments may precede it, module documentation (`/-! -/`) and doc
  comments may not; `lakefile.lean` is exempt; at most 10,000 lines; no
  symbolic links; valid UTF-8 (codes `source.module_required`,
  `source.file_too_long`, `source.symlink_not_allowed`,
  `source.invalid_utf8`, as Palomar names them);
- `lean-toolchain` is `leanprover/lean4:vX.Y.Z[-rcN]` at or above v4.35.0-rc2
  (`toolchain.unsupported`); a Lake configuration and no Git submodules;
- each comparator configuration (or the one `--config PATH` names: Palomar
  verifies only the configuration a submission names) has the required keys
  and no other key than `challenge_module`, `solution_module`,
  `theorem_names`, `definition_names`, `permitted_axioms` and
  `enable_nanoda`, permits only the standard axioms, has its module sources
  tracked, and its Challenge is at most 1,000 lines and 100 KiB; non-empty
  `definition_names` is a warning (compared by type, never by body);
- `formalization.yaml` is tracked, valid v0.4, free of placeholders, and has
  Palomar's metadata minimum (description, responsible maintainers, one to
  eight arXiv classes, sources with relationships, automation methods,
  review status);
- exactly one licence file at the repository root, matching `license_spdx`;
- a GitHub repository, public, with the commit pushed (local refs).

It does not run Lean's header parser, licensee, the taxonomy and ORCID
lookups, the Challenge import and shadowing checks, or the build and
comparator run; the JSON `not_checked` lists them. Like Palomar's browser
preflight it predicts, it does not decide.

**What header does a file need?**
`module` as its first command, `public import` for every import its
statements use, and its theorems inside `public section` ... `end` (or each
declared `public theorem`), since `lake comparator` cannot export a private
declaration; `@[expose]` on a shared definition whose body a proof unfolds.
The project template in [Getting started](getting-started.md#start-from-scratch)
is written that way and `palomar check` finds no source gap in it.
`lean-pkg init` creates only the registry and its event log; the only
command that writes `.lean` files is `palomar port-modules --write`, below.

**How do I port an existing project to the module system?**
`lean-pkg palomar port-modules NAME` plans the mechanical part from the
package's tracked `.lean` files at HEAD and prints a per-file report and a
unified diff, writing nothing: `module` first, `public import`, and the body
in `public section` ... `end`. It flags, without fixing, what needs a human:
definitions another file unfolds (`@[expose]`), `private` declarations,
`#eval`/`native_decide`/`initialize` (`meta import`), imports of a
non-module, and unbalanced scopes; `prelude` files are refused. With
`--write` it applies the plan to a clean working tree and never commits.
Then run `lake build`, add the `@[expose]` the build asks for, commit, and
run `palomar check`; a gated package needs a new gate, since its sources
changed. The flags are a heuristic: the build is the judge.

**`palomar check` says `judged_as` is an unknown key. Is my configuration wrong?**
For Palomar, yes: its verifier refuses any configuration key outside the six
above. lean-pkg no longer needs such keys: the definition-hole sign-off and
the scaffold's TODO markers live in `lean-pkg-signoff.json` beside the
configuration ([Comparator](comparator.md#definition-holes-need-a-human-sign-off)).
A configuration written by an earlier release still carries `judged_as` or
`lean_pkg_scaffold`; lean-pkg reads them with a deprecation warning, and
`lean-pkg comparator config --migrate-signoff comparator.json --write` moves
them out without touching the Palomar keys. The file's bytes change, so a
gated package then needs a new comparator receipt (and a re-gate at that
commit). Keep `definition_names` empty where you can: Palomar's reviewers
still judge each hole.

**What is planned next?**
An optional `comparator.headline.json` that feeds `status.main_results`
(medium).

## Reservoir

**How is lean-pkg different from Reservoir?**
[Reservoir](https://reservoir.lean-lang.org/) is the Lean FRO's package index
for Lake. It finds public Lake packages on GitHub automatically, about once a
day, and builds each one against new Lean toolchains, so it answers "which
packages exist, and do they still build?". A Reservoir build runs
`lake build` (and `lake test` if the package has a driver); it does not scan
for `sorry`, audit axioms, compare a statement with a paper, or replay
proofs in a second kernel. lean-pkg covers trust and lifecycle around a
package: who holds it, what was certified at which commit, whether the
evidence is complete before release, and what was recorded after (Palomar
id, DOI, Reservoir listing). [Palomar](https://palomar-registry.org/) is the
registry of kernel-checked research results.

| Question | Reservoir | Palomar | lean-pkg |
|---|---|---|---|
| Which public Lake packages exist? | yes (automatic) | no | no |
| Does a package build on a given toolchain? | yes (build history per toolchain) | for the registered commit only | no; it validates your receipts |
| Is a theorem kernel-checked against its stated challenge? | no | yes (comparator, independent kernels) | validates the comparator receipt before release (comparator tier) |
| Who is working on this package, and what is certified at which commit? | no | no | yes (leases, gate, receipts) |
| What was published, where, and with whose consent? | no | the registration | yes (evidence events) |

The three are complementary: a package can be held and certified in
lean-pkg, registered in Palomar, and listed on Reservoir, and lean-pkg
records the last two as facts.

**Do I need to register my package on Reservoir?**
No, and you cannot: there is no submission form. Reservoir lists a
repository automatically once it is public on GitHub, has a
`lake-manifest.json` at its root, has an OSI license that GitHub detects,
is not a fork (a repository made with GitHub's "Use this template" counts as
a fork), and has at least 2 stars. Set `description`, `keywords` and
`versionTags` (and optionally `homepage`) in the lakefile: they are what
Reservoir displays and what people search. To opt out, set
`reservoir := false`. The listing is confirmed when
`https://reservoir.lean-lang.org/api/v1/packages/<owner>/<name>` returns
HTTP 200 (404 means not listed yet); then record it with
`lean-pkg record NAME reservoir-listed --field url=<package page>`.
lean-pkg stores the fact; it does not record it for you, but
`lean-pkg reservoir check NAME` checks these criteria and the API and prints
the `record` command once the package is listed.
Before `promote --yes`, the items only promote can satisfy are marked
`pending promote`, and a release-candidate or nightly `lean-toolchain` is a
warning (Reservoir may display it poorly).

**Should I search Reservoir before formalizing?**
Yes, and not only Reservoir. Before writing any new definition, lemma or
library, run the
[search-before-building](https://github.com/lean-pkg/lean-pkg/blob/main/skills/search-before-building/SKILL.md)
skill: Loogle, Mathlib at your pin, the Reservoir index, Palomar, known
authors and a GitHub code search, with every query recorded, zero hits
included. One trap: the search box on the Reservoir site matches package
*names* only, so `kkytola/ExtremeValueProject`, whose description says
"Fisher-Tippett-Gnedenko", never appears for "Fisher-Tippett". Search the
descriptions in the index repository (`leanprover/reservoir-index`) instead,
with several phrasings: the same description never says "Gumbel". The indexes barely overlap (in
October 2026 only 22 of 363 Palomar source repositories were on
Reservoir), and zero hits in all of them still do not prove that nothing
exists.

## Contributing

**I verified something. How do I add it to the index?**
Open a pull request with an index entry: statement, hypotheses, paper
correspondence, the Palomar id if registered, the library pin and
declaration name, and a note on who uses it. CI checks that references
resolve; a maintainer reviews fidelity.

**Do I have to disclose AI assistance?**
Yes, in the pull request, following the Lean community's norms: name the
tool and the parts it touched, write review comments in your own words, and
be able to explain every line you submit. See the repository's
`CONTRIBUTING.md`.

**Does the site have search and a dark mode?**
Yes. The search box at the top of every page searches all pages client-side
(press `/` to focus it); the index is a JSON file generated at build time,
so there is no server. The theme toggle next to it follows your system
preference by default and remembers your choice in the browser.

**Can I list a project that is not finished?**
Yes, if what you list is what is proved. Headlines must be theorems that exist
at the pin and that the receipts cover; the goal and the gaps go in `notes`;
partial or conditional results are claims with status `experimental`. See
[An unfinished project as an entry](index-catalog.md#an-unfinished-project-as-an-entry).

**Something here is wrong or missing.**
Open an issue. Corrections to statements of fact about other projects are
especially welcome; this documentation cites its sources so you can check.
