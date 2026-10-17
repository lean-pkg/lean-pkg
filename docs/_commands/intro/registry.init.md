| Form | What it does | Key flags |
|---|---|---|
| `init` | bootstrap an empty registry file and its event log; refuses an existing registry unless `--force` and it holds no packages; the `registry-created` event records the registry file name and schema version (no host path), and `"forced": true` after `--force` | `[--force]` |
