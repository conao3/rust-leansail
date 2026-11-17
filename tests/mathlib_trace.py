import argparse
import hashlib
import json
import os
from pathlib import Path

from mathlib import ROOT, run


def replace(source, before, after):
    if source.count(before) != 1:
        raise ValueError(f"上流ソースの置換箇所を特定できません: {before}")
    return source.replace(before, after)


def build_backend():
    prefix = Path(
        run(["lean", "--print-prefix"], ROOT, capture_output=True).stdout.strip()
    )
    source = prefix / "src/lean/Lean/Server"
    target = ROOT / "target/mathlib-trace-backend"
    target.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for module in ["Watchdog", "FileWorker"]:
        original = (source / f"{module}.lean").read_text()
        hashes[module] = hashlib.sha256(original.encode()).hexdigest()
        text = original.replace(
            "public section", "import LeansailTrace\n\npublic section", 1
        )
        if module == "Watchdog":
            text = replace(
                text,
                "  let task ← ServerTask.IO.asTask do\n    let oleanSearchPath",
                '  if (← IO.getEnv "LEANSAIL_BENCH_SKIP_INDEX") == some "1" then\n'
                '    leansailTrace "index_skipped"\n    return\n'
                '  let task ← ServerTask.IO.asTask do\n    leansailTrace "index_start"\n'
                "    let oleanSearchPath",
            )
            text = replace(
                text,
                "  referenceData.atomically <| modify fun rd =>\n    { rd with loadingTask",
                "    let count ← referenceData.atomically do\n"
                "      return (← get).references.ileans.size\n"
                '    leansailTrace "index_done" (toString count)\n'
                "  referenceData.atomically <| modify fun rd =>\n    { rd with loadingTask",
            )
            text = replace(
                text,
                "    let workerProc ← Process.spawn {",
                '    leansailTrace "worker_spawn" m.uri\n    let workerProc ← Process.spawn {',
            )
            text = replace(
                text,
                '    fw.stdin.writeLspRequest ⟨0, "initialize", st.initParams⟩',
                '    leansailTrace "worker_spawned" m.uri\n    fw.stdin.writeLspRequest ⟨0, "initialize", st.initParams⟩',
            )
            text = replace(
                text,
                '  let initRequest ← i.readLspRequestAs "initialize" InitializeParams',
                '  leansailTrace "watchdog_ready"\n  let initRequest ← i.readLspRequestAs "initialize" InitializeParams',
            )
        else:
            text = text.replace("server.reportDelayMs", "leansailBench.reportDelayMs")
            text = replace(
                text,
                "  let header := stx.toModuleHeader",
                '  leansailTrace "setup_start" doc.uri\n  let header := stx.toModuleHeader',
            )
            text = replace(
                text,
                "  let isSetupError :=",
                '  leansailTrace "setup_done" doc.uri\n  let isSetupError :=',
            )
            text = replace(
                text,
                "  return .ok {\n    mainModuleName",
                '  leansailTrace "imports_start" doc.uri\n  return .ok {\n    mainModuleName',
            )
            text = replace(
                text,
                "      let importClosure ← importClosure.filterMapM",
                '      leansailTrace "imports_done" doc.uri\n      let importClosure ← importClosure.filterMapM',
            )
            text = replace(
                text,
                '  let initParams ← i.readLspRequestAs "initialize" InitializeParams',
                '  leansailTrace "worker_ready"\n  let initParams ← i.readLspRequestAs "initialize" InitializeParams',
            )
        (target / f"Bench{module}.lean").write_text(text)
    main = (ROOT / "native/Main.lean").read_text()
    for module in ["Watchdog", "FileWorker"]:
        main = main.replace(f"import Lean.Server.{module}\n", f"import Bench{module}\n")
    (target / "Main.lean").write_text(main)
    index = (
        (ROOT / "native/LeansailIndex.lean")
        .read_text()
        .replace("import Lean.Server.Watchdog", "import BenchWatchdog")
    )
    index = replace(
        index,
        '  let some helper ← IO.getEnv "LEANSAIL_INDEX_HELPER"',
        '  if (← IO.getEnv "LEANSAIL_BENCH_SKIP_INDEX") == some "1" then\n    leansailTrace "index_skipped"\n    return\n  if (← IO.getEnv "LEANSAIL_BENCH_SKIP_CACHE") == some "1" then\n    return ← startLoadingReferences referenceData\n  let some helper ← IO.getEnv "LEANSAIL_INDEX_HELPER"',
    )
    (target / "LeansailIndex.lean").write_text(index)
    (target / "LeansailTrace.lean").write_bytes(
        (ROOT / "native/LeansailTrace.lean").read_bytes()
    )
    (target / "lakefile.toml").write_text(
        (ROOT / "native/lakefile.toml").read_text()
        + '\n[[lean_lib]]\nname = "BenchWatchdog"\n\n[[lean_lib]]\nname = "BenchFileWorker"\n'
    )
    (target / "upstream-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    run(["taskset", "-c", "0,1", "lake", "build"], target)
    link = target / ".lake/build/src/lean"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to(prefix / "src/lean", target_is_directory=True)
    return target / ".lake/build/bin/leansail-backend"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="段階別の時刻を記録する backend のビルド"
    )
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    os.environ["PATH"] = (
        str(args.toolchain.resolve() / "bin") + os.pathsep + os.environ["PATH"]
    )
    os.environ["LEAN_SYSROOT"] = str(args.toolchain.resolve())
    print(build_backend())
