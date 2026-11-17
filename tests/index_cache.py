import json
import os
import subprocess
import tempfile
from pathlib import Path

from mathlib import ROOT


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / "target") as temporary:
        directory = Path(temporary)
        source = directory / "Fixture.ilean"
        source.write_text("索引の入力")
        executable = ROOT / "target/debug/leansail"
        manifest = {
            "files": [str(source)],
            "toolchain": "検査用の版",
            "backend": str(executable),
            "source_path": str(directory),
            "search_path": ["先に探す場所", "後に探す場所"],
        }

        def inspect(commit=False):
            return json.loads(
                subprocess.run(
                    [str(executable), "index-cache", *(["--commit"] if commit else [])],
                    input=json.dumps(manifest),
                    capture_output=True,
                    text=True,
                    check=True,
                    env=dict(
                        os.environ, LEANSAIL_INDEX_CACHE_DIR=str(directory / "cache")
                    ),
                ).stdout
            )

        first = inspect()
        assert not first["valid"]
        payload = Path(first["path"])
        payload.write_bytes(b"snapshot")
        assert inspect(True)["valid"]
        assert inspect()["valid"]
        manifest["search_path"].reverse()
        assert not inspect()["valid"]
        manifest["search_path"].reverse()
        assert inspect()["valid"]
        original_stat = source.stat()
        source.write_text("同長の変更")
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert not inspect()["valid"]
        source.write_text("索引の入力")
        assert inspect()["valid"]
        metadata = payload.with_name("metadata.json")
        original = metadata.read_text()
        for key, value in [
            ("version", 2),
            ("toolchain", "別の版"),
            ("key", "別のハッシュ"),
        ]:
            data = json.loads(original)
            data[key] = value
            metadata.write_text(json.dumps(data))
            assert not inspect()["valid"], key
        metadata.write_text(original)
        payload.write_bytes(b"corrupt!")
        assert not inspect()["valid"]
        payload.unlink()
        assert not inspect()["valid"]
    print("索引キャッシュの内容、版、toolchain、破損の検査に成功")


if __name__ == "__main__":
    main()
