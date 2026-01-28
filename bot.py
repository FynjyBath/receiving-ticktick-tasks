import logging
import os
from dataclasses import dataclass
from datetime import datetime, time
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


def load_config() -> Config:
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    ticktick_access_token = os.environ.get("TICKTICK_ACCESS_TOKEN")
    ticktick_project_id = os.environ.get("TICKTICK_PROJECT_ID")
    timezone_name = os.environ.get("TIMEZONE", "UTC")
    ticktick_base_url = os.environ.get("TICKTICK_BASE_URL", "https://api.ticktick.com")

    missing = [
        name
        for name, value in [
            ("TELEGRAM_BOT_TOKEN", telegram_token),
            ("TICKTICK_ACCESS_TOKEN", ticktick_access_token),
            ("TICKTICK_PROJECT_ID", ticktick_project_id),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
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
    payload = build_task_payload(text, config)

    async with httpx.AsyncClient(base_url=config.ticktick_base_url, timeout=10.0) as client:
        response = await client.post(
            "/open/v1/task",
            json=payload,
            headers={"Authorization": f"Bearer {config.ticktick_access_token}"},
        )
    if response.is_success:
        await update.message.reply_text("Задача добавлена ✅")
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
