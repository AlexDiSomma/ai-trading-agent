"""Test della struttura di mercato: swing, struttura, livelli, rotture, tendenza.

Il test più importante del file è `test_gli_ultimi_swing_non_sono_confermati`.
Verifica la proprietà che separa un sistema onesto da uno che "vede" i massimi nel
momento in cui si formano: uno swing richiede barre alla propria destra, quindi le
ultime non sono classificabili. Senza quel vincolo i backtest diventano brillanti
e il sistema reale perde denaro.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from backend.core.enums import (
    BreakoutKind,
    LevelKind,
    MarketRegime,
    StructureBreak,
    StructureTrend,
    SwingKind,
)
from backend.regime.trend import classifica_trend
from backend.schemas.quant import IndicatorSnapshot, StructureSnapshot
from backend.structure.breakout import rileva_breakout
from backend.structure.levels import trova_livelli
from backend.structure.market_structure import analizza_struttura
from backend.structure.swings import alterna, solo_confermati, trova_swing

INIZIO = datetime(2026, 1, 1, tzinfo=UTC)


def tempi(n: int) -> list[datetime]:
    return [INIZIO + timedelta(hours=i) for i in range(n)]


def zigzag(valori: list[float]) -> tuple[list[float], list[float], list[datetime]]:
    """Massimi e minimi attorno a una serie di prezzi dati, barra per barra."""
    massimi = [v + 0.5 for v in valori]
    minimi = [v - 0.5 for v in valori]
    return massimi, minimi, tempi(len(valori))


def da_pivot(
    pivot: list[float], barre_per_tratto: int = 4
) -> tuple[list[float], list[float], list[datetime]]:
    """Serie costruita interpolando fra pivot dati.

    Serve perche' un massimo e' uno swing solo se le `forza` barre successive non
    lo superano: una lista di prezzi a zigzag "stretto" non contiene veri estremi
    locali, e il riconoscimento — correttamente — non trova nulla. Interpolando
    con quattro barre per tratto, ogni pivot ha abbastanza spazio su entrambi i
    lati per essere confermato con forza 2 o 3.
    """
    prezzi: list[float] = [pivot[0]]
    for partenza, arrivo in pairwise(pivot):
        for passo in range(1, barre_per_tratto + 1):
            prezzi.append(partenza + (arrivo - partenza) * passo / barre_per_tratto)
    return zigzag(prezzi)


class TestSwing:
    def test_massimo_locale_riconosciuto(self) -> None:
        prezzi = [10, 11, 12, 13, 20, 13, 12, 11, 10]
        massimi, minimi, istanti = zigzag([float(p) for p in prezzi])
        punti = trova_swing(massimi, minimi, istanti, forza=3)
        alti = [p for p in punti if p.kind is SwingKind.HIGH]
        assert any(p.index == 4 for p in alti)

    def test_gli_ultimi_swing_non_sono_confermati(self) -> None:
        """La proprietà che impedisce di sapere il futuro.

        Con forza 3, un massimo sulle ultime tre barre non può essere confermato:
        servono tre barre successive che non lo superino, e non esistono ancora.
        """
        prezzi = [float(i) for i in range(20)] + [25.0, 24.0]
        massimi, minimi, istanti = zigzag(prezzi)
        punti = trova_swing(massimi, minimi, istanti, forza=3)
        ultimo_indice = len(prezzi) - 1
        for punto in punti:
            if punto.index > ultimo_indice - 3:
                assert punto.confirmed is False

    def test_i_confermati_hanno_barre_a_destra(self) -> None:
        prezzi = [10, 12, 20, 12, 10, 11, 18, 11, 9, 10, 15, 10, 8]
        massimi, minimi, istanti = zigzag([float(p) for p in prezzi])
        punti = solo_confermati(trova_swing(massimi, minimi, istanti, forza=2))
        for punto in punti:
            assert punto.index + punto.strength <= len(prezzi) - 1

    def test_alternanza_tiene_il_massimo_piu_alto(self) -> None:
        """Due massimi di seguito senza minimo in mezzo: resta il più alto."""
        prezzi = [10, 20, 15, 22, 10, 5, 10]
        massimi, minimi, istanti = zigzag([float(p) for p in prezzi])
        punti = alterna(trova_swing(massimi, minimi, istanti, forza=1))
        tipi = [p.kind for p in punti]
        for primo, secondo in pairwise(tipi):
            assert primo is not secondo

    def test_forza_non_valida(self) -> None:
        with pytest.raises(ValueError):
            trova_swing([1.0], [1.0], tempi(1), forza=0)

    def test_serie_di_lunghezze_diverse(self) -> None:
        with pytest.raises(ValueError, match="pari lunghezza"):
            trova_swing([1.0, 2.0], [1.0], tempi(2))


class TestStrutturaDiMercato:
    def costruisci(self, pivot: list[float], forza: int = 2) -> StructureSnapshot:
        massimi, minimi, istanti = da_pivot(pivot)
        punti = trova_swing(massimi, minimi, istanti, forza=forza)
        return analizza_struttura(punti, pivot[-1])

    def test_tendenza_rialzista(self) -> None:
        # Minimi crescenti e massimi crescenti.
        prezzi = [10, 8, 14, 11, 18, 15, 22, 19, 26, 23, 30, 27, 34]
        stato = self.costruisci([float(p) for p in prezzi])
        assert stato.trend is StructureTrend.UPTREND

    def test_tendenza_ribassista(self) -> None:
        prezzi = [34, 36, 30, 32, 26, 28, 22, 24, 18, 20, 14, 16, 10]
        stato = self.costruisci([float(p) for p in prezzi])
        assert stato.trend is StructureTrend.DOWNTREND

    def test_massimi_crescenti_e_minimi_decrescenti_sono_un_range(self) -> None:
        """Un mercato che si allarga non è rialzista, anche se fa nuovi massimi."""
        prezzi = [20, 18, 24, 14, 28, 10, 32, 6, 36]
        stato = self.costruisci([float(p) for p in prezzi])
        assert stato.trend is StructureTrend.RANGE

    def test_pochi_swing_struttura_indefinita(self) -> None:
        stato = self.costruisci([10.0, 12.0, 11.0, 13.0])
        assert stato.trend is StructureTrend.UNDEFINED

    def test_rottura_al_rialzo(self) -> None:
        pivot = [10.0, 8.0, 14.0, 11.0, 18.0, 15.0, 22.0, 19.0, 26.0, 23.0]
        massimi, minimi, istanti = da_pivot(pivot)
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        stato = analizza_struttura(punti, 100.0)
        assert stato.structure_break is StructureBreak.BREAK_UP

    def test_cambio_di_carattere(self) -> None:
        """Rottura al ribasso dentro una struttura rialzista."""
        pivot = [10.0, 8.0, 14.0, 11.0, 18.0, 15.0, 22.0, 19.0, 26.0, 23.0, 30.0]
        massimi, minimi, istanti = da_pivot(pivot)
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        stato = analizza_struttura(punti, 1.0)
        assert stato.trend is StructureTrend.UPTREND
        assert stato.structure_break is StructureBreak.BREAK_DOWN
        assert stato.change_of_character is True

    def test_usa_solo_swing_confermati(self) -> None:
        """Un massimo non confermato non deve entrare nella struttura.

        L'ultimo pivot a 99 e' sulle barre finali: con forza 3 non ha ancora le
        barre a destra che lo validino, quindi non puo' comparire come ultimo
        massimo di struttura.
        """
        pivot = [10.0, 8.0, 14.0, 11.0, 18.0, 15.0, 22.0, 19.0, 26.0, 23.0, 99.0]
        massimi, minimi, istanti = da_pivot(pivot)
        punti = trova_swing(massimi, minimi, istanti, forza=3)
        stato = analizza_struttura(punti, 99.0)
        assert stato.last_swing_high is not None
        assert stato.last_swing_high < 99


class TestLivelli:
    def test_supporti_sotto_e_resistenze_sopra(self) -> None:
        pivot = [10.0, 8.0, 14.0, 11.0, 18.0, 15.0, 22.0, 19.0, 26.0, 23.0, 30.0, 27.0]
        massimi, minimi, istanti = da_pivot(pivot)
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        livelli = trova_livelli(punti, 20.0, atr=1.0, ultimo_indice=len(massimi) - 1)
        for livello in livelli:
            if livello.kind is LevelKind.SUPPORT:
                assert livello.price < 20.0
            else:
                assert livello.price > 20.0

    def test_senza_atr_nessun_livello(self) -> None:
        """Una tolleranza arbitraria produrrebbe zone prive di significato."""
        massimi, minimi, istanti = da_pivot([10.0, 20.0, 10.0, 20.0, 10.0])
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        assert trova_livelli(punti, 15.0, atr=None, ultimo_indice=len(massimi) - 1) == ()

    def test_swing_vicini_raggruppati(self) -> None:
        """Due tocchi sullo stesso prezzo sono un livello, non due."""
        pivot = [10.0, 20.0, 10.0, 20.1, 10.0, 20.05, 10.0]
        massimi, minimi, istanti = da_pivot(pivot)
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        livelli = trova_livelli(punti, 15.0, atr=1.0, ultimo_indice=len(massimi) - 1)
        resistenze = [lv for lv in livelli if lv.kind is LevelKind.RESISTANCE]
        assert len(resistenze) == 1
        assert resistenze[0].touches >= 2

    def test_distanza_espressa_in_atr(self) -> None:
        massimi, minimi, istanti = da_pivot([10.0, 30.0, 10.0, 30.0, 10.0])
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        livelli = trova_livelli(punti, 20.0, atr=2.0, ultimo_indice=len(massimi) - 1)
        for livello in livelli:
            assert livello.distance_atr == pytest.approx(abs(20.0 - livello.price) / 2.0, abs=0.01)

    def test_forza_fra_zero_e_uno(self) -> None:
        massimi, minimi, istanti = da_pivot([10.0, 20.0, 10.0, 20.0, 10.0, 20.0, 10.0])
        punti = trova_swing(massimi, minimi, istanti, forza=2)
        for livello in trova_livelli(punti, 15.0, atr=1.0, ultimo_indice=len(massimi) - 1):
            assert 0.0 <= livello.strength <= 1.0


class TestBreakout:
    def serie_laterale(self, n: int = 30, centro: float = 100.0, ampiezza: float = 1.0):
        massimi = [centro + ampiezza for _ in range(n)]
        minimi = [centro - ampiezza for _ in range(n)]
        chiusure = [centro + (ampiezza / 2 if i % 2 else -ampiezza / 2) for i in range(n)]
        return massimi, minimi, chiusure

    def test_compressione_riconosciuta(self) -> None:
        """Range stretto senza rotture recenti: compressione, non ritest.

        Prima versione del codice: la vicinanza al bordo del range bastava a
        classificare un RETEST, e in un range stretto quella condizione e' sempre
        vera. Un ritest richiede una rottura precedente.
        """
        massimi, minimi, chiusure = self.serie_laterale(ampiezza=0.5)
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.kind is BreakoutKind.COMPRESSION
        assert stato.bars_since_break is None
        assert stato.range_width_atr is not None
        assert stato.range_width_atr <= stato.compression_threshold_atr

    def test_rottura_al_rialzo(self) -> None:
        massimi, minimi, chiusure = self.serie_laterale()
        chiusure[-1] = 110.0
        massimi[-1] = 110.5
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.kind is BreakoutKind.BREAKOUT_UP
        assert stato.broken_level == pytest.approx(101.0)
        assert stato.bars_since_break == 0

    def test_rottura_al_ribasso(self) -> None:
        massimi, minimi, chiusure = self.serie_laterale()
        chiusure[-1] = 90.0
        minimi[-1] = 89.5
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.kind is BreakoutKind.BREAKOUT_DOWN

    def test_il_range_esclude_la_barra_che_lo_rompe(self) -> None:
        """Includerla allargherebbe il range fino a contenere sempre la rottura."""
        massimi, minimi, chiusure = self.serie_laterale()
        massimi[-1] = 200.0
        chiusure[-1] = 199.0
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.range_high == pytest.approx(101.0)
        assert stato.kind is BreakoutKind.BREAKOUT_UP

    def test_falsa_rottura(self) -> None:
        massimi, minimi, chiusure = self.serie_laterale()
        chiusure[-2] = 110.0  # ha rotto
        chiusure[-1] = 100.0  # ed è rientrata
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.kind is BreakoutKind.FALSE_BREAKOUT_UP
        assert stato.bars_since_break == 1

    def test_ombra_oltre_il_livello_non_e_rottura(self) -> None:
        """Conta la chiusura: una barra che sfiora e rientra non ha rotto."""
        massimi, minimi, chiusure = self.serie_laterale()
        massimi[-1] = 105.0  # ombra fuori
        chiusure[-1] = 100.3  # chiusura dentro
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.kind not in (BreakoutKind.BREAKOUT_UP, BreakoutKind.BREAKOUT_DOWN)

    def test_ritest_richiede_una_rottura_precedente(self) -> None:
        """Rottura due barre fa, prezzo tornato sul livello: ritest."""
        massimi, minimi, chiusure = self.serie_laterale()
        chiusure[-2] = 110.0  # ha rotto
        chiusure[-1] = 100.9  # tornato entro 0.25 ATR da 101.0
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0)
        assert stato.kind is BreakoutKind.RETEST
        assert stato.broken_level == pytest.approx(101.0)
        assert stato.bars_since_break == 1

    def test_serie_troppo_corta(self) -> None:
        stato = rileva_breakout([1.0] * 5, [1.0] * 5, [1.0] * 5, atr=1.0)
        assert stato.kind is BreakoutKind.NONE

    def test_senza_atr_nessuna_valutazione(self) -> None:
        massimi, minimi, chiusure = self.serie_laterale()
        assert rileva_breakout(massimi, minimi, chiusure, atr=None).kind is BreakoutKind.NONE

    def test_soglie_riportate_nel_risultato(self) -> None:
        """Il risultato non si interpreta senza sapere con che soglie è stato prodotto."""
        massimi, minimi, chiusure = self.serie_laterale()
        stato = rileva_breakout(massimi, minimi, chiusure, atr=1.0, lookback=15)
        assert stato.lookback_bars == 15
        assert stato.compression_threshold_atr > 0


def indicatori(**override) -> IndicatorSnapshot:  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "ema20": 110.0,
        "ema50": 105.0,
        "ema200": 100.0,
        "atr_pct": 1.0,
        "adx14": 30.0,
        "di_plus": 30.0,
        "di_minus": 10.0,
        "ema50_slope_pct": 0.2,
        "close": 112.0,
        "bars_used": 300,
    }
    base.update(override)
    return IndicatorSnapshot(**base)  # type: ignore[arg-type]


def struttura(trend: StructureTrend = StructureTrend.UPTREND) -> StructureSnapshot:
    return StructureSnapshot(trend=trend, confirmed_swings=6)


class TestClassificazioneTendenza:
    def test_tendenza_rialzista_forte(self) -> None:
        esito = classifica_trend(indicatori(), struttura())
        assert esito.regime is MarketRegime.STRONG_TREND_UP
        assert esito.direction == 1
        assert esito.score > 0.5

    def test_tendenza_ribassista_forte(self) -> None:
        esito = classifica_trend(
            indicatori(
                ema20=90.0,
                ema50=95.0,
                ema200=100.0,
                close=88.0,
                di_plus=10.0,
                di_minus=30.0,
                ema50_slope_pct=-0.2,
            ),
            struttura(StructureTrend.DOWNTREND),
        )
        assert esito.regime is MarketRegime.STRONG_TREND_DOWN
        assert esito.direction == -1

    def test_laterale_con_adx_basso(self) -> None:
        esito = classifica_trend(
            indicatori(
                ema20=100.0,
                ema50=100.0,
                ema200=100.0,
                close=100.0,
                adx14=10.0,
                di_plus=15.0,
                di_minus=15.0,
                ema50_slope_pct=0.0,
            ),
            struttura(StructureTrend.RANGE),
        )
        assert esito.regime is MarketRegime.RANGING
        assert esito.direction == 0

    def test_compressione_di_volatilita(self) -> None:
        esito = classifica_trend(
            indicatori(
                ema20=100.0,
                ema50=100.0,
                ema200=100.0,
                close=100.0,
                adx14=19.0,
                di_plus=15.0,
                di_minus=15.0,
                ema50_slope_pct=0.0,
                bollinger_width_rank=0.05,
            ),
            struttura(StructureTrend.RANGE),
        )
        assert esito.regime is MarketRegime.VOLATILITY_COMPRESSION

    def test_senza_adx_regime_incerto(self) -> None:
        """Non si inventa una tendenza quando manca la misura della forza."""
        esito = classifica_trend(indicatori(adx14=None), struttura())
        assert esito.regime is MarketRegime.UNCERTAIN

    def test_fattori_separati_e_leggibili(self) -> None:
        """Un punteggio di cui non si leggono le componenti è inutilizzabile."""
        esito = classifica_trend(indicatori(), struttura())
        assert set(esito.factors) == {
            "allineamento_medie",
            "prezzo_vs_ema200",
            "pendenza_ema50",
            "direzione_di",
            "struttura",
        }
        for valore in esito.factors.values():
            assert -1.0 <= valore <= 1.0

    def test_motivazioni_in_italiano(self) -> None:
        esito = classifica_trend(indicatori(), struttura())
        assert esito.reasons
        assert any("Medie allineate" in m for m in esito.reasons)

    def test_punteggio_e_confidenza_nei_limiti(self) -> None:
        for adx_valore in (None, 0.0, 15.0, 50.0, 100.0):
            esito = classifica_trend(indicatori(adx14=adx_valore), struttura())
            assert -1.0 <= esito.score <= 1.0
            assert 0.0 <= esito.confidence <= 1.0

    def test_fattori_discordi_abbassano_la_confidenza(self) -> None:
        concordi = classifica_trend(indicatori(), struttura())
        discordi = classifica_trend(
            indicatori(ema50_slope_pct=-0.5, di_plus=10.0, di_minus=30.0),
            struttura(StructureTrend.DOWNTREND),
        )
        assert discordi.confidence < concordi.confidence
