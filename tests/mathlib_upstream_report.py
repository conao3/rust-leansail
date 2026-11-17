import argparse
import json
from pathlib import Path

from mathlib import FILES, METHODS, ROOT, stats
from mathlib_completion_report import counts
from mathlib_upstream import digest

METRICS = [
    ("ハイライト初回", "textDocument/documentHighlight", "first"),
    ("ハイライト反復", "textDocument/documentHighlight", "repeated"),
    ("semantic tokens 反復", "textDocument/semanticTokens/full", "repeated"),
    ("補完反復", "textDocument/completion", "repeated"),
    ("workspace/symbol 反復", "workspace/symbol", "repeated"),
    ("参照反復", "textDocument/references", "repeated"),
    ("定義反復", "textDocument/definition", "repeated"),
    ("hover 反復", "textDocument/hover", "repeated"),
    ("plainGoal 反復", "$/lean/plainGoal", "repeated"),
]


def distribution(values, unit):
    result = stats(values)
    return {
        "median": result["median_ms"],
        "p95": result["p95_ms"],
        "samples": len(values),
        "unit": unit,
    }


def comparison(upstream, leansail, unit="ms"):
    left = distribution(upstream, unit)
    right = distribution(leansail, unit)
    return {
        "upstream": left,
        "leansail": right,
        "ratio_median": left["median"] / right["median"],
        "ratio_p95": left["p95"] / right["p95"],
    }


def diagnostic_order(hashes, bodies):
    representatives = {digest(body): body for body in bodies}
    assert set(hashes) <= representatives.keys(), "診断本文の代表値を追加してください"
    normalized = {
        digest(
            sorted(
                representatives[value],
                key=lambda d: json.dumps(d, sort_keys=True, ensure_ascii=False),
            )
        )
        for value in hashes
    }
    assert len(normalized) == 1, "診断通知の内容が一致しません"
    return {
        "comparison": "診断配列の順序を正規化",
        "sha256": next(iter(normalized)),
        "observed_sha256": sorted(hashes),
        "representative_bodies": {value: representatives[value] for value in hashes},
    }


def diagnostics(source, reference, summary):
    for key in [
        "mathlib_revision",
        "host_sha256",
        "backend_sha256",
        "lean_git_hash",
        "machine",
    ]:
        assert source[key] == reference[key], key
    result = {}
    for relative, labels in summary["diagnostic_notification_sha256"].items():
        hashes = set().union(*labels.values())
        if len(hashes) == 1:
            result[relative] = {
                "comparison": "配列の順序も一致",
                "sha256": next(iter(hashes)),
            }
        else:
            bodies = [
                session["after_index"][-1]["diagnostics"]
                for session in reference["sessions"]
                if session["after_index"][-1]["uri"].endswith("/" + relative)
            ]
            result[relative] = diagnostic_order(hashes, bodies)
    return result


