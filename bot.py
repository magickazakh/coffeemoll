import asyncio
import json
import logging
import sys
import os
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
# Токен и ID берем из переменных окружения или вставляем вручную
TOKEN = os.getenv("BOT_TOKEN", "8444027240:AAFEiACM5x-OPmR9CFgk1zyrmU24PgovyCY") 
ADMIN_CHAT_ID = -1003356844624
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"
# -----------------

logging.basicConfig(level=logging.INFO)

# Инициализация
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# FSM: Машина состояний для ввода времени
class OrderState(StatesGroup):
    waiting_for_custom_time = State()

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def health_check(request):
    return web.Response(text="Bot is running OK!")

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    
    await site.start()
    logging.info(f"✅ WEB SERVER STARTED ON PORT {port}")

# --- КЛАВИАТУРЫ ---

def get_decision_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"dec_accept_{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_reject_{user_id}")
        ]
    ])

def get_time_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5 мин", callback_data=f"time_5_{user_id}"),
            InlineKeyboardButton(text="10 мин", callback_data=f"time_10_{user_id}"),
            InlineKeyboardButton(text="15 мин", callback_data=f"time_15_{user_id}")
        ],
        [
            InlineKeyboardButton(text="20 мин", callback_data=f"time_20_{user_id}")
            InlineKeyboardButton(text="30 мин", callback_data=f"time_30_{user_id}"),
            InlineKeyboardButton(text="✍️ Своё время", callback_data=f"time_custom_{user_id}")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"time_back_{user_id}")]
    ])

def get_ready_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏁 Готов к выдаче", callback_data=f"order_ready_{user_id}")]
    ])

# --- ОБРАБОТЧИКИ КОМАНД ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    await message.answer("Добро пожаловать в Кофемолку! 🥐", reply_markup=markup)

# --- ОБРАБОТКА ЗАКАЗА ---

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
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
            
        text += f"💳 <b>Оплата:</b> {info.get('paymentType')}\n"
        
        if info.get('paymentType') in ['Kaspi', 'Halyk']:
            text += f"📱 <b>Счет на:</b> <code>{info.get('paymentPhone')}</code>\n"
        
        if info.get('comment'):
            text += f"💬 <b>Коммент:</b> <i>{info.get('comment')}</i>\n"
            
        text += f"➖➖➖➖➖➖➖➖➖➖\n<b>📋 ЗАКАЗ:</b>\n"
        
        for i, item in enumerate(cart, 1):
            options = item.get('options', [])
            name = item.get('name', 'Товар')
            opts = [o for o in options if o and o != "Без сахара"]
            opts_str = f" ({', '.join(opts)})" if opts else ""
            text += f"{i}. <b>{name}</b>{opts_str}\n"
            
        text += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_delivery: text += "\n⚠️ <i>+ Доставка</i>"

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=get_decision_kb(message.chat.id))
        await message.answer(f"✅ Заказ принят!\nСумма: {total} ₸\nЖдите подтверждения времени.")

    except Exception as e:
        logging.error(f"Error: {e}")

# --- ЛОГИКА СТАТУСОВ ---

@dp.callback_query(F.data.startswith("dec_"))
async def decision_callback(callback: CallbackQuery):
    action, user_id = callback.data.split("_")[1], callback.data.split("_")[2]
    if action == "accept":
        await callback.message.edit_reply_markup(reply_markup=get_time_kb(user_id))
    elif action == "reject":
        current_text = callback.message.html_text.split("\n\n")[0]
        await callback.message.edit_text(text=f"{current_text}\n\n❌ <b>ОТКЛОНЕН</b>", reply_markup=None)
        try: await bot.send_message(chat_id=user_id, text="❌ Заказ отклонен.") 
        except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("time_"))
async def time_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    action, user_id = parts[1], parts[2]
    
    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=get_decision_kb(user_id))
        return

    if action == "custom":
        await callback.message.answer("✍️ <b>Введите время</b> (например: '40 мин'):")
        await state.update_data(order_msg_id=callback.message.message_id, client_id=user_id)
        await state.set_state(OrderState.waiting_for_custom_time)
        await callback.answer()
        return
    
    time_val = f"{action} минут"
    original_text = callback.message.html_text.split("\n\n")[0]
    await callback.message.edit_text(text=f"{original_text}\n\n✅ <b>ПРИНЯТ</b> ({time_val})", reply_markup=get_ready_kb(user_id))
    try: await bot.send_message(chat_id=user_id, text=f"👨‍🍳 Заказ принят!\n⏳ Готовность: <b>{time_val}</b>.")
    except: pass
    await callback.answer()

@dp.message(OrderState.waiting_for_custom_time)
async def custom_time_handler(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    order_msg_id = user_data['order_msg_id']
    client_id = user_data['client_id']
    custom_time = message.text
    
    try: await message.delete()
    except: pass

    try:
        await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=order_msg_id, reply_markup=get_ready_kb(client_id))
        await message.answer(f"✅ Время установлено: <b>{custom_time}</b>", reply_to_message_id=order_msg_id)
        await bot.send_message(client_id, f"👨‍🍳 Заказ принят!\n⏳ Готовность: <b>{custom_time}</b>.")
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("order_ready_"))
async def ready_callback(callback: CallbackQuery):
    user_id = callback.data.split("_")[2]
    current_text = callback.message.html_text
    
    if "ПРИНЯТ" in current_text:
        final_text = current_text.replace("✅ <b>ПРИНЯТ", "🏁 <b>ГОТОВ / ВЫДАН").split("В РАБОТУ")[0] + "</b>"
        if "ГОТОВ" not in final_text: final_text = current_text + "\n\n🏁 <b>ЗАКАЗ ГОТОВ!</b>"
    else:
        final_text = f"{current_text}\n\n🏁 <b>ЗАКАЗ ГОТОВ!</b>"

    await callback.message.edit_text(text=final_text, reply_markup=None)
    try: await bot.send_message(chat_id=user_id, text="🎉 <b>Заказ готов!</b>\nПриятного аппетита! ☕️")
    except: pass
    await callback.answer()

# --- ЗАПУСК ---
async def main():
    # 1. СНАЧАЛА запускаем веб-сервер (чтобы Render увидел порт)
    await start_web_server()
    
    # 2. Чистим вебхуки
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 3. Запускаем поллинг
    print("🤖 Bot started polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
