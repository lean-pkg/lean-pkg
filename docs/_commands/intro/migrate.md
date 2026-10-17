| Form | What it does | Key flags |
|---|---|---|
| `migrate` | upgrade the registry schema in place to version 3; v1 to v2 adds `license_spdx`, `formalization_yaml`, `palomar[]`, `doi`, `reservoir`, `consent[]`, v2 to v3 adds `claims_file`, `claims[]`, `claims_synced_commit`, all with empty defaults; additive and idempotent; a `local_path` stored before 0.6 as a literal `~/x` (which resolved to `$HOME/~/x`) is rewritten through the `--path` normaliser to its `$HOME`-relative form (`~user/...` is left alone); the `registry-migrated` event records it | `[--dry-run]` |
