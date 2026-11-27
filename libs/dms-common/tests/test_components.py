"""Software components of an image (P100-S1, ADR 0271)."""

import json

from dms_common import components
from dms_common.settings import BaseServiceSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient

DPKG = """Package: openssl
Status: install ok installed
Architecture: amd64
Version: 3.0.15-1~deb12u1

Package: removed-thing
Status: deinstall ok config-files
Version: 1.0

Package: libc6
Status: install ok installed
Architecture: amd64
Version: 2.36-9+deb12u9
Description: GNU C Library
 continuation line: not a field
"""


def test_dpkg_status_lists_installed_packages_only():
    parsed = components._parse_dpkg_status(DPKG)
    assert [(c["name"], c["version"]) for c in parsed] == [
        ("libc6", "2.36-9+deb12u9"),
        ("openssl", "3.0.15-1~deb12u1"),
    ]
    assert parsed[1]["purl"] == "pkg:deb/debian/openssl@3.0.15-1~deb12u1?arch=amd64"


def test_document_holds_python_and_os_components(tmp_path):
    status = tmp_path / "status"
    status.write_text(DPKG)
    document = components.build_document("example-service", status_file=status)
    assert document["bomFormat"] == "CycloneDX" and document["specVersion"] == "1.6"
    assert document["metadata"]["component"]["name"] == "example-service"
    by_name = {c["name"].lower(): c for c in document["components"]}
    fastapi = by_name["fastapi"]
    assert fastapi["purl"].startswith("pkg:pypi/fastapi@")
    assert {"name": "dms:ecosystem", "value": "PyPI"} in fastapi["properties"]
    own = by_name["dms-common"]
    assert {"name": "dms:own", "value": "true"} in own["properties"]
    assert "openssl" in by_name


def test_fingerprint_ignores_time_and_serial_and_compare_names_differences(tmp_path):
    status = tmp_path / "status"
    status.write_text(DPKG)
    first = components.build_document("x", status_file=status)
    second = components.build_document("x", status_file=status)
    assert first["serialNumber"] != second["serialNumber"]
    assert components.fingerprint(first) == components.fingerprint(second)

    image = json.loads(json.dumps(first))
    image["components"] = [c for c in image["components"] if c["name"] != "openssl"]
    result = components.compare(image, second)
    assert result["matches_image"] is False
    assert result["only_at_runtime"] == ["pkg:deb/debian/openssl@3.0.15-1~deb12u1?arch=amd64"]
    assert result["only_in_image"] == []


def test_main_writes_the_file(tmp_path):
    out = tmp_path / "META-INF" / "components.cdx.json"
    assert components.main(["--name", "probe-service", "--out", str(out)]) == 0
    written = json.loads(out.read_text())
    assert written["metadata"]["component"]["name"] == "probe-service"
    assert written["components"]


class _Settings(BaseServiceSettings):
    service_name: str = "example-service"


def test_endpoint_answers_monitoring_only_and_compares_with_the_image_file(tmp_path, monkeypatch):
    from dms_common import otel

    image_file = tmp_path / "components.cdx.json"
    components.main(["--name", "example-service", "--out", str(image_file)])
    monkeypatch.setattr(components, "COMPONENTS_FILE", image_file)
    monkeypatch.setattr(components.runtime_report, "__defaults__", (image_file,))
    app = FastAPI()
    otel._register_components_endpoint(app, _Settings(_env_file=None))
    client = TestClient(app)

    assert client.get("/component-report").status_code == 403
    assert client.get("/component-report", headers={"X-DMS-Principal": "alice"}).status_code == 403
    report = client.get(
        "/component-report", headers={"X-DMS-Principal": "monitoring-service"}
    ).json()
    assert report["service"] == "example-service"
    assert report["image_file"]["matches_image"] is True
    assert report["image_file"]["fingerprint"] == report["fingerprint"]
