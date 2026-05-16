"""
main.py — TimeControl Telegram Bot
Запускается на сервере (Ubuntu), работает 24/7 через systemd.
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
#  НАСТРОЙКИ — обязательно замени
# ══════════════════════════════════════════
API_TOKEN     = "8180368862:AAF7X17HFyhbWkPNpt2ib-13KPiR19YGAJE"   # получи у @BotFather
AGENT_FILE_ID = ""                    # вставь после загрузки .exe боту (см. гайд)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")

# ══════════════════════════════════════════
#  КОНСТАНТЫ
# ══════════════════════════════════════════
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

REMINDER_TICKS = 360   # 360 × 10 сек = 60 минут (меняется через настройки)


# ══════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════
async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS activity (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                window_name TEXT    NOT NULL,
                duration    INTEGER NOT NULL,
                date        TEXT    NOT NULL,
                timestamp   REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                status  TEXT NOT NULL DEFAULT 'pending'
            );
        """)
        defaults = [
            ("afk_seconds",       "180"),
            ("reminders_enabled", "1"),
            ("reminder_interval", "60"),
            ("work_apps",  "code,pycharm,visualstudio,word,excel,figma,photoshop,notion"),
            ("fun_apps",   "youtube,vk,telegram,steam,dota,csgo,netflix,discord,twitch"),
        ]
        for key, val in defaults:
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (key, val)
            )
        await db.commit()


async def gs(key: str) -> str:
    """get setting"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as c:
            row = await c.fetchone()
            return row[0] if row else ""


async def ss(key: str, value: str):
    """set setting"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value)
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


# ══════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📸 Скриншот"),    KeyboardButton(text="⚙️ Настройки")],
        [KeyboardButton(text="🚨 Выключить ПК"), KeyboardButton(text="❌ Отмена выключения")],
    ],
    resize_keyboard=True,
)


async def build_settings_kb() -> InlineKeyboardMarkup:
    afk_min  = int(await gs("afk_seconds") or 180) // 60
    rem_flag = await gs("reminders_enabled")
    interval = await gs("reminder_interval") or "60"
    rem_icon = "✅ Вкл" if rem_flag == "1" else "❌ Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⏰ AFK-порог: {afk_min} мин",
            callback_data="cfg_afk")],
        [InlineKeyboardButton(
            text=f"🔔 Уведомления: {rem_icon}",
            callback_data="cfg_rem")],
        [InlineKeyboardButton(
            text=f"🕑 Интервал: каждые {interval} мин",
            callback_data="cfg_interval")],
        [InlineKeyboardButton(
            text="💼 Список «Работа»",
            callback_data="cfg_work")],
        [InlineKeyboardButton(
            text="🎮 Список «Развлечения»",
            callback_data="cfg_fun")],
    ])


