"""
Chorva Bozor — Bot + Mini App (bitta faylda, birga ishlaydi)
================================================================
Bu fayl ikkita narsani BIR VAQTDA ishga tushiradi:
1. Telegram bot (avvalgi bot.py bilan bir xil funksiyalar)
2. Kichik veb-server (Flask) — Mini App sahifasini va API'ni beradi

Kerakli kutubxonalar: requirements.txt ga qarang

Muhit o'zgaruvchilari:
    BOT_TOKEN     - @BotFather dan olingan token
    ADMIN_ID      - Sizning shaxsiy Telegram ID raqamingiz
    MINI_APP_URL  - Railway bergan havola (masalan https://xxxx.up.railway.app)
                    Bu deploy qilingandan KEYIN ma'lum bo'ladi, boshida bo'sh qoldirsa ham bo'ladi
    PORT          - Railway avtomatik beradi, noutbukda test qilishda 8000 ishlatiladi
"""

import logging
import os
import sqlite3
import math
import threading
import json
import hmac
import hashlib
import urllib.parse
import time
from datetime import datetime

import requests
from flask import Flask, jsonify, request, redirect, render_template

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# SOZLAMALAR
# ---------------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "SIZNING_BOT_TOKENINGIZ_BU_YERGA")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
MINI_APP_URL = os.environ.get("MINI_APP_URL", "")
PORT = int(os.environ.get("PORT", "8000"))

# DB_DIR — Railway'da "Volume" (doimiy xotira) ulanganda shu yerga yozing (masalan /data)
# Agar DB_DIR o'rnatilmagan bo'lsa, oddiy joyga yoziladi (lekin bu holda Railway qayta
# ishga tushganda ma'lumot yo'qolishi mumkin — shuning uchun volume qo'shish tavsiya etiladi)
DB_DIR = os.environ.get("DB_DIR", os.path.dirname(__file__))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "chorva.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

CATEGORIES = ["🐄 Mol", "🐑 Qo'y", "🐐 Echki"]

# ---------------------------------------------------------------------------
# XAVFSIZLIK: Telegram Mini App'dan kelgan initData'ni tekshirish
# ---------------------------------------------------------------------------
# Bu funksiya Telegramning rasmiy yo'riqnomasiga asoslangan: initData ichidagi
# "hash" qiymatini bot tokeni yordamida qayta hisoblab, mos kelishini tekshiradi.
# Agar mos kelmasa — bu ma'lumot soxta (birov o'zgartirgan) degani, va rad etiladi.


def verify_telegram_init_data(init_data: str):
    if not init_data:
        logger.warning("initData bo'sh keldi (uzunligi: 0)")
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            logger.warning("initData ichida 'hash' topilmadi. Kalitlar: %s", list(parsed.keys()))
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            logger.warning(
                "Hash mos kelmadi. Kutilgan(qisqartirilgan)=%s..., Kelgan(qisqartirilgan)=%s..., BOT_TOKEN uzunligi=%d",
                computed_hash[:10], received_hash[:10], len(BOT_TOKEN),
            )
            return None

        user_json = parsed.get("user")
        if not user_json:
            logger.warning("initData'da 'user' maydoni topilmadi")
            return None
        user = json.loads(user_json)
        return user  # {"id": ..., "first_name": ..., ...}
    except Exception as e:
        logger.warning("initData tekshirishda xatolik: %s", e)
        return None

(
    CHOOSING_MODE,
    SELL_CATEGORY,
    SELL_PHOTO,
    SELL_DESC,
    SELL_PRICE,
    SELL_LOCATION,
    SELL_PHONE,
    SELL_CONFIRM,
    BUY_CATEGORY,
    BUY_LOCATION,
) = range(10)


# ---------------------------------------------------------------------------
# MA'LUMOTLAR BAZASI
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            kategoriya TEXT,
            tavsif TEXT,
            narx TEXT,
            photo_file_id TEXT,
            lat REAL,
            lon REAL,
            telefon TEXT,
            status TEXT DEFAULT 'pending',
            sana TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def add_listing(telegram_id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO listings
           (telegram_id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon, status, sana)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (telegram_id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon, datetime.now().isoformat()),
    )
    conn.commit()
    listing_id = cur.lastrowid
    conn.close()
    return listing_id


def set_listing_status(listing_id, status):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE listings SET status = ? WHERE id = ?", (status, listing_id))
    conn.commit()
    conn.close()


