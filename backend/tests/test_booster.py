"""Integración del router de booster packs (sin red, Steam mockeado)."""
from __future__ import annotations

from httpx import AsyncClient

from conftest import FakeSteam, make_orderbook, make_price


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


async def test_booster_quick_con_profit(client, steam):
    """Saco $1.00 + booster 400 gemas ($0.40) con buy order de $0.60 -> profit rápido."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}
    steam.orderbooks = {"570-Dota 2 Booster Pack": make_orderbook(max_buy_cents=60)}

    r = await client.get("/api/booster/570/quick", params={"gem_cost": 400, "name": "Dota 2"})
    assert r.status_code == 200
    body = r.json()
    assert body["gem_cost_value"] == 0.4
    assert body["buy_order_price"] == 0.6
    assert body["buy_order_net"] == round(0.6 / 1.15, 4)
    assert body["profit"] == round(0.6 / 1.15 - 0.4, 4)
    assert body["profit_positive"] is True


async def test_booster_quick_sin_buy_orders(client, steam):
    """Sin buy orders vigentes (amtMaxBuyOrder 0), el profit rápido queda en None."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}
    steam.orderbooks = {"570-Dota 2 Booster Pack": make_orderbook(max_buy_cents=0)}

    r = await client.get("/api/booster/570/quick", params={"gem_cost": 400, "name": "Dota 2"})
    assert r.status_code == 200
    body = r.json()
    assert body["buy_order_price"] is None
    assert body["profit"] is None
    assert body["profit_positive"] is False


async def test_booster_quick_sin_listing(client, steam):
    """Booster inexistente (orderbook success=False): profit en None, sin abortar."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}

    r = await client.get("/api/booster/999/quick", params={"gem_cost": 400, "name": "Sin Mercado"})
    assert r.status_code == 200
    body = r.json()
    assert body["buy_order_price"] is None
    assert body["profit"] is None


async def test_booster_quick_cachea_orderbook(client, steam):
    """El orderbook se pide una sola vez: la 2da consulta sale de la caché."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}
    steam.orderbooks = {"570-Dota 2 Booster Pack": make_orderbook(max_buy_cents=60)}

    await client.get("/api/booster/570/quick", params={"gem_cost": 400, "name": "Dota 2"})
    await client.get("/api/booster/570/quick", params={"gem_cost": 400, "name": "Dota 2"})

    orderbook_calls = sum(v for k, v in steam.calls.items() if k.endswith("/market/orderbook"))
    assert orderbook_calls == 1


async def test_booster_avg_con_profit(client, steam):
    """Saco $1.00 + booster 400 gemas ($0.40) con promedio reciente de $0.80 -> profit."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}

    r = await client.get(
        "/api/booster/570/avg",
        params={"gem_cost": 400, "name": "Dota 2", "avg_price": 0.8, "sample_days": 2, "sample_volume": 37},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["gem_cost_value"] == 0.4
    assert body["avg_sale_price"] == 0.8
    assert body["avg_sale_net"] == round(0.8 / 1.15, 4)
    assert body["profit"] == round(0.8 / 1.15 - 0.4, 4)
    assert body["profit_positive"] is True
    assert body["sample_days"] == 2
    assert body["sample_volume"] == 37


async def test_booster_avg_sin_ventas_recientes(client, steam):
    """Sin ``avg_price`` (la extensión no encontró ventas en la ventana): profit en None."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}

    r = await client.get("/api/booster/570/avg", params={"gem_cost": 400, "name": "Dota 2"})
    assert r.status_code == 200
    body = r.json()
    assert body["avg_sale_price"] is None
    assert body["profit"] is None
    assert body["profit_positive"] is False


async def test_booster_avg_no_pega_a_steam_por_el_promedio(client, steam):
    """El backend no consulta orderbook/priceoverview del booster para este modo (solo el saco)."""
    steam.prices = {"753-Sack of Gems": make_price(lowest="$1.00")}

    await client.get(
        "/api/booster/570/avg",
        params={"gem_cost": 400, "name": "Dota 2", "avg_price": 0.8},
    )

    booster_price_calls = sum(
        v for k, v in steam.calls.items() if k.endswith("/market/priceoverview/")
    )
    # Solo la consulta del Saco de Gemas (referencia de costo), ninguna del booster.
    assert booster_price_calls == 1
