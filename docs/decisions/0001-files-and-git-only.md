# 0001: Files and git only

Date: 2026-10-05. Status: accepted.

## Context

A registry and trust gate must remain readable, auditable and forkable for
decades, by people and by agents, without depending on any service staying
up. Hosted databases, web applications and bespoke APIs all fail that test;
plain files under version control pass it.

## Decision

Every piece of state lean-pkg owns is a file, and git is the only database:
the registry (YAML), the event log (JSON lines), receipts (JSON next to the
code they certify, under `.lean-receipts/`), comparator configurations and
logs, the catalog entries (one directory per package), the documentation, and
these decisions. The tool reads and writes files; it never talks to a server
that holds state. External services (GitHub, Palomar, Reservoir, Zenodo) are
consulted for facts but never trusted as the record.

## Consequences

- Any command can be re-run from a clone; evidence is re-derived from bytes.
- Schema changes are migrations of files (`lean-pkg migrate`), versioned and
  idempotent.
- No web application, no accounts, no uptime. Search and views are generated
  from the files.
- Splitting the catalog into its own repository later is a configuration
  change, not a migration.
