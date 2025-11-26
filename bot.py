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
TOKEN = os.getenv("BOT_TOKEN", "8444027240:AAFEiACM5x-OPmR9CFgk1zyrmU24PgovyCY") 
ADMIN_CHAT_ID = -1003356844624
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"
# -----------------

logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера с хранилищем состояний (в памяти)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Глобальные настройки (хранятся в памяти до перезагрузки)
SHOP_SETTINGS = {
    "is_open": True,
    "pizza_available": True
}

# --- FSM: МАШИНА СОСТОЯНИЙ ДЛЯ ВВОДА ВРЕМЕНИ ---
class OrderState(StatesGroup):
    waiting_for_custom_time = State()
    # Храним данные о заказе, пока админ вводит время
    current_order_data = {} 

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

# --- КЛАВИАТУРЫ ---

def get_admin_panel_kb():
    status_text = "🟢 Кофейня ОТКРЫТА" if SHOP_SETTINGS["is_open"] else "🔴 Кофейня ЗАКРЫТА"
    pizza_text = "🍕 Пицца ЕСТЬ" if SHOP_SETTINGS["pizza_available"] else "🚫 Пиццы НЕТ"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=status_text, callback_data="toggle_shop")],
        [InlineKeyboardButton(text=pizza_text, callback_data="toggle_pizza")],
        [InlineKeyboardButton(text="Закрыть", callback_data="close_panel")]
    ])

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
            InlineKeyboardButton(text="15 мин", callback_data=f"time_15_{user_id}"),
            InlineKeyboardButton(text="20 мин", callback_data=f"time_20_{user_id}")
        ],
        [
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
    await message.answer(
        "Добро пожаловать в CoffeeMoll! 🥐\nНажмите кнопку ниже, чтобы открыть меню.",
        reply_markup=markup
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.chat.id == ADMIN_CHAT_ID:
        await message.answer("🔧 <b>Админ-панель</b>", reply_markup=get_admin_panel_kb())
    else:
        await message.answer("У вас нет прав доступа.")

# --- ОБРАБОТКА ЗАКАЗА (WEB APP) ---

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    try:
        json_data = message.web_app_data.data
        data = json.loads(json_data)
        
        cart = data.get('cart', [])
        total = data.get('total', 0)
        info = data.get('info', {})

        # Формирование чека
        is_delivery = (info.get('deliveryType') == 'Доставка')
        order_icon = "🚗" if is_delivery else "🏃"
        
        text = f"{order_icon} <b>НОВЫЙ ЗАКАЗ</b>\n"
        text += f"➖➖➖➖➖➖➖➖➖➖\n"
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
        
        time_comment = ""
        if "Ко времени" in str(info.get('comment')):
             time_comment = " ⏰"

        if info.get('comment'):
            text += f"💬 <b>Комментарий:</b> <i>{info.get('comment')}</i>\n"
            
        text += f"➖➖➖➖➖➖➖➖➖➖\n"
        text += "<b>📋 СОСТАВ ЗАКАЗА:</b>\n"
        
        for i, item in enumerate(cart, 1):
            options = item.get('options', [])
            name = item.get('name', 'Товар')
            opts = [o for o in options if o and o != "Без сахара"]
            opts_str = f"\n   └ <i>{', '.join(opts)}</i>" if opts else ""
            text += f"{i}. <b>{name}</b> {opts_str}\n"
            
        text += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_delivery:
            text += "\n⚠️ <i>+ Доставка (от 600 ₸)</i>"

        # Отправка админу с кнопками "Принять/Отклонить"
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=get_decision_kb(message.chat.id))
        
        # Ответ клиенту
        response = f"✅ Спасибо, {info.get('name')}! Заказ передан бариста.\nСумма: {total} ₸."
        if is_delivery:
            response += "\n\n📞 Мы свяжемся с вами для уточнения доставки."
        else:
            response += "\n\n⏳ Ждите подтверждения времени готовности."
            
        await message.answer(response)

    except Exception as e:
        logging.error(f"Error processing order: {e}")
        await message.answer("Произошла ошибка. Свяжитесь с нами по телефону.")

# --- ЛОГИКА АДМИН-ПАНЕЛИ ---

@dp.callback_query(F.data.in_(['toggle_shop', 'toggle_pizza', 'close_panel']))
async def admin_panel_callback(callback: CallbackQuery):
    action = callback.data
    if action == "close_panel":
        await callback.message.delete()
        return

    if action == "toggle_shop":
        SHOP_SETTINGS["is_open"] = not SHOP_SETTINGS["is_open"]
    elif action == "toggle_pizza":
        SHOP_SETTINGS["pizza_available"] = not SHOP_SETTINGS["pizza_available"]
    
    await callback.message.edit_reply_markup(reply_markup=get_admin_panel_kb())
    await callback.answer("Настройки обновлены")

# --- ЛОГИКА ОБРАБОТКИ ЗАКАЗА (CALLBACKS) ---

# 1. Принять / Отклонить
@dp.callback_query(F.data.startswith("dec_"))
async def decision_callback(callback: CallbackQuery):
    action, user_id = callback.data.split("_")[1], callback.data.split("_")[2]
    
    if action == "accept":
        # Меняем клавиатуру на выбор времени
        await callback.message.edit_reply_markup(reply_markup=get_time_kb(user_id))
    
    elif action == "reject":
        # Редактируем сообщение и уведомляем клиента
        current_text = callback.message.html_text
        await callback.message.edit_text(text=f"{current_text}\n\n❌ <b>ОТКЛОНЕН</b>", reply_markup=None)
        try:
            await bot.send_message(chat_id=user_id, text="😔 К сожалению, мы не можем выполнить ваш заказ прямо сейчас. Скоро свяжемся с вами для уточнения.")
        except:
            pass
    
    await callback.answer()

# 2. Выбор времени (Пресеты)
@dp.callback_query(F.data.startswith("time_"))
async def time_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    action = parts[1] # 10, 15, custom, back
    user_id = parts[2]
    
    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=get_decision_kb(user_id))
        return

    if action == "custom":
        # Запускаем сценарий ввода своего времени
        await callback.message.answer("✍️ <b>Напишите время ожидания</b> (например: '45 мин' или 'к 18:30'):")
        # Сохраняем данные, чтобы потом отредактировать сообщение заказа
        await state.update_data(order_msg_id=callback.message.message_id, client_id=user_id, admin_chat_id=callback.message.chat.id)
        await state.set_state(OrderState.waiting_for_custom_time)
        await callback.answer()
        return
    
    # Если выбрано готовое время (число)
    time_val = f"{action} минут"
    
    # Обновляем сообщение админа
    # Aiogram 3 хранит текст в html_text, но при редактировании нужно быть аккуратным
    # Просто берем текст из callback.message и добавляем статус
    
    original_text = callback.message.html_text.split("\n\n")[0] # Чистим от предыдущих статусов если были
    
    await callback.message.edit_text(
        text=f"{original_text}\n\n✅ <b>ПРИНЯТ В РАБОТУ</b>\n⏱ Готовность через: <b>{time_val}</b>",
        reply_markup=get_ready_kb(user_id)
    )
    
    # Уведомляем клиента
    try:
        await bot.send_message(chat_id=user_id, text=f"👨‍🍳 Ваш заказ принят!\n⏳ Время готовности: <b>{time_val}</b>.")
    except:
        pass
    
    await callback.answer()

