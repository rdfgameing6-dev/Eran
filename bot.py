import os
import sqlite3
import logging
from decimal import Decimal, InvalidOperation

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# IMPORTANT:
# Set BOT_TOKEN in your environment. Do not publish your bot token.
BOT_TOKEN ="8106689891:AAHEBr5-nJsPzgTIw6YKY3NsmGwVTg0IZLM"
ADMIN_IDS = {8926100324}
DB_FILE = "reward_bot.db"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row


def init_db():
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        name TEXT DEFAULT '',
        balance REAL DEFAULT 0,
        upi TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        amount REAL NOT NULL,
        link TEXT NOT NULL,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS task_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        request_type TEXT NOT NULL,
        submission TEXT DEFAULT '',
        screenshot_file_id TEXT DEFAULT '',
        status TEXT DEFAULT 'PENDING',
        reward REAL DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        processed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS gifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        amount REAL NOT NULL,
        per_account INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS gift_claims (
        gift_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(gift_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        method TEXT NOT NULL,
        payment TEXT NOT NULL,
        status TEXT DEFAULT 'PENDING',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        processed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender INTEGER NOT NULL,
        receiver INTEGER NOT NULL,
        amount REAL NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)
    db.commit()


def is_admin(user_id):
    return user_id in ADMIN_IDS


def money(value):
    return f"₹{float(value):.2f}"


def parse_amount(value):
    try:
        amount = Decimal(str(value).strip().replace(",", "").replace("₹", ""))
        if not amount.is_finite() or amount <= 0:
            return None
        return float(amount.quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return None


def save_user(user):
    db.execute("""
        INSERT INTO users(user_id, username, name)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, name=excluded.name
    """, (user.id, user.username or "", user.full_name or ""))
    db.commit()


def get_user(user_id):
    return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def get_setting(key):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key, value):
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    db.commit()


def clear_state(context):
    context.user_data.clear()


def main_menu(user_id):
    rows = [
        ["📋 BOT TASK", "💸 MY BALANCE"],
        ["🎁 GIFT CODE", "⚡ QUICK PAY"],
        ["💳 PAYOUT METHOD", "🚀 WITHDRAW"],
    ]
    if is_admin(user_id):
        rows.append(["🛠 ADMIN CONTROL"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def back_main():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="back_main")]])


def back_admin():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="back_admin")]])


