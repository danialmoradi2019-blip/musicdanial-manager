import os
import re
import sqlite3
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import tempfile

import imageio_ffmpeg
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================================================
# ENV
# =========================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@musicdanial2023")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))

# =========================================================
# SETTINGS
# =========================================================

PREVIEW_SECONDS = 30

INSTAGRAM_ID = "@Deandaniall"

INVITE_DELAY = 1.5

DB = "musicdanial.db"

# Telegram Bot API:
# دانلود معمولی با get_file محدود است.
# برای فایل‌های بزرگ‌تر از این مقدار، Preview ساخته نمی‌شود.
DOWNLOAD_LIMIT = 20 * 1024 * 1024

# حدود سقف ارسال معمولی Bot API
MAX_SEND_SIZE = 50 * 1024 * 1024


# =========================================================
# VALIDATE ENV
# =========================================================

if not TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

if ADMIN_USER_ID == 0:
    raise RuntimeError("ADMIN_USER_ID تنظیم نشده است.")


# =========================================================
# UTC TIME
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_iso():
    return utc_now().isoformat()


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path in ("/", "/health", "/healthz"):

            body = b"Music Danial Bot is running"

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.end_headers()

            self.wfile.write(body)

        else:

            body = b"Not Found"

            self.send_response(404)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )

            self.send_header(
                "Content-Length",
                str(len(body))
            )

            self.end_headers()

            self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"Health server running on 0.0.0.0:{PORT}"
    )

    server.serve_forever()


# =========================================================
# DATABASE
# =========================================================

def db():

    con = sqlite3.connect(DB)

    con.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_message_id INTEGER,
            file_id TEXT,
            caption TEXT,
            scheduled_at TEXT,
            status TEXT DEFAULT 'published',
            created_at TEXT NOT NULL,
            file_size INTEGER DEFAULT 0
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)

    # -----------------------------------------------------
    # اگر دیتابیس قبلی باشد و file_size نداشته باشد
    # -----------------------------------------------------

    columns = con.execute(
        "PRAGMA table_info(posts)"
    ).fetchall()

    column_names = [
        column[1]
        for column in columns
    ]

    if "file_size" not in column_names:

        try:

            con.execute(
                """
                ALTER TABLE posts
                ADD COLUMN file_size INTEGER DEFAULT 0
                """
            )

        except sqlite3.OperationalError:
            pass

    con.commit()

    return con


# =========================================================
# ADMIN CHECK
# =========================================================

def admin_only(function):

    @wraps(function)
    async def wrapper(update, context):

        if not update.effective_user:
            return

        if update.effective_user.id != ADMIN_USER_ID:
            return

        return await function(
            update,
            context
        )

    return wrapper


# =========================================================
# SAVE USER
# =========================================================

