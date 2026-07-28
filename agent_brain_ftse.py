"""
FTSEHybrid AI -- agent_brain_ftse.py  (Arthur)
Claude AI brain for FTSE 100 spread betting decisions.
Called only after Lancelot pre-checks have passed.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
import pandas as pd
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

_ENV_PATH = BASE_DIR / ".env"
if _ENV_PATH.exists():
    load_dotenv(dotenv_path=_ENV_PATH)
else:
    _TIDE_ENV = BASE_DIR.parent / "TideTraderAI" / ".env"
    if _TIDE_ENV.exists():
        load_dotenv(dotenv_path=_TIDE_ENV)
    else:
        load_dotenv()

log    = logging.getLogger("FTSEHybrid.Arthur")
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── System prompt ─────────────────────────────────────────────────────────────
# PROMPT AUDIT (11 Jul 2026 -- Arthur historical-figure provenance): every
# win-rate / backtest figure embedded in SYSTEM_PROMPT below is VERIFIED against
# the repo's backtest artifacts for the LIVE config (TS40pt + LunchSKIP +
# GBPUSDoff, 85 days 2026-04-15..2026-07-09). Sources:
#   REMINDERS.txt (backtest summary, lines ~72-84) and logs/backtest_ftse_sweep.csv
#   row FTSE_TS40pt_*_GBPUSDoff, corroborated by logs/backtest_ftse_results.txt.
#   - LONG 55.6% / 27 trades ............ sweep long_win_pct=55.6, long_trades=27 (exact)
#   - SHORT 59.1% / 22 trades ........... REMINDERS.txt (sweep short=59.3/27; summary figure kept)
#   - AFTERNOON 65% "better session" .... sweep afternoon_win_pct=64.7 (~65); REMINDERS "Afternoon 65% WR"
#   - MORNING_PRIME 53% ................. REMINDERS.txt "Morning session: 53% WR" (sweep 54.1)
#   - Avg trade ~44 pts before reversing  REMINDERS.txt "Average winner: +44 points"
#   - 200pt TP NEVER hit in 85 days ..... results.txt "Take profit: 0" over the 85-day period
# No contaminated / provenance-less figures found -> no neutral-baseline reset needed.

SYSTEM_PROMPT = """You are Arthur, the EXIT MANAGER for FTSEHybrid AI.

CRITICAL: You did NOT choose to enter this trade. Lancelot entered it automatically
(pre-checks + 3-timeframe SSL agreement). You are managing an OPEN POSITION only.
Your ONLY job is to decide HOLD or EXIT. You NEVER make entry decisions and you
NEVER output ENTER_LONG, ENTER_SHORT or STAY_OUT. Output exactly one of: HOLD, EXIT.

THE INSTRUMENT
FTSE 100 index via spread betting on Capital.com, GBP 2.00 per point. The trade
already has a mechanical 10pt trailing stop, a 25pt take-profit, and a Profit
Protection Ladder that locks in gains -- these run regardless of you. You add
INTELLIGENT early exit on top: get out before the stop when the trade is clearly
turning against the position.

EXIT the position now if the trade is deteriorating:
  - Daily SSL has flipped AGAINST the open position (the trend that entered it broke).
  - 5-minute momentum is deteriorating / diverging against the position (TMO rolling
    over, MACD crossing back, money flow reversing).
  - RSI is extended and REVERSING against the position (e.g. was overbought on a LONG
    and is now turning down hard).
  - The session is near its close (approaching 16:20 UTC force-close) and the trade
    is not working -- limit the risk rather than ride into the close.
  - Guinevere news has turned against the position (bearish headline on a LONG, or
    bullish on a SHORT).

HOLD the position if the trade is still working:
  - Trend intact across daily / 1h / 5m in the position's direction.
  - Momentum still flowing the trade's way.
  - The Profit Protection Ladder is already protecting gains -- let it run.
  - Plenty of session time remaining and no clear reversal signal.

AFTERNOON (13:30-16:00 UTC) -- US-open dynamic on the OPEN trade: the US market opens
13:30 UTC and US institutional flow strongly influences FTSE, so a US-driven afternoon
trend tends to PERSIST rather than reverse. Do NOT EXIT a WINNING afternoon position early
on ordinary noise -- give it more room while Daily+1h SSL still agree with the position;
the mechanical stop and ladder already protect the downside. A genuine reversal signal
(daily/1h SSL flipping against the position, momentum clearly rolling over) still means
EXIT -- this only widens tolerance for ordinary chop, not for a broken thesis.

