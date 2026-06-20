"""Rate limiter async para el endpoint priceoverview de Steam.

priceoverview tiene el límite más agresivo (~20 req/min). Este throttle combina:
- un **semáforo** que limita la concurrencia, y
- un **intervalo mínimo** entre el inicio de requests consecutivos.

Se usa como context manager async::

    async with priceoverview_throttle:
        ...  # request a Steam
"""
from __future__ import annotations

import asyncio
import time


class AsyncThrottle:
    """Limitador de tasa async (semáforo + intervalo mínimo entre llamadas)."""

    def __init__(self, interval: float, concurrency: int = 1) -> None:
        self._interval = interval
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def __aenter__(self) -> "AsyncThrottle":
        await self._semaphore.acquire()
        # El lock garantiza que el intervalo se respete aun con concurrencia.
        async with self._lock:
            elapsed = time.monotonic() - self._last_call
            wait = self._interval - elapsed
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._semaphore.release()


# Throttle global compartido para priceoverview. Se inicializa en main.py con los
# valores de config; mientras tanto queda con defaults seguros.
from .config import settings  # noqa: E402  (import tardío para evitar ciclos)

priceoverview_throttle = AsyncThrottle(
    interval=settings.throttle_interval,
    concurrency=settings.throttle_concurrency,
)
