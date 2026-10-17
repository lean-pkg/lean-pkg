# Governance

How lean-pkg is run: who may do what, how decisions are made, and how the
project gains maintainers. The current names are in
[MAINTAINERS.md](MAINTAINERS.md); the reasoning behind this model is
decision [0002](docs/decisions/0002-benevolent-maintainer-with-deputies.md).

## Roles

**Maintainer of record.** Le Chen. Decides scope, releases, the trust rules
and what merges; accepts or declines [design proposals](docs/proposals/README.md);
owns the repository settings and the release process
([RELEASING.md](RELEASING.md)). Accountable for every merged change, including
those the operations agent prepared.

**Maintainers.** People with merge rights who review and merge pull requests
and triage issues. Today the maintainer of record is the only maintainer;
adding a second one is an open goal (below).

**Deputy.** Named in MAINTAINERS.md for operations and releases. May cut a
release when the maintainer of record has been unavailable for thirty days
(decision 0002). The maintainer's GitHub account successor is the second
line of succession (decision
[0005](docs/decisions/0005-github-account-successor-until-a-deputy-is-named.md)).

**Area maintainers.** Reviewers of index entries in one mathematical area
(for example stochastic analysis or random matrices). None are named yet;
they will be listed in MAINTAINERS.md as the index grows.

**Contributors.** Anyone who opens an issue, a field report, a proposal or a
pull request, or reviews one. Reviews from contributors are welcome and
count: a reviewer need not be a maintainer. The rules are in
[CONTRIBUTING.md](CONTRIBUTING.md).

**AI operations agent.** The maintainer runs an AI coding agent (Anthropic's
Claude, through Claude Code) to do much of the routine work. It is a tool,
not a member, and it has no account of its own: it acts through the
maintainer's GitHub account, so everything it does is the maintainer's act
and responsibility. What it does, under the maintainer's standing
direction:

- writes code, tests and docs, and opens pull requests that disclose it
  (the commit trailer and the PR's *AI assistance* section);
- triages issues and field reports, reproduces findings and drafts fixes;
- merges a pull request only when CI is green and the change is one the
  maintainer has directed or approved, and cuts a release only when the
  maintainer has asked for one;
- drafts design proposals when asked.

What it does not do:

- accept or decline a design proposal, or change the trust rules (what a
  receipt proves, what a gate accepts) on its own initiative;
- name, add or remove maintainers or collaborators, or change repository
  settings, visibility or branch protection;
- answer a security report beyond acknowledging it, or publish an advisory;
- pass itself off as a person: its pull requests and comments carry an
  AI-assistance disclosure.

Contributors' own use of AI tools is governed by CONTRIBUTING.md (disclose,
understand, use your own words).

## How decisions are made

- **Ordinary changes** (fixes, new commands and flags, docs): a pull request,
  reviewed and merged by a maintainer once CI is green.
- **Format, command-removal and trust-model changes**: a
  [design proposal](docs/proposals/README.md), accepted by the maintainer of
  record before implementation.
- **Project decisions** (governance, licensing, history, succession): a
  dated record in [docs/decisions](docs/decisions/README.md).
- **Disagreements** are argued on the pull request or proposal. The
  maintainer of record rules, and the ruling and its reasoning are written
  into the proposal or a decision record. Once there are two or more
  maintainers, the goal is consensus among them; the maintainer of record
  keeps the deciding vote until this document is changed by a decision.

## Adding a maintainer

The project wants a second human maintainer, preferably from another
institution. The process:

1. **Who can be nominated.** Someone who has contributed for a sustained
   period: merged pull requests, reviews or index entries, and familiarity
   with the trust model. Anyone may nominate, including themselves, by
   writing to the maintainer of record (or by an issue, if the nominee
   agrees to a public one).
2. **Agreement.** The nominee agrees in writing to the responsibilities:
   review within a reasonable time, follow CONTRIBUTING.md and this
   document, keep two-factor authentication on their GitHub account, and
   handle security reports privately.
3. **Decision.** The maintainer of record decides. The appointment is
   recorded as a decision in docs/decisions, the name is added to
   MAINTAINERS.md, and the person is given write access to the repository
   (admin rights stay with the maintainer of record unless the decision says
   otherwise).
4. **Stepping down.** A maintainer may step down at any time by saying so;
   one inactive for a year may be moved to an emeritus line in
   MAINTAINERS.md after being asked.

Open item for the maintainer of record: who the second maintainer will be.
No one is named here until they have agreed.

## Changing this document

By pull request, accepted by the maintainer of record, with a decision
record when it changes who decides.
