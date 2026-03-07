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
import html
import logging
from contextlib import contextmanager

load_dotenv()

# ANSI Ranglar
G = "\033[92m"  # Yashil
R = "\033[91m"  # Qizil
Y = "\033[93m"  # Sariq
B = "\033[94m"  # Moviy
W = "\033[0m"   # Reset

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
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            username TEXT,
            phone TEXT,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS zakazlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number INTEGER,
            user_id INTEGER,
            user_type TEXT,
            message TEXT,
            group_name TEXT,
            group_id INTEGER,
            sana DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
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
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Default sozlamalarni qo'shish
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', 
                  ('order_header', '🚕 <b>ASSALOMU ALAYKUM HURMATLI VIJDON TAXI HAYDOVCHILARI</b> 🆕 <b>YANGI BUYURTMA KELDI!</b>'))
    
    # Default adminlarni qo'shish
    for admin_id in ADMIN_IDS:
        cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
    
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

def get_incomplete_orders(status='pending'):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, order_number, user_name, original_message, missing_info, group_name, created_date
                FROM incomplete_orders 
                WHERE status = ?
                ORDER BY created_date DESC
                LIMIT 20
            ''', (status,))
            orders = cursor.fetchall()
            return orders
    except Exception as e:
        logger.error(f"Error getting incomplete orders: {e}")
        return []

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

def get_incomplete_order_by_id(order_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM incomplete_orders WHERE id = ?
            ''', (order_id,))
            order = cursor.fetchone()
            return order
    except Exception as e:
        logger.error(f"Error getting incomplete order by ID: {e}")
        return None

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



# Settings functions
def get_setting(key, default=None):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row[0] if row else default
    except Exception as e:
        logger.error(f"Error getting setting {key}: {e}")
        return default

def set_setting(key, value):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
            conn.commit()
            logger.info(f"Setting updated: {key} = {value}")
            return True
    except Exception as e:
        logger.error(f"Error setting {key}: {e}")
        return False



# Asosiy menu
def main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="🔍 Qidiruv")],
            [KeyboardButton(text="📝 So'zlar qo'shish"), KeyboardButton(text="⚙️ Sozlamalar")],
            [KeyboardButton(text="📋 Guruh statistikasi"), KeyboardButton(text="🕜 Oxirgi 10 ta zakaz")],
            [KeyboardButton(text="⚠️ To'liq bo'lmagan zakazlar"), KeyboardButton(text="✅ Zakazni to'ldirish")],
            [KeyboardButton(text="🚫 Bloklangan foydalanuvchilar"), KeyboardButton(text="📱 Profil qo'shish")],
            [KeyboardButton(text="🔐 Admin qo'shish"), KeyboardButton(text="📈 Xatoliklar")]
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
            [InlineKeyboardButton(text="📋 Barcha so'zlar", callback_data="list_words")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]
        ]
    )
    return keyboard

def is_admin(user_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,))
            return cursor.fetchone() is not None
    except:
        return user_id in ADMIN_IDS

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
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM zakazlar")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE user_type LIKE '%Haydovchi%'")
    drivers = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE user_type LIKE '%Yolovchi%' OR user_type = '' OR user_type IS NULL")
    passengers = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE DATE(sana) = DATE('now')")
    today = cursor.fetchone()[0]
    
    conn.close()
    
    await message.answer(
        f"📊 Statistika:\n\n"
        f"📈 Jami zakazlar: {total}\n"
        f"🚗 Haydovchilar: {drivers}\n"
        f"🙋 Yo'lovchilar: {passengers}\n"
        f"📅 Bugun: {today}"
    )

@dp.message(lambda message: message.text == "📋 Guruh statistikasi")
async def group_stats_handler(message: types.Message):
    conn = sqlite3.connect('zakazlar.db')
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
    conn.close()
    
    if not results:
        await message.answer("📝 Guruh statistikasi yo'q")
        return
    
    text = "📋 Guruh statistikasi:\n\n"
    for i, (group_name, count) in enumerate(results, 1):
        text += f"{i}. {group_name}: {count} ta zakaz\n"
    
    await message.answer(text)

