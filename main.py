"""
main.py — TimeControl Telegram Bot
Многопользовательская версия — каждый видит свою статистику.
"""

import asyncio
import aiosqlite
import os
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ══════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════
API_TOKEN     = "8180368862:AAF7X17HFyhbWkPNpt2ib-13KPiR19YGAJE"
AGENT_FILE_ID = "BQACAgIAAxkBAAIDlmoIp98EmlZ47riWYSRwL-dfTKUsAAKHrQACqpNISO-_rSYl4LAYOwQ"
DB_PATH       = "tracker.db"

SUPPORT_TIPS = [
    "💧 Выпей стакан воды — твоё тело скажет спасибо!",
    "👀 Посмотри вдаль 20 секунд — дай отдохнуть глазам.",
    "🧘 Сделай пару глубоких вдохов и расслабь плечи.",
    "🚶 Встань и пройдись хотя бы минуту — разгони кровь.",
    "🤸 Потяни шею и спину — это важно при долгой работе!",
    "☕ Самое время сделать паузу — выпей чай или кофе.",
    "🌿 Открой окно и проветри комнату — мозгу нужен кислород!",
    "🕐 Работаешь больше часа? Сделай 5-минутный перерыв.",
]

REMINDER_TICKS = 360


# ══════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS activity (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                window_name TEXT    NOT NULL,
                duration    INTEGER NOT NULL,
                date        TEXT    NOT NULL,
                timestamp   REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                afk_seconds INTEGER DEFAULT 180,
                reminders   INTEGER DEFAULT 1,
                reminder_interval INTEGER DEFAULT 60,
                work_apps   TEXT DEFAULT 'code,pycharm,visualstudio,word,excel,figma,photoshop,notion',
                fun_apps    TEXT DEFAULT 'youtube,vk,telegram,steam,dota,csgo,netflix,discord,twitch'
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                command     TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending'
            );
        """)
        await db.commit()


async def ensure_user(user_id: int, username: str = "", first_name: str = ""):
    """Создаёт пользователя если его нет"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?,?,?)",
            (user_id, username, first_name)
        )
        await db.commit()


async def get_user(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
            if not row:
                return {}
            cols = [d[0] for d in c.description]
            return dict(zip(cols, row))


async def set_user(user_id: int, key: str, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE users SET {key}=? WHERE user_id=?", (value, user_id)
        )
        await db.commit()


# ══════════════════════════════════════════
#  БОТ
# ══════════════════════════════════════════
bot = Bot(token=API_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


class Form(StatesGroup):
    edit_work = State()
    edit_fun  = State()
    set_afk   = State()


MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📸 Скриншот"),    KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="🚨 Выключить ПК"), KeyboardButton(text="❌ Отмена выключения")],
    ],
    resize_keyboard=True,
)


async def build_settings_kb(user_id: int) -> InlineKeyboardMarkup:
    u = await get_user(user_id)
    afk_min  = (u.get("afk_seconds") or 180) // 60
    rem_flag = u.get("reminders", 1)
    interval = u.get("reminder_interval", 60)
    rem_icon = "✅ Вкл" if rem_flag == 1 else "❌ Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏰ AFK-порог: {afk_min} мин",          callback_data="cfg_afk")],
        [InlineKeyboardButton(text=f"🔔 Уведомления: {rem_icon}",            callback_data="cfg_rem")],
        [InlineKeyboardButton(text=f"🕑 Интервал: каждые {interval} мин",   callback_data="cfg_interval")],
        [InlineKeyboardButton(text="💼 Список «Работа»",                     callback_data="cfg_work")],
        [InlineKeyboardButton(text="🎮 Список «Развлечения»",                callback_data="cfg_fun")],
    ])