def save_user(user):

    if not user:
        return

    con = db()

    now = utc_now_iso()

    con.execute("""
        INSERT INTO users (
            user_id,
            username,
            first_name,
            created_at,
            last_seen
        )
        VALUES (?, ?, ?, ?, ?)

        ON CONFLICT(user_id)
        DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            last_seen=excluded.last_seen
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        now,
        now
    ))

    con.commit()
    con.close()


# =========================================================
# TRACK USERS
# =========================================================

async def track_user(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user:

        save_user(
            update.effective_user
        )


# =========================================================
# CAPTION
# =========================================================

def make_caption(title, artist=""):

    if artist:

        return (
            f"🎵 {artist} — {title}\n\n"
            f"🎧 Music Danial\n"
            f"@musicdanial2023"
        )

    return (
        f"🎵 {title}\n\n"
        f"🎧 Music Danial\n"
        f"@musicdanial2023"
    )


# =========================================================
# INVITE LINK
# =========================================================

async def create_direct_invite(bot):

    link = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name="Music Danial",
        creates_join_request=False
    )

    return link.invite_link


# =========================================================
# GET AUDIO MEDIA
# =========================================================

def get_audio_from_reply(reply):

    if not reply:
        return None

    if reply.audio:

        return reply.audio

    if reply.document:

        mime = reply.document.mime_type or ""

        if mime.startswith("audio/"):

            return reply.document

        filename = reply.document.file_name or ""

        audio_extensions = (
            ".mp3",
            ".m4a",
            ".wav",
            ".flac",
            ".aac",
            ".ogg",
            ".opus"
        )

        if filename.lower().endswith(
            audio_extensions
        ):

            return reply.document

    return None


# =========================================================
# GET AUDIO DURATION
# =========================================================

async def get_audio_duration(
    ffmpeg,
    source
):

    command = [
        ffmpeg,
        "-i",
        source
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    _, stderr = await process.communicate()

    text = stderr.decode(
        errors="ignore"
    )

    match = re.search(
        r"Duration:\s*(\d+):(\d+):([\d.]+)",
        text
    )

    if not match:

        return 60.0

    hours = int(
        match.group(1)
    )

    minutes = int(
        match.group(2)
    )

    seconds = float(
        match.group(3)
    )

    return (
        hours * 3600
        + minutes * 60
        + seconds
    )


# =========================================================
# SEND LARGE FILE
# =========================================================

async def send_large_audio(
    bot,
    file_id,
    caption_text
):

    print(
        "Large file detected."
    )

    print(
        "Sending directly using Telegram file_id..."
    )

    try:

        message = await bot.send_audio(
            chat_id=CHANNEL_ID,
            audio=file_id,
            caption=caption_text
        )

        print(
            "Large file sent successfully."
        )

        return message

    except Exception as error:

        print(
            "Large file send error:",
            error
        )

        raise RuntimeError(
            "ارسال فایل بزرگ انجام نشد.\n\n"
            f"{error}"
        )


# =========================================================
# PUBLISH MUSIC
#
# فایل <= 20MB:
#   Preview از وسط آهنگ
#   سپس آهنگ کامل
#
# فایل > 20MB:
#   بدون دانلود مجدد
#   ارسال مستقیم با file_id
#
# =========================================================

async def publish_music(
    bot,
    file_id,
    caption_text,
    file_size=None
):

    # =====================================================
    # CHECK FILE SIZE
    # =====================================================

    if file_size is None:

        print(
            "File size is unknown."
        )

    else:

        size_mb = (
            file_size
            / (1024 * 1024)
        )

        print(
            f"File size: {size_mb:.2f} MB"
        )

    # =====================================================
    # LARGE FILE
    # =====================================================

    if (
        file_size is not None
        and file_size > DOWNLOAD_LIMIT
    ):

        return await send_large_audio(
            bot,
            file_id,
            caption_text
        )

    # =====================================================
    # NORMAL FILE
    # =====================================================

    with tempfile.TemporaryDirectory() as tmp:

        source = os.path.join(
            tmp,
            "source"
        )

        preview = os.path.join(
            tmp,
            "preview.mp3"
        )

        # -------------------------------------------------
        # DOWNLOAD
        # -------------------------------------------------

        print(
            "Downloading file for preview..."
        )

        telegram_file = await bot.get_file(
            file_id
        )

        await telegram_file.download_to_drive(
            source
        )

        # -------------------------------------------------
        # FFMPEG
        # -------------------------------------------------

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        # -------------------------------------------------
        # DURATION
        # -------------------------------------------------

        duration = await get_audio_duration(
            ffmpeg,
            source
        )

        print(
            f"Audio duration: {duration:.2f} seconds"
        )

        # -------------------------------------------------
        # MIDDLE OF SONG
        # -------------------------------------------------

        if duration <= PREVIEW_SECONDS:

            start_time = 0

            preview_duration = duration

        else:

            start_time = (
                duration / 2
            ) - (
                PREVIEW_SECONDS / 2
            )

            start_time = max(
                0,
                start_time
            )

            preview_duration = PREVIEW_SECONDS

        # -------------------------------------------------
        # CREATE PREVIEW
        # -------------------------------------------------

        print(
            "Creating preview..."
        )

        command = [
            ffmpeg,
            "-y",
            "-ss",
            str(start_time),
            "-i",
            source,
            "-t",
            str(preview_duration),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            preview
        ]

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        _, preview_error = await process.communicate()

        if process.returncode != 0:

            error_text = preview_error.decode(
                errors="ignore"
            )

            raise RuntimeError(
                "ساخت پیش‌نمایش ناموفق بود.\n"
                + error_text[-1000:]
            )

        if not os.path.exists(preview):

            raise RuntimeError(
                "فایل پیش‌نمایش ساخته نشد."
            )

        # -------------------------------------------------
        # SEND PREVIEW
        # -------------------------------------------------

        print(
            "Sending preview..."
        )

        with open(
            preview,
            "rb"
        ) as preview_file:

            await bot.send_audio(
                chat_id=CHANNEL_ID,
                audio=preview_file,
                caption=(
                    f"📸 Instagram: {INSTAGRAM_ID}\n\n"
                    + caption_text
                ),
                title="Music Danial - Preview"
            )

        # -------------------------------------------------
        # SEND FULL SONG
        # -------------------------------------------------

        print(
            "Sending full song..."
        )

        full_message = await bot.send_audio(
            chat_id=CHANNEL_ID,
            audio=file_id,
            caption=caption_text
        )

        print(
            "Full song sent successfully."
        )

        return full_message


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    save_user(user)

    if user.id == ADMIN_USER_ID:

        await update.message.reply_text(

            "🎵 Music Danial Manager\n\n"

            "🎧 انتشار موزیک:\n"
            "/post عنوان | خواننده\n\n"

            "🕐 زمان‌بندی:\n"
            "/schedule عنوان | خواننده | دقیقه\n\n"

            "📋 صف:\n"
            "/queue\n\n"

            "🗑 حذف:\n"
            "/delete شناسه\n\n"

            "📌 پین:\n"
            "/pin شناسه\n\n"

            "📝 توضیحات کانال:\n"
            "/desc متن\n\n"

            "👥 مدیریت کاربران:\n"
            "/invite\n"
            "/sendinvites\n"
            "/inviteid USER_ID\n"
            "/users"
        )

        return

    # -----------------------------------------------------
    # NORMAL USER
    # -----------------------------------------------------

    try:

        invite_link = await create_direct_invite(
            context.bot
        )

        await update.message.reply_text(

            "🎵 به Music Danial خوش آمدی ❤️\n\n"

            "🎧 برای عضویت در کانال روی لینک زیر بزن:\n\n"

            f"🔗 {invite_link}\n\n"

            "👥 می‌توانی لینک را برای دوستانت هم بفرستی."
        )

    except Exception:

        await update.message.reply_text(

            "🎵 به Music Danial خوش آمدی ❤️\n\n"

            "لینک عضویت فعلاً آماده نیست."
        )


# =========================================================
# POST
# =========================================================

@admin_only
async def post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.reply_to_message:

        await update.message.reply_text(

            "روی فایل موزیک Reply کن و سپس بنویس:\n\n"
            "/post عنوان | خواننده"
        )

        return

    reply = update.message.reply_to_message

    media = get_audio_from_reply(
        reply
    )

    if not media:

        await update.message.reply_text(
            "❌ پیام Reply شده باید فایل صوتی باشد."
        )

        return

    raw = " ".join(
        context.args
    ).strip()

    parts = [
        p.strip()
        for p in raw.split("|")
    ]

    title = (
        parts[0]
        if parts and parts[0]
        else "موزیک جدید"
    )

    artist = (
        parts[1]
        if len(parts) > 1
        else ""
    )

    caption = make_caption(
        title,
        artist
    )

    # -----------------------------------------------------
    # FILE SIZE
    # -----------------------------------------------------

    file_size = getattr(
        media,
        "file_size",
        None
    )

    if file_size:

        print(
            f"POST file size: "
            f"{file_size / (1024 * 1024):.2f} MB"
        )

    # -----------------------------------------------------
    # PUBLISH
    # -----------------------------------------------------

    try:

        message = await publish_music(
            context.bot,
            media.file_id,
            caption,
            file_size
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ انتشار انجام نشد:\n\n"
            f"{error}"
        )

        return

    # -----------------------------------------------------
    # SAVE POST
    # -----------------------------------------------------

    con = db()

    con.execute(
        """
        INSERT INTO posts (
            telegram_message_id,
            file_id,
            caption,
            created_at,
            file_size
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            message.message_id,
            media.file_id,
            caption,
            utc_now_iso(),
            file_size or 0
        )
    )

    con.commit()
    con.close()

    # -----------------------------------------------------
    # RESPONSE
    # -----------------------------------------------------

    if (
        file_size
        and file_size > DOWNLOAD_LIMIT
    ):

        await update.message.reply_text(

            "✅ موزیک منتشر شد!\n\n"

            "🎵 فایل بزرگ بود و مستقیم ارسال شد.\n"
            "ℹ️ برای فایل‌های بزرگ Preview ساخته نمی‌شود."
        )

    else:

        await update.message.reply_text(

            "✅ موزیک منتشر شد!\n\n"

            "📸 پیش‌نمایش ۳۰ ثانیه‌ای از وسط آهنگ\n"
            "🎵 سپس آهنگ کامل"
        )


