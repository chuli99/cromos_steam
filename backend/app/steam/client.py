"""Cliente httpx async compartido hacia Steam.

Centraliza timeout, User-Agent y la lógica de reintentos con backoff exponencial
ante 429 (rate limit) y errores 5xx transitorios.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..config import settings

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


async def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET que devuelve JSON, con reintentos y backoff exponencial.

    Reintenta ante 429 y 5xx esperando ``backoff_base ** intento`` segundos.
    Lanza la última excepción si se agotan los reintentos.
    """
    client = get_client()
    last_exc: Exception | None = None

    for attempt in range(settings.max_retries):
        try:
            resp = await client.get(url, params=params)

            if resp.status_code == 429:
                # Rate limited: esperar y reintentar.
                await asyncio.sleep(settings.backoff_base ** attempt)
                last_exc = httpx.HTTPStatusError(
                    "429 Too Many Requests", request=resp.request, response=resp
                )
                continue

            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPStatusError as exc:
            last_exc = exc
            # 5xx: reintentar; otros 4xx: abortar.
            if exc.response is not None and 500 <= exc.response.status_code < 600:
                await asyncio.sleep(settings.backoff_base ** attempt)
                continue
            raise
        except httpx.TransportError as exc:
            # Timeout / error de red: reintentar.
            last_exc = exc
            await asyncio.sleep(settings.backoff_base ** attempt)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"No se pudo obtener {url} tras {settings.max_retries} intentos")
