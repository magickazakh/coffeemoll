import asyncio
import json
import logging
import sys
import os
import re 
import time
import html
from datetime import datetime, timedelta, timezone
from aiohttp import web

# --- SUPABASE IMPORTS ---
from supabase import create_client, Client
# ------------------------

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN") 
if not TOKEN:
    logging.critical("❌ BOT_TOKEN is not set!")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") # Important: Use Service Role Key for Admin actions

if not SUPABASE_URL or not SUPABASE_KEY:
    logging.critical("❌ SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY is not set!")

ADMIN_CHAT_ID = -1003472248648
WEB_APP_URL = "https://magickazakh.github.io/coffeemoll/"

TOPIC_ID_ORDERS = 20
TOPIC_ID_REVIEWS = 3
TOPIC_ID_SUPPORT = 96

KASPI_NUMBER = "+7 747 240 20 02" 

BARISTAS = {
    "1": {"name": "Анара", "phone": "+7 747 240 20 02 (только Kaspi)"},
    "2": {"name": "Карина", "phone": "+7 776 962 28 14 (Kaspi/Halyk)"}, 
    "3": {"name": "Павел", "phone": "+7 771 904 44 55 (Kaspi/Halyk/Forte/Freedom)"}
}

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class OrderState(StatesGroup):
    waiting_for_custom_time = State()
    waiting_for_broadcast = State()
    waiting_for_rejection_reason = State()

class ReviewState(StatesGroup):
    waiting_for_service_rate = State()
    waiting_for_food_rate = State()
    waiting_for_tips_decision = State()
    waiting_for_barista_choice = State()
    waiting_for_comment = State()

class SupportState(StatesGroup):
    waiting_for_admin_reply = State()

# --- ГЛОБАЛЬНЫЙ КЕШ ---
PROMO_CACHE = {}
NAMES_CACHE = {}

# --- SUPABASE SETUP ---
_supabase: Client = None

def init_supabase():
    global _supabase
    if _supabase:
        return _supabase
    
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            logging.info("🔥 Supabase Connected!")
        except Exception as e:
            logging.error(f"❌ Supabase Init Error: {e}")
            return None
    else:
        logging.warning("⚠️ Supabase credentials not found!")
        return None
    
    return _supabase

db = init_supabase()

def clean_id(raw_id):
    if not raw_id: return ""
    return re.sub(r'\D', '', str(raw_id))

# --- ФОНОВАЯ ЗАДАЧА: СИНХРОНИЗАЦИЯ КЕША ---
async def cache_updater_task():
    global PROMO_CACHE
    while True:
        try:
            if db:
                # Supabase Select
                response = db.table('promocodes').select('*').execute()
                data = response.data
                
                new_cache = {}
                for item in data:
                    code = item.get('code', '').strip().upper()
                    try:
                        limit = int(item.get('limit', 0))
                        discount = float(item.get('discount', 0.0))
                    except ValueError:
                        limit = 0
                        discount = 0.0
                    new_cache[code] = {'discount': discount, 'limit': limit}
                
                PROMO_CACHE = new_cache
        except Exception as e:
            logging.error(f"Cache Update Error: {e}")
        
        await asyncio.sleep(60)

# --- ЛОГИКА ПРОМОКОДОВ (RPC) ---

def check_promo_firebase(code, user_id):
    # This acts as a "Check" only, not incrementing
    if not db: return "ERROR", 0
    code = code.strip().upper()
    uid = clean_id(user_id)
    
    logging.info(f"Checking promo (legacy name): {code} for user {uid}")
    
    try:
        # 1. Fetch promo
        p_res = db.table('promocodes').select('*').eq('code', code).execute()
        if not p_res.data: return "NOT_FOUND", 0
        
        promo = p_res.data[0]
        limit = promo.get('limit', 0)
        discount = promo.get('discount', 0)
        
        if limit <= 0: return "LIMIT", 0

        # 2. Check history (simple select)
        # Check by generated ID or composite lookup
        # History ID structure in RPC was: uid_code
        h_id = f"{uid}_{code}"
        h_res = db.table('promo_history').select('*').eq('id', h_id).execute()
        
        if h_res.data: return "USED", 0
        
        # Second check: loose query
        h_res2 = db.table('promo_history').select('*').eq('user_id', uid).eq('code', code).execute()
        if h_res2.data: return "USED", 0

        return "OK", discount
            
    except Exception as e:
        logging.error(f"Check Error: {e}")
        return "ERROR", 0

