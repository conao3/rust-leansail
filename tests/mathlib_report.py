import argparse
import json
from pathlib import Path

from mathlib import FILES, METHODS, stats


def summarize(source, server, allow_incomplete=False):
    sessions = [
        s for s in source["sessions"] if s["server"] == server and s["accepted"]
    ]
    if not allow_incomplete:
        assert len(sessions) == source["repetitions"], "成功した反復が揃っていません"
    assert len({s["repetition"] for s in sessions}) == len(sessions)
    if not sessions:
        return None
    result = {
        "startup": stats([s["startup_first_diagnostic_ms"] for s in sessions]),
        "initialize": stats([s["initialize_ms"] for s in sessions]),
        "outside_cpu_cores": [s["resources"]["outside_cpu_cores"] for s in sessions],
        "files": {},
        "rss_peak_mib": {},
    }
    for relative in FILES:
        files = [next(f for f in s["files"] if f["path"] == relative) for s in sessions]
        result["files"][relative] = {
            "open_complete": stats([f["open_complete_ms"] for f in files]),
            "requests": {
                method: {
                    "first": stats([f["requests"][method]["first_ms"] for f in files]),
                    "warm": stats(
                        [
                            sample
                            for f in files
                            for sample in f["requests"][method]["samples_ms"]
                        ]
                    ),
                }
                for method in METHODS
            },
            "edits": {
                kind: stats(
                    [sample for f in files for sample in f["edits"][kind]["samples_ms"]]
                )
                for kind in ["tail", "early"]
            },
            "response_hashes": [f["response_hashes"] for f in files],
        }
    for session in sessions:
        peaks = {}
        for process in session["resources"]["processes"]:
            command = process["command"]
            if "--worker" in command:
                role = "worker:" + command[-1].split("/Mathlib/", 1)[1]
            elif "--server" in command:
                role = "watchdog"
            elif "run-server" in command:
                role = "host"
            else:
                role = "setup"
            peaks[role] = max(peaks.get(role, 0), process["peak_rss_kib"] / 1024)
        peaks["simultaneous_total"] = (
            session["resources"]["simultaneous_peak_rss_kib"] / 1024
        )
        for role, value in peaks.items():
            result["rss_peak_mib"].setdefault(role, []).append(value)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Mathlib の生の標本を集計し、応答の一致を検査する"
    )
    parser.add_argument(
        "--series",
        nargs=3,
        metavar=("名前", "サーバー", "JSON"),
        action="append",
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    series = {}
    inputs = {}
    counts = {}
    for name, server, path in args.series:
        if args.allow_incomplete and not Path(path).exists():
            series[name] = None
            inputs[name] = {"status": "未計測"}
            counts[name] = {"accepted": 0, "rejected": 0}
            continue
        source = json.loads(Path(path).read_text())
        assert not source["perf"] and not source["profile"], (
            "プロファイルの標本は通常計測と分けてください"
        )
        series[name] = summarize(source, server, args.allow_incomplete)
        counts[name] = {
            "accepted": sum(
                s["server"] == server and s["accepted"] for s in source["sessions"]
            ),
            "rejected": sum(
                s["server"] == server and not s["accepted"] for s in source["sessions"]
            ),
            "preflight_skips": sum(
                s["server"] == server for s in source["preflight_skips"]
            ),
            "interrupted": sum(
                s["server"] == server for s in source["interrupted_sessions"]
            ),
        }
        inputs[name] = {
            key: source[key]
            for key in [
                "mathlib_revision",
                "lean_git_hash",
                "samples",
                "repetitions",
                "edits",
                "cpu_count",
                "max_outside_cores",
                "host_sha256",
                "backend_sha256",
                "machine",
                "scenario",
            ]
        }
        if server == "before":
            inputs[name]["backend_sha256"] = source["before_backend_sha256"]
    for key in [
        "mathlib_revision",
        "lean_git_hash",
        "samples",
        "repetitions",
        "edits",
        "cpu_count",
        "max_outside_cores",
        "host_sha256",
        "scenario",
    ]:
        assert (
            len({metadata[key] for metadata in inputs.values() if key in metadata}) == 1
        ), key
    assert (
        len(
            {
                json.dumps(metadata["machine"], sort_keys=True)
                for metadata in inputs.values()
                if "machine" in metadata
            }
        )
        == 1
    ), "異なるホストの標本は混ぜられません"
    for relative in FILES:
        for method in METHODS:
            hashes = {
                h[method]
                for s in series.values()
                if s is not None
                for h in s["files"][relative]["response_hashes"]
            }
            assert len(hashes) == 1, (relative, method, "初回の応答が一致しません")
    output = {
        "inputs": inputs,
        "counts": counts,
        "series": series,
        "response_parity": True,
        "response_parity_scope": [
            name for name, data in series.items() if data is not None
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")

    def header():
        print("| 対象 | " + " | ".join(series) + " |")
        print("|---|" + "---:|" * len(series))

    def row(label, values):
        print(
            "| "
            + label
            + " | "
            + " | ".join(
                f"{v['median_ms']:.3f} / {v['p95_ms']:.3f}"
                if v is not None
                else "未計測"
                for v in values
            )
            + " |"
        )

    header()
    row("起動から最初の診断", [s["startup"] if s else None for s in series.values()])
    for relative in FILES:
        print(f"\n{relative}\n")
        header()
        row(
            "open から解析完了",
            [
                s["files"][relative]["open_complete"] if s else None
                for s in series.values()
            ],
        )
        for method in METHODS:
            row(
                method,
                [
                    s["files"][relative]["requests"][method]["warm"] if s else None
                    for s in series.values()
                ],
            )
        row(
            "ハイライト初回",
            [
                s["files"][relative]["requests"]["textDocument/documentHighlight"][
                    "first"
                ]
                if s
                else None
                for s in series.values()
            ],
        )
        for kind in ["tail", "early"]:
            row(
                f"編集 {kind}",
                [
                    s["files"][relative]["edits"][kind] if s else None
                    for s in series.values()
                ],
            )


if __name__ == "__main__":
    main()
