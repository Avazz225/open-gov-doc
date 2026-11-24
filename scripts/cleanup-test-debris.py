#!/usr/bin/env python3
"""Removes what test runs leave behind in the dev stack (P94-S3).

The suites run against the live services, and many create root folders,
roles, groups, four-eyes configs and Keycloak realm roles without removing
them: by P94-S3 the dev stack held 1,147 root folders, ~2,600 roles and ~200
realm roles, almost all of it test debris. This script finds such leftovers
by name patterns that only tests produce (each with a random suffix, or an
exact name together with the test principal that creates it) and removes
them through the services' APIs - folders and documents go to the trash
(restorable), the rest is deleted.

Dry run by default: it lists what it would remove. `--apply` acts.
`scripts/run-tests.sh` calls it with `--apply --since <run start>` after a
run, so every run removes the root folders it created. Roles, groups,
configs and realm roles carry no timestamp and are matched by pattern alone;
an object whose type no longer exists is always debris.

Usage:
  uv run python scripts/cleanup-test-debris.py            # dry run, everything
  uv run python scripts/cleanup-test-debris.py --apply
  uv run python scripts/cleanup-test-debris.py --apply --since 2026-11-24T10:00:00Z
  uv run python scripts/cleanup-test-debris.py --only folders,roles
"""

import argparse
import os
import re
import sys
from datetime import datetime

import httpx

FOLDER = os.environ.get("DEBRIS_FOLDER_SERVICE_URL", "http://localhost:8008")
DOCUMENT = os.environ.get("DEBRIS_DOCUMENT_SERVICE_URL", "http://localhost:8006")
PERMISSION = os.environ.get("DEBRIS_PERMISSION_SERVICE_URL", "http://localhost:8004")
AUTH = os.environ.get("DEBRIS_AUTH_SERVICE_URL", "http://localhost:8003")
# users-admin (admin.user_management) for roles, groups, configs, realm roles;
# a principal with admin.object_config for the conformity reports.
ADMIN = os.environ.get("DEBRIS_ADMIN_PRINCIPAL", "2")
CONFIG_ADMIN = os.environ.get("DEBRIS_CONFIG_ADMIN_PRINCIPAL", "workflow-test-config-admin")
# Reads as a service principal see every folder (P94-S3, document-service
# trusts service reads; folder-service lists everything readable).
LISTER = "migration-service"

HEX8 = r"[0-9a-f]{8}"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

# Root folders: (pattern, required creator or None).
ROOT_FOLDER_RULES = [
    (
        rf"^(Facet-Isoliert|Handakte|Handakte-lesbar|Handakte-verborgen|Isoliert|Ordner)-{HEX8}$",
        None,
    ),
    (rf"^Vorgang {HEX8}$", None),
    (rf"^cmis-ordner-{HEX8}$", None),
    (rf"^(dry-run-quelle|quelle|transfer-quelle)-{HEX8}$", None),
    (rf"^(sichtbar|ziel|webdav-ordner|webdav-viele-kinder|webdav-mkcol)-{HEX8}$", None),
    (r"^(Testordner|Testordner-PerDoc|Typisierter Ordner|Zielordner|Zielordner-Promote)$", "alice"),
    (r"^e2e-user-ui-\d+-[a-z0-9]+$", None),
    (r"^p77s9-verify-\d+$", None),
    (r"^p94s[0-9]+-[a-z]+-\d+$", None),
    (r"^P55-S2 Verify$", None),
]
ROLE_RULES = [
    r"^(export-test-role|redaction-test-role|share-link-test-role|webdav-edit-token-test-role"
    r"|webdav-connector-edit-token-test-role|search-test-role|hand-folder-test-role"
    r"|search-test-hand-folder-role|search-test-hf-role|config-import-test|config-import-applied"
    rf"|config-import-gated|P80S2-Rolle|folder-reader|document-reader-doc)-{HEX8}$",
    rf"^(document-reader|case-reader)-{UUID}$",
    r"^Freigabe-[0-9a-f]{6}$",
    r"^loesch-test$",
]
GROUP_RULES = [
    rf"^OrgUnitWorkflowG4-{HEX8}$",
    r"^(Sachbearbeitung|Kasse)-[0-9a-f]{6}$",
    r"^E2E-Lane-\d+$",
]
APPROVAL_RULES = [r"^config\.import\.test\.", r"^delete\.test\.action$"]
REALM_ROLE_RULES = [
    rf"^realm-role-test-{HEX8}$",
    r"^auditor-[0-9a-f]{6,}$",
    r"^config-import-realm-role-[0-9a-f]{6,}$",
]


def matches(name: str, rules) -> bool:
    return any(re.search(rule, name) for rule in rules)


