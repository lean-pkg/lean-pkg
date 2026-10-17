| Form | What it does | Key flags |
|---|---|---|
| `llms` | print the agent contract (`--json`: `{"ok": true, "text": ...}`). `--short`: a compact contract of about 40 lines cut from the full text by its section headers, never kept by hand: the title, the `PURPOSE` header and its first three lines, `STANDING RULES`, and the `SUBCOMMANDS` header with the first line of each subcommand (no flag continuations, examples, receipt schemas, event schema or exit codes); every line is a line of the full text; `--json` adds `"short": true` | `[--short]` |
