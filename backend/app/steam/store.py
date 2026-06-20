"""Storefront API: precio del juego vía appdetails."""
from __future__ import annotations

from ..config import settings
from .client import get_json


async def fetch_game(appid: int) -> tuple[str | None, float | None]:
    """Devuelve ``(nombre, precio)`` del juego en unidades de moneda.

    - Si el appid no existe / no es válido -> ``(None, None)``.
    - Si el juego es gratuito o no tiene ``price_overview`` -> ``(nombre, None)``.
    - Si tiene precio -> ``(nombre, precio_final)`` (``final`` viene en centavos).
    """
    url = f"{settings.steam_store_base}/api/appdetails"
    params = {
        "appids": appid,
        "cc": settings.country_code,
        "l": settings.language,
    }
    data = await get_json(url, params)

    entry = data.get(str(appid)) if isinstance(data, dict) else None
    if not entry or not entry.get("success"):
        return None, None

    info = entry.get("data", {}) or {}
    name = info.get("name") or f"App {appid}"

    price_overview = info.get("price_overview")
    if not price_overview:
        # Juego gratuito o sin precio (ej: free-to-play).
        return name, None

    final_cents = price_overview.get("final")
    price = final_cents / 100 if final_cents is not None else None
    return name, price