def _already_gone(response: httpx.Response) -> bool:
    """A 404 from the service about the item itself - not a route that
    doesn't exist (an older service without the delete endpoint)."""
    if response.status_code != 404:
        return False
    try:
        return response.json().get("detail") != "Not Found"
    except ValueError:
        return False


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Cleaner:
    def __init__(self, apply: bool, since: datetime | None, quiet: bool) -> None:
        self.apply = apply
        self.since = since
        self.quiet = quiet
        self.http = httpx.Client(timeout=30)
        self.summary: dict[str, list[int]] = {}
        self.skipped = 0

    def note(self, kind: str, label: str, ok: bool) -> None:
        done, failed = self.summary.setdefault(kind, [0, 0])
        self.summary[kind] = [done + ok, failed + (not ok)]
        if not self.quiet:
            verb = ("removed" if ok else "FAILED ") if self.apply else "would remove"
            print(f"  {verb}  {kind:<13} {label}")

    def recent(self, created_at: str | None) -> bool:
        return self.since is None or (
            created_at is not None and parse_time(created_at) >= self.since
        )

    def folders(self) -> None:
        response = self.http.get(
            f"{FOLDER}/folders/root/children", headers={"X-DMS-Principal": LISTER}
        )
        response.raise_for_status()
        for folder in response.json():
            creator_ok = any(
                re.search(rule, folder["name"]) and (who is None or folder.get("created_by") == who)
                for rule, who in ROOT_FOLDER_RULES
            )
            if not creator_ok or not self.recent(folder.get("created_at")):
                continue
            ok = True
            if self.apply:
                ok = self.http.post(
                    f"{FOLDER}/folders/{folder['id']}/trash",
                    headers={"X-DMS-Principal": ADMIN},
                    json={},
                ).is_success
            self.note("folder", folder["name"], ok)

    def unknown_types(self) -> None:
        """Folders and documents whose object type a test deleted after use
        (the conformity reports' `unknown_type`)."""
        for base, kind, trash in (
            (FOLDER, "folder(type)", "folders"),
            (DOCUMENT, "document(type)", "documents"),
        ):
            response = self.http.get(
                f"{base}/structure/conformity",
                params={"limit": 5000},
                headers={"X-DMS-Principal": CONFIG_ADMIN},
            )
            if not response.is_success:
                print(
                    f"  skipped {kind}: conformity report {response.status_code}", file=sys.stderr
                )
                continue
            for item in response.json().get("items", []):
                if not any(p.get("kind") == "unknown_type" for p in item.get("problems", [])):
                    continue
                label = item.get("path") or item.get("title") or item["id"]
                ok = True
                if self.apply:
                    status = self.http.post(
                        f"{base}/{trash}/{item['id']}/trash",
                        headers={"X-DMS-Principal": ADMIN},
                        json={},
                    ).status_code
                    if status == 403:
                        # An object with its own permissions (inherit=False):
                        # deliberately out of the admin account's reach.
                        self.skipped += 1
                        continue
                    ok = status in (200, 404)
                self.note(kind, label, ok)

    def _delete_matching(self, kind, list_url, delete_url, key, rules, headers=None) -> None:
        response = self.http.get(list_url, headers=headers or {})
        response.raise_for_status()
        for row in response.json():
            name = row[key]
            if not matches(name, rules):
                continue
            ok = True
            if self.apply:
                target = delete_url.format(id=row.get("id"), name=name)
                response = self.http.delete(target, headers={"X-DMS-Principal": ADMIN})
                ok = response.status_code == 204 or _already_gone(response)
            self.note(kind, name, ok)

    def roles(self) -> None:
        self._delete_matching(
            "role", f"{PERMISSION}/roles", f"{PERMISSION}/roles/{{id}}", "name", ROLE_RULES
        )

    def groups(self) -> None:
        self._delete_matching(
            "group",
            f"{PERMISSION}/groups",
            f"{PERMISSION}/groups/{{id}}",
            "name",
            GROUP_RULES,
            headers={"X-DMS-Principal": ADMIN},
        )

    def approval_configs(self) -> None:
        self._delete_matching(
            "approval",
            f"{PERMISSION}/approval-config",
            f"{PERMISSION}/approval-config/{{name}}",
            "action_type",
            APPROVAL_RULES,
        )

    def realm_roles(self) -> None:
        self._delete_matching(
            "realm-role",
            f"{AUTH}/realm-roles",
            f"{AUTH}/realm-roles/{{name}}",
            "name",
            REALM_ROLE_RULES,
        )


STEPS = ("folders", "unknown_types", "roles", "groups", "approval_configs", "realm_roles")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="act instead of listing")
    parser.add_argument("--since", help="folders: only those created at or after this ISO time")
    parser.add_argument("--only", help=f"comma-separated subset of: {', '.join(STEPS)}")
    parser.add_argument("--quiet", action="store_true", help="summary only")
    args = parser.parse_args()
    steps = args.only.split(",") if args.only else list(STEPS)
    cleaner = Cleaner(args.apply, parse_time(args.since), args.quiet)
    for step in steps:
        try:
            getattr(cleaner, step)()
        except httpx.HTTPError as exc:
            print(f"  skipped {step}: {exc}", file=sys.stderr)
    mode = "removed" if args.apply else "would remove (dry run)"
    if cleaner.skipped:
        mode += f", {cleaner.skipped} skipped (own permissions)"
    print(
        "test debris:",
        ", ".join(
            f"{k} {v[0]}" + (f" ({v[1]} failed)" if v[1] else "")
            for k, v in cleaner.summary.items()
        )
        or "none",
        f"- {mode}",
    )
    return 1 if any(v[1] for v in cleaner.summary.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
