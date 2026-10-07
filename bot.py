import asyncio
from datetime import datetime, date, timedelta
from typing import List
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "ВАШ_ТОКЕН"
ADMIN_IDS = [123456789]  # Укажите ваш Telegram ID (число)
DB_NAME = "salon_booking.db"

# Рабочие слоты мастера
WORK_HOURS = ["10:00", "11:30", "13:00", "14:30", "16:00", "17:30", "19:00"]

SERVICES = {
    1: {"name": "Стрижка и укладка", "price": 2000, "duration": 60},
    2: {"name": "Окрашивание волос", "price": 4500, "duration": 120},
    3: {"name": "Маникюр с покрытием", "price": 1800, "duration": 90},
    4: {"name": "Массаж лица", "price": 2500, "duration": 60},
}


# ==================== БАЗА ДАННЫХ ====================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                user_phone TEXT NOT NULL,
                service_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                date_str TEXT NOT NULL,
                time_str TEXT NOT NULL,
                status TEXT DEFAULT 'confirmed',
                reminder_sent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def get_booked_times(date_str: str) -> List[str]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT time_str FROM appointments WHERE date_str = ? AND status = 'confirmed'",
            (date_str,)
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def save_booking(user_id: int, user_name: str, phone: str, service_id: int, date_str: str, time_str: str) -> int:
    service_name = SERVICES[service_id]["name"]
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            INSERT INTO appointments (user_id, user_name, user_phone, service_id, service_name, date_str, time_str)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, user_name, phone, service_id, service_name, date_str, time_str))
        await db.commit()
        return cursor.lastrowid


# ==================== СОСТОЯНИЯ (FSM) ====================
class BookingFSM(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    entering_name = State()
    entering_phone = State()


class AdminCancelFSM(StatesGroup):
    waiting_for_id = State()


# ==================== КЛАВИАТУРЫ ====================
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗓 Записаться на визит")],
            [KeyboardButton(text="📋 Мои записи"), KeyboardButton(text="ℹ️ О нас / Прайс")]
        ],
        resize_keyboard=True
    )