@dp.message(lambda message: message.text == "🕜 Oxirgi 10 ta zakaz")
async def passengers_only_handler(message: types.Message):
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    
    # Фақат йўловчилар филтри - user_type орқали
    cursor.execute("""
        SELECT z.id, z.order_number, z.user_id, z.user_type, z.message, z.group_name, z.group_id, z.sana, u.user_name, u.username, u.phone 
        FROM zakazlar z 
        LEFT JOIN users u ON z.user_id = u.user_id 
        WHERE z.user_type LIKE '%Yolovchi%' OR z.user_type = '' OR z.user_type IS NULL
        ORDER BY z.sana DESC 
        LIMIT 10
    """)
    passenger_orders = cursor.fetchall()
    
    conn.close()
    
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
    
    incomplete_orders = get_incomplete_orders('pending')
    
    if not incomplete_orders:
        await message.answer("✅ Barcha zakazlar to'liq!")
        return
    
    text = "⚠️ To'liq bo'lmagan zakazlar:\n\n"
    for i, order in enumerate(incomplete_orders, 1):
        order_id, order_number, user_name, original_msg, missing_info, group_name, created_date = order
        text += f"{i}. <b>Zakaz #{order_number}</b>\n"
        text += f"   👤 {user_name}\n"
        text += f"   💬 {original_msg[:50]}...\n"
        text += f"   ❌ Yetishmayotgan: {missing_info}\n"
        text += f"   🫂 {group_name}\n"
        text += f"   📅 {created_date[:16]}\n"
        text += f"   🆔 ID: {order_id}\n\n"
    
    await message.answer(text, parse_mode='HTML')

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
        "So'zlarni vergul bilan ajratib yozing:\n"
        "Masalan: ketaman, boraman, olib ketaman"
    )

