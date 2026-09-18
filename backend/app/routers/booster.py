"""Router del valor de booster packs.

- ``GET /api/gems/sack``: precio de referencia del Saco de Gemas (1000 gemas).
- ``GET /api/booster/{appid}``: compara el costo en gemas de crear un booster pack
  (gemas valuadas según el Saco de Gemas) contra su precio de venta en el market.
- ``GET /api/booster/{appid}/quick``: igual, pero contra el **pedido de compra más
  alto** (buy order): lo que se cobra vendiendo al instante, sin esperar comprador.
- ``GET /api/booster/{appid}/avg``: punto intermedio entre los dos anteriores: contra
  el **promedio de ventas recientes** (``pricehistory``, ponderado por volumen). Ese
  endpoint de Steam requiere sesión logueada, así que la extensión lo consulta ella
  misma (desde el navegador del usuario) y este endpoint solo recibe el promedio ya
  calculado para aplicarle el mismo costo/fee/profit que los otros modos.

Los datos por juego (appid, nombre, costo en gemas) los provee la extensión leyéndolos
de la página del booster creator de Steam; el backend solo agrega precios + cálculo.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ..cache import get_or_set
from ..config import settings
from ..models import BoosterAvgValue, BoosterQuickValue, BoosterValue, GemSackPrice
from ..steam.market import (
    GEM_SACK_HASH,
    GEMS_PER_SACK,
    booster_hash_name,
    fetch_card_price,
    fetch_orderbook,
)
from ..steam.parser import apply_fee, parse_price

router = APIRouter()


async def _gem_sack_price() -> float | None:
    """Precio del Saco de Gemas (1000 gemas), cacheado. ``None`` si no hay precio."""
    raw = await get_or_set(
        f"gemsack:{settings.currency}",
        lambda: fetch_card_price(GEM_SACK_HASH),
        settings.cache_ttl_cards,
    )
    if raw and raw.get("success"):
        return parse_price(raw.get("lowest_price"))
    return None


@router.get("/gems/sack", response_model=GemSackPrice)
async def get_gem_sack_price() -> GemSackPrice:
    """Precio de referencia del Saco de Gemas (1000 gemas) y el precio por gema."""
    price = await _gem_sack_price()
    if price is None:
        raise HTTPException(status_code=502, detail="No se pudo obtener el precio del Saco de Gemas.")
    return GemSackPrice(
        market_hash_name=GEM_SACK_HASH,
        gems=GEMS_PER_SACK,
        price=round(price, 2),
        price_per_gem=round(price / GEMS_PER_SACK, 6),
        currency=settings.currency,
    )


@router.get("/booster/{appid}", response_model=BoosterValue)
async def get_booster_value(
    appid: int,
    gem_cost: Annotated[int, Query(gt=0, description="Costo del booster en gemas (de la página).")],
    name: Annotated[str, Query(min_length=1, description="Nombre del juego (arma el market_hash_name del booster).")],
) -> BoosterValue:
    """Compara el costo en gemas de un booster pack contra su precio de venta.

    - **Costo**: ``(gem_cost / 1000) * precio_saco`` (Saco de Gemas de referencia).
    - **Venta**: precio de mercado del booster pack, neto del fee de Steam.
    - **Profit**: venta_neta − costo.

    Si el booster no tiene precio de mercado, ``booster_price`` y ``profit`` quedan
    en ``None`` (no se puede valuar), sin abortar.
    """
    booster_hash = booster_hash_name(appid, name)
    raw = await get_or_set(
        f"boosterprice:{settings.currency}:{booster_hash}",
        lambda: fetch_card_price(booster_hash),
        settings.cache_ttl_cards,
    )
    booster_price = parse_price(raw.get("lowest_price")) if raw and raw.get("success") else None
    booster_net = apply_fee(booster_price, settings.fee_rate) if booster_price is not None else None

    sack_price = await _gem_sack_price()
    gem_cost_value = (gem_cost / GEMS_PER_SACK) * sack_price if sack_price is not None else None

    profit = (
        booster_net - gem_cost_value
        if booster_net is not None and gem_cost_value is not None
        else None
    )

    return BoosterValue(
        appid=appid,
        name=name,
        currency=settings.currency,
        gem_cost=gem_cost,
        gem_price_per_1000=round(sack_price, 4) if sack_price is not None else None,
        gem_cost_value=round(gem_cost_value, 4) if gem_cost_value is not None else None,
        booster_price=round(booster_price, 4) if booster_price is not None else None,
        booster_net_price=round(booster_net, 4) if booster_net is not None else None,
        fee_rate=settings.fee_rate,
        profit=round(profit, 4) if profit is not None else None,
        profit_positive=bool(profit is not None and profit > 0),
    )


async def _highest_buy_order(booster_hash: str) -> float | None:
    """Pedido de compra más alto del booster (en unidades), o ``None`` si no hay.

    Una sola request cacheada al orderbook del ítem (TTL de precios).
    ``amtMaxBuyOrder`` viene en centavos; ``0`` o ausente = sin buy orders.
    """
    book = await get_or_set(
        f"orderbook:{settings.currency}:{booster_hash}",
        lambda: fetch_orderbook(booster_hash),
        settings.cache_ttl_cards,
    )
    if not book or not book.get("success"):
        return None
    raw = (book.get("data") or {}).get("amtMaxBuyOrder")
    try:
        return int(raw) / 100 if raw else None
    except (TypeError, ValueError):
        return None


@router.get("/booster/{appid}/quick", response_model=BoosterQuickValue)
async def get_booster_quick_value(
    appid: int,
    gem_cost: Annotated[int, Query(gt=0, description="Costo del booster en gemas (de la página).")],
    name: Annotated[str, Query(min_length=1, description="Nombre del juego (arma el market_hash_name del booster).")],
) -> BoosterQuickValue:
    """Profit "rápido": costo en gemas vs el **pedido de compra más alto** del booster.

    Vender contra el buy order más alto cobra menos que listar, pero es **inmediato y
    garantizado** (el comprador ya puso la orden). Útil para asegurar profit sin
    esperar a que alguien compre el listado.

    - **Costo**: ``(gem_cost / 1000) * precio_saco`` (igual que el modo normal).
    - **Venta**: ``amtMaxBuyOrder`` del orderbook del ítem, neto del fee.
    - **Profit**: venta_neta − costo. Si no hay buy orders, queda en ``None``.
    """
    booster_hash = booster_hash_name(appid, name)
    buy_order = await _highest_buy_order(booster_hash)
    buy_order_net = apply_fee(buy_order, settings.fee_rate) if buy_order is not None else None

    sack_price = await _gem_sack_price()
    gem_cost_value = (gem_cost / GEMS_PER_SACK) * sack_price if sack_price is not None else None

    profit = (
        buy_order_net - gem_cost_value
        if buy_order_net is not None and gem_cost_value is not None
        else None
    )

    return BoosterQuickValue(
        appid=appid,
        name=name,
        currency=settings.currency,
        gem_cost=gem_cost,
        gem_price_per_1000=round(sack_price, 4) if sack_price is not None else None,
        gem_cost_value=round(gem_cost_value, 4) if gem_cost_value is not None else None,
        buy_order_price=round(buy_order, 4) if buy_order is not None else None,
        buy_order_net=round(buy_order_net, 4) if buy_order_net is not None else None,
        fee_rate=settings.fee_rate,
        profit=round(profit, 4) if profit is not None else None,
        profit_positive=bool(profit is not None and profit > 0),
    )


@router.get("/booster/{appid}/avg", response_model=BoosterAvgValue)
async def get_booster_avg_value(
    appid: int,
    gem_cost: Annotated[int, Query(gt=0, description="Costo del booster en gemas (de la página).")],
    name: Annotated[str, Query(min_length=1, description="Nombre del juego (arma el market_hash_name del booster).")],
    avg_price: Annotated[
        float | None,
        Query(gt=0, description="Promedio ponderado por volumen de ventas recientes, calculado por la extensión."),
    ] = None,
    sample_days: Annotated[int | None, Query(gt=0, description="Ventana en días considerada (informativo).")] = None,
    sample_volume: Annotated[int | None, Query(ge=0, description="Unidades vendidas en la ventana (informativo).")] = None,
) -> BoosterAvgValue:
    """Profit contra el **promedio de ventas recientes**: punto intermedio entre el
    modo normal (precio listado) y el rápido (buy order más alto).

    Steam no expone el historial de ventas (``pricehistory``) a requests anónimas
    (requiere sesión logueada), así que a diferencia de los otros modos este backend
    **no** consulta Steam por ese dato: ``avg_price`` viene ya calculado por la
    extensión, que sí puede pedirlo (corre en el navegador del usuario, con su propia
    sesión). Este endpoint solo aplica el mismo costo en gemas + fee + profit que los
    demás modos, para no duplicar esa lógica en el content script.

    - **Costo**: ``(gem_cost / 1000) * precio_saco`` (igual que los otros modos).
    - **Venta**: ``avg_price``, neto del fee.
    - **Profit**: venta_neta − costo. Si no hay ventas recientes (``avg_price`` ausente),
      queda en ``None``, sin abortar.
    """
    avg_net = apply_fee(avg_price, settings.fee_rate) if avg_price is not None else None

    sack_price = await _gem_sack_price()
    gem_cost_value = (gem_cost / GEMS_PER_SACK) * sack_price if sack_price is not None else None

    profit = (
        avg_net - gem_cost_value
        if avg_net is not None and gem_cost_value is not None
        else None
    )

    return BoosterAvgValue(
        appid=appid,
        name=name,
        currency=settings.currency,
        gem_cost=gem_cost,
        gem_price_per_1000=round(sack_price, 4) if sack_price is not None else None,
        gem_cost_value=round(gem_cost_value, 4) if gem_cost_value is not None else None,
        avg_sale_price=round(avg_price, 4) if avg_price is not None else None,
        avg_sale_net=round(avg_net, 4) if avg_net is not None else None,
        sample_days=sample_days,
        sample_volume=sample_volume,
        fee_rate=settings.fee_rate,
        profit=round(profit, 4) if profit is not None else None,
        profit_positive=bool(profit is not None and profit > 0),
    )
