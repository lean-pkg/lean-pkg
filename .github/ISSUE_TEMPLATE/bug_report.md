---
name: Bug report
about: A command did the wrong thing, or refused when it should not have
title: ""
labels: bug
---

`lean-pkg report --command "ARGS"` fills this template for you, with private
paths redacted. A way to get a false "verified" is a security report: use the
repository's private advisory form instead (SECURITY.md).

Which track are you on? Tick one (`lean-pkg report` leaves this for you to tick):

- [ ] Paper: formalizing the results of a paper, posted or not (a paper companion, coverage `kind: paper`)
- [ ] Lean package: a library with no manuscript of its own, such as a Mathlib contribution or a public GitHub repository (coverage `kind: library`)
- [ ] Not sure, or neither (say which workflow below)

**Version**: output of `lean-pkg --version` (the release and the build that runs: commit for an editable install, install path otherwise), and your Python version.

**Command** (exact, with flags; redact private paths):

```
lean-pkg ...
```

**What happened** (paste the output, including any JSON reason):

**What you expected**:

**Minimal registry or receipt** that reproduces it, if the problem is about
validation (attach a file; replace private paths with placeholders):

**Checklist**
- [ ] I ran the command with `--json` and included the full reason.
- [ ] The registry passes `lean-pkg validate` apart from this problem.
