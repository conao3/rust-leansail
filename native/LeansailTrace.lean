module

public import Lean.Data.Json

public section

open Lean

def leansailTrace (event : String) (detail : String := "") : IO Unit := do
  if let some path ← IO.getEnv "LEANSAIL_TRACE" then
    let ns ← IO.monoNanosNow
    let pid ← IO.Process.getPID
    let stream ← IO.FS.Handle.mk s!"{path}.{pid}.jsonl" .append
    stream.putStrLn (Json.mkObj [
      ("event", toJson event), ("detail", toJson detail),
      ("ns", toJson ns), ("pid", toJson pid.toNat)]).compress
    stream.flush
