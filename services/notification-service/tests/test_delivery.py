import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from notification_service import delivery


@pytest.fixture
def webhook_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server-Konvention
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):  # Testausgabe nicht mit Zugriffs-Logs fluten
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/webhook", received
    finally:
        server.shutdown()
        thread.join()


async def test_send_webhook_delivers_json_payload(webhook_server):
    url, received = webhook_server
    await delivery.send_webhook(url, "Betreff", "Nachricht")
    assert received == [{"subject": "Betreff", "body": "Nachricht"}]


async def test_send_webhook_unreachable_url_raises_delivery_error():
    with pytest.raises(delivery.DeliveryError):
        await delivery.send_webhook("http://127.0.0.1:1/nope", "Betreff", "Nachricht")


async def test_send_email_via_mailpit_succeeds(settings):
    """Läuft gegen den echten `mailpit`-Container (infra/docker-compose.yml) -
    kein Mocking, siehe PROGRESS.md 'Tooling & Testing'."""
    await delivery.send_email(settings, "empfaenger@example.com", "Betreff", "Nachricht")


async def test_send_email_unreachable_smtp_raises_delivery_error(settings):
    settings.smtp_host = "127.0.0.1"
    settings.smtp_port = 1
    with pytest.raises(delivery.DeliveryError):
        await delivery.send_email(settings, "empfaenger@example.com", "Betreff", "Nachricht")
