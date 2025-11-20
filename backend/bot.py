import asyncio
import json
import logging
import sys
import os
from aiohttp import web # Нужен для "обмана" Render, чтобы он дал порт

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
# Токен лучше брать из переменных окружения (безопасность), но можно и так
TOKEN = os.getenv("BOT_TOKEN", "8444027240:AAFEiACM5x-OPmR9CFgk1zyrmU24PgovyCY") 
ADMIN_CHAT_ID = 1054308942 # ВАШ ID (ЧИСЛОМ)
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"
# -----------------

logging.basicConfig(level=logging.INFO)

# Инициализация бота (БЕЗ ПРОКСИ)
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# --- ФУНКЦИЯ ДЛЯ RENDER (Чтобы бот не падал) ---
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    # Render выдает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")
# -----------------------------------------------

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=WEB_APP_URL))]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "Добро пожаловать! Нажмите кнопку ниже, чтобы открыть меню.",
        reply_markup=markup
    )

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    try:
        json_data = message.web_app_data.data
        data = json.loads(json_data)
        
        cart = data.get('cart', [])
        total = data.get('total', 0)
        info = data.get('info', {})

        order_type_icon = "🚗" if info.get('deliveryType') == 'Доставка' else "🏃"
        
        text = f"{order_type_icon} <b>НОВЫЙ ЗАКАЗ ({info.get('deliveryType')})</b>\n"
        text += f"➖➖➖➖➖➖➖➖➖➖\n"
        
        text += f"👤 <b>Имя:</b> {info.get('name')}\n"
        text += f"📞 <b>Телефон:</b> {info.get('phone')}\n"
        
        if info.get('deliveryType') == 'Доставка':
            text += f"📍 <b>Адрес:</b> {info.get('address')}\n"
            
        payment_method = info.get('paymentType')
        text += f"💳 <b>Оплата:</b> {payment_method}\n"
        
        if payment_method in ['Kaspi', 'Halyk']:
            text += f"📱 <b>Счет выставить на:</b> {info.get('paymentPhone')}\n"
            
        text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
        
        text += "<b>📋 СОСТАВ ЗАКАЗА:</b>\n"
        for i, item in enumerate(cart, 1):
            options = item.get('options', [])
            valid_options = [opt for opt in options if opt and opt != "Без сахара"]
            
            options_str = ""
            if valid_options:
                options_str = f"\n   └ <i>{', '.join(valid_options)}</i>"
            
            item_name = item.get('name')
            item_price = item.get('price', 0)
            
            text += f"{i}. <b>{item_name}</b> {options_str}\n"
        
        text += f"\n💰 <b>ИТОГО К ОПЛАТЕ: {total} ₸</b>"

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
        await message.answer(f"✅ Заказ принят, {info.get('name')}!")

    except Exception as e:
        logging.error(f"Error: {e}")

async def main():
    # Запускаем и веб-сервер (для Render), и бота (для Телеграм) одновременно
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")