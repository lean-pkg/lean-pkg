| Form | What it does | Key flags |
|---|---|---|
| `leases` | held leases, soonest expiry first, with `live` / `EXPIRED` state and a last `CONTACT` column (blank when the lease has none); `--json` rows carry `contact` (null when unset); read-only | `[--agent A]` `[--expiring HOURS]` (HOURS >= 0; a negative window exits 2) |
