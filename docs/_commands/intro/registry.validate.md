| Form | What it does | Key flags |
|---|---|---|
| `validate` | schema, enum, and history sanity (a lease's optional `contact`, when present, must be a string; the optional top-level `lean_pkg` must be a mapping and its `recommended`, when present, a version string `X.Y.Z`: see [`registry recommend`](registry-recommend.md)); invalid: `INVALID (N error(s))` before the `FAIL` lines and again last; exit 0 or 1 | |
