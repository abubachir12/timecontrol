"""
server.py — TimeControl API Server
Принимает данные от агента, раздаёт задачи, отдаёт статистику.
Запускается вместе с main.py через systemd.
"""

import os
import aiosqlite
import time
from datetime import datetime
import uvicorn
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = "/root/timecontrol/tracker.db"

app = FastAPI(title="TimeControl API", version="1.0")

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
#  2. ПРИЁМ СТАТИСТИКИ ОТ АГЕНТА
# ══════════════════════════════════════════
@app.post("/api/update")
async def update_stats(request: Request):
    try:
        data     = await request.json()
        name     = str(data.get("name", "Неизвестно"))[:100]
        duration = int(data.get("duration", 0))

        if duration <= 0:
            return {"status": "skipped"}

        today = datetime.now().strftime("%Y-%m-%d")
        ts    = time.time()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO activity (window_name, duration, date, timestamp) "
                "VALUES (?,?,?,?)",
                (name, duration, today, ts),
            )
            await db.commit()

        print(f"[DATA] {name[:40]} — {duration}s")
        return {"status": "ok"}
    except Exception as e:
        print(f"[ERROR] /api/update: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ══════════════════════════════════════════
#  3. ЗАДАЧИ (скриншот / выключение)
# ══════════════════════════════════════════
@app.get("/api/get_task")
async def get_task():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, command FROM tasks WHERE status='pending' LIMIT 1"
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
    """Агент спрашивает: отменил ли пользователь shutdown?"""
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
#  4. ЗАГРУЗКА СКРИНШОТА
# ══════════════════════════════════════════
@app.post("/api/upload_screen")
async def upload_screen(file: UploadFile = File(...)):
    try:
        content    = await file.read()
        # Сохраняем рядом с БД — main.py найдёт файл там же
        save_path  = os.path.join(os.path.dirname(DB_PATH), "remote_screen.png")
        with open(save_path, "wb") as f:
            f.write(content)
        print(f"[SCREEN] Скриншот сохранён ({len(content) // 1024} KB)")
        return {"status": "ok"}
    except Exception as e:
        print(f"[ERROR] /api/upload_screen: {e}")
        return JSONResponse({"status": "error"}, status_code=500)


# ══════════════════════════════════════════
#  5. СТАТИСТИКА ДЛЯ МИНИ-ПРИЛОЖЕНИЯ
# ══════════════════════════════════════════
@app.get("/api/stats")
async def get_stats():
    try:
        today = datetime.now().strftime("%Y-%m-%d")

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT value FROM settings WHERE key='work_apps'"
            ) as c:
                r = await c.fetchone()
            work_list = r[0].split(",") if r else []

            async with db.execute(
                "SELECT value FROM settings WHERE key='fun_apps'"
            ) as c:
                r = await c.fetchone()
            fun_list = r[0].split(",") if r else []

            async with db.execute(
                "SELECT window_name, SUM(duration) AS s FROM activity "
                "WHERE date=? GROUP BY window_name ORDER BY s DESC",
                (today,),
            ) as c:
                all_rows = await c.fetchall()

            async with db.execute(
                "SELECT date, SUM(duration) FROM activity "
                "WHERE date >= date('now','-6 days') "
                "GROUP BY date ORDER BY date ASC"
            ) as c:
                week_rows = await c.fetchall()

        total = work = fun = 0
        for name, dur in all_rows:
            total += dur
            low    = name.lower()
            if any(w in low for w in work_list if w): work += dur
            elif any(f in low for f in fun_list if f): fun += dur

        h, m   = divmod(total // 60, 60)
        top    = all_rows[:8]

        return {
            "total_h":    h,
            "total_m":    m,
            "work_min":   work  // 60,
            "fun_min":    fun   // 60,
            "other_min":  (total - work - fun) // 60,
            "labels":     [r[0].split("—")[0].split("-")[0].strip()[:20] for r in top],
            "values":     [r[1] // 60 for r in top],
            "week_labels": [r[0][5:] for r in week_rows],
            "week_values": [r[1] // 60 for r in week_rows],
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ══════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════
if __name__ == "__main__":
    print("▶ TimeControl API запущен на порту 8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
