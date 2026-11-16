import argparse
import json
from pathlib import Path

from mathlib import stats


def summarize(source):
    result = {
        "machine": source["machine"],
        "inputs": {
            key: source[key]
            for key in [
                "mathlib_revision",
                "lean_git_hash",
                "host_sha256",
                "backend_sha256",
                "samples",
                "repetitions",
                "max_outside_cores",
                "scenario",
            ]
        },
        "counts": {},
        "series": {},
    }
    assert not source["profile"] and not source["perf"]
    response_hashes = {}
    for server in ["upstream", "leansail"]:
        sessions = [
            s for s in source["sessions"] if s["server"] == server and s["accepted"]
        ]
        assert len(sessions) == source["repetitions"], "採用した反復が揃っていません"
        assert len({s["repetition"] for s in sessions}) == len(sessions)
        result["counts"][server] = {
            "accepted": len(sessions),
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
        rounds = [r for s in sessions for r in s["rounds"]]
        entry = {
            "cold_open_complete": stats([s["cold_open_complete_ms"] for s in sessions]),
            "outside_cpu_cores": [
                s["resources"]["outside_cpu_cores"] for s in sessions
            ],
            "peak_total_pss_mib": [
                s["resources"]["simultaneous_peak_pss_kib"] / 1024 for s in sessions
            ],
            "peak_total_rss_mib": [
                s["resources"]["simultaneous_peak_rss_kib"] / 1024 for s in sessions
            ],
            "steady_total_pss_mib": [
                s["steady_pss"]["total_pss_kib"] / 1024 for s in sessions
            ],
            "process_peak_pss_mib": {},
        }
        for session in sessions:
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
                entry["process_peak_pss_mib"].setdefault(role, []).append(
                    process.get("peak_pss_kib", 0) / 1024
                )
        for phase in ["quiet", "busy"]:
            selected = [r for r in rounds if r["phase"] == phase]
            assert len(selected) == source["samples"] * len(sessions)
            entry[phase] = {
                "samples": len(selected),
                "latency": {
                    method: stats([r["latency_ms"][method] for r in selected])
                    for method in selected[0]["latency_ms"]
                },
                "diagnostics_notification": stats(
                    [r["diagnostics_notification_ms"] for r in selected]
                ),
                "background_pending_at_foreground_end": [
                    r["background_pending_at_foreground_end"] for r in selected
                ],
            }
        for row in rounds:
            for method, value in row["response_hashes"].items():
                response_hashes.setdefault(method, set()).add(value)
        result["series"][server] = entry
    assert all(len(values) == 1 for values in response_hashes.values()), (
        "前景要求の応答本文が一致しません"
    )
    result["response_parity"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description="並列解析時の要求と PSS を集計する")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(json.loads(args.source.read_text()))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("| 構成 | 条件 | hover p95 ms | 補完 p95 ms | 診断完了 p95 ms |")
    print("|---|---|---:|---:|---:|")
    for server, data in result["series"].items():
        for phase in ["quiet", "busy"]:
            print(
                f"| {server} | {phase} | "
                + " | ".join(
                    f"{value['p95_ms']:.3f}"
                    for value in data[phase]["latency"].values()
                )
                + " |"
            )
        print(f"{server}: 合計 PSS 最大 {max(data['peak_total_pss_mib']):.1f} MiB")


if __name__ == "__main__":
    main()
