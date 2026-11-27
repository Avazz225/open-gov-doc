"""Software components of a service image (P100-S1, ADR 0271).

One CycloneDX 1.6 document per image:
- every installed Python distribution (`importlib.metadata`), so what the image
  really holds, not what a lock file intended;
- every installed Debian package of the base image (`/var/lib/dpkg/status`),
  where OpenSSL & co. live.

The same code runs twice:
- at image build: `python -m dms_common.components --name <service> --out
  /app/META-INF/components.cdx.json` freezes the file into the image (readable
  without running the service, signable with it);
- at runtime: `GET /component-report` (registered by `setup_observability`) reports
  what this process sees, plus whether it matches the file in the image.

Standard library only: generating it must not download anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

COMPONENTS_FILE = Path("/app/META-INF/components.cdx.json")
DPKG_STATUS = Path("/var/lib/dpkg/status")
OS_RELEASE = Path("/etc/os-release")
SPEC_VERSION = "1.6"
# Workspace packages of this repository (libs/*, services/*).
_OWN_PREFIXES = ("dms-", "dms_")
_OWN_SUFFIXES = ("-service", "-connector", "-hub-service")


def _python_components() -> list[dict]:
    seen: dict[str, dict] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        version = (dist.version or "").strip()
        if not name or not version:
            continue
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        own = key.startswith(_OWN_PREFIXES) or key.endswith(_OWN_SUFFIXES)
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{key}@{version}",
            "bom-ref": f"pkg:pypi/{key}@{version}",
            "properties": [{"name": "dms:ecosystem", "value": "PyPI"}],
        }
        if own:
            component["properties"].append({"name": "dms:own", "value": "true"})
        license_text = (dist.metadata.get("License-Expression") or "").strip()
        if license_text:
            component["licenses"] = [{"expression": license_text}]
        seen[key] = component
    return sorted(seen.values(), key=lambda c: c["name"].lower())


def os_release(path: Path = OS_RELEASE) -> str | None:
    """`debian:13`, `alpine:3.21.3` - vulnerability records are per
    distribution release (OSV `Debian:13`), so the match needs it (P100-S2)."""
    try:
        fields = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    except OSError:
        return None
    distro = fields.get("ID", "").strip('"')
    version = fields.get("VERSION_ID", "").strip('"')
    return f"{distro}:{version}" if distro and version else None


def _parse_dpkg_status(text: str, distro: str | None = None) -> list[dict]:
    components = []
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if not line or line[0].isspace() or ":" not in line:
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        if not fields.get("Package") or "installed" not in fields.get("Status", "").split():
            continue
        name, version = fields["Package"], fields.get("Version", "")
        arch = fields.get("Architecture", "")
        purl = f"pkg:deb/debian/{name}@{version}" + (f"?arch={arch}" if arch else "")
        # Security records name the source package (`openssl`), not the
        # binaries built from it (`libssl3t64`) - P100-S2.
        source = (fields.get("Source") or name).split(" ", 1)[0]
        properties = [
            {"name": "dms:ecosystem", "value": "Debian"},
            {"name": "dms:source", "value": source},
        ]
        if distro:
            properties.append({"name": "dms:distro", "value": distro})
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "bom-ref": purl,
                "properties": properties,
            }
        )
    return sorted(components, key=lambda c: c["name"])


def _os_components(status_file: Path = DPKG_STATUS) -> list[dict]:
    try:
        return _parse_dpkg_status(
            status_file.read_text(encoding="utf-8", errors="replace"), os_release()
        )
    except OSError:
        return []  # not a Debian-based image, or run outside a container


def _runtime_components() -> list[dict]:
    """The Python interpreter itself: the `python:*-slim` images build it from
    source, outside dpkg, so it would otherwise be invisible (P100-S1 note)."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    purl = f"pkg:generic/cpython@{version}"
    return [
        {
            "type": "platform",
            "name": "cpython",
            "version": version,
            "purl": purl,
            "bom-ref": purl,
            "properties": [{"name": "dms:ecosystem", "value": "runtime"}],
        }
    ]


def build_document(name: str, *, status_file: Path = DPKG_STATUS) -> dict:
    """The CycloneDX document for this process's environment."""
    components = _python_components() + _os_components(status_file) + _runtime_components()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {"type": "application", "name": name, "bom-ref": name},
            "tools": {"components": [{"type": "library", "name": "dms_common.components"}]},
        },
        "components": components,
    }


def fingerprint(document: dict) -> str:
    """Identity of the component set (name, version, purl), independent of
    timestamp and serial number - equal for the image file and the runtime
    view when nothing differs."""
    keys = sorted(c.get("purl") or f"{c['name']}@{c['version']}" for c in document["components"])
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def compare(image: dict, runtime: dict) -> dict:
    """What the running process sees beyond, or short of, the image file."""
    image_keys = {c.get("purl") for c in image.get("components", [])}
    runtime_keys = {c.get("purl") for c in runtime.get("components", [])}
    return {
        "matches_image": image_keys == runtime_keys,
        "only_at_runtime": sorted(k for k in runtime_keys - image_keys if k),
        "only_in_image": sorted(k for k in image_keys - runtime_keys if k),
    }


def runtime_report(name: str, components_file: Path = COMPONENTS_FILE) -> dict:
    """`GET /component-report`: the runtime document plus its comparison with the
    image file (absent outside an image built since P100-S1)."""
    runtime = build_document(name)
    report = {"service": name, "document": runtime, "fingerprint": fingerprint(runtime)}
    try:
        image = json.loads(components_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report["image_file"] = None
        return report
    report["image_file"] = {
        "path": str(components_file),
        "fingerprint": fingerprint(image),
        "created": image.get("metadata", {}).get("timestamp"),
        **compare(image, runtime),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="the service/image name")
    parser.add_argument("--out", type=Path, default=COMPONENTS_FILE)
    args = parser.parse_args(argv)
    document = build_document(args.name)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{args.out}: {len(document['components'])} components", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
