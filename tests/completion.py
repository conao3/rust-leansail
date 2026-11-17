import json
import os
import shutil
from pathlib import Path

from client import Client
from conformance import finish, initialize
from mathlib import ROOT

HEADER = """import Lean
namespace CompletionProbe
private def probePrivate : Nat := 1
@[deprecated probePrivate (since := "2026-01-01")] def probeOld : Nat := 2
namespace Inner
def probeInner : Nat := 3
end Inner
export Inner (probeInner)
def «αprobe» : Nat := 4
"""
PARTS = [
    ("#check probePrivate\n", "probePr"),
    ("#check probeOld\n", "probeO"),
    ("#check probeInner\n", "probeI"),
    ("#check αprobe\n", "αp"),
    ("#check Nat.succ\n", "Nat.su"),
    ("#check List.map\n", "List.ma"),
    ("#check Lean.Name.str\n", "Lean.Name."),
    ("end CompletionProbe\nopen CompletionProbe\n#check probeInner\n", "probeI"),
    ("open Nat hiding succ\n#check succ\n", "suc"),
    ("open Nat (succ)\n#check succ\n", "suc"),
    ("namespace CompletionProbe.Inner\n#check probeInner\n", "probeI"),
    (
        "end CompletionProbe.Inner\n#check CompletionProbe.Inner.probeInner\n",
        "CompletionProbe.Inner.",
    ),
    ("def withLocal (localProbe : Nat) := localProbe\n", "localPr"),
    ("#check Nat.succ\n", "Nat.su"),
]


def check(command, environment):
    uri = (ROOT / "CompletionProbe.lean").as_uri()
    with Client(command, environment) as client:
        initialize(client)
        text = HEADER + "".join(part for part, _ in PARTS)

        def opened(content):
            client.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "lean4",
                        "version": 1,
                        "text": content,
                    }
                },
            )

        def wait(version):
            client.response(
                client.start(
                    "textDocument/waitForDiagnostics", {"uri": uri, "version": version}
                ),
                timeout=120,
            )

        def query(content):
            result = []
            offset = len(content) - sum(len(part) for part, _ in PARTS)
            for part, prefix in PARTS:
                point = offset + part.rindex(prefix) + len(prefix)
                before = content[:point]
                position = {
                    "line": before.count("\n"),
                    "character": len(before.rsplit("\n", 1)[-1].encode("utf-16-le"))
                    // 2,
                }
                params = {"textDocument": {"uri": uri}, "position": position}
                initial = client.request("textDocument/completion", params)
                assert initial["items"], (prefix, initial)
                assert client.request("textDocument/completion", params) == initial
                resolved = [
                    client.request("completionItem/resolve", item)
                    for item in initial["items"]
                ]
                result.append(
                    {"prefix": prefix, "initial": initial, "resolved": resolved}
                )
                offset += len(part)
            return result

        opened(text)
        wait(1)
        original = query(text)
        edited = text.replace("probePrivate : Nat", "probePrivate : String").replace(
            "probePrivate : String := 1", 'probePrivate : String := "値"'
        )
        client.notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": edited}],
            },
        )
        wait(2)
        changed = query(edited)
        assert original != changed
        client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        opened(edited)
        wait(1)
        reopened = query(edited)
        assert changed == reopened
        finish(client)
        return {"original": original, "edited": changed, "reopened": reopened}


def main():
    environment = dict(os.environ)
    environment.pop("LEAN_WORKER_PATH", None)
    binary = Path(
        os.environ.get("LEANSAIL_TEST_BINARY", ROOT / "target/debug/leansail")
    )
    candidate = [str(binary), "server", "run-server"]
    if "LEANSAIL_TEST_BINARY" not in os.environ:
        candidate += [
            "--backend",
            str(ROOT / "native/.lake/build/bin/leansail-backend"),
        ]
    upstream = check([shutil.which("lean"), "--server"], environment)
    leansail = check(candidate, environment)
    assert upstream == leansail
    (ROOT / "target/completion.json").write_text(
        json.dumps(
            {"upstream": upstream, "leansail": leansail}, ensure_ascii=False, indent=2
        )
        + "\n"
    )
    print(
        "補完の全候補・順序・resolve・名前空間・open・別名・private・Unicode・編集・再オープンの一致を確認しました"
    )


if __name__ == "__main__":
    main()
