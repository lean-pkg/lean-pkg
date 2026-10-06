---
name: statement-vs-use-span-audit
description: "Audit each lemma statement against every one of its use sites (the dual of checking a use against the cited statement), ranking labels by citation count times distance so the long-range drifts are reviewed first; applies to a manuscript before formalization and to the hypothesis interfaces of a partial Lean formalization. Trigger phrases: lemma stated more narrowly than used, statement does not cover the use site, specification-use mismatch, hypothesis drift, joint continuity used but only fixed-t proved, inequality applied outside its stated scope, false justification with a true conclusion, span audit, strengthen the statement not the use."
license: Apache-2.0
---

# Statement-versus-use span audit

## Problem

There are two directions of hypothesis checking. The usual one asks whether
an invocation satisfies the cited result's hypotheses; the fix lands at the
use site. The other asks whether your own lemma, true as written, is strictly
narrower than what you later rely on; the fix lands at the statement. The
second defect exists only in the relation between two passages that may be
thousands of lines apart, so a conclusion-directed review never sees it.

## Why it happens

Three sub-patterns:

1. **Stated narrower than used.** The lemma proves continuity in `x` at fixed
   `t`; two later sites need joint continuity in `(t, x)`.
2. **Stated form differs from invoked form.** A bound stated for one kernel is
   invoked for a difference of two kernels.
3. **True conclusion, false justification.** The displayed conclusion is
   right; the sentence justifying it asserts something false. Checking or
   formalizing the conclusion cannot catch this.

A complete Lean formalization catches 1 and 2 by unification. A partial one,
where the lemma is a hypothesis field, does not (see
`lean-partial-formalization-boundary`).

## Rule

Rank every label by `number_of_uses × max_distance_to_a_use`, and for the top
of the list ask a reviewer one narrow question per label: does the statement
**as written** cover each invocation? If not, strengthen the statement; do not
patch the use.

## How to apply

Rank labels in a LaTeX source:

```python
import re, collections
src = open("main.tex", encoding="utf-8", errors="replace").read().split("\n")
defn = {}
for i, line in enumerate(src, 1):
    for m in re.finditer(r"\\label\{([^}]+)\}", line):
        defn.setdefault(m.group(1), i)
uses = collections.defaultdict(list)
for i, line in enumerate(src, 1):
    for m in re.finditer(r"\\(?:eq|c|auto)?ref\{([^}]+)\}", line):
        if defn.get(m.group(1)) != i:
            uses[m.group(1)].append(i)
rows = sorted(((len(v) * max(abs(x - defn[k]) for x in v), k, len(v))
               for k, v in uses.items() if k in defn), reverse=True)
for score, label, n in rows[:25]:
    print(f"{score:>9}  {label:<40} uses={n}")
```

For a Lean development, the analogue is every hypothesis-structure field or
named hypothesis against each `apply`/`exact` site that discharges a goal
with it. Then brief one reviewer per label:

> Here is the statement of `<label>` verbatim and its N use sites verbatim.
> For each site answer only: does the statement as written cover this use?
> Do not judge whether the mathematics is true. Reply SITE / COVERED /
> NOT-COVERED / reason.

## Anti-patterns

- Fixing at the use site with an inline caveat while other sites cite the
  narrow statement directly.
- Treating "the conclusion is true" as closing sub-pattern 3.
- Numeric constant tracking as the instrument: none of these is a numeric
  error.

## Verification

Positive control: run the ranking on a label already known to have drifted.
If it does not rank high, the metric or the parser is wrong. Then work the
list top-down and record a verdict per label.

Learned from: three defects a coauthor reported in a long manuscript, all of
this class, all past a Lean closure certificate and several review panels;
retroactive ranking placed them in the top dozen of about two hundred labels,
August 2026.
