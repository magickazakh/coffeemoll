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
TOKEN = os.getenv("BOT_TOKEN", "8444027240:AAFEiACM5x-OPmR9CFgk1zyrmU24PgovyCY") 
ADMIN_CHAT_ID = -1003472248648
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"

# --- НАСТРОЙКИ ТЕМ (TOPICS) ---
TOPIC_ID_ORDERS = 20
TOPIC_ID_REVIEWS = 3
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
        cred_path = "firebase_creds.json"
        if os.path.exists("/etc/secrets/firebase_creds.json"):
            cred_path = "/etc/secrets/firebase_creds.json"
            
        if os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            logging.info("🔥 Firebase Connected!")
        else:
            logging.error("❌ Firebase credentials file not found!")
            return None
    
    _db_client = firestore.client()
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

# --- ЛОЯЛЬНОСТЬ ---
def get_user_loyalty(user_id):
    if not db: return 0
    try:
        doc = db.collection('users').document(str(user_id)).get()
        return doc.to_dict().get('loyalty_points', 0) if doc.exists else 0
    except: return 0

def update_loyalty_points(user_id, cups_count, target):
    """
    Возвращает: (is_free_cup_awarded: bool, new_balance: int)
    Логика: 1 бесплатная чашка, если (баланс + покупка) >= цели.
    """
    if not db: return False, 0
    try:
        user_ref = db.collection('users').document(str(user_id))
        doc = user_ref.get()
        current_points = doc.to_dict().get('loyalty_points', 0) if doc.exists else 0
        
        total_potential = current_points + cups_count
        is_free = False
        new_balance = total_potential
        
        # Если накопили достаточно
        if target > 0 and total_potential >= target:
            is_free = True
            # Списываем ровно 'target' баллов за 1 бесплатную чашку
            new_balance = total_potential - target 
        
        user_ref.set({'loyalty_points': new_balance}, merge=True)
        return is_free, new_balance
    except Exception as e:
        logging.error(f"Loyalty Error: {e}")
        return False, 0

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
    if request.method == 'OPTIONS': return web.Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type"})
    try:
        d = await request.json()
        loop = asyncio.get_running_loop()
        s, disc = await loop.run_in_executor(None, check_promo_firebase, d.get('code',''), d.get('userId'))
        return web.json_response({'status': s, 'discount': disc}, headers={"Access-Control-Allow-Origin": "*"})
    except: return web.json_response({'status': 'ERROR'}, headers={"Access-Control-Allow-Origin": "*"})

async def api_get_user_info(request):
    if request.method == 'OPTIONS': return web.Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type"})
    try:
        d = await request.json()
        loop = asyncio.get_running_loop()
        p = await loop.run_in_executor(None, get_user_loyalty, d.get('userId'))
        return web.json_response({'points': p}, headers={"Access-Control-Allow-Origin": "*"})
    except: return web.json_response({'points': 0}, headers={"Access-Control-Allow-Origin": "*"})

async def health_check(r): return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    app.router.add_post("/api/check_promo", api_check_promo)
    app.router.add_post("/api/get_user_info", api_get_user_info)
    # Add OPTIONS for CORS
    app.router.add_options("/api/check_promo", api_check_promo)
    app.router.add_options("/api/get_user_info", api_get_user_info)
    
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()

async def main():
    asyncio.create_task(cache_updater_task())
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Bot started...")
    await dp.start_polling(bot)

# --- HANDLERS ---
@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    unique_url = f"{WEB_APP_URL}?v={int(time.time())}"
    await m.answer("Добро пожаловать в CoffeeMoll! 🥐", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=unique_url))]], resize_keyboard=True))

