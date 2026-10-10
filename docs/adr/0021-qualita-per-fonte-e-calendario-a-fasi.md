# ADR 0021 — Qualità per fonte, metadati delle fonti, DXY come verifica, calendario a fasi

**Stato:** accettata · **Data:** 2026-10-05 · **Origine:** correzioni dell'utente
all'approvazione della fase 4.5 · **Modifica:** ADR 0018, ADR 0020

## 1. Qualità separata per dominio

**Prima.** Una sola `data_quality` mescolava prezzi XM, VIX, calendario e tassi: un
VIX vecchio di un giorno e un M15 fermo producevano lo stesso "DEGRADED".

**Ora.** `source_quality` riporta la qualità di cinque domini (`SourceDomain`):
`BROKER_DATA`, `MARKET_REFERENCE`, `CALENDAR`, `MACRO`, `NEWS`, ciascuno con
`configured`, motivi e fonti. La qualità **complessiva** (`global_market/sources.py`):

- parte da `BROKER_DATA` (i prezzi negoziabili sono la base);
- un calendario non `GOOD` la porta al massimo a `DEGRADED` (rischio evento non noto);
- un provider **configurato** in `FAILED` o `STALE` la porta al massimo a `DEGRADED`;
- un provider **non configurato per scelta** non la peggiora: finisce in
  `known_limitations`, così chi legge (persona o AI) sa cosa manca.

## 2. Ogni fonte dichiara frequenza, ritardo e uso previsto

`ProviderInfo` / `ProviderStatus` / `SourceDescriptor` hanno `frequency`
(`DataFrequency`), `last_observation`, `publication_delay`, `intended_use`
(`IntendedUse`: `INTRADAY_CONTEXT`, `STRUCTURAL_CONTEXT`, `EVENT_RISK`,
`CROSS_CHECK`). VIX (CBOE) e curva USA (Tesoro) sono `DAILY` /
`STRUCTURAL_CONTEXT`: entrano solo nella lettura D1 e non vengono mai presentati come
descrizione delle ultime ore. Il calendario è `WEEKLY_SCHEDULE` / `EVENT_RISK`.

## 3. Valute: il grafo è la misura, il DXY una verifica

**Prima.** Il DXY ricostruito partecipava al rischio (conflitto
`DXY_CONTRO_FORZA_USD`): due misure dello stesso dollaro contate due volte.

**Ora.** La forza a grafo (ADR 0020) è la sola misura delle valute. Il DXY ricostruito
è `CROSS_CHECK`: la verifica `DXY_RICOSTRUITO_VS_USD_GRAFO` confronta i due. Se
discordano, la qualità della forza valutaria scende al massimo a `DEGRADED`; il regime
non cambia. Il conflitto `DXY_CONTRO_FORZA_USD` è rimosso.

## 4. Grafo robusto

- **Pesi** 1/varianza per cross: `(L_w + J)·x = b`. Un cross molto volatile non
  domina gli altri.
- **Cross fermi** esclusi (`crosses_excluded`), mai trattati come rendimento zero.
- **Outlier**: residui oltre `max(6·1,4826·MAD, 1e-4)` rimossi, al massimo 3 per barra,
  con nuova stima (`outliers_removed`). Il grafo deve restare connesso.
- **Cross mancanti**: con il grafo connesso la stima resta valida (ADR 0020).

## 5. Calendario: previous / forecast / actual e fasi

`EconomicEvent` porta `previous`, `forecast`, `actual` e `actual_seen_at`. L'actual è
visibile **solo se** `actual_seen_at <= as_of` e `as_of >= scheduled_at`: un replay non
vede mai un dato pubblicato dopo. Fasi (`EventPhase`): `PRE_EVENT`, `EVENT_WINDOW`,
`POST_EVENT`, `ACTUAL_AVAILABLE`; sorpresa numerica (suffissi K/M/B/T e %) rispetto a
forecast e previous. Il provider resta intercambiabile dietro
`EconomicCalendarProvider`.

**Limite misurato.** Il feed gratuito di Forex Factory (JSON e XML) **non contiene gli
actual**: `ACTUAL_AVAILABLE` comparirà solo con un provider che li fornisce. È
dichiarato fra le `known_limitations`.
