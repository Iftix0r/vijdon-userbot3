import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import sqlite3
import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

import re
import json
import logging
from contextlib import contextmanager

load_dotenv()

# Logging sozlash
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Admin hisobotlari uchun - oxirgi xatoliklar
recent_errors = []
MAX_RECENT_ERRORS = 10

class ErrorCollectorHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            msg = self.format(record)
            recent_errors.append(msg[:200])
            if len(recent_errors) > MAX_RECENT_ERRORS:
                recent_errors.pop(0)

error_handler = ErrorCollectorHandler()
error_handler.setLevel(logging.ERROR)
logger.addHandler(error_handler)

@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = sqlite3.connect('zakazlar.db', timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.close()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ORDER_GROUP_ID = int(os.getenv('ORDER_GROUP_ID'))
ADMIN_IDS = [int(x.strip()) for x in os.getenv('ADMIN_IDS', '0').split(',')]
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
HAYDOVCHI_ADMIN_PHONE = os.getenv('HAYDOVCHI_ADMIN_PHONE', '')
HAYDOVCHI_ADMIN_USERNAME = os.getenv('HAYDOVCHI_ADMIN_USERNAME', '').strip().lstrip('@')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Ma'lumotlar bazasini ishga tushirish
def init_keywords_db():
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT,
            word TEXT,
            sana DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS order_groups (
            group_id INTEGER PRIMARY KEY,
            group_name TEXT,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reklama_groups (
            group_id TEXT PRIMARY KEY,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id INTEGER PRIMARY KEY,
            blocked_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_name TEXT UNIQUE NOT NULL,
            phone TEXT,
            tg_user_id INTEGER,
            username TEXT,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS incomplete_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number INTEGER,
            user_id INTEGER,
            user_name TEXT,
            original_message TEXT,
            missing_info TEXT,
            group_name TEXT,
            group_id INTEGER,
            message_id INTEGER,
            status TEXT DEFAULT 'pending',
            admin_id INTEGER,
            admin_info TEXT,
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_date DATETIME
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT UNIQUE NOT NULL,
            setting_value TEXT,
            updated_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_orders_group (
            group_id INTEGER PRIMARY KEY,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_orders_group (
            group_id INTEGER PRIMARY KEY,
            added_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Default settings
    cursor.execute('''
        INSERT OR IGNORE INTO settings (setting_key, setting_value)
        VALUES (?, ?)
    ''', ('order_message_header', '🚕 <b>ASSALOMU ALEYKUM HURMATLI TAXI HAYDOVCHILARI 🆕 YANGI BUYURTMA KELDI!</b>'))
    
    # Default adminlarni qo'shish
    for admin_id in ADMIN_IDS:
        cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))

    # Tez qidiruv uchun indekslar (zakazlar/users mavjud bo'lsa)
    try:
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_zakazlar_sana ON zakazlar (sana DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_zakazlar_user_id ON zakazlar (user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_zakazlar_user_type ON zakazlar (user_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_user_name ON users (user_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)')
    except Exception:
        pass

    conn.commit()
    conn.close()

def save_keyword(word_type, word):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', (word_type, word))
            conn.commit()
            logger.info(f"Keyword saved: {word_type} - {word}")
    except Exception as e:
        logger.error(f"Error saving keyword: {e}")
        raise

def get_keywords(word_type):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT word FROM keywords WHERE type = ?', (word_type,))
            words = [row[0] for row in cursor.fetchall()]
            return words
    except Exception as e:
        logger.error(f"Error getting keywords: {e}")
        return []

def delete_keyword(word_type, word):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM keywords WHERE type = ? AND word = ?', (word_type, word))
            conn.commit()
            logger.info(f"Keyword deleted: {word_type} - {word}")
    except Exception as e:
        logger.error(f"Error deleting keyword: {e}")
        raise

# Incomplete orders functions
def save_incomplete_order(order_number, user_id, user_name, original_message, missing_info, group_name, group_id, message_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO incomplete_orders 
                (order_number, user_id, user_name, original_message, missing_info, group_name, group_id, message_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ''', (order_number, user_id, user_name, original_message, missing_info, group_name, group_id, message_id))
            conn.commit()
            logger.info(f"Incomplete order saved: #{order_number} - {user_name}")
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Error saving incomplete order: {e}")
        raise



def complete_incomplete_order(order_id, admin_id, admin_info):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE incomplete_orders 
                SET status = 'completed', admin_id = ?, admin_info = ?, completed_date = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (admin_id, admin_info, order_id))
            conn.commit()
            logger.info(f"Incomplete order completed: #{order_id} by admin {admin_id}")
            return True
    except Exception as e:
        logger.error(f"Error completing incomplete order: {e}")
        return False



def delete_incomplete_order(order_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM incomplete_orders WHERE id = ?', (order_id,))
            conn.commit()
            logger.info(f"Incomplete order deleted: #{order_id}")
            return True
    except Exception as e:
        logger.error(f"Error deleting incomplete order: {e}")
        return False



# Asosiy menu
def main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="🔍 Qidiruv")],
            [KeyboardButton(text="📝 So'zlar qo'shish"), KeyboardButton(text="⚙️ Sozlamalar")],
            [KeyboardButton(text="📋 Guruh statistikasi"), KeyboardButton(text="🕜 Oxirgi 10 ta zakaz")],
            [KeyboardButton(text="⚠️ To'liq bo'lmagan zakazlar"), KeyboardButton(text="✅ Zakazni to'ldirish")]
        ],
        resize_keyboard=True
    )
    return keyboard

# So'z qo'shish menu
def words_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Yo'lovchi so'zi qo'shish", callback_data="add_passenger")],
            [InlineKeyboardButton(text="➖ Yo'lovchi so'zi o'chirish", callback_data="delete_passenger")],
            [InlineKeyboardButton(text="🚗 Haydovchi so'zi qo'shish", callback_data="add_driver")],
            [InlineKeyboardButton(text="❌ Haydovchi so'zi o'chirish", callback_data="delete_driver")],
            [InlineKeyboardButton(text="🗑️ Barcha haydovchi so'zlarini o'chirish", callback_data="delete_all_driver")],
            [InlineKeyboardButton(text="📋 Barcha so'zlar", callback_data="list_words")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]
        ]
    )
    return keyboard

def is_admin(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,))
            return cursor.fetchone() is not None
    except:
        return False

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    args = message.text.split()
    
    # Reklama guruhdan: haydovchi bo'lish uchun admin ma'lumotlari
    if len(args) > 1 and args[1] == 'haydovchi':
        buttons = []
        if HAYDOVCHI_ADMIN_PHONE:
            phone = HAYDOVCHI_ADMIN_PHONE.replace(' ', '').replace('-', '')
            if not phone.startswith('+'):
                phone = '+998' + phone if phone.startswith('998') else '+' + phone
            buttons.append([InlineKeyboardButton(text="📞 Admin bilan bog'lanish", url=f"https://onmap.uz/tel/{phone}")])
        if HAYDOVCHI_ADMIN_USERNAME:
            buttons.append([InlineKeyboardButton(text=f"👤 @{HAYDOVCHI_ADMIN_USERNAME}", url=f"https://t.me/{HAYDOVCHI_ADMIN_USERNAME}")])
        
        if buttons:
            text = (
                "🚗 <b>Haydovchi bo'lish uchun</b>\n\n"
                "Bizning jamoamizga qo'shilmoqchimisiz? "
                "Quyidagi admin bilan bog'laning:"
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        else:
            text = (
                "🚗 <b>Haydovchi bo'lish uchun</b>\n\n"
                "Admin ma'lumotlari hozircha sozlanmagan. "
                "Guruh adminlariga murojaat qiling."
            )
            keyboard = None
        await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
        return
    
    # Tez guruhdan buyurtma guruhiga yuborish (deep link)
    if len(args) > 1 and args[1].startswith('fastsend_'):
        try:
            parts = args[1].replace('fastsend_', '').split('_')
            src_user_id = int(parts[0])

            # Barcha DB lardan foydalanuvchi ma'lumotlarini qidirish
            user_name = "Foydalanuvchi"
            username = None
            phone = None
            order_text = None

            db_files = ['zakazlar.db'] + [
                f for f in os.listdir('.') if f.startswith('zakazlar_account') and f.endswith('.db')
            ]
            for db_file in db_files:
                try:
                    conn = sqlite3.connect(db_file, timeout=10)
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT z.message, u.user_name, u.username, u.phone
                        FROM zakazlar z
                        LEFT JOIN users u ON z.user_id = u.user_id
                        WHERE z.user_id = ?
                        ORDER BY z.sana DESC LIMIT 1
                    """, (src_user_id,))
                    row = cursor.fetchone()
                    conn.close()
                    if row:
                        order_text = row[0]
                        user_name = row[1] or "Foydalanuvchi"
                        username = row[2]
                        phone = row[3]
                        break
                except Exception as dbe:
                    logger.error(f"fastsend db {db_file}: {dbe}")

            # Buyurtma guruhlarini olish
            order_groups = []
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT group_id FROM order_groups')
                    order_groups = [row[0] for row in cursor.fetchall()]
            except Exception as e:
                logger.error(f"fastsend order_groups: {e}")

            if not order_groups:
                order_groups = [ORDER_GROUP_ID]

            # Xabar matni tayyorlash
            caption = "🚕 <b>ASSALOMU ALEYKUM HURMATLI TAXI HAYDOVCHILARI 🆕 YANGI BUYURTMA KELDI!</b>\n\n"
            caption += f"👤 <a href='tg://user?id={src_user_id}'>{user_name}</a>\n"
            if username:
                caption += f"🤙 @{username}\n"
            if order_text:
                caption += f"\n💬 <b><i>{order_text}</i></b>\n"
            if phone:
                p = str(phone).replace(' ', '').replace('-', '')
                if not p.startswith('+'):
                    p = '+998' + p if p.startswith('998') else '+' + p
                caption += f"\n📞 {p}"

            # Tugmalar
            inline_buttons = []
            if username:
                inline_buttons.append([{"text": f"👤 {user_name}", "url": f"https://t.me/{username}"}])
            if phone:
                p = str(phone).replace(' ', '').replace('-', '')
                if not p.startswith('+'):
                    p = '+998' + p if p.startswith('998') else '+' + p
                inline_buttons.append([{"text": f"📞 {p}", "url": f"https://onmap.uz/tel/{p}"}])

            sent = 0
            errors = []
            import aiohttp as _aiohttp
            async with _aiohttp.ClientSession() as session:
                for gid in order_groups:
                    try:
                        payload = {
                            "chat_id": gid,
                            "text": caption,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": True,
                            "reply_markup": {"inline_keyboard": inline_buttons}
                        }
                        async with session.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json=payload
                        ) as resp:
                            resp_data = await resp.json()
                            if resp.status == 200:
                                sent += 1
                            else:
                                err_msg = resp_data.get('description', str(resp.status))
                                errors.append(f"{gid}: {err_msg}")
                                logger.error(f"Fastsend send {gid}: {resp.status} - {err_msg}")
                    except Exception as e:
                        errors.append(f"{gid}: {e}")
                        logger.error(f"Fastsend send {gid}: {e}")

            result_text = f"✅ {sent} ta buyurtma guruhiga yuborildi!"
            if errors:
                result_text += f"\n\n❌ Xatolar:\n" + "\n".join(errors[:3])
            await message.answer(result_text)
        except Exception as e:
            logger.error(f"Fastsend deep link: {e}")
            await message.answer(f"❌ Xatolik: {e}")
        return

    # Buyurtmani ko'rish (deep link orqali)
    if len(args) > 1 and args[1].startswith('zakaz_'):
        try:
            order_num = int(args[1].replace('zakaz_', ''))
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT z.order_number, z.message, z.user_type, z.sana, u.user_name, u.username, u.phone 
                    FROM zakazlar z 
                    LEFT JOIN users u ON z.user_id = u.user_id 
                    WHERE z.order_number = ?
                """, (order_num,))
                order = cursor.fetchone()
                
            if order:
                text = (
                    f"🚕 <b>ZAKAZ #{order[0]}</b>\n\n"
                    f"👤 <b>Foydalanuvchi:</b> {order[4] or 'Nomaum'}\n"
                    f"💬 <b>Xabar:</b> {order[1]}\n"
                    f"📅 <b>Sana:</b> {order[3]}\n"
                )
                
                buttons = []
                if order[6]: # phone
                    phone = order[6].replace(' ', '').replace('-', '')
                    if not phone.startswith('+'):
                        phone = '+998' + phone if phone.startswith('998') else '+998' + phone
                    buttons.append([InlineKeyboardButton(text="📞 Qo'ng'iroq qilish", url=f"https://onmap.uz/tel/{phone}")])
                
                if order[5]: # username
                    buttons.append([InlineKeyboardButton(text=f"👤 @{order[5]}", url=f"https://t.me/{order[5]}")])
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
                return
            else:
                await message.answer("❌ Buyurtma topilmadi.")
        except Exception as e:
            logger.error(f"Start deep link error: {e}")
            return

    # Bloklash (deep link orqali)
    if len(args) > 1 and args[1].startswith('block_'):
        if not is_admin(message.from_user.id):
            await message.answer(
                "😂 <b>Siz admin emassiz-ku!</b>\n\n"
                "Uyalmaysizmi birovni bloklashga urinishga? 🤡\n"
                "Faqat haqiqiy adminlargina bu sehrli kuchga ega! ✨",
                parse_mode='HTML'
            )
            return
        try:
            parts = args[1].replace('block_', '').split('_')
            user_id_to_block = int(parts[0])
            chat_id_to_delete = int(parts[1]) if len(parts) > 1 else None
            msg_id_to_delete = int(parts[2]) if len(parts) > 2 and parts[2] != 'None' else None
            block_user(user_id_to_block)
            # Guruhdan zakaz xabarini o'chirish
            if chat_id_to_delete and msg_id_to_delete:
                try:
                    await bot.delete_message(chat_id=chat_id_to_delete, message_id=msg_id_to_delete)
                except Exception as del_err:
                    logger.error(f"Xabar o'chirishda xatolik: {del_err}")
            await message.answer(
                f"🚫 <b>Foydalanuvchi muvaffaqiyatli bloklandi!</b>\n\n"
                f"🆔 ID: <code>{user_id_to_block}</code>\n\n"
                f"Endi bu foydalanuvchidan buyurtmalar kelmaydi.",
                parse_mode='HTML'
            )
            return
        except Exception as e:
            logger.error(f"Deep link block error: {e}")
            await message.answer(f"❌ Bloklashda xatolik: {e}")
            return

    # Kontakt ko'rish (deep link orqali)
    if len(args) > 1 and args[1].startswith('contact_'):
        try:
            contact_user_id = int(args[1].replace('contact_', ''))
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_name, username, phone FROM users WHERE user_id = ?", (contact_user_id,))
                user = cursor.fetchone()
            
            if user:
                user_name = user[0] or "Noma'lum"
                text = f"👤 <b>Mijoz ma'lumotlari:</b>\n\n"
                text += f"👤 <a href='tg://user?id={contact_user_id}'>{user_name}</a>\n"
                if user[1]:  # username
                    text += f"🤙 @{user[1]}\n"
                if user[2]:  # phone
                    text += f"📞 +{user[2]}\n"
                text += f"\n🆔 ID: <code>{contact_user_id}</code>"
                
                buttons = []
                if user[1]:  # username
                    buttons.append([InlineKeyboardButton(text=f"👤 @{user[1]}", url=f"https://t.me/{user[1]}")])
                if user[2]:  # phone
                    phone = user[2].replace(' ', '').replace('-', '')
                    if not phone.startswith('+'):
                        phone = '+' + phone
                    buttons.append([InlineKeyboardButton(text=f"📞 {phone}", url=f"https://onmap.uz/tel/{phone}")])
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
                await message.answer(text, parse_mode='HTML', reply_markup=keyboard)
            else:
                await message.answer(
                    f"👤 <a href='tg://user?id={contact_user_id}'>Mijoz bilan bog'lanish</a>\n\n"
                    f"🆔 ID: <code>{contact_user_id}</code>",
                    parse_mode='HTML'
                )
            return
        except Exception as e:
            logger.error(f"Deep link contact error: {e}")
            await message.answer(f"❌ Xatolik: {e}")
            return

    if is_admin(message.from_user.id):
        await message.answer(
            "🤖 Userbot boshqaruv paneli\n\n"
            "Quyidagi tugmalardan birini tanlang:",
            reply_markup=main_menu()
        )
    else:
        # Oddiy foydalanuvchilar uchun taksi bot
        await message.answer(
            "🚕 <b>Taksi Bot</b>\n\n"
            "👋 Assalomu alaykum!\n"
            "Men sizga taksi topishda yordam beraman.\n\n"
            "📍 Bormoqchi yo'nalishingizni tanlang:",
            reply_markup=direction_menu(),
            parse_mode='HTML'
        )

@dp.message(lambda message: message.text == "📊 Statistika")
async def stats_handler(message: types.Message):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM zakazlar")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE user_type LIKE '%Haydovchi%'")
        drivers = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE user_type LIKE '%Yolovchi%' OR user_type = '' OR user_type IS NULL")
        passengers = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE DATE(sana) = DATE('now')")
        today = cursor.fetchone()[0]
    
    await message.answer(
        f"📊 Statistika:\n\n"
        f"📈 Jami zakazlar: {total}\n"
        f"🚗 Haydovchilar: {drivers}\n"
        f"🙋 Yo'lovchilar: {passengers}\n"
        f"📅 Bugun: {today}"
    )

@dp.message(lambda message: message.text == "📋 Guruh statistikasi")
async def group_stats_handler(message: types.Message):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT group_name, COUNT(*) as zakaz_soni
            FROM zakazlar
            WHERE group_name IS NOT NULL AND group_name != ''
            GROUP BY group_name
            ORDER BY zakaz_soni DESC
            LIMIT 15
        """)
        results = cursor.fetchall()
    
    if not results:
        await message.answer("📝 Guruh statistikasi yo'q")
        return
    
    text = "📋 Guruh statistikasi:\n\n"
    for i, (group_name, count) in enumerate(results, 1):
        text += f"{i}. {group_name}: {count} ta zakaz\n"
    
    await message.answer(text)

@dp.message(lambda message: message.text == "🕜 Oxirgi 10 ta zakaz")
async def passengers_only_handler(message: types.Message):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT z.id, z.order_number, z.user_id, z.user_type, z.message, z.group_name, z.group_id, z.sana, u.user_name, u.username, u.phone
            FROM zakazlar z
            LEFT JOIN users u ON z.user_id = u.user_id
            WHERE z.user_type LIKE '%Yolovchi%' OR z.user_type = '' OR z.user_type IS NULL
            ORDER BY z.sana DESC
            LIMIT 10
        """)
        passenger_orders = cursor.fetchall()
    
    if not passenger_orders:
        await message.answer("📭 Yo'lovchi zakazlari topilmadi")
        return
    
    await message.answer(f"🕜 Oxirgi {len(passenger_orders)} ta zakaz:")
    
    for order in passenger_orders:
        message_link = f"https://t.me/c/{str(order[6])[4:]}/1" if len(order) > 6 and str(order[6]).startswith('-100') else "#"
        group_link = f"https://t.me/c/{str(order[6])[4:]}" if len(order) > 6 and str(order[6]).startswith('-100') else "#"
        
        phone_patterns = [
            r'\+998\d{9}',
            r'998\d{9}',
            r'\d{9}',
            r'\d{2}\s\d{3}\s\d{2}\s\d{2}',
            r'\d{2}-\d{3}-\d{2}-\d{2}',
        ]
        phones = []
        # Telefon raqam - avval users jadvalidan, keyin xabar matnidan
        if len(order) > 9 and order[9]:  # phone from users table
            phones = [order[9]]
        else:
            for pattern in phone_patterns:
                found = re.findall(pattern, order[4] if len(order) > 4 else '')  # message
                phones.extend(found)
                if phones:
                    break
        
        clean_message = order[4] if len(order) > 4 else ''  # message
        if phones:
            for phone in phones:
                clean_message = clean_message.replace(phone, '')
        
        text_parts = []
        
        if len(order) > 7 and order[7]:  # user_name from users table
            if len(order) > 3 and order[3]:  # user_type
                text_parts.append(f"👤 {order[7]} ({order[3]})")
            else:
                text_parts.append(f"👤 {order[7]}")
        elif len(order) > 2 and order[2]:  # user_name from zakazlar table
            text_parts.append(f"👤 {order[2]}")
        
        if clean_message and clean_message.strip():
            text_parts.append(f"💬 {clean_message.strip()}")
        
        if len(order) > 5 and order[5] and group_link != "#":
            text_parts.append(f"🫂 <a href='{group_link}'>{order[5]}</a>")
        
        if len(order) > 7 and order[7] and isinstance(order[7], str):
            text_parts.append(f"📅 {order[7][:16]}")
        elif len(order) > 6 and isinstance(order[6], str):
            text_parts.append(f"📅 {str(order[6])[:16]}")
        
        text = "\n\n".join(text_parts)
        
        buttons = []
        
        if phones:
            phone = phones[0].replace(' ', '').replace('-', '')
            if phone.startswith('998'):
                phone = '+' + phone
            elif not phone.startswith('+998'):
                phone = '+998' + phone
            buttons.append([InlineKeyboardButton(text=f"📞 {phone}", url=f"https://onmap.uz/tel/{phone}")])
        
        # Username tugmasi (akkaunt orqali) - faqat username bo'lsa
        if len(order) > 9 and order[9]:  # username
            buttons.append([InlineKeyboardButton(text=f"👤 @{order[9]}", url=f"https://t.me/{order[9]}")])
        
        # Bloklash/Blokdan chiqarish tugmasi
        if len(order) > 2 and order[2] and isinstance(order[2], int):  # user_id mavjud va int bo'lsa
            # Foydalanuvchi bloklangan yoki yo'qligini tekshirish
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM blocked_users WHERE user_id = ?', (order[2],))
                    is_blocked = cursor.fetchone() is not None
                
                if is_blocked:
                    buttons.append([InlineKeyboardButton(text="✅ Blokdan chiqarish", callback_data=f"unblock_{order[2]}")])
                else:
                    buttons.append([InlineKeyboardButton(text="🚫 Bloklash", callback_data=f"block_{order[2]}")])
            except Exception as e:
                logger.error(f"Bloklash holatini tekshirishda xatolik: {e}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        
        await message.answer(text, parse_mode='HTML', disable_web_page_preview=True, reply_markup=keyboard)



@dp.message(lambda message: message.text == "📝 So'zlar qo'shish")
async def add_words_handler(message: types.Message):
    await message.answer(
        "📝 Qaysi turdagi so'zlar qo'shasiz?",
        reply_markup=words_menu()
    )

@dp.message(lambda message: message.text == "⚠️ To'liq bo'lmagan zakazlar")
async def incomplete_orders_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    await message.answer("✅ Barcha zakazlar to'liq!")

@dp.message(lambda message: message.text == "✅ Zakazni to'ldirish")
async def complete_order_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    user_states[message.from_user.id] = 'waiting_order_id_to_complete'
    await message.answer(
        "✅ Zakazni to'ldirish:\n\n"
        "To'ldirmoqchi bo'lgan zakaz ID sini yuboring:\n"
        "(To'liq bo'lmagan zakazlar ro'yxatidan ID ni oling)"
    )

# Viloyatlar menusi
def regions_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏙️ Toshkent", callback_data="region_toshkent")],
            [InlineKeyboardButton(text="🕌 Samarqand", callback_data="region_samarqand")],
            [InlineKeyboardButton(text="🕌 Buxoro", callback_data="region_buxoro")],
            [InlineKeyboardButton(text="🌿 Namangan", callback_data="region_namangan")],
            [InlineKeyboardButton(text="🌾 Andijon", callback_data="region_andijon")],
            [InlineKeyboardButton(text="🍇 Farg'ona", callback_data="region_fargona")],
            [InlineKeyboardButton(text="🏔️ Qashqadaryo", callback_data="region_qashqadaryo")],
            [InlineKeyboardButton(text="⛰️ Surxondaryo", callback_data="region_surxondaryo")],
            [InlineKeyboardButton(text="🏭 Jizzax", callback_data="region_jizzax")],
            [InlineKeyboardButton(text="🌾 Sirdaryo", callback_data="region_sirdaryo")],
            [InlineKeyboardButton(text="⛏️ Navoiy", callback_data="region_navoiy")],
            [InlineKeyboardButton(text="🏛️ Xorazm", callback_data="region_xorazm")],
            [InlineKeyboardButton(text="🏜️ Qoraqalpog'iston", callback_data="region_qoraqalpogiston")]
        ]
    )
    return keyboard

# Qayerga borish tugmalari
def destination_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏙️ Toshkent", callback_data="dest_toshkent")],
            [InlineKeyboardButton(text="🕌 Samarqand", callback_data="dest_samarqand")],
            [InlineKeyboardButton(text="🕌 Buxoro", callback_data="dest_buxoro")],
            [InlineKeyboardButton(text="🌿 Namangan", callback_data="dest_namangan")],
            [InlineKeyboardButton(text="🌾 Andijon", callback_data="dest_andijon")],
            [InlineKeyboardButton(text="🍇 Farg'ona", callback_data="dest_fargona")],
            [InlineKeyboardButton(text="🏔️ Qashqadaryo", callback_data="dest_qashqadaryo")],
            [InlineKeyboardButton(text="⛰️ Surxondaryo", callback_data="dest_surxondaryo")],
            [InlineKeyboardButton(text="🏭 Jizzax", callback_data="dest_jizzax")],
            [InlineKeyboardButton(text="🌾 Sirdaryo", callback_data="dest_sirdaryo")],
            [InlineKeyboardButton(text="⛏️ Navoiy", callback_data="dest_navoiy")],
            [InlineKeyboardButton(text="🏛️ Xorazm", callback_data="dest_xorazm")],
            [InlineKeyboardButton(text="🏜️ Qoraqalpog'iston", callback_data="dest_qoraqalpogiston")]
        ]
    )
    return keyboard

# Qayerdan chiqish tugmalari  
def departure_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏙️ Toshkent", callback_data="dep_toshkent")],
            [InlineKeyboardButton(text="🕌 Samarqand", callback_data="dep_samarqand")],
            [InlineKeyboardButton(text="🕌 Buxoro", callback_data="dep_buxoro")],
            [InlineKeyboardButton(text="🌿 Namangan", callback_data="dep_namangan")],
            [InlineKeyboardButton(text="🌾 Andijon", callback_data="dep_andijon")],
            [InlineKeyboardButton(text="🍇 Farg'ona", callback_data="dep_fargona")],
            [InlineKeyboardButton(text="🏔️ Qashqadaryo", callback_data="dep_qashqadaryo")],
            [InlineKeyboardButton(text="⛰️ Surxondaryo", callback_data="dep_surxondaryo")],
            [InlineKeyboardButton(text="🏭 Jizzax", callback_data="dep_jizzax")],
            [InlineKeyboardButton(text="🌾 Sirdaryo", callback_data="dep_sirdaryo")],
            [InlineKeyboardButton(text="⛏️ Navoiy", callback_data="dep_navoiy")],
            [InlineKeyboardButton(text="🏛️ Xorazm", callback_data="dep_xorazm")],
            [InlineKeyboardButton(text="🏜️ Qoraqalpog'iston", callback_data="dep_qoraqalpogiston")]
        ]
    )
    return keyboard

# Yo'nalish tanlash
def direction_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="𝗧𝗢𝗦𝗛𝗞𝗘𝗡𝗧 ➡️ 𝗣𝗢𝗣 𝗖𝗛𝗨𝗦𝗧", callback_data="dir_namangan_toshkent")],
            [InlineKeyboardButton(text="𝗣𝗢𝗣 𝗖𝗛𝗨𝗦𝗧 ➡️ 𝗧𝗢𝗦𝗛𝗞𝗘𝗡𝗧", callback_data="dir_toshkent_namangan")]
        ]
    )
    return keyboard

# Joylashuv so'rash
def location_request_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Joylashuvni yuborish", request_location=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

# Telefon raqam so'rash
def phone_request_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    return keyboard

# Foydalanuvchi holati
user_states = {}
taxi_users = {}  # Taksi foydalanuvchilari uchun
pending_profile_auth = {}  # Profil qo'shish: {user_id: (client, phone, session_name)}

@dp.callback_query(lambda c: c.data == "add_driver")
async def add_driver_words(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_driver_words'
    await callback.message.edit_text(
        "🚗 Haydovchi so'zlarini qo'shish:\n\n"
        "So'z yoki so'zlarni vergul bilan ajratib yuboring.\n"
        "Har xabar yangi so'z qo'shadi. Tugatish uchun 'Bekor qilish' bosing.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_add_words")]
        ])
    )

@dp.callback_query(lambda c: c.data == "add_passenger")
async def add_passenger_words(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_passenger_words'
    await callback.message.edit_text(
        "🙋♂️ Yo'lovchi so'zlarini qo'shish:\n\n"
        "So'z yoki so'zlarni vergul bilan ajratib yuboring.\n"
        "Har xabar yangi so'z qo'shadi. Tugatish uchun 'Bekor qilish' bosing.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_add_words")]
        ])
    )

@dp.callback_query(lambda c: c.data == "cancel_add_words")
async def cancel_add_words(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_states and user_states[user_id] in ('waiting_driver_words', 'waiting_passenger_words'):
        del user_states[user_id]
    await callback.message.edit_text(
        "✅ So'z qo'shish tugadi.",
        reply_markup=words_menu()
    )

@dp.callback_query(lambda c: c.data == "delete_driver")
async def delete_driver_words(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_delete_driver_words'
    driver_words = get_keywords('driver')
    if driver_words:
        words_text = "\n".join([f"{i+1}. {word}" for i, word in enumerate(driver_words)])
        await callback.message.edit_text(
            f"🚗 Haydovchi so'zlarini o'chirish:\n\n{words_text}\n\nO'chirish uchun so'zni yozing:"
        )
    else:
        await callback.message.edit_text("📭 Haydovchi so'zlari yo'q")

@dp.callback_query(lambda c: c.data == "delete_all_driver")
async def delete_all_driver_words(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Sizga ruxsat yo'q!")
        return
    
    driver_words = get_keywords('driver')
    if not driver_words:
        await callback.answer("📭 Haydovchi so'zlari yo'q")
        return
    
    # Barcha haydovchi so'zlarini o'chirish
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM keywords WHERE type = ?', ('driver',))
            conn.commit()
        
        await callback.message.edit_text(
            f"✅ Barcha haydovchi so'zlari o'chirildi!\n\n"
            f"Jami o'chirilgan: {len(driver_words)} ta so'z",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]
            ])
        )
        logger.info(f"Admin {callback.from_user.id} barcha haydovchi so'zlarini o'chirdi ({len(driver_words)} ta)")
    except Exception as e:
        logger.error(f"Barcha haydovchi so'zlarini o'chirishda xatolik: {e}")
        await callback.answer("❌ Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data == "delete_passenger")
async def delete_passenger_words(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_delete_passenger_words'
    passenger_words = get_keywords('passenger')
    if passenger_words:
        words_text = "\n".join([f"{i+1}. {word}" for i, word in enumerate(passenger_words)])
        await callback.message.edit_text(
            f"🙋♂️ Yo'lovchi so'zlarini o'chirish:\n\n{words_text}\n\nO'chirish uchun so'zni yozing:"
        )
    else:
        await callback.message.edit_text("📭 Yo'lovchi so'zlari yo'q")

@dp.callback_query(lambda c: c.data == "list_words")
async def list_words(callback: types.CallbackQuery):
    passenger_words = get_keywords('passenger')
    driver_words = get_keywords('driver')
    
    text = f"📋 Yo'lovchi so'zlari ({len(passenger_words)}):\n\n"
    text += ", ".join(passenger_words[:50]) if passenger_words else "Yo'q"
    text += f"\n\n🚗 Haydovchi so'zlari ({len(driver_words)}):\n\n"
    text += ", ".join(driver_words[:50]) if driver_words else "Yo'q"
    
    # Xabar uzunligini cheklash (Telegram 4096 belgigacha)
    if len(text) > 4000:
        text = text[:3990] + "..."
    
    await callback.message.edit_text(text)

@dp.callback_query(lambda c: c.data == "back_main")
async def back_main(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🤖 Userbot boshqaruv paneli\n\n"
        "Quyidagi tugmalardan birini tanlang:"
    )

@dp.callback_query(lambda c: c.data.startswith("block_"))
async def block_user_callback(callback: types.CallbackQuery):
    try:
        user_id = int(callback.data.replace("block_", ""))
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO blocked_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        
        await callback.answer(f"✅ Foydalanuvchi bloklandi: {user_id}")
        # Xabarni yangilash
        await callback.message.edit_reply_markup(reply_markup=None)
    except ValueError:
        await callback.answer("❌ Noto'g'ri foydalanuvchi ID")
    except Exception as e:
        logger.error(f"Bloklashda xatolik: {e}")
        await callback.answer("❌ Bloklashda xatolik yuz berdi")

@dp.callback_query(lambda c: c.data.startswith("unblock_"))
async def unblock_user_callback(callback: types.CallbackQuery):
    try:
        user_id = int(callback.data.replace("unblock_", ""))
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM blocked_users WHERE user_id = ?', (user_id,))
            conn.commit()
        
        await callback.answer(f"✅ Foydalanuvchi blokdan chiqarildi: {user_id}")
        # Xabarni yangilash
        await callback.message.edit_reply_markup(reply_markup=None)
    except ValueError:
        await callback.answer("❌ Noto'g'ri foydalanuvchi ID")
    except Exception as e:
        logger.error(f"Blokdan chiqarishda xatolik: {e}")
        await callback.answer("❌ Blokdan chiqarishda xatolik yuz berdi")



# Yo'nalish tanlash handleri
@dp.callback_query(lambda c: c.data.startswith("dir_"))
async def direction_handler(callback: types.CallbackQuery):
    direction = callback.data.replace("dir_", "")
    
    if direction == "namangan_toshkent":
        from_city = "TOSHKENT"
        to_city = "POP CHUST"
    elif direction == "toshkent_namangan":
        from_city = "POP CHUST"
        to_city = "TOSHKENT"
    else:
        from_city = "Noma'lum"
        to_city = "Noma'lum"
    
    taxi_users[callback.from_user.id] = {
        "from_city": from_city,
        "to_city": to_city
    }
    await callback.answer()
    
    # Yo'lovchilar soni yoki pochta so'rash
    user_states[callback.from_user.id] = 'waiting_passenger_count'
    await callback.message.edit_text(
        f"🚗 <b>{from_city} ➡️ {to_city}</b>\n\n"
        "👥 Nechta yo'lovchi bor yoki 📦 pochta?\n\n"
        "💡 <i>Misol: 3 kishi yoki pochta</i>",
        parse_mode='HTML'
    )

# Qayerga borish handleri
@dp.callback_query(lambda c: c.data.startswith("dest_"))
async def destination_handler(callback: types.CallbackQuery):
    if callback.from_user.id not in taxi_users:
        await callback.answer("⚠️ Sessiya tugagan. /start bosing!", show_alert=True)
        return
    
    dest = callback.data.replace("dest_", "")
    region_names = {
        "toshkent": "Toshkent",
        "samarqand": "Samarqand", 
        "buxoro": "Buxoro",
        "namangan": "Namangan",
        "andijon": "Andijon",
        "fargona": "Farg'ona",
        "qashqadaryo": "Qashqadaryo",
        "surxondaryo": "Surxondaryo",
        "jizzax": "Jizzax",
        "sirdaryo": "Sirdaryo",
        "navoiy": "Navoiy",
        "xorazm": "Xorazm",
        "qoraqalpogiston": "Qoraqalpog'iston"
    }
    
    selected_dest = region_names.get(dest, dest)
    taxi_users[callback.from_user.id]["destination"] = selected_dest
    
    # Avval saqlangan kontaktni tekshirish
    saved_phone = None
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT phone FROM users WHERE user_id = ?', (callback.from_user.id,))
            result = cursor.fetchone()
            if result and result[0]:
                saved_phone = result[0]
    except Exception as e:
        logger.error(f"Kontakt tekshirishda xatolik: {e}")
    
    if saved_phone:
        # Saqlangan kontakt bor - darhol yuborish
        taxi_users[callback.from_user.id]["phone"] = saved_phone
        await send_taxi_order(callback.message, callback.from_user, saved_phone)
    else:
        # Kontakt yo'q - so'rash
        await callback.message.edit_text(
            f"🎯 Qayerga: {selected_dest}\n\n"
            "Telefon raqamingizni yuboring:"
        )
        
        await callback.message.answer(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_request_menu()
        )



# Joylashuv qabul qilish
@dp.message(lambda message: message.chat.type == "private" and message.location)
async def location_handler(message: types.Message):
    if is_admin(message.from_user.id):
        return
    
    taxi_users[message.from_user.id] = {
        "latitude": message.location.latitude,
        "longitude": message.location.longitude
    }
    
    await message.answer(
        "🎯 Qayerga borasiz?",
        reply_markup=destination_menu()
    )

# Telefon raqam qabul qilish
@dp.message(lambda message: message.chat.type == "private" and message.contact)
async def contact_handler(message: types.Message):
    if is_admin(message.from_user.id):
        return
    
    if message.from_user.id not in taxi_users:
        await message.answer("⚠️ /start bosing!")
        return
    
    taxi_users[message.from_user.id]["phone"] = message.contact.phone_number

    # Kontaktni saqlash
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO users (user_id, user_name, phone) VALUES (?, ?, ?)',
                (message.from_user.id, message.from_user.first_name or 'Foydalanuvchi', message.contact.phone_number))
            conn.commit()
    except Exception as e:
        logger.error(f"Kontakt saqlash: {e}")

    user_data = taxi_users[message.from_user.id]
    if "free_text" in user_data:
        await send_free_order(message, message.from_user, message.contact.phone_number)
    elif "destination" not in user_data:
        await message.answer("🎯 Qayerga borishingizni tanlang:", reply_markup=destination_menu())
    else:
        await send_taxi_order(message, message.from_user, message.contact.phone_number)

# Zakaz yuborish funksiyasi (yo'nalish tanlash uchun)
async def send_taxi_order_simple(message, user, phone):
    user_data = taxi_users[user.id]
    
    # Keyingi zakaz raqamini olish
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COALESCE(MAX(order_number), 0) + 1 FROM zakazlar')
            order_number = cursor.fetchone()[0]
    except:
        order_number = 1
    
    # Telefon raqamni formatlash
    formatted_phone = phone.replace(' ', '').replace('-', '')
    if not formatted_phone.startswith('+'):
        if formatted_phone.startswith('998'):
            formatted_phone = '+' + formatted_phone
        else:
            formatted_phone = '+998' + formatted_phone
    
    # Zakazni guruhga yuborish
    user_name = f"{user.first_name or 'Foydalanuvchi'}"
    if user.last_name:
        user_name = f"{user.first_name} {user.last_name}"
    
    order_message = (
        f"🚕 <b>ZAKAZ #{order_number}</b>\n"
        f"{'='*25}\n"
        f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
        f"📞 {formatted_phone}\n"
        f'👥 Yo\'lovchilar: {user_data.get("passenger_count", "Noma'lum")}'
    )
    
    # Tugmalarni tayyorlash
    buttons = []
    
    # Qongiroq tugmasi
    buttons.append([InlineKeyboardButton(text=f"📞 Qo'ngiroq qilish", url=f"https://onmap.uz/tel/{formatted_phone}")])
    
    # Username tugmasi (akkaunt orqali) - faqat username bo'lsa
    if user.username:
        buttons.append([InlineKeyboardButton(text=f"👤 @{user.username}", url=f"https://t.me/{user.username}")])
    
    order_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    # Asosiy guruhga yuborish - yagona xabar
    try:
        # Avval xabarni havola'siz yuborish
        order_message_without_link = (
            f"🚕 <b>ZAKAZ #{order_number}</b>\n"
            f"{'='*25}\n\n"
            f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
            f"📞 <b>Telefon:</b> {formatted_phone}\n\n"
            f"🚗 <b>Yo'nalish:</b>\n"
            f"   {user_data['from_city']} ➡️ {user_data['to_city']}\n\n"
            f'👥 <b>Yo\'lovchilar:</b> {user_data.get("passenger_count", "Noma'lum")}\n'
            f'🕐 <b>Vaqt:</b> {user_data.get("departure_time", "Noma'lum")}\n\n'
            f"<b>Mijoz:</b> {user_name}"
        )
        
        sent_message = await bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=order_message_without_link,
            parse_mode='HTML',
            reply_markup=order_keyboard
        )
        message_id = sent_message.message_id
        
        # Xabarni havola'si bilan edit qilish
        order_message_with_link = order_message_without_link + f"\n\n<a href='https://t.me/c/{str(ORDER_GROUP_ID)[4:]}/{message_id}'>📨 Habarni ko'rish</a>"
        await bot.edit_message_text(
            chat_id=ORDER_GROUP_ID,
            message_id=message_id,
            text=order_message_with_link,
            parse_mode='HTML',
            reply_markup=order_keyboard
        )
        
        # Qo'shimcha guruhlarga ham yuborish
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM order_groups WHERE group_id != ?', (ORDER_GROUP_ID,))
                order_groups = [row[0] for row in cursor.fetchall()]
                
                for group_id in order_groups:
                    await bot.send_message(
                        chat_id=group_id,
                        text=order_message,
                        parse_mode='HTML',
                        reply_markup=order_keyboard
                    )
        except Exception as e:
            logger.error(f"Qo'shimcha guruhlarga yuborishda xatolik: {e}")
        
        if hasattr(message, 'answer'):
            await message.answer(
                "✅ <b>Zakazingiz qabul qilindi!</b>\n\n"
                "🚗 Tez orada haydovchilar siz bilan bog'lanishadi.\n\n"
                "🔄 Yangi zakaz berish uchun /start bosing.",
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode='HTML'
            )
        else:
            await bot.send_message(
                chat_id=user.id,
                text="✅ <b>Zakazingiz qabul qilindi!</b>\n\n"
                     "🚗 Tez orada haydovchilar siz bilan bog'lanishadi.\n\n"
                     "🔄 Yangi zakaz berish uchun /start bosing.",
                parse_mode='HTML'
            )
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Zakaz yuborishda xatolik: {e}")
        
        if "chat not found" in error_msg.lower():
            error_text = "❌ <b>Guruh topilmadi!</b>\n\nAdmin bilan bog'laning."
        else:
            error_text = f"❌ <b>Xatolik yuz berdi!</b>\n\n{error_msg}\n\nQaytadan urinib ko'ring."
            
        if hasattr(message, 'answer'):
            await message.answer(error_text, parse_mode='HTML')
        else:
            await bot.send_message(chat_id=user.id, text=error_text, parse_mode='HTML')
    
    # Foydalanuvchi holatini tozalash
    if user.id in taxi_users:
        del taxi_users[user.id]

# Zakaz yuborish funksiyasi (erkin matn uchun)
async def send_free_order(message, user, phone):
    user_data = taxi_users.get(user.id, {})
    free_text = user_data.get("free_text", "")

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COALESCE(MAX(order_number), 0) + 1 FROM zakazlar')
            order_number = cursor.fetchone()[0]
    except:
        order_number = 1

    formatted_phone = phone.replace(' ', '').replace('-', '')
    if not formatted_phone.startswith('+'):
        formatted_phone = '+' + ('998' + formatted_phone if not formatted_phone.startswith('998') else formatted_phone)

    user_name = user.first_name or 'Foydalanuvchi'
    if user.last_name:
        user_name = f"{user.first_name} {user.last_name}"

    buttons = [[InlineKeyboardButton(text="📞 Qo'ngiroq qilish", url=f"https://onmap.uz/tel/{formatted_phone}")]]
    if user.username:
        buttons.append([InlineKeyboardButton(text=f"👤 @{user.username}", url=f"https://t.me/{user.username}")])
    order_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    order_text = (
        f"🚕 <b>ZAKAZ #{order_number}</b>\n"
        f"{'='*25}\n\n"
        f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
        f"📞 <b>Telefon:</b> {formatted_phone}\n\n"
        f"📝 <b>Zakaz:</b>\n{free_text}\n\n"
        f"<b>Mijoz:</b> {user_name}"
    )

    try:
        sent = await bot.send_message(chat_id=ORDER_GROUP_ID, text=order_text, parse_mode='HTML', reply_markup=order_keyboard)
        # Qo'shimcha guruhlar
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM order_groups WHERE group_id != ?', (ORDER_GROUP_ID,))
                for (gid,) in cursor.fetchall():
                    try:
                        await bot.send_message(chat_id=gid, text=order_text, parse_mode='HTML', reply_markup=order_keyboard)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Qo'shimcha guruhlarga yuborishda xatolik: {e}")

        await message.answer(
            "✅ <b>Zakazingiz qabul qilindi!</b>\n\n"
            "🚗 Tez orada haydovchilar siz bilan bog'lanishadi.\n\n"
            "🔄 Yangi zakaz berish uchun /start bosing.",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"send_free_order xatolik: {e}")
        await message.answer("❌ Zakazni yuborishda xatolik yuz berdi. Qaytadan urinib ko'ring.")
    finally:
        if user.id in taxi_users:
            del taxi_users[user.id]


# Zakaz yuborish funksiyasi (joylashuv tanlash uchun)
async def send_taxi_order(message, user, phone):
    user_data = taxi_users[user.id]
    
    # Telefon raqamni formatlash
    formatted_phone = phone.replace(' ', '').replace('-', '')
    if not formatted_phone.startswith('+'):
        if formatted_phone.startswith('998'):
            formatted_phone = '+' + formatted_phone
        else:
            formatted_phone = '+998' + formatted_phone
    
    # Zakazni guruhga yuborish
    user_name = f"{user.first_name or 'Foydalanuvchi'}"
    if user.last_name:
        user_name = f"{user.first_name} {user.last_name}"
    
    order_message = (
        f"🚕 <b>YANGI ZAKAZ</b>\n"
        f"{'='*25}\n"
        f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
        f"📞 {formatted_phone}\n"
        f"🎯 {user_data.get('destination', 'Noma\'lum')}"
    )
    
    # Tugmalarni tayyorlash
    buttons = []
    
    # Qongiroq tugmasi
    buttons.append([InlineKeyboardButton(text=f"📞 Qo'ngiroq qilish", url=f"https://onmap.uz/tel/{formatted_phone}")])
    
    # Username tugmasi (akkaunt orqali) - faqat username bo'lsa
    if user.username:
        buttons.append([InlineKeyboardButton(text=f"👤 @{user.username}", url=f"https://t.me/{user.username}")])
    
    order_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    # Asosiy guruhga yuborish
    try:
        # Yagona xabar - barcha ma'lumotlar bilan (havola'siz)
        order_message_without_link = (
            f"🚕 <b>YANGI ZAKAZ</b>\n"
            f"{'='*25}\n\n"
            f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
            f"📞 <b>Telefon:</b> {formatted_phone}\n"
            f"🎯 <b>Qayerga:</b> {user_data.get('destination', 'Noma lum')}"
        )
        
        sent_message = await bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=order_message_without_link,
            parse_mode='HTML',
            reply_markup=order_keyboard
        )
        message_id = sent_message.message_id
        
        # Xabarni havola'si bilan edit qilish
        order_message_with_link = order_message_without_link + f"\n\n<a href='https://t.me/c/{str(ORDER_GROUP_ID)[4:]}/{message_id}'>📨 Habarni ko'rish</a>"
        await bot.edit_message_text(
            chat_id=ORDER_GROUP_ID,
            message_id=message_id,
            text=order_message_with_link,
            parse_mode='HTML',
            reply_markup=order_keyboard
        )
        
        # Joylashuvni yuborish (agar mavjud bo'lsa)
        if "latitude" in user_data and "longitude" in user_data:
            await bot.send_location(
                chat_id=ORDER_GROUP_ID,
                latitude=user_data["latitude"],
                longitude=user_data["longitude"]
            )
        
        # Qo'shimcha guruhlarga ham yuborish
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM order_groups WHERE group_id != ?', (ORDER_GROUP_ID,))
                order_groups = [row[0] for row in cursor.fetchall()]
                
                for group_id in order_groups:
                    # Matn xabari
                    await bot.send_message(
                        chat_id=group_id,
                        text=order_message_with_link,
                        parse_mode='HTML',
                        reply_markup=order_keyboard
                    )
                    # Joylashuv
                    if "latitude" in user_data and "longitude" in user_data:
                        await bot.send_location(
                            chat_id=group_id,
                            latitude=user_data["latitude"],
                            longitude=user_data["longitude"]
                        )
        except Exception as e:
            logger.error(f"Qo'shimcha guruhlarga yuborishda xatolik: {e}")
        
        if hasattr(message, 'answer'):
            await message.answer(
                "✅ Zakazingiz muvaffaqiyatli yuborildi!\n\n"
                "Tez orada haydovchilar siz bilan bog'lanishadi.\n\n"
                "Yangi zakaz berish uchun /start bosing.",
                reply_markup=types.ReplyKeyboardRemove()
            )
        else:
            await bot.send_message(
                chat_id=user.id,
                text="✅ Zakazingiz muvaffaqiyatli yuborildi!\n\n"
                     "Tez orada haydovchilar siz bilan bog'lanishadi.\n\n"
                     "Yangi zakaz berish uchun /start bosing."
            )
    except Exception as e:
        logger.error(f"Zakaz yuborishda xatolik: {e}")
        if hasattr(message, 'answer'):
            await message.answer("❌ Zakazni yuborishda xatolik yuz berdi")
        else:
            await bot.send_message(chat_id=user.id, text="❌ Zakazni yuborishda xatolik yuz berdi")
    
    # Foydalanuvchi holatini tozalash
    if user.id in taxi_users:
        del taxi_users[user.id]

# Tugmalar bilan zakaz yuborish funksiyasi (userbot xabaridan keyin)
async def send_order_buttons(user_id, user_name, formatted_phone, username):
    """Tugmalar bilan alohida xabar yuborish"""
    try:
        # Tugmalarni tayyorlash
        buttons = []
        
        # Qongiroq tugmasi
        buttons.append([InlineKeyboardButton(text=f"📞 Qo'ngiroq qilish", url=f"https://onmap.uz/tel/{formatted_phone}")])
        
        # Username tugmasi (akkaunt orqali) - faqat username bo'lsa
        if username:
            buttons.append([InlineKeyboardButton(text=f"👤 @{username}", url=f"https://t.me/{username}")])
        
        order_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Tugmalar xabari
        buttons_message = f"🚕 <b>ZAKAZ TUGMALARI</b>\n\n👤 {user_name}\n📞 {formatted_phone}"
        
        # Asosiy guruhga yuborish
        await bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=buttons_message,
            parse_mode='HTML',
            reply_markup=order_keyboard
        )
        
        # Qo'shimcha guruhlarga ham yuborish
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM order_groups WHERE group_id != ?', (ORDER_GROUP_ID,))
                order_groups = [row[0] for row in cursor.fetchall()]
                
                for group_id in order_groups:
                    await bot.send_message(
                        chat_id=group_id,
                        text=buttons_message,
                        parse_mode='HTML',
                        reply_markup=order_keyboard
                    )
        except Exception as e:
            logger.error(f"Tugmalar qo'shimcha guruhlarga yuborishda xatolik: {e}")
    except Exception as e:
        logger.error(f"Tugmalar yuborishda xatolik: {e}")
        print(f"❌ XATOLIK: Tugmalar yuborishda - {e}")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Zakaz yuborishda xatolik: {e}")
        
        if "chat not found" in error_msg.lower():
            error_text = "❌ Guruh topilmadi. Admin bilan bog'laning.\n\nBotni guruhga qo'shish kerak."
        else:
            error_text = f"❌ Xatolik yuz berdi: {error_msg}\n\nQaytadan urinib ko'ring."
            
        if hasattr(message, 'answer'):
            await message.answer(error_text)
        else:
            await bot.send_message(chat_id=user.id, text=error_text)
    
    # Foydalanuvchi holatini tozalash
    if user.id in taxi_users:
        del taxi_users[user.id]

@dp.message(lambda message: message.text == "🔍 Qidiruv")
async def search_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    user_states[message.from_user.id] = 'waiting_search_query'
    await message.answer("🔍 Qidiruv uchun:\n\n👤 Foydalanuvchi ismini yoki\n🆔 Chat ID raqamini yozing:")

@dp.message(lambda message: message.chat.type == "private" and message.text and not message.text.startswith('/') and not message.text in ["📊 Statistika", "📝 So'zlar qo'shish", "⚙️ Sozlamalar", "🔍 Qidiruv", "🕜 Oxirgi 10 ta zakaz", "📋 Guruh statistikasi"])
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    
    # Guruhga qo'shilish - faqat ID qabul qilish (DB ga tegmasdan)
    if user_id in user_states and is_admin(user_id):
        state = user_states.get(user_id, '')
        if isinstance(state, str) and state.startswith('waiting_join_monitored_'):
            profile_id = int(state.replace('waiting_join_monitored_', ''))
            del user_states[user_id]
            try:
                group_id = int(message.text.strip())
                _save_to_monitored(profile_id, group_id)
                await message.answer(
                    f"✅ <b>Guruh kuzatuvga qo'shildi!</b>\n\n"
                    f"🆔 Guruh ID: <code>{group_id}</code>\n"
                    f"👤 Profil: #{profile_id}\n\n"
                    f"⚠️ Userbotni qayta ishga tushiring: <code>python main.py</code>",
                    parse_mode='HTML'
                )
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Faqat ID raqam kiriting.\nMisol: -1001234567890")
            return
        elif state == 'waiting_join_group_link':
            del user_states[user_id]
            try:
                group_id = int(message.text.strip())
                import glob as _glob
                added = []
                for cfg_file in sorted(_glob.glob('account_config_*.json')):
                    try:
                        with open(cfg_file, 'r') as _f:
                            cfg = json.load(_f)
                        pid = cfg.get('account_id')
                        if pid is not None:
                            _save_to_monitored(pid, group_id)
                            added.append(pid)
                    except:
                        pass
                if added:
                    await message.answer(
                        f"✅ <b>Guruh barcha profillarga qo'shildi!</b>\n\n"
                        f"🆔 Guruh ID: <code>{group_id}</code>\n"
                        f"👥 Profillar: {', '.join(f'#{p}' for p in added)}\n\n"
                        f"⚠️ Userbotni qayta ishga tushiring: <code>python main.py</code>",
                        parse_mode='HTML'
                    )
                else:
                    await message.answer("❌ Hech qanday profil topilmadi")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Faqat ID raqam kiriting.\nMisol: -1001234567890")
            return

    # Profil sozlamalari - buyurtma guruhi o'zgartirish
    if user_id in user_states and is_admin(user_id):
        state = user_states[user_id]
        if isinstance(state, str) and state.startswith('waiting_order_group_'):
            profile_id = int(state.replace('waiting_order_group_', ''))
            try:
                order_group_id = int(message.text.strip())
                config_file = f'account_config_{profile_id}.json'
                
                if os.path.exists(config_file):
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                else:
                    config = {'account_id': profile_id, 'order_group_id': order_group_id, 'monitored_groups': [], 'reklama_groups': ["@vijdontaxireklama", "@iymontaxi", "@sobirtaxi_vodiy_voha", "@iymontaxigroup"]}
                
                config['order_group_id'] = order_group_id
                
                with open(config_file, 'w') as f:
                    json.dump(config, f, indent=2)
                
                del user_states[user_id]
                await message.answer(f"✅ Profil #{profile_id} uchun buyurtma guruhi o'zgartirildi: {order_group_id}")
                logger.info(f"Profil {profile_id} buyurtma guruhi: {order_group_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            except Exception as e:
                logger.error(f"Profil sozlash: {e}")
                await message.answer(f"❌ Xatolik: {e}")
            return
    
    # Taksi foydalanuvchilari uchun holatlar
    if user_id in user_states:
        # Yo'lovchilar soni
        if user_states[user_id] == 'waiting_passenger_count':
            taxi_users[user_id]["passenger_count"] = message.text.strip()
            user_states[user_id] = 'waiting_departure_time'
            await message.answer(
                "🕐 <b>Qachon yo'lga chiqmoqchisiz?</b>\n\n"
                "💡 <i>Misol: Bugun 17:00, Ertaga 09:00</i>",
                parse_mode='HTML'
            )
            return
        
        # Yo'lga chiqish vaqti
        elif user_states[user_id] == 'waiting_departure_time':
            taxi_users[user_id]["departure_time"] = message.text.strip()
            del user_states[user_id]
            
            # Avval saqlangan kontaktni tekshirish
            saved_phone = None
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,))
                    result = cursor.fetchone()
                    if result and result[0]:
                        saved_phone = result[0]
            except Exception as e:
                logger.error(f"Kontakt tekshirishda xatolik: {e}")
            
            if saved_phone:
                # Saqlangan kontakt bor - darhol yuborish
                taxi_users[user_id]["phone"] = saved_phone
                await send_taxi_order_simple(message, message.from_user, saved_phone)
            else:
                # Kontakt yo'q - so'rash
                await message.answer(
                    "📞 Telefon raqamingizni yuboring:",
                    reply_markup=phone_request_menu()
                )
            return
    
    # Profil qo'shish holatlari
    if user_id in user_states and is_admin(user_id):
        if user_states[user_id] == 'waiting_profile_phone':
            phone = message.text.strip().replace(' ', '').replace('-', '')
            if not phone.startswith('+'):
                phone = '+998' + phone if (phone.startswith('998') and len(phone) >= 9) else '+' + phone
            if len(phone) < 12:
                await message.answer("❌ Noto'g'ri format. Misol: +998901234567")
                return
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM profiles')
                    next_id = cursor.fetchone()[0]
                session_name = f"userbot_profile_{next_id}"
                client = TelegramClient(session_name, API_ID, API_HASH)
                await client.connect()
                await client.send_code_request(phone)
                pending_profile_auth[user_id] = (client, phone, session_name)
                user_states[user_id] = 'waiting_profile_code'
                await message.answer("📱 Telegramga kod yuborildi.\n\nKodni yuboring (vergul bilan ajratib agar 2 qism bo'lsa, masalan: 12,345):")
            except Exception as e:
                logger.error(f"Profil kod yuborish: {e}")
                del user_states[user_id]
                await message.answer(f"❌ Xatolik: {e}")
            return
        elif user_states[user_id] == 'waiting_profile_code':
            if user_id not in pending_profile_auth:
                del user_states[user_id]
                await message.answer("❌ Sessiya tugadi. Qaytadan boshlang.")
                return
            client, phone, session_name = pending_profile_auth[user_id]
            code = message.text.strip().replace(',', '').replace(' ', '').replace('-', '')
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                user_states[user_id] = 'waiting_profile_2fa'
                await message.answer("🔐 2FA yoqilgan. Parolni yuboring:")
                return
            except Exception as e:
                del pending_profile_auth[user_id]
                del user_states[user_id]
                await client.disconnect()
                await message.answer(f"❌ Xatolik: {e}")
                return
            # Muvaffaqiyat
            try:
                me = await client.get_me()
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('INSERT INTO profiles (session_name, phone, tg_user_id, username) VALUES (?, ?, ?, ?)',
                        (session_name, phone, me.id, me.username))
                    conn.commit()
                del pending_profile_auth[user_id]
                del user_states[user_id]
                await client.disconnect()
                await message.answer(f"✅ Profil qo'shildi! (@{me.username or me.id})\n\nUserbotni qayta ishga tushiring: python main.py")
            except Exception as e:
                logger.error(f"Profil saqlash: {e}")
                del pending_profile_auth[user_id]
                del user_states[user_id]
                await client.disconnect()
                await message.answer(f"❌ Saqlashda xatolik: {e}")
            return
        elif user_states[user_id] == 'waiting_profile_2fa':
            if user_id not in pending_profile_auth:
                del user_states[user_id]
                await message.answer("❌ Sessiya tugadi. Qaytadan boshlang.")
                return
            client, phone, session_name = pending_profile_auth[user_id]
            try:
                await client.sign_in(password=message.text.strip())
            except Exception as e:
                await message.answer(f"❌ Parol xato. Qaytadan urinib ko'ring: {e}")
                return
            try:
                me = await client.get_me()
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('INSERT INTO profiles (session_name, phone, tg_user_id, username) VALUES (?, ?, ?, ?)',
                        (session_name, phone, me.id, me.username))
                    conn.commit()
                del pending_profile_auth[user_id]
                del user_states[user_id]
                await client.disconnect()
                await message.answer(f"✅ Profil qo'shildi! (@{me.username or me.id})\n\nUserbotni qayta ishga tushiring: python main.py")
            except Exception as e:
                logger.error(f"Profil saqlash: {e}")
                del pending_profile_auth[user_id]
                del user_states[user_id]
                await client.disconnect()
                await message.answer(f"❌ Saqlashda xatolik: {e}")
            return

    # So'z qo'shish holatlari
    if user_id in user_states:
        if user_states[user_id] == 'waiting_driver_words':
            words = [w.strip() for w in message.text.split(',') if w.strip()]
            for word in words:
                save_keyword('driver', word)
            await message.answer(
                f"✅ {len(words)} ta so'z qo'shildi: {', '.join(words)}\n\nYana so'z yuboring yoki tugatish uchun 'Bekor qilish' bosing.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_add_words")]
                ])
            )
            return
        elif user_states[user_id] == 'waiting_passenger_words':
            words = [w.strip() for w in message.text.split(',') if w.strip()]
            for word in words:
                save_keyword('passenger', word)
            await message.answer(
                f"✅ {len(words)} ta so'z qo'shildi: {', '.join(words)}\n\nYana so'z yuboring yoki tugatish uchun 'Bekor qilish' bosing.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_add_words")]
                ])
            )
            return
        elif user_states[user_id] == 'waiting_delete_driver_words':
            word = message.text.strip()
            driver_words = get_keywords('driver')
            if word in driver_words:
                delete_keyword('driver', word)
                await message.answer(f"❌ Haydovchi so'zi o'chirildi: {word}")
            else:
                await message.answer(f"⚠️ So'z topilmadi: {word}")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_delete_passenger_words':
            word = message.text.strip()
            passenger_words = get_keywords('passenger')
            if word in passenger_words:
                delete_keyword('passenger', word)
                await message.answer(f"❌ Yo'lovchi so'zi o'chirildi: {word}")
            else:
                await message.answer(f"⚠️ So'z topilmadi: {word}")
            del user_states[user_id]
            return

        elif user_states[user_id] == 'waiting_block_user_id':
            try:
                user_id_to_block = int(message.text)
                block_user(user_id_to_block)
                await message.answer(f"🚫 Bloklandi: {user_id_to_block}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            del user_states[user_id]
            return
        
        # Profil uchun guruh boshqarish
        elif isinstance(state, str) and state.startswith('waiting_add_monitored_'):
            profile_id = int(state.replace('waiting_add_monitored_', ''))
            try:
                group_id = int(message.text.strip())
                config_file = f'account_config_{profile_id}.json'
                config = {}
                if os.path.exists(config_file):
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                
                monitored = config.get('monitored_groups', [])
                if group_id not in monitored:
                    monitored.append(group_id)
                    config['monitored_groups'] = monitored
                    with open(config_file, 'w') as f:
                        json.dump(config, f, indent=2)
                    await message.answer(f"✅ Profil #{profile_id}: Guruh qo'shildi: {group_id}")
                else:
                    await message.answer(f"⚠️ Guruh allaqachon mavjud.")
            except:
                await message.answer("❌ Xatolik! To'g'ri ID yuboring.")
            del user_states[user_id]
            return
            
        elif isinstance(state, str) and state.startswith('waiting_remove_monitored_'):
            profile_id = int(state.replace('waiting_remove_monitored_', ''))
            try:
                group_id = int(message.text.strip())
                config_file = f'account_config_{profile_id}.json'
                if os.path.exists(config_file):
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                    monitored = config.get('monitored_groups', [])
                    if group_id in monitored:
                        monitored.remove(group_id)
                        config['monitored_groups'] = monitored
                        with open(config_file, 'w') as f:
                            json.dump(config, f, indent=2)
                        await message.answer(f"✅ Profil #{profile_id}: Guruh o'chirildi: {group_id}")
                    else:
                        await message.answer(f"⚠️ Guruh topilmadi.")
                else:
                    await message.answer("❌ Profil konfigi topilmadi.")
            except:
                await message.answer("❌ Xatolik!")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_unblock_user_id':
            try:
                user_id_to_unblock = int(message.text)
                unblock_user(user_id_to_unblock)
                await message.answer(f"✅ Blokdan chiqarildi: {user_id_to_unblock}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_add_order_group_id':
            try:
                order_group_id = int(message.text)
                order_groups = load_order_groups()
                if order_group_id not in order_groups:
                    save_order_group(order_group_id)
                    await message.answer(f"✅ Buyurtma guruhi qo'shildi: {order_group_id}")
                else:
                    await message.answer(f"⚠️ Buyurtma guruhi allaqachon mavjud: {order_group_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_remove_order_group_id':
            try:
                order_group_id = int(message.text)
                order_groups = load_order_groups()
                if order_group_id in order_groups:
                    remove_order_group(order_group_id)
                    await message.answer(f"❌ Buyurtma guruhi o'chirildi: {order_group_id}")
                else:
                    await message.answer(f"⚠️ Buyurtma guruhi topilmadi: {order_group_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_add_reklama_group_id':
            reklama_group_username = message.text.strip()
            if not reklama_group_username.startswith('@') and not reklama_group_username.startswith('-100'):
                await message.answer("❌ Noto'g'ri format! @username yoki -100 toifasidagi ID kiriting.")
            else:
                reklama_groups = load_reklama_groups()
                if reklama_group_username not in reklama_groups:
                    save_reklama_group(reklama_group_username)
                    await message.answer(f"✅ Reklama guruhi qo'shildi: {reklama_group_username}")
                else:
                    await message.answer(f"⚠️ Reklama guruhi allaqachon mavjud: {reklama_group_username}")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_remove_reklama_group_id':
            reklama_group_username = message.text.strip()
            reklama_groups = load_reklama_groups()
            if reklama_group_username in reklama_groups:
                remove_reklama_group(reklama_group_username)
                await message.answer(f"❌ Reklama guruhi o'chirildi: {reklama_group_username}")
            else:
                await message.answer(f"⚠️ Reklama guruhi topilmadi: {reklama_group_username}")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_search_query':
            await search_user_func(message)
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_add_admin_id':
            try:
                admin_id = int(message.text)
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
                    conn.commit()
                await message.answer(f"✅ Admin qo'shildi: {admin_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_remove_admin_id':
            try:
                admin_id = int(message.text)
                if admin_id == user_id:
                    await message.answer("❌ O'zingizni adminlikdan chiqara olmaysiz!")
                else:
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute('DELETE FROM admins WHERE user_id = ?', (admin_id,))
                        conn.commit()
                    await message.answer(f"❌ Adminlikdan chiqarildi: {admin_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_pending_group_id':
            try:
                group_id = int(message.text.strip())
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM pending_orders_group')
                    cursor.execute('INSERT INTO pending_orders_group (group_id) VALUES (?)', (group_id,))
                    conn.commit()
                await message.answer(f"✅ Noma'lum zakazlar guruhi o'rnatildi: {group_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            except Exception as e:
                await message.answer(f"❌ Xatolik: {e}")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_blocked_group_id':
            try:
                group_id = int(message.text.strip())
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM blocked_orders_group')
                    cursor.execute('INSERT INTO blocked_orders_group (group_id) VALUES (?)', (group_id,))
                    conn.commit()
                await message.answer(f"✅ Blocklar guruhi o'rnatildi: {group_id}")
            except ValueError:
                await message.answer("❌ Noto'g'ri format! Raqam kiriting.")
            except Exception as e:
                await message.answer(f"❌ Xatolik: {e}")
            del user_states[user_id]
            return
        elif user_states[user_id] == 'waiting_order_header':
            if not is_admin(user_id):
                await message.answer("❌ Ruxsat yo'q!")
                return
            new_header = message.text.strip()
            if len(new_header) > 200:
                await message.answer("❌ Xabar juda uzun! 200 belgidan kam bo'lishi kerak.")
                return
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT OR REPLACE INTO settings (setting_key, setting_value)
                        VALUES (?, ?)
                    ''', ('order_message_header', new_header))
                    conn.commit()
                del user_states[user_id]
                await message.answer(
                    f"✅ <b>Buyurtma xabari muvaffaqiyatli o'zgartirildi!</b>\n\n"
                    f"<b>Yangi xabar:</b>\n"
                    f"<code>{new_header}</code>",
                    parse_mode='HTML'
                )
                logger.info(f"Admin {user_id} buyurtma xabarini o'zgartirdi")
            except Exception as e:
                await message.answer(f"❌ Xatolik: {e}")
                logger.error(f"Buyurtma xabari o'zgartirilishda xatolik: {e}")
            return
    
    # Qidiruv funksiyasi - faqat admin uchun
    if is_admin(message.from_user.id):
        await search_user_func(message)
        return

    # Oddiy foydalanuvchi o'z matnini yozsa - erkin zakaz sifatida qabul qilish
    free_text = message.text.strip()
    if not free_text:
        return
    taxi_users[user_id] = {"free_text": free_text}

    saved_phone = None
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT phone FROM users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                saved_phone = result[0]
    except Exception as e:
        logger.error(f"Kontakt tekshirishda xatolik: {e}")

    if saved_phone:
        taxi_users[user_id]["phone"] = saved_phone
        await send_free_order(message, message.from_user, saved_phone)
    else:
        await message.answer(
            "📞 Telefon raqamingizni yuboring:",
            reply_markup=phone_request_menu()
        )

async def search_user_func(message: types.Message):
    search_term = message.text.strip()
    
    # Barcha bazalardan qidirish
    db_files = ['zakazlar.db']
    for f in os.listdir('.'):
        if f.startswith('zakazlar_account') and f.endswith('.db'):
            db_files.append(f)
    
    all_results = []
    
    for db_file in db_files:
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            
            # Chat ID orqali qidirish
            if search_term.isdigit():
                cursor.execute("""
                    SELECT z.id, z.order_number, z.user_id, z.user_type, z.message, z.group_name, z.group_id, z.sana, u.user_name, u.username, u.phone 
                    FROM zakazlar z 
                    LEFT JOIN users u ON z.user_id = u.user_id 
                    WHERE z.user_id = ? 
                    ORDER BY z.sana DESC LIMIT 10
                """, (int(search_term),))
            else:
                # Ism orqali qidirish
                cursor.execute("""
                    SELECT z.id, z.order_number, z.user_id, z.user_type, z.message, z.group_name, z.group_id, z.sana, u.user_name, u.username, u.phone 
                    FROM zakazlar z 
                    LEFT JOIN users u ON z.user_id = u.user_id 
                    WHERE u.user_name LIKE ? OR z.message LIKE ?
                    ORDER BY z.sana DESC LIMIT 10
                """, (f"%{search_term}%", f"%{search_term}%"))
            
            results = cursor.fetchall()
            all_results.extend(results)
            conn.close()
        except Exception as e:
            logger.error(f"Error searching in {db_file}: {e}")
            continue
    results = all_results
    
    if not results:
        await message.answer(f"❌ '{search_term}' bo'yicha natija topilmadi")
        return
    
    await message.answer(f"🔍 Topildi: {len(results)} ta natija")
    
    for result in results:
        # Xabar va guruh havolalarini yaratish
        message_link = f"https://t.me/c/{str(result[6])[4:]}/1" if len(result) > 6 and str(result[6]).startswith('-100') else "#"
        group_link = f"https://t.me/c/{str(result[6])[4:]}" if len(result) > 6 and str(result[6]).startswith('-100') else "#"
        
        # Telefon raqam topish
        phone_patterns = [
            r'\+998\d{9}',
            r'998\d{9}',
            r'\d{9}',
            r'\d{2}\s\d{3}\s\d{2}\s\d{2}',
            r'\d{2}-\d{3}-\d{2}-\d{2}',
        ]
        phones = []
        
        # Users jadvalidan telefon
        if len(result) > 10 and result[10]:
            phones = [result[10]]
        else:
            # Xabar matnidan telefon
            for pattern in phone_patterns:
                found = re.findall(pattern, result[4] if len(result) > 4 else '')
                phones.extend(found)
                if phones:
                    break
        
        clean_message = result[4] if len(result) > 4 else ''
        if phones:
            for phone in phones:
                clean_message = clean_message.replace(phone, '')
        
        text_parts = []
        
        # User ID qo'shish
        if len(result) > 2 and result[2]:
            text_parts.append(f"🆔 ID: {result[2]}")
        
        # User name
        if len(result) > 8 and result[8]:
            if len(result) > 3 and result[3]:
                text_parts.append(f"👤 {result[8]} ({result[3]})")
            else:
                text_parts.append(f"👤 {result[8]}")
        
        if clean_message and clean_message.strip():
            text_parts.append(f"💬 {clean_message.strip()}")
        
        if len(result) > 5 and result[5] and group_link != "#":
            text_parts.append(f"🫂 <a href='{group_link}'>{result[5]}</a>")
        
        if len(result) > 7 and result[7] and isinstance(result[7], str):
            text_parts.append(f"📅 {result[7][:16]}")
        
        text = "\n\n".join(text_parts)
        
        buttons = []
        
        # Telefon tugmasi
        if phones:
            phone = phones[0].replace(' ', '').replace('-', '')
            if phone.startswith('998'):
                phone = '+' + phone
            elif not phone.startswith('+998'):
                phone = '+998' + phone
            buttons.append([InlineKeyboardButton(text=f"📞 {phone}", url=f"https://onmap.uz/tel/{phone}")])
        
        # Username tugmasi (akkaunt orqali) - faqat username bo'lsa
        if len(result) > 9 and result[9]:  # username
            buttons.append([InlineKeyboardButton(text=f"👤 @{result[9]}", url=f"https://t.me/{result[9]}")])
        
        # Bloklash/Blokdan chiqarish tugmasi
        if len(result) > 2 and result[2] and isinstance(result[2], int):
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM blocked_users WHERE user_id = ?', (result[2],))
                    is_blocked = cursor.fetchone() is not None
                
                if is_blocked:
                    buttons.append([InlineKeyboardButton(text="✅ Blokdan chiqarish", callback_data=f"unblock_{result[2]}")])
                else:
                    buttons.append([InlineKeyboardButton(text="🚫 Bloklash", callback_data=f"block_{result[2]}")])
            except Exception as e:
                logger.error(f"Bloklash holatini tekshirishda xatolik: {e}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        
        await message.answer(text, parse_mode='HTML', disable_web_page_preview=True, reply_markup=keyboard)

def load_groups():
    try:
        with open('groups.json', 'r') as f:
            return json.load(f)
    except:
        return []

def save_groups(groups):
    with open('groups.json', 'w') as f:
        json.dump(groups, f)

def load_order_groups():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT group_id FROM order_groups')
            return [row[0] for row in cursor.fetchall()]
    except:
        return []

def save_order_group(group_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO order_groups (group_id) VALUES (?)', (group_id,))
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving order group: {e}")
        raise

def remove_order_group(group_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM order_groups WHERE group_id = ?', (group_id,))
            conn.commit()
            logger.info(f"Order group removed: {group_id}")
    except Exception as e:
        logger.error(f"Error removing order group: {e}")
        raise

def load_reklama_groups():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT group_id FROM reklama_groups')
            groups = [row[0] for row in cursor.fetchall()]
        return groups
    except:
        return []

def save_reklama_group(group_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO reklama_groups (group_id) VALUES (?)', (group_id,))
            conn.commit()
            logger.info(f"Reklama group added: {group_id}")
    except Exception as e:
        logger.error(f"Error saving reklama group: {e}")
        raise

def remove_reklama_group(group_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM reklama_groups WHERE group_id = ?', (group_id,))
            conn.commit()
            logger.info(f"Reklama group removed: {group_id}")
    except Exception as e:
        logger.error(f"Error removing reklama group: {e}")
        raise

def _save_to_monitored(profile_id, group_id):
    """Guruh ID ni profil config ga saqlash (DB ga tegmasdan)"""
    config_file = f'account_config_{profile_id}.json'
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                cfg = json.load(f)
        else:
            cfg = {'account_id': profile_id, 'monitored_groups': []}
        monitored = cfg.get('monitored_groups', [])
        if group_id not in monitored:
            monitored.append(group_id)
            cfg['monitored_groups'] = monitored
            with open(config_file, 'w') as f:
                json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Config saqlash: {e}")


def block_user(user_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO blocked_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
            logger.info(f"User blocked: {user_id}")
    except Exception as e:
        logger.error(f"Error blocking user {user_id}: {e}")
        raise

def unblock_user(user_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM blocked_users WHERE user_id = ?', (user_id,))
            conn.commit()
            logger.info(f"User unblocked: {user_id}")
    except Exception as e:
        logger.error(f"Error unblocking user {user_id}: {e}")
        raise

# Admin menu
def admin_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Guruh boshqaruvi", callback_data="groups_menu")],
            [InlineKeyboardButton(text="👥 Foydalanuvchilar boshqaruvi", callback_data="users_menu")],
            [InlineKeyboardButton(text="👤 Profillar boshqaruvi", callback_data="profiles_menu")],
            [InlineKeyboardButton(text="💬 Xabar sozlamalari", callback_data="message_settings_menu")]
        ]
    )
    return keyboard

# Groups menu
def groups_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤝 Guruhga qo'shilish (ID)", callback_data="join_group_prompt")],
            [InlineKeyboardButton(text="📤 Buyurtma guruhlari", callback_data="list_order_groups")],
            [InlineKeyboardButton(text="➕ Buyurtma guruh qo'shish", callback_data="add_order_group_prompt")],
            [InlineKeyboardButton(text="➖ Buyurtma o'chirish", callback_data="remove_order_group_prompt")],
            [InlineKeyboardButton(text="🚫 Blocklar guruhi", callback_data="blocked_orders_group_menu")],
            [InlineKeyboardButton(text="❓ Noma'lum zakazlar guruhi", callback_data="pending_orders_group_menu")],
            [InlineKeyboardButton(text="📢 Reklama guruhlari (Haydovchilar)", callback_data="list_reklama_groups")],
            [InlineKeyboardButton(text="➕ Reklama guruh qo'shish", callback_data="add_reklama_group_prompt")],
            [InlineKeyboardButton(text="➖ Reklama guruh o'chirish", callback_data="remove_reklama_group_prompt")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")]
        ]
    )
    return keyboard

# Profillar menu
def profiles_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Profil qo'shish", callback_data="add_profile_prompt")],
            [InlineKeyboardButton(text="📋 Profillar ro'yxati", callback_data="list_profiles")],
            [InlineKeyboardButton(text="⚙️ Profil sozlamalari", callback_data="profile_settings_prompt")],
            [InlineKeyboardButton(text="➖ Profil o'chirish", callback_data="remove_profile_prompt")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")]
        ]
    )
    return keyboard

# Users menu
def users_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Bloklangan foydalanuvchilar", callback_data="list_blocked")],
            [InlineKeyboardButton(text="🚫 Foydalanuvchini bloklash", callback_data="block_user_prompt")],
            [InlineKeyboardButton(text="✅ Blokdan chiqarish", callback_data="unblock_user_prompt")],
            [InlineKeyboardButton(text="👑 Admin qo'shish", callback_data="add_admin_prompt")],
            [InlineKeyboardButton(text="❌ Adminlikdan chiqarish", callback_data="remove_admin_prompt")],
            [InlineKeyboardButton(text="📋 Adminlar ro'yxati", callback_data="list_admins")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")]
        ]
    )
    return keyboard

@dp.message(lambda message: message.text == "⚙️ Sozlamalar")
async def settings_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    # Bot guruh holatini tekshirish
    try:
        chat_info = await bot.get_chat(ORDER_GROUP_ID)
        bot_status = "✅ Bot guruhda"
        group_name = chat_info.title if hasattr(chat_info, 'title') else f"ID: {ORDER_GROUP_ID}"
    except Exception as e:
        bot_status = "❌ Bot guruhga kiritilmagan"
        group_name = f"ID: {ORDER_GROUP_ID}"
        logger.error(f"Bot guruh tekshiruvi: {e}")
    
    await message.answer(
        f"🔧 Admin Panel:\n\n"
        f"📤 Buyurtma guruhi: {group_name}\n"
        f"🤖 Bot holati: {bot_status}\n\n"
        f"⚠️ Agar bot guruhga kiritilmagan bo'lsa, botni guruhga admin sifatida qo'shing!",
        reply_markup=admin_menu()
    )

@dp.callback_query(lambda c: c.data == "admin_menu")
async def admin_menu_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🔧 Admin Panel:",
        reply_markup=admin_menu()
    )

@dp.callback_query(lambda c: c.data == "groups_menu")
async def groups_menu_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📋 Guruh boshqaruvi:",
        reply_markup=groups_menu()
    )

@dp.callback_query(lambda c: c.data == "users_menu")
async def users_menu_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "👥 Foydalanuvchilar boshqaruvi:",
        reply_markup=users_menu()
    )

@dp.callback_query(lambda c: c.data == "profiles_menu")
async def profiles_menu_handler(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "👤 Profillar boshqaruvi:\n\n"
        "Userbot bir nechta Telegram akkauntlardan ishlashi mumkin. "
        "Har bir profil guruhlarni kuzatadi va zakazlarni qabul qiladi.",
        reply_markup=profiles_menu()
    )

@dp.callback_query(lambda c: c.data == "add_profile_prompt")
async def add_profile_prompt_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    if not API_ID or not API_HASH:
        await callback.message.edit_text("❌ API_ID va API_HASH .env da sozlanishi kerak!")
        return
    user_states[callback.from_user.id] = 'waiting_profile_phone'
    await callback.message.edit_text(
        "➕ <b>Yangi profil qo'shish</b>\n\n"
        "Telefon raqamni yuboring (+998xxxxxxxxx):",
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data == "list_profiles")
async def list_profiles_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, phone, username, session_name, added_date FROM profiles ORDER BY id')
            rows = cursor.fetchall()
        if not rows:
            await callback.message.edit_text("📭 Hech qanday profil yo'q.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="profiles_menu")]]))
            return
        text = "📋 <b>Profillar:</b>\n\n"
        for r in rows:
            phone = r[1] or '—'
            username = f"@{r[2]}" if r[2] else '—'
            text += f"• ID:{r[0]} | {phone} | {username}\n"
        await callback.message.edit_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="profiles_menu")]]))
    except Exception as e:
        logger.error(f"Profillar ro'yxati: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(lambda c: c.data == "profile_settings_prompt")
async def profile_settings_prompt_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, phone FROM profiles WHERE is_active = 1 ORDER BY id')
            rows = cursor.fetchall()
        if not rows:
            await callback.message.edit_text("📭 Aktiv profil yo'q.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="profiles_menu")]]))
            return
        buttons = [[InlineKeyboardButton(text=f"⚙️ Profil #{r[0]} ({r[1]})", callback_data=f"profile_config_{r[0]}")] for r in rows]
        buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="profiles_menu")])
        await callback.message.edit_text("Sozlash uchun profilni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        logger.error(f"Profil sozlamalari: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(lambda c: c.data and c.data.startswith("profile_config_"))
async def profile_config_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        profile_id = int(callback.data.replace("profile_config_", ""))
        user_states[callback.from_user.id] = f'profile_config_{profile_id}'
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📤 Buyurtma guruhi o'zgartirish", callback_data=f"set_order_group_{profile_id}")],
                [InlineKeyboardButton(text="📋 Kuzatiladigan guruhlar", callback_data=f"list_monitored_{profile_id}")],
                [InlineKeyboardButton(text="🤝 Guruhga qo'shilish (ID)", callback_data=f"join_monitored_{profile_id}")],
                [InlineKeyboardButton(text="➖ Guruh o'chirish", callback_data=f"remove_monitored_{profile_id}")],
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="profile_settings_prompt")]
            ]
        )
        await callback.message.edit_text(f"⚙️ Profil #{profile_id} sozlamalari:", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Profil config: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(lambda c: c.data and c.data.startswith("set_order_group_"))
async def set_order_group_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        profile_id = int(callback.data.replace("set_order_group_", ""))
        user_states[callback.from_user.id] = f'waiting_order_group_{profile_id}'
        await callback.message.edit_text(f"Profil #{profile_id} uchun buyurtma guruhi ID sini yuboring:\nMisol: -1001234567890")
    except Exception as e:
        logger.error(f"Set order group: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(lambda c: c.data == "join_group_prompt")
async def join_group_prompt_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return
    user_states[callback.from_user.id] = 'waiting_join_group_link'
    await callback.message.edit_text(
        "🤝 <b>Guruhga qo'shilish</b>\n\n"
        "Guruh ID raqamini yuboring:\n"
        "💡 <i>Misol: -1001234567890</i>",
        parse_mode='HTML'
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("join_monitored_"))
async def join_monitored_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        profile_id = int(callback.data.replace("join_monitored_", ""))
        user_states[callback.from_user.id] = f'waiting_join_monitored_{profile_id}'
        await callback.message.edit_text(
            f"🤝 <b>Profil #{profile_id}: Guruh ID qo'shish</b>\n\n"
            "Guruh ID raqamini yuboring:\n"
            "💡 <i>Misol: -1001234567890</i>",
            parse_mode='HTML'
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ Xatolik: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("list_monitored_"))
async def list_monitored_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        profile_id = int(callback.data.replace("list_monitored_", ""))
        config_file = f'account_config_{profile_id}.json'
        
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
            groups = config.get('monitored_groups', [])
        else:
            groups = []
        
        if groups:
            text = f"📋 Profil #{profile_id} kuzatiladigan guruhlar:\n\n"
            for i, g in enumerate(groups, 1):
                text += f"{i}. {g}\n"
        else:
            text = f"📭 Profil #{profile_id} uchun kuzatiladigan guruh yo'q"
        
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data=f"profile_config_{profile_id}")]]))
    except Exception as e:
        logger.error(f"List monitored: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(lambda c: c.data == "remove_profile_prompt")
async def remove_profile_prompt_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, phone, username FROM profiles ORDER BY id')
            rows = cursor.fetchall()
        if not rows:
            await callback.message.edit_text("📭 O'chirish uchun profil yo'q.")
            return
        buttons = [[InlineKeyboardButton(text=f"❌ {r[1] or r[2] or f'Profil #{r[0]}'}", callback_data=f"remove_profile_{r[0]}")] for r in rows]
        buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="profiles_menu")])
        await callback.message.edit_text("O'chiriladigan profilni tanlang:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    except Exception as e:
        logger.error(f"Profil o'chirish: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

@dp.callback_query(lambda c: c.data == "list_groups")
async def list_groups_handler(callback: types.CallbackQuery):
    groups = load_groups()
    if groups:
        groups_text = "📋 Kuzatilayotgan guruhlar:\n" + "\n".join([f"• {g}" for g in groups])
    else:
        groups_text = "📭 Kuzatilayotgan guruhlar yo'q"
    await callback.message.edit_text(groups_text)

@dp.callback_query(lambda c: c.data == "order_group_info")
async def order_group_info_handler(callback: types.CallbackQuery):
    ORDER_GROUP_ID = os.getenv('ORDER_GROUP_ID')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM zakazlar")
        total_orders = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE DATE(sana) = DATE('now')")
        today_orders = cursor.fetchone()[0]
    
    text = f"📤 Buyurtma guruhi:\n\n"
    text += f"🆔 ID: {ORDER_GROUP_ID}\n"
    text += f"📈 Jami yuborilgan: {total_orders}\n"
    text += f"📅 Bugun yuborilgan: {today_orders}"
    
    await callback.message.edit_text(text)

@dp.callback_query(lambda c: c.data == "list_blocked")
async def list_blocked_handler(callback: types.CallbackQuery):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM blocked_users')
        blocked = [str(row[0]) for row in cursor.fetchall()]
    
    if blocked:
        text = "🚫 Bloklangan:\n" + "\n".join(blocked)
    else:
        text = "📭 Bloklangan yo'q"
    await callback.message.edit_text(text)

@dp.callback_query(lambda c: c.data == "add_group_prompt")
async def add_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_group_id'
    await callback.message.edit_text("Guruh ID sini yuboring:\nMisol: -1001234567890")

@dp.callback_query(lambda c: c.data == "remove_group_prompt")
async def remove_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_remove_group_id'
    await callback.message.edit_text("O'chirish uchun guruh ID sini yuboring:\nMisol: -1001234567890")

@dp.callback_query(lambda c: c.data == "block_user_prompt")
async def block_user_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_block_user_id'
    await callback.message.edit_text("Bloklash uchun foydalanuvchi ID sini yuboring:\nMisol: 123456789")

@dp.callback_query(lambda c: c.data == "unblock_user_prompt")
async def unblock_user_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_unblock_user_id'
    await callback.message.edit_text("Blokdan chiqarish uchun foydalanuvchi ID sini yuboring:\nMisol: 123456789")

def unblock_user(user_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM blocked_users WHERE user_id = ?', (user_id,))
            conn.commit()
            logger.info(f"User unblocked: {user_id}")
    except Exception as e:
        logger.error(f"Error unblocking user {user_id}: {e}")
        raise

@dp.callback_query(lambda c: c.data == "list_order_groups")
async def list_order_groups_handler(callback: types.CallbackQuery):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT group_id FROM order_groups')
            order_groups = [row[0] for row in cursor.fetchall()]
        
        if order_groups:
            groups_text = f"📤 Buyurtma guruhlari ({len(order_groups)} ta):\n\n"
            for i, gid in enumerate(order_groups, 1):
                # Guruh nomini olishga harakat qilish
                try:
                    chat_info = await bot.get_chat(gid)
                    group_name = chat_info.title if hasattr(chat_info, 'title') else f"ID: {gid}"
                    bot_status = "✅"
                except:
                    group_name = f"ID: {gid}"
                    bot_status = "❌"
                
                groups_text += f"{i}. {bot_status} {group_name}\n"
                groups_text += f"   <code>{gid}</code>\n\n"
            
            groups_text += "✅ - Bot guruhda\n❌ - Bot guruhga kiritilmagan"
        else:
            groups_text = "📭 Buyurtma guruhlari yo'q"
        
        await callback.message.edit_text(
            groups_text, 
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="groups_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"List order groups error: {e}")
        await callback.message.edit_text("❌ Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data == "add_order_group_prompt")
async def add_order_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_add_order_group_id'
    await callback.message.edit_text("Buyurtma guruhi ID sini yuboring:\nMisol: -1001234567890")

@dp.callback_query(lambda c: c.data == "remove_order_group_prompt")
async def remove_order_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_remove_order_group_id'
    await callback.message.edit_text("O'chirish uchun buyurtma guruhi ID sini yuboring:\nMisol: -1001234567890")

@dp.callback_query(lambda c: c.data == "list_reklama_groups")
async def list_reklama_groups_handler(callback: types.CallbackQuery):
    reklama_groups = load_reklama_groups()
    if reklama_groups:
        groups_text = "📢 Reklama guruhlari (Haydovchilar):\n" + "\n".join([f"• {g}" for g in reklama_groups])
    else:
        groups_text = "📭 Reklama guruhlari yo'q"
    await callback.message.edit_text(groups_text)

@dp.callback_query(lambda c: c.data == "add_reklama_group_prompt")
async def add_reklama_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_add_reklama_group_id'
    await callback.message.edit_text("Reklama guruhi Username yoki ID sini yuboring:\nMisol: @vijdontaxireklama yoki -1001234567890")

@dp.callback_query(lambda c: c.data == "remove_reklama_group_prompt")
async def remove_reklama_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_remove_reklama_group_id'
    await callback.message.edit_text("O'chirish uchun reklama guruhi Username yoki ID sini yuboring:\nMisol: @vijdontaxireklama")

@dp.callback_query(lambda c: c.data == "add_admin_prompt")
async def add_admin_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_add_admin_id'
    await callback.message.edit_text("Admin qo'shish uchun foydalanuvchi ID sini yuboring:\nMisol: 123456789")

@dp.callback_query(lambda c: c.data == "remove_admin_prompt")
async def remove_admin_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_remove_admin_id'
    await callback.message.edit_text("Adminlikdan chiqarish uchun foydalanuvchi ID sini yuboring:\nMisol: 123456789")

@dp.callback_query(lambda c: c.data == "list_admins")
async def list_admins_handler(callback: types.CallbackQuery):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM admins')
            admins = [str(row[0]) for row in cursor.fetchall()]
        
        if admins:
            text = "👑 Adminlar:\n" + "\n".join([f"• {admin_id}" for admin_id in admins])
        else:
            text = "📭 Adminlar yo'q"
        await callback.message.edit_text(text)
    except Exception as e:
        logger.error(f"Adminlar ro'yxatini olishda xatolik: {e}")
        await callback.message.edit_text("❌ Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data and c.data.startswith("remove_profile_"))
async def remove_profile_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    try:
        profile_id = int(callback.data.replace("remove_profile_", ""))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT session_name FROM profiles WHERE id = ?', (profile_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute('DELETE FROM profiles WHERE id = ?', (profile_id,))
                conn.commit()
                session_file = f"{row[0]}.session"
                if os.path.exists(session_file):
                    try:
                        os.remove(session_file)
                    except:
                        pass
                await callback.message.edit_text(f"✅ Profil #{profile_id} o'chirildi. Userbotni qayta ishga tushiring.")
            else:
                await callback.message.edit_text("❌ Profil topilmadi.")
    except Exception as e:
        logger.error(f"Profil o'chirish: {e}")
        await callback.message.edit_text(f"❌ Xatolik: {e}")

async def send_demo_orders():
    """Bot ishga tushganda 10 ta demo zakaz yuborish"""
    print("🔍 Guruhlarni tekshirish...")
    
    # Asosiy guruhni tekshirish
    try:
        chat_info = await bot.get_chat(ORDER_GROUP_ID)
        print(f"✅ Asosiy guruh mavjud: {chat_info.title if hasattr(chat_info, 'title') else ORDER_GROUP_ID}")
    except Exception as e:
        print(f"❌ Asosiy guruhga kirish imkoni yo'q: {e}")
        logger.error(f"Asosiy guruh tekshiruvi: {e}")
        return
    print("📋 Demo zakazlar tayyorlanmoqda...")
    
    demo_orders = [
        {"name": "Aziz", "phone": "+998901234567", "message": "Toshkentga ketish kerak"},
        {"name": "Dilnoza", "phone": "+998907654321", "message": "Samarqandga borish kerak"},
        {"name": "Bobur", "phone": "+998912345678", "message": "Namanganga yo'lovchi kerak"},
        {"name": "Malika", "phone": "+998923456789", "message": "Farg'onaga ketmoqchiman"},
        {"name": "Jasur", "phone": "+998934567890", "message": "Andijonga borish kerak"},
        {"name": "Nargiza", "phone": "+998945678901", "message": "Buxoroga yo'lovchi kerak"},
        {"name": "Otabek", "phone": "+998956789012", "message": "Jizzaxga ketish kerak"},
        {"name": "Sevara", "phone": "+998967890123", "message": "Navoiyga bormoqchiman"},
        {"name": "Rustam", "phone": "+998978901234", "message": "Xorazmga yo'lovchi kerak"},
        {"name": "Gulnora", "phone": "+998989012345", "message": "Qashqadaryoga ketish kerak"}
    ]
    
    successful_orders = 0
    
    for i, order in enumerate(demo_orders, 1):
        print(f"📤 Demo zakaz #{i} yuborilmoqda...")
        try:
            # Qo'ngiroq qilish tugmasi
            buttons = [
                [InlineKeyboardButton(text=f"📞 {order['phone']}", url=f"https://onmap.uz/tel/{order['phone']}")]
            ]
            
            # Demo zakazda username bo'lmagani uchun xabar ko'rish tugmasi
            buttons.append([InlineKeyboardButton(text="💬 Xabarni ko'rish", callback_data=f"view_demo_order_{i}")])
            
            call_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            # Keyingi zakaz raqamini olish
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT COALESCE(MAX(order_number), 0) + 1 FROM zakazlar')
                    order_number = cursor.fetchone()[0]
            except:
                order_number = i
            
            # Demo zakaz xabari
            order_message = (
                f"🚕 <b>ZAKAZ #{order_number}</b>\n"
                f"{'='*25}\n\n"
                f"👤 <b>Mijoz:</b> {order['name']}\n"
                f"📞 <b>Telefon:</b> {order['phone']}\n"
                f"💬 <b>Xabar:</b> {order['message']}\n\n"
                f"⚠️ <i>Bu demo zakaz - test maqsadida</i>"
            )
            
            # Asosiy guruhga yuborish
            try:
                await bot.send_message(
                    chat_id=ORDER_GROUP_ID,
                    text=order_message,
                    parse_mode='HTML',
                    reply_markup=call_keyboard
                )
                logger.info(f"Demo zakaz #{i} asosiy guruhga yuborildi")
            except Exception as e:
                logger.error(f"Demo zakaz #{i} asosiy guruhga yuborishda xatolik: {e}")
            
            # Qo'shimcha guruhlarga ham yuborish
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT group_id FROM order_groups WHERE group_id != ? LIMIT 3', (ORDER_GROUP_ID,))  # Faqat 3 ta guruhga
                    order_groups = [row[0] for row in cursor.fetchall()]
                    
                    for group_id in order_groups:
                        try:
                            await bot.send_message(
                                chat_id=group_id,
                                text=order_message,
                                parse_mode='HTML',
                                reply_markup=call_keyboard
                            )
                            logger.info(f"Demo zakaz #{i} guruhga yuborildi: {group_id}")
                        except Exception as group_error:
                            logger.warning(f"Guruh {group_id} ga yuborib bo'lmadi: {group_error}")
                            # Agar bot guruhdan chiqarilgan bo'lsa, uni bazadan o'chirish
                            if "kicked" in str(group_error).lower() or "forbidden" in str(group_error).lower():
                                cursor.execute('DELETE FROM order_groups WHERE group_id = ?', (group_id,))
                                conn.commit()
                                logger.info(f"Guruh {group_id} bazadan o'chirildi")
            except Exception as e:
                logger.error(f"Demo zakazni qo'shimcha guruhlarga yuborishda xatolik: {e}")
            
            # Demo zakazni bazaga ham saqlash
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO zakazlar (order_number, user_id, user_type, message, group_name, group_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (order_number, 0, '🚕 Demo', order['message'], 'Demo Group', ORDER_GROUP_ID))
                    conn.commit()
            except Exception as e:
                logger.error(f"Demo zakazni bazaga saqlashda xatolik: {e}")
            
            successful_orders += 1
            print(f"✅ Demo zakaz #{i} muvaffaqiyatli yuborildi")
            
            # Har bir zakaz orasida 1 soniya kutish
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"❌ Demo zakaz #{i} xatolik: {e}")
            logger.error(f"Demo zakaz #{i} umumiy xatolik: {e}")
            continue  # Keyingi zakazga o'tish
    
    print(f"✅ {successful_orders}/10 demo zakaz muvaffaqiyatli yuborildi")
    logger.info(f"Demo zakazlar yuborish tugadi: {successful_orders}/10")

# ========== XABAR SOZLAMALARI ==========

@dp.callback_query(lambda c: c.data == "message_settings_menu")
async def message_settings_menu_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        logger.warning(f"Non-admin {callback.from_user.id} tried to access message settings")
        return
    
    logger.info(f"Admin {callback.from_user.id} accessed message settings")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT setting_value FROM settings WHERE setting_key = ?', 
                         ('order_message_header',))
            result = cursor.fetchone()
            current_header = result[0] if result else '🚕 ASSALOMU ALEYKUM HURMATLI TAXI HAYDOVCHILARI 🆕 YANGI BUYURTMA KELDI!'
    except Exception as e:
        logger.error(f"Error fetching settings: {e}")
        current_header = '🚕 ASSALOMU ALEYKUM HURMATLI TAXI HAYDOVCHILARI 🆕 YANGI BUYURTMA KELDI!'
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Buyurtma xabari o'zgartirilish", callback_data="edit_order_header")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")]
        ]
    )
    
    await callback.message.edit_text(
        f"💬 <b>Xabar sozlamalari</b>\n\n"
        f"<b>Hozirgi buyurtma xabari:</b>\n"
        f"<code>{current_header}</code>",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data == "edit_order_header")
async def edit_order_header_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    user_states[callback.from_user.id] = 'waiting_order_header'
    await callback.message.edit_text(
        "✏️ <b>Yangi buyurtma xabari yuboring:</b>\n\n"
        "Masalan: 🚕 ASSALOMU ALEYKUM HURMATLI TAXI HAYDOVCHILARI 🆕 YANGI BUYURTMA KELDI!",
        parse_mode='HTML'
    )

async def main():
    print("🤖 Bot ishga tushmoqda...")
    
    # Ma'lumotlar bazasini ishga tushirish
    init_keywords_db()
    print("✅ Keywords bazasi tayyor")
    
    # Main.py ni avtomatik ishga tushirish
    import subprocess
    import sys
    subprocess.Popen([sys.executable, 'main.py'])
    print("📱 Userbot ham ishga tushdi")
    
    try:
        await dp.start_polling(bot)
    finally:
        pass

if __name__ == "__main__":
    asyncio.run(main())




@dp.callback_query(lambda c: c.data == "pending_orders_group_menu")
async def pending_orders_group_menu_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT group_id FROM pending_orders_group LIMIT 1')
            result = cursor.fetchone()
            current_group = result[0] if result else "O'rnatilmagan"
    except:
        current_group = "O'rnatilmagan"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Noma'lum zakazlar guruhini o'rnatish", callback_data="set_pending_group")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="groups_menu")]
        ]
    )
    
    await callback.message.edit_text(
        f"❓ <b>Noma'lum zakazlar guruhi</b>\n\n"
        f"Na yo'lovchi na haydovchi deb aniqlanmagan zakazlar shu guruhga tushadi.\n\n"
        f"<b>Hozirgi guruh:</b> <code>{current_group}</code>",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data == "set_pending_group")
