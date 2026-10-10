"""Test degli indicatori e delle primitive di calcolo.

Tre livelli di verifica, in ordine di forza:

1. **valori di riferimento** — RSI confrontato con la serie di esempio classica di
   Wilder, ATR calcolato a mano;
2. **proprietà invarianti** — RSI sempre in [0,100], ATR non negativo, EMA fra il
   minimo e il massimo della finestra, Bollinger nulla su serie costante;
3. **allineamento** — ogni serie restituita ha la stessa lunghezza dell'ingresso,
   con `None` dove il valore non è definito. È la proprietà che impedisce gli
   errori di disallineamento, i più insidiosi perché producono backtest che
   funzionano e sono sbagliati.

Nota sul riferimento RSI: la tabella pubblicata più diffusa riporta 70.53 sul
primo valore, questa implementazione dà 70.4641. La differenza viene
dall'arrotondamento dei prezzi nella tabella — 70.4641 è il risultato esatto della
formula di Wilder su quei prezzi a due decimali, verificato a mano. Il valore
atteso qui è quindi quello corretto per i dati dati, non quello della tabella.
"""

from __future__ import annotations

import pytest

from backend.core.enums import Timeframe
from backend.quant.indicators import (
    adx,
    ampiezza_bollinger,
    atr,
    bollinger,
    macd,
    momentum,
    rsi,
    volatilita_realizzata,
)
from backend.quant.series import (
    InsufficientDataError,
    SerieChiuse,
    deviazione_standard,
    ema,
    pendenza,
    rango_percentile,
    rendimenti_logaritmici,
    sma,
    true_range,
    ultimo,
    wilder,
)

#: Serie di prezzi dell'esempio RSI-14 classico.
PREZZI_RSI = [
    44.34,
    44.09,
    44.15,
    43.61,
    44.33,
    44.83,
    45.10,
    45.42,
    45.84,
    46.08,
    45.89,
    46.03,
    45.61,
    46.28,
    46.28,
    46.00,
    46.03,
    46.41,
    46.22,
    45.64,
    46.21,
    46.25,
    45.71,
    46.45,
    45.78,
    45.35,
    44.03,
    44.18,
    44.22,
    44.57,
    43.42,
    42.66,
    43.13,
]


def serie_rialzista(n: int = 120, passo: float = 2.0, base: float = 100.0):
    """Massimi, minimi e chiusure di una tendenza rialzista pulita."""
    massimi = [base + i * passo + 1 for i in range(n)]
    minimi = [base + i * passo - 1 for i in range(n)]
    chiusure = [base + i * passo for i in range(n)]
    return massimi, minimi, chiusure


class TestAllineamentoDelleSerie:
    """La proprietà che previene gli errori più insidiosi."""

    @pytest.mark.parametrize("periodo", [3, 14, 50])
    def test_lunghezza_conservata(self, periodo: int) -> None:
        valori = [float(i) for i in range(100)]
        for serie in (sma(valori, periodo), ema(valori, periodo)):
            assert len(serie) == len(valori)

    def test_none_prima_del_primo_valore_definito(self) -> None:
        valori = [float(i) for i in range(10)]
        risultato = sma(valori, 5)
        assert risultato[:4] == (None, None, None, None)
        assert risultato[4] is not None

    def test_serie_piu_corta_del_periodo_e_tutta_none(self) -> None:
        assert set(ema([1.0, 2.0], 14)) == {None}

    def test_true_range_non_definito_sulla_prima_barra(self) -> None:
        massimi, minimi, chiusure = serie_rialzista(10)
        risultato = true_range(massimi, minimi, chiusure)
        assert risultato[0] is None
        assert all(v is not None for v in risultato[1:])


class TestMedie:
    def test_sma_calcolo_diretto(self) -> None:
        assert sma([1.0, 2.0, 3.0, 4.0], 2)[3] == 3.5

    def test_ema_seminata_con_la_sma(self) -> None:
        """Convenzione MetaTrader: cambiare il seeding sposta i valori."""
        valori = [float(i) for i in range(1, 11)]
        risultato = ema(valori, 3)
        assert risultato[2] == pytest.approx(2.0)  # SMA(1,2,3)
        assert risultato[3] == pytest.approx(4 * 0.5 + 2.0 * 0.5)

    def test_ema_resta_fra_minimo_e_massimo(self) -> None:
        valori = [float(i % 7) for i in range(200)]
        for valore in ema(valori, 20):
            if valore is not None:
                assert min(valori) <= valore <= max(valori)

    def test_periodo_non_valido(self) -> None:
        with pytest.raises(ValueError):
            ema([1.0, 2.0], 0)