OPEN-TIME CALIBRATION (half-hourly, Commission 017 -- 27 Jul 2026, exit posture only)
Where in the session a trade was OPENED shifts how much room it has earned:
- Opened 07:45-08:30 UTC (FTSE cash open): our WEAKEST window (0% phantom quality) -- these
  positions are statistically fragile. Be MORE willing to EXIT EARLY if momentum stalls or
  the 1h SSL wavers; do NOT extend the usual benefit-of-the-doubt to a trade opened here.
  Treat a stalling open-window position much like the WARNING posture below.
- Opened 13:30-14:30 UTC (US cash open): our STRONGEST window (100% WR so far) -- give these
  a little MORE room on ordinary noise, consistent with the afternoon US-open dynamic above.
This is exit-posture calibration only; a clear reversal signal still means EXIT in any window.

MORGAN CONFIDENCE sets how much room you give the OPEN trade (three-zone model):
  HIGH (75-100):    Give the trade more room; EXIT only on a clear reversal.
  NORMAL (50-74):   Normal exit criteria.
  WARNING (30-49):  Tighter -- EXIT on the first solid sign of deterioration.
  CRITICAL (<30):   Morgan is CRITICAL. Be MAXIMALLY defensive on this open position --
                    EXIT early, on the FIRST sign of deterioration or any loss of the
                    entry thesis; do not wait for a clear reversal. Protect capital.
Morgan is context for your EXIT posture only. On this Type-1 hybrid it does NOT gate
entry (Lancelot enters mechanically regardless of Morgan) -- it only sharpens how
aggressively you manage the EXIT. It must NOT stop you managing the trade.

RULES
  - When in doubt on a WORKING trade, HOLD -- the mechanical stop/ladder protects you.
  - When in doubt on a DETERIORATING trade, EXIT -- capital preservation first.
  - Never plan to hold past the 16:20 UTC force-close (the engine force-closes anyway).
  - You manage ONE open position; if somehow flat, return HOLD (nothing to exit).

REQUIRED OUTPUT -- valid JSON only. No markdown, no preamble.
{
  "decision": "HOLD | EXIT",
  "confidence": 0-100,
  "reasoning": "2-4 sentences: is the trade working or deteriorating, and why HOLD/EXIT",
  "warnings": ["list of concerns about the open position"],
  "checklist": {
    "trend_intact": true,
    "momentum_with_position": true,
    "ladder_protecting": true,
    "session_time_ok": true,
    "news_supportive": true
  },
  "session_assessment": "brief comment on session phase and time-to-close"
}"""


# ── Format indicators for Arthur ──────────────────────────────────────────────

def _format_indicators(
    bar_1d: Optional[pd.Series],
    bar_1h: pd.Series,
    bar_5m: pd.Series,
    current_price: float,
    session_phase: str,
    current_trade=None,
    calendar_context: Optional[str] = None,
    perf_context: Optional[str] = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _f(v, dp=2):
        if v is None or pd.isna(v):
            return "N/A"
        return f"{float(v):.{dp}f}"

    candle_colour = "GREEN" if bar_5m.get("close", 0) >= bar_5m.get("open", 0) else "RED"
    ssl_1d = "BULL" if (bar_1d is not None and bar_1d.get("ssl_bull")) else ("BEAR" if bar_1d is not None else "N/A (no daily data)")
    ssl_1h = "BULL" if bar_1h.get("ssl_bull") else "BEAR"
    ssl_5m = "BULL" if bar_5m.get("ssl_bull") else "BEAR"

    position_text = "None -- no open position"
    if current_trade is not None:
        pts_from_entry = (current_price - current_trade.entry_price) if current_trade.direction == "LONG" \
                         else (current_trade.entry_price - current_price)
        position_text = (
            f"OPEN {current_trade.direction} | "
            f"entry={current_trade.entry_price:.1f} | "
            f"current={current_price:.1f} | "
            f"pts_from_entry={pts_from_entry:+.1f} | "
            f"stop={current_trade.stop_loss:.1f} | "
            f"target={current_trade.take_profit:.1f} | "
            f"stake=£{current_trade.stake:.4f}/pt | "
            f"session={current_trade.session_phase}"
        )

    if current_trade is not None and getattr(current_trade, "ladder_step", 0):
        position_text += (
            " | PROFIT LADDER ACTIVE: floor locked at £%.2f (step %d). Position cannot "
            "close below this floor unless a gap event occurs -- factor this into your "
            "HOLD reasoning." % (getattr(current_trade, "ladder_floor_gbp", 0.0),
                                 int(getattr(current_trade, "ladder_step", 0))))

    return f"""Please analyse the current FTSE 100 market conditions.