def process_promo_firebase(code, user_id):
    # This applies the promo using RPC 'use_promo'
    if not db: return "ERROR"
    code = code.strip().upper()
    uid = clean_id(user_id)
    try:
        # Call Postgres Function
        params = {'promo_code': code, 'user_id_param': uid}
        response = db.rpc('use_promo', params).execute()
        
        # response.data is whatever the function returned (json)
        result_json = response.data
        if not result_json: return "ERROR"
        
        status = result_json.get('status', 'ERROR')
        
        if status == "OK" and code in PROMO_CACHE:
             PROMO_CACHE[code]['limit'] -= 1
        return status
    except Exception as e:
        logging.error(f"Transaction (RPC) Error: {e}")
        return "ERROR"

def cancel_promo_firebase(code, user_id):
    # This reverts the promo using RPC 'cancel_promo'
    if not db: return
    code = code.strip().upper()
    uid = clean_id(user_id)
    try:
        params = {'promo_code': code, 'user_id_param': uid}
        response = db.rpc('cancel_promo', params).execute()
        
        result_json = response.data
        if result_json and result_json.get('status') == 'OK' and code in PROMO_CACHE:
             PROMO_CACHE[code]['limit'] += 1
             
        logging.info(f"Reverted promo {code} for {uid}: {result_json}")
    except Exception as e:
        logging.error(f"Revert Error: {e}")

# --- СОХРАНЕНИЕ ДАННЫХ ---

async def save_order_background(user_id, order_data, total_price):
    if not db: return
    def _save():
        try:
            # Upsert User
            user_info = order_data.get('info', {})
            user_payload = {
                'id': str(user_id),
                'name': user_info.get('name', 'Unknown'),
                'phone': user_info.get('phone', ''),
                'last_order': datetime.now(timezone.utc).isoformat()
            }
            db.table('users').upsert(user_payload).execute()

            # Insert Order
            # For 'id' we let Supabase generate it (uuid) OR we generate one. 
            # Original code let Firebase auto-generate ID.
            # Supabase 'id' in schema is TEXT PRIMARY KEY. Better use UUID or generate one.
            # We can use order_data checksum or timestamp combination, or just let DB handle if ID is defaulting to uuid_generate_v4().
            # Assume schema.sql `id` is TEXT. Let's create a random ID here to be safe and consistent with previous app logic if it needed ID.
            # Actually, standard is to let DB handle it if configured, but here we can just pass nothing if column is SERIAL or UUID DEFAULT.
            # BUT schema said: id TEXT PRIMARY KEY. So we MUST provide it or set default in schema to uuid.
            # Let's generate a TS-based ID like Firebase often does roughly.
            import uuid
            order_id = str(uuid.uuid4())
            
            db.table('orders').insert({
                'id': order_id,
                'user_id': str(user_id),
                'cart': order_data.get('cart', []),
                'info': order_data.get('info', {}),
                'total_price': total_price,
                'status': 'new',
                'created_at': datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e: logging.error(f"Save Order Error: {e}")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save)

async def save_review_background(user_id, name, service_rate, food_rate, tips, comment):
    if not db: return
    def _save():
        try:
            import uuid
            rev_id = str(uuid.uuid4())
            db.table('reviews').insert({
                'id': rev_id,
                'user_id': str(user_id),
                'name': name,
                'service_rate': service_rate,
                'food_rate': food_rate,
                'tips': tips,
                'comment': comment,
                'created_at': datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception as e: logging.error(f"Save Review Error: {e}")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _save)

# --- API ---

async def api_check_promo(request):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "86400"
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
        logging.error(f"API Error: {e}")
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
    print("🤖 Bot started ...")
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
def get_reply_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Ответить", callback_data=f"reply_{user_id}")]
    ])
