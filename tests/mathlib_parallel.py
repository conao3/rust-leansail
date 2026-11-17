import hashlib
import json
import os
import signal
import time

from client import Client
from mathlib import FILES, Monitor, descendants, fixture, main, processes, stats


class TimedClient(Client):
    def __init__(self, command, env):
        self.received_at = {}
        self.diagnostics_at = {}
        super().__init__(command, env)

    def receive(self, timeout):
        message = super().receive(timeout)
        now = time.perf_counter_ns()
        if "method" not in message:
            self.received_at[message["id"]] = now
        elif message["method"] == "textDocument/publishDiagnostics":
            params = message["params"]
            self.diagnostics_at[(params["uri"], params.get("version"))] = now
        return message


def parallel_session(args, label, command, repetition):
    environment = dict(os.environ)
    environment.pop("LEAN_WORKER_PATH", None)
    if args.index_cache_dir:
        environment["LEANSAIL_INDEX_CACHE_DIR"] = str(args.index_cache_dir)
    client = TimedClient(command, environment)
    monitor = Monitor(client.process.pid, pss=True)
    result = {
        "server": label,
        "repetition": repetition,
        "command": command,
        "rounds": [],
    }

    def finish(request_id):
        reply = client.response(request_id, timeout=args.timeout)
        if "error" in reply:
            raise RuntimeError(f"要求が失敗しました: {reply}")
        return reply.get("result")

    def wait_document(uri, version):
        return client.start(
            "textDocument/waitForDiagnostics", {"uri": uri, "version": version}
        )

    def change(document, point, insert):
        document["version"] += 1
        client.notify(
            "textDocument/didChange",
            {
                "textDocument": {
                    "uri": document["uri"],
                    "version": document["version"],
                },
                "contentChanges": [
                    {
                        "range": {
                            "start": point,
                            "end": dict(
                                point,
                                character=point["character"] + (0 if insert else 1),
                            ),
                        },
                        "text": " " if insert else "",
                    }
                ],
            },
        )

    try:
        finish(
            client.start(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": args.workspace.as_uri(),
                    "capabilities": {},
                },
            )
        )
        client.notify("initialized", {})
        documents = []
        opened = time.perf_counter_ns()
        for relative in FILES:
            path = args.workspace / ".lake/packages/mathlib" / relative
            text, point, goal, early = fixture(path)
            document = {
                "uri": path.as_uri(),
                "version": 1,
                "point": point,
                "goal": goal,
                "early": early,
            }
            documents.append(document)
            client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": document["uri"],
                        "version": 1,
                        "languageId": "lean4",
                        "text": text,
                    }
                },
            )
        initial = [wait_document(d["uri"], d["version"]) for d in documents]
        for request_id in initial:
            finish(request_id)
        result["cold_open_complete_ms"] = (time.perf_counter_ns() - opened) / 1e6
        result["files"] = FILES
        active, *background = documents
        result["warmup_ms"] = {}
        for method in ["textDocument/hover", "textDocument/completion"]:
            started = time.perf_counter_ns()
            response = finish(
                client.start(
                    method,
                    {
                        "textDocument": {"uri": active["uri"]},
                        "position": active["point"],
                    },
                )
            )
            assert response, (method, response)
            result["warmup_ms"][method] = (time.perf_counter_ns() - started) / 1e6
        for sample in range(args.samples):
            phases = (
                ["quiet", "busy"]
                if (sample + repetition) % 2 == 0
                else ["busy", "quiet"]
            )
            for phase in phases:
                background_waits = []
                if phase == "busy":
                    for document in background:
                        change(document, document["early"], sample % 2 == 0)
                        background_waits.append(
                            wait_document(document["uri"], document["version"])
                        )
                started = time.perf_counter_ns()
                change(active, active["goal"], active["version"] % 2 == 1)
                requests = {
                    method: client.start(
                        method,
                        {
                            "textDocument": {"uri": active["uri"]},
                            "position": active["point"],
                        },
                    )
                    for method in ["textDocument/hover", "textDocument/completion"]
                }
                requests["diagnostics_complete"] = wait_document(
                    active["uri"], active["version"]
                )
                hashes = {}
                for method, request_id in requests.items():
                    response = finish(request_id)
                    if method != "diagnostics_complete":
                        assert response, (method, response)
                        if method.endswith("/hover"):
                            assert "twice" in response["contents"]["value"]
                        hashes[method] = hashlib.sha256(
                            json.dumps(
                                response, sort_keys=True, ensure_ascii=False
                            ).encode()
                        ).hexdigest()
                foreground_end = max(client.received_at[i] for i in requests.values())
                for request_id in background_waits:
                    finish(request_id)
                diagnostics = [
                    n["params"]
                    for n in client.notifications
                    if n["method"] == "textDocument/publishDiagnostics"
                    and n["params"]["uri"] == active["uri"]
                    and n["params"].get("version") == active["version"]
                ]
                assert diagnostics, "最新版の診断通知が届いていません"
                assert not [
                    d for d in diagnostics[-1]["diagnostics"] if d.get("severity") == 1
                ], diagnostics[-1]
                result["rounds"].append(
                    {
                        "phase": phase,
                        "sample": sample,
                        "active_version": active["version"],
                        "latency_ms": {
                            method: (client.received_at[request_id] - started) / 1e6
                            for method, request_id in requests.items()
                        },
                        "diagnostics_notification_ms": (
                            client.diagnostics_at[(active["uri"], active["version"])]
                            - started
                        )
                        / 1e6,
                        "background_pending_at_foreground_end": sum(
                            client.received_at[i] > foreground_end
                            for i in background_waits
                        ),
                        "background_complete_ms": [
                            (client.received_at[i] - started) / 1e6
                            for i in background_waits
                        ],
                        "response_hashes": hashes,
                    }
                )
                print(f"{label} {repetition}: {phase} {sample} の計測完了", flush=True)
        result["summary"] = {
            phase: {
                method: stats(
                    [
                        r["latency_ms"][method]
                        for r in result["rounds"]
                        if r["phase"] == phase
                    ]
                )
                for method in [
                    "textDocument/hover",
                    "textDocument/completion",
                    "diagnostics_complete",
                ]
            }
            for phase in ["quiet", "busy"]
        }
        client.drain(0.3)
        assert monitor.pss_samples, "PSS の標本を取得できませんでした"
        result["steady_pss"] = monitor.pss_samples[-1]
        finish(client.start("shutdown"))
        client.notify("exit")
        client.process.stdin.close()
        client.process.wait(timeout=10)
    except Exception as error:
        result["failure"] = repr(error)
        raise
    finally:
        result["resources"] = monitor.finish()
        result["stderr"] = client.errors
        for pid in descendants(processes(), client.process.pid) - {client.process.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()
        if "failure" in result:
            args.output.with_name(
                f"{args.output.stem}-{label}-{repetition}-failure.json"
            ).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    assert not result["resources"]["sampling_error"], result["resources"]
    result["accepted"] = (
        result["resources"]["outside_cpu_cores"] <= args.max_outside_cores
    )
    return result


if __name__ == "__main__":
    main(parallel_session)
