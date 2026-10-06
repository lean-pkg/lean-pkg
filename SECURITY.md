# Security policy

lean-pkg is a registry and trust gate: a defect in it can let a package claim
evidence it does not have. Please report such defects privately.

## Reporting

- Email the maintainer of record listed in [MAINTAINERS.md](MAINTAINERS.md)
  (the address on the maintainer's GitHub profile), or use GitHub's private
  vulnerability reporting on this repository if it is enabled.
- Include the version (`lean-pkg --version` or the tag), the command, and a
  minimal registry or receipt that reproduces the problem.
- You will get an acknowledgement within seven days and a fix or a public
  advisory within ninety.

## In scope

- Accepting a receipt that does not bind to the commit it claims.
- Accepting a comparator receipt whose logs, configs or digests do not match
  the bytes on disk.
- A `promote --yes` that flips visibility without the dry-run checklist.
- Any path by which a private path, host or user name leaks into a public
  artifact.

## Out of scope

- Lean, Lake, Mathlib, Palomar, Reservoir and GitHub themselves.
- A registry file edited by hand.

## Supported versions

| Version | Supported |
|---|---|
| latest minor (0.N.x) | yes |
| older | no; upgrade |

There is no bounty.