async def check_forced_sub(update, context):
    user_id = update.effective_user.id
    if is_admin(user_id):
        return True
    raw = get_setting("required_channels")
    if not raw:
        return True
    channels = [x.strip() for x in raw.split(",") if x.strip()]
    for link in channels:
        try:
            chat = link
            if "t.me/" in chat:
                chat = "@" + chat.split("t.me/")[-1].split("/")[0]
            member = await context.bot.get_chat_member(chat_id=chat, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception:
            log.exception("Could not check membership for %s", link)
            return False
    return True


async def show_join_required(update, context):
    raw = get_setting("required_channels") or ""
    channels = [x.strip() for x in raw.split(",") if x.strip()]
    keyboard = [[InlineKeyboardButton(f"📢 Join Channel {i}", url=link)]
                for i, link in enumerate(channels, 1)]
    keyboard.append([InlineKeyboardButton("✅ Check Join", callback_data="check_join")])
    message = update.effective_message
    await message.reply_text(
        "📢 CHANNEL SUBSCRIPTION REQUIRED\n\nJoin all listed channels, then tap Check Join.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    clear_state(context)
    if not await check_forced_sub(update, context):
        await show_join_required(update, context)
        return
    await update.effective_message.reply_text(
        "🎉 Welcome to ERAN TASK BOT!\n\nChoose an option:",
        reply_markup=main_menu(update.effective_user.id),
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_user)
    await update.effective_message.reply_text(
        f"🆔 Your Telegram User ID:\n{update.effective_user.id}",
        reply_markup=back_main(),
    )


async def show_tasks(update, context):
    if not await check_forced_sub(update, context):
        await show_join_required(update, context)
        return
    save_user(update.effective_user)
    clear_state(context)
    tasks = db.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    if not tasks:
        await update.effective_message.reply_text("📋 AVAILABLE TASKS\n\nNo tasks available right now.", reply_markup=back_main())
        return
    keyboard = [[InlineKeyboardButton(f"{t['name']} ⚡ ({money(t['amount'])})", callback_data=f"task:{t['id']}")] for t in tasks]
    keyboard.append([InlineKeyboardButton("⬅️ BACK", callback_data="back_main")])
    await update.effective_message.reply_text("📋 AVAILABLE TASKS\n\nSelect a task:", reply_markup=InlineKeyboardMarkup(keyboard))


async def task_details(update, context):
    q = update.callback_query
    await q.answer()
    task_id = int(q.data.split(":")[1])
    task = db.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
    if not task:
        await q.message.reply_text("❌ Task no longer available.", reply_markup=back_main())
        return
    user_id = q.from_user.id
    approved = db.execute("SELECT 1 FROM task_requests WHERE task_id=? AND user_id=? AND status='APPROVED' LIMIT 1", (task_id, user_id)).fetchone()
    pending = db.execute("SELECT 1 FROM task_requests WHERE task_id=? AND user_id=? AND status='PENDING' LIMIT 1", (task_id, user_id)).fetchone()
    keyboard = [
        [InlineKeyboardButton("🔗 TASK LINK", url=task["link"])],
        [InlineKeyboardButton("📸 UPLOAD SCREENSHOT", callback_data=f"upload:{task_id}")],
        [InlineKeyboardButton("🔢 SEND REFER LINK / NUMBER", callback_data=f"refer:{task_id}")],
    ]
    if approved:
        keyboard.append([InlineKeyboardButton("✅ TASK COMPLETED", callback_data="nothing")])
    elif pending:
        keyboard.append([InlineKeyboardButton("⏳ SUBMISSION PENDING", callback_data="nothing")])
    keyboard.append([InlineKeyboardButton("⬅️ BACK", callback_data="back_tasks")])
    await q.message.reply_text(
        f"📋 TASK DETAILS\n\n📝 TASK NAME: {task['name']}\n💰 REWARD: {money(task['amount'])}\n🔗 LINK:\n{task['link']}\n\nComplete the task and submit proof. Admin reviews submissions.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def begin_task_submission(update, context, kind):
    q = update.callback_query
    await q.answer()
    task_id = int(q.data.split(":")[1])
    task = db.execute("SELECT id FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
    if not task:
        await q.message.reply_text("❌ Task is no longer available.", reply_markup=back_main())
        return
    clear_state(context)
    context.user_data["state"] = "task_screenshot" if kind == "SCREENSHOT" else "task_refer"
    context.user_data["task_id"] = task_id
    prompt = ("📸 Complete the task, then send your screenshot here." if kind == "SCREENSHOT"
              else "🔢 Send your referral link or referral number.")
    await q.message.reply_text(prompt, reply_markup=back_main())


async def submit_task(update, context, kind, submission="", screenshot=""):
    task_id = context.user_data.get("task_id")
    user_id = update.effective_user.id
    if not task_id:
        clear_state(context)
        await update.effective_message.reply_text("❌ Task session expired.", reply_markup=main_menu(user_id))
        return
    task = db.execute("SELECT * FROM tasks WHERE id=? AND active=1", (task_id,)).fetchone()
    if not task:
        clear_state(context)
        await update.effective_message.reply_text("❌ Task is no longer available.", reply_markup=main_menu(user_id))
        return
    pending = db.execute("SELECT 1 FROM task_requests WHERE task_id=? AND user_id=? AND status='PENDING'", (task_id, user_id)).fetchone()
    if pending:
        clear_state(context)
        await update.effective_message.reply_text("⏳ You already have a pending submission for this task.", reply_markup=main_menu(user_id))
        return
    cur = db.execute("""INSERT INTO task_requests(task_id,user_id,request_type,submission,screenshot_file_id,reward)
                        VALUES(?,?,?,?,?,?)""",
                     (task_id, user_id, kind, submission, screenshot, task["amount"]))
    request_id = cur.lastrowid
    db.commit()
    user = get_user(user_id)
    for admin_id in ADMIN_IDS:
        try:
            text = (f"📥 NEW TASK SUBMISSION\n\nRequest: #{request_id}\nTask: {task['name']}\nReward: {money(task['amount'])}\n\n"
                    f"User ID: {user_id}\nName: {user['name']}\nUsername: @{user['username'] or '-'}\nType: {kind}")
            if submission:
                text += f"\n\nSubmission:\n{submission}"
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ APPROVE", callback_data=f"taskapprove:{request_id}"),
                InlineKeyboardButton("❌ REJECT", callback_data=f"taskreject:{request_id}"),
            ]])
            await context.bot.send_message(admin_id, text, reply_markup=keyboard)
            if screenshot:
                await context.bot.send_photo(admin_id, screenshot, caption=f"📸 Proof for request #{request_id}, user {user_id}")
        except Exception:
            log.exception("Could not notify admin %s", admin_id)
    clear_state(context)
    await update.effective_message.reply_text(
        f"✅ SUBMISSION SENT\n\nRequest: #{request_id}\nTask: {task['name']}\nReward: {money(task['amount'])}\n\n⏳ Status: PENDING\nReward is added only after approval.",
        reply_markup=main_menu(user_id),
    )


async def photo_handler(update, context):
    if context.user_data.get("state") != "task_screenshot":
        return
    if not await check_forced_sub(update, context):
        await show_join_required(update, context)
        return
    await submit_task(update, context, "SCREENSHOT", screenshot=update.message.photo[-1].file_id)


async def gift_menu(update, context):
    if not await check_forced_sub(update, context):
        await show_join_required(update, context)
        return
    clear_state(context)
    context.user_data["state"] = "gift"
    await update.effective_message.reply_text("🎁 GIFT CODE\n\nEnter your gift code:", reply_markup=back_main())


async def process_gift(update, context):
    code = update.message.text.strip()
    gift = db.execute("SELECT * FROM gifts WHERE code=? AND active=1", (code,)).fetchone()
    if not gift:
        await update.message.reply_text("❌ Invalid gift code.", reply_markup=back_main())
        return
    used = db.execute("SELECT COUNT(*) FROM gift_claims WHERE gift_id=? AND user_id=?", (gift["id"], update.effective_user.id)).fetchone()[0]
    if used >= gift["per_account"]:
        await update.message.reply_text("⚠️ This gift code has reached your account limit.", reply_markup=back_main())
        return
    try:
        db.execute("INSERT INTO gift_claims(gift_id,user_id) VALUES(?,?)", (gift["id"], update.effective_user.id))
        db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (gift["amount"], update.effective_user.id))
        db.commit()
    except sqlite3.IntegrityError:
        await update.message.reply_text("⚠️ This gift code was already used.", reply_markup=back_main())
        return
    clear_state(context)
    await update.message.reply_text(f"🎉 GIFT SUCCESSFUL!\n\n🎁 {gift['name']}\n💰 Added: {money(gift['amount'])}", reply_markup=main_menu(update.effective_user.id))


