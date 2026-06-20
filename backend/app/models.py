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