# =========================================================
# SCHEDULE
# =========================================================

@admin_only
async def schedule(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message.reply_to_message:

        await update.message.reply_text(

            "روی فایل موزیک Reply کن و سپس:\n\n"
            "/schedule عنوان | خواننده | دقیقه"
        )

        return

    raw = " ".join(
        context.args
    ).strip()

    parts = [
        p.strip()
        for p in raw.split("|")
    ]

    if len(parts) < 3:

        await update.message.reply_text(

            "فرمت درست:\n\n"
            "/schedule عنوان | خواننده | دقیقه"
        )

        return

    title = parts[0]

    artist = parts[1]

    try:

        minutes = int(
            parts[2]
        )

        if minutes < 1 or minutes > 10080:

            raise ValueError

    except ValueError:

        await update.message.reply_text(

            "❌ دقیقه باید بین 1 تا 10080 باشد."
        )

        return

    reply = update.message.reply_to_message

    media = get_audio_from_reply(
        reply
    )

    if not media:

        await update.message.reply_text(

            "❌ پیام Reply شده باید فایل صوتی باشد."
        )

        return

    caption = make_caption(
        title,
        artist
    )

    # -----------------------------------------------------
    # FILE SIZE
    # -----------------------------------------------------

    file_size = getattr(
        media,
        "file_size",
        None
    )

    # -----------------------------------------------------
    # SCHEDULE TIME
    # -----------------------------------------------------

    scheduled_time = (
        utc_now()
        + timedelta(minutes=minutes)
    ).isoformat()

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    con = db()

    cursor = con.execute(
        """
        INSERT INTO posts (
            file_id,
            caption,
            scheduled_at,
            status,
            created_at,
            file_size
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            media.file_id,
            caption,
            scheduled_time,
            "scheduled",
            utc_now_iso(),
            file_size or 0
        )
    )

    post_id = cursor.lastrowid

    con.commit()
    con.close()

    # -----------------------------------------------------
    # PUBLISH JOB
    # -----------------------------------------------------

    async def publish_job(
        job_context
    ):

        con2 = db()

        row = con2.execute(
            """
            SELECT
                file_id,
                caption,
                file_size
            FROM posts
            WHERE id=?
            AND status='scheduled'
            """,
            (post_id,)
        ).fetchone()

        if not row:

            con2.close()

            return

        try:

            message = await publish_music(
                job_context.bot,
                row[0],
                row[1],
                row[2]
            )

            con2.execute(
                """
                UPDATE posts
                SET telegram_message_id=?,
                    status='published'
                WHERE id=?
                """,
                (
                    message.message_id,
                    post_id
                )
            )

            con2.commit()

            print(
                f"Scheduled post #{post_id} published."
            )

        except Exception as error:

            print(
                f"Scheduled post #{post_id} error:",
                error
            )

            con2.execute(
                """
                UPDATE posts
                SET status='error'
                WHERE id=?
                """,
                (post_id,)
            )

            con2.commit()

        con2.close()

    # -----------------------------------------------------
    # JOB
    # -----------------------------------------------------

    context.job_queue.run_once(
        publish_job,
        when=minutes * 60,
        name=f"post-{post_id}"
    )

    await update.message.reply_text(

        f"🕐 موزیک زمان‌بندی شد.\n\n"
        f"شناسه: {post_id}\n"
        f"زمان: {minutes} دقیقه"
    )


# =========================================================
# QUEUE
# =========================================================

@admin_only
async def queue(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    con = db()

    rows = con.execute(
        """
        SELECT
            id,
            scheduled_at,
            status
        FROM posts
        WHERE status='scheduled'
        ORDER BY id DESC
        """
    ).fetchall()

    con.close()

    if not rows:

        await update.message.reply_text(
            "📋 صف زمان‌بندی خالی است."
        )

        return

    text = "\n".join(
        f"#{row[0]} — {row[1]} — {row[2]}"
        for row in rows
    )

    await update.message.reply_text(

        "📋 صف زمان‌بندی:\n\n"
        + text
    )


# =========================================================
# DELETE
# =========================================================

@admin_only
async def delete(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        post_id = int(
            context.args[0]
        )

    except (IndexError, ValueError):

        await update.message.reply_text(
            "/delete شناسه"
        )

        return

    con = db()

    row = con.execute(
        """
        SELECT telegram_message_id
        FROM posts
        WHERE id=?
        """,
        (post_id,)
    ).fetchone()

    if not row:

        con.close()

        await update.message.reply_text(
            "❌ چنین پستی پیدا نشد."
        )

        return

    message_id = row[0]

    try:

        if message_id:

            await context.bot.delete_message(
                chat_id=CHANNEL_ID,
                message_id=message_id
            )

        con.execute(
            """
            UPDATE posts
            SET status='deleted'
            WHERE id=?
            """,
            (post_id,)
        )

        con.commit()

        await update.message.reply_text(
            "✅ پست حذف شد."
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ حذف انجام نشد:\n"
            f"{error}"
        )

    finally:

        con.close()


# =========================================================
# PIN
# =========================================================

@admin_only
async def pin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        post_id = int(
            context.args[0]
        )

    except (IndexError, ValueError):

        await update.message.reply_text(
            "/pin شناسه"
        )

        return

    con = db()

    row = con.execute(
        """
        SELECT telegram_message_id
        FROM posts
        WHERE id=?
        """,
        (post_id,)
    ).fetchone()

    con.close()

    if not row or not row[0]:

        await update.message.reply_text(
            "❌ پیام پیدا نشد."
        )

        return

    try:

        await context.bot.pin_chat_message(
            chat_id=CHANNEL_ID,
            message_id=row[0],
            disable_notification=True
        )

        await update.message.reply_text(
            "📌 پست پین شد."
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ پین انجام نشد:\n"
            f"{error}"
        )


# =========================================================
# CHANNEL DESCRIPTION
# =========================================================

@admin_only
async def desc(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await update.message.reply_text(
            "/desc متن توضیحات جدید"
        )

        return

    try:

        await context.bot.set_chat_description(
            chat_id=CHANNEL_ID,
            description=text
        )

        await update.message.reply_text(
            "✅ توضیحات کانال تغییر کرد."
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ تغییر توضیحات انجام نشد:\n"
            f"{error}"
        )


# =========================================================
# INVITE
# =========================================================

@admin_only
async def invite(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        link = await create_direct_invite(
            context.bot
        )

        await update.message.reply_text(

            "🔗 لینک عضویت مستقیم:\n\n"
            f"{link}\n\n"
            "👥 این لینک را می‌توانی برای دیگران بفرستی."
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ ساخت لینک انجام نشد:\n"
            f"{error}"
        )


# =========================================================
# INVITE USER ID
# =========================================================

@admin_only
async def inviteid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        user_id = int(
            context.args[0]
        )

    except (IndexError, ValueError):

        await update.message.reply_text(
            "/inviteid USER_ID"
        )

        return

    try:

        link = await create_direct_invite(
            context.bot
        )

        await context.bot.send_message(

            chat_id=user_id,

            text=(

                "🎵 دعوت به Music Danial\n\n"

                "🔗 لینک عضویت:\n\n"
                f"{link}"
            )
        )

        await update.message.reply_text(
            "✅ دعوت برای کاربر ارسال شد."
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ ارسال دعوت انجام نشد:\n"
            f"{error}"
        )


# =========================================================
# SEND INVITES TO ALL USERS
# =========================================================

@admin_only
async def sendinvites(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        link = await create_direct_invite(
            context.bot
        )

    except Exception as error:

        await update.message.reply_text(

            f"❌ ساخت لینک انجام نشد:\n"
            f"{error}"
        )

        return

    con = db()

    users = con.execute(
        """
        SELECT user_id
        FROM users
        ORDER BY user_id
        """
    ).fetchall()

    con.close()

    if not users:

        await update.message.reply_text(
            "👥 هنوز کاربری ثبت نشده است."
        )

        return

    sent = 0

    failed = 0

    await update.message.reply_text(

        f"📨 ارسال دعوت برای "
        f"{len(users)} کاربر شروع شد..."
    )

    for row in users:

        user_id = row[0]

        try:

            await context.bot.send_message(

                chat_id=user_id,

                text=(

                    "🎵 Music Danial\n\n"

                    "برای عضویت در کانال:\n\n"

                    f"🔗 {link}\n\n"

                    "👥 می‌توانی این لینک را "
                    "برای دوستانت هم بفرستی."
                )
            )

            sent += 1

        except Exception:

            failed += 1

        await asyncio.sleep(
            INVITE_DELAY
        )

    await update.message.reply_text(

        "✅ ارسال دعوت تمام شد.\n\n"

        f"📨 ارسال موفق: {sent}\n"
        f"❌ ناموفق: {failed}"
    )


# =========================================================
# USERS
# =========================================================

@admin_only
async def users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    con = db()

    count = con.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]

    rows = con.execute(
        """
        SELECT
            user_id,
            username,
            first_name,
            last_seen
        FROM users
        ORDER BY last_seen DESC
        LIMIT 30
        """
    ).fetchall()

    con.close()

    text = (
        f"👥 تعداد کاربران ثبت‌شده: {count}\n\n"
    )

    for row in rows:

        user_id = row[0]

        username = row[1]

        first_name = row[2]

        if username:

            name = f"@{username}"

        elif first_name:

            name = first_name

        else:

            name = "بدون نام"

        text += (

            f"👤 {name}\n"
            f"🆔 {user_id}\n\n"
        )

    await update.message.reply_text(
        text[:4000]
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    print(
        "BOT ERROR:",
        context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    con = db()

    con.close()

    # -----------------------------------------------------
    # HEALTH SERVER
    # -----------------------------------------------------

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True
    )

    health_thread.start()

    # -----------------------------------------------------
    # TELEGRAM APPLICATION
    # -----------------------------------------------------

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "post",
            post
        )
    )

    application.add_handler(
        CommandHandler(
            "schedule",
            schedule
        )
    )

    application.add_handler(
        CommandHandler(
            "queue",
            queue
        )
    )

    application.add_handler(
        CommandHandler(
            "delete",
            delete
        )
    )

    application.add_handler(
        CommandHandler(
            "pin",
            pin
        )
    )

    application.add_handler(
        CommandHandler(
            "desc",
            desc
        )
    )

    application.add_handler(
        CommandHandler(
            "invite",
            invite
        )
    )

    application.add_handler(
        CommandHandler(
            "sendinvites",
            sendinvites
        )
    )

    application.add_handler(
        CommandHandler(
            "inviteid",
            inviteid
        )
    )

    application.add_handler(
        CommandHandler(
            "users",
            users
        )
    )

    # -----------------------------------------------------
    # TRACK USERS
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            track_user
        )
    )

    # -----------------------------------------------------
    # ERROR
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    print(
        "==================================="
    )

    print(
        "Music Danial Manager"
    )

    print(
        "Telegram Bot: STARTING"
    )

    print(
        f"Render PORT: {PORT}"
    )

    print(
        "Health: /health"
    )

    print(
        "Large File Mode: ENABLED"
    )

    print(
        "==================================="
    )

    # -----------------------------------------------------
    # POLLING
    # -----------------------------------------------------

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
