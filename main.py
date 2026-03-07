import time
from telethon import TelegramClient, events
from telethon.tl.functions.users import GetFullUserRequest
from telethon.errors import SessionPasswordNeededError
import html
import asyncio
import aiohttp
import os
import json
import sqlite3
import re
import logging
from dotenv import load_dotenv
from contextlib import contextmanager

# ANSI Ranglar
G = "\033[92m"  # Yashil
R = "\033[91m"  # Qizil
Y = "\033[93m"  # Sariq
B = "\033[94m"  # Moviy
W = "\033[0m"   # Reset


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

ERROR_FILE = "userbot_errors.txt"

class ErrorFileHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                with open(ERROR_FILE, "a", encoding="utf-8") as f:
                    f.write(self.format(record)[:300] + "\n")
            except:
                pass
logger.addHandler(ErrorFileHandler())


# ========== UMUMIY SOZLAMALAR ==========
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEFAULT_ORDER_GROUP_ID = int(os.getenv('ORDER_GROUP_ID'))
HAYDOVCHI_ADMIN_USERNAME = os.getenv('HAYDOVCHI_ADMIN_USERNAME', '').strip().lstrip('@')

client = TelegramClient('userbot', API_ID, API_HASH)  # Legacy - profillar bo'lmaganda

MAX_PROCESSED_CACHE = 10000


