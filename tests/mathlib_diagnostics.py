import hashlib
import json
import os
import signal

from mathlib import FILES, Monitor, descendants, fixture, main, processes
from mathlib_startup import StartupClient


def probe(args, label, command, repetition):
    if args.index_cache_dir is None:
        raise ValueError("--index-cache-dir が必要です")
    environment = dict(os.environ, LEANSAIL_INDEX_CACHE_DIR=str(args.index_cache_dir))
    environment.pop("LEAN_WORKER_PATH", None)
    client = StartupClient(command, environment)
    monitor = Monitor(client.process.pid)
    result = {"server": label, "repetition": repetition}
    try:
        client.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": args.workspace.as_uri(),
                "capabilities": {},
            },
        )
        client.notify("initialized", {})
        path = args.workspace / ".lake/packages/mathlib" / FILES[-1]
        text, _, _, _ = fixture(path)
        uri = path.as_uri()
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
        reply = client.response(
            client.start("textDocument/waitForDiagnostics", {"uri": uri, "version": 1}),
            timeout=args.timeout,
        )
        assert "error" not in reply, reply
        result["at_wait_for_diagnostics"] = list(client.events)
        client.drain(1)
        result["after_drain"] = list(client.events)
        reply = client.response(
            client.start("$/lean/waitForILeans", {"uri": uri, "version": 1}),
            timeout=args.timeout,
        )
        assert "error" not in reply, reply
        client.drain(1)
        result["after_index"] = list(client.events)
        for key in ["at_wait_for_diagnostics", "after_drain", "after_index"]:
            for event in result[key]:
                event["sha256"] = hashlib.sha256(
                    json.dumps(
                        event["diagnostics"], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()
        client.request("shutdown")
        client.notify("exit")
        client.process.stdin.close()
        client.process.wait(timeout=10)
        assert client.process.returncode == 0, client.errors
    finally:
        result["resources"] = monitor.finish()
        result["stderr"] = client.errors
        for pid in descendants(processes(), client.process.pid) - {client.process.pid}:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()
    assert not result["resources"]["sampling_error"], result["resources"]
    result["accepted"] = (
        result["resources"]["outside_cpu_cores"] <= args.max_outside_cores
    )
    return result


if __name__ == "__main__":
    main(probe)
