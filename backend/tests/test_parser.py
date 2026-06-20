"""Tests del parser de precios y del fee."""
import pytest

from app.steam.parser import apply_fee, parse_price


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$0.18", 0.18),
        ("$1.99", 1.99),
        ("1,23€", 1.23),
        ("$1,234.56", 1234.56),     # coma = miles, punto = decimal
        ("1.234,56 €", 1234.56),    # punto = miles, coma = decimal
        ("0,18 руб", 0.18),
        ("  $2.00  ", 2.00),
        ("5", 5.0),                 # entero sin separador
    ],
)
def test_parse_price_formatos(raw, expected):
    assert parse_price(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "N/A", "gratis", "--"])
def test_parse_price_invalido_devuelve_none(raw):
    assert parse_price(raw) is None


def test_apply_fee_descuenta_15():
    # Un valor de mercado de 1.15 deja 1.00 neto con fee 0.15.
    assert apply_fee(1.15, 0.15) == pytest.approx(1.0)


def test_apply_fee_default():
    assert apply_fee(2.30) == pytest.approx(2.0)