@dp.message(F.web_app_data)
async def web_app_data_handler(m: types.Message):
    try:
        d = json.loads(m.web_app_data.data)
        if d.get('type') == 'review': return
        cart, total, info = d.get('cart', []), d.get('total', 0), d.get('info', {})
        promo, disc = info.get('promoCode', ''), info.get('discount', 0)
        loyalty_target = int(info.get('loyaltyTarget', 0))
        
        if info.get('name'): NAMES_CACHE[str(m.from_user.id)] = info.get('name')

        d_txt, warn = "", ""
        if promo and disc > 0:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, process_promo_firebase, promo, m.from_user.id)
            if res == "OK": d_txt = f"\n🎁 <b>Промокод:</b> {promo}"
            else: 
                try: total = int(round(total/(1-disc)))
                except: pass
                warn = f"\n⚠️ <b>Промокод {promo} не сработал!</b>"

        # --- ЛОЯЛЬНОСТЬ ---
        loyalty_msg = ""
        coffee_prices = []
        for item in cart:
            # Ищем кофе. Если category "Кофе" или в названии есть латте/капучино
            cat = item.get('cat', '').upper()
            name = item.get('name', '').upper()
            if 'КОФЕ' in cat or 'COFFEE' in cat or 'LATTE' in name or 'CAPPUCCINO' in name:
                for _ in range(item.get('qty', 1)):
                    coffee_prices.append(item.get('price', 0))
        
        coffee_count = len(coffee_prices)
        free_cup_applied = False
        
        if coffee_count > 0 and loyalty_target > 0:
            loop = asyncio.get_running_loop()
            is_free, new_bal = await loop.run_in_executor(None, update_loyalty_points, m.from_user.id, coffee_count, loyalty_target)
            
            if is_free:
                # Ищем самую дешевую чашку
                coffee_prices.sort() # [1200, 1500, 1800] -> min is 1200
                discount_amount = coffee_prices[0] # Бесплатна только 1 (самая дешевая)
                
                # Уменьшаем итог, но не ниже 0
                total = max(0, total - discount_amount)
                free_cup_applied = True
                
                loyalty_msg = f"\n🎁 <b>БОНУС: 1 чашка бесплатно!</b> (-{discount_amount} ₸)\n⭐️ Баланс: {new_bal}/{loyalty_target}"
            else:
                loyalty_msg = f"\n⭐️ Баланс: {new_bal}/{loyalty_target} (+{coffee_count})"

        # --- ФОРМИРОВАНИЕ ЧЕКА ---
        txt = f"{'🚗' if info.get('deliveryType') == 'Доставка' else '🏃'} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n👤 {info.get('name')} (<a href='tel:{info.get('phone')}'>{info.get('phone')}</a>)\n"
        txt += f"📍 {'Адрес: ' + info.get('address') if info.get('deliveryType') == 'Доставка' else info.get('deliveryType')}\n💳 {info.get('paymentType')}\n"
        if info.get('paymentType') in ['Kaspi', 'Halyk']: txt += f"📱 <b>Счет:</b> <code>{info.get('paymentPhone')}</code>\n"
        if info.get('comment'): txt += f"💬 <i>{info.get('comment')}</i>\n"
        if "Ко времени" in str(info.get('comment')): txt += "⏰ <b>КО ВРЕМЕНИ!</b>\n"
        
        txt += f"➖➖➖➖➖➖➖➖➖➖\n"
        for i, item in enumerate(cart, 1):
            opts = [o for o in item.get('options', []) if o and o != "Без сахара"]
            q = item.get('qty', 1)
            txt += f"{i}. <b>{item.get('name')}</b> {'('+ ', '.join(opts) +')' if opts else ''}{f' <b>x {q}</b>' if q > 1 else ''}\n"
        
        txt += d_txt
        txt += f"\n💰 <b>ИТОГО: {total} ₸</b>"
        if info.get('deliveryType') == 'Доставка': txt += "\n⚠️ <i>+ Доставка</i>"
        txt += loyalty_msg

        await bot.send_message(ADMIN_CHAT_ID, txt, reply_markup=get_decision_kb(m.chat.id), message_thread_id=TOPIC_ID_ORDERS)
        
        resp = f"✅ Заказ отправлен!\nСумма к оплате: {total} ₸"
        if warn: resp += warn
        if free_cup_applied: resp += "\n\n🎁 <b>Сработала лояльность!</b>\nОдна чашка кофе в этом заказе для вас бесплатно."
        resp += "\n\nЖдите подтверждения времени."
        await m.answer(resp)
        
    except Exception as e: logging.error(f"Order Error: {e}")