async def my_balance(update, context):
    if not await check_forced_sub(update, context):
        await show_join_required(update, context)
        return
    clear_state(context)
    user = get_user(update.effective_user.id)
    await update.effective_message.reply_text(f"💸 MY BALANCE\n\n💰 Balance: {money(user['balance'])}\n🆔 User ID: {user['user_id']}", reply_markup=back_main())


async def payout_method(update, context):
    user = get_user(update.effective_user.id)
    keyboard = [[InlineKeyboardButton("✏️ CHANGE UPI" if user["upi"] else "➕ SET UPI", callback_data="setupi")],
                [InlineKeyboardButton("⬅️ BACK", callback_data="back_main")]]
    message = f"💳 PAYOUT METHOD\n\nUPI ID:\n{user['upi']}\n\nYour saved UPI will be used during withdrawal." if user["upi"] else "💳 PAYOUT METHOD\n\nUPI is not set."
    await update.effective_message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard))


async def set_upi(update, context):
    q = update.callback_query
    await q.answer()
    clear_state(context)
    context.user_data["state"] = "upi"
    await q.message.reply_text("💳 Enter your UPI ID:", reply_markup=back_main())


async def save_upi(update, context):
    upi = update.message.text.strip()
    if "@" not in upi or " " in upi or len(upi) < 5:
        await update.message.reply_text("❌ Enter a valid UPI ID.", reply_markup=back_main())
        return
    db.execute("UPDATE users SET upi=? WHERE user_id=?", (upi, update.effective_user.id))
    db.commit()
    clear_state(context)
    await update.message.reply_text(f"✅ UPI SAVED\n\nUPI: {upi}", reply_markup=main_menu(update.effective_user.id))


async def withdraw_menu(update, context):
    user = get_user(update.effective_user.id)
    if not user["upi"]:
        await update.effective_message.reply_text("🚀 Set your UPI ID before withdrawing.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ SET UPI", callback_data="setupi")],
            [InlineKeyboardButton("⬅️ BACK", callback_data="back_main")],
        ]))
        return
    clear_state(context)
    context.user_data["state"] = "withdraw"
    await update.effective_message.reply_text(f"🚀 WITHDRAW\n\nBalance: {money(user['balance'])}\nUPI: {user['upi']}\n\nEnter withdrawal amount:", reply_markup=back_main())


async def create_withdrawal(update, context):
    amount = parse_amount(update.message.text)
    if amount is None:
        await update.message.reply_text("❌ Enter a valid amount.", reply_markup=back_main())
        return
    user = get_user(update.effective_user.id)
    if user["balance"] < amount:
        await update.message.reply_text("❌ Insufficient balance.", reply_markup=back_main())
        return
    db.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, user["user_id"], amount))
    cur = db.execute("INSERT INTO withdrawals(user_id,amount,method,payment) VALUES(?,?,?,?)", (user["user_id"], amount, "UPI", user["upi"]))
    request_id = cur.lastrowid
    db.commit()
    clear_state(context)
    updated = get_user(user["user_id"])
    await update.message.reply_text(f"✅ Withdrawal request submitted!\n\nAmount: {money(amount)}\nUPI: {user['upi']}\nStatus: PENDING\nNew balance: {money(updated['balance'])}", reply_markup=main_menu(user["user_id"]))
    for admin in ADMIN_IDS:
        try:
            await context.bot.send_message(admin, f"🚀 NEW WITHDRAWAL #{request_id}\n\nUser ID: {user['user_id']}\nName: {user['name']}\nAmount: {money(amount)}\nUPI: {user['upi']}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{request_id}"),
                    InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{request_id}"),
                ]]))
        except Exception:
            log.exception("Could not notify admin")


