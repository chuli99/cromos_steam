"""Integración del router de booster packs (sin red, Steam mockeado)."""
from __future__ import annotations

from httpx import AsyncClient

from conftest import FakeSteam, make_price


async def test_gem_sack_price(client: AsyncClient, steam: FakeSteam):
    """GET /api/gems/sack devuelve el precio del saco y el precio por gema."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$0.50")}

    r = await client.get("/api/gems/sack")
    assert r.status_code == 200
    body = r.json()
    assert body["gems"] == 1000
    assert body["price"] == 0.5
    assert body["price_per_gem"] == round(0.5 / 1000, 6)


async def test_gem_sack_sin_precio_502(client, steam):
    """Si el saco no tiene precio de mercado, el endpoint responde 502."""
    steam.prices = {}  # "753-Sack of Gems" -> success False

    r = await client.get("/api/gems/sack")
    assert r.status_code == 502


async def test_booster_value_con_profit(client, steam):
    """Saco $1.00 (gema $0.001) + booster 400 gemas ($0.40) vendido a $1.00 -> profit."""
    steam.prices = {
        "753-Sack of Gems": make_price(lowest="$1.00"),
        "570-Dota 2 Booster Pack": make_price(lowest="$1.00"),
    }

    r = await client.get("/api/booster/570", params={"gem_cost": 400, "name": "Dota 2"})
    assert r.status_code == 200
    body = r.json()
    assert body["gem_cost_value"] == 0.4
    assert body["booster_net_price"] == round(1.0 / 1.15, 4)
    assert body["profit_positive"] is True
    assert body["profit"] == round(1.0 / 1.15 - 0.4, 4)


async def test_booster_sin_precio_de_mercado(client, steam):
    """Si el booster no se vende en el market, profit queda en None (no aborta)."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}

    r = await client.get("/api/booster/999", params={"gem_cost": 400, "name": "Sin Mercado"})
    assert r.status_code == 200
    body = r.json()
    assert body["booster_price"] is None
    assert body["profit"] is None
    assert body["profit_positive"] is False


async def test_booster_requiere_gem_cost_positivo(client, steam):
    """gem_cost <= 0 es inválido (422 de validación de FastAPI)."""
    r = await client.get("/api/booster/570", params={"gem_cost": 0, "name": "Dota 2"})
    assert r.status_code == 422
