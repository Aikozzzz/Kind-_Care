import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
BACKEND_URL = os.environ.get("TELEGRAM_BACKEND_URL", "http://backend:8000").rstrip("/")
SERVICE_TOKEN = _required("TELEGRAM_SERVICE_TOKEN")
HTTP_TIMEOUT = float(os.environ.get("TELEGRAM_HTTP_TIMEOUT", "10"))
POLL_TIMEOUT = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "30"))
RETRY_SECONDS = float(os.environ.get("TELEGRAM_RETRY_SECONDS", "5"))
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def telegram_call(method: str, payload: dict[str, object]) -> dict[str, object]:
    body = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(
        f"{API_URL}/{method}", data=body, method="POST"
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError("Telegram request was rejected")
    return result


def backend_call(path: str, payload: dict[str, object]) -> dict[str, object] | None:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        f"{BACKEND_URL}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Telegram-Service-Token": SERVICE_TOKEN,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read()).get("data")
    except urllib.error.HTTPError:
        return None


def send_text(chat_id: str, text: str) -> None:
    telegram_call("sendMessage", {"chat_id": chat_id, "text": text})


def handle_update(update: dict[str, object]) -> None:
    message = update.get("message")
    if not isinstance(message, dict) or message.get("chat", {}).get("type") != "private":
        return
    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = str(chat.get("id", ""))
    telegram_user_id = str(sender.get("id", ""))
    text = str(message.get("text", "")).strip()
    if not chat_id or not telegram_user_id:
        return
    command, _, argument = text.partition(" ")
    command = command.casefold().split("@", 1)[0]
    if command in {"/start", "/help"}:
        send_text(chat_id, "KindCare commands: /link CODE, /request E001, /status E001, /unlink")
    elif command == "/link":
        data = backend_call(
            "/api/telegram/bind",
            {
                "code": argument.strip(),
                "telegram_user_id": telegram_user_id,
                "chat_id": chat_id,
                "chat_type": "private",
            },
        )
        send_text(chat_id, "Telegram linked." if data else "Link code is invalid or expired.")
    elif command == "/status":
        data = backend_call(
            "/api/telegram/status",
            {"telegram_user_id": telegram_user_id, "elderly_id": argument.strip()},
        )
        if not data:
            send_text(chat_id, "Status is unavailable or you are not authorized.")
        else:
            send_text(
                chat_id,
                "KindCare status\n"
                f"Resident: {data['elderly_id']}\n"
                f"Risk: {data['current_risk']}\n"
                f"Device: {data['device_status']}\n"
                f"Active alerts: {data['active_alert_count']}",
            )
    elif command == "/request":
        data = backend_call(
            "/api/telegram/request",
            {"telegram_user_id": telegram_user_id, "elderly_id": argument.strip()},
        )
        send_text(
            chat_id,
            "Access request submitted. A caregiver administrator must approve it."
            if data
            else "Access request could not be recorded.",
        )
    elif command == "/unlink":
        send_text(chat_id, "Use the KindCare dashboard to unlink Telegram.")
    else:
        send_text(chat_id, "Unknown command. Use /help.")


def deliver_one() -> bool:
    event = backend_call("/api/internal/telegram/claim", {})
    if not event:
        return False
    for delivery in event.get("deliveries", []):
        text = (
            "KindCare alert\n"
            f"Resident: {event['elderly_id']}\n"
            f"Type: {event['alert_type']}\n"
            f"Severity: {event['severity']}"
        )
        success = True
        try:
            send_text(str(delivery["chat_id"]), text)
        except (OSError, RuntimeError):
            success = False
        backend_call(
            "/api/internal/telegram/complete",
            {
                "event_id": event["event_id"],
                "telegram_user_id": delivery["telegram_user_id"],
                "success": success,
            },
        )
    return True


def run() -> None:
    offset = 0
    while True:
        try:
            result = telegram_call(
                "getUpdates",
                {"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": '["message"]'},
            )
            for update in result.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                handle_update(update)
            deliver_one()
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    run()
