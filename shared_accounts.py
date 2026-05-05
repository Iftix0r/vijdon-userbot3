import re

# Precompiled regex — har chaqiruvda qayta kompilatsiya qilinmaydi
_RE_ANCHOR = re.compile(r"<a href='(tg://user\?id=\d+)'>(.*?)</a>")
_RE_BOLD   = re.compile(r'<b>(.*?)</b>', re.DOTALL)
_RE_ITALIC = re.compile(r'<i>(.*?)</i>', re.DOTALL)
_RE_TAG    = re.compile(r'<[^>]+>')

# Barcha akkauntlar uchun umumiy client pool
all_accounts = []

def register_account(client, acc):
    all_accounts.append((client, acc))

def html_to_telethon(text):
    """HTML formatdagi matnni Telethon uchun to'g'ri formatga o'tkazish"""
    text = _RE_ANCHOR.sub(lambda m: f"[{m.group(2)}]({m.group(1)})", text)
    text = _RE_BOLD.sub(r'**\1**', text)
    text = _RE_ITALIC.sub(r'__\1__', text)
    text = _RE_TAG.sub('', text)
    return text

async def send_to_any_available(order_group_id, caption, sender=None, keyboard=None):
    """Qaysi akkaunt guruhda bo'lsa shundan yuborish"""
    telethon_caption = html_to_telethon(caption)
    for client, acc in all_accounts:
        try:
            if sender:
                try:
                    photos = await client.get_profile_photos(sender)
                    if photos:
                        await client.send_file(entity=order_group_id, file=photos[0], caption=telethon_caption, parse_mode='md', link_preview=False, buttons=keyboard)
                    else:
                        await client.send_message(entity=order_group_id, message=telethon_caption, parse_mode='md', link_preview=False, buttons=keyboard)
                except:
                    await client.send_message(entity=order_group_id, message=telethon_caption, parse_mode='md', link_preview=False, buttons=keyboard)
            else:
                await client.send_message(entity=order_group_id, message=telethon_caption, parse_mode='md', link_preview=False, buttons=keyboard)
            return True, acc.profile_id
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Akkaunt #{acc.profile_id} {order_group_id} ga yubora olmadi: {e}")
            continue
    return False, None

