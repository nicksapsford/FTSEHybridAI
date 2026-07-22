"""
guinevere_news.py -- FTSE News Sentiment Module
Albion Trading Desk -- FTSEHybrid A.I. (System 1 Review, 17 Jul 2026)
Fetches UK / FTSE-related news from the Currents API to inform Arthur's confidence
(direction-aware +/-8, matching Gold/Oil/Gas). Bidirectional: bullish news supports
LONG sessions, bearish news supports SHORT sessions. Hard-block calendar windows are
handled separately by calendar_ftse. All times UTC. API key in .env (CURRENTS_API_KEY).
"""

import os
import csv
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# -- API configuration ------------------------------------------------------
CURRENTS_API_KEY = os.getenv('CURRENTS_API_KEY')
CURRENTS_BASE_URL = 'https://api.currentsapi.services/v1'

# -- FTSE / UK keywords (System 1 Review 17 Jul 2026; includes political keywords
# for Monday 20 Jul when a new UK PM & Chancellor take office). Hardcoded DEFAULTS
# / fallback; live values read from logs/guinevere_keywords.json (editable).
# Bullish = positive for LONG sessions; Bearish = positive for SHORT sessions.
SYSTEM_NAME = "FTSEHybrid"
BULLISH_KEYWORDS = [
    "Bank of England cut", "BOE cut", "BOE dovish",
    "UK rate cut", "interest rate cut", "UK economy growth",
    "UK GDP beat", "strong UK GDP", "GBP rising",
    "pound strengthens", "UK PMI beat", "UK consumer confidence",
    "FTSE rally", "UK earnings beat", "UK fiscal stimulus",
    "UK spending plans", "UK growth agenda", "business friendly",
    "UK investment", "UK productivity", "trade deal UK",
    "North Sea exploration", "new prime minister", "new chancellor",
    "fiscal stimulus", "growth package", "investment pledge",
    # Guinevere fix 21 Jul 2026 -- directional UK-driver coverage (BoE/MPC,
    # budget, FTSE 100, sterling) + the pro-growth angle of the Burnham cabinet
    # (defence stocks are a heavy FTSE weight).
    "Bank of England dovish", "BoE dovish", "MPC cut", "rate cut hopes",
    "defence spending", "defense spending", "spending uplift", "defence boost",
    "sterling strengthens", "sterling rally",
    "UK budget stimulus", "growth budget", "pro-growth budget",
    "FTSE 100 rally", "FTSE 100 rises", "FTSE 100 gains"
]

BEARISH_KEYWORDS = [
    "Bank of England hike", "BOE hike", "BOE hawkish",
    "UK rate rise", "UK recession", "weak UK GDP",
    "UK GDP miss", "GBP falling", "pound weakens",
    "UK PMI miss", "UK inflation surprise", "FTSE sell-off",
    "UK tax rises", "UK austerity", "UK spending cuts",
    "UK political uncertainty", "UK political risk",
    "cabinet reshuffle", "new prime minister uncertainty",
    "chancellor", "UK debt", "UK deficit",
    "tax rises", "austerity measures", "spending review",
    "political uncertainty",
    # Guinevere fix 21 Jul 2026 -- directional UK-driver coverage + the
    # uncertainty angle of a newly-formed cabinet (Burnham, 20 Jul 2026).
    "Bank of England hawkish", "BoE hawkish", "MPC hike", "rate hike fears",
    "new cabinet", "cabinet uncertainty", "government instability",
    "Burnham uncertainty", "sterling weakens", "sterling slides",
    "UK budget cuts", "emergency budget", "budget tax rises",
    "gilt sell-off", "gilt yields surge",
    "FTSE 100 sell-off", "FTSE 100 falls", "FTSE 100 slide"
]

KEYWORDS_FILE       = os.path.join(os.path.dirname(__file__), 'logs', 'guinevere_keywords.json')
KEYWORD_CHANGE_LOG  = os.path.join(os.path.dirname(__file__), 'logs', 'guinevere_keyword_changes.log')
MACRO_FILE          = os.path.join(os.path.dirname(__file__), '..', 'RoundTableAI', 'logs', 'macro_sentiment.json')
_kw_cache = {'ts': None, 'bullish': None, 'bearish': None, 'last_updated': None, 'updated_by': None}

