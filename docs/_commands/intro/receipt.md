| Form | What it does | Key flags |
|---|---|---|
| `receipt` | produce lower-tier receipts; writes files in the package checkout, never the registry. Three forms, one pipeline: `receipt slurm` (a Slurm job), `receipt systemd` (alias `receipt local`: a systemd --user service unit) and `receipt ci` (inside a GitHub Actions job; `--collect RUN` fetches its receipt). `receipt check` reads them back: a published package's receipts checked from git alone, with no registry | `slurm` `systemd` `ci` `check` |