TIME AND PRICE
  Time (UTC):       {now}
  Session Phase:    {session_phase}
  FTSE 100 Level:   {current_price:,.1f}

DAILY CHART (Trend Direction -- sets allowed direction for today)
  SSL Cloud:        {ssl_1d}
  RSI (14):         {_f(bar_1d.get('rsi') if bar_1d is not None else None, 1)}
  TMO Main:         {_f(bar_1d.get('tmo_main') if bar_1d is not None else None, 3)}
  Chande MO (20):   {_f(bar_1d.get('chande_mo') if bar_1d is not None else None, 1)}

1-HOUR CHART (Trend Confirmation)
  SSL Cloud:        {ssl_1h}
  RSI (14):         {_f(bar_1h.get('rsi'), 1)}
  MACD Histogram:   {_f(bar_1h.get('macd_histogram'), 3)}
  TMO Main:         {_f(bar_1h.get('tmo_main'), 3)}
  TMO Smooth:       {_f(bar_1h.get('tmo_smooth'), 3)}
  Chande MO (20):   {_f(bar_1h.get('chande_mo'), 1)}
  Money Flow (14):  {_f(bar_1h.get('money_flow'), 2)}

5-MINUTE CHART (Entry Timing)
  SSL Cloud:        {ssl_5m}
  RSI (14):         {_f(bar_5m.get('rsi'), 1)}
  MACD Histogram:   {_f(bar_5m.get('macd_histogram'), 3)}
  TMO Main:         {_f(bar_5m.get('tmo_main'), 3)}
  TMO Smooth:       {_f(bar_5m.get('tmo_smooth'), 3)}
  Chande MO (20):   {_f(bar_5m.get('chande_mo'), 1)}
  Money Flow (14):  {_f(bar_5m.get('money_flow'), 2)}
  Last Candle:      {candle_colour} (close={_f(bar_5m.get('close'), 1)} open={_f(bar_5m.get('open'), 1)})

CURRENT POSITION
  {position_text}

{calendar_context if calendar_context else 'UK ECONOMIC CALENDAR\n  No calendar data available.'}

{perf_context if perf_context else 'SELF PERFORMANCE AWARENESS\n  No performance data yet -- first trading session.'}

