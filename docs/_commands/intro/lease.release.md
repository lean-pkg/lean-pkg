| Form | What it does | Key flags |
|---|---|---|
| `lease release NAME` | release a lease; holder-only unless forced; `--force` needs `--reason`; `released_by` is the acting `--agent`, else `force` | `[--agent]` `[--force --reason TEXT]` |

Exporting or adopting a public repository was `repo export NAME DIR` / `--adopt-repo DIR` before 1.0; it is [`repo export`](repo-export.md) / [`repo adopt`](repo-adopt.md) now (the old spellings keep working).
