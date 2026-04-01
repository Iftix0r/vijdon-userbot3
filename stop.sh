#!/bin/bash

# Vijdon Userbot 3 - Stop Script
# Botni to'xtatish (unikal PID fayllar bilan)

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

# Unikal PID fayl nomlari (start.sh bilan bir xil)
BOT_PID_FILE="$PROJECT_DIR/vijdon3_bot.pid"
MAIN_PID_FILE="$PROJECT_DIR/vijdon3_main.pid"
PM2_APP_NAME="vijdon-userbot3"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}🛑 VIJDON USERBOT 3 - TO'XTATISH${NC}"
echo -e "${BLUE}========================================${NC}"

STOPPED=0

# PM2 o'rnatilganligini tekshirish
if command -v pm2 &> /dev/null; then
    if pm2 list | grep -q "$PM2_APP_NAME"; then
        echo -e "${YELLOW}🔧 PM2 orqali to'xtatilmoqda...${NC}"
        pm2 stop "$PM2_APP_NAME" 2>/dev/null || true
        echo -e "${GREEN}✅ Bot PM2 orqali to'xtatildi${NC}"
        echo -e "${BLUE}Qayta ishga tushirish: pm2 start $PM2_APP_NAME${NC}"
        STOPPED=1
    fi
fi

# PID fayllardan processlarni to'xtatish
if [ -f "$BOT_PID_FILE" ]; then
    BOT_PID=$(cat "$BOT_PID_FILE")
    if kill -0 "$BOT_PID" 2>/dev/null; then
        echo -e "${YELLOW}🔧 bot.py to'xtatilmoqda (PID: $BOT_PID)...${NC}"
        kill "$BOT_PID" 2>/dev/null || true
        sleep 1
        # Agar hali ishlayotgan bo'lsa, kuchli to'xtatish
        if kill -0 "$BOT_PID" 2>/dev/null; then
            kill -9 "$BOT_PID" 2>/dev/null || true
        fi
        echo -e "${GREEN}✅ bot.py to'xtatildi${NC}"
        STOPPED=1
    else
        echo -e "${YELLOW}⚠️  bot.py allaqachon to'xtatilgan (PID: $BOT_PID)${NC}"
    fi
    rm -f "$BOT_PID_FILE"
fi

if [ -f "$MAIN_PID_FILE" ]; then
    MAIN_PID=$(cat "$MAIN_PID_FILE")
    if kill -0 "$MAIN_PID" 2>/dev/null; then
        echo -e "${YELLOW}🔧 main.py to'xtatilmoqda (PID: $MAIN_PID)...${NC}"
        kill "$MAIN_PID" 2>/dev/null || true
        sleep 1
        # Agar hali ishlayotgan bo'lsa, kuchli to'xtatish
        if kill -0 "$MAIN_PID" 2>/dev/null; then
            kill -9 "$MAIN_PID" 2>/dev/null || true
        fi
        echo -e "${GREEN}✅ main.py to'xtatildi${NC}"
        STOPPED=1
    else
        echo -e "${YELLOW}⚠️  main.py allaqachon to'xtatilgan (PID: $MAIN_PID)${NC}"
    fi
    rm -f "$MAIN_PID_FILE"
fi

# Screen sessiyasini ham tekshirish
if command -v screen &> /dev/null; then
    if screen -list | grep -q "$PM2_APP_NAME"; then
        echo -e "${YELLOW}📺 Screen sessiyasida to'xtatilmoqda...${NC}"
        screen -S "$PM2_APP_NAME" -X quit
        echo -e "${GREEN}✅ Screen sessiyasi to'xtatildi${NC}"
        STOPPED=1
    fi
fi

if [ "$STOPPED" -eq 0 ]; then
    echo -e "${YELLOW}⚠️  Hech qanday ishlaydigan process topilmadi${NC}"
fi

# WAL fayllari o'chirish (readonly database xatoligini oldini olish)
echo -e "${YELLOW}🧹 Vaqtinchalik fayllar tozalanmoqda...${NC}"
rm -f zakazlar*.db-wal zakazlar*.db-shm zakazlar*.db-journal 2>/dev/null || true
echo -e "${GREEN}✅ Vaqtinchalik fayllar o'chirildi${NC}"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ BOT MUVAFFAQIYATLI TO'XTATILDI!${NC}"
echo -e "${GREEN}========================================${NC}"
