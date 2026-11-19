import unittest

from bench_mathlib_upstream import report
from mathlib import FILES


class ReportTest(unittest.TestCase):
    def test_complete_comparison(self):
        data = {
            "repetitions": 1,
            "samples": 1,
            "lean_git_hash": "lean-hash",
            "machine": {"platform": "Linux"},
            "cpu_count": 8,
            "host_info": {"cpu": "CPU", "memory_kib": 1024},
            "max_outside_cores": 0.75,
            "preflight_skips": [],
            "interrupted_sessions": [],
            "sessions": [],
        }
        for label, duration in [("upstream", 4), ("leansail", 2)]:
            data["sessions"].append(
                {
                    "server": label,
                    "accepted": True,
                    "source_revision": "revision",
                    "files": [
                        {
                            "path": path,
                            "requests": {
                                "textDocument/hover": {
                                    "first": {"ms": duration},
                                    "repeated": [{"ms": duration}],
                                }
                            },
                        }
                        for path in FILES
                    ],
                }
            )
        markdown = report(data)
        self.assertEqual(
            markdown.count("| 4.000 / 4.000 | 2.000 / 2.000 | 2.00 / 2.00 |"), 6
        )
        self.assertIn("Preflight skips: 0", markdown)

    def test_empty_comparison(self):
        with self.assertRaisesRegex(ValueError, "Incomplete comparison"):
            report({"sessions": [], "repetitions": 1})

    def test_rejected_comparison(self):
        with self.assertRaisesRegex(ValueError, "Incomplete comparison"):
            report(
                {
                    "sessions": [{"server": "upstream", "accepted": False}],
                    "repetitions": 1,
                }
            )


if __name__ == "__main__":
    unittest.main()
