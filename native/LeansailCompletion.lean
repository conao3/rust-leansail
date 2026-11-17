/-
Copyright (c) 2021 Microsoft Corporation. All rights reserved.
Copyright (c) 2024 Lean FRO, LLC. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Leonardo de Moura, Marc Huisinga
-/
module

import all Init.Util
import all Lean.Server.Completion.CompletionCollectors
import all Lean.Server.Completion
public import Lean.Server.FileWorker.RequestHandling
import LeansailTrace

public section

open Lean Lean.Elab Lean.Lsp Lean.Server Lean.Server.Completion Lean.Server.RequestM

namespace Leansail.Completion

structure HeaderQuery where
  mainModule : Name
  currNamespace : Name
  opens : List (Name × List Name)
  id : Name
  danglingDot : Bool
  deriving DecidableEq

private def matchName? (query : HeaderQuery) (ns : Name) (rawName : Name) : Option Name := do
  let declName ← match privateToUserName? rawName with
    | none => some rawName
    | some userName =>
      if mkPrivateNameCore query.mainModule (privateToUserName userName) == rawName then some userName else none
  if !ns.isPrefixOf declName then
    return ← none
  let declName := declName.replacePrefix ns Name.anonymous
  if query.danglingDot then
    if query.id.isPrefixOf declName then
      let declName := declName.replacePrefix query.id Name.anonymous
      if declName.isAtomic && !declName.isAnonymous then
        return declName
  else if let (.str p₁ s₁, .str p₂ s₂) := (query.id, declName) then
    if p₁ == p₂ then
      if s₁.charactersIn s₂ then return .mkSimple s₂ else return ← none
    else if p₁.isAnonymous then
      if s₁.charactersIn s₂ then return declName else return ← none
  none

private def bestName? (query : HeaderQuery) (declName : Name) : Option Name := Id.run do
  let update (best : Option Name) (ns : Name) : Option Name := do
    let label ← matchName? query ns declName
    match best with
    | none => some label
    | some bestLabel => some (if label.isSuffixOf bestLabel then label else bestLabel)
  let visit (best : Option Name) (ns : Name) := (update best ns).or best
  let rec namespaces (ns : Name) (best : Option Name) : Option Name :=
    match ns with
    | .str parent _ => namespaces parent (visit best ns)
    | _ => best
  let mut best := namespaces query.currNamespace none
  for (ns, exs) in query.opens do
    if !exs.contains declName then
      best := visit best ns
  return visit best Name.anonymous

def headerMatches (header : EligibleHeaderDecls) (query : HeaderQuery) : Array (Name × Name) :=
  header.toArray.filterMap fun (name, _) => (bestName? query name).map (name, ·)

structure PreparedHeader where
  source : EligibleHeaderDecls
  query : HeaderQuery
  selections : Array (Name × Name)
  valid : selections = headerMatches source query

def reuseHeaderMatches (header : EligibleHeaderDecls) (query : HeaderQuery)
    (cached : PreparedHeader) : Array (Name × Name) :=
  if hq : query = cached.query then
    if withPtrEq header cached.source
        (fun _ => decide (headerMatches header query = cached.selections))
        (fun h => by simp [h, cached.valid, hq]) then
      cached.selections
    else
      headerMatches header query
  else
    headerMatches header query

theorem reuseHeaderMatches_eq (header : EligibleHeaderDecls) (query : HeaderQuery)
    (cached : PreparedHeader) : reuseHeaderMatches header query cached = headerMatches header query := by
  unfold reuseHeaderMatches
  split
  · by_cases h : headerMatches header query = cached.selections
    · simp [withPtrEq, h]
    · simp [withPtrEq, h]
  · rfl

initialize headerCache : Std.Mutex (Option PreparedHeader) ← Std.Mutex.new none

def completionTrace (event : String) (detail := "") : IO Unit := do
  if (← IO.getEnv "LEANSAIL_COMPLETION_PROFILE") == some "1" then
    leansailTrace event detail

