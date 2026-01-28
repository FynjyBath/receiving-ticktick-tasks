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

Создайте файл `config.ini` на основе `config.ini.example` и заполните значения:

```ini
[telegram]
bot_token=...                 # токен Telegram бота
notify_chat_id=...            # чат ID для уведомлений о новых задачах (опционально)

[ticktick]
access_token=...              # OAuth access token TickTick
project_id=...                # ID списка (project) TickTick
base_url=https://api.ticktick.com

[app]
timezone=Europe/Moscow        # таймзона для дедлайна (опционально)
```

### Как получить TickTick Access Token
1. Зарегистрируйте приложение и получите доступ к Open API согласно документации: https://developer.ticktick.com/docs#/openapi
2. Пройдите OAuth-авторизацию и получите `access_token`.
3. Укажите `access_token` в секции `[ticktick]` файла `config.ini`.

### Как узнать ID списка TickTick
Используйте Open API TickTick, чтобы получить список проектов (списков) и выбрать нужный `id`. Его значение укажите в `project_id` секции `[ticktick]` файла `config.ini`.

## Запуск

1. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Запустите бота:

```bash
python bot.py
```

## Примечания
- Сообщения `/start` обрабатываются отдельно и не создают задачи.
- Если нужно изменить время дедлайна или формат, отредактируйте функцию `format_due_date` в `bot.py`.
- Чтобы получать уведомления о новых задачах в личный аккаунт, укажите `notify_chat_id` в секции `[telegram]`.
