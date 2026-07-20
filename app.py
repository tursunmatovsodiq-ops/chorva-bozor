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
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
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

CATEGORIES = ["🐄 Mol", "🐑 Qo'y", "🐐 Echki", "🐔 Parranda", "🌾 Yem-hashak"]

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
    # Migratsiya: agar eski bazada "hudud" ustuni yo'q bo'lsa, uni xavfsiz qo'shamiz
    # (mavjud ma'lumotlar hech qanday yo'qolmaydi)
    cur.execute("PRAGMA table_info(listings)")
    existing_cols = [row[1] for row in cur.fetchall()]
    if "hudud" not in existing_cols:
        cur.execute("ALTER TABLE listings ADD COLUMN hudud TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def add_listing(telegram_id, kategoriya, tavsif, narx, photo_file_ids, lat, lon, telefon, hudud=""):
    """photo_file_ids — bitta yoki bir nechta file_id, '|' bilan ajratilgan"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO listings
           (telegram_id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon, hudud, status, sana)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (telegram_id, kategoriya, tavsif, narx, photo_file_ids, lat, lon, telefon, hudud, datetime.now().isoformat()),
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


def set_listing_photos(listing_id, photo_file_ids):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE listings SET photo_file_id = ? WHERE id = ?", (photo_file_ids, listing_id))
    conn.commit()
    conn.close()


def get_listing(listing_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
    row = cur.fetchone()
    conn.close()
    return row


def search_listings(kategoriya=None, search_text=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    query = """SELECT id, kategoriya, tavsif, narx, photo_file_id, lat, lon, telefon, hudud FROM listings
               WHERE status = 'approved'"""
    params = []
    if kategoriya and kategoriya != "all":
        query += " AND kategoriya = ?"
        params.append(kategoriya)
    if search_text:
        query += " AND (tavsif LIKE ? OR hudud LIKE ?)"
        params.append(f"%{search_text}%")
        params.append(f"%{search_text}%")
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_my_listings(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, kategoriya, tavsif, narx, photo_file_id, status, sana, hudud FROM listings
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


def photo_ids_to_urls(photo_file_id_field):
    """'id1|id2|id3' -> ['/photo/id1', '/photo/id2', '/photo/id3']"""
    if not photo_file_id_field:
        return []
    ids = [x for x in photo_file_id_field.split("|") if x]
    return [f"/photo/{fid}" for fid in ids]


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
    search_text = request.args.get("q", "").strip()
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    rows = search_listings(kategoriya, search_text if search_text else None)

    result = []
    for id_, kat, tavsif, narx, photo_file_id, item_lat, item_lon, telefon, hudud in rows:
        dist = None
        if lat is not None and lon is not None and item_lat is not None and item_lon is not None:
            dist = round(distance_km(lat, lon, item_lat, item_lon), 1)
        photo_urls = photo_ids_to_urls(photo_file_id)
        result.append(
            {
                "id": id_,
                "kategoriya": kat,
                "tavsif": tavsif,
                "narx": narx,
                "photo_url": photo_urls[0] if photo_urls else "",
                "photo_urls": photo_urls,
                "telefon": telefon,
                "distance_km": dist,
                "lat": item_lat,
                "lon": item_lon,
                "hudud": hudud or "",
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


def resolve_user_id(init_data: str, fallback_user_id: str):
    """
    Avval imzolangan initData orqali tekshirishga harakat qiladi (eng xavfsiz).
    Agar Telegram klienti initData yubormasa (ba'zi eski versiyalarda uchraydi),
    ehtiyot chorasi sifatida, faqat ID raqamiga ishonib davom etadi.
    """
    user = verify_telegram_init_data(init_data)
    if user:
        return user["id"], True  # (id, tasdiqlangan_imzo_bilanmi)

    if fallback_user_id:
        try:
            uid = int(fallback_user_id)
            logger.warning("Imzosiz (fallback) autentifikatsiya ishlatildi: user_id=%s", uid)
            return uid, False
        except (TypeError, ValueError):
            pass

    return None, False


@flask_app.route("/api/my-listings")
def api_my_listings():
    telegram_id, _ = resolve_user_id(
        request.args.get("init_data", ""), request.args.get("user_id", "")
    )
    if not telegram_id:
        return jsonify({"error": "Tasdiqlanmagan so'rov. Mini App'ni Telegram orqali oching."}), 401

    rows = get_my_listings(telegram_id)
    result = []
    for id_, kat, tavsif, narx, photo_file_id, status, sana, hudud in rows:
        photo_urls = photo_ids_to_urls(photo_file_id)
        result.append(
            {
                "id": id_,
                "kategoriya": kat,
                "tavsif": tavsif,
                "narx": narx,
                "photo_url": photo_urls[0] if photo_urls else "",
                "photo_urls": photo_urls,
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "sana": sana,
                "hudud": hudud or "",
            }
        )
    return jsonify(result)


@flask_app.route("/api/mark-sold", methods=["POST"])
def api_mark_sold():
    data = request.get_json(force=True)
    telegram_id, _ = resolve_user_id(data.get("init_data", ""), data.get("user_id", ""))
    if not telegram_id:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401

    listing_id = data.get("id")
    if not listing_id:
        return jsonify({"success": False, "error": "id kerak"}), 400
    success = mark_as_sold(listing_id, telegram_id)
    return jsonify({"success": success})


@flask_app.route("/api/delete-listing", methods=["POST"])
def api_delete_listing():
    data = request.get_json(force=True)
    telegram_id, _ = resolve_user_id(data.get("init_data", ""), data.get("user_id", ""))
    if not telegram_id:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401

    listing_id = data.get("id")
    if not listing_id:
        return jsonify({"success": False, "error": "id kerak"}), 400
    success = delete_own_listing(listing_id, telegram_id)
    return jsonify({"success": success})


@flask_app.route("/api/create-listing", methods=["POST"])
def api_create_listing():
    try:
        telegram_id, _ = resolve_user_id(
            request.form.get("init_data", ""), request.form.get("user_id", "")
        )
        if not telegram_id:
            return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov. Mini App'ni Telegram orqali oching."}), 401

        kategoriya = request.form.get("kategoriya", "").strip()
        tavsif = request.form.get("tavsif", "").strip()
        narx = request.form.get("narx", "").strip()
        telefon = request.form.get("telefon", "").strip()
        hudud = request.form.get("hudud", "").strip()
        lat = float(request.form.get("lat"))
        lon = float(request.form.get("lon"))
        photos = request.files.getlist("photos")[:3]  # eng ko'pi bilan 3 ta rasm

        if not all([kategoriya, tavsif, narx, telefon]) or not photos:
            return jsonify({"success": False, "error": "Barcha maydonlarni to'ldiring"}), 400

        # Avval bo'sh rasm bilan yozuvni yaratamiz (id olish uchun)
        listing_id = add_listing(telegram_id, kategoriya, tavsif, narx, "", lat, lon, telefon, hudud)

        admin_caption = (
            f"🆕 Yangi e'lon (Mini App orqali, ID: {listing_id}):\n\n"
            f"Turi: {kategoriya}\nHudud: {hudud}\nTavsif: {tavsif}\nNarx: {narx}\nTelefon: {telefon}"
        )
        admin_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash", "callback_data": f"admin:approve:{listing_id}"},
                    {"text": "❌ Rad etish", "callback_data": f"admin:reject:{listing_id}"},
                ]
            ]
        }

        file_ids = []
        target_chat = ADMIN_ID if ADMIN_ID else telegram_id

        for i, photo in enumerate(photos):
            try:
                data = {"chat_id": target_chat}
                if i == 0:
                    # Faqat birinchi rasmga tavsif va tasdiqlash tugmalarini qo'shamiz
                    data["caption"] = admin_caption
                    data["reply_markup"] = json.dumps(admin_keyboard)
                resp = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data=data,
                    files={"photo": (photo.filename, photo.stream, photo.mimetype)},
                    timeout=20,
                )
                result = resp.json()
                if result.get("ok"):
                    file_ids.append(result["result"]["photo"][-1]["file_id"])
            except Exception as e:
                logger.warning("Rasm %d ni yuborishda xatolik: %s", i, e)

        if file_ids:
            set_listing_photos(listing_id, "|".join(file_ids))

        # Foydalanuvchiga tasdiq xabari
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": telegram_id,
                    "text": "Rahmat! 🙌 E'loningiz admin tomonidan tez orada ko'rib chiqiladi.",
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

# ---------------------------------------------------------------------------
# /start — ILOVANI OCHISH TUGMASI
# ---------------------------------------------------------------------------

def get_mini_app_url():
    if not MINI_APP_URL:
        return None
    # Har safar yangi "vaqt belgisi" qo'shiladi — shunda Telegram sahifani
    # hech qachon eski (keshlangan) holatda ko'rsatmaydi, har doim eng yangisini oladi
    return f"{MINI_APP_URL}?v={int(time.time())}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fresh_url = get_mini_app_url()
    if fresh_url:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🐄 Chorva Bozorni ochish", web_app=WebAppInfo(url=fresh_url))]]
        )
    else:
        keyboard = None

    text = (
        "Assalomu alaykum! 👋🐄🐑🐐\n\n"
        "Chorva Bozorga xush kelibsiz — mol, qo'y, echki, parranda va "
        "yem-hashak sotish yoki topish endi bir necha tugma ichida!\n\n"
        "Boshlash uchun pastdagi tugmani bosing 👇"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    else:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)


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
            await context.bot.send_message(chat_id=telegram_id, text="🎉 Ajoyib! E'loningiz tasdiqlandi va endi katalogda ko'rinadi.")
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)
    else:
        set_listing_status(listing_id, "rejected")
        await query.message.reply_text(f"❌ Rad etildi: {tavsif}")
        try:
            await context.bot.send_message(chat_id=telegram_id, text="Afsuski, e'loningiz hozircha tasdiqlanmadi. Savol bo'lsa, admin bilan bog'laning.")
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Xatolik yuz berdi: %s", context.error)


# ---------------------------------------------------------------------------
# ASOSIY FUNKSIYA
# ---------------------------------------------------------------------------

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT)


async def post_init(application: Application) -> None:
    """Bot ishga tushganda, doimiy 'Menu Button'ni sozlaymiz — shunda foydalanuvchi
    /start yozmasdan, har doim pastdagi tugma orqali ilovani ocha oladi."""
    if MINI_APP_URL:
        try:
            from telegram import MenuButtonWebApp

            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🐄 Bozorni ochish", web_app=WebAppInfo(url=MINI_APP_URL)
                )
            )
            logger.info("Doimiy Menu Button muvaffaqiyatli sozlandi")
        except Exception as e:
            logger.warning("Menu Button sozlashda xatolik: %s", e)


def main():
    init_db()

    if BOT_TOKEN == "SIZNING_BOT_TOKENINGIZ_BU_YERGA":
        print("XATOLIK: BOT_TOKEN o'rnatilmagan!")
        return

    # Flask serverni fon rejimida (alohida thread'da) ishga tushiramiz
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"Mini App serveri {PORT}-portda ishga tushdi...")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_decision, pattern="^admin:"))
    app.add_error_handler(error_handler)

    print("Chorva Bozor bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
