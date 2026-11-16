import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from client import Client

ROOT = Path(__file__).resolve().parent.parent
URI = (ROOT / "Protocol.lean").as_uri()
DOCUMENT = {"textDocument": {"uri": URI}}
POSITION = {**DOCUMENT, "position": {"line": 3, "character": 12}}
GOAL = {**DOCUMENT, "position": {"line": 4, "character": 2}}
TEXT = (ROOT / "tests/fixtures/Protocol.lean").read_text()
CAPABILITIES = {
    "workspace": {
        "didChangeWatchedFiles": {"dynamicRegistration": True},
        "semanticTokens": {"refreshSupport": True},
        "inlayHint": {"refreshSupport": True},
    },
    "lean": {"rpcWireFormat": "v1"},
}


def initialize(client):
    result = client.request(
        "initialize",
        {
            "processId": os.getpid(),
            "rootUri": ROOT.as_uri(),
            "capabilities": CAPABILITIES,
        },
    )
    client.notify("initialized", {})
    return result


def opened(client, text=TEXT, version=1):
    client.notify(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": URI,
                "languageId": "lean4",
                "version": version,
                "text": text,
            }
        },
    )


def wait_document(client, version=1):
    return client.request(
        "textDocument/waitForDiagnostics", {"uri": URI, "version": version}
    )


def finish(client):
    result = client.request("shutdown")
    client.notify("exit")
    client.process.wait(timeout=10)
    assert client.process.returncode == 0, client.errors
    return result


def fixture_environment():
    output = ROOT / "target/fixtures"
    output.mkdir(parents=True, exist_ok=True)
    fixtures = ROOT / "tests/fixtures"
    subprocess.run(
        [
            "lean",
            "-R",
            str(fixtures),
            "-o",
            str(output / "Dependency.olean"),
            str(fixtures / "Dependency.lean"),
        ],
        check=True,
    )
    env = dict(os.environ)
    for name, path in [("LEAN_PATH", output), ("LEAN_SRC_PATH", fixtures)]:
        env[name] = os.pathsep.join([str(path), *([env[name]] if name in env else [])])
    return env


def dependency_effect(client, method):
    source = ROOT / "tests/fixtures/Dependency.lean"
    dependent_uri = (ROOT / "Dependent.lean").as_uri()
    for uri, text in [
        (source.as_uri(), source.read_text()),
        (dependent_uri, "import Dependency\n#check conformanceDependency\n"),
    ]:
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "lean4",
                    "version": 1,
                    "text": text,
                }
            },
        )
        client.request("textDocument/waitForDiagnostics", {"uri": uri, "version": 1})
    client.request("$/lean/waitForILeans", {"uri": dependent_uri, "version": 1})
    first = len(client.notifications)

    def diagnostics():
        return [
            m["params"]
            for m in client.notifications[first:]
            if m["method"] == "textDocument/publishDiagnostics"
            and m["params"]["uri"] == dependent_uri
            and any("out of date" in d["message"] for d in m["params"]["diagnostics"])
        ]

    deadline = time.monotonic() + 10
    while not diagnostics():
        assert time.monotonic() < deadline, (method, client.notifications[first:])
        if method == "textDocument/didSave":
            client.notify(
                method,
                {"textDocument": {"uri": source.as_uri()}, "text": source.read_text()},
            )
        else:
            client.notify(method, {"changes": [{"uri": source.as_uri(), "type": 2}]})
        client.drain(0.1)
    result = diagnostics()[-1]
    for uri in [dependent_uri, source.as_uri()]:
        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    return result


