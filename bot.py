import asyncio
import json
import logging
import sys
import os
from aiohttp import web

# --- БИБЛИОТЕКИ ДЛЯ GOOGLE SHEETS ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials
# ------------------------------------

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
ADMIN_CHAT_ID = 1054308942
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"
SHEET_NAME = "COFFEEMOLL TELEGRAM" # <--- УКАЖИТЕ ТОЧНОЕ НАЗВАНИЕ ВАШЕЙ ТАБЛИЦЫ В GOOGLE
# -----------------

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class OrderState(StatesGroup):
    waiting_for_custom_time = State()

# --- ЛОГИКА GOOGLE SHEETS И PROMO ---

def get_creds_path():
    """Определяет, где лежит файл ключей (Локально или на Render)"""
    if os.path.exists("creds.json"):
        return "creds.json"
    elif os.path.exists("/etc/secrets/creds.json"):
        return "/etc/secrets/creds.json"
    return None

def process_promo_code(code):
    """
    Ищет промокод в таблице, проверяет лимит.
    Если ок -> уменьшает лимит на 1.
    """
    if not code: return False
    
    creds_file = get_creds_path()
    if not creds_file:
        logging.error("❌ Файл creds.json не найден!")
        return True # Если ошибка на нашей стороне, лучше простить клиенту скидку

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_file, scope)
        client = gspread.authorize(creds)
        
        # Открываем таблицу и лист Promocodes
        sheet = client.open(SHEET_NAME).worksheet("Promocodes")
        
        # Ищем ячейку с кодом
        try:
            cell = sheet.find(code)
        except gspread.exceptions.CellNotFound:
            logging.warning(f"Промокод {code} не найден в таблице")
            return False 

        # Лимит находится в колонке 3 (C) той же строки
        limit_cell_val = sheet.cell(cell.row, 3).value
        limit = int(limit_cell_val) if limit_cell_val else 0
        
        if limit > 0:
            # Уменьшаем лимит на 1
            sheet.update_cell(cell.row, 3, limit - 1)
            logging.info(f"Промокод {code} применен. Осталось: {limit - 1}")
            return True
        else:
            logging.warning(f"Лимит промокода {code} исчерпан")
            return False

    except Exception as e:
        logging.error(f"Ошибка API Google Sheets: {e}")
        return True # В случае ошибки API сохраняем скидку

# --- ВЕБ-СЕРВЕР ---
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
            InlineKeyboardButton(text="20 мин", callback_data=f"time_20_{user_id}"),
            InlineKeyboardButton(text="30 мин", callback_data=f"time_30_{user_id}"),
            InlineKeyboardButton(text="✍️ Своё время", callback_data=f"time_custom_{user_id}")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"time_back_{user_id}")]
    ])

