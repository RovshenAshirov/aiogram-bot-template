# aiogram-bot-template

[aiogram 3](https://docs.aiogram.dev/) asosidagi kengaytiriladigan Telegram botlar uchun shablon.

Tayyor imkoniyatlar:

- `/start` — foydalanuvchini bazaga saqlaydi, `/help` — buyruqlar ro'yxati;
- adminlar uchun: `/ad` — istalgan turdagi xabarni hamma foydalanuvchilarga yuborish, `/stats` — foydalanuvchilar soni;
- throttling (Redis), structlog loglari, xatolarni ushlovchi router;
- adminlarga alohida buyruqlar menyusi va bot ishga tushgani haqida xabar;
- long polling yoki webhook (aiohttp + aiojobs) rejimi.

## Ishga tushirish

Kerak: Python 3.14+, [uv](https://docs.astral.sh/uv/), PostgreSQL, Redis 7+.

```bash
uv sync
cp example.env .env   # BOT_TOKEN, ADMINS va baza sozlamalarini to'ldiring
uv run python bot.py
```

`users` jadvali bot ishga tushganda o'zi yaratiladi. Webhook rejimi uchun `.env` da `USE_WEBHOOK=true` qilib, `MAIN_WEBHOOK_*` qiymatlarini to'ldiring.
