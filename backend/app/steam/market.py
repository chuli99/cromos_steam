"""Steam Community Market: lista de cromos y precio por cromo.

- ``fetch_card_list``: usa search/render para listar los cromos de un juego
  (item_class_2 = trading cards). Por defecto los **normales** (cardborder_0);
  con ``foil=True`` lista las **foils** (cardborder_1).
- ``fetch_card_price``: usa priceoverview (pasando por el throttle global) para el
  precio de un cromo. Todos los cromos viven bajo ``appid=753``.
"""
from __future__ import annotations

from typing import Any

from ..config import settings
from ..throttle import priceoverview_throttle
from .client import get_json


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
    """Devuelve el JSON crudo de priceoverview para un cromo.

    Pasa por el throttle global para respetar el rate limit de Steam.
    """
    url = f"{settings.steam_community_base}/market/priceoverview/"
    params = {
        "appid": settings.cards_appid,
        "currency": settings.currency,
        "market_hash_name": market_hash_name,
    }
    async with priceoverview_throttle:
        data = await get_json(url, params)
    return data if isinstance(data, dict) else None
