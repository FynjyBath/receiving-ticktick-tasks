# receiving-ticktick-tasks

Telegram-бот для добавления задач в TickTick. Каждое текстовое сообщение (кроме `/start`) превращается в задачу в указанном списке TickTick с дедлайном в 23:00 текущего дня.

## Возможности
- принимает текстовые сообщения в Telegram;
- добавляет задачу в выбранный список TickTick;
- выставляет дедлайн на 23:00 в день получения сообщения.

## Требования
- Python 3.11+
- аккаунт TickTick с доступом к API
- Telegram бот и токен

## Настройка

Создайте файл `.env` на основе `.env.example` и заполните значения:

```ini
TELEGRAM_BOT_TOKEN=...        # токен Telegram бота
TICKTICK_ACCESS_TOKEN=...     # OAuth access token TickTick
TICKTICK_PROJECT_ID=...       # ID списка (project) TickTick
TIMEZONE=Europe/Moscow        # таймзона для дедлайна (опционально)
TICKTICK_BASE_URL=https://api.ticktick.com
```

### Как получить TickTick Access Token
1. Зарегистрируйте приложение и получите доступ к Open API согласно документации: https://developer.ticktick.com/docs#/openapi
2. Пройдите OAuth-авторизацию и получите `access_token`.
3. Используйте `access_token` в переменной `TICKTICK_ACCESS_TOKEN`.

### Как узнать ID списка TickTick
Используйте Open API TickTick, чтобы получить список проектов (списков) и выбрать нужный `id`. Его значение используйте в `TICKTICK_PROJECT_ID`.

## Запуск

1. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Запустите бота:

```bash
export $(cat .env | xargs)  # или другой способ загрузки env
python bot.py
```

## Примечания
- Сообщения `/start` обрабатываются отдельно и не создают задачи.
- Если нужно изменить время дедлайна или формат, отредактируйте функцию `format_due_date` в `bot.py`.
