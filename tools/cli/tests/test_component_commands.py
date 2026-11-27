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


RESULT = {
    "query": "CVE-2099-0001",
    "source": "https://api.osv.dev",
    "records": [{"id": "GHSA-test", "summary": "urllib3 leaks", "severity": "HIGH"}],
    "affected": [
        {
            "service": "document-service",
            "component": "urllib3",
            "version": "2.2.1",
            "fixed_in": "2.3.0",
            "ecosystem": "PyPI",
            "record_id": "GHSA-test",
            "note": None,
        }
    ],
    "not_affected": [],
    "checked_services": 35,
}


def test_vulnerability_check_by_id_file_and_hand(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=RESULT)

    mock_transport_factory(handler)
    by_id = runner.invoke(app, ["vulnerabilities", "check", "CVE-2099-0001"])
    assert by_id.exit_code == 0, by_id.output
    assert "BETROFFEN" in by_id.output and "2.3.0" in by_id.output

    record = tmp_path / "osv.json"
    record.write_text(json.dumps({"id": "GHSA-test", "affected": []}))
    runner.invoke(app, ["vulnerabilities", "check", "--file", str(record)])
    runner.invoke(
        app,
        [
            "vulnerabilities",
            "check",
            "--package",
            "openssl",
            "--ecosystem",
            "Debian",
            "--fixed",
            "3.3.2-1",
        ],
    )
    assert sent[0] == {"id": "CVE-2099-0001"}
    assert sent[1]["record"]["id"] == "GHSA-test"
    assert sent[2]["manual"]["package"] == "openssl" and sent[2]["manual"]["fixed"] == "3.3.2-1"

    both = runner.invoke(app, ["vulnerabilities", "check", "CVE-1", "--package", "x"])
    assert both.exit_code == 2


SCAN = {
    "id": "s1",
    "status": "done",
    "source": "offline",
    "trigger": "manual",
    "started_at": "2026-11-27T10:00:00+00:00",
    "finished_at": "2026-11-27T10:00:05+00:00",
    "checked_services": 36,
    "error": None,
    "summary": {
        "vulnerabilities": 2,
        "fixable": 1,
        "by_severity": {"high": {"fixable": 1, "unfixed": 0}, "low": {"fixable": 0, "unfixed": 1}},
    },
    "vulnerabilities": [
        {
            "id": "CVE-2099-1",
            "severity": "high",
            "score": 7.5,
            "fixable": True,
            "summary": "urllib3 leaks",
            "installations": [
                {
                    "service": "document-service",
                    "component": "urllib3",
                    "version": "2.2.1",
                    "fixed_in": "2.3.0",
                }
            ],
        },
        {
            "id": "CVE-2099-2",
            "severity": "low",
            "score": 3.1,
            "fixable": False,
            "summary": "glibc",
            "installations": [
                {
                    "service": "auth-service",
                    "component": "libc6",
                    "version": "2.41-12",
                    "fixed_in": None,
                }
            ],
        },
    ],
    "uncovered": [{"ecosystem": "npm", "name": "react", "reason": "keine Datenbank für npm"}],
}


def test_scan_waits_and_report_filters(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "s1", "source": "offline", "status": "running"})
        return httpx.Response(200, json=SCAN)

    mock_transport_factory(handler)
    scanned = runner.invoke(app, ["vulnerabilities", "scan"])
    assert scanned.exit_code == 0, scanned.output
    assert "2 bekannte Schwachstellen (1 behebbar)" in scanned.output
    assert "keine Datenbank für npm: 1" in scanned.output
    fixable = runner.invoke(app, ["vulnerabilities", "report", "--fixable"])
    assert "CVE-2099-1" in fixable.output and "CVE-2099-2" not in fixable.output


def test_database_import_filters_the_dump(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    import zipfile

    def record(rid, eco, name):
        return {"id": rid, "affected": [{"package": {"ecosystem": eco, "name": name}}]}

    dump = tmp_path / "all.zip"
    with zipfile.ZipFile(dump, "w") as archive:
        archive.writestr("a.json", json.dumps(record("GHSA-a", "PyPI", "URLlib3")))
        archive.writestr("b.json", json.dumps(record("GHSA-b", "PyPI", "django")))
        archive.writestr("c.json", json.dumps(record("GO-c", "Go", "x")))
    uploads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/vulnerabilities/database/wanted"):
            return httpx.Response(200, json={"PyPI": ["urllib3"], "npm": ["react"]})
        if path.endswith("/vulnerabilities/database"):
            uploads.append(
                {"body": json.loads(request.content), "name": request.url.params["source_name"]}
            )
            return httpx.Response(202, json={"id": "j1", "status": "running"})
        return httpx.Response(
            200,
            json={"imports": [{"id": "j1", "status": "done", "ecosystems": {"PyPI": 1}}]},
        )

    mock_transport_factory(handler)
    result = runner.invoke(app, ["vulnerabilities", "database", "import", str(dump)])
    assert result.exit_code == 0, result.output
    [upload] = uploads
    assert upload["name"] == "all.zip"
    assert [r["id"] for r in upload["body"]["records"]] == ["GHSA-a"]
    assert upload["body"]["ecosystems"] == {"PyPI": 2, "Go": 1}
    assert "eingespielt" in result.output
