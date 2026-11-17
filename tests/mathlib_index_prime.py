import json
import os
import signal
import time

from client import Client
from mathlib import Monitor, descendants, main, processes
from mathlib_trace import build_backend


def prime_session(args, label, command, repetition):
    if label != "leansail" or args.index_cache_dir is None:
        raise ValueError("--server leansail と --index-cache-dir が必要です")
    prefix = args.output.parent / f"{args.output.stem}-{repetition}"
    for path in prefix.parent.glob(prefix.name + ".*.jsonl"):
        path.unlink()
    env = dict(
        os.environ,
        LEANSAIL_TRACE=str(prefix),
        LEANSAIL_INDEX_CACHE_DIR=str(args.index_cache_dir),
    )
    env.pop("LEAN_WORKER_PATH", None)
    started = time.monotonic_ns()
    client = Client(command, env)
    monitor = Monitor(client.process.pid, pss=True)
    result = {"server": label, "repetition": repetition, "command": command}
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
        reply = client.response(
            client.start("$/lean/waitForILeans", {}), timeout=args.timeout
        )
        assert "error" not in reply, reply
        result["index_ready_ms"] = (time.monotonic_ns() - started) / 1e6
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            events = [
                json.loads(line)
                for path in prefix.parent.glob(prefix.name + ".*.jsonl")
                for line in path.read_text().splitlines()
            ]
            if any(
                e["event"] == "index_saved"
                or (e["event"] == "index_done" and e["detail"] == "cache")
                for e in events
            ):
                result["events"] = events
                break
            client.drain(0.2)
        else:
            raise TimeoutError("索引の保存完了を待つ時間が上限を超えました")
        result["cache_ready_ms"] = (time.monotonic_ns() - started) / 1e6
        client.request("shutdown")
        client.notify("exit")
        client.process.stdin.close()
        client.process.wait(timeout=10)
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
    main(prime_session, backend_builder=build_backend)
