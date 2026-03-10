import asyncio
import time
from typing import Any, Callable, TypeVar

from worker.logging.json_logger import get_logger

logger = get_logger("retry")
T = TypeVar("T")


def retry_sync(
    fn: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    backoff: float = 2.0,
    timeout: float = 60.0,
    **kwargs: Any,
) -> T:
    last_exc: Exception | None = None
    deadline = time.monotonic() + timeout
    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Attempt %d/%d failed: %s",
                attempt,
                max_retries,
                exc,
                extra={"action": "retry", "step": f"attempt_{attempt}"},
            )
            if attempt < max_retries:
                wait = backoff ** (attempt - 1)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(wait, remaining))
    raise RuntimeError(
        f"All {max_retries} retries exhausted for {fn.__name__}"
    ) from last_exc


async def retry_async(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    backoff: float = 2.0,
    timeout: float = 60.0,
    **kwargs: Any,
) -> Any:
    last_exc: Exception | None = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    for attempt in range(1, max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Attempt %d/%d failed: %s",
                attempt,
                max_retries,
                exc,
                extra={"action": "retry", "step": f"attempt_{attempt}"},
            )
            if attempt < max_retries:
                wait = backoff ** (attempt - 1)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(wait, remaining))
    raise RuntimeError(
        f"All {max_retries} retries exhausted for {fn.__name__}"
    ) from last_exc
