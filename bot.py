"""
Телеграм бот для учёта ежедневных начислений
Записывает данные через Google Apps Script Web App — без credentials.json!
"""

import re
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date

import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ============================================================
# НАСТРОЙКИ — задайте как Secrets на HuggingFace
# ============================================================
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")  # URL из Deploy → Web App
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


# ---------- Обработчики бота ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post or update.message
    if not message or not message.text:
        return


    data = parse_message(message.text)
    if not data:
        return

    result = await sheets_request({"action": "add_transaction", **data})

    if result and result.get("ok"):
        logger.info(f"✅ {data['date']} | +{data['zachislenie']} TJS")
    else:
        logger.error(f"❌ Ошибка записи: {result}")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today().strftime("%d.%m.%Y")
    result = await sheets_request({"action": "get_today", "date": today})

    if not result or not result.get("ok") or not result.get("data"):
        await update.message.reply_text(f"За {today} транзакций пока нет.")
        return

    d = result["data"]
    await update.message.reply_text(
        f"📊 *Итоги за {today}*\n"
        f"Транзакций: `{d['count']}`\n"
        f"Сумма: `{d['summa']} TJS`\n"
        f"Комиссия: `{d['komis']} TJS`\n"
        f"Зачислено: `{d['zachislenie']} TJS`",
        parse_mode="Markdown"
    )


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    month = datetime.now().strftime("%m.%Y")
    result = await sheets_request({"action": "get_month", "month": month})

    if not result or not result.get("ok") or not result.get("data"):
        await update.message.reply_text(f"За {month} данных нет.")
        return

    d = result["data"]
    await update.message.reply_text(
        f"📅 *Итоги за {month}*\n"
        f"Транзакций: `{d['count']}`\n"
        f"Сумма: `{d['summa']} TJS`\n"
        f"Комиссия: `{d['komis']} TJS`\n"
        f"Зачислено: `{d['zachislenie']} TJS`",
        parse_mode="Markdown"
    )


# ---------- Запуск ----------

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан!")
    if not APPS_SCRIPT_URL:
        raise ValueError("APPS_SCRIPT_URL не задан!")

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("month", cmd_month))

    logger.info("🤖 Бот запущен...")
    app.run_polling(allowed_updates=["message", "channel_post"])


if __name__ == "__main__":
    main()
