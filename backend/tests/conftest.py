"""Fixtures de integración: un "Steam falso" servido por httpx.MockTransport.

La idea es ejercitar el stack real del backend (router -> caché -> store/market
-> parser -> cliente httpx con reintentos) interceptando únicamente la capa de red.
El cliente httpx compartido se reemplaza por uno con ``MockTransport`` que enruta
según el path del endpoint de Steam.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import unquote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app import cache as cache_module
from app import throttle as throttle_module
from app.config import settings as app_settings
from app.main import app
from app.steam import client as steam_client


class FakeSteam:
    """Steam simulado: se configura con las respuestas crudas de cada endpoint.

    - ``appdetails``: dict tal como lo devuelve la Storefront API (``{str(appid): ...}``).
    - ``search``: dict de search/render (``{"results": [...]}``).
    - ``prices``: mapa ``market_hash_name -> JSON crudo de priceoverview``.
    - ``nameids``: mapa ``market_hash_name -> item_nameid`` (página del listing).
    - ``histograms``: mapa ``item_nameid -> JSON crudo de itemordershistogram``.
    - ``fail_sequence``: por path, códigos de error a devolver antes de la respuesta
      normal (para simular 429/5xx transitorios y verificar los reintentos).
    - ``calls``: contador de requests por path (para asserts de caché/throttle).
    """

    def __init__(self) -> None:
        self.appdetails: dict | None = None
        self.search: dict | None = None
        self.foil_search: dict | None = None
        self.prices: dict[str, dict] = {}
        self.nameids: dict[str, int] = {}
        self.histograms: dict[int, dict] = {}
        self.fail_sequence: dict[str, list[int]] = {}
        self.calls: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] = self.calls.get(path, 0) + 1

        # Errores transitorios encolados para este path (se consumen de a uno).
        seq = self.fail_sequence.get(path)
        if seq:
            return httpx.Response(seq.pop(0), json={})

        if path.endswith("/api/appdetails"):
            return httpx.Response(200, json=self.appdetails or {})
        if path.endswith("/market/search/render/"):
            # Normales vs foils se distinguen por el parámetro cardborder.
            border = request.url.params.get("category_753_cardborder[]", "")
            if border == "tag_cardborder_1":
                return httpx.Response(200, json=self.foil_search or {"results": []})
            return httpx.Response(200, json=self.search or {"results": []})
        if path.endswith("/market/priceoverview/"):
            name = request.url.params.get("market_hash_name", "")
            return httpx.Response(200, json=self.prices.get(name, {"success": False}))
        if "/market/listings/" in path:
            # Página HTML del listing: expone el item_nameid en Market_LoadOrderSpread.
            hash_name = unquote(path.rsplit("/", 1)[-1])
            nameid = self.nameids.get(hash_name)
            body = (
                f"<html>Market_LoadOrderSpread( {nameid} );</html>"
                if nameid is not None
                else "<html>sin listado</html>"
            )
            return httpx.Response(200, text=body)
        if path.endswith("/market/itemordershistogram"):
            nameid = int(request.url.params.get("item_nameid", "0"))
            return httpx.Response(200, json=self.histograms.get(nameid, {"success": 0}))

        return httpx.Response(404, json={})


# --- Helpers para armar las respuestas crudas de Steam ---

def make_appdetails(
    appid: int,
    name: str = "Juego Test",
    final_cents: int | None = 299,
    success: bool = True,
    app_type: str = "game",
) -> dict:
    """Arma el JSON de appdetails.

    ``final_cents=None`` simula juego gratuito; ``app_type="dlc"`` simula un DLC.
    """
    if not success:
        return {str(appid): {"success": False}}
    data: dict = {"name": name, "type": app_type}
    if final_cents is not None:
        data["price_overview"] = {"final": final_cents, "currency": "USD"}
    return {str(appid): {"success": True, "data": data}}


def make_search(hash_names: list[str]) -> dict:
    """Arma el JSON de search/render a partir de los market_hash_name."""
    return {"results": [{"hash_name": h} for h in hash_names]}


def make_price(lowest: str = "$0.16", median: str = "$0.16", volume: str = "503") -> dict:
    """Arma el JSON de priceoverview de un cromo con precio."""
    return {"success": True, "lowest_price": lowest, "median_price": median, "volume": volume}


def make_histogram(highest_buy_cents: int | None) -> dict:
    """Arma el JSON de itemordershistogram (``highest_buy_order`` viene en centavos)."""
    return {
        "success": 1,
        "highest_buy_order": str(highest_buy_cents) if highest_buy_cents is not None else None,
    }


@pytest.fixture
def steam() -> FakeSteam:
    """Steam falso, fresco en cada test."""
    return FakeSteam()


@pytest.fixture
async def client(steam: FakeSteam) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP async contra la app, con la red de Steam mockeada.

    Reemplaza el cliente httpx compartido por uno con ``MockTransport``, limpia la
    caché y anula los intervalos de throttle (por host) para que los tests sean rápidos.
    """
    steam_client._client = httpx.AsyncClient(transport=httpx.MockTransport(steam.handler))

    await cache_module.clear()
    # Throttles por host a 0s: se recrean perezosamente con la config actual.
    original = (app_settings.community_interval, app_settings.store_interval)
    app_settings.community_interval = 0.0
    app_settings.store_interval = 0.0
    throttle_module.reset_throttles()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    await steam_client.close_client()
    app_settings.community_interval, app_settings.store_interval = original
    throttle_module.reset_throttles()
    await cache_module.clear()
