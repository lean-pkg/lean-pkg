# 0005: GitHub account successor until a deputy is named

Date: 2026-10-08. Status: accepted.

## Context

Decision 0002 makes continuity depend on deputies named in `MAINTAINERS.md`,
and gate G7 of the public-flip checklist asks for at least one named
successor. No deputy has been named yet, and the maintainer prefers to leave
the deputy line as "to be named" rather than remove it or name someone who
has not agreed.

## Decision

Until a deputy is named, succession is covered by the maintainer's GitHub
account successor (GitHub Settings, Account, Successor settings), who can
take over the maintainer's repositories, including this one, if the
maintainer can no longer act. `MAINTAINERS.md` keeps "Deputy: to be named".

## Consequences

- The project has a working succession path before a deputy exists; it
  covers continuity of the repository, not day-to-day releases.
- The thirty-day release rule of decision 0002 applies only once a deputy
  is named.
- Gate G7 is met when a named deputy is added to `MAINTAINERS.md`; until
  then the maintainer may sign G7 on the basis of this decision.