class TestWilder:
    def test_media_iniziale_semplice(self) -> None:
        valori = (None, 1.0, 1.0, 1.0, 1.0)
        risultato = wilder(valori, 4)
        assert risultato[4] == pytest.approx(1.0)
        assert risultato[3] is None

    def test_buco_in_mezzo_alla_serie_solleva(self) -> None:
        """I prezzi arrivano contigui: un buco è un difetto, non un caso normale."""
        with pytest.raises(InsufficientDataError, match="contigua"):
            wilder((1.0, 1.0, 1.0, None, 1.0), 2)


class TestRsi:
    def test_valori_contro_il_riferimento(self) -> None:
        calcolati = [v for v in rsi(PREZZI_RSI, 14) if v is not None]
        assert calcolati[0] == pytest.approx(70.4641, abs=0.0001)
        assert calcolati[1] == pytest.approx(66.2496, abs=0.0001)
        assert len(calcolati) == len(PREZZI_RSI) - 14

    def test_sempre_fra_zero_e_cento(self) -> None:
        for serie in (PREZZI_RSI, list(reversed(PREZZI_RSI))):
            for valore in rsi(serie, 14):
                if valore is not None:
                    assert 0.0 <= valore <= 100.0

    def test_salita_continua_da_cento(self) -> None:
        assert ultimo(rsi([float(i) for i in range(1, 40)], 14)) == 100.0

    def test_discesa_continua_da_zero(self) -> None:
        assert ultimo(rsi([float(i) for i in range(40, 1, -1)], 14)) == pytest.approx(0.0)

    def test_serie_piatta_da_cinquanta(self) -> None:
        """Nessun guadagno e nessuna perdita: 50 e non una divisione per zero."""
        assert ultimo(rsi([5.0] * 40, 14)) == 50.0


class TestAtr:
    def test_calcolo_a_mano(self) -> None:
        massimi = [10.0, 11.0, 12.0, 11.5]
        minimi = [9.0, 10.0, 11.0, 10.0]
        chiusure = [9.5, 10.5, 11.5, 10.5]
        # TR = 1.5, 1.5, 1.5 -> ATR(3) = 1.5
        assert atr(massimi, minimi, chiusure, 3)[3] == pytest.approx(1.5)

    def test_mai_negativo(self) -> None:
        massimi, minimi, chiusure = serie_rialzista(80)
        for valore in atr(massimi, minimi, chiusure, 14):
            if valore is not None:
                assert valore >= 0


class TestAdx:
    def test_tendenza_rialzista_pulita(self) -> None:
        """Valori verificati a mano sulla serie usata.

        Con passo 2 e barre di ampiezza 2 centrate sulla chiusura: +DM = 2 e
        TR = max(2, |massimo - chiusura precedente|, ...) = 3, quindi
        +DI = 100 x 2/3 = 66.67. Il numero dipende dalla distanza fra la barra e
        la chiusura precedente, non solo dal passo: e' il motivo per cui va
        ricalcolato a mano e non intuito.
        """
        massimi, minimi, chiusure = serie_rialzista(60)
        risultato = adx(massimi, minimi, chiusure, 14)
        assert ultimo(risultato.di_positivo) == pytest.approx(66.6667, abs=0.001)
        assert ultimo(risultato.di_negativo) == pytest.approx(0.0, abs=0.01)
        # Direzione unica: DX = 100 x |+DI - -DI| / (+DI + -DI) = 100.
        assert ultimo(risultato.adx) == pytest.approx(100.0, abs=0.01)

    def test_tendenza_ribassista_pulita(self) -> None:
        massimi, minimi, chiusure = serie_rialzista(60, passo=-2.0, base=300.0)
        risultato = adx(massimi, minimi, chiusure, 14)
        assert ultimo(risultato.di_negativo) > ultimo(risultato.di_positivo)  # type: ignore[operator]

    def test_solo_un_movimento_direzionale_per_barra(self) -> None:
        """La regola che, se sbagliata, fa sembrare laterale ogni tendenza."""
        massimi, minimi, chiusure = serie_rialzista(60)
        risultato = adx(massimi, minimi, chiusure, 14)
        # In una salita pulita il -DM è sempre zero, quindi -DI è zero.
        assert ultimo(risultato.di_negativo) == pytest.approx(0.0, abs=0.01)

    def test_adx_fra_zero_e_cento(self) -> None:
        massimi, minimi, chiusure = serie_rialzista(100, passo=0.0)
        for valore in adx(massimi, minimi, chiusure, 14).adx:
            if valore is not None:
                assert 0.0 <= valore <= 100.0


