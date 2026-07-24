"""
FTSEHybrid AI -- main_ftsehybrid.py
FTSE 100 spread betting main loop.
Mon-Fri 08:00-16:30 UK only. Force close at 16:20. No overnight positions.

PAPER_TRADING_MODE = True until demo account is verified.
"""

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytz
from dotenv import load_dotenv

# ── Config ────────────────────────────────────────────────────────────────────

PAPER_TRADING_MODE = True
_VER_FILE          = Path(__file__).resolve().parent / "VERSION"
VERSION            = _VER_FILE.read_text().strip() if _VER_FILE.exists() else "1.0.0"
CANDLE_INTERVAL    = 300      # 5-minute candle loop (seconds)
POSITION_INTERVAL  = 30       # position monitoring (seconds)
HEARTBEAT_INTERVAL = 240      # emit a liveness log at least this often, even when idle
                              # (LUNCH_LULL / PRE_OPEN produce no other output; without
                              #  this the watchdog reads the silence as a freeze and kills us)
DASHBOARD_INTERVAL = 15       # push live top-line state to the dashboard this often,
                              # in every session phase (not just the 5-min candle ticks)
BASE_DIR           = Path(__file__).resolve().parent
LOG_DIR            = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SHUTDOWN_FLAG      = LOG_DIR / "shutdown.flag"
LIFT_FLAG          = LOG_DIR / "confidence_lift.json"   # manual Morgan confidence lift (live)

# ── Env / logging setup ───────────────────────────────────────────────────────

_ENV_PATH = BASE_DIR / ".env"
# Capital.com / Anthropic credentials: prefer this system's own .env, then the
# template original's .env, then a known-good sibling (all Albion systems share the
# one Capital.com demo account). Fixes the yfinance fallback when a freshly-cloned
# hybrid has no own .env -- TideTraderAI/.env carries only Kraken keys, no CAPITALCOM_.
_ENV_CANDIDATES = [
    _ENV_PATH,
    BASE_DIR.parent / "AlbionTraderAI" / ".env",
    BASE_DIR.parent / "USTraderAI" / ".env",
    BASE_DIR.parent / "GoldTraderAI" / ".env",
]
for _cand in _ENV_CANDIDATES:
    if _cand.exists():
        load_dotenv(dotenv_path=_cand)
        break
else:
    load_dotenv()

# ─── ALBION STANDING RULE: ALL LOG TIMESTAMPS ARE UTC ────────────────────────
# Force Python's logging to emit %(asctime)s in UTC, not BST/local. Without this
# line, logging defaults to local time and every log line is +1h vs the UTC CSV
# artefacts (phantom_trades.csv etc.) — the exact BST/UTC mismatch that caused a
# misread on 11 Jul 2026. Never interpret an Albion log timestamp as local time;
# confirm UTC before analysing. (Baked in per Nick's directive, 12 Jul 2026.)
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-28s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S UTC",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "ftsehybrid.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("FTSEHybrid.Main")

# ── Internal imports ──────────────────────────────────────────────────────────

from agent_brain_ftse   import get_trading_decision, format_decision_for_display
from calendar_ftse      import check_calendar, is_hard_blocked, get_calendar_context
from data_feed_ftse     import FTSEDataFeed, get_session_phase, is_market_open, minutes_until_next_open
from capitalcom_connector import CapitalComConnector, FTSE_EPIC
from notifier_ftse      import (
    notify_system_startup, notify_system_shutdown,
    notify_trade_opened, notify_trade_closed_win, notify_trade_closed_loss,
    notify_kill_switch_triggered, notify_kill_switch_reset,
    notify_calendar_block, notify_daily_summary, notify_system_error,
)
from paper_trader_ftse  import PaperTraderFTSE
import performance_ftse
import guinevere_news
from performance_ftse   import (
    get_performance_context, get_perf_dashboard_dict, invalidate_cache,
    generate_milestone_review,
)
from pre_checks_ftse    import run_all_pre_checks, run_individual_pre_checks

UK_TZ = pytz.timezone("Europe/London")

# ── Graceful shutdown ─────────────────────────────────────────────────────────

_SHUTDOWN = False

def _handle_signal(sig, frame):
    global _SHUTDOWN
    log.info("Shutdown signal received (%s)", sig)
    _SHUTDOWN = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── Account state ─────────────────────────────────────────────────────────────

