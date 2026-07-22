## [2.1.23] - 2026-07-20
### Added -- Snag 19: recent phantom rows in the Archie Brief
- The Archie Brief now lists the **last 5 phantom rows** (newest first) directly under
  the STAY OUT QUALITY summary, so Archie sees overnight phantom activity inline without
  a separate PHANTOM-page screenshot. Columns: Date/Time (UTC), Direction, Confidence,
  1hr Move, Verdict. PENDING rows shown as PENDING; empty -> "No phantom data yet".
  Display only -- reads the same stay_out_quality decisions; no logic/threshold change.

## [2.1.22] - 2026-07-19
### Changed -- Macro Sentiment Live Reload (required before go-live)
- `get_macro()` now re-reads `macro_sentiment.json` fresh from disk on every Arthur
  consultation instead of caching for 5 min at startup. Changing the macro flag on
  RoundTable (e.g. NEUTRAL -> RISK_OFF) now takes effect on the next consultation --
  within one candle interval, NO restart, open positions unaffected.
- A 5-second debounce coalesces the several get_macro() calls made within a single
  consultation (fetch_sentiment / format_news_context / regime_block) so it doesn't
  hammer disk. Weighting logic unchanged -- overlay still feeds Arthur as context +
  directional sentiment only; Arthur makes every call.

## [2.1.21] - 2026-07-19
### Added -- dedicated PHANTOM page (desk rollout, template CryptoTrader v1.7.3)
- New **PHANTOM &rarr;** header button opens page 4: "PHANTOM TRADES -- Stay Out Quality"
  with a summary (Quality %% / Correct / Wrong / Neutral / Net Saved / Net Missed) and a
  clean **last-20** table (newest first): Date/Time UTC | Direction | Entry Price |
  Confidence | 1hr Move | colour-coded Verdict. Back to Dashboard + Trading nav.
- The right-panel Stay Out Quality card is now a **compact** clickable summary that opens
  the full page. Standardised to the last 20 rows (was 10). Display only -- reads the same
  get_stay_out_quality() data; no threshold/logic/recording change.

## [2.1.20] - 2026-07-18
### Changed -- Guinevere moved to a dedicated page (display only)
- The full Guinevere section (news sentiment + keyword editor) now lives on a dedicated
  page reached via a **GUINEVERE** button in the header (same pattern as P&L), with a
  "Back to Dashboard" link. This fixes the editor overlapping the main grid and the
  ADD BEARISH button falling below the visible area in the narrow right panel.
- The main dashboard right panel now shows a **compact** Guinevere summary (sentiment +
  score + top headline) that opens the full page on click. No trading-logic change.

## [2.1.19] - 2026-07-18
### Added -- Guinevere news module on dashboard + macro overlay (Parts 4 & 5)
- **Dashboard Guinevere news card** (`/api/news`): sentiment, score, headlines, event
  caution window and macro flag -- same pattern as Gold/Oil/Gas. Direction-aware +/-8
  confidence (LONG+BULLISH/SHORT+BEARISH +8; opposite -8) was already wired in the
  engine from the System 1 reset.
- **Keyword editor** (Part 3) + `/api/keywords` GET/POST on the FTSE dashboard.
- **Macro sentiment overlay** (Part 4): get_macro()/get_macro_adjustment()/get_macro_context()
  read RoundTable's macro_sentiment.json; FTSE nudge RISK_ON +2, RISK_OFF -2, CRISIS -3
  (+ CRISIS confidence bar +10); macro flag shown on the news card and in Arthur's prompt.
- **Keyword** "North Sea exploration" added to the FTSE bullish set (brief Part 5).

## [2.1.18] - 2026-07-18
### Fixed -- Guinevere score now logged to phantom rows (desk-wide sweep)
- `guinevere_score` is now written to phantom rows at signal time via the cached
  `fetch_ftse_sentiment (both the Morgan-gate and STAY_OUT phantom records)` -- previously blank on all phantom rows (flagged by the System 5/6 Gaius
  reviews). Logging-only change (build_snapshot(guinevere_score=...)); no trading-rule
  change, no backtest. Takes effect on restart. Existing rows stay blank (going-forward fix).

## [2.1.17] - 2026-07-18
### Changed -- phantom verdict threshold (System 5 Review desk-wide, Rec 1 pattern)
- **`VERDICT_THRESHOLD` 10 -> 7** (index pts, 1hr window). Re-scored 78 rows: CORRECT
  20/WRONG 8/NEUTRAL 50 -> 25/14/39 (50.0%% classified, 11 changed). Data-only (logs/, gitignored).
  No trading-rule change; no backtest required. Verdict threshold mis-scaling was assessed
  desk-wide on 18 Jul; see the OilTrader v1.1.18 fix that established this pattern.