async def quick_pay(update, context):
    clear_state(context)
    context.user_data["state"] = "send_user"
    await update.effective_message.reply_text("⚡ QUICK PAY\n\nEnter receiver User ID:", reply_markup=back_main())


async def quick_pay_user(update, context):
    try:
        receiver = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ User ID must be a number.", reply_markup=back_main())
        return
    if receiver == update.effective_user.id:
        await update.message.reply_text("❌ You cannot send money to yourself.", reply_markup=back_main())
        return
    if not get_user(receiver):
        await update.message.reply_text("❌ User not found. Receiver must open the bot first.", reply_markup=back_main())
        return
    context.user_data["receiver"] = receiver
    context.user_data["state"] = "send_amount"
    user = get_user(receiver)
    await update.message.reply_text(f"👤 Receiver: {user['name']}\nID: {receiver}\n\nEnter amount:", reply_markup=back_main())


async def quick_pay_amount(update, context):
    amount = parse_amount(update.message.text)
    receiver = context.user_data.get("receiver")
    sender = get_user(update.effective_user.id)
    if amount is None:
        await update.message.reply_text("❌ Enter a valid amount.", reply_markup=back_main())
        return
    if not receiver or not sender or sender["balance"] < amount:
        await update.message.reply_text("❌ Insufficient balance or invalid receiver.", reply_markup=back_main())
        return
    try:
        db.execute("BEGIN")
        debit = db.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, sender["user_id"], amount))
        if debit.rowcount != 1:
            db.rollback()
            await update.message.reply_text("❌ Insufficient balance.", reply_markup=back_main())
            return
        db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, receiver))
        db.execute("INSERT INTO transfers(sender,receiver,amount) VALUES(?,?,?)", (sender["user_id"], receiver, amount))
        db.commit()
    except Exception:
        db.rollback()
        raise
    clear_state(context)
    await update.message.reply_text(f"✅ PAYMENT SENT\n\nAmount: {money(amount)}\nReceiver ID: {receiver}", reply_markup=main_menu(update.effective_user.id))
    try:
        await context.bot.send_message(receiver, f"💰 PAYMENT RECEIVED\n\nAmount: {money(amount)}\nFrom User ID: {sender['user_id']}")
    except Exception:
        pass


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ADD BOT TASK", callback_data="admin_add_task"), InlineKeyboardButton("✏️ EDIT TASK", callback_data="admin_edit_task")],
        [InlineKeyboardButton("📥 TASK REQUESTS", callback_data="admin_task_requests"), InlineKeyboardButton("🎁 GIFTS", callback_data="admin_gifts")],
        [InlineKeyboardButton("👥 ALL USERS", callback_data="admin_users"), InlineKeyboardButton("🚀 WITHDRAWALS", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("➕➖ USER BALANCE", callback_data="admin_balance"), InlineKeyboardButton("📜 WITHDRAWAL HISTORY", callback_data="admin_history")],
        [InlineKeyboardButton("📢 SET JOIN CHANNELS", callback_data="admin_set_channel")],
        [InlineKeyboardButton("⬅️ BACK", callback_data="back_main")],
    ])


