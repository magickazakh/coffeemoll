import asyncio
import json
import logging
import sys
import os
import re 
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
    waiting_for_custom_time = State()

class ReviewState(StatesGroup):
    waiting_for_service_rate = State()
    waiting_for_food_rate = State()
    waiting_for_tips_decision = State()
    waiting_for_barista_choice = State()
    waiting_for_comment = State()

# --- FIREBASE SETUP ---
def init_firebase():
    # Проверяем, не инициализировано ли уже приложение, чтобы избежать ошибок при перезагрузке
    if not firebase_admin._apps:
        # Определяем путь к файлу ключей (Локально или на Render)
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
    return firestore.client()

db = init_firebase()

def clean_id(raw_id):
    """Удаляет всё кроме цифр из ID"""
    if not raw_id: return ""
    return re.sub(r'\D', '', str(raw_id))

# --- ЛОГИКА ПРОМОКОДОВ (FIREBASE) ---

def check_promo_firebase(code, user_id):
    """Проверяет статус промокода (Read-only)"""
    if not db: return "ERROR", 0
    
    code = code.strip().upper()
    uid = clean_id(user_id)
    
    try:
        # 1. Проверяем историю (Коллекция 'promo_history', документ = UID_CODE)
        # Это мгновенный поиск по ключу (O(1))
        history_ref = db.collection('promo_history').document(f"{uid}_{code}")
        if history_ref.get().exists:
            return "USED", 0

        # 2. Проверяем сам промокод (Коллекция 'promocodes', документ = CODE)
        promo_ref = db.collection('promocodes').document(code)
        promo_doc = promo_ref.get()
        
        if not promo_doc.exists:
            return "NOT_FOUND", 0
            
        data = promo_doc.to_dict()
        limit = data.get('limit', 0)
        discount = data.get('discount', 0)
        
        # Приводим скидку к float на всякий случай
        try: discount = float(discount)
        except: discount = 0
        
        if limit > 0:
            return "OK", discount
        else:
            return "LIMIT", 0
            
    except Exception as e:
        logging.error(f"Firebase Check Error: {e}")
        return "ERROR", 0

# Транзакционная функция для атомарного списания
@firestore.transactional
def use_promo_transaction(transaction, code, uid):
    promo_ref = db.collection('promocodes').document(code)
    history_ref = db.collection('promo_history').document(f"{uid}_{code}")
    
    # Читаем данные внутри транзакции
    snapshot = promo_ref.get(transaction=transaction)
    
    if not snapshot.exists:
        return "NOT_FOUND"
    
    current_limit = snapshot.get('limit')
    
    if current_limit <= 0:
        return "LIMIT"
        
    # Проверяем историю (на случай если успел использовать в параллельном потоке)
    hist_snap = history_ref.get(transaction=transaction)
    if hist_snap.exists:
        return "USED"

    # Пишем данные
    transaction.update(promo_ref, {'limit': current_limit - 1})
    transaction.set(history_ref, {
        'user_id': uid,
        'code': code,
        'timestamp': firestore.SERVER_TIMESTAMP
    })
    return "OK"

def process_promo_firebase(code, user_id):
    """Пытается списать промокод"""
    if not db: return "ERROR"
    
    code = code.strip().upper()
    uid = clean_id(user_id)
    
    try:
        transaction = db.transaction()
        result = use_promo_transaction(transaction, code, uid)
        return result
    except Exception as e:
        logging.error(f"Transaction Error: {e}")
        return "ERROR"

# --- ЗАПИСЬ ОТЗЫВОВ (FIREBASE) ---

def save_review_firebase(user_id, name, service_rate, food_rate, tips, comment):
    if not db: return
    try:
        # Просто добавляем документ в коллекцию 'reviews' с авто-ID
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
    except Exception as e:
        logging.error(f"Save Review Error: {e}")

# --- API ДЛЯ WEB APP ---

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
        # Вызываем Firebase проверку в отдельном потоке, чтобы не блочить бота
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

# --- ЗАПУСК ---
async def main():
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    print("🤖 Bot started with Firebase...")
    await dp.start_polling(bot)

# --- КЛАВИАТУРЫ ---
# (Остались без изменений)
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
    b.append([InlineKeyboardButton(text="Отмена", callback_data="tips_no")])
    return InlineKeyboardMarkup(inline_keyboard=b)
def get_skip_comment_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⏩ Пропустить", callback_data="skip_comment")]])


# --- ОБРАБОТЧИКИ (С ИНТЕГРАЦИЕЙ FIREBASE) ---

@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    await m.answer("Добро пожаловать в CoffeeMoll! 🥐", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="☕️ Сделать заказ", web_app=WebAppInfo(url=WEB_APP_URL))]], resize_keyboard=True))

@dp.message(F.web_app_data)
async def web_app_data_handler(m: types.Message):
    try:
        d = json.loads(m.web_app_data.data)
        if d.get('type') == 'review': return
        cart, total, info = d.get('cart', []), d.get('total', 0), d.get('info', {})
        promo, disc = info.get('promoCode', ''), info.get('discount', 0)
        d_txt, warn = "", ""
        
        if promo and disc > 0:
            # --- FIREBASE PROCESS ---
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
        txt = f"{'🚗' if is_del else '🏃'} <b>НОВЫЙ ЗАКАЗ</b>\n➖➖➖➖➖➖➖➖➖➖\n👤 {info.get('name')} (<a href='tel:{info.get('phone')}'>{info.get('phone')}</a>)\n"
        txt += f"📍 {'Адрес: ' + info.get('address') if is_del else info.get('deliveryType')}\n💳 {info.get('paymentType')}\n"
        if info.get('paymentType') in ['Kaspi', 'Halyk']: txt += f"📱 <b>Счет:</b> <code>{info.get('paymentPhone')}</code>\n"
        if info.get('comment'): txt += f"💬 <i>{info.get('comment')}</i>\n"
        if "Ко времени" in str(info.get('comment')): txt += "⏰ <b>КО ВРЕМЕНИ!</b>\n"
        txt += f"➖➖➖➖➖➖➖➖➖➖\n"
        for i, item in enumerate(cart, 1):
            opts = [o for o in item.get('options', []) if o and o != "Без сахара"]
            q = item.get('qty', 1)
            txt += f"{i}. <b>{item.get('name')}</b> {'('+ ', '.join(opts) +')' if opts else ''}{f' <b>x {q}</b>' if q > 1 else ''}\n"
        txt += f"{d_txt}\n💰 <b>ИТОГО: {total} ₸</b>"
        if is_del: txt += "\n⚠️ <i>+ Доставка</i>"

        await bot.send_message(ADMIN_CHAT_ID, txt, reply_markup=get_decision_kb(m.chat.id), message_thread_id=TOPIC_ID_ORDERS)
        
        response_text = f"✅ Заказ принят!\nСумма: {total} ₸"
        if warn: response_text += f"\n{warn}"
        response_text += "\n\nЖдите подтверждения времени."
        await m.answer(response_text)
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
    loop.run_in_executor(None, save_review_firebase, 
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









