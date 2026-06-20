"""Tests de integración de GET /api/profit/{appid}.

Ejercitan el stack completo (router + caché + throttle + cliente httpx con
reintentos + parser) contra un Steam falso (``MockTransport``), sin tocar la red.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.steam import client as steam_client
from conftest import FakeSteam, make_appdetails, make_price, make_search

APPID = 292030
CARDS = [f"{APPID}-{n}" for n in ("Triss", "Geralt", "Yennefer", "Ciri", "Dijkstra", "Vesemir")]


async def test_profit_ok(client: AsyncClient, steam: FakeSteam):
    """Camino feliz: juego con precio y 6 cromos con precio -> ProfitResponse completo."""
    steam.appdetails = make_appdetails(APPID, name="The Witcher 3", final_cents=299)
    steam.search = make_search(CARDS)
    steam.prices = {name: make_price(lowest="$0.16") for name in CARDS}

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 200

    body = resp.json()
    assert body["appid"] == APPID
    assert body["game_name"] == "The Witcher 3"
    assert body["game_price"] == 2.99
    assert body["currency"] == 1
    assert body["total_cards"] == 6
    assert body["cards_dropped"] == 3            # round(6 * 0.5)
    assert body["avg_card_price"] == pytest.approx(0.16)
    assert body["gross_card_value"] == pytest.approx(0.48, abs=1e-4)   # 0.16 * 3
    assert body["net_card_value"] == pytest.approx(0.48 / 1.15, abs=1e-4)
    assert body["profit"] == pytest.approx(0.48 / 1.15 - 2.99, abs=1e-4)
    assert body["profit_positive"] is False
    # El desglose incluye un cromo por cada carta, con el nombre sin el prefijo "{appid}-".
    assert len(body["cards"]) == 6
    assert {c["name"] for c in body["cards"]} == {"Triss", "Geralt", "Yennefer", "Ciri", "Dijkstra", "Vesemir"}


async def test_appid_inexistente_404(client: AsyncClient, steam: FakeSteam):
    """appdetails con ``success: false`` -> 404."""
    steam.appdetails = make_appdetails(APPID, success=False)

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 404
    assert "appid" in resp.json()["detail"].lower()


async def test_juego_gratuito_422(client: AsyncClient, steam: FakeSteam):
    """Juego sin ``price_overview`` (free-to-play) -> 422."""
    steam.appdetails = make_appdetails(APPID, name="Team Fortress 2", final_cents=None)

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 422
    assert "gratuito" in resp.json()["detail"].lower()


async def test_dlc_422_no_consulta_cromos(client: AsyncClient, steam: FakeSteam):
    """Un DLC -> 422 y se corta antes de pedir la lista/precios de cromos."""
    steam.appdetails = make_appdetails(APPID, app_type="dlc", final_cents=499)

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 422
    assert "dlc" in resp.json()["detail"].lower()
    # No se gastó ninguna llamada de cromos: se cortó en appdetails.
    assert "/market/search/render/" not in steam.calls
    assert "/market/priceoverview/" not in steam.calls


async def test_juego_sin_cromos_404(client: AsyncClient, steam: FakeSteam):
    """Juego con precio pero sin cromos -> 404."""
    steam.appdetails = make_appdetails(APPID, final_cents=999)
    steam.search = make_search([])

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 404
    assert "cromos" in resp.json()["detail"].lower()


async def test_cromos_sin_precio_no_rompe(client: AsyncClient, steam: FakeSteam):
    """Si ningún cromo tiene precio (success:false), el cálculo no aborta: avg 0."""
    steam.appdetails = make_appdetails(APPID, final_cents=200)
    steam.search = make_search(CARDS)
    steam.prices = {}  # priceoverview devuelve {"success": false} para todos

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 200

    body = resp.json()
    assert body["avg_card_price"] == 0.0
    assert body["gross_card_value"] == 0.0
    assert body["profit"] == pytest.approx(-2.0)
    assert body["profit_positive"] is False
    assert all(c["success"] is False for c in body["cards"])


async def test_promedio_excluye_cromos_sin_precio(client: AsyncClient, steam: FakeSteam):
    """El promedio se calcula solo sobre cromos con precio (mezcla de con/sin precio)."""
    steam.appdetails = make_appdetails(APPID, final_cents=100)
    steam.search = make_search(CARDS[:4])
    # 2 con precio (0.20) y 2 sin precio -> avg = 0.20.
    steam.prices = {CARDS[0]: make_price(lowest="$0.20"), CARDS[2]: make_price(lowest="$0.20")}

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 200

    body = resp.json()
    assert body["total_cards"] == 4
    assert body["cards_dropped"] == 2
    assert body["avg_card_price"] == pytest.approx(0.20)


async def test_cache_evita_segunda_llamada_a_steam(client: AsyncClient, steam: FakeSteam):
    """La segunda request al mismo appid se sirve de caché: no vuelve a pegarle a Steam."""
    steam.appdetails = make_appdetails(APPID, final_cents=299)
    steam.search = make_search(CARDS[:2])
    steam.prices = {name: make_price() for name in CARDS[:2]}

    first = await client.get(f"/api/profit/{APPID}")
    assert first.status_code == 200

    # Tras la 1ª llamada: 1 appdetails + 1 search + 2 priceoverview.
    assert steam.calls["/api/appdetails"] == 1
    assert steam.calls["/market/search/render/"] == 1
    assert steam.calls["/market/priceoverview/"] == 2

    second = await client.get(f"/api/profit/{APPID}")
    assert second.status_code == 200
    assert second.json() == first.json()

    # Los contadores no cambian: todo salió de caché.
    assert steam.calls["/api/appdetails"] == 1
    assert steam.calls["/market/search/render/"] == 1
    assert steam.calls["/market/priceoverview/"] == 2


async def test_reintenta_ante_5xx_transitorio(
    client: AsyncClient, steam: FakeSteam, monkeypatch: pytest.MonkeyPatch
):
    """Un 5xx transitorio en appdetails se reintenta y termina respondiendo 200."""
    # Anula el backoff para que el reintento sea instantáneo.
    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(steam_client.asyncio, "sleep", _no_sleep)

    # appdetails falla 2 veces (500) y a la 3ª responde normal (max_retries=3).
    steam.fail_sequence["/api/appdetails"] = [500, 500]
    steam.appdetails = make_appdetails(APPID, final_cents=299)
    steam.search = make_search(CARDS[:1])
    steam.prices = {CARDS[0]: make_price()}

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 200
    # Se llamó 3 veces a appdetails: 2 fallos + 1 éxito.
    assert steam.calls["/api/appdetails"] == 3


async def test_sin_include_foils_no_consulta_foils(client: AsyncClient, steam: FakeSteam):
    """Sin ``include_foils`` la respuesta no trae foils y solo se pide la lista normal."""
    steam.appdetails = make_appdetails(APPID, final_cents=299)
    steam.search = make_search(CARDS[:2])
    steam.prices = {name: make_price() for name in CARDS[:2]}

    resp = await client.get(f"/api/profit/{APPID}")
    assert resp.status_code == 200
    assert resp.json()["foils"] is None
    # Solo una búsqueda (la de cromos normales), no la de foils.
    assert steam.calls["/market/search/render/"] == 1


async def test_include_foils_agrega_resumen(client: AsyncClient, steam: FakeSteam):
    """Con ``include_foils=true`` se agrega el resumen de foils (cálculo aparte)."""
    foils = [f"{APPID}-Triss (Foil)", f"{APPID}-Geralt (Foil)"]
    steam.appdetails = make_appdetails(APPID, final_cents=299)
    steam.search = make_search(CARDS[:2])
    steam.foil_search = make_search(foils)
    steam.prices = {
        CARDS[0]: make_price(lowest="$0.16"),
        CARDS[1]: make_price(lowest="$0.16"),
        foils[0]: make_price(lowest="$2.00"),
        foils[1]: make_price(lowest="$4.00"),
    }

    resp = await client.get(f"/api/profit/{APPID}?include_foils=true")
    assert resp.status_code == 200

    body = resp.json()
    # El profit de cromos normales no se ve afectado por las foils.
    assert body["avg_card_price"] == pytest.approx(0.16)

    foil_summary = body["foils"]
    assert foil_summary is not None
    assert foil_summary["total_foils"] == 2
    assert foil_summary["avg_foil_price"] == pytest.approx(3.0)         # (2 + 4) / 2
    assert foil_summary["net_avg_foil_price"] == pytest.approx(3.0 / 1.15, abs=1e-4)
    assert {f["name"] for f in foil_summary["foils"]} == {"Triss (Foil)", "Geralt (Foil)"}
    # Se hicieron dos búsquedas: cromos normales + foils.
    assert steam.calls["/market/search/render/"] == 2


async def test_include_foils_sin_foils_no_rompe(client: AsyncClient, steam: FakeSteam):
    """Si el juego no tiene foils, el resumen viene vacío (total 0), sin romper."""
    steam.appdetails = make_appdetails(APPID, final_cents=299)
    steam.search = make_search(CARDS[:2])
    steam.foil_search = make_search([])
    steam.prices = {name: make_price() for name in CARDS[:2]}

    resp = await client.get(f"/api/profit/{APPID}?include_foils=true")
    assert resp.status_code == 200

    foil_summary = resp.json()["foils"]
    assert foil_summary == {"total_foils": 0, "avg_foil_price": 0.0, "net_avg_foil_price": 0.0, "foils": []}


async def test_health(client: AsyncClient):
    """Healthcheck simple."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
