"""`dms components …` (P100-S1)."""

import json

import httpx
from dms_cli.main import app

BASE = "/api/monitoring-service/components"
SUMMARY = [
    {
        "service": "document-service",
        "kind": "service",
        "instances": 2,
        "component_count": 141,
        "ecosystems": {"Debian": 87, "PyPI": 54},
        "replicas_consistent": False,
        "matches_image": True,
        "image_created": "2026-11-27T10:00:00+00:00",
        "error": None,
    }
]


class Recorder:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, dict(request.url.params)))
        status, payload = self.routes.get(
            f"{request.method} {request.url.path}", (404, {"detail": "no"})
        )
        return httpx.Response(status, json=payload)


def test_list_search_show_export(cli_home, runner, logged_in, mock_transport_factory, tmp_path):
    recorder = Recorder(
        {
            f"GET {BASE}": (200, SUMMARY),
            f"GET {BASE}/search": (
                200,
                [
                    {
                        "service": "document-service",
                        "name": "urllib3",
                        "version": "2.2.1",
                        "ecosystem": "PyPI",
                        "purl": "pkg:pypi/urllib3@2.2.1",
                    }
                ],
            ),
            f"GET {BASE}/document-service": (
                200,
                {
                    **SUMMARY[0],
                    "fingerprints": {},
                    "image_file": {"created": "x"},
                    "components": [
                        {
                            "name": "urllib3",
                            "version": "2.2.1",
                            "ecosystem": "PyPI",
                            "purl": "p",
                            "own": False,
                        },
                        {
                            "name": "openssl",
                            "version": "3.5.5",
                            "ecosystem": "Debian",
                            "purl": "q",
                            "own": False,
                        },
                    ],
                },
            ),
            f"GET {BASE}/export": (200, {"bomFormat": "CycloneDX", "components": [{"name": "x"}]}),
        }
    )
    mock_transport_factory(recorder)

    listed = runner.invoke(app, ["components", "list"])
    assert listed.exit_code == 0, listed.output
    assert "NEIN" in listed.output  # replicas differ

    found = runner.invoke(app, ["components", "search", "urllib3"])
    assert "2.2.1" in found.output
    assert recorder.calls[-1] == (f"{BASE}/search", {"q": "urllib3"})

    shown = runner.invoke(app, ["components", "show", "document-service", "--filter", "ssl"])
    assert "openssl" in shown.output and "urllib3" not in shown.output

    out = tmp_path / "bom.json"
    exported = runner.invoke(app, ["components", "export", "-o", str(out)])
    assert exported.exit_code == 0
    assert json.loads(out.read_text())["bomFormat"] == "CycloneDX"
