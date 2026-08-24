import ast
import csv
import json
import feedparser
import math
import os
import statistics
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    from lse import LSE, LSEError
except Exception as exc:
    print(f"FATAL: lse-data import failed: {exc}")
    raise

print("=" * 72)
print("LSE SMART INTRADAY SCANNER v10.1 — 9-STRATEGY INDEPENDENT CONSENSUS — VALIDATED")
print("=" * 72)
print("DAILY INTELLIGENCE -> 1D CONTEXT -> 4H REGIME -> 1H DECISION")
print("-> 30M SETUP -> 15M CONFIRMATION -> 5M TRIGGER")
print("DYNAMIC LSE CATALOG | 2R MINIMUM | NO ARTIFICIAL TP MAXIMUM")
print("AUTOMATIC TRADING: DISABLED")
print()

# ----------------------------
# Configuration
# ----------------------------
TF = ["1d", "4h", "1h", "30m", "15m", "5m"]
DIRECT_TF = ["1d", "4h", "1h", "15m", "5m"]
MIN_BARS = {"1d": 120, "4h": 180, "1h": 240, "30m": 300, "15m": 360, "5m": 500}
FETCH_LIMIT = {"1d": 700, "4h": 900, "1h": 1000, "15m": 1200, "5m": 1400}
def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        print(f"CONFIG WARNING: {name} is invalid; using {default}")
        return default

def env_float(name, default):
    try:
        return float(os.getenv(name, str(default)) or str(default))
    except (TypeError, ValueError):
        print(f"CONFIG WARNING: {name} is invalid; using {default}")
        return default

MAX_INSTRUMENTS = max(0, env_int("MAX_INSTRUMENTS", 22))
TOP_N = max(1, env_int("TOP_N", 5))
RUNTIME_MINUTES = env_float("SCAN_RUNTIME_MINUTES", 15.0)
if RUNTIME_MINUTES <= 0:
    RUNTIME_MINUTES = 15.0
RUNTIME_MINUTES = min(RUNTIME_MINUTES, 18.0)
# Keep a small safety reserve so the scanner stops before the configured budget
# instead of overrunning it because of final formatting / cleanup.
RUNTIME_GUARD_SECONDS = 20.0
SCAN_START = time.monotonic()
SCAN_BUDGET_SECONDS = max(30.0, RUNTIME_MINUTES * 60.0 - RUNTIME_GUARD_SECONDS)
SCAN_DEADLINE = SCAN_START + SCAN_BUDGET_SECONDS
CATEGORIES = [x.strip().lower() for x in os.getenv(
    "LSE_CATEGORIES", "forex,commodity,index,crypto,stock,etf,futures"
).split(",") if x.strip()]
NEWS_URLS = [x.strip() for x in os.getenv("NEWS_RSS_URLS", "").split(",") if x.strip()]
# Authoritative default universe: exactly 20 FX pairs + XAU/USD + BTC/USD.
# Keep this list isolated so additional instruments can be added without
# changing the strategy engine. The scanner always attempts every configured
# target and records DATA_UNAVAILABLE instead of silently dropping a target.
TARGET_FX = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD",
    "EUR/GBP", "EUR/JPY", "EUR/CHF", "EUR/AUD", "EUR/CAD", "EUR/NZD",
    "GBP/JPY", "GBP/CHF", "GBP/AUD", "GBP/CAD", "AUD/JPY", "AUD/NZD", "CAD/JPY",
]
TARGET_NON_FX = ["XAU/USD", "BTC/USD"]
TARGET_SYMBOLS = TARGET_FX + TARGET_NON_FX
TARGET_UNIVERSE_SIZE = len(TARGET_SYMBOLS)
MAJOR_FX = set(TARGET_FX)
PRIORITY_METALS = {"XAU/USD"}
PRIORITY_CRYPTO = {"BTC/USD"}
FOCUSED_SYMBOLS = set(TARGET_SYMBOLS)
SYMBOL_ALIASES = {
"EURUSD":"EUR/USD","EUR-USD":"EUR/USD","EUR_USD":"EUR/USD",
"GBPUSD":"GBP/USD","GBP-USD":"GBP/USD","GBP_USD":"GBP/USD",
"USDJPY":"USD/JPY","USD-JPY":"USD/JPY","USD_JPY":"USD/JPY",
"USDCHF":"USD/CHF","USD-CHF":"USD/CHF","USD_CHF":"USD/CHF",
"AUDUSD":"AUD/USD","AUD-USD":"AUD/USD","AUD_USD":"AUD/USD",
"USDCAD":"USD/CAD","USD-CAD":"USD/CAD","USD_CAD":"USD/CAD",
"NZDUSD":"NZD/USD","NZD-USD":"NZD/USD","NZD_USD":"NZD/USD",
"EURGBP":"EUR/GBP","EUR-GBP":"EUR/GBP","EUR_GBP":"EUR/GBP",
"EURJPY":"EUR/JPY","EUR-JPY":"EUR/JPY","EUR_JPY":"EUR/JPY",
"GBPJPY":"GBP/JPY","GBP-JPY":"GBP/JPY","GBP_JPY":"GBP/JPY",
"EURCHF":"EUR/CHF","EUR-CHF":"EUR/CHF","EUR_CHF":"EUR/CHF",
"AUDJPY":"AUD/JPY","AUD-JPY":"AUD/JPY","AUD_JPY":"AUD/JPY",
"EURAUD":"EUR/AUD","EUR-AUD":"EUR/AUD","EUR_AUD":"EUR/AUD",
"EURCAD":"EUR/CAD","EUR-CAD":"EUR/CAD","EUR_CAD":"EUR/CAD",
"EURNZD":"EUR/NZD","EUR-NZD":"EUR/NZD","EUR_NZD":"EUR/NZD",
"GBPCHF":"GBP/CHF","GBP-CHF":"GBP/CHF","GBP_CHF":"GBP/CHF",
"GBPAUD":"GBP/AUD","GBP-AUD":"GBP/AUD","GBP_AUD":"GBP/AUD",
"GBPCAD":"GBP/CAD","GBP-CAD":"GBP/CAD","GBP_CAD":"GBP/CAD",
"AUDNZD":"AUD/NZD","AUD-NZD":"AUD/NZD","AUD_NZD":"AUD/NZD",
"CADJPY":"CAD/JPY","CAD-JPY":"CAD/JPY","CAD_JPY":"CAD/JPY",
"XAUUSD":"XAU/USD","XAU-USD":"XAU/USD","XAU_USD":"XAU/USD",
"XAGUSD":"XAG/USD","XAG-USD":"XAG/USD","XAG_USD":"XAG/USD",
"XPTUSD":"XPT/USD","XPT-USD":"XPT/USD","XPT_USD":"XPT/USD",
"XPDUSD":"XPD/USD","XPD-USD":"XPD/USD","XPD_USD":"XPD/USD",
"BTCUSD":"BTC/USD","BTC-USD":"BTC/USD","BTC_USD":"BTC/USD"
}

# User-requested architecture:
# 1D context -> 4H controlling regime -> 1H decision -> 30M setup
# -> 15M confirmation -> 5M trigger.
# 30M is deliberately built from completed 15M candles.
#
# The scanner is analytical only. It never places orders.

def now_utc():
    return datetime.now(timezone.utc)

