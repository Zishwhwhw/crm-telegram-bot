import asyncio
import logging
import os
import aiosqlite
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command, CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import LabeledPrice, PreCheckoutQuery
from dotenv import load_dotenv
import openpyxl
from io import BytesIO
from google import genai

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://example.com")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def init_db():
    async with aiosqlite.connect('crm.db') as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS clients 
                            (user_id INTEGER PRIMARY KEY, username TEXT, status TEXT, balance INTEGER DEFAULT 0, referrer_id INTEGER)''')
        try:
            await db.execute("ALTER TABLE clients ADD COLUMN balance INTEGER DEFAULT 0")
        except: pass
        try:
            await db.execute("ALTER TABLE clients ADD COLUMN referrer_id INTEGER")
        except: pass
        
        await db.execute('''CREATE TABLE IF NOT EXISTS bookings 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, service TEXT, datetime TEXT)''')
        await db.commit()

# --- USER COMMANDS ---

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📖 <b>Справка по боту:</b>\n\n"
        "/start — Главное меню\n"
        "/profile — Твой профиль, баланс и статусы\n"
        "/menu — Открыть меню кнопок\n"
        "/services — Список наших услуг\n\n"
        "🎙 <i>Отправь голосовое или текст для связи с ИИ.</i>"
    )
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
        text += "\n\n👑 <b>Админ-команды:</b>\n/admin — Панель управления\n/leads — Выгрузить Excel\n/broadcast <текст> — Рассылка"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    async with aiosqlite.connect('crm.db') as db:
        async with db.execute("SELECT status, balance FROM clients WHERE user_id = ?", (message.from_user.id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                status, balance = row
            else:
                return await message.answer("❌ Профиль не найден. Нажмите /start")
        
        async with db.execute("SELECT COUNT(*) FROM clients WHERE referrer_id = ?", (message.from_user.id,)) as cursor:
            ref_count = (await cursor.fetchone())[0]
            
        async with db.execute("SELECT service, datetime FROM bookings WHERE user_id = ? ORDER BY id DESC LIMIT 3", (message.from_user.id,)) as cursor:
            bookings = await cursor.fetchall()
            
    text = f"👤 <b>Твой профиль:</b>\n\n"
    text += f"💎 Статус: <b>{status.upper()}</b>\n"
    text += f"💰 Баланс: <b>{balance}</b> бонусов\n"
    text += f"🤝 Приглашено друзей: <b>{ref_count}</b>\n\n"
    if bookings:
        text += "📅 <b>Твои свежие записи:</b>\n"
        for b in bookings:
            text += f"- {b[0]} ({b[1]})\n"
    else:
        text += "У тебя пока нет записей."
        
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("services"))
async def cmd_services(message: types.Message):
    await message.answer(
        "🛠 <b>Наши Услуги:</b>\n\n"
        "1. <b>AI Consultation</b> — Разберём твой бизнес и внедрим ИИ.\n"
        "2. <b>Bot Development</b> — Создадим Telegram Mini App под ключ.\n"
        "3. <b>UI/UX Design</b> — Нарисуем премиальный дизайн.\n\n"
        "Жми /menu и кнопку 'Записаться'!", parse_mode="HTML"
    )

@dp.message(Command("menu"))
async def cmd_menu(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📅 Записаться (Mini App)", web_app=types.WebAppInfo(url=f"{WEBAPP_URL}book")))
    builder.row(types.InlineKeyboardButton(text="💎 Купить Premium", callback_data="buy_premium"))
    builder.row(types.InlineKeyboardButton(text="🤝 Моя реф. ссылка", callback_data="ref_link"))
    await message.answer("📱 <b>Главное меню:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    global ADMIN_ID
    if ADMIN_ID == 0:
        ADMIN_ID = message.from_user.id
        await message.answer(f"👑 Вы назначены администратором! Ваш ID: {ADMIN_ID}.")

    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])
        if referrer_id != message.from_user.id:
            async with aiosqlite.connect('crm.db') as db:
                await db.execute("UPDATE clients SET balance = balance + 50 WHERE user_id = ?", (referrer_id,))
                await db.commit()
            try:
                await bot.send_message(referrer_id, "🎉 По вашей ссылке зарегистрировался новый пользователь! Вы получили 50 бонусов.")
            except: pass

    async with aiosqlite.connect('crm.db') as db:
        await db.execute("INSERT OR IGNORE INTO clients (user_id, username, status, referrer_id) VALUES (?, ?, ?, ?)", 
                         (message.from_user.id, message.from_user.username, "new_lead", referrer_id))
        await db.commit()

    await cmd_help(message)
    await cmd_menu(message)

@dp.callback_query(lambda c: c.data == 'ref_link')
async def process_ref(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    bot_info = await bot.me()
    ref_link = f"https://t.me/{bot_info.username}?start={callback_query.from_user.id}"
    await bot.send_message(callback_query.from_user.id, f"🤝 Твоя реферальная ссылка:\n{ref_link}\n\nПолучай 50 бонусов за каждого друга!")

# --- ADMIN PANEL ---

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID: return
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats"))
    builder.row(types.InlineKeyboardButton(text="📅 Последние записи", callback_data="admin_bookings"))
    builder.row(types.InlineKeyboardButton(text="👥 Список юзеров", callback_data="admin_users"))
    await message.answer("👑 <b>Панель управления (Admin):</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(lambda c: c.data.startswith('admin_'))
async def admin_callbacks(callback_query: types.CallbackQuery):
    if ADMIN_ID and callback_query.from_user.id != ADMIN_ID: return
    await bot.answer_callback_query(callback_query.id)
    
    async with aiosqlite.connect('crm.db') as db:
        if callback_query.data == "admin_stats":
            async with db.execute("SELECT COUNT(*) FROM clients") as c: total_users = (await c.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM clients WHERE status='premium'") as c: total_prem = (await c.fetchone())[0]
            async with db.execute("SELECT SUM(balance) FROM clients") as c: total_bal = (await c.fetchone())[0] or 0
            async with db.execute("SELECT COUNT(*) FROM bookings") as c: total_book = (await c.fetchone())[0]
            
            text = (
                "📈 <b>Статистика проекта:</b>\n\n"
                f"👥 Всего юзеров: <b>{total_users}</b>\n"
                f"💎 Premium юзеров: <b>{total_prem}</b>\n"
                f"💰 Выдано бонусов: <b>{total_bal}</b>\n"
                f"📅 Всего записей: <b>{total_book}</b>\n"
            )
            await bot.send_message(callback_query.from_user.id, text, parse_mode="HTML")
            
        elif callback_query.data == "admin_bookings":
            async with db.execute("SELECT user_id, service, datetime FROM bookings ORDER BY id DESC LIMIT 10") as c:
                books = await c.fetchall()
            text = "📅 <b>Последние записи:</b>\n\n"
            for b in books: text += f"ID:{b[0]} — {b[1]} ({b[2]})\n"
            await bot.send_message(callback_query.from_user.id, text or "Нет записей.", parse_mode="HTML")
            
        elif callback_query.data == "admin_users":
            async with db.execute("SELECT username, status, balance FROM clients ORDER BY user_id DESC LIMIT 15") as c:
                users = await c.fetchall()
            text = "👥 <b>Последние 15 юзеров:</b>\n\n"
            for u in users: text += f"@{u[0]} | {u[1]} | {u[2]} баллов\n"
            await bot.send_message(callback_query.from_user.id, text or "Пусто.", parse_mode="HTML")

@dp.message(Command("leads"))
async def cmd_leads(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["User ID", "Username", "Status", "Balance"])
    async with aiosqlite.connect('crm.db') as db:
        async with db.execute("SELECT user_id, username, status, balance FROM clients") as cursor:
            async for row in cursor: ws.append(row)
    file_bytes = BytesIO()
    wb.save(file_bytes)
    file_bytes.seek(0)
    document = types.BufferedInputFile(file_bytes.read(), filename="crm_leads.xlsx")
    await message.answer_document(document, caption="📊 Выгрузка лидов из CRM")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    text_to_send = message.text.replace("/broadcast", "").strip()
    if not text_to_send:
        return await message.answer("Использование: /broadcast <текст>")
    success, failed = 0, 0
    async with aiosqlite.connect('crm.db') as db:
        async with db.execute("SELECT user_id FROM clients") as cursor:
            async for row in cursor:
                try:
                    await bot.send_message(row[0], f"📢 Рассылка:\n\n{text_to_send}")
                    success += 1
                except:
                    failed += 1
    await message.answer(f"✅ Рассылка завершена!\nУспешно: {success}\nОшибок: {failed}")

# --- PAYMENTS & MINI APP & AI ---

@dp.callback_query(lambda c: c.data == 'buy_premium')
async def buy_premium(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    prices = [LabeledPrice(label="Premium Status", amount=1)]
    await bot.send_invoice(callback_query.from_user.id, title="Premium Подписка", description="Эксклюзивные функции CRM", payload="premium_payload", provider_token="", currency="XTR", prices=prices)

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    async with aiosqlite.connect('crm.db') as db:
        await db.execute("UPDATE clients SET status = 'premium' WHERE user_id = ?", (message.from_user.id,))
        await db.commit()
    await message.answer("⭐️ Спасибо за оплату! Твой статус изменён на Premium.")

@dp.message(F.web_app_data)
async def web_app_data_handler(message: types.Message):
    data = json.loads(message.web_app_data.data)
    if data.get('action') == 'book':
        service = data.get('service')
        time = data.get('time')
        async with aiosqlite.connect('crm.db') as db:
            await db.execute("INSERT INTO bookings (user_id, service, datetime) VALUES (?, ?, ?)", (message.from_user.id, service, time))
            await db.commit()
        await message.answer(f"✅ Успешно! Ты записан на {service} в {time}.")
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, f"🔔 Новая запись!\nПользователь: @{message.from_user.username}\nУслуга: {service}\nВремя: {time}")

@dp.message(F.voice)
async def voice_handler(message: types.Message):
    if not GEMINI_API_KEY: return await message.answer("🤖 ИИ не настроен.")
    msg = await message.answer("🎧 Слушаю...")
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, "voice.ogg")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        audio_file = client.files.upload(file="voice.ogg")
        response = client.models.generate_content(model='gemini-2.5-flash', contents=["Расшифруй голосовое сообщение и ответь (кратко):", audio_file])
        await msg.edit_text(f"🎙 <b>ИИ Ответ:</b>\n{response.text}", parse_mode="HTML")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка ИИ: {e}")

@dp.message(F.text)
async def ai_chat(message: types.Message):
    if message.text.startswith('/'): return
    if not GEMINI_API_KEY: return await message.answer("🤖 ИИ не настроен.")
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model='gemini-2.5-flash', contents=message.text)
        await message.answer(response.text)
    except Exception as e:
        await message.answer(f"❌ Ошибка ИИ: {e}")

async def main():
    await init_db()
    print("🤖 Бот CRM с Админкой и Профилями запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
