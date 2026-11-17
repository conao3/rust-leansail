import hashlib
import json
import os
import signal
import time
from pathlib import Path

from client import Client
from mathlib import FILES, Monitor, descendants, fixture, main, processes, stats
from mathlib_trace import build_backend


class StartupClient(Client):
    def __init__(self, command, env):
        self.events = []
        self.received_at = {}
        super().__init__(command, env)

    def receive(self, timeout):
        message = super().receive(timeout)
        if "method" not in message:
            self.received_at[message["id"]] = time.monotonic_ns()
        if message.get("method") == "textDocument/publishDiagnostics":
            self.events.append(
                {
                    "event": "client_diagnostic",
                    "ns": time.monotonic_ns(),
                    "uri": message["params"]["uri"],
                    "count": len(message["params"]["diagnostics"]),
                    "diagnostics": message["params"]["diagnostics"],
                }
            )
        return message


def snapshot(root):
    table = processes()
    result = []
    for pid in descendants(table, root) & table.keys():
        try:
            fields = dict(
                line.split(":", 1)
                for line in Path(f"/proc/{pid}/smaps_rollup")
                .read_text()
                .splitlines()[1:]
            )
            result.append(
                {
                    "pid": pid,
                    "pss_kib": int(fields["Pss"].split()[0]),
                    "private_kib": sum(
                        int(fields[key].split()[0])
                        for key in ["Private_Clean", "Private_Dirty"]
                    ),
                    "private_clean_kib": int(fields["Private_Clean"].split()[0]),
                    "private_dirty_kib": int(fields["Private_Dirty"].split()[0]),
                    "shared_clean_kib": int(fields["Shared_Clean"].split()[0]),
                    "shared_dirty_kib": int(fields["Shared_Dirty"].split()[0]),
                    "cpu_ticks": table[pid]["ticks"],
                    "command": Path(f"/proc/{pid}/cmdline")
                    .read_bytes()
                    .decode()
                    .split("\0")[:-1],
                }
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


def startup_session(args, label, command, repetition):
    prefix = args.output.parent / f"{args.output.stem}-{label}-{repetition}"
    for path in prefix.parent.glob(prefix.name + ".*.jsonl"):
        path.unlink()
    env = dict(os.environ, LEANSAIL_TRACE=str(prefix))
    env.pop("LEAN_WORKER_PATH", None)
    env.pop("LEANSAIL_BENCH_SKIP_INDEX", None)
    env.pop("LEANSAIL_BENCH_SKIP_CACHE", None)
    env["LEANSAIL_INDEX_CACHE_DIR"] = str(
        args.index_cache_dir.resolve()
        if args.index_cache_dir
        else args.output.parent / (args.output.stem + "-cache")
    )
    if label == "no-index":
        env["LEANSAIL_BENCH_SKIP_INDEX"] = "1"
    if label == "no-cache":
        env["LEANSAIL_BENCH_SKIP_CACHE"] = "1"
    if label in ["native", "baseline"]:
        env["LEAN_WORKER_PATH"] = str(args.backend)
    started = time.monotonic_ns()
    client = StartupClient(command, env)
    monitor = Monitor(client.process.pid, pss=True)
    result = {
        "server": label,
        "repetition": repetition,
        "command": command,
        "files": [],
        "events": [{"event": "process_spawn", "ns": started}],
        "memory": {},
    }

    def finish(request_id):
        reply = client.response(request_id, timeout=args.timeout)
        if "error" in reply:
            raise RuntimeError(f"要求が失敗しました: {reply}")
        return reply.get("result")

    def request(method, params):
        start = time.monotonic_ns()
        response = finish(client.start(method, params))
        elapsed = (time.monotonic_ns() - start) / 1e6
        return {
            "ms": elapsed,
            "count": len(response) if isinstance(response, list) else None,
            "sha256": hashlib.sha256(
                json.dumps(response, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
        }

    def batch(uri, point):
        methods = {
            "workspace/symbol": {"query": "aecover_ball"},
            "textDocument/references": {
                "textDocument": {"uri": uri},
                "position": point,
                "context": {"includeDeclaration": True},
            },
            "textDocument/definition": {
                "textDocument": {"uri": uri},
                "position": point,
            },
            "textDocument/documentHighlight": {
                "textDocument": {"uri": uri},
                "position": point,
            },
        }
        pending = {
            method: (time.monotonic_ns(), client.start(method, params))
            for method, params in methods.items()
        }
        values = {}
        for method, (start, request_id) in pending.items():
            response = finish(request_id)
            values[method] = {
                "ms": (client.received_at[request_id] - start) / 1e6,
                "count": len(response) if isinstance(response, list) else None,
                "sha256": hashlib.sha256(
                    json.dumps(response, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
            }
        return methods, values

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
        result["events"].append(
            {"event": "client_initialized", "ns": time.monotonic_ns()}
        )
        result["memory"]["initialized"] = snapshot(client.process.pid)
        client.notify("initialized", {})
        for index, relative in enumerate(FILES):
            path = args.workspace / ".lake/packages/mathlib" / relative
            text, point, _, _ = fixture(path)
            uri = path.as_uri()
            opened = time.monotonic_ns()
            result["events"].append(
                {"event": "client_did_open", "ns": opened, "uri": uri}
            )
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
            methods, cold = ({}, {}) if args.startup_only else batch(uri, point)
            client.until(
                lambda uri=uri: any(
                    e["uri"] == uri and e["count"] for e in client.events
                ),
                timeout=args.timeout,
            )
            first = next(
                e["ns"] for e in client.events if e["uri"] == uri and e["count"]
            )
            if index == 0:
                result["startup_first_diagnostic_ms"] = (first - started) / 1e6
            finish(
                client.start(
                    "textDocument/waitForDiagnostics", {"uri": uri, "version": 1}
                )
            )
            if args.startup_only:
                result["memory"]["document_0_complete"] = snapshot(client.process.pid)
                break
            wait_start = time.monotonic_ns()
            finish(client.start("$/lean/waitForILeans", {"uri": uri, "version": 1}))
            result["events"].append(
                {
                    "event": "index_and_document_ready",
                    "ns": time.monotonic_ns(),
                    "uri": uri,
                }
            )
            entry = {
                "path": relative,
                "cold": cold,
                "open_complete_ms": (time.monotonic_ns() - opened) / 1e6,
                "remaining_index_wait_ms": (time.monotonic_ns() - wait_start) / 1e6,
                "warm": {},
            }
            for method, params in methods.items():
                samples = [request(method, params) for _ in range(args.samples)]
                assert len({s["sha256"] for s in samples}) == 1, samples
                entry["warm"][method] = {
                    **stats([s["ms"] for s in samples]),
                    "count": samples[0]["count"],
                    "sha256": samples[0]["sha256"],
                }
            result["memory"][f"document_{index}_warm"] = snapshot(client.process.pid)
            result["files"].append(entry)
            print(f"{label} {repetition}: {relative} の索引計測完了", flush=True)
        finish(client.start("shutdown"))
        client.notify("exit")
        client.process.stdin.close()
        client.process.wait(timeout=10)
    finally:
        result["resources"] = monitor.finish()
        result["events"] += client.events
        for path in prefix.parent.glob(prefix.name + ".*.jsonl"):
            result["events"] += [
                json.loads(line) for line in path.read_text().splitlines()
            ]
        result["events"].sort(key=lambda e: e["ns"])
        for event in result["events"]:
            event["ms"] = (event["ns"] - started) / 1e6
        result["stderr"] = client.errors
        for pid in descendants(processes(), client.process.pid) - {client.process.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()
        prefix.with_suffix(".session.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    assert not result["resources"]["sampling_error"], result["resources"]
    result["index_source"] = next(
        (e.get("detail") for e in result["events"] if e["event"] == "index_done"),
        None,
    )
    if (
        args.require_cache_hit
        and label == "leansail"
        and result["index_source"] != "cache"
    ):
        raise RuntimeError(
            "永続索引の cache hit が必要です。索引の保存完了を確認してください"
        )
    result["accepted"] = (
        result["resources"]["outside_cpu_cores"] <= args.max_outside_cores
    )
    return result


if __name__ == "__main__":
    main(startup_session, backend_builder=build_backend)
