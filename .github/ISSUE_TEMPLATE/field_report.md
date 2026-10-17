---
name: Field report
about: What you saw using lean-pkg on a real package (several findings from one session are fine)
title: "field report: <package or workflow>"
labels: field-report
---

One issue per session on one package. Each finding becomes its own pull
request (CONTRIBUTING.md, "From a field report to a pull request"). Replace
private paths, host names and unposted paper names with placeholders. A way
to get a false "verified" is a security report: use the private advisory form
instead (SECURITY.md).

**Version**: `lean-pkg --version`, Python version, OS; Lean toolchain and Mathlib revision of the package.

**The package**: public URL, or a description if it is private (size in declarations, layout: Challenge/Solution, paper companion, library).

**What you were trying to do** (the goal, and the commands in order):

**Findings** (one numbered item each, with a severity: HIGH = wrong or
missing evidence accepted, data loss, or a blocked workflow with no
workaround; MEDIUM = a blocked step with a workaround, or a wrong
diagnostic; LOW = friction, unclear output, docs):

1. [SEVERITY] What happened, the exact command and its output (`--json` if it has it), and what you expected.
2. ...

**Workarounds you used**, if any:

**What worked well** (optional; it tells us what not to break):

**AI assistance**: whether an AI agent ran the commands or wrote this report, and which one, or "none".
