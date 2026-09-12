"""
HUDUD YANGILIKLARI — Bot + Mini App (bitta faylda)
====================================================
Chorva Bozor kodi asosida qayta yozildi:
- 3 darajali hudud: O'zbekiston / Viloyat / Tuman
- Yangiliklar: rasm YOKI video (Telegram file_id bilan saqlanadi)
- Kommentariyalar
- Bot: qisqa matn + "Batafsil" tugmasi (ilovaga to'g'ri ulanadi)

Muhit o'zgaruvchilari:
    BOT_TOKEN     - @BotFather dan token
    ADMIN_ID      - Sizning Telegram ID'ingiz
    CHANNEL_ID    - Yangiliklar chiqadigan kanal ID (masalan -1001234567890) — ixtiyoriy
    MINI_APP_URL  - Railway havolasi
    BOT_USERNAME  - Bot nomi ("@" siz)
    PORT          - Port (Railway avtomatik beradi)
    DB_DIR        - Doimiy xotira papkasi (Railway Volume)
"""

import logging
import os
import sqlite3
import urllib.parse
import threading
import json
import hmac
import hashlib
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
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")  # kanal bo'sh bo'lsa, faqat admin ko'radi
MINI_APP_URL = os.environ.get("MINI_APP_URL", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
PORT = int(os.environ.get("PORT", "8000"))

DB_DIR = os.environ.get("DB_DIR", os.path.dirname(__file__))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "yangilik.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

LEVELS = {"uz": "O'zbekiston", "viloyat": "Viloyat", "tuman": "Tuman"}

STATUS_LABELS = {
    "pending": "⏳ Tekshirilmoqda",
    "approved": "✅ Faol",
    "rejected": "❌ Rad etilgan",
}


# ---------------------------------------------------------------------------
# XAVFSIZLIK: Telegram initData tekshiruvi
# ---------------------------------------------------------------------------

def verify_telegram_init_data(init_data: str):
    if not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, strict_parsing=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed_hash, received_hash):
            return None
        user_json = parsed.get("user")
        if not user_json:
            return None
        return json.loads(user_json)
    except Exception as e:
        logger.warning("initData tekshirishda xatolik: %s", e)
        return None


def resolve_user_id(init_data: str, fallback_user_id: str):
    user = verify_telegram_init_data(init_data)
    if user:
        return user["id"], user
    if fallback_user_id:
        try:
            return int(fallback_user_id), None
        except (TypeError, ValueError):
            pass
    return None, None


# ---------------------------------------------------------------------------
# MA'LUMOTLAR BAZASI
# ---------------------------------------------------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            sarlavha TEXT,
            matn TEXT,
            media TEXT DEFAULT '',
            media_types TEXT DEFAULT '',
            level TEXT DEFAULT 'uz',
            viloyat TEXT DEFAULT '',
            tuman TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            views INTEGER DEFAULT 0,
            sana TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            telegram_id INTEGER,
            ism TEXT DEFAULT '',
            matn TEXT,
            sana TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            ism_familya TEXT,
            telefon TEXT,
            avatar_file_id TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def add_news(telegram_id, sarlavha, matn, level, viloyat, tuman):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO news
           (telegram_id, sarlavha, matn, level, viloyat, tuman, status, sana)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (telegram_id, sarlavha, matn, level, viloyat, tuman, datetime.now().isoformat()),
    )
    conn.commit()
    news_id = cur.lastrowid
    conn.close()
    return news_id


def set_news_media(news_id, file_ids, media_types):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE news SET media = ?, media_types = ? WHERE id = ?",
        ("|".join(file_ids), "|".join(media_types), news_id),
    )
    conn.commit()
    conn.close()


def set_news_status(news_id, status):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE news SET status = ? WHERE id = ?", (status, news_id))
    conn.commit()
    conn.close()


