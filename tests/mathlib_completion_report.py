import argparse
import json
from itertools import pairwise
from pathlib import Path

from mathlib import FILES, ROOT, stats
from mathlib_report import summarize


def counts(source, label):
    sessions = [s for s in source["sessions"] if s["server"] == label]
    accepted = [s for s in sessions if s["accepted"]]
    assert len({s["repetition"] for s in accepted}) == len(accepted)
    for session in sessions:
        assert session["accepted"] == (
            session["resources"]["outside_cpu_cores"] <= source["max_outside_cores"]
        )
    return {
        "accepted": len(accepted),
        "rejected": sum(not s["accepted"] for s in sessions),
        "preflight_skips": sum(s["server"] == label for s in source["preflight_skips"]),
        "interrupted": sum(
            s["server"] == label for s in source["interrupted_sessions"]
        ),
    }


def groups(session):
    pending = {}
    active = {}
    result = []
    for event in session["events"]:
        kind = event["event"]
        if kind == "client_start":
            method = event["method"]
            pending[event["id"]] = [event]
            active[method] = event["id"]
        elif kind.startswith(("completion_", "resolve_")):
            method = (
                "textDocument/completion"
                if kind.startswith("completion_")
                else "completionItem/resolve"
            )
            if method in active:
                pending[active[method]].append(event)
        elif kind == "client_received":
            group = pending.pop(event["id"])
            group.append(event)
            active.pop(event["method"])
            result.append(group)
    assert not pending, pending
    return result


def stage_values(group):
    start, *backend, end = group
    result = {"client_round_trip_ms": (end["ns"] - start["ns"]) / 1e6}
    assert backend, group
    assert backend[0]["event"].endswith("_start")
    assert backend[-1]["event"].endswith("_serialize_done")
    assert len({event["event"] for event in backend}) == len(backend), group
    for a, b in pairwise(backend):
        result[b["event"]] = (b["ns"] - a["ns"]) / 1e6
    result["backend_total_ms"] = (backend[-1]["ns"] - backend[0]["ns"]) / 1e6
    result["transport_and_client_ms"] = (
        result["client_round_trip_ms"] - result["backend_total_ms"]
    )
    return result


def profile(source):
    result = {}
    for label in ["before", "leansail"]:
        collected = {}
        records = []
        excluded = []
        for session in source["sessions"]:
            if session["server"] != label:
                continue
            completions = 0
            for group in groups(session):
                method = group[0]["method"]
                path = (
                    FILES[0]
                    if "rounds" in session
                    else FILES[
                        max(0, completions - (method == "completionItem/resolve"))
                        // (source["samples"] + 1)
                    ]
                )
                if method == "completionItem/resolve":
                    condition = "resolve"
                elif "rounds" in session:
                    condition = (
                        "first"
                        if completions == 0
                        else session["rounds"][completions - 1]["phase"]
                    )
                    completions += 1
                else:
                    first = completions % (source["samples"] + 1) == 0
                    condition = "first" if first else "warm"
                    completions += 1
                values = stage_values(group)
                if session["accepted"]:
                    collected.setdefault(condition, []).append(values)
                destination = records if session["accepted"] else excluded
                destination.append(
                    {
                        "repetition": session["repetition"],
                        "file": path,
                        "condition": condition,
                        "outside_cpu_cores": session["resources"]["outside_cpu_cores"],
                        "ms": values,
                        "details": {
                            e["event"]: e.get("detail", "") for e in group[1:-1]
                        },
                    }
                )
            expected = (
                1 + len(session["rounds"])
                if "rounds" in session
                else len(FILES) * (source["samples"] + 1)
            )
            assert completions == expected, (completions, expected)
        result[label] = {
            "status": "計測済み"
            if counts(source, label)["accepted"] >= source["repetitions"]
            else "未計測",
            "counts": counts(source, label),
            "stages": {
                condition: {key: stats([row[key] for row in rows]) for key in rows[0]}
                for condition, rows in collected.items()
            },
            "samples": records,
            "excluded_samples": excluded,
        }
    return result


def normal(source):
    result = {}
    all_hashes = {}
    for label in ["upstream", "before", "leansail"]:
        counted = counts(source, label)
        measured = summarize(source, label, allow_incomplete=True)
        result[label] = {
            "status": "計測済み" if counted["accepted"] >= 5 else "未計測",
            "counts": counted,
            "measurements": measured,
        }
        for session in source["sessions"]:
            if session["server"] != label:
                continue
            for entry in session["files"]:
                for key, value in [
                    ("completion", entry["response_hashes"]["textDocument/completion"]),
                    ("resolved", entry["completion_resolved_sha256"]),
                ]:
                    all_hashes.setdefault((entry["path"], key), set()).add(value)
    assert all(len(values) == 1 for values in all_hashes.values()), all_hashes
    return {
        "series": result,
        "response_parity": {
            f"{path}:{kind}": sorted(values)
            for (path, kind), values in all_hashes.items()
        },
    }