class Normalize:
    def __init__(self):
        self.references = {}

    def __call__(self, value):
        if isinstance(value, dict):
            if set(value) == {"__rpcref"}:
                key = str(value["__rpcref"])
                if key not in self.references:
                    self.references[key] = len(self.references)
                return {"__rpcref": self.references[key]}
            return {key: self(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [self(item) for item in value]
        return value


def protocol_session(client):
    results = {}
    normalize = Normalize()

    def record(method, result):
        results[method] = normalize(result)
        return result

    def request(method, params=None):
        return record(method, client.request(method, params))

    record("initialize", initialize(client))
    opened(client)
    client.until(
        lambda: any(
            m["method"] == "workspace/semanticTokens/refresh"
            for m in client.server_requests
        ),
        timeout=5,
    )
    request("textDocument/waitForDiagnostics", {"uri": URI, "version": 1})
    client.request("$/lean/waitForILeans", {"uri": URI, "version": 1})
    diagnostics = [
        m["params"]
        for m in client.notifications
        if m["method"] == "textDocument/publishDiagnostics"
    ]
    assert diagnostics and diagnostics[-1]["diagnostics"], diagnostics
    assert all(d.get("severity") != 1 for d in diagnostics[-1]["diagnostics"]), (
        diagnostics
    )
    record("textDocument/didOpen", request("$/lean/plainGoal", GOAL))
    record("initialized", True)
    completion = request(
        "textDocument/completion",
        {**DOCUMENT, "position": {"line": 5, "character": 10}},
    )
    items = completion["items"] if isinstance(completion, dict) else completion
    assert items, completion
    request("completionItem/resolve", next(i for i in items if i["label"] == "twice"))
    area = {"start": {"line": 3, "character": 0}, "end": {"line": 4, "character": 5}}
    action_position = {"line": len(TEXT.splitlines()) - 1, "character": 0}
    actions = request(
        "textDocument/codeAction",
        {
            **DOCUMENT,
            "range": {"start": action_position, "end": action_position},
            "context": {"diagnostics": []},
        },
    )
    assert actions, actions
    resolved = request(
        "codeAction/resolve", next(a for a in actions if a["title"] == "検証")
    )
    assert resolved["title"] == "解決済み", resolved
    for method in [
        "signatureHelp",
        "hover",
        "documentHighlight",
        "definition",
        "declaration",
        "typeDefinition",
        "prepareRename",
    ]:
        request("textDocument/" + method, POSITION)
    for method in [
        "documentSymbol",
        "foldingRange",
        "documentColor",
        "semanticTokens/full",
    ]:
        request("textDocument/" + method, DOCUMENT)
    for method in ["semanticTokens/range", "inlayHint"]:
        request("textDocument/" + method, {**DOCUMENT, "range": area})
    request(
        "textDocument/references", {**POSITION, "context": {"includeDeclaration": True}}
    )
    calls = request("textDocument/prepareCallHierarchy", POSITION)
    assert calls, calls
    for method in ["incomingCalls", "outgoingCalls"]:
        request("callHierarchy/" + method, {"item": calls[0]})
    modules = request("$/lean/prepareModuleHierarchy", DOCUMENT)
    assert modules, modules
    for method in ["imports", "importedBy"]:
        request("$/lean/moduleHierarchy/" + method, {"module": modules})
    request("textDocument/rename", {**POSITION, "newName": "thrice"})
    request("workspace/symbol", {"query": "twice"})
    request(
        "$/lean/plainTermGoal", {**DOCUMENT, "position": {"line": 3, "character": 25}}
    )
    request("$/lean/waitForILeans", {})
    session = client.request("$/lean/rpc/connect", {"uri": URI})
    assert isinstance(session["sessionId"], (int, str)), session
    record("$/lean/rpc/connect", {"sessionId": "セッション"})

    def rpc(method, params):
        return client.request(
            "$/lean/rpc/call",
            {
                **DOCUMENT,
                "position": action_position,
                **session,
                "method": method,
                "params": params,
            },
        )

    goals = record(
        "Lean.Widget.getInteractiveGoals", rpc("Lean.Widget.getInteractiveGoals", GOAL)
    )
    assert goals and goals["goals"], goals
    info = goals["goals"][0]["type"]["tag"][0]["info"]
    record(
        "Lean.Widget.InteractiveDiagnostics.infoToInteractive",
        rpc("Lean.Widget.InteractiveDiagnostics.infoToInteractive", info),
    )
    record(
        "Lean.Widget.getGoToLocation",
        rpc("Lean.Widget.getGoToLocation", {"kind": "definition", "info": info}),
    )
    record(
        "Lean.Widget.getInteractiveTermGoal",
        rpc(
            "Lean.Widget.getInteractiveTermGoal",
            {**DOCUMENT, "position": {"line": 3, "character": 25}},
        ),
    )
    interactive = record(
        "Lean.Widget.getInteractiveDiagnostics",
        rpc("Lean.Widget.getInteractiveDiagnostics", {}),
    )
    assert interactive, interactive
    record("Lean.Widget.getWidgets", rpc("Lean.Widget.getWidgets", GOAL["position"]))
    record(
        "Lean.Widget.highlightMatches",
        rpc(
            "Lean.Widget.highlightMatches",
            {"query": "twice", "msg": interactive[-1]["message"]},
        ),
    )
    message, children = rpc("conformanceRefs", {})
    record(
        "Lean.Widget.InteractiveDiagnostics.msgToInteractive",
        rpc(
            "Lean.Widget.InteractiveDiagnostics.msgToInteractive",
            {"msg": message, "indent": 0},
        ),
    )
    record(
        "Lean.Widget.lazyTraceChildrenToInteractive",
        rpc("Lean.Widget.lazyTraceChildrenToInteractive", children),
    )
    widget_hash = rpc("conformanceWidgetHash", {})
    record(
        "Lean.Widget.getWidgetSource",
        rpc(
            "Lean.Widget.getWidgetSource",
            {"hash": widget_hash, "pos": {"line": 23, "character": 0}},
        ),
    )
    record("$/lean/rpc/call", True)
    client.notify("$/lean/rpc/keepAlive", {"uri": URI, **session})
    record(
        "$/lean/rpc/keepAlive",
        rpc(
            "Lean.Widget.InteractiveDiagnostics.msgToInteractive",
            {"msg": message, "indent": 0},
        ),
    )
    client.notify(
        "$/lean/rpc/release", {"uri": URI, **session, "refs": [message, children]}
    )
    released = client.response(
        client.start(
            "$/lean/rpc/call",
            {
                **GOAL,
                **session,
                "method": "Lean.Widget.InteractiveDiagnostics.msgToInteractive",
                "params": {"msg": message, "indent": 0},
            },
        )
    )
    assert "error" in released, released
    record("$/lean/rpc/release", released["error"])
    for method in ["textDocument/didSave", "workspace/didChangeWatchedFiles"]:
        record(method, dependency_effect(client, method))
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": 2},
            "contentChanges": [{"text": TEXT.replace("n + n", "n * 2")}],
        },
    )
    wait_document(client, 2)
    record(
        "textDocument/didChange",
        client.request("textDocument/documentHighlight", POSITION),
    )
    client.drain()
    required = {
        "client/registerCapability",
        "workspace/semanticTokens/refresh",
        "workspace/inlayHint/refresh",
    }
    client.until(
        lambda: required <= {m["method"] for m in client.server_requests}, timeout=5
    )
    final_diagnostics = [
        m["params"]
        for m in client.notifications
        if m["method"] == "textDocument/publishDiagnostics"
        and m["params"]["uri"] == URI
    ][-1]
    assert final_diagnostics["version"] == 2, final_diagnostics
    record("textDocument/publishDiagnostics", final_diagnostics)
    progress = [
        m["params"]
        for m in client.notifications
        if m["method"] == "$/lean/fileProgress"
    ]
    assert progress and any(p["processing"] == [] for p in progress), progress
    record(
        "$/lean/fileProgress",
        sorted(
            {json.dumps(p, sort_keys=True) for p in progress if not p["processing"]}
        ),
    )
    for method in [
        "client/registerCapability",
        "workspace/semanticTokens/refresh",
        "workspace/inlayHint/refresh",
    ]:
        requests = [
            json.loads(json.dumps(m.get("params")))
            for m in client.server_requests
            if m["method"] == method
        ]
        assert requests, (method, client.server_requests)
        if method == "client/registerCapability":
            for params in requests:
                for registration in params["registrations"]:
                    registration.pop("id")
        record(method, sorted({json.dumps(p, sort_keys=True) for p in requests}))
    client.notify("textDocument/didClose", DOCUMENT)
    closed = client.response(client.start("textDocument/hover", POSITION))
    assert closed["error"]["code"] == -32801, closed
    record("textDocument/didClose", closed["error"])
    record("shutdown", finish(client))
    record("exit", client.process.returncode)
    return results


