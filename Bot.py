"""Бот записи «Студия Лотос»: запись на услуги + ИИ-консультант.
Запуск: python Bot.py
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiohttp import web
from dotenv import load_dotenv
from openai import AsyncOpenAI
from database import add_booking, get_today_bookings, get_user_bookings, init_db
load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("lotus")
# ================= НАСТРОЙКИ =================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID") or "0")
PROXYAPI_KEY = (os.getenv("PROXYAPI_KEY") or "").strip()
TIMEZONE = os.getenv("TZ", "Europe/Moscow")
bot: Bot | None = None
client: AsyncOpenAI | None = None
dp = Dispatcher()
# ================= СОСТОЯНИЯ =================
class BookingStates(StatesGroup):
    service = State()
    date = State()
    time = State()
    name = State()
    phone = State()
# ================= КНОПКИ =================
def get_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Записаться", callback_data="book")],
            [InlineKeyboardButton(text="Мои записи", callback_data="my_bookings")],
            [InlineKeyboardButton(text="Услуги и цены", callback_data="prices")],
        ]
    )
def get_services() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Стрижка - 1500 руб", callback_data="serv_Стрижка")],
            [InlineKeyboardButton(text="Борода - 900 руб", callback_data="serv_Борода")],
            [InlineKeyboardButton(text="Комплекс - 2200 руб", callback_data="serv_Комплекс")],
            [InlineKeyboardButton(text="Назад", callback_data="back")],
        ]
    )
def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back")]]
    )
# ================= ОБРАБОТЧИКИ =================
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Добро пожаловать в Студию Лотос!\n"
        "Меня зовут Лотос, я помогу вам записаться на услугу за 20 секунд.\n\n"
        "Выберите действие или просто напишите свой вопрос:",
        reply_markup=get_main_menu(),
    )
@dp.callback_query(F.data == "back")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())
    await callback.answer()
@dp.callback_query(F.data == "prices")
async def show_prices(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(
        "Прайс-лист:\n\nСтрижка - 1500 руб\nБорода - 900 руб\n"
        "Комплекс (стрижка + борода) - 2200 руб",
        reply_markup=back_button(),
    )
    await callback.answer()
@dp.callback_query(F.data == "my_bookings")
async def my_bookings(callback: types.CallbackQuery) -> None:
    bookings = await get_user_bookings(callback.from_user.id)
    if not bookings:
        await callback.message.edit_text("У вас пока нет записей.", reply_markup=back_button())
        await callback.answer()
        return
    text = "Ваши записи:\n\n"
    for b in bookings:
        text += f"— {b[0]} на {b[1]} в {b[2]}\n"
    await callback.message.edit_text(text, reply_markup=back_button())
    await callback.answer()
@dp.callback_query(F.data == "book")
async def start_booking(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text("Выберите услугу:", reply_markup=get_services())
    await state.set_state(BookingStates.service)
    await callback.answer()
@dp.callback_query(F.data.startswith("serv_"), BookingStates.service)
async def process_service(callback: types.CallbackQuery, state: FSMContext) -> None:
    service = callback.data.split("_", 1)[1]
    await state.update_data(service=service)
    await callback.message.edit_text(
        f"Вы выбрали: {service}.\nВведите дату (например, 25.10):"
    )
    await state.set_state(BookingStates.date)
    await callback.answer()
@dp.message(BookingStates.date)
async def process_date(message: types.Message, state: FSMContext) -> None:
    await state.update_data(date=(message.text or "").strip())
    await message.answer("Введите время (например, 14:00):")
    await state.set_state(BookingStates.time)
@dp.message(BookingStates.time)
async def process_time(message: types.Message, state: FSMContext) -> None:
    await state.update_data(time=(message.text or "").strip())
    await message.answer("Как вас зовут?")
    await state.set_state(BookingStates.name)
@dp.message(BookingStates.name)
async def process_name(message: types.Message, state: FSMContext) -> None:
    await state.update_data(name=(message.text or "").strip())
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить телефон", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer("Отправьте ваш номер телефона кнопкой ниже:", reply_markup=kb)
    await state.set_state(BookingStates.phone)
@dp.message(BookingStates.phone, F.contact)
async def process_phone(message: types.Message, state: FSMContext) -> None:
    assert message.contact is not None and bot is not None
    phone = message.contact.phone_number
    data = await state.get_data()
    await add_booking(
        message.from_user.id,
        data.get("service", ""),
        data.get("date", ""),
        data.get("time", ""),
        data.get("name", ""),
        phone,
    )
    if ADMIN_ID:
        admin_text = (
            "НОВАЯ ЗАПИСЬ\n\n"
            f"Услуга: {data.get('service', '')}\n"
            f"Дата: {data.get('date', '')}\n"
            f"Время: {data.get('time', '')}\n"
            f"Имя: {data.get('name', '')}\n"
            f"Телефон: {phone}\n"
            f"Клиент: @{message.from_user.username or 'нет'}"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text)
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось уведомить админа: %s", exc)
    await message.answer(
        "Вы успешно записаны!\nЖдём вас в Студии Лотос.",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await message.answer("Главное меню:", reply_markup=get_main_menu())
    await state.clear()
@dp.message(BookingStates.phone)
async def process_phone_text(message: types.Message) -> None:
    await message.answer(
        "Пожалуйста, отправьте номер кнопкой ниже — так он точно сохранится."
    )
@dp.message(Command("admin"))
async def admin_panel(message: types.Message) -> None:
    if message.from_user.id != ADMIN_ID:
        return
    today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%d.%m")
    bookings = await get_today_bookings(today)
    if not bookings:
        await message.answer(f"На {today} записей нет.")
        return
    text = f"Записи на {today}:\n\n"
    for b in bookings:
        text += f"{b[0]} | {b[1]} | {b[2]} | {b[3]}\n"
    await message.answer(text)
@dp.message(F.text)
async def ai_consultant(message: types.Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return
    if client is None or bot is None:
        await message.answer("Бот ещё запускается, попробуйте через минуту.")
        return
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    system_prompt = (
        "Ты — администратор барбершопа «Студия Лотос».\n"
        "Адрес: Москва, ул. Примерная 10. Телефон: +7 999 123-45-67. "
        "Работаем ежедневно с 10:00 до 20:00.\n"
        "Услуги: Стрижка (1500 руб), Борода (900 руб), Комплекс (2200 руб). "
        "Оплата картой или наличными. Есть парковка.\n"
        "Отвечай вежливо, кратко, без эмодзи. "
        "Если клиент хочет записаться — предложи нажать «Записаться» в меню "
        "или отправить /start."
    )
    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-v4.1-flash",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.text or ""},
            ],
            timeout=30.0,
        )
        answer = (response.choices[0].message.content or "").strip()
        await message.answer(answer or "Уточните, пожалуйста, вопрос.")
    except Exception as exc:  # noqa: BLE001
        log.warning("Ошибка ИИ: %s", exc)
        await message.answer(
            "Извините, произошла ошибка. Пожалуйста, воспользуйтесь кнопками меню."
        )
# ================= ЗАПУСК =================
async def web_server() -> None:
    app = web.Application()
    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Веб-сервер на порту %s", port)
def build_session() -> AiohttpSession:
    """Прокси нужен только там, где Telegram заблокирован.
    На Render и большинстве хостингов он не нужен: если HTTPS_PROXY пустой,
    работаем напрямую. Если задан — передаём строку, а не готовый коннектор.
    """
    proxy_url = (os.getenv("HTTPS_PROXY") or "").strip()
    if proxy_url.startswith("socks5h://"):
        proxy_url = proxy_url.replace("socks5h://", "socks5://", 1)
    if proxy_url:
        log.info("Telegram через прокси: %s", proxy_url.split("@")[-1])
        return AiohttpSession(proxy=proxy_url)
    return AiohttpSession()
async def main() -> None:
    global bot, client
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    if PROXYAPI_KEY:
        client = AsyncOpenAI(
            api_key=PROXYAPI_KEY,
            base_url="https://api.proxyapi.ru/v1",
            timeout=30,
            max_retries=1,
        )
    else:
        log.warning("PROXYAPI_KEY не задан — ИИ-режим выключен, работают только кнопки")
    bot = Bot(token=BOT_TOKEN, session=build_session())
    await init_db()
    log.info("Бот Студия Лотос запущен!")
    asyncio.create_task(web_server())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        log.info("Остановка: %s", exc or "signal")