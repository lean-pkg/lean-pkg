---
name: lean-publishing
description: "Publish a verified Lean formalization: an ordinary (non-template) public repository in the Palomar layout (Mathlib-only Challenge, Solution, comparator configs), a formalization.yaml with honest automation and fidelity blocks, a Palomar preflight and submission at an exact commit, a Zenodo DOI read back from the API for a release at that commit, and a Reservoir listing; human decisions stay human. Trigger phrases: publish the Lean proof, Palomar submission, Palomar registry, Challenge.lean, comparator config, formalization.yaml, Zenodo DOI for Lean, cite the formalization, Reservoir listing, PalomarTemplate."
license: Apache-2.0
---

# Publishing a Lean formalization

## Problem

Publication has several one-way steps (public visibility, a permanent
registry entry, a DOI) and several places where a cheap mistake becomes
permanent: a template-generated repository that Reservoir never lists, a
comparator config path that later moves, a DOI for a commit other than the
registered one, an automation disclosure that names a model nobody used.

## Why it happens

Each service has its own identity rule and its own evidence. Agents tend to
copy a previous project's steps instead of reading the current policy, and to
act on a human's behalf at the steps that require a human.

## Rule

- **Re-read the current rules first.** Fetch
  `https://submit.palomar-registry.org/llms.txt`, Palomar's policy
  `CONTRIBUTING.md`, and `toolchains.json` from PalomarSubmission. If they
  disagree with this skill, they are right.
- **Human-owned steps:** deciding that the result is a headline theorem
  worth a registry entry; consent of every coauthor (public repository,
  registration naming the paper, DOI, disclosure text, author list);
  approving each Challenge statement; submitting; registering after reading
  the review; enabling Zenodo. An agent prepares, never decides.
- **One commit X** is the subject of the comparator receipt, the Palomar
  entry and the DOI release. Later citation edits are descendants of X.

## How to apply

1. **Repository.** Create an ordinary repository and copy files from
   `PalomarRegistry/PalomarTemplate` (do not use GitHub's "Use this
   template": Reservoir treats such repositories as forks). Layout per
   headline theorem: `Challenge.lean` (trusted, Mathlib-only, statements with
   `sorry`), `Solution.lean` (same names and statements, with proofs), support
   modules proving each Challenge definition equivalent to its library
   counterpart, and a `DESIGN.md` table (Challenge definition, library object,
   bridge). Choose the config path once (for example
   `comparator/config-<thm>.json`); it is part of the registration identity.
2. **Source rules.** Toolchain at least Palomar's minimum and exactly
   Mathlib's at the pinned revision; every file uses the `module` system;
   per-file line caps (stricter for Challenge files); git dependencies are
   public HTTPS URLs at full revisions; no LFS, submodules, symlinked `.lean`
   files or build artifacts. Run the template's source checker.
3. **Challenge.** Define every object from Mathlib primitives with a
   docstring of its ordinary meaning; state every hypothesis of the paper's
   theorem; list known divergences. Run the cold read of `lean-claim-ledger`.
4. **`formalization.yaml`.** Start from the template; fill project, authors
   and responsible maintainers (humans only), license, classification,
   sources (the paper), automation (only models and tools actually used, with
   a plain statement of human direction), review status (what is true:
   author-verified, agent-reviewed, or self-assessed), and fidelity notes.
   Write "novelty not assessed" unless a literature search was done. Run the
   template's validator.
5. **Gate.** Build, axiom audit, comparator receipt at X (`lean-pkg-agent`).
6. **Preflight.** Run PalomarSubmission's reusable `submission.yml` workflow
   (`mode: full`, the standard execution profile; pin the workflow ref and
   `pipeline_commit` to the same commit) at X; continue only on `status: pass`.
7. **Duplicates and versions.** Check
   `https://data.palomar-registry.org/repositories/<owner>/<repo>.json`. An
   existing (repository, config path) identity means a new version: pass
   `existing_id`.
8. **Submit** (a human, by default through the web form). The relationship
   answer is public and permanent. Keep any access token out of files and
   logs. The review is private until registration.
9. **Register** only on the human's explicit instruction after reading the
   review; then confirm the public entry's `source.commit` equals X.
10. **DOI.** With Zenodo's GitHub integration enabled by the owner, release
    at X and read the DOIs back:

    ```bash
    gh release create v1.0.0 --target "$X" --title "v1.0.0" --notes "Archive of $X"
    test "$(git rev-list -n1 v1.0.0)" = "$X"
    # after Zenodo archives the release, read the record (id from the Zenodo GitHub page)
    curl -s "https://zenodo.org/api/records/<record-id>" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["doi"], r.get("conceptdoi"))'
    ```

    Record the concept and version DOIs only as read back. `CITATION.cff`
    (without a DOI before the release) supplies Zenodo's metadata.
11. **Citation commit** (a descendant of X): DOI badge, `CITATION.cff` DOI,
    the Palomar id, and wording such as "the automated Palomar review found no
    blocking problem", never "accepted" or "peer-reviewed".
12. **Reservoir** lists public, non-fork, licensed Lake packages
    automatically; check after a few days.

## Anti-patterns

- Driving a browser sign-in or pressing "register" for a human.
- A silent coauthor treated as consent.
- Pasting the private review into a public file before registration.
- Writing a DOI that was not read back from Zenodo.
- Calling a Palomar registration evidence of Mathlib readiness.

## Verification

The Palomar entry's commit, the release tag's commit and the comparator
receipt's commit are the same 40-hex X; the DOI resolves to that release; the
README states which theorems were checked, under which axioms and kernels,
and what remains informal.

Learned from: preparing the first formalizations of a research group for the
Palomar registry and Zenodo, where reading the current registry and
Reservoir rules exposed traps (template-generated repositories are never
listed; the config path is part of the identity), October 2026.
