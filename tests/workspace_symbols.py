import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from client import Client
from conformance import finish, initialize
from mathlib import ROOT

QUERIES = [
    "",
    "leansailProbe",
    "LEANSAILPROBE",
    "lP",
    "😀",
    "α",
    "hiddenProbe",
    "leansailProbeUnsaved",
    "leansailProbeEdited",
    "zzzzMissingProbe",
]


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / "target") as temporary:
        directory = Path(temporary)
        module = directory / "SymbolFixture.lean"
        ilean = module.with_suffix(".ilean")
        text = "\n".join(f"def leansailProbe{i:04} : Nat := {i}" for i in range(1100))
        text += "\nprivate def hiddenProbe : Nat := 7\ndef «😀» : Nat := 1\ndef α : Nat := 2\n"
        module.write_text(text)

        def build():
            subprocess.run(
                ["lean", "-R", str(directory), "-i", str(ilean), str(module)],
                check=True,
                capture_output=True,
            )

        build()
        environment = dict(os.environ)
        environment.pop("LEAN_WORKER_PATH", None)
        environment["LEAN_PATH"] = str(directory)
        environment["LEAN_SRC_PATH"] = str(directory)
        environment["LEANSAIL_INDEX_CACHE_DIR"] = str(directory / "cache")
        environment["LEANSAIL_TRACE"] = str(directory / "trace")
        binary = Path(
            os.environ.get("LEANSAIL_TEST_BINARY", ROOT / "target/debug/leansail")
        )
        candidate = [str(binary), "server", "run-server"]
        if "LEANSAIL_TEST_BINARY" not in os.environ:
            candidate += [
                "--backend",
                str(ROOT / "native/.lake/build/bin/leansail-backend"),
            ]

        def check(command, save=False):
            with Client(command, environment) as client:
                initialize(client)
                client.response(client.start("$/lean/waitForILeans", {}), timeout=120)

                def query():
                    return {
                        q: client.request("workspace/symbol", {"query": q})
                        for q in QUERIES
                    }

                original = query()
                assert len(original["leansailProbe"]) == 1000
                assert original == query()
                if save:
                    deadline = time.monotonic() + 120
                    while not any(
                        '"event":"index_saved"' in p.read_text()
                        for p in directory.glob("trace.*.jsonl")
                    ):
                        if time.monotonic() > deadline:
                            raise TimeoutError(
                                "検索名を含む永続索引の保存が完了しません"
                            )
                        client.drain(0.1)
                uri = (directory / "Unsaved.lean").as_uri()
                client.notify(
                    "textDocument/didOpen",
                    {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "lean4",
                            "version": 1,
                            "text": "def leansailProbeUnsaved : Nat := 1\n",
                        }
                    },
                )
                client.response(
                    client.start("$/lean/waitForILeans", {"uri": uri, "version": 1}),
                    timeout=120,
                )
                opened = query()
                client.notify(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": 2},
                        "contentChanges": [
                            {"text": "def leansailProbeEdited : Nat := 2\n"}
                        ],
                    },
                )
                client.response(
                    client.start("$/lean/waitForILeans", {"uri": uri, "version": 2}),
                    timeout=120,
                )
                edited = query()
                assert opened != edited
                client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
                closed = query()
                assert closed == original
                module.write_text("def leansailProbeDiskChanged : Nat := 3\n")
                build()
                client.notify(
                    "workspace/didChangeWatchedFiles",
                    {"changes": [{"uri": ilean.as_uri(), "type": 2}]},
                )
                changed = query()
                assert len(changed["leansailProbe"]) == 1
                client.notify(
                    "workspace/didChangeWatchedFiles",
                    {"changes": [{"uri": ilean.as_uri(), "type": 3}]},
                )
                deleted = query()
                assert deleted["leansailProbe"] == []
                finish(client)
                return {
                    "original": original,
                    "opened": opened,
                    "edited": edited,
                    "closed": closed,
                    "changed": changed,
                    "deleted": deleted,
                }

        upstream = check([shutil.which("lean"), "--server"])
        module.write_text(text)
        build()
        leansail = check(candidate, save=True)
        assert upstream == leansail
        module.write_text(text)
        build()
        cached = check(candidate)
        assert cached == upstream
        assert any(
            '"detail":"cache"' in p.read_text() for p in directory.glob("trace.*.jsonl")
        )
        output = ROOT / "target/workspace-symbols.json"
        output.write_text(
            json.dumps(
                {
                    "queries": QUERIES,
                    "phases": list(upstream),
                    "equal": True,
                    "cache_hit": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    print(
        "workspace/symbol の順序・上限・Unicode・private 名・編集・ファイル変更・永続索引の上流一致を確認"
    )


if __name__ == "__main__":
    main()