def get_rejection_kb(uid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌭 Хот-догов нет", callback_data=f"reason_hd_{uid}")],
        [InlineKeyboardButton(text="🥐 Круассанов нет", callback_data=f"reason_cr_{uid}")],
        [InlineKeyboardButton(text="🔒 Мы закрыты", callback_data=f"reason_closed_{uid}")],
        [InlineKeyboardButton(text="✍️ Своя причина", callback_data=f"reason_custom_{uid}")]
    ])


# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    unique_url = f"{WEB_APP_URL}?v={int(time.time())}&uid={m.from_user.id}"

    if m.chat.id == ADMIN_CHAT_ID:
        await m.answer(f"Привет, Админ! 👋\nКоманды:\n/stats - Статистика\n/broadcast - Рассылка")

    await m.answer(
        "Добро пожаловать в CoffeeMoll! 🥐", 
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=unique_url))]], 
            resize_keyboard=True
        )
    )

@dp.message(Command("stats"))
async def cmd_stats(m: types.Message):
    if m.chat.id != ADMIN_CHAT_ID: return
    if not db:
        await m.answer("❌ База данных не подключена.")
        return
    await m.answer("📊 Считаем статистику...")
    try:
        today_str = datetime.now().strftime("%Y-%m-%d")
        # In Supabase we could do count but let's fetch for sum
        # Ideally using aggregate query, but 'select' is easier to migrate 1-to-1
        # Fetching all orders is bad for scale, but OK for small shops.
        # Optimized: filter by created_at > today
        
        # Get all time count
        count_res = db.table('orders').select('*', count='exact', head=True).execute()
        total_count = count_res.count
        
        # Get all sum (expensive loop if not doing SQL SUM)
        # Let's just limit to today for detail stats to save bandwidth if many orders
        # Or fetch all for total sum?
        # Let's do a simple full fetch as in original code (not great but guaranteed same logic)
        
        docs = db.table('orders').select('total_price, created_at').execute().data
        
        total_sum = 0
        today_count = 0
        today_sum = 0
        
        current_date_prefix = datetime.now().strftime("%Y-%m-%d")

        for d in docs:
            price = d.get('total_price', 0)
            created_at = d.get('created_at', '')
            total_sum += price
            
            if created_at.startswith(current_date_prefix):
                today_count += 1
                today_sum += price
                
        msg = f"📅 <b>Статистика на {today_str}</b>\n\n🔹 <b>За сегодня:</b>\nЗаказов: {today_count}\nВыручка: {today_sum} ₸\n\n🔸 <b>За все время:</b>\nЗаказов: {total_count}\nОборот: {total_sum} ₸"
        await m.answer(msg)
    except Exception as e:
        await m.answer(f"❌ Ошибка: {e}")

@dp.message(Command("broadcast"))
async def cmd_broadcast(m: types.Message, state: FSMContext):
    if m.chat.id != ADMIN_CHAT_ID: return
    await m.answer("📢 Введите текст сообщения для рассылки.")
    await state.set_state(OrderState.waiting_for_broadcast)

