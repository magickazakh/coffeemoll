import asyncio
import json
import logging
import sys
import os
from datetime import datetime
from aiohttp import web

# --- БИБЛИОТЕКИ ДЛЯ GOOGLE SHEETS ---
import gspread
from oauth2client.service_account import ServiceAccountCredentials
# ------------------------------------

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
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
SHEET_NAME = "COFFEEMOLL TELEGRAM"

# --- НАСТРОЙКА БАРИСТА ---
# ID - это внутренний ключ (1, 2, 3). 
# Впишите сюда реальные имена и номера Kaspi
BARISTAS = {
    "1": {"name": "Павел", "phone": "+7 771 904 44 55"},
    "2": {"name": "Карина", "phone": "+7 700 000 00 02"},
    "3": {"name": "Анара", "phone": "+7 700 000 00 03"}
}
# -----------------

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# --- СОСТОЯНИЯ (FSM) ---
class OrderState(StatesGroup):
    waiting_for_custom_time = State()

class ReviewState(StatesGroup):
    waiting_for_service_rate = State()
    waiting_for_food_rate = State()
    waiting_for_tips_decision = State() # Да/Нет
    waiting_for_barista_choice = State() # Выбор бариста
    waiting_for_comment = State() # Текст

# --- GOOGLE SHEETS ---

def get_creds_path():
    if os.path.exists("creds.json"): return "creds.json"
    elif os.path.exists("/etc/secrets/creds.json"): return "/etc/secrets/creds.json"
    return None

def get_gspread_client():
    path = get_creds_path()
    if not path: return None
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(path, scope)
    return gspread.authorize(creds)

def process_promo_code(code, user_id):
    if not code: return "NOT_FOUND"
    client = get_gspread_client()
    if not client: return "ERROR"
    
    try:
        sheet = client.open(SHEET_NAME)
        sheet_promo = sheet.worksheet("Promocodes")
        sheet_history = sheet.worksheet("PromoHistory")
        
        history = sheet_history.get_all_values()
        for row in history:
            if str(row[0]) == str(user_id) and str(row[1]).upper() == code.upper():
                return "USED"

        try: cell = sheet_promo.find(code)
        except: return "NOT_FOUND"

        limit = int(sheet_promo.cell(cell.row, 3).value or 0)
        if limit > 0:
            sheet_promo.update_cell(cell.row, 3, limit - 1)
            sheet_history.append_row([str(user_id), code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            return "OK"
        else: return "LIMIT"
    except Exception as e:
        logging.error(f"Sheets Error: {e}")
        return "ERROR"

def save_review(user_id, name, service_rate, food_rate, tips, comment):
    client = get_gspread_client()
    if not client: return
    try:
        sheet = client.open(SHEET_NAME).worksheet("Reviews")
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            str(user_id),
            name,
            service_rate,
            food_rate,
            tips,
            comment
        ])
    except Exception as e:
        logging.error(f"Save Review Error: {e}")

# --- ВЕБ-СЕРВЕР ---
async def health_check(request): return web.Response(text="OK")
async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- КЛАВИАТУРЫ ---

def get_decision_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"dec_accept_{user_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_reject_{user_id}")]
    ])

def get_time_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 мин", callback_data=f"time_5_{user_id}"),
         InlineKeyboardButton(text="10 мин", callback_data=f"time_10_{user_id}"),
         InlineKeyboardButton(text="15 мин", callback_data=f"time_15_{user_id}")],
        [InlineKeyboardButton(text="20 мин", callback_data=f"time_20_{user_id}"),
         InlineKeyboardButton(text="30 мин", callback_data=f"time_30_{user_id}"),
         InlineKeyboardButton(text="✍️ Своё", callback_data=f"time_custom_{user_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"time_back_{user_id}")]
    ])

def get_ready_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏁 Готов (Позвать клиента)", callback_data=f"ord_ready_{user_id}")]
    ])

