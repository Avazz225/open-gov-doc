"""Workflow Engine (7.1) - Prozessdefinitionen/-instanzen/Tasks."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from dms_cli.context import get_client, output_format
from dms_cli.output import emit

workflow_app = typer.Typer(help="Workflow Engine (7.1).")
definitions_app = typer.Typer(help="Prozessdefinitionen.")
instances_app = typer.Typer(help="Prozessinstanzen.")
workflow_app.add_typer(definitions_app, name="definitions")
workflow_app.add_typer(instances_app, name="instances")


def register(app: typer.Typer) -> None:
    app.add_typer(workflow_app, name="workflow")


@definitions_app.command("list")
def definitions_list(ctx: typer.Context) -> None:
    client = get_client()
    result = client.get("workflow-service", "process-definitions")
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", "name", "version", "bpmn_process_id"],
    )


@definitions_app.command("get")
def definitions_get(ctx: typer.Context, definition_id: int) -> None:
    client = get_client()
    result = client.get("workflow-service", f"process-definitions/{definition_id}")
    emit(result, output_format=output_format(ctx))


@instances_app.command("list")
def instances_list(
    ctx: typer.Context,
    status: str | None = typer.Option(None, "--status"),
    process_definition_id: int | None = typer.Option(None, "--definition"),
) -> None:
    client = get_client()
    result = client.get(
        "workflow-service",
        "instances",
        params={"status": status, "process_definition_id": process_definition_id},
    )
    emit(
        result,
        output_format=output_format(ctx),
        columns=["id", "process_definition_id", "status", "business_key", "created_at"],
    )


@instances_app.command("get")
def instances_get(ctx: typer.Context, instance_id: str) -> None:
    client = get_client()
    result = client.get("workflow-service", f"instances/{instance_id}")
    emit(result, output_format=output_format(ctx))


@instances_app.command("tasks")
def instances_tasks(ctx: typer.Context, instance_id: str) -> None:
    client = get_client()
    result = client.get("workflow-service", f"instances/{instance_id}/tasks")
    emit(result, output_format=output_format(ctx), columns=["id", "name", "lane"])


@instances_app.command("complete-task")
def complete_task(
    ctx: typer.Context,
    instance_id: str,
    task_id: str,
    completed_by: str = typer.Option(..., "--completed-by"),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, help="JSON-Datei mit zusaetzlichen Taskdaten."
    ),
    signature_id: str | None = typer.Option(
        None, "--signature-id", help="Pflicht bei Signature Tasks (3.10)."
    ),
) -> None:
    client = get_client()
    data = json.loads(file.read_text(encoding="utf-8")) if file else {}
    result = client.post(
        "workflow-service",
        f"instances/{instance_id}/tasks/{task_id}/complete",
        json_body={"completed_by": completed_by, "data": data, "signature_id": signature_id},
    )
    emit(result, output_format=output_format(ctx))


# --- Upload and administrative operations (P96-S2, ADR 0249/P93-S2, P95-S1c) --


@definitions_app.command("upload")
def definitions_upload(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, help="BPMN-Datei"),
    name: str = typer.Option(..., "--name", help="Prozessfamilie; gleicher Name = neue Version."),
) -> None:
    client = get_client()
    result = client.post_files(
        "workflow-service",
        "process-definitions",
        data={"name": name},
        files={"bpmn_xml": (file.name, file.read_bytes(), "application/xml")},
    )
    emit(result, output_format=output_format(ctx))


def _operation(path: str, body: dict, ctx: typer.Context) -> None:
    client = get_client()
    emit(client.post("workflow-service", path, json_body=body), output_format=output_format(ctx))


@instances_app.command("skip")
def instances_skip(
    ctx: typer.Context,
    instance_id: str,
    task_id: str,
    reason: str = typer.Option(..., "--reason"),
    expected_version: int | None = typer.Option(None, "--expected-version"),
) -> None:
    """Schliesst eine bereite Aufgabe administrativ ab (workflow.instance_modify)."""
    _operation(
        f"instances/{instance_id}/tasks/{task_id}/skip",
        {"reason": reason, "expected_version": expected_version},
        ctx,
    )


@instances_app.command("send-back")
def instances_send_back(
    ctx: typer.Context,
    instance_id: str,
    target_task_id: str = typer.Option(..., "--to", help="ID einer abgeschlossenen Aufgabe"),
    reason: str = typer.Option(..., "--reason"),
    expected_version: int | None = typer.Option(None, "--expected-version"),
) -> None:
    _operation(
        f"instances/{instance_id}/send-back",
        {"target_task_id": target_task_id, "reason": reason, "expected_version": expected_version},
        ctx,
    )


@instances_app.command("migration-preview")
def instances_migration_preview(
    ctx: typer.Context,
    instance_id: str,
    target_definition_id: int = typer.Option(..., "--to"),
) -> None:
    _operation(
        f"instances/{instance_id}/migration-preview",
        {"target_definition_id": target_definition_id},
        ctx,
    )


@instances_app.command("migrate")
def instances_migrate(
    ctx: typer.Context,
    instance_id: str,
    target_definition_id: int = typer.Option(..., "--to"),
    reason: str = typer.Option(..., "--reason"),
    expected_version: int | None = typer.Option(None, "--expected-version"),
) -> None:
    _operation(
        f"instances/{instance_id}/migrate",
        {
            "target_definition_id": target_definition_id,
            "reason": reason,
            "expected_version": expected_version,
        },
        ctx,
    )


@instances_app.command("terminate")
def instances_terminate(
    ctx: typer.Context,
    instance_id: str,
    reason: str = typer.Option(..., "--reason"),
    expected_version: int | None = typer.Option(None, "--expected-version"),
) -> None:
    """Beendet eine laufende Instanz endgueltig - nicht umkehrbar."""
    _operation(
        f"instances/{instance_id}/terminate",
        {"reason": reason, "expected_version": expected_version},
        ctx,
    )
