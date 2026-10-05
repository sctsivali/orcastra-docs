"""Retry with exponential backoff for flaky network steps (apt, image pulls, first HTTP
probes). DNS inside freshly started LXD instances is known to fail for the first seconds."""
from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def retry(fn: Callable[[], T], *, attempts: int = 5, delay: float = 3.0, backoff: float = 2.0,
          max_delay: float = 60.0, ok: Optional[Callable[[T], bool]] = None,
          log=None, what: str = "operation") -> T:
    """Call `fn` until `ok(result)` is true (default: truthy result) or attempts run out.
    Exceptions count as a failed attempt and the last one is re-raised. Returns the last
    result either way so the caller can report its error detail."""
    accept = ok or bool
    wait = delay
    result: Optional[T] = None
    for i in range(1, attempts + 1):
        try:
            result = fn()
            if accept(result):
                return result
        except Exception:  # noqa: BLE001 - re-raised after the last attempt
            if i == attempts:
                raise
        if i < attempts:
            if log is not None:
                log.detail(f"{what}: attempt {i}/{attempts} failed, retrying in {int(wait)}s")
            time.sleep(wait)
            wait = min(wait * backoff, max_delay)
    return result  # type: ignore[return-value]


def wait_until(predicate: Callable[[], bool], *, timeout: float, interval: float = 3.0) -> bool:
    """Poll `predicate` until true or `timeout` seconds pass. Exceptions count as false."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
