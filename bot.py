import logging
import re
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
import sys
from typing import Dict, List, Optional, Tuple

if sys.version_info < (3, 9):
    from backports.zoneinfo import ZoneInfo
else:
    from zoneinfo import ZoneInfo

from dateparser import parse as parse_date
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
    notify_chat_id: Optional[int]
    ticktick_access_token: str
    ticktick_project_id: str
    timezone: ZoneInfo
    ticktick_base_url: str


def load_config(config_path: Optional[Path] = None) -> Config:
    config_path = config_path or Path(__file__).with_name("config.ini")
    if not config_path.exists():
        raise RuntimeError(
            f"Config file not found: {config_path}. "
            "Create it from config.ini.example."
        )

    parser = ConfigParser()
    parser.read(config_path, encoding="utf-8")

    def get_required(section: str, option: str) -> Optional[str]:
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

    notify_chat_id: Optional[int] = None
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
    numeric_candidates = _extract_numeric_date_candidates(message_text, now, timezone)
    if numeric_candidates:
        for candidate in numeric_candidates:
            if candidate["has_time"]:
                due = candidate["datetime"]
                if due > now:
                    return due
        due = numeric_candidates[0]["datetime"]
        return due if due > now else default_due

    matches = search_dates(
        message_text,
        languages=["ru", "en"],
        settings={
            "DATE_ORDER": "DMY",
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": timezone.key,
            "RELATIVE_BASE": now,
        },
    )
    if not matches:
        return default_due

    combined_match = None
    date_match = None
    time_match = None

    for matched_text, parsed_datetime in matches:
        has_date = bool(DATE_PATTERN.search(matched_text))
        has_time = bool(TIME_PATTERN.search(matched_text))

        if has_date and has_time:
            combined_match = parsed_datetime
            break
        if has_date and date_match is None:
            date_match = parsed_datetime
        if has_time and not has_date and time_match is None:
            time_match = parsed_datetime

    if combined_match:
        due = combined_match.astimezone(timezone)
    elif date_match and time_match:
        due = datetime.combine(
            date_match.astimezone(timezone).date(),
            time_match.time(),
            tzinfo=timezone,
        )
    elif date_match:
        due = datetime.combine(
            date_match.astimezone(timezone).date(),
            time(23, 0),
            tzinfo=timezone,
        )
    elif time_match:
        due = datetime.combine(now.date(), time_match.time(), tzinfo=timezone)
        if due <= now:
            due = due + timedelta(days=1)
    else:
        due = matches[0][1].astimezone(timezone)

    if due <= now:
        return default_due
    return due


def _extract_numeric_date_candidates(
    message_text: str,
    now: datetime,
    timezone: ZoneInfo,
) -> List[Dict[str, object]]:
    candidates = []
    for match in re.finditer(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", message_text):
        date_value = _parse_numeric_date(match.group(0), now)
        if date_value is None:
            continue
        time_value = _find_time_near_match(message_text, match, now, timezone)
        candidate_datetime = datetime.combine(
            date_value,
            time_value or time(23, 0),
            tzinfo=timezone,
        )
        candidates.append(
            {
                "datetime": candidate_datetime,
                "has_time": time_value is not None,
                "position": match.start(),
            }
        )
    return sorted(candidates, key=lambda item: item["position"])


def _parse_numeric_date(date_text: str, now: datetime) -> Optional[datetime.date]:
    parts = re.split(r"[./-]", date_text)
    if len(parts) < 2:
        return None
    try:
        day = int(parts[0])
        month = int(parts[1])
        if len(parts) >= 3:
            year = int(parts[2])
            if year < 100:
                year += 2000
        else:
            year = now.year
        parsed_date = datetime(year, month, day).date()
    except ValueError:
        return None

    if len(parts) < 3 and parsed_date < now.date():
        try:
            parsed_date = datetime(year + 1, month, day).date()
        except ValueError:
            return None
    return parsed_date


def _find_time_near_match(
    text: str,
    match: re.Match,
    now: datetime,
    timezone: ZoneInfo,
) -> Optional[time]:
    window_before = text[max(0, match.start() - 12) : match.start()]
    window_after = text[match.end() : match.end() + 20]
    for window in (window_after, window_before):
        time_match = TIME_PATTERN.search(window)
        if time_match:
            return _parse_time_text(time_match.group(0), now, timezone)
    return None


def _parse_time_text(time_text: str, now: datetime, timezone: ZoneInfo) -> Optional[time]:
    if re.search(r"[:.]", time_text):
        raw = time_text.replace(".", ":")
        parts = raw.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            return time(hour=hour, minute=minute)
        except ValueError:
            return None
    parsed = parse_date(
        time_text,
        languages=["ru", "en"],
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": timezone.key,
            "RELATIVE_BASE": now,
        },
    )
    if parsed:
        return parsed.astimezone(timezone).time()
    return None


def build_task_payload(text: str, config: Config) -> Tuple[Dict[str, str], datetime]:
    now = datetime.now(config.timezone)
    due_datetime = infer_due_datetime(text, now, config.timezone)
    payload = {
        "title": text,
        "projectId": config.ticktick_project_id,
        "dueDate": due_datetime.strftime("%Y-%m-%dT%H:%M:%S.000%z"),
        "timeZone": config.timezone.key,
        "reminders": ["TRIGGER:PT0S"],
    }
    return payload, due_datetime


def format_due_datetime(due_datetime: datetime) -> str:
    return due_datetime.strftime("%d.%m.%Y %H:%M")


def format_sender_label(sender_username: Optional[str], sender_name: Optional[str]) -> str:
    if sender_username:
        return f"@{sender_username}"
    return sender_name or "Неизвестный отправитель"


def format_task_text(
    message_text: str,
    sender_username: Optional[str],
    sender_name: Optional[str],
) -> str:
    sender_label = format_sender_label(sender_username, sender_name)
    return f"{sender_label} {message_text}"


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Привет!\nОтправь мне текст задачи, и я добавлю её в TickTick Антону.\nПри желании можешь указать в конце сообщения дату или время в любом формате."
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
    payload, due_datetime = build_task_payload(task_text, config)
    due_label = format_due_datetime(due_datetime)

    async with httpx.AsyncClient(base_url=config.ticktick_base_url, timeout=10.0) as client:
        response = await client.post(
            "/open/v1/task",
            json=payload,
            headers={"Authorization": f"Bearer {config.ticktick_access_token}"},
        )
    if response.is_success:
        await update.message.reply_text(
            f"Задача добавлена ✅\n{task_text}"
        )
        if config.notify_chat_id:
            await context.bot.send_message(
                chat_id=config.notify_chat_id,
                text=f"Новая задача от {sender_label}:\n{text}\nДедлайн: {due_label}",
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
