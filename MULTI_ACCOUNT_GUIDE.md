# Multi-Account Rejimi - Qo'llanma

## Tizim Arxitekturasi

```
Bot (bot.py) - Admin panel, profil qo'shish
    ↓
Umumiy baza (zakazlar.db) - profiles, keywords, admins
    ↓
Har bir Akkaunt (main.py - AccountConfig)
    ├── Akkaunt 1:
    │   ├── account_config_1.json  (guruhlar, buyurtma guruhi, reklama)
    │   └── zakazlar_account1.db   (users, zakazlar, blocked)
    ├── Akkaunt 2:
    │   ├── account_config_2.json
    │   └── zakazlar_account2.db
    └── Akkaunt N:
        ├── account_config_N.json
        └── zakazlar_accountN.db
```

## Har bir Akkaunt Mustaqil Ishlaydi

- **Alohida SQLite baza** - buyurtmalar, foydalanuvchilar, bloklangan
- **Alohida guruhlar** - turli akkauntlar turli guruhlarni kuzatadi
- **Alohida buyurtma guruhi** - har bir akkaunt o'z guruhiga yuboradi
- **Alohida reklama guruhlari** - har birida turlicha bo'lishi mumkin

## Akkaunt Qo'shish

1. Telegram botga `/start` yuboring
2. **⚙️ Sozlamalar** → **👤 Profillar boshqaruvi** → **➕ Profil qo'shish**
3. Telegram telefon raqamini kiriting
4. Telegram kodini kiriting
5. 2FA parolini kiriting (agar mavjud bo'lsa)

## Akkaunt Sozlamalari

1. **⚙️ Profil sozlamalari** → Profilni tanlang
2. **📤 Buyurtma guruhi o'zgartirish** → Guruh ID sini kiriting
3. **📋 Kuzatiladigan guruhlar** → Guruhlar ro'yxatini ko'ring

### Konfiguratsiya Fayli (`account_config_{id}.json`)

```json
{
  "account_id": 1,
  "order_group_id": -1001234567890,
  "monitored_groups": [-1001111, -1002222, -1003333],
  "reklama_groups": ["@vijdontaxireklama", "@iymontaxi"]
}
```

## Misol

- **Akkaunt 1** → Guruh A, B, C → Buyurtma Guruhi 1
- **Akkaunt 2** → Guruh D, E, F → Buyurtma Guruhi 2
- **Akkaunt 3** → Guruh G, H, I → Buyurtma Guruhi 3

## Ishga Tushirish

```bash
./start.sh
# yoki
python bot.py  # bot.py main.py ni avtomatik ishga tushiradi
```

## To'xtatish

```bash
./stop.sh
```

## Loglar

```bash
tail -f userbot.log
tail -f bot.log
```
