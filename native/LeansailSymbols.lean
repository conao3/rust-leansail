import Lean.Data.Lsp
import Std.Sync.Mutex
import Lean.Server.References
import Lean.Data.FuzzyMatching
import LeansailTrace

open Lean Lean.Server Lean.Lsp

namespace Leansail

structure PreparedDefinition where
  name : String
  startLine : Nat
  startCharacter : Nat
  endLine : Nat
  endCharacter : Nat
  deriving DecidableEq

def prepareDefinitions (refs : Lsp.ModuleRefs) : Array PreparedDefinition := Id.run do
  let mut result := #[]
  for (ident, info) in refs do
    let (RefIdent.const _ raw, some loc) := (ident, info.definition?)
      | continue
    let name := raw.toName
    result := result.push {
      name := privateToUserName? name |>.getD name |>.toString
      startLine := loc.range.start.line
      startCharacter := loc.range.start.character
      endLine := loc.range.end.line
      endCharacter := loc.range.end.character
    }
  return result

structure PreparedModule where
  source : Lsp.ModuleRefs
  definitions : Array PreparedDefinition
  valid : definitions = prepareDefinitions source

def reuseDefinitions (refs : Lsp.ModuleRefs) (cached : PreparedModule) : Array PreparedDefinition :=
  if withPtrEq refs cached.source
      (fun _ => decide (prepareDefinitions refs = cached.definitions))
      (fun h => by simp [h, cached.valid]) then
    cached.definitions
  else
    prepareDefinitions refs

theorem reuseDefinitions_eq (refs : Lsp.ModuleRefs) (cached : PreparedModule) :
    reuseDefinitions refs cached = prepareDefinitions refs := by
  by_cases h : prepareDefinitions refs = cached.definitions
  · simp only [reuseDefinitions, withPtrEq, h, decide_true, ite_true]
  · simp [reuseDefinitions, withPtrEq, h]

abbrev SymbolCache := Std.TreeMap Name PreparedModule Name.quickCmp

initialize symbolCache : Std.Mutex SymbolCache ← Std.Mutex.new ∅

def prepareModule (refs : Lsp.ModuleRefs) (cached? : Option PreparedModule) : PreparedModule :=
  match cached? with
  | some cached => ⟨refs, reuseDefinitions refs cached, reuseDefinitions_eq refs cached⟩
  | none => ⟨refs, prepareDefinitions refs, rfl⟩

def prepareSymbolCache (ileans : ILeanMap) : SymbolCache :=
  ileans.map fun _ ilean => prepareModule ilean.refs none

def symbolTrace (event : String) (detail := "") : IO Unit := do
  if (← IO.getEnv "LEANSAIL_SYMBOL_PROFILE") == some "1" then
    leansailTrace event detail

def searchWorkspaceSymbols (references : References) (query : String) : IO (Array SymbolInformation) := do
  if query.isEmpty then
    return #[]
  symbolTrace "symbol_start" query
  let all := references.allRefs
  symbolTrace "symbol_modules"
  let cached ← symbolCache.atomically get
  let mut nextCache : SymbolCache := ∅
  let mut modules := #[]
  for (module, uri, refs, _) in all do
    let prepared := prepareModule refs cached[module]?
    nextCache := nextCache.insert module prepared
    modules := modules.push (uri, prepared.definitions)
  symbolCache.atomically (set nextCache)
  symbolTrace "symbol_prepared"
  let mut found : Array (PreparedDefinition × DocumentUri × Float) := #[]
  for (uri, definitions) in modules do
    for definition in definitions do
      if let some score := FuzzyMatching.fuzzyMatchScoreWithThreshold? query definition.name then
        found := found.push (definition, uri, score)
  symbolTrace "symbol_match" (toString found.size)
  let sorted := found.toList.mergeSort fun (a, _, sa) (b, _, sb) =>
    if sa == sb then a.name.length < b.name.length else sa > sb
  let sorted := sorted.extract 0 1000
  symbolTrace "symbol_sort"
  let result := sorted.map fun (definition, uri, _) => ({
    name := definition.name
    kind := SymbolKind.constant
    location := {
      uri
      range := {
        start := ⟨definition.startLine, definition.startCharacter⟩
        «end» := ⟨definition.endLine, definition.endCharacter⟩
      }
    }
  } : SymbolInformation)
  let result := result.toArray
  symbolTrace "symbol_format"
  return result

end Leansail
