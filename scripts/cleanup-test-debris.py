#!/usr/bin/env python3
"""Removes what test runs leave behind in the dev stack (P94-S3).

The suites run against the live services, and many create root folders,
roles, groups, four-eyes configs and Keycloak realm roles without removing
them: by P94-S3 the dev stack held 1,147 root folders, ~2,600 roles and ~200
realm roles, almost all of it test debris. This script finds such leftovers
by name patterns that only tests produce (each with a random suffix, or an
exact name together with the test principal that creates it) and removes
them through the services' APIs - folders and documents go to the trash
(restorable), running process instances are terminated (P95-S1c: history
kept), the rest is deleted.

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
WORKFLOW = os.environ.get("DEBRIS_WORKFLOW_SERVICE_URL", "http://localhost:8014")
# users-admin (admin.user_management) for roles, groups, configs, realm roles;
# a principal with admin.object_config for the conformity reports.
ADMIN = os.environ.get("DEBRIS_ADMIN_PRINCIPAL", "2")
CONFIG_ADMIN = os.environ.get("DEBRIS_CONFIG_ADMIN_PRINCIPAL", "workflow-test-config-admin")
# workflow.instance_modify (seeded role `domain-admin-workflow-instances`) to
# terminate instances (P95-S1c); granted on first use if missing.
INSTANCE_ADMIN = os.environ.get("DEBRIS_INSTANCE_ADMIN_PRINCIPAL", "workflow-test-instance-admin")
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
# Running process instances (P95-S1c): definitions only tests upload (each
# with a random suffix), circulations the case-service suite starts, and
# OCR reviews of documents that no longer exist (the OCR suites start them
# for documents of their own test database).
INSTANCE_DEFINITION_RULES = [
    rf"^case-service-(pseudonym-)?test-(no-tasks-)?{UUID}$",
    rf"^mail-connector-test-{HEX8}$",
    r"^E2E-Eingriff-\d+$",
]
INSTANCE_CREATOR_RULES = [("Umlauf", "case-service-tests")]
OCR_REVIEW_DEFINITION = "ocr_review"

APPROVAL_RULES = [r"^config\.import\.test\.", r"^delete\.test\.action$"]
REALM_ROLE_RULES = [
    rf"^realm-role-test-{HEX8}$",
    r"^auditor-[0-9a-f]{6,}$",
    r"^config-import-realm-role-[0-9a-f]{6,}$",
]


# Pending four-eyes requests that test suites leave in the live
# permission-service (P98-S2): (initiator, action type pattern). The suites'
# apps call the live service, so every run added some - 600+ by P98. Each is
# withdrawn as its own initiator (an initiator may always withdraw), which
# keeps the audit trail; notification-service sends nothing for a withdrawal.
APPROVAL_REQUEST_RULES = [
    ("document-service-test-declassification-admin", r"^document\.classification\.declassify$"),
    ("workflow-test-config-admin", r"^workflow\.process_definition\.import$"),
    ("admin", r"^document\.force_unlock$"),
    ("alice", r"^(document|folder)\.delete$"),
    ("2", r"^auth\.ad_group_(role_mapping|role_composite_rule|mapping)\."),
    ("tester", r"^migration\.transfer\.start$"),
    ("config-service-import-tests", r"^(config\.import|permission\.role_assignment\.create)$"),
]
# A user's own tracking opt-in (`auth.user_tracking_config.update`, initiator =
# the principal itself): debris only if that principal is no real user - the
# auth suite opts in random ids.
TRACKING_OPT_IN = "auth.user_tracking_config.update"
DEBRIS_ADMIN_USERNAME = os.environ.get("DEBRIS_ADMIN_USERNAME", "users-admin")
DEBRIS_ADMIN_PASSWORD = os.environ.get("DEBRIS_ADMIN_PASSWORD", "users-admin")


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

    def _known_user_ids(self) -> set[str] | None:
        """Every Keycloak user id, or None if that can't be read (then the
        tracking opt-ins are left alone)."""
        try:
            login = self.http.post(
                f"{AUTH}/login",
                json={"username": DEBRIS_ADMIN_USERNAME, "password": DEBRIS_ADMIN_PASSWORD},
            )
            login.raise_for_status()
            users = self.http.get(
                f"{AUTH}/users",
                headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            )
            users.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError):
            print("  skipped tracking opt-ins: user list not readable", file=sys.stderr)
            return None
        return {user["id"] for user in users.json()}

    def approval_requests(self) -> None:
        """Pending four-eyes requests created by test suites, withdrawn by
        their own initiator (P98-S2)."""
        response = self.http.get(f"{PERMISSION}/approval-requests", params={"status": "pending"})
        response.raise_for_status()
        pending = [r for r in response.json() if self.recent(r.get("created_at"))]
        known_users: set[str] | None = None
        users_loaded = False
        for request in pending:
            initiator, action = request["initiated_by"], request["action_type"]
            debris = any(
                initiator == who and re.search(rule, action) for who, rule in APPROVAL_REQUEST_RULES
            )
            if not debris and action == TRACKING_OPT_IN and re.fullmatch(UUID, initiator):
                if not users_loaded:
                    known_users = self._known_user_ids()
                    users_loaded = True
                # Unreadable or empty user list: never guess, leave it.
                debris = bool(known_users) and initiator not in known_users
            if not debris:
                continue
            ok = True
            if self.apply:
                result = self.http.post(
                    f"{PERMISSION}/approval-requests/{request['id']}/reject",
                    headers={"X-DMS-Principal": initiator},
                    json={"reason": "Testrest zurückgezogen (scripts/cleanup-test-debris.py)"},
                )
                # 409: decided meanwhile - nothing left to do.
                ok = result.is_success or result.status_code == 409
            self.note("approval-request", f"{action} by {initiator}", ok)

    def realm_roles(self) -> None:
        self._delete_matching(
            "realm-role",
            f"{AUTH}/realm-roles",
            f"{AUTH}/realm-roles/{{name}}",
            "name",
            REALM_ROLE_RULES,
        )

    def _instance_admin(self) -> dict[str, str]:
        """The terminating principal, given its role once if it lacks it."""
        headers = {"X-DMS-Principal": INSTANCE_ADMIN}
        assignments = self.http.get(
            f"{PERMISSION}/role-assignments", params={"principal_id": INSTANCE_ADMIN}
        ).json()
        roles = self.http.get(f"{PERMISSION}/roles").json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-workflow-instances")
        if self.apply and not any(a["role_id"] == role_id for a in assignments):
            self.http.post(
                f"{PERMISSION}/role-assignments",
                json={
                    "principal_type": "user",
                    "principal_id": INSTANCE_ADMIN,
                    "role_id": role_id,
                    "resource_id": "root",
                },
            ).raise_for_status()
        return headers

    def instances(self) -> None:
        """Running process instances tests left behind - terminated through
        workflow-service (status `terminated`, history kept, claims
        released), not deleted."""
        running: list[dict] = []
        while True:
            page = self.http.get(
                f"{WORKFLOW}/instances",
                params={"status": "running", "limit": 500, "offset": len(running)},
            )
            page.raise_for_status()
            running += page.json()
            if len(page.json()) < 500:
                break
        names: dict[int, str] = {}

        def definition_name(definition_id: int) -> str:
            if definition_id not in names:
                response = self.http.get(f"{WORKFLOW}/process-definitions/{definition_id}")
                names[definition_id] = response.json()["name"] if response.is_success else ""
            return names[definition_id]

        debris = []
        for instance in running:
            if not self.recent(instance.get("created_at")):
                continue
            name = definition_name(instance["process_definition_id"])
            if matches(name, INSTANCE_DEFINITION_RULES) or any(
                name == n and instance["created_by"] == who for n, who in INSTANCE_CREATOR_RULES
            ):
                debris.append((instance, name))
            elif name == OCR_REVIEW_DEFINITION and instance.get("business_key"):
                document = self.http.get(
                    f"{DOCUMENT}/documents/{instance['business_key']}",
                    headers={"X-DMS-Principal": LISTER},
                )
                if document.status_code == 404:
                    debris.append((instance, f"{name} (document gone)"))
        if not debris:
            return
        headers = self._instance_admin()
        for instance, label in debris:
            ok = True
            if self.apply:
                response = self.http.post(
                    f"{WORKFLOW}/instances/{instance['id']}/terminate",
                    json={"reason": "Testrest (scripts/cleanup-test-debris.py)"},
                    headers=headers,
                )
                # 409: ended meanwhile (completed or terminated) - nothing left to do.
                ok = response.is_success or response.status_code == 409
            self.note("instance", f"{label} {instance['id']}", ok)


STEPS = (
    "folders",
    "unknown_types",
    "roles",
    "groups",
    "approval_configs",
    "approval_requests",
    "realm_roles",
    "instances",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="act instead of listing")
    parser.add_argument(
        "--since",
        help="folders, instances, approval requests: only those created at or after this ISO time",
    )
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
