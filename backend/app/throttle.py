"""Rate limiter async por host de Steam, con cooldown adaptativo.

Cada host de Steam tiene su propio rate limit, así que se throttlea por separado:
- ``steamcommunity.com`` (priceoverview + search/render): ~20 req/min.
- ``store.steampowered.com`` (appdetails): ~200 req/5 min.

Los intervalos por defecto se eligen **por debajo** del máximo teórico (dejar margen,
porque Steam usa una ventana deslizante y baja el límite en horario pico). Además el
throttle es **adaptativo**: ante un 429 (vía ``penalize``) el host entra en *cooldown*
y sube su intervalo; con cada éxito (``relax``) el intervalo decae hacia el base.

Se usa como context manager async::

    async with get_throttle(url):
        ...  # request a Steam
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

# Factores del comportamiento adaptativo.
_BUMP = 1.5    # cuánto sube el intervalo ante un 429
_DECAY = 0.9   # cuánto baja por cada éxito
_MAX_FACTOR = 4.0  # tope del intervalo = base * _MAX_FACTOR


class AsyncThrottle:
    """Limitador de tasa async (semáforo + intervalo mínimo) con cooldown adaptativo."""

    def __init__(self, interval: float, concurrency: int = 1) -> None:
        self._base = interval
        self._interval = interval
        self._max_interval = interval * _MAX_FACTOR
        self._cooldown_until = 0.0
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def __aenter__(self) -> "AsyncThrottle":
        await self._semaphore.acquire()
        # El lock garantiza que el espaciado/cooldown se respete aun con concurrencia.
        async with self._lock:
            now = time.monotonic()
            spacing_wait = self._interval - (now - self._last_call)
            cooldown_wait = self._cooldown_until - now
            wait = max(spacing_wait, cooldown_wait, 0.0)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._semaphore.release()

    def penalize(self, cooldown: float) -> None:
        """Tras un 429: pausa el host ``cooldown`` segundos y sube el intervalo."""
        self._cooldown_until = time.monotonic() + cooldown
        self._interval = min(self._interval * _BUMP, self._max_interval)

    def relax(self) -> None:
        """Tras un éxito: el intervalo decae gradualmente hacia el base."""
        if self._interval > self._base:
            self._interval = max(self._base, self._interval * _DECAY)


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
