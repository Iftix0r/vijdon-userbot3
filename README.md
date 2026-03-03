# Vijdon Userbot

A Telegram userbot for managing taxi orders and monitoring group chats.

## Features

- 🚕 Automatic order tracking and forwarding
- 👥 User management and blocking system
- 📊 Order statistics and database
- 🔍 Keyword-based message filtering
- 📱 Profile photo and user information extraction
- 🤖 Bot integration with inline buttons
- 📋 Multiple group monitoring

## Requirements

- Python 3.8+
- Telethon
- aiohttp
- python-dotenv

## Installation

1. Clone the repository:
```bash
git clone https://github.com/[username]/vijdon-userbot3.git
cd vijdon-userbot3
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create `.env` file:
```
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
ORDER_GROUP_ID=your_order_group_id
# Reklama guruhda "Zakazni olish" tugmasi uchun (haydovchi bo'lish - admin kontakt):
HAYDOVCHI_ADMIN_PHONE=+998901234567
HAYDOVCHI_ADMIN_USERNAME=admin_username
```

5. Run the bot:
```bash
python main.py
```

## Commands

### Group Management
- `/add_group -1001234567890` - Add group to monitoring
- `/remove_group -1001234567890` - Remove group from monitoring
- `/groups` - List monitored groups
- `/make_admin -1001234567890` - Make bot admin in group

### User Management
- `/block 123456789` - Block user
- `/unblock 123456789` - Unblock user
- `/blocked` - List blocked users

### Other
- `/help` - Show help message

## Configuration

Edit keywords in the database or modify default keywords in `main.py`:

- **Passenger keywords**: Words that indicate a passenger order
- **Driver keywords**: Words that indicate a driver order

## Database

The bot uses SQLite database (`zakazlar.db`) with the following tables:
- `users` - User information
- `zakazlar` - Orders
- `blocked_users` - Blocked users
- `order_groups` - Additional order groups
- `keywords` - Filter keywords

## Logging

Logs are saved to:
- `userbot.log` - Main bot logs
- `bot.log` - Bot API logs
- `app.log` - Application logs

## License

MIT License

## Author

Vijdon Taxi Team
# vijdon-userbot3
# vijdon-userbot3
