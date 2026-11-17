import hashlib
import json
import os
import signal
import time

from mathlib import (
    FILES,
    METHODS,
    ROOT,
    Monitor,
    descendants,
    fixture,
    main,
    processes,
    run,
    stats,
)
from mathlib_startup import StartupClient, snapshot


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def upstream_session(args, label, command, repetition):
    if label not in ["upstream", "leansail"]:
        raise ValueError("本家と現行 Leansail の 2 構成を指定してください")
    if not args.require_cache_hit or args.index_cache_dir is None:
        raise ValueError("--require-cache-hit と --index-cache-dir が必要です")
    prefix = args.output.parent / f"{args.output.stem}-{label}-{repetition}"
    for path in prefix.parent.glob(prefix.name + ".*.jsonl"):
        path.unlink()
    environment = dict(os.environ)
    for name in [
        "LEAN_WORKER_PATH",
        "LEAN_LS_PROFILE",
        "LEANSAIL_SYMBOL_PROFILE",
        "LEANSAIL_BENCH_SKIP_INDEX",
        "LEANSAIL_BENCH_SKIP_CACHE",
        "LEANSAIL_TRACE",
    ]:
        environment.pop(name, None)
    environment["LEANSAIL_INDEX_CACHE_DIR"] = str(args.index_cache_dir)
    if label == "leansail":
        environment["LEANSAIL_TRACE"] = str(prefix)
    result = {
        "server": label,
        "repetition": repetition,
        "command": command,
        "source_revision": run(
            ["git", "rev-parse", "HEAD"], ROOT, capture_output=True
        ).stdout.strip(),
        "source_hashes": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [*ROOT.glob("native/*.lean"), *ROOT.glob("src/*.rs")]
                + [ROOT / "tests/mathlib.py", ROOT / "tests/mathlib_upstream.py"]
            )
        },
        "files": [],
        "events": [],
        "memory": {},
    }
    responses = {}
    started = time.monotonic_ns()
    client = StartupClient(command, environment)
    monitor = Monitor(client.process.pid, pss=True)

    def finish(request_id):
        reply = client.response(request_id, timeout=args.timeout)
        if "error" in reply:
            raise RuntimeError(f"要求が失敗しました: {reply}")
        return reply.get("result")

    def request(method, params):
        start = time.monotonic_ns()
        request_id = client.start(method, params)
        body = finish(request_id)
        return body, {
            "ms": (client.received_at[request_id] - start) / 1e6,
            "sha256": digest(body),
        }

    try:
        request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": args.workspace.as_uri(),
                "capabilities": {},
            },
        )
        client.notify("initialized", {})
        documents = []
        for relative in [] if args.startup_only else FILES:
            path = args.workspace / ".lake/packages/mathlib" / relative
            text, point, goal, _ = fixture(path)
            uri = path.as_uri()
            opened = time.monotonic_ns()
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
            client.until(
                lambda uri=uri: any(
                    e["uri"] == uri and e["count"] for e in client.events
                ),
                timeout=args.timeout,
            )
            first = next(
                e["ns"] for e in client.events if e["uri"] == uri and e["count"]
            )
            if not documents:
                result["startup_first_diagnostic_ms"] = (first - started) / 1e6
            request("textDocument/waitForDiagnostics", {"uri": uri, "version": 1})
            diagnostics = [e for e in client.events if e["uri"] == uri][-1][
                "diagnostics"
            ]
            assert diagnostics and all(d.get("severity") != 1 for d in diagnostics)
            documents.append((relative, uri, point, goal))
            result["files"].append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "fixture_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "first_diagnostic_ms": (first - opened) / 1e6,
                    "open_complete_ms": (time.monotonic_ns() - opened) / 1e6,
                    "diagnostics_sha256": digest(diagnostics),
                    "requests": {},
                }
            )
            print(f"{label} {repetition}: {relative} の解析完了", flush=True)
        _, index_wait = request("$/lean/waitForILeans", {})
        result["index_ready_ms"] = (time.monotonic_ns() - started) / 1e6
        result["remaining_index_wait_ms"] = index_wait["ms"]
        for _, uri, _, _ in documents:
            request("$/lean/waitForILeans", {"uri": uri, "version": 1})
        if documents:
            result["memory"]["three_documents_parsed"] = snapshot(client.process.pid)
        for entry, (relative, uri, point, goal) in zip(
            result["files"], documents, strict=True
        ):
            responses[relative] = {}
            methods = (
                ["textDocument/documentHighlight"]
                + [
                    method
                    for method in METHODS
                    if method != "textDocument/documentHighlight"
                ]
                + ["workspace/symbol"]
            )
            for method in methods:
                if method == "workspace/symbol":
                    params = {"query": "aecover_ball"}
                else:
                    params = {"textDocument": {"uri": uri}}
                    if not method.endswith("/full"):
                        params["position"] = (
                            goal if method == "$/lean/plainGoal" else point
                        )
                    if method == "textDocument/references":
                        params["context"] = {"includeDeclaration": True}
                body, first = request(method, params)
                assert body, (relative, method, body)
                if method == "$/lean/plainGoal":
                    assert body["goals"], body
                if method == "textDocument/hover":
                    assert "twice" in body["contents"]["value"], body
                responses[relative][method] = body
                samples = []
                for _ in range(args.samples):
                    repeated, sample = request(method, params)
                    assert repeated == body, (relative, method, sample)
                    samples.append(sample)
                entry["requests"][method] = {
                    "first": first,
                    "repeated": samples,
                    **stats([s["ms"] for s in samples]),
                }
                if method == "textDocument/completion":
                    resolved = [
                        request("completionItem/resolve", item)[0]
                        for item in body["items"]
                        if item["label"] == "twice"
                    ]
                    assert resolved, relative
                    entry["completion_resolved_sha256"] = digest(resolved)
                    responses[relative]["completionItem/resolve"] = resolved
            print(f"{label} {repetition}: {relative} の要求計測完了", flush=True)
        if documents:
            result["memory"]["three_documents_warm"] = snapshot(client.process.pid)
        request("shutdown", None)
        client.notify("exit")
        client.process.stdin.close()
        client.process.wait(timeout=10)
        assert client.process.returncode == 0, client.errors
    except Exception as error:
        result["failure"] = repr(error)
        raise
    finally:
        result["resources"] = monitor.finish()
        result["stderr"] = client.errors
        result["events"] = [
            json.loads(line)
            for path in prefix.parent.glob(prefix.name + ".*.jsonl")
            for line in path.read_text().splitlines()
        ]
        for event in result["events"]:
            event["ms"] = (event["ns"] - started) / 1e6
        for pid in descendants(processes(), client.process.pid) - {client.process.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()
        prefix.with_suffix(".session.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        prefix.with_suffix(".responses.json").write_text(
            json.dumps(responses, ensure_ascii=False) + "\n"
        )
    assert not result["resources"]["sampling_error"], result["resources"]
    result["index_source"] = next(
        (e["detail"] for e in result["events"] if e["event"] == "index_done"), None
    )
    if label == "leansail" and result["index_source"] != "cache":
        raise RuntimeError("Leansail の永続索引の cache hit を確認してください")
    result["accepted"] = (
        result["resources"]["outside_cpu_cores"] <= args.max_outside_cores
    )
    return result


if __name__ == "__main__":
    main(upstream_session)