def set_listing_photo(listing_id, photo_file_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE listings SET photo_file_id = ? WHERE id = ?", (photo_file_id, listing_id))
    conn.commit()
    conn.close()


def get_listing(listing_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
    row = cur.fetchone()
    conn.close()
    return row


def search_listings(kategoriya=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if kategoriya and kategoriya != "all":
        cur.execute(
            """SELECT id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon FROM listings
               WHERE kategoriya = ? AND status = 'approved'""",
            (kategoriya,),
        )
    else:
        cur.execute(
            """SELECT id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon FROM listings
               WHERE status = 'approved'"""
        )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_my_listings(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, kategoriya, tavsif, narx, photo_file_id, status, sana FROM listings
           WHERE telegram_id = ? ORDER BY id DESC""",
        (telegram_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def mark_as_sold(listing_id, telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE listings SET status = 'sold' WHERE id = ? AND telegram_id = ?",
        (listing_id, telegram_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_own_listing(listing_id, telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM listings WHERE id = ? AND telegram_id = ?",
        (listing_id, telegram_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def distance_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return R * c


# ---------------------------------------------------------------------------
# FLASK — MINI APP SERVERI
# ---------------------------------------------------------------------------

flask_app = Flask(__name__)


@flask_app.route("/")
def index():
    resp = flask_app.make_response(render_template("index.html"))
    # Telegram Mini App sahifani keshlab qo'ymasligi uchun — har doim eng yangi versiyani olib turadi
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@flask_app.route("/api/listings")
def api_listings():
    kategoriya = request.args.get("kategoriya", "all")
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    rows = search_listings(kategoriya)

    result = []
    for id_, kat, tavsif, narx, photo_file_id, item_lat, item_lon, telefon in rows:
        dist = None
        if lat is not None and lon is not None and item_lat is not None and item_lon is not None:
            dist = round(distance_km(lat, lon, item_lat, item_lon), 1)
        result.append(
            {
                "id": id_,
                "kategoriya": kat,
                "tavsif": tavsif,
                "narx": narx,
                "photo_url": f"/photo/{photo_file_id}",
                "telefon": telefon,
                "distance_km": dist,
                "lat": item_lat,
                "lon": item_lon,
            }
        )

    # Masofa borligicha yaqinlik bo'yicha, aks holda ID bo'yicha tartiblash
    if lat is not None and lon is not None:
        result.sort(key=lambda x: (x["distance_km"] is None, x["distance_km"]))
    else:
        result.sort(key=lambda x: -x["id"])

    return jsonify(result)


@flask_app.route("/photo/<file_id>")
def photo(file_id):
    # Telegram serveridan rasmning haqiqiy manzilini so'raymiz
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id}, timeout=10
        )
        data = resp.json()
        file_path = data["result"]["file_path"]
        return redirect(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
    except Exception as e:
        logger.warning("Rasmni olishda xatolik: %s", e)
        return "", 404


STATUS_LABELS = {
    "pending": "⏳ Tekshirilmoqda",
    "approved": "✅ Faol",
    "rejected": "❌ Rad etilgan",
    "sold": "💰 Sotildi",
}


@flask_app.route("/api/my-listings")
def api_my_listings():
    user = verify_telegram_init_data(request.args.get("init_data", ""))
    if not user:
        return jsonify({"error": "Tasdiqlanmagan so'rov. Mini App'ni Telegram orqali oching."}), 401
    telegram_id = user["id"]

    rows = get_my_listings(telegram_id)
    result = []
    for id_, kat, tavsif, narx, photo_file_id, status, sana in rows:
        result.append(
            {
                "id": id_,
                "kategoriya": kat,
                "tavsif": tavsif,
                "narx": narx,
                "photo_url": f"/photo/{photo_file_id}",
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "sana": sana,
            }
        )
    return jsonify(result)


@flask_app.route("/api/mark-sold", methods=["POST"])
def api_mark_sold():
    data = request.get_json(force=True)
    user = verify_telegram_init_data(data.get("init_data", ""))
    if not user:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401
    telegram_id = user["id"]

    listing_id = data.get("id")
    if not listing_id:
        return jsonify({"success": False, "error": "id kerak"}), 400
    success = mark_as_sold(listing_id, telegram_id)
    return jsonify({"success": success})


@flask_app.route("/api/delete-listing", methods=["POST"])
def api_delete_listing():
    data = request.get_json(force=True)
    user = verify_telegram_init_data(data.get("init_data", ""))
    if not user:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401
    telegram_id = user["id"]

    listing_id = data.get("id")
    if not listing_id:
        return jsonify({"success": False, "error": "id kerak"}), 400
    success = delete_own_listing(listing_id, telegram_id)
    return jsonify({"success": success})


@flask_app.route("/api/create-listing", methods=["POST"])
def api_create_listing():
    try:
        user = verify_telegram_init_data(request.form.get("init_data", ""))
        if not user:
            return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov. Mini App'ni Telegram orqali oching."}), 401
        telegram_id = user["id"]

        kategoriya = request.form.get("kategoriya", "").strip()
        tavsif = request.form.get("tavsif", "").strip()
        narx = request.form.get("narx", "").strip()
        telefon = request.form.get("telefon", "").strip()
        lat = float(request.form.get("lat"))
        lon = float(request.form.get("lon"))
        photo = request.files.get("photo")

        if not all([kategoriya, tavsif, narx, telefon, photo]):
            return jsonify({"success": False, "error": "Barcha maydonlarni to'ldiring"}), 400

        # Avval bo'sh rasm bilan yozuvni yaratamiz (id olish uchun)
        listing_id = add_listing(telegram_id, kategoriya, tavsif, narx, "", lat, lon, telefon)

        # Rasmni Telegram'ga (admin chatiga) yuborib, undan file_id olamiz
        admin_caption = (
            f"🆕 Yangi e'lon (Mini App orqali, ID: {listing_id}):\n\n"
            f"Turi: {kategoriya}\nTavsif: {tavsif}\nNarx: {narx}\nTelefon: {telefon}"
        )
        admin_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash", "callback_data": f"admin:approve:{listing_id}"},
                    {"text": "❌ Rad etish", "callback_data": f"admin:reject:{listing_id}"},
                ]
            ]
        }

        file_id = None
        if ADMIN_ID:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": ADMIN_ID,
                    "caption": admin_caption,
                    "reply_markup": json.dumps(admin_keyboard),
                },
                files={"photo": (photo.filename, photo.stream, photo.mimetype)},
                timeout=20,
            )
            result = resp.json()
            if result.get("ok"):
                photos = result["result"]["photo"]
                file_id = photos[-1]["file_id"]

        if not file_id:
            # Admin sozlanmagan yoki xatolik bo'lsa, e'lonni foydalanuvchining o'ziga yuboramiz
            photo.stream.seek(0)
            resp2 = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": telegram_id},
                files={"photo": (photo.filename, photo.stream, photo.mimetype)},
                timeout=20,
            )
            result2 = resp2.json()
            if result2.get("ok"):
                file_id = result2["result"]["photo"][-1]["file_id"]

        if file_id:
            set_listing_photo(listing_id, file_id)

        # Foydalanuvchiga tasdiq xabari
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": telegram_id,
                    "text": "Rahmat! E'loningiz admin tomonidan tekshirilmoqda.",
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning("Foydalanuvchiga xabar yuborishda xatolik: %s", e)

        return jsonify({"success": True, "id": listing_id})

    except Exception as e:
        logger.warning("E'lon yaratishda xatolik: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# TELEGRAM BOT — YORDAMCHI
# ---------------------------------------------------------------------------

def build_keyboard(options, prefix, columns=2):
    buttons = [InlineKeyboardButton(opt, callback_data=f"{prefix}:{opt}") for opt in options]
    rows = [buttons[i : i + columns] for i in range(0, len(buttons), columns)]
    return InlineKeyboardMarkup(rows)


location_keyboard = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Joylashuvni yuborish", request_location=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


# ---------------------------------------------------------------------------
# /start VA REJIM TANLASH
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    buttons = [
        [InlineKeyboardButton("🐮 Sotmoqchiman", callback_data="mode:sell")],
        [InlineKeyboardButton("🔍 Qidiryapman", callback_data="mode:buy")],
    ]
    if MINI_APP_URL:
        # Har safar yangi "vaqt belgisi" qo'shiladi — shunda Telegram sahifani
        # hech qachon eski (keshlangan) holatda ko'rsatmaydi, har doim eng yangisini oladi
        fresh_url = f"{MINI_APP_URL}?v={int(time.time())}"
        buttons.append([InlineKeyboardButton("📸 Katalogni ochish", web_app=WebAppInfo(url=fresh_url))])
    keyboard = InlineKeyboardMarkup(buttons)
    text = (
        "Assalomu alaykum! Chorva Bozor botiga xush kelibsiz 🐄🐑🐐\n\n"
        "🐮 Mol/qo'y/echki sotmoqchimisiz?\n"
        "🔍 Yoki qidiryapsizmi?\n"
        "📸 Yoki rasmli katalogni ko'rmoqchimisiz?"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    return CHOOSING_MODE


async def mode_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode = query.data.split(":")[1]

    if mode == "sell":
        keyboard = build_keyboard(CATEGORIES, "sellcat")
        await query.edit_message_text("Qaysi turdagi chorva sotmoqchisiz?", reply_markup=keyboard)
        return SELL_CATEGORY
    else:
        keyboard = build_keyboard(CATEGORIES, "buycat")
        await query.edit_message_text("Qaysi turdagi chorva kerak?", reply_markup=keyboard)
        return BUY_CATEGORY


# ---------------------------------------------------------------------------
# SOTISH OQIMI
# ---------------------------------------------------------------------------

async def sell_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["kategoriya"] = query.data.split(":")[1]
    await query.edit_message_text("📸 Hayvonning rasmini yuboring:")
    return SELL_PHOTO


async def sell_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("Iltimos, rasm yuboring.")
        return SELL_PHOTO
    photo_file_id = update.message.photo[-1].file_id
    context.user_data["photo_file_id"] = photo_file_id
    await update.message.reply_text(
        "✍️ Endi qisqacha tavsif kiriting (yoshi, jinsi, holati). Masalan: \"3 yoshli qorabayir sigir, sog'in\""
    )
    return SELL_DESC


async def sell_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["tavsif"] = update.message.text.strip()
    await update.message.reply_text("💰 Narxini kiriting:")
    return SELL_PRICE


async def sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["narx"] = update.message.text.strip()
    await update.message.reply_text(
        "📍 Endi joylashuvingizni yuboring:", reply_markup=location_keyboard
    )
    return SELL_LOCATION


async def sell_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.location:
        await update.message.reply_text("Iltimos, pastdagi tugma orqali joylashuvingizni yuboring.")
        return SELL_LOCATION
    loc = update.message.location
    context.user_data["lat"] = loc.latitude
    context.user_data["lon"] = loc.longitude
    await update.message.reply_text("📞 Telefon raqamingizni kiriting:", reply_markup=ReplyKeyboardRemove())
    return SELL_PHONE


async def sell_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["telefon"] = update.message.text.strip()
    d = context.user_data
    caption = (
        f"✅ E'loningiz:\n\nTuri: {d['kategoriya']}\nTavsif: {d['tavsif']}\n"
        f"Narx: {d['narx']}\nTelefon: {d['telefon']}\n\nTo'g'rimi?"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Ha, yuborish", callback_data="sellconfirm:yes")],
            [InlineKeyboardButton("❌ Bekor qilish", callback_data="sellconfirm:no")],
        ]
    )
    await update.message.reply_photo(photo=d["photo_file_id"], caption=caption, reply_markup=keyboard)
    return SELL_CONFIRM


async def sell_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]

    if choice == "no":
        await query.message.reply_text("Bekor qilindi. /start bilan qayta boshlang.")
        return ConversationHandler.END

    d = context.user_data
    telegram_id = update.effective_user.id
    listing_id = add_listing(
        telegram_id, d["kategoriya"], d["tavsif"], d["narx"], d["photo_file_id"], d["lat"], d["lon"], d["telefon"]
    )

    await query.message.reply_text("Rahmat! E'loningiz admin tomonidan tekshirilmoqda.")

    if ADMIN_ID:
        admin_caption = (
            f"🆕 Yangi e'lon (ID: {listing_id}):\n\nTuri: {d['kategoriya']}\n"
            f"Tavsif: {d['tavsif']}\nNarx: {d['narx']}\nTelefon: {d['telefon']}"
        )
        admin_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"admin:approve:{listing_id}"),
                    InlineKeyboardButton("❌ Rad etish", callback_data=f"admin:reject:{listing_id}"),
                ]
            ]
        )
        try:
            await context.bot.send_photo(
                chat_id=ADMIN_ID, photo=d["photo_file_id"], caption=admin_caption, reply_markup=admin_keyboard
            )
        except Exception as e:
            logger.warning("Adminga xabar yuborishda xatolik: %s", e)

    context.user_data.clear()
    return ConversationHandler.END


async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, listing_id_str = query.data.split(":")
    listing_id = int(listing_id_str)
    row = get_listing(listing_id)

    if not row:
        await query.message.reply_text("E'lon topilmadi.")
        return

    telegram_id = row[1]
    tavsif = row[3]

    if action == "approve":
        set_listing_status(listing_id, "approved")
        await query.message.reply_text(f"✅ Tasdiqlandi: {tavsif}")
        try:
            await context.bot.send_message(chat_id=telegram_id, text="🎉 E'loningiz tasdiqlandi!")
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)
    else:
        set_listing_status(listing_id, "rejected")
        await query.message.reply_text(f"❌ Rad etildi: {tavsif}")
        try:
            await context.bot.send_message(chat_id=telegram_id, text="Afsuski, e'loningiz rad etildi.")
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)


# ---------------------------------------------------------------------------
# QIDIRISH OQIMI (bot ichida, matn tarzida - Mini App'ga qo'shimcha)
# ---------------------------------------------------------------------------

async def buy_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["kategoriya"] = query.data.split(":")[1]
    await query.edit_message_text("📍 Joylashuvingizni yuboring:")
    await query.message.reply_text("Pastdagi tugmani bosing:", reply_markup=location_keyboard)
    return BUY_LOCATION


async def buy_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.location:
        await update.message.reply_text("Iltimos, pastdagi tugma orqali joylashuvingizni yuboring.")
        return BUY_LOCATION

    user_lat = update.message.location.latitude
    user_lon = update.message.location.longitude
    kategoriya = context.user_data.get("kategoriya")

    rows = search_listings(kategoriya)
    await update.message.reply_text("Qidirilmoqda...", reply_markup=ReplyKeyboardRemove())

    if not rows:
        await update.message.reply_text(f"Afsuski, {kategoriya} bo'yicha hozircha e'lon yo'q.")
        context.user_data.clear()
        return ConversationHandler.END

    with_distance = []
    for id_, kat, tavsif, narx, photo_file_id, lat, lon, telefon in rows:
        dist = distance_km(user_lat, user_lon, lat, lon)
        with_distance.append((dist, tavsif, narx, photo_file_id, telefon))
    with_distance.sort(key=lambda x: x[0])

    await update.message.reply_text(f"📋 Topildi ({len(with_distance)} ta), eng yaqinidan boshlab:")
    for dist, tavsif, narx, photo_file_id, telefon in with_distance[:10]:
        caption = f"{tavsif}\n💰 {narx}\n📍 {dist:.1f} km uzoqlikda\n📞 {telefon}"
        try:
            await update.message.reply_photo(photo=photo_file_id, caption=caption)
        except Exception as e:
            logger.warning("Rasm yuborishda xatolik: %s", e)
            await update.message.reply_text(caption)

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi. /start bilan qayta boshlang.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Xatolik yuz berdi: %s", context.error)


# ---------------------------------------------------------------------------
# ASOSIY FUNKSIYA
# ---------------------------------------------------------------------------

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


def main():
    init_db()

    if BOT_TOKEN == "SIZNING_BOT_TOKENINGIZ_BU_YERGA":
        print("XATOLIK: BOT_TOKEN o'rnatilmagan!")
        return

    # Flask serverni fon rejimida (alohida thread'da) ishga tushiramiz
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"Mini App serveri {PORT}-portda ishga tushdi...")

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_MODE: [CallbackQueryHandler(mode_chosen, pattern="^mode:")],
            SELL_CATEGORY: [CallbackQueryHandler(sell_category, pattern="^sellcat:")],
            SELL_PHOTO: [MessageHandler(filters.PHOTO, sell_photo)],
            SELL_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_desc)],
            SELL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_price)],
            SELL_LOCATION: [MessageHandler(filters.LOCATION, sell_location)],
            SELL_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_phone)],
            SELL_CONFIRM: [CallbackQueryHandler(sell_confirm, pattern="^sellconfirm:")],
            BUY_CATEGORY: [CallbackQueryHandler(buy_category, pattern="^buycat:")],
            BUY_LOCATION: [MessageHandler(filters.LOCATION, buy_location)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(admin_decision, pattern="^admin:"))
    app.add_error_handler(error_handler)

    print("Chorva Bozor bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
