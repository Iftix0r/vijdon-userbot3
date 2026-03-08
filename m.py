from telethon import TelegramClient

api_id = 23251130  # bu yerga api_id yozing
api_hash = "7490c15bbfcdcd070a022bf771f422b7"

client = TelegramClient("session", api_id, api_hash)

async def main():
    me = await client.get_me()
    print("Userbot ishga tushdi!")
    print("Akkaunt:", me.first_name)

with client:
    client.loop.run_until_complete(main())