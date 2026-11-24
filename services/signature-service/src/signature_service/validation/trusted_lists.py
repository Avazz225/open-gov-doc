"""EU trusted lists for incoming-signature validation (P92-S2, ADR 0248
Decision 3).

The EU list of the lists (LOTL) names every member state's trusted list (TL)
and the certificates that sign it. Only this source can say "qualified".

- **Validation before storage.** The LOTL is checked against its signers -
  pyHanko's bundled, last known set, or an operator override - and each TL
  against the signers the LOTL names for it. Only lists that pass are stored;
  the database then serves as the offline cache.
- **Offline installations** import the same XML files by hand (LOTL first,
  then TLs); the checks are identical.
- **Building the registry** from stored lists parses them again without
  re-checking the signatures (they were checked on the way in) and runs in a
  worker thread, once per change of the stored set.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

import aiohttp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pyhanko.sign.validation.qualified import eutl_fetch, eutl_parse
from pyhanko.sign.validation.qualified.tsp import TSPRegistry

logger = logging.getLogger(__name__)

LOTL_TERRITORY = "EU"
EU_LOTL_URL = eutl_fetch.EU_LOTL_LOCATION


class TrustedListError(ValueError):
    pass


@dataclass(frozen=True)
class ListInfo:
    kind: str  # "lotl" | "tl"
    territory: str
    sequence_number: int | None
    issued_at: datetime | None
    next_update: datetime | None
    sha256: str


@dataclass(frozen=True)
class CheckedList:
    info: ListInfo
    xml: str
    location_uri: str | None


def describe(xml: str) -> ListInfo:
    """Kind, territory, sequence number and dates of a list, unchecked."""
    try:
        info = eutl_parse._raw_tl_parse(xml).scheme_information  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same answer
        raise TrustedListError(f"Keine lesbare Vertrauensliste: {exc}") from exc
    if info is None:
        raise TrustedListError("Vertrauensliste ohne SchemeInformation")

    def _text(value) -> str:
        # xsdata wraps some simple elements in their own classes (`.value`).
        value = getattr(value, "value", value)
        return str(value) if value is not None else ""

    list_type = _text(info.tsltype)
    territory = _text(info.scheme_territory).upper()
    kind = "lotl" if list_type.endswith("EUlistofthelists") else "tl"

    def _dt(value):
        return value.to_datetime() if value is not None else None

    return ListInfo(
        kind=kind,
        territory=LOTL_TERRITORY if kind == "lotl" else territory,
        sequence_number=int(_text(info.tslsequence_number)) if info.tslsequence_number else None,
        issued_at=_dt(info.list_issue_date_time),
        next_update=_dt(info.next_update.date_time) if info.next_update else None,
        sha256=hashlib.sha256(xml.encode()).hexdigest(),
    )


def lotl_signers(override_pem: str | None) -> list[asn1_x509.Certificate]:
    """The certificates a LOTL must be signed with: an operator override
    (PEM, one or more), else pyHanko's bundled last known set."""
    if not override_pem:
        return eutl_parse.latest_known_lotl_tlso_certs()
    certs = x509.load_pem_x509_certificates(override_pem.encode())
    return [asn1_x509.Certificate.load(c.public_bytes(serialization.Encoding.DER)) for c in certs]


def check_lotl(xml: str, signers: list[asn1_x509.Certificate]) -> eutl_parse.LOTLParseResult:
    info = describe(xml)
    if info.kind != "lotl":
        raise TrustedListError("Datei ist keine EU-Liste der Listen (LOTL)")
    try:
        return eutl_parse.validate_and_parse_lotl(xml, signers)
    except Exception as exc:  # noqa: BLE001
        raise TrustedListError(f"Signatur der Liste der Listen ungültig: {exc}") from exc


def _tl_reference(lotl: eutl_parse.LOTLParseResult, territory: str) -> eutl_parse.TLReference:
    for ref in lotl.references:
        if ref.territory.upper() == territory and eutl_parse.LOTL_RULE not in ref.scheme_rules:
            return ref
    raise TrustedListError(f"Die Liste der Listen nennt keine Vertrauensliste für {territory}")


def check_tl(xml: str, lotl: eutl_parse.LOTLParseResult) -> tuple[ListInfo, str]:
    """Checks a national TL against the signers the LOTL names for its
    territory. Returns its description and the LOTL's location for it."""
    info = describe(xml)
    if info.kind != "tl":
        raise TrustedListError("Datei ist keine nationale Vertrauensliste")
    ref = _tl_reference(lotl, info.territory)
    try:
        eutl_parse.trust_list_to_registry(xml, ref.tlso_certs)
    except Exception as exc:  # noqa: BLE001
        raise TrustedListError(
            f"Signatur der Vertrauensliste {info.territory} ungültig: {exc}"
        ) from exc
    return info, ref.location_uri


async def _get(session: aiohttp.ClientSession, url: str, max_bytes: int) -> str:
    """Reads a list in chunks with a cap on the inflated size (the session
    decompresses gzip, so the wire-level cap alone isn't enough)."""
    async with session.get(url) as response:
        response.raise_for_status()
        received = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            received.extend(chunk)
            if len(received) > max_bytes:
                raise TrustedListError(f"Liste größer als {max_bytes} Bytes: {url}")
        return received.decode("utf-8")


async def fetch_and_check(
    session: aiohttp.ClientSession,
    *,
    lotl_url: str,
    signers: list[asn1_x509.Certificate],
    territories: set[str] | None,
    max_bytes: int = 64 * 1024 * 1024,
) -> tuple[list[CheckedList], list[str]]:
    """Downloads the LOTL and the TLs it names (optionally only some
    territories) and returns those that pass, plus one message per failure.
    A LOTL that fails stops everything: without it no TL can be checked."""
    lotl_xml = await _get(session, lotl_url, max_bytes)
    try:
        lotl = check_lotl(lotl_xml, signers)
    except TrustedListError:
        # The LOTL signers may have changed since this pyHanko release: follow
        # the pivot chain from the official bootstrap set (needs network).
        logger.info("trusted_list_bootstrap_lotl_signers")
        bootstrapped = await eutl_fetch.bootstrap_lotl_signers(lotl_xml, session)
        lotl = check_lotl(lotl_xml, bootstrapped)
    checked = [CheckedList(describe(lotl_xml), lotl_xml, lotl_url)]
    errors: list[str] = []
    for ref in lotl.references:
        if eutl_parse.LOTL_RULE in ref.scheme_rules:
            continue
        territory = ref.territory.upper()
        if territories and territory not in territories:
            continue
        try:
            tl_xml = await _get(session, ref.location_uri, max_bytes)
            info, location = check_tl(tl_xml, lotl)
        except Exception as exc:  # noqa: BLE001 - one bad list doesn't stop the others
            errors.append(f"{territory}: {type(exc).__name__}: {exc}"[:500])
            continue
        checked.append(CheckedList(info, tl_xml, location))
    return checked, errors


def build_registry(tl_xmls: list[str]) -> tuple[TSPRegistry, list[str]]:
    """A registry from stored (already checked) national lists."""
    registry = TSPRegistry()
    errors: list[str] = []
    for xml in tl_xmls:
        try:
            _, list_errors = eutl_parse.trust_list_to_registry_unsafe(xml, registry)
            errors.extend(str(e) for e in list_errors)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:500])
    return registry, errors
