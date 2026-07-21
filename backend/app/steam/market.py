"""Steam Community Market: lista de cromos y precio por cromo.

- ``fetch_card_list``: usa search/render para listar los cromos de un juego
  (item_class_2 = trading cards). Por defecto los **normales** (cardborder_0);
  con ``foil=True`` lista las **foils** (cardborder_1).
- ``fetch_card_price``: usa priceoverview (pasando por el throttle global) para el
  precio de un cromo. Todos los cromos viven bajo ``appid=753``.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from ..config import settings
from .client import get_json, get_text

# El item_nameid aparece en el HTML del listing como Market_LoadOrderSpread( 12345 ).
_NAMEID_RE = re.compile(r"Market_LoadOrderSpread\(\s*(\d+)\s*\)")

# Saco de Gemas: ítem del market (appid 753) que entrega 1000 gemas. Su precio sirve
# de referencia para valuar las gemas que cuesta crear un booster pack.
GEM_SACK_HASH = "753-Sack of Gems"
GEMS_PER_SACK = 1000


def booster_hash_name(appid: int, game_name: str) -> str:
    """``market_hash_name`` del booster pack de un juego (ítem vendible, appid 753).

    Formato de Steam: ``"{appid}-{Nombre del juego} Booster Pack"`` (ej:
    ``"570-Dota 2 Booster Pack"``). El nombre lo provee la página del booster creator.
    """
    return f"{appid}-{game_name} Booster Pack"


async def fetch_card_list(appid: int, foil: bool = False) -> list[str]:
    """Devuelve los ``market_hash_name`` de los cromos del juego.

    ``norender=1`` es obligatorio para recibir JSON en vez de HTML.
    ``foil=False`` lista los cromos normales; ``foil=True``, las foils.
    """
    # cardborder_0 = normales (las que dropean); cardborder_1 = foils (raras).
    cardborder = "tag_cardborder_1" if foil else "tag_cardborder_0"
    url = f"{settings.steam_community_base}/market/search/render/"
    params = {
        "appid": settings.cards_appid,        # 753 (cromos)
        "norender": 1,                        # respuesta JSON
        "count": 100,
        "category_753_Game[]": f"tag_app_{appid}",
        "category_753_item_class[]": "tag_item_class_2",   # trading cards
        "category_753_cardborder[]": cardborder,
    }
    data = await get_json(url, params)

    results = data.get("results", []) if isinstance(data, dict) else []
    names: list[str] = []
    for item in results:
        # El hash_name puede venir directo o dentro de asset_description.
        hash_name = item.get("hash_name")
        if not hash_name:
            hash_name = (item.get("asset_description") or {}).get("market_hash_name")
        if hash_name:
            names.append(hash_name)
    return names


async def fetch_card_price(market_hash_name: str) -> dict[str, Any] | None:
    """Devuelve el JSON crudo de priceoverview para un ítem del market (appid 753).

    Es genérico: sirve para cromos, foils, el Saco de Gemas o un booster pack (todos
    viven bajo ``appid=753``). ``get_json`` ya aplica el throttle del host
    (steamcommunity.com) para respetar el rate limit de Steam.
    """
    url = f"{settings.steam_community_base}/market/priceoverview/"
    params = {
        "appid": settings.cards_appid,
        "currency": settings.currency,
        "market_hash_name": market_hash_name,
    }
    data = await get_json(url, params)
    return data if isinstance(data, dict) else None


async def fetch_item_nameid(market_hash_name: str) -> int | None:
    """Devuelve el ``item_nameid`` de un ítem del market (appid 753).

    Steam no lo expone por API: se scrapea del HTML de la página del listing, donde
    aparece en la llamada ``Market_LoadOrderSpread( <nameid> )``. Es un ID fijo por
    ítem, así que se puede cachear por mucho tiempo.
    """
    url = (
        f"{settings.steam_community_base}/market/listings/"
        f"{settings.cards_appid}/{quote(market_hash_name)}"
    )
    html = await get_text(url)
    m = _NAMEID_RE.search(html)
    return int(m.group(1)) if m else None


async def fetch_order_histogram(item_nameid: int) -> dict[str, Any] | None:
    """JSON crudo de itemordershistogram: buy/sell orders vigentes de un ítem.

    Incluye ``highest_buy_order`` (el pedido de compra más alto, en centavos): lo que
    se cobra HOY vendiendo al instante, sin esperar comprador. Requiere el
    ``item_nameid`` (ver ``fetch_item_nameid``).
    """
    url = f"{settings.steam_community_base}/market/itemordershistogram"
    params = {
        "country": settings.country_code,
        "language": settings.language,
        "currency": settings.currency,
        "item_nameid": item_nameid,
        "two_factor": 0,
    }
    data = await get_json(url, params)
    return data if isinstance(data, dict) else None
