#!/bin/bash

echo "========================================"
echo "🚀 VIJDON USERBOT 3 - ISHGA TUSHIRISH"
echo "========================================"

# Loyiha katalogi
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Unikal PID fayl nomlari (boshqa loyihalar bilan aralashmasligi uchun)
BOT_PID_FILE="$PROJECT_DIR/vijdon3_bot.pid"
MAIN_PID_FILE="$PROJECT_DIR/vijdon3_main.pid"
PM2_APP_NAME="vijdon-userbot3"

# Avvalgi processlarni tozalash
if [ -f "$BOT_PID_FILE" ]; then
    OLD_PID=$(cat "$BOT_PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Eski bot.py process ($OLD_PID) to'xtatilmoqda..."
        kill "$OLD_PID" 2>/dev/null
        sleep 1
    fi
    rm -f "$BOT_PID_FILE"
fi

if [ -f "$MAIN_PID_FILE" ]; then
    OLD_PID=$(cat "$MAIN_PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Eski main.py process ($OLD_PID) to'xtatilmoqda..."
        kill "$OLD_PID" 2>/dev/null
        sleep 1
    fi
    rm -f "$MAIN_PID_FILE"
fi

# Virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Virtual environment yaratilmoqda..."
    python3 -m venv venv
fi

echo "📦 Virtual environment faollantirilmoqda..."
source venv/bin/activate

echo "📥 Dependensiyalar o'rnatilmoqda..."
pip install -q -r requirements.txt
echo "✅ Dependensiyalar o'rnatildi"

if [ ! -f ".env" ]; then
    echo "❌ .env fayli topilmadi!"
    exit 1
fi
echo "✅ .env fayli topildi"

# PM2 orqali ishga tushirish
if command -v pm2 &> /dev/null; then
    echo "🚀 PM2 orqali ishga tushirilmoqda..."
    pm2 delete "$PM2_APP_NAME" 2>/dev/null
    pm2 start ecosystem.config.js --name "$PM2_APP_NAME"
    echo "✅ Ishga tushdi!"
    echo ""
    echo "Loglarni ko'rish: pm2 logs $PM2_APP_NAME"
    echo "To'xtatish: pm2 stop $PM2_APP_NAME"
    echo "Qayta ishga tushirish: pm2 restart $PM2_APP_NAME"
else
    echo "🚀 Oddiy rejimda ishga tushirilmoqda..."
    echo "⚠️  To'xtatish uchun: bash stop.sh"
    echo ""

    # bot.py ni fonda ishga tushirish va PID ni saqlash
    python bot.py &
    echo $! > "$BOT_PID_FILE"
    echo "✅ bot.py ishga tushdi (PID: $(cat $BOT_PID_FILE))"

    # main.py ni ishga tushirish va PID ni saqlash
    python main.py &
    echo $! > "$MAIN_PID_FILE"
    echo "✅ main.py ishga tushdi (PID: $(cat $MAIN_PID_FILE))"

    echo ""
    echo "📋 PID fayllar:"
    echo "   bot.py  -> $BOT_PID_FILE"
    echo "   main.py -> $MAIN_PID_FILE"

    # Har ikkala processni kutish
    wait
fi