@dp.message(OrderState.waiting_for_broadcast)
async def process_broadcast(m: types.Message, state: FSMContext):
    if not db: return
    text = m.text
    await m.answer("⏳ Рассылка...")
    count = 0
    try:
        # Fetch users
        # Pagination might be needed if > 1000 users. Supabase defaults to 1000 limit.
        response = db.table('users').select('id').execute()
        users = response.data
        
        for u in users:
            uid = u.get('id')
            if uid:
                try:
                    await bot.send_message(uid, f"🔔 <b>НОВОСТИ COFFEEMOLL</b>\n\n{text}")
                    count += 1
                    await asyncio.sleep(0.05)
                except: pass
        await m.answer(f"✅ Рассылка завершена! Доставлено: {count}.")
    except Exception as e: await m.answer(f"❌ Ошибка: {e}")
    await state.clear()

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
        
        if db: asyncio.create_task(save_order_background(m.from_user.id, d, total))
        
        if promo and disc > 0:
            loop = asyncio.get_running_loop()
            res = await loop.run_in_executor(None, process_promo_firebase, promo, m.from_user.id)
            if res == "OK":
                try:
                    if 1 - disc > 0:
                        saving = int(round(total / (1 - disc)) - total)
                        d_txt = f"\n🎁 <b>Промокод:</b> {promo} (-{saving} ₸)"
                    else:
                        d_txt = f"\n🎁 <b>Промокод:</b> {promo}"
                except:
                    d_txt = f"\n🎁 <b>Промокод:</b> {promo}"
            else:
                reasons = {"USED": "Повтор", "LIMIT": "Лимит"}
                user_reasons = {"USED": "уже использован вами", "LIMIT": "исчерпан"}
                d_txt = f"\n❌ <b>Промокод:</b> {promo} ({reasons.get(res, 'Ошибка')})"
                warn = f"\n⚠️ <b>Промокод {promo} {user_reasons.get(res, 'не сработал')}!</b>"

        is_del = (info.get('deliveryType') == 'Доставка')
        safe_name = str(info.get('name', '')).replace('<', '&lt;').replace('>', '&gt;')
        safe_comment = str(info.get('comment', '')).replace('<', '&lt;').replace('>', '&gt;')
        # Получаем время заказа из нового поля (с защитой от старой версии фронтенда)
        order_time = info.get('orderTime', '⚡ Как можно скорее')
        txt = f"{'🚗' if is_del else '🏃'} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n👤 {safe_name} (<a href='tel:{info.get('phone')}'>{info.get('phone')}</a>)\n"
        txt += f"📍 {'Адрес: ' + info.get('address') if is_del else info.get('deliveryType')}\n"
        # Выводим время отдельной строкой
        txt += f"⏳ <b>Время:</b> {order_time}\n"
        txt += f"💳 {info.get('paymentType')}\n"
        if info.get('paymentType') in ['Kaspi', 'Halyk']: txt += f"📱 <b>Счет:</b> <code>{info.get('paymentPhone')}</code>\n"
        # Выводим комментарий, только если пользователь его написал
        if safe_comment: txt += f"💬 <i>{safe_comment}</i>\n"
        # Проверка на заказ ко времени для жирного выделения
        if "⏰" in order_time: txt += "⏰ <b>КО ВРЕМЕНИ!</b>\n"
        txt += f"➖➖➖➖➖➖➖➖➖➖\n"
        for i, item in enumerate(cart, 1):
            opts = [o for o in item.get('options', []) if o and o != "Без сахара"]
            q = item.get('qty', 1)
            txt += f"{i}. <b>{item.get('name')}</b> {'('+ ', '.join(opts) +')' if opts else ''}{f' <b>x {q}</b>' if q > 1 else ''}\n"
        txt += f"{d_txt}\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_del: txt += "\n⚠️ <i>+ Доставка</i>"

        await bot.send_message(ADMIN_CHAT_ID, txt, reply_markup=get_decision_kb(m.chat.id), message_thread_id=TOPIC_ID_ORDERS)
        
        response_text = f"✅ Заказ отправлен!\nСумма: {total} ₸"
        if warn: response_text += f"\n{warn}"
        response_text += "\n\nОжидайте удаленного счета."
        await m.answer(response_text)
    except Exception as e: 
        logging.error(f"Order Error: {e}")
        await m.answer("⚠️ Ошибка обработки заказа.")