def get_ready_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏁 Готов к выдаче", callback_data=f"order_ready_{user_id}")]
    ])

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    await message.answer("Добро пожаловать в CoffeeMoll! 🥐", reply_markup=markup)

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        cart = data.get('cart', [])
        total = data.get('total', 0)
        info = data.get('info', {})

        # --- ОБРАБОТКА ПРОМОКОДА ---
        promo_code = info.get('promoCode', '')
        discount_rate = info.get('discount', 0)
        discount_text_for_admin = ""
        
        # Если есть код, пытаемся списать его в базе
        if promo_code and discount_rate > 0:
            # Запускаем в отдельном потоке, чтобы не блокировать бота
            loop = asyncio.get_running_loop()
            promo_success = await loop.run_in_executor(None, process_promo_code, promo_code)
            
            if promo_success:
                # Рассчитываем сумму скидки для красоты чека
                try:
                    # total - это уже цена со скидкой. Восстанавливаем полную цену.
                    # Формула: Total = Original * (1 - rate)  => Original = Total / (1 - rate)
                    original_price = int(total / (1 - discount_rate))
                    discount_amount = original_price - total
                    discount_text_for_admin = f"\n🎁 <b>Промокод:</b> {promo_code} (-{discount_amount} ₸)"
                except:
                    discount_text_for_admin = f"\n🎁 <b>Промокод:</b> {promo_code}"
            else:
                # Если код не списался (лимит кончился прямо сейчас), можно уведомить админа
                discount_text_for_admin = f"\n⚠️ <b>Промокод:</b> {promo_code} (Ошибка списания/Лимит)"
        # ---------------------------

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
        
        # Время (из комментария)
        if "Ко времени" in str(info.get('comment')):
             text += "⏰ <b>КО ВРЕМЕНИ!</b>\n"

        text += f"➖➖➖➖➖➖➖➖➖➖\n<b>📋 ЗАКАЗ:</b>\n"
        
        for i, item in enumerate(cart, 1):
            options = item.get('options', [])
            name = item.get('name', 'Товар')
            opts = [o for o in options if o and o != "Без сахара"]
            opts_str = f" ({', '.join(opts)})" if opts else ""
            text += f"{i}. <b>{name}</b>{opts_str}\n"
            
        # Добавляем строку про скидку, если есть
        text += discount_text_for_admin
        
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
        old_text = callback.message.text 
        
        await callback.message.edit_text(
            text=f"{old_text}\n\n❌ <b>ОТКЛОНЕН</b>", 
            reply_markup=None
        )
        try: 
            await bot.send_message(chat_id=user_id, text="❌ Заказ отклонен.\nСкоро свяжемся с вами для уточнения.") 
        except: 
            pass
    
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
    
    old_text = callback.message.text
    clean_text = old_text.split("\n\n✅")[0] 
    
    await callback.message.edit_text(
        text=f"{clean_text}\n\n✅ <b>ПРИНЯТ</b> ({time_val})", 
        reply_markup=get_ready_kb(user_id)
    )
    
    try: 
        await bot.send_message(chat_id=user_id, text=f"👨‍🍳 Заказ принят!\n⏳ Готовность: <b>{time_val}</b>.\n📞Телефон для связи: +77006437303")
    except: 
        pass
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
        await bot.edit_message_reply_markup(
            chat_id=message.chat.id, 
            message_id=order_msg_id, 
            reply_markup=get_ready_kb(client_id)
        )
        await bot.send_message(
            chat_id=message.chat.id,
            text=f"✅ Время для заказа выше установлено: <b>{custom_time}</b>",
            reply_to_message_id=order_msg_id
        )
        
        await bot.send_message(client_id, f"👨‍🍳 Заказ принят!\n⏳ Готовность: <b>{custom_time}</b>.\n📞Телефон для связи: +77006437303")
    except Exception as e:
        logging.error(f"Custom time error: {e}")
    
    await state.clear()

@dp.callback_query(F.data.startswith("order_ready_"))
async def ready_callback(callback: CallbackQuery):
    user_id = callback.data.split("_")[2]
    old_text = callback.message.text or ""
    
    is_delivery = "Доставка" in old_text
    
    if is_delivery:
        admin_status = "🏁 <b>ЗАКАЗ ПЕРЕДАН КУРЬЕРУ</b>"
        client_msg = "📦 <b>Ваш заказ передан курьеру!</b>\nОжидайте доставку. Приятного аппетита!"
    else:
        admin_status = "🏁 <b>ЗАКАЗ ГОТОВ / ВЫДАН</b>"
        client_msg = "🎉 <b>Ваш заказ готов!</b>\nЖдем вас на выдаче. Приятного аппетита! ☕️"

    if "ПРИНЯТ" in old_text:
        clean_text = old_text.split("✅")[0].strip()
        final_text = f"{clean_text}\n\n{admin_status}"
    else:
        final_text = f"{old_text}\n\n{admin_status}"

    await callback.message.edit_text(text=final_text, reply_markup=None)
    
    try: 
        await bot.send_message(chat_id=user_id, text=client_msg)
    except: 
        pass
        
    await callback.answer()

# --- ЗАПУСК ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Bot started polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")

