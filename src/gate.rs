use serde_json::{Value, json};

use crate::lifecycle::model::{self, Event, Phase};

pub enum Action {
    Forward,
    Drop,
    Reply(Value),
}

pub struct Gate {
    phase: Phase,
    initialize: Option<Value>,
    shutdown: Option<Value>,
}

impl Gate {
    pub fn new() -> Self {
        Self {
            phase: Phase::Fresh,
            initialize: None,
            shutdown: None,
        }
    }

    fn advance(&mut self, event: Event) -> bool {
        if let Some(phase) = model::transition(self.phase, event) {
            self.phase = phase;
            true
        } else {
            false
        }
    }

    pub fn client(&mut self, message: &Value) -> Action {
        let Some(method) = message.get("method").and_then(Value::as_str) else {
            return Action::Forward;
        };
        if let Some(id) = message.get("id") {
            let event = match method {
                "initialize" => Event::Initialize,
                "shutdown" => Event::Shutdown,
                _ => Event::Request,
            };
            if !self.advance(event) {
                let code = if matches!(
                    self.phase,
                    Phase::Fresh | Phase::Initializing | Phase::Ready
                ) {
                    -32002
                } else {
                    -32600
                };
                return Action::Reply(json!({"jsonrpc":"2.0", "id":id,
                    "error":{"code":code, "message":"Request is not valid in the current lifecycle phase"}}));
            }
            match method {
                "initialize" => self.initialize = Some(id.clone()),
                "shutdown" => self.shutdown = Some(id.clone()),
                _ => {}
            }
            return Action::Forward;
        }
        let accepted = match method {
            "initialized" => self.advance(Event::Initialized),
            "exit" => self.advance(Event::Exit),
            _ => matches!(self.phase, Phase::Running),
        };
        if accepted {
            Action::Forward
        } else {
            Action::Drop
        }
    }

    pub fn server(&mut self, message: &Value) {
        if message.get("method").is_some() {
            return;
        }
        let Some(id) = message.get("id") else { return };
        let success = message.get("error").is_none();
        if self.initialize.as_ref() == Some(id) {
            self.advance(if success {
                Event::InitializeOk
            } else {
                Event::InitializeError
            });
            self.initialize = None;
        } else if self.shutdown.as_ref() == Some(id) {
            self.advance(if success {
                Event::ShutdownOk
            } else {
                Event::ShutdownError
            });
            self.shutdown = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lifecycle_matches_string_ids_and_rejects_requests_after_shutdown() {
        let mut gate = Gate::new();
        assert!(matches!(
            gate.client(&json!({"id":1,"method":"textDocument/hover"})),
            Action::Reply(_)
        ));
        assert!(matches!(
            gate.client(&json!({"id":"init","method":"initialize"})),
            Action::Forward
        ));
        gate.server(&json!({"id":"unrelated","result":null}));
        assert!(matches!(
            gate.client(&json!({"method":"initialized"})),
            Action::Drop
        ));
        gate.server(&json!({"id":"init","result":{"capabilities":{}}}));
        assert!(matches!(
            gate.client(&json!({"method":"initialized"})),
            Action::Forward
        ));
        assert!(matches!(
            gate.client(&json!({"id":2,"method":"textDocument/hover"})),
            Action::Forward
        ));
        assert!(matches!(
            gate.client(&json!({"id":3,"method":"shutdown"})),
            Action::Forward
        ));
        gate.server(&json!({"id":3,"result":null}));
        assert!(matches!(
            gate.client(&json!({"id":4,"method":"textDocument/hover"})),
            Action::Reply(_)
        ));
        assert!(matches!(
            gate.client(&json!({"method":"exit"})),
            Action::Forward
        ));
    }
}
