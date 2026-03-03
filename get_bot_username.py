import os
import asyncio
import aiohttp
from dotenv import load_dotenv

load_dotenv()

async def get_me():
    token = os.getenv('BOT_TOKEN')
    async with aiohttp.ClientSession() as s:
        async with s.get(f'https://api.telegram.org/bot{token}/getMe') as r:
            res = await r.json()
            if 'result' in res:
                print(res['result']['username'])
            else:
                print("Error: ", res)

if __name__ == "__main__":
    asyncio.run(get_me())
