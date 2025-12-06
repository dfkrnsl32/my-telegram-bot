import os
import threading
import sqlite3
from datetime import datetime, timedelta, timezone
from flask import Flask, request
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

# ============================
# НАСТРОЙКИ
# ============================
TOKEN = os.getenv("TELEGRAM_TOKEN")  # токен от BotFather
CRYPTOBOT_API = os.getenv("CRYPTOBOT_API")  # токен CryptoBot
CHANNEL_ID = os.getenv("CHANNEL_ID")  # например @мой_канал
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # твой Telegram ID
PRICE_USDT = 3  # цена рекламы
MSK = timezone(timedelta(hours=3))  # московское время

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
# ИНВОЙСЫ
# ============================
def create_invoice(amount, description):
    url = "https://pay.crypt.bot/api/createInvoice"
    payload = {"amount": amount, "currency_type": "crypto", "asset": "USDT", "description": description}
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API}
    r = requests.post(url, headers=headers, json=payload).json()
    return r["result"]["pay_url"], r["result"]["invoice_id"]

def check_invoice_status(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_API}
    r = requests.get(url, headers=headers).json()
    return r["result"]["items"][0]["status"] == "paid"

# ============================
# ФОНОВАЯ ПРОВЕРКА ОПЛАТ
# ============================
def payment_checker():
    import time
    while True:
        sql.execute("SELECT id, user_id, text, photo_file_id, invoice_id, post_date FROM ads WHERE paid = 0")
        for ad_id, user_id, text, photo_file_id, invoice_id, post_date in sql.fetchall():
            if check_invoice_status(invoice_id):
                sql.execute("UPDATE ads SET paid = 1 WHERE id = ?", (ad_id,))
                db.commit()
                bot.send_message(user_id, f"✅ Оплата получена!\nВаше объявление будет опубликовано.\n📅 Дата: {post_date}")
                bot.send_message(ADMIN_ID, f"💰 Оплачено!\nЗаявка #{ad_id}\n📅 {post_date}\n{text}")
        time.sleep(15)

# ============================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================
bot = telebot.TeleBot(TOKEN)
threading.Thread(target=payment_checker, daemon=True).start()
user_ads = {}

# ============================
# ФЛАСК ДЛЯ WEBHOOK
# ============================
app = Flask(_name_)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "OK", 200

# ============================
# КОМАНДА /START
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
# ОБРАБОТКА КНОПОК
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
        sql.execute("SELECT id, user_id, text, paid, post_date FROM ads ORDER BY id DESC")
        ads = sql.fetchall()
        if not ads:
            bot.send_message(call.message.chat.id, "Нет заявок.")
            return
        for ad_id, user_id, text, paid, post_date in ads:
            status = "✅ Оплачено" if paid else "❌ Не оплачено"
            bot.send_message(call.message.chat.id,
                f"📌 Заявка #{ad_id}\n👤 Пользователь: {user_id}\n📅 Дата: {post_date}\n📝 Текст: {text}\n💳 Статус: {status}")

# ============================
# ПОЛУЧЕНИЕ ТЕКСТА
# ============================
def get_ad_content(message):
    user_ads[message.from_user.id] = {"text": message.text, "photo": None, "date": None}
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(MSK) + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now(MSK) + timedelta(days=2)).strftime("%Y-%m-%d")
    kb.add(KeyboardButton("Сегодня"))
    kb.add(KeyboardButton("Завтра"))
    kb.add(KeyboardButton("Через 2 дня"))
    kb.add(KeyboardButton("Ввести вручную"))
    msg = bot.send_message(message.chat.id,
                           "📅 Выберите дату публикации:", reply_markup=kb)
    bot.register_next_step_handler(msg, get_post_date)

def get_post_date(message):
    user_id = message.from_user.id
    choice = message.text.lower()
    now = datetime.now(MSK)
    if choice == "сегодня":
        user_ads[user_id]["date"] = now.strftime("%Y-%m-%d")
    elif choice == "завтра":
        user_ads[user_id]["date"] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    elif choice == "через 2 дня":
        user_ads[user_id]["date"] = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        bot.send_message(message.chat.id, "✍ Введите дату вручную в формате ГГГГ-ММ-ДД")
        bot.register_next_step_handler(message, manual_date)
        return
    bot.send_message(message.chat.id, "📸 Теперь отправьте фото или напишите 'Пропустить'")
    
def manual_date(message):
    user_id = message.from_user.id
    try:
        dt = datetime.strptime(message.text, "%Y-%m-%d")
        user_ads[user_id]["date"] = dt.strftime("%Y-%m-%d")
        bot.send_message(message.chat.id, "📸 Теперь отправьте фото или напишите 'Пропустить'")
    except:
        bot.send_message(message.chat.id, "❌ Некорректный формат даты. Введите в формате ГГГГ-ММ-ДД")
        bot.register_next_step_handler(message, manual_date)

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
# СОЗДАНИЕ ИНВОЙСА
# ============================
def create_ad_invoice(message):
    user_id = message.from_user.id
    ad = user_ads.pop(user_id)
    text = ad["text"]
    photo = ad["photo"]
    post_date = ad["date"] if ad["date"] else datetime.now(MSK).strftime("%Y-%m-%d")

    pay_url, invoice_id = create_invoice(PRICE_USDT, "Оплата рекламы")

    sql.execute("INSERT INTO ads (user_id, text, photo_file_id, invoice_id, post_date) VALUES (?, ?, ?, ?, ?)",
                (user_id, text, photo, invoice_id, post_date))
    db.commit()

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("💳 Оплатить USDT", url=pay_url))

    bot.send_message(user_id,
        f"💵 Стоимость: {PRICE_USDT} USDT\n📅 Дата подачи: {post_date}\nНажмите кнопку для оплаты:",
        reply_markup=kb
    )
    bot.send_message(ADMIN_ID, f"🆕 Новая заявка!\nПользователь: {user_id}\n📅 {post_date}\n{text}")

# ============================
# ЗАПУСК ФЛАСК
# ============================
if _name_ == "_main_":
    bot.remove_webhook()
    bot.set_webhook(url=f"https://my-telegram-bot-ujca.onrender.com/{TOKEN}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
