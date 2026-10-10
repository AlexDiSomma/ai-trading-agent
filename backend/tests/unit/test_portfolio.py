"""Test di esposizione di portafoglio e storico operazioni.

Due proprietà che questi test difendono.

**Il rischio non misurabile non diventa zero.** Una posizione senza stop loss ha
rischio indeterminato; sommarla come se fosse nulla darebbe un portafoglio che
sembra prudente e non lo è. Il totale resta `None` e lo snapshot dichiara su quali
strumenti manca lo stop.

**Il realizzato si conta una volta sola.** MT5 registra un deal in ingresso e uno
in uscita per ogni posizione: sommare tutti i profitti conterebbe doppio. E i
movimenti di cassa non sono operazioni.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.core.enums import AssetClass, PositionSide
from backend.portfolio.deals import DealHistory
from backend.portfolio.exposure import ExposureCalculator
from backend.schemas.account import (
    AccountSnapshot,
    DealSnapshot,
    PortfolioSnapshot,
    PositionSnapshot,
)
from backend.schemas.instrument import ContractSpecification, Instrument
from backend.valuation.service import BrokerValuationService

ADESSO = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)

#: Valori misurati sul conto XM reale: il valore di un'unità di prezzo per lotto,
#: in euro, calcolato dal broker.
VALORE_UNITA = {"EURUSD": 88_870.0, "GOLD": 88.87, "US100Cash": 0.889}

#: Identità economica degli strumenti (ADR 0014): l'oro è XAU contro USD anche se
#: XM lo dichiara USD/USD, un indice è l'indice contro la sua valuta.
IDENTITA = {
    "EURUSD": ("EURUSD", AssetClass.FX, "EUR", "USD", 5, 0.00001),
    "GOLD": ("XAUUSD", AssetClass.METAL, "XAU", "USD", 2, 0.01),
    "US100Cash": ("US100", AssetClass.INDEX, "US100", "USD", 2, 0.01),
}


def strumento(nome: str) -> Instrument:
    canonico, classe, base, quotazione, cifre, punto = IDENTITA[nome]
    return Instrument(
        canonical=canonico,
        broker_symbol=nome,
        asset_class=classe,
        base_currency=base,
        quote_currency=quotazione,
        profit_currency="USD",
        margin_currency="USD",
        contract=ContractSpecification(
            digits=cifre,
            point=punto,
            tick_size=punto,
            contract_size=100.0,
            volume_min=0.01,
            volume_max=50.0,
            volume_step=0.01,
            stops_level_points=0,
            freeze_level_points=0,
            trade_mode=4,
        ),
        mapping_source="catalogo",
        as_of=ADESSO,
    )


class CalcolatoreLineare:
    """Calcolatore del broker finto: lineare, con i valori misurati su XM."""

    def __init__(self, guasto: bool = False) -> None:
        self.guasto = guasto

    async def calculate_profit(
        self,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
        *,
        sell: bool = False,
    ) -> float:
        if self.guasto:
            from backend.core.errors import BrokerError

            raise BrokerError(f"Il broker non calcola il valore per {symbol}")
        segno = -1 if sell else 1
        return round(segno * (price_close - price_open) * VALORE_UNITA[symbol] * volume, 2)

    async def calculate_margin(
        self, symbol: str, volume: float, price: float, *, sell: bool = False
    ) -> float:
        return 0.0


def calcolatore_con(broker: CalcolatoreLineare) -> ExposureCalculator:
    async def per_simbolo(simbolo: str) -> Instrument:
        return strumento(simbolo)

    return ExposureCalculator(
        valuation=BrokerValuationService(broker, account_currency="EUR"),
        instrument_of=per_simbolo,
    )


def posizione(
    simbolo: str,
    side: PositionSide,
    volume: float,
    apertura: float,
    corrente: float,
    stop: float | None = None,
    ticket: int = 1,
) -> PositionSnapshot:
    return PositionSnapshot(
        ticket=ticket,
        symbol=simbolo,
        side=side,
        volume=volume,
        price_open=apertura,
        price_current=corrente,
        stop_loss=stop,
        profit=0.0,
        opened_at=ADESSO - timedelta(hours=2),
        as_of=ADESSO,
    )


def portafoglio(*posizioni: PositionSnapshot, equity: float = 10_000.0) -> PortfolioSnapshot:
    conto = AccountSnapshot(
        login=1,
        server="XMGlobalMU-MT5 2",
        currency="EUR",
        leverage=1_000,
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
        account_type="DEMO",
        as_of=ADESSO,
    )
    return PortfolioSnapshot(account=conto, positions=posizioni, as_of=ADESSO)


@pytest.fixture
def calcolatore() -> ExposureCalculator:
    return calcolatore_con(CalcolatoreLineare())


class TestNozionale:
    async def test_nozionale_in_valuta_del_conto(self, calcolatore: ExposureCalculator) -> None:
        """0.1 lotti EURUSD a 1.125 valgono circa 10.000 euro di nozionale."""
        snap = await calcolatore.build(
            portafoglio(posizione("EURUSD", PositionSide.BUY, 0.1, 1.125, 1.125, 1.120))
        )
        assert snap.by_symbol[0].net_notional == pytest.approx(9_997.9, abs=1.0)

    async def test_nozionale_oro_con_il_valore_del_broker(
        self, calcolatore: ExposureCalculator
    ) -> None:
        """0.01 lotti di GOLD = 1 oncia a 4140 USD, cioè circa 3.679 euro.

        Con `tick_value` derivato sarebbe stato 4.140: il 12,5% in più, perché su
        metalli e indici quel campo non è nella valuta del conto (ADR 0012).
        """
        snap = await calcolatore.build(
            portafoglio(posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0))
        )
        assert snap.by_symbol[0].net_notional == pytest.approx(3_679.2, abs=1.0)

    async def test_short_ha_nozionale_negativo(self, calcolatore: ExposureCalculator) -> None:
        snap = await calcolatore.build(
            portafoglio(posizione("GOLD", PositionSide.SELL, 0.01, 4_140.0, 4_140.0, 4_150.0))
        )
        assert snap.by_symbol[0].net_notional < 0
        assert snap.by_symbol[0].direction == "SHORT"

    async def test_leva_lorda_sull_equity(self, calcolatore: ExposureCalculator) -> None:
        snap = await calcolatore.build(
            portafoglio(
                posizione("EURUSD", PositionSide.BUY, 0.1, 1.125, 1.125, 1.120),
                equity=10_000.0,
            )
        )
        assert snap.gross_leverage == pytest.approx(1.0, abs=0.01)


class TestRischioAgliStop:
    async def test_rischio_calcolato_dalla_distanza_dello_stop(
        self, calcolatore: ExposureCalculator
    ) -> None:
        """1 oncia di oro con stop a 10 dollari: 10 x 1 x 88.87/100 = 8.89 euro."""
        snap = await calcolatore.build(
            portafoglio(posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0))
        )
        assert snap.total_risk_at_stop == pytest.approx(8.89, abs=0.02)
        assert snap.risk_is_measurable is True

    async def test_posizione_senza_stop_rende_il_totale_non_misurabile(
        self, calcolatore: ExposureCalculator
    ) -> None:
        """Il test più importante: zero sarebbe una bugia rassicurante."""
        snap = await calcolatore.build(
            portafoglio(
                posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0, ticket=1),
                posizione("EURUSD", PositionSide.BUY, 0.1, 1.125, 1.125, None, ticket=2),
            )
        )
        assert snap.total_risk_at_stop is None
        assert snap.total_risk_pct is None
        assert snap.risk_is_measurable is False
        assert "EURUSD" in snap.positions_without_stop
        assert any("senza stop loss" in a for a in snap.warnings)

    async def test_rischio_percentuale_sull_equity(self, calcolatore: ExposureCalculator) -> None:
        snap = await calcolatore.build(
            portafoglio(
                posizione("GOLD", PositionSide.BUY, 0.1, 4_140.0, 4_140.0, 4_130.0),
                equity=10_000.0,
            )
        )
        assert snap.total_risk_pct == pytest.approx(0.889, abs=0.01)


class TestEsposizionePerValuta:
    async def test_coppia_forex_espone_due_valute_in_direzioni_opposte(
        self, calcolatore: ExposureCalculator
    ) -> None:
        """Un long EURUSD è lungo euro e corto dollaro."""
        snap = await calcolatore.build(
            portafoglio(posizione("EURUSD", PositionSide.BUY, 0.1, 1.125, 1.125, 1.120))
        )
        per_valuta = {c.currency: c.net_notional for c in snap.by_currency}
        assert per_valuta["EUR"] > 0
        assert per_valuta["USD"] < 0
        assert per_valuta["EUR"] == pytest.approx(-per_valuta["USD"])

    async def test_oro_espone_xau_contro_usd(self, calcolatore: ExposureCalculator) -> None:
        """XM dichiara GOLD come USD/USD, ma un long oro è lungo XAU e corto USD.

        Sostituisce `test_oro_contribuisce_una_volta_sola` (2026-10-04, ADR 0014):
        con le valute dichiarate dal broker l'oro risultava un'esposizione
        **lunga** sul dollaro, cioè di segno opposto a quella reale.
        """
        snap = await calcolatore.build(
            portafoglio(posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0))
        )
        per_valuta = {c.currency: c.net_notional for c in snap.by_currency}
        assert set(per_valuta) == {"XAU", "USD"}
        assert per_valuta["XAU"] > 0
        assert per_valuta["USD"] == pytest.approx(-per_valuta["XAU"])


class TestEsposizionePerClasse:
    async def test_oro_e_indice_restano_separati(self, calcolatore: ExposureCalculator) -> None:
        """La ragione per cui esiste questa vista.

        Per valuta, oro e Nasdaq finiscono entrambi in "USD" e sembrano rischio
        concentrato. Per classe di attività si vede che sono due scommesse diverse.
        """
        snap = await calcolatore.build(
            portafoglio(
                posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0, ticket=1),
                posizione(
                    "US100Cash", PositionSide.BUY, 1.0, 30_800.0, 30_800.0, 30_600.0, ticket=2
                ),
            )
        )
        classi = {e.asset_class for e in snap.by_asset_class}
        assert classi == {AssetClass.METAL, AssetClass.INDEX}
        # Per valuta il dollaro somma i due lati corti; oro e indice restano
        # visibili come esposizioni lunghe distinte.
        per_valuta = {c.currency: c.net_notional for c in snap.by_currency}
        assert set(per_valuta) == {"XAU", "US100", "USD"}
        assert per_valuta["USD"] == pytest.approx(-(per_valuta["XAU"] + per_valuta["US100"]))

    async def test_rischio_per_classe(self, calcolatore: ExposureCalculator) -> None:
        snap = await calcolatore.build(
            portafoglio(posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0))
        )
        metalli = next(e for e in snap.by_asset_class if e.asset_class is AssetClass.METAL)
        assert metalli.risk_at_stop == pytest.approx(8.89, abs=0.02)


class TestCoperture:
    async def test_posizioni_opposte_segnalate(self, calcolatore: ExposureCalculator) -> None:
        """Due spread pagati per non avere esposizione: va visto."""
        snap = await calcolatore.build(
            portafoglio(
                posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0, ticket=1),
                posizione("GOLD", PositionSide.SELL, 0.01, 4_140.0, 4_140.0, 4_150.0, ticket=2),
            )
        )
        oro = snap.by_symbol[0]
        assert oro.positions == 2
        assert oro.hedged is True
        assert oro.net_volume == pytest.approx(0.0)
        assert any("opposte" in a for a in snap.warnings)


class TestValoreNonCalcolabile:
    async def test_posizione_esclusa_dai_valori_ma_dichiarata(self) -> None:
        """Non si approssima: si dice che quel valore manca."""
        calcolatore = calcolatore_con(CalcolatoreLineare(guasto=True))
        snap = await calcolatore.build(
            portafoglio(posizione("GOLD", PositionSide.BUY, 0.01, 4_140.0, 4_140.0, 4_130.0))
        )
        assert snap.by_symbol == ()
        assert snap.total_risk_at_stop is None
        assert any("non calcolabile" in a for a in snap.warnings)


class TestPortafoglioVuoto:
    async def test_nessuna_posizione(self, calcolatore: ExposureCalculator) -> None:
        snap = await calcolatore.build(portafoglio())
        assert snap.open_positions == 0
        assert snap.gross_notional == 0.0
        assert snap.total_risk_at_stop == 0.0
        assert snap.risk_is_measurable is True


# ======================================================================
# Storico operazioni
# ======================================================================
def deal(
    ticket: int,
    *,
    entry: str,
    deal_type: str = "BUY",
    profit: float = 0.0,
    position_id: int = 100,
    volume: float = 0.1,
    price: float = 1.0,
    quando: datetime | None = None,
    commission: float = 0.0,
    swap: float = 0.0,
) -> DealSnapshot:
    return DealSnapshot(
        ticket=ticket,
        position_id=position_id,
        symbol="EURUSD",
        entry=entry,  # type: ignore[arg-type]
        deal_type=deal_type,
        volume=volume,
        price=price,
        profit=profit,
        commission=commission,
        swap=swap,
        executed_at=quando or ADESSO,
    )


def storico(*deals: DealSnapshot) -> DealHistory:
    async def leggi(_da: datetime, _a: datetime) -> tuple[DealSnapshot, ...]:
        return deals

    return DealHistory(leggi)


class TestRealizzato:
    async def test_solo_le_uscite_realizzano(self) -> None:
        """Sommare ingressi e uscite conterebbe due volte lo stesso trade."""
        riepilogo = await storico(
            deal(1, entry="IN", profit=0.0),
            deal(2, entry="OUT", deal_type="SELL", profit=50.0),
        ).realized_between(ADESSO - timedelta(days=1), ADESSO)
        assert riepilogo.realized_pnl == 50.0
        assert riepilogo.trades == 1
        assert riepilogo.wins == 1

    async def test_movimenti_di_cassa_fuori_dal_realizzato(self) -> None:
        """Un versamento muove l'equity ma non è un'operazione."""
        riepilogo = await storico(
            deal(1, entry="OUT", deal_type="SELL", profit=20.0),
            deal(2, entry="IN", deal_type="BALANCE", profit=1_000.0),
        ).realized_between(ADESSO - timedelta(days=1), ADESSO)
        assert riepilogo.realized_pnl == 20.0
        assert riepilogo.non_trading_flow == 1_000.0
        assert riepilogo.trades == 1

    async def test_costi_inclusi_nel_risultato(self) -> None:
        riepilogo = await storico(
            deal(1, entry="OUT", deal_type="SELL", profit=10.0, commission=-2.0, swap=-0.5)
        ).realized_between(ADESSO - timedelta(days=1), ADESSO)
        assert riepilogo.realized_pnl == pytest.approx(7.5)

    async def test_percentuale_vinte(self) -> None:
        riepilogo = await storico(
            deal(1, entry="OUT", deal_type="SELL", profit=10.0, position_id=1),
            deal(2, entry="OUT", deal_type="SELL", profit=-5.0, position_id=2),
            deal(3, entry="OUT", deal_type="SELL", profit=15.0, position_id=3),
        ).realized_between(ADESSO - timedelta(days=1), ADESSO)
        assert riepilogo.win_rate == pytest.approx(2 / 3, abs=0.01)

    async def test_nessun_deal(self) -> None:
        riepilogo = await storico().realized_between(ADESSO - timedelta(days=1), ADESSO)
        assert riepilogo.realized_pnl == 0.0
        assert riepilogo.win_rate is None

    async def test_giornata_sull_ora_del_broker(self) -> None:
        """L'intervallo parte dalla mezzanotte del broker, non da quella locale."""
        chiamate: list[tuple[datetime, datetime]] = []

        async def leggi(da: datetime, a: datetime) -> tuple[DealSnapshot, ...]:
            chiamate.append((da, a))
            return ()

        ora_broker = datetime(2026, 3, 10, 14, 30, tzinfo=UTC)
        await DealHistory(leggi).realized_on_broker_day(ora_broker)
        assert chiamate[0][0] == datetime(2026, 3, 10, 0, 0, tzinfo=UTC)

    async def test_settimana_dal_lunedi(self) -> None:
        chiamate: list[tuple[datetime, datetime]] = []

        async def leggi(da: datetime, a: datetime) -> tuple[DealSnapshot, ...]:
            chiamate.append((da, a))
            return ()

        mercoledi = datetime(2026, 3, 11, 14, 30, tzinfo=UTC)
        await DealHistory(leggi).realized_on_broker_week(mercoledi)
        assert chiamate[0][0].weekday() == 0
        assert chiamate[0][0].date() == datetime(2026, 3, 9).date()


