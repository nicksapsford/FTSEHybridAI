"""
FTSEHybrid AI -- calendar_ftse.py  (Guinevere)
UK economic calendar with hard blocks around BoE decisions.
Hard block: 30 min either side of BoE rate announcements.
Soft context: CPI, GDP, employment, NFP, Fed decisions passed to Arthur.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("FTSEHybrid.Guinevere")

HARD_BLOCK_MINUTES = 30

# ── Bank of England rate decisions ────────────────────────────────────────────
# All at 12:00 UK time. Parsed as UTC (GMT in winter, BST in summer).
# NOTE: BoE announces at 12:00 UK = 11:00 UTC (summer BST) or 12:00 UTC (winter GMT).
# We store as UK local hour=12 and convert at runtime.

BOE_DATES_2026 = [
    "2026-02-06", "2026-03-20", "2026-05-08", "2026-06-19",
    "2026-08-06", "2026-09-17", "2026-11-05", "2026-12-18",
]
BOE_DATES_2027 = [
    "2027-02-05", "2027-03-19", "2027-05-06", "2027-06-17",
    "2027-08-05", "2027-09-16", "2027-11-04", "2027-12-16",
]

FED_DATES_2026 = [
    "2026-01-29", "2026-03-19", "2026-05-07", "2026-06-18",
    "2026-07-30", "2026-09-17", "2026-11-05", "2026-12-10",
]


def _uk_noon_to_utc(date_str: str) -> datetime:
    """Convert a date string to UTC datetime for 12:00 UK time (BST/GMT aware)."""
    try:
        import pytz
        uk_tz = pytz.timezone("Europe/London")
        naive = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12, minute=0)
        uk_dt = uk_tz.localize(naive)
        return uk_dt.astimezone(timezone.utc)
    except ImportError:
        # Approximate: use 12:00 UTC (1h off in summer but acceptable)
        base = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
        return base


def _us_1330_uk_to_utc(date_str: str) -> datetime:
    """13:30 UK time to UTC (NFP, US CPI etc)."""
    try:
        import pytz
        uk_tz = pytz.timezone("Europe/London")
        naive = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=13, minute=30)
        uk_dt = uk_tz.localize(naive)
        return uk_dt.astimezone(timezone.utc)
    except ImportError:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(hour=13, minute=30, tzinfo=timezone.utc)


def _uk_0700_to_utc(date_str: str) -> datetime:
    """07:00 UK time to UTC (CPI, GDP, employment)."""
    try:
        import pytz
        uk_tz = pytz.timezone("Europe/London")
        naive = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=7, minute=0)
        uk_dt = uk_tz.localize(naive)
        return uk_dt.astimezone(timezone.utc)
    except ImportError:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(hour=7, tzinfo=timezone.utc)


def _build_boe_events(year_dates: list) -> list:
    events = []
    for d in year_dates:
        try:
            events.append({
                "name":      "Bank of England Rate Decision",
                "datetime":  _uk_noon_to_utc(d),
                "impact":    "HARD_BLOCK",
                "source":    "BoE",
                "date_str":  d,
            })
        except Exception:
            pass
    return events


def _get_all_hard_events() -> list:
    return _build_boe_events(BOE_DATES_2026) + _build_boe_events(BOE_DATES_2027)


def _get_fed_events() -> list:
    events = []
    for d in FED_DATES_2026:
        try:
            events.append({
                "name":      "Federal Reserve Decision",
                "datetime":  _us_1330_uk_to_utc(d),
                "impact":    "SOFT",
                "source":    "Fed",
                "date_str":  d,
            })
        except Exception:
            pass
    return events


def _nfp_dates_2026() -> list:
    """First Friday of each month in 2026."""
    dates = []
    for month in range(1, 13):
        d = datetime(2026, month, 1)
        while d.weekday() != 4:
            d += timedelta(days=1)
        dates.append(d.strftime("%Y-%m-%d"))
    return dates


def _get_nfp_events() -> list:
    events = []
    for d in _nfp_dates_2026():
        events.append({
            "name":      "US Non-Farm Payrolls",
            "datetime":  _us_1330_uk_to_utc(d),
            "impact":    "SOFT",
            "source":    "US",
            "date_str":  d,
        })
    return events


# ── Public API ────────────────────────────────────────────────────────────────

def check_calendar(now_utc: Optional[datetime] = None) -> dict:
    """
    Main calendar check. Returns:
      hard_block      : bool -- True means DO NOT TRADE
      block_reason    : str  -- why if hard_block
      upcoming_events : list -- events in next 24h
      calendar_summary: str  -- plain English for Arthur
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    hard_events = _get_all_hard_events()
    soft_events = _get_fed_events() + _get_nfp_events()

    # Check hard block window
    hard_block   = False
    block_reason = ""
    for ev in hard_events:
        ev_time    = ev["datetime"]
        delta_secs = (ev_time - now_utc).total_seconds()
        window     = HARD_BLOCK_MINUTES * 60
        if -window <= delta_secs <= window:
            hard_block   = True
            if delta_secs >= 0:
                mins = int(delta_secs / 60)
                block_reason = (
                    f"{ev['name']} in {mins} minutes "
                    f"(12:00 UK, {ev['date_str']}) -- "
                    f"hard block {HARD_BLOCK_MINUTES} min either side"
                )
            else:
                mins = int(-delta_secs / 60)
                block_reason = (
                    f"{ev['name']} announced {mins} minutes ago -- "
                    f"volatility window active, {HARD_BLOCK_MINUTES - mins} min remaining"
                )
            log.warning("GUINEVERE HARD BLOCK: %s", block_reason)
            break

    # Upcoming events in next 24 hours
    upcoming = []
    window_24h = timedelta(hours=24)
    for ev in hard_events + soft_events:
        delta = ev["datetime"] - now_utc
        if timedelta(0) <= delta <= window_24h:
            mins_away = int(delta.total_seconds() / 60)
            upcoming.append({
                "name":      ev["name"],
                "time_utc":  ev["datetime"].strftime("%H:%M UTC"),
                "date":      ev["date_str"],
                "impact":    ev["impact"],
                "mins_away": mins_away,
            })
    upcoming.sort(key=lambda x: x["mins_away"])

    # Next BoE decision
    future_boe = sorted(
        [ev for ev in hard_events if ev["datetime"] > now_utc],
        key=lambda x: x["datetime"],
    )
    next_boe = future_boe[0] if future_boe else None
    next_boe_str = ""
    if next_boe:
        delta   = next_boe["datetime"] - now_utc
        days    = delta.days
        next_boe_str = (
            f"Next BoE: {next_boe['date_str']} at 12:00 UK "
            f"({days} days away)"
        )

    # Calendar summary for Arthur
    if hard_block:
        summary = f"HARD BLOCK ACTIVE: {block_reason}"
    elif upcoming:
        ev_list = "; ".join(
            f"{u['name']} in {u['mins_away']} min" for u in upcoming[:3]
        )
        summary = f"Upcoming: {ev_list}. {next_boe_str}"
    else:
        summary = f"Calendar clear -- no events in next 24h. {next_boe_str}"

    return {
        "hard_block":       hard_block,
        "block_reason":     block_reason,
        "upcoming_events":  upcoming,
        "calendar_summary": summary,
        "next_boe":         next_boe_str,
    }