# --- КЛАВИАТУРЫ ---
def get_decision_kb(uid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Принять", callback_data=f"dec_accept_{uid}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"dec_reject_{uid}")]])
def get_time_kb(uid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="5 мин", callback_data=f"time_5_{uid}"), InlineKeyboardButton(text="10 мин", callback_data=f"time_10_{uid}"), InlineKeyboardButton(text="15 мин", callback_data=f"time_15_{uid}")], [InlineKeyboardButton(text="20 мин", callback_data=f"time_20_{uid}"), InlineKeyboardButton(text="30 мин", callback_data=f"time_30_{uid}"), InlineKeyboardButton(text="✍️ Своё", callback_data=f"time_custom_{uid}")], [InlineKeyboardButton(text="🔙 Назад", callback_data=f"time_back_{uid}")]])
def get_ready_kb(uid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏁 Готов", callback_data=f"ord_ready_{uid}")]])
def get_given_kb(uid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Выдан / Передан курьеру", callback_data=f"ord_given_{uid}")]])
def get_received_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 Заказ получен", callback_data="ord_received")]])
def get_stars_kb(c): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{i} ⭐", callback_data=f"rate_{c}_{i}") for i in range(1, 6)]])
def get_yes_no_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Да 👍", callback_data="tips_yes"), InlineKeyboardButton(text="Нет 🙅‍♂️", callback_data="tips_no")]])
def get_baristas_kb(): 
    b = [[InlineKeyboardButton(text=d['name'], callback_data=f"barista_{k}")] for k, d in BARISTAS.items()]
    b.append([InlineKeyboardButton(text="Отмена", callback_data="barista_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=b)
def get_skip_comment_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ Пропустить", callback_data="skip_comment")]])

# --- CALLBACKS ---
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
    if act == "back": await c.message.edit_reply_markup(reply_markup=get_decision_kb(uid)); return
    if act == "custom":
        await c.message.answer("Введите время (напр. '40' или '17:30'):")
        await state.update_data(msg_id=c.message.message_id, uid=uid)
        await state.set_state(OrderState.waiting_for_custom_time)
        await c.answer(); return
    t_val = f"{act} мин"
    clean = c.message.text.split("\n\n✅")[0]
    await c.message.edit_text(f"{clean}\n\n✅ <b>ПРИНЯТ</b> ({t_val})", reply_markup=get_ready_kb(uid))
    try: await bot.send_message(uid, f"👨‍🍳 Оплата принята! Готовность: <b>{t_val}</b>.\n📞Телефон: +77006437303")
    except: pass
    await c.answer()

@dp.message(OrderState.waiting_for_custom_time)
async def custom_time(m: types.Message, state: FSMContext):
    d = await state.get_data()
    try: await m.delete()
    except: pass
    final_time = f"{m.text} мин" if re.match(r'^\d+$', m.text) else m.text
    try:
        await bot.edit_message_reply_markup(m.chat.id, d['msg_id'], reply_markup=get_ready_kb(d['uid']))
        await bot.send_message(m.chat.id, f"✅ Время: <b>{final_time}</b>", reply_to_message_id=d['msg_id'], message_thread_id=TOPIC_ID_ORDERS)
        await bot.send_message(d['uid'], f"👨‍🍳 Оплата принята! Готовность: <b>{final_time}</b>.")
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("ord_ready_"))
async def ready(c: CallbackQuery):
    uid = c.data.split("_")[2]
    is_del = "🚗" in c.message.text
    await c.message.edit_text(f"{c.message.text.split('🏁')[0]}\n\n🏁 <b>ГОТОВ</b>", reply_markup=get_given_kb(uid))
    try: await bot.send_message(uid, "📦 <b>Заказ готов!</b>\nОжидаем курьера." if is_del else "🎉 <b>Ваш заказ готов!</b>\nЖдем вас на выдаче ☕️")
    except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("ord_given_"))
async def given(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split("_")[2])
    is_del = "🚗" in c.message.text
    await c.message.edit_text(f"{c.message.text.split('🏁')[0]}\n\n{'🚗 <b>КУРЬЕР ВЫЕХАЛ</b>' if is_del else '🤝 <b>ВЫДАН / ЗАВЕРШЕН</b>'}")
    try:
        if is_del: await bot.send_message(uid, "🚗 Курьер выехал!\nКогда получите заказ, нажмите кнопку:", reply_markup=get_received_kb())
        else: await start_review_process(uid, state)
    except: pass
    await c.answer()

@dp.callback_query(F.data == "ord_received")
async def received(c: CallbackQuery, state: FSMContext):
    await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer("Приятного аппетита! 😋")
    await start_review_process(c.from_user.id, state)
    await c.answer()

@dp.callback_query(F.data.startswith("rate_service_"))
async def rate_s(c: CallbackQuery, state: FSMContext):
    await state.update_data(service_rate=int(c.data.split("_")[2]))
    await c.message.edit_text("Оцените <b>еду и напитки</b>:", reply_markup=get_stars_kb("food"))
    await state.set_state(ReviewState.waiting_for_food_rate)
    await c.answer()

@dp.callback_query(F.data.startswith("rate_food_"), ReviewState.waiting_for_food_rate)
async def rate_f(c: CallbackQuery, state: FSMContext):
    await state.update_data(food_rate=int(c.data.split("_")[2]))
    d = await state.get_data()
    if d.get('service_rate', 0) >= 4:
        await c.message.edit_text("Оставить <b>чаевые</b>?", reply_markup=get_yes_no_kb())
        await state.set_state(ReviewState.waiting_for_tips_decision)
    else:
        await state.update_data(tips="Нет")
        await c.message.edit_text("Ваш отзыв:", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)
    await c.answer()

@dp.callback_query(F.data.startswith("tips_"), ReviewState.waiting_for_tips_decision)
async def tips_d(c: CallbackQuery, state: FSMContext):
    if c.data.split("_")[1] == "yes":
        await c.message.edit_text("Кому?", reply_markup=get_baristas_kb())
        await state.set_state(ReviewState.waiting_for_barista_choice)
    else:
        await state.update_data(tips="Нет")
        await c.message.edit_text("Ваш отзыв:", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)
    await c.answer()

@dp.callback_query(F.data.startswith("barista_"), ReviewState.waiting_for_barista_choice)
async def bar_c(c: CallbackQuery, state: FSMContext):
    bid = c.data.split("_")[1]
    if bid == "cancel":
        await state.update_data(tips="Нет")
        await c.message.edit_text("Ваш отзыв:", reply_markup=get_skip_comment_kb())
    elif bid in BARISTAS:
        b = BARISTAS[bid]
        await state.update_data(tips=f"Выбрано: {b['name']}")
        await c.message.edit_text(f"💳 Kaspi ({b['name']}):\n<code>{b['phone']}</code>\n\nВаш отзыв:", reply_markup=get_skip_comment_kb())
    await state.set_state(ReviewState.waiting_for_comment)
    await c.answer()

@dp.callback_query(F.data == "skip_comment", ReviewState.waiting_for_comment)
async def skip_c(c: CallbackQuery, state: FSMContext):
    await finalize_review(c.message, state, "Без текста", c.from_user)
    await c.answer()

@dp.message(ReviewState.waiting_for_comment)
async def comment_t(m: types.Message, state: FSMContext):
    await finalize_review(m, state, m.text, m.from_user)

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
