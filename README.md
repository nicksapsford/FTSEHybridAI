# FTSEHybrid A.I.

Part of the **Albion Trading Desk** — the third parallel FTSE system, testing the
**hybrid hypothesis**: *Lancelot enters, Arthur manages the exit.*

- **Port:** 5042 · **Instrument:** FTSE 100 (Capital.com) · **Balance:** £1,000 · Paper only
- **Template:** FTSETrader v2.2.2 · **Direction:** BIDIRECTIONAL · **Session:** 07:45–16:30 UTC

Runs beside FTSETrader (5002, Arthur gates entry + exit) and FTSEBenchmark (5022,
Lancelot entry + mechanical exit). The three-way test:

    Hybrid P&L − Benchmark P&L = Arthur's pure EXIT value

(both share Lancelot entry — the one measurement no phantom analysis can give;
see Gaius Commission 005, 22 Jul 2026).

## Architecture
**ENTRY — Lancelot only (no Arthur):** a trade fires immediately when (1) all
`pre_checks_ftse` pass, (2) Daily + 1h + 5m SSL all agree a direction, and (3) no
HARD_BLOCK calendar event is within 60 min. Fully bidirectional — SHORTs enter on
the same terms as LONGs (the Morgan SHORT gate was removed 24 Jul 2026). No Arthur,
no RSI/confidence entry gate, and (Type-1) no Morgan hard-block on entry — Lancelot
enters regardless of Morgan; Morgan only sharpens Arthur's exit posture.

**EXIT — Arthur only:** once in a trade Arthur is consulted every 5 min and outputs
**HOLD** or **EXIT** only (never an entry decision). He exits early on a daily-SSL
flip, deteriorating 5m momentum, extended/reversing RSI, news turning against the
position, or the approaching close. Morgan confidence sets his exit posture under the
three-zone model (NORMAL ≥50 = normal room, WARNING 30-49 = tighter, CRITICAL <30 =
exit early/maximally defensive). The mechanical 10pt stop / 25pt target / Profit
Protection Ladder run regardless.

## Parameters
Stop 10pt · Target 25pt · Spread 3pt · Stake £2/pt · fully bidirectional (no SHORT gate) ·
three-zone Morgan (exit posture only) · Force close 16:20 UTC. **No phantom logging**
(this system never STAY-OUTs on entry).

## Running
```
python dashboard_ftse.py      # port 5042
python watchdog_ftse.py       # supervises main_ftsehybrid.py
```
Or the **Start FTSEHybrid** desktop shortcut. Dashboard pages: Dashboard | P&L →.

All times UTC.
