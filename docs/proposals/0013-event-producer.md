# 0013: Which lean-pkg wrote each registry event

Status: accepted. Author: drafted by the AI operations agent at Le Chen's request. Created: 2026-11-09.
Decided: 2026-11-09, accepted by Le Chen (maintainer of record), as written, with these answers to the open
questions: no `history[]` key (1); audit reporting in a later proposal (2); no commit from the install marker
(3). Implemented in: (release).

## Summary

Every line lean-pkg appends to the event log (`lean-package-events.jsonl`) gains one key, `producer`: the
lean-pkg release that wrote it, how it is installed, and, for a checkout, its commit. The key is additive
within event-line version 1, carries no path, host or user name, and costs no hashing. Receipts have
recorded their producer since #297 and #300; this does the same for the registry's own history, so that an
audit can tell which release wrote a `gate-passed`, a `regated` or an `updated` line.

## Motivation

The event log is the registry's append-only history. Its lines say what happened, when and to which
package, but not which lean-pkg wrote them. Several releases write to one registry at once: the fleet runs
whatever each machine has installed, and an agent may run an editable checkout of an unreleased commit.
A field request (2026-11-07) asked for the writer to be recorded, because nothing in the log tells which
release applied which rule to a package. When a release changes
what `gate`, `update` or `lease claim` accept (the upgrade record lists such changes), an audit needs to
know which side of the change an event was written on.

Receipts already answer this for evidence (`producer`, docs/receipts.md, "Which lean-pkg wrote a
receipt"). The event log has no equivalent.

## Design

### The key

Every event line written by a lean-pkg that implements this proposal carries:

```json
"producer": {"lean_pkg": "0.29.0", "install": "installed", "commit": null, "dirty": null}
```

- `lean_pkg`: the running release (`lean_pkg.__version__`).
- `install`: `installed` (a wheel or pipx install), `editable` (a git checkout) or `unknown` (git is not on
  `PATH`), the detection behind `lean-pkg --version` and `doctor`.
- `commit`: for `editable`, the checkout's full `HEAD`; otherwise null. An installed release is identified
  by its version, whose tag names the commit.
- `dirty`: for `editable`, whether tracked files are modified; otherwise null.

This is the head of a receipt's `producer`, without the source and bytecode hashes: those cost a pass over
every file of the package, which a receipt pays once and an event log would pay on every write. The value
is computed once per process.

### The format

- The key goes on every line, log-only events and package events alike, and is not copied into the
  package's `history[]` in the registry YAML (open question 1).
- Event lines stay `v: 1`. A reader that interprets events ignores keys it does not know, so an older
  lean-pkg reads the new lines as before; raising the version to 2 would make those readers skip every new
  line instead (`lease claim`'s prior-art note skips a newer `v`). A line without `producer` (every line
  written before) is read as before and means "producer unknown".
- docs/formats.md, "Event log", gains the key beside `ts`, `event`, `name` and `v`, with the release it
  first appears in; `EVENT_LINE_KEYS` and tests/test_formats.py follow.

### Reading it

This proposal records the key and changes no reader. `registry audit` and `status` may later report events
written by a lean-pkg newer than the running one, as `status` does for receipts (open question 2).

## Compatibility and migration

- Additive: no event is rewritten, no reader changes, a registry written by an older lean-pkg is valid.
- An older lean-pkg ignores the key. A newer one treats a line without it as producer unknown.
- The committed-mode commit of the registry and the event log (`--commit`) is unchanged; the key is in the
  log lines it commits.
- No `--json` output changes, except where a command echoes event lines it read (the board's last event),
  which then show the key.

## Trust model

None changes. The event log is a record, not evidence: anyone with write access to the registry can append
any line, and no check trusts it. `producer` says which lean-pkg claims to have written a line; it is as
trustworthy as the line itself. It contains no path, host or user name (`install` is a kind, `commit` is a
lean-pkg commit), so the public-safety and private-boundary rules are unaffected.

## Alternatives

- **The full receipt `producer`**, hashes included: precise, but a pass over lean-pkg's sources on every
  event write, for an attribution the version and commit already give.
- **Event-line version 2**: honest about the change, but older readers would skip every new line.
- **Only on gate events** (`gate-passed`, `regated`, `promoted`): smaller, but an `update` or a `lease
  claim` written by another release is as relevant to an audit.
- **In `history[]` too**: one more key per entry in the registry YAML, which agents read whole; the log is
  where history is audited.

## Open questions

1. Whether `history[]` entries also carry `producer`.
2. Whether `registry audit` (or `status NAME`) reports events written by a lean-pkg newer than the running
   one, and with which level (info or warning), in this proposal or a later one.
3. Whether `commit` should also be recorded for an installed release when the install marker of the lab
   installer names one (`.lean-pkg-install.json`), at the cost of reading a file outside the package.

## AI assistance

Drafted with Claude Code (Claude Opus 5.5) at Le Chen's request, from a field request by an AI agent (the
2026-11-07 triage of seven requests, item "producer on registry events"). Le Chen reviews and decides.
