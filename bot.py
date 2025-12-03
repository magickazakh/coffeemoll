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
ADMIN_CHAT_ID = -1003356844624
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"
SHEET_NAME = "CoffeeMoll Menu"

# --- НАСТРОЙКИ ТЕМ (TOPICS) ---
TOPIC_ID_ORDERS = 68
TOPIC_ID_REVIEWS = 69
# ------------------------------

KASPI_NUMBER = "+7 747 240 20 02" 

# --- НАСТРОЙКА БАРИСТА ---
BARISTAS = {
    "1": {"name": "Анара", "phone": "+7 700 000 00 01"},
    "2": {"name": "Карина", "phone": "+7 700 000 00 02"},
    "3": {"name": "Павел", "phone": "+7 771 904 44 55"}
}
# -----------------

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class OrderState(StatesGroup):
    waiting_f8or_custom_time = State()

class ReviewState(StatesGroup):
    waiting_for_service_rate = State()
    waiting_for_food_rate = State()
    waiting_for_tips_decision = State()
    waiting_for_barista_choice = State()
    waiting_for_comment = State()

# --- GOOGLE SHEETS ---
_gs_client = None
_gs_sheet_cache = None

def get_creds_path():
    if os.path.exists("creds.json"): return "creds.json"
    elif os.path.exists("/etc/secrets/creds.json"): return "/etc/secrets/creds.json"
    return None

def get_gspread_service():
    global _gs_client, _gs_sheet_cache
    try:
        if _gs_client and _gs_sheet_cache:
            return _gs_client, _gs_sheet_cache
        path = get_creds_path()
        if not path: return None, None
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(path, scope)
        _gs_client = gspread.authorize(creds)
        _gs_sheet_cache = _gs_client.open(SHEET_NAME)
        return _gs_client, _gs_sheet_cache
    except Exception as e:
        logging.error(f"Connection Error: {e}")
        _gs_client = None
        _gs_sheet_cache = None
        return None, None

# --- ФУНКЦИЯ ДЛЯ ФОНОВОЙ ПРОВЕРКИ (ТОЛЬКО ЧТЕНИЕ) ---
def check_promo_status(code, user_id):
    """Проверяет статус, но НЕ списывает использование"""
    global _gs_client
    if not code: return "NOT_FOUND", 0
    
    for attempt in range(2):
        try:
            client, spreadsheet = get_gspread_service()
            if not spreadsheet: return "ERROR", 0
            
            sheet_promo = spreadsheet.worksheet("Promocodes")
            sheet_history = spreadsheet.worksheet("PromoHistory")
            
            # 1. Проверка истории
            history = sheet_history.get_all_values()
            for row in history:
                if str(row[0]) == str(user_id) and str(row[1]).upper() == code.upper():
                    return "USED", 0

            # 2. Поиск кода
            try: cell = sheet_promo.find(code)
            except: return "NOT_FOUND", 0

            # 3. Получение скидки и лимита
            # B=Discount(2), C=Limit(3)
            discount_val = sheet_promo.cell(cell.row, 2).value
            limit = int(sheet_promo.cell(cell.row, 3).value or 0)
            
            if limit > 0:
                try:
                    discount = float(str(discount_val).replace(',', '.'))
                except:
                    discount = 0
                return "OK", discount
            else: 
                return "LIMIT", 0
                
        except Exception as e:
            logging.warning(f"Check Promo Error: {e}")
            _gs_client = None
            if attempt == 1: return "ERROR", 0

# --- ФУНКЦИЯ СПИСАНИЯ (ДЛЯ ЗАКАЗА) ---
def process_promo_code(code, user_id):
    global _gs_client
    for attempt in range(2):
        try:
            client, spreadsheet = get_gspread_service()
            if not spreadsheet: return "ERROR"
            sheet_promo = spreadsheet.worksheet("Promocodes")
            sheet_history = spreadsheet.worksheet("PromoHistory")
            
            # Повторная проверка перед записью (на всякий случай)
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
            _gs_client = None
            if attempt == 1: return "ERROR"

def save_review(user_id, name, service_rate, food_rate, tips, comment):
    client, spreadsheet = get_gspread_service()
    if not spreadsheet: return
    try:
        spreadsheet.worksheet("Reviews").append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(user_id), name, service_rate, food_rate, tips, comment])
    except: pass

# --- API ДЛЯ WEB APP ---
async def api_check_promo(request):
    # CORS заголовки
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    
    if request.method == 'OPTIONS':
        return web.Response(headers=headers)

    try:
        data = await request.json()
        code = data.get('code', '').strip().upper()
        user_id = data.get('userId')
        
        loop = asyncio.get_running_loop()
        status, discount = await loop.run_in_executor(None, check_promo_status, code, user_id)
        
        return web.json_response({'status': status, 'discount': discount}, headers=headers)
    except Exception as e:
        return web.json_response({'status': 'ERROR', 'error': str(e)}, headers=headers)

