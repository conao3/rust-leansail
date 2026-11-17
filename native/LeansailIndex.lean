import Lean.Server.Watchdog
import LeansailTrace
import LeansailSymbols

open Lean Lean.Server Lean.Server.Watchdog Lean.Lsp Lean.JsonRpc IO

initialize indexRegions : IO.Ref (Array CompactedRegion) ← IO.mkRef #[]

def inspectIndexCache (helper : String) (manifest : Json) (commit := false) : IO Json := do
  let output ← IO.Process.output {
    cmd := helper
    args := if commit then #["index-cache", "--commit"] else #["index-cache"]
  } (some manifest.compress)
  if output.exitCode != 0 then
    throw <| IO.userError s!"索引キャッシュの検証が失敗しました: {output.stderr}"
  IO.ofExcept (Json.parse output.stdout)

def loadIndexSnapshot (path : System.FilePath) (referenceData : Std.Mutex ReferenceData) : IO Unit := do
  let ((ileans, symbols), region) ← unsafe CompactedRegion.read (α := ILeanMap × Leansail.SymbolCache) path #[]
  indexRegions.modify (·.push region)
  Leansail.symbolCache.atomically (set symbols)
  referenceData.atomically do
    modify fun rd => rd.modifyReferences fun refs => { refs with ileans }