def parallel(source):
    result = {}
    hashes = {}
    for label in ["upstream", "before", "leansail"]:
        counted = counts(source, label)
        sessions = [
            s for s in source["sessions"] if s["server"] == label and s["accepted"]
        ]
        entry = {
            "status": "計測済み" if counted["accepted"] >= 5 else "未計測",
            "counts": counted,
            "first_completion": stats(
                [s["warmup_ms"]["textDocument/completion"] for s in sessions]
            )
            if sessions
            else None,
        }
        for phase in ["quiet", "busy"]:
            rows = [
                row
                for session in sessions
                for row in session["rounds"]
                if row["phase"] == phase
            ]
            entry[phase] = (
                {
                    "latency": {
                        method: stats([r["latency_ms"][method] for r in rows])
                        for method in rows[0]["latency_ms"]
                    },
                    "diagnostics_notification": stats(
                        [r["diagnostics_notification_ms"] for r in rows]
                    ),
                    "background_pending": [
                        r["background_pending_at_foreground_end"] for r in rows
                    ],
                }
                if rows
                else None
            )
        entry["steady_pss_mib"] = [
            s["steady_pss"]["total_pss_kib"] / 1024 for s in sessions
        ]
        entry["peak_pss_mib"] = [
            s["resources"]["simultaneous_peak_pss_kib"] / 1024 for s in sessions
        ]
        result[label] = entry
    for session in source["sessions"]:
        for row in session["rounds"]:
            for method, value in row["response_hashes"].items():
                hashes.setdefault(method, set()).add(value)
    assert all(len(values) == 1 for values in hashes.values()), hashes
    return {
        "series": result,
        "response_parity": {
            method: sorted(values) for method, values in hashes.items()
        },
    }


def compact(source):
    return {
        "configuration": {
            key: value
            for key, value in source.items()
            if key not in ["sessions", "preflight_skips", "interrupted_sessions"]
        },
        "preflight_skips": source["preflight_skips"],
        "interrupted_sessions": source["interrupted_sessions"],
        "attempts": [
            {
                "server": s["server"],
                "repetition": s["repetition"],
                "accepted": s["accepted"],
                "outside_cpu_cores": s["resources"]["outside_cpu_cores"],
                **{
                    key: s[key]
                    for key in ["files", "rounds", "warmup_ms", "completion_stages"]
                    if key in s
                },
            }
            for s in source["sessions"]
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="補完の通常・並列・段階計測と応答の一致を集計する"
    )
    parser.add_argument("--normal", type=Path, required=True)
    parser.add_argument("--parallel", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--parallel-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = {
        key: json.loads(getattr(args, key).read_text())
        for key in ["normal", "parallel", "profile", "parallel_profile"]
    }
    for key, source in sources.items():
        for field in [
            "machine",
            "mathlib_revision",
            "lean_git_hash",
            "max_outside_cores",
            "host_sha256",
        ]:
            assert source[field] == sources["normal"][field], (key, field)
        assert not source["profile"] and not source["perf"]
    assert (
        sources["normal"]["repetitions"] >= 5
        and sources["parallel"]["repetitions"] >= 5
    )
    for field in ["backend_sha256", "before_backend_sha256"]:
        assert sources["normal"][field] == sources["parallel"][field]
        assert sources["profile"][field] == sources["parallel_profile"][field]
    result = {
        "normal": normal(sources["normal"]),
        "parallel": parallel(sources["parallel"]),
        "profile": profile(sources["profile"]),
        "parallel_profile": profile(sources["parallel_profile"]),
        "inputs": {key: compact(source) for key, source in sources.items()},
    }
    parity = []
    references = result["normal"]["response_parity"]
    for name in ["profile", "parallel_profile"]:
        for session in sources[name]["sessions"]:
            if "rounds" in session:
                comparisons = [
                    (
                        FILES[0] + ":completion",
                        row["response_hashes"]["textDocument/completion"],
                    )
                    for row in session["rounds"]
                ]
            else:
                comparisons = [
                    pair
                    for entry in session["files"]
                    for pair in [
                        (
                            entry["path"] + ":completion",
                            entry["response_hashes"]["textDocument/completion"],
                        ),
                        (
                            entry["path"] + ":resolved",
                            entry["completion_resolved_sha256"],
                        ),
                    ]
                ]
            for key, value in comparisons:
                expected = references.get(key)
                if expected:
                    assert expected == [value], (name, key, value, expected)
                parity.append(bool(expected))
    result["profile_response_parity"] = "一致" if parity and all(parity) else "未計測"
    output = (
        json.dumps(result, ensure_ascii=False, indent=2)
        .replace(str(ROOT), "<repo>")
        .replace(str(Path.home()), "<home>")
    )
    args.output.write_text(output + "\n")
    print("補完の採用判定、実行条件、順序を含む応答本文と resolve の照合を集計しました")


if __name__ == "__main__":
    main()
