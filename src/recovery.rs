use std::collections::{HashMap, HashSet};

use serde_json::{Value, json};

leanfran::lean! {
    pub mod model;
    r#"
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
    "#
}

#[derive(Default)]
pub struct Recovery {
    versions: HashMap<String, (Value, u64)>,
    crashed: HashSet<String>,
    revision: u64,
    pending: Vec<Pending>,
}

struct Pending {
    id: Value,
    document: Option<(String, u64)>,
    cancelled: bool,
}

fn document_uri(message: &Value) -> Option<&str> {
    [
        "/params/textDocument/uri",
        "/params/uri",
        "/params/data/uri",
        "/params/data/params/textDocument/uri",
        "/params/item/uri",
        "/params/module/uri",
    ]
    .iter()
    .find_map(|path| message.pointer(path).and_then(Value::as_str))
}

impl Recovery {
    pub fn server(&mut self, message: &Value) -> Option<Value> {
        if message.get("method").is_none() {
            let index = self
                .pending
                .iter()
                .position(|p| Some(&p.id) == message.get("id"))?;
            let pending = self.pending.remove(index);
            let same_version = pending.document.as_ref().is_none_or(|(uri, revision)| {
                self.versions
                    .get(uri)
                    .is_some_and(|(_, current)| current == revision)
            });
            let code = match model::response_decision(pending.cancelled, same_version) {
                model::ResponseDecision::Forward => return None,
                model::ResponseDecision::Cancelled => -32800,
                model::ResponseDecision::Modified => -32801,
            };
            return Some(json!({"jsonrpc":"2.0", "id":pending.id,
                "error":{"code":code,"message":"要求が取り消されたか、文書が変更されました"}}));
        }
        if message["method"] != "$/lean/fileProgress" {
            return None;
        }
        if let (Some(uri), Some(processing)) = (
            message["params"]["textDocument"]["uri"].as_str(),
            message["params"]["processing"].as_array(),
        ) && self.versions.get(uri).map(|(version, _)| version)
            == message["params"]["textDocument"].get("version")
            && processing.iter().any(|entry| entry["kind"] == 2)
        {
            self.crashed.insert(uri.to_owned());
        }
        None
    }