async def admin_control(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return
    clear_state(context)
    await update.effective_message.reply_text("🛠 ADMIN CONTROL\n\nChoose an option:", reply_markup=admin_keyboard())


async def admin_callback(update, context):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True)
        return
    await q.answer()
    data = q.data
    if data == "admin_set_channel":
        clear_state(context)
        context.user_data["admin_state"] = "set_channel"
        await q.message.reply_text("Send up to 4 public channel links separated by commas, or send OFF to disable:", reply_markup=back_admin())
    elif data == "admin_add_task":
        clear_state(context)
        context.user_data["admin_state"] = "task_name"
        await q.message.reply_text("➕ ADD TASK\n\nEnter task name:", reply_markup=back_admin())
    elif data == "admin_edit_task":
        tasks = db.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        keyboard = [[InlineKeyboardButton(f"{t['name']} ({'ON' if t['active'] else 'OFF'})", callback_data=f"edit_task:{t['id']}")] for t in tasks]
        keyboard.append([InlineKeyboardButton("⬅️ BACK", callback_data="back_admin")])
        await q.message.reply_text("Select a task to edit:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "admin_task_requests":
        rows = db.execute("""SELECT r.*,t.name task_name,u.name user_name,u.username
            FROM task_requests r JOIN tasks t ON t.id=r.task_id JOIN users u ON u.user_id=r.user_id
            WHERE r.status='PENDING' ORDER BY r.id DESC LIMIT 30""").fetchall()
        if not rows:
            await q.message.reply_text("No pending task submissions.", reply_markup=back_admin())
        for r in rows:
            txt = f"📥 REQUEST #{r['id']}\nTask: {r['task_name']}\nReward: {money(r['reward'])}\nUser: {r['user_name']} ({r['user_id']})\nUsername: @{r['username'] or '-'}\nType: {r['request_type']}\nSubmission: {r['submission'] or '-'}"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ APPROVE", callback_data=f"taskapprove:{r['id']}"), InlineKeyboardButton("❌ REJECT", callback_data=f"taskreject:{r['id']}")]])
            await q.message.reply_text(txt, reply_markup=kb)
            if r["screenshot_file_id"]:
                await context.bot.send_photo(q.from_user.id, r["screenshot_file_id"], caption=f"Proof for request #{r['id']}")
    elif data == "admin_gifts":
        gifts = db.execute("SELECT * FROM gifts ORDER BY id DESC").fetchall()
        keyboard = [[InlineKeyboardButton(f"{g['code']} • {money(g['amount'])}", callback_data=f"edit_gift:{g['id']}")] for g in gifts]
        keyboard.append([InlineKeyboardButton("➕ ADD GIFT", callback_data="admin_add_gift")])
        keyboard.append([InlineKeyboardButton("⬅️ BACK", callback_data="back_admin")])
        await q.message.reply_text("🎁 GIFT MANAGEMENT", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "admin_add_gift":
        clear_state(context)
        context.user_data["admin_state"] = "gift_code"
        await q.message.reply_text("Enter new gift code:", reply_markup=back_admin())
    elif data == "admin_users":
        rows = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 40").fetchall()
        text = "👥 USERS\n\n" + "\n────────────\n".join(f"ID: {u['user_id']}\nName: {u['name']}\nBalance: {money(u['balance'])}\nUPI: {u['upi'] or '-'}" for u in rows)
        await q.message.reply_text(text[:3900] or "No users.", reply_markup=back_admin())
    elif data == "admin_withdrawals":
        rows = db.execute("SELECT w.*,u.name FROM withdrawals w LEFT JOIN users u ON u.user_id=w.user_id WHERE w.status='PENDING' ORDER BY w.id DESC LIMIT 30").fetchall()
        if not rows:
            await q.message.reply_text("No pending withdrawals.", reply_markup=back_admin())
        for w in rows:
            await q.message.reply_text(f"🚀 WITHDRAWAL #{w['id']}\nUser: {w['name']} ({w['user_id']})\nAmount: {money(w['amount'])}\nUPI: {w['payment']}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{w['id']}"), InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{w['id']}")]]))
    elif data == "admin_balance":
        clear_state(context)
        context.user_data["admin_state"] = "balance_user"
        await q.message.reply_text("Enter user ID:", reply_markup=back_admin())
    elif data == "admin_history":
        rows = db.execute("SELECT * FROM withdrawals ORDER BY id DESC LIMIT 30").fetchall()
        text = "📜 WITHDRAWAL HISTORY\n\n" + "\n────────────\n".join(f"#{w['id']} {w['status']}\nUser: {w['user_id']}\nAmount: {money(w['amount'])}\nUPI: {w['payment']}" for w in rows)
        await q.message.reply_text(text[:3900] or "No withdrawal history.", reply_markup=back_admin())
    elif data.startswith("edit_task:"):
        task_id = int(data.split(":")[1])
        t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            await q.message.reply_text("Task not found.", reply_markup=back_admin()); return
        await q.message.reply_text(f"✏️ TASK\nName: {t['name']}\nReward: {money(t['amount'])}\nLink: {t['link']}\nStatus: {'ON' if t['active'] else 'OFF'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ NAME", callback_data=f"taskeditname:{task_id}"), InlineKeyboardButton("💰 AMOUNT", callback_data=f"taskeditamount:{task_id}")],
                [InlineKeyboardButton("🔗 LINK", callback_data=f"taskeditlink:{task_id}")],
                [InlineKeyboardButton("🔄 ON/OFF", callback_data=f"tasktoggle:{task_id}"), InlineKeyboardButton("🗑 DELETE", callback_data=f"taskdel:{task_id}")],
                [InlineKeyboardButton("⬅️ BACK", callback_data="admin_edit_task")],
            ]))
    elif data.startswith("edit_gift:"):
        gift_id = int(data.split(":")[1])
        g = db.execute("SELECT * FROM gifts WHERE id=?", (gift_id,)).fetchone()
        if not g:
            await q.message.reply_text("Gift not found.", reply_markup=back_admin()); return
        await q.message.reply_text(f"🎁 GIFT\nCode: {g['code']}\nName: {g['name']}\nAmount: {money(g['amount'])}\nLimit: {g['per_account']}\nStatus: {'ON' if g['active'] else 'OFF'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ NAME", callback_data=f"giftname:{gift_id}"), InlineKeyboardButton("💰 AMOUNT", callback_data=f"giftamount:{gift_id}")],
                [InlineKeyboardButton("👤 LIMIT", callback_data=f"giftlimit:{gift_id}"), InlineKeyboardButton("🔄 ON/OFF", callback_data=f"gifttoggle:{gift_id}")],
                [InlineKeyboardButton("🗑 DELETE", callback_data=f"giftdel:{gift_id}")],
                [InlineKeyboardButton("⬅️ BACK", callback_data="admin_gifts")],
            ]))
    elif data.startswith(("taskeditname:", "taskeditamount:", "taskeditlink:", "giftname:", "giftamount:", "giftlimit:")):
        prefix, item_id = data.split(":")
        clear_state(context)
        context.user_data["item_id"] = int(item_id)
        context.user_data["admin_state"] = prefix
        prompts = {"taskeditname":"Enter new task name:", "taskeditamount":"Enter new task reward:", "taskeditlink":"Enter new task link:", "giftname":"Enter new gift name:", "giftamount":"Enter new gift amount:", "giftlimit":"Enter per-account limit:"}
        await q.message.reply_text(prompts[prefix], reply_markup=back_admin())
    elif data.startswith("tasktoggle:"):
        item_id = int(data.split(":")[1])
        db.execute("UPDATE tasks SET active=1-active WHERE id=?", (item_id,)); db.commit()
        await q.message.reply_text("Task status updated.", reply_markup=back_admin())
    elif data.startswith("gifttoggle:"):
        item_id = int(data.split(":")[1])
        db.execute("UPDATE gifts SET active=1-active WHERE id=?", (item_id,)); db.commit()
        await q.message.reply_text("Gift status updated.", reply_markup=back_admin())
    elif data.startswith("taskdel:"):
        item_id = int(data.split(":")[1])
        db.execute("DELETE FROM task_requests WHERE task_id=?", (item_id,))
        db.execute("DELETE FROM tasks WHERE id=?", (item_id,)); db.commit()
        await q.message.reply_text("Task deleted.", reply_markup=back_admin())
    elif data.startswith("giftdel:"):
        item_id = int(data.split(":")[1])
        db.execute("DELETE FROM gift_claims WHERE gift_id=?", (item_id,))
        db.execute("DELETE FROM gifts WHERE id=?", (item_id,)); db.commit()
        await q.message.reply_text("Gift deleted.", reply_markup=back_admin())


