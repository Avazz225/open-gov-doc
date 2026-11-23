"""Bounded shutdown steps (P91-S4): a step that hangs is logged by name and
skipped instead of hanging the whole shutdown. Pure, no DB."""

import asyncio
import logging

from document_service import main


async def test_a_hanging_step_is_logged_and_skipped(monkeypatch, caplog):
    monkeypatch.setattr(main, "_SHUTDOWN_STEP_TIMEOUT_SECONDS", 0.05)
    finished: list[str] = []

    async def hangs():
        await asyncio.sleep(10)

    async def quick():
        finished.append("quick")

    with caplog.at_level(logging.ERROR):
        await main._bounded_shutdown_step("hangs", hangs())
        await main._bounded_shutdown_step("quick", quick())
    assert finished == ["quick"]
    assert "shutdown_step_timeout: hangs" in caplog.text


async def test_a_cancelled_task_and_a_failing_step_do_not_stop_the_rest(caplog):
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()
    await main._bounded_shutdown_step("cancelled task", task)

    async def fails():
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        await main._bounded_shutdown_step("fails", fails())
    assert "shutdown_step_failed: fails" in caplog.text