def get_given_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выдан (Запросить отзыв)", callback_data=f"ord_given_{user_id}")]
    ])

# Клавиатуры для отзывов
def get_stars_kb(category):
    buttons = []
    for i in range(1, 6):
        buttons.append(InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rate_{category}_{i}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

def get_yes_no_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да 👍", callback_data="tips_yes"),
         InlineKeyboardButton(text="Нет 🙅‍♂️", callback_data="tips_no")]
    ])

def get_baristas_kb():
    buttons = []
    # Генерируем кнопки на основе словаря BARISTAS
    for b_id, data in BARISTAS.items():
        buttons.append([InlineKeyboardButton(text=data['name'], callback_data=f"barista_{b_id}")])
    # Добавляем кнопку отмены на всякий случай
    buttons.append([InlineKeyboardButton(text="Передумал", callback_data="tips_no")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_skip_comment_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Пропустить", callback_data="skip_comment")]
    ])

# --- ОБРАБОТЧИКИ ЗАКАЗА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    markup = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=WEB_APP_URL))]], resize_keyboard=True)
    await message.answer("Добро пожаловать в CoffeeMoll! 🥐", reply_markup=markup)

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        
        if data.get('type') == 'review': return

        cart = data.get('cart', [])
        total = data.get('total', 0)
        info = data.get('info', {})

        promo_code = info.get('promoCode', '')
        discount_rate = info.get('discount', 0)
        discount_text = ""
        
        if promo_code and discount_rate > 0:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, process_promo_code, promo_code, message.from_user.id)
            if res == "OK":
                try:
                    orig = round(total / (1 - discount_rate))
                    discount_text = f"\n🎁 <b>Промокод:</b> {promo_code} (-{int(orig - total)} ₸)"
                except: discount_text = f"\n🎁 <b>Промокод:</b> {promo_code}"
            else:
                try: total = int(round(total / (1 - discount_rate)))
                except: pass
                discount_text = f"\n❌ <b>Промокод:</b> {promo_code} (Ошибка/Лимит)"

        is_del = (info.get('deliveryType') == 'Доставка')
        text = f"{'🚗' if is_del else '🏃'} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        text += f"👤 {info.get('name')} (<a href='tel:{info.get('phone')}'>{info.get('phone')}</a>)\n"
        text += f"📍 {info.get('address') if is_del else 'Самовывоз'}\n"
        text += f"💳 {info.get('paymentType')}\n"
        if info.get('comment'): text += f"💬 <i>{info.get('comment')}</i>\n"
        
        text += f"➖➖➖➖➖➖➖➖➖➖\n"
        for i, item in enumerate(cart, 1):
            opts = [o for o in item.get('options', []) if o and o != "Без сахара"]
            text += f"{i}. <b>{item.get('name')}</b> {'('+ ', '.join(opts) +')' if opts else ''}\n"
            
        text += discount_text
        text += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_del: text += "\n⚠️ <i>+ Доставка</i>"

        await bot.send_message(ADMIN_CHAT_ID, text, reply_markup=get_decision_kb(message.chat.id))
        await message.answer(f"✅ Заказ принят!\nСумма: {total} ₸\nЖдите подтверждения времени.")

    except Exception as e: logging.error(f"Order Error: {e}")

# --- ЛОГИКА СТАТУСОВ (ADMIN) ---

@dp.callback_query(F.data.startswith("dec_"))
async def decision(c: CallbackQuery):
    act, uid = c.data.split("_")[1], c.data.split("_")[2]
    if act == "accept": await c.message.edit_reply_markup(reply_markup=get_time_kb(uid))
    else:
        await c.message.edit_text(f"{c.message.text}\n\n❌ <b>ОТКЛОНЕН</b>")
        try: await bot.send_message(uid, "❌ Заказ отклонен.")
        except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("time_"))