async def admin_text(update, context):
    if not is_admin(update.effective_user.id):
        return False
    state = context.user_data.get("admin_state")
    if not state:
        return False
    text = update.message.text.strip()
    if state == "set_channel":
        if text.upper() == "OFF":
            db.execute("DELETE FROM settings WHERE key='required_channels'"); db.commit()
        else:
            links = [x.strip() for x in text.split(",") if x.strip()]
            if len(links) > 4 or any(not x.startswith("https://t.me/") for x in links):
                await update.message.reply_text("Send up to 4 https://t.me/ channel links, separated by commas, or OFF.", reply_markup=back_admin())
                return True
            set_setting("required_channels", ",".join(links))
        clear_state(context)
        await update.message.reply_text("✅ Channel setting updated.", reply_markup=main_menu(update.effective_user.id))
        return True
    if state == "task_name":
        context.user_data["new_task_name"] = text
        context.user_data["admin_state"] = "task_amount"
        await update.message.reply_text("Enter task reward amount:", reply_markup=back_admin()); return True
    if state == "task_amount":
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("Invalid amount. Try again:", reply_markup=back_admin()); return True
        context.user_data["new_task_amount"] = amount
        context.user_data["admin_state"] = "task_link"
        await update.message.reply_text("Enter task link (https:// or tg://):", reply_markup=back_admin()); return True
    if state == "task_link":
        if not text.startswith(("https://", "http://", "tg://")):
            await update.message.reply_text("Invalid link. Try again:", reply_markup=back_admin()); return True
        db.execute("INSERT INTO tasks(name,amount,link) VALUES(?,?,?)", (context.user_data["new_task_name"], context.user_data["new_task_amount"], text)); db.commit()
        clear_state(context)
        await update.message.reply_text("✅ Task added.", reply_markup=main_menu(update.effective_user.id)); return True
    if state == "gift_code":
        if db.execute("SELECT 1 FROM gifts WHERE code=?", (text,)).fetchone():
            await update.message.reply_text("Gift code already exists. Enter another:", reply_markup=back_admin()); return True
        context.user_data["new_gift_code"] = text
        context.user_data["admin_state"] = "gift_name"
        await update.message.reply_text("Enter gift name:", reply_markup=back_admin()); return True
    if state == "gift_name":
        context.user_data["new_gift_name"] = text
        context.user_data["admin_state"] = "gift_amount"
        await update.message.reply_text("Enter gift reward amount:", reply_markup=back_admin()); return True
    if state == "gift_amount":
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("Invalid amount. Try again:", reply_markup=back_admin()); return True
        context.user_data["new_gift_amount"] = amount
        context.user_data["admin_state"] = "gift_limit"
        await update.message.reply_text("Enter per-account limit (e.g. 1):", reply_markup=back_admin()); return True
    if state == "gift_limit":
        try:
            limit = int(text)
            if limit < 1: raise ValueError
        except ValueError:
            await update.message.reply_text("Enter a whole number of 1 or more:", reply_markup=back_admin()); return True
        try:
            db.execute("INSERT INTO gifts(code,name,amount,per_account) VALUES(?,?,?,?)", (context.user_data["new_gift_code"], context.user_data["new_gift_name"], context.user_data["new_gift_amount"], limit))
            db.commit()
        except sqlite3.IntegrityError:
            await update.message.reply_text("That code already exists.", reply_markup=back_admin()); return True
        clear_state(context)
        await update.message.reply_text("✅ Gift created.", reply_markup=main_menu(update.effective_user.id)); return True
    if state == "balance_user":
        try: uid = int(text)
        except ValueError:
            await update.message.reply_text("Enter a numeric user ID:", reply_markup=back_admin()); return True
        if not get_user(uid):
            await update.message.reply_text("User not found.", reply_markup=back_admin()); return True
        context.user_data["balance_user_id"] = uid
        context.user_data["admin_state"] = "balance_action"
        await update.message.reply_text("Send ADD 100 or DECREASE 100:", reply_markup=back_admin()); return True
    if state == "balance_action":
        parts = text.split()
        if len(parts) != 2 or parts[0].upper() not in ("ADD", "DECREASE") or parse_amount(parts[1]) is None:
            await update.message.reply_text("Use ADD 100 or DECREASE 100:", reply_markup=back_admin()); return True
        action, amount = parts[0].upper(), parse_amount(parts[1])
        uid = context.user_data["balance_user_id"]
        if action == "ADD":
            db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, uid))
        else:
            cur = db.execute("UPDATE users SET balance=balance-? WHERE user_id=? AND balance>=?", (amount, uid, amount))
            if cur.rowcount != 1:
                await update.message.reply_text("User has insufficient balance.", reply_markup=back_admin()); return True
        db.commit(); clear_state(context)
        await update.message.reply_text("✅ Balance updated.", reply_markup=main_menu(update.effective_user.id)); return True
    if state in ("taskeditname", "taskeditamount", "taskeditlink", "giftname", "giftamount", "giftlimit"):
        item_id = context.user_data["item_id"]
        if state == "taskeditname":
            db.execute("UPDATE tasks SET name=? WHERE id=?", (text, item_id))
        elif state == "taskeditamount":
            amount = parse_amount(text)
            if amount is None:
                await update.message.reply_text("Invalid amount:", reply_markup=back_admin()); return True
            db.execute("UPDATE tasks SET amount=? WHERE id=?", (amount, item_id))
        elif state == "taskeditlink":
            if not text.startswith(("https://", "http://", "tg://")):
                await update.message.reply_text("Invalid link:", reply_markup=back_admin()); return True
            db.execute("UPDATE tasks SET link=? WHERE id=?", (text, item_id))
        elif state == "giftname":
            db.execute("UPDATE gifts SET name=? WHERE id=?", (text, item_id))
        elif state == "giftamount":
            amount = parse_amount(text)
            if amount is None:
                await update.message.reply_text("Invalid amount:", reply_markup=back_admin()); return True
            db.execute("UPDATE gifts SET amount=? WHERE id=?", (amount, item_id))
        elif state == "giftlimit":
            try:
                limit = int(text)
                if limit < 1: raise ValueError
            except ValueError:
                await update.message.reply_text("Enter a number of 1 or more:", reply_markup=back_admin()); return True
            db.execute("UPDATE gifts SET per_account=? WHERE id=?", (limit, item_id))
        db.commit(); clear_state(context)
        await update.message.reply_text("✅ Updated.", reply_markup=main_menu(update.effective_user.id)); return True
    return False


