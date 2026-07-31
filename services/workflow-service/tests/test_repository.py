import pytest
from workflow_service import repository


async def test_create_process_definition_auto_detects_process_id(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    assert definition.bpmn_process_id == "Process_cozt5fu"
    assert definition.name == "Approval"


async def test_create_process_definition_duplicate_name_raises(session, manual_task_bpmn):
    await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    with pytest.raises(repository.DuplicateNameError):
        await repository.create_process_definition(
            session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
        )


async def test_create_process_definition_invalid_bpmn_raises(session):
    with pytest.raises(repository.InvalidBpmnError):
        await repository.create_process_definition(
            session, name="Kaputt", bpmn_xml="not valid xml", process_id=None
        )


async def test_get_process_definition_unknown_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_process_definition(session, 999999)


async def test_list_process_definitions(session, manual_task_bpmn, no_tasks_bpmn):
    await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    await repository.create_process_definition(
        session, name="NoTasks", bpmn_xml=no_tasks_bpmn, process_id=None
    )
    definitions = await repository.list_process_definitions(session)
    assert {d.name for d in definitions} == {"Approval", "NoTasks"}


async def test_delete_process_definition_without_instances_succeeds(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    await repository.delete_process_definition(session, definition.id)
    with pytest.raises(repository.NotFoundError):
        await repository.get_process_definition(session, definition.id)


async def test_delete_process_definition_with_instance_raises(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    await repository.start_instance(
        session, definition.id, created_by="alice", business_key=None, initial_data={}
    )
    with pytest.raises(repository.ProcessDefinitionInUseError):
        await repository.delete_process_definition(session, definition.id)


async def test_start_instance_with_manual_task_stays_running(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    instance = await repository.start_instance(
        session, definition.id, created_by="alice", business_key="doc-1", initial_data={}
    )
    assert instance.status == "running"
    assert instance.business_key == "doc-1"
    assert instance.completed_at is None


async def test_start_instance_fully_automatic_completes_immediately(session, no_tasks_bpmn):
    definition = await repository.create_process_definition(
        session, name="NoTasks", bpmn_xml=no_tasks_bpmn, process_id=None
    )
    instance = await repository.start_instance(
        session, definition.id, created_by="alice", business_key=None, initial_data={}
    )
    assert instance.status == "completed"
    assert instance.completed_at is not None


async def test_start_instance_unknown_definition_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.start_instance(
            session, 999999, created_by="alice", business_key=None, initial_data={}
        )


async def test_get_ready_tasks_returns_manual_task(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    instance = await repository.start_instance(
        session, definition.id, created_by="alice", business_key=None, initial_data={}
    )
    tasks = await repository.get_ready_tasks(session, instance.id)
    assert len(tasks) == 1
    assert tasks[0].name == "manual"


async def test_get_ready_tasks_unknown_instance_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_ready_tasks(session, "does-not-exist")


async def test_complete_task_finishes_the_instance(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    instance = await repository.start_instance(
        session, definition.id, created_by="alice", business_key=None, initial_data={}
    )
    tasks = await repository.get_ready_tasks(session, instance.id)

    updated = await repository.complete_task(
        session, instance.id, tasks[0].id, completed_by="bob", data={"decision": "approved"}
    )
    assert updated.status == "completed"
    assert updated.completed_at is not None
    assert await repository.get_ready_tasks(session, instance.id) == []


async def test_complete_task_unknown_task_id_raises(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    instance = await repository.start_instance(
        session, definition.id, created_by="alice", business_key=None, initial_data={}
    )
    with pytest.raises(repository.TaskNotReadyError):
        await repository.complete_task(
            session, instance.id, "does-not-exist", completed_by="bob", data={}
        )


async def test_complete_task_already_completed_raises(session, manual_task_bpmn):
    definition = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    instance = await repository.start_instance(
        session, definition.id, created_by="alice", business_key=None, initial_data={}
    )
    tasks = await repository.get_ready_tasks(session, instance.id)
    await repository.complete_task(session, instance.id, tasks[0].id, completed_by="bob", data={})
    with pytest.raises(repository.TaskNotReadyError):
        await repository.complete_task(
            session, instance.id, tasks[0].id, completed_by="bob", data={}
        )


async def test_list_instances_filters_by_status(session, manual_task_bpmn, no_tasks_bpmn):
    running_def = await repository.create_process_definition(
        session, name="Approval", bpmn_xml=manual_task_bpmn, process_id=None
    )
    completed_def = await repository.create_process_definition(
        session, name="NoTasks", bpmn_xml=no_tasks_bpmn, process_id=None
    )
    await repository.start_instance(
        session, running_def.id, created_by="alice", business_key=None, initial_data={}
    )
    await repository.start_instance(
        session, completed_def.id, created_by="alice", business_key=None, initial_data={}
    )

    running = await repository.list_instances(session, status="running")
    completed = await repository.list_instances(session, status="completed")
    assert len(running) == 1
    assert len(completed) == 1
