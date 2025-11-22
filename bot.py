import asyncio
import json
import logging
import sys
import os
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# --- НАСТРОЙКИ ---
# Токен берем из секретов Hugging Face
TOKEN = os.environ.get("BOT_TOKEN") 

# ВАШИ НАСТРОЙКИ (ИЗМЕНИТЕ ЭТО!)
ADMIN_CHAT_ID = 12345678 # <--- ВАШ ID (ЦИФРЫ)
WEB_APP_URL = "https://ваш-ник.github.io/ваша-папка/" # <--- ССЫЛКА НА GITHUB PAGES
# -----------------

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# --- WEB SERVER (ОБЯЗАТЕЛЬНО ДЛЯ HUGGING FACE) ---
async def health_check(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    # Hugging Face всегда использует порт 7860
    port = int(os.environ.get("PORT", 7860))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

# --- ЛОГИКА БОТА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    await message.answer(
        "Добро пожаловать в Кофемолку! 🥐\nНажмите кнопку ниже, чтобы открыть меню.",
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

        is_delivery = (info.get('deliveryType') == 'Доставка')
        order_icon = "🚗" if is_delivery else "🏃"
        
        text = f"{order_icon} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        text += f"👤 <b>Имя:</b> {info.get('name')}\n"
        text += f"📞 <b>Тел:</b> <a href='tel:{info.get('phone')}'>{info.get('phone')}</a>\n"
        
        if is_delivery:
            text += f"📍 <b>Адрес:</b> {info.get('address')}\n"
        else:
            text += f"📍 <b>Самовывоз</b>\n"
            
        pay_type = info.get('paymentType')
        text += f"💳 <b>Оплата:</b> {pay_type}\n"
        
        if pay_type in ['Kaspi', 'Halyk']:
            text += f"📱 <b>Счет на номер:</b> <code>{info.get('paymentPhone')}</code>\n"
            
        if info.get('comment'):
            text += f"💬 <b>Комментарий:</b> <i>{info.get('comment')}</i>\n"
            
        text += f"➖➖➖➖➖➖➖➖➖➖\n\n<b>📋 ЗАКАЗ:</b>\n"
        
        for i, item in enumerate(cart, 1):
            options = item.get('options', [])
            # Фильтр пустых опций (безопасный метод)
            opts = [o for o in options if o and o != "Без сахара"]
            opts_str = f"\n   └ <i>{', '.join(opts)}</i>" if opts else ""
            
            item_name = item.get('name', 'Товар')
            text += f"{i}. <b>{item_name}</b> {opts_str}\n"
            
        text += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_delivery: text += "\n⚠️ <i>+ Доставка (от 600 ₸)</i>"

        # Кнопки
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ 15 мин", callback_data=f"acc_15_{message.chat.id}"),
                InlineKeyboardButton(text="✅ 30 мин", callback_data=f"acc_30_{message.chat.id}")
            ],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_{message.chat.id}")]
        ])

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=kb)
        
        response_text = f"✅ Заказ принят!\nСумма: {total} ₸"
        if is_delivery: response_text += "\nМенеджер свяжется для подтверждения."
        await message.answer(response_text)

    except Exception as e:
        logging.error(f"Error: {e}")

@dp.callback_query(F.data.startswith("acc_"))
async def accept_order(callback: CallbackQuery):
    parts = callback.data.split("_")
    time, user_id = parts[1], parts[2]
    await callback.message.edit_text(text=f"{callback.message.text}\n\n✅ <b>ПРИНЯТ ({time} мин)</b>", reply_markup=None)
    try: await bot.send_message(chat_id=user_id, text=f"👨‍🍳 Ваш заказ принят!\nГотовность через: <b>{time} мин</b>.")
    except: pass

@dp.callback_query(F.data.startswith("dec_"))
async def decline_order(callback: CallbackQuery):
    user_id = callback.data.split("_")[1]
    await callback.message.edit_text(text=f"{callback.message.text}\n\n❌ <b>ОТКЛОНЕН</b>", reply_markup=None)
    try: await bot.send_message(chat_id=user_id, text=f"😔 Извините, мы не можем принять заказ. Менеджер свяжется с вами.")
    except: pass

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(start_web_server(), dp.start_polling(bot))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
