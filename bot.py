import os
import sqlite3
from datetime import datetime
from functools import wraps

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@musicdanial2023")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

DB = "musicdanial.db"

def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS posts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_message_id INTEGER,
        file_id TEXT,
        caption TEXT,
        scheduled_at TEXT,
        status TEXT DEFAULT 'published',
        created_at TEXT NOT NULL
    )""")
    con.commit()
    return con

def admin_only(fn):
    @wraps(fn)
    async def wrapper(update, context):
        if not update.effective_user or update.effective_user.id != ADMIN_USER_ID:
            return
        return await fn(update, context)
    return wrapper

def caption(title, artist=""):
    if artist:
        return f"🎵 {artist} — {title}\n\n🎧 Music Danial\n@musicdanial2023"
    return f"🎵 {title}\n\n🎧 Music Danial\n@musicdanial2023"

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Music Danial Manager آماده است.\n\n"
        "/post عنوان | خواننده — انتشار موزیک بعدی\n"
        "/schedule عنوان | خواننده | دقیقه — زمان‌بندی\n"
        "/queue — صف زمان‌بندی‌شده\n"
        "/delete شناسه — حذف پست\n"
        "/pin شناسه — پین کردن پست\n"
        "/desc متن — تغییر توضیحات کانال"
    )

@admin_only
async def post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Reply to an audio/document containing audio.
    if not update.message.reply_to_message:
        await update.message.reply_text("روی فایل موزیک Reply کن و بعد /post عنوان | خواننده را بفرست.")
        return
    reply = update.message.reply_to_message
    media = reply.audio or reply.document
    if not media:
        await update.message.reply_text("پیام Reply شده باید فایل صوتی باشد.")
        return
    raw = " ".join(context.args).strip()
    parts = [p.strip() for p in raw.split("|")]
    title = parts[0] if parts else "موزیک جدید"
    artist = parts[1] if len(parts) > 1 else ""
    cap = caption(title, artist)
    msg = await context.bot.send_audio(
        chat_id=CHANNEL_ID,
        audio=media.file_id,
        caption=cap
    )
    con = db()
    con.execute(
        "INSERT INTO posts(telegram_message_id,file_id,caption,created_at) VALUES(?,?,?,?)",
        (msg.message_id, media.file_id, cap, datetime.utcnow().isoformat())
    )
    con.commit(); con.close()
    await update.message.reply_text(f"✅ منتشر شد. شناسه: {msg.message_id}")

@admin_only
async def schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "روی فایل موزیک Reply کن و /schedule عنوان | خواننده | دقیقه را بفرست."
        )
        return
    raw = " ".join(context.args).strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await update.message.reply_text("فرمت: /schedule عنوان | خواننده | دقیقه")
        return
    title, artist, minutes_s = parts[0], parts[1], parts[2]
    try:
        minutes = int(minutes_s)
        if minutes < 1 or minutes > 10080:
            raise ValueError
    except ValueError:
        await update.message.reply_text("دقیقه باید عددی بین 1 تا 10080 باشد.")
        return

    reply = update.message.reply_to_message
    media = reply.audio or reply.document
    if not media:
        await update.message.reply_text("پیام Reply شده باید فایل صوتی باشد.")
        return

    cap = caption(title, artist)
    con = db()
    cur = con.execute(
        "INSERT INTO posts(file_id,caption,scheduled_at,status,created_at) VALUES(?,?,?,?,?)",
        (media.file_id, cap, f"+{minutes}m", "scheduled", datetime.utcnow().isoformat())
    )
    post_id = cur.lastrowid
    con.commit(); con.close()

    async def publish_job(ctx):
        con2 = db()
        row = con2.execute("SELECT file_id,caption FROM posts WHERE id=?", (post_id,)).fetchone()
        if row:
            msg = await ctx.bot.send_audio(chat_id=CHANNEL_ID, audio=row[0], caption=row[1])
            con2.execute(
                "UPDATE posts SET telegram_message_id=?, status='published' WHERE id=?",
                (msg.message_id, post_id)
            )
            con2.commit()
        con2.close()

    context.job_queue.run_once(publish_job, when=minutes*60, name=f"post-{post_id}")
    await update.message.reply_text(f"🕐 در صف قرار گرفت. شناسه: {post_id}")

@admin_only
async def queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db()
    rows = con.execute(
        "SELECT id,scheduled_at,status FROM posts WHERE status='scheduled' ORDER BY id DESC"
    ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("صف خالی است.")
        return
    text = "\n".join(f"#{r[0]} — {r[1]} — {r[2]}" for r in rows)
    await update.message.reply_text("📋 صف:\n" + text)

@admin_only
async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("فرمت: /delete شناسه")
        return
    con = db()
    row = con.execute(
        "SELECT telegram_message_id FROM posts WHERE id=?", (pid,)
    ).fetchone()
    if not row or not row[0]:
        await update.message.reply_text("پست پیدا نشد یا هنوز منتشر نشده.")
        con.close(); return
    await context.bot.delete_message(CHANNEL_ID, row[0])
    con.execute("UPDATE posts SET status='deleted' WHERE id=?", (pid,))
    con.commit(); con.close()
    await update.message.reply_text("🗑️ حذف شد.")

@admin_only
async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("فرمت: /pin شناسه")
        return
    con = db()
    row = con.execute(
        "SELECT telegram_message_id FROM posts WHERE id=?", (pid,)
    ).fetchone()
    con.close()
    if not row or not row[0]:
        await update.message.reply_text("پست پیدا نشد.")
        return
    await context.bot.pin_chat_message(CHANNEL_ID, row[0], disable_notification=True)
    await update.message.reply_text("📌 پین شد.")

@admin_only
async def desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("فرمت: /desc متن توضیحات")
        return
    await context.bot.set_chat_description(CHANNEL_ID, text[:255])
    await update.message.reply_text("✅ توضیحات کانال تغییر کرد.")

def main():
    if not TOKEN or ADMIN_USER_ID == 0:
        raise RuntimeError("BOT_TOKEN و ADMIN_USER_ID را در .env تنظیم کن.")
    db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("post", post))
    app.add_handler(CommandHandler("schedule", schedule))
    app.add_handler(CommandHandler("queue", queue))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("pin", pin))
    app.add_handler(CommandHandler("desc", desc))
    app.run_polling()

if __name__ == "__main__":
    main()
