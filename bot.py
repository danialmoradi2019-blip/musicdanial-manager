import os
import re
import sqlite3
import asyncio
from datetime import datetime
from functools import wraps
import tempfile
import imageio_ffmpeg
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@musicdanial2023")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
DB = "musicdanial.db"
# =========================
# SETTINGS
# =========================
PREVIEW_SECONDS = 30
INSTAGRAM_ID = "@Deandaniall"
# فاصله ارسال دعوت به کاربران
INVITE_DELAY = 1.5
# =========================
# DATABASE
# =========================
def db():
    con = sqlite3.connect(DB)
    # موزیک‌های منتشرشده
    con.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_message_id INTEGER,
            file_id TEXT,
            caption TEXT,
            scheduled_at TEXT,
            status TEXT DEFAULT 'published',
            created_at TEXT NOT NULL
        )
    """)
    # کاربران ربات
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at TEXT NOT NULL,
            last_seen TEXT NOT NULL
        )
    """)
    con.commit()
    return con
# =========================
# ADMIN CHECK
# =========================
def admin_only(function):
    @wraps(function)
    async def wrapper(update, context):
        if not update.effective_user:
            return
        if update.effective_user.id != ADMIN_USER_ID:
            return
        return await function(update, context)
    return wrapper
# =========================
# SAVE USER
# =========================
def save_user(user):
    if not user:
        return
    con = db()
    now = datetime.utcnow().isoformat()
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
# =========================
# CAPTION
# =========================
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
# =========================
# CREATE DIRECT INVITE LINK
# =========================
async def create_direct_invite(bot):
    link = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name="Music Danial",
        creates_join_request=False
    )
    return link.invite_link
