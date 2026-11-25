"""Per-service ranking for one load-test window (P95-S1).

Usage:
  uv run python loadtest/rank_services.py --start <unix> --end <unix> [--rest-start <unix>]
         [--traces 400] [--out results/ranking-<name>.json]

Sources (all already running in the dev stack):
- Prometheus: cadvisor CPU/RSS per container, the services' own
  `http_requests_total` and `http_request_duration_seconds` histograms
  (scraped through monitoring-service);
- Tempo: a sample of the run's traces (sampled at the configured ratio),
  broken down per hop: each service's own time (server span minus its
  outgoing calls), its DB time and its calls to others. Only the synchronous
  part of a request counts (cut at CONSUMER spans); event handlers are
  reported separately.

Two rankings come out, deliberately apart (IMPLEMENTATION_PLAN P95-S1):
- cost per request: CPU-seconds and RSS per 1,000 requests the service served;
- latency share: the service's own (non-waiting) time as a share of the
  end-to-end time of the sampled requests, plus how much of its time is DB.
A service can be slow because it waits; a rewrite doesn't fix waiting.
"""

import argparse
import base64
import json
from collections import defaultdict

import httpx

PROM = "http://localhost:9090"
TEMPO = "http://localhost:3200"
CLIENT = ("SPAN_KIND_CLIENT", 3)
PRODUCER = ("SPAN_KIND_PRODUCER", 4)
CONSUMER = ("SPAN_KIND_CONSUMER", 5)
SERVER = ("SPAN_KIND_SERVER", 2)

http = httpx.Client(timeout=60)


def prom(query: str, at: int) -> list[dict]:
    response = http.get(f"{PROM}/api/v1/query", params={"query": query, "time": at})
    response.raise_for_status()
    return response.json()["data"]["result"]


def service_of_container(name: str) -> str:
    # dms-document-service-1 -> document-service; the extra instances of
    # P95-S2b's compose profile (dms-permission-service-replica-2) count
    # toward their service.
    return name.removeprefix("dms-").rsplit("-", 1)[0].removesuffix("-replica")


def resources(start: int, end: int, rest_start: int | None) -> dict[str, dict]:
    window = f"{end - start}s"
    out: dict[str, dict] = defaultdict(dict)
    for row in prom(
        f'sum by (name) (increase(container_cpu_usage_seconds_total{{name=~"dms-.+"}}[{window}]))',
        end,
    ):
        # Summed over every instance of the service (P95-S2c).
        data = out[service_of_container(row["metric"]["name"])]
        data["cpu_seconds"] = data.get("cpu_seconds", 0.0) + float(row["value"][1])
        data["instances"] = data.get("instances", 0) + 1
    for row in prom(
        f'max by (name) (max_over_time(container_memory_rss{{name=~"dms-.+"}}[{window}]))', end
    ):
        # Per instance: the largest one.
        data = out[service_of_container(row["metric"]["name"])]
        data["rss_peak_mb"] = max(data.get("rss_peak_mb", 0.0), float(row["value"][1]) / 2**20)
    if rest_start:
        rest_window = f"{start - rest_start}s"
        for row in prom(
            f'avg by (name) (avg_over_time(container_memory_rss{{name=~"dms-.+"}}[{rest_window}]))',
            start,
        ):
            out[service_of_container(row["metric"]["name"])]["rss_rest_mb"] = (
                float(row["value"][1]) / 2**20
            )
    for row in prom(f"sum by (service) (increase(http_requests_total[{window}]))", end):
        service = row["metric"].get("service")
        if service:
            out[service]["requests"] = float(row["value"][1])
    for data in out.values():
        requests = data.get("requests") or 0
        if requests >= 100 and "cpu_seconds" in data:
            data["cpu_seconds_per_1k_requests"] = data["cpu_seconds"] / requests * 1000
    return out


def route_latencies(start: int, end: int, top: int = 40) -> list[dict]:
    window = f"{end - start}s"
    counts = {
        (r["metric"].get("service"), r["metric"].get("route")): float(r["value"][1])
        for r in prom(
            f"sum by (service, route) (increase(http_request_duration_seconds_count[{window}]))",
            end,
        )
    }
    rows = []
    for q in (0.5, 0.99):
        query = (
            f"histogram_quantile({q}, sum by (service, route, le) "
            f"(increase(http_request_duration_seconds_bucket[{window}])))"
        )
        for r in prom(query, end):
            key = (r["metric"].get("service"), r["metric"].get("route"))
            value = r["value"][1]
            if value in ("NaN", "+Inf"):
                continue
            rows.append((key, q, float(value) * 1000))
    merged: dict = defaultdict(dict)
    for key, q, ms in rows:
        merged[key]["p50_ms" if q == 0.5 else "p99_ms"] = round(ms, 1)
    result = [
        {"service": k[0], "route": k[1], "requests": int(counts.get(k, 0)), **v}
        for k, v in merged.items()
        if counts.get(k, 0) >= 50
    ]
    return sorted(result, key=lambda r: -r["requests"])[:top]


def _id(value):
    if not value:
        return None
    if len(value) in (16, 32) and all(c in "0123456789abcdef" for c in value.lower()):
        return value.lower()
    return base64.b64decode(value).hex()


