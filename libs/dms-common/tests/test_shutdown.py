"""Bounded shutdown steps (P91-S6; moved here from document-service's P91-S4
tests). Pure, no DB."""

import asyncio
import logging

from dms_common.shutdown import bounded_shutdown_step


async def test_step_that_never_finishes_is_cut_off_and_named(caplog):
    caplog.set_level(logging.ERROR)
    finished: list[str] = []

    async def quick():
        finished.append("quick")

    await bounded_shutdown_step("hangs", asyncio.Event().wait(), limit_seconds=0.05)
    await bounded_shutdown_step("quick", quick())
    assert finished == ["quick"]
    assert "shutdown_step_timeout: hangs" in caplog.text


async def test_failing_step_is_logged_and_does_not_raise(caplog):
    async def boom():
        raise RuntimeError("x")

    await bounded_shutdown_step("boom", boom())
    assert "shutdown_step_failed: boom" in caplog.text


async def test_cancelled_task_is_silent(caplog):
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()
    await bounded_shutdown_step("task", task)
    assert caplog.text == ""
