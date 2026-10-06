# 0003: History before the public flip

Date: 2026-10-06. Status: proposed (the maintainer decides; nothing changes
until this is accepted).

## Context

`make check` scans the tree git would ship. The public flip exposes every
commit, so `make history` (added 2026-10-06) scans every blob and commit
message reachable from any ref. On the first run it found two hits that the
tree scan cannot see: one line in an early version of `CHANGELOG.md` naming
the private lab the tool was extracted from, and one commit message naming a
private notification channel. Both were scrubbed from the tree long ago; both
remain in history.

## Options

1. **Rewrite history once, just before the flip.** `git filter-repo` with a
   replacement list removes the two strings; every commit after the first
   hit gets a new id, so tags v0.1.0 onward are re-cut at the rewritten
   commits and the GitHub releases re-pointed. Consequence: commit ids cited
   before the flip (in the lab's own records) stop resolving; nothing public
   cites them yet.
2. **Publish from a fresh history.** Push the current tree as a single
   initial commit to the public repository; keep the private history where
   it is. Consequence: no public blame or bisect before the flip; the
   releases restart at the flip version; simplest boundary.

## Decision (proposed)

Option 1, run as the last step before the flip, with `make history` green as
the acceptance test and the re-cut tags verified by `scripts/release.sh`'s
version agreement check. Until the flip, `make history` stays outside
`make check` so the current, honest red does not block releases.

## Consequences

- `RELEASING.md` names `make history` as the flip prerequisite.
- The rewrite is scripted and reviewed before it runs; the replacement list
  is built from the boundary script's own forbidden strings.
- Any new hit after this decision is removed the same way, at the flip.
