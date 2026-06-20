"""Wrapper de caché en memoria (aiocache) con TTL configurable.

En v1 no hay base de datos: se usa caché en memoria del proceso. La función
``get_or_set`` implementa el patrón "read-through": devuelve el valor cacheado o
ejecuta la corutina, guarda el resultado y lo devuelve.
"""
from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

from aiocache import Cache

T = TypeVar("T")

# Caché en memoria del proceso (sin serialización: guarda objetos Python tal cual).
_cache: Cache = Cache(Cache.MEMORY)

# Centinela para distinguir "no está en caché" de un valor cacheado None.
_MISS = object()


async def get_or_set(
    key: str,
    factory: Callable[[], Awaitable[T]],
    ttl: int,
) -> T:
    """Devuelve el valor cacheado para ``key`` o lo calcula con ``factory``.

    El resultado de ``factory`` se cachea con el ``ttl`` dado (en segundos).
    """
    cached = await _cache.get(key, default=_MISS)
    if cached is not _MISS:
        return cached  # type: ignore[return-value]

    value = await factory()
    await _cache.set(key, value, ttl=ttl)
    return value


async def clear() -> None:
    """Vacía la caché (útil para tests)."""
    await _cache.clear()