def services_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"{item['name']} — {item['price']} ₽",
            callback_data=f"service:{sid}"
        )]
        for sid, item in SERVICES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def dates_keyboard() -> InlineKeyboardMarkup:
    today = date.today()
    buttons = []
    # Выводим ближайшие 7 дней
    for i in range(7):
        target_date = today + timedelta(days=i)
        date_str = target_date.strftime("%Y-%m-%d")
        day_caption = target_date.strftime("%d.%m (%a)")
        buttons.append([InlineKeyboardButton(text=day_caption, callback_data=f"date:{date_str}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def times_keyboard(selected_date_str: str) -> InlineKeyboardMarkup:
    booked = await get_booked_times(selected_date_str)
    now = datetime.now()
    is_today = selected_date_str == now.strftime("%Y-%m-%d")

    row = []
    keyboard = []
    for slot in WORK_HOURS:
        if slot in booked:
            continue

        # Если запись на сегодня, исключаем уже прошедшие часы
        if is_today:
            slot_hour, slot_min = map(int, slot.split(":"))
            slot_time = now.replace(hour=slot_hour, minute=slot_min, second=0)
            if slot_time <= now:
                continue

        row.append(InlineKeyboardButton(text=slot, callback_data=f"time:{slot}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к датам", callback_data="back_to_dates")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def phone_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить контакт", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# ==================== ХЭНДЛЕРЫ КЛИЕНТА ====================
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        f"Здравствуйте, {message.from_user.first_name}! 👋\n"
        "Добро пожаловать в сервис онлайн-записи.\n"
        "Выберите действие ниже:",
        reply_markup=main_menu_keyboard()
    )


@dp.message(F.text == "ℹ️ О нас / Прайс")
async def show_info(message: Message):
    text = "💈 <b>Наши услуги и цены:</b>\n\n"
    for item in SERVICES.values():
        text += f"• <b>{item['name']}</b> ({item['duration']} мин) — {item['price']} ₽\n"
    text += "\n📍 Адрес: ул. Примерная, 10\n⏰ Часы работы: 10:00 — 20:00"
    await message.answer(text, parse_mode="HTML")


@dp.message(F.text == "🗓 Записаться на визит")
async def start_booking(message: Message, state: FSMContext):
    await state.set_state(BookingFSM.choosing_service)
    await message.answer("Выберите интересующую услугу:", reply_markup=services_keyboard())


@dp.callback_query(BookingFSM.choosing_service, F.data.startswith("service:"))
async def process_service(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)
    await state.set_state(BookingFSM.choosing_date)
    await callback.message.edit_text("Выберите дату посещения:", reply_markup=dates_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "back_to_dates")
async def back_to_dates(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BookingFSM.choosing_date)
    await callback.message.edit_text("Выберите дату посещения:", reply_markup=dates_keyboard())
    await callback.answer()


@dp.callback_query(BookingFSM.choosing_date, F.data.startswith("date:"))
async def process_date(callback: CallbackQuery, state: FSMContext):
    selected_date = callback.data.split(":")[1]
    await state.update_data(selected_date=selected_date)

    kb = await times_keyboard(selected_date)
    if not kb.inline_keyboard or (len(kb.inline_keyboard) == 1 and kb.inline_keyboard[0][0].callback_data == "back_to_dates"):
        await callback.message.edit_text(
            f"К сожалению, на {selected_date} нет свободных окон. Пожалуйста, выберите другой день:",
            reply_markup=dates_keyboard()
        )
    else:
        await state.set_state(BookingFSM.choosing_time)
        await callback.message.edit_text(f"Свободные окна на {selected_date}:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(BookingFSM.choosing_time, F.data.startswith("time:"))
async def process_time(callback: CallbackQuery, state: FSMContext):
    selected_time = callback.data.split(":", 1)[1]
    await state.update_data(selected_time=selected_time)

    await state.set_state(BookingFSM.entering_name)
    await callback.message.delete()
    await callback.message.answer(
        "Введите ваше <b>Имя и Фамилию</b>:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    await callback.answer()


@dp.message(BookingFSM.entering_name, F.text)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text.strip())
    await state.set_state(BookingFSM.entering_phone)
    await message.answer(
        "Нажмите кнопку ниже, чтобы отправить номер телефона, или введите его вручную:",
        reply_markup=phone_request_keyboard()
    )


@dp.message(BookingFSM.entering_phone)
async def process_phone(message: Message, state: FSMContext, bot: Bot):
    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()
    else:
        await message.answer("Пожалуйста, отправьте контакт или введите корректный номер телефона.")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    service_id = data["service_id"]
    date_str = data["selected_date"]
    time_str = data["selected_time"]
    client_name = data["client_name"]

    # Двойная проверка занятости перед подтверждением
    booked_slots = await get_booked_times(date_str)
    if time_str in booked_slots:
        await message.answer(
            "⚠️ К сожалению, этот слот только что заняли. Пожалуйста, выберите другое время.",
            reply_markup=main_menu_keyboard()
        )
        await state.clear()
        return

    booking_id = await save_booking(user_id, client_name, phone, service_id, date_str, time_str)
    service_name = SERVICES[service_id]["name"]
    price = SERVICES[service_id]["price"]

    await state.clear()

    # Сообщение клиенту
    await message.answer(
        f"✅ <b>Вы успешно записаны!</b>\n\n"
        f"🔖 <b>Номер записи:</b> #{booking_id}\n"
        f"💇 <b>Услуга:</b> {service_name}\n"
        f"📅 <b>Дата:</b> {date_str}\n"
        f"⏰ <b>Время:</b> {time_str}\n"
        f"💵 <b>Стоимость:</b> {price} ₽\n\n"
        f"Мы напомним вам о визите за 1 день. До встречи!",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

    # Уведомление администраторам
    admin_alert = (
        f"🔔 <b>Новая запись #{booking_id}!</b>\n\n"
        f"👤 Клиент: {client_name}\n"
        f"📞 Телефон: {phone}\n"
        f"💇 Услуга: {service_name}\n"
        f"📅 Дата: {date_str} {time_str}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_alert, parse_mode="HTML")
        except Exception:
            pass


@dp.message(F.text == "📋 Мои записи")
async def show_user_bookings(message: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, service_name, date_str, time_str, status
            FROM appointments
            WHERE user_id = ? AND status = 'confirmed' AND date_str >= date('now')
            ORDER BY date_str, time_str
        """, (message.from_user.id,))
        rows = await cursor.fetchall()

    if not rows:
        await message.answer("У вас нет активных записей.")
        return

    text = "📋 <b>Ваши актуальные записи:</b>\n\n"
    for r in rows:
        text += f"• <b>#{r[0]}</b> | {r[1]} — {r[2]} в {r[3]}\n"
    await message.answer(text, parse_mode="HTML")


# ==================== ПАНЕЛЬ АДМИНИСТРАТОРА ====================
def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Записи на сегодня", callback_data="admin_today")],
            [InlineKeyboardButton(text="📆 Все активные записи", callback_data="admin_all")],
            [InlineKeyboardButton(text="❌ Отменить запись", callback_data="admin_cancel")]
        ]
    )


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("У вас нет прав доступа к этой команде.")
        return
    await message.answer("Панель управления администратора:", reply_markup=admin_keyboard())


@dp.callback_query(F.data == "admin_today")
async def admin_schedule_today(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    today_str = date.today().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, user_name, user_phone, service_name, time_str
            FROM appointments
            WHERE date_str = ? AND status = 'confirmed'
            ORDER BY time_str
        """, (today_str,))
        rows = await cursor.fetchall()

    if not rows:
        await callback.message.edit_text(f"На сегодня ({today_str}) записей нет.", reply_markup=admin_keyboard())
        return

    text = f"📅 <b>Расписание на сегодня ({today_str}):</b>\n\n"
    for r in rows:
        text += f"⏰ <b>{r[4]}</b> | #{r[0]} | {r[1]} ({r[2]}) — <i>{r[3]}</i>\n"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "admin_all")
async def admin_schedule_all(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    today_str = date.today().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, user_name, user_phone, service_name, date_str, time_str
            FROM appointments
            WHERE date_str >= ? AND status = 'confirmed'
            ORDER BY date_str, time_str
        """, (today_str,))
        rows = await cursor.fetchall()

    if not rows:
        await callback.message.edit_text("Нет предстоящих записей.", reply_markup=admin_keyboard())
        return

    text = "📆 <b>Предстоящие записи:</b>\n\n"
    for r in rows:
        text += f"• <b>#{r[0]}</b> | {r[4]} {r[5]} — {r[1]} ({r[2]}), <i>{r[3]}</i>\n"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "admin_cancel")
async def admin_request_cancel_id(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminCancelFSM.waiting_for_id)
    await callback.message.answer("Введите <b>ID записи</b> для отмены (например: 1):", parse_mode="HTML")
    await callback.answer()


@dp.message(AdminCancelFSM.waiting_for_id, F.text)
async def admin_cancel_booking(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введите корректный числовой ID.")
        return

    booking_id = int(message.text)
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT user_id, user_name, service_name, date_str, time_str
            FROM appointments
            WHERE id = ? AND status = 'confirmed'
        """, (booking_id,))
        row = await cursor.fetchone()

        if not row:
            await message.answer(f"Активная запись #{booking_id} не найдена.")
            await state.clear()
            return

        user_id, client_name, service_name, date_str, time_str = row
        await db.execute("UPDATE appointments SET status = 'cancelled' WHERE id = ?", (booking_id,))
        await db.commit()

    await state.clear()
    await message.answer(f"✅ Запись #{booking_id} успешно отменена, слот освобожден.")

    # Оповещение клиента об отмене
    try:
        await bot.send_message(
            user_id,
            f"⚠️ <b>Ваша запись отменена администратором</b>\n\n"
            f"Запись #{booking_id} на услугу «{service_name}» ({date_str} в {time_str}) была отменена.\n"
            f"Для переноса или новой записи воспользуйтесь меню бота.",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ==================== НАПОМИНАНИЯ (ЗА 1 ДЕНЬ) ====================
async def check_and_send_reminders(bot: Bot):
    """Каждый час проверяет записи на завтра и шлет пуши клиентам."""
    tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, user_id, service_name, time_str
            FROM appointments
            WHERE date_str = ? AND status = 'confirmed' AND reminder_sent = 0
        """, (tomorrow_str,))
        records = await cursor.fetchall()

        for b_id, u_id, s_name, t_str in records:
            try:
                await bot.send_message(
                    u_id,
                    f"🔔 <b>Напоминание о записи!</b>\n\n"
                    f"Завтра ({tomorrow_str}) в <b>{t_str}</b> вы записаны на <b>{s_name}</b>.\n"
                    f"Пожалуйста, сообщите, если ваши планы изменятся.",
                    parse_mode="HTML"
                )
                await db.execute("UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (b_id,))
            except Exception:
                pass
        await db.commit()


# ==================== ТОЧКА ВХОДА ====================
async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN)

    # Инициализация планировщика
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(check_and_send_reminders, "interval", hours=1, args=[bot])
    scheduler.start()

    print("Бот успешно запущен!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())