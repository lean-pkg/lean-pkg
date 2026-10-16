---
name: lean-axiom-audit
description: "Build an exact-inventory #print axioms audit for a Lean package (one command per advertised declaration, name equality against the live transcript, rejection of sorryAx, user axioms, missing, extra and duplicate names), parse wrapped and namespace-qualified transcripts correctly, and prove the audit can fail with a semantic negative control. Trigger phrases: print axioms audit, axiom audit false fail, wrapped axiom list, namespace-qualified transcript, exact inventory, AxiomCheck.lean, sorryAx, negative control, tamper test, ellipsis in printed types, pp.proofs.threshold."
license: Apache-2.0
---

# Exact-inventory axiom audit

## Problem

"`lake build` is green" says nothing about axioms, and a hand-picked list of
`#print axioms` commands stays green after a new theorem is added but left out.
Audits written naively also fail on correct code, which trains people to
ignore them.

## Why it happens

- The audit list drifts from the set of public theorems.
- Lean wraps long axiom lists across lines, so a line-based regex misses
  declarations.
- `#print axioms foo` inside `namespace Bar` prints `'Bar.foo' depends on`,
  so expected and observed names differ.
- A tamper test that fails for the wrong reason (missing import, instance
  failure, `decide` not reducing) looks like a passing negative control.
- `#print` of a structure elides proof arguments as `⋯` even with
  `pp.proofs true`, which breaks a printed-type inventory that bans the
  ellipsis.

## Rule

The audit compares the live transcript with an explicit inventory by exact
name equality and rejects: a missing name, an unexpected name, a duplicate,
`sorryAx`, any axiom outside the declared allowed set, and a malformed or
truncated transcript. It is not trusted until a negative control makes it
fail for the intended reason.

## How to apply

Inventory file, one line per advertised declaration:

```lean
import MyPkg
set_option pp.proofs.threshold 100000   -- no ⋯ in printed structures
#print axioms MyPkg.main_theorem
#print axioms MyPkg.helper_bound
```

Parser essentials:

```python
import re
def parse(transcript: str) -> dict[str, list[str]]:
    lines = []
    for raw in transcript.splitlines():
        if raw[:1].isspace() and lines:
            lines[-1] += " " + raw.strip()          # unwrap continuation lines first
        else:
            lines.append(raw)
    out = {}
    for line in lines:
        m = re.match(r"'(.+?)' depends on axioms: \[(.*)\]", line)
        if m:
            name = m.group(1)
            if name in out:
                raise ValueError(f"duplicate: {name}")
            out[name] = [a.strip() for a in m.group(2).split(",") if a.strip()]
        elif re.match(r"'(.+?)' does not depend on any axioms", line):
            out[line.split("'")[1]] = []
    return out
```

Qualify expected names with the enclosing namespace (or normalize both
sides), compare as sets, and check every axiom list against the allowed set,
usually exactly `propext`, `Classical.choice`, `Quot.sound`.

Negative control, in a scratch clone, never the publishable tree:

```bash
git clone --quiet . /tmp/audit-control && cd /tmp/audit-control
sed -i 's/^  exact final_step$/  sorry/' MyPkg/Main.lean
lake build MyPkg && lake lean AxiomCheck.lean > control.txt 2>&1
python3 audit.py control.txt   # must fail, naming sorryAx on MyPkg.main_theorem
```

Run the audit file with `lake lean`, not `lake env lean`: Lake lists the root
package's lib directory last on `LEAN_PATH` and Lean loads `MyPkg/` from the
first entry holding it, so under `lake env` a dependency's build directory
holding `MyPkg/` would be audited instead (`lake lean` passes explicit module
paths; see `lean-receipt-discipline` for the `LEAN_PATH` preflight).

For a mathematical mutation (a wrong coefficient, a reversed inequality),
require the failure to be the intended `False` or type mismatch; an earlier
unrelated error makes the control inconclusive. Keep the positive transcript
and the negative-control transcript as separate files: a receipt must be
derived from the positive one only.

## Anti-patterns

- A theorem count instead of name equality.
- Hand-editing the transcript to make a parser accept it.
- Accepting a nonzero exit as a detected mutation without reading the error.
- Restoring a tampered file in place instead of discarding the scratch clone.

## Verification

Positive control: the audit passes on the known-good package. Negative
control: an injected `sorry` fails with the specific `sorryAx` reason on the
specific name. Both outputs are kept.

Learned from: an exact-inventory audit that false-failed twice on correct
code (wrapped lists, namespace qualification) and arithmetic tamper controls
that initially failed for the wrong reason, August to October 2026.