# News older than this is ignored.
MAX_NEWS_AGE_HOURS = 4

# Cache to avoid hammering the API.
_news_cache = {
    'timestamp': None,
    'sentiment': 'NEUTRAL',
    'score': 0,
    'headlines': [],
    'reason': 'No data yet',
}
CACHE_DURATION_MINUTES = 5

# -- Sentiment CSV persistence (audit trail; same schema as Oil/Gas so
#    Merlin's Chronicle reads it with the identical parser) ----------------
SENTIMENT_LOG = os.path.join(os.path.dirname(__file__), 'logs', 'guinevere_sentiment.csv')
SENTIMENT_FIELDNAMES = ['timestamp', 'sentiment', 'score', 'headline_1', 'headline_2', 'headline_3', 'eia_window']


def save_sentiment(sentiment_data):
    """Append the latest sentiment read to logs/guinevere_sentiment.csv.
    The 'eia_window' column is kept for exact schema parity with Oil/Gas; for
    Gold it carries the soft Fed/CPI/NFP caution-window flag."""
    try:
        os.makedirs(os.path.dirname(SENTIMENT_LOG), exist_ok=True)
        h = sentiment_data.get('headlines', []) or []

        def _title(i):
            try:
                return h[i]['title'] if isinstance(h[i], dict) else str(h[i])
            except Exception:
                return ''
        row = {'timestamp': datetime.now(timezone.utc).isoformat(),
               'sentiment': sentiment_data.get('sentiment', 'NEUTRAL'),
               'score': sentiment_data.get('score', 0),
               'headline_1': _title(0), 'headline_2': _title(1), 'headline_3': _title(2),
               'eia_window': sentiment_data.get('eia_window', False)}
        file_exists = os.path.exists(SENTIMENT_LOG)
        with open(SENTIMENT_LOG, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=SENTIMENT_FIELDNAMES)
            if not file_exists:
                w.writeheader()
            w.writerow(row)
    except Exception as e:
        logger.warning("Guinevere: could not save sentiment: %s", e)


