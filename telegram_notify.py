#!/usr/bin/env python3
"""
Telegram notifier for the LSE Smart Signal Scanner.

Required:
  TELEGRAM_BOT_TOKEN

Optional:
  TELEGRAM_CHAT_ID

If TELEGRAM_CHAT_ID is not supplied, the script discovers the most recent
incoming chat through Telegram getUpdates. Before the first run, send /start
(or any message) to the bot from the chat that should receive reports.

No Telegram token or chat ID is stored in this file.
"""

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 3900


def api_call(token, method, payload=None, timeout=20):
    url = f"{API_ROOT}/bot{token}/{method}"
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=body, headers=headers, method="POST" if body else "GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram network error: {exc}") from exc

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected {method}: {result.get('description', result)}")
    return result.get("result")


def discover_chat_id(token):
    updates = api_call(token, "getUpdates", {"limit": 20, "timeout": 0})
    candidates = []

    for update in updates or []:
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("id") is None:
            continue

        candidates.append(
            (
                int(update.get("update_id", 0)),
                str(chat["id"]),
                str(chat.get("type", "")),
                str(chat.get("title") or chat.get("username") or chat.get("first_name") or ""),
            )
        )

    if not candidates:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is not configured and Telegram returned no incoming "
            "updates. Open the target bot and send /start once, then rerun the workflow."
        )

    candidates.sort(key=lambda x: x[0])
    _, chat_id, chat_type, label = candidates[-1]
    print(f"Telegram chat auto-discovered: type={chat_type or 'unknown'} label={label or 'unknown'}")
    return chat_id


def render_report(data):
    def num(value, decimals=5):
        if value is None:
            return "WAIT"
        try:
            return f"{float(value):.{decimals}f}"
        except Exception:
            return str(value)

    def rr(value):
        if value is None:
            return "DEVELOPING"
        try:
            return f"{float(value):.2f}R"
        except Exception:
            return str(value)

    def render(item):
        return (
            f"{item.get('symbol', '?')} | {item.get('side', 'WAIT')}\n"
            f"Style: {item.get('opportunity_style') or item.get('opportunity_class') or 'OPPORTUNITY'} | "
            f"TF: {item.get('execution_timeframe') or ', '.join(item.get('signal_timeframes', [])) or '5M'}\n"
            f"Status: {item.get('status') or item.get('entry_state') or 'WAIT'} | "
            f"Score: {num(item.get('strategy_score', item.get('score')), 1)}\n"
            f"Entry/Trigger: {num(item.get('entry', item.get('trigger', item.get('entry_trigger'))))} | "
            f"SL: {num(item.get('sl', item.get('stop_loss')))}\n"
            f"TP: {num(item.get('tp', item.get('take_profit')))} | "
            f"R:R: {rr(item.get('rr', item.get('risk_reward')))}"
        )

    top_symbols = {"XAU/USD", "EUR/USD", "BTC/USD"}
    deep = []
    for key in ("sniper_opportunities", "entries", "potential", "waiting", "setup_waiting"):
        for item in data.get(key, []) or []:
            if isinstance(item, dict) and item.get("symbol") in top_symbols:
                deep.append(item)

    priority = {"CONFIRMED_ENTRY": 0, "POTENTIAL": 1, "WAIT": 2}
    chosen = {}

    for item in deep:
        sym = item.get("symbol")
        if not sym:
            continue
        try:
            deep_score = float(item.get("strategy_score", 0.0) or 0.0)
            execution_quality = float(item.get("execution_quality_score", 0.0) or 0.0)
            strategy_count = int(item.get("confirmed_strategy_count", 0) or 0)
        except (TypeError, ValueError):
            continue

        rr_value = item.get("rr")
        tp_value = item.get("take_profit")
        tp_status = str(item.get("tp_status", ""))

        try:
            rr_ok = rr_value is not None and float(rr_value) >= 2.0
        except (TypeError, ValueError):
            rr_ok = False

        strict_top = (
            sym in top_symbols
            and item.get("side") in ("BUY", "SELL")
            and deep_score >= 85.0
            and execution_quality >= 85.0
            and strategy_count >= 3
            and rr_ok
            and tp_value is not None
            and tp_status == "VALID_STRUCTURAL_RR"
        )
        if not strict_top:
            continue

        status = str(item.get("status", "WAIT"))
        old = chosen.get(sym)
        if old is None or priority.get(status, 3) < priority.get(str(old.get("status", "WAIT")), 3):
            chosen[sym] = item

    fast = sorted(
        (data.get("fast_filter") or {}).get("candidates", []) or [],
        key=lambda x: float(x.get("score", 0) or 0),
        reverse=True,
    )[:8]

    lines = [
        "⚡ LSE SMART SCANNER",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Generated: {data.get('generated_at', '')}",
        "",
        "⚡ FAST SCANNED — TOP 8",
    ]

    if fast:
        for i, item in enumerate(fast, 1):
            lines.append(
                f"#{i} {item.get('symbol', '?')} | {item.get('side', 'WAIT')} | "
                f"Score {float(item.get('score', 0) or 0):.1f}/100"
            )
    else:
        lines.append("No fast candidates.")

    lines += ["", "🎯 TOP OPPORTUNITIES — DEEP", "XAU/USD • EUR/USD • BTC/USD", ""]

    for sym in ("XAU/USD", "EUR/USD", "BTC/USD"):
        if sym in chosen:
            lines.append(render(chosen[sym]))
            lines.append("")

    if not any(sym in chosen for sym in top_symbols):
        lines.append("No qualifying/developing top opportunity detected.")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "Automatic trading: DISABLED",
        "TOP: deep score >=85 + execution quality >=85 + >=3/9 strategies + genuine TP + >=2R.",
        "FAST: >=55. Automatic trading: DISABLED.",
    ]
    return "\n".join(lines)


def chunk_message(message):
    chunks = []
    while len(message) > MAX_MESSAGE_LENGTH:
        cut = message.rfind("\n", 0, MAX_MESSAGE_LENGTH)
        if cut <= 0:
            cut = MAX_MESSAGE_LENGTH
        chunks.append(message[:cut])
        message = message[cut:].lstrip("\n")
    if message:
        chunks.append(message)
    return chunks


def send_message(token, chat_id, text):
    result = api_call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )
    return result


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not configured. Add the bot token as a GitHub Actions secret."
        )

    report_path = os.environ.get("SCANNER_JSON", "scan_results.json")
    with open(report_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not chat_id:
        chat_id = discover_chat_id(token)

    message = render_report(data)
    chunks = chunk_message(message)

    for chunk in chunks:
        send_message(token, chat_id, chunk)
        time.sleep(0.5)

    print(f"Telegram report sent successfully: {len(chunks)} message(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"TELEGRAM SEND ERROR: {exc}", file=sys.stderr)
        raise
