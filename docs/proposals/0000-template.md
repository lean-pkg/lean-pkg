# NNNN: Title

Status: draft. Author: name (@handle). Created: YYYY-MM-DD.
Decided: (date and maintainer, once accepted or declined). Implemented in: (release).

## Summary

One paragraph: what changes, for whom.

## Motivation

The problem, with a concrete case (an issue, a field report, a command and its
output). What happens today and why that is not good enough.

## Design

The change exactly as a user or a script sees it: commands and flags, help
text, `--json` keys, file formats with a small example, exit codes. For a
schema change, the new version string and every field that is added, removed
or changes meaning.

## Compatibility and migration

- What an existing registry, receipt or companion repository does after the
  change, and what an older lean-pkg does with a newer file.
- The migration command, if a format changes (a schema change always ships
  with one), and the deprecation period for a removed or renamed spelling (at
  least one minor release with a warning; RELEASING.md, 1.0.0).
- The golden CLI contract changes this needs (`tests/golden`).

## Trust model

Does this change what a receipt proves or what a gate accepts? If yes: what a
dishonest or mistaken submitter could now get accepted that they could not
before, and the test that pins the refusal. An effect not yet established is
written "unknown", never "none".

## Alternatives

What else was considered, including doing nothing, and why not.

## Open questions

What must be settled before acceptance.

## AI assistance

Which AI tools helped write this proposal, and how, or "none".
