"""Test delle primitive: orologio, freschezza, kill switch, bus, logging.

Sono le fondamenta su cui poggiano le garanzie di sicurezza del sistema: se il
kill switch non blocca o se un segreto finisce nei log, nulla di quello che
viene costruito sopra è affidabile.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.core.clock import FrozenClock, age_seconds, ensure_utc
from backend.core.errors import KillSwitchEngagedError, StaleDataError
from backend.core.events import Event, EventBus, EventType
from backend.core.freshness import Fresh
from backend.core.kill_switch import KillSwitch
from backend.core.logging import redact_secrets

ADESSO = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)


class TestOrologio:
    def test_datetime_naive_interpretato_come_utc(self) -> None:
        """MT5 restituisce timestamp naive: trattarli come ora locale
        sposterebbe le candele di ore."""
        naive = datetime(2026, 3, 10, 10, 0)
        assert ensure_utc(naive).tzinfo is UTC

    def test_eta_in_secondi(self) -> None:
        passato = ADESSO - timedelta(seconds=90)
        assert age_seconds(passato, now=ADESSO) == 90.0

    def test_orologio_fermo_avanzabile(self) -> None:
        clock = FrozenClock(ADESSO)
        assert clock.now() == ADESSO
        clock.advance(minutes=5)
        assert clock.now() == ADESSO + timedelta(minutes=5)


class TestFreschezza:
    def test_dato_fresco_restituito(self) -> None:
        fresh = Fresh(value=42, as_of=ADESSO, ttl_seconds=60, source="prova")
        assert fresh.require_fresh(ADESSO + timedelta(seconds=30)) == 42

    def test_dato_scaduto_solleva_con_il_dettaglio(self) -> None:
        fresh = Fresh(value=42, as_of=ADESSO, ttl_seconds=60, source="prova")
        with pytest.raises(StaleDataError) as errore:
            fresh.require_fresh(ADESSO + timedelta(seconds=90))
        assert errore.value.context["age_seconds"] == 90.0
        assert errore.value.context["source"] == "prova"

    def test_il_valore_scaduto_resta_leggibile_solo_esplicitamente(self) -> None:
        """Ignorare la scadenza deve essere una riga visibile nelle review."""
        fresh = Fresh(value=42, as_of=ADESSO, ttl_seconds=1, source="prova")
        dopo = ADESSO + timedelta(seconds=10)
        assert fresh.is_stale(dopo) is True
        assert fresh.value == 42

    def test_as_of_normalizzato_a_utc(self) -> None:
        fresh = Fresh(value=1, as_of=datetime(2026, 3, 10, 10, 0), ttl_seconds=60, source="x")
        assert fresh.as_of.tzinfo is UTC


class TestKillSwitch:
    def test_assente_significa_libero(self, tmp_path: Path) -> None:
        ks = KillSwitch(tmp_path / "KILL", clock=FrozenClock(ADESSO))
        assert ks.state().engaged is False
        ks.require_clear()

    def test_attivazione_blocca_e_motiva(self, tmp_path: Path) -> None:
        ks = KillSwitch(tmp_path / "KILL", clock=FrozenClock(ADESSO))
        ks.engage("drawdown oltre il limite")
        stato = ks.state()
        assert stato.engaged is True
        assert stato.reason is not None
        assert "drawdown" in stato.reason
        with pytest.raises(KillSwitchEngagedError):
            ks.require_clear()

    def test_sopravvive_al_riavvio(self, tmp_path: Path) -> None:
        """Non deve disarmarsi da solo: il file resta."""
        percorso = tmp_path / "KILL"
        KillSwitch(percorso, clock=FrozenClock(ADESSO)).engage("fermato a mano")
        nuovo_processo = KillSwitch(percorso, clock=FrozenClock(ADESSO))
        assert nuovo_processo.state().engaged is True

    def test_rilascio(self, tmp_path: Path) -> None:
        ks = KillSwitch(tmp_path / "KILL", clock=FrozenClock(ADESSO))
        ks.engage("prova")
        ks.release()
        assert ks.state().engaged is False

    def test_forzato_da_configurazione(self, tmp_path: Path) -> None:
        ks = KillSwitch(tmp_path / "KILL", forced=True, clock=FrozenClock(ADESSO))
        stato = ks.state()
        assert stato.engaged is True
        assert stato.source == "config"

    def test_stato_illeggibile_blocca_comunque(self, tmp_path: Path) -> None:
        """Fail closed: se non sappiamo, non si opera."""
        # Una directory al posto del file rende la lettura impossibile.
        percorso = tmp_path / "KILL"
        percorso.mkdir()
        ks = KillSwitch(percorso, clock=FrozenClock(ADESSO))
        stato = ks.state()
        assert stato.engaged is True
        assert stato.source == "file-error"

    def test_attivazione_idempotente(self, tmp_path: Path) -> None:
        ks = KillSwitch(tmp_path / "KILL", clock=FrozenClock(ADESSO))
        ks.engage("uno")
        ks.engage("due")
        stato = ks.state()
        assert stato.reason is not None
        assert "due" in stato.reason


class TestEventBus:
    async def test_consegna_agli_iscritti(self) -> None:
        bus = EventBus()
        ricevuti: list[Event] = []

        async def handler(event: Event) -> None:
            ricevuti.append(event)

        bus.subscribe(EventType.CANDLE_CLOSED, handler)
        await bus.publish(Event(type=EventType.CANDLE_CLOSED, occurred_at=ADESSO))
        assert len(ricevuti) == 1

    async def test_wildcard_riceve_tutto(self) -> None:
        """Il journal si iscrive così: i moduli non sanno che esiste."""
        bus = EventBus()
        contati: list[str] = []

        async def spia(event: Event) -> None:
            contati.append(str(event.type))

        bus.subscribe_all(spia)
        await bus.publish(Event(type=EventType.ORDER_FILLED, occurred_at=ADESSO))
        await bus.publish(Event(type=EventType.POSITION_CLOSED, occurred_at=ADESSO))
        assert len(contati) == 2

    async def test_un_handler_rotto_non_ferma_gli_altri(self) -> None:
        """Il diario non deve poter far cadere l'esecuzione, né viceversa."""
        errori: list[str] = []
        bus = EventBus(on_handler_error=lambda e, nome, exc: errori.append(nome))
        eseguiti: list[str] = []

        async def rotto(event: Event) -> None:
            raise RuntimeError("guasto simulato")

        async def sano(event: Event) -> None:
            eseguiti.append("sano")

        bus.subscribe(EventType.TICK_RECEIVED, rotto, name="rotto")
        bus.subscribe(EventType.TICK_RECEIVED, sano, name="sano")
        await bus.publish(Event(type=EventType.TICK_RECEIVED, occurred_at=ADESSO))
        assert eseguiti == ["sano"]
        assert errori == ["rotto"]

    async def test_evento_senza_iscritti_non_fallisce(self) -> None:
        bus = EventBus()
        await bus.publish(Event(type=EventType.SYSTEM_STARTED, occurred_at=ADESSO))

    async def test_gli_handler_girano_insieme(self) -> None:
        bus = EventBus()
        ordine: list[str] = []

        async def lento(event: Event) -> None:
            await asyncio.sleep(0.02)
            ordine.append("lento")

        async def veloce(event: Event) -> None:
            ordine.append("veloce")

        bus.subscribe(EventType.NEWS_BATCH_INGESTED, lento)
        bus.subscribe(EventType.NEWS_BATCH_INGESTED, veloce)
        await bus.publish(Event(type=EventType.NEWS_BATCH_INGESTED, occurred_at=ADESSO))
        # Il veloce non ha aspettato il lento: un modulo lento non blocca gli altri.
        assert ordine == ["veloce", "lento"]


class TestCensuraSegreti:
    def test_le_chiavi_sensibili_vengono_azzerate(self) -> None:
        risultato = redact_secrets(
            None,
            "info",
            {
                "event": "connessione",
                "mt5_password": "segretissima",
                "anthropic_api_key": "sk-ant-xxx",
                "authorization": "Bearer abc",
                "login": 169_444_760,
            },
        )
        assert risultato["mt5_password"] == "**********"
        assert risultato["anthropic_api_key"] == "**********"
        assert risultato["authorization"] == "**********"
        # I dati non sensibili restano leggibili, altrimenti i log sono inutili.
        assert risultato["login"] == 169_444_760

    def test_censura_anche_annidata(self) -> None:
        risultato = redact_secrets(
            None,
            "info",
            {"config": {"db": {"password": "p"}, "host": "localhost"}},
        )
        assert risultato["config"]["db"]["password"] == "**********"
        assert risultato["config"]["host"] == "localhost"

    def test_censura_dentro_le_liste(self) -> None:
        risultato = redact_secrets(None, "info", {"conti": [{"token": "t", "id": 1}]})
        assert risultato["conti"][0]["token"] == "**********"
        assert risultato["conti"][0]["id"] == 1
