import argparse
import json
import random
import statistics
from collections import Counter
from pathlib import Path

from mathlib import FILES, stats


def role(command):
    if "--worker" in command:
        return "worker:" + command[-1].split("/Mathlib/")[-1]
    if "--server" in command:
        return "watchdog"
    if "run-server" in command:
        return "host"
    if "index-cache" in command:
        return "index_validation"
    return "setup"


def stages(session):
    events = session["events"]

    def first(name):
        return next((e["ms"] for e in events if e["event"] == name), None)

    def duration(start, end):
        a, b = first(start), first(end)
        return b - a if a is not None and b is not None else None

    result = {
        name: first(name)
        for name in [
            "host_main",
            "backend_spawned",
            "backend_main",
            "client_initialized",
            "client_did_open",
            "did_open_forwarded",
            "worker_spawn",
            "worker_ready",
            "setup_start",
            "setup_done",
            "imports_start",
            "imports_done",
            "index_start",
            "cache_checked",
            "index_done",
            "index_saved",
            "index_and_document_ready",
        ]
    }
    result["handlers_cost"] = duration("search_path_ready", "handlers_ready")
    result["setup_cost"] = duration("setup_start", "setup_done")
    result["imports_cost"] = duration("imports_start", "imports_done")
    result["index_cost"] = duration("index_start", "index_done")
    result["snapshot_load_cost"] = (
        duration("cache_checked", "index_done")
        if any(
            e["event"] == "index_done" and e.get("detail") == "cache" for e in events
        )
        else None
    )
    result["first_publish"] = first("client_diagnostic")
    result["first_nonempty_publish"] = session["startup_first_diagnostic_ms"]
    return result


def summarize(source, label):
    sessions = [s for s in source["sessions"] if s["server"] == label and s["accepted"]]
    counts = {
        "accepted": len(sessions),
        "rejected": sum(
            s["server"] == label and not s["accepted"] for s in source["sessions"]
        ),
        "preflight_skips": sum(s["server"] == label for s in source["preflight_skips"]),
        "interrupted": sum(
            s["server"] == label for s in source["interrupted_sessions"]
        ),
    }
    result = {
        "counts": counts,
        "status": "計測済み" if len(sessions) >= source["repetitions"] else "未計測",
    }
    if not sessions:
        return result
    result["index_sources"] = dict(
        Counter(
            next(
                (
                    e.get("detail", "")
                    for e in s["events"]
                    if e["event"] == "index_done"
                ),
                "省略" if label == "no-index" else "上流",
            )
            for s in sessions
        )
    )
    values = [stages(s) for s in sessions]
    result["stages"] = {
        name: stats([v[name] for v in values if v[name] is not None])
        for name in values[0]
        if any(v[name] is not None for v in values)
    }
    result["outside_cpu_cores"] = [
        s["resources"]["outside_cpu_cores"] for s in sessions
    ]
    result["memory_pss_mib"] = {}
    result["memory_private_mib"] = {}
    result["total_pss_mib"] = {}
    for phase in sessions[0]["memory"]:
        result["total_pss_mib"][phase] = [
            sum(p["pss_kib"] for p in s["memory"][phase]) / 1024 for s in sessions
        ]
        result["memory_pss_mib"][phase] = {}
        result["memory_private_mib"][phase] = {}
        for session in sessions:
            for process in session["memory"][phase]:
                name = role(process["command"])
                result["memory_pss_mib"][phase].setdefault(name, []).append(
                    process["pss_kib"] / 1024
                )
                result["memory_private_mib"][phase].setdefault(name, []).append(
                    process["private_kib"] / 1024
                )
    result["cpu_seconds"] = {}
    for session in [] if source["startup_only"] else sessions:
        grouped = {}
        for process in session["resources"]["processes"]:
            name = role(process["command"])
            grouped[name] = (
                grouped.get(name, 0)
                + process["cpu_ticks"] / session["resources"]["clock_ticks_per_second"]
            )
        for name, value in grouped.items():
            result["cpu_seconds"].setdefault(name, []).append(value)
    result["files"] = {}
    for relative in FILES:
        files = [f for s in sessions for f in s["files"] if f["path"] == relative]
        if not files:
            continue
        methods = files[0]["cold"]
        result["files"][relative] = {
            "first_after_ready": {
                method: stats([f["warm"][method]["samples_ms"][0] for f in files])
                for method in methods
            },
            "cold": {
                method: {
                    **stats([f["cold"][method]["ms"] for f in files]),
                    "counts": [f["cold"][method]["count"] for f in files],
                }
                for method in methods
            },
            "warm": {
                method: {
                    **stats(
                        [t for f in files for t in f["warm"][method]["samples_ms"][1:]]
                    ),
                    "counts": [f["warm"][method]["count"] for f in files],
                    "response_hashes": sorted(
                        {f["warm"][method]["sha256"] for f in files}
                    ),
                }
                for method in methods
            },
        }
    return result


