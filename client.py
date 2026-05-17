"""
client.py — TimeControl Agent
Многопользовательская версия с пингом сервера.
Напоминания приходят только пока агент активен.
"""

import time
import os
import sys
import requests

# ════════════════════════════════════════
#  НАСТРОЙКИ
# ════════════════════════════════════════
SERVER_URL    = "https://web-production-583b7.up.railway.app"
POLL_INTERVAL = 5    # тиков между проверкой команд
PING_INTERVAL = 30   # тиков между пингами (30 × 1 сек = 30 сек)
SEND_MIN_SECS = 3    # минимальная длина сессии для отправки

# ════════════════════════════════════════
#  ПОЛУЧИТЬ USER_ID
# ════════════════════════════════════════
ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_id.txt")

def get_user_id() -> int:
    if os.path.exists(ID_FILE):
        try:
            uid = int(open(ID_FILE).read().strip())
            if uid > 0:
                return uid
        except Exception:
            pass

    print("=" * 44)
    print("  Первый запуск TimeControl Agent")
    print("=" * 44)
    print()
    print("1. Открой Telegram")
    print("2. Напиши боту /start")
    print("3. Бот пришлёт твой ID (число)")
    print()
    while True:
        try:
            uid = int(input("Введи свой Telegram ID: ").strip())
            if uid > 0:
                open(ID_FILE, "w").write(str(uid))
                print(f"✅ ID сохранён: {uid}")
                return uid
        except ValueError:
            print("❌ Введи число!")

USER_ID = get_user_id()

# ════════════════════════════════════════
#  ОПРЕДЕЛЕНИЕ ОС
# ════════════════════════════════════════
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        import pygetwindow as gw
        GW_OK = True
    except Exception:
        GW_OK = False
else:
    GW_OK = False

try:
    from mss import MSS as mss_lib
    MSS_OK = True
except ImportError:
    try:
        from mss import mss as mss_lib
        MSS_OK = True
    except Exception:
        MSS_OK = False

# ════════════════════════════════════════
#  AFK-ДЕТЕКТОР
# ════════════════════════════════════════
_last_activity = time.time()
_afk_limit     = 180

def _on_activity(*_):
    global _last_activity
    _last_activity = time.time()

def _is_afk() -> bool:
    return (time.time() - _last_activity) > _afk_limit

try:
    from pynput import mouse, keyboard
    mouse.Listener(
        on_move=_on_activity,
        on_click=_on_activity,
        on_scroll=_on_activity,
        daemon=True,
    ).start()
    keyboard.Listener(on_press=_on_activity, daemon=True).start()
except Exception as e:
    print(f"[WARN] Слушатели ввода не запустились: {e}")


# ════════════════════════════════════════
#  АКТИВНОЕ ОКНО
# ════════════════════════════════════════
def get_active_window() -> str:
    if not GW_OK:
        return "Неизвестно"
    try:
        w = gw.getActiveWindow()
        if w and w.title and w.title.strip():
            return w.title.strip()[:100]
    except Exception:
        pass
    return "Система"


# ════════════════════════════════════════
#  СВЯЗЬ С СЕРВЕРОМ
# ════════════════════════════════════════
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent":   "TimeControl-Agent/2.0",
}


def send_ping():
    """Сообщаем серверу что агент онлайн"""
    try:
        requests.post(
            f"{SERVER_URL}/api/ping",
            json={"user_id": USER_ID},
            timeout=3,
            headers=HEADERS,
        )
    except Exception:
        pass


def send_activity(name: str, duration: int) -> bool:
    try:
        r = requests.post(
            f"{SERVER_URL}/api/update",
            json={"user_id": USER_ID, "name": name, "duration": duration},
            timeout=5,
            headers=HEADERS,
        )
        return r.status_code == 200
    except Exception:
        return False


