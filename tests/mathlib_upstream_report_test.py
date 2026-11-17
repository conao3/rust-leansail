import copy
import unittest

from mathlib import FILES, METHODS
from mathlib_upstream import digest
from mathlib_upstream_report import diagnostic_order, summarize


def fixture():
    source = {
        "repetitions": 5,
        "samples": 2,
        "profile": False,
        "perf": False,
        "mathlib_revision": "mathlib",
        "host_sha256": "host",
        "backend_sha256": "backend",
        "lean_git_hash": "lean",
        "machine": {"hostname": "helios"},
        "max_outside_cores": 0.75,
        "startup_only": False,
        "sessions": [],
        "preflight_skips": [],
        "interrupted_sessions": [],
    }
    for repetition in range(5):
        for label in ["upstream", "leansail"]:
            scale = 2 if label == "upstream" else 1
            source["sessions"].append(
                {
                    "server": label,
                    "repetition": repetition,
                    "accepted": True,
                    "source_revision": "revision",
                    "source_hashes": {"source": "hash"},
                    "index_source": "cache" if label == "leansail" else None,
                    "startup_first_diagnostic_ms": scale * 10,
                    "index_ready_ms": scale * 20,
                    "resources": {"outside_cpu_cores": 0.1},
                    "memory": {
                        phase: [{"pss_kib": 1024}] * (4 if scale == 2 else 5)
                        for phase in ["three_documents_parsed", "three_documents_warm"]
                    },
                    "files": [
                        {
                            "path": path,
                            "fixture_sha256": "fixture",
                            "diagnostics_sha256": "diagnostics",
                            "completion_resolved_sha256": "resolved",
                            "requests": {
                                method: {
                                    "first": {"ms": scale * 10, "sha256": method},
                                    "repeated": [
                                        {"ms": scale * ms, "sha256": method}
                                        for ms in [1, 3]
                                    ],
                                }
                                for method in METHODS + ["workspace/symbol"]
                            },
                        }
                        for path in FILES
                    ],
                }
            )
    index = copy.deepcopy(source)
    index["startup_only"] = True
    for session in index["sessions"]:
        session["files"] = []
    prime = copy.deepcopy(source)
    prime["sessions"] = [
        {
            "server": "leansail",
            "repetition": 0,
            "accepted": True,
            "resources": {"outside_cpu_cores": 0.1},
            "events": [{"event": "index_saved"}],
            "index_ready_ms": 20,
            "cache_ready_ms": 30,
        }
    ]
    return source, index, prime


class ComparisonTests(unittest.TestCase):
    def test_only_diagnostic_array_order_is_normalized(self):
        first = [{"message": "警告", "severity": 2}, {"message": "情報", "severity": 3}]
        second = list(reversed(first))
        result = diagnostic_order({digest(first), digest(second)}, [first, second])
        self.assertEqual(result["sha256"], digest(second))
        changed = [{"message": "別の警告", "severity": 2}]
        with self.assertRaises(AssertionError):
            diagnostic_order({digest(first), digest(changed)}, [first, changed])
        with self.assertRaises(AssertionError):
            diagnostic_order({digest(first), digest(second)}, [first])

    def test_diagnostic_notifications_are_separate_from_request_results(self):
        source, index, prime = fixture()
        source["sessions"][0]["files"][0]["diagnostics_sha256"] = "途中の通知"
        summary = summarize(source, index, prime)
        self.assertEqual(
            set(summary["diagnostic_notification_sha256"][FILES[0]]["upstream"]),
            {"diagnostics", "途中の通知"},
        )
        self.assertEqual(
            summary["equivalence"][FILES[0]][METHODS[0]]["bodies_checked"], 30
        )

    def test_raw_samples_and_pss_totals(self):
        summary = summarize(*fixture())
        metric = summary["files"][FILES[0]]["補完反復"]
        self.assertEqual(metric["upstream"]["median"], 4)
        self.assertEqual(metric["upstream"]["p95"], 6)
        self.assertEqual(metric["upstream"]["samples"], 10)
        self.assertEqual(metric["ratio_median"], 2)
        pss = summary["session_metrics"]["3 文書解析直後の全 PSS"]
        self.assertEqual(pss["upstream"]["median"], 4)
        self.assertEqual(pss["leansail"]["median"], 5)
        self.assertEqual(pss["ratio_median"], 0.8)

    def test_rejected_outlier_is_excluded_and_accounted(self):
        source, index, prime = fixture()
        outlier = copy.deepcopy(source["sessions"][0])
        outlier["accepted"] = False
        outlier["resources"]["outside_cpu_cores"] = 1
        outlier["startup_first_diagnostic_ms"] = 999999
        source["sessions"].append(outlier)
        summary = summarize(source, index, prime)
        self.assertEqual(summary["counts"]["upstream"]["documents"]["rejected"], 1)
        self.assertEqual(
            summary["session_metrics"]["起動から最初の非空診断"]["upstream"]["p95"],
            20,
        )
        outlier["files"][0]["requests"][METHODS[0]]["repeated"][0]["sha256"] = (
            "異なる本文"
        )
        with self.assertRaises(AssertionError):
            summarize(source, index, prime)

    def test_incomplete_mixed_build_and_cache_miss_are_rejected(self):
        for change in ["sessions", "backend_sha256", "index_source"]:
            source, index, prime = fixture()
            if change == "sessions":
                index["sessions"].pop()
            elif change == "index_source":
                source["sessions"][1]["index_source"] = "ilean"
            else:
                prime[change] = "異なるビルド"
            with self.assertRaises(AssertionError):
                summarize(source, index, prime)


if __name__ == "__main__":
    unittest.main()
