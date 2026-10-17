| Form | What it does | Key flags |
|---|---|---|
| `claims list [NAME]` | table of cached claims: id, kind, status, trust (derived from the receipts), pin, declaration; with `NAME` it also states the package's coverage (`synced`, `empty`, `unsynced` or `none`), and with none cached it prints `NAME: coverage: none (cached_claims: 0, claims.yaml: absent)`, any legacy `*claims.json` in the checkout, and the next command | |