# =========================
# PUBLISH MUSIC
# 30 SECOND PREVIEW
# FROM MIDDLE OF SONG
# THEN FULL SONG
# =========================
async def publish_music(bot, file_id, caption_text):
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "source")
        preview = os.path.join(tmp, "preview.mp3")
        # -------------------------
        # Download audio
        # -------------------------
        telegram_file = await bot.get_file(file_id)
        await telegram_file.download_to_drive(source)
        # -------------------------
        # FFmpeg
        # -------------------------
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        # -------------------------
        # Find duration
        # -------------------------
        probe_command = [
            ffmpeg,
            "-i",
            source
        ]
        probe = await asyncio.create_subprocess_exec(
            *probe_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await probe.communicate()
        duration_match = re.search(
            r"Duration:\s*(\d+):(\d+):([\d.]+)",
            stderr.decode(errors="ignore")
        )
        if duration_match:
            hours = int(duration_match.group(1))
            minutes = int(duration_match.group(2))
            seconds = float(duration_match.group(3))
            duration = (
                hours * 3600
                + minutes * 60
                + seconds
            )
        else:
            duration = 60
        # -------------------------
        # Middle of song
        # -------------------------
        start_time = max(
            0,
            (duration / 2) - (PREVIEW_SECONDS / 2)
        )
        # -------------------------
        # Create preview
        # -------------------------
        command = [
            ffmpeg,
            "-y",
            "-ss",
            str(start_time),
            "-i",
            source,
            "-t",
            str(PREVIEW_SECONDS),
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
            raise RuntimeError(
                "ساخت پیش‌نمایش ۳۰ ثانیه‌ای ناموفق بود."
            )
        if not os.path.exists(preview):
            raise RuntimeError(
                "فایل پیش‌نمایش ساخته نشد."
            )
        # -------------------------
        # Send preview
        # -------------------------
        with open(preview, "rb") as preview_file:
            await bot.send_audio(
                chat_id=CHANNEL_ID,
                audio=preview_file,
                caption=(
                    f"📸 Instagram: {INSTAGRAM_ID}\n\n"
                    + caption_text
                ),
                title="Music Danial - Preview"
            )
        # -------------------------
        # Send full song
        # -------------------------
        full_message = await bot.send_audio(
            chat_id=CHANNEL_ID,
            audio=file_id,
            caption=caption_text
        )
        return full_message
# =========================
# START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    # ذخیره کاربر
    save_user(user)
    # -------------------------
    # اگر ادمین است
    # -------------------------
    if user.id == ADMIN_USER_ID:
        await update.message.reply_text(
            "🎵 Music Danial Manager آماده است.\n\n"
            "🎧 موزیک:\n"
            "/post عنوان | خواننده\n"
            "/schedule عنوان | خواننده | دقیقه\n"
            "/queue\n"
            "/delete شناسه\n"
            "/pin شناسه\n"
            "/desc متن\n\n"
            "👥 مدیریت دعوت:\n"
            "/invite\n"
            "/sendinvites\n"
            "/inviteid USER_ID\n"
            "/users"
        )
        return
    # -------------------------
    # کاربر عادی
    # -------------------------
    try:
        invite_link = await create_direct_invite(
            context.bot
        )
        await update.message.reply_text(
            "🎵 به Music Danial خوش آمدی ❤️\n\n"
            "🎧 برای عضویت در کانال روی لینک زیر بزن:\n\n"
            f"🔗 {invite_link}\n\n"
            "👥 می‌توانی این لینک را برای دوستانت هم بفرستی "
            "تا آنها هم مستقیم وارد کانال شوند."
        )
    except Exception as error:
        await update.message.reply_text(
            "🎵 به Music Danial خوش آمدی ❤️\n\n"
            "لینک عضویت فعلاً آماده نیست."
        )
# =========================
# POST
# =========================
@admin_only
async def post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "روی فایل موزیک Reply کن و سپس بنویس:\n\n"
            "/post عنوان | خواننده"
        )
        return
    reply = update.message.reply_to_message
    media = reply.audio or reply.document
    if not media:
        await update.message.reply_text(
            "❌ پیام Reply شده باید فایل صوتی باشد."
        )
        return
    raw = " ".join(context.args).strip()
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
    try:
        message = await publish_music(
            context.bot,
            media.file_id,
            caption
        )
    except Exception as error:
        await update.message.reply_text(
            f"❌ انتشار انجام نشد:\n{error}"
        )
        return
    con = db()
    con.execute(
        """
        INSERT INTO posts (
            telegram_message_id,
            file_id,
            caption,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            message.message_id,
            media.file_id,
            caption,
            datetime.utcnow().isoformat()
        )
    )
    con.commit()
    con.close()
    await update.message.reply_text(
        "✅ موزیک منتشر شد!\n\n"
        "📸 پیش‌نمایش ۳۰ ثانیه‌ای از وسط آهنگ\n"
        "🎵 سپس آهنگ کامل"
    )
# =========================
# SCHEDULE
# =========================
@admin_only
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "روی فایل موزیک Reply کن و سپس:\n\n"
            "/schedule عنوان | خواننده | دقیقه"
        )
        return
    raw = " ".join(context.args).strip()
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
        minutes = int(parts[2])
        if minutes < 1 or minutes > 10080:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ دقیقه باید بین 1 تا 10080 باشد."
        )
        return
    reply = update.message.reply_to_message
    media = reply.audio or reply.document
    if not media:
        await update.message.reply_text(
            "❌ پیام Reply شده باید فایل صوتی باشد."
        )
        return
    caption = make_caption(
        title,
        artist
    )
    con = db()
    cursor = con.execute(
        """
        INSERT INTO posts (
            file_id,
            caption,
            scheduled_at,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            media.file_id,
            caption,
            f"+{minutes}m",
            "scheduled",
            datetime.utcnow().isoformat()
        )
    )
    post_id = cursor.lastrowid
    con.commit()
    con.close()
    async def publish_job(context):
        con2 = db()
        row = con2.execute(
            """
            SELECT file_id, caption
            FROM posts
            WHERE id=?
            """,
            (post_id,)
        ).fetchone()
        if row:
            try:
                message = await publish_music(
                    context.bot,
                    row[0],
                    row[1]
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
            except Exception:
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
# =========================
# QUEUE
# =========================
@admin_only
async def queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db()
    rows = con.execute(
        """
        SELECT id, scheduled_at, status
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
# =========================
# DELETE
# =========================
@admin_only
async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        post_id = int(context.args[0])
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
    if not row or not row[0]:
        await update.message.reply_text(
            "❌ پست پیدا نشد یا هنوز منتشر نشده."
        )
        con.close()
        return
    try:
        await context.bot.delete_message(
            chat_id=CHANNEL_ID,
            message_id=row[0]
        )
    except Exception as error:
        await update.message.reply_text(
            f"❌ حذف نشد:\n{error}"
        )
        con.close()
        return
    con.execute(
        """
        UPDATE posts
        SET status='deleted'
        WHERE id=?
        """,
        (post_id,)
    )
    con.commit()
    con.close()
    await update.message.reply_text(
        "🗑️ پست حذف شد."
    )
# =========================
# PIN
# =========================
@admin_only
async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        post_id = int(context.args[0])
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
            "❌ پست پیدا نشد."
        )
        return
    try:
        await context.bot.pin_chat_message(
            chat_id=CHANNEL_ID,
            message_id=row[0],
            disable_notification=True
        )
    except Exception as error:
        await update.message.reply_text(
            f"❌ پین نشد:\n{error}"
        )
        return
    await update.message.reply_text(
        "📌 پست پین شد."
    )
# =========================
# CHANNEL DESCRIPTION
# =========================
@admin_only
async def desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(
        context.args
    ).strip()
    if not text:
        await update.message.reply_text(
            "/desc متن توضیحات"
        )
        return
    try:
        await context.bot.set_chat_description(
            chat_id=CHANNEL_ID,
            description=text[:255]
        )
    except Exception as error:
        await update.message.reply_text(
            f"❌ توضیحات تغییر نکرد:\n{error}"
        )
        return
    await update.message.reply_text(
        "✅ توضیحات کانال تغییر کرد."
    )
# =========================================================
# INVITE SYSTEM
# =========================================================
# =========================
# CREATE INVITE
# =========================
@admin_only
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        link = await create_direct_invite(
            context.bot
        )
    except Exception as error:
        await update.message.reply_text(
            "❌ ساخت لینک دعوت انجام نشد.\n\n"
            f"{error}\n\n"
            "مطمئن شو ربات در کانال Admin است و "
            "اجازه مدیریت لینک‌های دعوت را دارد."
        )
        return
    await update.message.reply_text(
        "🔗 لینک عضویت مستقیم Music Danial:\n\n"
        f"{link}\n\n"
        "✅ بدون درخواست عضویت\n"
        "✅ بدون تأیید ادمین\n"
        "👥 قابل ارسال برای دیگران"
    )
# =========================
# MY INVITE
# هر کاربر لینک دعوت می‌گیرد
# =========================
async def myinvite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    save_user(user)
    try:
        link = await create_direct_invite(
            context.bot
        )
        await update.message.reply_text(
            "🔗 لینک دعوت Music Danial:\n\n"
            f"{link}\n\n"
            "👥 این لینک را برای دوستانت بفرست "
            "تا مستقیم وارد کانال شوند."
        )
    except Exception as error:
        await update.message.reply_text(
            "❌ ساخت لینک دعوت انجام نشد."
        )
# =========================
# SEND INVITE TO ONE USER
# =========================
@admin_only
async def inviteid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text(
            "فرمت درست:\n\n"
            "/inviteid USER_ID"
        )
        return
    # بررسی اینکه کاربر قبلاً ربات را Start کرده
    con = db()
    row = con.execute(
        """
        SELECT user_id, first_name, username
        FROM users
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()
    con.close()
    if not row:
        await update.message.reply_text(
            "❌ این User ID در لیست کاربران ربات نیست.\n\n"
            "کاربر باید ابتدا ربات را Start کند."
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
                "برای عضویت مستقیم در کانال روی لینک زیر بزن:\n\n"
                f"🔗 {link}\n\n"
                "👥 می‌توانی این لینک را برای دوستانت هم بفرستی."
            )
        )
        await update.message.reply_text(
            f"✅ دعوت برای کاربر {user_id} ارسال شد."
        )
    except Exception as error:
        await update.message.reply_text(
            "❌ ارسال دعوت انجام نشد.\n\n"
            f"{error}"
        )
# =========================
# SEND INVITES TO ALL USERS
# =========================
@admin_only
async def sendinvites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⏳ ارسال دعوت‌ها شروع شد...\n\n"
        "برای جلوگیری از ارسال بیش‌ازحد، بین پیام‌ها فاصله گذاشته شده."
    )
    # ساخت یک لینک مشترک
    try:
        link = await create_direct_invite(
            context.bot
        )
    except Exception as error:
        await update.message.reply_text(
            f"❌ ساخت لینک دعوت انجام نشد:\n{error}"
        )
        return
    con = db()
    users = con.execute(
        """
        SELECT user_id
        FROM users
        ORDER BY created_at ASC
        """
    ).fetchall()
    con.close()
    total = len(users)
    sent = 0
    failed = 0
    for (user_id,) in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "🎵 Music Danial\n\n"
                    "🔗 برای عضویت مستقیم در کانال روی لینک زیر بزن:\n\n"
                    f"{link}\n\n"
                    "👥 این لینک را می‌توانی برای دوستانت "
                    "هم ارسال کنی."
                )
            )
            sent += 1
        except Exception:
            failed += 1
        # فاصله بین ارسال‌ها
        await asyncio.sleep(INVITE_DELAY)
    await update.message.reply_text(
        "✅ ارسال دعوت‌ها تمام شد.\n\n"
        f"👥 کل کاربران: {total}\n"
        f"📨 ارسال موفق: {sent}\n"
        f"❌ ناموفق: {failed}"
    )
