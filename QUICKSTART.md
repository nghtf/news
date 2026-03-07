# Quickstart

1. Создайте и заполните `.env` (можно на базе `.env.example`).
2. Установите зависимости:

```bash
make install
```

3. Запустите бота:

```bash
make run
```
`state.json` будет создан автоматически (если отсутствует).

Тестовый режим (публикация в `TELEGRAM_TEST_CHANNEL_ID`):
```bash
make run-test
```

Запуск в Docker:
```bash
make docker-build
make docker-up
make docker-logs
```

Docker в тестовом режиме:
```bash
make docker-up-test
make docker-logs
```
Перед запуском также проверяется, что `state.json` — это файл, а не директория.

Остановить:
```bash
make docker-down
```

4. Команды в чате с ботом (для admin id):
- `/start`
- `/check` — вручную проверить RSS
- `/stats` — состояние (`seen_links`, `pending`)

Сброс состояния для теста "с нуля":
```bash
make reset-state
```

Сброс состояния в Docker:
```bash
make docker-reset-state
```

Быстрая локальная очистка без Python (кэши + reset `state.json`):
```bash
make clean
```

Поток модерации:
1. Бот присылает новость из RSS в исходном сокращенном виде + источник (`Принять в работу` / `Отклонить`).
   Сейчас включен фильтр по категории RSS: только `Industry news`.
2. После `Принять в работу` бот делает перевод/саммари через LLM и присылает итоговый текст без источника (`Опубликовать` / `Переписать` / `Отклонить`).
В этой карточке также показывается строка `Источник текста для перевода` (например: полный текст статьи / расширенный текст RSS / краткий текст RSS).
3. Вместо кнопки `Опубликовать` можно ответить на эту карточку цитированием и своим текстом: в канал будет опубликован именно текст ответа.

## Минимальный `.env`

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL_ID=-100... # id канала или @channel_username
TELEGRAM_TEST_CHANNEL_ID=-100... # тестовый канал для make run-test / docker-up-test
TELEGRAM_ADMIN_CHAT_ID=...  # id человека или группы, НЕ id бота

OPENAI_API_KEY=...
LLM_MODEL=gpt-4o-mini
LLM_PROVIDER=openai

RSS_FEEDS=https://www.example.com/feed/
POLL_INTERVAL_MINUTES=10
MAX_ITEMS_PER_POLL=3
STATE_FILE=state.json
```

`TELEGRAM_ADMIN_CHAT_ID` и `TELEGRAM_CHANNEL_ID` как получить через утилиту:
1. Напишите вашему боту `/start` из личного аккаунта (и при необходимости из группы).
2. Добавьте бота в канал и назначьте администратором с правом публикации.
3. Запустите:

```bash
make telegram-ids
```

Утилита покажет найденные кандидаты для `ADMIN_CHAT_ID` и `CHANNEL_ID`.

Если нужно проверить конкретный публичный канал:

```bash
python3 -m src.telegram_ids --channel @your_channel_username
```

Тестовый режим публикации:
1. Создайте отдельный тестовый канал и добавьте туда бота администратором.
2. Укажите в `.env` `TELEGRAM_TEST_CHANNEL_ID=-100...` (или `@username`).
3. Запускайте бот через `make run-test` (или `make docker-up-test` для Docker).
