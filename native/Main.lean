import Lean.Server.Watchdog
import Lean.Server.FileWorker
import LeansailTrace
import LeansailIndex
import LeansailCompletion

open Lean Lean.Server Lean.Server.RequestM

def indexedModuleName? (modules : Array EffectiveImport) (index : ModuleIdx) : Option Name :=
  modules[index.toNat]?.map (·.module)

theorem indexedModuleName?_eq (modules : Array EffectiveImport) (index : ModuleIdx) :
    indexedModuleName? modules index = (modules.map (·.module))[index]? := by
  change modules[index.toNat]?.map (·.module) = (modules.map (·.module))[index.toNat]?
  simp

def indexedIdentOf (ci : Elab.ContextInfo) (info : Elab.Info) : Option (Lsp.RefIdent × Bool) := do
  let moduleFor (name : Name) : Option Name := do
    let some index := ci.env.getModuleIdxFor? name
      | ci.env.header.mainModule
    indexedModuleName? ci.env.header.modules index
  match info with
  | .ofTermInfo ti => match ti.expr with
    | .const name .. =>
      some (Lsp.RefIdent.const (← moduleFor name).toString name.toString, ti.isBinder)
    | .fvar id =>
      some (Lsp.RefIdent.fvar ci.env.header.mainModule.toString id.name.toString, ti.isBinder)
    | _ => none
  | .ofFieldInfo fi =>
    some (Lsp.RefIdent.const (← moduleFor fi.projName).toString fi.projName.toString, false)
  | .ofOptionInfo oi =>
    some (Lsp.RefIdent.const (← moduleFor oi.declName).toString oi.declName.toString, false)
  | .ofDocElabInfo dei =>
    some (Lsp.RefIdent.const (← moduleFor dei.name).toString dei.name.toString, false)
  | _ => none

theorem indexedIdentOf_eq (ci : Elab.ContextInfo) (info : Elab.Info) :
    indexedIdentOf ci info = identOf ci info := by
  cases info <;> simp only [indexedIdentOf, identOf, getModuleContainingDecl?,
    indexedModuleName?_eq, Environment.allImportedModuleNames, EnvironmentHeader.moduleNames] <;> rfl

def indexedFindReferences (text : FileMap) (trees : Array Elab.InfoTree) : Array Reference :=
  Id.run <| StateT.run' (s := #[]) do
  for tree in trees do
    tree.visitM' (postNode := fun ci info _ => do
      let some (ident, isBinder) := indexedIdentOf ci info
        | return
      let some range := info.range?
        | return
      if info.stx.getHeadInfo matches .original .. then
        modify (·.push { ident, range := range.toLspRange text, stx := info.stx, ci, info, isBinder }))
  get

theorem indexedFindReferences_eq (text : FileMap) (trees : Array Elab.InfoTree) :
    indexedFindReferences text trees = findReferences text trees := by
  unfold indexedFindReferences findReferences
  simp only [indexedIdentOf_eq]
  rfl

def indexedModuleRefs (text : FileMap) (trees : Array Elab.InfoTree) : ModuleRefs := Id.run do
  let refs := indexedFindReferences text trees
  let refs := dedupReferences (combineIdents trees refs)
  return refs.foldl (init := Std.TreeMap.empty) fun map ref => map.addRef ref

theorem indexedModuleRefs_eq (text : FileMap) (trees : Array Elab.InfoTree) :
    indexedModuleRefs text trees = findModuleRefs text trees := by
  unfold indexedModuleRefs findModuleRefs
  rw [indexedFindReferences_eq]
  rfl

structure ReferenceCache where
  uri : Lsp.DocumentUri
  version : Nat
  refs : Lsp.ModuleRefs

structure SemanticCacheEntry where
  uri : Lsp.DocumentUri
  version : Nat
  response : SerializedLspResponse

structure SemanticCache where
  generation : Nat := 0
  entry? : Option SemanticCacheEntry := none

def cachedSemanticTokens (original : StatefulRequestHandler)
    (cache : Std.Mutex SemanticCache) (params : Json) :
    RequestM (RequestTask SerializedLspResponse) := do
  let _ ← RequestM.parseRequestParams Lsp.SemanticTokensParams params
  let doc ← readDoc
  RequestM.checkCancelled
  let (generation, cached) ← cache.atomically do
    let state ← get
    return (state.generation, state.entry?.filter fun entry =>
      entry.uri == doc.meta.uri && entry.version == doc.meta.version)
  if let some entry := cached then
    return ← RequestM.asTask do
      RequestM.checkCancelled
      return entry.response
  let task ← original.handle params
  RequestM.mapTaskCheap task fun result => do
    RequestM.checkCancelled
    match result with
    | .error error => throw error
    | .ok response =>
      if response.isComplete then
        cache.atomically do
          let state ← get
          if state.generation == generation then
            set { state with entry? := some {
              uri := doc.meta.uri, version := doc.meta.version,
              response := { response with response? := none } } }
      return response

