"""The installation's software components (P100-S1, ADR 0271).

Collected from every running instance (`GET /component-report`, answered only for
monitoring-service) and from the frontends' served npm file, cached briefly.
Each replica is asked on its own, so a replica running a different image shows
up (`replicas_consistent`). Read-only; the CVE matching builds on it (P100-S2).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

READER_HEADERS = {"X-DMS-Principal": "monitoring-service"}


def ecosystem(component: dict) -> str:
    for prop in component.get("properties") or []:
        if prop.get("name") == "dms:ecosystem":
            return str(prop.get("value"))
    purl = component.get("purl") or ""
    return purl.split(":", 1)[1].split("/", 1)[0] if purl.startswith("pkg:") else "unknown"


def is_own(component: dict) -> bool:
    return any(
        p.get("name") == "dms:own" and p.get("value") == "true"
        for p in component.get("properties") or []
    )


@dataclass
class ServiceComponents:
    service: str
    kind: str  # "service" | "frontend"
    instances: list[str] = field(default_factory=list)
    document: dict | None = None
    fingerprints: dict[str, str] = field(default_factory=dict)
    image_file: dict | None = None
    error: str | None = None

    @property
    def components(self) -> list[dict]:
        return list((self.document or {}).get("components") or [])

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for component in self.components:
            counts[ecosystem(component)] = counts.get(ecosystem(component), 0) + 1
        return {
            "service": self.service,
            "kind": self.kind,
            "instances": len(self.instances),
            "component_count": len(self.components),
            "ecosystems": dict(sorted(counts.items())),
            "replicas_consistent": len(set(self.fingerprints.values())) <= 1,
            "matches_image": (self.image_file or {}).get("matches_image"),
            "image_created": (self.image_file or {}).get("created"),
            "error": self.error,
        }


async def _fetch_service(
    client: httpx.AsyncClient, instance
) -> tuple[object, dict | None, str | None]:
    try:
        response = await client.get(
            f"{instance.address.rstrip('/')}/component-report", headers=READER_HEADERS
        )
        if response.status_code == 404:
            return instance, None, "liefert keine Komponenten (Image vor P100)"
        response.raise_for_status()
        return instance, response.json(), None
    except (httpx.HTTPError, ValueError) as exc:
        return instance, None, f"nicht erreichbar: {type(exc).__name__}"


async def _fetch_frontend(client: httpx.AsyncClient, name: str, url: str) -> ServiceComponents:
    entry = ServiceComponents(service=name, kind="frontend", instances=[url])
    try:
        response = await client.get(url)
        response.raise_for_status()
        entry.document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        entry.error = f"nicht erreichbar: {type(exc).__name__}"
    return entry


async def collect(
    client: httpx.AsyncClient, instances: list, frontend_urls: dict[str, str]
) -> dict[str, ServiceComponents]:
    services: dict[str, ServiceComponents] = {}
    results = await asyncio.gather(*(_fetch_service(client, i) for i in instances))
    for instance, report, error in results:
        entry = services.setdefault(
            instance.service_type, ServiceComponents(service=instance.service_type, kind="service")
        )
        entry.instances.append(instance.instance_id)
        if report is None:
            entry.error = entry.error or error
            continue
        entry.fingerprints[instance.instance_id] = report.get("fingerprint", "")
        if entry.document is None:
            entry.document = report.get("document")
            entry.image_file = report.get("image_file")
            entry.error = None
    frontends = await asyncio.gather(
        *(_fetch_frontend(client, name, url) for name, url in frontend_urls.items())
    )
    for frontend in frontends:
        services[frontend.service] = frontend
    return dict(sorted(services.items()))


class Inventory:
    """A short-lived cache in front of `collect` - an inventory page or a
    search would otherwise ask every instance on every request."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._at = 0.0
        self._data: dict[str, ServiceComponents] | None = None
        self._lock = asyncio.Lock()

    async def get(self, loader, *, refresh: bool = False) -> dict[str, ServiceComponents]:
        async with self._lock:
            if refresh or self._data is None or time.monotonic() - self._at > self._ttl:
                self._data = await loader()
                self._at = time.monotonic()
            return self._data


def search(services: dict[str, ServiceComponents], query: str) -> list[dict]:
    needle = query.strip().lower()
    rows = []
    for entry in services.values():
        for component in entry.components:
            if (
                needle in component.get("name", "").lower()
                or needle in (component.get("purl") or "").lower()
            ):
                rows.append(
                    {
                        "service": entry.service,
                        "name": component.get("name"),
                        "version": component.get("version"),
                        "ecosystem": ecosystem(component),
                        "purl": component.get("purl"),
                    }
                )
    return sorted(rows, key=lambda r: (r["name"].lower(), r["version"] or "", r["service"]))


def merged_document(services: dict[str, ServiceComponents], only: str | None = None) -> dict:
    """One CycloneDX document for the installation (or one service): every
    component once, with the services that run it - the form Dependency-Track,
    Trivy & co. read."""
    by_purl: dict[str, dict] = {}
    for entry in services.values():
        if only and entry.service != only:
            continue
        for component in entry.components:
            key = component.get("purl") or f"{component.get('name')}@{component.get('version')}"
            merged = by_purl.setdefault(
                key, {**component, "properties": list(component.get("properties") or [])}
            )
            names = merged.setdefault("_services", [])
            if entry.service not in names:
                names.append(entry.service)
    components = []
    for component in by_purl.values():
        services_using = sorted(component.pop("_services"))
        component["properties"] = [
            p for p in component["properties"] if p.get("name") != "dms:services"
        ] + [{"name": "dms:services", "value": ",".join(services_using)}]
        components.append(component)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {
                "type": "application",
                "name": only or "OG Doc installation",
                "bom-ref": only or "og-doc",
            },
        },
        "components": sorted(
            components, key=lambda c: (c.get("name", "").lower(), c.get("version", ""))
        ),
    }
