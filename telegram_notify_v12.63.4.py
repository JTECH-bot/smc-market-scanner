#!/usr/bin/env python3
"""
LSE Smart Scanner Telegram notifier.

Important:
- FAST ranking and DEEP qualification are different pipelines.
- This notifier never turns a FAST score into a trade.
- For XAU/USD, EUR/USD and BTC/USD it reports the best available DEEP
  record even when the record is WAITING/POTENTIAL, so the Telegram report
  explains the actual blocker instead of saying "No qualifying..." and hiding it.
- TELEGRAM_CHAT_ID is optional; if omitted, the latest incoming Telegram chat
  is discovered through getUpdates.
"""

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 3900
TOP_SYMBOLS = ("XAU/USD", "EUR/USD", "BTC/USD")


def api_call(token, method, payload=None, timeout=20):
    url = f"{API_ROOT}/bot{token}/{method}"
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(
        url,
        data=body,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {exc.code}: {detail[:1000]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram network error: {exc}") from exc

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API rejected {method}: "
            f"{result.get('description', result)}"
        )
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
                str(
                    chat.get("title")
                    or chat.get("username")
                    or chat.get("first_name")
                    or ""
                ),
            )
        )

    if not candidates:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is not configured and Telegram returned no incoming "
            "updates. Open the bot and send /start once, then rerun the workflow."
        )

    candidates.sort(key=lambda x: x[0])
    _, chat_id, chat_type, label = candidates[-1]
    print(
        f"Telegram chat auto-discovered: "
        f"type={chat_type or 'unknown'} label={label or 'unknown'}"
    )
    return chat_id


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


def collect_deep_records(data):
    records = []

    # These are the scanner's own deep-summary collections.
    for key in (
        "sniper_opportunities",
        "entries",
        "potential",
        "waiting",
        "setup_waiting",
        "broader_potential_setups",
        "waiting_blocker_setups",
    ):
        for item in data.get(key, []) or []:
            if isinstance(item, dict) and item.get("symbol") in TOP_SYMBOLS:
                records.append(item)

    # Also consume analyzed_currency_context when available.
    for item in data.get("analyzed_currency_context", []) or []:
        if isinstance(item, dict) and item.get("symbol") in TOP_SYMBOLS:
            records.append(item)

    # Some scanner versions expose the full records directly.
    for item in data.get("results", []) or []:
        if isinstance(item, dict) and item.get("symbol") in TOP_SYMBOLS:
            records.append(item)

    return records


def best_deep_per_symbol(data):
    # Prefer real directional/developing information over empty NO_ACTION
    # records, then prefer higher score/execution quality.
    status_priority = {
        "CONFIRMED_ENTRY": 0,
        "POTENTIAL_BUY": 1,
        "POTENTIAL_SELL": 1,
        "WAITING_FOR_TRIGGER": 2,
        "SETUP_READY": 3,
        "DEVELOPING": 4,
        "WAIT": 5,
        "NO_ACTION": 6,
    }

    chosen = {}

    for item in collect_deep_records(data):
        symbol = item.get("symbol")
        if symbol not in TOP_SYMBOLS:
            continue

        status = str(item.get("status") or item.get("entry_state") or "WAIT")

        try:
            score = float(item.get("strategy_score", item.get("score", 0)) or 0)
        except Exception:
            score = 0.0

        try:
            execution = float(item.get("execution_quality_score", 0) or 0)
        except Exception:
            execution = 0.0

        try:
            strategies = int(item.get("confirmed_strategy_count", 0) or 0)
        except Exception:
            strategies = 0

        rank = (
            status_priority.get(status, 9),
            -score,
            -execution,
            -strategies,
        )

        old = chosen.get(symbol)
        if old is None or rank < old["_telegram_rank"]:
            copy = dict(item)
            copy["_telegram_rank"] = rank
            chosen[symbol] = copy

    return chosen


