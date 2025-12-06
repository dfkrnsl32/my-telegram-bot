import os
import time
import threading
import sqlite3
from datetime import datetime

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ============================
# НАСТРОЙКИ — ВСТАВЬ СВОИ
# ============================
TOKEN = os.getenv("TELEGRAM_TOKEN")  # Токен бота
CRYPTOBOT_API = os.getenv("CRYPTOBOT_API")  # Токен CryptoBot
CHANNEL_ID = os.getenv("CHANNEL_ID")  # Ваш канал, например '@мой_канал'
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # Ваш Telegram ID
PRICE_USDT = float(os.getenv("PRICE_USDT", 5))  # Цена рекламы

# ============================
# БАЗА ДАННЫХ
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
    post_date TEXT
)
""")
db.commit()

# ============================
# СОЗДАНИЕ ИНВОЙСА
# ============================
def create_invoice(amount, description):
    url = "https://pay.crypt.bot/api/createInvoice"
    payload = {"amount": amount, "currency_type": "crypto", "asset": "USDT", "description": description}
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API}
    r = requests.post(url, headers=headers, json=payload).json()
    pay_url = r["result"]["pay_url"]
    invoice_id = r["result"]["invoice_id"]
    return pay_url, invoice_id

def check_invoice_status(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API}
    r = requests.get(url, headers=headers).json()
    status = r["result"]["items"][0]["status"]
    return status == "paid"

# ============================
# ФОНОВАЯ ПРОВЕРКА ОПЛАТ
# ============================
def payment_checker():
    while True:
        sql.execute("SELECT id, user_id, text, photo_file_id, invoice_id, post_date FROM ads WHERE paid = 0")
        for ad_id, user_id, text, photo_file_id, invoice_id, post_date in sql.fetchall():
            if check_invoice_status(invoice_id):
                sql.execute("UPDATE ads SET paid = 1 WHERE id = ?", (ad_id,))
                db.commit()
                bot.send_message(user_id, "✅ Оплата получена! Ваша реклама будет опубликована.")
                bot.send_message(ADMIN_ID, f"💰 Оплачено!\nЗаявка #{ad_id}\n{text}")
        time.sleep(15)

# ============================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================
bot = telebot.TeleBot(TOKEN)
threading.Thread(target=payment_checker, daemon=True).start()

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
# ОБРАБОТКА КНОПОК
# ============================
user_ads = {}

@bot.callback_query_handler(func=lambda c: True)
def callback(call):
    # ============================
    # ПРАЙС
    # ============================
    if call.data == "price":
        bot.send_message(call.message.chat.id, f"💵 Стоимость рекламы: {PRICE_USDT} USDT")
    # ============================
    # О БОТЕ
    # ============================
    elif call.data == "about":
        bot.send_message(call.message.chat.id, "ℹ️ Бот для заказа рекламы в Telegram канале. Оплата через USDT CryptoBot.")
    # ============================
    # ЗАКАЗ РЕКЛАМЫ
    # ============================
    elif call.data == "order":
        bot.send_message(call.message.chat.id, "✍ Отправьте текст рекламы:")
        bot.register_next_step_handler(call.message, get_ad_content)
    # ============================
    # АДМИН-ПАНЕЛЬ
    # ============================
    elif call.data == "admin_panel":
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ У вас нет доступа")
            return
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📄 Просмотр заявок", callback_data="view_ads"))
        bot.send_message(call.message.chat.id, "👑 Админ-панель:", reply_markup=kb)
    elif call.data == "view_ads":
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ У вас нет доступа")
            return
        sql.execute("SELECT id, user_id, text, paid FROM ads ORDER BY id DESC")
        ads = sql.fetchall()
        if not ads:
            bot.send_message(call.message.chat.id, "Нет заявок.")
        else:
            for ad in ads:
                ad_id, user_id, text, paid = ad
                status = "✅ Оплачено" if paid else "❌ Не оплачено"
                bot.send_message(call.message.chat.id, f"Заявка #{ad_id}\nПользователь: {user_id}\nТекст: {text}\nСтатус: {status}")

# ============================
# ПОЛУЧЕНИЕ ТЕКСТА РЕКЛАМЫ
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
# СОЗДАНИЕ ИНВОЙСА И СОХРАНЕНИЕ ЗАЯВКИ
# ============================
def create_ad_invoice(message):
    user_id = message.from_user.id
    ad = user_ads.pop(user_id)
    text = ad["text"]
    photo = ad["photo"]
    pay_url, invoice_id = create_invoice(PRICE_USDT, "Оплата рекламы")
    sql.execute("INSERT INTO ads (user_id, text, photo_file_id, invoice_id, post_date) VALUES (?, ?, ?, ?, ?)",
                (user_id, text, photo, invoice_id, datetime.now().strftime("%Y-%m-%d")))
    db.commit()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Оплатить USDT", url=pay_url))
    bot.send_message(user_id, f"💵 Стоимость: {PRICE_USDT} USDT\nПерейдите по кнопке для оплаты:", reply_markup=kb)
    bot.send_message(ADMIN_ID, f"🆕 Новая заявка!\nПользователь: {user_id}\nТекст: {text}")

# ============================
# ЗАПУСК БОТА
# ============================
bot.infinity_polling()
