"""Checking a vulnerability against the component inventory (P100-S2, ADR 0272).

OG Doc ships no vulnerability data (user decision 2026-11-27): it would be
stale on delivery. A record comes either live from OSV.dev (online, off by
default) or from a file the admin brings in (air-gapped), in OSV format, or as
a hand-entered package with an affected range. Matching is local, against the
inventory of what actually runs (ADR 0271).

Ecosystems and their version rules:
- PyPI: PEP 440 (`packaging`), names normalised (PEP 503);
- npm: SemVer;
- Debian: dpkg ordering (epoch, `~`), matched by source package and release
  (OSV `Debian:13` against the image's `debian:13`);
- Alpine: apk ordering (`_rc`, `-rN`), matched by origin package and release
  (`Alpine:v3.21` against `alpine:3.21.x`).

Records for a CVE are spread: the CVE record itself often has only git ranges;
the affected packages sit in its aliases (GHSA, PYSEC) and in distribution
records that aren't linked (`DEBIAN-CVE-…`, `ALPINE-CVE-…`). `fetch_records`
follows all of those.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from monitoring_service.components_inventory import ServiceComponents, ecosystem
from packaging.version import InvalidVersion, Version

# --- Version ordering per ecosystem -----------------------------------------------


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


def compare_pypi(a: str, b: str) -> int:
    return _cmp(Version(a), Version(b))


_SEMVER = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$")


def compare_semver(a: str, b: str) -> int:
    def parse(text: str):
        match = _SEMVER.match(text.strip())
        if not match:
            raise ValueError(f"keine SemVer-Version: {text!r}")
        major, minor, patch, pre = match.groups()
        return (int(major), int(minor or 0), int(patch or 0)), pre

    (core_a, pre_a), (core_b, pre_b) = parse(a), parse(b)
    if core_a != core_b:
        return _cmp(core_a, core_b)
    if pre_a == pre_b:
        return 0
    if pre_a is None:
        return 1  # a release ranks above its pre-releases
    if pre_b is None:
        return -1
    for x, y in zip(pre_a.split("."), pre_b.split("."), strict=False):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return _cmp(int(x), int(y))
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return _cmp(x, y)
    return _cmp(len(pre_a.split(".")), len(pre_b.split(".")))


def _dpkg_order(char: str) -> int:
    if char == "~":
        return -1
    if not char or char.isdigit():
        return 0
    if char.isalpha():
        return ord(char)
    return ord(char) + 256


def _dpkg_compare_part(a: str, b: str) -> int:
    while a or b:
        while (a and not a[0].isdigit()) or (b and not b[0].isdigit()):
            first_a = _dpkg_order(a[0]) if a else 0
            first_b = _dpkg_order(b[0]) if b else 0
            if first_a != first_b:
                return _cmp(first_a, first_b)
            a, b = a[1:], b[1:]
        digits_a = re.match(r"\d*", a).group()
        digits_b = re.match(r"\d*", b).group()
        if int(digits_a or 0) != int(digits_b or 0):
            return _cmp(int(digits_a or 0), int(digits_b or 0))
        a, b = a[len(digits_a) :], b[len(digits_b) :]
    return 0


def compare_debian(a: str, b: str) -> int:
    def parse(text: str) -> tuple[int, str, str]:
        epoch, _, rest = text.rpartition(":") if ":" in text else ("0", "", text)
        upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "0")
        return int(epoch or 0), upstream, revision

    epoch_a, up_a, rev_a = parse(a)
    epoch_b, up_b, rev_b = parse(b)
    if epoch_a != epoch_b:
        return _cmp(epoch_a, epoch_b)
    return _dpkg_compare_part(up_a, up_b) or _dpkg_compare_part(rev_a, rev_b)


_APK_SUFFIX = {
    "alpha": -4,
    "beta": -3,
    "pre": -2,
    "rc": -1,
    "": 0,
    "cvs": 1,
    "svn": 2,
    "git": 3,
    "hg": 4,
    "p": 5,
}
_APK = re.compile(
    r"^([0-9][0-9.]*)([a-z]?)((?:_(?:alpha|beta|pre|rc|cvs|svn|git|hg|p)\d*)*)(?:-r(\d+))?$"
)


def compare_alpine(a: str, b: str) -> int:
    def parse(text: str):
        match = _APK.match(text.strip())
        if not match:
            raise ValueError(f"keine apk-Version: {text!r}")
        numbers, letter, suffixes, release = match.groups()
        suffix_key = [
            (_APK_SUFFIX[s.rstrip("0123456789")], int(re.sub(r"\D", "", s) or 0))
            for s in suffixes.split("_")
            if s
        ] or [(0, 0)]
        return (
            tuple(int(n) for n in numbers.split(".") if n),
            letter,
            suffix_key,
            int(release or 0),
        )

    return _cmp(parse(a), parse(b))


COMPARATORS: dict[str, Callable[[str, str], int]] = {
    "PyPI": compare_pypi,
    "npm": compare_semver,
    "Debian": compare_debian,
    "Alpine": compare_alpine,
}

# --- OSV ranges -------------------------------------------------------------------


def affected_by_range(version: str, events: list[dict], compare: Callable[[str, str], int]) -> bool:
    """OSV's range semantics: walk the events in version order; `introduced`
    opens, `fixed` closes (exclusive), `last_affected` closes (inclusive).
    `introduced: "0"` means from the very first version."""
    ordered = [
        next(iter(event.items()))
        for event in events
        if next(iter(event)) in ("introduced", "fixed", "last_affected")
    ]
    affected = False
    for kind, value in sorted(ordered, key=lambda item: _SortKey(item[1], compare)):
        if kind == "introduced" and (value == "0" or compare(version, value) >= 0):
            affected = True
        elif kind == "fixed" and compare(version, value) >= 0:
            affected = False
        elif kind == "last_affected" and compare(version, value) > 0:
            affected = False
    return affected


class _SortKey:
    def __init__(self, value: str, compare: Callable[[str, str], int]) -> None:
        self.value, self.compare = value, compare

    def __lt__(self, other: _SortKey) -> bool:
        if self.value == "0":
            return other.value != "0"
        if other.value == "0":
            return False
        return self.compare(self.value, other.value) < 0


def first_fix_after(version: str, events: list[dict], compare) -> str | None:
    fixes = [e["fixed"] for e in events if "fixed" in e]
    later = [f for f in fixes if compare(f, version) > 0]
    if not later:
        return None
    best = later[0]
    for candidate in later[1:]:
        if compare(candidate, best) < 0:
            best = candidate
    return best


# --- Matching ---------------------------------------------------------------------


def _normalise(eco: str, name: str) -> str:
    if eco == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name


def _component_name(component: dict, eco: str) -> str:
    if eco in ("Debian", "Alpine"):
        for prop in component.get("properties") or []:
            if prop.get("name") == "dms:source":
                return prop["value"]
    return _normalise(eco, component.get("name", ""))


def _release_matches(component: dict, eco: str, release: str) -> bool:
    """`Debian:13` vs `debian:13`; `Alpine:v3.21` vs `alpine:3.21.3`. No release
    on the record means any."""
    if not release:
        return True
    distro = next(
        (p["value"] for p in component.get("properties") or [] if p.get("name") == "dms:distro"),
        None,
    )
    if not distro:
        return False
    version = distro.split(":", 1)[1]
    if eco == "Debian":
        return version.split(".")[0] == release
    if eco == "Alpine":
        return ".".join(version.split(".")[:2]) == release.lstrip("v")
    return True


@dataclass
class Finding:
    service: str
    kind: str
    component: str
    version: str
    ecosystem: str
    record_id: str
    affected: bool
    fixed_in: str | None = None
    note: str | None = None


def match(records: list[dict], services: dict[str, ServiceComponents]) -> list[Finding]:
    """Every installed component a record names, affected or not."""
    findings: list[Finding] = []
    for record in records:
        for entry in record.get("affected") or []:
            package = entry.get("package") or {}
            eco_full = package.get("ecosystem") or ""
            eco, _, release = eco_full.partition(":")
            compare = COMPARATORS.get(eco)
            if compare is None:
                continue
            wanted = _normalise(eco, package.get("name", ""))
            ranges = [
                r for r in entry.get("ranges") or [] if r.get("type") in ("ECOSYSTEM", "SEMVER")
            ]
            listed = set(entry.get("versions") or [])
            for service in services.values():
                for component in service.components:
                    if ecosystem(component) != eco:
                        continue
                    if _component_name(component, eco) != wanted:
                        continue
                    if not _release_matches(component, eco, release):
                        continue
                    version = component.get("version", "")
                    finding = Finding(
                        service=service.service,
                        kind=service.kind,
                        component=component.get("name", ""),
                        version=version,
                        ecosystem=eco_full,
                        record_id=record.get("id", ""),
                        affected=False,
                    )
                    try:
                        hit = version in listed or any(
                            affected_by_range(version, r.get("events") or [], compare)
                            for r in ranges
                        )
                        finding.affected = hit
                        if hit:
                            finding.fixed_in = next(
                                (
                                    fix
                                    for r in ranges
                                    if (
                                        fix := first_fix_after(
                                            version, r.get("events") or [], compare
                                        )
                                    )
                                ),
                                None,
                            )
                    except (InvalidVersion, ValueError) as exc:
                        finding.note = f"Version nicht vergleichbar: {exc}"
                    findings.append(finding)
    # One line per (service, component, version): one vulnerability is usually
    # published under several ids (GHSA and PYSEC for the same PyPI package), and
    # an admin wants one answer per installation. Affected if any record says so;
    # the ids listed are those that led to the verdict.
    groups: dict[tuple, list[Finding]] = {}
    for finding in findings:
        groups.setdefault((finding.service, finding.component, finding.version), []).append(finding)
    merged: list[Finding] = []
    for group in groups.values():
        hits = [f for f in group if f.affected]
        basis = hits or group
        first = basis[0]
        merged.append(
            Finding(
                service=first.service,
                kind=first.kind,
                component=first.component,
                version=first.version,
                ecosystem=first.ecosystem,
                record_id=", ".join(sorted({f.record_id for f in basis})),
                affected=bool(hits),
                fixed_in=next((f.fixed_in for f in hits if f.fixed_in), None),
                note=next((f.note for f in group if f.note), None),
            )
        )
    return sorted(merged, key=lambda f: (not f.affected, f.service, f.component))


# --- Records -----------------------------------------------------------------------

_FOLLOW = ("GHSA-", "PYSEC-", "DEBIAN-", "DSA-", "DLA-", "ALPINE-", "CVE-")
MAX_RECORDS = 25


async def fetch_records(client: httpx.AsyncClient, base_url: str, vuln_id: str) -> list[dict]:
    """The record for `vuln_id` and everything that names packages for it:
    its aliases and related records (of the ecosystems that matter here) and,
    for a CVE, the distribution records OSV keeps separately."""
    queue = [vuln_id]
    if vuln_id.upper().startswith("CVE-"):
        queue += [f"DEBIAN-{vuln_id.upper()}", f"ALPINE-{vuln_id.upper()}"]
    fetched: dict[str, dict] = {}
    seen: set[str] = set()
    while queue and len(seen) < MAX_RECORDS:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        response = await client.get(f"{base_url.rstrip('/')}/v1/vulns/{current}")
        if response.status_code == 404:
            continue
        response.raise_for_status()
        record = response.json()
        fetched[record.get("id", current)] = record
        for other in (record.get("aliases") or []) + (record.get("related") or []):
            if other not in seen and other.upper().startswith(_FOLLOW):
                queue.append(other)
    return list(fetched.values())


def manual_record(
    ecosystem_name: str,
    package: str,
    *,
    introduced: str | None,
    fixed: str | None,
    last_affected: str | None,
    label: str | None,
) -> dict:
    """A hand-entered package and range, in OSV form (air-gapped without a file)."""
    events: list[dict] = [{"introduced": introduced or "0"}]
    if fixed:
        events.append({"fixed": fixed})
    if last_affected:
        events.append({"last_affected": last_affected})
    return {
        "id": label or f"MANUELL-{package}",
        "summary": "Von Hand eingegebener Bereich",
        "affected": [
            {
                "package": {"ecosystem": ecosystem_name, "name": package},
                "ranges": [{"type": "ECOSYSTEM", "events": events}],
            }
        ],
    }


def record_summary(record: dict) -> dict:
    database = record.get("database_specific") or {}
    return {
        "id": record.get("id"),
        "aliases": record.get("aliases") or [],
        "summary": record.get("summary") or (record.get("details") or "")[:300],
        "severity": database.get("severity"),
        "cvss": [s.get("score") for s in record.get("severity") or [] if s.get("score")],
        "published": record.get("published"),
        "packages": sorted(
            {
                f"{a['package'].get('ecosystem')}/{a['package'].get('name')}"
                for a in record.get("affected") or []
                if a.get("package")
            }
        ),
    }
