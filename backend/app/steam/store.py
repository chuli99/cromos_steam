"""Storefront API: precio del juego vía appdetails."""
from __future__ import annotations

from ..config import settings
from .client import get_json


async def fetch_game(appid: int) -> tuple[str | None, float | None, str | None]:
    """Devuelve ``(nombre, precio, tipo)`` del juego en unidades de moneda.

    - Si el appid no existe / no es válido -> ``(None, None, None)``.
    - Si el juego es gratuito o no tiene ``price_overview`` -> ``(nombre, None, tipo)``.
    - Si tiene precio -> ``(nombre, precio_final, tipo)`` (``final`` viene en centavos).

    ``tipo`` es el campo ``type`` de Storefront: ``"game"``, ``"dlc"``, ``"demo"``,
    etc. Se usa para descartar DLCs (no dropean cromos propios).
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
        return None, None, None

    info = entry.get("data", {}) or {}
    name = info.get("name") or f"App {appid}"
    app_type = info.get("type")

    price_overview = info.get("price_overview")
    if not price_overview:
        # Juego gratuito o sin precio (ej: free-to-play).
        return name, None, app_type

    final_cents = price_overview.get("final")
    price = final_cents / 100 if final_cents is not None else None
    return name, price, app_type
