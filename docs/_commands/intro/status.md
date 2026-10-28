| Form | What it does | Key flags |
|---|---|---|
| `status NAME` | full record plus the last five history entries; a lease that has lapsed ends the output with `lease: EXPIRED <age> ago (<agent>); renew no longer works: lean-pkg lease claim ...`, one lapsing within 2 h with `lease: expires in <time> (<agent>); keep it: lean-pkg lease renew ...`, and `--json` carries `lease_state` {state: `live`\|`expiring`\|`expired`\|`invalid`\|`none`, seconds_left}; in a registry git clone every lease line (then also `lease: live, expires in <time> (<agent>)` and `lease: none`) names the registry commit it was read from, `(as of registry <sha7>, committed 6h ago)`, `--json` adds `registry_state`, and a clone behind its last-fetched `origin/<branch>` prints one stderr warning (see [Lease state on a stale clone](#lease-state-on-a-stale-clone)) (optional keys such as the DOI `track` appear when set; `--fields track` reads it, null when unset); `lean-pkg status --help` lists the record's top-level keys (see [Package record](../cli.md#package-record)). `--fields a,b,c` prints only those keys: dotted paths reach into mappings (`gate.receipt`, `lease.agent`, the derived `lease.expires_at`, the optional `lease.contact`; through a null lease, or for a lease without a contact, the value is null); the aliases named after the table columns, `pin` (`PIN`) for `mathlib_pin`, `vis` (`VIS`) for `visibility`, `reaudit` (`REAUDIT`) for `reaudit_after`, `expires` and `lease.expires` (`EXPIRES` in `list`/`board`/`leases`) for `lease.expires_at`, and `lease.claimed` (the `CLAIMED` column of `leases`) for `lease.claimed_at`, are accepted by `status`, `list` and `board`, and the output and the `fields` list carry the record key; `--json` keeps the record's nesting (`package` holds just the selected sub-tree, plus an additive `fields` list), text mode prints them as YAML without the history lines; an unknown field, top-level or nested, is a usage error (exit 2) listing the valid keys at that level. `--fields` trims fields; `--no-claims` drops the `[bulk]` keys (`claims`) from the record, JSON or text, with every other key unchanged (`--json` adds an `omitted` list). Text mode adds a `branch: evaluating branch X, not the default D` line when the checkout is off its default branch or detached; `--json` (without `--fields`) always adds `branch: {current, default, detached, is_default}` (`current` null when detached, `default` null when none can be determined); without `--fields`, text mode prints a `scope:` block after the history lines and `--json` adds `scope` {gate {receipt, commit, head, head_is_gated_commit, readable}, publication {status, visibility, publication_hold, github}, product {kind: `paper`\|`library`\|null, source, note}, comparator (null, or {receipt, readable, commit, mechanism, configs[] {path, theorem_names[], definition_names[]}, theorem_names[], definition_names[], claimed_theorems, claimed_theorems_covered, reusable_theorems, reusable_theorems_covered, whole_package: false, note}), claims {total, by_status, synced_commit, note}}: the gated source commit (the recorded gate receipt's), the recorded privacy and publication hold (offline: GitHub is not queried; `audit` asks gh), the product the page certifies (`product: library` or `paper`, read from `kind` in `coverage.yaml` committed at HEAD, paper when absent; `not declared` with the reason when there is no committed or readable file; never the DOI `track`; see [Two tracks](../design/two-tracks.md)), the exact declarations the recorded comparator receipt compared and how many claimed theorem/lemma declarations they cover by the `comparator coverage` rule (a comparator compares only its `theorem_names`, never the whole package: `compared exactly N declaration(s), not the whole package`), and the claims' counts by status (`claim counts: ...; counts, not completion percentages`); when a cached claim uses `hypotheses`, `discharges` or `witness` (proposal 0003), also the claims ledger's counts at the promote stage, `claims: 3 (1 verified, 2 conditional; outstanding hypotheses: Hyp.B) (claims ledger, promote stage: ready)`, and `--json` `scope.claims.ledger` {stage, package_status, total, counts, outstanding_hypotheses} ([Conditional claims](../lifecycle.md#conditional-claims)) | `[--fields a,b,c]` `[--no-claims]` |

For the `scope:` block, `--json` adds `scope.product` {kind, source, note}:
the product the page certifies (`paper` or `library`), read from the committed
`coverage.yaml` at HEAD (`kind: paper` is the default). With no committed or
readable file, the product is `null` and the output says `not declared` with a
reason; status never refuses. This is separate from the record's DOI `track`.

Without `--fields`, text mode also prints a `producer` block, one line per
gate receipt: the lean-pkg that wrote it (its `producer.lean_pkg`, or
`producer unknown`) against the running one, and `--json` adds
`producer_skew` {running, receipts[] {role, receipt, readable, producer,
producer_record, direction, breaking[], other_breaking[], level, message,
command}, warnings[], skew, data_problem}. A line starts with `WARN` when an
older producer lies before a breaking upgrade-record entry that touches
receipts, gate or claims (it names the entries and `lean-pkg doctor
--upgrade-from vPRODUCER --source DIR`) and when the producer is newer than
the running lean-pkg; with `--json` those lines go to stderr. Never a
refusal; see [Which lean-pkg wrote a receipt](../receipts.md#which-lean-pkg-wrote-a-receipt-producer-skew).

### Lease state on a stale clone

Read-only commands never fetch, so the lease `status` shows is the one in the
local registry clone. When the registry is a git clone, the lease line names
the commit it was read from and how old that commit is:

```
lease: live, expires in 21h (vera) (as of registry 3f2a9c1, committed 3h ago)
```

`, registry file modified` is added when the registry file has uncommitted
changes. `--json` adds `registry_state` {root, commit, committed_at,
age_seconds, branch, upstream, behind, ahead, dirty}, counted from local refs
only: `branch` is null on a detached HEAD, and `upstream`, `behind` and
`ahead` are null without a local `origin/<branch>` ref. It is null when the
registry is not in a git work tree, and under `--registry-snapshot`. A clone
that is behind the `origin/<branch>` it last fetched prints one stderr line:

```
lean-pkg: WARN: registry clone is 1 commit(s) behind origin/main as last fetched (lease state may be stale): git -C /path/to/registry pull --ff-only, or lean-pkg doctor --fetch
```

A clone that was never fetched looks up to date to this check; the commit and
its age in the stamp are what to compare with another machine.
`gate NAME --check` and `lease list` say the same, and so does the lease
warning any other command prints for a lapsed or lapsing lease.
