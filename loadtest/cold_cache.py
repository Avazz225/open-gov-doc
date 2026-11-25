"""Cold permission cache on a large folder (P95-S1, found in P92-S4).

A folder with N children; for fresh principals the first filtered listing
(folder-service -> permission-service `POST /check/batch` over every child)
is measured against the second one, and again after a role assignment to an
unrelated principal. Until P95-S1d that bumped the global cache generation and
made every principal cold; since then (ADR 0263) it only touches that
principal, so the second round should stay warm. Talks to the services
directly (local ports), not through the gateway. Cleans up after itself.

Usage: uv run python loadtest/cold_cache.py [--children 1000] [--principals 5]
"""

import argparse
import statistics
import time
import uuid

import httpx

FOLDER = "http://localhost:8008"
PERMISSION = "http://localhost:8004"
ADMIN = {"X-DMS-Principal": "2"}


def timed(http: httpx.Client, url: str, headers: dict) -> tuple[float, httpx.Response]:
    start = time.perf_counter()
    response = http.get(url, headers=headers)
    return (time.perf_counter() - start) * 1000, response


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--children", type=int, default=1000)
    parser.add_argument("--principals", type=int, default=5)
    args = parser.parse_args()
    http = httpx.Client(timeout=120)
    tag = uuid.uuid4().hex[:8]
    parent = http.post(
        f"{FOLDER}/folders", json={"name": f"p95s1-cold-{tag}", "parent_id": "root"}, headers=ADMIN
    ).json()
    try:
        start = time.perf_counter()
        for i in range(args.children):
            http.post(
                f"{FOLDER}/folders",
                json={"name": f"kind-{i:04d}", "parent_id": parent["id"]},
                headers=ADMIN,
            ).raise_for_status()
        print(f"created {args.children} children in {time.perf_counter() - start:.1f} s")
        url = f"{FOLDER}/folders/{parent['id']}/children"

        principals = [f"p95s1-cold-{tag}-{n}" for n in range(args.principals)]

        def round_for(label: str) -> None:
            cold, warm = [], []
            for principal in principals:
                who = {"X-DMS-Principal": principal}
                ms, response = timed(http, url, who)
                assert response.status_code == 200 and len(response.json()) == args.children
                cold.append(ms)
                warm.append(timed(http, url, who)[0])
            print(
                f"{label:<22} cold median {statistics.median(cold):7.0f} ms (max {max(cold):.0f})"
                f"   warm median {statistics.median(warm):6.0f} ms"
            )

        round_for("fresh principals")
        # A role assignment to someone else - the same, now warm principals
        # are measured again.
        roles = http.get(f"{PERMISSION}/roles").json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-monitoring")
        grant = http.post(
            f"{PERMISSION}/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": f"p95s1-cold-{tag}-bump",
                "role_id": role_id,
                "resource_id": "root",
            },
        ).json()["role_assignment"]["id"]
        http.delete(f"{PERMISSION}/role-assignments/{grant}", headers=ADMIN)
        round_for("after unrelated grant")
    finally:
        http.post(f"{FOLDER}/folders/{parent['id']}/trash", headers=ADMIN, json={})


if __name__ == "__main__":
    main()
