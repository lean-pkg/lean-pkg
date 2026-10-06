---
name: Index entry
about: Propose a verified package for the index (schema index-entry/1; see docs/index-catalog.md)
title: "index: <package name>"
labels: index
---

Field names below are the keys of `index/<name>/entry.yaml`. Trust is not
asked for: `lean-pkg index add` computes it from the receipts.

**`name`** (the directory under `index/`):

**`repo`** (public https URL, with a license GitHub detects):

**`pin.commit`** (40 hex, the commit the receipts bind) and **`pin.tag`** (or none):

**`lean_toolchain`** and **`mathlib_rev`** (as in `lean-toolchain` and `lake-manifest.json` at the pin):

**`license_spdx`**:

**`headline`** (one per line: fully qualified name, repository-relative `.lean` file, one-sentence summary):

**`receipts.lower`** (path under `.lean-receipts/`, committed at the pin or in an evidence-only descendant):

**`receipts.comparator`** (path, and its mechanism: `toolchain` or `legacy`):

**`palomar`** (id and version, if registered), **`doi`** (version DOI, if minted), **`reservoir`** (listing URL, if any):

**`formalization_yaml`** (path, if the package has one):

**`fields`** (lowercase tags, for example `probability`):

**`consent.recorded`**: every coauthor of the result has agreed to the listing (yes/no; the records stay private).

**AI assistance**: which tools were used and how (required; see the PR template).
