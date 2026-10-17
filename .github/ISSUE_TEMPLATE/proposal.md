---
name: Proposal
about: Discuss a format, command-removal or trust-model change before writing a design proposal
title: "proposal: <short title>"
labels: proposal
---

A schema change, a removed or renamed command, flag or `--json` key, or a
change to what a receipt or gate trusts needs a design proposal
(docs/proposals/README.md). Use this issue to check the idea first; the
proposal itself is a pull request adding `docs/proposals/NNNN-short-title.md`
from the template. Additions (a new command or flag) can go straight to a
pull request.

**The problem** (a concrete case: an issue, a field report, a command and its output):

**The change you propose** (as a user or script would see it):

**Kind**: schema change / command, flag or JSON key removal or rename / trust-model change / not sure

**Who is affected** (existing registries, receipts, companion repositories, scripts parsing `--json`) and how they would migrate:

**Trust model**: could this let something be accepted that is refused today? Write "unknown" if you have not established it.

**AI assistance**: which tools helped, and how, or "none".
