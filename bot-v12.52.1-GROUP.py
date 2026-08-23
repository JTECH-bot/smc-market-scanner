import os
import time
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"].strip()
TELEGRAM_GROUP_CHAT_ID = os.environ.get("TELEGRAM_GROUP_CHAT_ID", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "mwendaj857-sketch/smc-market-scanner").strip()
GITHUB_REF = os.environ.get("GITHUB_REF", "main").strip()
SCANNER_WORKFLOW = os.environ.get(
    "SCANNER_WORKFLOW",
    "LSE-Smart-Scanner-v12.52.1-single-authoritative-context-resolver-500KB-FIXED.yml",
).strip()

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
GITHUB_API = "https://api.github.com"


def telegram(method, data=None):
    response = requests.post(
        f"{API}/{method}",
        json=data or {},
        timeout=35,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok", False):
        raise RuntimeError(f"Telegram API error: {result}")
    return result


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )


def copy_message_to_group(from_chat_id, message_id):
    if not TELEGRAM_GROUP_CHAT_ID:
        return None
    return telegram(
        "copyMessage",
        {
            "chat_id": TELEGRAM_GROUP_CHAT_ID,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        },
    )


def forward_message_to_group(message, source_chat_id):
    """Copy private-chat messages to the configured group. Never copy group messages to itself."""
    if not TELEGRAM_GROUP_CHAT_ID:
        return False
    if str(source_chat_id) == TELEGRAM_GROUP_CHAT_ID:
        return False
    message_id = message.get("message_id")
    if not message_id:
        return False
    copy_message_to_group(source_chat_id, message_id)
    return True


def trigger_scanner():
    """Dispatch the authoritative v12.52.1 workflow; do not run scanner code here."""
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not configured for the bot")
    if not GITHUB_REPOSITORY or "/" not in GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_REPOSITORY must be owner/repository")
    if not SCANNER_WORKFLOW:
        raise RuntimeError("SCANNER_WORKFLOW is not configured")

    owner, repo = GITHUB_REPOSITORY.split("/", 1)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/actions/workflows/{SCANNER_WORKFLOW}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "LSE-Smart-Telegram-Bot",
    }
    payload = {
        "ref": GITHUB_REF,
        "inputs": {
            "max_instruments": "0",
            "categories": "forex,commodity,crypto",
            "top_n": "5",
            "runtime_minutes": "8",
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=35)
    if response.status_code != 204:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"GitHub workflow dispatch failed ({response.status_code}): {detail}")


def authorized(chat_id):
    # Only the configured admin chat or source group may trigger GitHub Actions.
    allowed = {TELEGRAM_GROUP_CHAT_ID} if TELEGRAM_GROUP_CHAT_ID else set()
    return str(chat_id) in allowed


def help_text():
    return (
        "📡 LSE SMART SCANNER v12.52.1\n\n"
        "Commands:\n"
        "/scan — start a fresh scanner run\n"
        "/start — show this help\n"
        "/id — show this chat ID\n\n"
        "Message forwarding: messages sent privately to this bot are copied to the configured Telegram group. Messages already in the group are not copied back into the same group.\n"
        "The scanner remains analytical only.\n"
        "Automatic trading: DISABLED."
    )


def main():
    print("SMC Telegram Bot starting...")
    print(f"GitHub repository: {GITHUB_REPOSITORY}")
    print(f"Scanner workflow: {SCANNER_WORKFLOW}")

    if not TELEGRAM_GROUP_CHAT_ID:
        print("WARNING: TELEGRAM_GROUP_CHAT_ID is not configured; group delivery and /scan authorization are disabled.")
    if not GITHUB_TOKEN:
        print("WARNING: GITHUB_TOKEN is not configured; /scan cannot trigger GitHub Actions.")

    offset = None

    while True:
        try:
            data = {"timeout": 25}
            if offset is not None:
                data["offset"] = offset

            result = telegram("getUpdates", data)

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = (message.get("text") or "").strip()

                if chat_id is None:
                    continue

                print(f"Message received from {chat_id}: {text}")

                try:
                    forwarded = forward_message_to_group(message, chat_id)
                    if forwarded:
                        print(f"Message {message.get('message_id')} copied to group {TELEGRAM_GROUP_CHAT_ID}")
                except Exception as exc:
                    print(f"Telegram forwarding error: {exc}")
                    if str(chat_id) != TELEGRAM_GROUP_CHAT_ID:
                        send_message(chat_id, "⚠️ I could not copy that message to the configured Telegram group. Check TELEGRAM_GROUP_CHAT_ID and that the bot can post in the group.")

                if text in ("/start", "/help"):
                    send_message(chat_id, help_text())
                    continue

                if text == "/id":
                    send_message(chat_id, f"🆔 Chat ID: {chat_id}")
                    continue

                if text == "/scan":
                    if not authorized(chat_id):
                        send_message(
                            chat_id,
                            "⛔ This chat is not authorized to trigger the scanner.",
                        )
                        continue

                    try:
                        trigger_scanner()
                        send_message(
                            chat_id,
                            "🟢 SCAN REQUEST ACCEPTED\n\n"
                            "GitHub Actions has started the authoritative LSE Smart Scanner v12.52.1.\n\n"
                            "The completed validated result will be sent here automatically.\n"
                            "Automatic trading: DISABLED.",
                        )
                    except Exception as exc:
                        print(f"Scan trigger error: {exc}")
                        send_message(
                            chat_id,
                            "🔴 SCAN COULD NOT START\n\n"
                            f"Reason: {exc}\n\n"
                            "No trade was placed.",
                        )
                    continue

                if text:
                    send_message(
                        chat_id,
                        "I understand /scan to start a fresh scanner run.\n\n" + help_text(),
                    )

        except Exception as exc:
            print(f"Bot polling error: {exc}")
            time.sleep(10)


if __name__ == "__main__":
    main()