def cachedHighlight (original : RequestHandler)
    (cache : Std.Mutex (Option ReferenceCache)) (params : Json) :
    RequestM (RequestTask SerializedLspResponse) := do
  let p ← RequestM.parseRequestParams Lsp.DocumentHighlightParams params
  let doc ← readDoc
  RequestM.asTask do
    RequestM.checkCancelled
    let cached ← cache.atomically do
      return (← get).filter fun entry =>
        entry.uri == doc.meta.uri && entry.version == doc.meta.version
    let refs? ← match cached with
      | some entry => pure (some entry.refs)
      | none => do
        let (snaps, error?, complete) ← doc.cmdSnaps.getFinishedPrefix
        if !complete || error?.isSome then
          pure none
        else
          let (refs, _) ← (indexedModuleRefs doc.meta.text (snaps.toArray.map (·.infoTree))).toLspModuleRefs
          RequestM.checkCancelled
          cache.atomically do
            set (some ({ uri := doc.meta.uri, version := doc.meta.version, refs } : ReferenceCache))
          pure (some refs)
    if let some refs := refs? then
      let mut ranges : Array Lsp.DocumentHighlight := #[]
      for ident in refs.findAt p.position (includeStop := true) do
        if let some info := refs.get? ident then
          if let some loc := info.definition? then
            ranges := ranges.push { range := loc.range, kind? := some .text }
          ranges := ranges ++ info.usages.map fun loc => { range := loc.range, kind? := some .text }
      RequestM.checkCancelled
      if !ranges.isEmpty then
        let response := toJson ranges
        return { response? := some response, serialized := response.compress, isComplete := true }
    let task ← original.handle params
    match ← task.wait with
    | .ok response => return response
    | .error error => throw error

def profileRequest (method : String)
    (handle : Json → RequestM (RequestTask SerializedLspResponse))
    (log : Std.Mutex IO.FS.Handle) (params : Json) :
    RequestM (RequestTask SerializedLspResponse) := do
  let start ← IO.monoNanosNow
  let task ← handle params
  ServerTask.BaseIO.mapTaskCheap (t := task) fun result => do
    let elapsed := (← IO.monoNanosNow) - start
    let entry := Json.mkObj [
      ("method", toJson method), ("elapsed_ns", toJson elapsed),
      ("ok", toJson result.isOk)]
    let written ← log.atomically do
      let stream ← get
      (do stream.putStrLn entry.compress; stream.flush).toBaseIO
    match written with
    | .error error => return .error (RequestError.ofIoError error)
    | .ok () => return result

def installHandlers (useCache : Bool) : IO Unit := do
  if useCache then
    let method := "textDocument/completion"
    let some original ← lookupLspRequestHandler method
      | throw <| IO.userError s!"上流ハンドラーが見つかりません: {method}"
    requestHandlers.modify (·.insert method { original with handle := Leansail.Completion.handleCompletion })
    let method := "textDocument/documentHighlight"
    let some original ← lookupLspRequestHandler method
      | throw <| IO.userError s!"Missing upstream handler: {method}"
    let cache ← Std.Mutex.new (none : Option ReferenceCache)
    requestHandlers.modify (·.insert method { original with handle := cachedHighlight original cache })
    let method := "textDocument/semanticTokens/full"
    let some original ← lookupStatefulLspRequestHandler method
      | throw <| IO.userError s!"上流ハンドラーが見つかりません: {method}"
    let cache ← Std.Mutex.new ({} : SemanticCache)
    let onDidChange := fun params => do
      cache.atomically do
        modify fun state => { generation := state.generation + 1, entry? := none }
      original.onDidChange params
    statefulRequestHandlers.modify (·.insert method { original with
      handle := cachedSemanticTokens original cache, onDidChange })
  if let some profilePath ← IO.getEnv "LEAN_LS_PROFILE" then
    let stream ← IO.FS.Handle.mk s!"{profilePath}.{← IO.Process.getPID}.jsonl" .append
    let log ← Std.Mutex.new stream
    let handlers ← requestHandlers.get
    for (method, original) in handlers.toList do
      requestHandlers.modify (·.insert method
        { original with handle := profileRequest method original.handle log })
    for (method, original) in (← statefulRequestHandlers.get).toList do
      statefulRequestHandlers.modify (·.insert method
        { original with handle := profileRequest method original.handle log })

unsafe def main (args : List String) : IO UInt32 := do
  leansailTrace "backend_main"
  if args == ["--version"] then
    IO.println s!"Leansail backend (Lean {Lean.versionString})"
    return 0
  enableInitializersExecution
  initSearchPath (← findSysroot)
  leansailTrace "search_path_ready"
  let baseline := args.contains "--baseline"
  let args := args.filter (· != "--baseline")
  installHandlers (!baseline)
  leansailTrace "handlers_ready"
  if args.contains "--worker" then
    for arg in args do
      if arg != "--worker" && !arg.startsWith "file://" then
        throw <| IO.userError s!"Unsupported worker argument: {arg}"
    return ← Lean.Server.FileWorker.workerMain {}
  if args != [] && args != ["--server"] then
    throw <| IO.userError "Usage: leansail-backend [--server] [--baseline]"
  if baseline then
    Lean.Server.Watchdog.watchdogMain ["--baseline"]
  else
    runIndexedWatchdog []
