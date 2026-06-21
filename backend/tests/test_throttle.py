"""Tests del comportamiento adaptativo del throttle (sin red)."""
import time

from app.throttle import AsyncThrottle


def test_penalize_sube_intervalo_y_setea_cooldown():
    t = AsyncThrottle(interval=2.0)
    assert t._interval == 2.0

    t.penalize(30.0)
    assert t._interval == 2.0 * 1.5          # _BUMP
    assert t._cooldown_until > time.monotonic()  # quedó en cooldown


def test_relax_decae_hacia_el_base():
    t = AsyncThrottle(interval=2.0)
    t.penalize(1.0)            # interval -> 3.0
    t.relax()                  # 3.0 * 0.9 = 2.7
    assert 2.0 <= t._interval < 3.0
    # Con suficientes éxitos vuelve al base, sin pasarse.
    for _ in range(50):
        t.relax()
    assert t._interval == 2.0


def test_intervalo_tiene_tope():
    t = AsyncThrottle(interval=2.0)
    for _ in range(20):
        t.penalize(1.0)
    assert t._interval <= 2.0 * 4.0  # _MAX_FACTOR
