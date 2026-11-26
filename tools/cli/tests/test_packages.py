"""Guards for the shipped configuration packages (`packages/*/config.json`).

P96-S3 found the eGov package's roles carrying generic `read`/`write` permissions that no
service checks, so assigning such a role granted nothing. Every role permission in a package
must be a `<resource>.<action>` name that some service actually uses.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGES = sorted((REPO_ROOT / "packages").glob("*/config.json"))


def _service_string_literals() -> set[str]:
    """All quoted dotted literals in service sources: the permission names services check."""
    literals: set[str] = set()
    pattern = re.compile(r"[\"']([a-z_]+\.[a-z_.]+)[\"']")
    for source in (REPO_ROOT / "services").glob("*/src/**/*.py"):
        literals.update(pattern.findall(source.read_text(encoding="utf-8")))
    return literals


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: p.parent.name)
def test_package_role_permissions_are_real_permission_names(package: Path) -> None:
    roles = json.loads(package.read_text(encoding="utf-8")).get("roles") or []
    if not roles:
        pytest.skip("package ships no roles")
    known = _service_string_literals()
    unknown = {
        (role["name"], permission)
        for role in roles
        for permission in role["permissions"]
        if "." not in permission or permission not in known
    }
    assert not unknown, f"permissions no service checks: {sorted(unknown)}"
