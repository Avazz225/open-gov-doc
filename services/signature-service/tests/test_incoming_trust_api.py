"""P92-S2 end to end: offline import of the EU trusted lists, a qualified
signature recognised as QES, its ETSI report downloadable, re-validation as a
new run, the backfill, and a scheduled-style refresh from a list server."""

import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import testpki
import testtl
from fastapi.testclient import TestClient
from signature_service.main import app

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
ADMIN = {"X-DMS-Principal": "signature-service-tests"}
with open(os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf"), "rb") as _f:
    PDF = _f.read()


@pytest.fixture
def client():
    with TestClient(app, headers=ADMIN) as c:
        yield c


@pytest.fixture(scope="module")
def eu():
    tl_signer = testtl.make_tlso("DE TL signer")
    ca = testpki.make_cert("Test-QTSP Qualified CA", ca=True)
    return {
        "ca": ca,
        "tl_signer": tl_signer,
        "tl": testtl.make_tl(qualified_ca=ca, tlso=tl_signer),
        "lotl": testtl.make_lotl(
            lotl_signer=testtl.LOTL_SIGNER, tl_signer=tl_signer, tl_url="https://tl.test/TL-DE.xml"
        ),
    }


def _import(client: TestClient, xml: str, name: str = "liste.xml"):
    return client.post(
        "/incoming-signatures/trusted-lists/import",
        files={"file": (name, xml.encode(), "application/xml")},
    )


def _upload(data: bytes) -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": "Eingang QES", "created_by": "alice"},
        files={"file": ("qes.pdf", data, "application/pdf")},
        headers={"X-DMS-Principal": "alice"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["id"]


def _wait(client: TestClient, document_id: str, *, runs: int = 1, timeout: float = 45) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(
            "/incoming-signatures", params={"document_id": document_id, "history": True}
        ).json()
        if len({v["run_id"] for v in body["validations"]}) >= runs:
            return body
        time.sleep(0.3)
    raise AssertionError(f"fewer than {runs} runs for {document_id}")


def test_import_order_and_checks(client, eu):
    early = _import(client, eu["tl"])
    assert early.status_code == 422 and "Zuerst" in early.json()["detail"]

    lotl = _import(client, eu["lotl"])
    assert lotl.status_code == 200, lotl.text
    assert lotl.json()["kind"] == "lotl" and lotl.json()["sequence_number"] == 398

    forged = testtl.make_tl(qualified_ca=eu["ca"], tlso=testtl.make_tlso("Fälscher"))
    assert _import(client, forged).status_code == 422
    tl = _import(client, eu["tl"])
    assert tl.status_code == 200 and tl.json()["territory"] == "DE"
    assert tl.json()["location_uri"] == "https://tl.test/TL-DE.xml"

    status = client.get("/incoming-signatures/trusted-lists").json()
    assert [(i["kind"], i["territory"], i["source"]) for i in status["lists"]] == [
        ("lotl", "EU", "import"),
        ("tl", "DE", "import"),
    ]
    assert status["last_fetch"]["source"] == "import"
    assert status["poll_interval_seconds"] == 0

    nobody = {"X-DMS-Principal": "someone-without-role"}
    assert client.get("/incoming-signatures/trusted-lists", headers=nobody).status_code == 403


def test_qualified_signature_is_qes_with_a_report_and_can_be_revalidated(client, eu):
    assert _import(client, eu["lotl"]).status_code == 200
    assert _import(client, eu["tl"]).status_code == 200
    leaf = testtl.make_qualified_leaf("Erika Qualifiziert", eu["ca"])
    document_id = _upload(asyncio.run(testpki.sign_pdf(PDF, leaf, [eu["ca"]])))

    body = _wait(client, document_id)
    [summary] = body["summaries"]
    assert summary["best_qualification"] == "qes"
    [row] = body["validations"]
    assert row["trust_source"] == "eu_trusted_list"
    assert row["qc_type"] == "esign"
    assert row["report_available"] is True

    report = client.get(f"/incoming-signatures/reports/{row['run_id']}")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/xml")
    assert b"ValidationReport" in report.content
    assert "attachment" in report.headers["content-disposition"]

    again = client.post("/incoming-signatures/revalidate", json={"document_id": document_id})
    assert again.status_code == 202 and again.json()["version_number"] == 1
    body = _wait(client, document_id, runs=2)
    triggers = sorted({(v["trigger"], v["run_id"]) for v in body["validations"]})
    assert [t for t, _ in triggers].count("revalidation") == 1
    # The latest-only view shows just the newer run.
    latest = client.get("/incoming-signatures", params={"document_id": document_id}).json()
    assert {v["trigger"] for v in latest["validations"]} == {"revalidation"}


def test_revalidate_and_report_errors(client):
    assert (
        client.post("/incoming-signatures/revalidate", json={"document_id": "gibt-es-nicht"})
    ).status_code == 404
    assert client.get("/incoming-signatures/reports/unbekannt").status_code == 404


def test_backfill_validates_versions_missed_while_switched_off(client):
    off = {"enabled": False, "revocation_mode": "soft-fail", "outbound_fetching": False}
    assert client.put("/incoming-signatures/config", json=off).status_code == 200
    root = testpki.make_cert("Root", ca=True)
    leaf = testpki.make_cert("Nachlauf", root, ca=False)
    started = datetime.now(UTC) - timedelta(seconds=5)
    document_id = _upload(asyncio.run(testpki.sign_pdf(PDF, leaf, [root])))
    time.sleep(2)  # the event arrives and is skipped
    assert (
        client.get("/incoming-signatures", params={"document_id": document_id}).json()["summaries"]
        == []
    )
    on = dict(off, enabled=True)
    assert client.put("/incoming-signatures/config", json=on).status_code == 200

    assert client.post("/incoming-signatures/backfill", json={}).status_code == 422
    nobody = {"X-DMS-Principal": "someone-without-role"}
    assert (
        client.post(
            "/incoming-signatures/backfill",
            json={"created_from": started.isoformat()},
            headers=nobody,
        ).status_code
        == 403
    )
    started_backfill = client.post(
        "/incoming-signatures/backfill", json={"created_from": started.isoformat()}
    )
    assert started_backfill.status_code == 202, started_backfill.text
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = client.get("/incoming-signatures/backfill").json()
        if status["state"] != "running":
            break
        time.sleep(0.3)
    assert status["state"] == "done", status
    assert status["queued"] >= 1 and status["started_by"] == "signature-service-tests"

    body = _wait(client, document_id)
    assert {v["trigger"] for v in body["validations"]} == {"backfill"}


class _ListServer:
    def __init__(self):
        self.files: dict[str, str] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                body = outer.files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = body.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_refresh_fetches_from_the_list_server(client, eu):
    server = _ListServer()
    try:
        signer = testtl.make_tlso("DE TL signer")
        server.files["/TL-DE.xml"] = testtl.make_tl(qualified_ca=eu["ca"], tlso=signer, sequence=9)
        server.files["/eu-lotl.xml"] = testtl.make_lotl(
            lotl_signer=testtl.LOTL_SIGNER, tl_signer=signer, tl_url=f"{server.base}/TL-DE.xml"
        )
        incoming = app.state.incoming
        incoming.lotl_url = f"{server.base}/eu-lotl.xml"
        incoming.allow_local_test_server = True
        assert client.post("/incoming-signatures/trusted-lists/refresh").status_code == 202
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get("/incoming-signatures/trusted-lists").json()
            if status["last_fetch"] and status["last_fetch"]["finished_at"]:
                break
            time.sleep(0.3)
        assert status["last_fetch"]["source"] == "fetch"
        assert status["last_fetch"]["errors"] == []
        assert status["last_fetch"]["lists_stored"] == 2
        de = next(i for i in status["lists"] if i["territory"] == "DE")
        assert de["sequence_number"] == 9 and de["source"] == "fetch"
    finally:
        app.state.incoming.lotl_url = None
        app.state.incoming.allow_local_test_server = False
        server.server.shutdown()