def is_hard_blocked(now_utc: Optional[datetime] = None) -> tuple:
    """
    Quick check. Returns (hard_block: bool, reason: str, event_name: str, mins_remaining: int).
    """
    result = check_calendar(now_utc)
    if result["hard_block"]:
        return True, result["block_reason"], "BoE Decision", HARD_BLOCK_MINUTES
    return False, "", "", 0


def get_calendar_context(now_utc: Optional[datetime] = None) -> str:
    """Return formatted calendar context string for Arthur."""
    cal = check_calendar(now_utc)
    lines = [
        "UK ECONOMIC CALENDAR",
        f"  Status: {'HARD BLOCK ACTIVE' if cal['hard_block'] else 'Clear'}",
    ]
    if cal["block_reason"]:
        lines.append(f"  Block reason: {cal['block_reason']}")
    if cal["upcoming_events"]:
        lines.append("  Upcoming (next 24h):")
        for ev in cal["upcoming_events"][:5]:
            impact_str = "[HARD BLOCK]" if ev["impact"] == "HARD_BLOCK" else "[soft]"
            lines.append(
                f"    {ev['name']} -- {ev['time_utc']} "
                f"(in {ev['mins_away']} min) {impact_str}"
            )
    if cal["next_boe"]:
        lines.append(f"  {cal['next_boe']}")
    lines.append(
        "  NOTE: US market opens 14:30 UK -- watch for FTSE volatility."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log.info("Guinevere self-test")
    now = datetime.now(timezone.utc)
    log.info("Current UTC: %s", now.strftime("%Y-%m-%d %H:%M"))
    cal = check_calendar(now)
    log.info("Hard block: %s", cal["hard_block"])
    log.info("Summary: %s", cal["calendar_summary"])
    if cal["upcoming_events"]:
        log.info("Upcoming events:")
        for ev in cal["upcoming_events"]:
            log.info("  %s in %d min", ev["name"], ev["mins_away"])
    log.info("Guinevere self-test complete.")
