"""Vulnerability matching (P100-S2, ADR 0272): version rules per ecosystem,
OSV ranges, and matching against the inventory - pure functions."""

import pytest
from monitoring_service import vulnerabilities as v
from monitoring_service.components_inventory import ServiceComponents


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("70.0.0", "69.5.1", 1),
        ("2.0.0rc1", "2.0.0", -1),
        ("1.0.post1", "1.0", 1),
        ("1.0", "1.0.0", 0),
    ],
)
def test_pypi_pep440(a, b, expected):
    assert v.compare_pypi(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("1.10.0", "1.9.9", 1),
        ("2.0.0-beta.2", "2.0.0-beta.11", -1),
        ("2.0.0-rc.1", "2.0.0", -1),
        ("v3.1.0", "3.1.0", 0),
    ],
)
def test_npm_semver(a, b, expected):
    assert v.compare_semver(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("3.5.5-1~deb13u1", "3.5.5-1", -1),  # ~ sorts before anything
        ("3.0.15-1~deb12u1", "3.0.14-1", 1),
        ("1:1.0-1", "2.0-1", 1),  # epoch wins
        ("2.36-9+deb12u9", "2.36-9+deb12u10", -1),
        ("3.3.2-1", "3.3.2-1", 0),
    ],
)
def test_debian_dpkg_ordering(a, b, expected):
    assert v.compare_debian(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("3.3.3-r0", "3.3.2-r5", 1),
        ("3.3.3_rc1-r0", "3.3.3-r0", -1),
        ("1.2.3-r2", "1.2.3-r10", -1),
        ("1.2.3a-r0", "1.2.3-r0", 1),
    ],
)
def test_alpine_apk_ordering(a, b, expected):
    assert v.compare_alpine(a, b) == expected


def test_osv_ranges():
    events = [{"introduced": "0"}, {"fixed": "70.0.0"}]
    assert v.affected_by_range("69.5.1", events, v.compare_pypi)
    assert not v.affected_by_range("70.0.0", events, v.compare_pypi)
    two = [{"introduced": "1.0"}, {"fixed": "1.2"}, {"introduced": "2.0"}, {"last_affected": "2.1"}]
    assert not v.affected_by_range("0.9", two, v.compare_pypi)
    assert v.affected_by_range("1.1", two, v.compare_pypi)
    assert not v.affected_by_range("1.5", two, v.compare_pypi)
    assert v.affected_by_range("2.1", two, v.compare_pypi)
    assert not v.affected_by_range("2.2", two, v.compare_pypi)
    assert v.first_fix_after("1.1", two, v.compare_pypi) == "1.2"


def _component(name, version, ecosystem, **props):
    properties = [{"name": "dms:ecosystem", "value": ecosystem}]
    properties += [{"name": f"dms:{k}", "value": val} for k, val in props.items()]
    return {"name": name, "version": version, "properties": properties}


SERVICES = {
    "document-service": ServiceComponents(
        service="document-service",
        kind="service",
        document={
            "components": [
                _component("setuptools", "69.5.1", "PyPI"),
                _component(
                    "libssl3t64", "3.5.5-1~deb13u1", "Debian", source="openssl", distro="debian:13"
                ),
            ]
        },
    ),
    "permission-service": ServiceComponents(
        service="permission-service",
        kind="service",
        document={"components": [_component("Setuptools", "75.0.0", "PyPI")]},
    ),
    "admin-ui": ServiceComponents(
        service="admin-ui",
        kind="frontend",
        document={
            "components": [
                _component(
                    "libssl3", "3.3.3-r0", "Alpine", source="openssl", distro="alpine:3.21.3"
                )
            ]
        },
    ),
}