async def process_task_request(update, context):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True); return
    await q.answer()
    action, request_id = q.data.split(":")
    r = db.execute("SELECT r.*,t.name task_name FROM task_requests r JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (int(request_id),)).fetchone()
    if not r or r["status"] != "PENDING":
        await q.message.reply_text("Request missing or already processed."); return
    if action == "taskapprove":
        db.execute("UPDATE task_requests SET status='APPROVED',processed_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'", (request_id,))
        db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (r["reward"], r["user_id"]))
        db.commit()
        message = f"🎉 Task approved: {r['task_name']}\nReward added: {money(r['reward'])}"
    else:
        db.execute("UPDATE task_requests SET status='REJECTED',processed_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'", (request_id,))
        db.commit()
        message = f"❌ Task rejected: {r['task_name']}\nNo reward was added."
    await q.message.reply_text(f"Request #{request_id} processed.")
    try: await context.bot.send_message(r["user_id"], message)
    except Exception: pass


async def process_withdrawal(update, context):
    q = update.callback_query
    if not is_admin(q.from_user.id):
        await q.answer("Admin only.", show_alert=True); return
    await q.answer()
    action, request_id = q.data.split(":")
    r = db.execute("SELECT * FROM withdrawals WHERE id=?", (int(request_id),)).fetchone()
    if not r or r["status"] != "PENDING":
        await q.message.reply_text("Withdrawal missing or already processed."); return
    if action == "approve":
        db.execute("UPDATE withdrawals SET status='APPROVED',processed_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'", (request_id,))
        message = f"✅ Withdrawal approved\nAmount: {money(r['amount'])}\nUPI: {r['payment']}"
    else:
        db.execute("UPDATE withdrawals SET status='REJECTED',processed_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING'", (request_id,))
        db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (r["amount"], r["user_id"]))
        message = f"❌ Withdrawal rejected\nRefunded: {money(r['amount'])}"
    db.commit()
    await q.message.reply_text(f"Withdrawal #{request_id}: {action}d.")
    try:
        u = get_user(r["user_id"])
        await context.bot.send_message(r["user_id"], message + f"\nNew balance: {money(u['balance'])}")
    except Exception: pass


