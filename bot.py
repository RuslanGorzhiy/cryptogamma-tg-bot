"""
Telegram-бот CryptoGamma Signal Bot.

Команды:
    /start, /help   — краткая справка
    /btc            — снимок метрик по BTC
    /eth            — снимок метрик по ETH
    /both           — снимок по BTC и ETH сразу

Запуск (long polling, локально или на сервере):
    export TELEGRAM_BOT_TOKEN=xxxx
    python bot.py

Токен НИКОГДА не должен коммититься в репозиторий — используйте
переменную окружения / GitHub Secrets (см. README.md).
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from cryptogamma_client import CryptoGammaError, fetch_snapshot
from signals import format_snapshot_message, trackable_fields
from state_store import load_previous, save_previous

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("cryptogamma-bot")

HELP_TEXT = (
    "👋 <b>CryptoGamma Signal Bot</b>\n\n"
    "Показывает метрики опционного Gamma Exposure (GEX) для BTC и ETH "
    "на основе данных <a href=\"https://cryptogamma.io\">cryptogamma.io</a> "
    "(источник — Deribit).\n\n"
    "<b>Команды:</b>\n"
    "/btc — снимок по Bitcoin\n"
    "/eth — снимок по Ethereum\n"
    "/both — снимок по BTC и ETH\n"
    "/help — эта справка\n\n"
    "<i>Не является финансовым советом.</i>"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def _send_asset(update: Update, asset: str) -> None:
    await update.message.chat.send_action("typing")
    try:
        snap = fetch_snapshot(asset)
    except CryptoGammaError as exc:
        logger.warning("Ошибка получения данных %s: %s", asset, exc)
        await update.message.reply_text(f"⚠️ Не удалось получить данные по {asset}: {exc}")
        return

    # Динамика между запросами: используем тот же файл состояния, что и
    # alert.py (state/last_snapshot.json), поэтому картина согласована
    # между интерактивным ботом и плановыми алертами, если они работают
    # в общем рабочем каталоге.
    previous = load_previous(asset)
    text = format_snapshot_message(snap, previous=previous)
    save_previous(asset, trackable_fields(snap))

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_btc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_asset(update, "BTC")


async def cmd_eth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_asset(update, "ETH")


async def cmd_both(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_asset(update, "BTC")
    await _send_asset(update, "ETH")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Необработанная ошибка при обработке апдейта: %s", context.error)


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Переменная окружения TELEGRAM_BOT_TOKEN не задана. "
            "Получите токен у @BotFather и экспортируйте его перед запуском."
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("btc", cmd_btc))
    app.add_handler(CommandHandler("eth", cmd_eth))
    app.add_handler(CommandHandler("both", cmd_both))
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    app = build_application()
    logger.info("Бот запущен, начинаю polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