class TestOperazioniRicostruite:
    async def test_ingresso_e_uscita_formano_un_operazione(self) -> None:
        operazioni = await storico(
            deal(1, entry="IN", deal_type="BUY", price=1.10, quando=ADESSO - timedelta(hours=3)),
            deal(2, entry="OUT", deal_type="SELL", price=1.12, profit=20.0, quando=ADESSO),
        ).closed_trades(ADESSO - timedelta(days=1), ADESSO)

        assert len(operazioni) == 1
        trade = operazioni[0]
        assert trade.direction == "LONG"
        assert trade.entry_price == pytest.approx(1.10)
        assert trade.exit_price == pytest.approx(1.12)
        assert trade.net_result == 20.0
        assert trade.is_win is True
        assert trade.duration == timedelta(hours=3)

    async def test_posizione_ancora_aperta_non_e_un_operazione(self) -> None:
        """Nessuna uscita: non si inventa una chiusura."""
        operazioni = await storico(deal(1, entry="IN")).closed_trades(
            ADESSO - timedelta(days=1), ADESSO
        )
        assert operazioni == ()

    async def test_uscita_in_tranche_media_ponderata(self) -> None:
        """Una media semplice darebbe un prezzo che non corrisponde al risultato."""
        operazioni = await storico(
            deal(1, entry="IN", deal_type="BUY", volume=1.0, price=100.0),
            deal(2, entry="OUT", deal_type="SELL", volume=0.75, price=110.0, profit=7.5),
            deal(3, entry="OUT", deal_type="SELL", volume=0.25, price=120.0, profit=5.0),
        ).closed_trades(ADESSO - timedelta(days=1), ADESSO)

        trade = operazioni[0]
        assert trade.exit_price == pytest.approx(112.5)
        assert trade.net_result == pytest.approx(12.5)

    async def test_short_riconosciuto_dall_ingresso(self) -> None:
        operazioni = await storico(
            deal(1, entry="IN", deal_type="SELL", price=1.12),
            deal(2, entry="OUT", deal_type="BUY", price=1.10, profit=20.0),
        ).closed_trades(ADESSO - timedelta(days=1), ADESSO)
        assert operazioni[0].direction == "SHORT"

    async def test_operazioni_ordinate_per_chiusura(self) -> None:
        operazioni = await storico(
            deal(1, entry="IN", position_id=1, quando=ADESSO - timedelta(hours=5)),
            deal(2, entry="OUT", deal_type="SELL", position_id=1, quando=ADESSO),
            deal(3, entry="IN", position_id=2, quando=ADESSO - timedelta(hours=4)),
            deal(
                4,
                entry="OUT",
                deal_type="SELL",
                position_id=2,
                quando=ADESSO - timedelta(hours=1),
            ),
        ).closed_trades(ADESSO - timedelta(days=1), ADESSO)
        assert [t.position_id for t in operazioni] == [2, 1]
