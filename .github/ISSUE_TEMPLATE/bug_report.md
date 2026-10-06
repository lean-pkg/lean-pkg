---
name: Bug report
about: A command did the wrong thing, or refused when it should not have
title: ""
labels: bug
---

**Version**: output of `pip show lean-pkg` or the tag, and your Python version.

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
