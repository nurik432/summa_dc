"""
Телеграм бот для учёта ежедневных начислений
Читает сообщения из канала/группы, парсит и сохраняет в Google Sheets

Адаптирован для HuggingFace Spaces:
- Конфиг берётся из переменных окружения (Secrets)
- Встроенный HTTP сервер на порту 7860 (чтобы Space не засыпал)
- credentials.json читается из переменной окружения GOOGLE_CREDS_JSON
"""

import re
import json
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date

from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# НАСТРОЙКИ из переменных окружения (HuggingFace Secrets)
# ============================================================
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID       = int(os.environ.get("CHANNEL_ID", "0"))       # например: -1001234567890
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Zachisleniya")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")     # содержимое credentials.json
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ---------- Keep-alive HTTP сервер ----------
# HuggingFace усыпляет Space если нет HTTP трафика.
# Этот минимальный сервер отвечает на пинги и держит Space живым.

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass  # Отключаем лишние логи

def run_health_server():
    server = HTTPServer(("0.0.0.0", 7860), HealthHandler)
    logger.info("Health server started on port 7860")
    server.serve_forever()


# ---------- Google Sheets ----------

def get_google_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    if GOOGLE_CREDS_JSON:
        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        # Fallback: читаем файл (для локального запуска)
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_worksheet(spreadsheet, title, headers):
    try:
        sheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=title, rows=10000, cols=len(headers))
        sheet.append_row(headers)
    return sheet


# ---------- Парсер сообщений ----------

def parse_message(text: str) -> dict | None:
    """
    Парсит сообщение формата:
    Zachislenie
    Summa 13.00 TJS
    Komis 0.00 TJS
    Zachislenie 13.00 TJS
    Data 09:22 03.03.26
    Otpravitel 9929278***2545
    Kod 15363257462
    Karta 9762000002728433
    Balans 519.05 TJS
    """
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

        if data_raw:
            dt = datetime.strptime(data_raw, "%H:%M %d.%m.%y")
        else:
            dt = datetime.now()

        return {
            "date":        dt.strftime("%d.%m.%Y"),
            "time":        dt.strftime("%H:%M"),
            "summa":       summa,
            "komis":       komis,
            "zachislenie": zach,
            "otpravitel":  otprav,
            "kod":         kod,
            "karta":       karta,
            "balans":      balans,
        }
    except Exception as e:
        logger.error(f"Ошибка парсинга: {e}")
        return None


def update_daily_summary(summary_sheet, date_str, summa, komis, zach):
    records = summary_sheet.get_all_values()
    for i, row in enumerate(records[1:], start=2):
        if row and row[0] == date_str:
            count  = int(row[1]) + 1
            s_sum  = round(float(row[2]) + summa, 2)
            s_kom  = round(float(row[3]) + komis, 2)
            s_zach = round(float(row[4]) + zach, 2)
            summary_sheet.update(f"B{i}:E{i}", [[count, s_sum, s_kom, s_zach]])
            return
    summary_sheet.append_row([date_str, 1, round(summa, 2), round(komis, 2), round(zach, 2)])


# ---------- Обработчики бота ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post or update.message
    if not message or not message.text:
        return

    if CHANNEL_ID and message.chat.id != CHANNEL_ID:
        return

    data = parse_message(message.text)
    if not data:
        return

    try:
        client      = get_google_client()
        spreadsheet = client.open(SPREADSHEET_NAME)

        tx_sheet = get_or_create_worksheet(spreadsheet, "Транзакции", [
            "Дата", "Время", "Сумма (TJS)", "Комиссия (TJS)",
            "Зачисление (TJS)", "Отправитель", "Код", "Карта", "Баланс (TJS)"
        ])
        tx_sheet.append_row([
            data["date"], data["time"],
            data["summa"], data["komis"], data["zachislenie"],
            data["otpravitel"], data["kod"], data["karta"], data["balans"]
        ])

        summary_sheet = get_or_create_worksheet(spreadsheet, "Итоги по дням", [
            "Дата", "Кол-во", "Сумма (TJS)", "Комиссия (TJS)", "Зачислено (TJS)"
        ])
        update_daily_summary(summary_sheet, data["date"],
                             data["summa"], data["komis"], data["zachislenie"])

        logger.info(f"✅ {data['date']} | +{data['zachislenie']} TJS")

    except Exception as e:
        logger.error(f"❌ Ошибка Google Sheets: {e}")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today().strftime("%d.%m.%Y")
    try:
        client        = get_google_client()
        spreadsheet   = client.open(SPREADSHEET_NAME)
        summary_sheet = get_or_create_worksheet(spreadsheet, "Итоги по дням", [
            "Дата", "Кол-во", "Сумма (TJS)", "Комиссия (TJS)", "Зачислено (TJS)"
        ])
        for row in summary_sheet.get_all_values()[1:]:
            if row and row[0] == today:
                await update.message.reply_text(
                    f"📊 *Итоги за {today}*\n"
                    f"Транзакций: `{row[1]}`\n"
                    f"Сумма: `{row[2]} TJS`\n"
                    f"Комиссия: `{row[3]} TJS`\n"
                    f"Зачислено: `{row[4]} TJS`",
                    parse_mode="Markdown"
                )
                return
        await update.message.reply_text(f"За {today} транзакций пока нет.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now          = datetime.now()
    month_suffix = now.strftime("%m.%Y")
    try:
        client        = get_google_client()
        spreadsheet   = client.open(SPREADSHEET_NAME)
        summary_sheet = get_or_create_worksheet(spreadsheet, "Итоги по дням", [
            "Дата", "Кол-во", "Сумма (TJS)", "Комиссия (TJS)", "Зачислено (TJS)"
        ])
        total = [0, 0.0, 0.0, 0.0]
        for row in summary_sheet.get_all_values()[1:]:
            if row and row[0].endswith(month_suffix):
                total[0] += int(row[1])
                total[1] += float(row[2])
                total[2] += float(row[3])
                total[3] += float(row[4])

        if total[0] == 0:
            await update.message.reply_text(f"За {now.strftime('%m.%Y')} данных нет.")
            return

        await update.message.reply_text(
            f"📅 *Итоги за {now.strftime('%m.%Y')}*\n"
            f"Транзакций: `{total[0]}`\n"
            f"Сумма: `{round(total[1],2)} TJS`\n"
            f"Комиссия: `{round(total[2],2)} TJS`\n"
            f"Зачислено: `{round(total[3],2)} TJS`",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


# ---------- Запуск ----------

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан! Добавьте его в Secrets на HuggingFace.")

    # Запускаем keep-alive сервер в отдельном потоке
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("month", cmd_month))

    logger.info("🤖 Бот запущен...")
    app.run_polling(allowed_updates=["message", "channel_post"])


if __name__ == "__main__":
    main()