def cancellation(client, candidate):
    initialize(client)
    slow = "#eval IO.sleep 1500\ndef obsolete : Nat := 1\n#check obsolete\n"
    opened(client, slow)
    query = {**DOCUMENT, "position": {"line": 2, "character": 9}}
    cancelled = client.start("textDocument/hover", query)
    client.notify("$/cancelRequest", {"id": cancelled})
    response = client.response(cancelled, timeout=5)
    assert response.get("error", {}).get("code") == -32800, response
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": 2},
            "contentChanges": [{"text": slow.replace("1500", "1501")}],
        },
    )
    client.request("$/lean/rpc/connect", {"uri": URI})
    stale = client.start("textDocument/hover", query)
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": 3},
            "contentChanges": [
                {"text": "def current : Bool := true\n#check current\n"}
            ],
        },
    )
    response = client.response(stale, timeout=5)
    if candidate:
        assert response.get("error", {}).get("code") == -32801, response
    else:
        assert (
            response.get("error", {}).get("code") == -32801
            or response.get("result", False) is None
        ), response
    wait_document(client, 3)
    current = client.request(
        "textDocument/hover", {**DOCUMENT, "position": {"line": 1, "character": 9}}
    )
    assert (
        "current" in current["contents"]["value"]
        and "Bool" in current["contents"]["value"]
    ), current
    client.drain()
    assert "result" not in client.responses[cancelled] and (
        client.responses[stale].get("result") is None
    )
    client.notify("textDocument/didClose", DOCUMENT)
    finish(client)
    return {"cancel_code": -32800, "stale_response": response, "current": current}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "target/conformance.json")
    parser.add_argument("--upstream-only", action="store_true")
    args = parser.parse_args()
    baseline = [shutil.which("lean"), "--server"]
    candidate = [str(ROOT / "tests/command"), "--server"]
    report = {
        "lean_version": subprocess.check_output(
            [baseline[0], "--version"], text=True
        ).strip()
    }
    prefix = Path(
        subprocess.check_output([baseline[0], "--print-prefix"], text=True).strip()
    )
    source = (prefix / "src/lean/Lean/Server/ProtocolOverview.lean").read_text()
    upstream_methods = [
        a or b for a, b in re.findall(r'method := (?:"([^"]+)"|`([\w.]+))', source)
    ]
    inventory = json.loads((ROOT / "tests/protocol.json").read_text())["entries"]
    assert [entry["method"] for entry in inventory] == upstream_methods
    args.output.parent.mkdir(parents=True, exist_ok=True)
    env = fixture_environment()
    try:
        if args.upstream_only:
            with Client(baseline) as client:
                try:
                    report["baseline_cancellation"] = cancellation(client, False)
                finally:
                    (args.output.parent / "baseline-cancellation.json").write_text(
                        json.dumps(client.transcript, ensure_ascii=False, indent=2)
                        + "\n"
                    )
            return
        sessions = []
        for label, command in [("baseline", baseline), ("candidate", candidate)]:
            with Client(command, env=env) as client:
                try:
                    sessions.append(protocol_session(client))
                finally:
                    (args.output.parent / f"{label}-protocol.json").write_text(
                        json.dumps(client.transcript, ensure_ascii=False, indent=2)
                        + "\n"
                    )
            with Client(command) as client:
                try:
                    report[label + "_cancellation"] = cancellation(
                        client, label == "candidate"
                    )
                finally:
                    (args.output.parent / f"{label}-cancellation.json").write_text(
                        json.dumps(client.transcript, ensure_ascii=False, indent=2)
                        + "\n"
                    )
        baseline_results, candidate_results = sessions
        for method in baseline_results:
            assert baseline_results[method] == candidate_results[method], (
                method,
                baseline_results[method],
                candidate_results[method],
            )
        for field in ["cancel_code", "current"]:
            assert (
                report["baseline_cancellation"][field]
                == report["candidate_cancellation"][field]
            )
        candidate_results["$/cancelRequest"] = report["candidate_cancellation"][
            "cancel_code"
        ]
        assert len(inventory) == 59
        assert set(candidate_results) == {entry["method"] for entry in inventory}, set(
            candidate_results
        ) ^ {entry["method"] for entry in inventory}
        report["entries"] = [
            {**entry, "status": "一致", "result": candidate_results[entry["method"]]}
            for entry in inventory
        ]
        report["matched"] = len(inventory)
        report["excluded"] = []
        report["comparison_rules"] = [
            "応答本文と配列の順序を比較する。RPC の参照 ID は同一性を保つ連番に変換し、sessionId は接続の成功と同じ接続での呼び出しを検査する。",
            "server→client 要求の ID と登録 ID を除き、method・params の集合を比較する。要求ごとに元の ID で応答する。",
            "publishDiagnostics は最終版、fileProgress は版ごとの完了通知を比較する。解析途中の範囲・通知回数・到着順序は実行時刻に依存するため比較対象から除く。",
            "保存・ファイル監視は import closure の非同期登録を待ちながら通知し、依存文書の古い import を知らせる診断本文を比較する。",
        ]
    finally:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("59 項目とキャンセル・編集後の旧要求の比較に成功しました")


if __name__ == "__main__":
    main()