@dp.callback_query(lambda c: c.data == "add_passenger")
async def add_passenger_words(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_passenger_words'
    await callback.message.edit_text(
        "🙋♂️ Yo'lovchi so'zlarini qo'shish:\n\n"
        "So'zlarni vergul bilan ajratib yozing:\n"
        "Masalan: kerak, ketish kerak, olib keting"
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
    text += ", ".join(passenger_words) if passenger_words else "Yo'q"
    text += f"\n\n🚗 Haydovchi so'zlari ({len(driver_words)}):\n\n"
    text += ", ".join(driver_words) if driver_words else "Yo'q"
    
    await callback.message.edit_text(text)

@dp.callback_query(lambda c: c.data == "back_main")
async def back_main(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🤖 Userbot boshqaruv paneli\n\n"
        "Quyidagi tugmalardan birini tanlang:"
    )

@dp.callback_query(lambda c: c.data.startswith("block_user_") or c.data.startswith("block_"))
async def block_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("🚫 Siz admin emassiz!", show_alert=True)
        return
    try:
        # block_user_123_456 yoki block_123 formatini qabul qiladi
        data = callback.data.replace("block_user_", "").replace("block_", "")
        user_id = int(data.split("_")[0])
        
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
@dp.message(lambda message: message.location)
async def location_handler(message: types.Message):
    if is_admin(message.from_user.id):
        return
    
    location = message.location
    taxi_users[message.from_user.id] = {
        "latitude": location.latitude,
        "longitude": location.longitude
    }
    
    await message.answer(
        "🎯 Qayerga borasiz?",
        reply_markup=destination_menu()
    )

# Telefon raqam qabul qilish
@dp.message(lambda message: message.contact)
async def contact_handler(message: types.Message):
    if is_admin(message.from_user.id):
        return
        
    if message.from_user.id not in taxi_users:
        await message.answer("⚠️ Avval /start bosing!")
        return
    
    phone = message.contact.phone_number
    taxi_users[message.from_user.id]["phone"] = phone
    
    # Kontaktni bazaga saqlash
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO users (user_id, user_name, phone) VALUES (?, ?, ?)',
                (message.from_user.id, message.from_user.first_name or 'Foydalanuvchi', phone)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Kontakt saqlashda xatolik: {e}")
    
    await send_taxi_order(message, message.from_user, phone)

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
        f"{'='*25}\n\n"
        f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
        f"📞 <b>Telefon:</b> {formatted_phone}\n\n"
        f"🚗 <b>Yo'nalish:</b>\n"
        f"   {user_data['from_city']} ➡️ {user_data['to_city']}\n\n"
        f'👥 <b>Yo\'lovchilar:</b> {user_data.get("passenger_count", "Noma'lum")}\n'
        f'🕐 <b>Vaqt:</b> {user_data.get("departure_time", "Noma'lum")}\n\n'
        f"<b>Mijoz:</b> {user_name}"
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
        await bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=order_message,
            parse_mode='HTML',
            reply_markup=order_keyboard
        )
        
        # Qo'shimcha guruhlarga ham yuborish
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT group_id FROM order_groups')
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
        f"{'='*25}\n\n"
        f"👤 <a href='tg://user?id={user.id}'><b>{user_name}</b></a>\n"
        f"📞 <b>Telefon:</b> {formatted_phone}\n"
        f"🎯 <b>Qayerga:</b> {user_data['destination']}"
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
        # Yagona xabar - barcha ma'lumotlar bilan
        await bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=order_message,
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
                cursor.execute('SELECT group_id FROM order_groups')
                order_groups = [row[0] for row in cursor.fetchall()]
                
                for group_id in order_groups:
                    # Matn xabari
                    await bot.send_message(
                        chat_id=group_id,
                        text=order_message,
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
                cursor.execute('SELECT group_id FROM order_groups')
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
        print(f"{R}❌ XATOLIK: Tugmalar yuborishda - {e} {W}")
        
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

@dp.message(lambda message: message.text and not message.text.startswith('/') and not message.text in ["📊 Statistika", "📝 So'zlar qo'shish", "⚙️ Sozlamalar", "🔍 Qidiruv", "🕜 Oxirgi 10 ta zakaz", "📋 Guruh statistikasi"])
async def handle_text_message(message: types.Message):
    user_id = message.from_user.id
    
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

    # Sozlalar va boshqa holatlar
    if user_id in user_states:
        if user_states[user_id] == 'waiting_order_header':
            header = message.text.strip()
            set_setting('order_header', header)
            del user_states[user_id]
            keyboard, new_header = general_settings_menu()
            await message.answer(
                f"✅ Buyurtma sarlavhasi saqlandi!\n\n"
                f"📝 <b>Hozirgi sarlavha:</b>\n{new_header}",
                reply_markup=keyboard,
                parse_mode='HTML'
            )
            return
        
        if user_states[user_id] == 'waiting_driver_words':
            words = [w.strip() for w in message.text.split(',')]
            for word in words:
                if word:
                    save_keyword('driver', word)
            del user_states[user_id]
            await message.answer(f"✅ {len(words)} ta haydovchi so'zi qo'shildi!")
            return
        elif user_states[user_id] == 'waiting_passenger_words':
            words = [w.strip() for w in message.text.split(',')]
            for word in words:
                if word:
                    save_keyword('passenger', word)
            del user_states[user_id]
            await message.answer(f"✅ {len(words)} ta yo'lovchi so'zi qo'shildi!")
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
    
    # Qidiruv funksiyasi - faqat admin uchun
    if is_admin(message.from_user.id):
        await search_user_func(message)

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
        conn = sqlite3.connect('zakazlar.db')
        cursor = conn.cursor()
        cursor.execute('SELECT group_id FROM order_groups')
        groups = [row[0] for row in cursor.fetchall()]
        conn.close()
        return groups
    except:
        return []

def save_order_group(group_id):
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO order_groups (group_id) VALUES (?)', (group_id,))
    conn.commit()
    conn.close()

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
            [InlineKeyboardButton(text="⚙️ Umumiy sozlamalar", callback_data="general_settings_menu")]
        ]
    )
    return keyboard

# Groups menu
def groups_menu():
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Buyurtma guruhlari", callback_data="list_order_groups")],
            [InlineKeyboardButton(text="➕ Buyurtma guruh qo'shish", callback_data="add_order_group_prompt")],
            [InlineKeyboardButton(text="➖ Buyurtma guruh o'chirish", callback_data="remove_order_group_prompt")],
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

# General Settings menu
def general_settings_menu():
    current_header = get_setting('order_header', '')
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Buyurtma sarlavhasini o'zgartirish", callback_data="edit_order_header")],
            [InlineKeyboardButton(text="🗑 Sarlavhani o'chirish", callback_data="clear_order_header")],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_menu")]
        ]
    )
    return keyboard, current_header

@dp.callback_query(lambda c: c.data == "general_settings_menu")
async def general_settings_menu_handler(callback: types.CallbackQuery):
    keyboard, header = general_settings_menu()
    await callback.message.edit_text(
        f"⚙️ <b>Umumiy sozlamalar</b>\n\n"
        f"📝 <b>Hozirgi buyurtma sarlavhasi:</b>\n{header}\n\n"
        f"Sarlavhani o'zgartirish uchun quyidagi tugmani bosing:",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data == "edit_order_header")