def get_news(news_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM news WHERE id = ?", (news_id,))
    row = cur.fetchone()
    conn.close()
    return row


def search_news(level="uz", viloyat="", tuman="", search_text=""):
    """3 darajali qoida:
    - 'uz' tanlansa: barcha yangiliklar
    - viloyat tanlansa: respublika + o'sha viloyat + o'sha viloyatning tumanlari
    - tuman tanlansa: respublika + o'sha viloyat + o'sha tuman
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    query = "SELECT id, sarlavha, matn, media, media_types, level, viloyat, tuman, views, sana FROM news WHERE status = 'approved'"
    params = []
    if level == "viloyat" and viloyat:
        query += " AND (level = 'uz' OR (level = 'viloyat' AND viloyat = ?) OR (level = 'tuman' AND viloyat = ?))"
        params += [viloyat, viloyat]
    elif level == "tuman" and viloyat and tuman:
        query += " AND (level = 'uz' OR (level = 'viloyat' AND viloyat = ?) OR (level = 'tuman' AND viloyat = ? AND tuman = ?))"
        params += [viloyat, viloyat, tuman]
    if search_text:
        query += " AND (sarlavha LIKE ? OR matn LIKE ?)"
        params += [f"%{search_text}%", f"%{search_text}%"]
    query += " ORDER BY id DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_my_news(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, sarlavha, matn, media, media_types, level, viloyat, tuman, status, views, sana FROM news WHERE telegram_id = ? ORDER BY id DESC",
        (telegram_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def increment_views(news_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE news SET views = views + 1 WHERE id = ? AND status = 'approved'", (news_id,))
    conn.commit()
    conn.close()


def delete_own_news(news_id, telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM comments WHERE news_id = ?", (news_id,))
    cur.execute("DELETE FROM news WHERE id = ? AND telegram_id = ?", (news_id, telegram_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def update_own_news(news_id, telegram_id, sarlavha, matn):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE news SET sarlavha = ?, matn = ? WHERE id = ? AND telegram_id = ?",
        (sarlavha, matn, news_id, telegram_id),
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def media_to_list(media_field, types_field):
    if not media_field:
        return []
    ids = [x for x in media_field.split("|") if x]
    types = [x for x in (types_field or "").split("|") if x]
    result = []
    for i, fid in enumerate(ids):
        t = types[i] if i < len(types) else "photo"
        result.append({"url": f"/media/{fid}", "type": t})
    return result


# ---------------- Kommentariyalar ----------------

def add_comment(news_id, telegram_id, ism, matn):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO comments (news_id, telegram_id, ism, matn, sana) VALUES (?, ?, ?, ?, ?)",
        (news_id, telegram_id, ism, matn, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_comments(news_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, ism, matn, sana FROM comments WHERE news_id = ? ORDER BY id DESC",
        (news_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def comment_counts_map():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT news_id, COUNT(*) FROM comments GROUP BY news_id")
    result = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return result


# ---------------- Profil ----------------

def get_user_profile(telegram_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT telegram_id, ism_familya, telefon, avatar_file_id FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def upsert_user_profile(telegram_id, ism_familya, telefon, avatar_file_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO users (telegram_id, ism_familya, telefon, avatar_file_id, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
               ism_familya = excluded.ism_familya,
               telefon = excluded.telefon,
               avatar_file_id = COALESCE(NULLIF(excluded.avatar_file_id, ''), avatar_file_id),
               updated_at = excluded.updated_at""",
        (telegram_id, ism_familya, telefon, avatar_file_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def hudud_label(level, viloyat, tuman):
    if level == "uz":
        return "O'zbekiston"
    if level == "viloyat":
        return viloyat
    return f"{tuman}, {viloyat}" if tuman else viloyat


# ---------------------------------------------------------------------------
# FLASK — MINI APP SERVERI
# ---------------------------------------------------------------------------

flask_app = Flask(__name__)


@flask_app.route("/")
def index():
    resp = flask_app.make_response(
        render_template("index.html", bot_username=BOT_USERNAME, mini_app_url=MINI_APP_URL)
    )
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@flask_app.route("/media/<file_id>")
def media(file_id):
    """Rasm yoki videoni Telegram serveridan olib qaytaramiz"""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=10,
        )
        data = resp.json()
        file_path = data["result"]["file_path"]
        return redirect(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
    except Exception as e:
        logger.warning("Mediani olishda xatolik: %s", e)
        return "", 404


@flask_app.route("/api/news")
def api_news():
    level = request.args.get("level", "uz")
    viloyat = request.args.get("viloyat", "").strip()
    tuman = request.args.get("tuman", "").strip()
    search_text = request.args.get("q", "").strip()

    rows = search_news(level, viloyat, tuman, search_text)
    ccounts = comment_counts_map()

    result = []
    for id_, sarlavha, matn, media, media_types, lvl, vil, tum, views, sana in rows:
        media_list = media_to_list(media, media_types)
        result.append(
            {
                "id": id_,
                "sarlavha": sarlavha,
                "matn": matn,
                "media": media_list,
                "level": lvl,
                "viloyat": vil,
                "tuman": tum,
                "hudud": hudud_label(lvl, vil, tum),
                "views": views or 0,
                "comment_count": ccounts.get(id_, 0),
                "sana": sana,
            }
        )
    return jsonify(result)


@flask_app.route("/api/news/<int:news_id>")
def api_news_detail(news_id):
    row = get_news(news_id)
    if not row or row[9] != "approved":  # status indeks 9
        return jsonify({"error": "Topilmadi"}), 404
    id_, telegram_id, sarlavha, matn, media, media_types, level, viloyat, tuman, status, views, sana = row
    return jsonify(
        {
            "id": id_,
            "sarlavha": sarlavha,
            "matn": matn,
            "media": media_to_list(media, media_types),
            "level": level,
            "viloyat": viloyat,
            "tuman": tuman,
            "hudud": hudud_label(level, viloyat, tuman),
            "views": views or 0,
            "sana": sana,
        }
    )


@flask_app.route("/api/comments")
def api_comments():
    news_id = request.args.get("news_id", type=int)
    if not news_id:
        return jsonify({"error": "news_id kerak"}), 400
    rows = get_comments(news_id)
    return jsonify(
        [{"id": r[0], "ism": r[1] or "Foydalanuvchi", "matn": r[2], "sana": r[3]} for r in rows]
    )


@flask_app.route("/api/add-comment", methods=["POST"])
def api_add_comment():
    data = request.get_json(force=True)
    telegram_id, tg_user = resolve_user_id(data.get("init_data", ""), data.get("user_id", ""))
    if not telegram_id:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401

    news_id = data.get("news_id")
    matn = (data.get("matn") or "").strip()
    if not news_id or not matn:
        return jsonify({"success": False, "error": "Matn bo'sh"}), 400

    # Ism: profildan, yo'qsa Telegram nomidan
    ism = ""
    prof = get_user_profile(telegram_id)
    if prof and prof[1]:
        ism = prof[1]
    elif tg_user:
        ism = (tg_user.get("first_name") or "") + " " + (tg_user.get("last_name") or "")
        ism = ism.strip()

    add_comment(news_id, telegram_id, ism, matn)
    return jsonify({"success": True})


@flask_app.route("/api/view-news", methods=["POST"])
def api_view_news():
    data = request.get_json(force=True)
    news_id = data.get("id")
    if news_id:
        increment_views(news_id)
    return jsonify({"success": True})


@flask_app.route("/api/create-news", methods=["POST"])
def api_create_news():
    try:
        telegram_id, _ = resolve_user_id(
            request.form.get("init_data", ""), request.form.get("user_id", "")
        )
        if not telegram_id:
            return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401

        sarlavha = request.form.get("sarlavha", "").strip()
        matn = request.form.get("matn", "").strip()
        level = request.form.get("level", "uz")
        viloyat = request.form.get("viloyat", "").strip()
        tuman = request.form.get("tuman", "").strip()
        files = request.files.getlist("media")[:3]  # max 3 ta

        if level == "uz":
            viloyat, tuman = "", ""
        elif level == "viloyat":
            if not viloyat:
                return jsonify({"success": False, "error": "Viloyat tanlanmagan"}), 400
            tuman = ""
        elif level == "tuman":
            if not viloyat or not tuman:
                return jsonify({"success": False, "error": "Viloyat va tuman tanlanmagan"}), 400

        if not sarlavha or not matn or not files:
            return jsonify({"success": False, "error": "Sarlavha, matn va kamida 1 ta rasm/video kerak"}), 400

        news_id = add_news(telegram_id, sarlavha, matn, level, viloyat, tuman)

        hudud = hudud_label(level, viloyat, tuman)
        admin_caption = (
            f"🆕 Yangi yangilik (ID: {news_id}):\n\n"
            f"📍 {hudud}\n📰 {sarlavha}\n\n{matn[:300]}"
        )
        admin_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash", "callback_data": f"admin:approve:{news_id}"},
                    {"text": "❌ Rad etish", "callback_data": f"admin:reject:{news_id}"},
                ]
            ]
        }

        file_ids, media_types = [], []
        target_chat = ADMIN_ID if ADMIN_ID else telegram_id

        for i, f in enumerate(files):
            is_video = f.mimetype.startswith("video/")
            method = "sendVideo" if is_video else "sendPhoto"
            data = {"chat_id": target_chat}
            if i == 0:
                data["caption"] = admin_caption
                data["reply_markup"] = json.dumps(admin_keyboard)
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                    data=data,
                    files={"video" if is_video else "photo": (f.filename, f.stream, f.mimetype)},
                    timeout=30,
                )
                result = resp.json()
                if result.get("ok"):
                    key = "video" if is_video else "photo"
                    file_ids.append(result["result"][key][-1]["file_id"])
                    media_types.append("video" if is_video else "photo")
            except Exception as e:
                logger.warning("Media %d yuborishda xatolik: %s", i, e)

        if file_ids:
            set_news_media(news_id, file_ids, media_types)

        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": telegram_id,
                    "text": "Rahmat! 🙌 Yangiligingiz yuborildi — admin tez orada ko'rib chiqadi.",
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)

        return jsonify({"success": True, "id": news_id})

    except Exception as e:
        logger.warning("Yangilik yaratishda xatolik: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@flask_app.route("/api/my-news")
def api_my_news():
    telegram_id, _ = resolve_user_id(request.args.get("init_data", ""), request.args.get("user_id", ""))
    if not telegram_id:
        return jsonify({"error": "Tasdiqlanmagan so'rov"}), 401
    rows = get_my_news(telegram_id)
    result = []
    for id_, sarlavha, matn, media, media_types, level, viloyat, tuman, status, views, sana in rows:
        result.append(
            {
                "id": id_,
                "sarlavha": sarlavha,
                "matn": matn,
                "media": media_to_list(media, media_types),
                "hudud": hudud_label(level, viloyat, tuman),
                "status": status,
                "status_label": STATUS_LABELS.get(status, status),
                "views": views or 0,
                "sana": sana,
            }
        )
    return jsonify(result)


@flask_app.route("/api/update-news", methods=["POST"])
def api_update_news():
    data = request.get_json(force=True)
    telegram_id, _ = resolve_user_id(data.get("init_data", ""), data.get("user_id", ""))
    if not telegram_id:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401
    news_id = data.get("id")
    sarlavha = (data.get("sarlavha") or "").strip()
    matn = (data.get("matn") or "").strip()
    if not news_id or not sarlavha or not matn:
        return jsonify({"success": False, "error": "Maydonlarni to'ldiring"}), 400
    ok = update_own_news(news_id, telegram_id, sarlavha, matn)
    return jsonify({"success": ok})


@flask_app.route("/api/delete-news", methods=["POST"])
def api_delete_news():
    data = request.get_json(force=True)
    telegram_id, _ = resolve_user_id(data.get("init_data", ""), data.get("user_id", ""))
    if not telegram_id:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401
    news_id = data.get("id")
    if not news_id:
        return jsonify({"success": False, "error": "id kerak"}), 400
    ok = delete_own_news(news_id, telegram_id)
    return jsonify({"success": ok})


@flask_app.route("/api/get-profile")
def api_get_profile():
    telegram_id, tg_user = resolve_user_id(request.args.get("init_data", ""), request.args.get("user_id", ""))
    if not telegram_id:
        return jsonify({"error": "Tasdiqlanmagan so'rov"}), 401
    row = get_user_profile(telegram_id)
    tg_name = ""
    if tg_user:
        tg_name = ((tg_user.get("first_name") or "") + " " + (tg_user.get("last_name") or "")).strip()
    if not row:
        return jsonify({"exists": False, "tg_name": tg_name})
    return jsonify(
        {
            "exists": True,
            "ism_familya": row[1] or tg_name,
            "telefon": row[2] or "",
            "avatar_url": f"/media/{row[3]}" if row[3] else "",
            "tg_name": tg_name,
        }
    )


@flask_app.route("/api/update-profile", methods=["POST"])
def api_update_profile():
    telegram_id, _ = resolve_user_id(request.form.get("init_data", ""), request.form.get("user_id", ""))
    if not telegram_id:
        return jsonify({"success": False, "error": "Tasdiqlanmagan so'rov"}), 401
    ism_familya = (request.form.get("ism_familya") or "").strip()
    telefon = (request.form.get("telefon") or "").strip()
    avatar = request.files.get("avatar")
    if not ism_familya:
        return jsonify({"success": False, "error": "Ism kiritilishi shart"}), 400
    avatar_file_id = ""
    if avatar:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": telegram_id},
                files={"photo": (avatar.filename, avatar.stream, avatar.mimetype)},
                timeout=20,
            )
            result = resp.json()
            if result.get("ok"):
                avatar_file_id = result["result"]["photo"][-1]["file_id"]
        except Exception as e:
            logger.warning("Avatar yuborishda xatolik: %s", e)
    upsert_user_profile(telegram_id, ism_familya, telefon, avatar_file_id)
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# TELEGRAM BOT
# ---------------------------------------------------------------------------