async def health_check(request): return web.Response(text="OK")

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    
    # ДОБАВЛЕН НОВЫЙ МАРШРУТ
    app.router.add_post("/api/check_promo", api_check_promo)
    app.router.add_options("/api/check_promo", api_check_promo)
    
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
        [InlineKeyboardButton(text="🏁 Готов", callback_data=f"ord_ready_{user_id}")]
    ])

def get_given_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выдан / Передан курьеру", callback_data=f"ord_given_{user_id}")]
    ])

def get_received_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Заказ получен", callback_data="ord_received")]
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
    for b_id, data in BARISTAS.items():
        buttons.append([InlineKeyboardButton(text=data['name'], callback_data=f"barista_{b_id}")])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="tips_no")])
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
        client_warning = "" 
        
        if promo_code and discount_rate > 0:
            loop = asyncio.get_running_loop()
            # Промокоды проверяем в executor, с механизмом повторных попыток
            res = await loop.run_in_executor(None, process_promo_code, promo_code, message.from_user.id)
            
            if res == "OK":
                try:
                    orig = round(total / (1 - discount_rate))
                    discount_text = f"\n🎁 <b>Промокод:</b> {promo_code} (-{int(orig - total)} ₸)"
                except: discount_text = f"\n🎁 <b>Промокод:</b> {promo_code}"
            else:
                try: total = int(round(total / (1 - discount_rate)))
                except: pass
                
                if res == "USED":
                    discount_text = f"\n❌ <b>Промокод:</b> {promo_code} (Повтор)"
                    client_warning = f"\n⚠️ <b>Промокод {promo_code} уже использован вами!</b>\nСкидка отменена."
                elif res == "LIMIT":
                    discount_text = f"\n❌ <b>Промокод:</b> {promo_code} (Лимит)"
                    client_warning = f"\n⚠️ <b>Лимит промокода {promo_code} исчерпан!</b>\nСкидка отменена."
                else:
                    discount_text = f"\n❌ <b>Промокод:</b> {promo_code} (Ошибка)"
                    client_warning = f"\n⚠️ <b>Ошибка применения промокода!</b>\nСкидка отменена."

        # Определение типа заказа для чека
        delivery_type = info.get('deliveryType') # "Доставка", "В зале", "Самовывоз"
        is_del = (delivery_type == 'Доставка')
        
        order_icon = "🚗" if is_del else "🏃"
        
        text = f"{order_icon} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n"
        text += f"👤 {info.get('name')} (<a href='tel:{info.get('phone')}'>{info.get('phone')}</a>)\n"
        
        if is_del:
            text += f"📍 <b>Адрес:</b> {info.get('address')}\n"
        else:
            text += f"📍 <b>{delivery_type}</b>\n"
            
        text += f"💳 {info.get('paymentType')}\n"
        
        # --- ИСПРАВЛЕНИЕ: Добавляем номер телефона для Kaspi/Halyk ---
        if info.get('paymentType') in ['Kaspi', 'Halyk']:
            text += f"📱 <b>Счет:</b> <code>{info.get('paymentPhone')}</code>\n"
        # ------------------------------------------------------------

        if info.get('comment'): text += f"💬 <i>{info.get('comment')}</i>\n"
        
        text += f"➖➖➖➖➖➖➖➖➖➖\n"
        for i, item in enumerate(cart, 1):
            opts = [o for o in item.get('options', []) if o and o != "Без сахара"]
            opts_str = f" ({', '.join(opts)})" if opts else ""
            qty = item.get('qty', 1)
            qty_str = f" <b>x {qty}</b>" if qty > 1 else ""
            
            text += f"{i}. <b>{item.get('name')}</b>{opts_str}{qty_str}\n"
            
        text += discount_text
        text += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_del: text += "\n⚠️ <i>+ Доставка</i>"

        await bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=text, 
            reply_markup=get_decision_kb(message.chat.id),
            message_thread_id=TOPIC_ID_ORDERS
        )
        
        # Ответ клиенту с предупреждением, если промокод слетел
        response_text = f"✅ Заказ принят!\nСумма: {total} ₸"
        if client_warning:
            response_text += f"\n{client_warning}"
        response_text += "\n\nЖдите подтверждения времени."

        await message.answer(response_text)

    except Exception as e: logging.error(f"Order Error: {e}")

# --- ЛОГИКА СТАТУСОВ (ADMIN) ---

@dp.callback_query(F.data.startswith("dec_"))
async def decision(c: CallbackQuery):
    act, uid = c.data.split("_")[1], c.data.split("_")[2]
    if act == "accept": await c.message.edit_reply_markup(reply_markup=get_time_kb(uid))
    else:
        await c.message.edit_text(f"{c.message.text}\n\n❌ <b>ОТКЛОНЕН</b>")
        try: await bot.send_message(uid, "❌ Заказ отклонен. Скоро свяжемся с вами для уточнения.")
        except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("time_"))
