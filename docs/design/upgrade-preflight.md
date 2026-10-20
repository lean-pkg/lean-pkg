---
title: Upgrade preflight
---

[&larr; lean-pkg documentation](../index.md)

# Design: the upgrade preflight (`doctor --upgrade-from`)

Status: implemented (first version). Date: 2026-10-17. Origin: two field
requests from package authors who pin lean-pkg in their own release
audits and need to know, before moving to a newer lean-pkg (or to a newer
commit carrying the same version string), what changes for them.

## The question

"I verified my package with lean-pkg OLD. What do I have to know or do to
use NEW?" The answer has to be machine-readable (a package's own CI reads
it) and has to work between two commits with the same version string,
since agents run lean-pkg from an editable checkout between releases.

## What can be derived, and what cannot

The two source trees answer part of the question mechanically. The rest
is a rule about evidence ("a receipt without its sealed log is now
refused") that lives in code paths no diff summarises; that part needs a
curated record, written once per change by whoever makes it.

| Change | Mechanical source | How |
|---|---|---|
| Python floor, dependencies, executables | `pyproject.toml` | `tomllib`: `requires-python`, `dependencies`, `[project.scripts]` |
| Format versions | module-level constants of `src/lean_pkg/*.py` | every string constant `family/N` (`"comparator-receipt/2"`); `REGISTRY_VERSION`, `SUPPORTED_REGISTRY_VERSIONS`, formalization's `SCHEMA_VERSION` |
| On-disk key lists | the same constants | `PACKAGE_FIELDS`, `LEASE_FIELDS`, `RECEIPT_FIELDS`, `COMPARATOR_FIELDS`, `COMPARATOR_CONFIG_FIELDS`, `COMPARATOR_TOOLCHAIN_DIGESTS`, formalization's `DERIVED_PATHS` |
| Commands and flags | the golden CLI contract, `tests/golden/help/*.txt` | file name = command path; option lines = flags. The goldens are the public API (RELEASING.md), so this is complete by construction |
| `--json` key sets | `tests/golden/json-keys.json` | per command |
| Aliases | `src/lean_pkg/command_tree.py` | `COMMAND_ALIASES`, `FLAG_ALIASES`, `IMPLIED_VERBS`; used to report a renamed command or flag as `renamed`, not removed |
| Packaged data | `src/lean_pkg/{schemas,templates,scripts,site_profiles}/` | git blob ids: added, removed, changed |
| What a rule accepts or refuses | none | curated record |
| A key whose meaning changed under the same name | none | curated record |
| Which old format versions readers still accept | partly (`SUPPORTED_REGISTRY_VERSIONS`, `COMPARATOR_SCHEMAS`) | curated record when it changes |

Every mechanical value is read from **git objects** (`git show REV:path`,
`git ls-tree`) and Python sources are **parsed with `ast`, never imported or
run**. The old tree's code is not trusted to be importable by today's
interpreter, and running it would let a preflight execute whatever an old
commit contains. A constant whose value is not a literal (a call, an
f-string, arithmetic) reads as unknown; a key-list row like
`("status", "v1", "lifecycle status: " + ...)` still yields its first item.

## The curated record

`docs/upgrades/<version>.yaml`, schema `lean-pkg-upgrade-record/1`, one
file per release, written in the same pull request as the change it
describes (or before the release is cut):

```yaml
schema: lean-pkg-upgrade-record/1
version: 0.11.0
changes:
  - pr: 202                       # the pull request that made the change
    id: receipt-sealed-log-required
    kind: behavior                # behavior | format | command | requirement
    area: receipts
    summary: ...                  # what changed, in one paragraph
    action: ...                   # what a package author does about it
    breaking: true                # something that passed can now be refused, or a published file goes stale
    changelog: Fixed              # the CHANGELOG.md heading of the entry
    split: true                   # optional: this PR made several separate changes, one entry each
no_author_change: [200, 203]      # PRs of the release read for this record and found to need nothing
```

**One entry per pull request.** A pull request has one entry, unless it
made several separate changes: then it has one entry per change, and each
of them carries `split: true`. The preflight adds to `record_problems[]` a
pull request with several entries not all marked `split: true` (one change
recorded twice, as #285 was in 0.19.0), a lone entry marked `split: true`,
and a pull request listed both as a change and under `no_author_change`.

The first pull request after a release opens the next record. If it changes
nothing a package author must know, it opens it with `changes: []` and
itself in `no_author_change`; `changes` may be empty only then.

**The provisional name.** That first pull request cannot know what the
release will hold, so it names the record for the next minor
(`0.(N+1).0`): the name is provisional. The record's `kind` and `changelog`
fields decide the release's version (RELEASING.md, "How the version is
checked": a `command`, `format` or `requirement` entry, or one under
`Added`, `Deprecated` or `Removed`, makes a minor; anything else a patch),
and `make cut-release VERSION=X.Y.Z` renames both files to X.Y.Z, with the
`version:` key and the page's title, heading and links rewritten, in a
commit of its own before the release commit. So each released record is
named for the release it describes, and the file name always equals the
`version:` key. Only the newest record may be unreleased. The preflight
adds to `record_problems[]`: a record whose file name is not its version;
an older record whose version was never released (no tag, no `release:`
commit); and an unreleased newest record that lists a pull request already
in the history of the tree's current release (the rename was missed: the
record is that release's). A clone with no release tag or commit at all
(shallow) compares nothing. No key of the schema changed.

A human page `docs/upgrades/<version>.md` goes with it; a test checks that
every `id` and PR number of the record appears on the page.

**Which entries apply.** An entry applies when its pull request's squash
commit (subject ending `(#N)`, the form every merge on main has) lies in
`OLD..NEW`. The records are read at the source checkout's `HEAD` when it
descends from NEW (a record completed after a release still describes
it), else at NEW; `records_at` names the commit. So a comparison between two commits
of the same version gets exactly the entries merged in between, and a
comparison across several releases gets the applying entries of every
record. Version strings are never compared.

**Completeness is visible.** Every pull request in the range that no record
names, neither under `changes` nor under `no_author_change`, is listed in
`uncurated[]`. An empty `uncurated[]` means every change in the range was
read for the record; a non-empty one tells the author to read those
CHANGELOG entries. A malformed record is listed under `record_problems[]`
and its malformed entries are not applied.
A record that breaks the one-entry-per-pull-request rule above (several
entries not all marked `split: true`, a lone entry marked `split: true`, or
a pull request both under `changes` and `no_author_change`) is malformed.

## The command

```bash
lean-pkg doctor --upgrade-from 0.10.0                 # the running checkout, 0.10.0 to HEAD
lean-pkg doctor --upgrade-from 0.10.0 --to 0.11.0 --json
lean-pkg doctor --upgrade-from 4c541b8 --to 48836b7   # two commits, one version string
lean-pkg doctor --upgrade-from 0.9.0 --source ~/src/lean-pkg
```

- OLD and NEW are a version (`0.10.0` or `v0.10.0`: the tag, else the
  commit whose subject is `release: 0.10.0`) or any revision. NEW
  defaults to `HEAD`; uncommitted changes are not seen.
- `--source DIR`: a clone of the lean-pkg repository; default, the git
  checkout the running lean-pkg is imported from. A wheel install has no
  history: pass a clone. OLD must be an ancestor of NEW.
- Read-only: no registry, no network, nothing written. Exit 0 with the
  report, 1 on a refusal (an unknown revision, OLD not an ancestor,
  `--source` not a git repository), 2 for `--to`/`--source` without
  `--upgrade-from`.
- `doctor` is the place because the 1.0 command tree names it the one
  discovery point for migrations (docs/design/1.0-command-tree.md,
  section 6, item 5).

`--json` (schema `lean-pkg-upgrade/1`):

| Key | Content |
|---|---|
| `from`, `to` | `{rev, commit, version}` |
| `same_version` | the two version strings are equal |
| `mechanical.version`, `.requires_python` | `{from, to, changed}` |
| `mechanical.dependencies`, `.scripts` | `{added, removed}` |
| `mechanical.formats` | `{added {family: marker}, removed, changed {family: {from, to}}}` |
| `mechanical.named` | `registry_version`, `registry_versions_read`, `formalization_yaml_version`: `{from, to, changed}` |
| `mechanical.keys` | per on-disk key list: `{added, removed}` |
| `mechanical.commands` | `{added, removed, renamed {old: new}, options {path: {added, removed, renamed, moved}}}` (`moved`: an option that went with a mode split off as its own command, `MODE_ALIASES`), or null when a tree has no help goldens |
| `mechanical.json_keys` | `{added, removed, changed {command: {added, removed}}}`, or null |
| `mechanical.aliases` | `commands`, `flags`, `implied_verbs`: `{added, removed}` |
| `mechanical.data_files` | `{added, removed, changed}` (paths under `src/lean_pkg/`) |
| `behavior[]` | the applying record entries, each with `record`, `release`, `commit` |
| `breaking[]` | the ids of the applying entries with `breaking: true` |
| `uncurated[]` | `{pr, commit, subject}` |
| `record_problems[]`, `records_at` | validation problems of the records, and the commit they were read at |
| `not_derivable[]` | what the mechanical half cannot see |

## Limits and later work

- Formats that carry no version marker (the lower-tier receipt, the event
  log; section 6 of the command-tree proposal) show up only through their
  key lists, and only for the lists named above. When those formats gain
  a `schema` key the mechanical half sees them with no change here.
- Option parsing reads the option lines of the help goldens; a flag that
  is hidden from help (`argparse.SUPPRESS`) is invisible, as it is to
  users.
- The record is only as good as its authors. `uncurated[]` makes a gap
  visible; a reviewer's checklist item ("does this PR need a
  docs/upgrades entry?") closes it.
- A wheel-only install could carry the mechanical surface of its own
  version as package data, so that a preflight needs only the older
  checkout. Not built: every current user runs from a checkout.
