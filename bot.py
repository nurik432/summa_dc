"""
Телеграм бот для учёта ежедневных начислений
Записывает данные через Google Apps Script Web App
Принимает пересланные сообщения в личку
"""

import re
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, MessageHandler, CommandHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ============================================================
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ---------- Keep-alive HTTP сервер ----------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", 7860), HealthHandler)
    logger.info("Health server started on port 7860")
    server.serve_forever()


# ---------- Кнопки ----------

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Итоги за сегодня", callback_data="today"),
            InlineKeyboardButton("📅 Итоги за месяц",  callback_data="month"),
        ]
    ])


# ---------- Google Apps Script ----------

async def sheets_request(payload: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.post(APPS_SCRIPT_URL, json=payload)
            return resp.json()
    except Exception as e:
        logger.error(f"Ошибка запроса к Apps Script: {e}")
        return None


# ---------- Парсер сообщений ----------

def parse_message(text: str) -> dict | None:
    if "Zachislenie" not in text:
        return None

    def extract(pattern, txt, default=""):
        m = re.search(pattern, txt)
        return m.group(1).strip() if m else default

    try:
        summa  = float(extract(r"Summa\s+([\d.]+)\s+TJS", text, "0"))
        komis  = float(extract(r"Komis\s+([\d.]+)\s+TJS", text, "0"))
        zach   = float(extract(r"Zachislenie\s+([\d.]+)\s+TJS", text, "0"))
        balans = float(extract(r"Balans\s+([\d.]+)\s+TJS", text, "0"))

        data_raw = extract(r"Data\s+(\d{2}:\d{2}\s+\d{2}\.\d{2}\.\d{2})", text)
        otprav   = extract(r"Otpravitel\s+(\S+)", text)
        kod      = extract(r"Kod\s+(\d+)", text)
        karta    = extract(r"Karta\s+(\d+)", text)

        dt = datetime.strptime(data_raw, "%H:%M %d.%m.%y") if data_raw else datetime.now()

        return {
            "date": dt.strftime("%d.%m.%Y"),
            "time": dt.strftime("%H:%M"),
            "summa": summa, "komis": komis, "zachislenie": zach,
            "otpravitel": otprav, "kod": kod, "karta": karta, "balans": balans,
        }
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
        return None


# ---------- Текст итогов ----------

def today_text(d: dict, date_str: str) -> str:
    return (
        f"📊 *Итоги за {date_str}*\n"
        f"Транзакций: `{d['count']}`\n"
        f"Сумма: `{d['summa']} TJS`\n"
        f"Комиссия: `{d['komis']} TJS`\n"
        f"Зачислено: `{d['zachislenie']} TJS`"
    )

def month_text(d: dict, month_str: str) -> str:
    return (
        f"📅 *Итоги за {month_str}*\n"
        f"Транзакций: `{d['count']}`\n"
        f"Сумма: `{d['summa']} TJS`\n"
        f"Комиссия: `{d['komis']} TJS`\n"
        f"Зачислено: `{d['zachislenie']} TJS`"
    )


# ---------- Обработчики ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Пересылай мне сообщения о начислениях.\n\n"
        "Или нажми кнопку для просмотра статистики:",
        reply_markup=main_keyboard()
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not message.text:
        return

    data = parse_message(message.text)
    if not data:
        return

    result = await sheets_request({"action": "add_transaction", **data})

    if result and result.get("ok"):
        logger.info(f"✅ {data['date']} | +{data['zachislenie']} TJS")
        await message.reply_text(
            f"✅ Записано: *{data['zachislenie']} TJS* за {data['date']}",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
    else:
        logger.error(f"❌ Ошибка записи: {result}")
        await message.reply_text("❌ Ошибка записи в таблицу.")


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "today":
        today = date.today().strftime("%d.%m.%Y")
        result = await sheets_request({"action": "get_today", "date": today})
        if not result or not result.get("ok") or not result.get("data"):
            await query.edit_message_text(f"За {today} транзакций пока нет.", reply_markup=main_keyboard())
        else:
            await query.edit_message_text(
                today_text(result["data"], today),
                parse_mode="Markdown",
                reply_markup=main_keyboard()
            )

    elif query.data == "month":
        month = datetime.now().strftime("%m.%Y")
        result = await sheets_request({"action": "get_month", "month": month})
        if not result or not result.get("ok") or not result.get("data"):
            await query.edit_message_text(f"За {month} данных нет.", reply_markup=main_keyboard())
        else:
            await query.edit_message_text(
                month_text(result["data"], month),
                parse_mode="Markdown",
                reply_markup=main_keyboard()
            )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today().strftime("%d.%m.%Y")
    result = await sheets_request({"action": "get_today", "date": today})
    if not result or not result.get("ok") or not result.get("data"):
        await update.message.reply_text(f"За {today} транзакций пока нет.", reply_markup=main_keyboard())
        return
    await update.message.reply_text(
        today_text(result["data"], today),
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = datetime.now().strftime("%m.%Y")
    result = await sheets_request({"action": "get_month", "month": month})
    if not result or not result.get("ok") or not result.get("data"):
        await update.message.reply_text(f"За {month} данных нет.", reply_markup=main_keyboard())
        return
    await update.message.reply_text(
        month_text(result["data"], month),
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


# ---------- Запуск ----------

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")
    if not APPS_SCRIPT_URL:
        raise ValueError("APPS_SCRIPT_URL не задан!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🤖 Бот запущен...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
