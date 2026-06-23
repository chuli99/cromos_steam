"""Router del valor de booster packs.

- ``GET /api/gems/sack``: precio de referencia del Saco de Gemas (1000 gemas).
- ``GET /api/booster/{appid}``: compara el costo en gemas de crear un booster pack
  (gemas valuadas según el Saco de Gemas) contra su precio de venta en el market.

Los datos por juego (appid, nombre, costo en gemas) los provee la extensión leyéndolos
de la página del booster creator de Steam; el backend solo agrega precios + cálculo.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from ..cache import get_or_set
from ..config import settings
from ..models import BoosterValue, GemSackPrice
from ..steam.market import GEM_SACK_HASH, GEMS_PER_SACK, booster_hash_name, fetch_card_price
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
