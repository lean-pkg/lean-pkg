# 0004: Copyright and license confirmed by Auburn University

Date: 2026-10-08. Status: accepted.

## Context

lean-pkg was developed with support from NSF awards. Before a public
release, the maintainer asked Auburn University's office for intellectual
property (IPX) whether an open-source release needs approval. IPX replied
that Auburn has an ownership interest in the software, that releasing it
under an open-source license is consistent with the awards, and asked about
third-party code. The maintainer listed the one runtime dependency (PyYAML,
MIT, installed separately) and the two vendored Apache-2.0 files, and
proposed a joint copyright notice.

## Decision

On 2026-10-08 IPX agreed to the proposed notice and to distribution under
the Apache License 2.0. The notice is:

    Copyright (c) 2026 Le Chen and Auburn University

It appears in `LICENSE` (appendix line), `NOTICE`, `REUSE.toml` and the
README's license section; a test keeps them identical.

## Consequences

- Gate G6 of the public-flip checklist (institutional determination) is met.
- Vendored third-party files keep their own notices; `NOTICE` lists them.
- A future change of holder or license needs a new decision.
