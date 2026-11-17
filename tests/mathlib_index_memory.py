import json
import os
import re
import signal
import time
from pathlib import Path

from client import Client
from mathlib import Monitor, descendants, main, processes
from mathlib_startup import snapshot
from mathlib_trace import build_backend


def mapped_snapshot(pid):
    result = snapshot(pid)
    for process in result:
        mapped = {}
        selected = False
        for line in Path(f"/proc/{process['pid']}/smaps").read_text().splitlines():
            if re.match(r"^[0-9a-f]+-[0-9a-f]+ ", line):
                selected = "/snapshot.bin" in line
            elif selected and ":" in line:
                key, value = line.split(":", 1)
                if key in {
                    "Rss",
                    "Pss",
                    "Private_Clean",
                    "Private_Dirty",
                    "Shared_Clean",
                    "Shared_Dirty",
                }:
                    mapped[key] = mapped.get(key, 0) + int(value.split()[0])
        process["index_mapping_kib"] = mapped
    assert sum(p["index_mapping_kib"].get("Pss", 0) for p in result) > 0
    return result


def memory_session(args, label, command, repetition):
    if label != "leansail":
        raise ValueError("共有ページの検査は leansail を指定してください")
    if args.index_cache_dir is None:
        raise ValueError("--index-cache-dir に準備済みの索引を指定してください")
    prefix = args.output.parent / f"{args.output.stem}-{label}-{repetition}"
    for path in prefix.parent.glob(prefix.name + ".*.jsonl"):
        path.unlink()
    env = dict(os.environ, LEANSAIL_TRACE=str(prefix))
    env.pop("LEAN_WORKER_PATH", None)
    env["LEANSAIL_INDEX_CACHE_DIR"] = str(args.index_cache_dir)
    clients = []
    monitor = None
    result = {
        "server": label,
        "repetition": repetition,
        "command": command,
        "snapshots": [],
    }
    try:
        for index in range(2):
            start = time.monotonic_ns()
            client = Client(command, env)
            clients.append(client)
            if monitor is None:
                monitor = Monitor(client.process.pid, pss=True)
            else:
                monitor.roots += (client.process.pid,)
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
            symbols = client.request("workspace/symbol", {"query": "aecover_ball"})
            assert len(symbols) == 2, symbols
            result["snapshots"].append(
                {
                    "sessions": index + 1,
                    "start_to_query_ms": (time.monotonic_ns() - start) / 1e6,
                    "processes": [
                        p for c in clients for p in mapped_snapshot(c.process.pid)
                    ],
                }
            )
        for client in reversed(clients):
            client.request("shutdown")
            client.notify("exit")
            client.process.stdin.close()
            client.process.wait(timeout=10)
    finally:
        if monitor:
            result["resources"] = monitor.finish()
        result["events"] = [
            json.loads(line)
            for path in prefix.parent.glob(prefix.name + ".*.jsonl")
            for line in path.read_text().splitlines()
        ]
        for client in clients:
            for pid in descendants(processes(), client.process.pid) - {
                client.process.pid
            }:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            client.close()
        prefix.with_suffix(".session.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    assert (
        len(
            [
                e
                for e in result["events"]
                if e["event"] == "index_done" and e["detail"] == "cache"
            ]
        )
        == 2
    )
    assert not result["resources"]["sampling_error"], result["resources"]
    result["accepted"] = (
        result["resources"]["outside_cpu_cores"] <= args.max_outside_cores
    )
    return result


if __name__ == "__main__":
    main(memory_session, backend_builder=build_backend)