def paired(source, baseline, candidate, metric="first_nonempty_publish"):
    def selected(label):
        return {
            s["repetition"]: stages(s)[metric]
            for s in source["sessions"]
            if s["server"] == label and s["accepted"] and stages(s)[metric] is not None
        }

    a, b = selected(baseline), selected(candidate)
    deltas = [b[k] - a[k] for k in sorted(a.keys() & b.keys())]
    if not deltas:
        return None
    rng = random.Random(0)
    bootstrap = sorted(
        statistics.median(rng.choices(deltas, k=len(deltas))) for _ in range(10000)
    )
    return {
        "pairs": len(deltas),
        "delta_ms": deltas,
        "median_delta_ms": statistics.median(deltas),
        "bootstrap_95_percent_ms": [bootstrap[249], bootstrap[9749]],
    }


def summarize_memory(source):
    sessions = [s for s in source["sessions"] if s["accepted"]]
    result = {
        "accepted": len(sessions),
        "rejected": len(source["sessions"]) - len(sessions),
        "preflight_skips": len(source["preflight_skips"]),
        "interrupted": len(source["interrupted_sessions"]),
        "status": "計測済み" if len(sessions) == source["repetitions"] else "未計測",
        "samples": [],
    }
    for session in sessions:
        values = []
        for snap in session["snapshots"]:
            values.append(
                {
                    "sessions": snap["sessions"],
                    "pss_mib": sum(p["pss_kib"] for p in snap["processes"]) / 1024,
                    "private_clean_mib": sum(
                        p["private_clean_kib"] for p in snap["processes"]
                    )
                    / 1024,
                    "private_dirty_mib": sum(
                        p["private_dirty_kib"] for p in snap["processes"]
                    )
                    / 1024,
                    "shared_clean_mib": sum(
                        p["shared_clean_kib"] for p in snap["processes"]
                    )
                    / 1024,
                    "shared_dirty_mib": sum(
                        p["shared_dirty_kib"] for p in snap["processes"]
                    )
                    / 1024,
                }
            )
            if all("index_mapping_kib" in p for p in snap["processes"]):
                values[-1]["index_mapping_mib"] = {
                    field: sum(
                        p["index_mapping_kib"].get(field, 0) for p in snap["processes"]
                    )
                    / 1024
                    for field in [
                        "Rss",
                        "Pss",
                        "Private_Clean",
                        "Private_Dirty",
                        "Shared_Clean",
                        "Shared_Dirty",
                    ]
                }
        result["samples"].append(
            {
                "repetition": session["repetition"],
                "snapshots": values,
                "shared_saving_mib": 2 * values[0]["pss_mib"] - values[1]["pss_mib"],
                "outside_cpu_cores": session["resources"]["outside_cpu_cores"],
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser(description="起動と索引の標本の集計")
    parser.add_argument(
        "--input", nargs=2, action="append", required=True, metavar=("名前", "JSON")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {}
    configuration = None
    all_responses = {}
    for name, path in args.input:
        source = json.loads(Path(path).read_text())
        common = {
            key: source[key]
            for key in [
                "mathlib_revision",
                "lean_git_hash",
                "machine",
                "cpu_count",
                "host_sha256",
                "backend_sha256",
                "max_outside_cores",
            ]
        }
        if configuration is None:
            configuration = common
        else:
            assert common == configuration, (
                "集計するホスト・toolchain・実行ファイル・負荷基準が一致しません"
            )
        if source["scenario"].endswith(".memory_session"):
            result[name] = {"memory_sharing": summarize_memory(source)}
            continue
        labels = list(
            dict.fromkeys(
                [s["server"] for s in source["sessions"]]
                + [s["server"] for s in source["preflight_skips"]]
            )
        )
        baseline = "upstream" if "upstream" in labels else "no-cache"
        result[name] = {
            "configuration": {
                key: source[key]
                for key in [
                    "mathlib_revision",
                    "lean_git_hash",
                    "machine",
                    "cpu_count",
                    "host_sha256",
                    "backend_sha256",
                    "max_outside_cores",
                    "samples",
                    "repetitions",
                    "startup_only",
                ]
            },
            "series": {label: summarize(source, label) for label in labels},
            "paired_baseline": baseline,
            "paired_startup": {
                label: paired(source, baseline, label)
                for label in labels
                if label != baseline
            },
            "paired_index_ready": {
                label: paired(source, baseline, label, "index_done")
                for label in labels
                if label != baseline
            },
        }
        if not source["startup_only"]:
            for relative in FILES:
                methods = {}
                for label, summary in result[name]["series"].items():
                    if label == "no-index":
                        continue
                    for method, data in (
                        summary.get("files", {})
                        .get(relative, {})
                        .get("warm", {})
                        .items()
                    ):
                        methods.setdefault(method, set()).update(
                            data["response_hashes"]
                        )
                        all_responses.setdefault((relative, method), set()).update(
                            data["response_hashes"]
                        )
                assert all(len(hashes) == 1 for hashes in methods.values()), (
                    relative,
                    methods,
                )
    assert all(len(hashes) == 1 for hashes in all_responses.values()), (
        "シナリオ間の解析後の応答が一致しません"
    )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                name: {
                    label: value["counts"] for label, value in data["series"].items()
                }
                if "series" in data
                else {
                    key: value
                    for key, value in data["memory_sharing"].items()
                    if key != "samples"
                }
                for name, data in result.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