# ══════════════════════════════════════════
#  /start
# ══════════════════════════════════════════
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await ss("user_id", str(message.chat.id))
    text = (
        f"👋 Привет, {html.bold(message.from_user.first_name)}!\n\n"
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
    if AGENT_FILE_ID:
        try:
            await bot.send_document(
                message.chat.id, AGENT_FILE_ID,
                caption="📥 <b>TimeControl Agent</b> — запусти на ПК",
                parse_mode="HTML",
            )
        except Exception:
            pass


@dp.message(F.document)
async def recv_doc(message: types.Message):
    """Хелпер: пришли боту .exe и получишь его file_id"""
    await message.answer(
        f"✅ Файл получен!\n\nВставь в <code>AGENT_FILE_ID</code> в main.py:\n\n"
        f"<code>{message.document.file_id}</code>",
        parse_mode="HTML",
    )


# ══════════════════════════════════════════
#  СТАТИСТИКА
# ══════════════════════════════════════════
@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    today  = datetime.now().strftime("%Y-%m-%d")
    w_list = (await gs("work_apps")).split(",")
    f_list = (await gs("fun_apps")).split(",")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT window_name, SUM(duration) AS s FROM activity "
            "WHERE date=? GROUP BY window_name ORDER BY s DESC LIMIT 12",
            (today,),
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

    total    = work_s + fun_s + other_s
    th, tm   = divmod(total // 60, 60)

    if   work_s == 0 and fun_s > 0: tip = "🔴 Сегодня только развлечения — пора за дело!"
    elif fun_s > work_s * 1.5:      tip = "⚠️ Развлечений больше, чем работы. Фокус!"
    elif work_s > fun_s * 2:        tip = "💪 Боевой режим! Не забудь отдохнуть."
    else:                            tip = "✅ Отличный баланс — продолжай в том же духе!"

    body = "\n".join(lines)
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
async def add_task(command: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tasks (command, status) VALUES (?,'pending')", (command,)
        )
        await db.commit()


@dp.message(F.text == "📸 Скриншот")
async def cmd_screenshot(message: types.Message):
    await add_task("screenshot")
    await message.answer("📸 Запрос отправлен агенту. Скриншот придёт через несколько секунд...")


@dp.message(F.text == "🚨 Выключить ПК")
async def cmd_shutdown(message: types.Message):
    await add_task("shutdown")
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
    await _cancel_shutdown()
    await message.answer("✅ Команда отмены отправлена агенту на ПК.")


@dp.callback_query(F.data == "cancel_shutdown")
async def cmd_cancel_cb(callback: CallbackQuery):
    await _cancel_shutdown()
    await callback.message.edit_text("✅ Команда отмены отправлена агенту на ПК.")
    await callback.answer()


async def _cancel_shutdown():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status='cancelled' "
            "WHERE command='shutdown' AND status='pending'"
        )
        await db.commit()


# ══════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════
@dp.message(F.text == "⚙️ Настройки")
async def cmd_settings(message: types.Message):
    await message.answer(
        "⚙️ <b>Настройки</b>\nНастрой бот под себя:",
        reply_markup=await build_settings_kb(),
        parse_mode="HTML",
    )


# ── AFK ──────────────────────────────────
@dp.callback_query(F.data == "cfg_afk")
async def cb_afk(callback: CallbackQuery, state: FSMContext):
    val = int(await gs("afk_seconds") or 180) // 60
    await callback.message.answer(
        f"⏰ AFK-порог сейчас: <b>{val} мин</b>\n\n"
        "Введи новое значение <b>в минутах</b> (от 1 до 60).\n"
        "Если ты не двигаешь мышью/клавиатурой дольше этого времени — "
        "агент перестаёт записывать активность.",
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
        await ss("afk_seconds", str(mins * 60))
        await message.answer(f"✅ AFK-порог установлен: <b>{mins} мин</b>", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Введи целое число от 1 до 60.")
    await state.clear()


# ── Уведомления вкл/выкл ─────────────────
@dp.callback_query(F.data == "cfg_rem")
async def cb_rem(callback: CallbackQuery):
    cur = await gs("reminders_enabled")
    new = "0" if cur == "1" else "1"
    await ss("reminders_enabled", new)
    status = "включены ✅" if new == "1" else "выключены ❌"
    await callback.message.edit_reply_markup(reply_markup=await build_settings_kb())
    await callback.answer(f"Уведомления {status}")


# ── Интервал напоминаний ──────────────────
@dp.callback_query(F.data == "cfg_interval")
async def cb_interval(callback: CallbackQuery):
    global REMINDER_TICKS
    options = [30, 45, 60, 90, 120]
    cur = int(await gs("reminder_interval") or 60)
    idx = options.index(cur) if cur in options else 2
    new = options[(idx + 1) % len(options)]
    await ss("reminder_interval", str(new))
    REMINDER_TICKS = new * 6   # тик = 10 сек
    await callback.message.edit_reply_markup(reply_markup=await build_settings_kb())
    await callback.answer(f"Интервал: каждые {new} мин")


# ── Списки программ ───────────────────────
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
    db_key, label = LABELS[callback.data]
    curr = await gs(db_key)
    await state.set_state(Form.edit_work if db_key == "work_apps" else Form.edit_fun)
    await state.update_data(db_key=db_key)
    await callback.message.answer(
        f"{label} — текущий список:\n"
        f"<code>{curr.replace(',', ', ')}</code>\n\n"
        "📝 <b>Как редактировать:</b>\n"
        "  <code>+firefox</code> — добавить программу\n"
        "  <code>-steam</code>   — удалить программу\n"
        "  <code>сброс</code>    — вернуть по умолчанию\n\n"
        "ℹ️ Агент сравнивает <i>часть заголовка окна</i> с этим списком.",
        parse_mode="HTML",
    )
    await callback.answer()


async def _edit_list(message: types.Message, state: FSMContext):
    data   = await state.get_data()
    db_key = data.get("db_key", "work_apps")
    cmd    = message.text.strip().lower()
    apps   = [a for a in (await gs(db_key)).split(",") if a]

    if cmd == "сброс":
        apps  = DEFAULTS[db_key].split(",")
        reply = "✅ Список сброшен до значений по умолчанию."
    elif cmd.startswith("+"):
        app = cmd[1:].strip()
        if not app:
            await message.answer("❌ Укажи название после <code>+</code>", parse_mode="HTML")
            return
        if app not in apps:
            apps.append(app)
            reply = f"✅ <code>{app}</code> добавлено."
        else:
            reply = f"⚠️ <code>{app}</code> уже есть в списке."
    elif cmd.startswith("-"):
        app = cmd[1:].strip()
        if app in apps:
            apps.remove(app)
            reply = f"🗑 <code>{app}</code> удалено."
        else:
            reply = f"⚠️ <code>{app}</code> не найдено в списке."
    else:
        await message.answer(
            "❌ Формат: <code>+имя</code>, <code>-имя</code> или <code>сброс</code>",
            parse_mode="HTML",
        )
        return

    await ss(db_key, ",".join(filter(bool, apps)))
    await message.answer(
        f"{reply}\n📋 Список: <code>{','.join(filter(bool, apps))}</code>",
        parse_mode="HTML",
    )
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

        # ── Скриншот ──────────────────────
        screen = os.path.join(os.path.dirname(DB_PATH), "remote_screen.png")
        if not os.path.exists(screen):
            screen = "remote_screen.png"

        if os.path.exists(screen):
            uid = await gs("user_id")
            if uid:
                try:
                    await bot.send_photo(
                        int(uid),
                        FSInputFile(screen),
                        caption="📸 Скриншот рабочего стола",
                    )
                except Exception as e:
                    print(f"[SCREEN ERROR] {e}")
                finally:
                    try: os.remove(screen)
                    except OSError: pass

        # ── Напоминания ───────────────────
        if tick >= REMINDER_TICKS:
            tick = 0
            if await gs("reminders_enabled") == "1":
                uid = await gs("user_id")
                if uid:
                    try:
                        await bot.send_message(int(uid), random.choice(SUPPORT_TIPS))
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