def startLoadingIndex (referenceData : Std.Mutex ReferenceData) : IO Unit := do
  let some helper ← IO.getEnv "LEANSAIL_INDEX_HELPER"
    | startLoadingReferences referenceData
  let task ← ServerTask.IO.asTask do
    leansailTrace "index_start"
    let searchPath ← Lean.searchPathRef.get
    let paths ← searchPath.findAllWithExt "ilean"
    let manifest := Json.mkObj [
      ("files", toJson (paths.map (·.toString))),
      ("backend", toJson (← IO.appPath).toString),
      ("source_path", toJson (← IO.getEnv "LEAN_SRC_PATH")),
      ("search_path", toJson (searchPath.map (·.toString))),
      ("toolchain", toJson s!"{Lean.versionString}:{← findSysroot}")]
    let cached? ← try
      let cached ← inspectIndexCache helper manifest
      leansailTrace "cache_checked"
      pure (some cached)
    catch error =>
      IO.eprintln s!"索引キャッシュを検証できません: {error}"
      pure none
    if let some cached := cached? then
      if (← IO.ofExcept (cached.getObjValAs? Bool "valid")) then
        let path := System.FilePath.mk (← IO.ofExcept (cached.getObjValAs? String "path"))
        loadIndexSnapshot path referenceData
        leansailTrace "index_done" "cache"
        return
    let mut failures := 0
    for path in paths do
      try
        let ilean ← Ilean.load path
        referenceData.atomically do
          let rd ← get
          set (← rd.modifyReferencesM (·.addIlean path ilean))
      catch error =>
        failures := failures + 1
        IO.eprintln s!"索引ファイルを読み込めません: {path}: {error}"
    let ileans ← referenceData.atomically do
      return (← get).references.ileans
    let symbols := Leansail.prepareSymbolCache ileans
    Leansail.symbolCache.atomically (set symbols)
    leansailTrace "index_done" "ilean"
    if failures == 0 then
      if let some cached := cached? then
        let _ ← ServerTask.IO.asTask do
          try
            let path := System.FilePath.mk (← IO.ofExcept (cached.getObjValAs? String "path"))
            let temporary := System.FilePath.mk s!"{path}.{← IO.Process.getPID}"
            let _ ← unsafe CompactedRegion.save temporary `leansailIndex (ileans, symbols) #[] none
            let current ← inspectIndexCache helper manifest
            if (← IO.ofExcept (current.getObjValAs? String "key")) ==
                (← IO.ofExcept (cached.getObjValAs? String "key")) then
              IO.FS.rename temporary path
              discard <| inspectIndexCache helper manifest true
              leansailTrace "index_saved"
            else
              IO.FS.removeFile temporary
          catch error =>
            IO.eprintln s!"索引キャッシュを保存できません: {error}"
        pure ()
  referenceData.atomically do
    modify fun rd => { rd with loadingTask := task.mapCheap fun _ => () }

partial def symbolMainLoop (clientTask : ServerTask ServerEvent) : ServerM Unit := do
  let st ← read
  let workers ← st.fileWorkersRef.get
  let mut workerTasks := #[]
  for (_, fw) in workers do
    let some commTask := fw.commTask?
      | continue
    if (← getWorkerState fw) matches WorkerState.crashed then
      continue
    workerTasks := workerTasks.push <| commTask.mapCheap (ServerEvent.workerEvent fw)

  let ev ← ServerTask.waitAny (workerTasks.toList ++ [clientTask]) (by simp)
  match ev with
  | ServerEvent.clientMsg msg =>
    match msg with
    | Message.request id "shutdown" _ =>
      shutdown
      writeMessage { id, result := Json.null : Response Json }
    | Message.request id method (some params) =>
      if method == "workspace/symbol" then
        handleReferenceRequest WorkspaceSymbolParams (Array SymbolInformation) id (toJson params)
          (fun p => do Leansail.searchWorkspaceSymbols (← read).references p.query)
      else
        handleRequest id method (toJson params)
      symbolMainLoop (← runClientTask)
    | Message.response id result =>
      handleResponse id result
      symbolMainLoop (← runClientTask)
    | Message.responseError id code message data? =>
      handleResponseError id code message data?
      symbolMainLoop (← runClientTask)
    | Message.notification method (some params) =>
      handleNotification method (toJson params)
      symbolMainLoop (← runClientTask)
    | _ => throwServerError "Got invalid JSON-RPC message"
  | ServerEvent.clientError e => throw e
  | ServerEvent.workerEvent fw ev =>
    let doc := fw.doc
    let uri := doc.uri
    match ev with
    | WorkerEvent.ioError e =>
      throwServerError s!"IO error while processing events for {uri}: {e}"
    | WorkerEvent.crashed exitCode =>
      let (errorCode, errorCausePointer) :=
        if exitCode = 1 then
          (ErrorCode.workerExited, "see stderr for exception")
        else
          (ErrorCode.workerCrashed, "likely due to a stack overflow or a bug")
      errorPendingRequests uri errorCode
        s!"Server process for {uri} crashed, {errorCausePointer}."
      writeMessage <| mkFileProgressAtPosNotification doc 0 (kind := LeanFileProgressKind.fatalError)
      setWorkerState fw .crashed
      symbolMainLoop clientTask
    | WorkerEvent.terminated =>
      throwServerError
        "Internal server error: Got termination event for worker that should have been removed"
    | .importsChanged =>
      startFileWorker doc
      symbolMainLoop clientTask

def initAndRunSymbolWatchdog : ServerM Unit := do
  let st ← read
  try
    discard $ st.hIn.readLspNotificationAs "initialized" InitializedParams
    writeMessage {
      id := RequestID.str "register_lean_watcher"
      method := "client/registerCapability"
      param := some {
        registrations := #[{
          id := "lean_watcher"
          method := "workspace/didChangeWatchedFiles"
          registerOptions := some <| toJson {
            watchers := #[ { globPattern := "**/*.lean" }, { globPattern := "**/*.ilean" } ]
            : DidChangeWatchedFilesRegistrationOptions
            }
        }]
        : RegistrationParams
      }
      : Request (Option RegistrationParams)
    }
    let clientTask ← runClientTask
    symbolMainLoop clientTask
  catch err =>
    shutdown
    throw err

  while true do
    let msg: JsonRpc.Message ←
      try
        st.hIn.readLspMessage
      catch _ =>
        pure (Message.notification "exit" none)
    match msg with
    | .notification "exit" none =>
      break
    | .request id _ _ =>
      writeMessage {
        id,
        code := .invalidRequest,
        message := "Request received after 'shutdown' request."
        : ResponseError Unit
      }
    | _ =>
      continue

def runIndexedWatchdog (args : List String) : IO UInt32 := do
  let i ← IO.getStdin
  let o ← IO.getStdout
  let workerPath ← findWorkerPath
  let referenceData : Std.Mutex ReferenceData ← Std.Mutex.new {
    loadingTask := ServerTask.pure ()
    references := .empty
    finalizedWorkerILeanVersions := ∅
    pendingWaitForILeanRequests := #[]
  }
  startLoadingIndex referenceData
  let fileWorkersRef ← IO.mkRef ({} : Std.TreeMap DocumentUri FileWorker)
  let serverRequestData ← IO.mkRef {
    pendingServerRequests := Std.TreeMap.empty
    freshServerRequestID := 0
  }
  let importData ← IO.mkRef ⟨Std.TreeMap.empty, Std.TreeMap.empty⟩
  let requestData ← RequestDataMutex.new
  let initRequest ← i.readLspRequestAs "initialize" InitializeParams
  o.writeLspResponse {
    id := initRequest.id
    result := {
      capabilities := mkLeanServerCapabilities
      serverInfo? := some { name := "Lean 4 Server", version? := "0.3.0" }
      : InitializeResult
    }
  }
  let logCfg ← Logging.LogConfig.ofLspLogConfig <| initRequest.param.initializationOptions?.bind (·.logCfg?)
  let logChan? ←
    if logCfg.isEnabled then
      pure <| some <| ← Std.Channel.new (capacity := some 1024)
    else
      pure none
  let logData : LogData := { cfg := logCfg, chan? := logChan? }
  runMessageLoggingTask logData
  try
    ReaderT.run initAndRunSymbolWatchdog {
      hIn := i
      hOut := o
      logData
      args
      fileWorkersRef
      initParams := initRequest.param
      referenceData
      workerPath
      serverRequestData
      importData
      requestData
      : ServerContext
    }
    IO.Process.forceExit 0
  catch error =>
    IO.eprintln s!"Watchdog: {error}"
    IO.Process.forceExit 1