async def set_time(c: CallbackQuery, state: FSMContext):
    act, uid = c.data.split("_")[1], c.data.split("_")[2]
    if act == "back": 
        await c.message.edit_reply_markup(reply_markup=get_decision_kb(uid))
        return
    if act == "custom":
        await c.message.answer("Введите время (напр. '40 мин или 17:30'):")
        await state.update_data(msg_id=c.message.message_id, uid=uid)
        await state.set_state(OrderState.waiting_for_custom_time)
        await c.answer()
        return
    
    t_val = f"{act} мин"
    old_text = c.message.text
    is_delivery = "🚗" in old_text 
    
    clean_text = old_text.split("\n\n✅")[0]
    await c.message.edit_text(f"{clean_text}\n\n✅ <b>ПРИНЯТ</b> ({t_val})", reply_markup=get_ready_kb(uid))
    
    msg_client = f"👨‍🍳 Принят! Готовность: <b>{t_val}</b>.\n📞Телефон для связи: +77006437303"
    if is_delivery:
        msg_client += "\n<i>(Время приготовления, без учета доставки)</i>"
        
    try: await bot.send_message(uid, msg_client)
    except: pass
    await c.answer()

@dp.message(OrderState.waiting_for_custom_time)
async def custom_time(m: types.Message, state: FSMContext):
    d = await state.get_data()
    try: await m.delete()
    except: pass
    try:
        await bot.edit_message_reply_markup(m.chat.id, d['msg_id'], reply_markup=get_ready_kb(d['uid']))
        
        await bot.send_message(
            chat_id=m.chat.id, 
            text=f"Время установлено: {m.text}", 
            reply_to_message_id=d['msg_id'],
            message_thread_id=TOPIC_ID_ORDERS
        )
        
        await bot.send_message(d['uid'], f"👨‍🍳 Принят! Готовность: <b>{m.text}</b>.\n<i>(Если это доставка, время пути не учтено)</i>")
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("ord_ready_"))
async def ready(c: CallbackQuery):
    uid = c.data.split("_")[2]
    old = c.message.text
    clean = old.split("\n\n")[0] if "ПРИНЯТ" in old else old
    
    is_del = "🚗" in old or "Доставка" in old
    
    # Меняем статус админа на "ГОТОВ"
    await c.message.edit_text(f"{clean}\n\n🏁 <b>ГОТОВ</b>", reply_markup=get_given_kb(uid))
    
    client_msg = "📦 <b>Заказ готов и упакован!</b>\nОжидаем курьера." if is_del else "🎉 <b>Ваш заказ готов!</b>\nЖдем вас на выдаче ☕️"
    
    try: await bot.send_message(uid, client_msg)
    except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("ord_given_"))
async def given(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split("_")[2])
    old = c.message.text
    clean = old.split("\n\n")[0]
    
    is_del = "🚗" in clean or "Доставка" in clean
    
    # Финальный статус у админа
    status_text = "🚗 <b>КУРЬЕР ВЫЕХАЛ</b>" if is_del else "🤝 <b>ВЫДАН / ЗАВЕРШЕН</b>"
    await c.message.edit_text(f"{clean}\n\n{status_text}")
    
    # --- ЛОГИКА ЗАПРОСА ОТЗЫВА ---
    try:
        if is_del:
            await bot.send_message(
                uid,
                "🚗 Курьер выехал!\nКак только получите заказ, нажмите кнопку ниже, чтобы оценить качество:",
                reply_markup=get_received_kb()
            )
        else:
            await start_review_process(uid, state)

    except Exception as e:
        logging.error(f"Err review req: {e}")
        
    await c.answer()

@dp.callback_query(F.data == "ord_received")
async def delivery_received(c: CallbackQuery, state: FSMContext):
    await c.message.edit_reply_markup(reply_markup=None) 
    await c.message.answer("Приятного аппетита! 😋")
    
    await state.update_data(is_delivery=True)
    await start_review_process(c.from_user.id, state)
    await c.answer()

async def start_review_process(user_id, state):
    await bot.send_message(
        user_id, 
        "Как вам наше <b>обслуживание</b>?", 
        reply_markup=get_stars_kb("service")
    )

# --- ЛОГИКА ОТЗЫВОВ (КЛИЕНТ) ---

@dp.callback_query(F.data.startswith("rate_service_"))
async def rate_service(c: CallbackQuery, state: FSMContext):
    rating = int(c.data.split("_")[2])
    await state.update_data(service_rate=rating)
    
    await c.message.edit_text(
        f"Обслуживание: {rating} ⭐\n\nКак оцените <b>еду и напитки</b>?", 
        reply_markup=get_stars_kb("food")
    )
    await state.set_state(ReviewState.waiting_for_food_rate)

@dp.callback_query(F.data.startswith("rate_food_"), ReviewState.waiting_for_food_rate)
async def rate_food(c: CallbackQuery, state: FSMContext):
    rating = int(c.data.split("_")[2])
    await state.update_data(food_rate=rating)
    
    data = await state.get_data()
    service_rate = data.get('service_rate', 0)
    is_delivery = data.get('is_delivery', False) 
    
    if service_rate >= 4 and not is_delivery:
        await c.message.edit_text(
            f"Еда: {rating} ⭐\n\nЖелаете оставить <b>чаевые</b> бариста?", 
            reply_markup=get_yes_no_kb()
        )
        await state.set_state(ReviewState.waiting_for_tips_decision)
    else:
        tips_reason = "Нет (Доставка)" if is_delivery else "Нет (Низкая оценка)"
        await state.update_data(tips=tips_reason)
        
        text_msg = "Пожалуйста, напишите ваш отзыв о доставке:" if is_delivery else "Пожалуйста, напишите ваш отзыв или предложение:"
        await c.message.edit_text(f"Еда: {rating} ⭐\n\n{text_msg}", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)

@dp.callback_query(F.data.startswith("tips_"), ReviewState.waiting_for_tips_decision)
async def tips_decision(c: CallbackQuery, state: FSMContext):
    choice = c.data.split("_")[1]
    
    if choice == "yes":
        await c.message.edit_text("Кому вы хотите оставить чаевые?", reply_markup=get_baristas_kb())
        await state.set_state(ReviewState.waiting_for_barista_choice)
    else:
        await state.update_data(tips="Нет")
        await c.message.edit_text("Поняли! 👌\nНапишите ваш отзыв (или нажмите пропустить):", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)

@dp.callback_query(F.data.startswith("barista_"), ReviewState.waiting_for_barista_choice)
async def barista_choice(c: CallbackQuery, state: FSMContext):
    b_id = c.data.split("_")[1]
    barista = BARISTAS.get(b_id)
    
    if barista:
        tips_info = f"Выбрано: {barista['name']}"
        await state.update_data(tips=tips_info)
        
        await c.message.edit_text(
            f"💳 Kaspi\Halyk ({barista['name']}):\n<code>{barista['phone']}</code>\n\nСпасибо за поддержку! ❤️\n\nНапишите ваш отзыв:", 
            reply_markup=get_skip_comment_kb()
        )
    else:
        await c.message.edit_text("Ошибка выбора. Напишите отзыв:", reply_markup=get_skip_comment_kb())
        
    await state.set_state(ReviewState.waiting_for_comment)

@dp.callback_query(F.data == "skip_comment", ReviewState.waiting_for_comment)
async def skip_comment(c: CallbackQuery, state: FSMContext):
    await finalize_review(c.message, state, "Без текста", c.from_user)
    await c.answer()

@dp.message(ReviewState.waiting_for_comment)
async def comment_text(m: types.Message, state: FSMContext):
    await finalize_review(m, state, m.text, m.from_user)

async def finalize_review(message, state, comment_text, user):
    data = await state.get_data()
    
    # Сохранение в фоне
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, save_review, 
                         user.id, 
                         user.first_name,
                         data.get('service_rate'),
                         data.get('food_rate'),
                         data.get('tips', 'Нет'),
                         comment_text)
    
    msg = f"⭐ <b>НОВЫЙ ОТЗЫВ</b>\n"
    msg += f"👤 {user.first_name}\n"
    msg += f"💁‍♂️ Сервис: {data.get('service_rate')} ⭐\n"
    msg += f"🍔 Еда: {data.get('food_rate')} ⭐\n"
    msg += f"💰 Чаевые: {data.get('tips')}\n"
    msg += f"💬 <i>{comment_text}</i>"
    
    await bot.send_message(
        chat_id=ADMIN_CHAT_ID, 
        text=msg,
        message_thread_id=TOPIC_ID_REVIEWS
    )
    
    avg_rate = (int(data.get('service_rate', 5)) + int(data.get('food_rate', 5))) / 2
    
    if avg_rate == 5:
        response_text = "Вау! 😍 Спасибо за высокую оценку!\nМы счастливы, что вам понравилось. Ждем вас снова за лучшим кофе! ☕️"
    elif avg_rate >= 4:
        response_text = "Спасибо за хороший отзыв! 😊\nБудем стараться стать еще лучше для вас."
    else:
        response_text = "Нам очень жаль, что мы вас расстроили. 😔\nСпасибо за честность, мы обязательно проработаем ошибки."

    if isinstance(message, types.Message):
        await message.answer(response_text)
    else:
        await message.edit_text(response_text)
        
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






