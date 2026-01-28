import logging
import re
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dateparser.search import search_dates
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

DATE_PATTERN = re.compile(
    r"(\b\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?\b)"
    r"|(\b(?:январ[ья]|феврал[ья]|март[а]?|апрел[ья]|ма[йя]|июн[ья]|"
    r"июл[ья]|август[а]?|сентябр[ья]|октябр[ья]|ноябр[ья]|декабр[ья])\b)"
    r"|(\b(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\b)"
    r"|(\b(?:сегодня|завтра|послезавтра)\b)",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(
    r"(\b\d{1,2}[:.]\d{2}\b)"
    r"|(\b\d{1,2}\s*(?:am|pm|утра|дня|вечера|ночи)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Config:
    telegram_token: str
    notify_chat_id: int | None
    ticktick_access_token: str
    ticktick_project_id: str
    timezone: ZoneInfo
    ticktick_base_url: str


def load_config(config_path: Path | None = None) -> Config:
    config_path = config_path or Path(__file__).with_name("config.ini")
    if not config_path.exists():
        raise RuntimeError(
            f"Config file not found: {config_path}. "
            "Create it from config.ini.example."
        )

    parser = ConfigParser()
    parser.read(config_path, encoding="utf-8")

    def get_required(section: str, option: str) -> str | None:
        if not parser.has_option(section, option):
            return None
        value = parser.get(section, option).strip()
        return value or None

    telegram_token = get_required("telegram", "bot_token")
    notify_chat_id_raw = parser.get("telegram", "notify_chat_id", fallback="").strip()
    ticktick_access_token = get_required("ticktick", "access_token")
    ticktick_project_id = get_required("ticktick", "project_id")

    missing = [
        name
        for name, value in [
            ("telegram.bot_token", telegram_token),
            ("ticktick.access_token", ticktick_access_token),
            ("ticktick.project_id", ticktick_project_id),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required config values: {', '.join(missing)}"
        )

    timezone_name = parser.get("app", "timezone", fallback="UTC")
    ticktick_base_url = parser.get(
        "ticktick", "base_url", fallback="https://api.ticktick.com"
    )

    notify_chat_id: int | None = None
    if notify_chat_id_raw:
        try:
            notify_chat_id = int(notify_chat_id_raw)
        except ValueError as exc:
            raise RuntimeError(
                "Invalid telegram.notify_chat_id value; expected integer chat ID."
            ) from exc

    return Config(
        telegram_token=telegram_token,
        notify_chat_id=notify_chat_id,
        ticktick_access_token=ticktick_access_token,
        ticktick_project_id=ticktick_project_id,
        timezone=ZoneInfo(timezone_name),
        ticktick_base_url=ticktick_base_url,
    )


def infer_due_datetime(message_text: str, now: datetime, timezone: ZoneInfo) -> datetime:
    default_due = datetime.combine(now.date(), time(23, 0), tzinfo=timezone)
    matches = search_dates(
        message_text,
        languages=["ru", "en"],
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": timezone.key,
            "RELATIVE_BASE": now,
        },
    )
    if not matches:
        return default_due

    matched_text, parsed_datetime = matches[0]
    has_date = bool(DATE_PATTERN.search(matched_text))
    has_time = bool(TIME_PATTERN.search(matched_text))

    if has_date and not has_time:
        due = datetime.combine(parsed_datetime.date(), time(23, 0), tzinfo=timezone)
    elif has_time and not has_date:
        due = datetime.combine(now.date(), parsed_datetime.time(), tzinfo=timezone)
    else:
        due = parsed_datetime.astimezone(timezone)

    if due <= now:
        return default_due
    return due


def build_task_payload(text: str, config: Config) -> dict:
    now = datetime.now(config.timezone)
    due_datetime = infer_due_datetime(text, now, config.timezone)
    return {
        "title": text,
        "projectId": config.ticktick_project_id,
        "dueDate": due_datetime.strftime("%Y-%m-%dT%H:%M:%S.000%z"),
    }


def format_sender_label(sender_username: str | None, sender_name: str | None) -> str:
    if sender_username:
        return f"@{sender_username}"
    return sender_name or "Неизвестный отправитель"


def format_task_text(
    message_text: str,
    sender_username: str | None,
    sender_name: str | None,
) -> str:
    sender_label = format_sender_label(sender_username, sender_name)
    return f"{sender_label} {message_text}"


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Привет! Отправь мне текст задачи, и я добавлю её в TickTick."
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text or text.startswith("/start"):
        return

    config: Config = context.bot_data["config"]
    sender = update.message.from_user
    sender_label = format_sender_label(
        sender.username if sender else None,
        sender.full_name if sender else None,
    )
    task_text = format_task_text(
        text,
        sender.username if sender else None,
        sender.full_name if sender else None,
    )
    payload = build_task_payload(task_text, config)

    async with httpx.AsyncClient(base_url=config.ticktick_base_url, timeout=10.0) as client:
        response = await client.post(
            "/open/v1/task",
            json=payload,
            headers={"Authorization": f"Bearer {config.ticktick_access_token}"},
        )
    if response.is_success:
        await update.message.reply_text(f"Задача добавлена ✅\n{task_text}")
        if config.notify_chat_id:
            await context.bot.send_message(
                chat_id=config.notify_chat_id,
                text=f"Новая задача от {sender_label}:\n{text}",
            )
    else:
        logging.error(
            "TickTick API error %s: %s", response.status_code, response.text
        )
        await update.message.reply_text(
            "Не удалось добавить задачу. Проверьте настройки и права доступа."
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config = load_config()
    application = Application.builder().token(config.telegram_token).build()
    application.bot_data["config"] = config

    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()


if __name__ == "__main__":
    main()