class AccountState:
    """Holds live trading account state passed to pre-checks."""

    def __init__(self, capital: float) -> None:
        self.capital_gbp        = capital
        self.daily_pnl_gbp      = 0.0
        self.consecutive_losses = 0
        self.last_loss_time     = None
        self.kill_switch_active = False
        self.kill_switch_tier   = 0
        self.kill_switch_until  = None   # ISO timestamp when kill expires
        self.kill_switch_reason = ""
        self.kill_history       = []     # ISO timestamps of triggers in last 48h

    def record_trade(self, pnl_gbp: float) -> None:
        self.daily_pnl_gbp += pnl_gbp
        self.capital_gbp = round(self.capital_gbp + pnl_gbp, 2)
        if pnl_gbp < 0:
            self.consecutive_losses += 1
            self.last_loss_time = datetime.now(timezone.utc)
        else:
            self.consecutive_losses = 0

    def reset_daily(self) -> None:
        self.daily_pnl_gbp = 0.0


# ── Dashboard push (best-effort) ──────────────────────────────────────────────

DASHBOARD_URL = "http://localhost:5042/api/update"

_last_dashboard_dict: dict = {}
_dash_first_ok:  bool  = False   # log the first successful push at INFO for confirmation
_dash_fail_count: int  = 0
_dash_last_warn: float = 0.0


def _dashboard_push_ok(kind: str, phase: str, price: float, status: str, http) -> None:
    """Confirm a push landed. First one logs at INFO; the rest at DEBUG."""
    global _dash_first_ok
    if not _dash_first_ok:
        _dash_first_ok = True
        log.info("Dashboard connected -- first %s push OK | phase=%s ftse=%.1f status=%s HTTP %s",
                 kind, phase, price, status, http)
    else:
        log.debug("Dashboard %s push | phase=%s ftse=%.1f status=%s HTTP %s",
                  kind, phase, price, status, http)


def _dashboard_push_warn(exc: Exception) -> None:
    """Surface push failures (throttled) instead of swallowing them silently."""
    global _dash_fail_count, _dash_last_warn
    _dash_fail_count += 1
    now = time.monotonic()
    if now - _dash_last_warn > 60:
        log.debug("Dashboard push failing (%d so far): %s -- is dashboard_ftse.py running on :5042?",
                  _dash_fail_count, exc)
        _dash_last_warn = now


def _serialise_trade(trade):
    if trade is None:
        return None
    if hasattr(trade, "__dict__"):
        return {k: str(v) for k, v in trade.__dict__.items()}
    return trade


def _safe_float(v):
    try:
        f = float(v)
        return None if f != f else f  # NaN check (NaN != NaN)
    except (TypeError, ValueError):
        return None


def _indicator_snapshot(bar) -> dict:
    if bar is None:
        return {}
    return {
        "ssl_bull":   bool(bar.get("ssl_bull", False)),
        "rsi":        _safe_float(bar.get("rsi")),
        "macd":       _safe_float(bar.get("macd")),
        "tmo_main":   _safe_float(bar.get("tmo_main")),
        "chande_mo":  _safe_float(bar.get("chande_mo")),
        "money_flow": _safe_float(bar.get("money_flow")),
    }


