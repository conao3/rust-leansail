#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path


def read(stream):
    headers = {}
    while (line := stream.readline()) != b"\r\n":
        if not line:
            raise EOFError("LSP ストリームが終了しました")
        name, value = line.decode().split(":", 1)
        headers[name.lower()] = value.strip()
    body = stream.read(int(headers["content-length"]))
    return json.loads(body)


def write(stream, message):
    body = json.dumps(dict(jsonrpc="2.0", **message)).encode()
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    stream.flush()


def backend():
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    initialize = read(source)
    write(sink, {"id": initialize["id"], "result": {"capabilities": {}}})
    assert read(source)["method"] == "initialized"
    requests = [
        {"id": 1, "method": "workspace/semanticTokens/refresh"},
        {"id": "inlay", "method": "workspace/inlayHint/refresh"},
        {
            "id": "watcher",
            "method": "client/registerCapability",
            "params": {
                "registrations": [
                    {
                        "id": "watcher",
                        "method": "workspace/didChangeWatchedFiles",
                        "registerOptions": {"watchers": []},
                    }
                ]
            },
        },
    ]
    for request in requests:
        write(sink, request)
        response = read(source)
        assert response["id"] == request["id"], response
        if request["id"] == "watcher" and os.environ["CAPABILITY_CASE"] != "supported":
            assert response["error"]["code"] == -32601, response
        else:
            assert "result" in response and response["result"] is None, response
    write(sink, {"method": "$/lean/capabilityTestComplete", "params": {}})
    shutdown = read(source)
    assert shutdown["method"] == "shutdown", shutdown
    write(sink, {"id": shutdown["id"], "result": None})
    assert read(source)["method"] == "exit"


def session(case, capabilities, forwarded):
    process = subprocess.Popen(
        [
            "target/debug/leansail",
            "server",
            "run-server",
            "--backend",
            str(Path(__file__).resolve()),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ, CAPABILITY_CASE=case),
    )
    try:
        write(
            process.stdin,
            {
                "id": 1,
                "method": "initialize",
                "params": {"capabilities": capabilities},
            },
        )
        assert read(process.stdout)["id"] == 1
        write(process.stdin, {"method": "initialized", "params": {}})
        seen = []
        while True:
            message = read(process.stdout)
            if message["method"] == "$/lean/capabilityTestComplete":
                break
            assert message["method"] in forwarded, message
            seen.append(message["method"])
            write(process.stdin, {"id": message["id"], "result": None})
        assert seen == forwarded, (case, seen)
        write(process.stdin, {"id": "shutdown", "method": "shutdown"})
        assert read(process.stdout) == {
            "jsonrpc": "2.0",
            "id": "shutdown",
            "result": None,
        }
        write(process.stdin, {"method": "exit"})
        _, errors = process.communicate(timeout=10)
        assert process.returncode == 0, errors.decode()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main():
    if sys.argv[1:] == ["--server"]:
        backend()
        return
    for case, value, forwarded in [
        ("missing", None, []),
        ("false", False, []),
        ("invalid", "true", []),
        ("partial", True, ["workspace/semanticTokens/refresh"]),
        (
            "supported",
            True,
            [
                "workspace/semanticTokens/refresh",
                "workspace/inlayHint/refresh",
                "client/registerCapability",
            ],
        ),
    ]:
        capabilities = {}
        if case != "missing":
            capabilities = {
                "workspace": {
                    "semanticTokens": {"refreshSupport": value},
                    "inlayHint": {"refreshSupport": value},
                    "didChangeWatchedFiles": {"dynamicRegistration": value},
                }
            }
        if case == "partial":
            capabilities["workspace"]["inlayHint"]["refreshSupport"] = False
            capabilities["workspace"]["didChangeWatchedFiles"][
                "dynamicRegistration"
            ] = False
        session(case, capabilities, forwarded)
    print("capability 交渉と backend への代理応答: 5 ケース成功")


if __name__ == "__main__":
    main()
