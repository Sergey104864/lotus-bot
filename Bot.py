import asyncio
import logging
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiohttp import web
from openai import AsyncOpenAI
from dotenv import load_dotenv

from database import init_db, add_booking, get_user_bookings, get_today_bookings

load_dotenv()

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PROXYAPI_KEY = os.getenv("PROXYAPI_KEY")

# ИИ
client = AsyncOpenAI(
    api_key=PROXYAPI_KEY,
    base_url="https://api.proxyapi.ru/v1"
)

# ================= БОТ =================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ================= СОСТОЯНИЯ (FSM) =================
class BookingStates(StatesGroup):
    service = State()
    date = State()
    time = State()
    name = State()
    phone = State()


# ================= КНОПКИ =================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Записаться", callback_data="book")],
        [InlineKeyboardButton(text="Мои записи", callback_data="my_bookings")],
        [InlineKeyboardButton(text="Услуги и цены", callback_data="prices")],
    ])


def get_services():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Стрижка - 1500 руб", callback_data="serv_Стрижка")],
        [InlineKeyboardButton(text="Борода - 900 руб", callback_data="serv_Борода")],
        [InlineKeyboardButton(text="Комплекс - 2200 руб", callback_data="serv_Комплекс")],
        [InlineKeyboardButton(text="Назад", callback_data="back")]
    ])


# ================= ОБРАБОТЧИКИ =================

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Добро пожаловать в Студию Лотос!\n"
        "Меня зовут Лотос, я помогу вам записаться на услугу за 20 секунд.\n\n"
        "Выберите действие или просто напишите свой вопрос:",
        reply_markup=get_main_menu()
    )


@dp.callback_query(F.data == "back")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())


@dp.callback_query(F.data == "prices")
async def show_prices(callback: types.CallbackQuery):
    text = "Прайс-лист:\n\nСтрижка - 1500 руб\nБорода - 900 руб\nКомплекс (стрижка + борода) - 2200 руб"
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]])
    )


@dp.callback_query(F.data == "my_bookings")
async def my_bookings(callback: types.CallbackQuery):
    bookings = await get_user_bookings(callback.from_user.id)
    if not bookings:
        await callback.message.edit_text(
            "У вас пока нет записей.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]])
        )
        return

    text = "Ваши записи:\n\n"
    for b in bookings:
        text += f"• {b[0]} на {b[1]} в {b[2]}\n"
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]])
    )


# === ЗАПИСЬ ===
@dp.callback_query(F.data == "book")
async def start_booking(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Выберите услугу:", reply_markup=get_services())
    await state.set_state(BookingStates.service)


@dp.callback_query(F.data.startswith("serv_"), BookingStates.service)
async def process_service(callback: types.CallbackQuery, state: FSMContext):
    service = callback.data.split("_")[1]
    await state.update_data(service=service)
    await callback.message.edit_text(f"Вы выбрали: {service}.\nВведите дату (например, 25.10):")
    await state.set_state(BookingStates.date)


@dp.message(BookingStates.date)
async def process_date(message: types.Message, state: FSMContext):
    await state.update_data(date=message.text)
    await message.answer("Введите время (например, 14:00):")
    await state.set_state(BookingStates.time)


@dp.message(BookingStates.time)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await message.answer("Как вас зовут?")
    await state.set_state(BookingStates.name)


@dp.message(BookingStates.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)

    # Кнопка для отправки телефона
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить телефон", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Отправьте ваш номер телефона кнопкой ниже:", reply_markup=kb)
    await state.set_state(BookingStates.phone)


@dp.message(BookingStates.phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    data = await state.get_data()

    # Сохраняем в БД
    await add_booking(message.from_user.id, data['service'], data['date'], data['time'], data['name'], phone)

    # Уведомляем админа
    admin_text = (
        f"НОВАЯ ЗАПИСЬ!\n\n"
        f"Услуга: {data['service']}\n"
        f"Дата: {data['date']}\n"
        f"Время: {data['time']}\n"
        f"Имя: {data['name']}\n"
        f"Телефон: {phone}\n"
        f"Клиент: @{message.from_user.username or 'нет'}"
    )
    await bot.send_message(ADMIN_ID, admin_text)

    # Ответ клиенту
    await message.answer(
        "Вы успешно записаны!\nЖдём вас в Студии Лотос.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await message.answer("Главное меню:", reply_markup=get_main_menu())
    await state.clear()


# === АДМИНКА ===
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    today = datetime.now().strftime("%d.%m")
    bookings = await get_today_bookings(today)

    if not bookings:
        await message.answer(f"На {today} записей нет.")
        return

    text = f"Записи на {today}:\n\n"
    for b in bookings:
        text += f"{b[0]} | {b[1]} | {b[2]} | {b[3]}\n"
    await message.answer(text)


# === ИИ (ОБРАБОТЧИК ВСЕХ ТЕКСТОВ) ===
@dp.message(F.text)
async def ai_consultant(message: types.Message, state: FSMContext):
    # Если пользователь в процессе записи, не мешаем
    current_state = await state.get_state()
    if current_state is not None:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    system_prompt = """
    Ты - администратор барбершопа "Студия Лотос". 
    Адрес: Москва, ул. Примерная 10. Телефон: +7 999 123-45-67. Работаем ежедневно с 10:00 до 20:00.
    Услуги: Стрижка (1500 руб), Борода (900 руб), Комплекс (2200 руб). Оплата картой или наличными. Есть парковка.
    Отвечай вежливо, кратко, без эмодзи. Если клиент хочет записаться, направь его в меню (нажми /start).
    """

    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.text}
            ],
            timeout=30.0
        )
        answer = response.choices[0].message.content
        await message.answer(answer)
    except Exception as e:
        await message.answer("Извините, произошла ошибка. Пожалуйста, воспользуйтесь кнопками меню.")
        print(f"Ошибка ИИ: {e}")


# ================= ЗАПУСК (Для Render) =================
async def web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot is alive!"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Веб-сервер на порту {port}")


async def main():
    await init_db()
    print("Бот Студия Лотос запущен!")
    asyncio.create_task(web_server())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())