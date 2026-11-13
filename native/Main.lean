import Lean.Server.Watchdog
import Lean.Server.FileWorker

open Lean Lean.Server Lean.Server.RequestM

structure ReferenceCache where
  uri : Lsp.DocumentUri
  version : Nat
  refs : Lsp.ModuleRefs

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
          let (refs, _) ← (findModuleRefs doc.meta.text (snaps.toArray.map (·.infoTree))).toLspModuleRefs
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

def installHandlers (useCache : Bool) : IO Unit := do
  if useCache then
    let method := "textDocument/documentHighlight"
    let some original ← lookupLspRequestHandler method
      | throw <| IO.userError s!"Missing upstream handler: {method}"
    let cache ← Std.Mutex.new (none : Option ReferenceCache)
    requestHandlers.modify (·.insert method { original with handle := cachedHighlight original cache })
  if let some profilePath ← IO.getEnv "LEAN_LS_PROFILE" then
    let stream ← IO.FS.Handle.mk s!"{profilePath}.{← IO.Process.getPID}.jsonl" .append
    let log ← Std.Mutex.new stream
    let handlers ← requestHandlers.get
    for (method, original) in handlers.toList do
      let handle := fun (params : Json) => do
        let start ← IO.monoNanosNow
        let task ← original.handle params
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
      requestHandlers.modify (·.insert method { original with handle })

unsafe def main (args : List String) : IO UInt32 := do
  if args == ["--version"] then
    IO.println s!"Leansail backend (Lean {Lean.versionString})"
    return 0
  enableInitializersExecution
  initSearchPath (← findSysroot)
  let baseline := args.contains "--baseline"
  let args := args.filter (· != "--baseline")
  installHandlers (!baseline)
  if args.contains "--worker" then
    for arg in args do
      if arg != "--worker" && !arg.startsWith "file://" then
        throw <| IO.userError s!"Unsupported worker argument: {arg}"
    return ← Lean.Server.FileWorker.workerMain {}
  if args != [] && args != ["--server"] then
    throw <| IO.userError "Usage: leansail-backend [--server] [--baseline]"
  Lean.Server.Watchdog.watchdogMain (if baseline then ["--baseline"] else [])