# ══════════════════════════════════════════
#  /start
# ══════════════════════════════════════════
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid   = message.from_user.id
    name  = message.from_user.first_name or ""
    uname = message.from_user.username or ""
    await ensure_user(uid, uname, name)

    text = (
        f"👋 Привет, {html.bold(name)}!\n\n"
        "Я — <b>TimeControl</b>, твой ассистент продуктивности.\n\n"
        "🔍 <b>Что умею:</b>\n"
        "  • Показываю время в каждом приложении\n"
        "  • Делаю скриншот по команде\n"
        "  • Выключаю ПК удалённо\n"
        "  • Напоминаю о перерывах и воде\n"
        "  • Анализирую баланс работы и отдыха\n\n"
        "🚀 <b>Как начать:</b>\n"
        "1️⃣ Скачай агента ниже\n"
        "2️⃣ Запусти на своём ПК\n"
        "3️⃣ Пользуйся кнопками!"
    )
    await message.answer(text, reply_markup=MAIN_KB, parse_mode="HTML")

    # Отправляем ID пользователю
    await message.answer(
        f"🆔 Твой Telegram ID для агента: <code>{uid}</code>\n"
        f"Скопируй его и вставь при первом запуске агента на ПК.",
        parse_mode="HTML"
    )

    if AGENT_FILE_ID:
        try:
            await bot.send_document(
                uid, AGENT_FILE_ID,
                caption="📥 <b>TimeControl Agent</b> — запусти на ПК",
                parse_mode="HTML",
            )
        except Exception:
            pass


@dp.message(F.document)
async def recv_doc(message: types.Message):
    await message.answer(
        f"✅ Файл получен!\n\nВставь в <code>AGENT_FILE_ID</code>:\n\n"
        f"<code>{message.document.file_id}</code>",
        parse_mode="HTML",
    )


# ══════════════════════════════════════════
#  СТАТИСТИКА
# ══════════════════════════════════════════
@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    uid   = message.from_user.id
    await ensure_user(uid)
    u     = await get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")

    w_list = (u.get("work_apps") or "").split(",")
    f_list = (u.get("fun_apps")  or "").split(",")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT window_name, SUM(duration) AS s FROM activity "
            "WHERE user_id=? AND date=? GROUP BY window_name ORDER BY s DESC LIMIT 12",
            (uid, today),
        ) as c:
            rows = await c.fetchall()

    if not rows:
        await message.answer(
            "📭 За сегодня данных нет.\n"
            "Убедись, что агент запущен на ПК!"
        )
        return

    work_s = fun_s = other_s = 0
    lines  = []
    for name, dur in rows:
        low  = name.lower()
        mins = dur // 60
        clean = name.split("—")[0].split("-")[0].strip()[:30].capitalize()
        lines.append(f"  ▸ {html.bold(clean)} — {mins} мин.")
        if any(w in low for w in w_list if w):   work_s  += dur
        elif any(f in low for f in f_list if f): fun_s   += dur
        else:                                     other_s += dur

    total  = work_s + fun_s + other_s
    th, tm = divmod(total // 60, 60)

    if   work_s == 0 and fun_s > 0: tip = "🔴 Сегодня только развлечения — пора за дело!"
    elif fun_s > work_s * 1.5:      tip = "⚠️ Развлечений больше, чем работы. Фокус!"
    elif work_s > fun_s * 2:        tip = "💪 Боевой режим! Не забудь отдохнуть."
    else:                            tip = "✅ Отличный баланс — продолжай в том же духе!"

    body   = "\n".join(lines)
    report = (
        f"📊 <b>АКТИВНОСТЬ — {today}</b>\n"
        f"{'─' * 28}\n"
        f"⏱ Всего: <b>{th}ч {tm}мин</b>\n"
        f"💼 Работа: <b>{work_s // 60} мин</b>  "
        f"🎮 Отдых: <b>{fun_s // 60} мин</b>  "
        f"⚙️ Прочее: <b>{other_s // 60} мин</b>\n"
        f"{'─' * 28}\n"
        f"{body}\n"
        f"{'─' * 28}\n"
        f"💡 <i>{tip}</i>"
    )
    await message.answer(report, parse_mode="HTML")


# ══════════════════════════════════════════
#  СКРИНШОТ / ВЫКЛЮЧЕНИЕ
# ══════════════════════════════════════════
async def add_task(user_id: int, command: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (user_id, command, status) VALUES (?,?,'pending')",
            (user_id, command)
        )
        await db.commit()


@dp.message(F.text == "📸 Скриншот")
async def cmd_screenshot(message: types.Message):
    await ensure_user(message.from_user.id)
    await add_task(message.from_user.id, "screenshot")
    await message.answer("📸 Запрос отправлен агенту. Скриншот придёт через несколько секунд...")


@dp.message(F.text == "🚨 Выключить ПК")
async def cmd_shutdown(message: types.Message):
    await ensure_user(message.from_user.id)
    await add_task(message.from_user.id, "shutdown")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Быстрая отмена", callback_data="cancel_shutdown")
    ]])
    await message.answer(
        "🚨 Команда выключения отправлена.\n"
        "⏳ ПК выключится через <b>60 секунд</b>.",
        reply_markup=kb, parse_mode="HTML",
    )


