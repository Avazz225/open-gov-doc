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
