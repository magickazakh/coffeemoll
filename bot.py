import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN") 
if not TOKEN:
    exit("❌ BOT_TOKEN is not set!")

# Инициализация простого бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- ЕДИНСТВЕННЫЙ ОТВЕТ БОТА ---
@dp.message()
async def holiday_handler(message: types.Message):
    await message.answer(
        "🎄 <b>С Новым Годом!</b>\n\n"
        "✨ Команда CoffeeMoll ушла на небольшие каникулы.\n"
        "📆 Мы не работаем с <b>1 по 4 января</b>.\n\n"
        "Ждем вас снова <b>5 января</b> за самым вкусным кофе! ☕️"
    )

async def main():
    print("🎅 Holiday Stub Started...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