## [2.1.10] - 2026-07-16
### Fixed
- Snag 9: confidence bar could display 50 when the real Morgan score was 0. The
  dashboard read `perf.confidence_score || 50`, and JS treats 0 as falsy, so a
  legitimate 0 was replaced by the 50 fallback. Changed to
  `(perf.confidence_score != null ? perf.confidence_score : 50)` -- 0 now shows as
  0; 50 is used only when the value is genuinely absent. In practice only GasTrader
  showed the wrong value (the only system with a 0 score, from a 5-loss streak); the
  latent bug was in all 6 dashboards. RoundTable was already correct.

## [2.1.9] - 2026-07-16
### Added
- Job 2 (Gaius Commission 001): contrarian phantom log. FTSEHybrid now writes
  logs/phantom_trades_contrarian.csv -- the opposite-direction (LONG) mirror of every
  blocked SHORT, tracking 30m/1h/2h P&L and verdicts (CORRECT = the contrarian trade
  profited). Derived from phantom_trades.csv (contrarian P&L = -direct P&L), so it
  reuses the existing price checkpoints, is always consistent, and is restart-safe.
  Enabled at startup via phantom_tracker.enable_contrarian("FTSEHybrid"); the
  machinery is inert unless enabled (configurable flag to add other systems later).
  DATA COLLECTION ONLY -- no change to live trading or Arthur's logic.
## [2.1.8] - 2026-07-16
### Added
- Job 1 (Gaius Commission 001, Priority 1): indicator snapshot at signal time in
  phantom_trades.csv. 17 columns APPENDED to the right of the existing 14-col schema
  (existing positions unchanged): ssl_daily/1hr/5min, rsi_daily/1hr/5min,
  tmo_1hr/5min, macd_1hr/5min, chande_mo_1hr/5min, money_flow_1hr/5min, morgan_score,
  session, guinevere_score. Captured from values Merlin already fetched for Arthur
  (no new data fetch) via phantom_tracker.build_snapshot() -> record_decision(indicators=).
  The snapshot build is wrapped in its own try/except so a failure can never stop a
  phantom row being written. phantom_tracker now migrates an older 14-col file in place
  on first use (old rows keep positions; new columns blank). Chronicle & Gaius read by
  column name and are unaffected. (guinevere_score currently blank pending a safe cached
  source -- column reserved.)

# FTSEHybrid AI Changelog

## [2.1.7] - 2026-07-14
### Fixed
- Morgan confidence (perf.confidence_score) now included in the lightweight always-running
  dashboard push (_push_dashboard_live), so /api/state exposes it in ALL market states --
  including after the 16:30 UTC close and out of hours. Previously perf was only pushed on
  full candle ticks (skipped when the market is closed), so RoundTable / Gaius / Chronicle
  showed null confidence out of hours. Matches CryptoTrader (performance in every push).

## [2.1.6] - 2026-07-13
### Fixed
- Bug C (desk-wide): "Locked P&L" now only shows once the trailing stop trails to break-even (genuine secured profit); until then "---" instead of an if-stopped loss figure.

## [2.1.5] - 2026-07-12
### Fixed
- Log timestamps now emitted in UTC (logging.Formatter.converter = time.gmtime; datefmt suffixed " UTC") across main, watchdog and dashboard. Previously local/BST, causing a +1h mismatch vs the UTC CSV artefacts (phantom_trades.csv etc.).
### Added
- ALBION STANDING RULE comment blocks baked into the logging setup and the log/analysis modules (phantom_tracker.py, performance_ftse.py, dashboard stay-out reader): all timestamps are UTC, never BST/local.

## [2.1.4] - 2026-07-12
### Fixed
- start_ftsehybrid.bat restored to a FTSE-only launcher. It had been overwritten with a broken 8-system `app.py` multi-launcher (traders have no app.py), which left the FTSE dashboard (:5042) down. Now launches dashboard_ftse.py + watchdog_ftse.py silently via the full pythonw path, with log rotation.

## [2.1.3] - 2026-07-11
### Added
- Silent launcher (pythonw -- no console windows); output to logs/console.log with daily rotation (7 days kept)
- Launcher now starts the dashboard + watchdog silently (was cmd windows)

## [2.1.2] - 2026-07-11
### Added
- Morgan confidence persistence: every confidence change now also appends to a CSV audit trail (logs/morgan_confidence.csv, columns timestamp/confidence/level/reason) alongside the existing JSON store; the trajectory is restored on restart so Morgan resumes where it left off instead of resetting to baseline 50
- performance_ftse.save_confidence()/load_confidence() (level = HIGH>=65 / LOW<=35 / MEDIUM); set_confidence() extended with a reason arg and now writes the CSV row after the JSON persist
- Startup restore hook in main_ftsehybrid.py after the phantom feedback poller (set_confidence(saved, reason='restore'))

