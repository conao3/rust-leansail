"""Run with nix develop --command python3 tests/bench_mathlib_upstream.py."""

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path

from mathlib import FILES, REVISION, ROOT, build_backend, stats


def report(data):
    selected = {
        label: [s for s in data["sessions"] if s["server"] == label and s["accepted"]]
        for label in ["upstream", "leansail"]
    }
    if any(len(sessions) != data["repetitions"] for sessions in selected.values()):
        raise ValueError("Incomplete comparison: every server needs all repetitions")
    lines = [
        "# Mathlib upstream comparison",
        "",
        (
            f"Mathlib v4.35.0: `{REVISION}`. Official Lean 4.35.0 "
            f"(`{data['lean_git_hash']}`)."
        ),
        (
            f"Leansail: `{data['sessions'][0]['source_revision']}` (host and backend "
            "SHA-256 and source hashes are in raw.json)."
        ),
        (
            f"Host: {data['machine']['platform']}; {data['cpu_count']} logical CPUs; "
            f"{data['host_info']['cpu']}; {data['host_info']['memory_kib']} KiB RAM."
        ),
        (
            f"Iterations: {data['repetitions']} sessions per server, "
            f"{data['samples']} repeated requests per method/file/session. "
            "Server order alternates between sessions. Edits are not used by this scenario."
        ),
        "",
        (
            "Both servers use the same cached Mathlib files and request fixtures. "
            "Requests follow completed diagnostics and index readiness; Leansail must "
            "hit its primed persistent index. These are warm-cache, not cold-start results. "
            "p95 uses the existing harness's nearest-rank estimator. Small samples and "
            "shared VM scheduling limit generalization. Ratios are upstream / Leansail "
            "(above 1 favors Leansail). All durations are milliseconds."
        ),
        "",
        (
            "Reproduce: `nix develop --command python3 tests/bench_mathlib_upstream.py "
            "--output-dir results/new-run` (choose a fresh directory for each run)."
        ),
        (
            "Use `--samples` and `--repetitions` to change counts; `--output-dir` "
            "selects a fresh destination. Requires Nix, Git, and network access. "
            "Only the cache downloader and Leansail backend are built, not Mathlib."
        ),
        "",
        (
            f"External CPU limit: {data['max_outside_cores']} cores. "
            f"Preflight skips: {len(data['preflight_skips'])}; "
            f"load-rejected sessions: {sum(not s['accepted'] for s in data['sessions'])}; "
            f"interruptions: {len(data['interrupted_sessions'])}."
        ),
        "",
    ]
    for relative in FILES:
        lines += [
            f"## {relative}",
            "",
            "| Request / phase | Upstream median / p95 | Leansail median / p95 | Ratio median / p95 |",
            "| --- | ---: | ---: | ---: |",
        ]
        entries = {
            label: [next(f for f in s["files"] if f["path"] == relative) for s in ss]
            for label, ss in selected.items()
        }
        for method in entries["upstream"][0]["requests"]:
            for phase in ["first", "repeated"]:
                values = {}
                for label, fs in entries.items():
                    values[label] = stats(
                        [
                            sample["ms"]
                            for f in fs
                            for sample in (
                                [f["requests"][method][phase]]
                                if phase == "first"
                                else f["requests"][method][phase]
                            )
                        ]
                    )
                a, b = values["upstream"], values["leansail"]
                lines.append(
                    f"| {method} / {phase} | {a['median_ms']:.3f} / {a['p95_ms']:.3f} | "
                    f"{b['median_ms']:.3f} / {b['p95_ms']:.3f} | "
                    f"{a['median_ms'] / b['median_ms']:.2f} / {a['p95_ms'] / b['p95_ms']:.2f} |"
                )
        lines.append("")
    lines += [
        (
            "Raw response digests are retained for inspection. The harness checks "
            "nonempty successful responses and repeat stability within each server; "
            "this report does not assert cross-server response equivalence."
        ),
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    if args.samples < 1 or args.repetitions < 1:
        parser.error("Iteration counts must be positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "raw.json").exists():
        parser.error("raw.json already exists; choose a fresh --output-dir")
    subprocess.run(
        [
            "nix",
            "shell",
            "--inputs-from",
            str(ROOT),
            "nixpkgs#elan",
            "--command",
            "elan",
            "toolchain",
            "install",
            "leanprover/lean4:v4.35.0",
        ],
        check=True,
    )
    toolchain = Path.home() / ".elan/toolchains/leanprover--lean4---v4.35.0"
    os.environ["PATH"] = str(toolchain / "bin") + os.pathsep + os.environ["PATH"]
    os.environ["LEAN_SYSROOT"] = str(toolchain)
    common = ["--toolchain", str(toolchain)]

    def python(script, *extra):
        subprocess.run(
            ["python3", str(ROOT / "tests" / script), *common, *extra],
            cwd=ROOT,
            check=True,
        )

    python("mathlib_upstream.py", "--prepare-only")
    subprocess.run(["cargo", "build", "--release", "-j", "4"], cwd=ROOT, check=True)
    common += ["--backend", str(build_backend())]
    work = ROOT / "target/upstream-entry"
    work.mkdir(parents=True, exist_ok=True)
    common += [
        "--index-cache-dir",
        str(work / "index"),
        "--wait-idle",
        "--max-attempts",
        "20",
    ]
    python(
        "mathlib_index_prime.py",
        "--server",
        "leansail",
        "--samples",
        "1",
        "--repetitions",
        "1",
        "--output",
        str(work / "prime.json"),
    )
    python(
        "mathlib_upstream.py",
        "--require-cache-hit",
        "--server",
        "upstream",
        "--server",
        "leansail",
        "--samples",
        str(args.samples),
        "--repetitions",
        str(args.repetitions),
        "--edits",
        "2",
        "--output",
        str(work / "raw.json"),
    )
    data = json.loads((work / "raw.json").read_text())
    data["host_info"] = host_info()
    write_results(data, output)
    prime = (work / "prime.json").read_text()
    (output / "prime.json").write_text(normalize(prime))


def host_info():
    return {
        "cpu": next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "memory_kib": int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1]),
        "kernel": platform.release(),
    }


def normalize(value):
    return value.replace(str(ROOT), "<repo>").replace(str(Path.home()), "<home>")


def write_results(data, output):
    markdown = report(data)
    (output / "raw.json").write_text(normalize(json.dumps(data, indent=2) + "\n"))
    (output / "README.md").write_text(markdown)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
