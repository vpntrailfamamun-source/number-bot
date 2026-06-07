import asyncio
import sqlite3
import re
import time as _time
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, KeyboardButton, InlineKeyboardButton, Document, CopyTextButton
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
import phonenumbers
import pycountry

TOKEN = "8584889523:AAHbfS-PNhmXsGowMHvmEunA3l0O1cZx1Ns"
ADMIN = 6113809829
OTP_GROUP_ID = -1003641960408

bot = Bot(token=TOKEN)
dp = Dispatcher()
admin_state = {}

# ================== helper ==================
def svc_btn(label, callback, style="primary"):
    return InlineKeyboardButton(text=label, callback_data=callback, style=style)

def get_country_info(number):
    try:
        parsed = phonenumbers.parse(number, None)
        region = phonenumbers.region_code_for_number(parsed)
        if not region:
            return "🌍", "Unknown", "XX"
        country = pycountry.countries.get(alpha_2=region)
        country_name = country.name if country else "Unknown"
        flag = "".join(chr(127397 + ord(c)) for c in region)
        return flag, country_name, region
    except:
        return "🌍", "Unknown", "XX"

def match_number(group_text, real):
    real_digits = re.sub(r'\D', '', real)
    if len(real_digits) < 6:
        return False

    # সরাসরি real নাম্বার group text-এ আছে কিনা দেখো
    if real_digits in re.sub(r'\D', '', group_text):
        return True

    tokens = re.split(r'\s+', group_text)
    for token in tokens:
        token_clean = token.strip('+')
        token_digits = re.sub(r'\D', '', token_clean)

        # masked নাম্বার: যেমন +9967MAMUN8598
        # prefix (শুরুর digit) এবং suffix (শেষের digit) আলাদা করো
        prefix_match = re.match(r'^(\d+)', token_clean)
        suffix_match = re.search(r'(\d+)$', token_clean)
        prefix = prefix_match.group(1) if prefix_match else ""
        suffix = suffix_match.group(1) if suffix_match else ""

        if prefix and suffix and len(prefix) >= 3 and len(suffix) >= 3:
            if real_digits.startswith(prefix) and real_digits.endswith(suffix):
                return True

        if not token_digits or len(token_digits) < 4:
            continue
        for start_len in range(2, min(6, len(token_digits), len(real_digits)) + 1):
            for end_len in range(2, min(6, len(token_digits), len(real_digits)) + 1):
                if (real_digits[:start_len] == token_digits[:start_len] and
                        real_digits[-end_len:] == token_digits[-end_len:]):
                    return True
    return False

async def send_otp_to_user(user_id, number, service, flag, iso, otp):
    msg = f"`{flag}{service} \u2022 {iso}`\n`{number}`"
    kb  = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"\U0001f511 OTP: {otp}", copy_text=CopyTextButton(text=otp), style="success"))
    await bot.send_message(user_id, msg, reply_markup=kb.as_markup(), parse_mode="Markdown")

# ================== DB ==================
def db(q, p=()):
    conn = sqlite3.connect("bot.db", timeout=30, check_same_thread=False)
    c = conn.cursor()
    c.execute(q, p)
    if q.strip().upper().startswith("SELECT"):
        r = c.fetchall()
    else:
        conn.commit()
        r = []
    conn.close()
    return r