def get_mini_app_url(extra=""):
    if not MINI_APP_URL:
        return None
    return f"{MINI_APP_URL}{extra}&v={int(time.time())}" if extra else f"{MINI_APP_URL}?v={int(time.time())}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fresh_url = get_mini_app_url()
    keyboard = (
        InlineKeyboardMarkup([[InlineKeyboardButton("📰 Ilovaga kirish", web_app=WebAppInfo(url=fresh_url))]])
        if fresh_url else None
    )
    text = (
        "Assalomu alaykum! 👋\n\n"
        "📰 *Hudud Yangiliklari* — mahalliy, viloyat va respublika "
        "yangiliklari endi bitta ilovada!\n\n"
        "Hududingizni tanlang va eng yaqin yangiliklardan birinchi bo'lib xabardor bo'ling. "
        "To'liq matn, rasmlar va kommentariyalar ilovada 👇"
    )
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, action, news_id_str = query.data.split(":")
    news_id = int(news_id_str)
    row = get_news(news_id)

    if not row:
        await query.message.reply_text("Yangilik topilmadi.")
        return

    author_id = row[1]
    sarlavha = row[2]
    matn = row[3]
    level, viloyat, tuman = row[6], row[7], row[8]
    hudud = hudud_label(level, viloyat, tuman)

    if action == "approve":
        set_news_status(news_id, "approved")
        await query.message.reply_text(f"✅ Tasdiqlandi: {sarlavha}")

        # Kanalga (yoki adminga) qisqa matn + "Batafsil" tugmasi
        short_text = matn[:250] + ("…" if len(matn) > 250 else "")
        detail_url = get_mini_app_url(extra=f"?news_id={news_id}")
        keyboard = None
        if detail_url:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📰 Batafsil — ilovada o'qish", web_app=WebAppInfo(url=detail_url))]]
            )
        post_text = f"📍 *{hudud}*\n\n📰 *{sarlavha}*\n\n{short_text}"
        target = CHANNEL_ID if CHANNEL_ID else ADMIN_ID
        if target:
            try:
                await context.bot.send_message(
                    chat_id=int(target), text=post_text,
                    reply_markup=keyboard, parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning("Kanalga yuborishda xatolik: %s", e)

        try:
            await context.bot.send_message(
                chat_id=author_id,
                text="🎉 Ajoyib! Yangiligingiz tasdiqlandi va e'lon qilindi.",
            )
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)
    else:
        set_news_status(news_id, "rejected")
        await query.message.reply_text(f"❌ Rad etildi: {sarlavha}")
        try:
            await context.bot.send_message(
                chat_id=author_id,
                text="Afsuski, yangiligingiz hozircha tasdiqlanmadi.",
            )
        except Exception as e:
            logger.warning("Xabar yuborishda xatolik: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Xatolik: %s", context.error)


# ---------------------------------------------------------------------------
# ASOSIY FUNKSIYA
# ---------------------------------------------------------------------------

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True)


async def post_init(application: Application) -> None:
    if MINI_APP_URL:
        try:
            from telegram import MenuButtonWebApp

            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="📰 Ilovaga kirish", web_app=WebAppInfo(url=MINI_APP_URL)
                )
            )
            logger.info("Menu Button sozlandi")
        except Exception as e:
            logger.warning("Menu Button sozlashda xatolik: %s", e)


def main():
    init_db()

    if BOT_TOKEN == "SIZNING_BOT_TOKENINGIZ_BU_YERGA":
        print("XATOLIK: BOT_TOKEN o'rnatilmagan!")
        return

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"Mini App serveri {PORT}-portda ishga tushdi...")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_decision, pattern="^admin:"))
    app.add_error_handler(error_handler)

    print("Hudud Yangiliklari boti ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
