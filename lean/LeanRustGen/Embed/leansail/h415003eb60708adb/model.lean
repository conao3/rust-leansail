namespace model

inductive Decision where
  | forward | acknowledge | unsupported
  deriving DecidableEq, Repr

def refreshCapability (method : String) : Option String :=
  match method with
  | "workspace/semanticTokens/refresh" => some "/workspace/semanticTokens/refreshSupport"
  | "workspace/inlayHint/refresh" => some "/workspace/inlayHint/refreshSupport"
  | "workspace/codeLens/refresh" => some "/workspace/codeLens/refreshSupport"
  | "workspace/inlineValue/refresh" => some "/workspace/inlineValue/refreshSupport"
  | "workspace/diagnostic/refresh" => some "/workspace/diagnostics/refreshSupport"
  | "workspace/foldingRange/refresh" => some "/workspace/foldingRange/refreshSupport"
  | _ => none

def registrationAllowed (method : String) (declared : Bool) : Bool :=
  if method == "workspace/didChangeWatchedFiles" then declared else true

def decide (method : String) (declared registrationsAllowed : Bool) : Decision :=
  if method == "client/registerCapability" then
    if registrationsAllowed then .forward else .unsupported
  else
    match refreshCapability method with
    | some _ => if declared then .forward else .acknowledge
    | none => .forward

theorem refresh_requires_support (method path : String)
    (h : refreshCapability method = some path)
    (hr : (method == "client/registerCapability") = false) (declared : Bool)
    (registrationsAllowed : Bool) :
    decide method declared registrationsAllowed = .forward ↔ declared = true := by
  cases declared <;> simp [decide, hr, h]

theorem unsupported_refresh_is_acknowledged (method path : String)
    (h : refreshCapability method = some path)
    (hr : (method == "client/registerCapability") = false) (registrationsAllowed : Bool) :
    decide method false registrationsAllowed = .acknowledge := by
  simp [decide, hr, h]

theorem watched_files_require_dynamic_registration (declared : Bool) :
    registrationAllowed "workspace/didChangeWatchedFiles" declared = declared := by
  simp [registrationAllowed]

theorem registration_requires_all_entries (declared allowed : Bool) :
    decide "client/registerCapability" declared allowed = .forward ↔ allowed = true := by
  cases allowed <;> simp [decide]

theorem unsupported_registration_is_rejected (declared : Bool) :
    decide "client/registerCapability" declared false = .unsupported := by
  simp [decide]

theorem unrelated_requests_are_forwarded (method : String)
    (h : refreshCapability method = none)
    (hr : (method == "client/registerCapability") = false)
    (declared registrationsAllowed : Bool) :
    decide method declared registrationsAllowed = .forward := by
  simp [decide, hr, h]

end model