@dp.message(F.text == "❌ Отмена выключения")
async def cmd_cancel_text(message: types.Message):
    await _cancel_shutdown(message.from_user.id)
    await message.answer("✅ Команда отмены отправлена агенту на ПК.")


@dp.callback_query(F.data == "cancel_shutdown")
async def cmd_cancel_cb(callback: CallbackQuery):
    await _cancel_shutdown(callback.from_user.id)
    await callback.message.edit_text("✅ Команда отмены отправлена агенту на ПК.")
    await callback.answer()


async def _cancel_shutdown(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status='cancelled' "
            "WHERE user_id=? AND command='shutdown' AND status='pending'",
            (user_id,)
        )
        await db.commit()


# ══════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════
@dp.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: types.Message):
    uid = message.from_user.id
    await ensure_user(uid)
    await message.answer(
        "⚙️ <b>Настройки</b>\nНастрой бот под себя:",
        reply_markup=await build_settings_kb(uid),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "cfg_afk")
async def cb_afk(callback: CallbackQuery, state: FSMContext):
    u = await get_user(callback.from_user.id)
    val = (u.get("afk_seconds") or 180) // 60
    await callback.message.answer(
        f"⏰ AFK-порог сейчас: <b>{val} мин</b>\n\n"
        "Введи новое значение в минутах (от 1 до 60):",
        parse_mode="HTML",
    )
    await state.set_state(Form.set_afk)
    await callback.answer()


