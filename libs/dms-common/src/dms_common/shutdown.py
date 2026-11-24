"""Bounded lifespan shutdown steps (P91-S6, first in document-service in P91-S4).

A FastAPI lifespan's shutdown awaits each client's `close()`/`stop()` in turn.
If one of them never completes, a pod's termination stalls until it is
killed, and a test's `TestClient.__exit__` hangs forever - seen in
document-service, mail-connector and workflow-service, with the event loop
idle and no step identifiable from a thread dump. Wrapping every step bounds
it and names the step in the log, so the next occurrence says which one.
"""

import asyncio
import logging
from collections.abc import Awaitable

logger = logging.getLogger(__name__)

DEFAULT_STEP_TIMEOUT_SECONDS = 10.0


async def bounded_shutdown_step(
    name: str,
    awaitable: Awaitable[object],
    *,
    limit_seconds: float = DEFAULT_STEP_TIMEOUT_SECONDS,
) -> None:
    """Await one shutdown step with a time limit. A timeout or an error is
    logged with the step's name and doesn't stop the remaining steps; a
    cancelled background task (the expected outcome after `task.cancel()`) is
    silent."""
    try:
        async with asyncio.timeout(limit_seconds):
            await awaitable
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        logger.error("shutdown_step_timeout: %s after %ss", name, limit_seconds)
    except Exception:
        logger.exception("shutdown_step_failed: %s", name)