def summarize(source, index, prime):
    assert source["repetitions"] >= 5
    assert all(not s["profile"] and not s["perf"] for s in [source, index, prime])
    for key in [
        "mathlib_revision",
        "host_sha256",
        "backend_sha256",
        "lean_git_hash",
        "machine",
        "max_outside_cores",
    ]:
        assert source[key] == prime[key] == index[key], key
    assert index["startup_only"] and not source["startup_only"]
    assert source["repetitions"] == index["repetitions"]
    selected = {}
    accounting = {}
    for label in ["upstream", "leansail"]:
        accounting[label] = {
            "documents": counts(source, label),
            "index": counts(index, label),
        }
        assert all(
            c["accepted"] == source["repetitions"] for c in accounting[label].values()
        )
        selected[label] = [
            s for s in source["sessions"] if s["server"] == label and s["accepted"]
        ]
    sessions = source["sessions"] + index["sessions"]
    assert len({s["source_revision"] for s in sessions}) == 1
    assert all(
        s["source_hashes"] == source["sessions"][0]["source_hashes"] for s in sessions
    )
    assert all(
        s["index_source"] == "cache" for s in sessions if s["server"] == "leansail"
    )
    assert all(not s["files"] for s in index["sessions"])
    for session in source["sessions"]:
        assert [f["path"] for f in session["files"]] == FILES
        for entry in session["files"]:
            assert all(
                len(r["repeated"]) == source["samples"]
                for r in entry["requests"].values()
            )
    equivalence = {}
    diagnostic_notifications = {}
    files = {}
    for relative in FILES:
        entries = {
            label: [next(f for f in s["files"] if f["path"] == relative) for s in ss]
            for label, ss in selected.items()
        }
        completed = [
            next(f for f in s["files"] if f["path"] == relative)
            for s in source["sessions"]
        ]
        assert len({f["fixture_sha256"] for f in completed}) == 1, relative
        hashes = {}
        for method in METHODS + ["workspace/symbol"]:
            requests = [f["requests"][method] for f in completed]
            values = [
                sample["sha256"]
                for request in requests
                for sample in [request["first"], *request["repeated"]]
            ]
            assert len(set(values)) == 1, (relative, method)
            hashes[method] = {"sha256": values[0], "bodies_checked": len(values)}
        diagnostic_notifications[relative] = {
            label: sorted(
                {
                    next(f for f in s["files"] if f["path"] == relative)[
                        "diagnostics_sha256"
                    ]
                    for s in source["sessions"]
                    if s["server"] == label
                }
            )
            for label in selected
        }
        assert len({f["completion_resolved_sha256"] for f in completed}) == 1, relative
        hashes["completionItem/resolve"] = {
            "sha256": completed[0]["completion_resolved_sha256"],
            "sessions": len(completed),
        }
        equivalence[relative] = hashes
        files[relative] = {}
        for title, method, phase in METRICS:
            values = {}
            for label, fs in entries.items():
                values[label] = [
                    sample["ms"]
                    for f in fs
                    for sample in (
                        [f["requests"][method]["first"]]
                        if phase == "first"
                        else f["requests"][method]["repeated"]
                    )
                ]
            files[relative][title] = comparison(**values)
    session_metrics = {}
    for title, key in [
        ("起動から最初の非空診断", "startup_first_diagnostic_ms"),
        ("起動からプロジェクト索引準備", "index_ready_ms"),
    ]:
        session_metrics[title] = comparison(
            **{
                label: [
                    s[key]
                    for s in (
                        index["sessions"]
                        if key == "index_ready_ms"
                        else source["sessions"]
                    )
                    if s["server"] == label and s["accepted"]
                ]
                for label in selected
            }
        )
    for title, key in [
        ("3 文書解析直後の全 PSS", "three_documents_parsed"),
        ("3 文書要求実行後の全 PSS", "three_documents_warm"),
    ]:
        values = {}
        for label, ss in selected.items():
            for s in ss:
                assert len(s["memory"][key]) == (4 if label == "upstream" else 5)
            values[label] = [
                sum(p["pss_kib"] for p in s["memory"][key]) / 1024 for s in ss
            ]
        session_metrics[title] = comparison(**values, unit="MiB")
    initial = prime["sessions"][0]
    assert any(e["event"] == "index_saved" for e in initial["events"])
    assert not any(
        e["event"] == "index_done" and e["detail"] == "cache" for e in initial["events"]
    )
    return {
        "source_revision": source["sessions"][0]["source_revision"],
        "counts": accounting,
        "files": files,
        "session_metrics": session_metrics,
        "equivalence": equivalence,
        "diagnostic_notification_sha256": diagnostic_notifications,
        "initial_creation": {
            "index_ready_ms": initial["index_ready_ms"],
            "cache_ready_ms": initial["cache_ready_ms"],
            "outside_cpu_cores": initial["resources"]["outside_cpu_cores"],
            "accepted": initial["accepted"],
            "counts": counts(prime, "leansail"),
        },
    }


