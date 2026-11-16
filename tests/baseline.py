import argparse
import json
import math
import os
import statistics
import subprocess
import time
from pathlib import Path

from client import Client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--declarations", type=int, default=0)
    parser.add_argument("--edits", type=int, default=0)
    parser.add_argument("--server-arg", action="append", default=[])
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--lifecycle-check", action="store_true")
    parser.add_argument("--touchstone", type=Path)
    parser.add_argument("--record-file", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    if args.declarations < 0 or args.edits < 0:
        parser.error("--declarations and --edits must be nonnegative")
    if bool(args.touchstone) != bool(args.record_file):
        parser.error("--touchstone and --record-file must be supplied together")
    env = dict(os.environ, LEAN_WORKER_PATH=args.lean)
    command = [args.lean, "--server", *args.server_arg]
    if args.touchstone:
        if args.record_file.exists():
            parser.error("--record-file must name a new file")
        command = [
            str(args.touchstone),
            "proxy",
            "run-proxy",
            "--record-file",
            str(args.record_file),
            "--server-command-args",
            *command,
        ]
    client = Client(command, env=env)
    process = client.process
    errors = client.errors
    notifications = {}
    diagnostics = []

    def send(message):
        if message.get("params") is None:
            message.pop("params", None)
        client.send(message)

    def request(method, params, expected_error=None):
        start = time.perf_counter_ns()
        response = client.response(client.start(method, params))
        notifications.clear()
        diagnostics.clear()
        for message in client.notifications + client.server_requests:
            name = message["method"]
            notifications[name] = notifications.get(name, 0) + 1
            if name == "textDocument/publishDiagnostics":
                diagnostics.append(message["params"])
        if "error" in response:
            if response["error"]["code"] != expected_error:
                raise RuntimeError(f"{method}: {response['error']}")
            result = response["error"]
        else:
            if expected_error is not None:
                raise AssertionError(f"{method} が予期せず成功しました")
            result = response.get("result")
        return result, (time.perf_counter_ns() - start) / 1e6

    try:
        if args.lifecycle_check:
            request(
                "textDocument/hover",
                {
                    "textDocument": {"uri": "file:///early.lean"},
                    "position": {"line": 0, "character": 0},
                },
                expected_error=-32002,
            )
        result, initialize_ms = request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": Path.cwd().as_uri(),
                "capabilities": {},
            },
        )
        send({"method": "initialized", "params": {}})
        uri = (Path.cwd() / "Baseline.lean").as_uri()
        text = (
            "def twice (n : Nat) : Nat := n + n\nexample : twice 2 = 4 := by\n  rfl\n"
        )
        text += "".join(
            f"def value{i} : Nat := {i}\n" for i in range(args.declarations)
        )
        send(
            {
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "lean4",
                        "version": 1,
                        "text": text,
                    }
                },
            }
        )
        _, elaboration_ms = request(
            "textDocument/waitForDiagnostics", {"uri": uri, "version": 1}
        )
        document = {"textDocument": {"uri": uri}}
        position = dict(**document, position={"line": 1, "character": 12})
        cases = {
            "textDocument/completion": position,
            "textDocument/hover": position,
            "textDocument/definition": position,
            "textDocument/documentSymbol": document,
            "textDocument/foldingRange": document,
            "textDocument/semanticTokens/full": document,
            "$/lean/plainGoal": dict(**document, position={"line": 2, "character": 2}),
            "textDocument/signatureHelp": position,
            "textDocument/documentHighlight": position,
            "textDocument/inlayHint": dict(
                **document,
                range={
                    "start": {"line": 0, "character": 0},
                    "end": {"line": args.declarations + 3, "character": 0},
                },
            ),
            "textDocument/codeAction": dict(
                **document,
                range={
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 2, "character": 5},
                },
                context={"diagnostics": []},
            ),
        }
        timings = {}
        responses = {}
        for method, params in cases.items():
            responses[method], cold_ms = request(method, params)
            samples = [request(method, params)[1] for _ in range(args.samples)]
            timings[method] = {
                "first_ms": cold_ms,
                "median_ms": statistics.median(samples),
                "p95_ms": sorted(samples)[math.ceil(len(samples) * 0.95) - 1],
                "samples_ms": samples,
            }
        if responses["textDocument/hover"] is None:
            raise AssertionError(
                f"hover returned null: {diagnostics}; stderr: {errors}"
            )
        if "twice" not in responses["textDocument/hover"]["contents"]["value"]:
            raise AssertionError("hover lost the declaration type")
        if not responses["textDocument/definition"]:
            raise AssertionError("definition returned no target")
        if len(responses["textDocument/documentSymbol"]) != args.declarations + 2:
            raise AssertionError("document symbols lost declarations")
        if responses["$/lean/plainGoal"]["goals"] != ["⊢ twice 2 = 4"]:
            raise AssertionError("goal state differs")
        edit_times = []
        for edit in range(args.edits):
            start = time.perf_counter_ns()
            send(
                {
                    "method": "textDocument/didChange",
                    "params": {
                        "textDocument": {"uri": uri, "version": edit + 2},
                        "contentChanges": [
                            {
                                "range": {
                                    "start": {"line": 0, "character": 29},
                                    "end": {"line": 0, "character": 34},
                                },
                                "text": "n * 2" if edit % 2 == 0 else "n + n",
                            }
                        ],
                    },
                }
            )
            request(
                "textDocument/waitForDiagnostics", {"uri": uri, "version": edit + 2}
            )
            edit_times.append((time.perf_counter_ns() - start) / 1e6)
        regression = {}
        if args.regression:
            for offset, extra in enumerate(["#check twice\n", ""]):
                version = args.edits + offset + 2
                send(
                    {
                        "method": "textDocument/didChange",
                        "params": {
                            "textDocument": {"uri": uri, "version": version},
                            "contentChanges": [{"text": text + extra}],
                        },
                    }
                )
                request(
                    "textDocument/waitForDiagnostics", {"uri": uri, "version": version}
                )
                highlights, _ = request("textDocument/documentHighlight", position)
                expected = 3 if extra else 2
                if len(highlights) != expected:
                    raise AssertionError(f"stale highlight index: {highlights}")
            regression["edit_invalidation"] = True
            local_highlights, _ = request(
                "textDocument/documentHighlight",
                {
                    **document,
                    "position": {"line": 0, "character": 11},
                },
            )
            if len(local_highlights) != 3:
                raise AssertionError(
                    f"cursor movement lost local references: {local_highlights}"
                )
            regression["cursor_movement"] = local_highlights
            unicode_uri = (Path.cwd() / "Unicode.lean").as_uri()
            unicode_text = "def «😀» (α : Nat) : Nat := α + α\n#check «😀»\n"
            unicode_doc = {"textDocument": {"uri": unicode_uri}}
            send(
                {
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": unicode_uri,
                            "languageId": "lean4",
                            "version": 1,
                            "text": unicode_text,
                        },
                    },
                }
            )
            request(
                "textDocument/waitForDiagnostics", {"uri": unicode_uri, "version": 1}
            )
            alpha_column = len(unicode_text.split("α", 1)[0].encode("utf-16-le")) // 2
            unicode_highlights, _ = request(
                "textDocument/documentHighlight",
                {
                    **unicode_doc,
                    "position": {"line": 0, "character": alpha_column},
                },
            )
            if len(unicode_highlights) != 3:
                raise AssertionError(
                    f"Unicode local references differ: {unicode_highlights}"
                )
            regression["utf16_local_references"] = unicode_highlights
            return_uri = (Path.cwd() / "Return.lean").as_uri()
            return_doc = {"textDocument": {"uri": return_uri}}
            send(
                {
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": return_uri,
                            "languageId": "lean4",
                            "version": 1,
                            "text": "def f : Id Nat := do\n  return 1\n",
                        },
                    },
                }
            )
            request(
                "textDocument/waitForDiagnostics", {"uri": return_uri, "version": 1}
            )
            return_highlights, _ = request(
                "textDocument/documentHighlight",
                {
                    **return_doc,
                    "position": {"line": 1, "character": 3},
                },
            )
            if len(return_highlights) != 1:
                raise AssertionError(f"return highlight differs: {return_highlights}")
            regression["return_highlight"] = return_highlights
            for extra_doc in [unicode_doc, return_doc]:
                send({"method": "textDocument/didClose", "params": extra_doc})
        send({"method": "textDocument/didClose", "params": document})
        request("shutdown", None)
        if args.lifecycle_check:
            request("textDocument/hover", position, expected_error=-32600)
        send({"method": "exit", "params": None})
        process.wait(timeout=10)
        if process.returncode:
            raise RuntimeError(f"server exit {process.returncode}: {errors}")
        report = {
            "lean_version": subprocess.check_output(
                [args.lean, "--version"], text=True
            ).strip(),
            "initialize_ms": initialize_ms,
            "elaboration_ms": elaboration_ms,
            "initialize_result": result,
            "timings": timings,
            "responses": responses,
            "notifications": notifications,
            "stderr": errors,
            "edit_ms": edit_times,
            "regression": regression,
            "lifecycle_checked": args.lifecycle_check,
            "workload": {
                "text": text,
                "samples": args.samples,
                "declarations": args.declarations,
                "edits": args.edits,
            },
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "initialize_ms": initialize_ms,
                    "elaboration_ms": elaboration_ms,
                    "timings": {
                        key: {k: v for k, v in value.items() if k != "samples_ms"}
                        for key, value in timings.items()
                    },
                },
                indent=2,
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
