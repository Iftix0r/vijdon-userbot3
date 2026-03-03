from telethon import TelegramClient, events
from telethon.tl.functions.users import GetFullUserRequest
from telethon.errors import SessionPasswordNeededError
import asyncio
import aiohttp
import os
import json
import sqlite3
import re
import logging
from dotenv import load_dotenv
from contextlib import contextmanager


load_dotenv()

# Logging sozlash
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('userbot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
ORDER_GROUP_ID = int(os.getenv('ORDER_GROUP_ID'))

client = TelegramClient('userbot', API_ID, API_HASH)

def load_groups():
    try:
        with open('groups.json', 'r') as f:
            return json.load(f)
    except:
        return []

def save_groups(groups):
    with open('groups.json', 'w') as f:
        json.dump(groups, f)

async def get_bot_username():
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('result', {}).get('username', 'vijdonuserbot')
        return 'vijdonuserbot'
    except Exception as e:
        logger.error(f"Bot userneymini olishda xatolik: {e}")
        return 'vijdonuserbot'

monitored_groups = load_groups()
bot_username = "vijdonuserbot" # Boshlang'ich qiymat
keywords = {"driver": [], "passenger": []}

def load_keywords_from_db():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT word FROM keywords WHERE type = ?', ('passenger',))
            passenger_words = [row[0] for row in cursor.fetchall()]
            
            cursor.execute('SELECT word FROM keywords WHERE type = ?', ('driver',))
            driver_words = [row[0] for row in cursor.fetchall()]
            
            return {
                "passenger": passenger_words,
                "driver": driver_words
            }
    except Exception as e:
        logger.error(f"Keywords yuklashda xatolik: {e}")
        return {"passenger": [], "driver": []}

def init_database():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
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
            
            # Zakazlar table
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
            
            # Blocked users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    blocked_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Order groups table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_groups (
                    group_id INTEGER PRIMARY KEY,
                    group_name TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Keywords table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    word TEXT NOT NULL,
                    sana DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(type, word)
                )
            ''')
            
            # Agar order_number ustuni yo'q bo'lsa, qo'shish
            cursor.execute("PRAGMA table_info(zakazlar)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'order_number' not in columns:
                cursor.execute('ALTER TABLE zakazlar ADD COLUMN order_number INTEGER')
                # Mavjud zakazlarga tartib raqami berish
                cursor.execute('''
                    UPDATE zakazlar 
                    SET order_number = (
                        SELECT COUNT(*) FROM zakazlar z2 
                        WHERE z2.id <= zakazlar.id
                    )
                    WHERE order_number IS NULL
                ''')
                logger.info("Order number column added and updated")
            
            conn.commit()
            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
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

def is_user_blocked(user_id):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM blocked_users WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Error checking blocked user {user_id}: {e}")
        return False

def save_user_and_zakaz(user_id, user_name, username, phone, user_type, message, group_name, group_id):
    conn = sqlite3.connect('zakazlar.db')
    cursor = conn.cursor()
    
    # Foydalanuvchini saqlash yoki yangilash
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, user_name, username, phone, first_seen, last_seen)
        VALUES (?, ?, ?, ?, 
                COALESCE((SELECT first_seen FROM users WHERE user_id = ?), CURRENT_TIMESTAMP),
                CURRENT_TIMESTAMP)
    ''', (user_id, user_name, username, phone, user_id))
    
    # Keyingi zakaz raqamini olish
    cursor.execute('SELECT COALESCE(MAX(order_number), 0) + 1 FROM zakazlar')
    next_order_number = cursor.fetchone()[0]
    
    # Zakazni saqlash
    cursor.execute('''
        INSERT INTO zakazlar (order_number, user_id, user_type, message, group_name, group_id)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (next_order_number, user_id, user_type, message, group_name, group_id))
    
    # Faqat oxirgi 50 ta zakazni saqlash
    cursor.execute('''
        DELETE FROM zakazlar WHERE id NOT IN (
            SELECT id FROM zakazlar ORDER BY sana DESC LIMIT 50
        )
    ''')
    
    conn.commit()
    conn.close()
    
    return next_order_number

def detect_user_type(text):
    if not text or not isinstance(text, str):
        return '🙋♂️ Yolovchi'
    
    keywords = load_keywords_from_db()
    text_lower = text.lower().strip()
    
    # Haydovchi so'zlarini tekshirish
    for word in keywords['driver']:
        if word.lower() in text_lower:
            return '🚗 Haydovchi'
    
    # Yo'lovchi so'zlarini tekshirish
    for word in keywords['passenger']:
        if word.lower() in text_lower:
            return '🙋♂️ Yolovchi'
    
    return '🙋♂️ Yolovchi'

@client.on(events.ChatAction)
async def chat_action_handler(event):
    """Handle chat actions like user join/leave, group title changes, etc."""
    try:
        me = await client.get_me()
        chat = await event.get_chat()
        chat_title = chat.title if hasattr(chat, 'title') else 'Nomaʼlum guruh'
        
        # Bot o'zini guruhdan chiqarsa
        if event.user_left or event.user_kicked:
            if event.user_id == me.id and event.chat_id in monitored_groups:
                monitored_groups.remove(event.chat_id)
                save_groups(monitored_groups)
                logger.info(f"Bot guruhdan chiqarildi: {chat_title} ({event.chat_id})")
                print(f"⚠️ Bot guruhdan chiqarildi: {chat_title}")
        
        # Guruh nomini o'zgartirilsa
        elif event.title_changed:
            logger.info(f"Guruh nomi o'zgartirildi: {chat_title} ({event.chat_id})")
            print(f"📝 Guruh nomi o'zgartirildi: {chat_title}")
        
        # Guruh fotosini o'zgartirilsa
        elif event.photo_changed:
            logger.info(f"Guruh fotosi o'zgartirildi: {chat_title} ({event.chat_id})")
            print(f"📸 Guruh fotosi o'zgartirildi: {chat_title}")
        
        # Foydalanuvchi guruhga qo'shilsa
        elif event.user_joined:
            sender = await event.get_user()
            user_name = f"{sender.first_name or 'Foydalanuvchi'}"
            if hasattr(sender, 'last_name') and sender.last_name:
                user_name = f"{sender.first_name} {sender.last_name}"
            logger.info(f"Foydalanuvchi guruhga qo'shildi: {user_name} - {chat_title}")
            print(f"👤 Foydalanuvchi qo'shildi: {user_name} ({chat_title})")
        
        # Foydalanuvchi guruhdan chiqsa
        elif event.user_left:
            sender = await event.get_user()
            user_name = f"{sender.first_name or 'Foydalanuvchi'}"
            if hasattr(sender, 'last_name') and sender.last_name:
                user_name = f"{sender.first_name} {sender.last_name}"
            logger.info(f"Foydalanuvchi guruhdan chiqdi: {user_name} - {chat_title}")
            print(f"👋 Foydalanuvchi chiqdi: {user_name} ({chat_title})")
        
        # Foydalanuvchi guruhdan chiqarilsa
        elif event.user_kicked:
            sender = await event.get_user()
            user_name = f"{sender.first_name or 'Foydalanuvchi'}"
            if hasattr(sender, 'last_name') and sender.last_name:
                user_name = f"{sender.first_name} {sender.last_name}"
            logger.info(f"Foydalanuvchi guruhdan chiqarildi: {user_name} - {chat_title}")
            print(f"🚫 Foydalanuvchi chiqarildi: {user_name} ({chat_title})")
        
    except Exception as e:
        logger.error(f"Chat action handler xatoligi: {e}")
        print(f"❌ Chat action xatoligi: {e}")

@client.on(events.NewMessage(incoming=True))
async def handler(event):
    if not event.is_group:
        return
    
    # O'z xabarlarini va bot xabarlarini ignore qilish
    me = await client.get_me()
    bot_id = int(BOT_TOKEN.split(':')[0])  # Bot ID ni olish
    
    if event.sender_id == me.id:
        return
        
    if event.sender_id == bot_id:
        return
    
    if event.chat_id not in monitored_groups:
        monitored_groups.append(event.chat_id)
        save_groups(monitored_groups)
    
    text_content = event.text or ""
    
    # Bo'sh xabar tekshiruvi
    if not text_content:
        return
    
    # 100 harf cheklovimain.
    if len(text_content) > 100:
        return
    
    # Emoji va sticker tekshiruvi
    if event.message.sticker:
        return
    
    # Emoji tekshiruvi (Unicode emoji range)
    import re
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map
        u"\U0001F1E0-\U0001F1FF"  # flags
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    
    if emoji_pattern.search(text_content):
        return
    
    # Sender va chat ma'lumotlarini xavfsiz olish
    sender = None
    chat = None
    
    try:
        sender = await event.get_sender()
    except:
        pass  # Sender ma'lumotini ololmasak ham davom etamiz
    
    try:
        chat = await event.get_chat()
    except:
        pass  # Chat ma'lumotini ololmasak ham davom etamiz
    
    # Forward xabarlarni ignore qilish
    if event.message.fwd_from:
        return
    
    # Foydalanuvchi ma'lumotlari
    user_details_parts = []
    user_info = "👤 Foydalanuvchi"
    user_id = 0
    
    # Oddiy xabar
    if sender:
        try:
            # O'z va bot xabarlarini ignore qilish (qo'shimcha tekshiruv)
            if sender.id == me.id:
                return
                
            if sender.id == bot_id:
                return
                
            user_id = sender.id
            user_name = f"{sender.first_name or 'Nomaʼlum'}"
            if hasattr(sender, 'last_name') and sender.last_name:
                user_name = f"{sender.first_name} {sender.last_name}"
            user_info = f"👤 <a href='tg://user?id={sender.id}'>{user_name}</a>"
            
            # ID ni qo'shmaslik
            if hasattr(sender, 'username') and sender.username:
                user_details_parts.append(f"🤙 @{sender.username}")
            if hasattr(sender, 'phone') and sender.phone:
                user_details_parts.append(f"☎️ +{sender.phone}")
        except:
            user_info = "👤 Noma'lum foydalanuvchi"
            user_id = 0
    # Sender yo'q bo'lsa
    else:
        user_info = "👤 Noma'lum foydalanuvchi"
        user_id = 0
        sender = None
    
    user_details = "\n".join(user_details_parts) if user_details_parts else ""
    
    # Xabar va guruh havolalarini xavfsiz yaratish
    message_link = "#"
    group_link = "#"
    group_info = "🫂 Guruh"
    
    if chat:
        try:
            if str(chat.id).startswith('-100'):
                chat_id_str = str(chat.id)[4:]
                message_link = f"https://t.me/c/{chat_id_str}/{event.id}"
            else:
                message_link = f"https://t.me/{chat.username}/{event.id}" if hasattr(chat, 'username') and chat.username else "#"
            
                    # Guruh nomini oddiy matn sifatida
            group_info = f"🫂 {chat.title}" if hasattr(chat, 'title') and chat.title else "🫂 Guruh"
        except:
            pass
    
    # Telefon raqam qidirish
    phone_patterns = [
        r'\+998\d{9}',
        r'998\d{9}',
        r'\d{9}',
        r'\d{2}\s\d{3}\s\d{2}\s\d{2}',
        r'\d{2}-\d{3}-\d{2}-\d{2}',
    ]
    phones = []
    
    for pattern in phone_patterns:
        found = re.findall(pattern, text_content)
        phones.extend(found)
        if phones:
            break
    
    # Haydovchi yoki yo'lovchi so'zlari bor xabarlarni olish
    keywords = load_keywords_from_db()
    text_lower = text_content.lower().strip()
    
    # Haydovchi so'zlarini tekshirish
    has_driver_words = False
    for word in keywords['driver']:
        if word.lower() in text_lower:
            has_driver_words = True
            break
    
    # Yo'lovchi so'zlarini tekshirish
    has_passenger_words = False
    for word in keywords['passenger']:
        if word.lower() in text_lower:
            has_passenger_words = True
            break
    
    # Agar haydovchi so'zlari bo'lsa, xabarni ignore qilish
    if has_driver_words:
        return
    
    # Agar yo'lovchi so'zlari yo'q bo'lsa, xabarni ignore qilish
    if not has_passenger_words:
        return
    
    user_type = '🙋♂️ Yolovchi'
    
    # Bloklangan foydalanuvchini tekshirish - zakazni bazaga saqlash lekin guruhga yubormaslik
    is_blocked = is_user_blocked(user_id)
    
    # Foydalanuvchi ma'lumotlarini ajratish
    clean_user_name = ''
    username = ''
    phone = ''
    
    if sender:
        clean_user_name = f"{sender.first_name or ''}"
        if hasattr(sender, 'last_name') and sender.last_name:
            clean_user_name += f" {sender.last_name}"
        if hasattr(sender, 'username') and sender.username:
            username = sender.username
        if hasattr(sender, 'phone') and sender.phone:
            phone = sender.phone
    
    chat_title = 'Nomaʼlum guruh'
    if chat and hasattr(chat, 'title') and chat.title:
        chat_title = chat.title
    
    # Haydovchi va yo'lovchilarni bazaga saqlash (bloklangan bo'lsa ham)
    order_number = save_user_and_zakaz(user_id, clean_user_name.strip(), username, phone, user_type, text_content, chat_title, event.chat_id)
    
    # Agar bloklangan bo'lsa, guruhga yubormaslik
    if is_blocked:
        return
    
    # BIRINCHI: Akkaunt orqali matn xabari yuborish
    try:
        user_name = clean_user_name.strip() if clean_user_name.strip() else 'Foydalanuvchi'
        
        # Xabar matni
        message_parts = [f"🚕 <b>VIJDON TAXI</b> <b>#{order_number}</b>"]
        message_parts.append(f"👤 <a href='tg://user?id={user_id}'>{user_name}</a>")
        
        if username:
            message_parts.append(f"🤙 @{username}")
        
        if text_content and text_content.strip():
            message_parts.append(f"💬 <b><i>{text_content.strip()}</i></b>")
        
        if phones:
            phone_num = phones[0].replace(' ', '').replace('-', '')
            if phone_num.startswith('998'):
                phone_num = '+' + phone_num
            elif not phone_num.startswith('+998'):
                phone_num = '+998' + phone_num
            message_parts.append(f"📞 {phone_num}")
        elif sender and hasattr(sender, 'phone') and sender.phone:
            message_parts.append(f"📞 +{sender.phone}")
        
        caption = "\n\n".join(message_parts)
        
        # Profil rasmini olish va yuborish
        if sender:
            try:
                profile_photos = await client.get_profile_photos(sender)
                if profile_photos:
                    await client.send_file(
                        entity=ORDER_GROUP_ID,
                        file=profile_photos[0],
                        caption=caption,
                        parse_mode='html',
                        link_preview=False
                    )
                    print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI YUBORILDI - {user_name} ({ORDER_GROUP_ID})")
                    logger.info(f"Zakaz #{order_number} akkaunt orqali yuborildi - {user_name}")
                else:
                    await client.send_message(
                        entity=ORDER_GROUP_ID,
                        message=caption,
                        parse_mode='html'
                    )
                    print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI YUBORILDI (MATN) - {user_name} ({ORDER_GROUP_ID})")
                    logger.info(f"Zakaz #{order_number} akkaunt orqali matn sifatida yuborildi - {user_name}")
            except Exception as e:
                logger.error(f"Profil rasmi yuborishda xatolik: {e}")
                print(f"❌ XATOLIK: Profil rasmi yuborishda - {e}")
                try:
                    await client.send_message(
                        entity=ORDER_GROUP_ID,
                        message=caption,
                        parse_mode='html'
                    )
                    print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI YUBORILDI (MATN - XATOLIKDAN KEYIN) - {user_name}")
                    logger.info(f"Zakaz #{order_number} xatolikdan keyin matn sifatida yuborildi")
                except Exception as e2:
                    print(f"❌ XATOLIK: Matn xabar yuborishda - {e2}")
                    logger.error(f"Matn xabar yuborishda xatolik: {e2}")
        else:
            await client.send_message(
                entity=ORDER_GROUP_ID,
                message=caption,
                parse_mode='html'
            )
            print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI YUBORILDI (SENDER YO'Q) - {user_name}")
    except Exception as e:
        logger.error(f"Akkaunt orqali xabar yuborishda xatolik: {e}")
        print(f"❌ XATOLIK: Akkaunt orqali yuborishda - {e}")
    
    # Tarjima holi (Bio) olish
    user_bio = ""
    if sender:
        try:
            full_user = await client(GetFullUserRequest(sender.id))
            user_bio = full_user.full_user.about or ""
        except Exception as e:
            logger.error(f"Bio olishda xatolik: {e}")

    # IKKINCHI: Bot orqali tugmalar yuborish
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Bot API orqali tugmalar yuborish
            user_name = clean_user_name.strip() if clean_user_name.strip() else 'Foydalanuvchi'
            
            if user_bio:
                buttons_message = f"<i>📝 {user_bio}</i>"
            else:
                buttons_message = f"🚕 <b>#{order_number}</b>"
            
            # Tugmalarni tayyorlash
            buttons = []
            row1 = []
            
            # 1. Qo'ng'iroq qilish tugmasi
            phone_to_call = None
            if phones:
                phone_to_call = phones[0].replace(' ', '').replace('-', '')
            elif sender and hasattr(sender, 'phone') and sender.phone:
                phone_to_call = sender.phone
            
            if phone_to_call:
                if phone_to_call.startswith('998'):
                    phone_to_call = '+' + phone_to_call
                elif not phone_to_call.startswith('+'):
                    phone_to_call = '+998' + phone_to_call
                row1.append({"text": "📞 Qo'ngiroq", "url": f"https://onmap.uz/tel/{phone_to_call}"})
            
            # 2. Xabarni ko'rish tugmasi (Guruhdagi asl xabarga havola)
            if message_link and message_link != "#":
                row1.append({"text": "🔍 Xabarni ko'rish", "url": message_link})
            
            if row1:
                buttons.append(row1)
                
            # 3. Profilni ko'rish tugmasi (ikkinchi qatorda)
            row2 = []
            if user_id and user_id > 0:
                row2.append({"text": f"👤 {user_name}", "url": f"tg://user?id={user_id}"})
            elif username:
                row2.append({"text": f"👤 {user_name}", "url": f"https://t.me/{username}"})
                
            if row2:
                buttons.append(row2)
            
            # Bot API orqali xabar yuborish (tugmalar bilan)
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": ORDER_GROUP_ID,
                "text": buttons_message,
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": buttons} if buttons else None
            }
            
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    print(f"✅ TUGMALAR BOT ORQALI YUBORILDI - {user_name}")
                    logger.info(f"Tugmalar bot orqali yuborildi - {user_name}")
                else:
                    print(f"❌ XATOLIK: Tugmalar yuborishda - {resp.status}")
                    logger.error(f"Tugmalar yuborishda xatolik: {resp.status}")
            
            # MAXSUS: vijdontaxireklama guruhiga yuborish
            try:
                special_group = "@vijdontaxireklama"
                
                # Matnni tayyorlash (Zakaz + Bio)
                special_message = f"🚕 <b>#{order_number}</b>\n\n"
                if text_content and text_content.strip():
                    special_message += f"<i>{text_content.strip()}</i>"
                
                if user_bio:
                    special_message += f"\n\n<i>{user_bio}</i>"
                
                # Tugmani tayyorlash
                special_buttons = [[{"text": "✅ Zakazni olish", "url": f"https://t.me/{bot_username}?start=zakaz_{order_number}"}]]
                
                special_payload = {
                    "chat_id": special_group,
                    "text": special_message,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": special_buttons}
                }
                
                async with session.post(url, json=special_payload) as resp:
                    if resp.status == 200:
                        print(f"✅ SPECIAL GURUHGA YUBORILDI - #{order_number}")
                        logger.info(f"Zakaz #{order_number} special guruhga yuborildi")
            except Exception as e:
                logger.error(f"Special guruhga yuborishda xatolik: {e}")

            # Qo'shimcha guruhlarga ham tugmalar yuborish
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT group_id FROM order_groups')
                    order_groups = [row[0] for row in cursor.fetchall()]
                
                for group_id in order_groups:
                    payload["chat_id"] = group_id
                    async with session.post(url, json=payload) as resp:
                        if resp.status == 200:
                            logger.info(f"Tugmalar qo'shimcha guruhga yuborildi - {group_id}")
            except Exception as e:
                logger.error(f"Tugmalar qo'shimcha guruhlarga yuborishda xatolik: {e}")

    except Exception as e:
        logger.error(f"Bot orqali tugmalar yuborishda xatolik: {e}")
        print(f"❌ XATOLIK: Bot orqali tugmalar yuborishda - {e}")
    
    # Qo'shimcha buyurtma guruhlariga yuborish
    try:
        conn = sqlite3.connect('zakazlar.db')
        cursor = conn.cursor()
        cursor.execute('SELECT group_id FROM order_groups')
        order_groups = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        for group_id in order_groups:
            if user_id and user_id > 0:
                user_name = clean_user_name.strip() if clean_user_name.strip() else 'Foydalanuvchi'
                
                # Xabar matni
                message_parts = [f"🚕 <b>VIJDON TAXI</b> <b>#{order_number}</b>"]
                message_parts.append(f"👤 <a href='tg://user?id={user_id}'>{user_name}</a>")
                
                if username:
                    message_parts.append(f"🤙 @{username}")
                
                if text_content and text_content.strip():
                    message_parts.append(f"💬 <b><i>{text_content.strip()}</i></b>")
                
                if phones:
                    phone = phones[0].replace(' ', '').replace('-', '')
                    if phone.startswith('998'):
                        phone = '+' + phone
                    elif not phone.startswith('+998'):
                        phone = '+998' + phone
                    message_parts.append(f"📞 {phone}")
                elif sender and hasattr(sender, 'phone') and sender.phone:
                    message_parts.append(f"📞 +{sender.phone}")
                
                caption = "\n\n".join(message_parts)
                
                # Caption-da tugmalar YO'Q - faqat matn
                
                try:
                    # Profil rasmini olish va yuborish (TUGMASIZ)
                    if sender:
                        try:
                            profile_photos = await client.get_profile_photos(sender)
                            if profile_photos:
                                # Profil rasmi bilan caption yuborish
                                await client.send_file(
                                    entity=group_id,
                                    file=profile_photos[0],
                                    caption=caption,
                                    parse_mode='html',
                                    link_preview=False
                                )
                                print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI QOSHIMCHA GURUHGA YUBORILDI - {user_name} ({group_id})")
                                logger.info(f"Zakaz #{order_number} qo'shimcha guruhga akkaunt orqali yuborildi - {group_id}")
                            else:
                                # Profil rasmi yo'q bo'lsa, matn xabar yuborish
                                await client.send_message(
                                    entity=group_id,
                                    message=caption,
                                    parse_mode='html'
                                )
                                print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI QOSHIMCHA GURUHGA YUBORILDI (MATN) - {user_name} ({group_id})")
                        except Exception as e:
                            logger.error(f"Profil rasmi qo'shimcha guruhga yuborishda xatolik: {e}")
                            print(f"❌ XATOLIK: Qo'shimcha guruhga profil rasmi yuborishda - {e}")
                            # Xatolik bo'lsa, matn xabar yuborish
                            try:
                                await client.send_message(
                                    entity=group_id,
                                    message=caption,
                                    parse_mode='html'
                                )
                                print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI QOSHIMCHA GURUHGA YUBORILDI (MATN - XATOLIKDAN KEYIN) - {user_name}")
                            except Exception as e2:
                                print(f"❌ XATOLIK: Qo'shimcha guruhga matn yuborishda - {e2}")
                                logger.error(f"Qo'shimcha guruhga matn yuborishda xatolik: {e2}")
                    else:
                        # Sender yo'q bo'lsa, matn xabar yuborish
                        await client.send_message(
                            entity=group_id,
                            message=caption,
                            parse_mode='html'
                        )
                        print(f"✅ ZAKAZ #{order_number} AKKAUNT ORQALI QOSHIMCHA GURUHGA YUBORILDI (SENDER YO'Q) - {user_name}")
                except Exception as e:
                    logger.error(f"Akkaunt orqali qo'shimcha guruhga xabar yuborishda xatolik: {e}")
                    print(f"❌ XATOLIK: Qo'shimcha guruhga yuborishda - {e}")
    except Exception as e:
        logger.error(f"Qo'shimcha guruhlar xatoligi: {e}")
        print(f"❌ XATOLIK: Qo'shimcha guruhlar - {e}")

async def main():
    # Bot userneymini olish
    global bot_username
    bot_username = await get_bot_username()
    print(f"🤖 Bot: @{bot_username}")
    
    print("💾 Ma'lumotlar bazasini tekshirish...")
    init_database()
    print("✅ Ma'lumotlar bazasi tayyor")
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            phone = input("Telefon raqamingizni kiriting (+998xxxxxxxxx): ")
            print("🤙 SMS kod yuborildi...")
            await client.send_code_request(phone)
            code = input("Telegram kodini kiriting: ")
            
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("2FA parolini kiriting: ")
                await client.sign_in(password=password)
        
        print("🔑 Kalit so'zlarni yuklash...")
        
        # Bazaga default so'zlarini qo'shish
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Yo'lovchi so'zlari
                cursor.execute('SELECT COUNT(*) FROM keywords WHERE type = ?', ('passenger',))
                passenger_count = cursor.fetchone()[0]
                
                if passenger_count == 0:
                    default_passenger_words = ["kerak", "ketish kerak", "olib keting", "yo'lovchi kerak", "borish kerak", "ketmoqchiman"]
                    for word in default_passenger_words:
                        cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', ('passenger', word))
                    conn.commit()
                    print("✅ Default yo'lovchi so'zlari qo'shildi")
                
                # Haydovchi so'zlari
                cursor.execute('SELECT COUNT(*) FROM keywords WHERE type = ?', ('driver',))
                driver_count = cursor.fetchone()[0]
                
                if driver_count == 0:
                    default_driver_words = ["ketaman", "boraman", "olib ketaman", "haydovchiman", "mashina bor", "taksi"]
                    for word in default_driver_words:
                        cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', ('driver', word))
                    conn.commit()
                    print("✅ Default haydovchi so'zlari qo'shildi")
        except Exception as e:
            logger.error(f"Default so'zlar qo'shishda xatolik: {e}")
        
        global keywords
        keywords = load_keywords_from_db()
        print(f"✅ Yuklandi: {len(keywords['passenger'])} yo'lovchi so'zi, {len(keywords['driver'])} haydovchi so'zi")
        
        print("📊 Statistikani hisoblash...")
        # Bazadagi statistikani ko'rsatish
        conn = sqlite3.connect('zakazlar.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE user_type LIKE '%Yolovchi%'")
        passengers_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM zakazlar WHERE user_type LIKE '%Haydovchi%'")
        drivers_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM users")
        unique_users = cursor.fetchone()[0]
        
        conn.close()
        
        print("\n" + "="*60)
        print("✅ USERBOT MUVAFFAQIYATLI ISHGA TUSHDI!")
        print("="*60)
        print(f"📊 Kuzatilayotgan guruhlar: {len(monitored_groups)}")
        print(f"🙋♂️ Yolovchi zakazlari: {passengers_count}")
        print(f"🚗 Haydovchi zakazlari: {drivers_count}")
        print(f"👥 Jami foydalanuvchilar: {unique_users}")
        print(f"📤 Buyurtma guruhi: {ORDER_GROUP_ID}")
        print("="*60)
        print("🔍 Xabarlarni kutish...\n")
        
        # Buyruqlar
        @client.on(events.NewMessage(pattern=r'/block (\d+)'))
        async def block_user_cmd(event):
            if event.is_private:
                user_id = int(event.pattern_match.group(1))
                block_user(user_id)
                await event.reply(f"🚫 Foydalanuvchi bloklandi: {user_id}")
        
        @client.on(events.NewMessage(pattern=r'/unblock (\d+)'))
        async def unblock_user_cmd(event):
            if event.is_private:
                user_id = int(event.pattern_match.group(1))
                unblock_user(user_id)
                await event.reply(f"✅ Foydalanuvchi blokdan chiqarildi: {user_id}")
        
        @client.on(events.NewMessage(pattern='/blocked'))
        async def list_blocked(event):
            if event.is_private:
                conn = sqlite3.connect('zakazlar.db')
                cursor = conn.cursor()
                cursor.execute('SELECT user_id FROM blocked_users')
                blocked = [str(row[0]) for row in cursor.fetchall()]
                conn.close()
                
                if blocked:
                    await event.reply(f"🚫 Bloklangan foydalanuvchilar:\n" + "\n".join(blocked))
                else:
                    await event.reply("📭 Bloklangan foydalanuvchi yo'q")
        
        @client.on(events.NewMessage(pattern=r'/add_group (-?\d+)'))
        async def add_group(event):
            if event.is_private:
                group_id = int(event.pattern_match.group(1))
                if group_id not in monitored_groups:
                    monitored_groups.append(group_id)
                    save_groups(monitored_groups)
                    await event.reply(f"✅ Guruh qo'shildi: {group_id}")
                else:
                    await event.reply(f"⚠️ Guruh allaqachon mavjud: {group_id}")
        
        @client.on(events.NewMessage(pattern=r'/remove_group (-?\d+)'))
        async def remove_group_by_id(event):
            if event.is_private:
                group_id = int(event.pattern_match.group(1))
                if group_id in monitored_groups:
                    monitored_groups.remove(group_id)
                    save_groups(monitored_groups)
                    await event.reply(f"❌ Guruh o'chirildi: {group_id}")
                else:
                    await event.reply(f"⚠️ Guruh topilmadi: {group_id}")
        
        @client.on(events.NewMessage(pattern='/groups'))
        async def list_groups(event):
            if event.is_private:
                if monitored_groups:
                    groups_info = []
                    for group_id in monitored_groups:
                        try:
                            chat = await client.get_entity(group_id)
                            groups_info.append(f"• {chat.title} ({group_id})")
                        except:
                            groups_info.append(f"• ID: {group_id}")
                    await event.reply(f"📋 Kuzatilayotgan guruhlar:\n" + "\n".join(groups_info))
                else:
                    await event.reply("📭 Hech qanday guruh kuzatilmayapti")
        
        @client.on(events.NewMessage(pattern=r'/make_admin (-?\d+)'))
        async def make_admin(event):
            if event.is_private:
                group_id = int(event.pattern_match.group(1))
                try:
                    await client.edit_admin(group_id, await client.get_me(), is_admin=True)
                    await event.reply(f"👑 Admin qilindi: {group_id}")
                except Exception as e:
                    await event.reply(f"❌ Admin qilishda xatolik: {e}")
        
        @client.on(events.NewMessage(pattern='/help'))
        async def help_cmd(event):
            if event.is_private:
                help_text = """🤖 Bot buyruqlari:

👥 Guruh boshqaruvi:
/add_group -1001234567890 - Guruh qo'shish
/remove_group -1001234567890 - Guruh o'chirish
/groups - Guruhlar ro'yxati
/make_admin -1001234567890 - Admin qilish

🚫 Bloklash:
/block 123456789 - Foydalanuvchini bloklash
/unblock 123456789 - Blokdan chiqarish
/blocked - Bloklangan foydalanuvchilar

📋 Boshqa:
/help - Yordam"""
                await event.reply(help_text)
        
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.critical(f"KRITIK XATOLIK: {e}")
        print(f"\n❌ KRITIK XATOLIK: {e}")
        print("Bot to'xtatildi!\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot to'xtatildi")