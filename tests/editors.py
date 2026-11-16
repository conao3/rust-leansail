import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from client import Client

ROOT = Path(__file__).resolve().parent.parent
OPERATIONS = [
    "initialize",
    "diagnostics",
    "hover",
    "definition",
    "completion",
    "goal",
    "typing_diagnostics",
    "clean_diagnostics",
    "reopen_diagnostics",
    "shutdown_exit",
]


def wire_results(path, exit_status):
    transcript = [json.loads(line) for line in path.read_text().splitlines()]
    pending = {}
    results = {}
    opened = closed = changes = 0
    shutdown = exit_seen = False
    version = None
    diagnostics = []
    snapshots = []
    for entry in transcript:
        if "send" in entry:
            message = entry["send"]
            method = message.get("method")
            if "id" in message and method:
                pending[message["id"]] = method
            if method == "textDocument/didOpen":
                opened += 1
                version = message["params"]["textDocument"]["version"]
            elif method == "textDocument/didClose":
                closed += 1
            elif method == "textDocument/didChange":
                changes += 1
                version = message["params"]["textDocument"]["version"]
            elif method == "exit":
                assert shutdown, "shutdown 応答より先に exit を送っています"
                exit_seen = True
        if "receive" in entry:
            message = entry["receive"]
            if "id" in message and "method" not in message:
                method = pending.pop(message["id"])
                if "error" in message:
                    assert method in (
                        "textDocument/semanticTokens/full",
                        "textDocument/documentColor",
                        "textDocument/inlayHint",
                    ) and message["error"]["code"] in (-32800, -32801), (
                        method,
                        message,
                    )
                    continue
                if method in (
                    "initialize",
                    "textDocument/hover",
                    "textDocument/definition",
                    "textDocument/completion",
                    "$/lean/plainGoal",
                ):
                    results.setdefault(method, []).append(message["result"])
                if method == "shutdown":
                    assert message["result"] is None
                    shutdown = True
                if method == "textDocument/waitForDiagnostics":
                    assert diagnostics and diagnostics[-1]["version"] == version
                    snapshots.append(diagnostics[-1])
            elif message.get("method") == "textDocument/publishDiagnostics":
                params = message["params"]
                if params.get("version") == version:
                    diagnostics.append(params)
    assert opened == 2 and closed >= 1, (opened, closed)
    assert changes >= 2, changes
    assert shutdown and exit_seen, transcript[-5:]
    if exit_status == 0:
        assert transcript[-1] == {"exit_code": 0}, transcript[-5:]
    else:
        assert exit_status == 9, exit_status
    assert diagnostics, "最新版の診断を受信できません"
    assert len(snapshots) == 4, snapshots
    results["diagnostics"] = snapshots
    return results, {"opens": opened, "closes": closed, "changes": changes}


def session(editor, server, output):
    name = f"{editor}-{server}"
    prefix = output / name
    fixture = output / "project/Editor.lean"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "tests/fixtures/Editor.lean", fixture)
    command = (
        [shutil.which("lean"), "--server"]
        if server == "upstream"
        else [str(ROOT / "tests/command"), "--server"]
    )
    config = {
        "file": str(fixture),
        "server": command,
        "command": [sys.executable, str(ROOT / "tests/editors/proxy.py")],
        "transcript": str(prefix.with_suffix(".ndjson")),
        "stderr": str(prefix.with_suffix(".server.log")),
        "result": str(prefix.with_suffix(".json")),
    }
    config_path = prefix.with_suffix(".config.json")
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    env = dict(os.environ, LEANSAIL_EDITOR_CONFIG=str(config_path))
    cmd = (
        ["emacs", "--batch", "-Q", "-l", str(ROOT / "tests/editors/emacs.el")]
        if editor == "emacs"
        else [
            "nvim",
            "--headless",
            "-u",
            "NONE",
            "-c",
            f"luafile {ROOT / 'tests/editors/neovim.lua'}",
        ]
    )
    with prefix.with_suffix(".editor.log").open("w") as log:
        process = subprocess.Popen(
            cmd,
            cwd=fixture.parent,
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            status = process.wait(timeout=180)
            assert status == 0, f"{name}: {prefix.with_suffix('.editor.log')}"
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
    result = json.loads(prefix.with_suffix(".json").read_text())
    wire, counts = wire_results(
        prefix.with_suffix(".ndjson"), result.get("exit_status", 0)
    )
    return {"editor": result, "wire": wire, "lifecycle": counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--editor", choices=["emacs", "neovim"])
    parser.add_argument("--output", type=Path, default=ROOT / "target/editors")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, params in [
        ("省略", {}),
        ("空オブジェクト", {"params": {}}),
        ("null", {"params": None}),
    ]:
        with Client([str(ROOT / "tests/command"), "--server"]) as client:
            client.request("initialize", {"rootUri": ROOT.as_uri(), "capabilities": {}})
            client.notify("initialized", {})
            client.request("shutdown")
            client.send({"method": "exit", **params})
            client.process.wait(timeout=10)
            assert client.process.returncode == 0, (name, client.errors)
    for editor in [args.editor] if args.editor else ["emacs", "neovim"]:
        upstream = session(editor, "upstream", output)
        leansail = session(editor, "leansail", output)
        upstream_exit = upstream["editor"].pop("exit_status", 0)
        leansail_exit = leansail["editor"].pop("exit_status", 0)
        assert leansail_exit == 0
        assert upstream_exit == (9 if editor == "emacs" else 0)
        assert upstream["editor"] == leansail["editor"], (upstream, leansail)
        assert upstream["wire"] == leansail["wire"], (upstream, leansail)
        summary[editor] = {
            "version": leansail["editor"]["version"],
            "operations": dict.fromkeys(OPERATIONS, "一致"),
            "lifecycle": leansail["lifecycle"],
            "exit_status": {"upstream": upstream_exit, "leansail": leansail_exit},
        }
        if upstream_exit != leansail_exit:
            summary[editor]["operations"]["shutdown_exit"] = (
                "上流は強制終了、Leansail は正常終了"
            )
        print(
            f"{editor}: {len(OPERATIONS)} 操作の検証に成功（終了結果は summary.json）",
            flush=True,
        )
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
