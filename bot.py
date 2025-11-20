import asyncio
import json
import logging
import sys
import os
from aiohttp import web # Для Render

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
# Если запускаете локально, вставьте токен прямо сюда вместо os.getenv(...)
TOKEN = os.getenv("BOT_TOKEN", "8444027240:AAFEiACM5x-OPmR9CFgk1zyrmU24PgovyCY") 
ADMIN_CHAT_ID = 1054308942 # ВАШ ID (ЧИСЛОМ)
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"
# -----------------

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER (Чтобы бот не падал) ---
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
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
        "Добро пожаловать в Кофемолку! 🥐\nНажмите кнопку ниже, чтобы открыть меню.",
        reply_markup=markup
    )

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    try:
        # 1. Распаковка данных
        json_data = message.web_app_data.data
        data = json.loads(json_data)
        
        cart = data.get('cart', [])
        total = data.get('total', 0)
        info = data.get('info', {})

        # 2. Определяем тип заказа (Иконка)
        is_delivery = (info.get('deliveryType') == 'Доставка')
        order_icon = "🚗" if is_delivery else "🏃"
        
        # 3. Формируем шапку чека
        text = f"{order_type_icon} <b>НОВЫЙ ЗАКАЗ</b>\n"
        text += f"➖➖➖➖➖➖➖➖➖➖\n"
        
        # 4. Данные клиента
        text += f"👤 <b>Имя:</b> {info.get('name')}\n"
        text += f"📞 <b>Тел:</b> <a href='tel:{info.get('phone')}'>{info.get('phone')}</a>\n"
        
        if is_delivery:
            text += f"📍 <b>Адрес:</b> {info.get('address')}\n"
        else:
            text += f"📍 <b>Самовывоз</b>\n"

        # 5. Оплата
        pay_type = info.get('paymentType')
        text += f"💳 <b>Оплата:</b> {pay_type}\n"
        
        if pay_type in ['Kaspi', 'Halyk']:
            pay_phone = info.get('paymentPhone', 'Не указан')
            text += f"📱 <b>Счет на номер:</b> <code>{pay_phone}</code>\n"

        # 6. Комментарий (НОВОЕ)
        comment = info.get('comment')
        if comment:
            text += f"💬 <b>Комментарий:</b> <i>{comment}</i>\n"
            
        text += f"➖➖➖➖➖➖➖➖➖➖\n\n"
        
        # 7. Список товаров
        text += "<b>📋 СОСТАВ ЗАКАЗА:</b>\n"
        for i, item in enumerate(cart, 1):
            # Опции (сиропы, молоко и т.д.)
            options = item.get('options', [])
            # Фильтруем пустые опции и "Без сахара" (чтобы не засорять чек)
            valid_options = [opt for opt in options if opt and opt != "Без сахара"]
            
            options_str = ""
            if valid_options:
                options_str = f"\n   └ <i>{', '.join(valid_options)}</i>"
            
            item_name = item.get('name', 'Товар')
            # item_price = item.get('price', 0) # Цену за позицию можно не писать, чтобы чек был компактнее
            
            text += f"{i}. <b>{item_name}</b> {options_str}\n"
        
        # 8. Итого
        text += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        
        if is_delivery:
            text += "\n⚠️ <i>+ Доставка (от 600 ₸)</i>"

        # 9. Отправка
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
        
        # Ответ клиенту
        response_text = f"✅ Заказ принят!\nСумма: {total} ₸"
        if is_delivery:
            response_text += "\nМенеджер свяжется для подтверждения доставки."
        
        await message.answer(response_text)

    except Exception as e:
        logging.error(f"Error processing order: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте еще раз.")

async def main():
    # Запускаем веб-сервер (для Render) и поллинг (для Telegram)
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