def extract_levels(item):
    entry = item.get("entry", item.get("potential_entry"))
    sl = item.get("sl", item.get("stop_loss"))
    tp = item.get("tp", item.get("take_profit"))

    levels = item.get("levels")
    if isinstance(levels, (list, tuple)):
        if len(levels) > 0:
            entry = levels[0]
        if len(levels) > 1:
            sl = levels[1]
        if len(levels) > 2:
            tp = levels[2]

    return entry, sl, tp


def get_reasons(item):
    values = item.get("blockers") or item.get("reasons") or []
    reasons = []

    for value in values:
        if isinstance(value, dict):
            value = value.get("reason") or value.get("message") or str(value)
        value = str(value).strip()

        if value and value not in reasons:
            reasons.append(value)

    return reasons[:4]


def strategy_text(item):
    names = item.get("confirmed_strategies") or []
    if names:
        return ", ".join(names[:6])

    count = int(item.get("confirmed_strategy_count", 0) or 0)
    return f"{count}/9 confirmed"


def render_report(data):
    fast = sorted(
        (data.get("fast_filter") or {}).get("candidates", []) or [],
        key=lambda x: float(x.get("score", 0) or 0),
        reverse=True,
    )[:8]

    deep = best_deep_per_symbol(data)

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
                f"#{i} {item.get('symbol', '?')} | "
                f"{item.get('side', 'WAIT')} | "
                f"Score {float(item.get('score', 0) or 0):.1f}/100"
            )
    else:
        lines.append("No fast candidates.")

    lines += [
        "",
        "🎯 DEEP OPPORTUNITY STATUS",
        "XAU/USD • EUR/USD • BTC/USD",
        "",
    ]

    for symbol in TOP_SYMBOLS:
        item = deep.get(symbol)

        if not item:
            lines.append(f"{symbol} | DATA_UNAVAILABLE / NOT_ANALYZED")
            lines.append("")
            continue

        status = str(item.get("status") or item.get("entry_state") or "WAIT")
        side = str(item.get("side") or "WAIT")

        entry, sl, tp = extract_levels(item)

        score = item.get("strategy_score", item.get("score"))
        execution = item.get("execution_quality_score")
        count = item.get("confirmed_strategy_count", 0)

        lines.append(f"{symbol} | {side} | {status}")
        lines.append(
            f"Score: {num(score,1)} | "
            f"Execution: {num(execution,1)} | "
            f"Strategies: {count}/9"
        )
        lines.append(
            f"Style: {item.get('opportunity_style') or item.get('opportunity_class') or 'DEVELOPING'} | "
            f"TF: {item.get('execution_timeframe') or item.get('position_timeframe') or 'WAIT'}"
        )
        lines.append(
            f"Entry: {num(entry)} | SL: {num(sl)} | "
            f"TP: {num(tp)} | R:R: {rr(item.get('rr'))}"
        )
        lines.append(f"Strategies: {strategy_text(item)}")

        reasons = get_reasons(item)

        if status == "CONFIRMED_ENTRY":
            lines.append("✅ CONFIRMED ENTRY — scanner confirmation gates passed.")
        elif reasons:
            lines.append("Next condition: " + " | ".join(reasons))
        else:
            gate = item.get("tp_status") or item.get("strategy_gate") or "awaiting final confirmation"
            lines.append(f"Gate state: {gate}")

        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "Automatic trading: DISABLED",
        "FAST = broad ranking; DEEP = XAU/USD + EUR/USD + BTC/USD.",
        "FAST scores do not become trades automatically.",
        "CONFIRMED_ENTRY is reported only when the scanner's confirmation gates pass.",
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
    return api_call(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not configured. "
            "Add it as a GitHub Actions repository secret."
        )

    report_path = os.environ.get("SCANNER_JSON", "scan_results.json")

    with open(report_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not chat_id:
        chat_id = discover_chat_id(token)

    message = render_report(data)

    for chunk in chunk_message(message):
        send_message(token, chat_id, chunk)
        time.sleep(0.5)

    print("Telegram report sent successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"TELEGRAM SEND ERROR: {exc}", file=sys.stderr)
        raise