# ========== UMUMIY DB (profiles, keywords, admins) ==========
@contextmanager
def get_main_db():
    conn = None
    try:
        conn = sqlite3.connect('zakazlar.db', timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Main DB error: {e}")
        raise
    finally:
        if conn:
            conn.close()


# ========== AKKAUNT KONFIGURATSIYASI ==========

class AccountConfig:
    """Har bir akkaunt uchun alohida konfiguratsiya"""
    
    def __init__(self, profile_id, session_name, phone):
        self.profile_id = profile_id
        self.session_name = session_name
        self.phone = phone
        self.config_file = f'account_config_{profile_id}.json'
        self.db_file = f'zakazlar_account{profile_id}.db'
        self.order_group_id = DEFAULT_ORDER_GROUP_ID
        self.monitored_groups = []
        self.reklama_groups = ["@vijdontaxireklama", "@iymontaxi", "@sobirtaxi_vodiy_voha", "@iymontaxigroup"]
        self.bot_username = "vijdonuserbot"
        self.keywords = {"driver": [], "passenger": []}
        self.processed_messages = set()  # Har akkaunt uchun ALOHIDA dublikat kesh
        self.last_keyword_load = 0
        self.last_config_load = 0
        self._load_config()
        self._init_db()
        self._load_keywords()
    
    def _load_config(self):
        """JSON konfiguratsiyasini yuklash"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                self.order_group_id = config.get('order_group_id', DEFAULT_ORDER_GROUP_ID)
                self.monitored_groups = config.get('monitored_groups', [])
                self.reklama_groups = config.get('reklama_groups', self.reklama_groups)
                logger.info(f"Akkaunt #{self.profile_id} konfiguratsiya yuklandi: guruhlar={len(self.monitored_groups)}, buyurtma={self.order_group_id}")
            else:
                # Yangi config yaratish - bo'sh guruhlar bilan (har akkaunt o'zi topadi)
                self._save_config()
                logger.info(f"Akkaunt #{self.profile_id} yangi konfiguratsiya yaratildi (guruhlar avtomatik topiladi)")
        except Exception as e:
            logger.error(f"Akkaunt #{self.profile_id} config yuklash xatolik: {e}")
    
    def _save_config(self):
        """JSON konfiguratsiyasini saqlash"""
        config = {
            'account_id': self.profile_id,
            'order_group_id': self.order_group_id,
            'monitored_groups': self.monitored_groups,
            'reklama_groups': self.reklama_groups
        }
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)
    
    def add_group(self, group_id):
        if group_id not in self.monitored_groups:
            self.monitored_groups.append(group_id)
            self._save_config()
            return True
        return False
    
    def remove_group(self, group_id):
        if group_id in self.monitored_groups:
            self.monitored_groups.remove(group_id)
            self._save_config()
            return True
        return False
    
    @contextmanager
    def get_db(self):
        """Akkaunt uchun alohida DB ulanish"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_file, timeout=30)
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Akkaunt #{self.profile_id} DB error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def _init_db(self):
        """Akkaunt bazasini yaratish"""
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        user_name TEXT,
                        username TEXT,
                        phone TEXT,
                        bio TEXT,
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
                    CREATE TABLE IF NOT EXISTS blocked_users (
                        user_id INTEGER PRIMARY KEY,
                        blocked_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS order_groups (
                        group_id INTEGER PRIMARY KEY,
                        group_name TEXT,
                        added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
                logger.info(f"Akkaunt #{self.profile_id} DB tayyor: {self.db_file}")
        except Exception as e:
            logger.error(f"Akkaunt #{self.profile_id} DB init xatolik: {e}")
            raise
    
    def _load_keywords(self):
        """Kalit so'zlarni umumiy bazadan yuklash"""
        try:
            with get_main_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT word FROM keywords WHERE type = ?', ('passenger',))
                passenger_words = [row[0] for row in cursor.fetchall()]
                cursor.execute('SELECT word FROM keywords WHERE type = ?', ('driver',))
                driver_words = [row[0] for row in cursor.fetchall()]
                self.keywords = {"passenger": passenger_words, "driver": driver_words}
        except Exception as e:
            logger.error(f"Keywords yuklash xatolik: {e}")
            self.keywords = {"passenger": [], "driver": []}
    
    def is_user_blocked(self, user_id):
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT 1 FROM blocked_users WHERE user_id = ?', (user_id,))
                return cursor.fetchone() is not None
        except:
            return False
    
    def get_user_bio(self, user_id):
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT bio FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except:
            return None

    def save_user_and_zakaz(self, user_id, user_name, username, phone, user_type, message, group_name, group_id, bio=None):
        """Buyurtmani akkaunt bazasiga saqlash"""
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, user_name, username, phone, bio, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, 
                            COALESCE((SELECT first_seen FROM users WHERE user_id = ?), CURRENT_TIMESTAMP),
                            CURRENT_TIMESTAMP)
                ''', (user_id, user_name, username, phone, bio, user_id))
                
                cursor.execute('SELECT COALESCE(MAX(order_number), 0) + 1 FROM zakazlar')
                next_order_number = cursor.fetchone()[0]
                
                cursor.execute('''
                    INSERT INTO zakazlar (order_number, user_id, user_type, message, group_name, group_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (next_order_number, user_id, user_type, message, group_name, group_id))
                
                cursor.execute('''
                    DELETE FROM zakazlar WHERE id NOT IN (
                        SELECT id FROM zakazlar ORDER BY sana DESC LIMIT 50
                    )
                ''')
                conn.commit()
                return next_order_number
        except Exception as e:
            logger.error(f"Akkaunt #{self.profile_id} zakaz saqlash: {e}")
            return 0


# ========== YORDAMCHI FUNKSIYALAR ==========

def load_profiles():
    """profiles jadvalidan aktiv profillarni olish"""
    try:
        with get_main_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, session_name, phone FROM profiles WHERE is_active = 1 ORDER BY id')
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"Profillar yuklash: {e}")
        return []

# Legacy funksiyalar (eski rejim uchun)
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

def reklama_matndan_olib_tashlash(text):
    """Reklama xabaridan telefon raqamlar, havolalar va @username ni olib tashlash"""
    if not text or not text.strip():
        return text
    t = text
    # Uzbek phone formatting
    # +998... yoki 998... formatlar
    t = re.sub(r'(?:\+998|998)?[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', '', t)
    t = re.sub(r'(?:\+998|998)?[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{4}', '', t)
    
    # 9-xonali va har xil probellar bilan raqamlar
    t = re.sub(r'\b\d{9}\b', '', t)
    t = re.sub(r'\b\d{2}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}\b', '', t)
    t = re.sub(r'\b\d{2}[\s\-]\d{3}[\s\-]\d{4}\b', '', t)
    
    t = re.sub(r'[Tt]el\.?\s*:?\s*', '', t)
    t = re.sub(r'[Tt]elefon\.?\s*:?\s*', '', t)
    t = re.sub(r'[Rr]aqam\.?\s*:?\s*', '', t)
    t = re.sub(r'https?://[^\s]+', '', t)
    t = re.sub(r't\.me/[^\s]+', '', t)
    t = re.sub(r'tg://[^\s]+', '', t)
    t = re.sub(r'@\w+', '', t)
    return re.sub(r'\s+', ' ', t).strip()
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

ERROR_FILE = "userbot_errors.txt"

class ErrorFileHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            try:
                with open(ERROR_FILE, "a", encoding="utf-8") as f:
                    f.write(self.format(record)[:300] + "\n")
            except:
                pass
logger.addHandler(ErrorFileHandler())


# ========== UMUMIY SOZLAMALAR ==========
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DEFAULT_ORDER_GROUP_ID = int(os.getenv('ORDER_GROUP_ID'))
HAYDOVCHI_ADMIN_USERNAME = os.getenv('HAYDOVCHI_ADMIN_USERNAME', '').strip().lstrip('@')

client = TelegramClient('userbot', API_ID, API_HASH)  # Legacy - profillar bo'lmaganda

MAX_PROCESSED_CACHE = 10000


# ========== UMUMIY DB (profiles, keywords, admins) ==========
@contextmanager
def get_main_db():
    conn = None
    try:
        conn = sqlite3.connect('zakazlar.db', timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Main DB error: {e}")
        raise
    finally:
        if conn:
            conn.close()


# ========== AKKAUNT KONFIGURATSIYASI ==========

class AccountConfig:
    """Har bir akkaunt uchun alohida konfiguratsiya"""
    
    def __init__(self, profile_id, session_name, phone):
        self.profile_id = profile_id
        self.session_name = session_name
        self.phone = phone
        self.config_file = f'account_config_{profile_id}.json'
        self.db_file = f'zakazlar_account{profile_id}.db'
        self.order_group_id = DEFAULT_ORDER_GROUP_ID
        self.monitored_groups = []
        self.reklama_groups = ["@vijdontaxireklama", "@iymontaxi", "@sobirtaxi_vodiy_voha", "@iymontaxigroup"]
        self.bot_username = "vijdonuserbot"
        self.keywords = {"driver": [], "passenger": []}
        self.processed_messages = set()  # Har akkaunt uchun ALOHIDA dublikat kesh
        self.last_keyword_load = 0
        self.last_config_load = 0
        self._load_config()
        self._init_db()
        self._load_keywords()
    
    def _load_config(self):
        """JSON konfiguratsiyasini yuklash"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                self.order_group_id = config.get('order_group_id', DEFAULT_ORDER_GROUP_ID)
                self.monitored_groups = config.get('monitored_groups', [])
                self.reklama_groups = config.get('reklama_groups', self.reklama_groups)
                logger.info(f"Akkaunt #{self.profile_id} konfiguratsiya yuklandi: guruhlar={len(self.monitored_groups)}, buyurtma={self.order_group_id}")
            else:
                # Yangi config yaratish - bo'sh guruhlar bilan (har akkaunt o'zi topadi)
                self._save_config()
                logger.info(f"Akkaunt #{self.profile_id} yangi konfiguratsiya yaratildi (guruhlar avtomatik topiladi)")
        except Exception as e:
            logger.error(f"Akkaunt #{self.profile_id} config yuklash xatolik: {e}")
    
    def _save_config(self):
        """JSON konfiguratsiyasini saqlash"""
        config = {
            'account_id': self.profile_id,
            'order_group_id': self.order_group_id,
            'monitored_groups': self.monitored_groups,
            'reklama_groups': self.reklama_groups
        }
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)
    
    def add_group(self, group_id):
        if group_id not in self.monitored_groups:
            self.monitored_groups.append(group_id)
            self._save_config()
            return True
        return False
    
    def remove_group(self, group_id):
        if group_id in self.monitored_groups:
            self.monitored_groups.remove(group_id)
            self._save_config()
            return True
        return False
    
    @contextmanager
    def get_db(self):
        """Akkaunt uchun alohida DB ulanish"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_file, timeout=30)
            conn.execute('PRAGMA journal_mode=WAL')
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Akkaunt #{self.profile_id} DB error: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def _init_db(self):
        """Akkaunt bazasini yaratish"""
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        user_name TEXT,
                        username TEXT,
                        phone TEXT,
                        bio TEXT,
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
                    CREATE TABLE IF NOT EXISTS blocked_users (
                        user_id INTEGER PRIMARY KEY,
                        blocked_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS order_groups (
                        group_id INTEGER PRIMARY KEY,
                        group_name TEXT,
                        added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
                logger.info(f"Akkaunt #{self.profile_id} DB tayyor: {self.db_file}")
        except Exception as e:
            logger.error(f"Akkaunt #{self.profile_id} DB init xatolik: {e}")
            raise
    
    def _load_keywords(self):
        """Kalit so'zlarni umumiy bazadan yuklash"""
        try:
            with get_main_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT word FROM keywords WHERE type = ?', ('passenger',))
                passenger_words = [row[0] for row in cursor.fetchall()]
                cursor.execute('SELECT word FROM keywords WHERE type = ?', ('driver',))
                driver_words = [row[0] for row in cursor.fetchall()]
                self.keywords = {"passenger": passenger_words, "driver": driver_words}
        except Exception as e:
            logger.error(f"Keywords yuklash xatolik: {e}")
            self.keywords = {"passenger": [], "driver": []}
    
    def is_user_blocked(self, user_id):
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT 1 FROM blocked_users WHERE user_id = ?', (user_id,))
                return cursor.fetchone() is not None
        except:
            return False
    
    def get_user_bio(self, user_id):
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT bio FROM users WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except:
            return None

    def save_user_and_zakaz(self, user_id, user_name, username, phone, user_type, message, group_name, group_id, bio=None):
        """Buyurtmani akkaunt bazasiga saqlash"""
        try:
            with self.get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO users (user_id, user_name, username, phone, bio, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, 
                            COALESCE((SELECT first_seen FROM users WHERE user_id = ?), CURRENT_TIMESTAMP),
                            CURRENT_TIMESTAMP)
                ''', (user_id, user_name, username, phone, bio, user_id))
                
                cursor.execute('SELECT COALESCE(MAX(order_number), 0) + 1 FROM zakazlar')
                next_order_number = cursor.fetchone()[0]
                
                cursor.execute('''
                    INSERT INTO zakazlar (order_number, user_id, user_type, message, group_name, group_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (next_order_number, user_id, user_type, message, group_name, group_id))
                
                cursor.execute('''
                    DELETE FROM zakazlar WHERE id NOT IN (
                        SELECT id FROM zakazlar ORDER BY sana DESC LIMIT 50
                    )
                ''')
                conn.commit()
                return next_order_number
        except Exception as e:
            logger.error(f"Akkaunt #{self.profile_id} zakaz saqlash: {e}")
            return 0


# ========== YORDAMCHI FUNKSIYALAR ==========

def load_profiles():
    """profiles jadvalidan aktiv profillarni olish"""
    try:
        with get_main_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, session_name, phone FROM profiles WHERE is_active = 1 ORDER BY id')
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"Profillar yuklash: {e}")
        return []

# Legacy funksiyalar (eski rejim uchun)
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

def reklama_matndan_olib_tashlash(text):
    """Reklama xabaridan telefon raqamlar, havolalar va @username ni olib tashlash"""
    if not text or not text.strip():
        return text
    t = text
    t = re.sub(r'\+998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', '', t)
    t = re.sub(r'998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', '', t)
    t = re.sub(r'\b9\d{8}\b', '', t)
    t = re.sub(r'\b9\d\s+\d{3}\s+\d{2}\s+\d{2}\b', '', t)
    t = re.sub(r'\b9\d\s+\d{3}\s+\d{4}\b', '', t)
    t = re.sub(r'\b9\d\s+\d{7}\b', '', t)
    t = re.sub(r'\b9\d\s*[-]?\s*\d{3}\s*[-]?\s*\d{2}\s*[-]?\s*\d{2}\b', '', t)
    t = re.sub(r'\b\d{2}\s+\d{3}\s+\d{2}\s+\d{2}\b', '', t)
    t = re.sub(r'\b\d{2}\s+\d{3}\s+\d{4}\b', '', t)
    t = re.sub(r'\b\d{3}\s+\d{2}\s+\d{2}\s+\d{2}\b', '', t)
    t = re.sub(r'\d{2}[\s\-]\d{3}[\s\-]\d{2}[\s\-]\d{2}', '', t)
    t = re.sub(r'[Tt]el\.?\s*:?\s*', '', t)
    t = re.sub(r'[Tt]elefon\.?\s*:?\s*', '', t)
    t = re.sub(r'[Rr]aqam\.?\s*:?\s*', '', t)
    t = re.sub(r'https?://[^\s]+', '', t)
    t = re.sub(r't\.me/[^\s]+', '', t)
    t = re.sub(r'tg://[^\s]+', '', t)
    t = re.sub(r'@\w+', '', t)
    return re.sub(r'\s+', ' ', t).strip()

def init_main_database():
    """Umumiy bazani yaratish (profiles, keywords, admins)"""
    try:
        with get_main_db() as conn:
            cursor = conn.cursor()
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
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    word TEXT NOT NULL,
                    sana DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(type, word)
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
                    sana DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    blocked_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_groups (
                    group_id INTEGER PRIMARY KEY,
                    group_name TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("Umumiy baza tayyor")
    except Exception as e:
        logger.error(f"Umumiy baza init: {e}")
        raise


# ========== XABAR HANDLERLARI (Account-specific) ==========

def create_message_handler(acc: AccountConfig):
    """Har bir akkaunt uchun alohida message handler yaratish"""
    
    async def message_handler(event):
        if not event.is_group:
            return
        
        # Har akkaunt uchun ALOHIDA dublikat tekshiruv
        msg_key = (event.chat_id, event.id)
        if msg_key in acc.processed_messages:
            return
        if len(acc.processed_messages) > MAX_PROCESSED_CACHE:
            acc.processed_messages.clear()
        acc.processed_messages.add(msg_key)
        
        # Konfiguratsiyani yangilash (buyurtma guruhi o'zgargan bo'lishi mumkin)
        if time.time() - acc.last_config_load > 300:
            acc._load_config()
            acc.last_config_load = time.time()
        
        me = await event.client.get_me()
        bot_id = int(BOT_TOKEN.split(':')[0])
        
        if event.sender_id == me.id:
            return
        if event.sender_id == bot_id:
            return
        
        # Akkaunt guruhlariga avtomatik qo'shish
        if event.chat_id not in acc.monitored_groups:
            acc.add_group(event.chat_id)
            pname = f"@{me.username}" if me.username else str(me.id)
            print(f"  📥 Akkaunt #{acc.profile_id}: Yangi guruh qo'shildi: {event.chat_id} (profil: {pname})")
            logger.info(f"Akkaunt #{acc.profile_id} yangi guruh: {event.chat_id}")
        
        is_voice = bool(event.message.voice)
        
        # Ovozli xabarlar sozlamasini tekshirish
        if is_voice:
            voice_enabled = True
            try:
                with get_main_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT value FROM settings WHERE key='voice_orders_enabled'")
                    row = cursor.fetchone()
                    if row:
                        voice_enabled = (row[0] == '1')
            except:
                pass
            
            if not voice_enabled:
                return

        text_content = event.message.message or ""
        
        if is_voice and not text_content:
            text_content = "🎤 Ovozli xabar"

        if not text_content and not is_voice:
            return
            
        if not is_voice:
            if len(text_content) > 150:
                return
            if event.message.sticker:
                return
        
        emoji_pattern = re.compile("[" 
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        if not is_voice and emoji_pattern.search(text_content):
            return
        
        sender = None
        chat = None
        try:
            sender = await event.get_sender()
        except:
            pass
        try:
            chat = await event.get_chat()
        except:
            pass
        
        user_id = 0
        user_info = "👤 Foydalanuvchi"
        user_details_parts = []
        user_bio = ""
        
        if sender:
            try:
                if getattr(sender, 'bot', False):
                    return
                if sender.id == me.id or sender.id == bot_id:
                    return
                user_id = sender.id
                user_name = f"{sender.first_name or 'Nomaʼlum'}"
                if hasattr(sender, 'last_name') and sender.last_name:
                    user_name = f"{sender.first_name} {sender.last_name}"
                user_info = f"👤 <a href='tg://user?id={sender.id}'>{user_name}</a>"
                if hasattr(sender, 'username') and sender.username:
                    user_details_parts.append(f"🤙 @{sender.username}")
                if hasattr(sender, 'phone') and sender.phone:
                    user_details_parts.append(f"☎️ +{sender.phone}")

            except:
                user_info = "👤 Noma'lum foydalanuvchi"
        if user_id > 0:
            try:
                with get_main_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,))
                    if cursor.fetchone(): return
                if acc.is_user_blocked(user_id): return
            except: pass
        
        # Xabar va guruh havolalari
        message_link = "#"
        group_info = "🫂 Guruh"
        if chat:
            try:
                if str(chat.id).startswith('-100'):
                    chat_id_str = str(chat.id)[4:]
                    message_link = f"https://t.me/c/{chat_id_str}/{event.id}"
                elif hasattr(chat, 'username') and chat.username:
                    message_link = f"https://t.me/{chat.username}/{event.id}"
                group_info = f"🫂 {chat.title}" if hasattr(chat, 'title') and chat.title else "🫂 Guruh"
            except:
                pass
        
        # Telefon qidirish
        phone_patterns = [r'\+998\d{9}', r'998\d{9}', r'\d{9}', r'\d{2}\s\d{3}\s\d{2}\s\d{2}', r'\d{2}-\d{3}-\d{2}-\d{2}']
        phones = []
        for pattern in phone_patterns:
            found = re.findall(pattern, text_content)
            phones.extend(found)
            if phones:
                break
        
        # Kalit so'z tekshirish 
        if not is_voice:
            if time.time() - acc.last_keyword_load > 600:
                acc._load_keywords()
                acc.last_keyword_load = time.time()
            text_lower = text_content.lower().strip()
            
            has_driver_words = any(w.lower() in text_lower for w in acc.keywords['driver'])
            has_passenger_words = any(w.lower() in text_lower for w in acc.keywords['passenger'])
            
            if has_driver_words:
                return
            if not has_passenger_words:
                return
        
        user_type = '🙋♂️ Yolovchi'
        is_blocked = acc.is_user_blocked(user_id)
        
        # Bio faqat zakaz aniqlangandan keyin tortiladi, shunda Flood Wait kamayadi
        if not user_bio and sender and hasattr(sender, 'id'):
            user_bio = acc.get_user_bio(sender.id)
            if not user_bio:
                try:
                    full_user = await event.client(GetFullUserRequest(sender.id))
                    if full_user.full_user.about:
                        about_text = full_user.full_user.about
                        if len(about_text) > 80: about_text = about_text[:77] + "..."
                        user_bio = html.escape(about_text)
                    else:
                        user_bio = " " # Mark as checked but empty
                except:
                    pass
        
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
        
        # Akkaunt bazasiga saqlash
        order_number = acc.save_user_and_zakaz(user_id, clean_user_name.strip(), username, phone, user_type, text_content, chat_title, event.chat_id, user_bio)
        
        if is_blocked:
            return
        
        # AKKAUNT O'Z BUYURTMA GURUHIGA YUBORISH
        ORDER_GID = acc.order_group_id
        
        # Bot orqali buyurtma yuborish
        try:
            async with aiohttp.ClientSession() as session:
                api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                
                esc_user_name = html.escape(user_name)
                esc_text = html.escape(text_content.strip() if text_content else "")
                
                # Sarlavhani olish
                order_header = ""
                try:
                    with get_main_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT value FROM settings WHERE key='order_header'")
                        row = cursor.fetchone()
                        if row: order_header = row[0]
                except: pass
                
                # 1. ASOSIY ZAKAZ XABARI
                message_parts = []
                if order_header:
                    # Agar sarlavha bo'lsa, unga #number qo'shamiz
                    if order_header.endswith('!'):
                        message_parts.append(f"{order_header} #{order_number}")
                    else:
                        message_parts.append(f"{order_header} #{order_number}")
                else:
                    message_parts.append(f"<b>Yangi buyurtma #{order_number}</b>")

                message_parts.append(f"👤 <a href='tg://user?id={user_id}'>{esc_user_name}</a>")
                if user_bio and user_bio.strip():
                    message_parts.append(f"ℹ️ <i>{user_bio}</i>")
                if username:
                    message_parts.append(f"🤙 @{username}")
                if esc_text:
                    message_parts.append(f"💬 <b><i>{esc_text}</i></b>")
                if phones:
                    phone_num = phones[0].replace(' ', '').replace('-', '')
                    if phone_num.startswith('998'): phone_num = '+' + phone_num
                    elif not phone_num.startswith('+998'): phone_num = '+998' + phone_num
                    message_parts.append(f"📞 {phone_num}")
                elif sender and hasattr(sender, 'phone') and sender.phone:
                    message_parts.append(f"📞 +{sender.phone}")
                
                caption = "\n\n".join(message_parts)
                
                # Tugmalar
                buttons = []
                row1 = []
                phone_to_call = None
                if phones:
                    phone_to_call = phones[0].replace(' ', '').replace('-', '')
                elif sender and hasattr(sender, 'phone') and sender.phone:
                    phone_to_call = sender.phone
                if phone_to_call:
                    if phone_to_call.startswith('998'): phone_to_call = '+' + phone_to_call
                    elif not phone_to_call.startswith('+'): phone_to_call = '+998' + phone_to_call
                    row1.append({"text": "📞 Qo'ngiroq", "url": f"https://onmap.uz/tel/{phone_to_call}"})
                if message_link and message_link != "#":
                    row1.append({"text": "🔍 Xabar", "url": message_link})
                if row1:
                    buttons.append(row1)
                
                row2 = []
                if user_id and user_id > 0:
                    row2.append({"text": "✍️ Yozish", "callback_data": f"write_{user_id}_{username or ''}"})
                    row2.append({"text": "🚫 Bloklash", "callback_data": f"block_{user_id}"})
                elif username:
                    row2.append({"text": "✍️ Yozish", "callback_data": f"write_0_{username}"})
                    row2.append({"text": "🚫 Bloklash", "callback_data": f"block_0_{username}"})
                    
                if row2:
                    buttons.append(row2)
                
                payload = {
                    "chat_id": ORDER_GID,
                    "text": caption,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": buttons} if buttons else None
                }
                
                # Asosiy guruhga yuborish
                try:
                    target_url = api_url
                    send_data = None
                    if is_voice:
                        target_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice"
                        voice_file = await event.message.download_media(file=bytes)
                        
                        form = aiohttp.FormData()
                        form.add_field('chat_id', str(ORDER_GID))
                        form.add_field('caption', caption)
                        form.add_field('parse_mode', 'HTML')
                        if buttons:
                            form.add_field('reply_markup', json.dumps({"inline_keyboard": buttons}))
                        form.add_field('voice', voice_file, filename='voice.ogg', content_type='audio/ogg')
                        send_data = form
                    else:
                        send_data = {
                            "chat_id": ORDER_GID,
                            "text": caption,
                            "parse_mode": "HTML",
                            "reply_markup": {"inline_keyboard": buttons} if buttons else None
                        }

                    if is_voice:
                         async with session.post(target_url, data=send_data) as resp:
                            if resp.status == 200:
                                print(f"{G}✅ BOT ZAKAZ (Ovozli) #{order_number} -> {ORDER_GID}{W} {W}")
                            else:
                                resp_text = await resp.text()
                                await event.client.send_message(entity=ORDER_GID, message=caption, parse_mode='html')
                                print(f"{R}❌ BOT VOICE XATOLIK: {resp.status}, detail: {resp_text}{W} {W}")
                    else:
                        async with session.post(target_url, json=send_data) as resp:
                            if resp.status == 200:
                                print(f"{G}✅ BOT ZAKAZ #{order_number} -> {ORDER_GID}{W} {W}")
                            else:
                                resp_text = await resp.text()
                                await event.client.send_message(entity=ORDER_GID, message=caption, parse_mode='html')
                                print(f"{R}❌ BOT XATOLIK: {resp.status}, detail: {resp_text}{W} {W}")
                except Exception as send_err:
                    logger.error(f"Bot yuborish xatolik: {send_err}")
                    await event.client.send_message(entity=ORDER_GID, message=caption, parse_mode='html')

                # 3. REKLAMA GURUHLARGA
                try:
                    clean_text = reklama_matndan_olib_tashlash(text_content or "")
                    if clean_text:
                        spec_msg = (
                            f"🚕 <b>Assalomu alaykum hurmatli haydovchilar</b>\n"
                            f"{html.escape(clean_text)}\n"
                            f"<b>Buyurtmalar guruhiga qo'shilish uchun 👇</b>"
                        )
                        admin_link = f"https://t.me/{HAYDOVCHI_ADMIN_USERNAME}" if HAYDOVCHI_ADMIN_USERNAME else f"https://t.me/{acc.bot_username}?start=haydovchi"
                        spec_btns = [[{"text": "👨‍💻 Operator bilan bog'lanish", "url": admin_link}]]
                        
                        for sg in acc.reklama_groups:
                            try:
                                if is_voice and 'voice_file' in locals():
                                    form = aiohttp.FormData()
                                    form.add_field('chat_id', str(sg))
                                    form.add_field('caption', spec_msg)
                                    form.add_field('parse_mode', 'HTML')
                                    form.add_field('reply_markup', json.dumps({"inline_keyboard": spec_btns}))
                                    form.add_field('voice', voice_file, filename='voice.ogg', content_type='audio/ogg')
                                    await session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice", data=form)
                                else:
                                    await session.post(api_url, json={
                                        "chat_id": sg,
                                        "text": spec_msg,
                                        "parse_mode": "HTML",
                                        "reply_markup": {"inline_keyboard": spec_btns}
                                    })
                                await asyncio.sleep(0.3)
                            except: pass
                except Exception as re_err:
                    logger.error(f"Reklama xatolik: {re_err}")

                # 4. QO'SHIMCHA GURUHLARGA
                try:
                    with acc.get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT group_id FROM order_groups')
                        extra_gids = [r[0] for r in cursor.fetchall()]
                    for egid in extra_gids:
                        if is_voice and 'voice_file' in locals():
                            form = aiohttp.FormData()
                            form.add_field('chat_id', str(egid))
                            form.add_field('caption', caption)
                            form.add_field('parse_mode', 'HTML')
                            if buttons:
                                form.add_field('reply_markup', json.dumps({"inline_keyboard": buttons}))
                            form.add_field('voice', voice_file, filename='voice.ogg', content_type='audio/ogg')
                            await session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice", data=form)
                        else:
                            payload["chat_id"] = egid
                            await session.post(api_url, json=payload)
                except: pass

        except Exception as e:
            logger.error(f"Akkaunt #{acc.profile_id} bot yuborish: {e}")
    
    return message_handler


def create_chat_action_handler(acc: AccountConfig):
    """Har bir akkaunt uchun chat action handler"""
    
    async def chat_action_handler(event):
        try:
            me = await event.client.get_me()
            chat = await event.get_chat()
            chat_title = chat.title if hasattr(chat, 'title') else 'Nomaʼlum guruh'
            
            if event.user_left or event.user_kicked:
                if event.user_id == me.id and event.chat_id in acc.monitored_groups:
                    acc.remove_group(event.chat_id)
                    logger.info(f"Akkaunt #{acc.profile_id} guruhdan chiqarildi: {chat_title}")
            elif event.new_title is not None:
                logger.info(f"Akkaunt #{acc.profile_id} guruh nomi: {chat_title}")
            elif event.user_joined:
                s = await event.get_user()
                logger.info(f"Akkaunt #{acc.profile_id} guruhga qo'shildi: {s.first_name} - {chat_title}")
        except Exception as e:
            logger.error(f"Akkaunt #{acc.profile_id} chat action: {e}")
    
    return chat_action_handler


def register_account_handlers(c, acc: AccountConfig):
    """Handlerlarni akkaunt clientga bog'lash"""
    c.add_event_handler(create_chat_action_handler(acc), events.ChatAction)
    c.add_event_handler(create_message_handler(acc), events.NewMessage(incoming=True))


def register_account_commands(c, acc: AccountConfig):
    """Buyruqlarni akkaunt clientga bog'lash"""
    @c.on(events.NewMessage(pattern=r'/block (\d+)'))
    async def _block(event):
        if event.is_private:
            uid = int(event.pattern_match.group(1))
            try:
                with acc.get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('INSERT OR REPLACE INTO blocked_users (user_id) VALUES (?)', (uid,))
                    conn.commit()
                await event.reply(f"🚫 Akkaunt #{acc.profile_id}: Bloklandi: {uid}")
            except Exception as e:
                await event.reply(f"❌ Xatolik: {e}")
    
    @c.on(events.NewMessage(pattern=r'/unblock (\d+)'))
    async def _unblock(event):
        if event.is_private:
            uid = int(event.pattern_match.group(1))
            try:
                with acc.get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM blocked_users WHERE user_id = ?', (uid,))
                    conn.commit()
                await event.reply(f"✅ Akkaunt #{acc.profile_id}: Blokdan chiqarildi: {uid}")
            except Exception as e:
                await event.reply(f"❌ Xatolik: {e}")
    
    @c.on(events.NewMessage(pattern='/groups'))
    async def _groups(event):
        if event.is_private:
            if acc.monitored_groups:
                info = []
                for gid in acc.monitored_groups[:20]:
                    try:
                        ch = await event.client.get_entity(gid)
                        info.append(f"• {ch.title} ({gid})")
                    except:
                        info.append(f"• ID: {gid}")
                await event.reply(f"📋 Akkaunt #{acc.profile_id} guruhlar ({len(acc.monitored_groups)}):\n" + "\n".join(info))
            else:
                await event.reply(f"📭 Akkaunt #{acc.profile_id}: Guruh yo'q")
    
    @c.on(events.NewMessage(pattern='/help'))
    async def _help(event):
        if event.is_private:
            await event.reply(f"""🤖 Akkaunt #{acc.profile_id} buyruqlari:
/groups - Guruhlar
/block ID - Bloklash
/unblock ID - Blokdan chiqarish
/help - Yordam
📤 Buyurtma guruhi: {acc.order_group_id}
📊 Guruhlar: {len(acc.monitored_groups)}
💾 Baza: {acc.db_file}""")


# ========== ISHGA TUSHIRISH ==========

async def run_account(c, acc: AccountConfig):
    """Bir akkauntni ishga tushirish"""
    await c.connect()
    if not await c.is_user_authorized():
        raise RuntimeError(f"Akkaunt #{acc.profile_id} avtorizatsiya qilinmagan: {acc.phone}")
    me = await c.get_me()
    profile_name = f"@{me.username}" if me.username else f"+{me.phone}" if me.phone else str(me.id)
    print(f"  {G}✅ Akkaunt #{acc.profile_id} ulandi: {profile_name} {W}")
    print(f"     📤 Buyurtma guruhi: {acc.order_group_id}")
    print(f"     📊 Guruhlar: {len(acc.monitored_groups)}")
    print(f"     💾 Baza: {acc.db_file}")
    logger.info(f"Akkaunt #{acc.profile_id} ulandi: {profile_name}")
    register_account_handlers(c, acc)
    register_account_commands(c, acc)
    await c.run_until_disconnected()


async def poll_pending_messages(clients):
    while True:
        try:
            with get_main_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS pending_userbot_messages (
                                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  chat_id INTEGER,
                                  message TEXT
                                  )''')
                cursor.execute("SELECT id, chat_id, message FROM pending_userbot_messages")
                rows = cursor.fetchall()
                for row in rows:
                    msg_id, chat_id, text = row
                    if clients:
                        try:
                            # Send message using the first client
                            client = clients[0]
                            await client.send_message(entity=chat_id, message=text, parse_mode='html')
                        except Exception as e:
                            logger.error(f"Pending send error: {e}")
                        finally:
                            cursor.execute("DELETE FROM pending_userbot_messages WHERE id = ?", (msg_id,))
                            conn.commit()
        except Exception as e:
            pass
        await asyncio.sleep(2)

async def main():
    bot_username = await get_bot_username()
    print(f"🤖 Bot: @{bot_username}")
    
    print("💾 Umumiy bazani tekshirish...")
    init_main_database()
    print("✅ Umumiy baza tayyor")
    
    # Default kalit so'zlar
    try:
        with get_main_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM keywords WHERE type = ?', ('passenger',))
            if cursor.fetchone()[0] == 0:
                for w in ["kerak", "ketish kerak", "olib keting", "yo'lovchi kerak", "borish kerak", "ketmoqchiman"]:
                    cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', ('passenger', w))
            cursor.execute('SELECT COUNT(*) FROM keywords WHERE type = ?', ('driver',))
            if cursor.fetchone()[0] == 0:
                for w in ["ketaman", "boraman", "olib ketaman", "haydovchiman", "mashina bor", "taksi"]:
                    cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', ('driver', w))
            conn.commit()
    except Exception as e:
        logger.error(f"Default so'zlar: {e}")
    
    profiles = load_profiles()
    
    try:
        if profiles:
            print(f"\n👤 {len(profiles)} ta akkaunt yuklandi")
            print(f"{B}{"=" * 60}{W}")
            
            accounts = []
            for pid, session_name, phone in profiles:
                acc = AccountConfig(pid, session_name, phone)
                acc.bot_username = bot_username
                c = TelegramClient(session_name, API_ID, API_HASH)
                accounts.append((c, acc))
            
            print(f"{G}✅ USERBOT ISHGA TUSHDI! (Ko'p akkaunt rejimi){W} {W}")
            print(f"{B}{"=" * 60}{W}")
            for c, acc in accounts:
                print(f"  📱 {Y}Akkaunt #{acc.profile_id}{W}: {G}{acc.phone or acc.session_name}{W}")
                print(f"     📤 Buyurtma: {B}{acc.order_group_id}{W}")
                print(f"     📊 Guruhlar: {len(acc.monitored_groups)}")
                print(f"     💾 Baza: {acc.db_file}")
            print("=" * 60 + "\n")
            
            async def run_one(idx):
                c, acc = accounts[idx]
                try:
                    await run_account(c, acc)
                except Exception as e:
                    logger.error(f"Akkaunt #{acc.profile_id} xatolik: {e}")
                    print(f"{R}❌ Akkaunt #{acc.profile_id}: {e} {W}")
            
            tasks = [run_one(i) for i in range(len(accounts))]
            tasks.append(poll_pending_messages([c for c, acc in accounts]))
            await asyncio.gather(*tasks)
        else:
            # Legacy: bitta userbot sessiya
            await client.connect()
            if not await client.is_user_authorized():
                phone = input("Telefon raqamingiz (+998xxxxxxxxx): ")
                print("🤙 Kod yuborildi...")
                await client.send_code_request(phone)
                code = input("Telegram kodini kiriting: ")
                try:
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    await client.sign_in(password=input("2FA paroli: "))
            
            # Legacy rejimda eski usulda ishlash
            legacy_groups = load_groups()
            
            async def legacy_msg_handler(event):
                # Eski usulda ishlash (bitta akkaunt)
                pass
            
            # Legacy uchun ham AccountConfig yaratish
            legacy_acc = AccountConfig(0, 'userbot', '')
            legacy_acc.monitored_groups = legacy_groups
            legacy_acc.db_file = 'zakazlar.db'
            legacy_acc.bot_username = bot_username
            
            register_account_handlers(client, legacy_acc)
            register_account_commands(client, legacy_acc)
            
            print(f"{B}\n" + "=" * 60 + W)
            print(f"{G}✅ USERBOT ISHGA TUSHDI! (Bitta akkaunt){W} {W}")
            print(f"{B}{"=" * 60}{W}")
            print(f"📊 Guruhlar: {len(legacy_groups)} | Buyurtma: {DEFAULT_ORDER_GROUP_ID}")
            print("=" * 60 + "\n")
            asyncio.create_task(poll_pending_messages([client]))
            await client.run_until_disconnected()
    except Exception as e:
        logger.critical(f"KRITIK XATOLIK: {e}")
        print(f"\n❌ KRITIK XATOLIK: {e}\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot to'xtatildi")
    """Umumiy bazani yaratish (profiles, keywords, admins)"""
    try:
        with get_main_db() as conn:
            cursor = conn.cursor()
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
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    word TEXT NOT NULL,
                    sana DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(type, word)
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
                    sana DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    blocked_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_groups (
                    group_id INTEGER PRIMARY KEY,
                    group_name TEXT,
                    added_date DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logger.info("Umumiy baza tayyor")
    except Exception as e:
        logger.error(f"Umumiy baza init: {e}")
        raise


# ========== XABAR HANDLERLARI (Account-specific) ==========

def create_message_handler(acc: AccountConfig):
    """Har bir akkaunt uchun alohida message handler yaratish"""
    
    async def message_handler(event):
        if not event.is_group:
            return
        
        # Har akkaunt uchun ALOHIDA dublikat tekshiruv
        msg_key = (event.chat_id, event.id)
        if msg_key in acc.processed_messages:
            return
        if len(acc.processed_messages) > MAX_PROCESSED_CACHE:
            acc.processed_messages.clear()
        acc.processed_messages.add(msg_key)
        
        # Konfiguratsiyani yangilash (buyurtma guruhi o'zgargan bo'lishi mumkin)
        if time.time() - acc.last_config_load > 300:
            acc._load_config()
            acc.last_config_load = time.time()
        
        me = await event.client.get_me()
        bot_id = int(BOT_TOKEN.split(':')[0])
        
        if event.sender_id == me.id:
            return
        if event.sender_id == bot_id:
            return
        
        # Akkaunt guruhlariga avtomatik qo'shish
        if event.chat_id not in acc.monitored_groups:
            acc.add_group(event.chat_id)
            pname = f"@{me.username}" if me.username else str(me.id)
            print(f"  📥 Akkaunt #{acc.profile_id}: Yangi guruh qo'shildi: {event.chat_id} (profil: {pname})")
            logger.info(f"Akkaunt #{acc.profile_id} yangi guruh: {event.chat_id}")
        
        is_voice = bool(event.message.voice)
        
        # Ovozli xabarlar sozlamasini tekshirish
        if is_voice:
            voice_enabled = True
            try:
                with get_main_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT value FROM settings WHERE key='voice_orders_enabled'")
                    row = cursor.fetchone()
                    if row:
                        voice_enabled = (row[0] == '1')
            except:
                pass
            
            if not voice_enabled:
                return

        text_content = event.message.message or ""
        
        if is_voice and not text_content:
            text_content = "🎤 Ovozli xabar"

        if not text_content and not is_voice:
            return
            
        if not is_voice:
            if len(text_content) > 150:
                return
            if event.message.sticker:
                return
        
        emoji_pattern = re.compile("[" 
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        if not is_voice and emoji_pattern.search(text_content):
            return
        
        sender = None
        chat = None
        try:
            sender = await event.get_sender()
        except:
            pass
        try:
            chat = await event.get_chat()
        except:
            pass
        
        user_id = 0
        user_info = "👤 Foydalanuvchi"
        user_details_parts = []
        user_bio = ""
        
        if sender:
            try:
                if getattr(sender, "bot", False): return
                if sender.id == me.id or sender.id == bot_id:
                    return
                user_id = sender.id
                user_name = f"{sender.first_name or 'Nomaʼlum'}"
                if hasattr(sender, 'last_name') and sender.last_name:
                    user_name = f"{sender.first_name} {sender.last_name}"
                user_info = f"👤 <a href='tg://user?id={sender.id}'>{user_name}</a>"
                if hasattr(sender, 'username') and sender.username:
                    user_details_parts.append(f"🤙 @{sender.username}")
                if hasattr(sender, 'phone') and sender.phone:
                    user_details_parts.append(f"☎️ +{sender.phone}")

            except:
                user_info = "👤 Noma'lum foydalanuvchi"
        
        # Xabar va guruh havolalari
        if user_id > 0:
            try:
                with get_main_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,))
                    if cursor.fetchone(): return
                if acc.is_user_blocked(user_id): return
            except: pass
        message_link = "#"
        group_info = "🫂 Guruh"
        if chat:
            try:
                if str(chat.id).startswith('-100'):
                    chat_id_str = str(chat.id)[4:]
                    message_link = f"https://t.me/c/{chat_id_str}/{event.id}"
                elif hasattr(chat, 'username') and chat.username:
                    message_link = f"https://t.me/{chat.username}/{event.id}"
                group_info = f"🫂 {chat.title}" if hasattr(chat, 'title') and chat.title else "🫂 Guruh"
            except:
                pass
        
        # Telefon qidirish
        phone_patterns = [r'\+998\d{9}', r'998\d{9}', r'\d{9}', r'\d{2}\s\d{3}\s\d{2}\s\d{2}', r'\d{2}-\d{3}-\d{2}-\d{2}']
        phones = []
        for pattern in phone_patterns:
            found = re.findall(pattern, text_content)
            phones.extend(found)
            if phones:
                break
        
        # Kalit so'z tekshirish 
        if not is_voice:
            if time.time() - acc.last_keyword_load > 600:
                acc._load_keywords()
                acc.last_keyword_load = time.time()
            text_lower = text_content.lower().strip()
            
            has_driver_words = any(w.lower() in text_lower for w in acc.keywords['driver'])
            has_passenger_words = any(w.lower() in text_lower for w in acc.keywords['passenger'])
            
            if has_driver_words:
                return
            if not has_passenger_words:
                return
        
        user_type = '🙋♂️ Yolovchi'
        is_blocked = acc.is_user_blocked(user_id)
        
        # Bio faqat zakaz aniqlangandan keyin tortiladi, shunda Flood Wait kamayadi
        if not user_bio and sender and hasattr(sender, 'id'):
            user_bio = acc.get_user_bio(sender.id)
            if not user_bio:
                try:
                    full_user = await event.client(GetFullUserRequest(sender.id))
                    if full_user.full_user.about:
                        about_text = full_user.full_user.about
                        if len(about_text) > 80: about_text = about_text[:77] + "..."
                        user_bio = html.escape(about_text)
                    else:
                        user_bio = " " # Mark as checked but empty
                except:
                    pass
        
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
        
        # Akkaunt bazasiga saqlash
        order_number = acc.save_user_and_zakaz(user_id, clean_user_name.strip(), username, phone, user_type, text_content, chat_title, event.chat_id, user_bio)
        
        if is_blocked:
            return
        
        # AKKAUNT O'Z BUYURTMA GURUHIGA YUBORISH
        ORDER_GID = acc.order_group_id
        
        # Bot orqali buyurtma yuborish
        try:
            async with aiohttp.ClientSession() as session:
                api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                
                esc_user_name = html.escape(user_name)
                esc_text = html.escape(text_content.strip() if text_content else "")
                
                # Sarlavhani olish
                order_header = ""
                try:
                    with get_main_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT value FROM settings WHERE key='order_header'")
                        row = cursor.fetchone()
                        if row: order_header = row[0]
                except: pass
                
                # 1. ASOSIY ZAKAZ XABARI
                message_parts = []
                if order_header:
                    message_parts.append(f"{order_header} #{order_number}")
                else:
                    message_parts.append(f"<b>Yangi buyurtma #{order_number}</b>")

                message_parts.append(f"👤 <a href='tg://user?id={user_id}'>{esc_user_name}</a>")
                if user_bio and user_bio.strip():
                    message_parts.append(f"ℹ️ <i>{user_bio}</i>")
                if username:
                    message_parts.append(f"🤙 @{username}")
                if esc_text:
                    message_parts.append(f"💬 <b><i>{esc_text}</i></b>")
                if phones:
                    phone_num = phones[0].replace(' ', '').replace('-', '')
                    if phone_num.startswith('998'): phone_num = '+' + phone_num
                    elif not phone_num.startswith('+998'): phone_num = '+998' + phone_num
                    message_parts.append(f"📞 {phone_num}")
                elif sender and hasattr(sender, 'phone') and sender.phone:
                    message_parts.append(f"📞 +{sender.phone}")
                
                caption = "\n\n".join(message_parts)
                
                # Tugmalar
                buttons = []
                row1 = []
                phone_to_call = None
                if phones:
                    phone_to_call = phones[0].replace(' ', '').replace('-', '')
                elif sender and hasattr(sender, 'phone') and sender.phone:
                    phone_to_call = sender.phone
                if phone_to_call:
                    if phone_to_call.startswith('998'): phone_to_call = '+' + phone_to_call
                    elif not phone_to_call.startswith('+'): phone_to_call = '+998' + phone_to_call
                    row1.append({"text": "📞 Qo'ngiroq", "url": f"https://onmap.uz/tel/{phone_to_call}"})
                if message_link and message_link != "#":
                    row1.append({"text": "🔍 Xabar", "url": message_link})
                if row1:
                    buttons.append(row1)
                
                row2 = []
                if user_id and user_id > 0:
                    row2.append({"text": "✍️ Yozish", "callback_data": f"write_{user_id}_{username or ''}"})
                    row2.append({"text": "🚫 Bloklash", "callback_data": f"block_{user_id}"})
                elif username:
                    row2.append({"text": "✍️ Yozish", "callback_data": f"write_0_{username}"})
                    row2.append({"text": "🚫 Bloklash", "callback_data": f"block_0_{username}"})
                    
                if row2:
                    buttons.append(row2)
                
                payload = {
                    "chat_id": ORDER_GID,
                    "text": caption,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": buttons} if buttons else None
                }
                
                # Asosiy guruhga yuborish
                try:
                    target_url = api_url
                    send_data = None
                    if is_voice:
                        target_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice"
                        voice_file = await event.message.download_media(file=bytes)
                        
                        form = aiohttp.FormData()
                        form.add_field('chat_id', str(ORDER_GID))
                        form.add_field('caption', caption)
                        form.add_field('parse_mode', 'HTML')
                        if buttons:
                            form.add_field('reply_markup', json.dumps({"inline_keyboard": buttons}))
                        form.add_field('voice', voice_file, filename='voice.ogg', content_type='audio/ogg')
                        send_data = form
                    else:
                        send_data = {
                            "chat_id": ORDER_GID,
                            "text": caption,
                            "parse_mode": "HTML",
                            "reply_markup": {"inline_keyboard": buttons} if buttons else None
                        }

                    if is_voice:
                         async with session.post(target_url, data=send_data) as resp:
                            if resp.status == 200:
                                print(f"{G}✅ BOT ZAKAZ (Ovozli) #{order_number} -> {ORDER_GID}{W} {W}")
                            else:
                                resp_text = await resp.text()
                                await event.client.send_message(entity=ORDER_GID, message=caption, parse_mode='html')
                                print(f"{R}❌ BOT VOICE XATOLIK: {resp.status}, detail: {resp_text}{W} {W}")
                    else:
                        async with session.post(target_url, json=send_data) as resp:
                            if resp.status == 200:
                                print(f"{G}✅ BOT ZAKAZ #{order_number} -> {ORDER_GID}{W} {W}")
                            else:
                                resp_text = await resp.text()
                                await event.client.send_message(entity=ORDER_GID, message=caption, parse_mode='html')
                                print(f"{R}❌ BOT XATOLIK: {resp.status}, detail: {resp_text}{W} {W}")
                except Exception as send_err:
                    logger.error(f"Bot yuborish xatolik: {send_err}")
                    await event.client.send_message(entity=ORDER_GID, message=caption, parse_mode='html')

                # 3. REKLAMA GURUHLARGA
                try:
                    clean_text = reklama_matndan_olib_tashlash(text_content or "")
                    if clean_text:
                        spec_msg = (
                            f"🚕 <b>Assalomu alaykum hurmatli haydovchilar</b>\n"
                            f"{html.escape(clean_text)}\n"
                            f"<b>Buyurtmalar guruhiga qo'shilish uchun 👇</b>"
                        )
                        admin_link = f"https://t.me/{HAYDOVCHI_ADMIN_USERNAME}" if HAYDOVCHI_ADMIN_USERNAME else f"https://t.me/{acc.bot_username}?start=haydovchi"
                        spec_btns = [[{"text": "👨‍💻 Operator bilan bog'lanish", "url": admin_link}]]
                        
                        for sg in acc.reklama_groups:
                            try:
                                if is_voice and 'voice_file' in locals():
                                    form = aiohttp.FormData()
                                    form.add_field('chat_id', str(sg))
                                    form.add_field('caption', spec_msg)
                                    form.add_field('parse_mode', 'HTML')
                                    form.add_field('reply_markup', json.dumps({"inline_keyboard": spec_btns}))
                                    form.add_field('voice', voice_file, filename='voice.ogg', content_type='audio/ogg')
                                    await session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice", data=form)
                                else:
                                    await session.post(api_url, json={
                                        "chat_id": sg,
                                        "text": spec_msg,
                                        "parse_mode": "HTML",
                                        "reply_markup": {"inline_keyboard": spec_btns}
                                    })
                                await asyncio.sleep(0.3)
                            except: pass
                except Exception as re_err:
                    logger.error(f"Reklama xatolik: {re_err}")

                # 4. QO'SHIMCHA GURUHLARGA (Bot orqali)
                try:
                    with acc.get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute('SELECT group_id FROM order_groups')
                        extra_gids = [r[0] for r in cursor.fetchall()]
                    for egid in extra_gids:
                        if is_voice and 'voice_file' in locals():
                            form = aiohttp.FormData()
                            form.add_field('chat_id', str(egid))
                            form.add_field('caption', caption)
                            form.add_field('parse_mode', 'HTML')
                            if buttons:
                                form.add_field('reply_markup', json.dumps({"inline_keyboard": buttons}))
                            form.add_field('voice', voice_file, filename='voice.ogg', content_type='audio/ogg')
                            await session.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendVoice", data=form)
                        else:
                            payload["chat_id"] = egid
                            await session.post(api_url, json=payload)
                except: pass

        except Exception as e:
            logger.error(f"Akkaunt #{acc.profile_id} bot yuborish: {e}")
    
    return message_handler


def create_chat_action_handler(acc: AccountConfig):
    """Har bir akkaunt uchun chat action handler"""
    
    async def chat_action_handler(event):
        try:
            me = await event.client.get_me()
            chat = await event.get_chat()
            chat_title = chat.title if hasattr(chat, 'title') else 'Nomaʼlum guruh'
            
            if event.user_left or event.user_kicked:
                if event.user_id == me.id and event.chat_id in acc.monitored_groups:
                    acc.remove_group(event.chat_id)
                    logger.info(f"Akkaunt #{acc.profile_id} guruhdan chiqarildi: {chat_title}")
            elif event.new_title is not None:
                logger.info(f"Akkaunt #{acc.profile_id} guruh nomi: {chat_title}")
            elif event.user_joined:
                s = await event.get_user()
                logger.info(f"Akkaunt #{acc.profile_id} guruhga qo'shildi: {s.first_name} - {chat_title}")
        except Exception as e:
            logger.error(f"Akkaunt #{acc.profile_id} chat action: {e}")
    
    return chat_action_handler


def register_account_handlers(c, acc: AccountConfig):
    """Handlerlarni akkaunt clientga bog'lash"""
    c.add_event_handler(create_chat_action_handler(acc), events.ChatAction)
    c.add_event_handler(create_message_handler(acc), events.NewMessage(incoming=True))


def register_account_commands(c, acc: AccountConfig):
    """Buyruqlarni akkaunt clientga bog'lash"""
    @c.on(events.NewMessage(pattern=r'/block (\d+)'))
    async def _block(event):
        if event.is_private:
            uid = int(event.pattern_match.group(1))
            try:
                with acc.get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('INSERT OR REPLACE INTO blocked_users (user_id) VALUES (?)', (uid,))
                    conn.commit()
                await event.reply(f"🚫 Akkaunt #{acc.profile_id}: Bloklandi: {uid}")
            except Exception as e:
                await event.reply(f"❌ Xatolik: {e}")
    
    @c.on(events.NewMessage(pattern=r'/unblock (\d+)'))
    async def _unblock(event):
        if event.is_private:
            uid = int(event.pattern_match.group(1))
            try:
                with acc.get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM blocked_users WHERE user_id = ?', (uid,))
                    conn.commit()
                await event.reply(f"✅ Akkaunt #{acc.profile_id}: Blokdan chiqarildi: {uid}")
            except Exception as e:
                await event.reply(f"❌ Xatolik: {e}")
    
    @c.on(events.NewMessage(pattern='/groups'))
    async def _groups(event):
        if event.is_private:
            if acc.monitored_groups:
                info = []
                for gid in acc.monitored_groups[:20]:
                    try:
                        ch = await event.client.get_entity(gid)
                        info.append(f"• {ch.title} ({gid})")
                    except:
                        info.append(f"• ID: {gid}")
                await event.reply(f"📋 Akkaunt #{acc.profile_id} guruhlar ({len(acc.monitored_groups)}):\n" + "\n".join(info))
            else:
                await event.reply(f"📭 Akkaunt #{acc.profile_id}: Guruh yo'q")
    
    @c.on(events.NewMessage(pattern='/help'))
    async def _help(event):
        if event.is_private:
            await event.reply(f"""🤖 Akkaunt #{acc.profile_id} buyruqlari:
/groups - Guruhlar
/block ID - Bloklash
/unblock ID - Blokdan chiqarish
/help - Yordam
📤 Buyurtma guruhi: {acc.order_group_id}
📊 Guruhlar: {len(acc.monitored_groups)}
💾 Baza: {acc.db_file}""")


# ========== ISHGA TUSHIRISH ==========

async def run_account(c, acc: AccountConfig):
    """Bir akkauntni ishga tushirish"""
    await c.connect()
    if not await c.is_user_authorized():
        raise RuntimeError(f"Akkaunt #{acc.profile_id} avtorizatsiya qilinmagan: {acc.phone}")
    me = await c.get_me()
    profile_name = f"@{me.username}" if me.username else f"+{me.phone}" if me.phone else str(me.id)
    print(f"  {G}✅ Akkaunt #{acc.profile_id} ulandi: {profile_name} {W}")
    print(f"     📤 Buyurtma guruhi: {acc.order_group_id}")
    print(f"     📊 Guruhlar: {len(acc.monitored_groups)}")
    print(f"     💾 Baza: {acc.db_file}")
    logger.info(f"Akkaunt #{acc.profile_id} ulandi: {profile_name}")
    register_account_handlers(c, acc)
    register_account_commands(c, acc)
    await c.run_until_disconnected()


async def poll_pending_messages(clients):
    while True:
        try:
            with get_main_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''CREATE TABLE IF NOT EXISTS pending_userbot_messages (
                                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  chat_id INTEGER,
                                  message TEXT
                                  )''')
                cursor.execute("SELECT id, chat_id, message FROM pending_userbot_messages")
                rows = cursor.fetchall()
                for row in rows:
                    msg_id, chat_id, text = row
                    if clients:
                        try:
                            # Send message using the first client
                            client = clients[0]
                            await client.send_message(entity=chat_id, message=text, parse_mode='html')
                        except Exception as e:
                            logger.error(f"Pending send error: {e}")
                        finally:
                            cursor.execute("DELETE FROM pending_userbot_messages WHERE id = ?", (msg_id,))
                            conn.commit()
        except Exception as e:
            pass
        await asyncio.sleep(2)

async def main():
    bot_username = await get_bot_username()
    print(f"🤖 Bot: @{bot_username}")
    
    print("💾 Umumiy bazani tekshirish...")
    init_main_database()
    print("✅ Umumiy baza tayyor")
    
    # Default kalit so'zlar
    try:
        with get_main_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM keywords WHERE type = ?', ('passenger',))
            if cursor.fetchone()[0] == 0:
                for w in ["kerak", "ketish kerak", "olib keting", "yo'lovchi kerak", "borish kerak", "ketmoqchiman"]:
                    cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', ('passenger', w))
            cursor.execute('SELECT COUNT(*) FROM keywords WHERE type = ?', ('driver',))
            if cursor.fetchone()[0] == 0:
                for w in ["ketaman", "boraman", "olib ketaman", "haydovchiman", "mashina bor", "taksi"]:
                    cursor.execute('INSERT OR IGNORE INTO keywords (type, word) VALUES (?, ?)', ('driver', w))
            conn.commit()
    except Exception as e:
        logger.error(f"Default so'zlar: {e}")
    
    profiles = load_profiles()
    
    try:
        if profiles:
            print(f"\n👤 {len(profiles)} ta akkaunt yuklandi")
            print(f"{B}{"=" * 60}{W}")
            
            accounts = []
            for pid, session_name, phone in profiles:
                acc = AccountConfig(pid, session_name, phone)
                acc.bot_username = bot_username
                c = TelegramClient(session_name, API_ID, API_HASH)
                accounts.append((c, acc))
            
            print(f"{G}✅ USERBOT ISHGA TUSHDI! (Ko'p akkaunt rejimi){W} {W}")
            print(f"{B}{"=" * 60}{W}")
            for c, acc in accounts:
                print(f"  📱 {Y}Akkaunt #{acc.profile_id}{W}: {G}{acc.phone or acc.session_name}{W}")
                print(f"     📤 Buyurtma: {B}{acc.order_group_id}{W}")
                print(f"     📊 Guruhlar: {len(acc.monitored_groups)}")
                print(f"     💾 Baza: {acc.db_file}")
            print("=" * 60 + "\n")
            
            async def run_one(idx):
                c, acc = accounts[idx]
                try:
                    await run_account(c, acc)
                except Exception as e:
                    logger.error(f"Akkaunt #{acc.profile_id} xatolik: {e}")
                    print(f"{R}❌ Akkaunt #{acc.profile_id}: {e} {W}")
            
            tasks = [run_one(i) for i in range(len(accounts))]
            tasks.append(poll_pending_messages([c for c, acc in accounts]))
            await asyncio.gather(*tasks)
        else:
            # Legacy: bitta userbot sessiya
            await client.connect()
            if not await client.is_user_authorized():
                phone = input("Telefon raqamingiz (+998xxxxxxxxx): ")
                print("🤙 Kod yuborildi...")
                await client.send_code_request(phone)
                code = input("Telegram kodini kiriting: ")
                try:
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    await client.sign_in(password=input("2FA paroli: "))
            
            # Legacy rejimda eski usulda ishlash
            legacy_groups = load_groups()
            
            async def legacy_msg_handler(event):
                # Eski usulda ishlash (bitta akkaunt)
                pass
            
            # Legacy uchun ham AccountConfig yaratish
            legacy_acc = AccountConfig(0, 'userbot', '')
            legacy_acc.monitored_groups = legacy_groups
            legacy_acc.db_file = 'zakazlar.db'
            legacy_acc.bot_username = bot_username
            
            register_account_handlers(client, legacy_acc)
            register_account_commands(client, legacy_acc)
            
            print(f"{B}\n" + "=" * 60 + W)
            print(f"{G}✅ USERBOT ISHGA TUSHDI! (Bitta akkaunt){W} {W}")
            print(f"{B}{"=" * 60}{W}")
            print(f"📊 Guruhlar: {len(legacy_groups)} | Buyurtma: {DEFAULT_ORDER_GROUP_ID}")
            print("=" * 60 + "\n")
            asyncio.create_task(poll_pending_messages([client]))
            await client.run_until_disconnected()
    except Exception as e:
        logger.critical(f"KRITIK XATOLIK: {e}")
        print(f"\n❌ KRITIK XATOLIK: {e}\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Bot to'xtatildi")
