"""Parseo de precios de Steam y aplicación del fee del market.

Steam devuelve precios como strings localizados (``"$0.18"``, ``"1,23€"``,
``"1.234,56 €"``). El parser debe ser robusto frente a símbolos de moneda,
separadores de miles/decimales y valores ausentes.
"""
from __future__ import annotations

import re


def parse_price(raw: str | None) -> float | None:
    """Convierte un string de precio de Steam a ``float`` en unidades.

    Maneja distintos formatos de separador:
    - ``"$0.18"``        -> 0.18
    - ``"1,23€"``        -> 1.23
    - ``"$1,234.56"``    -> 1234.56  (coma = miles, punto = decimal)
    - ``"1.234,56 €"``   -> 1234.56  (punto = miles, coma = decimal)

    Devuelve ``None`` si el valor es vacío o no parseable (no rompe).
    """
    if not raw:
        return None

    # Dejar solo dígitos y separadores.
    cleaned = re.sub(r"[^0-9.,]", "", raw)
    if not cleaned:
        return None

    last_sep = max(cleaned.rfind(","), cleaned.rfind("."))
    if last_sep == -1:
        # Sin separadores: número entero "crudo".
        try:
            return float(cleaned)
        except ValueError:
            return None

    # El último separador es el decimal; el resto son miles y se descartan.
    integer_part = re.sub(r"[.,]", "", cleaned[:last_sep])
    decimal_part = re.sub(r"[^0-9]", "", cleaned[last_sep + 1:])
    try:
        return float(f"{integer_part or '0'}.{decimal_part or '0'}")
    except ValueError:
        return None


def apply_fee(gross: float, fee_rate: float = 0.15) -> float:
    """Descuenta el fee de Steam de un valor bruto.

    El vendedor recibe ``gross / (1 + fee_rate)``: con ``fee_rate=0.15`` un valor
    de mercado de 1.15 deja 1.00 neto.
    """
    return gross / (1 + fee_rate)