async def set_time(c: CallbackQuery, state: FSMContext):
    act, uid = c.data.split("_")[1], c.data.split("_")[2]
    if act == "back": 
        await c.message.edit_reply_markup(reply_markup=get_decision_kb(uid))
        return
    if act == "custom":
        await c.message.answer("Введите время (напр. '40 мин'):")
        await state.update_data(msg_id=c.message.message_id, uid=uid)
        await state.set_state(OrderState.waiting_for_custom_time)
        await c.answer()
        return
    
    t_val = f"{act} мин"
    clean_text = c.message.text.split("\n\n✅")[0]
    await c.message.edit_text(f"{clean_text}\n\n✅ <b>ПРИНЯТ</b> ({t_val})", reply_markup=get_ready_kb(uid))
    try: await bot.send_message(uid, f"👨‍🍳 Принят! Готовность: <b>{t_val}</b>.")
    except: pass
    await c.answer()

@dp.message(OrderState.waiting_for_custom_time)
async def custom_time(m: types.Message, state: FSMContext):
    d = await state.get_data()
    try: await m.delete()
    except: pass
    try:
        await bot.edit_message_reply_markup(m.chat.id, d['msg_id'], reply_markup=get_ready_kb(d['uid']))
        await bot.send_message(m.chat.id, f"Время установлено: {m.text}", reply_to_message_id=d['msg_id'])
        await bot.send_message(d['uid'], f"👨‍🍳 Принят! Готовность: <b>{m.text}</b>.")
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("ord_ready_"))
async def ready(c: CallbackQuery):
    uid = c.data.split("_")[2]
    old = c.message.text
    clean = old.split("\n\n")[0] if "ПРИНЯТ" in old else old
    
    await c.message.edit_text(f"{clean}\n\n🏁 <b>ГОТОВ К ВЫДАЧЕ</b>", reply_markup=get_given_kb(uid))
    try: await bot.send_message(uid, "🎉 <b>Ваш заказ готов!</b>\nЖдем вас на выдаче ☕️")
    except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("ord_given_"))
async def given(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split("_")[2])
    old = c.message.text
    clean = old.split("\n\n")[0]
    
    await c.message.edit_text(f"{clean}\n\n🤝 <b>ВЫДАН / ЗАВЕРШЕН</b>")
    
    # --- НАЧИНАЕМ СБОР ОТЗЫВА ---
    try:
        # 1. Сервис
        await bot.send_message(
            uid, 
            "Спасибо за заказ! 👋\nКак вам наше <b>обслуживание</b>?", 
            reply_markup=get_stars_kb("service")
        )
        # Мы не можем установить стейт пользователю из чужого обработчика так просто.
        # Но так как мы отправили сообщение с кнопкой, следующее действие юзера (нажатие)
        # будет обработано callback-ом, который и поставит стейт.
    except Exception as e:
        logging.error(f"Err review req: {e}")
        
    await c.answer()

# --- ЛОГИКА ОТЗЫВОВ (КЛИЕНТ) ---

# 1. Оценка Сервиса
@dp.callback_query(F.data.startswith("rate_service_"))
async def rate_service(c: CallbackQuery, state: FSMContext):
    rating = int(c.data.split("_")[2])
    await state.update_data(service_rate=rating)
    
    await c.message.edit_text(
        f"Обслуживание: {rating} ⭐\n\nКак оцените <b>еду и напитки</b>?", 
        reply_markup=get_stars_kb("food")
    )
    await state.set_state(ReviewState.waiting_for_food_rate)

# 2. Оценка Еды -> Решение о чаевых
@dp.callback_query(F.data.startswith("rate_food_"), ReviewState.waiting_for_food_rate)
async def rate_food(c: CallbackQuery, state: FSMContext):
    rating = int(c.data.split("_")[2])
    await state.update_data(food_rate=rating)
    
    # Проверяем оценку за сервис
    data = await state.get_data()
    service_rate = data.get('service_rate', 0)
    
    if service_rate >= 4:
        # Если сервис хороший -> Предлагаем чаевые
        await c.message.edit_text(
            f"Еда: {rating} ⭐\n\nЖелаете оставить <b>чаевые</b> бариста?", 
            reply_markup=get_yes_no_kb()
        )
        await state.set_state(ReviewState.waiting_for_tips_decision)
    else:
        # Если сервис плохой -> Сразу к комментарию
        await state.update_data(tips="Нет (Низкая оценка)")
        await c.message.edit_text(f"Еда: {rating} ⭐\n\nПожалуйста, напишите ваш отзыв или предложение:", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)

