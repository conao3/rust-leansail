import argparse
import json
import random
import statistics
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
    for phase in sessions[0]["memory"]:
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
                        [t for f in files for t in f["warm"][method]["samples_ms"]]
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


def paired(source, baseline, candidate):
    def selected(label):
        return {
            s["repetition"]: s["startup_first_diagnostic_ms"]
            for s in source["sessions"]
            if s["server"] == label and s["accepted"]
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


def main():
    parser = argparse.ArgumentParser(description="起動と索引の標本の集計")
    parser.add_argument(
        "--input", nargs=2, action="append", required=True, metavar=("名前", "JSON")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {}
    for name, path in args.input:
        source = json.loads(Path(path).read_text())
        labels = list(
            dict.fromkeys(
                [s["server"] for s in source["sessions"]]
                + [s["server"] for s in source["preflight_skips"]]
            )
        )
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
            "paired_startup": {
                label: paired(source, "upstream", label)
                for label in labels
                if label != "upstream"
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
                assert all(len(hashes) == 1 for hashes in methods.values()), (
                    relative,
                    methods,
                )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                name: {
                    label: value["counts"] for label, value in data["series"].items()
                }
                for name, data in result.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