# =========================
# USERS
# نمایش کاربران ذخیره‌شده
# =========================
@admin_only
async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db()
    rows = con.execute(
        """
        SELECT user_id, username, first_name, created_at
        FROM users
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()
    count = con.execute(
        """
        SELECT COUNT(*)
        FROM users
        """
    ).fetchone()[0]
    con.close()
    if not rows:
        await update.message.reply_text(
            "📋 هنوز هیچ کاربری ربات را Start نکرده است."
        )
        return
    lines = []
    for user_id, username, first_name, created_at in rows:
        username_text = (
            f"@{username}"
            if username
            else "بدون username"
        )
        lines.append(
            f"👤 {first_name}\n"
            f"ID: {user_id}\n"
            f"Username: {username_text}\n"
            f"────────────"
        )
    await update.message.reply_text(
        f"👥 تعداد کاربران ذخیره‌شده: {count}\n\n"
        + "\n".join(lines)
    )
# =========================
# MAIN
# =========================
def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN در Environment تنظیم نشده است."
        )
    if ADMIN_USER_ID == 0:
        raise RuntimeError(
            "ADMIN_USER_ID در Environment تنظیم نشده است."
        )
    # ساخت دیتابیس
    db()
    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )
    # =========================
    # MUSIC COMMANDS
    # =========================
    application.add_handler(
        CommandHandler("start", start)
    )
    application.add_handler(
        CommandHandler("post", post)
    )
    application.add_handler(
        CommandHandler("schedule", schedule)
    )
    application.add_handler(
        CommandHandler("queue", queue)
    )
    application.add_handler(
        CommandHandler("delete", delete)
    )
    application.add_handler(
        CommandHandler("pin", pin)
    )
    application.add_handler(
        CommandHandler("desc", desc)
    )
    # =========================
    # INVITE COMMANDS
    # =========================
    application.add_handler(
        CommandHandler("invite", invite)
    )
    application.add_handler(
        CommandHandler("myinvite", myinvite)
    )
    application.add_handler(
        CommandHandler("inviteid", inviteid)
    )
    application.add_handler(
        CommandHandler("sendinvites", sendinvites)
    )
    application.add_handler(
        CommandHandler("users", users)
    )
    # =========================
    # START BOT
    # =========================
    application.run_polling()
# =========================
# RUN
# =========================
if __name__ == "__main__":
    main()