async def edit_order_header_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_states[user_id] = 'waiting_order_header'
    await callback.message.edit_text(
        "📝 <b>Yangi buyurtma sarlavhasini yuboring:</b>\n\n"
        "HTML teglardan foydalanish mumkin (b, i, a).\n"
        "Masalan: 🚕 <b>DIQQAT! YANGI BUYURTMA</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Bekor qilish", callback_data="general_settings_menu")]]),
        parse_mode='HTML'
    )

@dp.callback_query(lambda c: c.data == "clear_order_header")
async def clear_order_header_handler(callback: types.CallbackQuery):
    set_setting('order_header', '')
    await callback.answer("✅ Buyurtma sarlavhasi olib tashlandi", show_alert=True)
    keyboard, header = general_settings_menu()
    await callback.message.edit_text(
        f"⚙️ <b>Umumiy sozlamalar</b>\n\n"
        f"📝 <b>Hozirgi buyurtma sarlavhasi:</b>\n(Bo'sh)\n\n"
        f"Sarlavhani o'zgartirish uchun quyidagi tugmani bosing:",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.message(lambda message: message.text == "⚙️ Sozlamalar")
async def settings_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    await message.answer(
        "🔧 Admin Panel:",
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
                [InlineKeyboardButton(text="➕ Guruh qo'shish", callback_data=f"add_monitored_{profile_id}")],
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
    
    # Buyurtma guruhidagi zakazlar soni
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM zakazlar")
    total_orders = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE DATE(sana) = DATE('now')")
    today_orders = cursor.fetchone()[0]
    conn.close()
    
    text = f"📤 Buyurtma guruhi:\n\n"
    text += f"🆔 ID: {ORDER_GROUP_ID}\n"
    text += f"📈 Jami yuborilgan: {total_orders}\n"
    text += f"📅 Bugun yuborilgan: {today_orders}"
    
    await callback.message.edit_text(text)

@dp.callback_query(lambda c: c.data == "list_blocked")
async def list_blocked_handler(callback: types.CallbackQuery):
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM blocked_users')
    blocked = [str(row[0]) for row in cursor.fetchall()]
    conn.close()
    
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
    order_groups = load_order_groups()
    if order_groups:
        groups_text = "📤 Buyurtma guruhlari:\n" + "\n".join([f"• {g}" for g in order_groups])
    else:
        groups_text = "📭 Buyurtma guruhlari yo'q"
    await callback.message.edit_text(groups_text)

@dp.callback_query(lambda c: c.data == "add_order_group_prompt")
async def add_order_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_add_order_group_id'
    await callback.message.edit_text("Buyurtma guruhi ID sini yuboring:\nMisol: -1001234567890")

@dp.callback_query(lambda c: c.data == "remove_order_group_prompt")
async def remove_order_group_prompt_handler(callback: types.CallbackQuery):
    user_states[callback.from_user.id] = 'waiting_remove_order_group_id'
    await callback.message.edit_text("O'chirish uchun buyurtma guruhi ID sini yuboring:\nMisol: -1001234567890")

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


@dp.callback_query(lambda c: c.data and c.data.startswith('write_'))
async def write_user_handler(callback: types.CallbackQuery):
    try:
        parts = callback.data.split('_')
        user_id = parts[1]
        username_from_data = parts[2] if len(parts) > 2 else ""
        
        user_name = "Foydalanuvchi"
        if callback.message and (callback.message.text or callback.message.caption):
            msg_text_to_search = callback.message.text or callback.message.caption
            # message matnidan "👤 " bilan boshlangan qatorni qidiramiz
            lines = msg_text_to_search.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith("👤 "):
                    user_name = line[2:].strip()
                    break
        
        # Link generation
        if username_from_data and username_from_data != "None":
            msg_text = f"Bog'lanish <a href='https://t.me/{username_from_data}'>{html.escape(user_name)}</a>"
        elif user_id != '0':
            msg_text = f"Bog'lanish <a href='tg://user?id={user_id}'>{html.escape(user_name)}</a>"
        else:
            msg_text = f"Bog'lanish <a href='tg://user?id={user_id}'>{html.escape(user_name)}</a>"
            
        try:
            conn = sqlite3.connect('zakazlar.db')
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS pending_userbot_messages (
                              id INTEGER PRIMARY KEY AUTOINCREMENT,
                              chat_id INTEGER,
                              message TEXT
                              )''')
            cursor.execute('INSERT INTO pending_userbot_messages (chat_id, message) VALUES (?, ?)', (callback.message.chat.id, msg_text))
            conn.commit()
            conn.close()
            await callback.answer("✅ Xabar userbot akkaunti orqali yuborildi!", show_alert=True)
        except Exception as e:
            logger.error(f"DB xatolik: {e}")
            await callback.message.reply(msg_text, parse_mode='HTML')
            await callback.answer()
    except Exception as e:
        logger.error(f"Write tugmasi xatosi: {e}")
        await callback.answer(f"Xatolik yuz berdi: {e}", show_alert=True)

async def send_demo_orders():
    """Bot ishga tushganda 10 ta demo zakaz yuborish"""
    print("🔍 Guruhlarni tekshirish...")
    
    # Asosiy guruhni tekshirish
    try:
        chat_info = await bot.get_chat(ORDER_GROUP_ID)
        print(f"{G}✅ Asosiy guruh mavjud: {chat_info.title if hasattr(chat_info, 'title') else ORDER_GROUP_ID} {W}")
    except Exception as e:
        print(f"{R}❌ Asosiy guruhga kirish imkoni yo'q: {e} {W}")
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
        print(f"{B}📤 Demo zakaz #{i} yuborilmoqda... {W}")
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
                    cursor.execute('SELECT group_id FROM order_groups LIMIT 3')  # Faqat 3 ta guruhga
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
            print(f"{G}✅ Demo zakaz #{i} muvaffaqiyatli yuborildi {W}")
            
            # Har bir zakaz orasida 1 soniya kutish
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"{R}❌ Demo zakaz #{i} xatolik: {e} {W}")
            logger.error(f"Demo zakaz #{i} umumiy xatolik: {e}")
            continue  # Keyingi zakazga o'tish
    
    print(f"{G}✅ {successful_orders}/10 demo zakaz muvaffaqiyatli yuborildi {W}")
    logger.info(f"Demo zakazlar yuborish tugadi: {successful_orders}/10")

async def main():
    print(f"{B}🤖 Bot ishga tushmoqda...{W}")
    
    # Ma'lumotlar bazasini ishga tushirish
    init_keywords_db()
    print(f"{G}✅ Keywords bazasi tayyor{W} {W}")
    
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


@dp.message(lambda message: message.text == "🚫 Bloklangan foydalanuvchilar")
async def blocked_users_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, blocked_date FROM blocked_users ORDER BY blocked_date DESC LIMIT 20")
    blocked = cursor.fetchall()
    conn.close()
    
    if not blocked:
        await message.answer("✅ Bloklangan foydalanuvchilar yo'q")
        return
    
    text = f"🚫 Bloklangan foydalanuvchilar ({len(blocked)}):\n\n"
    for user_id, blocked_date in blocked:
        text += f"👤 ID: {user_id}\n📅 {blocked_date[:16]}\n\n"
    
    await message.answer(text)

@dp.message(lambda message: message.text == "📱 Profil qo'shish")
async def add_profile_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    user_states[message.from_user.id] = 'waiting_profile_phone'
    await message.answer(
        "📱 Profil qo'shish:\n\n"
        "Telefon raqamni yuboring:\n"
        "Masalan: +998901234567 yoki 901234567"
    )

@dp.message(lambda message: message.text == "🔐 Admin qo'shish")
async def add_admin_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    user_states[message.from_user.id] = 'waiting_add_admin_id'
    await message.answer(
        "🔐 Admin qo'shish:\n\n"
        "Foydalanuvchi ID sini yuboring:\n"
        "Masalan: 123456789"
    )

@dp.message(lambda message: message.text == "📈 Xatoliklar")
async def errors_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Sizga ruxsat yo'q!")
        return
    
    if not recent_errors:
        await message.answer("✅ Xatoliklar yo'q")
        return
    
    text = f"📈 Oxirgi {len(recent_errors)} ta xatolik:\n\n"
    for i, error in enumerate(recent_errors, 1):
        text += f"{i}. {error}\n\n"
    
    await message.answer(text)
