"""Correlazioni: intervallo, assenza di NaN, allineamento per orario.

Test di proprietà: per qualunque serie di numeri finiti la correlazione è in
[-1, 1] oppure `None` — mai NaN, mai infinito, mai fuori intervallo.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.intermarket.correlation import (
    allinea,
    correlazioni_mobili,
    pearson,
    rendimenti_per_orario,
    stabilita,
)

finiti = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
qualsiasi = st.floats(allow_nan=True, allow_infinity=True)
T0 = datetime(2026, 3, 10, tzinfo=UTC)


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(finiti, finiti), min_size=0, max_size=80))
def test_correlazione_sempre_nell_intervallo_o_none(coppie: list[tuple[float, float]]) -> None:
    xs = [a for a, _ in coppie]
    ys = [b for _, b in coppie]
    r = pearson(xs, ys)
    assert r is None or (-1.0 <= r <= 1.0 and math.isfinite(r))


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(qualsiasi, qualsiasi), min_size=3, max_size=40))
def test_nessun_nan_anche_con_ingressi_sporchi(coppie: list[tuple[float, float]]) -> None:
    r = pearson([a for a, _ in coppie], [b for _, b in coppie])
    assert r is None or (-1.0 <= r <= 1.0 and math.isfinite(r))


@settings(max_examples=100, deadline=None)
@given(st.lists(finiti, min_size=25, max_size=80), st.integers(3, 20))
def test_correlazioni_mobili_nell_intervallo(xs: list[float], finestra: int) -> None:
    ys = [math.sin(i) * 3 + x / 7 for i, x in enumerate(xs)]
    for r in correlazioni_mobili(xs, ys, finestra):
        assert r is None or -1.0 <= r <= 1.0


def test_casi_noti() -> None:
    xs = [1.0, 2.0, 4.0, 3.0, 7.0, 5.0]
    assert pearson(xs, xs) == 1.0
    assert pearson(xs, [-x for x in xs]) == -1.0
    assert pearson(xs, [3 * x + 10 for x in xs]) == 1.0


def test_serie_costante_non_definita_e_non_zero() -> None:
    """Varianza nulla: "nessuna relazione" sarebbe un'affermazione, non un dato."""
    assert pearson([1.0] * 10, list(range(10))) is None
    assert pearson([1.0, 2.0], [2.0, 3.0]) is None  # troppo pochi punti


def test_nan_o_infinito_danno_none() -> None:
    assert pearson([1.0, float("nan"), 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]) is None
    assert pearson([1.0, float("inf"), 3.0, 4.0], [1.0, 2.0, 3.0, 4.0]) is None


def test_allineamento_per_orario_e_non_per_posizione() -> None:
    """Indici e forex hanno sessioni diverse: si usano solo gli istanti comuni."""
    a = {T0 + timedelta(hours=h): float(h) for h in range(10)}
    b = {T0 + timedelta(hours=h): float(h) * 2 for h in range(5, 15)}
    xs, ys = allinea(a, b)
    assert len(xs) == 5
    assert xs == (5.0, 6.0, 7.0, 8.0, 9.0)
    assert ys == (10.0, 12.0, 14.0, 16.0, 18.0)


def test_rendimenti_ignorano_prezzi_non_validi() -> None:
    tempi = [T0 + timedelta(hours=h) for h in range(5)]
    r = rendimenti_per_orario(tempi, [1.0, 0.0, 2.0, float("nan"), 2.2])
    assert all(math.isfinite(v) for v in r.values())
    # 1→0, 0→2, 2→nan, nan→2.2: nessun rendimento definito.
    assert r == {}


def test_stabilita() -> None:
    assert stabilita([0.5, 0.6, -0.1, 0.4], 0.5) == 0.75
    assert stabilita([0.5], None) is None
    assert stabilita([None, None], 0.3) is None