    pub fn fail_pending(&mut self) -> impl Iterator<Item = Value> + '_ {
        self.pending.drain(..).map(|pending| pending.id)
    }

    pub fn client(&mut self, message: &Value) -> Option<Value> {
        let method = message["method"].as_str()?;
        let params = &message["params"];
        if method == "$/cancelRequest" {
            for pending in &mut self.pending {
                if pending.id == params["id"] {
                    pending.cancelled = true;
                }
            }
        }
        if let Some(id) = message.get("id") {
            self.pending.push(Pending {
                id: id.clone(),
                document: document_uri(message).and_then(|uri| {
                    self.versions
                        .get(uri)
                        .map(|(_, revision)| (uri.to_owned(), *revision))
                }),
                cancelled: false,
            });
        }
        let uri = document_uri(message)?;
        match method {
            "textDocument/didOpen" | "textDocument/didChange"
                if method == "textDocument/didOpen"
                    || params["contentChanges"]
                        .as_array()
                        .is_some_and(|changes| !changes.is_empty()) =>
            {
                self.revision += 1;
                self.versions.insert(
                    uri.to_owned(),
                    (params["textDocument"]["version"].clone(), self.revision),
                );
                self.crashed.remove(uri);
                None
            }
            "textDocument/didClose" => {
                self.versions.remove(uri);
                self.crashed.remove(uri);
                None
            }
            _ if model::restart_worker(
                self.crashed.contains(uri),
                self.versions.contains_key(uri),
                message.get("id").is_some(),
            ) =>
            {
                self.crashed.remove(uri);
                Some(json!({"jsonrpc":"2.0", "method":"textDocument/didChange",
                    "params":{"textDocument":{"uri":uri,"version":self.versions[uri].0},
                        "contentChanges":[{"range":{"start":{"line":0,"character":0},
                            "end":{"line":0,"character":0}},"text":""}]}}))
            }
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn open(recovery: &mut Recovery, uri: &str) {
        recovery.client(&json!({"method":"textDocument/didOpen",
            "params":{"textDocument":{"uri":uri,"version":1}}}));
    }

    #[test]
    fn cancellation_distinguishes_string_ids_and_server_requests() {
        let mut recovery = Recovery::default();
        open(&mut recovery, "file:///a.lean");
        for id in [json!(7), json!("7")] {
            recovery.client(&json!({"method":"textDocument/hover","id":id,
                "params":{"textDocument":{"uri":"file:///a.lean"}}}));
        }
        assert!(
            recovery
                .server(&json!({"method":"workspace/inlayHint/refresh","id":"7"}))
                .is_none()
        );
        recovery.client(&json!({"method":"$/cancelRequest","params":{"id":"7"}}));
        assert!(
            recovery
                .server(&json!({"id":7,"result":"current"}))
                .is_none()
        );
        assert_eq!(
            recovery
                .server(&json!({"id":"7","result":"obsolete"}))
                .unwrap()["error"]["code"],
            -32800
        );
        assert_eq!(recovery.fail_pending().count(), 0);
    }

    #[test]
    fn editing_and_reopening_invalidate_only_their_own_requests() {
        let mut recovery = Recovery::default();
        for uri in ["file:///a.lean", "file:///b.lean"] {
            open(&mut recovery, uri);
            recovery.client(&json!({"method":"textDocument/hover","id":uri,
                "params":{"textDocument":{"uri":uri}}}));
        }
        recovery.client(&json!({"method":"textDocument/didChange",
            "params":{"textDocument":{"uri":"file:///a.lean","version":2},
                "contentChanges":[{"text":"def changed := 1"}]}}));
        assert_eq!(
            recovery
                .server(&json!({"id":"file:///a.lean","result":"obsolete"}))
                .unwrap()["error"]["code"],
            -32801
        );
        assert!(
            recovery
                .server(&json!({"id":"file:///b.lean","result":"current"}))
                .is_none()
        );
        recovery.client(&json!({"method":"completionItem/resolve","id":3,
            "params":{"data":{"uri":"file:///a.lean"}}}));
        recovery.client(&json!({"method":"textDocument/didClose",
            "params":{"textDocument":{"uri":"file:///a.lean"}}}));
        open(&mut recovery, "file:///a.lean");
        assert_eq!(
            recovery
                .server(&json!({"id":3,"result":"obsolete"}))
                .unwrap()["error"]["code"],
            -32801
        );
    }

    #[test]
    fn fatal_progress_restarts_once_for_a_current_open_document() {
        let mut recovery = Recovery::default();
        open(&mut recovery, "file:///a.lean");
        let request = json!({"method":"textDocument/hover","id":1,
            "params":{"textDocument":{"uri":"file:///a.lean"}}});
        recovery.server(&json!({"method":"$/lean/fileProgress",
            "params":{"textDocument":{"uri":"file:///a.lean","version":0},
                "processing":[{"kind":2}]}}));
        assert!(recovery.client(&request).is_none());
        recovery.server(&json!({"method":"$/lean/fileProgress",
            "params":{"textDocument":{"uri":"file:///a.lean","version":1},
                "processing":[{"kind":2}]}}));
        recovery.client(&json!({"method":"textDocument/didChange",
            "params":{"textDocument":{"uri":"file:///a.lean","version":2},"contentChanges":[]}}));
        let restart = recovery.client(&request).unwrap();
        assert_eq!(restart["params"]["textDocument"]["version"], 1);
        assert_eq!(restart["params"]["contentChanges"][0]["text"], "");
        assert!(recovery.client(&request).is_none());
    }
}