class TestBollinger:
    def test_serie_costante_ampiezza_nulla(self) -> None:
        bande = bollinger([5.0] * 40, 20)
        assert ultimo(bande.ampiezza) == 0.0
        assert ultimo(bande.centrale) == 5.0

    def test_bande_ordinate(self) -> None:
        bande = bollinger(PREZZI_RSI, 20)
        for sup, cen, inf in zip(bande.superiore, bande.centrale, bande.inferiore, strict=True):
            if None not in (sup, cen, inf):
                assert inf <= cen <= sup  # type: ignore[operator]

    def test_deviazione_standard_di_popolazione(self) -> None:
        """Popolazione, non campione: è la convenzione delle bande."""
        valori = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        assert deviazione_standard(valori, 8)[7] == pytest.approx(2.0)

    def test_rango_percentile_della_compressione(self) -> None:
        # Ampiezza che si restringe: l'ultimo valore è il minimo storico.
        chiusure = [100 + (1 if i % 2 else -1) * max(10 - i * 0.1, 0.1) for i in range(200)]
        bande = bollinger(chiusure, 20)
        _, rango = ampiezza_bollinger(bande, 120)
        assert rango is not None
        assert rango < 0.2


class TestMacd:
    def test_linea_e_segnale_su_tendenza_lineare(self) -> None:
        risultato = macd([float(i) for i in range(1, 90)])
        assert ultimo(risultato.linea) == pytest.approx(7.0, abs=0.01)
        assert ultimo(risultato.istogramma) == pytest.approx(0.0, abs=0.01)

    def test_segnale_calcolato_sulla_serie_completa(self) -> None:
        """Il segnale non deve dipendere da quante barre si sono chieste."""
        lunga = macd([float(i) for i in range(1, 200)])
        corta = macd([float(i) for i in range(101, 200)])
        assert ultimo(lunga.segnale) == pytest.approx(ultimo(corta.segnale), abs=0.01)

    def test_serie_troppo_corta(self) -> None:
        risultato = macd([1.0, 2.0, 3.0])
        assert ultimo(risultato.linea) is None


class TestVolatilitaEMomentum:
    def test_rendimenti_logaritmici_additivi(self) -> None:
        risultato = rendimenti_logaritmici([100.0, 110.0, 121.0])
        assert risultato[1] == pytest.approx(risultato[2])  # type: ignore[arg-type]

    def test_volatilita_nulla_su_serie_costante(self) -> None:
        per_barra, annua = volatilita_realizzata([10.0] * 50, 20, 252)
        assert per_barra == pytest.approx(0.0)
        assert annua == pytest.approx(0.0)

    def test_annualizzazione_solo_se_richiesta(self) -> None:
        """Un fattore di annualizzazione inventato darebbe un numero autorevole e falso."""
        per_barra, annua = volatilita_realizzata(PREZZI_RSI, 20)
        assert per_barra is not None
        assert annua is None

    def test_momentum_su_piu_orizzonti(self) -> None:
        chiusure = [float(i) for i in range(1, 101)]
        risultato = momentum(chiusure, (5, 20, 60))
        assert risultato[5] == pytest.approx((100 / 95 - 1) * 100)
        assert risultato[20] is not None
        assert risultato[60] is not None

    def test_momentum_orizzonte_troppo_lungo(self) -> None:
        assert momentum([1.0, 2.0, 3.0], (60,))[60] is None


class TestPendenzaERango:
    def test_pendenza_positiva_su_salita(self) -> None:
        valori = tuple(float(i) for i in range(50))
        assert (pendenza(valori, 10) or 0) > 0

    def test_pendenza_nulla_su_serie_piatta(self) -> None:
        assert pendenza((5.0,) * 20, 10) == pytest.approx(0.0)

    def test_pendenza_normalizzata_sul_livello(self) -> None:
        """Deve essere confrontabile fra EURUSD a 1.12 e US100Cash a 30.000."""
        piccoli = tuple(1.0 + i * 0.01 for i in range(30))
        grandi = tuple(10_000.0 + i * 100.0 for i in range(30))
        assert pendenza(piccoli, 10) == pytest.approx(pendenza(grandi, 10), rel=0.05)

    def test_rango_richiede_storia(self) -> None:
        assert rango_percentile((1.0, 2.0), 120) is None


class TestSerieChiuse:
    async def test_costruzione_dalle_candele(self, connettore) -> None:  # type: ignore[no-untyped-def]
        serie = await connettore.get_candles("EURUSD", Timeframe.H1, 50)
        chiuse = SerieChiuse.da_candele(serie.closed)
        assert len(chiuse) == 50
        assert len(chiuse.massimi) == len(chiuse.minimi) == len(chiuse.chiusure)
        assert chiuse.ultima_chiusura == serie.closed[-1].close

    def test_richiedi_solleva_su_serie_corta(self) -> None:
        chiuse = SerieChiuse(
            aperture=(1.0,),
            massimi=(1.0,),
            minimi=(1.0,),
            chiusure=(1.0,),
            volumi=(1.0,),
            tempi=(1,),
        )
        with pytest.raises(InsufficientDataError):
            chiuse.richiedi(10, "prova")
