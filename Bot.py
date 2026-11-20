"""Бот записи «Студия Лотос» v2: мастера, слоты, отмена, админка, Google Таблица, ИИ.
Запуск: python Bot.py
"""
from __future__ import annotations
import asyncio
import csv
import io
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiohttp import ClientSession, ClientTimeout, web
from dotenv import load_dotenv
from openai import AsyncOpenAI
import database as db
load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("lotus")
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID") or "0")
PROXYAPI_KEY = (os.getenv("PROXYAPI_KEY") or "").strip()
TIMEZONE = os.getenv("TZ", "Europe/Moscow")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Студия Лотос")
CITY = os.getenv("CITY", "Москва")
ADDRESS = os.getenv("ADDRESS", "ул. Примерная, 10")
PHONE = os.getenv("PHONE", "+7 999 123-45-67")
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD") or "7")
SLOT_STEP = int(os.getenv("SLOT_STEP") or "30")
REMIND_HOURS = int(os.getenv("REMIND_HOURS") or "2")
SHEETS_URL = (os.getenv("SHEETS_WEBHOOK_URL") or "").strip()
SHEETS_TOKEN = (os.getenv("SHEETS_WEBHOOK_TOKEN") or "").strip()
# Секретная ссылка для =IMPORTDATA() в Google Таблице
EXPORT_TOKEN = (os.getenv("EXPORT_TOKEN") or "").strip()
bot: Bot | None = None
client: AsyncOpenAI | None = None
dp = Dispatcher()
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
# ================= ФОРМАТ ДАТ =================
def human_day(day: str) -> str:
    try:
        value = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return day
    return f"{WEEKDAYS[value.weekday()]}, {value.day} {MONTHS[value.month - 1]}"
def next_days(count: int) -> list[date]:
    today = date.today()
    return [today + timedelta(days=i) for i in range(count)]
def slots_for(master_row, day: str) -> list[str]:
    """Свободные слоты мастера на день."""
    booked = db.booked_slots(day, master_row["id"])
    result = []
    start = datetime.strptime(f"{day} {master_row['work_start']:02d}:00", "%Y-%m-%d %H:%M")
    end = datetime.strptime(f"{day} {master_row['work_end']:02d}:00", "%Y-%m-%d %H:%M")
    now = datetime.now()
    while start < end:
        label = start.strftime("%H:%M")
        if label not in booked and start > now:
            result.append(label)
        start += timedelta(minutes=SLOT_STEP)
    return result
