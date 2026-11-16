namespace model

def restartWorker (crashed opened request : Bool) : Bool :=
  crashed && opened && request

inductive ResponseDecision where
  | forward | cancelled | modified
  deriving DecidableEq, Repr

def responseDecision (cancelled sameVersion : Bool) : ResponseDecision :=
  if cancelled then .cancelled else if sameVersion then .forward else .modified

theorem cancelled_response_is_rejected (sameVersion : Bool) :
    responseDecision true sameVersion = .cancelled := by
  rfl

theorem stale_response_is_rejected : responseDecision false false = .modified := by
  rfl

theorem forwarded_response_is_current (cancelled sameVersion : Bool)
    (h : responseDecision cancelled sameVersion = .forward) :
    cancelled = false ∧ sameVersion = true := by
  cases cancelled <;> cases sameVersion <;> simp_all [responseDecision]

theorem restart_requires_crash (opened request : Bool) :
    restartWorker false opened request = false := by
  simp [restartWorker]

theorem restart_requires_open_request (crashed opened request : Bool)
    (h : restartWorker crashed opened request = true) :
    opened = true ∧ request = true := by
  cases crashed <;> cases opened <;> cases request <;> simp_all [restartWorker]

theorem crashed_open_request_restarts : restartWorker true true true = true := by
  rfl

end model
