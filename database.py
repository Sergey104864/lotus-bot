import aiosqlite

DB_PATH = "lotus.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                service TEXT,
                date TEXT,
                time TEXT,
                name TEXT,
                phone TEXT
            )
        """)
        await db.commit()

async def add_booking(user_id, service, date, time, name, phone):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO bookings (user_id, service, date, time, name, phone) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, service, date, time, name, phone)
        )
        await db.commit()

async def get_user_bookings(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT service, date, time FROM bookings WHERE user_id = ?", (user_id,))
        return await cursor.fetchall()

async def get_today_bookings(date):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT time, name, phone, service FROM bookings WHERE date = ? ORDER BY time", (date,))
        return await cursor.fetchall()