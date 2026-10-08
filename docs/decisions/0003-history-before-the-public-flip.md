# 0003: History before the public flip

Date: 2026-10-06. Status: accepted 2026-10-08 (option 1).

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

## Decision

Option 1, run as the last step before the flip, with `make history` green as
the acceptance test and the re-cut tags verified by `scripts/release.sh`'s
version agreement check. Until the flip, `make history` stays outside
`make check` so the current, honest red does not block releases.

## Consequences

- `RELEASING.md` names `make history` as the flip prerequisite.
- The rewrite is scripted and reviewed before it runs; the replacement list
  is built from the boundary script's own forbidden strings.
- Any new hit after this decision is removed the same way, at the flip.
- The rewrite is `scripts/rewrite-history.sh`. It refuses unless the scan
  finds exactly the known hits, and refuses to push unless the trees of main
  and every tag, the tag messages and the commit count are unchanged.
- The branches of merged pull requests (#2, #3, #5) hold pre-rewrite commits
  and are deleted, not rewritten.
- GitHub keeps every pull request's head commit under a read-only ref
  (`refs/pull/N/head`) that a push cannot change, so after the rewrite the
  old commits stay reachable from pull requests #1, #2, #3 and #5. Only
  GitHub Support can remove them; that request belongs before the flip.
