import logging
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


@dataclass(frozen=True)
class Config:
    telegram_token: str
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

    return Config(
        telegram_token=telegram_token,
        ticktick_access_token=ticktick_access_token,
        ticktick_project_id=ticktick_project_id,
        timezone=ZoneInfo(timezone_name),
        ticktick_base_url=ticktick_base_url,
    )


def format_due_date(now: datetime, timezone: ZoneInfo) -> str:
    due = datetime.combine(now.date(), time(23, 0), tzinfo=timezone)
    return due.strftime("%Y-%m-%dT%H:%M:%S.000%z")


def build_task_payload(text: str, config: Config) -> dict:
    now = datetime.now(config.timezone)
    return {
        "title": text,
        "projectId": config.ticktick_project_id,
        "dueDate": format_due_date(now, config.timezone),
    }


def format_task_text(message_text: str, sender_username: str | None, sender_name: str | None) -> str:
    if sender_username:
        sender_label = f"@{sender_username}"
    else:
        sender_label = sender_name or "Неизвестный отправитель"
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