def _write_keywords_file(bullish, bearish, updated_by):
    os.makedirs(os.path.dirname(KEYWORDS_FILE), exist_ok=True)
    data = {'bullish': list(bullish), 'bearish': list(bearish),
            'last_updated': datetime.now(timezone.utc).isoformat(),
            'updated_by': updated_by}
    with open(KEYWORDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    return data


def _load_keywords(force=False):
    """Active keyword lists from logs/guinevere_keywords.json (5-min cache).
    Auto-initialises the file from the hardcoded defaults if it is missing."""
    now = datetime.now(timezone.utc)
    if (not force and _kw_cache['ts']
            and (now - _kw_cache['ts']).total_seconds() < 300):
        return _kw_cache
    data = None
    try:
        with open(KEYWORDS_FILE, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d.get('bullish'), list) and isinstance(d.get('bearish'), list):
            data = d
    except Exception:
        data = None
    if data is None:
        data = _write_keywords_file(BULLISH_KEYWORDS, BEARISH_KEYWORDS, 'defaults')
    _kw_cache.update(ts=now, bullish=data['bullish'], bearish=data['bearish'],
                     last_updated=data.get('last_updated'), updated_by=data.get('updated_by'))
    return _kw_cache


def get_keywords():
    """Public: current keyword lists + metadata (for the dashboard editor)."""
    kw = _load_keywords(force=True)
    return {'bullish': list(kw['bullish']), 'bearish': list(kw['bearish']),
            'last_updated': kw['last_updated'], 'updated_by': kw['updated_by']}

def _log_keyword_change(action, keyword, kind, by):
    """Append a keyword add/remove to logs/guinevere_keyword_changes.log (Part 3)."""
    try:
        os.makedirs(os.path.dirname(KEYWORD_CHANGE_LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        with open(KEYWORD_CHANGE_LOG, 'a', encoding='utf-8') as f:
            f.write('[%s] %s "%s" (%s) by %s\n' % (ts, action, keyword, kind.upper(), by))
    except Exception as e:
        logger.warning("Guinevere: could not log keyword change: %s", e)


def save_keywords(bullish, bearish, updated_by='Nick'):
    """Persist a new keyword set from the dashboard editor (Part 3, live -- Guinevere
    re-reads every 5 min, no restart). Dedupes/strips, logs each add/remove vs the
    current set, writes logs/guinevere_keywords.json, and refreshes the cache.
    Returns the updated {bullish, bearish, last_updated, updated_by}."""
    def _clean(lst):
        seen, out = set(), []
        for k in (lst or []):
            k = str(k).strip()
            if k and k.lower() not in seen:
                seen.add(k.lower()); out.append(k)
        return out
    new_bull, new_bear = _clean(bullish), _clean(bearish)
    cur = _load_keywords(force=True)
    old_bull_l = {k.lower() for k in cur['bullish']}
    old_bear_l = {k.lower() for k in cur['bearish']}
    new_bull_l = {k.lower() for k in new_bull}
    new_bear_l = {k.lower() for k in new_bear}
    for k in new_bull:
        if k.lower() not in old_bull_l:
            _log_keyword_change('ADDED', k, 'BULLISH', updated_by)
    for k in cur['bullish']:
        if k.lower() not in new_bull_l:
            _log_keyword_change('REMOVED', k, 'BULLISH', updated_by)
    for k in new_bear:
        if k.lower() not in old_bear_l:
            _log_keyword_change('ADDED', k, 'BEARISH', updated_by)
    for k in cur['bearish']:
        if k.lower() not in new_bear_l:
            _log_keyword_change('REMOVED', k, 'BEARISH', updated_by)
    data = _write_keywords_file(new_bull, new_bear, updated_by)
    _load_keywords(force=True)   # refresh the 5-min cache immediately
    return {'bullish': data['bullish'], 'bearish': data['bearish'],
            'last_updated': data['last_updated'], 'updated_by': data['updated_by']}


# --- Macro sentiment overlay (Part 4) --------------------------------------
# Desk-wide macro flag set in RoundTable (logs/macro_sentiment.json), re-read
# every 5 min. Per-system nudge to the final Guinevere sentiment score, plus a
# CRISIS-only confidence-bar raise applied by the trading engine.
VALID_MACRO         = ('RISK_ON', 'NEUTRAL', 'RISK_OFF', 'CRISIS')
MACRO_SCORE_ADJ     = {'RISK_ON': 2, 'NEUTRAL': 0, 'RISK_OFF': -2, 'CRISIS': -3}   # FTSE
MACRO_CONF_BAR_ADJ  = {'RISK_ON': 0, 'NEUTRAL': 0, 'RISK_OFF': 0, 'CRISIS': 10}   # all systems
_macro_cache = {'ts': None, 'data': None}


def get_macro():
    """Current desk-wide macro sentiment flag from RoundTable. Re-read fresh from disk
    on every Arthur consultation -- live, no restart needed to change the flag (Macro
    Live Reload, 19 Jul 2026). A 5-second debounce coalesces the several get_macro()
    calls made within one consultation so high-frequency ticks don't hammer disk.
    Returns {'flag','set_at','set_by'}; defaults to NEUTRAL if the file is missing."""
    now = datetime.now(timezone.utc)
    if _macro_cache['data'] is not None and _macro_cache['ts'] is not None \
            and (now - _macro_cache['ts']).total_seconds() < 5:
        return _macro_cache['data']
    data = {'flag': 'NEUTRAL', 'set_at': '', 'set_by': ''}
    try:
        with open(MACRO_FILE, encoding='utf-8') as f:
            d = json.load(f)
        flag = str(d.get('flag', 'NEUTRAL')).upper()
        if flag not in VALID_MACRO:
            flag = 'NEUTRAL'
        data = {'flag': flag, 'set_at': d.get('set_at', ''), 'set_by': d.get('set_by', '')}
    except Exception:
        pass
    _macro_cache['data'] = data
    _macro_cache['ts'] = now
    return data


def get_macro_adjustment():
    """(score_adj, conf_bar_adj, macro_state) for THIS system under the current flag."""
    m = get_macro()
    return MACRO_SCORE_ADJ.get(m['flag'], 0), MACRO_CONF_BAR_ADJ.get(m['flag'], 0), m


def get_macro_context():
    """One-line macro description for Arthur's prompt (Part 4)."""
    score_adj, conf_bar, m = get_macro_adjustment()
    parts = []
    if score_adj:
        parts.append("Guinevere sentiment score %+d" % score_adj)
    if conf_bar:
        parts.append("confidence bar +%d (trade more conservatively)" % conf_bar)
    desc = "; ".join(parts) if parts else "no adjustment for this system"
    return "Global macro sentiment: %s (set %s UTC). %s." % (
        m['flag'], m.get('set_at') or 'n/a', desc)


def _score_headline(title, description=''):
    """Score a headline: +1 per bullish keyword, -1 per bearish (case-insensitive
    phrase match). Keywords are the live editable set (logs/guinevere_keywords.json)."""
    text = (title + ' ' + (description or '')).lower()
    kw = _load_keywords()
    score = 0
    for keyword in kw['bullish']:
        if keyword.lower() in text:
            score += 1
    for keyword in kw['bearish']:
        if keyword.lower() in text:
            score -= 1
    return score


def _is_recent(published_at_str):
    """Check if an article is within MAX_NEWS_AGE_HOURS."""
    try:
        published = datetime.fromisoformat(published_at_str.replace('Z', '+00:00'))
        age = datetime.now(timezone.utc) - published
        return age < timedelta(hours=MAX_NEWS_AGE_HOURS)
    except Exception:
        return False


def fetch_ftse_sentiment():
    """Fetch latest UK/FTSE news from the Currents API.
    Returns dict with sentiment, score, headlines, reason. Caches for
    CACHE_DURATION_MINUTES."""
    global _news_cache

    if _news_cache['timestamp']:
        age = datetime.now(timezone.utc) - _news_cache['timestamp']
        if age < timedelta(minutes=CACHE_DURATION_MINUTES):
            logger.debug("guinevere_news: Using cached sentiment")
            return _news_cache

    if not CURRENTS_API_KEY or CURRENTS_API_KEY == 'PASTE_YOUR_KEY_HERE':
        logger.warning("guinevere_news: No CURRENTS_API_KEY in .env")
        return {'sentiment': 'NEUTRAL', 'score': 0, 'headlines': [],
                'reason': 'No API key configured'}

    try:
        params = {
            'apiKey': CURRENTS_API_KEY,
            'keywords': 'FTSE OR "FTSE 100" OR "Bank of England" OR "UK economy" OR pound OR sterling OR chancellor',
            'language': 'en',
            'limit': 10,
        }
        response = requests.get(f'{CURRENTS_BASE_URL}/search', params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        articles = data.get('news', [])
        recent = [a for a in articles if _is_recent(a.get('published', ''))]

        if not recent:
            result = {'sentiment': 'NEUTRAL', 'score': 0, 'headlines': [],
                      'reason': 'No recent UK/FTSE news (last 4hrs)',
                      'timestamp': datetime.now(timezone.utc)}
            _news_cache = result
            return result

        # Dedup by url/title first -- the news API can return the same article
        # several times, inflating the score and repeating headlines (Snag 16).
        total_score = 0
        headlines = []
        seen = set()
        for article in recent:
            title = (article.get('title') or '').strip()
            key = (article.get('url') or article.get('link') or title).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            score = _score_headline(title, article.get('description', ''))
            total_score += score
            headlines.append({'title': title, 'score': score,
                              'published': article.get('published', '')})
            if len(headlines) >= 5:
                break

        # Part 4: macro sentiment overlay -- nudge the final score by FTSE's macro adj.
        macro_adj, macro_conf_bar, macro_state = get_macro_adjustment()
        base_score = total_score
        total_score += macro_adj

        if total_score >= 2:
            sentiment = 'BULLISH'
            reason = f"FTSE news BULLISH (score +{total_score})"
        elif total_score <= -2:
            sentiment = 'BEARISH'
            reason = f"FTSE news BEARISH (score {total_score})"
        else:
            sentiment = 'NEUTRAL'
            reason = f"FTSE news NEUTRAL (score {total_score})"
        if macro_adj:
            reason += f" [macro {macro_state['flag']} {macro_adj:+d}]"

        result = {'sentiment': sentiment, 'score': total_score, 'headlines': headlines,
                  'reason': reason, 'timestamp': datetime.now(timezone.utc),
                  'macro_flag': macro_state['flag'], 'macro_adj': macro_adj,
                  'macro_conf_bar': macro_conf_bar, 'base_score': base_score}
        _news_cache = result
        logger.info("guinevere_news: %s", reason)
        try:
            result['eia_window'] = get_ftse_calendar_status()[0]
        except Exception:
            result['eia_window'] = False
        try:
            save_sentiment(result)
        except Exception:
            pass
        return result

    except requests.exceptions.Timeout:
        logger.warning("guinevere_news: Currents API timeout")
        return {**_news_cache, 'reason': 'API timeout -- using cache'}
    except Exception as e:
        logger.error("guinevere_news: Error fetching news: %s", e)
        return {**_news_cache, 'reason': f'API error: {e}'}


def get_confidence_adjustment(direction):
    """Return (adjustment, reason) based on news sentiment and the trade
    direction being considered (direction-aware, matches Oil/Gas):
      LONG + BULLISH / SHORT + BEARISH  -> +8  (news supports entry)
      LONG + BEARISH / SHORT + BULLISH  -> -8  (news opposes entry)
      Any + NEUTRAL                     ->  0
    Soft context that informs Arthur -- never overrides him."""
    sentiment_data = fetch_ftse_sentiment()
    sentiment = sentiment_data['sentiment']
    reason = sentiment_data['reason']

    if sentiment == 'NEUTRAL':
        return 0.0, f"Guinevere News: NEUTRAL -- {reason}"
    if (direction == 'LONG' and sentiment == 'BULLISH') or \
       (direction == 'SHORT' and sentiment == 'BEARISH'):
        return 8.0, f"Guinevere News: +8 confidence -- {reason}"
    if (direction == 'LONG' and sentiment == 'BEARISH') or \
       (direction == 'SHORT' and sentiment == 'BULLISH'):
        return -8.0, f"Guinevere News: -8 confidence -- {reason}"
    return 0.0, f"Guinevere News: NEUTRAL -- {reason}"


def get_ftse_calendar_status():
    """Soft caution flag placeholder for schema parity with Gold/Oil/Gas. FTSE's
    hard-block calendar windows (BOE / UK CPI / NFP) are owned by calendar_ftse,
    so this news module does not duplicate them. Returns (False, "")."""
    return False, ""


def format_news_context():
    """Prompt-ready Guinevere news block for Arthur's prompt (uses the cached
    fetch). Direction-independent -- the +/-8 is applied post-decision by the
    main loop, so the block explains the rule rather than a fixed number."""
    d = fetch_ftse_sentiment()
    sent = d.get('sentiment', 'NEUTRAL')
    score = d.get('score', 0) or 0
    caution = get_ftse_calendar_status()[1] or 'none'
    lines = [
        "GUINEVERE NEWS SENTIMENT (FTSE)",
        f"  Sentiment: {sent} (news score {score:+d})",
        "  Effect: +8 confidence if you enter WITH the sentiment "
        "(LONG on BULLISH / SHORT on BEARISH); -8 if against it; 0 if NEUTRAL.",
        f"  Event caution window: {caution}",
        "  Top headlines:",
    ]
    hs = d.get('headlines', []) or []
    if hs:
        for h in hs[:3]:
            title = (h.get('title') or '')[:80]
            lines.append(f"    [{int(h.get('score', 0)):+d}] {title}")
    else:
        lines.append("    (no recent UK/FTSE-relevant headlines)")
    lines.append("")
    lines.append(get_macro_context())   # Part 4: desk-wide macro sentiment overlay
    return "\n".join(lines)