@dp.message(Form.set_afk)
async def input_afk(message: types.Message, state: FSMContext):
    try:
        mins = int(message.text.strip())
        if not 1 <= mins <= 60:
            raise ValueError
        await set_user(message.from_user.id, "afk_seconds", mins * 60)
        await message.answer(f"✅ AFK-порог: <b>{mins} мин</b>", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введи число от 1 до 60.")
    await state.clear()


@dp.callback_query(F.data == "cfg_rem")
async def cb_rem(callback: CallbackQuery):
    uid = callback.from_user.id
    u   = await get_user(uid)
    new = 0 if u.get("reminders", 1) == 1 else 1
    await set_user(uid, "reminders", new)
    status = "включены ✅" if new == 1 else "выключены ❌"
    await callback.message.edit_reply_markup(reply_markup=await build_settings_kb(uid))
    await callback.answer(f"Уведомления {status}")


@dp.callback_query(F.data == "cfg_interval")
async def cb_interval(callback: CallbackQuery):
    uid     = callback.from_user.id
    u       = await get_user(uid)
    options = [30, 45, 60, 90, 120]
    cur     = u.get("reminder_interval", 60)
    idx     = options.index(cur) if cur in options else 2
    new     = options[(idx + 1) % len(options)]
    await set_user(uid, "reminder_interval", new)
    await callback.message.edit_reply_markup(reply_markup=await build_settings_kb(uid))
    await callback.answer(f"Интервал: каждые {new} мин")


DEFAULTS = {
    "work_apps": "code,pycharm,visualstudio,word,excel,figma,photoshop,notion",
    "fun_apps":  "youtube,vk,telegram,steam,dota,csgo,netflix,discord,twitch",
}
LABELS = {
    "cfg_work": ("work_apps", "💼 Работа"),
    "cfg_fun":  ("fun_apps",  "🎮 Развлечения"),
}


@dp.callback_query(F.data.in_(["cfg_work", "cfg_fun"]))
async def cb_edit_list(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    db_key, label = LABELS[callback.data]
    u    = await get_user(uid)
    curr = u.get(db_key) or DEFAULTS[db_key]
    await state.set_state(Form.edit_work if db_key == "work_apps" else Form.edit_fun)
    await state.update_data(db_key=db_key)
    await callback.message.answer(
        f"{label} — текущий список:\n"
        f"<code>{curr.replace(',', ', ')}</code>\n\n"
        "  <code>+firefox</code> — добавить\n"
        "  <code>-steam</code>   — удалить\n"
        "  <code>сброс</code>    — по умолчанию",
        parse_mode="HTML",
    )
    await callback.answer()


async def _edit_list(message: types.Message, state: FSMContext):
    uid    = message.from_user.id
    data   = await state.get_data()
    db_key = data.get("db_key", "work_apps")
    cmd    = message.text.strip().lower()
    u      = await get_user(uid)
    apps   = [a for a in (u.get(db_key) or DEFAULTS[db_key]).split(",") if a]

    if cmd == "сброс":
        apps  = DEFAULTS[db_key].split(",")
        reply = "✅ Список сброшен."
    elif cmd.startswith("+"):
        app = cmd[1:].strip()
        if app and app not in apps:
            apps.append(app)
            reply = f"✅ <code>{app}</code> добавлено."
        else:
            reply = f"⚠️ Уже есть или пустое название."
    elif cmd.startswith("-"):
        app = cmd[1:].strip()
        if app in apps:
            apps.remove(app)
            reply = f"🗑 <code>{app}</code> удалено."
        else:
            reply = f"⚠️ Не найдено."
    else:
        await message.answer("❌ Формат: <code>+имя</code>, <code>-имя</code> или <code>сброс</code>", parse_mode="HTML")
        return

    await set_user(uid, db_key, ",".join(filter(bool, apps)))
    await message.answer(f"{reply}\n📋 <code>{','.join(filter(bool, apps))}</code>", parse_mode="HTML")
    await state.clear()


@dp.message(Form.edit_work)
async def input_work(m: types.Message, s: FSMContext): await _edit_list(m, s)

@dp.message(Form.edit_fun)
async def input_fun(m: types.Message, s: FSMContext):  await _edit_list(m, s)


# ══════════════════════════════════════════
#  ФОНОВЫЕ ЗАДАЧИ
# ══════════════════════════════════════════
async def bg_worker():
    tick = 0
    while True:
        await asyncio.sleep(10)
        tick += 1

        # Скриншоты для каждого пользователя
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT DISTINCT user_id FROM users") as c:
                user_ids = [r[0] for r in await c.fetchall()]

        for uid in user_ids:
            screen = f"screen_{uid}.png"
            if os.path.exists(screen):
                try:
                    await bot.send_photo(
                        uid,
                        FSInputFile(screen),
                        caption="📸 Скриншот рабочего стола",
                    )
                except Exception as e:
                    print(f"[SCREEN ERROR] uid={uid}: {e}")
                finally:
                    try: os.remove(screen)
                    except OSError: pass

        # Напоминания
        if tick >= REMINDER_TICKS:
            tick = 0
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT user_id, reminders FROM users WHERE reminders=1"
                ) as c:
                    remind_users = [r[0] for r in await c.fetchall()]
            for uid in remind_users:
                try:
                    await bot.send_message(uid, random.choice(SUPPORT_TIPS))
                except Exception:
                    pass


# ══════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════
async def main():
    await init_db()
    asyncio.create_task(bg_worker())
    await bot.delete_webhook(drop_pending_updates=True)
    print("▶ TimeControl Bot запущен")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
