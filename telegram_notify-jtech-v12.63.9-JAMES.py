꧁𖡹⃢JAMES𖣔⚠︎꧂
#!/usr/bin/env python3
import json, os, time, urllib.parse, urllib.request

BOT = os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID","").strip()
JSON_PATH = os.environ.get("SCANNER_JSON","scan_results.json")
CHANNEL_URL = "https://t.me/marketwitches"

if not BOT:
    raise SystemExit("TELEGRAM_BOT_TOKEN is not configured.")

def api(method, data=None):
    url = f"https://api.telegram.org/bot{BOT}/{method}"
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

def discover_chat():
    if CHAT:
        return CHAT
    try:
        result = api("getUpdates", {"timeout": 1, "limit": 20})
        updates = result.get("result", [])
        for u in reversed(updates):
            msg = u.get("message") or u.get("channel_post") or u.get("edited_message")
            if msg and msg.get("chat", {}).get("id") is not None:
                return str(msg["chat"]["id"])
    except Exception as exc:
        print(f"Telegram chat discovery failed: {type(exc).__name__}: {exc}")
    raise SystemExit("TELEGRAM_CHAT_ID is not configured and no incoming Telegram chat could be discovered.")

def esc(v):
    s = str(v if v is not None else "—")
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))

def num(v, digits=5):
    if v is None:
        return "WAIT"
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return esc(v)

def side_icon(side):
    return "🟢" if str(side).upper() == "BUY" else "🔴" if str(side).upper() == "SELL" else "🟡"

def strategies(r):
    vals = r.get("confirmed_strategies") or r.get("strategies") or []
    if isinstance(vals, str):
        return vals
    return ", ".join(str(x) for x in vals) if vals else "—"

def signal_card(r, mode="CONFIRMED"):
    side=str(r.get("side","WAIT")).upper()
    symbol=str(r.get("symbol","UNKNOWN"))
    status=str(r.get("status",""))
    trade=str(r.get("trade_type") or r.get("opportunity_class") or "UNSPECIFIED")
    tf=str(r.get("execution_timeframe") or r.get("position_timeframe") or "UNSPECIFIED").upper()
    rr=r.get("rr")
    rr_txt=f"{float(rr):.2f}R" if rr is not None else "WAIT"
    icon=side_icon(side)
    title="INSTITUTIONAL ENGINE" if mode=="CONFIRMED" else "INSTITUTIONAL WATCH"
    lv=r.get("levels")
    if isinstance(lv,(list,tuple)) and len(lv)>=3:
        entry, sl, tp = lv[0], lv[1], lv[2]
    else:
        entry, sl, tp = r.get("entry_price"), r.get("stop_loss"), r.get("take_profit")
    event=(r.get("event_alignment") or {}).get("state","UNAVAILABLE")
    news=r.get("rss_news_risk","UNAVAILABLE")
    smc=r.get("smc_base_confirmed")
    smc_txt="CONFIRMED" if smc is True else "PENDING" if smc is False else "—"
    lines=[
        f"{icon} <b>{title} • {side} • JTECH</b>",
        f"<b>{esc(symbol)}</b> — {esc(status or mode)}",
        f"Type: <b>{esc(trade)}</b> | TF: <b>{esc(tf)}</b>",
        f"Entry: <code>{num(entry)}</code> | SL: <code>{num(sl)}</code> | TP: <code>{num(tp)}</code>",
        f"R:R: <b>{esc(rr_txt)}</b> | Strategy score: <b>{float(r.get('strategy_score',0) or 0):.1f}/100</b>",
        f"Strategies: <b>{esc(strategies(r))}</b>",
        f"SMC base: <b>{smc_txt}</b>",
        f"News/Event: <b>{esc(event)}</b> | RSS: {esc(news)}",
    ]
    if mode!="CONFIRMED":
        blockers=r.get("blockers") or (r.get("potential") or {}).get("reasons") or []
        if blockers:
            lines.append("Waiting: " + esc(" | ".join(map(str, blockers[:2]))))
    return "\n".join(lines)

def fast_watch(r):
    side=str(r.get("side","WAIT")).upper()
    icon=side_icon(side)
    symbol=r.get("symbol","UNKNOWN")
    score=float(r.get("score",r.get("strategy_score",0)) or 0)
    return f"{icon} <b>{esc(symbol)} {side}</b> — FAST WATCH | Score {score:.1f}/100"

def chunk_lines(lines, limit=3900):
    chunks=[]; cur=""
    for line in lines:
        candidate=(cur+"\n"+line).strip()
        if len(candidate)>limit and cur:
            chunks.append(cur); cur=line
        else:
            cur=candidate
    if cur: chunks.append(cur)
    return chunks

with open(JSON_PATH, encoding="utf-8") as fh:
    d=json.load(fh)

confirmed=d.get("entries") or []
# Keep only real confirmed entries here. Fast ranking is never presented as a trade.
confirmed=sorted(confirmed, key=lambda r: float(r.get("strategy_score",0) or 0), reverse=True)

fast=(d.get("fast_filter") or {}).get("candidates") or []
fast=sorted(fast, key=lambda r: float(r.get("score",0) or 0), reverse=True)[:8]

top=[]
for r in confirmed:
    if r.get("symbol") in {"XAU/USD","EUR/USD","BTC/USD"}:
        top.append(r)
top=top[:5]

waiting=d.get("waiting") or []
waiting=sorted(waiting, key=lambda r: float(r.get("strategy_score",0) or 0), reverse=True)[:5]

lines=[
    "🏦 <b>INSTITUTIONAL ENGINE • JTECH</b>",
    "⚡ <b>LSE SMART SIGNAL SCANNER</b>",
    f"Generated: <code>{esc(d.get('generated_at','—'))}</code>",
    f"🔗 <a href=\"{CHANNEL_URL}\">JTECH Telegram Channel</a>",
    "",
]

if confirmed:
    lines += ["🟢 <b>CONFIRMED SIGNALS</b>"]
    for r in confirmed[:8]:
        lines += [signal_card(r), "────────────"]
else:
    lines += [
        "🟡 <b>NO CONFIRMED ENTRY</b>",
        "The scanner found candidates, but none has passed all confirmation gates.",
        ""
    ]

lines += ["👀 <b>FAST MARKET WATCH — NOT ENTRIES</b>"]
if fast:
    lines += [fast_watch(r) for r in fast]
else:
    lines.append("No fast candidates.")

if top:
    lines += ["", "🎯 <b>TOP OPPORTUNITY DEEP WATCH</b>"]
    for r in top:
        lines += [signal_card(r, mode="WATCH"), "────────────"]
elif not confirmed:
    lines += ["", "🎯 <b>TOP OPPORTUNITY DEEP WATCH</b>", "No confirmed deep opportunity."]

if waiting:
    lines += ["", "⏳ <b>WAITING / TRIGGER PENDING</b>"]
    for r in waiting[:5]:
        lines += [signal_card(r, mode="WATCH"), "────────────"]

lines += [
    "",
    "ℹ️ <b>Color key:</b> 🟢 BUY  🔴 SELL  🟡 WAIT/WATCH",
    "Automatic trading: <b>DISABLED</b>",
]

chat=discover_chat()
for msg in chunk_lines(lines):
    api("sendMessage", {
        "chat_id": chat,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    time.sleep(0.25)

print(f"Telegram report sent: {len(confirmed)} confirmed | {len(fast)} fast-watch | {len(waiting)} waiting")