@dp.callback_query(F.data.startswith("dec_"))
async def decision(c: CallbackQuery, state: FSMContext):
    act, uid = c.data.split("_")[1], c.data.split("_")[2]
    
    if act == "accept": 
        await c.message.edit_reply_markup(reply_markup=get_time_kb(uid))
    elif act == "reject":
        text = c.message.text or c.message.caption or ""
        prompt_msg = await c.message.answer(
            "✍️ <b>Выберите причину отказа:</b>", 
            reply_markup=get_rejection_kb(uid)
        )
        await state.update_data(
            reject_uid=uid, 
            reject_msg_id=c.message.message_id,
            reject_text=text,
            prompt_msg_id=prompt_msg.message_id
        )
    await c.answer()

@dp.callback_query(F.data.startswith("reason_"))
async def rejection_reason_callback(c: CallbackQuery, state: FSMContext):
    parts = c.data.split("_")
    r_type = parts[1]
    uid = parts[2]
    
    if r_type == "custom":
        await c.message.edit_text("✍️ <b>Напишите причину отказа:</b>", reply_markup=None)
        await state.set_state(OrderState.waiting_for_rejection_reason)
        await c.answer()
        return

    reasons_map = {
        "hd": "Хот-догов временно нет в наличии",
        "cr": "Круассанов временно нет в наличии",
        "closed": "К сожалению, мы закрыты"
    }
    reason = reasons_map.get(r_type, "Заказ отклонен")
    
    await execute_rejection(c.message, state, reason, is_preset=True)
    await c.answer()

@dp.message(OrderState.waiting_for_rejection_reason)
async def process_rejection_reason(m: types.Message, state: FSMContext):
    reason = m.text
    await execute_rejection(m, state, reason, is_preset=False)

async def execute_rejection(message_obj, state, reason, is_preset):
    data = await state.get_data()
    uid = data.get('reject_uid')
    msg_id = data.get('reject_msg_id')
    original_text = data.get('reject_text')
    prompt_msg_id = data.get('prompt_msg_id')
    
    try:
        match = re.search(r"Промокод:\s*([A-Za-z0-9]+)", original_text)
        if match:
            code = match.group(1)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, cancel_promo_firebase, code, uid)
    except Exception as e:
        logging.error(f"Auto-revert error: {e}")

    safe_original_text = html.escape(original_text)

    try:
        await bot.edit_message_text(
            chat_id=ADMIN_CHAT_ID, 
            message_id=msg_id,
            text=f"{safe_original_text}\n\n❌ <b>ОТКЛОНЕН ({reason})</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Edit rejection message error: {e}")

    try: 
        await bot.send_message(uid, f"❌ <b>Ваш заказ был отклонен.</b>\n\nПричина: {reason}")
    except: pass
    
    chat_id = message_obj.chat.id
    if prompt_msg_id:
        try: await bot.delete_message(chat_id=chat_id, message_id=prompt_msg_id)
        except: pass

    if not is_preset:
        try: await message_obj.delete()
        except: pass

    conf_msg = await bot.send_message(chat_id, "✅ Отказ отправлен.")
    await state.clear()
    
    await asyncio.sleep(3)
    try: await conf_msg.delete()
    except: pass

@dp.callback_query(F.data.startswith("time_"))
async def set_time(c: CallbackQuery, state: FSMContext):
    act, uid = c.data.split("_")[1], c.data.split("_")[2]
    if act == "back": 
        await c.message.edit_reply_markup(reply_markup=get_decision_kb(uid))
        return
    if act == "custom":
        await c.message.answer("Введите время:")
        await state.update_data(msg_id=c.message.message_id, uid=uid)
        await state.set_state(OrderState.waiting_for_custom_time)
        await c.answer()
        return
    
    t_val = f"{act} мин"
    clean_text = c.message.text.split("\n\n✅")[0]
    
    await c.message.edit_text(
        f"{clean_text}\n\n✅ <b>ПРИНЯТ</b> ({t_val})", 
        reply_markup=get_ready_kb(uid)
    )
    
    msg = f"👨‍🍳Оплата принята! Готовность: <b>{t_val}</b>.\n📞Телефон для связи: +77006437303"
    if "🚗" in clean_text or "Доставка" in clean_text: msg += "\n<i>(Время приготовления, без учета доставки)</i>"
    try: await bot.send_message(uid, msg)
    except: pass
    await c.answer()

