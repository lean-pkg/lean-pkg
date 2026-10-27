# lean-pkg-extract ledger --method types -- the elaborated-type comparison method types/1 (proposal 0009, S4)
"""The comparison method `types/1` of the extraction ledger (proposal 0009, stage S4, #329).

lean-pkg never runs Lean for it. `--emit-script DIR` writes two Lean programs, `DIR/source.lean` and
`DIR/target.lean`; the author runs each in its own checkout, at the commit the ledger compares, after
building the modules:

    cd SRC && git checkout C && lake build Src.A Src.B && lake env lean --run DIR/source.lean > source.out
    cd TDIR && lake build New.A New.B && lake env lean --run DIR/target.lean > target.out

and `--from-output source.out target.out` reads the two outputs back. Each program imports its side's
mapped modules (`importModules`, core Lean only) and, for every declaration the text comparison pairs
(both sides present, a named declaration), prints one canonical serialization of its elaborated type:
the `Expr` walked structurally, every constant renamed by the ledger's map on the source side (the
rename map on a name and every name under it, so generated names such as `foo.match_1` follow; else the
longest namespace prefix), binder names left out (alpha-equivalence), binder annotations kept, universe
parameters numbered by position, `mdata` dropped, private names marked as private and compared by their
user names. An inductive type (a `structure` or `class` too) carries its constructors' types. Nothing is
pretty-printed: the pretty printer's output is neither canonical nor round-trip safe.

Output contract (`lean-pkg-extract-types/1`), one JSON object per line, nothing else:

    {"schema": "lean-pkg-extract-types/1", "side": "source"|"target", "script_sha256": H,
     "commit": "<40 hex git HEAD of the working directory>"|null, "dirty": bool|null,
     "lean": Lean.versionString, "lean_githash": Lean.githash, "count": N}
    {"i": k, "decl": KEY, "kinds": ["theorem", ...], "type": SERIALIZATION}    (k = 0 .. N-1, in order)
    {"i": k, "decl": KEY, "missing": [NAME, ...]}                              (not in the environment)
    {"done": N}

`script_sha256` is the SHA-256 of the program with its own hash left as the placeholder; lean-pkg
regenerates the program for the ledger it computes and refuses an output whose hash, side, commit,
declaration list or line structure does not match, a dirty or non-git checkout, two different Lean
builds, and a truncated or duplicated output. The ledger records the SHA-256 of each serialization.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

from . import lean_scan

TYPES_OUTPUT_SCHEMA = "lean-pkg-extract-types/1"
SIDES = ("source", "target")
SCRIPT_FILES = {"source": "source.lean", "target": "target.lean"}
SHA_PLACEHOLDER = "__LEAN_PKG_SCRIPT_SHA256__"
DATA_PLACEHOLDER = "__LEAN_PKG_PLAN__"
# Lean's constant kinds whose elaborated type is the whole statement: a theorem's proof is irrelevant, an
# axiom and an opaque constant have no unfoldable value, an inductive type is its type and constructors.
# A `def`, `abbrev` or `instance` (kind def) has a value the types do not compare.
STATEMENT_KINDS = frozenset({"theorem", "axiom", "opaque", "inductive"})
CONST_KINDS = STATEMENT_KINDS | {"def", "quot", "constructor", "recursor"}
HEADER_KEYS = ("schema", "side", "script_sha256", "commit", "dirty", "lean", "lean_githash", "count")
TYPE_ROW_KEYS = ("i", "decl", "kinds", "type")
MISSING_ROW_KEYS = ("i", "decl", "missing")
HEX64 = re.compile(r"[0-9a-f]{64}")
HEX40 = re.compile(r"[0-9a-f]{40}")
SCHEMA_RE = re.compile(r"lean-pkg-extract-types/(\d+)")

LEAN_PROGRAM = """\
-- lean-pkg-extract types/1: the __SIDE__ side of an extraction ledger (proposal 0009). Generated; do not edit
-- (the output carries this program's hash, and lean-pkg-extract refuses an output of an edited program).
--
-- Run it in the __SIDE__ checkout, clean and at the commit the ledger compares, after building the modules:
--   lake build __BUILD__
--   lake env lean --run __FILE__ > __SIDE__.out
-- then pass both outputs to `lean-pkg-extract ledger --method types --from-output source.out target.out`.
-- It reads the compiled environment and `git rev-parse HEAD` / `git status`; it writes only to stdout.
import Lean

open Lean

namespace LeanPkgExtractTypes

def scriptSha256 : String := "__LEAN_PKG_SCRIPT_SHA256__"

/-- The plan: this side's modules, the name map (source side only) and the declarations to print. -/
def planJson : String := __LEAN_PKG_PLAN__

structure Plan where
  side : String
  modules : Array Name
  renames : List (Name × Name)
  namespaces : List (Name × Name)
  decls : Array (String × Name × Array Name)

def toName (j : Json) : Except String Name := do
  let parts ← j.getArr?
  parts.foldlM (init := Name.anonymous) fun n c => do return Name.mkStr n (← c.getStr?)

def toPair (j : Json) : Except String (Name × Name) := do
  let a ← j.getArr?
  if a.size != 2 then throw "a map entry is not a pair"
  return (← toName a[0]!, ← toName a[1]!)

def toDecl (j : Json) : Except String (String × Name × Array Name) := do
  let key ← (← j.getObjVal? "key").getStr?
  let m ← toName (← j.getObjVal? "module")
  let names ← (← (← j.getObjVal? "names").getArr?).mapM toName
  return (key, m, names)

def readPlan : Except String Plan := do
  let j ← Json.parse planJson
  return {
    side := ← (← j.getObjVal? "side").getStr?
    modules := ← (← (← j.getObjVal? "modules").getArr?).mapM toName
    renames := (← (← (← j.getObjVal? "renames").getArr?).mapM toPair).toList
    namespaces := (← (← (← j.getObjVal? "namespaces").getArr?).mapM toPair).toList
    decls := ← (← (← j.getObjVal? "decls").getArr?).mapM toDecl }

def q (s : String) : String := (Json.str s).compress

/-- The entry of `tbl` whose old name is the longest prefix of `n` (`n` itself only when not `proper`). -/
def best (n : Name) (tbl : List (Name × Name)) (proper : Bool) : Option (Name × Name) :=
  tbl.foldl (init := none) fun acc (o, t) =>
    if o.isPrefixOf n && (!proper || o != n) then
      match acc with
      | some (o', _) => if o.getNumParts > o'.getNumParts then some (o, t) else acc
      | none => some (o, t)
    else acc

/-- A constant's name under the map: a renamed name or a name under it, else the namespace map. -/
def mapUser (p : Plan) (n : Name) : Name :=
  match best n p.renames false with
  | some (o, t) => n.replacePrefix o t
  | none =>
    match best n p.namespaces true with
    | some (o, t) => n.replacePrefix o t
    | none => n

def cname (p : Plan) (n : Name) : String :=
  match privateToUserName? n with
  | some u => "(P " ++ q (toString (mapUser p u)) ++ ")"
  | none => q (toString (mapUser p n))

def idx (ps : List Name) (x : Name) : Option Nat :=
  let rec go : List Name → Nat → Option Nat
    | [], _ => none
    | y :: ys, i => if y == x then some i else go ys (i + 1)
  go ps 0

partial def lvl (ps : List Name) : Level → String
  | .zero => "z"
  | .succ l => "(s " ++ lvl ps l ++ ")"
  | .max a b => "(m " ++ lvl ps a ++ " " ++ lvl ps b ++ ")"
  | .imax a b => "(i " ++ lvl ps a ++ " " ++ lvl ps b ++ ")"
  | .param x =>
    match idx ps x with
    | some i => "(u " ++ toString i ++ ")"
    | none => "(un " ++ q (toString x) ++ ")"
  | .mvar _ => "(?)"

def binfo : BinderInfo → String
  | .default => "d"
  | .implicit => "i"
  | .strictImplicit => "s"
  | .instImplicit => "c"

/-- The canonical serialization: binder names left out, universe parameters by position. -/
partial def ser (p : Plan) (ps : List Name) : Expr → String
  | .bvar i => "#" ++ toString i
  | .fvar _ => "(?)"
  | .mvar _ => "(?)"
  | .sort l => "(S " ++ lvl ps l ++ ")"
  | .const n ls => "(C " ++ cname p n ++ " [" ++ " ".intercalate (ls.map (lvl ps)) ++ "])"
  | .app f a => "(A " ++ ser p ps f ++ " " ++ ser p ps a ++ ")"
  | .lam _ t b bi => "(L " ++ binfo bi ++ " " ++ ser p ps t ++ " " ++ ser p ps b ++ ")"
  | .forallE _ t b bi => "(F " ++ binfo bi ++ " " ++ ser p ps t ++ " " ++ ser p ps b ++ ")"
  | .letE _ t v b _ => "(E " ++ ser p ps t ++ " " ++ ser p ps v ++ " " ++ ser p ps b ++ ")"
  | .lit (.natVal n) => "(N " ++ toString n ++ ")"
  | .lit (.strVal s) => "(T " ++ q s ++ ")"
  | .mdata _ e => ser p ps e
  | .proj s i e => "(J " ++ cname p s ++ " " ++ toString i ++ " " ++ ser p ps e ++ ")"

def kindOf : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"

def typeOf (p : Plan) (ci : ConstantInfo) : String :=
  "(" ++ toString ci.levelParams.length ++ " " ++ ser p ci.levelParams ci.type ++ ")"

/-- A declaration by name, or by its private name in its module. -/
def find? (env : Environment) (m n : Name) : Option ConstantInfo :=
  (env.find? n).orElse fun _ => env.find? (Name.mkNum (privateHeader ++ m) 0 ++ n)

/-- (kind, serialization) of one constant: its mapped name and type; an inductive's constructors too. -/
def decl (p : Plan) (env : Environment) (m n : Name) : Option (String × String) := do
  let ci ← find? env m n
  let mut out := "(D " ++ cname p ci.name ++ " " ++ typeOf p ci
  if let .inductInfo v := ci then
    for c in v.ctors do
      match env.find? c with
      | some cc => out := out ++ " (K " ++ cname p c ++ " " ++ typeOf p cc ++ ")"
      | none => out := out ++ " (K? " ++ cname p c ++ ")"
  return (kindOf ci, out ++ ")")

/-- The first line of a git command's output in the working directory, or none. -/
def git (args : Array String) : IO (Option String) := do
  try
    let o ← IO.Process.output { cmd := "git", args := args }
    return if o.exitCode == 0 then some ((o.stdout.splitOn "\\n").headD "") else none
  catch _ => return none

end LeanPkgExtractTypes

open LeanPkgExtractTypes in
def main : IO UInt32 := do
  let p ← match readPlan with
    | .ok p => pure p
    | .error e => IO.eprintln s!"lean-pkg-extract types: unreadable plan: {e}"; return 2
  initSearchPath (← findSysroot)
  let commit ← git #["rev-parse", "--verify", "HEAD^{commit}"]
  let status ← git #["status", "--porcelain", "--untracked-files=no"]
  let env ← importModules (p.modules.map fun m => ({ module := m } : Import)) {}
  IO.println (Json.mkObj [("schema", "lean-pkg-extract-types/1"), ("side", p.side),
    ("script_sha256", scriptSha256), ("commit", match commit with | some c => Json.str c | none => Json.null),
    ("dirty", match status with | some s => Json.bool (!s.isEmpty) | none => Json.null),
    ("lean", Lean.versionString), ("lean_githash", Lean.githash), ("count", toJson p.decls.size)]).compress
  let mut i : Nat := 0
  for (key, m, names) in p.decls do
    let mut kinds : Array Json := #[]
    let mut parts : Array String := #[]
    let mut missing : Array Json := #[]
    for n in names do
      match decl p env m n with
      | some (k, s) =>
        kinds := kinds.push k
        parts := parts.push s
      | none => missing := missing.push (toString n)
    if missing.isEmpty then
      IO.println (Json.mkObj [("i", toJson i), ("decl", key), ("kinds", Json.arr kinds),
        ("type", " ".intercalate parts.toList)]).compress
    else
      IO.println (Json.mkObj [("i", toJson i), ("decl", key), ("missing", Json.arr missing)]).compress
    i := i + 1
  IO.println (Json.mkObj [("done", toJson p.decls.size)]).compress
  return 0
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _components(name: str) -> list[str]:
    """A Lean name's components as Lean stores them («» removed)."""
    parts = lean_scan.split_name(name) or [name]
    return [p[1:-1] if p.startswith("«") and p.endswith("»") else p for p in parts]


def lean_string(s: str) -> str:
    """A Lean string literal holding s."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


# ----------------------------------------------------------------------------------------------------
# the plan: which declarations are compared, and the program of each side

def plan(led: dict, names: dict) -> list[dict]:
    """The entries the types compare, in ledger order: both sides present and named (not an anonymous
    instance or an example). `names` maps a source key to (source names, target names), as `compute`
    fills it: the source names in the order of their mapped names, the target names sorted."""
    out = []
    for e in led["entries"]:
        if e["source"] is None or e["target_sha256"] is None or e["verdict"] not in ("IDENTICAL", "DIFFERS"):
            continue
        pair = names.get(e["source"])
        if not pair or not pair[0] or not pair[1]:
            continue
        out.append({"source": e["source"], "target": e["target"], "source_module": e["source_module"],
                    "target_module": e["target_module"], "source_names": list(pair[0]),
                    "target_names": list(pair[1])})
    return out


def program(side: str, items: list[dict], led: dict) -> tuple[str, str]:
    """(the Lean program of one side, its script_sha256)."""
    modules = sorted(r["module"] for r in led[side]["modules"])
    m = led["map"]
    data = {"side": side,
            "modules": [_components(x) for x in modules],
            "renames": [[_components(r["from"]), _components(r["to"])] for r in m["renames"]] if side == "source" else [],
            "namespaces": ([[_components(r["from"]), _components(r["to"])] for r in m["namespaces"]]
                           if side == "source" else []),
            "decls": [{"key": it[side], "module": _components(it[f"{side}_module"]),
                       "names": [_components(n) for n in it[f"{side}_names"]]} for it in items]}
    text = (LEAN_PROGRAM.replace("__SIDE__", side).replace("__FILE__", SCRIPT_FILES[side])
            .replace("__BUILD__", " ".join(modules) or "(no module)"))
    text = text.replace(DATA_PLACEHOLDER, lean_string(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                                                 separators=(",", ":"))), 1)
    sha = _sha(text)
    head, sep, tail = text.partition(SHA_PLACEHOLDER)
    return head + sha + tail, sha


def programs(led: dict, names: dict) -> tuple[list[dict], dict[str, tuple[str, str]]]:
    items = plan(led, names)
    return items, {side: program(side, items, led) for side in SIDES}


# ----------------------------------------------------------------------------------------------------
# reading an output back: strict

def read_output(data: bytes, where: str, refusals: list[str]) -> dict | None:
    """{"header": {...}, "rows": [...]} of one output, or None (refusals say why)."""
    n0 = len(refusals)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        refusals.append(f"{where}: not UTF-8")
        return None
    if not text:
        refusals.append(f"{where}: empty (did the program run? its errors go to stderr)")
        return None
    if not text.endswith("\n"):
        refusals.append(f"{where}: truncated (no final newline)")
        return None
    lines = text[:-1].split("\n")
    objs = []
    from . import extract as ex
    for k, line in enumerate(lines, 1):
        obj, err = ex.load_json_strict(line.encode("utf-8"), f"{where} line {k}")
        if err or not isinstance(obj, dict):
            refusals.append(f"{where} line {k}: not a line the program writes ({err or 'not an object'}); "
                            f"send only its stdout to the file")
            return None
        objs.append(obj)
    head = objs[0]
    schema = head.get("schema")
    if schema != TYPES_OUTPUT_SCHEMA:
        m = SCHEMA_RE.fullmatch(schema) if isinstance(schema, str) else None
        if m and int(m.group(1)) > 1:
            refusals.append(f"{where}: schema {schema} is newer than this lean-pkg reads ({TYPES_OUTPUT_SCHEMA}): "
                            f"upgrade lean-pkg")
        else:
            refusals.append(f"{where}: the first line is not a {TYPES_OUTPUT_SCHEMA} header")
        return None
    if not ex._keys(head, HEADER_KEYS, HEADER_KEYS, f"{where} header", refusals):
        return None
    if head["side"] not in SIDES:
        refusals.append(f"{where}: side {head['side']!r} is not source or target")
    if not (isinstance(head["script_sha256"], str) and HEX64.fullmatch(head["script_sha256"])):
        refusals.append(f"{where}: script_sha256 is not a SHA-256")
    if not (head["commit"] is None or (isinstance(head["commit"], str) and HEX40.fullmatch(head["commit"]))):
        refusals.append(f"{where}: commit is not a 40-hex commit or null")
    if head["dirty"] not in (True, False, None):
        refusals.append(f"{where}: dirty is not a boolean or null")
    for k in ("lean", "lean_githash"):
        if not isinstance(head[k], str):
            refusals.append(f"{where}: {k} is not a string")
    count = head["count"]
    if type(count) is not int or count < 0:
        refusals.append(f"{where}: count is not a non-negative integer")
        return None
    if len(refusals) != n0:
        return None
    body, last = objs[1:-1], objs[-1] if len(objs) > 1 else None
    if last is None or set(last) != {"done"}:
        refusals.append(f"{where}: truncated (no final done line; {len(objs) - 1} of {count} declaration(s) "
                        f"after the header)")
        return None
    if last["done"] != count or type(last["done"]) is not int:
        refusals.append(f"{where}: the done line says {last['done']!r}, the header {count}")
        return None
    rows, seen = [], set()
    for k, r in enumerate(body):
        w = f"{where} line {k + 2}"
        keys = TYPE_ROW_KEYS if "kinds" in r or "type" in r else MISSING_ROW_KEYS
        if not ex._keys(r, keys, keys, w, refusals):
            continue
        i = r["i"]
        if type(i) is not int:
            refusals.append(f"{w}: i is not an integer")
            continue
        if i in seen:
            refusals.append(f"{w}: declaration {i} is duplicated")
            continue
        seen.add(i)
        if i != len(rows):
            refusals.append(f"{w}: declaration {i} out of order (expected {len(rows)})")
            continue
        if not isinstance(r["decl"], str):
            refusals.append(f"{w}: decl is not a string")
        if "missing" in r:
            if not (isinstance(r["missing"], list) and r["missing"] and all(isinstance(x, str) for x in r["missing"])):
                refusals.append(f"{w}: missing is not a non-empty list of names")
        else:
            if not (isinstance(r["kinds"], list) and r["kinds"] and all(x in CONST_KINDS for x in r["kinds"])):
                refusals.append(f"{w}: kinds is not a non-empty list of Lean constant kinds")
            if not (isinstance(r["type"], str) and r["type"].startswith("(D ")):
                refusals.append(f"{w}: type is not a serialization the program writes")
        rows.append(r)
    if len(refusals) == n0 and len(rows) != count:
        refusals.append(f"{where}: {len(rows)} declaration line(s), the header says {count}")
    return {"header": head, "rows": rows} if len(refusals) == n0 else None


def load_outputs(paths: list[str], refusals: list[str]) -> dict[str, dict]:
    """{side: output} of the --from-output files: one per side, each read strictly."""
    got: dict[str, dict] = {}
    where_of: dict[str, str] = {}
    for p in paths:
        path = pathlib.Path(p).expanduser()
        try:
            data = path.read_bytes() if path.is_file() else None
        except OSError:
            data = None
        if data is None:
            refusals.append(f"--from-output {p}: not a readable regular file")
            continue
        out = read_output(data, f"--from-output {p}", refusals)
        if out is None:
            continue
        side = out["header"]["side"]
        if side in got:
            refusals.append(f"--from-output {p}: a second {side} output (the first is {where_of[side]})")
            continue
        got[side], where_of[side] = out, p
    for side in SIDES:
        if side not in got and not any(r.startswith("--from-output") for r in refusals):
            refusals.append(f"--from-output: no {side} output (pass the output of {SCRIPT_FILES[side]})")
    for side in got:
        got[side]["path"] = where_of[side]
    return got


def bind(outputs: dict, items: list[dict], progs: dict, led: dict, refusals: list[str],
         warnings: list[str]) -> dict | None:
    """{source key: (source type sha256, target type sha256, statement?)} from the two outputs, after
    checking that each was written by this ledger's program at the ledger's commit; None on a refusal."""
    n0 = len(refusals)
    for side in SIDES:
        o = outputs[side]
        h, where = o["header"], f"--from-output {o['path']} ({side})"
        if h["script_sha256"] != progs[side][1]:
            refusals.append(f"{where}: written by another program (script_sha256 {h['script_sha256'][:12]}, this "
                            f"ledger's {side} program is {progs[side][1][:12]}): emit it again with --emit-script "
                            f"and rerun it")
        want = led[side]["commit"]
        if h["commit"] is None:
            refusals.append(f"{where}: no commit (the program ran outside a git checkout); run it in the "
                            f"{side} checkout at {want}")
        elif h["commit"] != want:
            refusals.append(f"{where}: run at commit {h['commit']}, but the ledger compares {want}")
        if h["dirty"] is not False:
            refusals.append(f"{where}: the checkout had uncommitted changes to tracked files (dirty "
                            f"{h['dirty']!r}); its types are not those of {h['commit']}")
        rows = o["rows"]
        if [r["decl"] for r in rows] != [it[side] for it in items]:
            refusals.append(f"{where}: its declarations do not match the ledger's ({len(rows)} printed, "
                            f"{len(items)} compared)")
            continue
        for r in rows:
            if "missing" in r:
                refusals.append(f"{where}: {r['decl']}: {', '.join(r['missing'])} not in the environment (build "
                                f"the modules before running the program)")
            elif len(r["kinds"]) != len(items[r["i"]][f"{side}_names"]):
                refusals.append(f"{where}: {r['decl']}: {len(r['kinds'])} kind(s) for "
                                f"{len(items[r['i']][f'{side}_names'])} name(s)")
        pin = led[side]["lean_toolchain"]
        if pin is not None and not (pin == f"v{h['lean']}" or pin.endswith(f":v{h['lean']}")):
            warnings.append(f"types/1: the {side} program ran under Lean {h['lean']}, the {side} pins {pin}")
    s, t = outputs["source"]["header"], outputs["target"]["header"]
    if (s["lean"], s["lean_githash"]) != (t["lean"], t["lean_githash"]):
        refusals.append(f"--from-output: the two programs ran under different Lean builds (source {s['lean']} "
                        f"{s['lean_githash'][:12]}, target {t['lean']} {t['lean_githash'][:12]}); an elaborated "
                        f"type is only comparable under one toolchain")
    if len(refusals) != n0:
        return None
    out = {}
    for it, a, b in zip(items, outputs["source"]["rows"], outputs["target"]["rows"], strict=True):
        statement = all(k in STATEMENT_KINDS for k in a["kinds"] + b["kinds"])
        out[it["source"]] = (_sha(a["type"]), _sha(b["type"]), statement)
    return out


def types_block(outputs: dict, progs: dict) -> dict:
    s = outputs["source"]["header"]
    return {"lean": s["lean"], "lean_githash": s["lean_githash"],
            "source_script_sha256": progs["source"][1], "target_script_sha256": progs["target"][1]}
