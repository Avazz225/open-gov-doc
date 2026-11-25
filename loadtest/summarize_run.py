"""Summary of one k6 run from its JSON output (P95-S1e): requests, rate,
error share and goodput over the whole run, and p50/p95/p99 per action -
so two runs can be compared with the same arithmetic.

Usage: uv run python loadtest/summarize_run.py results/run-<ts>.json [--json out.json]
"""

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime


def quantile(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=1000, method="inclusive")[round(q * 1000) - 1]


def summarize(path: str) -> dict:
    durations: dict[str, list[float]] = defaultdict(list)
    good = total_goodput = 0
    failed = total_failed = 0
    first = last = None
    with open(path) as source:
        for line in source:
            if '"Point"' not in line:
                continue
            point = json.loads(line)
            metric, data = point["metric"], point["data"]
            if metric == "action_duration":
                durations[data["tags"].get("action", "?")].append(data["value"])
                stamp = data["time"]
                first = stamp if first is None or stamp < first else first
                last = stamp if last is None or stamp > last else last
            elif metric == "goodput":
                total_goodput += 1
                good += data["value"]
            elif metric == "http_req_failed":
                total_failed += 1
                failed += data["value"]
    seconds = (
        datetime.fromisoformat(last).timestamp() - datetime.fromisoformat(first).timestamp()
        if first and last
        else 0
    )
    requests = sum(len(v) for v in durations.values())
    return {
        "requests": requests,
        "per_second": requests / seconds if seconds else 0.0,
        "error_share": failed / total_failed if total_failed else 0.0,
        "goodput": good / total_goodput if total_goodput else 0.0,
        "actions": {
            action: {
                "count": len(values),
                "p50": quantile(values, 0.5),
                "p95": quantile(values, 0.95),
                "p99": quantile(values, 0.99),
            }
            for action, values in sorted(durations.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--json")
    args = parser.parse_args()
    summary = summarize(args.run)
    print(
        f"{summary['requests']} requests, {summary['per_second']:.0f}/s, "
        f"errors {summary['error_share']:.2%}, goodput {summary['goodput']:.2%}"
    )
    print(f"{'action':<24}{'count':>8}{'p50 ms':>10}{'p95 ms':>10}{'p99 ms':>10}")
    for action, row in summary["actions"].items():
        print(
            f"{action:<24}{row['count']:>8}{row['p50']:>10.1f}{row['p95']:>10.1f}{row['p99']:>10.1f}"
        )
    if args.json:
        with open(args.json, "w") as target:
            json.dump(summary, target, indent=2)


if __name__ == "__main__":
    main()