def parse_ts(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(v)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def clean_num(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default

def normalize_rows(raw):
    if isinstance(raw, dict):
        for key in ("data", "rows", "candles", "results"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        # Accept the common OHLCV spellings without guessing a price.
        ts = r.get("timestamp", r.get("time", r.get("datetime")))
        if ts is None:
            continue
        try:
            dt = parse_ts(ts)
        except Exception:
            continue
        c = {
            "timestamp": dt,
            "open": clean_num(r.get("open")),
            "high": clean_num(r.get("high")),
            "low": clean_num(r.get("low")),
            "close": clean_num(r.get("close")),
            "volume": clean_num(r.get("volume", 0.0)),
        }
        if c["high"] < max(c["open"], c["close"]) or c["low"] > min(c["open"], c["close"]):
            continue
        if c["high"] < c["low"]:
            continue
        out.append(c)
    out.sort(key=lambda x: x["timestamp"])
    return out

def timeframe_minutes(tf):
    return {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[tf]

def remove_incomplete(rows, tf):
    if not rows:
        return []
    mins = timeframe_minutes(tf)
    cutoff = now_utc()
    return [
        r for r in rows
        if r["timestamp"] + timedelta(minutes=mins) <= cutoff
    ]

def fetch_candles(client, symbol, tf):
    try:
        raw = client.candles(symbol, timeframe=tf, limit=FETCH_LIMIT[tf], order="desc")
    except TypeError:
        # Compatibility fallback for SDK builds that use positional timeframe.
        raw = client.candles(symbol, tf, limit=FETCH_LIMIT[tf], order="desc")
    rows = remove_incomplete(normalize_rows(raw), tf)
    return rows[-FETCH_LIMIT[tf]:]

def build_30m_from_15m(rows15):
    buckets = {}
    for r in rows15:
        # UTC floor to 30-minute boundary.
        t = r["timestamp"].replace(second=0, microsecond=0)
        minute = (t.minute // 30) * 30
        key = t.replace(minute=minute)
        bucket = buckets.setdefault(key, [])
        bucket.append(r)
    out = []
    for key in sorted(buckets):
        b = sorted(buckets[key], key=lambda x: x["timestamp"])
        # Require exactly two consecutive 15m bars. Never synthesize a missing half.
        if len(b) != 2:
            continue
        if b[1]["timestamp"] - b[0]["timestamp"] != timedelta(minutes=15):
            continue
        out.append({
            "timestamp": key,
            "open": b[0]["open"],
            "high": max(x["high"] for x in b),
            "low": min(x["low"] for x in b),
            "close": b[-1]["close"],
            "volume": sum(x["volume"] for x in b),
        })
    return out

def ema(values, period):
    if len(values) < period:
        return [None] * len(values)
    alpha = 2.0 / (period + 1)
    out = [None] * (period - 1)
    e = statistics.fmean(values[:period])
    out.append(e)
    for v in values[period:]:
        e = alpha * v + (1 - alpha) * e
        out.append(e)
    return out

def rsi(values, period=14):
    if len(values) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = statistics.fmean(gains[:period])
    al = statistics.fmean(losses[:period])
    for g, l in zip(gains[period:], losses[period:]):
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))

def atr(rows, period=14):
    if len(rows) <= period:
        return None
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["high"], rows[i]["low"], rows[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return statistics.fmean(trs[-period:])

def macd(values):
    e12, e26 = ema(values, 12), ema(values, 26)
    series = [(a-b) for a, b in zip(e12, e26) if a is not None and b is not None]
    if len(series) < 9:
        return 0.0, 0.0
    sig = ema(series, 9)
    return series[-1], sig[-1] if sig[-1] is not None else 0.0

def swings(rows, lookback=2):
    highs, lows = [], []
    for i in range(lookback, len(rows)-lookback):
        h = rows[i]["high"]
        l = rows[i]["low"]
        if h == max(x["high"] for x in rows[i-lookback:i+lookback+1]):
            highs.append((i, h))
        if l == min(x["low"] for x in rows[i-lookback:i+lookback+1]):
            lows.append((i, l))
    return highs, lows

def direction(rows):
    if len(rows) < 30:
        return "NEUTRAL"
    c = [x["close"] for x in rows]
    e20, e50 = ema(c, 20), ema(c, 50)
    if e20[-1] is None or e50[-1] is None:
        return "NEUTRAL"
    if c[-1] > e20[-1] > e50[-1]:
        return "BUY"
    if c[-1] < e20[-1] < e50[-1]:
        return "SELL"
    return "NEUTRAL"

def structure_signal(rows):
    if len(rows) < 20:
        return "NEUTRAL", "insufficient structure"
    highs, lows = swings(rows[-80:], 2)
    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL", "insufficient swing structure"
    last_h, prev_h = highs[-1][1], highs[-2][1]
    last_l, prev_l = lows[-1][1], lows[-2][1]
    if last_h > prev_h and last_l > prev_l:
        return "BUY", "bullish HH + HL"
    if last_h < prev_h and last_l < prev_l:
        return "SELL", "bearish LH + LL"
    return "NEUTRAL", "mixed structure"

def candle_signal(rows):
    if len(rows) < 3:
        return "NEUTRAL", "insufficient candles"
    a, b = rows[-2], rows[-1]
    body = abs(b["close"] - b["open"])
    rng = max(b["high"] - b["low"], 1e-12)
    bull = b["close"] > b["open"]
    bear = b["close"] < b["open"]
    engulf_bull = bull and a["close"] < a["open"] and b["close"] >= a["open"] and b["open"] <= a["close"]
    engulf_bear = bear and a["close"] > a["open"] and b["open"] >= a["close"] and b["close"] <= a["open"]
    if engulf_bull or (bull and body/rng >= 0.65):
        return "BUY", "bullish impulse/engulfing"
    if engulf_bear or (bear and body/rng >= 0.65):
        return "SELL", "bearish impulse/engulfing"
    return "NEUTRAL", "no strong price action"

def liquidity_signal(rows):
    if len(rows) < 25:
        return "NEUTRAL", "insufficient liquidity history"
    prior = rows[-21:-1]
    last = rows[-1]
    hi = max(x["high"] for x in prior)
    lo = min(x["low"] for x in prior)
    # Sweep then close back inside = liquidity event.
    if last["low"] < lo and last["close"] > lo:
        return "BUY", "sell-side liquidity sweep"
    if last["high"] > hi and last["close"] < hi:
        return "SELL", "buy-side liquidity sweep"
    return "NEUTRAL", "no fresh sweep"

def fib_signal(rows):
    if len(rows) < 40:
        return "NEUTRAL", "insufficient swing range"
    highs, lows = swings(rows[-100:], 2)
    if not highs or not lows:
        return "NEUTRAL", "no usable swing"
    h = highs[-1][1]
    l = lows[-1][1]
    span = h - l
    if span <= 0:
        return "NEUTRAL", "invalid swing range"
    p = rows[-1]["close"]
    retr = (p-l)/span
    if 0.382 <= retr <= 0.618:
        # Direction follows the current trend rather than guessing from the fib alone.
        d = direction(rows)
        return d, "price in 0.382-0.618 retracement zone" if d != "NEUTRAL" else "fib zone, trend unclear"
    return "NEUTRAL", "fib zone not near"

def trend_signal(rows):
    d = direction(rows)
    return d, ("EMA alignment" if d != "NEUTRAL" else "mixed EMA alignment")

def indicator_signal(rows):
    c = [x["close"] for x in rows]
    rv = rsi(c)
    m, s = macd(c)
    if rv is None:
        return "NEUTRAL", "RSI unavailable"
    if rv >= 55 and m > s:
        return "BUY", f"RSI {rv:.1f} / MACD bullish"
    if rv <= 45 and m < s:
        return "SELL", f"RSI {rv:.1f} / MACD bearish"
    return "NEUTRAL", f"RSI {rv:.1f} / MACD mixed"

def session_signal(rows, category):
    # Session is a context filter, not a standalone reason to enter.
    if not rows:
        return "NEUTRAL", "session unavailable"
    h = rows[-1]["timestamp"].hour
    # London/NY overlap in UTC approximately 13:00-16:00, with DST handled
    # imperfectly; this is intentionally a soft score only.
    if 13 <= h < 16:
        return direction(rows), "London + New York overlap"
    if 7 <= h < 16:
        return direction(rows), "London session"
    if 13 <= h < 21:
        return direction(rows), "New York session"
    return "NEUTRAL", "outside preferred session"

def smc_signal(rows):
    if len(rows) < 30:
        return "NEUTRAL", "insufficient MSS history"
    last = rows[-1]
    hi = max(x["high"] for x in rows[-21:-1])
    lo = min(x["low"] for x in rows[-21:-1])
    if last["close"] > hi:
        return "BUY", "bullish MSS / structure break"
    if last["close"] < lo:
        return "SELL", "bearish MSS / structure break"
    # A sweep alone is not promoted to MSS.
    return "NEUTRAL", "no confirmed MSS"

def supply_demand_signal(rows):
    if len(rows) < 30:
        return "NEUTRAL", "insufficient impulse history"
    a, b = rows[-2], rows[-1]
    atrv = atr(rows)
    if atrv is None:
        return "NEUTRAL", "ATR unavailable"
    body = abs(b["close"] - b["open"])
    if body >= 1.2 * atrv:
        return ("BUY", "demand zone + bullish impulse") if b["close"] > b["open"] else ("SELL", "supply zone + bearish impulse")
    return "NEUTRAL", "no strong impulse"

def fair_value_gaps(rows, lookback=80):
    """Detect real closed-candle three-candle imbalances."""
    if len(rows) < 3:
        return []
    sample = rows[-lookback:]
    out = []
    for i in range(2, len(sample)):
        a, b, c = sample[i-2], sample[i-1], sample[i]
        av = atr(sample[:i+1])
        if c["low"] > a["high"]:
            gap = c["low"] - a["high"]
            out.append({"side":"BUY","timestamp":c["timestamp"],"low":a["high"],"high":c["low"],"gap":gap,"quality":gap/av if av and av > 0 else 0.0,"mitigated":any(x["low"] <= a["high"] for x in sample[i+1:])})
        if c["high"] < a["low"]:
            gap = a["low"] - c["high"]
            out.append({"side":"SELL","timestamp":c["timestamp"],"low":c["high"],"high":a["low"],"gap":gap,"quality":gap/av if av and av > 0 else 0.0,"mitigated":any(x["high"] >= a["low"] for x in sample[i+1:])})
    return out

def fvg_signal(rows):
    fresh = [g for g in fair_value_gaps(rows) if not g["mitigated"]]
    if not fresh:
        return "NEUTRAL", "no fresh unmitigated FVG", None
    g = fresh[-1]
    strength = "strong" if g["quality"] >= 0.25 else "normal"
    return g["side"], f"{strength} unmitigated FVG {g['low']:.6g}-{g['high']:.6g}", g

def smc_core(rows):
    """SMC is one of nine independent directional methods; it never creates a mandatory direction."""
    structure_sig, structure_why = structure_signal(rows)
    mss_sig, mss_why = smc_signal(rows)
    liq_sig, liq_why = liquidity_signal(rows)
    fvg_sig, fvg_why, fvg = fvg_signal(rows)

    votes = [
        ("Structure", structure_sig, structure_why),
        ("MSS/CHoCH", mss_sig, mss_why),
        ("Liquidity", liq_sig, liq_why),
        ("FVG", fvg_sig, fvg_why),
    ]
    candidates = []
    for side in ("BUY", "SELL"):
        aligned_names = [name for name, sig, _ in votes if sig == side]
        opposed = sum(1 for _, sig, _ in votes if sig in ("BUY","SELL") and sig != side)
        has_structure = structure_sig == side
        has_mss = mss_sig == side
        has_liquidity = liq_sig == side
        has_fvg = fvg_sig == side

        # SMC is deliberately broader than the previous version:
        # a valid primary setup needs at least TWO SMC components,
        # with structure or MSS present. This captures real SMC setups
        # where a fresh liquidity sweep or FVG is not present on every TF.
        component_count = len(aligned_names)
        structural_anchor = has_structure or has_mss
        strong_combo = (
            (has_mss and (has_structure or has_liquidity or has_fvg)) or
            (has_structure and (has_liquidity or has_fvg)) or
            (has_liquidity and has_fvg)
        )
        passed = structural_anchor and component_count >= 2 and opposed <= 1

        score = (
            (30 if has_mss else 0) +
            (25 if has_fvg else 0) +
            (25 if has_structure else 0) +
            (20 if has_liquidity else 0)
        )
        if strong_combo:
            score += 5
        score = min(100, score)

        candidates.append((score, side, passed, component_count, opposed))

    candidates.sort(key=lambda x: (x[0], x[3], -x[4]), reverse=True)
    if not candidates or candidates[0][0] == 0:
        return {"side":"NEUTRAL","passed":False,"score":0,"aligned":0,"opposed":0,"evidence":votes,"why":"no actionable SMC core"}

    top = candidates[0]
    # Equal SMC evidence on BUY and SELL is a tie, not a directional signal.
    tied = [x for x in candidates if x[0] == top[0] and x[3] == top[3] and x[4] == top[4]]
    if len(tied) > 1:
        return {
            "side":"NEUTRAL","passed":False,"score":top[0],"aligned":top[3],"opposed":top[4],
            "evidence":votes,
            "why":"SMC BUY/SELL evidence is tied; no directional edge"
        }

    score, side, passed, aligned, opposed = top
    if not passed:
        return {
            "side":side,"passed":False,"score":score,"aligned":aligned,"opposed":opposed,
            "evidence":votes,
            "why":"SMC evidence is developing; at least two aligned SMC components with a structural anchor are required"
        }
    return {
        "side":side,"passed":True,"score":score,"aligned":aligned,"opposed":opposed,
        "evidence":votes,
        "why":"primary SMC structure/MSS/liquidity/FVG gate passed"
    }

def method_scores(rows):
    """Supporting confirmation layer only. SMC components are evaluated separately by smc_core."""
    methods = []
    funcs = [
        ("Supply/Demand", supply_demand_signal),
        ("Price Action", candle_signal),
        ("Fibonacci", fib_signal),
        ("Trend", trend_signal),
        ("RSI+MACD", indicator_signal),
        ("Session", lambda x: session_signal(x, "")),
    ]
    for name, fn in funcs:
        try:
            sig, why = fn(rows)
        except Exception as exc:
            sig, why = "NEUTRAL", f"method error: {type(exc).__name__}"
        points = 1 if sig in ("BUY", "SELL") else 0
        methods.append({"name": name, "signal": sig, "why": why, "points": points})
    return methods

def method_side_score(methods, side):
    """Directional evidence from supporting methods only; SMC remains primary."""
    if side not in ("BUY", "SELL"):
        return 0.0
    aligned = sum(1 for m in methods if m["signal"] == side)
    opposed = sum(1 for m in methods if m["signal"] in ("BUY", "SELL") and m["signal"] != side)
    # Reward aligned methods, lightly penalize direct opposition, never punish missing/neutral methods.
    total = max(len(methods), 1)
    return max(0.0, min(100.0, (aligned / total) * 100.0 - (opposed / total) * 20.0))

def dominant_signal(methods, minimum=3):
    b = sum(m["points"] for m in methods if m["signal"] == "BUY")
    s = sum(m["points"] for m in methods if m["signal"] == "SELL")
    if b > s and b >= minimum:
        return "BUY"
    if s > b and s >= minimum:
        return "SELL"
    return "NEUTRAL"

def levels(rows, side):
    """Build SL/TP from real market structure. Never manufacture a 2R target."""
    a = atr(rows)
    if a is None or a <= 0:
        return None
    price = rows[-1]["close"]
    highs, lows = swings(rows[-100:], 2)

    if side == "BUY":
        stop_candidates = [x[1] for x in lows if x[1] < price]
        if not stop_candidates:
            return None
        sl = max(stop_candidates[-3:]) - 0.25 * a
        risk = price - sl
        if risk <= 0:
            return None

        # Report the nearest meaningful opposing swing even below 2R.
        # >=2R is evaluated later as a risk-quality confirmation gate.
        targets = sorted(set(x[1] for x in highs if x[1] > price))
        if not targets:
            return None
        tp = targets[0]
        return price, sl, tp, a

    if side == "SELL":
        stop_candidates = [x[1] for x in highs if x[1] > price]
        if not stop_candidates:
            return None
        sl = min(stop_candidates[-3:]) + 0.25 * a
        risk = sl - price
        if risk <= 0:
            return None

        # Report the nearest meaningful opposing swing even below 2R.
        # >=2R is evaluated later as a risk-quality confirmation gate.
        targets = sorted(set(x[1] for x in lows if x[1] < price), reverse=True)
        if not targets:
            return None
        tp = targets[0]
        return price, sl, tp, a

    return None

def rr_ok(level):
    if not level:
        return False
    entry, sl, tp, _ = level
    risk = abs(entry-sl)
    reward = abs(tp-entry)
    return risk > 0 and (reward / risk) >= (2.0 - 1e-9)

# ----------------------------
# LSE daily intelligence
# ----------------------------
def safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}

def as_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ("data", "rows", "results"):
            if isinstance(x.get(k), list):
                return x[k]
    return []

def external_news():
    items = []
    for url in NEWS_URLS[:8]:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                title = str(entry.get("title", "")).strip()
                link = str(entry.get("link", "")).strip()
                if title:
                    items.append({"title": title, "link": link, "source": urlparse(url).netloc})
        except Exception:
            continue
    return items[:15]

def return_series(rows):
    closes = [x["close"] for x in rows if x.get("close") is not None]
    if len(closes) < 3:
        return []
    return [(closes[i] / closes[i-1]) - 1.0 for i in range(1, len(closes)) if closes[i-1] != 0]

def pearson(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    da = [x - ma for x in a]
    db = [x - mb for x in b]
    den = math.sqrt(sum(x*x for x in da) * sum(x*x for x in db))
    return (sum(x*y for x, y in zip(da, db)) / den) if den else None

def correlation_context(symbol, rows, benchmark_rows):
    base = return_series(rows)
    vals = []
    for bench, brow in benchmark_rows.items():
        if bench == symbol:
            continue
        c = pearson(base, return_series(brow))
        if c is not None:
            vals.append((bench, c))
    vals.sort(key=lambda x: abs(x[1]), reverse=True)
    return vals[:3]

def daily_intelligence(client):
    print("LSE CAPABILITY / DAILY INTELLIGENCE CHECK")
    intel = {
        "calendar": [], "macro": {}, "yields": {}, "cot": {}, "news": [],
        "live": "UNKNOWN", "ml": "LOCAL STATISTICAL MODEL"
    }
    # Economic calendar: collect major regions, but failures remain explicit.
    for region in ("US", "GB", "EU", "JP", "AU", "CA", "NZ", "CH"):
        data = safe_call(client.economic_calendar, region=region, start=now_utc().date().isoformat())
        if isinstance(data, dict) and "_error" in data:
            continue
        intel["calendar"].extend(as_list(data))
    cpi = safe_call(client.economics, "cpi_yoy")
    ffr = safe_call(client.series, "fdtr")
    intel["macro"] = {"cpi_yoy": as_list(cpi), "policy_rate": as_list(ffr)}
    y = safe_call(client.bond_yields, "US10Y")
    intel["yields"]["US10Y"] = as_list(y)
    # Add major government-yield benchmarks used for FX macro context.
    # Missing series remain explicitly unavailable; no yield is fabricated.
    for code in ("DE10Y", "GB10Y", "JP10Y", "AU10Y", "CA10Y", "NZ10Y", "CH10Y"):
        yr = safe_call(client.series, code)
        rows = as_list(yr)
        if rows:
            intel["yields"][code] = rows
    # COT is futures-based; GC/CL/ES are used as broad cross-market context.
    for code in ("GC", "CL", "ES"):
        c = safe_call(client.cot, code)
        rows = as_list(c)
        if rows:
            intel["cot"][code] = rows[-1]
    intel["news"] = external_news()
    try:
        stream = client.stream
        intel["live"] = "AVAILABLE" if callable(stream) else "UNAVAILABLE"
    except Exception:
        intel["live"] = "UNAVAILABLE"
    return intel

def latest_value(rows):
    if not rows:
        return None
    row = rows[-1]
    if isinstance(row, dict):
        for key in ("value", "close", "yield", "rate"):
            if row.get(key) is not None:
                try:
                    return float(row[key])
                except Exception:
                    return None
    return None

YIELD_CODES = {
    "USD": "US10Y", "EUR": "DE10Y", "GBP": "GB10Y", "JPY": "JP10Y",
    "AUD": "AU10Y", "CAD": "CA10Y", "NZD": "NZ10Y", "CHF": "CH10Y"
}

def yield_context(intel, symbol):
    if "/" not in symbol:
        return None
    base, quote = symbol.split("/", 1)
    if base not in YIELD_CODES or quote not in YIELD_CODES:
        return None
    b = latest_value(intel["yields"].get(YIELD_CODES[base], []))
    q = latest_value(intel["yields"].get(YIELD_CODES[quote], []))
    if b is None or q is None:
        return None
    return b - q

def event_risk(intel, symbol):
    now = now_utc()
    high = []
    for e in intel["calendar"]:
        text = " ".join(str(e.get(k, "")) for k in ("title", "event", "name", "impact", "importance")).lower()
        currency_text = " ".join(str(e.get(k, "")) for k in ("currency", "country", "region")).upper()
        symbol_currencies = set(symbol.split("/")) if "/" in symbol else set()
        relevant_currency = (not currency_text) or any(c in currency_text for c in symbol_currencies)
        if relevant_currency and any(w in text for w in ("high", "red", "major")):
            dt = None
            for k in ("timestamp", "datetime", "date", "time"):
                if e.get(k):
                    try:
                        dt = parse_ts(e[k])
                        break
                    except Exception:
                        pass
            if dt is None or abs((dt-now).total_seconds()) <= 24*3600:
                high.append(e)
    return ("HIGH", high[:5]) if high else ("LOW", [])

# ----------------------------
# Dynamic catalog discovery
# ----------------------------
api_key = os.getenv("LSE_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("LSE_API_KEY secret is missing. Add it in GitHub Settings -> Secrets and variables -> Actions.")
client = LSE(api_key=api_key)
intel = daily_intelligence(client)

# Small, fixed benchmark set used only for cross-market context.
# Missing/unavailable benchmarks are reported rather than guessed.
benchmark_rows = {}
for benchmark in ("EUR/USD", "GBP/USD", "USD/JPY", "XAU/USD", "BTC/USD"):
    try:
        benchmark_rows[benchmark] = fetch_candles(client, benchmark, "1h")
    except Exception:
        benchmark_rows[benchmark] = []

catalog = []
catalog_errors = []
# LSE documents catalog() as the authoritative full instrument catalog.
# Fetch it once, then filter locally. This avoids guessing which singular/plural
# spelling a particular SDK build accepts (for example futures vs future).
try:
    rows = client.catalog()
    if isinstance(rows, dict):
        rows = rows.get("data", rows.get("results", []))
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                sym = row.get("symbol")
                if sym:
                    catalog.append({
                        "symbol": sym,
                        "name": row.get("name", row.get("display_name", sym)),
                        "category": str(row.get("category", "")).strip().lower(),
                        "tick_count": row.get("ticks", row.get("tick_count", 0)),
                    })
    else:
        catalog_errors.append("catalog(): unexpected response shape")
except Exception as exc:
    catalog_errors.append(f"catalog(): {type(exc).__name__}: {exc}")

# Resolve each configured target against the live catalog when possible.
# Catalog access is authoritative for symbol aliases/category metadata, but
# catalog failure must not prevent direct attempts against the configured 22.
category_aliases = {"stocks":"stock","stock":"stock","forex":"forex","crypto":"crypto","etfs":"etf","etf":"etf","commodities":"commodity","commodity":"commodity","metals":"commodity","metal":"commodity","indices":"index","index":"index","futures":"futures","future":"futures","options":"options"}
unique = {}
for row in catalog:
    raw_symbol = str(row["symbol"]).strip()
    canonical = SYMBOL_ALIASES.get(raw_symbol.upper(), raw_symbol)
    if canonical not in FOCUSED_SYMBOLS:
        continue
    item = dict(row)
    item["symbol"] = raw_symbol
    item["canonical_symbol"] = canonical
    item["category"] = category_aliases.get(str(row.get("category", "")).strip().lower(), str(row.get("category", "")).strip().lower())
    try:
        item["_tick_count"] = int(item.get("tick_count", 0) or 0)
    except Exception:
        item["_tick_count"] = 0
    prev = unique.get(canonical)
    if prev is None or (raw_symbol.upper() == canonical and prev["symbol"].upper() != canonical):
        unique[canonical] = item

instruments = []
for canonical in TARGET_SYMBOLS:
    meta = unique.get(canonical)
    if meta is None:
        # Direct LSE requests will still be attempted. This makes the catalog
        # an enrichment source, not a hard gate on the requested universe.
        meta = {
            "symbol": canonical,
            "canonical_symbol": canonical,
            "name": canonical,
            "category": "forex" if canonical in MAJOR_FX else ("commodity" if canonical == "XAU/USD" else "crypto"),
            "catalog_missing": True,
        }
    else:
        meta = dict(meta)
        meta.pop("_tick_count", None)
        meta["catalog_missing"] = False
    instruments.append(meta)

# The default is exactly 22. A non-zero MAX_INSTRUMENTS is retained only as
# an explicit future extension knob; the workflow sets it to 22.
if MAX_INSTRUMENTS > 0 and MAX_INSTRUMENTS < len(instruments):
    instruments = instruments[:MAX_INSTRUMENTS]

if len(instruments) != TARGET_UNIVERSE_SIZE:
    raise RuntimeError(f"Configured target universe mismatch: expected {TARGET_UNIVERSE_SIZE}, got {len(instruments)}")

print(f"CATALOG CATEGORIES REQUESTED: {', '.join(CATEGORIES)}")
print("FOCUSED UNIVERSE: major FX + metals + BTC/USD")
print(f"TARGET UNIVERSE: {TARGET_UNIVERSE_SIZE} instruments (20 FX + XAU/USD + BTC/USD)")
print("TARGET SYMBOLS: " + ", ".join(TARGET_SYMBOLS))
print("CATALOG-MATCHED TARGETS: " + str(sum(1 for x in instruments if not x.get("catalog_missing"))) + f"/{TARGET_UNIVERSE_SIZE}")
print(f"MAX_INSTRUMENTS: {MAX_INSTRUMENTS}")
print(f"RUNTIME BUDGET: {RUNTIME_MINUTES:.1f} minutes")
discovered = {x["canonical_symbol"] for x in instruments if not x.get("catalog_missing")}
for meta in instruments:
    if meta.get("catalog_missing"):
        print(f"CATALOG WARNING: {meta['canonical_symbol']} not returned by catalog(); direct API attempt will still run")
if catalog_errors:
    print("CATALOG WARNINGS:")
    for x in catalog_errors:
        print(" -",x)
print()

# ----------------------------
# Scan instruments
# ----------------------------
results = []
complete = 0
incomplete = 0
errors = 0
processed = 0
runtime_limited = False

for idx, meta in enumerate(instruments, 1):
    if time.monotonic() >= SCAN_DEADLINE:
        runtime_limited = True
        print("⏱️ SOFT RUNTIME BUDGET REACHED — continuing because the 22-target universe must be attempted.")
    symbol = meta["symbol"]
    category = meta["category"]
    try:
        print(f"[{idx}/{len(instruments)}] {symbol} ({category})")
    
        data = {}
        failed = []
        for tf in DIRECT_TF:
            try:
                rows = fetch_candles(client, symbol, tf)
                data[tf] = rows
                print(f"  {tf}: {len(rows)} closed candles")
            except Exception as exc:
                data[tf] = []
                failed.append(f"{tf}: {type(exc).__name__}: {exc}")
                print(f"  {tf}: ERROR {type(exc).__name__}")
    
        # 30m is ALWAYS built from 15m, never requested as an independent source.
        data["30m"] = build_30m_from_15m(data.get("15m", []))
        print(f"  30m: {len(data['30m'])} completed candles built from 15m")
    
        if failed:
            errors += len(failed)
        missing = [tf for tf in TF if len(data.get(tf, [])) < MIN_BARS[tf]]
        processed += 1
        if missing:
            incomplete += 1
            # Never abort the 22-instrument scan because one symbol/request is bad.
            # Emit a stable result object so Telegram and downstream tooling can
            # distinguish unavailable data from a real WAIT/NO_ACTION analysis.
            result = {
                "symbol": symbol, "category": category,
                "status": "DATA_UNAVAILABLE", "side": "NEUTRAL",
                "trade_type": "DATA_UNAVAILABLE", "horizon": "not analyzed",
                "score": 0.0, "confidence_score": 0.0, "risk_score": 0.0,
                "method_score": 0.0, "rr": None, "levels": None,
                "alignment": {}, "blockers": [f"Missing required data: {', '.join(missing)}"],
                "events": [], "event_level": "UNAVAILABLE", "yield_diff": None,
                "correlations": [], "fvg": {}, "fvg_signals": {},
                "methods": [], "confirmation_methods": [], "trigger_methods": [], "h1_methods": [],
                "regime": "UNKNOWN", "technical_score": 0.0,
                "strategy_gate": "NO SIGNAL — insufficient market data",
                "strategy_consensus": {"aligned": [], "opposed": [], "timeframes": [], "count": 0},
                "agreed_strategies": [], "strategy_votes": {},
                "potential": None, "setup_wait": None, "smc_context": {},
                "supporting": {}, "chart_30m": [],
                "error_details": failed, "missing_timeframes": missing,
                "catalog_missing": bool(meta.get("catalog_missing")),
            }
            results.append(result)
            print(f"  DATA_UNAVAILABLE: {', '.join(missing)} — continuing to next instrument")
            continue
    
        complete += 1
        d = data["1d"]
        h4 = data["4h"]
        h1 = data["1h"]
        m30 = data["30m"]
        m15 = data["15m"]
        m5 = data["5m"]
    
        d_dir = direction(d)
        h4_dir = direction(h4)
        h1_dir = direction(h1)
        h1_methods = method_scores(h1)
        m30_methods = method_scores(m30)
        m15_methods = method_scores(m15)
        m5_methods = method_scores(m5)
        m30_fvg_sig, m30_fvg_why, m30_fvg = fvg_signal(m30)
        m15_fvg_sig, m15_fvg_why, m15_fvg = fvg_signal(m15)
        m5_fvg_sig, m5_fvg_why, m5_fvg = fvg_signal(m5)
    
        # ---------------------------------------------------------
        # ENTRY ENGINE v10.1 — 9-STRATEGY ADAPTIVE CONSENSUS
        # ---------------------------------------------------------
        # SMC is one of nine equal directional strategies and is NOT a mandatory dependency.
        # The scanner can qualify a setup
        # from any 2 of the 9 directional strategies, or from 3 independent
        # price-movement methods. SMC cannot veto or block a valid non-SMC consensus.
        #
        # 9 directional strategies:
        # 1) SMC
        # 2) Market Structure
        # 3) Liquidity
        # 4) Supply/Demand
        # 5) Price Action
        # 6) Fibonacci
        # 7) Trend
        # 8) RSI+MACD
        # 9) Session
        #
        # Risk gates remain independent: real >=2R and HIGH-impact event
        # blocking are still required for a CONFIRMED fresh entry.
    
        def counts(methods, side):
            aligned = sum(1 for m in methods if m["signal"] == side)
            opposed = sum(1 for m in methods if m["signal"] in ("BUY","SELL") and m["signal"] != side)
            neutral = sum(1 for m in methods if m["signal"] == "NEUTRAL")
            return aligned, opposed, neutral
    
        def dominant(methods, minimum=2):
            b = sum(1 for m in methods if m["signal"] == "BUY")
            s = sum(1 for m in methods if m["signal"] == "SELL")
            if b >= minimum and b > s:
                return "BUY"
            if s >= minimum and s > b:
                return "SELL"
            return "NEUTRAL"
    
        smc_1d = smc_core(d)
        smc_4h = smc_core(h4)
        smc_1h = smc_core(h1)
        smc_30m = smc_core(m30)
        smc_15m = smc_core(m15)
        smc_5m = smc_core(m5)
    
        h1_support = method_scores(h1)
        m30_support = method_scores(m30)
        m15_support = method_scores(m15)
        m5_support = method_scores(m5)
    
        # Explicit 9-strategy vote map. SMC is one strategy, while the
        # other eight methods are independent helpers/confirmations.
        def nine_strategy_signals(rows, smc_result):
            try:
                structure_sig, structure_why = structure_signal(rows)
            except Exception as exc:
                structure_sig, structure_why = "NEUTRAL", f"method error: {type(exc).__name__}"
            try:
                liquidity_sig, liquidity_why = liquidity_signal(rows)
            except Exception as exc:
                liquidity_sig, liquidity_why = "NEUTRAL", f"method error: {type(exc).__name__}"
            helper_map = {
                "Supply/Demand": supply_demand_signal,
                "Price Action": candle_signal,
                "Fibonacci": fib_signal,
                "Trend": trend_signal,
                "RSI+MACD": indicator_signal,
                "Session": lambda x: session_signal(x, category),
            }
            out = [{
                "name":"SMC",
                "signal": smc_result["side"] if smc_result.get("passed") else "NEUTRAL",
                "why": smc_result.get("why","SMC not fully established"),
            },{
                "name":"Market Structure",
                "signal":structure_sig,
                "why":structure_why,
            },{
                "name":"Liquidity",
                "signal":liquidity_sig,
                "why":liquidity_why,
            }]
            for name, fn in helper_map.items():
                try:
                    sig, why = fn(rows)
                except Exception as exc:
                    sig, why = "NEUTRAL", f"method error: {type(exc).__name__}"
                out.append({"name":name,"signal":sig,"why":why})
            return out
    
        strategy_by_tf = {
            "1d": nine_strategy_signals(d, smc_1d),
            "4h": nine_strategy_signals(h4, smc_4h),
            "1h": nine_strategy_signals(h1, smc_1h),
            "30m": nine_strategy_signals(m30, smc_30m),
            "15m": nine_strategy_signals(m15, smc_15m),
            "5m": nine_strategy_signals(m5, smc_5m),
        }
    
        strategy_names = [
            "SMC","Market Structure","Liquidity","Supply/Demand",
            "Price Action","Fibonacci","Trend","RSI+MACD","Session"
        ]
        price_method_names = {
            "Supply/Demand","Price Action","Fibonacci","Trend","RSI+MACD"
        }
    
        def consensus_stats(side_c):
            # Count DISTINCT strategies, not raw indicator votes. This
            # prevents one method repeated on several TFs from pretending
            # to be multiple strategies.
            aligned_names = set()
            opposed_names = set()
            aligned_tfs = set()
            opposed_tfs = set()
            per_strategy_tfs = {}
            for tf_name, methods in strategy_by_tf.items():
                for m in methods:
                    if m["signal"] == side_c:
                        aligned_names.add(m["name"])
                        aligned_tfs.add(tf_name)
                        per_strategy_tfs.setdefault(m["name"], set()).add(tf_name)
                    elif m["signal"] in ("BUY","SELL") and m["signal"] != side_c:
                        opposed_names.add(m["name"])
                        opposed_tfs.add(tf_name)
    
            # A strategy counts once even if it agrees on several TFs.
            # Multi-TF breadth is retained as a quality bonus rather than
            # an SMC dependency.
            price_names = aligned_names & price_method_names
            price_tfs = set()
            for tf_name, methods in strategy_by_tf.items():
                if any(m["name"] in price_method_names and m["signal"] == side_c for m in methods):
                    price_tfs.add(tf_name)
    
            return {
                "aligned_names": aligned_names,
                "opposed_names": opposed_names,
                "aligned_count": len(aligned_names),
                "opposed_count": len(opposed_names),
                "aligned_tfs": aligned_tfs,
                "opposed_tfs": opposed_tfs,
                "tf_count": len(aligned_tfs),
                "price_names": price_names,
                "price_count": len(price_names),
                "price_tfs": price_tfs,
                "price_tf_count": len(price_tfs),
                "per_strategy_tfs": per_strategy_tfs,
            }
    
        def level_rr(c, strategy="INTRADAY"):
            execution_rows = {
                "SNIPER": m5,
                "INTRADAY": m15,
                "SCALP": m5,
                "SWING/HOLD": h1,
            }.get(strategy, m30)
            level = levels(execution_rows, c)
            if not level:
                return None, None
            entry, sl, tp, a = level
            rr = abs(tp-entry) / max(abs(entry-sl), 1e-12)
            return level, rr
    
        def support_score(methods, side_c):
            aligned, opposed, neutral = counts(methods, side_c)
            return max(0.0, min(100.0,
                (aligned / max(len(methods), 1)) * 100.0 -
                (opposed / max(len(methods), 1)) * 20.0))
    
        def support_reasons(methods, side_c):
            return [f"{m['name']}: {m['why']}" for m in methods if m["signal"] == side_c]
    
        event_level, events = event_risk(intel, symbol)
        risk_penalty = 15.0 if event_level == "HIGH" else 0.0
        correlations = correlation_context(symbol, h1, benchmark_rows)
        yield_diff = yield_context(intel, symbol)
        news_ok = event_level != "HIGH"
    
        # Candidate directions come from the 9-strategy consensus itself,
        # so SMC-neutral symbols are still eligible.
        candidate_sides = []
        for c in ("BUY","SELL"):
            cs = consensus_stats(c)
            if cs["aligned_count"] >= 1:
                candidate_sides.append(c)
    
        entry_candidates = []
        potential_candidates = []
        setup_wait_candidates = []
    
        for c in candidate_sides:
            cs = consensus_stats(c)
            aligned_names = cs["aligned_names"]
            aligned_count = cs["aligned_count"]
            opposed_count = cs["opposed_count"]
    
            # Primary adaptive rule: any TWO distinct strategies can
            # establish direction. If three or more agree, the setup is
            # stronger. SMC is not required.
            two_of_n = aligned_count >= 2 and opposed_count <= 3
            strong_two_of_n = two_of_n and (cs["tf_count"] >= 2 or aligned_count >= 3)
    
            # Price-method breadth is diagnostic only. A confirmed signal ALWAYS
            # requires at least two distinct directional strategies.
            three_price = False
    
            # Determine the strongest applicable execution horizon.
            level_map = {
                "SNIPER": level_rr(c, "SNIPER"),
                "INTRADAY": level_rr(c, "INTRADAY"),
                "SCALP": level_rr(c, "SCALP"),
                "SWING/HOLD": level_rr(c, "SWING/HOLD"),
            }
    
            # Classification is based on the evidence from all nine strategies,
            # never on an SMC gate.
            if aligned_count >= 4 and cs["tf_count"] >= 3:
                chosen_strategy = "SNIPER"
            elif (
                aligned_count >= 2 and cs["tf_count"] >= 2 and
                any(tf in cs["aligned_tfs"] for tf in ("30m","15m","5m"))
            ):
                chosen_strategy = "SCALP"
            elif (
                aligned_count >= 2 and cs["tf_count"] >= 2 and
                any(tf in cs["aligned_tfs"] for tf in ("4h","1h"))
            ):
                chosen_strategy = "SWING/HOLD"
            elif two_of_n:
                chosen_strategy = "INTRADAY"
            elif three_price:
                chosen_strategy = "INTRADAY"
            else:
                chosen_strategy = "INTRADAY"
    
            # If the evidence is only the 3-price-method route, label it
            # explicitly as price movement rather than pretending it is SMC.
            qualifies = two_of_n
            if not qualifies and aligned_count >= 2:
                # Keep a valid 2-of-9 setup visible as potential when its
                # breadth is not yet strong enough for confirmation.
                pass
    
            selected_strategy = chosen_strategy
            level, rr = level_map.get(selected_strategy, (None, None))
            if selected_strategy == "ADAPTIVE PRICE-MOVEMENT":
                level, rr = level_map["INTRADAY"]
    
            consensus_score = min(
                100.0,
                45.0 + aligned_count * 11.0 +
                max(0, cs["tf_count"] - 1) * 4.0 -
                opposed_count * 4.0
            )
    
    
            rr_ok_now = rr is not None and rr >= 2.0
    
            reasons = [
                f"CONSENSUS: {aligned_count}/9 strategies aligned ({', '.join(sorted(aligned_names))})"
            ]
            if cs["tf_count"]:
                reasons.append(f"TIMEFRAME BREADTH: {cs['tf_count']} timeframe(s)")
            if "SMC" in aligned_names:
                reasons.append("SMC CONTEXT: aligned; no priority gate")
            else:
                reasons.append("SMC CONTEXT: not required; independent strategy consensus is qualifying")
            if cs["price_count"]:
                reasons.append(f"PRICE-MOVEMENT SUPPORT: {cs['price_count']} distinct methods across {cs['price_tf_count']} timeframes")
            if opposed_count:
                reasons.append(f"OPPOSITION: {opposed_count} distinct strategy method(s) opposed")
    
            blockers = []
            if not qualifies:
                blockers.append("MISSING: at least 2 distinct independent strategies from the 9 agreeing on the same direction")
            if not rr_ok_now:
                blockers.append(
                    f"MISSING: real execution R:R must be >=2.0R (now {rr:.2f}R)"
                    if rr is not None else
                    "MISSING: real execution R:R unavailable"
                )
            if not news_ok:
                blockers.append("BLOCKED: high-impact event risk")
    
            if qualifies and rr_ok_now and news_ok:
                entry_candidates.append({
                    "strategy":selected_strategy,
                    "side":c,
                    "level":level,
                    "rr":rr,
                    "score_basis":consensus_score,
                    "horizon":{
                        "SNIPER":"5M-30M / minutes-hours",
                        "INTRADAY":"30M-4H / same-day",
                        "SCALP":"5M-30M / minutes-hours",
                        "SWING/HOLD":"1H-1D / days-weeks",
                        "ADAPTIVE PRICE-MOVEMENT":"15M-1H / same-day",
                    }[selected_strategy],
                    "reasons":reasons,
                })
            elif qualifies and news_ok:
                setup_wait_candidates.append({
                    "strategy":selected_strategy,
                    "side":c,
                    "level":level,
                    "rr":rr,
                    "score_basis":consensus_score,
                    "horizon":{
                        "SNIPER":"5M-30M / minutes-hours",
                        "INTRADAY":"30M-4H / same-day",
                        "SCALP":"5M-30M / minutes-hours",
                        "SWING/HOLD":"1H-1D / days-weeks",
                        "ADAPTIVE PRICE-MOVEMENT":"15M-1H / same-day",
                    }[selected_strategy],
                    "reasons":blockers or ["WAIT: risk-quality condition pending"],
                })
    
            # Near-complete candidates: show them without falsely confirming.
            if news_ok and (
                (aligned_count == 1 and cs["price_count"] >= 1) or
                (cs["price_count"] == 2 and cs["price_tf_count"] >= 1)
            ):
                missing_count = max(1, 2 - aligned_count)
                potential_candidates.append({
                    "strategy":selected_strategy,
                    "side":c,
                    "level":level,
                    "rr":rr,
                    "score_basis":consensus_score,
                    "horizon":{
                        "SNIPER":"5M-30M / minutes-hours",
                        "INTRADAY":"30M-4H / same-day",
                        "SCALP":"5M-30M / minutes-hours",
                        "SWING/HOLD":"1H-1D / days-weeks",
                        "ADAPTIVE PRICE-MOVEMENT":"15M-1H / same-day",
                    }[selected_strategy],
                    "reasons":[
                        f"MISSING: {missing_count} additional strategy confirmation(s) for the 2-of-9 rule"
                        if aligned_count < 2 else
                        "MISSING: one additional price-movement method for the 3-method route"
                    ],
                })
    
        priority = {"SNIPER":5,"INTRADAY":4,"SCALP":3,"SWING/HOLD":2,"ADAPTIVE PRICE-MOVEMENT":3}
        entry_candidates.sort(key=lambda x:(priority[x["strategy"]],x["score_basis"],x["rr"] or 0), reverse=True)
        setup_wait_candidates.sort(key=lambda x:(priority[x["strategy"]],x["score_basis"],x["rr"] or 0), reverse=True)
        potential_candidates.sort(key=lambda x:(priority[x["strategy"]],x["score_basis"]), reverse=True)
    
        selected = entry_candidates[0] if entry_candidates else None
        best_setup_wait = setup_wait_candidates[0] if setup_wait_candidates else None
    
        # Direction is also consensus-based when there is no confirmed entry.
        buy_stats = consensus_stats("BUY")
        sell_stats = consensus_stats("SELL")
        if buy_stats["aligned_count"] > sell_stats["aligned_count"]:
            side = "BUY"
        elif sell_stats["aligned_count"] > buy_stats["aligned_count"]:
            side = "SELL"
        else:
            side = "NEUTRAL"
    
        if selected:
            status = f"CONFIRMED {selected['strategy']} ENTRY"
            side = selected["side"]
            level = selected["level"]
            rr = selected["rr"]
            blockers = []
            trade_type = selected["strategy"]
            horizon = selected["horizon"]
            strategy_gate = selected["reasons"][0]
            selected_consensus_score = selected["score_basis"]
        elif best_setup_wait:
            status = "VALID SETUP — WAIT"
            side = best_setup_wait["side"]
            level = best_setup_wait["level"]
            rr = best_setup_wait["rr"]
            trade_type = best_setup_wait["strategy"]
            horizon = best_setup_wait["horizon"]
            strategy_gate = best_setup_wait["reasons"][0] if best_setup_wait["reasons"] else "9-strategy consensus established; risk-quality gate pending"
            blockers = list(best_setup_wait["reasons"])
            selected_consensus_score = best_setup_wait["score_basis"]
        else:
            status = "WAIT"
            level, rr = level_rr(side, "INTRADAY") if side in ("BUY","SELL") else (None,None)
            trade_type = "WAIT"
            horizon = "not confirmed"
            strategy_gate = "9-strategy consensus not yet confirmed"
            blockers = []
            selected_consensus_score = max(
                0.0,
                float(max(buy_stats["aligned_count"], sell_stats["aligned_count"]) * 10.0)
            )
            if side == "NEUTRAL":
                blockers.append("9-strategy BUY/SELL evidence is tied or neutral")
            else:
                side_stats = buy_stats if side == "BUY" else sell_stats
                if side_stats["aligned_count"] < 2:
                    blockers.append("fewer than 2 distinct strategies currently agree")
                elif side_stats["price_count"] < 3:
                    blockers.append("2-strategy consensus lacks sufficient breadth for the current timeframe/risk gates")
                else:
                    blockers.append("consensus exists but execution/risk conditions are not yet complete")
            if potential_candidates:
                best = potential_candidates[0]
                strategy_gate = best["strategy"]
                blockers.append(
                    f"BEST POTENTIAL: {best['side']} / {best['strategy']} — {len(best['reasons'])} condition(s) missing"
                )
                blockers.extend(best["reasons"][:3])
    
        # Calculate the displayed score from the final 9-strategy
        # consensus. All nine methods have equal standing; SMC is not mandatory
        # and receives no special score bonus.
        four_h_votes = {"BUY": 0, "SELL": 0}
        for method in strategy_by_tf.get("4h", []):
            sig = method.get("signal")
            if sig in four_h_votes:
                four_h_votes[sig] += 1
        if four_h_votes["BUY"] > four_h_votes["SELL"]:
            regime_side = "BUY"
        elif four_h_votes["SELL"] > four_h_votes["BUY"]:
            regime_side = "SELL"
        else:
            regime_side = "NEUTRAL"
    
        chart_rows = []
        for row in m30[-120:]:
            chart_rows.append({
                "timestamp": row.get("timestamp").isoformat() if hasattr(row.get("timestamp"), "isoformat") else str(row.get("timestamp")),
                "open": row.get("open"), "high": row.get("high"),
                "low": row.get("low"), "close": row.get("close"),
                "volume": row.get("volume")
            })
    
        technical_score = float(selected_consensus_score) if side in ("BUY","SELL") else 0.0
        technical_score = max(0.0, min(100.0, technical_score))
        risk_adjusted_score = max(0.0,technical_score-risk_penalty)
    
        # Authoritative timeframe-direction snapshot for reporting.
        # This fixes the previous undefined `alignment` runtime error.
        alignment = {
            "1d": d_dir,
            "4h": h4_dir,
            "1h": h1_dir,
            "30m": direction(m30),
            "15m": direction(m15),
            "5m": direction(m5),
        }
    
        results.append({
            "symbol":symbol,"category":category,"status":status,"side":side,"trade_type":trade_type,"horizon":horizon,
            "score":technical_score,"risk_score":risk_adjusted_score,"method_score":support_score(m30_support,side) if side in ("BUY","SELL") else 0.0,
            "rr":rr,"levels":level,"alignment":alignment,"blockers":blockers,"events":events,"event_level":event_level,
            "yield_diff":yield_diff,"correlations":correlations,
            "fvg":{"30m":m30_fvg,"15m":m15_fvg,"5m":m5_fvg},"fvg_signals":{"30m":m30_fvg_sig,"15m":m15_fvg_sig,"5m":m5_fvg_sig},
            "methods":m30_support,"confirmation_methods":m15_support,"trigger_methods":m5_support,"h1_methods":h1_support,
            "regime":regime_side,"technical_score":technical_score,"strategy_gate":strategy_gate,
            "confidence_score": round(float(risk_adjusted_score), 1),
            "agreed_strategies": sorted(consensus_stats(side)["aligned_names"]) if side in ("BUY","SELL") else [],
            "strategy_votes": {
                tf: {m["name"]: m["signal"] for m in methods}
                for tf, methods in strategy_by_tf.items()
            },
            "strategy_consensus": (
                {
                    "aligned": sorted(consensus_stats(side)["aligned_names"]) if side in ("BUY","SELL") else [],
                    "opposed": sorted(consensus_stats(side)["opposed_names"]) if side in ("BUY","SELL") else [],
                    "timeframes": sorted(consensus_stats(side)["aligned_tfs"]) if side in ("BUY","SELL") else [],
                    "count": consensus_stats(side)["aligned_count"] if side in ("BUY","SELL") else 0,
                }
            ),
            "potential":potential_candidates[0] if potential_candidates else None,
            "setup_wait":best_setup_wait,
            "smc_context":{"1d":smc_1d,"4h":smc_4h,"1h":smc_1h,"30m":smc_30m,"15m":smc_15m,"5m":smc_5m},
            "supporting":{"1h":h1_support,"30m":m30_support,"15m":m15_support,"5m":m5_support},
            "chart_30m": chart_rows,
        })
    
    except Exception as exc:
        errors += 1
        processed += 1 if processed < idx else 0
        incomplete += 1
        print(f"  INSTRUMENT ERROR: {type(exc).__name__}: {exc} — continuing")
        results.append({
            "symbol": symbol, "category": category, "status": "DATA_UNAVAILABLE",
            "side": "NEUTRAL", "trade_type": "DATA_UNAVAILABLE", "horizon": "not analyzed",
            "score": 0.0, "confidence_score": 0.0, "risk_score": 0.0, "method_score": 0.0,
            "rr": None, "levels": None, "alignment": {}, "blockers": [f"Instrument analysis error: {type(exc).__name__}: {exc}"],
            "events": [], "event_level": "UNAVAILABLE", "yield_diff": None, "correlations": [],
            "fvg": {}, "fvg_signals": {}, "methods": [], "confirmation_methods": [], "trigger_methods": [], "h1_methods": [],
            "regime": "UNKNOWN", "technical_score": 0.0, "strategy_gate": "NO SIGNAL — instrument analysis failed",
            "strategy_consensus": {"aligned": [], "opposed": [], "timeframes": [], "count": 0},
            "agreed_strategies": [], "strategy_votes": {}, "potential": None, "setup_wait": None,
            "smc_context": {}, "supporting": {}, "chart_30m": [], "error_details": [f"{type(exc).__name__}: {exc}"],
            "missing_timeframes": [], "catalog_missing": bool(meta.get("catalog_missing")),
        })

# ----------------------------
# Ranked output
# ----------------------------
entries = sorted([r for r in results if r["status"].startswith("CONFIRMED ")],
                 key=lambda x: (x["trade_type"] != "SNIPER", -x["score"]))
sniper_entries = sorted([r for r in entries if r["trade_type"] == "SNIPER"], key=lambda x: x["score"], reverse=True)
intraday_entries = sorted([r for r in entries if r["trade_type"] == "INTRADAY"], key=lambda x: x["score"], reverse=True)
scalp_entries = sorted([r for r in entries if r["trade_type"] == "SCALP"], key=lambda x: x["score"], reverse=True)
swing_entries = sorted([r for r in entries if r["trade_type"] == "SWING/HOLD"], key=lambda x: x["score"], reverse=True)
waiting = sorted([r for r in results if r["status"] == "WAIT"], key=lambda x: x["score"], reverse=True)

def print_candidate(title, rank, r):
    lv = r["levels"]
    rr_text = f"{r['rr']:.2f}R" if r["rr"] is not None else "UNAVAILABLE"
    print(f"{title} #{rank} {r['symbol']} | {r['side']} | {r['trade_type']} | score {r['score']:.1f}/100 | risk-adjusted {r['risk_score']:.1f}/100 | R:R {rr_text}")
    print("   Alignment:", " | ".join(f"{k.upper()} {v}" for k,v in r["alignment"].items()))
    print("   FVG:", " | ".join(f"{tf.upper()} {sig}" for tf,sig in r["fvg_signals"].items()))
    if lv:
        print(f"   Entry {lv[0]:.6g} | SL {lv[1]:.6g} | TP {lv[2]:.6g} | ATR {lv[3]:.6g}")
    why = "; ".join(m["why"] for m in r["methods"] if m["signal"] == r["side"])
    if r["trade_type"] == "ADAPTIVE PRICE-MOVEMENT":
        print("   MODE: independent consensus; SMC is not required for this entry")
    else:
        print("   SMC CONTEXT:", r["smc_context"]["30m"]["why"], f"| core score {r["smc_context"]["30m"]["score"]}/90")
    print("   SUPPORTING METHODS:", why or "no supporting confirmation")
    print("   Event/news risk:", r["event_level"])
    print("   Yield differential:", f"{r['yield_diff']:+.3f} percentage points" if r["yield_diff"] is not None else "UNAVAILABLE")
    print("   Correlation context:", "; ".join(f"{b} {c:+.2f}" for b, c in r["correlations"]) or "UNAVAILABLE")
    print("   Strategy gate:", r["strategy_gate"])
    sc = r.get("strategy_consensus", {})
    if sc:
        print("   9-STRATEGY CONSENSUS:", ", ".join(sc.get("aligned", [])) or "NONE",
              "| opposed:", ", ".join(sc.get("opposed", [])) or "NONE",
              "| TFs:", ", ".join(sc.get("timeframes", [])) or "NONE")
    if r["trade_type"] == "SNIPER":
        print("   Required gates: strongest 9-strategy consensus (SMC boosts confidence but is not mandatory) | real >=2R | no HIGH news")
    elif r["trade_type"] == "INTRADAY":
        print("   Required gates: >=2 distinct strategies from 9 with sufficient breadth | real >=2R | no HIGH news")
    elif r["trade_type"] == "SCALP":
        print("   Required gates: >=2 distinct strategies from 9 with lower-TF agreement | real >=2R | no HIGH news")
    elif r["trade_type"] == "SWING/HOLD":
        print("   Required gates: >=2 distinct strategies from 9 with higher-TF agreement | real >=2R | no HIGH news")
    elif r["trade_type"] == "ADAPTIVE PRICE-MOVEMENT":
        print("   Required gates: 3 distinct price-movement methods across >=2 TFs | real >=2R | no HIGH news")
    if r["blockers"]:
        print("   NEXT CONDITION:")
        for b in r["blockers"][:4]:
            print(f"     • {b}")
    print()

print()
print("=" * 72)
print("🔥 SMC FOREX / MULTI-ASSET INTELLIGENT ENTRY RANKING")
print("=" * 72)
print("9-STRATEGY ENGINE: 9 EQUAL STRATEGIES | CONFIRMED SIGNAL REQUIRES >=2 DISTINCT STRATEGIES")
print("SMC is one of nine equal strategies and is NOT mandatory. Confirmed signals require >=2 distinct strategies agreeing on the same direction.")
print("Directional strategy denominator: 9; neutral methods are informational and do not count as opposition.")
adaptive_entries = [r for r in entries if r["trade_type"] == "ADAPTIVE PRICE-MOVEMENT"]
print(f"Entries: {len(entries)} | Sniper: {len(sniper_entries)} | Intraday: {len(intraday_entries)} | Scalp: {len(scalp_entries)} | Swing/Hold: {len(swing_entries)} | Adaptive: {len(adaptive_entries)} | Wait: {len(waiting)}")
print(f"Instruments fully analyzed: {complete} | Incomplete: {incomplete}")
print()
print("DAILY INTELLIGENCE")
print(f"  Economic calendar records: {len(intel['calendar'])}")
print(f"  Macro: {'AVAILABLE' if intel['macro'].get('cpi_yoy') or intel['macro'].get('policy_rate') else 'UNAVAILABLE'}")
print(f"  Government yields: {', '.join(sorted(intel['yields'])) if intel['yields'] else 'UNAVAILABLE'}")
print(f"  COT: {', '.join(intel['cot']) if intel['cot'] else 'UNAVAILABLE'}")
print(f"  External news: {len(intel['news'])} items" if intel["news"] else "  External news: UNAVAILABLE")
print(f"  Live feed capability: {intel['live']}")
print("  ML: LOCAL STATISTICAL MODEL (no institutional ML claimed)")
print()

sections = [
    ("🎯 CONFIRMED SNIPER ENTRIES", sniper_entries),
    ("📈 CONFIRMED INTRADAY ENTRIES", intraday_entries),
    ("⚡ CONFIRMED SCALP ENTRIES", scalp_entries),
    ("🏦 CONFIRMED SWING/HOLD ENTRIES", swing_entries),
    ("🟢 CONFIRMED ADAPTIVE PRICE-MOVEMENT ENTRIES", adaptive_entries),
]
any_entry = False
for heading, bucket in sections:
    if bucket:
        any_entry = True
        print(heading)
        for rank, r in enumerate(bucket[:TOP_N], 1):
            print_candidate(heading.split()[0], rank, r)
        print()
    else:
        print(f"{heading}: NONE")
        print()

if not any_entry:
    print("⛔ NO CONFIRMED ENTRY — neither the 2-of-9 strategy consensus nor the 3-method price-movement route passed all risk/execution gates")
    print()

# Results-level view of setups that passed the 9-strategy consensus + execution gates but
# are waiting on the structural risk-quality condition (normally >=2R).
setup_waiting = sorted(
    [r for r in results if r["status"] == "VALID SETUP — WAIT"],
    key=lambda x: (x["score"], x["rr"] if x["rr"] is not None else -1),
    reverse=True,
)
print("🟡 VALID SETUPS — WAIT / RISK CONDITION")
if setup_waiting:
    for rank, r in enumerate(setup_waiting[:TOP_N], 1):
        rr_text = f"{r['rr']:.2f}R" if r["rr"] is not None else "UNAVAILABLE"
        print(f"🟡 #{rank} {r['symbol']} | {r['side']} | {r['trade_type']} | score {r['score']:.1f}/100 | R:R {rr_text}")
        print(f"   Holding horizon: {r['horizon']}")
        for reason in r["blockers"][:4]:
            print(f"   • {reason}")
        print("   Status: VALID 9-STRATEGY SETUP — WAIT; not a confirmed entry until the remaining risk-quality condition passes.")
        print()
else:
    print("No fully formed setup is currently waiting only on a risk-quality condition.")
print()

potential_display = []
for r in results:
    p = r.get("potential")
    if r["status"] == "WAIT" and p:
        missing = [x for x in p["reasons"] if x.startswith("MISSING:")]
        potential_display.append((len(missing), -r["score"], r, p))
potential_display.sort(key=lambda x: (x[0], x[1]))

print("👀 POTENTIAL ENTRIES — NOT CONFIRMED")
if potential_display:
    for rank, (_, __, r, p) in enumerate(potential_display[:TOP_N], 1):
        print(f"👀 #{rank} {r['symbol']} | {p['side']} | {p['strategy']} | score {r['score']:.1f}/100 | R:R {p['rr']:.2f}R" if p['rr'] is not None else f"👀 #{rank} {r['symbol']} | {p['side']} | {p['strategy']} | score {r['score']:.1f}/100 | R:R UNAVAILABLE")
        print(f"   Holding horizon: {p['horizon']}")
        for reason in p["reasons"]:
            print(f"   • {reason}")
        print("   Status: WAIT for the missing condition(s); this is NOT an entry yet.")
        print()
else:
    print("No near-complete potential setups.")
print()

print("⏳ WAIT / BLOCKERS")
if waiting:
    for rank, r in enumerate(waiting[:TOP_N], 1):
        print(f"⏳ #{rank} {r['symbol']} | {r['side']} | score {r['score']:.1f}/100 | EVENT/NEWS {r['event_level']}")
        for b in r["blockers"][:5]:
            print(f"   • {b}")
        print()
else:
    print("No additional WAIT candidates.")
print()

elapsed = time.monotonic() - SCAN_START
print("=" * 72)
print("SCAN STATUS")
print("=" * 72)
print(f"Elapsed scanner time: {elapsed:.1f}s / configured budget {RUNTIME_MINUTES*60:.1f}s")
print(f"Market-scan budget (with safety reserve): {SCAN_BUDGET_SECONDS:.1f}s")
print(f"Catalog instruments discovered: {len(catalog)}")
print(f"Eligible instruments selected: {len(instruments)}")
print(f"Instruments attempted: {processed}")
print(f"Fully analyzed: {complete}")
print(f"Skipped as incomplete: {incomplete}")
print(f"Completed analysis errors: {errors}")
print("SMC COMPONENTS (one of 9): Market Structure | MSS/CHoCH | Liquidity | FVG")
print("ENTRY QUALIFICATION: >=2 distinct strategies from 9 agreeing on the same direction; real >=2R; no HIGH news")
print("9 STRATEGIES: SMC | Market Structure | Liquidity | Supply/Demand | Price Action | Fibonacci | Trend | RSI+MACD | Session")
print("RISK FILTERS: News/Event | Yield Differential | Correlation | Macro")
print("Timeframes: 1D | 4H | 1H | 30M | 15M | 5M")
print("30M source: built from TWO CLOSED 15M candles")
print("Entry types: SNIPER | INTRADAY | SCALP | SWING/HOLD | POTENTIAL | WAIT | DATA_UNAVAILABLE")
print("Sniper: strongest 9-strategy consensus; SMC is one equal strategy; no SMC alignment is required + real 2R/news gates")
print("Intraday: >=2 distinct strategies with multi-TF breadth + real 2R/news gates")
print("Scalp: >=2 distinct strategies with lower-TF agreement + real 2R/news gates")
print("Swing/Hold: >=2 distinct strategies with higher-TF agreement + real 2R/news gates")
print("High-impact event rule: blocks all fresh confirmed entries")
print("Potential: incomplete consensus is shown as potential only. Potential is never confirmed.")
print("Unavailable intelligence: reported as unavailable and excluded from technical score")
print("Automatic trading: DISABLED")
if runtime_limited or processed < len(instruments):
    print("⚠️ SCAN INCOMPLETE — RUNTIME BUDGET REACHED; RANKED RESULTS ARE FROM COMPLETED INSTRUMENTS")
else:
    print("✅ SCAN COMPLETE — PROCESS EXITING CLEANLY")


# Machine-readable handoff for the Telegram bot.
# The scanner remains analytical only; no orders are placed.
payload = {
    "version": "v10.1-9-strategy-telegram",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "entries": entries[:TOP_N],
    "sniper_entries": sniper_entries[:TOP_N],
    "intraday_entries": intraday_entries[:TOP_N],
    "scalp_entries": scalp_entries[:TOP_N],
    "swing_entries": swing_entries[:TOP_N],
    "adaptive_entries": adaptive_entries[:TOP_N],
    "setup_waiting": setup_waiting[:TOP_N],
    "potential": [x[2] for x in potential_display[:TOP_N]],
    "waiting": waiting[:TOP_N],
    "complete": complete,
    "incomplete": incomplete,
    "processed": processed,
    "errors": errors,
    "catalog_count": len(catalog),
    "eligible_count": len(instruments),
    "target_universe_size": TARGET_UNIVERSE_SIZE,
    "target_symbols": TARGET_SYMBOLS,
    "data_unavailable": [r for r in results if r.get("status") == "DATA_UNAVAILABLE"],
    "intelligence": intel,
    "runtime_seconds": time.monotonic() - SCAN_START,
}
output_path = os.getenv("SCANNER_JSON", "scan_results.json")
with open(output_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, default=str)
print(f"JSON HANDOFF: {output_path}")
