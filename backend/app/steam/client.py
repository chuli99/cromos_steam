"""Cliente httpx async compartido hacia Steam.

Centraliza timeout, User-Agent y la lógica de reintentos con backoff exponencial
ante 429 (rate limit) y errores 5xx transitorios.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..config import settings
from ..throttle import get_throttle

# Cliente único reutilizado (mantiene el pool de conexiones).
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Devuelve el cliente httpx compartido, creándolo si hace falta."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    """Cierra el cliente (llamado en el shutdown de FastAPI)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _backoff(attempt: int) -> float:
    """Backoff exponencial con tope: ``min(base ** intento, backoff_max)``."""
    return min(settings.backoff_base ** attempt, settings.backoff_max)


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    """Segundos a esperar ante un 429: honra ``Retry-After`` si viene, si no backoff."""
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return min(float(raw), settings.backoff_max)
        except ValueError:
            pass
    return _backoff(attempt)


async def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET que devuelve JSON, con throttle por host, reintentos y backoff.

    - **Throttle por host**: cada request a Steam respeta el intervalo mínimo del
      host (community/store se limitan por separado), evitando ráfagas que disparan 429.
    - Reintenta ante 429 (honrando ``Retry-After``), 5xx y errores de red.

    Lanza la última excepción si se agotan los reintentos.
    """
    client = get_client()
    throttle = get_throttle(url)
    last_exc: Exception | None = None

    for attempt in range(settings.max_retries):
        try:
            # El throttle espacia el inicio de cada request al host.
            async with throttle:
                resp = await client.get(url, params=params)

            if resp.status_code == 429:
                # Rate limited: esperar (Retry-After o backoff) y reintentar.
                last_exc = httpx.HTTPStatusError(
                    "429 Too Many Requests", request=resp.request, response=resp
                )
                await asyncio.sleep(_retry_after(resp, attempt))
                continue

            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPStatusError as exc:
            last_exc = exc
            # 5xx: reintentar; otros 4xx: abortar.
            if exc.response is not None and 500 <= exc.response.status_code < 600:
                await asyncio.sleep(_backoff(attempt))
                continue
            raise
        except httpx.TransportError as exc:
            # Timeout / error de red: reintentar.
            last_exc = exc
            await asyncio.sleep(_backoff(attempt))

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"No se pudo obtener {url} tras {settings.max_retries} intentos")
