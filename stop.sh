#!/bin/bash

# Vijdon Userbot - Stop Script
# Botni to'xtatish

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
echo -e "${BLUE}🛑 VIJDON USERBOT - TO'XTATISH${NC}"
echo -e "${BLUE}========================================${NC}"

# PM2 o'rnatilganligini tekshirish
if command -v pm2 &> /dev/null; then
    echo -e "${YELLOW}🔧 PM2 orqali to'xtatilmoqda...${NC}"
    
    # PM2 processni to'xtatish
    if pm2 list | grep -q "vijdon-userbot"; then
        pm2 stop vijdon-userbot 2>/dev/null || true
        echo -e "${GREEN}✅ Bot PM2 orqali to'xtatildi${NC}"
        echo -e "${BLUE}Qayta ishga tushirish: pm2 start vijdon-userbot${NC}"
    else
        echo -e "${YELLOW}⚠️  PM2 da 'vijdon-userbot' topilmadi${NC}"
    fi
else
    # Screen sessiyasini to'xtatish
    echo -e "${YELLOW}📺 Screen sessiyasida to'xtatilmoqda...${NC}"
    
    if screen -list | grep -q "vijdon-userbot"; then
        screen -S vijdon-userbot -X quit
        echo -e "${GREEN}✅ Bot screen sessiyasida to'xtatildi${NC}"
    else
        echo -e "${YELLOW}⚠️  Screen sessiyasi topilmadi${NC}"
    fi
fi

# WAL fayllari o'chirish (readonly database xatoligini oldini olish)
echo -e "${YELLOW}🧹 Vaqtinchalik fayllari tozalanmoqda...${NC}"
rm -f zakazlar.db-wal zakazlar.db-shm zakazlar.db-journal 2>/dev/null || true
echo -e "${GREEN}✅ Vaqtinchalik fayllari o'chirildi${NC}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ BOT MUVAFFAQIYATLI TO'XTATILDI!${NC}"
echo -e "${GREEN}========================================${NC}"