def test_pypi_record_names_affected_and_unaffected_installations():
    record = {
        "id": "GHSA-cx63-2mw6-8hw5",
        "affected": [
            {
                "package": {"ecosystem": "PyPI", "name": "setuptools"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "70.0.0"}]}
                ],
            }
        ],
    }
    findings = v.match([record], SERVICES)
    by_service = {f.service: f for f in findings}
    assert by_service["document-service"].affected is True
    assert by_service["document-service"].fixed_in == "70.0.0"
    assert by_service["permission-service"].affected is False  # case-insensitive name
    assert findings[0].affected  # affected first


def test_debian_record_matches_by_source_package_and_release_only():
    record = {
        "id": "DEBIAN-CVE-2024-5535",
        "affected": [
            {
                "package": {"ecosystem": "Debian:12", "name": "openssl"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [{"introduced": "0"}, {"fixed": "3.0.15-1~deb12u1"}],
                    }
                ],
            },
            {
                "package": {"ecosystem": "Debian:13", "name": "openssl"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.3.2-1"}]}
                ],
            },
        ],
    }
    [finding] = v.match([record], SERVICES)
    assert finding.component == "libssl3t64"
    assert finding.ecosystem == "Debian:13"  # the Debian:12 entry doesn't apply to this image
    assert finding.affected is False  # 3.5.5 is past the fix 3.3.2


def test_alpine_record_and_unknown_ecosystems():
    record = {
        "id": "ALPINE-CVE-X",
        "affected": [
            {
                "package": {"ecosystem": "Alpine:v3.21", "name": "openssl"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.3.4-r0"}]}
                ],
            },
            {"package": {"ecosystem": "Go", "name": "x"}, "ranges": []},
        ],
    }
    [finding] = v.match([record], SERVICES)
    assert (finding.service, finding.affected, finding.fixed_in) == ("admin-ui", True, "3.3.4-r0")


def test_manual_record():
    record = v.manual_record(
        "PyPI", "setuptools", introduced=None, fixed="70.0.0", last_affected=None, label=None
    )
    assert [f.service for f in v.match([record], SERVICES) if f.affected] == ["document-service"]


async def test_fetch_follows_aliases_and_distribution_records():
    import httpx

    records = {
        "CVE-2024-6345": {
            "id": "CVE-2024-6345",
            "aliases": ["GHSA-cx63"],
            "related": ["ALSA-1", "PYSEC-9"],
            "affected": [],
        },
        "GHSA-cx63": {
            "id": "GHSA-cx63",
            "aliases": ["CVE-2024-6345"],
            "affected": [{"package": {"ecosystem": "PyPI", "name": "setuptools"}}],
        },
        "PYSEC-9": {"id": "PYSEC-9", "affected": []},
        "DEBIAN-CVE-2024-6345": {"id": "DEBIAN-CVE-2024-6345", "affected": []},
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vid = request.url.path.rsplit("/", 1)[1]
        asked.append(vid)
        return httpx.Response(200, json=records[vid]) if vid in records else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        fetched = await v.fetch_records(client, "https://osv.example", "CVE-2024-6345")
    assert {r["id"] for r in fetched} == set(records)
    assert "ALSA-1" not in asked  # other distributions aren't followed
    assert "ALPINE-CVE-2024-6345" in asked  # tried, 404


def test_one_line_per_installation_across_aliases():
    def pypi(rid, fixed):
        return {
            "id": rid,
            "affected": [
                {
                    "package": {"ecosystem": "PyPI", "name": "setuptools"},
                    "ranges": [
                        {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": fixed}]}
                    ],
                }
            ],
        }

    # GHSA and PYSEC describe the same flaw; the PYSEC range is narrower.
    findings = v.match([pypi("GHSA-a", "70.0.0"), pypi("PYSEC-b", "69.0.0")], SERVICES)
    by_service = {f.service: f for f in findings}
    assert len(findings) == 2
    doc = by_service["document-service"]
    assert (doc.affected, doc.record_id, doc.fixed_in) == (True, "GHSA-a", "70.0.0")
    assert by_service["permission-service"].record_id == "GHSA-a, PYSEC-b"
