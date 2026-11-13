leanfran::lean! {
    pub mod model;
    r#"
    inductive Phase where
      | fresh | initializing | ready | running | stopping | stopped | exited
      deriving DecidableEq, Repr

    inductive Event where
      | initialize | initializeOk | initializeError | initialized
      | request | shutdown | shutdownOk | shutdownError | exit
      deriving DecidableEq, Repr

    def transition (phase : Phase) (event : Event) : Option Phase :=
      match phase, event with
      | _, .exit => some .exited
      | .fresh, .initialize => some .initializing
      | .initializing, .initializeOk => some .ready
      | .initializing, .initializeError => some .fresh
      | .ready, .initialized => some .running
      | .running, .request => some .running
      | .running, .shutdown => some .stopping
      | .stopping, .shutdownOk => some .stopped
      | .stopping, .shutdownError => some .running
      | _, _ => none

    theorem exit_is_always_accepted (phase : Phase) :
        transition phase .exit = some .exited := by
      cases phase <;> rfl

    theorem exited_is_terminal (event : Event) :
        transition .exited event = some .exited ∨ transition .exited event = none := by
      cases event <;> simp [transition]

    theorem running_has_a_valid_predecessor (phase : Phase) (event : Event)
        (h : transition phase event = some .running) :
        (phase = .ready ∧ event = .initialized) ∨
        (phase = .running ∧ event = .request) ∨
        (phase = .stopping ∧ event = .shutdownError) := by
      cases phase <;> cases event <;> simp_all [transition]

    theorem requests_require_running (phase : Phase) :
        transition phase .request = some .running ↔ phase = .running := by
      cases phase <;> simp [transition]
    "#
}
