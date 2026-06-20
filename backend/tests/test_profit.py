"""Tests del cálculo de profit (función pura, sin red)."""
import pytest

from app.models import CardPrice
from app.routers.profit import build_card_price, compute_foil_summary, compute_profit


def make_cards(prices: list[float | None]) -> list[CardPrice]:
    """Helper: arma una lista de CardPrice; None = cromo sin precio."""
    cards: list[CardPrice] = []
    for i, p in enumerate(prices):
        if p is None:
            cards.append(CardPrice(name=f"Card{i}", success=False))
        else:
            cards.append(
                CardPrice(name=f"Card{i}", lowest_price=p, median_price=p, volume=10, success=True)
            )
    return cards


def test_desglose_basico():
    cards = make_cards([0.20, 0.20, 0.20, 0.20])  # set de 4 cromos
    r = compute_profit(570, "Test", 1.0, 1, cards, fee_rate=0.15, drop_ratio=0.5)

    assert r.total_cards == 4
    assert r.cards_dropped == 2                      # round(4 * 0.5)
    assert r.avg_card_price == pytest.approx(0.20)
    assert r.gross_card_value == pytest.approx(0.40)  # 0.20 * 2
    assert r.net_card_value == pytest.approx(0.40 / 1.15, abs=1e-4)
    assert r.profit == pytest.approx(0.40 / 1.15 - 1.0, abs=1e-4)
    assert r.profit_positive is False


def test_excluye_cromos_sin_precio():
    # 2 con precio, 2 sin precio: el promedio se calcula solo sobre los 2 válidos.
    cards = make_cards([0.20, None, 0.20, None])
    r = compute_profit(570, "Test", 0.10, 1, cards, fee_rate=0.15, drop_ratio=0.5)

    assert r.total_cards == 4
    assert r.cards_dropped == 2
    assert r.avg_card_price == pytest.approx(0.20)


def test_profit_positivo():
    # Cromos caros + juego barato => profit positivo.
    cards = make_cards([5.0, 5.0])
    r = compute_profit(570, "Test", 1.0, 1, cards, fee_rate=0.15, drop_ratio=0.5)

    # cards_dropped = 1, bruto = 5.0, neto = 5/1.15 ≈ 4.35, profit ≈ 3.35
    assert r.cards_dropped == 1
    assert r.net_card_value == pytest.approx(5.0 / 1.15, abs=1e-4)
    assert r.profit_positive is True


def test_sin_cromos_con_precio_no_rompe():
    cards = make_cards([None, None])
    r = compute_profit(570, "Test", 2.0, 1, cards)

    assert r.avg_card_price == 0.0
    assert r.gross_card_value == 0.0
    assert r.profit == pytest.approx(-2.0)
    assert r.profit_positive is False


def test_foil_summary_promedio_y_neto():
    # 2 foils con precio (2.0 y 4.0) => promedio 3.0; neto = 3/1.15.
    foils = make_cards([2.0, 4.0])
    r = compute_foil_summary(foils, fee_rate=0.15)

    assert r.total_foils == 2
    assert r.avg_foil_price == pytest.approx(3.0)
    assert r.net_avg_foil_price == pytest.approx(3.0 / 1.15, abs=1e-4)
    assert len(r.foils) == 2


def test_foil_summary_sin_precio_no_rompe():
    foils = make_cards([None, None])
    r = compute_foil_summary(foils)

    assert r.total_foils == 2
    assert r.avg_foil_price == 0.0
    assert r.net_avg_foil_price == 0.0


def test_build_card_price_success_false():
    card = build_card_price("570-Juggernaut", {"success": False})
    assert card.name == "Juggernaut"
    assert card.success is False
    assert card.lowest_price is None


def test_build_card_price_ok():
    raw = {"success": True, "lowest_price": "$0.18", "median_price": "$0.20", "volume": "1,234"}
    card = build_card_price("570-Juggernaut", raw)
    assert card.name == "Juggernaut"
    assert card.success is True
    assert card.lowest_price == pytest.approx(0.18)
    assert card.median_price == pytest.approx(0.20)
    assert card.volume == 1234
