import httpx
from monitoring_service.clients import RegistryClient


async def test_list_active_instances_filters_out_instances_without_sensors():
    """Reales, live entdecktes Problem (P11-S1-Verifikation): die meisten
    registrierten Services haben (noch) keinen /metrics-Endpunkt (kein
    Vollretrofit) - ein Scrape-Versuch gegen sie waere ein strukturell
    erwarteter 404, kein echter Ausfall, und wuerde
    `monitoring_scrape_failures_total` mit Falschmeldungen fluten."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "instance_id": "document-service-1",
                    "service_type": "document-service",
                    "address": "http://document-service:8000",
                    "healthy": True,
                    "status": "active",
                    "sensors": [
                        {
                            "name": "document.upload.duration",
                            "group": "performance",
                            "cost": "expensive",
                            "description": "x",
                        }
                    ],
                },
                {
                    "instance_id": "auth-service-1",
                    "service_type": "auth-service",
                    "address": "http://auth-service:8000",
                    "healthy": True,
                    "status": "active",
                    "sensors": [],
                },
                {
                    "instance_id": "draining-1",
                    "service_type": "document-service",
                    "address": "http://old-document-service:8000",
                    "healthy": True,
                    "status": "draining",
                    "sensors": [{"name": "x", "group": "y", "cost": "cheap", "description": "z"}],
                },
                {
                    "instance_id": "unhealthy-1",
                    "service_type": "document-service",
                    "address": "http://dead-document-service:8000",
                    "healthy": False,
                    "status": "active",
                    "sensors": [{"name": "x", "group": "y", "cost": "cheap", "description": "z"}],
                },
            ],
        )

    client = RegistryClient(
        "http://registry-service",
        client=httpx.AsyncClient(
            base_url="http://registry-service", transport=httpx.MockTransport(handler)
        ),
    )
    instances = await client.list_active_instances()
    assert [i.instance_id for i in instances] == ["document-service-1"]
