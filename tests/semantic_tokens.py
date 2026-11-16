import json
import os
import shutil
from pathlib import Path

from client import Client
from conformance import finish, initialize

ROOT = Path(__file__).resolve().parents[1]
URI = (ROOT / "SemanticCache.lean").as_uri()
DOC = {"textDocument": {"uri": URI}}
TEXT = "def «😀» (α : Nat) : Nat := α + α\n#check «😀»\n"
EXTRA = "def semanticExtra : Nat := 7\n#check semanticExtra\n"


def tokens(client):
    return client.request("textDocument/semanticTokens/full", DOC)


def wait(client, version):
    client.request("textDocument/waitForDiagnostics", {"uri": URI, "version": version})


def open_document(client, text):
    client.notify(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": URI,
                "languageId": "lean4",
                "version": 1,
                "text": text,
            }
        },
    )


def change(client, text, version):
    client.notify(
        "textDocument/didChange",
        {
            "textDocument": {"uri": URI, "version": version},
            "contentChanges": [{"text": text}],
        },
    )


def check(command, environment):
    with Client(command, env=environment) as client:
        initialize(client)
        open_document(client, TEXT)
        wait(client, 1)
        original = tokens(client)
        assert original["data"]
        assert tokens(client) == original
        change(client, TEXT + EXTRA, 2)
        wait(client, 2)
        edited = tokens(client)
        assert len(edited["data"]) > len(original["data"])
        assert tokens(client) == edited
        change(client, TEXT, 3)
        wait(client, 3)
        assert tokens(client) == original
        client.notify("textDocument/didClose", DOC)
        open_document(client, TEXT + EXTRA)
        wait(client, 1)
        assert tokens(client) == edited
        slow = "import Lean\n#eval IO.sleep 4000\n" + TEXT + EXTRA
        change(client, slow, 2)
        partial = tokens(client)
        wait(client, 2)
        complete = tokens(client)
        assert len(complete["data"]) > len(partial["data"]), (partial, complete)
        assert tokens(client) == complete
        change(client, TEXT, 3)
        wait(client, 3)
        assert tokens(client) == original
        finish(client)
        return {
            "original": original,
            "edited": edited,
            "partial": partial,
            "complete": complete,
        }


def main():
    binary = (
        Path(os.environ["LEANSAIL_TEST_BINARY"])
        if "LEANSAIL_TEST_BINARY" in os.environ
        else ROOT / "target/debug/leansail"
    )
    backend = ROOT / "native/.lake/build/bin/leansail-backend"
    environment = dict(os.environ)
    environment.pop("LEAN_WORKER_PATH", None)
    upstream = check([shutil.which("lean"), "--server"], environment)
    command = [str(binary), "server", "run-server"]
    if "LEANSAIL_TEST_BINARY" not in os.environ:
        command.extend(["--backend", str(backend)])
    leansail = check(command, environment)
    for key in ["original", "edited", "complete"]:
        assert upstream[key] == leansail[key], key
    output = ROOT / "target/semantic-tokens.json"
    output.write_text(
        json.dumps(
            {"upstream": upstream, "leansail": leansail}, ensure_ascii=False, indent=2
        )
        + "\n"
    )
    print(
        "semantic tokens の再利用・編集・再オープン・部分応答・UTF-16 の比較に成功しました"
    )


if __name__ == "__main__":
    main()
