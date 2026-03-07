#!/bin/bash

# Vijdon Userbot - Start Script
# Doimiy ishaltish uchun PM2 yoki screen dan foydalanadi

set -e

# Ranglar
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Loyiha katalogi
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}🚀 VIJDON USERBOT - ISHGA TUSHIRISH${NC}"
echo -e "${BLUE}========================================${NC}"

# Virtual environment tekshirish
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}⚠️  Virtual environment topilmadi. Yaratilmoqda...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✅ Virtual environment yaratildi${NC}"
fi

# Virtual environment faollashtirish
echo -e "${YELLOW}📦 Virtual environment faollantirilmoqda...${NC}"
source venv/bin/activate

# Dependensiyalarni o'rnatish
echo -e "${YELLOW}📥 Dependensiyalar o'rnatilmoqda...${NC}"
pip install -q -r requirements.txt 2>/dev/null || {
    echo -e "${RED}❌ Dependensiyalar o'rnatishda xatolik${NC}"
    exit 1
}
echo -e "${GREEN}✅ Dependensiyalar o'rnatildi${NC}"

# .env fayli tekshirish
if [ ! -f ".env" ]; then
    echo -e "${RED}❌ .env fayli topilmadi!${NC}"
    echo -e "${YELLOW}Iltimos, .env faylini yarating va quyidagi o'zgaruvchilarni qo'shing:${NC}"
    echo "  - API_ID"
    echo "  - API_HASH"
    echo "  - BOT_TOKEN"
    echo "  - ORDER_GROUP_ID"
    exit 1
fi
echo -e "${GREEN}✅ .env fayli topildi${NC}"

# PM2 o'rnatilganligini tekshirish
if ! command -v pm2 &> /dev/null; then
    echo -e "${YELLOW}⚠️  PM2 topilmadi. npm orqali o'rnatilmoqda...${NC}"
    npm install -g pm2 2>/dev/null || {
        echo -e "${YELLOW}⚠️  PM2 o'rnatib bo'lmadi. Screen dan foydalaniladi...${NC}"
        USE_SCREEN=true
    }
fi

# PM2 yoki Screen dan foydalanish
if [ "$USE_SCREEN" = true ] || ! command -v pm2 &> /dev/null; then
    # Screen dan foydalanish
    echo -e "${BLUE}📺 Screen sessiyasida ishga tushirilmoqda...${NC}"
    
    if screen -list | grep -q "vijdon-userbot"; then
        echo -e "${YELLOW}⚠️  'vijdon-userbot' sessiyasi allaqachon mavjud${NC}"
        echo -e "${YELLOW}Mavjud sessiyani ko'rish: screen -r vijdon-userbot${NC}"
        exit 0
    fi
    
    screen -dmS vijdon-userbot bash -c "cd '$PROJECT_DIR' && source venv/bin/activate && python bot.py"
    
    sleep 2
    
    if screen -list | grep -q "vijdon-userbot"; then
        echo -e "${GREEN}✅ Bot screen sessiyasida ishga tushirildi${NC}"
        echo -e "${BLUE}Sessiyaga kirish: screen -r vijdon-userbot${NC}"
        echo -e "${BLUE}Sessiyadan chiqish: Ctrl+A, D${NC}"
    else
        echo -e "${RED}❌ Bot ishga tushmadi${NC}"
        exit 1
    fi
else
    # PM2 dan foydalanish
    echo -e "${BLUE}🔧 PM2 orqali ishga tushirilmoqda...${NC}"
    
    # Eski process o'chirish
    pm2 delete vijdon-userbot 2>/dev/null || true
    
    # Yangi process ishga tushirish
    pm2 start bot.py --name "vijdon-userbot" --interpreter python
    
    # PM2 saqlash
    pm2 save
    
    echo -e "${GREEN}✅ Bot PM2 orqali ishga tushirildi${NC}"
    echo -e "${BLUE}Statusni ko'rish: pm2 status${NC}"
    echo -e "${BLUE}Loglarni ko'rish: pm2 logs vijdon-userbot${NC}"
    echo -e "${BLUE}Botni to'xtatish: pm2 stop vijdon-userbot${NC}"
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ USERBOT MUVAFFAQIYATLI ISHGA TUSHDI!${NC}"
echo -e "${GREEN}========================================${NC}"