# ================= КНОПКИ =================
def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Записаться", callback_data="book")],
        [InlineKeyboardButton(text="Мастера", callback_data="masters")],
        [InlineKeyboardButton(text="Мои записи", callback_data="my")],
        [InlineKeyboardButton(text="Услуги и цены", callback_data="prices")],
        [InlineKeyboardButton(text="Контакты", callback_data="contacts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
def back_kb(to: str = "back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=to)]]
    )
def services_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{s['name']} · {s['price']} ₽", callback_data=f"svc:{s['id']}")]
        for s in db.services()
    ]
    rows.append([InlineKeyboardButton(text="Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def masters_kb(service_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{m['name']} — {m['spec']}", callback_data=f"mst:{m['id']}")]
        for m in db.masters_for_service(service_id)
    ]
    rows.append([InlineKeyboardButton(text="Любой мастер", callback_data="mst:0")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="book")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def days_kb(service_id: int, master_id: int) -> InlineKeyboardMarkup:
    rows = []
    master_row = db.master(master_id) if master_id else None
    for day in next_days(DAYS_AHEAD):
        key = day.isoformat()
        label = human_day(key)
        if master_row is not None and not slots_for(master_row, key):
            label += " · занято"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"day:{key}")]
        )
    rows.append([InlineKeyboardButton(text="Назад", callback_data=f"svc:{service_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def times_kb(day: str, slots: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(slots), 3):
        rows.append(
            [
                InlineKeyboardButton(text=slot, callback_data=f"tm:{slot}")
                for slot in slots[i : i + 3]
            ]
        )
    rows.append([InlineKeyboardButton(text="Другая дата", callback_data="book")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить", callback_data="confirm")],
            [InlineKeyboardButton(text="Отменить", callback_data="back")],
        ]
    )
def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить телефон", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
def my_kb(rows) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"✕ {human_day(r['day'])}, {r['time']} — {r['service_name']}",
            callback_data=f"cancel:{r['id']}")]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton(text="В меню", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
def admin_kb(rows) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"✕ {r['time']} {r['client_name']} ({r['master_name']})",
            callback_data=f"cancel:{r['id']}")]
        for r in rows
    ]
    buttons.append([InlineKeyboardButton(text="В меню", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
# ================= СОСТОЯНИЯ =================
class Form(StatesGroup):
    name = State()
    phone = State()
def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID
# ================= СТАРТ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"<b>{BUSINESS_NAME}</b>\n\n"
        "Запишу вас за 20 секунд: выберете услугу, мастера и удобное время.\n"
        "Или просто напишите вопрос — отвечу.",
        reply_markup=main_menu(),
    )
@dp.callback_query(F.data == "back")
async def cb_back(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
    await callback.answer()
@dp.callback_query(F.data == "contacts")
async def cb_contacts(callback: types.CallbackQuery) -> None:
    await callback.message.answer(
        f"<b>{BUSINESS_NAME}</b>\n{CITY}, {ADDRESS}\nТелефон: {PHONE}\n"
        "Ежедневно с 10:00 до 20:00"
    )
    await callback.answer()
@dp.callback_query(F.data == "prices")
async def cb_prices(callback: types.CallbackQuery) -> None:
    lines = ["Услуги и цены:\n"]
    for s in db.services():
        lines.append(f"• {s['name']} — {s['price']} ₽ ({s['duration']} мин)")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_kb())
    await callback.answer()
def masters_view(index: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Карточка одного мастера с листалкой."""
    rows = db.masters()
    if not rows:
        return None
    index = index % len(rows)
    m = rows[index]
    text = (
        f"<b>{m['name']}</b>\n"
        f"{m['spec']} · опыт {m['exp']}\n\n"
        f"{m['about'] or ''}"
    )
    nav = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="‹", callback_data=f"mcard:{(index - 1) % len(rows)}"),
                InlineKeyboardButton(text=f"{index + 1} из {len(rows)}", callback_data="noop"),
                InlineKeyboardButton(text="›", callback_data=f"mcard:{(index + 1) % len(rows)}"),
            ],
            [InlineKeyboardButton(text=f"Записаться к {m['name']}", callback_data=f"tomaster:{m['id']}")],
            [InlineKeyboardButton(text="В меню", callback_data="back")],
        ]
    )
    return text, nav
@dp.callback_query(F.data == "masters")
async def cb_masters(callback: types.CallbackQuery) -> None:
    view = masters_view(0)
    if view is None:
        await callback.message.edit_text("Пока нет мастеров.", reply_markup=back_kb())
        await callback.answer()
        return
    await callback.message.edit_text(view[0], reply_markup=view[1])
    await callback.answer()
@dp.callback_query(F.data.startswith("mcard:"))
async def cb_master_card(callback: types.CallbackQuery) -> None:
    index = int(callback.data.split(":")[1])
    view = masters_view(index)
    if view is None:
        await callback.answer("Мастера не найдены", show_alert=True)
        return
    await callback.message.edit_text(view[0], reply_markup=view[1])
    await callback.answer()
@dp.callback_query(F.data == "noop")
async def cb_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()
@dp.callback_query(F.data.startswith("tomaster:"))
async def cb_to_master(callback: types.CallbackQuery, state: FSMContext) -> None:
    master_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.update_data(master_id=master_id)
    await callback.message.answer(
        f"Записываем к мастеру {db.master(master_id)['name']}.\nВыберите услугу:",
        reply_markup=services_kb(),
    )
    await callback.answer()
# ================= ЗАПИСЬ =================
@dp.callback_query(F.data == "book")
async def cb_book(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Что вас интересует?", reply_markup=services_kb())
    await callback.answer()
@dp.callback_query(F.data.startswith("svc:"))
async def cb_service(callback: types.CallbackQuery, state: FSMContext) -> None:
    service_id = int(callback.data.split(":")[1])
    service_row = db.service(service_id)
    if service_row is None:
        await callback.answer("Услуга не найдена", show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(service_id=service_id)
    if data.get("master_id"):
        master_id = int(data["master_id"])
        await state.update_data(master_id=master_id)
        await callback.message.edit_text(
            f"{service_row['name']} · {service_row['price']} ₽\n\nВыберите день:",
            reply_markup=days_kb(service_id, master_id),
        )
    else:
        await callback.message.edit_text(
            f"{service_row['name']} · {service_row['price']} ₽\n\nВыберите мастера:",
            reply_markup=masters_kb(service_id),
        )
    await callback.answer()
@dp.callback_query(F.data.startswith("mst:"))
async def cb_master(callback: types.CallbackQuery, state: FSMContext) -> None:
    master_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    service_id = int(data.get("service_id") or 0)
    if not service_id:
        await callback.answer("Сначала выберите услугу", show_alert=True)
        return
    await state.update_data(master_id=master_id)
    label = db.master(master_id)["name"] if master_id else "любому мастеру"
    await callback.message.edit_text(
        f"Записываем {label}.\nВыберите день:",
        reply_markup=days_kb(service_id, master_id),
    )
    await callback.answer()
@dp.callback_query(F.data.startswith("day:"))
async def cb_day(callback: types.CallbackQuery, state: FSMContext) -> None:
    day = callback.data.split(":", 1)[1]
    data = await state.get_data()
    master_id = int(data.get("master_id") or 0)
    service_id = int(data.get("service_id") or 0)
    if master_id:
        slots = slots_for(db.master(master_id), day)
    else:
        # «любой мастер» — объединяем слоты всех подходящих мастеров
        slots_set: set[str] = set()
        for m in db.masters_for_service(service_id):
            slots_set.update(slots_for(m, day))
        slots = sorted(slots_set)
    if not slots:
        await callback.answer("На этот день свободного времени нет", show_alert=True)
        return
    await state.update_data(day=day)
    await callback.message.edit_text(
        f"{human_day(day)}\n\nВыберите время:", reply_markup=times_kb(day, slots)
    )
    await callback.answer()
@dp.callback_query(F.data.startswith("tm:"))
async def cb_time(callback: types.CallbackQuery, state: FSMContext) -> None:
    slot = callback.data.split(":", 1)[1]
    await state.update_data(time=slot)
    await state.set_state(Form.name)
    await callback.message.answer(f"Время {slot}. Как вас зовут?")
    await callback.answer()
@dp.message(Form.name, F.text)
async def step_name(message: types.Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Напишите, пожалуйста, имя — хотя бы две буквы.")
        return
    await state.update_data(client_name=name[:60])
    await state.set_state(Form.phone)
    await message.answer("Оставьте телефон — предупредим, если что-то изменится.", reply_markup=phone_kb())
@dp.message(Form.phone, F.contact)
async def step_contact(message: types.Message, state: FSMContext) -> None:
    assert message.contact is not None
    await _finish_phone(message, state, message.contact.phone_number)
@dp.message(Form.phone, F.text)
async def step_phone_text(message: types.Message, state: FSMContext) -> None:
    await _finish_phone(message, state, message.text or "")
async def _finish_phone(message: types.Message, state: FSMContext, raw: str) -> None:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 10:
        await message.answer("Номер неполный. Напишите в формате +7 999 123-45-67.")
        return
    phone = ("+" + digits) if raw.strip().startswith("+") else digits
    await state.update_data(phone=phone)
    await state.set_state(None)
    data = await state.get_data()
    service_row = db.service(int(data.get("service_id") or 0))
    master_id = int(data.get("master_id") or 0)
    master_name = db.master(master_id)["name"] if master_id else "любой мастер"
    await message.answer(
        "Проверьте запись:\n\n"
        f"Услуга: <b>{service_row['name']}</b>\n"
        f"Мастер: {master_name}\n"
        f"Когда: <b>{human_day(str(data.get('day')))} в {data.get('time')}</b>\n"
        f"Имя: {data.get('client_name')}\n"
        f"Телефон: {phone}",
        reply_markup=confirm_kb(),
    )
@dp.callback_query(F.data == "confirm")
async def cb_confirm(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    service_id = int(data.get("service_id") or 0)
    service_row = db.service(service_id)
    if service_row is None:
        await callback.answer("Сессия истекла, начните заново", show_alert=True)
        await state.clear()
        return
    day = str(data.get("day"))
    slot = str(data.get("time"))
    master_id = int(data.get("master_id") or 0)
    if master_id and not db.is_free(day, slot, master_id):
        await callback.answer("Это время только что заняли", show_alert=True)
        return
    booking_id = db.add_booking(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        client_name=str(data.get("client_name")),
        phone=str(data.get("phone")),
        service_id=service_id,
        service_name=service_row["name"],
        price=int(service_row["price"]),
        master_id=master_id,
        master_name=db.master(master_id)["name"] if master_id else "любой мастер",
        day=day,
        time=slot,
    )
    await state.clear()
    await callback.message.edit_text(
        "Готово, вы записаны.\n\n"
        f"{human_day(day)} в {slot}\n"
        f"{service_row['name']}\n\n"
        f"Напомним за {REMIND_HOURS} ч. Планы изменятся — /my",
        reply_markup=main_menu(),
    )
    await callback.answer("Запись создана")
    await _notify_admin(booking_id)
    await _send_to_sheet(booking_id)
async def _notify_admin(booking_id: int) -> None:
    if not ADMIN_ID or bot is None:
        return
    row = db.booking(booking_id)
    if row is None:
        return
    try:
        await bot.send_message(
            ADMIN_ID,
            "🗓 Новая запись\n\n"
            f"{human_day(row['day'])}, {row['time']}\n"
            f"{row['service_name']} — {row['price']} ₽\n"
            f"Мастер: {row['master_name']}\n"
            f"{row['client_name']}, {row['phone']}\n"
            f"@{row['username'] or 'нет'} · заявка {booking_id}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось уведомить админа: %s", exc)
async def _send_to_sheet(booking_id: int, action: str = "create") -> None:
    """Отправка записи в Google Таблицу через Apps Script вебхук.
    action = create — добавить строку; cancel — найти по ID и поменять статус.
    """
    if not (SHEETS_URL and SHEETS_TOKEN):
        return
    row = db.booking(booking_id)
    if row is None:
        return
    payload = {
        "token": SHEETS_TOKEN,
        "action": action,
        "id": row["id"],
        "date": row["day"],
        "time": row["time"],
        "master": row["master_name"],
        "service": row["service_name"],
        "price": row["price"],
        "client": row["client_name"],
        "phone": row["phone"],
        "status": "активна" if action == "create" else "отменена",
    }
    try:
        async with ClientSession(timeout=ClientTimeout(total=12)) as session:
            async with session.post(SHEETS_URL, json=payload) as resp:
                await resp.text()
    except Exception as exc:  # noqa: BLE001
        log.warning("Таблица недоступна: %s", exc)
# ================= МОИ ЗАПИСИ =================
@dp.message(Command("my"))
async def cmd_my(message: types.Message) -> None:
    await _show_my(message, message.from_user.id)
@dp.callback_query(F.data == "my")
async def cb_my(callback: types.CallbackQuery) -> None:
    await _show_my(callback.message, callback.from_user.id)
    await callback.answer()
async def _show_my(message: types.Message, user_id: int) -> None:
    rows = db.user_bookings(user_id)
    if not rows:
        await message.answer("Записей пока нет.", reply_markup=main_menu())
        return
    lines = ["Ваши записи:\n"]
    for r in rows:
        lines.append(
            f"• {human_day(r['day'])}, {r['time']} — {r['service_name']}, {r['master_name']}"
        )
    await message.answer("\n".join(lines), reply_markup=my_kb(rows))
@dp.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: types.CallbackQuery) -> None:
    booking_id = int(callback.data.split(":")[1])
    row = db.booking(booking_id)
    if row is None:
        await callback.answer("Запись не найдена", show_alert=True)
        return
    if callback.from_user.id != row["user_id"] and not is_admin(callback.from_user.id):
        await callback.answer("Это чужая запись", show_alert=True)
        return
    if not db.cancel(booking_id):
        await callback.answer("Уже отменена", show_alert=True)
        return
    await _send_to_sheet(booking_id, "cancel")
    text = f"Отменено: {human_day(row['day'])}, {row['time']} — {row['service_name']}"
    await callback.answer("Готово")
    if is_admin(callback.from_user.id) and callback.from_user.id != row["user_id"] and bot:
        try:
            await bot.send_message(
                row["user_id"],
                f"Ваша запись отменена: {human_day(row['day'])}, {row['time']}.\n"
                "Записаться заново — /start",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось предупредить клиента: %s", exc)
    await callback.message.answer(text, reply_markup=main_menu())
# ================= АДМИНКА =================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message) -> None:
    if not is_admin(message.from_user.id):
        return
    today = date.today().isoformat()
    rows = db.day_bookings(today)
    if not rows:
        await message.answer(f"На {human_day(today)} записей нет.")
        return
    total = sum(r["price"] or 0 for r in rows)
    lines = [f"<b>{human_day(today)}</b>\n"]
    for r in rows:
        lines.append(
            f"{r['time']} — {r['client_name']}, {r['phone']}\n"
            f"   {r['service_name']} · {r['master_name']}\n"
        )
    lines.append(f"Записей: {len(rows)} · сумма {total} ₽")
    await message.answer("\n".join(lines), reply_markup=admin_kb(rows))
# ================= ИИ =================
@dp.message(F.text)
async def ai_consultant(message: types.Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return
    if (message.text or "").startswith("/"):
        await message.answer("Не знаю такую команду. /start — меню, /my — мои записи.")
        return
    if client is None or bot is None:
        await message.answer("Бот ещё запускается, попробуйте через минуту.")
        return
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    masters_text = "; ".join(
        f"{m['name']} — {m['spec']}, опыт {m['exp']}" for m in db.masters()
    )
    services_text = "; ".join(
        f"{s['name']} — {s['price']} ₽" for s in db.services()
    )
    system_prompt = (
        f"Ты — администратор «{BUSINESS_NAME}».\n"
        f"Адрес: {CITY}, {ADDRESS}. Телефон: {PHONE}. Работаем ежедневно с 10:00 до 20:00.\n"
        f"Услуги: {services_text}.\n"
        f"Мастера: {masters_text}.\n"
        "Оплата картой или наличными, есть парковка.\n"
        "Отвечай вежливо, коротко, без эмодзи. Не выдумывай цены и свободное время. "
        "Если человек хочет записаться — предложи нажать «Записаться» в меню."
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
        await message.answer("Извините, не получилось ответить. Воспользуйтесь кнопками меню.")
# ================= НАПОМИНАНИЯ, HTTP, ЗАПУСК =================
async def reminder_loop() -> None:
    while True:
        try:
            for row in db.due_reminders(REMIND_HOURS):
                if bot is None:
                    break
                try:
                    await bot.send_message(
                        row["user_id"],
                        f"Напоминаем: сегодня в {row['time']} у вас "
                        f"{row['service_name']} в {BUSINESS_NAME}.\n"
                        "Планы изменились — /my",
                    )
                    db.mark_reminded(row["id"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("Напоминание не ушло: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ошибка напоминаний: %s", exc)
        await asyncio.sleep(600)
async def export_csv(request: web.Request) -> web.Response:
    """Выгрузка записей для =IMPORTDATA() в Google Таблице."""
    if not EXPORT_TOKEN or request.match_info.get("token") != EXPORT_TOKEN:
        raise web.HTTPNotFound()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["ID", "Дата", "Время", "Мастер", "Услуга", "Цена", "Клиент", "Телефон", "Статус"]
    )
    for row in db.all_bookings():
        try:
            day = datetime.strptime(str(row["day"]), "%Y-%m-%d").strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            day = str(row["day"])
        writer.writerow(
            [
                row["id"],
                day,
                row["time"],
                row["master_name"],
                row["service_name"],
                row["price"],
                row["client_name"],
                row["phone"],
                "активна" if row["status"] == "active" else "отменена",
            ]
        )
    return web.Response(
        text=buffer.getvalue(),
        content_type="text/csv",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )
async def web_server() -> None:
    app = web.Application()
    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    app.router.add_get("/export/{token}.csv", export_csv)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Веб-сервер на порту %s", port)
def build_session() -> AiohttpSession:
    proxy_url = (os.getenv("HTTPS_PROXY") or "").strip()
    if proxy_url.startswith("socks5h://"):
        proxy_url = proxy_url.replace("socks5h://", "socks5://", 1)
    return AiohttpSession(proxy=proxy_url) if proxy_url else AiohttpSession()
async def main() -> None:
    global bot, client
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан")
    if PROXYAPI_KEY:
        client = AsyncOpenAI(
            api_key=PROXYAPI_KEY, base_url="https://api.proxyapi.ru/v1", timeout=30, max_retries=1
        )
    else:
        log.warning("PROXYAPI_KEY не задан — ИИ выключен")
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=build_session(),
    )
    db.init_db()
    log.info("Бот %s запущен", BUSINESS_NAME)
    asyncio.create_task(web_server())
    asyncio.create_task(reminder_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        log.info("Остановка: %s", exc or "signal")