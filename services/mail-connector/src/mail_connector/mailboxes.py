"""Mailbox configuration in the database (P88-S4a, ADR 0244).

The runtime still works with `settings.MailboxConfig` (backends, RBAC) - this
module converts between it and the `Mailbox` table, encrypts and decrypts the
password, adopts `DMS_MAILBOXES` once, and holds the CRUD the admin API uses.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mail_connector import crypto
from mail_connector.models import InboundMessage, Mailbox, MailboxAdoption
from mail_connector.settings import MailboxConfig
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_ADOPTION_ROW_ID = 1


class MailboxNotFoundError(Exception):
    pass


class MailboxConflictError(Exception):
    """Duplicate id, or deleting a mailbox that still holds messages."""


class MailboxInvalidError(Exception):
    """The resulting configuration is incomplete or contradictory."""


def to_config(row: Mailbox, key: bytes | None) -> MailboxConfig:
    """The runtime view of a row, with the decrypted password. Raises
    `crypto.CredentialKeyError`/`DecryptionError` when it can't be read."""
    if row.password_encrypted is None:
        raise MailboxInvalidError(f"Postfach {row.id!r} hat kein Passwort")
    if key is None:
        raise crypto.CredentialKeyError("DMS_MAILBOX_CREDENTIALS_KEY ist nicht gesetzt")
    password = crypto.decrypt(row.password_encrypted, key)
    return build_config(row_values(row), password)


def row_values(row: Mailbox) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "owning_group_id": row.owning_group_id,
        "inbound_protocol": row.inbound_protocol,
        "host": row.host,
        "port": row.port,
        "username": row.username,
        "use_tls": row.use_tls,
        "imap_mailbox": row.imap_mailbox,
        "enabled": row.enabled,
    }


def build_config(values: dict, password: str | None) -> MailboxConfig:
    """Maps the flat host/port/username block onto the protocol-specific
    fields `MailboxConfig` (and with it every backend) expects, and lets
    its validator check kind/group and completeness."""
    protocol = values["inbound_protocol"]
    prefix = "pop3" if protocol == "pop3" else "imap"
    data = {
        "id": values["id"],
        "name": values["name"],
        "kind": values["kind"],
        "owning_group_id": values.get("owning_group_id"),
        "inbound_protocol": protocol,
        f"{prefix}_host": values["host"],
        f"{prefix}_port": values["port"],
        f"{prefix}_username": values["username"],
        f"{prefix}_password": password,
        f"{prefix}_use_tls": values["use_tls"],
    }
    if protocol == "imap":
        data["imap_mailbox"] = values.get("imap_mailbox") or "INBOX"
    try:
        return MailboxConfig(**data)
    except ValidationError as exc:
        messages = "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())
        raise MailboxInvalidError(messages) from exc


def _values_from_config(config: MailboxConfig) -> dict:
    prefix = config.inbound_protocol
    return {
        "id": config.id,
        "name": config.name,
        "kind": config.kind,
        "owning_group_id": config.owning_group_id,
        "inbound_protocol": config.inbound_protocol,
        "host": getattr(config, f"{prefix}_host"),
        "port": getattr(config, f"{prefix}_port"),
        "username": getattr(config, f"{prefix}_username"),
        "use_tls": getattr(config, f"{prefix}_use_tls"),
        "imap_mailbox": config.imap_mailbox,
        "enabled": True,
    }


async def list_rows(session: AsyncSession) -> list[Mailbox]:
    result = await session.execute(select(Mailbox).order_by(Mailbox.kind, Mailbox.name))
    return list(result.scalars().all())


async def get_row(session: AsyncSession, mailbox_id: str) -> Mailbox | None:
    return await session.get(Mailbox, mailbox_id)


async def adopt_from_settings(
    session: AsyncSession, configs: list[MailboxConfig], key: bytes | None
) -> list[str] | None:
    """One-time adoption of `DMS_MAILBOXES` (or the settings default) into
    the table on the first start after the upgrade. Returns the adopted ids,
    or None when adoption already happened. Without a key nothing with a
    password can be adopted: the caller logs and retries next start."""
    if await session.get(MailboxAdoption, _ADOPTION_ROW_ID) is not None:
        return None
    if configs and key is None:
        raise crypto.CredentialKeyError(
            "DMS_MAILBOX_CREDENTIALS_KEY fehlt - DMS_MAILBOXES kann nicht übernommen werden"
        )
    now = datetime.now(UTC)
    adopted = []
    for config in configs:
        if await session.get(Mailbox, config.id) is not None:
            continue
        values = _values_from_config(config)
        password = getattr(config, f"{config.inbound_protocol}_password")
        session.add(
            Mailbox(
                **values,
                password_encrypted=crypto.encrypt(password, key) if password else None,
                created_at=now,
                updated_at=now,
                updated_by="system:adopted-from-env",
            )
        )
        adopted.append(config.id)
    session.add(MailboxAdoption(id=_ADOPTION_ROW_ID, adopted_at=now, adopted_ids=adopted))
    await session.flush()
    return adopted


def resolve_password(
    row: Mailbox | None, new_password: str | None, key: bytes | None
) -> str | None:
    """The password a write ends up with: the new one if given (non-empty),
    otherwise the stored one. Write-only: an empty/omitted password keeps
    the existing secret."""
    if new_password:
        return new_password
    if row is None or row.password_encrypted is None:
        return None
    if key is None:
        raise crypto.CredentialKeyError("DMS_MAILBOX_CREDENTIALS_KEY ist nicht gesetzt")
    return crypto.decrypt(row.password_encrypted, key)


async def save(
    session: AsyncSession,
    values: dict,
    *,
    password: str | None,
    key: bytes | None,
    actor: str,
    create: bool,
) -> Mailbox:
    """Creates or replaces a mailbox. `values` holds every non-secret field;
    `password` None keeps the stored one. The merged result is validated as
    a `MailboxConfig` before anything is written."""
    row = await session.get(Mailbox, values["id"])
    if create and row is not None:
        raise MailboxConflictError(f"Postfach {values['id']!r} existiert bereits")
    if not create and row is None:
        raise MailboxNotFoundError(f"Postfach {values['id']!r} unbekannt")
    effective_password = resolve_password(row, password, key)
    build_config(values, effective_password)  # raises MailboxInvalidError
    if password and key is None:
        raise crypto.CredentialKeyError("DMS_MAILBOX_CREDENTIALS_KEY ist nicht gesetzt")

    now = datetime.now(UTC)
    if row is None:
        row = Mailbox(id=values["id"], created_at=now)
        session.add(row)
    for field, value in values.items():
        if field != "id":
            setattr(row, field, value)
    if password:
        row.password_encrypted = crypto.encrypt(password, key)
    row.updated_at = now
    row.updated_by = actor
    await session.flush()
    return row


async def message_count(session: AsyncSession, mailbox_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(InboundMessage)
            .where(InboundMessage.mailbox_id == mailbox_id)
        )
        or 0
    )


async def delete(session: AsyncSession, mailbox_id: str) -> None:
    row = await session.get(Mailbox, mailbox_id)
    if row is None:
        raise MailboxNotFoundError(f"Postfach {mailbox_id!r} unbekannt")
    count = await message_count(session, mailbox_id)
    if count:
        raise MailboxConflictError(
            f"Postfach {mailbox_id!r} enthält {count} Nachricht(en) - deaktivieren statt löschen"
        )
    await session.delete(row)
    await session.flush()
