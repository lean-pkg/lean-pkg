See also `lean-pkg-extract plan --repo SRC --commit COMMIT --manifest PATH`, a separate console script that plans a public Lean-verification repository from a mixed working repository, read-only and registry-free: [Extracting a public Lean repository](../extract.md).

`lean-pkg-extract ledger` and `lean-pkg-extract recheck` record and re-verify a declaration-level extraction ledger (`lean-pkg-extract-ledger/1`) for a library ported by hand from private modules: [Declaration-level extraction ledger](../extract.md#declaration-level-extraction-ledger).

`lean-pkg-extract decls` writes the target modules from the same map into a new directory (with `--scaffold NAME`, a whole package: pins copied from the source commit, `git init`, nothing committed), staged beside it and scanned with public-safety before it is moved into place, and records the ledger of what it wrote: [Writing the target modules](../extract.md#writing-the-target-modules-decls).