# 3. Решение о чаевых (Да/Нет)
@dp.callback_query(F.data.startswith("tips_"), ReviewState.waiting_for_tips_decision)
async def tips_decision(c: CallbackQuery, state: FSMContext):
    choice = c.data.split("_")[1] # yes или no
    
    if choice == "yes":
        # Показываем список бариста
        await c.message.edit_text("Кому вы хотите оставить чаевые?", reply_markup=get_baristas_kb())
        await state.set_state(ReviewState.waiting_for_barista_choice)
    else:
        # Отказ от чаевых -> К комментарию
        await state.update_data(tips="Нет")
        await c.message.edit_text("Поняли! 👌\nНапишите ваш отзыв (или нажмите пропустить):", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)

# 4. Выбор бариста -> Показ номера -> Переход к отзыву
@dp.callback_query(F.data.startswith("barista_"), ReviewState.waiting_for_barista_choice)
async def barista_choice(c: CallbackQuery, state: FSMContext):
    b_id = c.data.split("_")[1]
    barista = BARISTAS.get(b_id)
    
    if barista:
        tips_info = f"Выбрано: {barista['name']}"
        await state.update_data(tips=tips_info)
        
        # Показываем номер и сразу просим отзыв
        await c.message.edit_text(
            f"💳 Kaspi/Halyk ({barista['name']}):\n<code>{barista['phone']}</code>\n\nСпасибо за поддержку! ❤️\n\nНапишите ваш отзыв:", 
            reply_markup=get_skip_comment_kb()
        )
    else:
        await c.message.edit_text("Ошибка выбора. Напишите отзыв:", reply_markup=get_skip_comment_kb())
        
    await state.set_state(ReviewState.waiting_for_comment)

# 5. Текст отзыва (или Пропуск)
@dp.callback_query(F.data == "skip_comment", ReviewState.waiting_for_comment)
async def skip_comment(c: CallbackQuery, state: FSMContext):
    # Вызываем функцию завершения, передавая пустой текст
    await finalize_review(c.message, state, "Без текста", c.from_user)
    await c.answer()

@dp.message(ReviewState.waiting_for_comment)
async def comment_text(m: types.Message, state: FSMContext):
    await finalize_review(m, state, m.text, m.from_user)

async def finalize_review(message, state, comment_text, user):
    data = await state.get_data()
    
    # Сохраняем
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, save_review, 
                               user.id, 
                               user.first_name,
                               data.get('service_rate'),
                               data.get('food_rate'),
                               data.get('tips', 'Нет'),
                               comment_text)
    
    # Админу
    msg = f"⭐ <b>НОВЫЙ ОТЗЫВ</b>\n"
    msg += f"👤 {user.first_name}\n"
    msg += f"💁‍♂️ Сервис: {data.get('service_rate')} ⭐\n"
    msg += f"🍔 Еда: {data.get('food_rate')} ⭐\n"
    msg += f"💰 Чаевые: {data.get('tips')}\n"
    msg += f"💬 <i>{comment_text}</i>"
    
    await bot.send_message(ADMIN_CHAT_ID, msg)
    
    # Клиенту (если это callback, message.edit_text может быть недоступен, используем answer)
    if isinstance(message, types.Message):
        await message.answer("Спасибо за отзыв! Ждем вас снова! ❤️")
    else:
        await message.edit_text("Спасибо за отзыв! Ждем вас снова! ❤️")
        
    await state.clear()

# --- ЗАПУСК ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Bot started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
