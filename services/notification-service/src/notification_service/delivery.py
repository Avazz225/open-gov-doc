"""Pure delivery functions, isolated like `spiff_adapter.py` in workflow-service -
`repository.py` knows neither `aiosmtplib` nor `httpx` directly. Each raises
`DeliveryError` on a delivery failure; the caller translates that into `status="failed"`.
No retry (concept 7.1/P6-S2, see ADR 0020 and "Open Points" in
docs/services/notification-service.md) - a single failed attempt remains
as such."""

from email.message import EmailMessage

import aiosmtplib
import httpx

from notification_service.settings import Settings


class DeliveryError(Exception):
    """Delivery (email or webhook) has failed."""


async def send_email(settings: Settings, recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from_address
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    try:
        client = aiosmtplib.SMTP(
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            start_tls=settings.smtp_use_tls,
        )
        async with client:
            if settings.smtp_username is not None:
                await client.login(settings.smtp_username, settings.smtp_password or "")
            await client.send_message(message)
    except aiosmtplib.SMTPException as exc:
        raise DeliveryError(f"E-Mail-Zustellung an {recipient!r} fehlgeschlagen: {exc}") from exc
    except OSError as exc:
        raise DeliveryError(f"SMTP-Server nicht erreichbar: {exc}") from exc


async def send_webhook(recipient: str, subject: str, body: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(recipient, json={"subject": subject, "body": body})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DeliveryError(f"Webhook-Zustellung an {recipient!r} fehlgeschlagen: {exc}") from exc