# 3. Обработка ввода СВОЕГО времени (FSM)
@dp.message(OrderState.waiting_for_custom_time)
async def custom_time_handler(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    order_msg_id = user_data['order_msg_id']
    client_id = user_data['client_id']
    custom_time = message.text # Текст, который ввел админ
    
    # Удаляем сообщение админа с введенным временем (чтобы не мусорить)
    try:
        await message.delete()
    except:
        pass

    # Обновляем исходное сообщение с заказом (ставим статус и кнопку "Готов")
    # Нам нужно получить текст старого сообщения. В aiogram мы не можем просто "прочитать" чужое сообщение по ID.
    # ХИТРОСТЬ: Мы просто меняем клавиатуру на старом сообщении, а статус пишем новым сообщением-подтверждением
    # Либо (лучше): используем edit_message_reply_markup, чтобы сменить кнопки на "Готов", 
    # а информацию о времени отправляем отдельным реплаем.
    
    try:
        # Меняем кнопки на заказе на "Готов"
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id, 
            message_id=order_msg_id, 
            reply_markup=get_ready_kb(client_id)
        )
        # Отправляем подтверждение в чат админов
        await message.answer(
            f"✅ Заказ принят с ручным временем: <b>{custom_time}</b>", 
            reply_to_message_id=order_msg_id
        )
    except Exception as e:
        logging.error(f"FSM Edit Error: {e}")

    # Уведомляем клиента
    try:
        await bot.send_message(client_id, f"👨‍🍳 Ваш заказ принят!\n⏳ Время готовности: <b>{custom_time}</b>.")
    except:
        pass
    
    await state.clear()

# 4. Заказ Готов
@dp.callback_query(F.data.startswith("order_ready_"))
async def ready_callback(callback: CallbackQuery):
    user_id = callback.data.split("_")[2]
    
    # Убираем кнопки у админа, ставим статус "ГОТОВ"
    # Чтобы не терять текст заказа, берем текущий html_text
    current_text = callback.message.html_text
    
    # Если там уже был статус "Принят", заменяем его или добавляем новый
    if "ПРИНЯТ В РАБОТУ" in current_text:
        # Простая замена текста статуса (грубая)
        final_text = current_text.replace("✅ <b>ПРИНЯТ В РАБОТУ</b>", "🏁 <b>ЗАКАЗ ГОТОВ / ВЫДАН</b>")
    else:
        final_text = f"{current_text}\n\n🏁 <b>ЗАКАЗ ГОТОВ / ВЫДАН</b>"

    await callback.message.edit_text(text=final_text, reply_markup=None)
    
    try:
        await bot.send_message(chat_id=user_id, text="🎉 <b>Ваш заказ готов!</b>\nЖдем вас на выдаче. Приятного аппетита! ☕️")
    except:
        pass
        
    await callback.answer()

# --- ЗАПУСК ---
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