db('''CREATE TABLE IF NOT EXISTS numbers (id INTEGER PRIMARY KEY, service TEXT, number TEXT UNIQUE, country TEXT, country_iso TEXT, flag TEXT, status TEXT)''')
db('''CREATE TABLE IF NOT EXISTS settings (service TEXT PRIMARY KEY, show_count INTEGER)''')
db('''CREATE TABLE IF NOT EXISTS required_groups (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, name TEXT, link TEXT)''')
db('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
db('''CREATE TABLE IF NOT EXISTS pending_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    masked_number TEXT,
    created_at INTEGER
)''')
db('''CREATE TABLE IF NOT EXISTS otp_waiters (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, number TEXT, service TEXT, flag TEXT, country_iso TEXT, created_at INTEGER)''')

for s in ['WhatsApp', 'Facebook', 'Instagram', 'Tiktok', 'Telegram']:
    db("INSERT OR IGNORE INTO settings (service, show_count) VALUES (?, 1)", (s,))

# ================== মেম্বারশিপ ==================
async def check_membership(user_id):
    groups = db("SELECT group_id FROM required_groups")
    if not groups:
        return True
    for group in groups:
        try:
            member = await bot.get_chat_member(int(group[0]), user_id)
            if member.status in ["left", "kicked"]:
                return False
        except:
            return False
    return True

async def send_join_message(m):
    groups = db("SELECT name, link FROM required_groups")
    kb = InlineKeyboardBuilder()
    for name, link in groups:
        kb.row(InlineKeyboardButton(text=f"➡️ {name}", url=link))
    kb.row(InlineKeyboardButton(text="✅ আমি জয়েন করেছি", callback_data="verify_join", style="success"))
    await m.answer("🚫 *বটটি ব্যবহার করতে নিচের গ্রুপ/চ্যানেলে জয়েন করুন:*", reply_markup=kb.as_markup(), parse_mode="Markdown")

# ================== মেইন মেনু ==================
def main_menu(uid):
    kb = ReplyKeyboardBuilder()
    kb.row(
        KeyboardButton(text="📞 Get Number", style="success"),
        KeyboardButton(text="📊 Status", style="primary")
    )
    if uid == ADMIN:
        kb.row(KeyboardButton(text="👑 Admin Panel", style="danger"))
    return kb.as_markup(resize_keyboard=True)

# ================== OTP GROUP HANDLER (সবার আগে) ==================
@dp.message(lambda m: m.chat.id == OTP_GROUP_ID)
async def otp_group_handler(m: Message):
    raw_text = (m.text or m.caption or "").strip()
    if not raw_text:
        return

    cutoff = int(_time.time()) - 1800

    # inline keyboard button থেকে 💎 খোঁজো
    otp = None
    if m.reply_markup and hasattr(m.reply_markup, "inline_keyboard"):
        for row in m.reply_markup.inline_keyboard:
            for btn in row:
                btn_match = re.search(r'💎\s*(\d+)', btn.text or "")
                if btn_match:
                    otp = btn_match.group(1)
                    break
            if otp:
                break

    # text এও 💎 থাকতে পারে (fallback)
    if not otp:
        diamond_match = re.search(r'💎\s*(\d+)', raw_text)
        if diamond_match:
            otp = diamond_match.group(1)

    if not otp:
        return

    waiters = db(
        "SELECT id, user_id, number, service, flag, country_iso FROM otp_waiters WHERE created_at > ?",
        (cutoff,)
    )
    if not waiters:
        return

    # same message এ নাম্বার আছে কিনা দেখো (masked যেমন +9617MAMUN8377)
    for w_id, user_id, number, service, flag, iso in waiters:
        if match_number(raw_text, number):
            try:
                await send_otp_to_user(user_id, number, service, flag, iso, otp)
                db("DELETE FROM otp_waiters WHERE id=?", (w_id,))
            except Exception:
                pass
            return

    # Match না হলে সবচেয়ে recent waiter কে দাও
    w_id, user_id, number, service, flag, iso = waiters[-1]
    try:
        await send_otp_to_user(user_id, number, service, flag, iso, otp)
        db("DELETE FROM otp_waiters WHERE id=?", (w_id,))
    except Exception:
        pass

# ================== ভেরিফাই ==================
@dp.callback_query(F.data == "verify_join")
async def verify_join(c: CallbackQuery):
    user_id = c.from_user.id
    if await check_membership(user_id):
        db("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await c.message.delete()
        await c.message.answer("✅ *ভেরিফিকেশন সফল! স্বাগতম!*", reply_markup=main_menu(user_id), parse_mode="Markdown")
    else:
        await c.answer("❌ আপনি এখনো জয়েন করেননি!", show_alert=True)

# ================== স্টার্ট ==================
@dp.message(CommandStart())
async def start(m: Message):
    user_id = m.from_user.id
    db("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    if not await check_membership(user_id):
        await send_join_message(m)
        return
    await m.answer("✅ *স্বাগতম!*", reply_markup=main_menu(user_id), parse_mode="Markdown")

# ================== TEST COMMANDS ==================
@dp.message(Command("debug_msg"))
async def debug_msg_cmd(m: Message):
    """OTP গ্রুপে forward করা message এর structure দেখো"""
    if m.from_user.id != ADMIN:
        return
    if not m.reply_to_message:
        await m.answer("❌ কোনো message reply করে এই command দাও!")
        return
    r = m.reply_to_message
    info = []
    info.append(f"📝 text: `{repr(r.text)}`")
    info.append(f"📝 caption: `{repr(r.caption)}`")
    info.append(f"↩️ forward_origin: `{repr(r.forward_origin)}`")
    if r.forward_origin:
        o = r.forward_origin
        info.append(f"  type: `{type(o).__name__}`")
        if hasattr(o, 'chat') and o.chat:
            info.append(f"  chat.title: `{o.chat.title}`")
            info.append(f"  chat.id: `{o.chat.id}`")
        if hasattr(o, 'sender_user') and o.sender_user:
            info.append(f"  sender: `{o.sender_user.username}`")
    info.append(f"💬 entities: `{repr(r.entities)}`")
    await m.answer("\n".join(info), parse_mode="Markdown")

@dp.message(Command("test_otp"))
async def test_otp_cmd(m: Message):
    if m.from_user.id != ADMIN:
        return
    cutoff  = int(_time.time()) - 1800
    waiters = db(
        "SELECT id, user_id, number, service, flag, country_iso, created_at FROM otp_waiters WHERE created_at > ?",
        (cutoff,)
    )
    if not waiters:
        await m.answer("❌ কোনো active waiter নেই!\n\nআগে ইউজারকে একটা নাম্বার নিতে বলো।")
        return
    text = f"✅ Active waiters: {len(waiters)}\n\n"
    for w in waiters:
        text += f"👤 User: `{w[1]}`\n📞 Number: `{w[2]}`\n📱 Service: {w[3]}\n\n"
    await m.answer(text, parse_mode="Markdown")

@dp.message(Command("force_otp"))
async def force_otp_cmd(m: Message):
    if m.from_user.id != ADMIN:
        return
    parts = m.text.split()
    if len(parts) != 3:
        await m.answer("Usage: /force_otp +8801712345678 123456\nMasked: /force_otp +8801XXXXX1234 123456")
        return
    number = parts[1]
    otp    = parts[2]
    cutoff = int(_time.time()) - 1800
    waiters = db(
        "SELECT id, user_id, number, service, flag, country_iso FROM otp_waiters WHERE created_at > ?",
        (cutoff,)
    )
    found = False
    for w_id, user_id, db_number, service, flag, iso in waiters:
        if db_number == number or match_number(number, db_number) or match_number(db_number, number):
            found = True
            await send_otp_to_user(user_id, db_number, service, flag, iso, otp)
            db("DELETE FROM otp_waiters WHERE id=?", (w_id,))
            await m.answer(f"✅ OTP `{otp}` sent to user `{user_id}`", parse_mode="Markdown")
            break
    if not found:
        waiter_nums = [w[2] for w in waiters]
        await m.answer(f"❌ match হয়নি!\nDid: `{number}`\nWaiters: {waiter_nums}", parse_mode="Markdown")

# ================== STATUS ==================
@dp.message(F.text == "📊 Status")
async def status(m: Message):
    if not await check_membership(m.from_user.id):
        await send_join_message(m)
        return
    total = db("SELECT COUNT(*) FROM numbers")[0][0]
    avail = db("SELECT COUNT(*) FROM numbers WHERE status='available'")[0][0]
    used  = db("SELECT COUNT(*) FROM numbers WHERE status='used'")[0][0]
    users = db("SELECT COUNT(*) FROM users")[0][0]
    await m.answer(
        f"📊 *Bot Status*\n\n👥 Total Users: `{users}`\n📞 Total Numbers: `{total}`\n✅ Available: `{avail}`\n❌ Used: `{used}`",
        parse_mode="Markdown"
    )

# ================== GET NUMBER ==================
@dp.message(F.text == "📞 Get Number")
async def get_num(m: Message):
    if not await check_membership(m.from_user.id):
        await send_join_message(m)
        return
    svcs = list(set([r[0] for r in db("SELECT service FROM numbers WHERE status='available'")]))
    if not svcs:
        await m.answer("❌ এখন কোনো নাম্বার নেই!")
        return
    kb = InlineKeyboardBuilder()
    for s in svcs:
        kb.row(svc_btn(s, f"svc_{s}", style="primary"))
    await m.answer("🔍 *সার্ভিস সিলেক্ট করুন*", reply_markup=kb.as_markup(), parse_mode="Markdown")

# ================== সার্ভিস → কান্ট্রি ==================
@dp.callback_query(F.data.startswith("svc_"))
async def svc_sel(c: CallbackQuery):
    svc = c.data[4:]
    ctrys = db("SELECT DISTINCT country, flag FROM numbers WHERE service=? AND status='available'", (svc,))
    if not ctrys:
        await c.answer("এই সার্ভিসে নাম্বার নেই!", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for ct, fl in ctrys:
        cnt = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='available'", (svc, ct))[0][0]
        kb.row(InlineKeyboardButton(text=f"{fl} {ct} ({cnt})", callback_data=f"ctry_{svc}_{ct}", style="success"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="back_to_main", style="primary"))
    await c.message.edit_text(f"📱 *{svc}*\n🌍 কান্ট্রি সিলেক্ট করুন", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

# ================== কান্ট্রি → নাম্বার ==================
@dp.callback_query(F.data.startswith("ctry_"))
async def ctry_sel(c: CallbackQuery):
    parts = c.data.split("_", 2)
    svc = parts[1]
    ct  = parts[2]
    show = db("SELECT show_count FROM settings WHERE service=?", (svc,))
    show_count = show[0][0] if show else 1
    nums = db(
        "SELECT id, number FROM numbers WHERE service=? AND country=? AND status='available' ORDER BY RANDOM() LIMIT ?",
        (svc, ct, show_count)
    )
    fl_data = db("SELECT flag FROM numbers WHERE service=? AND country=? LIMIT 1", (svc, ct))
    fl = fl_data[0][0] if fl_data else "🌍"
    if not nums:
        await c.answer("এই কান্ট্রিতে নাম্বার নেই!", show_alert=True)
        return
    iso_data = db("SELECT country_iso FROM numbers WHERE service=? AND country=? LIMIT 1", (svc, ct))
    iso = iso_data[0][0] if iso_data else "XX"
    for row_id, num in nums:
        db("UPDATE numbers SET status='used' WHERE id=?", (row_id,))
        db("INSERT INTO otp_waiters (user_id, number, service, flag, country_iso, created_at) VALUES (?,?,?,?,?,?)",
           (c.from_user.id, num, svc, fl, iso, int(_time.time())))
    kb = InlineKeyboardBuilder()
    for row_id, num in nums:
        kb.row(InlineKeyboardButton(text=f"📞 {num}", copy_text=CopyTextButton(text=num), style="success"))
    kb.row(InlineKeyboardButton(text="🔄 Change Number",  callback_data=f"chg_{svc}_{ct}", style="primary"))
    kb.row(InlineKeyboardButton(text="🌍 Change Country", callback_data=f"chc_{svc}",      style="primary"))
    kb.row(InlineKeyboardButton(text="🌐 OTP Group",      url="https://t.me/famamunotpgroup", style="primary"))
    await c.message.edit_text(
        f"📱 *{svc}* | {fl} *{ct}*\n\n⏳ OTP এর জন্য অপেক্ষা করুন...",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )
    await c.answer()

# ================== চেঞ্জ নাম্বার ==================
@dp.callback_query(F.data.startswith("chg_"))
async def chg_num(c: CallbackQuery):
    parts = c.data.split("_", 2)
    svc = parts[1]
    ct  = parts[2]
    show = db("SELECT show_count FROM settings WHERE service=?", (svc,))
    show_count = show[0][0] if show else 1
    nums = db(
        "SELECT id, number FROM numbers WHERE service=? AND country=? AND status='available' ORDER BY RANDOM() LIMIT ?",
        (svc, ct, show_count)
    )
    fl_data = db("SELECT flag FROM numbers WHERE service=? AND country=? LIMIT 1", (svc, ct))
    fl = fl_data[0][0] if fl_data else "🌍"
    if not nums:
        await c.answer("আর নাম্বার নেই!", show_alert=True)
        return
    iso_data = db("SELECT country_iso FROM numbers WHERE service=? AND country=? LIMIT 1", (svc, ct))
    iso = iso_data[0][0] if iso_data else "XX"
    for row_id, num in nums:
        db("UPDATE numbers SET status='used' WHERE id=?", (row_id,))
        db("INSERT INTO otp_waiters (user_id, number, service, flag, country_iso, created_at) VALUES (?,?,?,?,?,?)",
           (c.from_user.id, num, svc, fl, iso, int(_time.time())))
    kb = InlineKeyboardBuilder()
    for row_id, num in nums:
        kb.row(InlineKeyboardButton(text=f"📞 {num}", copy_text=CopyTextButton(text=num), style="success"))
    kb.row(InlineKeyboardButton(text="🔄 Change Number",  callback_data=f"chg_{svc}_{ct}", style="primary"))
    kb.row(InlineKeyboardButton(text="🌍 Change Country", callback_data=f"chc_{svc}",      style="primary"))
    kb.row(InlineKeyboardButton(text="🔙 Back",           callback_data=f"back_svc_{svc}", style="primary"))
    await c.message.edit_text(
        f"📱 *{svc}* | {fl} *{ct}*\n\n⏳ OTP এর জন্য অপেক্ষা করুন...",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )
    await c.answer("🔄 নতুন নাম্বার দেওয়া হয়েছে!")

# ================== চেঞ্জ কান্ট্রি ==================
@dp.callback_query(F.data.startswith("chc_"))
async def chc_ctry(c: CallbackQuery):
    svc = c.data[4:]
    ctrys = db("SELECT DISTINCT country, flag FROM numbers WHERE service=? AND status='available'", (svc,))
    if not ctrys:
        await c.answer("নাম্বার নেই!", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for ct, fl in ctrys:
        cnt = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='available'", (svc, ct))[0][0]
        kb.row(InlineKeyboardButton(text=f"{fl} {ct} ({cnt})", callback_data=f"ctry_{svc}_{ct}", style="success"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="back_to_main", style="primary"))
    await c.message.edit_text(f"📱 *{svc}*\n🌍 কান্ট্রি সিলেক্ট করুন", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

# ================== ব্যাক বাটন ==================
@dp.callback_query(F.data == "back_to_main")
async def back_main(c: CallbackQuery):
    svcs = list(set([r[0] for r in db("SELECT service FROM numbers WHERE status='available'")]))
    if not svcs:
        await c.message.edit_text("❌ এখন কোনো নাম্বার নেই!")
        await c.answer()
        return
    kb = InlineKeyboardBuilder()
    for s in svcs:
        kb.row(svc_btn(s, f"svc_{s}", style="primary"))
    await c.message.edit_text("🔍 *সার্ভিস সিলেক্ট করুন*", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data.startswith("back_svc_"))
async def back_svc(c: CallbackQuery):
    svc = c.data[9:]
    ctrys = db("SELECT DISTINCT country, flag FROM numbers WHERE service=? AND status='available'", (svc,))
    kb = InlineKeyboardBuilder()
    for ct, fl in ctrys:
        cnt = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='available'", (svc, ct))[0][0]
        kb.row(InlineKeyboardButton(text=f"{fl} {ct} ({cnt})", callback_data=f"ctry_{svc}_{ct}", style="success"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="back_to_main", style="primary"))
    await c.message.edit_text(f"📱 *{svc}*\n🌍 কান্ট্রি সিলেক্ট করুন", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data == "noop")
async def noop(c: CallbackQuery):
    await c.answer()

# ================== ADMIN PANEL ==================
@dp.message(F.text == "👑 Admin Panel")
async def admin_panel(m: Message):
    if m.from_user.id != ADMIN:
        return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Add Numbers",       callback_data="adm_add",     style="success"))
    kb.row(InlineKeyboardButton(text="🗑️ Remove Numbers",   callback_data="adm_rem",     style="danger"))
    kb.row(InlineKeyboardButton(text="🔢 Set Show Count",   callback_data="adm_cnt",     style="primary"))
    kb.row(InlineKeyboardButton(text="📢 Required Channel", callback_data="adm_channel", style="primary"))
    kb.row(InlineKeyboardButton(text="📣 Broadcast",        callback_data="adm_bc",      style="primary"))
    total = db("SELECT COUNT(*) FROM numbers WHERE status='available'")[0][0]
    users = db("SELECT COUNT(*) FROM users")[0][0]
    await m.answer(
        f"🛠️ *Admin Panel*\n\n👥 Users: `{users}`\n✅ Available Numbers: `{total}`",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )

@dp.callback_query(F.data == "adm_back")
async def adm_back(c: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Add Numbers",       callback_data="adm_add",     style="success"))
    kb.row(InlineKeyboardButton(text="🗑️ Remove Numbers",   callback_data="adm_rem",     style="danger"))
    kb.row(InlineKeyboardButton(text="🔢 Set Show Count",   callback_data="adm_cnt",     style="primary"))
    kb.row(InlineKeyboardButton(text="📢 Required Channel", callback_data="adm_channel", style="primary"))
    kb.row(InlineKeyboardButton(text="📣 Broadcast",        callback_data="adm_bc",      style="primary"))
    total = db("SELECT COUNT(*) FROM numbers WHERE status='available'")[0][0]
    users = db("SELECT COUNT(*) FROM users")[0][0]
    await c.message.edit_text(
        f"🛠️ *Admin Panel*\n\n👥 Users: `{users}`\n✅ Available Numbers: `{total}`",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )
    await c.answer()

# ================== ADMIN ADD ==================
@dp.callback_query(F.data == "adm_add")
async def adm_add(c: CallbackQuery):
    admin_state[c.from_user.id] = {"step": "nums"}
    await c.message.answer(
        "📞 নাম্বার পাঠান।\n\n✅ *২ ভাবে পাঠাতে পারবেন:*\n১. সরাসরি টাইপ করে (প্রতি লাইনে একটা)\n২. `.txt` ফাইল আপলোড করে",
        parse_mode="Markdown"
    )
    await c.answer()

@dp.message(F.document)
async def handle_document(m: Message):
    if m.from_user.id != ADMIN:
        return
    if m.from_user.id not in admin_state:
        return
    if admin_state[m.from_user.id].get("step") != "nums":
        return
    doc = m.document
    if not doc.file_name.endswith(".txt"):
        await m.answer("❌ শুধু .txt ফাইল সাপোর্ট করে!")
        return
    file = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file.file_path)
    content = file_bytes.read().decode("utf-8", errors="ignore")
    nums = []
    for line in content.splitlines():
        clean = re.sub(r'\D', '', line.strip())
        if len(clean) >= 8:
            nums.append('+' + clean)
    if nums:
        admin_state[m.from_user.id]["nums"] = nums
        admin_state[m.from_user.id]["step"] = "svc"
        await m.answer(f"✅ ফাইল থেকে *{len(nums)}* টি নাম্বার পাওয়া গেছে।\n\nসার্ভিসের নাম পাঠান:", parse_mode="Markdown")
    else:
        await m.answer("❌ ফাইলে কোনো ভ্যালিড নাম্বার নেই!")

# ================== ADMIN REMOVE ==================
@dp.callback_query(F.data == "adm_rem")
async def adm_rem(c: CallbackQuery):
    svcs = list(set([r[0] for r in db("SELECT service FROM numbers")]))
    if not svcs:
        await c.answer("কোনো সার্ভিস নেই!", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for s in svcs:
        total = db("SELECT COUNT(*) FROM numbers WHERE service=?", (s,))[0][0]
        kb.row(InlineKeyboardButton(text=f"📱 {s} ({total})", callback_data=f"rem_svc_{s}", style="danger"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="adm_back", style="primary"))
    await c.message.edit_text("🗑️ *কোন সার্ভিসের নাম্বার রিমুভ করবেন?*", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data.startswith("rem_svc_"))
async def rem_svc_sel(c: CallbackQuery):
    svc = c.data[8:]
    ctrys = db("SELECT DISTINCT country, flag FROM numbers WHERE service=?", (svc,))
    if not ctrys:
        await c.answer("এই সার্ভিসে নাম্বার নেই!", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for ct, fl in ctrys:
        total = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=?", (svc, ct))[0][0]
        kb.row(InlineKeyboardButton(text=f"{fl} {ct} ({total})", callback_data=f"rem_ct_{svc}_{ct}", style="danger"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="adm_rem", style="primary"))
    await c.message.edit_text(f"📱 *{svc}*\n🗑️ কোন কান্ট্রির নাম্বার রিমুভ করবেন?", reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data.startswith("rem_ct_"))
async def rem_ct_sel(c: CallbackQuery):
    parts = c.data.split("_", 3)
    svc = parts[2]
    ct  = parts[3]
    total_all   = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=?", (svc, ct))[0][0]
    total_used  = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='used'", (svc, ct))[0][0]
    total_avail = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='available'", (svc, ct))[0][0]
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"🗑️ সব নাম্বার রিমুভ ({total_all})",   callback_data=f"rem_confirm_{svc}_{ct}_all",  style="danger"))
    kb.row(InlineKeyboardButton(text=f"♻️ শুধু Used রিমুভ ({total_used})",    callback_data=f"rem_confirm_{svc}_{ct}_used", style="primary"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data=f"rem_svc_{svc}", style="primary"))
    await c.message.edit_text(
        f"🗑️ *{svc}* | *{ct}*\n\n📊 মোট: `{total_all}` | ✅ Available: `{total_avail}` | ❌ Used: `{total_used}`\n\nকোনটি রিমুভ করবেন?",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )
    await c.answer()

@dp.callback_query(F.data.startswith("rem_confirm_"))
async def rem_confirm(c: CallbackQuery):
    parts    = c.data.split("_", 4)
    svc      = parts[2]
    ct       = parts[3]
    rem_type = parts[4]
    if rem_type == "all":
        count = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=?", (svc, ct))[0][0]
        text  = f"⚠️ *{svc}* | *{ct}* এর সব *{count}টি* নাম্বার ডিলেট হবে!\n\nনিশ্চিত?"
    else:
        count = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='used'", (svc, ct))[0][0]
        text  = f"⚠️ *{svc}* | *{ct}* এর *{count}টি Used* নাম্বার ডিলেট হবে!\n\nনিশ্চিত?"
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Yes", callback_data=f"rem_do_{svc}_{ct}_{rem_type}", style="success"),
        InlineKeyboardButton(text="❌ No",  callback_data=f"rem_ct_{svc}_{ct}",            style="danger")
    )
    await c.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    await c.answer()

@dp.callback_query(F.data.startswith("rem_do_"))
async def rem_do(c: CallbackQuery):
    parts    = c.data.split("_", 4)
    svc      = parts[2]
    ct       = parts[3]
    rem_type = parts[4]
    if rem_type == "all":
        count = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=?", (svc, ct))[0][0]
        db("DELETE FROM numbers WHERE service=? AND country=?", (svc, ct))
        await c.answer(f"✅ {count}টি নাম্বার ডিলেট হয়েছে!", show_alert=True)
    else:
        count = db("SELECT COUNT(*) FROM numbers WHERE service=? AND country=? AND status='used'", (svc, ct))[0][0]
        db("DELETE FROM numbers WHERE service=? AND country=? AND status='used'", (svc, ct))
        await c.answer(f"✅ {count}টি Used নাম্বার ডিলেট হয়েছে!", show_alert=True)
    svcs = list(set([r[0] for r in db("SELECT service FROM numbers")]))
    kb   = InlineKeyboardBuilder()
    for s in svcs:
        total = db("SELECT COUNT(*) FROM numbers WHERE service=?", (s,))[0][0]
        kb.row(InlineKeyboardButton(text=f"📱 {s} ({total})", callback_data=f"rem_svc_{s}", style="danger"))
    kb.row(InlineKeyboardButton(text="🔙 Admin", callback_data="adm_back", style="primary"))
    await c.message.edit_text("🗑️ *কোন সার্ভিসের নাম্বার রিমুভ করবেন?*", reply_markup=kb.as_markup(), parse_mode="Markdown")

# ================== ADMIN SET COUNT ==================
@dp.callback_query(F.data == "adm_cnt")
async def adm_cnt(c: CallbackQuery):
    svcs = list(set([r[0] for r in db("SELECT service FROM numbers")]))
    if not svcs:
        await c.answer("কোনো সার্ভিস নেই!", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for s in svcs:
        cnt   = db("SELECT show_count FROM settings WHERE service=?", (s,))
        count = cnt[0][0] if cnt else 1
        kb.row(InlineKeyboardButton(text=f"{s} (current: {count})", callback_data=f"setcnt_{s}", style="primary"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="adm_back", style="primary"))
    await c.message.edit_text("🔢 কোন সার্ভিসের শো কাউন্ট সেট করবেন?", reply_markup=kb.as_markup())
    await c.answer()

@dp.callback_query(F.data.startswith("setcnt_"))
async def setcnt_sel(c: CallbackQuery):
    svc = c.data[7:]
    admin_state[c.from_user.id] = {"step": "set_count", "service": svc}
    await c.message.answer(f"🔢 *{svc}* এর জন্য নতুন শো কাউন্ট পাঠান:", parse_mode="Markdown")
    await c.answer()

# ================== ADMIN CHANNEL ==================
@dp.callback_query(F.data == "adm_channel")
async def adm_channel(c: CallbackQuery):
    groups = db("SELECT id, name, group_id FROM required_groups")
    kb     = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Add Channel", callback_data="adm_add_ch", style="success"))
    for g_id, name, group_id in groups:
        kb.row(InlineKeyboardButton(text=f"🗑️ Remove: {name}", callback_data=f"adm_del_ch_{g_id}", style="danger"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="adm_back", style="primary"))
    await c.message.edit_text(
        f"📢 *Required Channels* ({len(groups)} total)\n\nইউজারদের এগুলোতে জয়েন করতে হবে।",
        reply_markup=kb.as_markup(), parse_mode="Markdown"
    )
    await c.answer()

@dp.callback_query(F.data == "adm_add_ch")
async def adm_add_ch(c: CallbackQuery):
    admin_state[c.from_user.id] = {"step": "add_channel"}
    await c.message.answer(
        "📢 এই ফরম্যাটে পাঠান:\n\n`channel_id | Channel Name | https://t.me/link`",
        parse_mode="Markdown"
    )
    await c.answer()

@dp.callback_query(F.data.startswith("adm_del_ch_"))
async def adm_del_ch(c: CallbackQuery):
    g_id = int(c.data.split("_")[-1])
    db("DELETE FROM required_groups WHERE id=?", (g_id,))
    await c.answer("✅ চ্যানেল রিমুভ হয়েছে!", show_alert=True)
    groups = db("SELECT id, name, group_id FROM required_groups")
    kb     = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="➕ Add Channel", callback_data="adm_add_ch", style="success"))
    for g_id2, name, group_id in groups:
        kb.row(InlineKeyboardButton(text=f"🗑️ Remove: {name}", callback_data=f"adm_del_ch_{g_id2}", style="danger"))
    kb.row(InlineKeyboardButton(text="🔙 Back", callback_data="adm_back", style="primary"))
    await c.message.edit_text(f"📢 *Required Channels* ({len(groups)} total)", reply_markup=kb.as_markup(), parse_mode="Markdown")

# ================== BROADCAST ==================
@dp.callback_query(F.data == "bc_yes")
async def bc_yes(c: CallbackQuery):
    if c.from_user.id not in admin_state:
        await c.answer()
        return
    state = admin_state[c.from_user.id]
    svc   = state.get("service", "")
    added = state.get("added", 0)
    fl    = state.get("svc_flag", "📱")
    users = db("SELECT user_id FROM users")
    sent  = 0
    failed = 0
    msg = f"🆕 *New Stock Added*\n\n{fl} *{svc}*\n\n📦 *TOTAL:* `{added}` Numbers"
    await c.message.edit_text(f"📣 {len(users)} জনকে broadcast করা হচ্ছে...")
    for (uid,) in users:
        try:
            await bot.send_message(uid, msg, parse_mode="Markdown")
            sent += 1
        except:
            failed += 1
    await c.message.answer(f"✅ Broadcast শেষ!\n✅ Sent: `{sent}`\n❌ Failed: `{failed}`", parse_mode="Markdown")
    admin_state.pop(c.from_user.id, None)
    await c.answer()

@dp.callback_query(F.data == "bc_skip")
async def bc_skip(c: CallbackQuery):
    admin_state.pop(c.from_user.id, None)
    await c.message.edit_text("⏭️ Broadcast skip করা হয়েছে।")
    await c.answer()

@dp.callback_query(F.data == "adm_bc")
async def adm_bc(c: CallbackQuery):
    admin_state[c.from_user.id] = {"step": "broadcast"}
    await c.message.answer("📣 ব্রডকাস্ট মেসেজ পাঠান:")
    await c.answer()

# ================== টেক্সট হ্যান্ডলার ==================
@dp.message(F.text & ~F.text.startswith("/") & (F.chat.type.in_({"private"})))
async def handle_text(m: Message):
    if m.from_user.id != ADMIN and m.from_user.id not in admin_state:
        return
    if m.from_user.id not in admin_state:
        return
    state = admin_state[m.from_user.id]
    step  = state.get("step")

    if step == "nums":
        nums = []
        for line in m.text.splitlines():
            clean = re.sub(r'\D', '', line.strip())
            if len(clean) >= 8:
                nums.append('+' + clean)
        if nums:
            admin_state[m.from_user.id]["nums"] = nums
            admin_state[m.from_user.id]["step"] = "svc"
            await m.answer(f"✅ *{len(nums)}* টি নাম্বার পাওয়া গেছে।\n\nসার্ভিসের নাম পাঠান:", parse_mode="Markdown")
        else:
            await m.answer("❌ ভ্যালিড নাম্বার পাওয়া যায়নি!")

    elif step == "svc":
        svc = m.text.strip()[:60]
        admin_state[m.from_user.id]["service"] = svc
        admin_state[m.from_user.id]["step"]    = "confirm"
        nums_count = len(admin_state[m.from_user.id]["nums"])
        await m.answer(
            f"✅ Service: *{svc}*\n📞 Numbers: `{nums_count}`\n\n`yes` পাঠালে এড হবে:",
            parse_mode="Markdown"
        )

    elif step == "confirm":
        if m.text.strip().lower() == "yes":
            svc     = admin_state[m.from_user.id]["service"]
            nums    = admin_state[m.from_user.id]["nums"]
            added   = 0
            skipped = 0
            await m.answer(f"⏳ *{len(nums)}* টি নাম্বার প্রসেস হচ্ছে...", parse_mode="Markdown")
            loop = asyncio.get_event_loop()
            for num in nums:
                flag, country, iso = await loop.run_in_executor(None, get_country_info, num)
                try:
                    db("INSERT INTO numbers (service, number, country, country_iso, flag, status) VALUES (?,?,?,?,?,?)",
                       (svc, num, country, iso, flag, "available"))
                    db("INSERT OR IGNORE INTO settings (service, show_count) VALUES (?, 1)", (svc,))
                    added += 1
                except:
                    skipped += 1
                await asyncio.sleep(0)  # yield to event loop between each number
            svc_flag = db("SELECT DISTINCT flag FROM numbers WHERE service=? LIMIT 1", (svc,))
            svc_fl   = svc_flag[0][0] if svc_flag else "📱"
            admin_state[m.from_user.id]["step"]     = "bc_ask"
            admin_state[m.from_user.id]["added"]    = added
            admin_state[m.from_user.id]["skipped"]  = skipped
            admin_state[m.from_user.id]["svc_flag"] = svc_fl
            kb = InlineKeyboardBuilder()
            kb.row(
                InlineKeyboardButton(text="📣 Yes, Broadcast", callback_data="bc_yes",  style="success"),
                InlineKeyboardButton(text="⏭️ Skip",           callback_data="bc_skip", style="primary")
            )
            await m.answer(
                f"✅ *{svc}* তে *{added}* টি নাম্বার এড হয়েছে\n⏭️ Skipped: `{skipped}`\n\nসবার কাছে broadcast পাঠাবেন?",
                reply_markup=kb.as_markup(), parse_mode="Markdown"
            )
        else:
            await m.answer("❌ বাতিল করা হয়েছে।")
            admin_state.pop(m.from_user.id)

    elif step == "set_count":
        try:
            count = int(m.text.strip())
            svc   = state["service"]
            db("INSERT OR REPLACE INTO settings (service, show_count) VALUES (?, ?)", (svc, count))
            await m.answer(f"✅ *{svc}* এর শো কাউন্ট `{count}` করা হয়েছে", parse_mode="Markdown")
        except:
            await m.answer("❌ ভ্যালিড নম্বর পাঠান!")
        admin_state.pop(m.from_user.id)

    elif step == "broadcast":
        users  = db("SELECT user_id FROM users")
        sent   = 0
        failed = 0
        await m.answer(f"📣 {len(users)} জনকে broadcast করা হচ্ছে...")
        for (uid,) in users:
            try:
                await bot.send_message(uid, m.text)
                sent += 1
            except:
                failed += 1
        await m.answer(f"✅ Broadcast শেষ!\n✅ Sent: `{sent}`\n❌ Failed: `{failed}`", parse_mode="Markdown")
        admin_state.pop(m.from_user.id)

    elif step == "add_channel":
        try:
            parts = [p.strip() for p in m.text.split("|")]
            if len(parts) != 3:
                await m.answer("❌ ভুল ফরম্যাট! ব্যবহার করুন: `channel_id | Name | link`", parse_mode="Markdown")
                return
            group_id, name, link = parts
            db("INSERT INTO required_groups (group_id, name, link) VALUES (?,?,?)", (group_id, name, link))
            await m.answer(f"✅ *{name}* চ্যানেল এড হয়েছে!", parse_mode="Markdown")
        except Exception as e:
            await m.answer(f"❌ Error: {e}")
        admin_state.pop(m.from_user.id)



async def main():
    print("🤖 Bot Started Successfully")
    # Webhook clear করো আগে
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "edited_message", 
            "channel_post",
            "callback_query",
            "chat_member",
            "my_chat_member"
        ]
    )

if __name__ == "__main__":
    asyncio.run(main())
