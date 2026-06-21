"""Rate limiter async por host de Steam.

Cada host de Steam tiene su propio rate limit, así que se throttlea por separado:
- ``steamcommunity.com`` (priceoverview + search/render): el límite más agresivo (~20 req/min).
- ``store.steampowered.com`` (appdetails): límite más laxo pero igual existe.

``AsyncThrottle`` combina un **semáforo** (concurrencia) con un **intervalo mínimo**
entre el inicio de requests consecutivos. Se usa como context manager async::

    async with get_throttle(url):
        ...  # request a Steam
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit


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


from .config import settings  # noqa: E402  (import tardío para evitar ciclos)

# Registro de throttles, uno por host. Se crean de forma perezosa.
_throttles: dict[str, AsyncThrottle] = {}


def _interval_for(host: str) -> float:
    """Intervalo mínimo según el host (community es el más restrictivo)."""
    if "steamcommunity" in host:
        return settings.community_interval
    return settings.store_interval


def get_throttle(url: str) -> AsyncThrottle:
    """Devuelve (creando si hace falta) el throttle del host de ``url``."""
    host = urlsplit(url).netloc.lower()
    throttle = _throttles.get(host)
    if throttle is None:
        throttle = AsyncThrottle(_interval_for(host), concurrency=settings.throttle_concurrency)
        _throttles[host] = throttle
    return throttle


def reset_throttles() -> None:
    """Limpia el registro (los throttles se recrean con la config actual)."""
    _throttles.clear()
