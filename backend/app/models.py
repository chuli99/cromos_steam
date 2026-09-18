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


class BoosterQuickValue(BaseModel):
    """Valor de un booster pack vendiéndolo AL INSTANTE contra el buy order más alto.

    A diferencia de ``BoosterValue`` (que usa el precio de venta listado, sujeto a que
    aparezca un comprador), acá la referencia es el **pedido de compra más alto**
    vigente en el market: lo que se cobra hoy mismo aceptando ese buy order. Es un
    profit "rápido": menor precio, pero venta garantizada e inmediata.
    """

    appid: int
    name: str
    currency: int

    gem_cost: int                      # costo del booster en gemas (de la página)
    gem_price_per_1000: float | None   # precio del Saco de Gemas (1000 gemas)
    gem_cost_value: float | None       # costo del booster en dinero

    buy_order_price: float | None      # pedido de compra más alto (lo que paga el comprador)
    buy_order_net: float | None        # lo que recibe el vendedor tras el fee de Steam
    fee_rate: float = Field(0.15)

    profit: float | None               # buy_order_net - gem_cost_value
    profit_positive: bool = False      # True si profit > 0


class BoosterAvgValue(BaseModel):
    """Valor de un booster pack contra el precio promedio de ventas RECIENTES.

    Punto intermedio entre ``BoosterValue`` (precio listado, hay que esperar
    comprador) y ``BoosterQuickValue`` (buy order más alto, cobra menos pero es
    instantáneo): acá la referencia es el promedio ponderado por volumen de las
    ventas efectivamente concretadas en una ventana reciente (``sample_days``).
    Cobra más que el buy order (son ventas reales, no la oferta de compra más baja
    que aceptaría el mercado) sin depender de una sola venta puntual como el precio
    listado más bajo.

    A diferencia de los otros modos, Steam no expone el historial de ventas
    (``pricehistory``) sin una sesión logueada, así que el backend no lo puede pedir
    él mismo: la extensión lo obtiene desde el propio navegador del usuario (ya
    logueado en steamcommunity.com) y manda acá el promedio ya calculado
    (``avg_price``); este endpoint solo aplica el mismo cálculo de costo/fee/profit
    que los demás modos, para no duplicar esa lógica en el content script.
    """

    appid: int
    name: str
    currency: int

    gem_cost: int                      # costo del booster en gemas (de la página)
    gem_price_per_1000: float | None   # precio del Saco de Gemas (1000 gemas)
    gem_cost_value: float | None       # costo del booster en dinero

    avg_sale_price: float | None       # promedio ponderado por volumen (lo que pagó el comprador)
    avg_sale_net: float | None         # lo que recibe el vendedor tras el fee de Steam
    sample_days: int | None = None     # ventana considerada por la extensión (informativo)
    sample_volume: int | None = None   # unidades vendidas en la ventana (informativo)
    fee_rate: float = Field(0.15)

    profit: float | None               # avg_sale_net - gem_cost_value
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