async def callback_router(update, context):
    q = update.callback_query
    data = q.data
    if data == "check_join":
        if await check_forced_sub(update, context):
            await q.answer("Subscription checked.")
            await q.message.reply_text("🎉 Thanks for joining. Main menu:", reply_markup=main_menu(q.from_user.id))
        else:
            await q.answer("You have not joined all required channels yet.", show_alert=True)
        return
    if data == "nothing":
        await q.answer(); return
    if data == "back_main":
        await q.answer(); clear_state(context)
        await q.message.reply_text("🏠 MAIN MENU", reply_markup=main_menu(q.from_user.id)); return
    if data == "back_admin":
        await q.answer()
        if not is_admin(q.from_user.id):
            await q.message.reply_text("Admin only."); return
        clear_state(context)
        await q.message.reply_text("🛠 ADMIN CONTROL", reply_markup=admin_keyboard()); return
    if data == "back_tasks":
        await q.answer()
        tasks = db.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
        keyboard = [[InlineKeyboardButton(f"{t['name']} ({money(t['amount'])})", callback_data=f"task:{t['id']}")] for t in tasks]
        keyboard.append([InlineKeyboardButton("⬅️ BACK", callback_data="back_main")])
        await q.message.reply_text("📋 AVAILABLE TASKS", reply_markup=InlineKeyboardMarkup(keyboard)); return
    if data == "setupi":
        await set_upi(update, context); return
    if data.startswith("task:"):
        await task_details(update, context); return
    if data.startswith("upload:"):
        await begin_task_submission(update, context, "SCREENSHOT"); return
    if data.startswith("refer:"):
        await begin_task_submission(update, context, "REFER_LINK_OR_NUMBER"); return
    if data.startswith(("taskapprove:", "taskreject:")):
        await process_task_request(update, context); return
    if data.startswith(("approve:", "reject:")):
        await process_withdrawal(update, context); return
    if data.startswith(("admin_", "edit_task:", "edit_gift:", "taskeditname:", "taskeditamount:", "taskeditlink:", "giftname:", "giftamount:", "giftlimit:", "gifttoggle:", "tasktoggle:", "giftdel:", "taskdel:")):
        await admin_callback(update, context); return
    await q.answer()


async def text_router(update, context):
    save_user(update.effective_user)
    text = update.message.text.strip()
    if text == "📋 BOT TASK":
        await show_tasks(update, context); return
    if text == "💸 MY BALANCE":
        await my_balance(update, context); return
    if text == "🎁 GIFT CODE":
        await gift_menu(update, context); return
    if text == "⚡ QUICK PAY":
        await quick_pay(update, context); return
    if text == "💳 PAYOUT METHOD":
        await payout_method(update, context); return
    if text == "🚀 WITHDRAW":
        await withdraw_menu(update, context); return
    if text == "🛠 ADMIN CONTROL":
        await admin_control(update, context); return
    if context.user_data.get("admin_state"):
        if await admin_text(update, context): return
    state = context.user_data.get("state")
    if state == "gift":
        await process_gift(update, context); return
    if state == "upi":
        await save_upi(update, context); return
    if state == "send_user":
        await quick_pay_user(update, context); return
    if state == "send_amount":
        await quick_pay_amount(update, context); return
    if state == "withdraw":
        await create_withdrawal(update, context); return
    if state == "task_refer":
        await submit_task(update, context, "REFER_LINK_OR_NUMBER", submission=text); return
    await update.message.reply_text("Please select an option:", reply_markup=main_menu(update.effective_user.id))


async def error_handler(update, context):
    log.error("BOT ERROR: %s", context.error, exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is missing. Set it in your environment before running the bot."
        )
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("admin", admin_control))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)
    print("ERAN TASK BOT STARTED")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
