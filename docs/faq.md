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
policy exists because the first run builds tools for about 25 minutes and a
full comparator run can be heavy; inside a Slurm job it is always allowed.

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
lean-pkg stores the fact; it does not query Reservoir for you.

**Should I search Reservoir before formalizing?**
Yes, and not only Reservoir. Before writing any new definition, lemma or
library, run the
[search-before-building](https://github.com/chenle02/lean-pkg/blob/main/skills/search-before-building/SKILL.md)
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

**Something here is wrong or missing.**
Open an issue. Corrections to statements of fact about other projects are
especially welcome; this documentation cites its sources so you can check.
