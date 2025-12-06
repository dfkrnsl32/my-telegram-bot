import os
import threading
import sqlite3
import requests
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ============================
# Настройки
# ============================
TOKEN = os.getenv("TELEGRAM_TOKEN")          # Telegram токен из Environment Variables
CRYPTOBOT_API = os.getenv("CRYPTOBOT_API")  # CryptoBot API токен из Environment Variables
CHANNEL_ID = os.getenv("CHANNEL_ID")        # ID канала из Environment Variables
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))    # Telegram ID админа
PRICE_USDT = 3                               # цена рекламы
RENDER_DOMAIN = "my-telegram-bot-ujca.onrender.com"  # твой Render-домен

# ============================
# База данных
# ============================
db = sqlite3.connect("ads.db", check_same_thread=False)
sql = db.cursor()
sql.execute("""
CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    photo_file_id TEXT,
    invoice_id INTEGER,
    paid INTEGER DEFAULT 0,
    posted INTEGER DEFAULT 0,
    created_at TEXT
)
""")
db.commit()

# ============================
# CryptoBot API
# ============================
def create_invoice(amount, description):
    r = requests.post(
        "https://pay.crypt.bot/api/createInvoice",
        headers={"Crypto-Pay-API-Token": CRYPTOBOT_API},
        json={"amount": amount, "currency_type": "crypto", "asset": "USDT", "description": description}
    ).json()
    return r["result"]["pay_url"], r["result"]["invoice_id"]

def check_invoice_status(invoice_id):
    r = requests.get(
        f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}",
        headers={"Crypto-Pay-API-Token": CRYPTOBOT_API}
    ).json()
    return r["result"]["items"][0]["status"] == "paid"

# ============================
# Проверка оплаты в фоне
# ============================
def payment_checker():
    import time
    while True:
        sql.execute("SELECT id, user_id, text, invoice_id FROM ads WHERE paid=0")
        for ad_id, user_id, text, invoice_id in sql.fetchall():
            if check_invoice_status(invoice_id):
                sql.execute("UPDATE ads SET paid=1 WHERE id=?", (ad_id,))
                db.commit()
                bot.send_message(user_id, "✅ Оплата получена! Ваша реклама будет опубликована.")
                bot.send_message(ADMIN_ID, f"💰 Оплачено!\nЗаявка #{ad_id}\n{text}")
        time.sleep(15)

# ============================
# Инициализация бота
# ============================
bot = telebot.TeleBot(TOKEN)
threading.Thread(target=payment_checker, daemon=True).start()
user_ads = {}

# ============================
# Flask для webhook
# ============================
app = Flask(__name__)  # двойное подчеркивание __name__

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

# ============================
# Команда /start
# ============================
@bot.message_handler(commands=['start'])
def start(message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📩 Заказать рекламу", callback_data="order"))
    kb.add(InlineKeyboardButton("💰 Прайс", callback_data="price"))
    kb.add(InlineKeyboardButton("ℹ️ О боте", callback_data="about"))
    if message.from_user.id == ADMIN_ID:
        kb.add(InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel"))
    bot.send_message(message.chat.id, "👋 Привет! Выбери действие:", reply_markup=kb)

# ============================
# Кнопки
# ============================
@bot.callback_query_handler(func=lambda c: True)
def callback(call):
    if call.data == "price":
        bot.send_message(call.message.chat.id, f"💵 Стоимость рекламы: {PRICE_USDT} USDT")
    elif call.data == "about":
        bot.send_message(call.message.chat.id, "ℹ️ Бот для заказа рекламы через CryptoBot.")
    elif call.data == "order":
        bot.send_message(call.message.chat.id, "✍ Отправьте текст объявления:")
        bot.register_next_step_handler(call.message, get_ad_content)
    elif call.data == "admin_panel":
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Нет доступа")
            return
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📄 Просмотр заявок", callback_data="view_ads"))
        bot.send_message(call.message.chat.id, "👑 Админ-панель:", reply_markup=kb)
    elif call.data == "view_ads":
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Нет доступа")
            return
        sql.execute("SELECT id, user_id, text, paid, created_at FROM ads ORDER BY id DESC")
        for ad_id, user_id, text, paid, created_at in sql.fetchall():
            status = "✅ Оплачено" if paid else "❌ Не оплачено"
            bot.send_message(call.message.chat.id,
                             f"📌 Заявка #{ad_id}\n👤 Пользователь: {user_id}\n📝 Текст: {text}\n💳 Статус: {status}\n🕒 Дата: {created_at}")

# ============================
# Получение текста объявления
# ============================
def get_ad_content(message):
    user_ads[message.from_user.id] = {"text": message.text, "photo": None}
    bot.send_message(message.chat.id, "📸 Можно отправить фото или написать 'Пропустить'.")

@bot.message_handler(content_types=['photo'])
def get_photo(message):
    if message.from_user.id in user_ads:
        user_ads[message.from_user.id]["photo"] = message.photo[-1].file_id
        create_ad_invoice(message)

@bot.message_handler(func=lambda m: m.text and m.text.lower() == "пропустить")
def skip_photo(message):
    if message.from_user.id in user_ads:
        create_ad_invoice(message)

# ============================
# Создание инвойса
# ============================
def create_ad_invoice(message):
    from datetime import datetime
    user_id = message.from_user.id
    ad = user_ads.pop(user_id)
    text = ad["text"]
    photo = ad["photo"]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    pay_url, invoice_id = create_invoice(PRICE_USDT, "Оплата рекламы")
    sql.execute("INSERT INTO ads (user_id, text, photo_file_id, invoice_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, text, photo, invoice_id, created_at))
    db.commit()

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Оплатить USDT", url=pay_url))
    bot.send_message(user_id, f"💵 Стоимость: {PRICE_USDT} USDT\nНажмите кнопку для оплаты:", reply_markup=kb)
    bot.send_message(ADMIN_ID, f"🆕 Новая заявка!\nПользователь: {user_id}\n{text}\n🕒 {created_at}")

# ============================
# Запуск Flask
# ============================
if _name_ == "_main_":
    bot.remove_webhook()
    bot.set_webhook(url=f"https://{RENDER_DOMAIN}/{TOKEN}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
