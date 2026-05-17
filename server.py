"""
server.py — TimeControl API Server
Многопользовательская версия с отслеживанием онлайн-статуса агента.
"""

import os
import time
import aiosqlite
from datetime import datetime
import uvicorn
import requests
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = "tracker.db"

# Онлайн-статус агентов: user_id -> timestamp последнего пинга
_agent_online: dict[int, float] = {}
AGENT_TIMEOUT = 60  # секунд без пинга = офлайн


import sqlite3 as _sqlite3

def _migrate():
    """Добавляет user_id в старые таблицы если их нет"""
    conn = _sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("PRAGMA table_info(activity)")
    cols = [r[1] for r in cur.fetchall()]
    if cols and "user_id" not in cols:
        cur.execute("ALTER TABLE activity ADD COLUMN user_id INTEGER DEFAULT 0")
    cur.execute("PRAGMA table_info(tasks)")
    cols = [r[1] for r in cur.fetchall()]
    if cols and "user_id" not in cols:
        cur.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER DEFAULT 0")
    conn.commit()
    conn.close()

_migrate()

app = FastAPI(title="TimeControl API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════
#  1. HEALTHCHECK
# ══════════════════════════════════════════
@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now().isoformat()}


# ══════════════════════════════════════════
#  2. ПИНГ — агент сообщает что он онлайн
# ══════════════════════════════════════════
@app.post("/api/ping")
async def agent_ping(request: Request):
    try:
        data    = await request.json()
        user_id = int(data.get("user_id", 0))
        if user_id:
            _agent_online[user_id] = time.time()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/is_online")
async def is_online(user_id: int = 0):
    last   = _agent_online.get(user_id, 0)
    online = (time.time() - last) < AGENT_TIMEOUT
    return {"online": online}


# ══════════════════════════════════════════
#  3. ПРИЁМ СТАТИСТИКИ ОТ АГЕНТА
# ══════════════════════════════════════════
@app.post("/api/update")
async def update_stats(request: Request):
    try:
        data     = await request.json()
        user_id  = int(data.get("user_id", 0))
        name     = str(data.get("name", "Неизвестно"))[:100]
        duration = int(data.get("duration", 0))

        if not user_id:
            return JSONResponse({"status": "error", "message": "user_id required"}, status_code=400)
        if duration <= 0:
            return {"status": "skipped"}

        today = datetime.now().strftime("%Y-%m-%d")
        ts    = time.time()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,)
            )
            await db.execute(
                "INSERT INTO activity (user_id, window_name, duration, date, timestamp) "
                "VALUES (?,?,?,?,?)",
                (user_id, name, duration, today, ts),
            )
            await db.commit()

        print(f"[DATA] uid={user_id} | {name[:30]} — {duration}s")
        return {"status": "ok"}
    except Exception as e:
        print(f"[ERROR] /api/update: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ══════════════════════════════════════════
#  4. ЗАДАЧИ
# ══════════════════════════════════════════
@app.get("/api/get_task")
async def get_task(user_id: int = 0):
    if not user_id:
        return {"task": None}
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, command FROM tasks "
                "WHERE user_id=? AND status='pending' LIMIT 1",
                (user_id,)
            ) as c:
                row = await c.fetchone()
        if row:
            return {"task": {"id": row[0], "command": row[1]}}
        return {"task": None}
    except Exception as e:
        return {"task": None, "error": str(e)}


@app.post("/api/complete_task")
async def complete_task(request: Request):
    try:
        data = await request.json()
        tid  = data.get("id")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE tasks SET status='completed' WHERE id=?", (tid,)
            )
            await db.commit()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/check_cancel")
async def check_cancel(request: Request):
    try:
        data = await request.json()
        tid  = data.get("id")
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT status FROM tasks WHERE id=?", (tid,)
            ) as c:
                row = await c.fetchone()
        if row and row[0] == "cancelled":
            return {"cancelled": True}
        return {"cancelled": False}
    except Exception as e:
        return {"cancelled": False, "error": str(e)}


# ══════════════════════════════════════════
#  5. СКРИНШОТ
# ══════════════════════════════════════════
@app.post("/api/upload_screen")
async def upload_screen(request: Request, file: UploadFile = File(...)):
    try:
        user_id  = request.query_params.get("user_id", "0")
        content  = await file.read()
        save_path = f"screen_{user_id}.png"
        with open(save_path, "wb") as f:
            f.write(content)
        print(f"[SCREEN] uid={user_id} ({len(content) // 1024} KB)")
        return {"status": "ok"}
    except Exception as e:
        print(f"[ERROR] /api/upload_screen: {e}")
        return JSONResponse({"status": "error"}, status_code=500)


# ══════════════════════════════════════════
#  6. СТАТИСТИКА
# ══════════════════════════════════════════
@app.get("/api/stats")
async def get_stats(user_id: int = 0):
    if not user_id:
        return JSONResponse({"error": "user_id required"}, status_code=400)
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT work_apps, fun_apps FROM users WHERE user_id=?", (user_id,)
            ) as c:
                row = await c.fetchone()
            work_list = row[0].split(",") if row and row[0] else []
            fun_list  = row[1].split(",") if row and row[1] else []

            async with db.execute(
                "SELECT window_name, SUM(duration) AS s FROM activity "
                "WHERE user_id=? AND date=? GROUP BY window_name ORDER BY s DESC",
                (user_id, today),
            ) as c:
                all_rows = await c.fetchall()

        total = work = fun = 0
        for name, dur in all_rows:
            total += dur
            low = name.lower()
            if any(w in low for w in work_list if w):  work += dur
            elif any(f in low for f in fun_list if f): fun  += dur

        h, m = divmod(total // 60, 60)
        top  = all_rows[:8]

        return {
            "total_h":   h,
            "total_m":   m,
            "work_min":  work  // 60,
            "fun_min":   fun   // 60,
            "other_min": (total - work - fun) // 60,
            "labels":    [r[0].split("—")[0].strip()[:20] for r in top],
            "values":    [r[1] // 60 for r in top],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ══════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"▶ TimeControl API запущен на порту {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)