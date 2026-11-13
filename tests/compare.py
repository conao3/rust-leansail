import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    candidate = json.loads(args.candidate.read_text())
    for field in ["initialize_result", "responses", "regression", "workload"]:
        if baseline[field] != candidate[field]:
            raise AssertionError(f"baseline and candidate differ: {field}")
    if not candidate["regression"]:
        raise AssertionError("comparison requires regression checks")
    report = {
        "lean_version": candidate["lean_version"],
        "same_initialize_response": True,
        "same_responses": list(candidate["responses"]),
        "regression": candidate["regression"],
        "declarations": candidate["workload"]["declarations"],
        "samples": candidate["workload"]["samples"],
        "timings": {
            method: {
                "baseline": baseline["timings"][method],
                "candidate": candidate["timings"][method],
            }
            for method in candidate["timings"]
        },
        "edit_ms": {"baseline": baseline["edit_ms"], "candidate": candidate["edit_ms"]},
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        f"Identical initialization, {len(candidate['responses'])} responses and regression results"
    )


if __name__ == "__main__":
    main()
