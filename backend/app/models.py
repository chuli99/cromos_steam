"""Schemas pydantic v2 de la API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CardPrice(BaseModel):
    """Precio de un cromo individual (resultado de priceoverview)."""

    name: str                          # nombre legible del cromo (sin prefijo appid)
    lowest_price: float | None = None  # precio más bajo en venta
    median_price: float | None = None  # precio mediano de ventas recientes
    volume: int | None = None          # cantidad vendida (si la informa Steam)
    success: bool = False              # True si Steam devolvió precio válido


class FoilSummary(BaseModel):
    """Resumen de las foils del juego (cálculo aparte, informativo).

    Las foils no siguen el modelo de drop de los cromos normales (son raras y
    distorsionarían el valor esperado), así que no se calcula un "profit" sobre
    ellas: solo se reporta su valor de mercado.
    """

    total_foils: int            # cantidad de foils del set
    avg_foil_price: float       # precio promedio por foil (solo las con precio)
    net_avg_foil_price: float   # promedio tras descontar el fee de Steam
    foils: list[CardPrice]      # desglose por foil


class GemSackPrice(BaseModel):
    """Precio de referencia del Saco de Gemas (1000 gemas)."""

    market_hash_name: str
    gems: int                   # gemas que da el saco (1000)
    price: float                # precio de mercado del saco
    price_per_gem: float        # price / gems
    currency: int


class BoosterValue(BaseModel):
    """Valor de un booster pack: costo en gemas vs precio de venta en el market.

    Compara lo que cuesta crear el booster (sus gemas convertidas a dinero según el
    precio del Saco de Gemas) contra lo que se obtendría vendiéndolo (precio de
    mercado del booster pack, neto del fee de Steam). El costo en gemas y el nombre
    del juego los provee la extensión leyéndolos de la página del booster creator.
    """

    appid: int
    name: str
    currency: int

    gem_cost: int                      # costo del booster en gemas (de la página)
    gem_price_per_1000: float | None   # precio del Saco de Gemas (1000 gemas)
    gem_cost_value: float | None       # costo del booster en dinero

    booster_price: float | None        # precio de venta del booster (lowest)
    booster_net_price: float | None    # tras descontar el fee de Steam
    fee_rate: float = Field(0.15)

    profit: float | None               # booster_net_price - gem_cost_value
    profit_positive: bool = False      # True si profit > 0


class ProfitResponse(BaseModel):
    """Respuesta completa del cálculo de profit con todo el desglose."""

    appid: int
    game_name: str
    game_price: float           # precio actual del juego (unidades)
    currency: int               # moneda usada para los cromos

    total_cards: int            # cantidad total de cromos normales del set
    cards_dropped: int          # cromos que efectivamente dropean (~mitad)
    avg_card_price: float       # precio promedio por cromo (solo los con precio)

    gross_card_value: float     # valor bruto esperado del drop (antes del fee)
    fee_rate: float = Field(0.15)  # fee de Steam aplicado
    net_card_value: float       # valor neto tras descontar el fee

    profit: float               # net_card_value - game_price
    profit_positive: bool       # True si profit > 0

    cards: list[CardPrice]      # desglose por cromo

    foils: FoilSummary | None = None  # resumen de foils (solo si se pidió)
