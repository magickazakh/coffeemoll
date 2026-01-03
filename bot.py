import asyncio
import logging
import os
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
# Если токена нет, не падаем сразу, чтобы сервер успел запуститься (для диагностики)
if not TOKEN:
    logging.warning("⚠️ BOT_TOKEN is not set!")

# Инициализация бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if TOKEN else None
dp = Dispatcher()

# --- ВЕБ-СЕРВЕР (ОБЯЗАТЕЛЬНО ДЛЯ RENDER) ---
async def health_check(request):
    return web.Response(text="Holiday Stub OK")

async def start_web_server():
    # Render передает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌍 Web server started on port {port}")

# --- ОТВЕТ БОТА ---
@dp.message()
async def holiday_handler(message: types.Message):
    await message.answer(
        "🎄 <b>С Новым Годом!</b>\n\n"
        "✨ Команда CoffeeMoll ушла на небольшие каникулы.\n"
        "📆 Мы не работаем с <b>1 по 5 января</b>.\n\n"
        "Ждем вас снова <b>6 января</b> за самым вкусным кофе! ☕️"
    )

async def main():
    # 1. Запускаем веб-сервер (чтобы Render не убил процесс)
    await start_web_server()
    
    # 2. Запускаем бота (если есть токен)
    if bot:
        print("🎅 Holiday Stub Started...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    else:
        print("❌ Bot not started (no token), but web server is running.")
        # Держим процесс живым, если бота нет (чтобы сервер не упал)
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass