| Form | What it does | Key flags |
|---|---|---|
| `receipt` | produce lower-tier receipts; writes files in the package checkout, never the registry. Two forms, one pipeline: `receipt slurm` (a Slurm job) and `receipt systemd` (alias `receipt local`: a systemd --user service unit) | `slurm` `systemd` |