private def cachedHeaderMatches (header : EligibleHeaderDecls) (query : HeaderQuery) : IO (Array (Name × Name)) := do
  headerCache.atomically do
    let cached ← get
    let prepared := match cached with
      | none => PreparedHeader.mk header query (headerMatches header query) rfl
      | some cached => ⟨header, query, reuseHeaderMatches header query cached, reuseHeaderMatches_eq header query cached⟩
    set (some prepared)
    return prepared.selections

private def cachedIdCompletionCore
    (ctx : ContextInfo)
    (stx : Syntax)
    (id : Name)
    (hoverInfo : HoverInfo)
    (danglingDot : Bool)
    : M Unit := do
  let mut id := id
  if id.hasMacroScopes then
    if stx.getHeadInfo matches .original .. then
      id := id.eraseMacroScopes
    else
      return
  let mut danglingDot := danglingDot
  if let HoverInfo.inside delta := hoverInfo then
    id := truncate id delta
    danglingDot := false
  if id.isAtomic then
    for localDecl in (← getLCtx) do
      if matchAtomic id localDecl.userName danglingDot then
        addUnresolvedCompletionItem localDecl.userName (.fvar localDecl.fvarId)
          (kind := CompletionItemKind.variable) (tags := #[])
  let env ← getEnv
  let header ← getEligibleHeaderDecls env
  completionTrace "completion_header_ready"
  let query : HeaderQuery := {
    mainModule := env.mainModule, currNamespace := ctx.currNamespace,
    opens := ctx.openDecls.filterMap fun
      | .simple ns exs => some (ns, exs)
      | .explicit .. => none
    id, danglingDot
  }
  let selections ← cachedHeaderMatches header query
  completionTrace "completion_header_search_done" (toString selections.size)
  for (declName, bestLabel) in selections do
    RequestCancellation.check
    if let some decl := header[declName]? then
      addUnresolvedCompletionItem bestLabel (.const declName) (← decl.kind) (← decl.tags)
  completionTrace "completion_header_done" (toString selections.size)
  env.constants.map₂.forM fun declName c => do
    if Lean.Meta.allowCompletion env declName then
      let bestMatch? ← bestLabelForDecl? ctx declName id danglingDot
      if let some bestLabel := bestMatch? then
        addUnresolvedCompletionItem bestLabel (.const declName)
          (← getCompletionKindForDecl c) (← getCompletionTagsForDecl declName)
  completionTrace "completion_locals_done"
  RequestCancellation.check
  let matchAlias (ns : Name) (alias : Name) : Bool :=
    if ns.isPrefixOf alias then
      let alias := alias.replacePrefix ns Name.anonymous
      matchAtomic id alias danglingDot
    else
      false
  let eligibleHeaderDecls ← getEligibleHeaderDecls env
  let addAlias (alias : Name) (declNames : List Name) : M Unit := do
    declNames.forM fun declName => do
      if allowCompletion eligibleHeaderDecls env declName then
        addUnresolvedCompletionItemForDecl (.mkSimple alias.getString!) declName
  for openDecl in ctx.openDecls do
    match openDecl with
    | OpenDecl.explicit openedId resolvedId =>
      if allowCompletion eligibleHeaderDecls env resolvedId then
        if matchAtomic id openedId danglingDot then
          addUnresolvedCompletionItemForDecl (.mkSimple openedId.getString!) resolvedId
    | OpenDecl.simple ns _      =>
      getAliasState env |>.forM fun alias declNames => do
        if matchAlias ns alias then
          addAlias alias declNames
  getAliasState env |>.forM fun alias declNames => do
    let rec searchAlias (ns : Name) : M Unit := do
      if matchAlias ns alias then
        addAlias alias declNames
      else
        match ns with
        | Name.str p ..  => searchAlias p
        | _ => return ()
    searchAlias ctx.currNamespace
  completionTrace "completion_aliases_done"
  if !danglingDot then
    if let .str .anonymous s := id then
      let keywords := Parser.getTokenTable env
      for keyword in keywords.findPrefix s do
        addKeywordCompletionItem keyword
  completionTrace "completion_keywords_done"
  completeNamespaces ctx id danglingDot
  completionTrace "completion_namespaces_done"

private def cachedIdCompletion
    (uri : DocumentUri)
    (pos : Lsp.Position)
    (completionInfoPos : Nat)
    (ctx : ContextInfo)
    (lctx : LocalContext)
    (stx : Syntax)
    (id : Name)
    (hoverInfo : HoverInfo)
    (danglingDot : Bool)
    : CancellableM (Array ResolvableCompletionItem) :=
  runM uri pos completionInfoPos ctx lctx do
    cachedIdCompletionCore ctx stx id hoverInfo danglingDot

private partial def find?
    (uri : DocumentUri)
    (pos : Lsp.Position)
    (fileMap : FileMap)
    (hoverPos : String.Pos.Raw)
    (cmdStx : Syntax)
    (infoTree : InfoTree)
    (caps : ClientCapabilities)
    : CancellableM ResolvableCompletionList := do
  let (prioritizedPartitions, isComplete) := findPrioritizedCompletionPartitionsAt fileMap hoverPos cmdStx infoTree
  completionTrace "completion_selection_done"
  let mut allCompletions := #[]
  for partition in prioritizedPartitions do
    for (i, completionInfoPos) in partition do
      CancellableM.checkCancelled
      let completions : Array ResolvableCompletionItem ←
        match i.info with
        | .id stx id danglingDot lctx .. =>
          cachedIdCompletion uri pos completionInfoPos i.ctx lctx stx id i.hoverInfo danglingDot
        | .dot info .. =>
          dotCompletion uri pos completionInfoPos i.ctx info
        | .dotId _ id lctx expectedType? =>
          dotIdCompletion uri pos completionInfoPos i.ctx lctx id expectedType?
        | .fieldId _ id lctx structName =>
          fieldIdCompletion uri pos completionInfoPos i.ctx lctx id structName
        | .option stx =>
          optionCompletion uri pos completionInfoPos i.ctx stx caps
        | .errorName _ partialId =>
          errorNameCompletion uri pos completionInfoPos i.ctx partialId caps
        | .endSection _ id? danglingDot scopeNames =>
          endSectionCompletion uri pos completionInfoPos id? danglingDot scopeNames
        | .tactic .. =>
          tacticCompletion uri pos completionInfoPos i.ctx
        | _ =>
          pure #[]
      allCompletions := allCompletions ++ completions
    if ! allCompletions.isEmpty then
      break

  completionTrace "completion_candidates_done" (toString allCompletions.size)
  let finalCompletions := allCompletions
    |> filterDuplicateCompletionItems
  completionTrace "completion_dedup_done" (toString finalCompletions.size)
  return { items := finalCompletions, isIncomplete := ! isComplete }
def handleCompletion (params : Json) : RequestM (RequestTask SerializedLspResponse) := do
  completionTrace "completion_start"
  let p ← parseRequestParams CompletionParams params
  let doc ← readDoc
  let pos := doc.meta.text.lspPosToUtf8Pos p.position
  let caps := (← read).initParams.capabilities
  mapTaskCostly (Lean.Server.FileWorker.findCompletionCmdDataAtPos doc pos) fun data? => do
    completionTrace "completion_snapshot_ready"
    let result ← match data? with
      | none => pure { items := #[], isIncomplete := true }
      | some (stx, tree) => find? doc.meta.uri p.position doc.meta.text pos stx tree caps
    let serialized := result.compressFast
    completionTrace "completion_serialize_done"
    return { response? := none, serialized, isComplete := true }

end Leansail.Completion