@dp.message(OrderState.waiting_for_custom_time)
async def custom_time(m: types.Message, state: FSMContext):
    data = await state.get_data()
    try: await m.delete()
    except: pass
    
    final_time = m.text.strip()
    if re.match(r'^\d+$', final_time): final_time += " мин"
    
    try:
        await bot.edit_message_reply_markup(chat_id=m.chat.id, message_id=data['msg_id'], reply_markup=get_ready_kb(data['uid']))
        await bot.send_message(chat_id=m.chat.id, text=f"✅ Время: <b>{final_time}</b>", reply_to_message_id=data['msg_id'], message_thread_id=TOPIC_ID_ORDERS)
        
        msg = f"👨‍🍳 Оплата принята! Готовность: <b>{final_time}</b>."
        await bot.send_message(chat_id=data['uid'], text=msg)
    except: pass
    await state.clear()

@dp.callback_query(F.data.startswith("ord_ready_"))
async def ready(c: CallbackQuery):
    uid = c.data.split("_")[2]
    old = c.message.text
    clean = old.split("\n\n")[0] if "ПРИНЯТ" in old else old
    await c.message.edit_text(f"{clean}\n\n🏁 <b>ГОТОВ</b>", reply_markup=get_given_kb(uid))
    
    client_msg = "🎉 <b>Ваш заказ готов!</b>\nЖдем вас на выдаче ☕️"
    if "🚗" in old or "Доставка" in old:
        client_msg = "📦 <b>Заказ готов и упакован!</b>\nОжидаем курьера."
        
    try: await bot.send_message(uid, client_msg)
    except: pass
    await c.answer()

@dp.callback_query(F.data.startswith("ord_given_"))
async def given(c: CallbackQuery, state: FSMContext):
    uid = int(c.data.split("_")[2])
    old = c.message.text
    clean = old.split("\n\n")[0]
    is_del = "🚗" in clean or "Доставка" in clean
    
    status_text = "🚗 <b>КУРЬЕР ВЫЕХАЛ</b>" if is_del else "🤝 <b>ВЫДАН</b>"
    await c.message.edit_text(f"{clean}\n\n{status_text}")
    try:
        if is_del:
            await bot.send_message(uid, "🚗 Курьер выехал!\nКак только получите заказ, нажмите кнопку:", reply_markup=get_received_kb())
        else:
            await start_review_process(uid, state)
    except: pass
    await c.answer()

@dp.callback_query(F.data == "ord_received")
async def delivery_received(c: CallbackQuery, state: FSMContext):
    await c.message.edit_reply_markup(reply_markup=None) 
    await c.message.answer("Приятного аппетита! 😋")
    await start_review_process(c.from_user.id, state)
    await c.answer()

async def start_review_process(uid, state):
    await bot.send_message(uid, "Как вам наше <b>обслуживание</b>?", reply_markup=get_stars_kb("service"))

@dp.callback_query(F.data.startswith("rate_service_"))
async def rate_service(c: CallbackQuery, state: FSMContext):
    await state.update_data(service_rate=int(c.data.split("_")[2]))
    await c.message.edit_text(
        f"Обслуживание: {c.data.split('_')[2]} ⭐\n\nКак оцените <b>еду и напитки</b>?", 
        reply_markup=get_stars_kb("food")
    )
    await state.set_state(ReviewState.waiting_for_food_rate)
    await c.answer()

