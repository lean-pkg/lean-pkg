# Security policy

lean-pkg is a registry and trust gate: a defect in it can let a package claim
evidence it does not have. Please report such defects privately.

## Reporting

Report privately through this repository's GitHub security advisories:
the **Security** tab, then **Report a vulnerability**
(<https://github.com/lean-pkg/lean-pkg/security/advisories/new>). Only the
maintainers see the report, and the fix can be prepared in a private fork
before the advisory is published. Do not open a public issue or pull request
for it, and do not send it by personal email.

If the form is not available, open a public issue that says only "I have a
security report; please open a private channel", with no details, and a
maintainer will set one up.

Include:

- the version (`lean-pkg --version` or the tag) and the command;
- a minimal registry, receipt or package that reproduces it, with private
  paths replaced by placeholders;
- what was accepted and what should have been refused.

You will get an acknowledgement within seven days and a fix or a public
advisory within ninety. Reporters are credited in the advisory unless they
ask not to be.

## Soundness bugs

The most serious report is a way to get a false "verified": any input that
makes lean-pkg record or display a trust level the evidence does not
support, for example a `verified-private` or `public` status, an index
trust level, or a verification page saying "verified", for a package whose
statement was not proved, was proved with an extra axiom or `sorry`, or was
proved about a different statement than the one compared. Report these
privately as above, even when the exploit needs a deliberately crafted
package; a crafted package is exactly the threat the gate exists for.

## In scope

- A false "verified", as above.
- Accepting a receipt that does not bind to the commit it claims.
- Accepting a comparator receipt whose logs, configs or digests do not match
  the bytes on disk.
- A `promote --yes` that flips visibility without the dry-run checklist.
- Any path by which a private path, host or user name leaks into a public
  artifact.

## Out of scope

- Lean, Lake, Mathlib, comparator, nanoda, Palomar, Reservoir and GitHub
  themselves: report to those projects (and tell us privately if lean-pkg
  could guard against it).
- A registry file edited by hand.

## Supported versions

| Version | Supported |
|---|---|
| latest minor (0.N.x) | yes |
| older | no; upgrade |

There is no bounty.
