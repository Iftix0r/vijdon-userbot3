#!/bin/bash

echo "========================================"
echo "🚀 VIJDON USERBOT - ISHGA TUSHIRISH"
echo "========================================"

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
    pm2 delete vijdon-userbot 2>/dev/null
    pm2 start ecosystem.config.js
    echo "✅ Ishga tushdi!"
    echo ""
    echo "Loglarni ko'rish: pm2 logs vijdon-userbot"
    echo "To'xtatish: pm2 stop vijdon-userbot"
    echo "Qayta ishga tushirish: pm2 restart vijdon-userbot"
else
    echo "🚀 Oddiy rejimda ishga tushirilmoqda..."
    echo "⚠️  Ctrl+C bosib to'xtatish mumkin"
    echo ""
    python bot.py &
    BOT_PID=$!
    python main.py
    kill $BOT_PID 2>/dev/null
fi