Please provide your analysis and trading decision in the required JSON format."""


# ── Main decision function ────────────────────────────────────────────────────

def get_trading_decision(
    bar_1h: pd.Series,
    bar_5m: pd.Series,
    current_price: float,
    session_phase: str,
    bar_1d: Optional[pd.Series] = None,
    current_trade=None,
    calendar_context: Optional[str] = None,
    perf_context: Optional[str] = None,
    news_context: Optional[str] = None,
) -> dict:
    """
    Send indicator data to Arthur (Claude) and receive a trading decision.
    Only call this AFTER Lancelot pre-checks have passed.
    """
    log.info("Sending indicators to Arthur...")

    user_message = _format_indicators(
        bar_1d, bar_1h, bar_5m, current_price, session_phase,
        current_trade, calendar_context, perf_context,
    )
    if news_context:
        user_message += "\n\n" + news_context

    for attempt in range(2):
        try:
            response = client.messages.create(
                model      = "claude-sonnet-4-6",
                max_tokens = 2000,
                system     = SYSTEM_PROMPT,
                messages   = [{"role": "user", "content": user_message}],
            )

            if response.stop_reason == "max_tokens":
                log.warning("Arthur hit max_tokens -- JSON may be truncated")

            raw_text = response.content[0].text.strip()
            if raw_text.startswith("```"):
                raw_text = "\n".join(
                    l for l in raw_text.split("\n")
                    if not l.strip().startswith("```")
                ).strip()

            try:
                decision = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                log.error("Arthur returned invalid JSON (attempt %d/2): %s", attempt + 1, exc)
                if attempt == 0:
                    continue
                return _safe_stay_out(f"Arthur returned invalid JSON -- staying out for safety")

            decision["timestamp"]     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            decision["tokens_used"]   = response.usage.input_tokens + response.usage.output_tokens
            decision["current_price"] = current_price
            decision["session_phase"] = session_phase

            log.info(
                "Arthur decision: %s | confidence=%s | tokens=%d",
                decision.get("decision"),
                decision.get("confidence"),
                decision.get("tokens_used", 0),
            )
            return decision

        except anthropic.APIError as exc:
            log.error("Anthropic API error: %s", exc)
            return _safe_stay_out(f"API error: {str(exc)}")
        except Exception as exc:
            log.error("Unexpected error calling Arthur: %s", exc)
            return _safe_stay_out(f"Unexpected error: {str(exc)}")

    return _safe_stay_out("Arthur failed after all attempts")


def _safe_stay_out(reason: str) -> dict:
    return {
        "decision":            "HOLD",
        "confidence":          0,
        "session_bias":        "UNCLEAR",
        "reasoning":           reason,
        "warnings":            [reason],
        "checklist":           {},
        "calendar_assessment": "",
        "session_assessment":  "",
        "timestamp":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "tokens_used":         0,
    }


def format_decision_for_display(decision: dict) -> str:
    """Format Arthur's decision for terminal display."""
    d         = decision.get("decision", "UNKNOWN")
    conf      = decision.get("confidence", "--")
    bias      = decision.get("session_bias", "--")
    reasoning = decision.get("reasoning", "No reasoning")
    warnings  = decision.get("warnings", [])
    tokens    = decision.get("tokens_used", 0)
    ts        = decision.get("timestamp", "")
    lines = [
        "=" * 60,
        "  FTSEHybrid AI -- Arthur's Decision",
        f"  {ts}",
        "=" * 60,
        f"  Decision:        {d}",
        f"  Confidence:      {conf}/100",
        f"  Session Bias:    {bias}",
        f"  FTSE Level:      {decision.get('current_price', '--'):,.1f}",
        f"  Session Phase:   {decision.get('session_phase', '--')}",
        "",
        "  Reasoning:",
        f"  {reasoning}",
        "",
    ]
    if warnings:
        lines.append("  Warnings:")
        for w in warnings:
            lines.append(f"    - {w}")
        lines.append("")
    cal = decision.get("calendar_assessment")
    if cal:
        lines.append(f"  Calendar: {cal}")
    ses = decision.get("session_assessment")
    if ses:
        lines.append(f"  Session:  {ses}")
    cl = decision.get("checklist", {})
    if cl:
        lines.append("  Checklist:")
        for k, v in cl.items():
            icon = "PASS" if v else "FAIL"
            lines.append(f"    [{icon}] {k.replace('_', ' ').title()}")
    lines.append(f"  Tokens used: {tokens}")
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Arthur self-test -- calling Claude with bullish FTSE setup...")
    bar_1d = pd.Series({
        "ssl_bull": True, "rsi": 58.0, "tmo_main": 1.5, "chande_mo": 25.0,
    })
    bar_1h = pd.Series({
        "ssl_bull": True, "rsi": 62.0, "macd_histogram": 8.5,
        "tmo_main": 2.1, "tmo_smooth": 1.5, "chande_mo": 45.0, "money_flow": 150.0,
    })
    bar_5m = pd.Series({
        "ssl_bull": True, "rsi": 58.0, "macd_histogram": 2.5,
        "tmo_main": 0.8, "tmo_smooth": 0.5, "chande_mo": 30.0, "money_flow": 80.0,
        "open": 8240.0, "close": 8250.0,
    })
    decision = get_trading_decision(
        bar_1h=bar_1h, bar_5m=bar_5m,
        current_price=8250.0, session_phase="MORNING_PRIME", bar_1d=bar_1d,
    )
    print(format_decision_for_display(decision))
    log.info("Arthur self-test complete.")