def _push_dashboard(
    stanley:    PaperTraderFTSE,
    account:    AccountState,
    decision:   dict = None,
    pre_checks: dict = None,
    phase:      str  = "",
    ftse_level: float = 0.0,
    calendar_summary: str = "",
    connector_status: str = "yahoo",
    panel_mode: str = "pre_checks",
    trend_1d:   str = "NEUTRAL",
    trend_1h:   str = "NEUTRAL",
    signal_5m:  str = "NEUTRAL",
    indicators_1d: dict = None,
    indicators_1h: dict = None,
    indicators_5m: dict = None,
) -> None:
    """Push latest state to dashboard via HTTP POST (separate process)."""
    try:
        import requests
        perf = get_perf_dashboard_dict()
        payload = {
            "mode":          "PAPER" if PAPER_TRADING_MODE else "LIVE",
            "version":       VERSION,
            "phase":         phase,
            "ftse_level":    ftse_level,
            "connector_status": connector_status,
            "capital":       stanley.capital_gbp,
            "daily_pnl":     account.daily_pnl_gbp,
            "total_trades":  stanley.total_trades,
            "win_rate":      stanley.win_rate,
            "in_trade":      stanley.in_trade,
            "current_trade": _serialise_trade(stanley.current_trade),
            "decision":      decision,
            "panel_mode":    panel_mode,
            "checklist":     (decision or {}).get("checklist", {}),
            "pre_checks":    pre_checks,
            "trend_1d":      trend_1d,
            "trend_1h":      trend_1h,
            "signal_5m":     signal_5m,
            "indicators_1d": indicators_1d or {},
            "indicators_1h": indicators_1h or {},
            "indicators_5m": indicators_5m or {},
            "perf":          perf,
            "calendar":      calendar_summary,
            "kill_switch":   account.kill_switch_active,
            "kill_tier":     account.kill_switch_tier,
            "updated_at":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        resp = requests.post(
            DASHBOARD_URL,
            data=json.dumps(payload, default=str),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        _dashboard_push_ok("full", phase, ftse_level, connector_status, resp.status_code)
    except Exception as exc:
        _dashboard_push_warn(exc)


def _push_dashboard_live(
    stanley: PaperTraderFTSE,
    account: AccountState,
    ig:      CapitalComConnector,
    feed:    FTSEDataFeed,
    now_utc: datetime,
) -> None:
    """
    Lightweight, frequent push of the always-known top-line fields (live price,
    phase, connector status, capital, P&L, open position). Runs every loop tick
    in ALL session phases -- including PRE_OPEN / LUNCH_LULL / CLOSING and the
    gaps between 5-minute candle ticks -- so the dashboard never sits on its
    0.0 / -- defaults.

    Deliberately omits decision / pre_checks / indicators so that this frequent
    merge does NOT overwrite the richer panel data from the last candle tick.
    """
    try:
        import requests
        phase = get_session_phase(now_utc)
        price = _get_price(ig, feed)
        connector_status = "capitalcom" if (ig is not None and ig.connected) else "yahoo"
        payload = {
            "mode":             "PAPER" if PAPER_TRADING_MODE else "LIVE",
            "version":          VERSION,
            "phase":            phase,
            "ftse_level":       price,
            "connector_status": connector_status,
            "capital":          stanley.capital_gbp,
            "daily_pnl":        account.daily_pnl_gbp,
            "total_trades":     stanley.total_trades,
            "win_rate":         stanley.win_rate,
            "in_trade":         stanley.in_trade,
            "current_trade":    _serialise_trade(stanley.current_trade),
            "kill_switch":      account.kill_switch_active,
            "kill_tier":        account.kill_switch_tier,
            "perf":             get_perf_dashboard_dict(),   # keep confidence exposed in ALL market states
            "updated_at":       now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
        resp = requests.post(
            DASHBOARD_URL,
            data=json.dumps(payload, default=str),
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        _dashboard_push_ok("live", phase, price, connector_status, resp.status_code)
    except Exception as exc:
        _dashboard_push_warn(exc)


# ── Core candle tick ──────────────────────────────────────────────────────────

def _ssl(bar):
    """LONG/SHORT/None from a bar's ssl_bull flag (None or NaN -> None)."""
    if bar is None:
        return None
    v = bar.get("ssl_bull")
    if v is None or (isinstance(v, float) and v != v):
        return None
    return "LONG" if bool(v) else "SHORT"


def ssl_agreement(bar_1d, bar_1h, bar_5m):
    """Daily + 1h + 5m SSL must ALL agree -> LONG/SHORT, else None. The Lancelot
    3-timeframe entry signal for FTSEHybrid (same as the Benchmark desk)."""
    d, h, m = _ssl(bar_1d), _ssl(bar_1h), _ssl(bar_5m)
    if d is not None and d == h == m:
        return d
    return None


def _major_event_within(now_utc, minutes):
    """True if a HARD_BLOCK calendar event is within `minutes` ahead (entry look-ahead)."""
    try:
        for ev in check_calendar(now_utc).get("upcoming_events", []):
            if ev.get("impact") == "HARD_BLOCK" and 0 <= ev.get("mins_away", 1e9) <= minutes:
                return True
    except Exception:
        pass
    return False


def run_candle_tick(
    feed:    FTSEDataFeed,
    stanley: PaperTraderFTSE,
    account: AccountState,
    ig:      CapitalComConnector,
) -> None:
    """
    Called once every 5 minutes during a trading session.
    Gathers indicators, runs pre-checks, calls Arthur, acts on decision.
    """
    now_utc      = datetime.now(timezone.utc)
    phase        = get_session_phase(now_utc)
    ftse_price   = _get_price(ig, feed)
    connector_status = "capitalcom" if (ig is not None and ig.connected) else "yahoo"

    log.info("--- CANDLE TICK | %s | phase=%s | FTSE=%.1f ---",
             now_utc.strftime("%H:%M:%S UTC"), phase, ftse_price)

    # Calendar check
    hard_blocked, block_reason, event_name, mins_remain = is_hard_blocked(now_utc)
    cal_context = get_calendar_context(now_utc)
    cal_summary = check_calendar(now_utc).get("calendar_summary", "")

    if hard_blocked:
        log.warning("CALENDAR HARD BLOCK: %s (%d min remaining)", block_reason, mins_remain)
        if not stanley.in_trade:
            _push_dashboard(stanley, account, phase=phase, ftse_level=ftse_price,
                            calendar_summary=cal_summary, connector_status=connector_status)
            return

    # Refresh data
    try:
        feed.refresh()
    except Exception as exc:
        log.error("Data refresh failed: %s", exc)
        return

    bar_1d = feed.latest_bar("1d")
    bar_1h = feed.latest_bar("1h")
    bar_5m = feed.latest_bar("5m")

    if bar_1h is None or bar_5m is None:
        log.warning("Insufficient indicator data -- skipping tick")
        return

    # Performance context
    perf_context = get_performance_context()

    # Determine proposed direction from composite signals
    sig_1h = feed.composite_signal("1h")
    sig_5m = feed.composite_signal("5m")
    trend_1d = "LONG" if bar_1d.get("ssl_bull") else "SHORT"
    # Bidirectional (System 1 Review 17 Jul): the DAILY SSL sets the session
    # direction -- BULL -> LONG session, BEAR -> SHORT session (Morgan-gated below).
    # NEUTRAL only when the daily SSL is unavailable (None/NaN).
    _ssl_1d = bar_1d.get("ssl_bull")
    proposed_direction = "NEUTRAL" if (_ssl_1d is None or _ssl_1d != _ssl_1d) else trend_1d

    ind_1d = _indicator_snapshot(bar_1d)
    ind_1h = _indicator_snapshot(bar_1h)
    ind_5m = _indicator_snapshot(bar_5m)

    # ── HYBRID DECISION ───────────────────────────────────────────────────
    # FTSEHybrid: Arthur manages the EXIT only. Entry is LANCELOT-only --
    # pre-checks + 3-timeframe SSL agreement + a 60-min calendar look-ahead.
    # Arthur is NEVER consulted on entry (no RSI/confidence entry gate beyond the
    # SHORT Morgan gate). No phantom logging in this system.
    perf_context = get_performance_context()

    if stanley.in_trade:
        # EXIT MANAGEMENT -- Arthur decides HOLD or EXIT only.
        try:
            news_ctx = guinevere_news.format_news_context()
        except Exception:
            news_ctx = None
        decision = get_trading_decision(
            bar_1h=bar_1h, bar_5m=bar_5m, current_price=ftse_price,
            session_phase=phase, bar_1d=bar_1d, current_trade=stanley.current_trade,
            calendar_context=cal_context, perf_context=perf_context, news_context=news_ctx,
        )
        action = decision.get("decision", "HOLD")
        if action != "EXIT":                 # Arthur only manages exits in this system
            action = "HOLD"
            decision["decision"] = "HOLD"
        log.info(format_decision_for_display(decision))
        _push_dashboard(stanley, account, decision=decision,
                        phase=phase, ftse_level=ftse_price, calendar_summary=cal_summary,
                        connector_status=connector_status, panel_mode="claude",
                        trend_1d=trend_1d, trend_1h=sig_1h, signal_5m=sig_5m,
                        indicators_1d=ind_1d, indicators_1h=ind_1h, indicators_5m=ind_5m)
        if action == "EXIT":
            _close_trade(stanley, account, ig, ftse_price, "ARTHUR_EXIT")
        else:
            log.info("Arthur says HOLD -- maintaining position")
        return

    # ── FLAT: LANCELOT ENTRY (no Arthur) ─────────────────────────────────
    checks = run_all_pre_checks(
        bar_1h=bar_1h, bar_5m=bar_5m, account=account,
        current_trade=None, bar_1d=bar_1d, proposed_direction=proposed_direction,
    )
    individual_checks = run_individual_pre_checks(
        bar_1h=bar_1h, bar_5m=bar_5m, account=account,
        current_trade=None, bar_1d=bar_1d, proposed_direction=proposed_direction,
    )

    def _push_flat(panel="pre_checks", decision=None):
        _push_dashboard(stanley, account, decision=decision, pre_checks=individual_checks,
                        phase=phase, ftse_level=ftse_price, calendar_summary=cal_summary,
                        connector_status=connector_status, panel_mode=panel,
                        trend_1d=trend_1d, trend_1h=sig_1h, signal_5m=sig_5m,
                        indicators_1d=ind_1d, indicators_1h=ind_1h, indicators_5m=ind_5m)

    # NOTE (Job 2, 24 Jul 2026): a Type-1 hybrid's ENTRY is Lancelot-only and must fire
    # REGARDLESS of Morgan -- so there is NO Morgan hard-block on entry here (the earlier
    # three-zone hard-block was removed as architecturally wrong for Type-1). Morgan <30
    # instead sharpens Arthur's EXIT posture (see the in_trade branch above, which passes
    # the critical-Morgan context into Arthur's exit decision). Type-2 hybrids (Gold/Oil,
    # where Arthur gates entry) KEEP their entry hard-block.

    if not checks["passed"]:
        log.info("Pre-checks FAILED: %s", checks.get("reason"))
        _push_flat()
        if checks.get("kill_switch_triggered"):
            account.kill_switch_active = True
            tier = checks.get("kill_tier", 1)
            account.kill_switch_tier = tier
            wait_hours = {1: 6, 2: 12}.get(tier, 24)
            account.kill_switch_until = None
            notify_kill_switch_triggered(
                tier=tier, reason=checks.get("reason", ""), wait_hours=wait_hours,
                daily_pnl=account.daily_pnl_gbp, capital=stanley.capital_gbp)
        elif checks.get("kill_switch_reset"):
            account.kill_switch_active = False
            notify_kill_switch_reset(tier=account.kill_switch_tier, wait_hours=0,
                                     capital=stanley.capital_gbp)
            account.kill_switch_tier = 0
        return

    # 3-timeframe SSL agreement = the Lancelot entry signal.
    ssl_dir = ssl_agreement(bar_1d, bar_1h, bar_5m)
    if ssl_dir not in ("LONG", "SHORT"):
        log.info("No 3-TF SSL agreement -- no entry")
        _push_flat()
        return

    # Calendar look-ahead -- HARD BLOCK a new entry if a major event is within 60 min.
    if _major_event_within(now_utc, 60):
        log.info("Entry blocked -- major calendar event within 60 min")
        _push_flat()
        return

    # ENTER immediately -- Lancelot only, no Arthur.
    log.info("LANCELOT ENTRY -- %s (pre-checks passed + Daily/1h/5m SSL agreed)", ssl_dir)
    _open_trade(stanley, account, ig, ssl_dir, ftse_price, phase)
    _push_flat(panel="claude", decision={
        "decision": "ENTER_" + ssl_dir, "confidence": None,
        "reasoning": "Lancelot entry: pre-checks passed and Daily/1h/5m SSL all agreed %s. No Arthur on entry." % ssl_dir,
    })

    # Milestone review every 50 trades
    if stanley.total_trades > 0 and stanley.total_trades % 50 == 0:
        from paper_trader_ftse import TRADES_LOG
        milestone = stanley.total_trades // 50
        generate_milestone_review(TRADES_LOG, milestone)


# ── Position monitoring ───────────────────────────────────────────────────────

def monitor_open_position(
    stanley:  PaperTraderFTSE,
    account:  AccountState,
    ig:       CapitalComConnector,
    feed:     FTSEDataFeed,
) -> None:
    """
    Called every 30 seconds while in a position.
    Checks trailing stop, force close at 16:20.
    """
    if not stanley.in_trade:
        return

    now_utc    = datetime.now(timezone.utc)
    ftse_price = _get_price(ig, feed)

    # Force close at 16:20 UK time
    from strategy_ftse import should_force_close
    if should_force_close(now_utc):
        log.warning("Force close at 16:20 UK -- closing all positions")
        _close_trade(stanley, account, ig, ftse_price, "FORCE_CLOSE_1620")
        return

    # Trailing stop + take profit check
    reason = stanley.monitor_trade(ftse_price)
    if reason:
        trade = stanley.trade_history[-1] if stanley.trade_history else None
        _handle_closed_trade(account, trade)
        log.info("Position auto-closed: %s | price=%.1f", reason, ftse_price)
        invalidate_cache()


# ── Open / close helpers ──────────────────────────────────────────────────────

def _open_trade(
    stanley:  PaperTraderFTSE,
    account:  AccountState,
    ig:       CapitalComConnector,
    direction: str,
    price:    float,
    phase:    str,
) -> None:
    trade = stanley.open_trade(direction, price, phase)
    if PAPER_TRADING_MODE:
        log.info("[PAPER] OPEN %s | entry=%.1f | stop=%.1f | target=%.1f | stake=£%.4f/pt",
                 direction, price, trade.stop_loss, trade.take_profit, trade.stake)
    else:
        try:
            ig.open_position(
                epic         = FTSE_EPIC,
                direction    = direction,
                size         = trade.stake,
                stop_distance= trade.stop_pts,
            )
            log.info("[LIVE] OPEN %s via Capital.com | entry=%.1f", direction, price)
        except Exception as exc:
            log.error("Capital.com open_position failed: %s -- position tracked paper only", exc)
            notify_system_error(f"Capital.com open failed: {exc}")

    notify_trade_opened(
        direction=direction, entry_price=price,
        stop_loss=trade.stop_loss, take_profit=trade.take_profit,
        stake=trade.stake, session_phase=phase,
    )
    log.info("Trade opened: %s", trade.summary())


def _close_trade(
    stanley:  PaperTraderFTSE,
    account:  AccountState,
    ig:       CapitalComConnector,
    price:    float,
    reason:   str,
) -> None:
    trade = stanley.close_trade(price, reason)
    if trade is None:
        return
    _handle_closed_trade(account, trade)
    invalidate_cache()

    if not PAPER_TRADING_MODE:
        try:
            positions = ig.get_open_positions()
            for pos in positions:
                ig.close_position(
                    deal_id   = pos.get("dealId"),
                    direction = "SELL" if trade.direction == "LONG" else "BUY",
                    size      = trade.stake,
                )
            log.info("[LIVE] Position closed via Capital.com | reason=%s", reason)
        except Exception as exc:
            log.error("Capital.com close_position failed: %s", exc)
            notify_system_error(f"Capital.com close failed: {exc}")

    if trade.pnl_gbp >= 0:
        notify_trade_closed_win(
            direction=trade.direction, exit_price=price,
            pnl_pts=trade.pnl_pts, pnl_gbp=trade.pnl_gbp,
            capital=account.capital_gbp, reason=reason,
        )
    else:
        notify_trade_closed_loss(
            direction=trade.direction, exit_price=price,
            pnl_pts=trade.pnl_pts, pnl_gbp=trade.pnl_gbp,
            capital=account.capital_gbp, reason=reason,
        )


def _handle_closed_trade(account: AccountState, trade) -> None:
    if trade is None:
        return
    account.record_trade(trade.pnl_gbp)
    log.info("Trade result: %s%+.2f GBP | capital=£%.2f",
             "+" if trade.pnl_gbp >= 0 else "", trade.pnl_gbp, account.capital_gbp)


# ── Price getter ──────────────────────────────────────────────────────────────

def _get_price(ig: CapitalComConnector, feed: FTSEDataFeed) -> float:
    """Get current FTSE price -- Capital.com first, yfinance fallback."""
    try:
        if ig is not None and ig.connected:
            price_data = ig.get_price(FTSE_EPIC)
            return price_data.get("mid", 0.0)
    except Exception:
        pass
    try:
        df = feed.get("5m")
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    return 0.0


# ── Daily summary ─────────────────────────────────────────────────────────────

_last_summary_date: str = ""


def _maybe_send_daily_summary(stanley: PaperTraderFTSE, account: AccountState) -> None:
    global _last_summary_date
    today = datetime.now(UK_TZ).strftime("%Y-%m-%d")
    if today == _last_summary_date:
        return
    now_uk = datetime.now(UK_TZ)
    if now_uk.hour == 16 and now_uk.minute >= 30:
        notify_daily_summary(
            date_str=today,
            trades=stanley.total_trades,
            pnl_gbp=account.daily_pnl_gbp,
            capital=stanley.capital_gbp,
            win_rate=stanley.win_rate,
        )
        account.reset_daily()
        _last_summary_date = today
        log.info("Daily summary sent for %s", today)


# ── Main loop ─────────────────────────────────────────────────────────────────

def _apply_confidence_lift() -> None:
    """Apply a pending manual confidence lift (logs/confidence_lift.json) in-process
    so a Gaius/dashboard lift takes effect LIVE -- Morgan's persisted baseline is
    otherwise cached in this process until restart. Written by the dashboard
    /api/lift-confidence endpoint (or Gaius --lift); consumed here via the existing
    set_confidence() and the flag deleted. Does not change the confidence algorithm."""
    import json
    try:
        if not LIFT_FLAG.exists():
            return
        data = json.loads(LIFT_FLAG.read_text(encoding="utf-8"))
        val = max(0.0, min(100.0, float(data.get("confidence", 50.0))))
        reason = data.get("reason") or "CONFIDENCE LIFT -- manual override"
        prior = performance_ftse.get_confidence()
        performance_ftse.set_confidence(val, reason=reason)
        LIFT_FLAG.unlink(missing_ok=True)
        log.warning("Morgan CONFIDENCE LIFT applied live: %.1f -> %.1f (%s)", prior, val, reason)
    except Exception as _exc:
        log.warning("Confidence lift apply failed: %s", _exc)
        try:
            LIFT_FLAG.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> None:
    global _SHUTDOWN
    log.info("=" * 70)
    log.info("  FTSEHybrid AI v%s", VERSION)
    log.info("  FTSE 100 Spread Betting -- Capital.com")
    log.info("  Mode: %s", "PAPER TRADING" if PAPER_TRADING_MODE else "LIVE TRADING")
    log.info("=" * 70)

    # Capital.com connector -- always connect for live price data, even in
    # paper trading mode. PAPER_TRADING_MODE only controls whether trades
    # are sent to Excalibur (live) or tracked by Stanley (paper) below.
    ig = CapitalComConnector()
    try:
        ig.connect()
        ig_connected = True
        log.info("Capital.com connected")
    except Exception as exc:
        log.error("Capital.com connection failed: %s -- yfinance fallback", exc)
        ig_connected = False

    # Data feed
    feed = FTSEDataFeed(ig_connector=ig if ig_connected else None)
    try:
        feed.initialise()
    except Exception as exc:
        log.warning("Initial data load partial: %s -- will retry", exc)

    # NOTE: FTSEHybrid has NO phantom logging. Phantom tracks Arthur STAY-OUT
    # decisions on entry; this system never asks Arthur to gate entry (Lancelot
    # enters), so there is nothing to phantom-log. Morgan still learns from the
    # actual trade outcomes (exit results), just not from phantom verdicts.

    # Restore Morgan's confidence trajectory from the CSV audit trail so a
    # restart resumes where it left off instead of resetting to baseline 50.
    try:
        _saved_conf = performance_ftse.load_confidence()
        if _saved_conf is not None:
            performance_ftse.set_confidence(_saved_conf, reason='restore')
            log.info("Morgan: confidence restored from CSV -> %.1f", _saved_conf)
        else:
            log.info("Morgan: no persisted confidence found -- baseline 50")
    except Exception as _exc:
        log.warning("Morgan confidence restore failed: %s", _exc)

    # Apply any confidence lift requested while the engine was down (Step 4).
    _apply_confidence_lift()

    # Paper trader + account
    stanley = PaperTraderFTSE()
    account = AccountState(capital=stanley.capital_gbp)
    stanley.print_status()

    notify_system_startup(
        capital=stanley.capital_gbp,
        mode="PAPER" if PAPER_TRADING_MODE else "LIVE",
    )

    # Clear any stale shutdown flag left over from a previous session so we
    # don't immediately exit. During this run the flag is only ever *written*
    # by the dashboard and *consumed* (deleted) by the watchdog -- see below.
    SHUTDOWN_FLAG.unlink(missing_ok=True)

    log.info("FTSEHybrid AI is running. Ctrl+C to stop.")
    log.info("Dashboard: http://localhost:5042  (start dashboard_ftse.py separately)")

    last_candle_tick    = 0.0
    last_position_check = 0.0
    last_heartbeat      = 0.0
    last_dashboard_push = 0.0
    _force_close_done   = False

    while not _SHUTDOWN:
        try:
            now     = time.monotonic()
            now_utc = datetime.now(timezone.utc)
            now_uk  = datetime.now(UK_TZ)

            # ── Dashboard shutdown check ──────────────────────────────────────
            # NOTE: do NOT delete the flag here. We exit cleanly and leave the
            # flag on disk so the watchdog (Galahad) sees it, stops itself, and
            # does not relaunch us. The watchdog removes the flag on its way out.
            if SHUTDOWN_FLAG.exists():
                log.info("Shutdown requested via dashboard -- stopping (flag left for watchdog)")
                break

            # Apply a pending manual confidence lift live (Gaius intervention Step 4).
            _apply_confidence_lift()

            # ── Live dashboard push (all phases, every ~15s) ──────────────────
            # Keeps the dashboard's price/phase/status tiles current outside the
            # 5-minute candle ticks and outside the active-trading windows.
            if (now - last_dashboard_push) >= DASHBOARD_INTERVAL:
                _push_dashboard_live(stanley, account, ig, feed, now_utc)
                last_dashboard_push = now

            # ── Liveness heartbeat ────────────────────────────────────────────
            # Guarantees regular output during quiet phases (LUNCH_LULL, PRE_OPEN,
            # market-closed) so Galahad's freeze detector never mistakes a healthy
            # idle bot for a hang. Idle sleeps below are capped to stay under it.
            if (now - last_heartbeat) >= HEARTBEAT_INTERVAL:
                log.info("Heartbeat -- alive | %s UK | phase=%s | in_trade=%s",
                         now_uk.strftime("%H:%M"), get_session_phase(now_utc),
                         stanley.in_trade)
                last_heartbeat = now

            # Skip weekends entirely
            if now_uk.weekday() >= 5:
                log.debug("Weekend -- idle")
                _interruptible_sleep(HEARTBEAT_INTERVAL)
                continue

            # Outside market window entirely
            hour = now_uk.hour
            if hour < 8 or hour >= 17:
                mins = minutes_until_next_open()
                sleep_sec = max(60, min(mins * 60, HEARTBEAT_INTERVAL)) if mins else HEARTBEAT_INTERVAL
                log.info("Market closed (UK %s) -- next open in %s min",
                         now_uk.strftime("%H:%M"), mins if mins else "?")
                _interruptible_sleep(sleep_sec)
                _force_close_done = False
                continue

            # Force close at 16:20 UK
            if hour == 16 and now_uk.minute >= 20:
                if stanley.in_trade and not _force_close_done:
                    price = _get_price(ig, feed)
                    log.warning("16:20 force close triggered")
                    _close_trade(stanley, account, ig, price, "FORCE_CLOSE_1620")
                    _force_close_done = True
                _maybe_send_daily_summary(stanley, account)
                _interruptible_sleep(60)
                continue

            if hour == 16 and now_uk.minute >= 30:
                _maybe_send_daily_summary(stanley, account)
                _interruptible_sleep(60)
                continue

            # Position monitoring every 30 seconds
            if stanley.in_trade and (now - last_position_check) >= POSITION_INTERVAL:
                monitor_open_position(stanley, account, ig, feed)
                last_position_check = now

            # Candle tick every 5 minutes (only during trading sessions)
            if is_market_open() and (now - last_candle_tick) >= CANDLE_INTERVAL:
                run_candle_tick(feed, stanley, account, ig)
                last_candle_tick = now

            _interruptible_sleep(5)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            log.error("Main loop error: %s", exc, exc_info=True)
            notify_system_error(str(exc)[:200])
            time.sleep(30)

    # Shutdown
    log.info("")
    log.info("=" * 70)
    log.info("  FTSEHybrid AI -- Shutdown")
    log.info("=" * 70)
    if stanley.in_trade:
        log.warning("Position still open at shutdown -- closing paper record")
        price = _get_price(ig, feed)
        _close_trade(stanley, account, ig, price, "SHUTDOWN")
    stanley.print_status()
    notify_system_shutdown(stanley.capital_gbp)
    log.info("FTSEHybrid AI stopped cleanly.")


def _interruptible_sleep(seconds: float) -> None:
    """Sleep that responds to _SHUTDOWN flag."""
    end = time.monotonic() + seconds
    while not _SHUTDOWN and time.monotonic() < end:
        time.sleep(min(1, end - time.monotonic()))


if __name__ == "__main__":
    main()
