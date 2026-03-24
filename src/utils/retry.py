from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable)


def retry(max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """Decorator: retry with exponential backoff on any exception."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts:
                        logger.error(
                            "All retry attempts exhausted",
                            extra={"function": fn.__name__, "attempts": max_attempts, "error": str(exc)},
                        )
                        raise
                    logger.warning(
                        "Retrying after error",
                        extra={"function": fn.__name__, "attempt": attempt, "wait_s": wait, "error": str(exc)},
                    )
                    time.sleep(wait)
                    wait *= backoff

        return wrapper  # type: ignore[return-value]

    return decorator
