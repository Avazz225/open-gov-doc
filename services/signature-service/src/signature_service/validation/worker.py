"""Bounded execution of incoming-signature validations (ADR 0248 Decision 6).

Jobs wait in a bounded `asyncio.Queue`; a few runner tasks take them one at a
time. The CPU-heavy validation itself runs in a thread pool with its own
event loop per run, so the signing API on the main loop stays responsive. A
full queue makes `submit` wait, which in turn holds back the event consumer -
results are delayed, nothing is dropped and nothing else is blocked.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from signature_service.validation.interface import (
    SignatureValidator,
    ValidationOutcome,
    ValidationPolicy,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationJob:
    document_id: str
    version_number: int
    content_type: str | None
    filename: str | None
    trigger: str  # "event", "revalidation" or "backfill"
    # Who asked (re-validation, backfill); None for the event path.
    requested_by: str | None = None


class ValidationWorker:
    def __init__(
        self,
        *,
        queue_size: int,
        workers: int,
        process: Callable[[ValidationJob], Awaitable[None]],
    ) -> None:
        self._queue: asyncio.Queue[ValidationJob] = asyncio.Queue(maxsize=queue_size)
        self._workers = workers
        self._process = process
        self._executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="incoming-signature"
        )
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._run()) for _ in range(self._workers)]

    async def submit(self, job: ValidationJob) -> None:
        await self._queue.put(job)

    def pending(self) -> int:
        return self._queue.qsize()

    async def join(self) -> None:
        """Waits until every submitted job is done (tests)."""
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process(job)
            except Exception:
                logger.exception(
                    "incoming_signature_job_failed",
                    extra={"document_id": job.document_id, "version": job.version_number},
                )
            finally:
                self._queue.task_done()

    async def run_validator(
        self, validator: SignatureValidator, data: bytes, policy: ValidationPolicy
    ) -> ValidationOutcome:
        """Runs one validation in the pool, on a fresh event loop owned by that
        thread (the fenced aiohttp session is created and closed there)."""
        return await self.run_in_pool(lambda: validator.validate(data, policy))

    async def run_in_pool(
        self, make_coroutine: Callable[[], Awaitable[ValidationOutcome]]
    ) -> ValidationOutcome:
        """Any validation coroutine in the pool (e.g. a detached CAdES check,
        which needs the signed document's bytes too)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: asyncio.run(make_coroutine()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        # A validation still running in a thread finishes on its own (it is
        # bounded by its time limit); don't wait for it on shutdown.
        self._executor.shutdown(wait=False, cancel_futures=True)
