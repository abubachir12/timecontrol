"""
client.py — TimeControl Agent
Запускается на Windows ПК пользователя.
Отслеживает активные окна, отправляет статистику на сервер,
принимает команды (скриншот / выключение).
"""

import time
import os
import sys
import requests
from mss import mss
from pynput import mouse, keyboard

# ════════════════════════════════════════
#  НАСТРОЙКИ — замени IP на свой сервер
# ════════════════════════════════════════
SERVER_URL = "https://074bb2d5c5219122-185-237-220-25.serveousercontent.com"   # адрес сервера
POLL_INTERVAL  = 5     # секунд между проверками команд
SEND_MIN_SECS  = 3     # не отправлять сессии короче 3 секунд


# ════════════════════════════════════════
#  ОПРЕДЕЛЕНИЕ ОС
# ════════════════════════════════════════
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    import pygetwindow as gw


# ════════════════════════════════════════
#  AFK-ДЕТЕКТОР
# ════════════════════════════════════════
_last_activity = time.time()
_afk_limit     = 180   # будет обновляться с сервера

def _on_activity(*_):
    global _last_activity
    _last_activity = time.time()

def _is_afk() -> bool:
    return (time.time() - _last_activity) > _afk_limit

# Запускаем слушатели в фоне
try:
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
#  ПОЛУЧИТЬ ЗАГОЛОВОК АКТИВНОГО ОКНА
# ════════════════════════════════════════
def get_active_window() -> str:
    if not IS_WINDOWS:
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
def send_activity(name: str, duration: int) -> bool:
    try:
        r = requests.post(
            f"{SERVER_URL}/api/update",
            json={"name": name, "duration": duration},
            timeout=5,
            headers={
                "bypass-tunnel-reminder": "true",
                "Content-Type": "application/json",
                "User-Agent": "TimeControl-Agent/1.0"
            }
        )
        return r.status_code == 200
    except Exception:
        return False


def get_task() -> dict | None:
    try:
        r = requests.get(
            f"{SERVER_URL}/api/get_task",
            timeout=3,
            headers={
                "bypass-tunnel-reminder": "true",
                "User-Agent": "TimeControl-Agent/1.0"
            }
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("task")
    except Exception:
        pass
    return None


def complete_task(task_id: int):
    try:
        requests.post(
            f"{SERVER_URL}/api/complete_task",
            json={"id": task_id},
            timeout=3,
            headers={"bypass-tunnel-reminder": "true"}
        )
    except Exception:
        pass


def check_cancelled(task_id: int) -> bool:
    try:
        r = requests.post(
            f"{SERVER_URL}/api/check_cancel",
            json={"id": task_id},
            timeout=3,
        )
        if r.status_code == 200:
            return r.json().get("cancelled", False)
    except Exception:
        pass
    return False


def upload_screenshot() -> bool:
    path = "_tc_screen.png"
    try:
        with mss() as sct:
            sct.shot(output=path)
        with open(path, "rb") as f:
            r = requests.post(
                f"{SERVER_URL}/api/upload_screen",
                files={"file": f},
                timeout=10,
                headers={"bypass-tunnel-reminder": "true"}
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
    """Выключение с проверкой отмены каждые 5 секунд в течение 60 сек"""
    print("[CMD] Выключение через 60 секунд...")
    if IS_WINDOWS:
        os.system("shutdown /s /t 60")
    else:
        os.system("shutdown -h +1")

    # Проверяем отмену каждые 5 секунд
    for _ in range(12):
        time.sleep(5)
        if check_cancelled(task_id):
            print("[CMD] Отмена выключения получена!")
            if IS_WINDOWS:
                os.system("shutdown /a")
            else:
                os.system("shutdown -c")
            complete_task(task_id)
            return

    complete_task(task_id)


# ════════════════════════════════════════
#  ОБРАБОТКА ВХОДЯЩИХ КОМАНД
# ════════════════════════════════════════
def handle_task(task: dict):
    task_id = task["id"]
    command = task["command"]
    print(f"[CMD] Получена команда: {command} (id={task_id})")

    if command == "screenshot":
        ok = upload_screenshot()
        complete_task(task_id)
        print(f"[CMD] Скриншот {'отправлен' if ok else 'не удалось отправить'}")

    elif command == "shutdown":
        do_shutdown(task_id)   # блокирующий — выполняется в основном потоке

    else:
        print(f"[WARN] Неизвестная команда: {command}")
        complete_task(task_id)


# ════════════════════════════════════════
#  ОСНОВНОЙ ЦИКЛ
# ════════════════════════════════════════
def main():
    print("=" * 44)
    print("  TimeControl Agent запущен")
    print(f"  Сервер: {SERVER_URL}")
    print("=" * 44)

    current_window = get_active_window()
    window_start   = time.time()
    cmd_tick       = 0   # счётчик для опроса команд

    while True:
        try:
            # ── Команды с сервера ────────────────
            cmd_tick += 1
            if cmd_tick >= POLL_INTERVAL:
                cmd_tick = 0
                task = get_task()
                if task:
                    handle_task(task)

            # ── AFK — пропускаем запись ──────────
            if _is_afk():
                time.sleep(1)
                # Сбрасываем таймер окна, чтобы AFK не записывалось
                current_window = get_active_window()
                window_start   = time.time()
                continue

            # ── Трекинг активного окна ───────────
            new_window = get_active_window()

            if new_window != current_window:
                duration = int(time.time() - window_start)
                if duration >= SEND_MIN_SECS and current_window:
                    ok = send_activity(current_window, duration)
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