def table(metrics):
    lines = [
        "| 項目 | 本家 中央値 / p95 | Leansail 中央値 / p95 | 倍率 中央値 / p95 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for title, metric in metrics.items():
        left, right = metric["upstream"], metric["leansail"]
        lines.append(
            f"| {title} | {left['median']:.3f} / {left['p95']:.3f} | "
            f"{right['median']:.3f} / {right['p95']:.3f} | "
            f"{metric['ratio_median']:.2f} / {metric['ratio_p95']:.2f} |"
        )
    return "\n".join(lines)


def markdown(summary):
    lines = []
    for relative, metrics in summary["files"].items():
        lines += [f"### {Path(relative).name}", "", table(metrics), ""]
    lines += ["### 起動と 3 文書の PSS", "", table(summary["session_metrics"]), ""]
    lines += [
        "| 計測 | 構成 | 採用 | 負荷除外 | 開始前の見送り | 中断 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for label, scenarios in summary["counts"].items():
        for scenario, values in scenarios.items():
            lines.append(
                f"| {'3 文書' if scenario == 'documents' else '索引のみ'} | "
                f"{'本家' if label == 'upstream' else 'Leansail'} | "
                f"{values['accepted']} | {values['rejected']} | "
                f"{values['preflight_skips']} | {values['interrupted']} |"
            )
    return "\n".join(lines) + "\n"


def chart(summary, output):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager
    from matplotlib import pyplot as plt

    if not any(f.name == "Harano Aji Gothic" for f in font_manager.fontManager.ttflist):
        raise RuntimeError("グラフの日本語表示には Harano Aji Gothic が必要です")
    plt.rcParams.update({"font.family": "Harano Aji Gothic", "svg.fonttype": "path"})
    figure, axes = plt.subplots(1, 3, figsize=(17, 6), sharex=True)
    for axis, (relative, metrics) in zip(axes, summary["files"].items(), strict=True):
        names = list(metrics)
        y = list(range(len(names)))
        for key, marker, color, title in [
            ("ratio_median", "o", "#176b63", "中央値の比"),
            ("ratio_p95", "x", "#a65321", "p95 の比"),
        ]:
            values = [m[key] for m in metrics.values()]
            axis.scatter(values, y, marker=marker, color=color, label=title, zorder=3)
        axis.axvline(1, color="#697482", linestyle="--", linewidth=1)
        axis.set_xscale("log")
        axis.set_yticks(y, names)
        axis.invert_yaxis()
        axis.set_title(Path(relative).name)
        axis.set_xlabel("本家 / Leansail（対数軸、1 より大きいと短縮）")
        axis.grid(axis="x", alpha=0.2)
    figure.legend(
        *axes[0].get_legend_handles_labels(),
        loc="upper center",
        bbox_to_anchor=(0.5, 0.94),
        ncols=2,
    )
    figure.suptitle("公式 Lean 4.35.0 と Leansail：解析・索引準備後の要求")
    figure.text(
        0.5,
        0.015,
        "helios / Mathlib v4.35.0 / "
        f"各 {summary['counts']['upstream']['documents']['accepted']} セッション / "
        f"初回 {summary['files'][FILES[0]]['ハイライト初回']['upstream']['samples']} 標本・"
        f"反復 {summary['files'][FILES[0]]['ハイライト反復']['upstream']['samples']} 標本 / "
        "外部 CPU 上限 0.75 cores",
        ha="center",
        fontsize=10,
    )
    figure.tight_layout(rect=(0, 0.05, 1, 0.93))
    figure.savefig(output, metadata={"Date": None})
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description="本家との同一回の比較を集計する")
    parser.add_argument("source", type=Path)
    parser.add_argument("--prime", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--diagnostics-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--chart", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source.read_text())
    prime = json.loads(args.prime.read_text())
    index = json.loads(args.index.read_text())
    summary = summarize(source, index, prime)
    reference = json.loads(args.diagnostics_reference.read_text())
    summary["diagnostic_equivalence"] = diagnostics(source, reference, summary)
    result = {
        "summary": summary,
        "measurement": source,
        "index": index,
        "prime": prime,
        "diagnostic_reference": reference,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    for path, replacement in [
        (str(ROOT), "<repo>"),
        (
            str(Path.home() / ".elan/toolchains/leanprover--lean4---v4.35.0"),
            "<toolchain>",
        ),
    ]:
        serialized = serialized.replace(path, replacement)
    assert str(Path.home()) not in serialized, "個人のパスを正規化してください"
    args.output.write_text(serialized + "\n")
    args.markdown.write_text(markdown(summary))
    chart(summary, args.chart)
    print(markdown(summary))


if __name__ == "__main__":
    main()