def get_task() -> dict | None:
    try:
        r = requests.get(
            f"{SERVER_URL}/api/get_task",
            params={"user_id": USER_ID},
            timeout=3,
            headers=HEADERS,
        )
        if r.status_code == 200:
            try:
                return r.json().get("task")
            except Exception:
                return None
    except Exception:
        pass
    return None


def complete_task(task_id: int):
    try:
        requests.post(
            f"{SERVER_URL}/api/complete_task",
            json={"id": task_id},
            timeout=3,
            headers=HEADERS,
        )
    except Exception:
        pass


def check_cancelled(task_id: int) -> bool:
    try:
        r = requests.post(
            f"{SERVER_URL}/api/check_cancel",
            json={"id": task_id},
            timeout=3,
            headers=HEADERS,
        )
        if r.status_code == 200:
            return r.json().get("cancelled", False)
    except Exception:
        pass
    return False


def upload_screenshot() -> bool:
    if not MSS_OK:
        print("[ERROR] mss не установлен")
        return False
    path = "_tc_screen.png"
    try:
        with mss_lib() as sct:
            sct.shot(output=path)
        with open(path, "rb") as f:
            r = requests.post(
                f"{SERVER_URL}/api/upload_screen",
                params={"user_id": USER_ID},
                files={"file": ("screen.png", f, "image/png")},
                timeout=15,
            )
        return r.status_code == 200
    except Exception as e:
        print(f"[ERROR] Скриншот: {e}")
        return False
    finally:
        if os.path.exists(path):
            try: os.remove(path)
            except OSError: pass


def do_shutdown(task_id: int):
    print("[CMD] Выключение через 60 секунд...")
    if IS_WINDOWS:
        os.system("shutdown /s /t 60")
    else:
        os.system("shutdown -h +1")
    for _ in range(12):
        time.sleep(5)
        if check_cancelled(task_id):
            print("[CMD] Отмена выключения!")
            if IS_WINDOWS:
                os.system("shutdown /a")
            else:
                os.system("shutdown -c")
            complete_task(task_id)
            return
    complete_task(task_id)


def handle_task(task: dict):
    task_id = task["id"]
    command = task["command"]
    print(f"[CMD] Команда: {command} (id={task_id})")
    if command == "screenshot":
        ok = upload_screenshot()
        complete_task(task_id)
        print(f"[CMD] Скриншот {'✓' if ok else '✗'}")
    elif command == "shutdown":
        do_shutdown(task_id)
    else:
        complete_task(task_id)


# ════════════════════════════════════════
#  ОСНОВНОЙ ЦИКЛ
# ════════════════════════════════════════
def main():
    print("=" * 44)
    print("  TimeControl Agent запущен")
    print(f"  Сервер:  {SERVER_URL}")
    print(f"  User ID: {USER_ID}")
    print("=" * 44)

    current_window = get_active_window()
    window_start   = time.time()
    cmd_tick       = 0
    ping_tick      = 0

    # Первый пинг сразу при запуске
    send_ping()

    while True:
        try:
            cmd_tick  += 1
            ping_tick += 1

            # ── Пинг сервера ─────────────────────
            if ping_tick >= PING_INTERVAL:
                ping_tick = 0
                send_ping()

            # ── Команды с сервера ─────────────────
            if cmd_tick >= POLL_INTERVAL:
                cmd_tick = 0
                task = get_task()
                if task:
                    handle_task(task)

            # ── AFK ───────────────────────────────
            if _is_afk():
                time.sleep(1)
                current_window = get_active_window()
                window_start   = time.time()
                continue

            # ── Трекинг окон ──────────────────────
            new_window = get_active_window()
            if new_window != current_window:
                duration = int(time.time() - window_start)
                if duration >= SEND_MIN_SECS and current_window:
                    ok     = send_activity(current_window, duration)
                    status = "✓" if ok else "✗"
                    print(f"[{status}] {current_window[:40]} — {duration}s")
                current_window = new_window
                window_start   = time.time()

            time.sleep(1)

        except KeyboardInterrupt:
            print("\n[STOP] Агент остановлен.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()