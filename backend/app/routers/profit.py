"""Router del cálculo de profit: GET /api/profit/{appid}.

Orquesta las llamadas a Steam (con caché + throttle) y arma el ``ProfitResponse``
con el desglose completo. La lógica de cálculo (``compute_profit``) es una función
pura para poder testearla sin red.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from ..cache import get_or_set
from ..config import settings
from ..models import CardPrice, FoilSummary, ProfitResponse
from ..steam.market import fetch_card_list, fetch_card_price
from ..steam.parser import apply_fee, parse_price
from ..steam.store import fetch_game

router = APIRouter()


def _parse_volume(raw: Any) -> int | None:
    """Convierte el campo ``volume`` ('1,234') a int; None si no se puede."""
    if raw is None:
        return None
    try:
        return int(str(raw).replace(",", "").replace(".", "").strip())
    except (ValueError, TypeError):
        return None


def build_card_price(market_hash_name: str, raw: dict[str, Any] | None) -> CardPrice:
    """Construye un ``CardPrice`` a partir del JSON crudo de priceoverview.

    Maneja ``success: false`` y campos faltantes sin romper: en ese caso el cromo
    queda con ``success=False`` y se excluirá del promedio.
    """
    # Nombre legible: quitar el prefijo "{appid}-" del market_hash_name.
    display = market_hash_name.split("-", 1)[1] if "-" in market_hash_name else market_hash_name

    if not raw or not raw.get("success"):
        return CardPrice(name=display, success=False)

    lowest = parse_price(raw.get("lowest_price"))
    median = parse_price(raw.get("median_price"))
    volume = _parse_volume(raw.get("volume"))

    return CardPrice(
        name=display,
        lowest_price=lowest,
        median_price=median,
        volume=volume,
        success=lowest is not None,
    )


def compute_foil_summary(
    foils: list[CardPrice],
    fee_rate: float = settings.fee_rate,
) -> FoilSummary:
    """Resume el valor de mercado de las foils (sin modelo de drop ni profit).

    El promedio se calcula solo sobre foils con precio; las que no tienen se
    excluyen (pero igual aparecen en el desglose con ``success=False``).
    """
    priced = [c.lowest_price for c in foils if c.success and c.lowest_price is not None]
    avg = sum(priced) / len(priced) if priced else 0.0
    return FoilSummary(
        total_foils=len(foils),
        avg_foil_price=round(avg, 4),
        net_avg_foil_price=round(apply_fee(avg, fee_rate), 4),
        foils=foils,
    )


def compute_profit(
    appid: int,
    game_name: str,
    game_price: float,
    currency: int,
    cards: list[CardPrice],
    fee_rate: float = settings.fee_rate,
    drop_ratio: float = settings.drop_ratio,
) -> ProfitResponse:
    """Calcula el profit esperado a partir del precio del juego y los cromos.

    Modelo de valor esperado:
        precio_promedio = sum(lowest_price de cromos con precio) / nº con precio
        valor_bruto     = precio_promedio * cards_dropped
        valor_neto      = valor_bruto / (1 + fee_rate)
        profit          = valor_neto - precio_juego

    Los cromos sin precio (``success=False``) se excluyen del promedio, no abortan
    el cálculo.
    """
    total_cards = len(cards)
    # Cromos que dropean: la mitad del set (aproximación estándar), configurable.
    cards_dropped = round(total_cards * drop_ratio)

    priced = [c.lowest_price for c in cards if c.success and c.lowest_price is not None]
    avg_card_price = sum(priced) / len(priced) if priced else 0.0

    gross_card_value = avg_card_price * cards_dropped
    net_card_value = apply_fee(gross_card_value, fee_rate)
    profit = net_card_value - game_price

    return ProfitResponse(
        appid=appid,
        game_name=game_name,
        game_price=round(game_price, 2),
        currency=currency,
        total_cards=total_cards,
        cards_dropped=cards_dropped,
        avg_card_price=round(avg_card_price, 4),
        gross_card_value=round(gross_card_value, 4),
        fee_rate=fee_rate,
        net_card_value=round(net_card_value, 4),
        profit=round(profit, 4),
        profit_positive=profit > 0,
        cards=cards,
    )


async def _fetch_prices(card_names: list[str]) -> list[CardPrice]:
    """Resuelve el precio de cada cromo (con caché TTL largo + throttle)."""
    cards: list[CardPrice] = []
    for name in card_names:
        raw = await get_or_set(
            f"cardprice:{settings.currency}:{name}",
            lambda n=name: fetch_card_price(n),
            settings.cache_ttl_cards,
        )
        cards.append(build_card_price(name, raw))
    return cards


@router.get("/profit/{appid}", response_model=ProfitResponse)
async def get_profit(
    appid: int,
    include_foils: Annotated[bool, Query(description="Incluir resumen de foils (cálculo aparte).")] = False,
) -> ProfitResponse:
    """Devuelve el desglose de profit para un juego de Steam.

    Con ``include_foils=true`` se agrega un resumen del valor de las foils, como
    cálculo aparte (no entra en el profit de los cromos normales).
    """
    # 1) Precio del juego (caché TTL medio).
    game_name, game_price = await get_or_set(
        f"game:{appid}:{settings.country_code}",
        lambda: fetch_game(appid),
        settings.cache_ttl_game,
    )
    if game_name is None:
        raise HTTPException(status_code=404, detail="appid no encontrado en la store de Steam.")
    if game_price is None:
        raise HTTPException(
            status_code=422,
            detail="El juego es gratuito o no tiene precio; no aplica cálculo de profit.",
        )

    # 2) Lista de cromos normales (caché TTL largo).
    card_names = await get_or_set(
        f"cardlist:{appid}",
        lambda: fetch_card_list(appid),
        settings.cache_ttl_card_list,
    )
    if not card_names:
        raise HTTPException(status_code=404, detail="El juego no tiene cromos (trading cards).")

    # 3) Precio de cada cromo normal (throttle + caché dentro de _fetch_prices).
    cards = await _fetch_prices(card_names)

    # 4) Cálculo del profit de cromos normales.
    result = compute_profit(appid, game_name, game_price, settings.currency, cards)

    # 5) (Opcional) Resumen de foils, como cálculo aparte.
    if include_foils:
        foil_names = await get_or_set(
            f"foillist:{appid}",
            lambda: fetch_card_list(appid, foil=True),
            settings.cache_ttl_card_list,
        )
        foil_cards = await _fetch_prices(foil_names) if foil_names else []
        result.foils = compute_foil_summary(foil_cards)

    return result
