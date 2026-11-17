use serde_json::{Value, json};

use crate::gate::Action;

leanfran::lean! {
    pub mod model;
    r#"
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

    theorem refresh_requires_support (declared registrationsAllowed : Bool) :
        (decide "workspace/semanticTokens/refresh" declared registrationsAllowed = .forward ↔ declared = true) ∧
        (decide "workspace/inlayHint/refresh" declared registrationsAllowed = .forward ↔ declared = true) ∧
        (decide "workspace/codeLens/refresh" declared registrationsAllowed = .forward ↔ declared = true) ∧
        (decide "workspace/inlineValue/refresh" declared registrationsAllowed = .forward ↔ declared = true) ∧
        (decide "workspace/diagnostic/refresh" declared registrationsAllowed = .forward ↔ declared = true) ∧
        (decide "workspace/foldingRange/refresh" declared registrationsAllowed = .forward ↔ declared = true) := by
      cases declared <;> simp [decide, refreshCapability]

    theorem unsupported_refresh_is_acknowledged (registrationsAllowed : Bool) :
        decide "workspace/semanticTokens/refresh" false registrationsAllowed = .acknowledge ∧
        decide "workspace/inlayHint/refresh" false registrationsAllowed = .acknowledge ∧
        decide "workspace/codeLens/refresh" false registrationsAllowed = .acknowledge ∧
        decide "workspace/inlineValue/refresh" false registrationsAllowed = .acknowledge ∧
        decide "workspace/diagnostic/refresh" false registrationsAllowed = .acknowledge ∧
        decide "workspace/foldingRange/refresh" false registrationsAllowed = .acknowledge := by
      simp [decide, refreshCapability]

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
        (h : method ∉ ["workspace/semanticTokens/refresh", "workspace/inlayHint/refresh",
          "workspace/codeLens/refresh", "workspace/inlineValue/refresh",
          "workspace/diagnostic/refresh", "workspace/foldingRange/refresh"])
        (hr : (method == "client/registerCapability") = false)
        (declared registrationsAllowed : Bool) :
        decide method declared registrationsAllowed = .forward := by
      simp_all [decide, refreshCapability]

    theorem unrelated_registrations_are_allowed (method : String) (declared : Bool)
        (h : method ≠ "workspace/didChangeWatchedFiles") :
        registrationAllowed method declared = true := by
      simp [registrationAllowed, h]

    theorem refresh_capabilities_are_independent :
        (["workspace/semanticTokens/refresh", "workspace/inlayHint/refresh",
          "workspace/codeLens/refresh", "workspace/inlineValue/refresh",
          "workspace/diagnostic/refresh", "workspace/foldingRange/refresh"].filterMap
            refreshCapability).Nodup := by
      decide
    "#
}

pub struct Capabilities(Value);

impl Capabilities {
    pub fn new(value: Value) -> Self {
        Self(value)
    }

    fn declared(&self, path: &str) -> bool {
        self.0.pointer(path).and_then(Value::as_bool) == Some(true)
    }

    pub fn server(&self, message: &Value) -> Action {
        let (Some(method), Some(id)) = (message["method"].as_str(), message.get("id")) else {
            return Action::Forward;
        };
        let declared = model::refresh_capability(method).is_some_and(|path| self.declared(&path));
        let registrations_allowed =
            message["params"]["registrations"]
                .as_array()
                .is_some_and(|entries| {
                    entries.iter().all(|entry| {
                        entry["method"].as_str().is_some_and(|method| {
                            model::registration_allowed(
                                method,
                                self.declared(
                                    "/workspace/didChangeWatchedFiles/dynamicRegistration",
                                ),
                            )
                        })
                    })
                });
        match model::decide(method, declared, registrations_allowed) {
            model::Decision::Forward => Action::Forward,
            model::Decision::Acknowledge => {
                Action::Reply(json!({"jsonrpc":"2.0", "id":id, "result":null}))
            }
            model::Decision::Unsupported => Action::Reply(json!({"jsonrpc":"2.0", "id":id,
                "error":{"code":-32601, "message":"クライアントが動的登録への対応を宣言していません"}})),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refresh_capabilities_are_independent_and_require_boolean_true() {
        let methods = [
            ("semanticTokens", "semanticTokens"),
            ("inlayHint", "inlayHint"),
            ("codeLens", "codeLens"),
            ("inlineValue", "inlineValue"),
            ("diagnostic", "diagnostics"),
            ("foldingRange", "foldingRange"),
        ];
        for (method, capability) in methods {
            let request = json!({"id":"refresh", "method":format!("workspace/{method}/refresh")});
            for value in [
                Value::Null,
                json!(false),
                json!("true"),
                json!({}),
                json!(true),
            ] {
                let policy =
                    Capabilities::new(json!({"workspace":{capability:{"refreshSupport":value}}}));
                let action = policy.server(&request);
                if value == json!(true) {
                    assert!(matches!(action, Action::Forward));
                } else {
                    let Action::Reply(reply) = action else {
                        panic!()
                    };
                    assert_eq!(
                        reply,
                        json!({"jsonrpc":"2.0", "id":"refresh", "result":null})
                    );
                }
            }
            let other =
                Capabilities::new(json!({"workspace":{"unrelated":{"refreshSupport":true}}}));
            assert!(matches!(other.server(&request), Action::Reply(_)));
        }
    }

    #[test]
    fn registration_checks_every_entry_before_forwarding() {
        let request = json!({"id":7, "method":"client/registerCapability", "params":{
        "registrations":[
            {"id":"extension", "method":"$/lean/extension"},
            {"id":"watcher", "method":"workspace/didChangeWatchedFiles"}
        ]}});
        let unsupported = Capabilities::new(json!({}));
        let Action::Reply(reply) = unsupported.server(&request) else {
            panic!()
        };
        assert_eq!(reply["id"], 7);
        assert_eq!(reply["error"]["code"], -32601);
        let supported = Capabilities::new(
            json!({"workspace":{"didChangeWatchedFiles":{"dynamicRegistration":true}}}),
        );
        assert!(matches!(supported.server(&request), Action::Forward));
    }

    #[test]
    fn notifications_responses_and_extensions_are_forwarded() {
        let policy = Capabilities::new(json!({}));
        for message in [
            json!({"method":"textDocument/publishDiagnostics", "params":{"diagnostics":[]}}),
            json!({"id":1, "result":null}),
            json!({"id":1, "method":"$/lean/extension"}),
        ] {
            assert!(matches!(policy.server(&message), Action::Forward));
        }
    }
}
