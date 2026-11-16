import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from client import Client
from conformance import (
    DOCUMENT,
    ROOT,
    TEXT,
    URI,
    finish,
    initialize,
    opened,
    wait_document,
)


def processes(parent):
    rows = subprocess.check_output(["ps", "-eo", "pid=,ppid=,args="], text=True)
    descendants = {parent}
    result = {}
    rows = [row.split(None, 2) for row in rows.splitlines()]
    while True:
        found = {
            int(pid): args
            for pid, ppid, args in rows
            if int(ppid) in descendants and int(pid) not in descendants
        }
        if not found:
            return result
        descendants.update(found)
        result.update(found)


def worker(client):
    matches = [
        pid for pid, args in processes(client.process.pid).items() if "--worker" in args
    ]
    assert len(matches) == 1, matches
    return matches[0]


def fatal(client):
    return any(
        message["method"] == "$/lean/fileProgress"
        and message["params"]["textDocument"]["uri"] == URI
        and any(info["kind"] == 2 for info in message["params"]["processing"])
        for message in client.notifications
    )


def slow_request(client):
    session = client.request("$/lean/rpc/connect", {"uri": URI})
    request = client.start(
        "$/lean/rpc/call",
        {
            **DOCUMENT,
            **session,
            "position": {"line": len(TEXT.splitlines()) - 1, "character": 0},
            "method": "conformanceSlow",
            "params": {},
        },
    )
    client.drain(0.1)
    assert request not in client.responses, client.responses[request]
    return request


def worker_recovery(client, pending):
    initialize(client)
    opened(client)
    wait_document(client)
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": 2},
            "contentChanges": [
                {"text": TEXT + "\ndef restored : Bool := true\n#check restored\n"}
            ],
        },
    )
    wait_document(client, 2)
    line = len(TEXT.splitlines()) + 1
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": 3},
            "contentChanges": [
                {
                    "range": {
                        "start": {"line": line, "character": 15},
                        "end": {"line": line, "character": 19},
                    },
                    "text": "Nat",
                },
                {
                    "range": {
                        "start": {"line": line, "character": 22},
                        "end": {"line": line, "character": 26},
                    },
                    "text": "42",
                },
            ],
        },
    )
    wait_document(client, 3)
    query = {**DOCUMENT, "position": {"line": line + 1, "character": 9}}
    before = client.request("textDocument/hover", query)
    assert "Nat" in before["contents"]["value"], before
    request = slow_request(client) if pending else None
    old_worker = worker(client)
    os.kill(old_worker, signal.SIGKILL)
    client.until(lambda: fatal(client), timeout=5)
    if pending:
        response = client.response(request, timeout=5)
        assert response.get("error", {}).get("code") == -32902, response
    started = time.monotonic()
    after = client.request("textDocument/hover", query)
    assert after == before, (before, after)
    new_worker = worker(client)
    assert new_worker != old_worker
    wait_document(client, 3)
    client.notify("textDocument/didClose", DOCUMENT)
    finish(client)
    return {
        "fatal_progress": True,
        "pending_error": -32902 if pending else None,
        "same_version_and_content": True,
        "new_worker": True,
        "recovery_ms": (time.monotonic() - started) * 1000,
    }


def watchdog_failure(client, pending):
    initialize(client)
    opened(client)
    wait_document(client)
    request = slow_request(client) if pending else None
    matches = [
        pid
        for pid, args in processes(client.process.pid).items()
        if "--server" in args and "--worker" not in args
    ]
    assert len(matches) == 1, matches
    os.kill(matches[0], signal.SIGKILL)
    client.until(
        lambda: any(m["method"] == "window/showMessage" for m in client.notifications),
        timeout=5,
    )
    notice = next(
        m for m in client.notifications if m["method"] == "window/showMessage"
    )
    assert notice["params"]["type"] == 1, notice
    if pending:
        response = client.response(request, timeout=5)
        assert response.get("error", {}).get("code") == -32603, response
    client.process.wait(timeout=5)
    assert client.process.returncode == 255, client.process.returncode
    return {
        "error_notification": notice,
        "pending_error": -32603 if pending else None,
        "exit_code": client.process.returncode,
    }


def upstream_worker(client):
    initialize(client)
    text = "def recovered : Nat := 42\n#check recovered\n"
    opened(client, text)
    wait_document(client)
    query = {**DOCUMENT, "position": {"line": 1, "character": 9}}
    expected = client.request("textDocument/hover", query)
    os.kill(worker(client), signal.SIGKILL)
    client.until(lambda: fatal(client), timeout=5)
    request = client.start("textDocument/hover", query)
    client.request("$/lean/waitForILeans", {})
    client.drain(0.3)
    assert request not in client.responses, client.responses.get(request)
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": 2},
            "contentChanges": [{"text": text}],
        },
    )
    response = client.response(request, timeout=5)
    assert response.get("result") == expected, response
    wait_document(client, 2)
    client.notify("textDocument/didClose", DOCUMENT)
    finish(client)
    return {"request_pending_after_barrier_ms": 300, "recovered_after_didChange": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "target/recovery.json")
    parser.add_argument("--upstream-only", action="store_true")
    args = parser.parse_args()
    report = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Client([shutil.which("lean"), "--server"]) as client:
            try:
                report["upstream_worker"] = upstream_worker(client)
            finally:
                (args.output.parent / "upstream-worker.json").write_text(
                    json.dumps(client.transcript, ensure_ascii=False, indent=2) + "\n"
                )
        if args.upstream_only:
            return
        for name, test in [("worker", worker_recovery), ("watchdog", watchdog_failure)]:
            for pending in [False, True]:
                label = name + ("_pending" if pending else "_idle")
                with Client([str(ROOT / "tests/command"), "--server"]) as client:
                    try:
                        report[label] = test(client, pending)
                    finally:
                        (args.output.parent / f"{label}.json").write_text(
                            json.dumps(client.transcript, ensure_ascii=False, indent=2)
                            + "\n"
                        )
    finally:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("FileWorker と Watchdog の障害検査: 待機中・要求中の 4 ケースに成功しました")


if __name__ == "__main__":
    main()
