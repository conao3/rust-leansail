import argparse
import json
from itertools import pairwise
from pathlib import Path

from mathlib import FILES, stats
from mathlib_startup_report import summarize


def profile(source, label):
    sessions = [s for s in source["sessions"] if s["server"] == label]
    skips = [s for s in source["preflight_skips"] if s["server"] == label]
    interrupted = [s for s in source["interrupted_sessions"] if s["server"] == label]
    steps = [
        "symbol_start",
        "symbol_modules",
        "symbol_scan",
        "symbol_names",
        "symbol_match",
        "symbol_sort",
        "symbol_format",
        "reference_json",
        "reference_compress",
        "reference_sent",
    ]
    if any(e["event"] == "symbol_prepared" for s in sessions for e in s["events"]):
        steps[2:4] = ["symbol_prepared"]
    samples = []
    records = []
    for session in sessions:
        if not session["accepted"]:
            continue
        groups = []
        current = None
        for event in session["events"]:
            if event["event"] == "symbol_start":
                current = [event]
            elif current is not None and event["event"] in steps:
                current.append(event)
                if event["event"] == steps[-1]:
                    if [e["event"] for e in current] == steps:
                        groups.append(current)
                    current = None
        opens = [e for e in session["events"] if e["event"] == "client_did_open"]
        ready = [
            e for e in session["events"] if e["event"] == "index_and_document_ready"
        ]
        for i, (entry, event) in enumerate(zip(session["files"], ready, strict=True)):
            stop = opens[i + 1]["ns"] if i + 1 < len(opens) else float("inf")
            selected = [g for g in groups if event["ns"] < g[0]["ns"] < stop]
            latencies = entry["warm"]["workspace/symbol"]["samples_ms"]
            assert len(selected) == len(latencies), (len(selected), len(latencies))
            for group, latency in list(zip(selected, latencies, strict=True))[1:]:
                values = {
                    b["event"]: (b["ns"] - a["ns"]) / 1e6 for a, b in pairwise(group)
                }
                values["backend_total"] = (group[-1]["ns"] - group[0]["ns"]) / 1e6
                values["client_round_trip"] = latency
                values["client_remaining"] = latency - values["backend_total"]
                samples.append(values)
                records.append(
                    {
                        "repetition": session["repetition"],
                        "file": entry["path"],
                        "ms": values,
                        "details": {e["event"]: e.get("detail", "") for e in group},
                    }
                )
    return {
        "status": "計測済み"
        if sum(s["accepted"] for s in sessions) >= source["repetitions"]
        else "未計測",
        "attempts": [
            {
                "repetition": s["repetition"],
                "accepted": s["accepted"],
                "outside_cpu_cores": s["resources"]["outside_cpu_cores"],
            }
            for s in sessions
        ],
        "preflight_skips": skips,
        "interrupted_sessions": interrupted,
        "counts": {
            "accepted": sum(s["accepted"] for s in sessions),
            "rejected": sum(not s["accepted"] for s in sessions),
            "preflight_skips": len(skips),
            "interrupted": len(interrupted),
        },
        "stages": {name: stats([s[name] for s in samples]) for name in samples[0]}
        if samples
        else {},
        "samples": records,
    }


def main():
    parser = argparse.ArgumentParser(
        description="workspace/symbol の比較・段階計測を集計する"
    )
    parser.add_argument("measurement", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.measurement.read_text())
    assert not source["profile"] and not source["symbol_profile"]
    assert source["repetitions"] >= 5
    labels = sorted(
        {s["server"] for s in source["sessions"]}
        | {s["server"] for s in source["preflight_skips"]}
    )
    for session in source["sessions"]:
        assert session["accepted"] == (
            session["resources"]["outside_cpu_cores"] <= source["max_outside_cores"]
        )
    series = {label: summarize(source, label) for label in labels}
    result = {
        "configuration": {
            key: source[key]
            for key in [
                "machine",
                "mathlib_revision",
                "lean_git_hash",
                "host_sha256",
                "backend_sha256",
                "before_backend_sha256",
                "samples",
                "repetitions",
                "max_outside_cores",
                "symbol_profile",
            ]
        },
        "series": series,
        "upstream_equal": {},
    }
    result["attempts"] = [
        {
            "server": session["server"],
            "repetition": session["repetition"],
            "accepted": session["accepted"],
            "outside_cpu_cores": session["resources"]["outside_cpu_cores"],
            "index_source": session["index_source"],
            "files": session["files"],
        }
        for session in source["sessions"]
    ]
    result["preflight_skips"] = source["preflight_skips"]
    result["interrupted_sessions"] = source["interrupted_sessions"]
    for relative in FILES:
        methods = sorted(
            {
                method
                for entry in series.values()
                for method in entry.get("files", {}).get(relative, {}).get("warm", {})
            }
        )
        result["upstream_equal"][relative] = {}
        for method in methods:
            hashes = {
                label: entry["files"][relative]["warm"][method]["response_hashes"]
                for label, entry in series.items()
                if relative in entry.get("files", {})
            }
            if "upstream" in hashes:
                assert all(value == hashes["upstream"] for value in hashes.values()), (
                    hashes
                )
            result["upstream_equal"][relative][method] = hashes
    result["profiles"] = {}
    if args.profile:
        data = json.loads(args.profile.read_text())
        assert data["symbol_profile"]
        assert data["max_outside_cores"] == source["max_outside_cores"]
        assert data["repetitions"] >= 5
        assert data["machine"] == source["machine"]
        assert data["mathlib_revision"] == source["mathlib_revision"]
        assert data["lean_git_hash"] == source["lean_git_hash"]
        for session in data["sessions"]:
            if session["accepted"]:
                for entry in session["files"]:
                    for method, value in entry["warm"].items():
                        assert (
                            value["sha256"]
                            in result["upstream_equal"][entry["path"]][method][
                                "upstream"
                            ]
                        )
        for name, label, hash_key in [
            ("before", "before", "before_backend_sha256"),
            ("after", "leansail", "backend_sha256"),
        ]:
            result["profiles"][name] = {
                "backend_sha256": data[hash_key],
                **profile(data, label),
            }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for label, entry in series.items():
        print(label, entry["status"], entry["counts"])
        for relative, data in entry.get("files", {}).items():
            value = data["warm"]["workspace/symbol"]
            print(Path(relative).name, value["median_ms"], value["p95_ms"])


if __name__ == "__main__":
    main()