## [2.1.0] - 2026-07-11
### Added
- Morgan individual phantom-verdict feedback: each judged STAY OUT verdict now nudges a persistent per-decision confidence (logs/morgan_confidence.json), separate from the aggregate STAY OUT quality nudge
- performance_ftse.apply_phantom_verdict_feedback() — NEUTRAL=no change; raw = clamp(abs(pnl_1hr)/50, 0.5, 2.0); CORRECT +raw, WRONG -raw (per-verdict cap ±2.0)
- performance_ftse.process_new_phantom_verdicts() — daemon poller (thread MorganPhantomPoller) scanning phantom_tracker.get_unprocessed_verdicts() every 300s, applying feedback and calling mark_processed(); started from main after the phantom resolve/watchdog hook
- Persistent Morgan confidence store (get_confidence/set_confidence, JSON-backed, thread-safe); the (confidence - 50) delta is folded into reported confidence exactly once (fresh system adds 0). Uses the morgan_processed field for idempotent consumption
- get_stay_out_adjustment() aggregate breaker unchanged
### Fixed
- Arthur prompt audit: all embedded win-rate/backtest figures VERIFIED against REMINDERS.txt + logs/backtest_ftse_sweep.csv (TS40pt live config) + backtest_ftse_results.txt; provenance comment added, no contaminated figures found (no reset required)

## [2.0.8] - 2026-07-11
### Added
- /api/state now merges 7 flat convenience fields derived from the existing state: lancelot_status, lancelot_fails, lancelot_fail_reasons, arthur_decision, arthur_confidence, arthur_consulted, locked_pnl (computed in compute_flat_fields(), wrapped in try/except so /api/state never 500s)
### Fixed
- Open Position panel now uses a compact two-column layout (fixed ~120px label column, value immediately after) instead of the wide space-between label-left/value-hard-right rows

## [2.0.7] - 2026-07-10
### Fixed
- Suppressed yfinance "^FTSE possibly delisted" log noise (silenced the yfinance library logger; Capital.com is the primary feed)
- Dashboard push timeout raised 2s -> 10s and the push-failing log downgraded from WARNING to DEBUG

## [2.0.6] - 2026-07-09
### Added
- phantom_tracker.start_watchdog() — continuous daemon thread that runs resolve_stale_pending() every 15 min, so stale PENDING rows resolve dynamically without a restart. Idempotent (single thread per process). Started in main after startup resolution.

## [2.0.5] - 2026-07-09
### Fixed
- Morgan quality score now excludes NEUTRAL decisions from the denominator (only CORRECT/WRONG judged)
- Morgan penalty minimum raised from 5 to 8 judged decisions before firing
### Changed
- 1-hour RSI confirmation threshold relaxed 55→52 (LONG) / 45→48 (SHORT) — backtest-validated

## [2.0.2] - 2026-07-08
### Removed
- ig_connector.py (IG Markets integration — abandoned dead code, not imported anywhere)
### Fixed
- STAY OUT QUALITY panel now ignores PENDING rows in the quality score (matches Morgan's get_summary)
### Changed
- README rewritten with Albion Trading Desk branding and team roster

## [2.0.1] - 2026-07-08
### Added
- phantom_tracker.py — STAY OUT decision recorder
- Morgan STAY OUT quality integration
- Main loop hook for STAY OUT recording

## v2.0.0 -- 7 Jul 2026
### Changed
- Rebranded to FTSEHybrid AI
- All FTSEHybrid references updated

## v1.9.0 -- 7 Jul 2026
### Added
- Countdown timer to next update
- Session Phase card
### Fixed
- Arthur FTSE range reference
- Lunch lull entry investigation
- Spread cost verification

## v1.8.0 -- 6 Jul 2026
### Added
- Shutdown button
- Lightweight dashboard push every 15s
- Arthur checklist panel
### Fixed
- Dashboard not updating outside hours

## v1.7.0 -- 6 Jul 2026
### Fixed
- Dashboard HTTP push bug
- Pre-check display bug
- Duplicate watchdog prevention

## v1.6.0 -- 5 Jul 2026
### Added
- Capital.com connector
- Royal Blue theme (#4169E1)
### Fixed
- UK100 epic (was FTSE causing 404s)
- Heartbeat (midday freeze fixed)

## v1.5.0 -- 4 Jul 2026
### Fixed
- AccountState dict access bug
- Capital £500->£1,000
- Stanley state persistence
- Stake £0.25->£0.50/point

## v1.0.0 -- 3 Jul 2026
### Added
- Initial build (15 files, 4,191 lines)
- Arthurian team
- Two-page dashboard port 5042
