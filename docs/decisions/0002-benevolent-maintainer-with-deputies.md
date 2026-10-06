# 0002: Benevolent maintainer with named deputies

Date: 2026-10-05. Status: accepted.

## Context

The project needs decisions to be made quickly and consistently while it is
small, and needs continuity if the maintainer is unavailable. Committees slow
a young project; a single point of failure endangers an old one.

## Decision

Le Chen is the maintainer of record and decides on scope, releases and the
trust rules. Deputies are named in `MAINTAINERS.md` by area; a deputy may cut
a release if the maintainer is unavailable for thirty days, and may merge
changes in their area. Disagreements are recorded as decisions here, with
the maintainer's ruling and the reasoning.

## Consequences

- One accountable person per decision; a written trail for each.
- Succession is explicit and tested by the thirty-day rule.
- This decision is revisited when the catalog has area maintainers from more
  than one institution.
