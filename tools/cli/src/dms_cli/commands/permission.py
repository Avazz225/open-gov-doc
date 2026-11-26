"""Permission explanation (P96-S2, ADR 0253 D1) - why a principal has or
hasn't a permission on a resource: every node of the walk, the assignments
that match there and where they come from, active scope locks, the verdict."""

from __future__ import annotations

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

permission_app = typer.Typer(help="Berechtigungen erklaeren (permission-service).")


def register(app: typer.Typer) -> None:
    app.add_typer(permission_app, name="permission")


@permission_app.command("explain")
def explain(
    ctx: typer.Context,
    principal_id: str = typer.Argument(..., help="Benutzer-ID (Keycloak-ID) oder Service-Name."),
    resource_id: str = typer.Argument(..., help="Ordner-, Dokument- oder Vorgangs-ID."),
    permission: str = typer.Option("folder.read", "--permission", help="z. B. document.read"),
    access_type: str = typer.Option(
        None, "--access-type", help="read oder write - Default: aus der Berechtigung abgeleitet."
    ),
) -> None:
    """Erklaert eine Berechtigungsentscheidung (braucht admin.user_management)."""
    if access_type is None:
        access_type = "write" if permission.endswith(".write") else "read"
    client = get_client()
    result = client.get(
        "permission-service",
        f"explain/{principal_id}/{resource_id}",
        params={"permission": permission, "access_type": access_type},
    )
    if output_format(ctx) == "json":
        emit(result, output_format="json")
        return
    verdict = "ERLAUBT" if result["allowed"] else "VERWEIGERT"
    typer.echo(f"{verdict}: {result['reason']}")
    if result.get("groups"):
        typer.echo(f"Gruppen von {principal_id}: {', '.join(result['groups'])}")
    rows = []
    for step in result["steps"]:
        if not step.get("registered"):
            rows.append(
                {
                    "knoten": step["resource_id"],
                    "vererbt": "",
                    "zuweisungen": "nicht registriert",
                    "sperren": "",
                }
            )
            continue
        assignments = ", ".join(
            f"{a['role']} via {a['source']}" + (" (gewaehrt)" if a["grants_permission"] else "")
            for a in step["assignments"]
        )
        locks = ", ".join(str(lock["lock_id"]) for lock in step["active_locks"])
        rows.append(
            {
                "knoten": f"{step['resource_id']} ({step['resource_type']})",
                "vererbt": "ja" if step["inherit"] else "nein - Aufstieg endet",
                "zuweisungen": assignments or "-",
                "sperren": locks or "-",
            }
        )
    emit(rows, output_format="table", columns=["knoten", "vererbt", "zuweisungen", "sperren"])
    cached = result.get("cached")
    if cached is not None and not cached.get("agrees"):
        typer.echo("Hinweis: der zwischengespeicherte Stand weicht ab.", err=True)