def spans_of(trace_id: str) -> list[dict]:
    body = http.get(f"{TEMPO}/api/traces/{trace_id}").json()
    out = []
    for batch in body.get("batches", []):
        attrs = {a["key"]: list(a["value"].values())[0] for a in batch["resource"]["attributes"]}
        for scope in batch.get("scopeSpans", []):
            for s in scope["spans"]:
                sattrs = {a["key"]: list(a["value"].values())[0] for a in s.get("attributes", [])}
                out.append(
                    {
                        "service": attrs.get("service.name"),
                        "kind": s.get("kind", ""),
                        "id": _id(s["spanId"]),
                        "parent": _id(s.get("parentSpanId")),
                        "start": int(s["startTimeUnixNano"]),
                        "end": int(s["endTimeUnixNano"]),
                        "attrs": sattrs,
                        "scope": scope.get("scope", {}).get("name", ""),
                        "name": s["name"],
                    }
                )
    return out


def breakdown(spans: list[dict]) -> tuple[float, dict]:
    """End-to-end ms of the synchronous part, and per service own/db/wait ms."""
    ids = {s["id"] for s in spans}
    children = defaultdict(list)
    for s in spans:
        children[s["parent"]].append(s)
    roots = [s for s in spans if s["parent"] not in ids]
    if not roots:
        return 0.0, {}
    root = max(roots, key=lambda s: s["end"] - s["start"])
    total = (root["end"] - root["start"]) / 1e6
    ms = lambda s: (s["end"] - s["start"]) / 1e6  # noqa: E731
    parts: dict = defaultdict(lambda: defaultdict(float))
    stack = [root]
    sync = []
    while stack:
        s = stack.pop()
        if s["kind"] in CONSUMER:
            continue
        sync.append(s)
        stack.extend(children[s["id"]])
    for s in sync:
        is_server = s["kind"] in SERVER or (
            s["scope"].endswith("fastapi") and s["parent"] not in ids
        )
        if not is_server:
            continue
        inner, outgoing = list(children[s["id"]]), []
        while inner:
            c = inner.pop()
            if c["kind"] in CLIENT or c["kind"] in PRODUCER:
                outgoing.append(c)
            elif not (c["kind"] in SERVER or c["kind"] in CONSUMER):
                inner.extend(children[c["id"]])
        db = sum(
            ms(c)
            for c in outgoing
            if "db.system" in c["attrs"] or c["scope"].endswith("sqlalchemy")
        )
        wait = sum(ms(c) for c in outgoing) - db
        parts[s["service"]]["own"] += ms(s) - db - wait
        parts[s["service"]]["calls"] += 1
        parts[s["service"]]["db"] += db
        parts[s["service"]]["wait"] += wait
    return total, parts


def trace_shares(start: int, end: int, limit: int) -> dict:
    found = (
        http.get(
            f"{TEMPO}/api/search",
            params={
                "tags": "service.name=gateway-service",
                "start": start,
                "end": end,
                "limit": limit,
            },
        )
        .json()
        .get("traces", [])
    )
    totals = defaultdict(lambda: defaultdict(float))
    e2e = 0.0
    used = 0
    for t in found:
        total, parts = breakdown(spans_of(t["traceID"]))
        if not total:
            continue
        used += 1
        e2e += total
        for service, p in parts.items():
            for k, v in p.items():
                totals[service][k] += v
    shares = {}
    for service, p in totals.items():
        busy = p["own"] + p["db"]
        shares[service] = {
            "own_share_of_e2e": round(p["own"] / e2e, 4) if e2e else None,
            "db_share_of_e2e": round(p["db"] / e2e, 4) if e2e else None,
            "db_share_of_own_work": round(p["db"] / busy, 3) if busy else None,
            "own_ms_per_call": round(p["own"] / p["calls"], 2) if p["calls"] else None,
            "db_ms_per_call": round(p["db"] / p["calls"], 2) if p["calls"] else None,
            "calls_per_request": round(p["calls"] / used, 2) if used else None,
        }
    return {
        "traces": used,
        "e2e_ms_mean": round(e2e / used, 1) if used else None,
        "services": shares,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--rest-start", type=int)
    parser.add_argument("--traces", type=int, default=400)
    parser.add_argument("--out")
    args = parser.parse_args()
    report = {
        "window": {"start": args.start, "end": args.end},
        "resources": resources(args.start, args.end, args.rest_start),
        "routes": route_latencies(args.start, args.end),
        "traces": trace_shares(args.start, args.end, args.traces),
    }
    text = json.dumps(report, indent=1, default=str)
    if args.out:
        open(args.out, "w").write(text)
    print_report(report)


def print_report(report: dict) -> None:
    res = report["resources"]
    ranked = sorted(
        (s for s in res if "cpu_seconds_per_1k_requests" in res[s]),
        key=lambda s: -res[s]["cpu_seconds_per_1k_requests"],
    )
    print("== cost: CPU-s per 1,000 requests served (and RSS)")
    for s in ranked:
        r = res[s]
        print(
            f"  {s:<30} {r['cpu_seconds_per_1k_requests']:7.2f}  req {int(r['requests']):>7}  "
            f"rss rest {r.get('rss_rest_mb', 0):6.0f} MB  peak {r.get('rss_peak_mb', 0):6.0f} MB"
        )
    tr = report["traces"]
    sh = tr["services"]
    print(f"== latency share ({tr['traces']} traces, e2e mean {tr['e2e_ms_mean']} ms)")
    for s in sorted(sh, key=lambda s: -(sh[s]["own_share_of_e2e"] or 0)):
        v = sh[s]
        print(
            f"  {s:<30} own {v['own_share_of_e2e']:.1%}  db {v['db_share_of_e2e']:.1%}  "
            f"db of work {v['db_share_of_own_work']:.0%}  own {v['own_ms_per_call']} ms/call  "
            f"db {v['db_ms_per_call']} ms/call  {v['calls_per_request']} calls/req"
        )


if __name__ == "__main__":
    main()
