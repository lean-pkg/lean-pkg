/-
Copyright 2026 Le Chen. SPDX-License-Identifier: Apache-2.0
-/
-- lean_pkg.controls: the Lean half (proposal 0014, the `controls/1` section of the verify log).
-- Elaborates ONE control file, as `lean FILE` would, with info trees on, and prints one line of facts:
--
--   lean --run ControlsProbe.lean KIND FILE BUILD_DIR [--theorem N]... [--target N]... [--expect S]
--     KIND      must-fail | refutes | compiles
--     BUILD_DIR the package's own build directory (<package>/.lake/build): a target must be declared in a
--               module whose .olean lies under it (the package's own library, any lean_lib)
--   prints
--     ctl-facts: {"exit":E,"errors":N,"first_error":T|null,"first_kind":K|null,"setup_error":S|null,
--                 "expect":"matched"|"not-matched"|"absent","sorry":B,"theorems":{N:{"found":B,"negation":B,
--                 "axioms":[...]}},"other_axioms":[...],"axioms":[...],"targets":{N:W|null},"ok":B,
--                 "message":M|null}
-- Run with LEAN_PATH=<the package's>. The job (controls.job_sh) adds id, kind, file, sha256 and seconds and
-- writes the `ctl:` line; a run that fails (exit code not 0, or a timeout) is replaced by a failed `ctl:`
-- line there. Core Lean only.
import Lean

open Lean Elab Meta

namespace ControlsProbe

def js (s : String) : String := (Json.str s).compress

def obj (pairs : List (String × String)) : String :=
  "{" ++ ",".intercalate (pairs.map fun (k, v) => js k ++ ":" ++ v) ++ "}"

def arr (xs : List String) : String := "[" ++ ",".intercalate xs ++ "]"

def bool (b : Bool) : String := if b then "true" else "false"

def opt (o : Option String) : String := o.map js |>.getD "null"

/-- Path prefixes removed: a whitespace-separated token with a `/` that starts with `/` (optionally quoted) is
replaced by its last component, so no host path reaches the sealed log. -/
def stripPaths (s : String) : String :=
  let tok (t : String) : String :=
    let q := t.startsWith "'" || t.startsWith "`" || t.startsWith "\""
    let core := if q then (t.drop 1).toString else t
    -- a path: a leading `/` followed by a name character (so `1 / 2` and `a/b` are left alone)
    let isPath := core.startsWith "/" && core.length > 1 &&
      (let c := (core.drop 1).front; c.isAlphanum || c == '.' || c == '_' || c == '-' || c == '~')
    if isPath then
      let last := (core.splitOn "/").getLast!
      (if q then (t.take 1).toString else "") ++ last
    else t
  "\n".intercalate ((s.splitOn "\n").map fun l => " ".intercalate ((l.splitOn " ").map tok))

/-- The setup-error list (proposal 0014, "Setup errors"), case-insensitive; the canonical form is
lean_pkg/controls.py SETUP_PATTERNS, and a test checks that both judge the same sample texts alike. -/
def setupError (text kind : String) : Option String := Id.run do
  let t := text.toLower
  let has (s : String) : Bool := (t.splitOn s).length > 1
  for (n, e) in [("lean.unknownIdentifier", "lean.unknownIdentifier"), ("lean.invalidField", "lean.invalidField"),
                 ("lean.invalidDottedIdent", "lean.invalidDottedIdent")] do
    if kind == n || kind == n ++ "._namedError" then return some e
  for w in ["identifier", "constant", "namespace", "tactic", "module"] do
    if has ("unknown " ++ w) then return some "unknown (identifier|constant|namespace|tactic|module)"
  if has "invalid field" then return some "invalid field"
  if has "unexpected token" then return some "unexpected token"
  if has "unexpected end of input" then return some "unexpected end of input"
  if has "unknown option" then return some "unknown option"
  if has "unknown attribute" then return some "unknown attribute"
  if has "object file" && has "does not exist" then return some "object file .* does not exist"
  if has "(deterministic) timeout" then return some "\\(deterministic\\) timeout"
  if has "deterministic timeout" then return some "deterministic timeout"
  if has "maximum recursion" then return some "maximum recursion"
  return none

structure Msg where
  severity : MessageSeverity
  kind : String
  text : String
  pos : Position

def isError (m : Msg) : Bool := m.severity == .error

/-- Elaborate FILE as `lean FILE` does: the same processor (`Language.Lean.process`, so a `module` header is
handled as Lean handles it), elaboration not async (deterministic), every snapshot's messages and info tree. -/
def runFile (path : String) : IO (Environment × Array Msg × Array InfoTree × FileMap) := do
  let input ← IO.FS.readFile path
  let inputCtx := Parser.mkInputContext input path
  let opts := Elab.async.set ({} : Options) false
  let setup (stx : HeaderSyntax) : Language.ProcessingT IO (Except Language.Lean.HeaderProcessedSnapshot Language.Lean.SetupImportsResult) :=
    return .ok { imports := stx.imports, isModule := stx.isModule, mainModuleName := `_lean_pkg_control,
                 opts, trustLevel := 1024, plugins := #[] }
  let snap ← Language.Lean.process setup none { inputCtx with }
  let snaps := Language.toSnapshotTree snap
  let all := snaps.getAll
  let mut msgs := #[]
  let mut trees := #[]
  for s in all do
    for m in s.diagnostics.msgLog.toList do
      msgs := msgs.push { severity := m.severity, kind := toString m.data.kind, text := ← m.data.toString,
                          pos := m.pos }
    if let some t := s.infoTree? then trees := trees.push t
  let env ← match Language.Lean.waitForFinalCmdState? snap with
    | some cs => pure cs.env
    | none => mkEmptyEnvironment
  return (env, msgs, trees, inputCtx.fileMap)

/-- The constants of the terms elaborated inside `r` (a source range) of a tree, split by whether they lie in
`sig` (the command's signature range) or not. -/
partial def treeConsts (sig : Option (String.Pos.Raw × String.Pos.Raw)) (t : InfoTree)
    : NameSet × NameSet :=
  t.foldInfo (init := ({}, {})) fun _ i (st, pf) =>
    match i with
    | .ofTermInfo ti =>
      let cs := ti.expr.getUsedConstants
      let inSig := match sig, ti.stx.getPos?, ti.stx.getTailPos? with
        | some (a, b), some p, some q => a ≤ p && q ≤ b
        | _, _, _ => false
      if inSig then (cs.foldl (·.insert ·) st, pf) else (st, cs.foldl (·.insert ·) pf)
    | _ => (st, pf)

/-- The command syntax a tree is about (the outer command node), if any. -/
def treeStx? : InfoTree → Option Syntax
  | .context _ t => treeStx? t
  | .node (.ofCommandInfo ci) _ => some ci.stx
  | _ => none

def sigRange (stx : Syntax) : Option (String.Pos.Raw × String.Pos.Raw) := do
  let s ← stx.find? fun n => n.getKind == ``Parser.Command.declSig || n.getKind == ``Parser.Command.optDeclSig
  return (← s.getPos?, ← s.getTailPos?)

def runCore (env : Environment) (x : CoreM α) : IO α := do
  let ctx : Core.Context := { fileName := "<controlsprobe>", fileMap := default }
  let (a, _) ← x.toIO ctx { env := env }
  return a

def isNegation (env : Environment) (ty : Expr) : IO Bool :=
  runCore env <| MetaM.run' (forallTelescope ty fun _ body => do
    let body ← instantiateMVars body
    return body.isConstOf ``False || body.isAppOfArity ``Not 1 || body.isAppOfArity ``Ne 3 : MetaM Bool)

def axiomsOf (env : Environment) (n : Name) : IO (Array Name) :=
  runCore env (collectAxioms n)

def STANDARD : List Name := [``propext, ``Quot.sound, ``Classical.choice]

def ownModule (env : Environment) (buildDir : String) (n : Name) : IO Bool := do
  match env.getModuleIdxFor? n with
  | none => return false
  | some idx =>
    let mod := env.header.moduleNames[idx.toNat]!
    try
      let p ← findOLean mod
      let abs ← IO.FS.realPath p
      let bd ← IO.FS.realPath buildDir
      return abs.toString.startsWith (bd.toString ++ "/")
    catch _ => return false

unsafe def main (args : List String) : IO UInt32 := do
  match args with
  | kind :: file :: buildDir :: rest =>
    unless kind == "must-fail" || kind == "refutes" || kind == "compiles" do
      IO.eprintln s!"unknown kind {kind}"; return 2
    let mut thms : Array Name := #[]
    let mut targets : Array Name := #[]
    let mut expect : Option String := none
    let mut r := rest
    while !r.isEmpty do
      match r with
      | "--theorem" :: n :: t => thms := thms.push n.toName; r := t
      | "--target" :: n :: t => targets := targets.push n.toName; r := t
      | "--expect" :: s :: t => expect := some s; r := t
      | _ => IO.eprintln "usage: lean --run ControlsProbe.lean KIND FILE BUILD_DIR [--theorem N]... [--target N]... [--expect S]"; return 2
    initSearchPath (← findSysroot)
    enableInitializersExecution
    let (env, msgs, trees, fm) ← runFile file
    let errs := msgs.filter isError
    let exitCode := if errs.isEmpty then 0 else 1
    let first := errs[0]?
    let setup := errs.findSome? fun m => setupError m.text m.kind
    let firstText := first.map fun m => ((stripPaths m.text).take 400).toString
    let expectState := match expect, first with
      | none, _ => "absent"
      | some e, some m => if (m.text.splitOn e).length > 1 then "matched" else "not-matched"
      | some _, none => "not-matched"
    let sorryMsg := msgs.any fun m =>
      (m.text.splitOn "declaration uses 'sorry'").length > 1 || (m.text.splitOn "declaration uses `sorry`").length > 1
    -- the declarations the file adds
    let added := env.constants.map₂.foldl (fun acc n _ => acc.push n) (#[] : Array Name)
    let mut thmFacts : List String := []
    let mut allAx : NameSet := {}
    let mut otherAx : NameSet := {}
    let mut sorryAx := false
    for n in added do
      let ax ← axiomsOf env n
      if ax.contains ``sorryAx then sorryAx := true
      allAx := ax.foldl (·.insert ·) allAx
      unless thms.contains n do otherAx := ax.foldl (·.insert ·) otherAx
    let mut thmsOk := true
    for n in thms do
      match env.find? n with
      | some (.thmInfo ti) =>
        let neg ← isNegation env ti.type
        let ax ← axiomsOf env n
        unless neg && ax.all STANDARD.contains do thmsOk := false
        thmFacts := thmFacts ++ [js n.toString ++ ":" ++ obj [("found", "true"), ("negation", bool neg),
          ("axioms", arr ((ax.qsort Name.lt).toList.map (js ·.toString)))]]
      | _ =>
        thmsOk := false
        thmFacts := thmFacts ++ [js n.toString ++ ":" ++ obj [("found", "false"), ("negation", "false"),
          ("axioms", "[]")]]
    -- targets
    let mut tgtFacts : List String := []
    let mut tgtsOk := true
    let firstTree := first.bind fun m =>
      trees.find? fun t => match treeStx? t with
        | some stx => match stx.getPos?, stx.getTailPos? with
          | some a, some b => let p := fm.ofPosition m.pos; a ≤ p && p ≤ b
          | _, _ => false
        | none => false
    for tgt in targets do
      let own ← ownModule env buildDir tgt
      let mut whereFound : Option String := none
      if own then
        if kind == "must-fail" then
          if let some t := firstTree then
            let (st, pf) := treeConsts ((treeStx? t).bind sigRange) t
            if st.contains tgt then whereFound := some "statement"
            else if pf.contains tgt then whereFound := some "proof"
        else
          for n in added do
            if let some ci := env.find? n then
              let used := ci.type.getUsedConstants ++ ((ci.value? (allowOpaque := true)).map (·.getUsedConstants) |>.getD #[])
              if used.contains tgt then
                if thms.contains n then whereFound := some "negation"
                else if whereFound.isNone then whereFound := some "other"
          if whereFound.isNone then
            for t in trees do
              let (st, pf) := treeConsts none t
              if st.contains tgt || pf.contains tgt then whereFound := some "other"
      if whereFound.isNone then tgtsOk := false
      tgtFacts := tgtFacts ++ [js tgt.toString ++ ":" ++ opt whereFound]
    let usesSorry := sorryMsg || sorryAx
    let stdOnly (s : NameSet) := s.toList.all STANDARD.contains
    let (ok, message) : Bool × Option String :=
      if let some e := setup then (false, some s!"setup error: {e}")
      else if kind == "must-fail" then
        if exitCode == 0 then (false, some "the file compiled")
        else if expectState == "not-matched" then (false, some "expect not found in the first error message")
        else if !tgtsOk then (false, some "a target was not found in the failing command")
        else (true, none)
      else if exitCode != 0 then (false, some (firstText.getD "error"))
      else if usesSorry then (false, some "the file uses sorry")
      else if kind == "refutes" && !thmsOk then (false, some "a listed theorem is missing, not a negation, or uses a non-standard axiom")
      else if !stdOnly (if kind == "refutes" then otherAx else allAx) then (false, some "a declaration uses a non-standard axiom")
      else if !tgtsOk then (false, some "a target was not found")
      else (true, none)
    let pairs : List (String × String) :=
      [("exit", toString exitCode), ("errors", toString errs.size), ("first_error", opt firstText),
       ("first_kind", opt (first.map (·.kind))), ("setup_error", opt setup), ("expect", js expectState),
       ("sorry", bool usesSorry)] ++
      (if kind == "refutes" then [("theorems", "{" ++ ",".intercalate thmFacts ++ "}"),
         ("other_axioms", arr (otherAx.toList.map (js ·.toString) |>.mergeSort))] else []) ++
      (if kind == "compiles" then [("axioms", arr (allAx.toList.map (js ·.toString) |>.mergeSort))] else []) ++
      [("targets", if targets.isEmpty then "null" else "{" ++ ",".intercalate tgtFacts ++ "}"),
       ("ok", bool ok), ("message", opt message)]
    IO.println ("ctl-facts: " ++ obj pairs)
    return 0
  | _ =>
    IO.eprintln "usage: lean --run ControlsProbe.lean KIND FILE BUILD_DIR [--theorem N]... [--target N]... [--expect S]"
    return 2

end ControlsProbe

unsafe def main (args : List String) : IO UInt32 := ControlsProbe.main args