async def set_pending_group_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    user_states[callback.from_user.id] = 'waiting_pending_group_id'
    await callback.message.edit_text(
        "❓ <b>Noma'lum zakazlar guruhini o'rnatish</b>\n\n"
        "Guruh ID sini yuboring:\n"
        "Misol: -1001234567890",
        parse_mode='HTML'
    )


@dp.callback_query(lambda c: c.data == "blocked_orders_group_menu")
async def blocked_orders_group_menu_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT group_id FROM blocked_orders_group LIMIT 1')
            result = cursor.fetchone()
            current_group = result[0] if result else "O'rnatilmagan"
    except:
        current_group = "O'rnatilmagan"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Blocklar guruhini o'rnatish", callback_data="set_blocked_group")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="groups_menu")]
        ]
    )
    
    await callback.message.edit_text(
        f"🚫 <b>Blocklar guruhi</b>\n\n"
        f"Bloklangan odamlardan kelgan zakazlar shu guruhga tushadi.\n\n"
        f"<b>Hozirgi guruh:</b> <code>{current_group}</code>",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data == "set_blocked_group")
async def set_blocked_group_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    user_states[callback.from_user.id] = 'waiting_blocked_group_id'
    await callback.message.edit_text(
        "🚫 <b>Blocklar guruhini o'rnatish</b>\n\n"
        "Guruh ID sini yuboring:\n"
        "Misol: -1001234567890",
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data.startswith("send_as_passenger_"))
async def send_as_passenger_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    try:
        parts = callback.data.replace("send_as_passenger_", "").split("_")
        user_id = int(parts[0])
        order_number = int(parts[1])
        
        # Zakazni yo'lovchi sifatida yuborish
        await send_order_as_type(user_id, order_number, "passenger", callback)
        
    except Exception as e:
        logger.error(f"Send as passenger error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data.startswith("send_as_driver_"))
async def send_as_driver_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    try:
        parts = callback.data.replace("send_as_driver_", "").split("_")
        user_id = int(parts[0])
        order_number = int(parts[1])
        
        # Zakazni haydovchi sifatida yuborish (reklama guruhlarga)
        await send_order_as_type(user_id, order_number, "driver", callback)
        
    except Exception as e:
        logger.error(f"Send as driver error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data.startswith("ignore_order_"))
async def ignore_order_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    try:
        parts = callback.data.replace("ignore_order_", "").split("_")
        user_id = int(parts[0])
        order_number = int(parts[1])
        
        await callback.answer("✅ Zakaz ignore qilindi!")
        await callback.message.edit_text(
            f"❌ <b>ZAKAZ #{order_number} IGNORE QILINDI</b>\n\n"
            f"Admin tomonidan ignore qilindi: {callback.from_user.first_name}",
            parse_mode='HTML'
        )
        logger.info(f"Admin {callback.from_user.id} zakaz #{order_number} ni ignore qildi")
        
    except Exception as e:
        logger.error(f"Ignore order error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")

async def send_order_as_type(user_id, order_number, order_type, callback):
    """Zakazni belgilangan tur sifatida yuborish"""
    try:
        # Foydalanuvchi ma'lumotlarini olish
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT z.message, u.user_name, u.phone, u.username
                FROM zakazlar z
                LEFT JOIN users u ON z.user_id = u.user_id
                WHERE z.order_number = ? AND z.user_id = ?
                LIMIT 1
            ''', (order_number, user_id))
            result = cursor.fetchone()
        
        if not result:
            await callback.answer("❌ Zakaz topilmadi")
            return
        
        message_text, user_name, phone, username = result
        
        if order_type == "passenger":
            # Yo'lovchi sifatida buyurtma guruhlariga yuborish
            header = "🚕 <b>ASSALOMU ALEYKUM HURMATLI TAXI HAYDOVCHILARI 🆕</b>"
            order_msg = (
                f"{header} <b>#{order_number}</b>\n\n"
                f"👤 <a href='tg://user?id={user_id}'>{user_name or 'Foydalanuvchi'}</a>\n"
                f"💬 <i>{message_text}</i>\n\n"
                f"⚠️ <i>Admin tomonidan yo'lovchi sifatida yuborildi</i>"
            )
            
            # Tugmalar
            buttons = []
            if phone:
                p = phone.replace(' ', '').replace('-', '')
                if not p.startswith('+'): p = '+998' + p if p.startswith('998') else '+' + p
                buttons.append([InlineKeyboardButton(text=f"📞 {p}", url=f"https://onmap.uz/tel/{p}")])
            if username:
                buttons.append([InlineKeyboardButton(text=f"👤 @{username}", url=f"https://t.me/{username}")])
            buttons.append([InlineKeyboardButton(text="💬 Lichkaga yozish", callback_data=f"send_private_{user_id}_{order_number}")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
            
            # Buyurtma guruhlariga yuborish
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM order_groups')
                order_groups = [row[0] for row in cursor.fetchall()]
            
            for group_id in order_groups:
                try:
                    await bot.send_message(
                        chat_id=group_id,
                        text=order_msg,
                        parse_mode='HTML',
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"Guruh {group_id} ga yuborish xato: {e}")
            
            await callback.answer("✅ Yo'lovchi sifatida yuborildi!")
            
        elif order_type == "driver":
            # Haydovchi sifatida reklama guruhlarga yuborish
            reklama_msg = (
                f"🚕 <b>Assalomu alaykum hurmatli haydovchilar</b>\n\n"
                f"<i>{message_text}</i>\n\n"
                f"<b>Buyurtmalar guruhiga qo'shilish uchun 👇</b>"
            )
            
            admin_link = f"https://t.me/{HAYDOVCHI_ADMIN_USERNAME}" if HAYDOVCHI_ADMIN_USERNAME else "#"
            reklama_buttons = [[InlineKeyboardButton(text="👨‍💻 Operator bilan bog'lanish", url=admin_link)]]
            reklama_keyboard = InlineKeyboardMarkup(inline_keyboard=reklama_buttons)
            
            # Reklama guruhlarga yuborish
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM reklama_groups')
                reklama_groups = [row[0] for row in cursor.fetchall()]
            
            for group_id in reklama_groups:
                try:
                    await bot.send_message(
                        chat_id=group_id,
                        text=reklama_msg,
                        parse_mode='HTML',
                        reply_markup=reklama_keyboard
                    )
                except Exception as e:
                    logger.error(f"Reklama guruh {group_id} ga yuborish xato: {e}")
            
            await callback.answer("✅ Haydovchi sifatida reklama guruhlarga yuborildi!")
        
        # Xabarni yangilash
        await callback.message.edit_text(
            f"✅ <b>ZAKAZ #{order_number} YUBORILDI</b>\n\n"
            f"Tur: {'Yo\'lovchi' if order_type == 'passenger' else 'Haydovchi'}\n"
            f"Admin: {callback.from_user.first_name}",
            parse_mode='HTML'
        )
        
        logger.info(f"Admin {callback.from_user.id} zakaz #{order_number} ni {order_type} sifatida yubordi")
        
    except Exception as e:
        logger.error(f"Send order as type error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")


@dp.callback_query(lambda c: c.data.startswith("view_message_"))
async def view_message_handler(callback: types.CallbackQuery):
    try:
        parts = callback.data.replace("view_message_", "").split("_")
        user_id = int(parts[0])
        order_number = int(parts[1])
        
        # Zakazni bazadan olish
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT z.message, u.user_name, z.group_name, z.sana
                FROM zakazlar z
                LEFT JOIN users u ON z.user_id = u.user_id
                WHERE z.order_number = ? AND z.user_id = ?
                LIMIT 1
            ''', (order_number, user_id))
            result = cursor.fetchone()
        
        if not result:
            await callback.answer("❌ Zakaz topilmadi")
            return
        
        message_text, user_name, group_name, sana = result
        
        # Xabar ma'lumotlarini ko'rsatish
        message_info = f"📄 <b>Zakaz #{order_number} xabari:</b>\n\n"
        message_info += f"👤 <b>Foydalanuvchi:</b> {user_name or 'Noma\'lum'}\n"
        message_info += f"🫂 <b>Guruh:</b> {group_name or 'Noma\'lum'}\n"
        message_info += f"📅 <b>Sana:</b> {sana[:16] if sana else 'Noma\'lum'}\n\n"
        message_info += f"💬 <b>Xabar matni:</b>\n<i>{message_text}</i>"
        
        await callback.message.answer(message_info, parse_mode='HTML')
        await callback.answer("✅ Xabar ko'rsatildi!")
        
    except Exception as e:
        logger.error(f"View message error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")


@dp.callback_query(lambda c: c.data.startswith("send_private_"))
async def send_private_message_handler(callback: types.CallbackQuery):
    try:
        parts = callback.data.replace("send_private_", "").split("_")
        user_id = int(parts[0])
        order_number = int(parts[1])
        
        # Zakazni bazadan olish
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT z.message, u.user_name, u.phone, u.username
                FROM zakazlar z
                LEFT JOIN users u ON z.user_id = u.user_id
                WHERE z.order_number = ? AND z.user_id = ?
                LIMIT 1
            ''', (order_number, user_id))
            result = cursor.fetchone()
        
        if not result:
            await callback.answer("❌ Zakaz topilmadi")
            return
        
        message_text, user_name, phone, username = result
        
        # Mijoz ma'lumotlarini ko'rsatish
        contact_info = f"👤 <b>Mijoz ma'lumotlari:</b>\n\n"
        contact_info += f"📝 Ism: {user_name or 'Noma\'lum'}\n"
        if username:
            contact_info += f"🤙 Username: @{username}\n"
        if phone:
            contact_info += f"📞 Telefon: +{phone}\n"
        contact_info += f"\n💬 <b>Zakaz #{order_number}:</b>\n<i>{message_text}</i>\n\n"
        contact_info += f"ℹ️ Mijozga yozish uchun yuqoridagi ma'lumotlardan foydalaning."
        
        await callback.message.answer(contact_info, parse_mode='HTML')
        await callback.answer("✅ Mijoz ma'lumotlari ko'rsatildi!")
        
    except Exception as e:
        logger.error(f"Send private message error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")


@dp.callback_query(lambda c: c.data.startswith("send_blocked_"))
async def send_blocked_order_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!")
        return
    
    try:
        parts = callback.data.replace("send_blocked_", "").split("_")
        user_id = int(parts[0])
        order_number = int(parts[1])
        
        # Zakazni bazadan olish
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT z.message, u.user_name, u.phone, u.username
                FROM zakazlar z
                LEFT JOIN users u ON z.user_id = u.user_id
                WHERE z.order_number = ? AND z.user_id = ?
                LIMIT 1
            ''', (order_number, user_id))
            result = cursor.fetchone()
        
        if not result:
            await callback.answer("❌ Zakaz topilmadi")
            return
        
        message_text, user_name, phone, username = result
        
        # Buyurtma guruhga yuborish
        order_msg = (
            f"🚕 <b>ZAKAZ #{order_number}</b>\n\n"
            f"👤 {user_name or 'Foydalanuvchi'}\n"
            f"💬 {message_text}\n\n"
            f"⚠️ <i>Bloklangan odamdan kelgan zakaz (admin tomonidan tasdiqlangan)</i>"
        )
        
        buttons = []
        if phone:
            p = phone.replace(' ', '').replace('-', '')
            if not p.startswith('+'): p = '+998' + p if p.startswith('998') else '+' + p
            buttons.append([InlineKeyboardButton(text=f"📞 {p}", url=f"https://onmap.uz/tel/{p}")])
        if username:
            buttons.append([InlineKeyboardButton(text=f"👤 @{username}", url=f"https://t.me/{username}")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
        
        await bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=order_msg,
            parse_mode='HTML',
            reply_markup=keyboard
        )
        
        await callback.answer("✅ Zakaz buyurtma guruhga yuborildi!")
        await callback.message.edit_reply_markup(reply_markup=None)
        logger.info(f"Admin {callback.from_user.id} bloklangan zakaz #{order_number} ni yubordi")
        
    except Exception as e:
        logger.error(f"Send blocked order error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")


@dp.callback_query(lambda c: c.data.startswith("fast_send_"))
async def fast_send_handler(callback: types.CallbackQuery):
    try:
        user_id_from_data = callback.data.replace("fast_send_", "")
        original_text = callback.message.text or callback.message.caption or ""
        
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT group_id FROM order_groups')
            order_groups = [row[0] for row in cursor.fetchall()]
        
        if not order_groups:
            order_groups = [ORDER_GROUP_ID]
        
        # Yuborish tugmasini olib tashlagan keyboard
        new_buttons = []
        if callback.message.reply_markup:
            for row in callback.message.reply_markup.inline_keyboard:
                new_row = [btn for btn in row if not (btn.callback_data and btn.callback_data.startswith("fast_send_"))]
                if new_row:
                    new_buttons.append(new_row)
        keyboard = InlineKeyboardMarkup(inline_keyboard=new_buttons) if new_buttons else None
        
        sent = 0
        for gid in order_groups:
            try:
                await bot.copy_message(
                    chat_id=gid,
                    from_chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    reply_markup=keyboard
                )
                sent += 1
            except Exception as e:
                logger.error(f"Fast send guruh {gid}: {e}")
        
        await callback.answer(f"✅ {sent} ta guruhga yuborildi!")
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.error(f"Fast send error: {e}")
        await callback.answer("❌ Xatolik yuz berdi")
