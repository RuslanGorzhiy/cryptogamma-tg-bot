# CryptoGamma Telegram Bot

Telegram-бот для BTC и ETH на основе публичных данных
[cryptogamma.io](https://cryptogamma.io/dashboard/) (Gamma Exposure,
dealer bias, squeeze levels, IV/RV, flow, put/call ratio).

Данные берутся из JSON snapshot API cryptogamma.io:

```
GET https://cryptogamma.io/api/public/snapshot?asset=BTC
GET https://cryptogamma.io/api/public/snapshot?asset=ETH
```

Источник данных на самом cryptogamma.io — публичное Deribit options
API, обновление раз в ~15 минут.

> ⚠️ **Требуется API-токен.** Ранее этот эндпоинт отдавал JSON без
> авторизации, но сейчас cryptogamma.io закрыл анонимный доступ и
> возвращает `401 Unauthorized` без токена. Получить бесплатный токен
> можно на [cryptogamma.io/api-docs](https://cryptogamma.io/api-docs/),
> войдя через Google-аккаунт. Точный формат передачи токена на странице
> не задокументирован открытым текстом — после логина проверьте
> вкладку **Network** в DevTools браузера при обращении дашборда к API
> (заголовок `Authorization: Bearer ...` либо `X-API-Key: ...`).
>
> Полученный токен задайте в переменной окружения
> `CRYPTOGAMMA_API_TOKEN`. Если сайт использует не `Bearer`, а
> `X-API-Key`, дополнительно задайте `CRYPTOGAMMA_AUTH_SCHEME=apikey`.

## Два режима работы

Репозиторий содержит два независимых сценария — используйте один или оба:

1. **Интерактивный бот** (`bot.py`) — отвечает на команды `/btc`, `/eth`, `/both` в реальном времени. Требует постоянно работающий процесс (сервер/VPS/Railway/Render — GitHub Actions для этого не подходит, см. ниже).
2. **Плановые алерты** (`alert.py` + workflow в `.github/workflows/alert.yml`) — раз в N минут сам присылает снимок в заданный чат/канал через GitHub Actions, без сервера.

## Быстрый старт

### 1. Создать бота

Напишите [@BotFather](https://t.me/BotFather) в Telegram, создайте бота командой `/newbot`, сохраните токен.

### 2. Локальный запуск интерактивного бота

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # заполните TELEGRAM_BOT_TOKEN
export $(grep -v '^#' .env | xargs)   # или используйте python-dotenv

python bot.py
```

Бот начнёт отвечать на `/start`, `/btc`, `/eth`, `/both`.

### 3. Плановые алерты через GitHub Actions

1. Запушьте этот репозиторий на GitHub.
2. В настройках репозитория: **Settings → Secrets and variables → Actions → New repository secret** — добавьте:
   - `TELEGRAM_BOT_TOKEN` — токен бота
   - `TELEGRAM_CHAT_ID` — id чата/канала, куда слать алерты
   - `CRYPTOGAMMA_API_TOKEN` — токен, полученный на cryptogamma.io/api-docs (см. предупреждение выше)
3. Узнать `TELEGRAM_CHAT_ID`: напишите боту любое сообщение, затем откройте
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates` и найдите `"chat":{"id": ...}`.
   Для канала — добавьте бота администратором и используйте id канала (обычно начинается с `-100`).
4. Workflow `.github/workflows/alert.yml` запускается каждые 30 минут (cron) либо вручную через вкладку **Actions → CryptoGamma Telegram Alert → Run workflow**. Расписание меняется правкой строки `cron`.

## Структура проекта

```
cryptogamma-tg-bot/
├── bot.py                  # интерактивный бот (long polling)
├── alert.py                # разовый скрипт для расписания (GitHub Actions)
├── cryptogamma_client.py   # клиент публичного API cryptogamma.io
├── signals.py              # форматирование сообщений, сигнал и его динамика
├── state_store.py          # хранение снимка метрик между запусками (для динамики)
├── state/last_snapshot.json # сам файл состояния (коммитится workflow'ом)
├── requirements.txt
├── .env.example
├── .gitignore
└── .github/workflows/alert.yml
```

## Динамика сигнала между снимками

Сигнал не просто оценивает текущий снимок, но и сравнивает его с
предыдущим (файл `state/last_snapshot.json`), чтобы учитывать:

- разворот dealer bias (BEARISH → BULLISH и наоборот) — сильный сигнал
- рост/падение Net GEX по сравнению с прошлым снимком
- рост/падение put/call ratio

Для `alert.py` в GitHub Actions последний шаг workflow коммитит
обновлённый файл состояния обратно в репозиторий — без этого он бы
терялся при каждом запуске, так как раннер каждый раз чистый. Для
интерактивного бота (`bot.py`) состояние просто лежит на диске между
вызовами команд в рамках одного запущенного процесса.

## Пример сообщения бота

```
🟢 ETH — Gamma Exposure (cryptogamma.io)

Цена: $2,462.39
Net GEX: 668.88K (Call 1.09M / Put -421.45K)
Dealer bias: BULLISH (72.1% call-weighted)

Squeeze levels:
  Поддержка: $2,460.00
  Сопротивление: $2,500.00
  Пробой: $2,470.00

IV (ATM): 72.1% | RV (7д): 68.5% | Премия: 3.6%
Flow 24ч: call 0.00 / put 0.00 | C/P: 0

Delta hedging: Balanced | Squeeze risk: Neutral | Pin risk: Low

Источник: cryptogamma.io (данные Deribit). Не является финансовым советом.
```

## Важно

- Не коммитьте `.env` и токен бота в git — используйте GitHub Secrets.
- `cryptogamma_client.py` разбирает JSON защитно (по нескольким возможным именам полей), так как публичная структура ответа официально не задокументирована и может немного отличаться — при необходимости скорректируйте список ключей в `_pick(...)`.
- Это аналитический инструмент, не финансовый совет. cryptogamma.io не аффилирован с Deribit.