@dp.callback_query(F.data.startswith("rate_food_"), ReviewState.waiting_for_food_rate)
async def rate_food(c: CallbackQuery, state: FSMContext):
    rating = int(c.data.split("_")[2])
    await state.update_data(food_rate=rating)
    data = await state.get_data()
    if data.get('service_rate', 0) >= 4:
        await c.message.edit_text(
            f"Еда: {rating} ⭐\n\nЖелаете оставить <b>чаевые</b> бариста?", 
            reply_markup=get_yes_no_kb()
        )
        await state.set_state(ReviewState.waiting_for_tips_decision)
    else:
        await state.update_data(tips="Нет")
        await c.message.edit_text("Напишите отзыв:", reply_markup=get_skip_comment_kb())
        await state.set_state(ReviewState.waiting_for_comment)
    await c.answer()

@dp.callback_query(F.data.startswith("tips_"), ReviewState.waiting_for_tips_decision)
async def tips_decision(c: CallbackQuery, state: FSMContext):
    if c.data.split("_")[1] == "yes":
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
            b = BARISTAS[b_id]
            await state.update_data(tips=f"Выбрано: {b['name']}")
            await c.message.edit_text(
                f"💳 Номер телефона для перевода ({b['name']}):\n<code>{b['phone']}</code>\n\nСпасибо за поддержку! ❤️\n\nНапишите ваш отзыв:", 
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
    if avg >= 5: resp = "Вау! 😍 Спасибо за высокую оценку!\nМы счастливы, что вам понравилось. Ждем вас снова за лучшим кофе! ☕️"
    elif avg >= 4: resp = "Спасибо за ваш отзыв! 👍\nМы рады, что вы с нами. Будем стараться стать еще лучше!"
    elif avg >= 3: resp = "Спасибо за отзыв.\nНам жаль, что не всё прошло идеально. Мы учтем ваши замечания. 🙏"
    else: resp = "Нам очень жаль, что мы вас расстроили. 😔\nСпасибо за честность, мы обязательно примем меры и исправимся."
    
    if isinstance(message, types.Message): await message.answer(resp)
    else: await message.edit_text(resp)
    await state.clear()

# --- ЧАТ ПОДДЕРЖКИ ---
@dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"), StateFilter(None))
async def handle_user_support_message(m: types.Message):
    user_name = NAMES_CACHE.get(str(m.from_user.id), m.from_user.full_name)
    user_id = m.from_user.id
    username_link = f"@{m.from_user.username}" if m.from_user.username else "без юзернейма"
    text_to_admin = (
        f"📩 <b>СООБЩЕНИЕ ОТ ГОСТЯ</b>\n"
        f"👤 <b>От:</b> {user_name} ({username_link})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"➖➖➖➖➖➖➖➖➖➖\n"
        f"{m.text}"
    )
    try:
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text_to_admin,
            message_thread_id=TOPIC_ID_SUPPORT,
            reply_markup=get_reply_kb(user_id)
        )
    except Exception as e:
        logging.error(f"Support msg error: {e}")

@dp.callback_query(F.data.startswith("reply_"))
async def admin_reply_start(c: CallbackQuery, state: FSMContext):
    user_id = c.data.split("_")[1]
    await state.update_data(reply_user_id=user_id)
    await state.set_state(SupportState.waiting_for_admin_reply)
    await c.message.answer(f"✍️ Введите ответ для пользователя (ID: {user_id}):\nИли напишите /cancel для отмены.")
    await c.answer()

@dp.message(SupportState.waiting_for_admin_reply, Command("cancel"))
async def admin_reply_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Ответ отменен.")

@dp.message(SupportState.waiting_for_admin_reply)
async def admin_reply_send(m: types.Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get('reply_user_id')
    
    if not target_user_id:
        await m.answer("❌ Ошибка: потерян ID пользователя.")
        await state.clear()
        return

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=f"👨‍🍳 <b>Ответ от CoffeeMoll:</b>\n\n{m.text}"
        )
        await m.answer("✅ Ответ отправлен.")
    except Exception as e:
        await m.answer(f"❌ Не удалось отправить сообщение\nОшибка: {e}")
    
    await state.clear()
    
if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass
