import asyncio
import json
import logging
import sys
import os
import re 
import time
from datetime import datetime
from aiohttp import web

# --- FIREBASE IMPORTS ---
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
# ------------------------

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
# ВАЖНО: Никогда не храните токен в коде. Используйте переменные окружения.
TOKEN = os.getenv("BOT_TOKEN") 
if not TOKEN:
    logging.critical("❌ BOT_TOKEN is not set!")
    sys.exit(1)

ADMIN_CHAT_ID = -1003356844624
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"

# --- НАСТРОЙКИ ТЕМ (TOPICS) ---
TOPIC_ID_ORDERS = 68
TOPIC_ID_REVIEWS = 69
# ------------------------------

KASPI_NUMBER = "+7 747 240 20 02" 

# --- НАСТРОЙКА БАРИСТА ---
BARISTAS = {
    "1": {"name": "Анара", "phone": "+7 747 240 20 02 (только Kaspi)"},
    "2": {"name": "Карина", "phone": "+7 776 962 28 14"},
    "3": {"name": "Павел", "phone": "+7 771 904 44 55"}
}
# -----------------

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class OrderState(StatesGroup):
    waiting_for_custom_time = State()

class ReviewState(StatesGroup):
    waiting_for_service_rate = State()
    waiting_for_food_rate = State()
    waiting_for_tips_decision = State()
    waiting_for_barista_choice = State()
    waiting_for_comment = State()

# --- ГЛОБАЛЬНЫЙ КЕШ ---
PROMO_CACHE = {}
NAMES_CACHE = {}

# --- FIREBASE SETUP ---
_db_client = None

def init_firebase():
    global _db_client
    if _db_client:
        return _db_client

    if not firebase_admin._apps:
        # Попытка найти файл кредов в разных местах (локально или в Docker volume)
        possible_paths = ["firebase_creds.json", "/etc/secrets/firebase_creds.json"]
        cred_path = None
        
        for path in possible_paths:
            if os.path.exists(path):
                cred_path = path
                break
            
        if cred_path:
            try:
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
                logging.info(f"🔥 Firebase Connected using {cred_path}!")
                _db_client = firestore.client()
            except Exception as e:
                logging.error(f"❌ Firebase Init Error: {e}")
                return None
        else:
            logging.warning("⚠️ Firebase credentials file not found! Database features will be disabled.")
            return None
    
    return _db_client

db = init_firebase()

def clean_id(raw_id):
    if not raw_id: return ""
    return re.sub(r'\D', '', str(raw_id))

# --- ФОНОВАЯ ЗАДАЧА: СИНХРОНИЗАЦИЯ КЕША ---
async def cache_updater_task():
    global PROMO_CACHE
    while True:
        try:
            if db:
                docs = db.collection('promocodes').stream()
                new_cache = {}
                for doc in docs:
                    data = doc.to_dict()
                    code = doc.id.strip().upper()
                    limit = data.get('limit', 0)
                    discount = data.get('discount', 0.0)
                    new_cache[code] = {'discount': float(discount), 'limit': int(limit)}
                
                PROMO_CACHE = new_cache
        except Exception as e:
            logging.error(f"Cache Update Error: {e}")
        
        await asyncio.sleep(60)

# --- ЛОГИКА ПРОМОКОДОВ ---

def check_promo_firebase(code, user_id):
    if not db: return "ERROR", 0
    code = code.strip().upper()
    uid = clean_id(user_id)
    
    try:
        # 1. Кеш
        promo_data = PROMO_CACHE.get(code)
        if not promo_data:
            doc = db.collection('promocodes').document(code).get()
            if not doc.exists: return "NOT_FOUND", 0
            promo_data = doc.to_dict()

        limit = promo_data.get('limit', 0)
        discount = promo_data.get('discount', 0)
        try: discount = float(discount)
        except: discount = 0
        
        if limit <= 0: return "LIMIT", 0

        # 2. История
        history_ref = db.collection('promo_history').document(f"{uid}_{code}")
        if history_ref.get().exists: return "USED", 0
        
        return "OK", discount
            
    except Exception as e:
        logging.error(f"Check Error: {e}")
        return "ERROR", 0

@firestore.transactional
def use_promo_transaction(transaction, code, uid):
    promo_ref = db.collection('promocodes').document(code)
    history_ref = db.collection('promo_history').document(f"{uid}_{code}")
    
    snapshot = promo_ref.get(transaction=transaction)
    if not snapshot.exists: return "NOT_FOUND"
    
    current_limit = snapshot.get('limit')
    if current_limit <= 0: return "LIMIT"
        
    hist_snap = history_ref.get(transaction=transaction)
    if hist_snap.exists: return "USED"

    transaction.update(promo_ref, {'limit': current_limit - 1})
    transaction.set(history_ref, {
        'user_id': uid,
        'code': code,
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    return "OK"

def process_promo_firebase(code, user_id):
    if not db: return "ERROR"
    code = code.strip().upper()
    uid = clean_id(user_id)
    try:
        transaction = db.transaction()
        result = use_promo_transaction(transaction, code, uid)
        if result == "OK" and code in PROMO_CACHE:
             PROMO_CACHE[code]['limit'] -= 1
        return result
    except Exception as e:
        logging.error(f"Transaction Error: {e}")
        return "ERROR"

# --- ЗАПИСЬ ОТЗЫВОВ ---

async def save_review_background(user_id, name, service_rate, food_rate, tips, comment):
    if not db: return
    def _save():
        try:
            db.collection('reviews').add({
                'user_id': str(user_id),
                'name': name,
                'service_rate': service_rate,
                'food_rate': food_rate,
                'tips': tips,
                'comment': comment,
                'timestamp': firestore.SERVER_TIMESTAMP,
                'date_str': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        except Exception as e: logging.error(f"Save Review Error: {e}")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save)

# --- API ---

async def api_check_promo(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    }
    if request.method == 'OPTIONS': return web.Response(headers=headers)

    try:
        data = await request.json()
        code = data.get('code', '')
        user_id = data.get('userId')
        loop = asyncio.get_running_loop()
        status, discount = await loop.run_in_executor(None, check_promo_firebase, code, user_id)
        return web.json_response({'status': status, 'discount': discount}, headers=headers)
    except Exception as e:
        return web.json_response({'status': 'ERROR', 'error': str(e)}, headers=headers)

async def health_check(request): return web.Response(text="OK")

async def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    app.router.add_post("/api/check_promo", api_check_promo)
    app.router.add_options("/api/check_promo", api_check_promo)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    asyncio.create_task(cache_updater_task())
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Bot started...")
    await dp.start_polling(bot)

# --- КЛАВИАТУРЫ ---
def get_decision_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять", callback_data=f"dec_accept_{user_id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_reject_{user_id}")]])
def get_time_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="5 мин", callback_data=f"time_5_{user_id}"), InlineKeyboardButton(text="10 мин", callback_data=f"time_10_{user_id}"), InlineKeyboardButton(text="15 мин", callback_data=f"time_15_{user_id}")], [InlineKeyboardButton(text="20 мин", callback_data=f"time_20_{user_id}"), InlineKeyboardButton(text="30 мин", callback_data=f"time_30_{user_id}"), InlineKeyboardButton(text="✍️ Своё", callback_data=f"time_custom_{user_id}")], [InlineKeyboardButton(text="🔙 Назад", callback_data=f"time_back_{user_id}")]])
def get_ready_kb(user_id): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏁 Готов", callback_data=f"ord_ready_{user_id}")]])
def get_given_kb(user_id): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Выдан / Передан курьеру", callback_data=f"ord_given_{user_id}")]])
def get_received_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 Заказ получен", callback_data="ord_received")]])
def get_stars_kb(c): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rate_{c}_{i}") for i in range(1, 6)]])
def get_yes_no_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Да 👍", callback_data="tips_yes"), InlineKeyboardButton(text="Нет 🙅‍♂️", callback_data="tips_no")]])
def get_baristas_kb():
    b = [[InlineKeyboardButton(text=d['name'], callback_data=f"barista_{k}")] for k, d in BARISTAS.items()]
    b.append([InlineKeyboardButton(text="Отмена", callback_data="barista_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=b)
def get_skip_comment_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ Пропустить", callback_data="skip_comment")]])


# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    # Добавляем timestamp для обхода кеша картинок и стилей в WebApp при обновлении
    unique_url = f"{WEB_APP_URL}?v={int(time.time())}"

    await m.answer("Добро пожаловать в CoffeeMoll! 🥐", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=unique_url))]], resize_keyboard=True))
    
@dp.message(F.web_app_data)
async def web_app_data_handler(m: types.Message):
    try:
        if not m.web_app_data.data: return

        d = json.loads(m.web_app_data.data)
        if d.get('type') == 'review': return
        
        cart, total, info = d.get('cart', []), d.get('total', 0), d.get('info', {})
        promo, disc = info.get('promoCode', ''), info.get('discount', 0)
        d_txt, warn = "", ""

        client_name = info.get('name')
        if client_name: NAMES_CACHE[str(m.from_user.id)] = client_name
        
        if promo and disc > 0:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, process_promo_firebase, promo, m.from_user.id)
            
            if res == "OK":
                try: d_txt = f"\n🎁 <b>Промокод:</b> {promo} (-{int(round(total/(1-disc)) - total)} ₸)"
                except: d_txt = f"\n🎁 <b>Промокод:</b> {promo}"
            else:
                try: total = int(round(total/(1-disc)))
                except: pass
                reasons = {"USED": "Повтор", "LIMIT": "Лимит"}
                user_reasons = {"USED": "уже использован вами", "LIMIT": "исчерпан"}
                d_txt = f"\n❌ <b>Промокод:</b> {promo} ({reasons.get(res, 'Ошибка')})"
                warn = f"\n⚠️ <b>Промокод {promo} {user_reasons.get(res, 'не сработал')}!</b>\nСкидка отменена."

        is_del = (info.get('deliveryType') == 'Доставка')
        # Экранирование HTML в пользовательском вводе для безопасности
        safe_name = str(info.get('name', '')).replace('<', '&lt;').replace('>', '&gt;')
        safe_comment = str(info.get('comment', '')).replace('<', '&lt;').replace('>', '&gt;')
        
        txt = f"{'🚗' if is_del else '🏃'} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n👤 {safe_name} (<a href='tel:{info.get('phone')}'>{info.get('phone')}</a>)\n"
        txt += f"📍 {'Адрес: ' + info.get('address') if is_del else info.get('deliveryType')}\n💳 {info.get('paymentType')}\n"
        if info.get('paymentType') in ['Kaspi', 'Halyk']: txt += f"📱 <b>Счет:</b> <code>{info.get('paymentPhone')}</code>\n"
        if safe_comment: txt += f"💬 <i>{safe_comment}</i>\n"
        if "Ко времени" in str(safe_comment): txt += "⏰ <b>КО ВРЕМЕНИ!</b>\n"
        txt += f"➖➖➖➖➖➖➖➖➖➖\n"
        for i, item in enumerate(cart, 1):
            opts = [o for o in item.get('options', []) if o and o != "Без сахара"]
            q = item.get('qty', 1)
            txt += f"{i}. <b>{item.get('name')}</b> {'('+ ', '.join(opts) +')' if opts else ''}{f' <b>x {q} шт.</b>' if q > 1 else ''}\n"
        txt += f"{d_txt}\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_del: txt += "\n⚠️ <i>+ Доставка</i>"

        await bot.send_message(ADMIN_CHAT_ID, txt, reply_markup=get_decision_kb(m.chat.id), message_thread_id=TOPIC_ID_ORDERS)
        
        response_text = f"✅ Заказ отправлен!\nСумма: {total} ₸"
        if warn: response_text += f"\n{warn}"
        response_text += "\n\nОжидайте удаленного счета. Начнем готовить только после оплаты."
        await m.answer(response_text)
    except Exception as e: 
        logging.error(f"Order Error: {e}")
        await m.answer("⚠️ Произошла ошибка при обработке заказа. Пожалуйста, попробуйте еще раз.")

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
        await c.message.answer("Введите время (напр. '40' или '17:30'):")
        await state.update_data(msg_id=c.message.message_id, uid=uid)
        await state.set_state(OrderState.waiting_for_custom_time)
        await c.answer()
        return
    
    t_val = f"{act} мин"
    clean_text = c.message.text.split("\n\n✅")[0]
    await c.message.edit_text(f"{clean_text}\n\n✅ <b>ПРИНЯТ</b> ({t_val})", reply_markup=get_ready_kb(uid))
    msg = f"👨‍🍳Оплата принята! Готовность: <b>{t_val}</b>.\n📞Телефон для связи: +77006437303"
    if "🚗" in c.message.text: msg += "\n<i>(Время приготовления, без учета доставки)</i>"
    try: await bot.send_message(uid, msg)
    except: pass
    await c.answer()

# --- ВАЛИДАЦИЯ И ОБРАБОТКА КАСТОМНОГО ВРЕМЕНИ ---
@dp.message(OrderState.waiting_for_custom_time)
async def custom_time(m: types.Message, state: FSMContext):
    data = await state.get_data()
    order_msg_id = data.get('msg_id')
    user_id = data.get('uid')

    try: await m.delete()
    except: pass

    if not order_msg_id or not user_id:
        await m.answer("⚠️ Ошибка контекста. Повторите действие.")
        await state.clear()
        return

    input_text = m.text.strip()
    final_time = ""

    # Проверка формата
    if re.match(r'^\d+$', input_text):
        final_time = f"{input_text} мин"
    elif re.match(r'^([01]?[0-9]|2[0-3]):[0-5][0-9]$', input_text):
        final_time = input_text
    else:
        msg = await m.answer("⚠️ <b>Неверный формат!</b>\nВведите количество минут (например: <code>40</code>)\nИли точное время (например: <code>18:30</code>)")
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass
        return # Не сбрасываем состояние, ждем повторного ввода

    try:
        await bot.edit_message_reply_markup(
            chat_id=m.chat.id, 
            message_id=order_msg_id, 
            reply_markup=get_ready_kb(user_id)
        )
        
        await bot.send_message(
            chat_id=m.chat.id, 
            text=f"✅ Время установлено: <b>{final_time}</b>", 
            reply_to_message_id=order_msg_id, 
            message_thread_id=TOPIC_ID_ORDERS
        )
        
        await bot.send_message(
            chat_id=user_id, 
            text=f"👨‍🍳 Оплата принята! Готовность: <b>{final_time}</b>.\n📞Телефон для связи: +77006437303\n<i>(Если это доставка, время пути не учтено)</i>"
        )
    except Exception as e:
        logging.error(f"Custom time error: {e}")
        await m.answer(f"⚠️ Ошибка: {e}")
    
    finally:
        await state.clear()

@dp.callback_query(F.data.startswith("ord_ready_"))
async def ready(c: CallbackQuery):
    uid = c.data.split("_")[2]
    old = c.message.text
    clean = old.split("\n\n")[0] if "ПРИНЯТ" in old else old
    is_del = "🚗" in old or "Доставка" in old
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
    
    status_text = "🚗 <b>КУРЬЕР ВЫЕХАЛ</b>" if is_del else "🤝 <b>ВЫДАН / ЗАВЕРШЕН</b>"
    await c.message.edit_text(f"{clean}\n\n{status_text}")
    try:
        if is_del:
            await bot.send_message(
                uid,
                "🚗 Курьер выехал!\nКак только получите заказ, нажмите кнопку ниже, чтобы оценить качество:",
                reply_markup=get_received_kb()
            )
        else:
            await start_review_process(uid, state)
    except Exception as e: logging.error(f"Err review req: {e}")
    await c.answer()

@dp.callback_query(F.data == "ord_received")
async def delivery_received(c: CallbackQuery, state: FSMContext):
    await c.message.edit_reply_markup(reply_markup=None) 
    await c.message.answer("Приятного аппетита! 😋")
    await state.update_data(is_delivery=True)
    await start_review_process(c.from_user.id, state)
    await c.answer()

async def start_review_process(uid, state):
    await bot.send_message(uid, "Как вам наше <b>обслуживание</b>?", reply_markup=get_stars_kb("service"))

@dp.callback_query(F.data.startswith("rate_service_"))
async def rate_service(c: CallbackQuery, state: FSMContext):
    rating = int(c.data.split("_")[2])
    await state.update_data(service_rate=rating)
    await c.message.edit_text(
        f"Обслуживание: {rating} ⭐\n\nКак оцените <b>еду и напитки</b>?", 
        reply_markup=get_stars_kb("food")
    )
    await state.set_state(ReviewState.waiting_for_food_rate)
    await c.answer()

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
    await c.answer()

@dp.callback_query(F.data.startswith("tips_"), ReviewState.waiting_for_tips_decision)
async def tips_decision(c: CallbackQuery, state: FSMContext):
    choice = c.data.split("_")[1]
    if choice == "yes":
        await c.message.edit_text("Кому вы хотите оставить чаевые?", reply_markup=get_baristas_kb())
        await state.set_state(ReviewState.waiting_for_barista_choice)
    else:
        await state.update_data(tips="Нет")
        await c.message.edit_text("Напишите отзыв:", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)
    await c.answer()

@dp.callback_query(F.data.startswith("barista_"))
async def barista_choice(c: CallbackQuery, state: FSMContext):
    try:
        b_id = c.data.split("_")[1]
        
        if b_id == "cancel":
             await state.update_data(tips="Нет")
             await c.message.edit_text("Напишите отзыв:", reply_markup=get_skip_comment_kb())
             await state.set_state(ReviewState.waiting_for_comment)
             return

        if b_id in BARISTAS:
            barista = BARISTAS[b_id]
            await state.update_data(tips=f"Выбрано: {barista['name']}")
            await c.message.edit_text(
                f"💳 Kaspi\Halyk ({barista['name']}):\n<code>{barista['phone']}</code>\n\nСпасибо за поддержку! ❤️\n\nНапишите ваш отзыв:", 
                reply_markup=get_skip_comment_kb()
            )
        else:
            await c.message.edit_text("Напишите отзыв:", reply_markup=get_skip_comment_kb())
        
        await state.set_state(ReviewState.waiting_for_comment)
    except Exception as e:
        logging.error(f"Error in barista_choice: {e}")
    finally:
        await c.answer()

@dp.callback_query(F.data == "skip_comment", ReviewState.waiting_for_comment)
async def skip_comment(c: CallbackQuery, state: FSMContext):
    await finalize_review(c.message, state, "Без текста", c.from_user)
    await c.answer()

@dp.message(ReviewState.waiting_for_comment)
async def comment_text(m: types.Message, state: FSMContext):
    await finalize_review(m, state, m.text, m.from_user)

async def finalize_review(message, state, comment_text, user):
    data = await state.get_data()
    c_name = NAMES_CACHE.get(str(user.id), user.first_name)
    
    asyncio.create_task(save_review_background(user.id, c_name, data.get('service_rate'), data.get('food_rate'), data.get('tips', 'Нет'), comment_text))
    
    msg = f"⭐ <b>НОВЫЙ ОТЗЫВ</b>\n👤 {c_name}\n💁‍♂️ Сервис: {data.get('service_rate')} ⭐\n🍔 Еда: {data.get('food_rate')} ⭐\n💰 Чаевые: {data.get('tips')}\n💬 <i>{comment_text}</i>"
    await bot.send_message(ADMIN_CHAT_ID, msg, message_thread_id=TOPIC_ID_REVIEWS)
    
    avg = (int(data.get('service_rate', 5)) + int(data.get('food_rate', 5))) / 2
    resp = "Спасибо за отзыв! ❤️"
    if avg >= 5: resp = "Вау! 😍 Спасибо за высокую оценку!\nМы счастливы, что вам понравилось. Ждем вас снова за лучшим кофе! ☕️"
    elif avg < 4: resp = "Нам жаль, что мы вас расстроили. 😔\nМы обязательно исправимся."
    
    if isinstance(message, types.Message): await message.answer(resp)
    else: await message.edit_text(resp)
    await state.clear()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass


